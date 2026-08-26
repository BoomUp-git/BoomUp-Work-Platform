from __future__ import annotations

import json
import logging
import secrets
import subprocess
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pymupdf
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.auth.dependencies import current_session, get_db
from app.integrations.customer_price.diagnostics import source_diagnostics
from app.models import AuthSession, InvoiceJob, InvoiceJobStatus, UserRole, utc_now
from app.modules.invoice_price_check.rule_engine import VisualIntent

router = APIRouter(prefix="/invoice-price-check")
log = logging.getLogger(__name__)
RULE_ENGINE_VERSION = "phase1c-v1"
RETENTION_DAYS = 30

REVIEW_TEXT_ZH = {
    "Customer cannot be uniquely resolved": "无法唯一确认客户",
    "Confirm the authoritative customer before processing": "请确认正确的客户后再处理",
    "Multiple valid Exact rules match the SKU": "该货号匹配到多条有效的精确规则",
    "Choose the authoritative Exact rule": "请选择正确的精确规则",
    "Multiple valid Prefix rules match the SKU": "该货号匹配到多条有效的前缀规则",
    "Choose the authoritative Prefix rule": "请选择正确的前缀规则",
    "Matched rule contains Notes": "匹配规则包含备注",
    "Matched rule Price is blank": "匹配规则的价格为空",
    "Matched rule Price is zero": "匹配规则的价格为 0",
    "Review the complete rule and confirm the current Invoice values": (
        "请检查完整规则，并确认当前发票中的价格、折扣和金额"
    ),
    "Correct or confirm the structured discount fields": "请修正或确认折扣设置",
    "A structured discount boolean is blank": "折扣选项存在空值",
    "NoDiscount and HasDiscount are both true": "NoDiscount 与 HasDiscount 同时为真",
    "NoDiscount conflicts with DiscountValue": "NoDiscount 与 DiscountValue 冲突",
    "HasDiscount is true but DiscountValue is blank": "HasDiscount 为真，但 DiscountValue 为空",
    "DiscountValue is present while HasDiscount is false": (
        "HasDiscount 为假，但填写了 DiscountValue"
    ),
}

DISPLAY_VALUE_ZH = {
    "Exact": "精确匹配",
    "Prefix": "前缀匹配",
    "Regular": "普通价格",
    "Carton": "整箱价格",
    "Invoice discount retained": "保留发票折扣",
}


def _review_text_zh(value: str | None) -> str:
    if not value:
        return "-"
    translated = value
    for english, chinese in REVIEW_TEXT_ZH.items():
        translated = translated.replace(english, chinese)
    return translated


def _display_value_zh(value: str | None) -> str:
    if not value:
        return "-"
    return DISPLAY_VALUE_ZH.get(value, value)


def _storage_root(request: Request) -> Path:
    root = Path(request.app.state.settings.invoice_storage_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_path(root: Path, reference: str) -> Path:
    path = (root / reference).resolve()
    if root != path and root not in path.parents:
        raise HTTPException(status_code=404, detail="File not found")
    return path


def _cleanup_expired(db: Session, root: Path) -> None:
    jobs = db.scalars(
        select(InvoiceJob).where(InvoiceJob.retention_expires_at <= utc_now())
    ).all()
    changed = False
    for job in jobs:
        for reference in (job.original_file_ref, job.output_file_ref):
            if not reference:
                continue
            path = _safe_path(root, reference)
            if path.is_file():
                path.unlink()
            changed = True
        job.original_file_ref = "expired"
        job.output_file_ref = None
    if changed:
        db.commit()


def _visible_jobs(db: Session, session: AuthSession):
    query = select(InvoiceJob).order_by(desc(InvoiceJob.created_at)).limit(30)
    if session.user.role is not UserRole.ADMIN:
        query = query.where(InvoiceJob.operator_id == session.user_id)
    return db.scalars(query).all()


def _job(db: Session, session: AuthSession, job_id: str) -> InvoiceJob:
    job = db.get(InvoiceJob, job_id)
    if job is None or (
        session.user.role is not UserRole.ADMIN and job.operator_id != session.user_id
    ):
        raise HTTPException(status_code=404, detail="Invoice job not found")
    return job


def _modified_pdf(job: InvoiceJob, request: Request) -> Path:
    if job.status not in {InvoiceJobStatus.SUCCESS, InvoiceJobStatus.MANUAL_REVIEW}:
        raise HTTPException(status_code=404, detail="Modified Invoice is unavailable")
    if not job.output_file_ref:
        raise HTTPException(status_code=404, detail="Modified Invoice has expired")
    path = _safe_path(_storage_root(request), job.output_file_ref)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Modified Invoice has expired")
    return path


def _money(value: Decimal) -> str:
    return f"${value:.2f}"


def _platform_version(request: Request) -> str:
    configured = request.app.state.settings.work_platform_version
    if configured != "development":
        return configured
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).resolve().parents[3],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
        return completed.stdout.strip()[:64] or "development"
    except (OSError, subprocess.SubprocessError):
        return "development"


def _discount(value: Decimal | None) -> str:
    return "-" if value is None else f"{value.normalize():f}%"


def _payload(result) -> dict:
    lines = result.decision.lines
    return {
        "accounting": result.accounting.__dict__,
        "price_changes": [
            {
                "sku": line.sku,
                "original": _money(line.original_price),
                "final": _money(line.final_price),
            }
            for line in lines
            if line.price_changed
        ],
        "carton": [
            {
                "sku": line.sku,
                "price": _money(line.final_price),
                "changed": line.price_changed,
                "original": _money(line.original_price),
            }
            for line in lines
            if line.carton
        ],
        "discount": [
            {
                "sku": line.sku,
                "original": _discount(line.original_discount),
                "final": _discount(line.final_discount),
                "no_discount": VisualIntent.NO_DISCOUNT in line.visual_intents,
            }
            for line in lines
            if VisualIntent.DISCOUNT in line.visual_intents
            or VisualIntent.NO_DISCOUNT in line.visual_intents
            or line.discount_changed
        ],
        "notes": [
            {"sku": line.sku, "notes": line.notes} for line in lines if line.notes
        ],
        "manual_review": [
            {
                "sku": line.sku,
                "invoice_price": _money(line.original_price),
                "invoice_discount": _discount(line.original_discount),
                "matching_rule": line.match_type or line.match_status.value,
                "reason": line.manual_review_reason,
                "decision_required": line.decision_required,
                "candidates": [
                    {
                        "price_id": candidate.price_id,
                        "item_rule": candidate.item_rule,
                        "match_type": candidate.match_type,
                        "price": "Blank" if candidate.price is None else _money(candidate.price),
                        "price_type": candidate.price_type,
                        "discount": (
                            "NO DISC"
                            if candidate.no_discount
                            else _discount(candidate.discount_value)
                            if candidate.has_discount
                            else "Invoice discount retained"
                        ),
                    }
                    for candidate in line.candidates
                ],
            }
            for line in lines
            if line.manual_review
        ],
    }


@router.get("", response_class=HTMLResponse)
def invoice_page(
    request: Request,
    session: AuthSession = Depends(current_session),
    db: Session = Depends(get_db),
):
    root = _storage_root(request)
    _cleanup_expired(db, root)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="invoice_price_check.html",
        context={
            "user": session.user,
            "session": session,
            "source_health": source_diagnostics(request),
            "jobs": _visible_jobs(db, session),
            "max_mb": request.app.state.settings.invoice_max_upload_bytes // (1024 * 1024),
            "zh_ui": True,
        },
    )


@router.post("/process")
async def process_invoice(
    request: Request,
    csrf_token: str = Form(...),
    invoice_pdf: UploadFile = File(...),
    session: AuthSession = Depends(current_session),
    db: Session = Depends(get_db),
):
    if not secrets.compare_digest(session.csrf_token, csrf_token):
        raise HTTPException(status_code=400, detail="Invalid CSRF token")
    service = request.app.state.invoice_price_check_service
    if service is None:
        raise HTTPException(status_code=503, detail="Customer Price Manager unavailable")
    filename = Path(invoice_pdf.filename or "").name
    if not filename or Path(filename).suffix.casefold() != ".pdf":
        raise HTTPException(status_code=400, detail="Upload a PDF file")
    if invoice_pdf.content_type not in {"application/pdf", "application/octet-stream"}:
        raise HTTPException(status_code=400, detail="Upload a PDF file")

    job = InvoiceJob(
        operator_id=session.user_id,
        original_filename=filename[:255],
        original_file_ref="pending",
        rule_engine_version=RULE_ENGINE_VERSION,
        platform_version=_platform_version(request),
        status=InvoiceJobStatus.PROCESSING,
        retention_expires_at=utc_now() + timedelta(days=RETENTION_DAYS),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    root, directory = _storage_root(request), _storage_root(request) / job.id
    directory.mkdir(mode=0o700)
    source, output = directory / "original.pdf", directory / "modified.pdf"
    job.original_file_ref = str(source.relative_to(root))
    db.commit()
    try:
        size = 0
        with source.open("xb") as handle:
            while chunk := await invoice_pdf.read(1024 * 1024):
                size += len(chunk)
                if size > request.app.state.settings.invoice_max_upload_bytes:
                    raise ValueError("PDF exceeds the upload size limit")
                handle.write(chunk)
        if size < 5 or source.read_bytes()[:5] != b"%PDF-":
            raise ValueError("File is not a PDF")
        with pymupdf.open(source) as document:
            if not document.is_pdf or document.page_count < 1:
                raise ValueError("PDF structure is invalid")

        result = service.process(source, output)
        job.invoice_number = result.decision.invoice_number
        job.customer = result.decision.resolved_customer
        job.invoice_date = result.decision.invoice_date
        job.processed_at = utc_now()
        job.price_retrieved_at = result.source.retrieved_at.replace(tzinfo=None)
        job.manual_review = bool(result.accounting.manual_review)
        job.status = (
            InvoiceJobStatus.MANUAL_REVIEW if job.manual_review else InvoiceJobStatus.SUCCESS
        )
        job.output_file_ref = str(output.relative_to(root))
        job.result_json = json.dumps(_payload(result), separators=(",", ":"))
        db.commit()
    except Exception:
        log.exception("Invoice processing job %s failed", job.id)
        output.unlink(missing_ok=True)
        job.status = InvoiceJobStatus.FAILED
        job.processed_at = utc_now()
        job.safe_error = (
            "Invoice processing failed validation. Confirm the PDF and Customer Price Manager "
            "status, then try again."
        )
        db.commit()
    finally:
        await invoice_pdf.close()
    return RedirectResponse(url=f"/invoice-price-check/jobs/{job.id}", status_code=303)


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_result(
    job_id: str,
    request: Request,
    session: AuthSession = Depends(current_session),
    db: Session = Depends(get_db),
):
    job = _job(db, session, job_id)
    result = json.loads(job.result_json) if job.result_json else None
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="invoice_result.html",
        context={
            "user": session.user,
            "session": session,
            "job": job,
            "result": result,
            "zh_ui": True,
            "review_zh": _review_text_zh,
            "value_zh": _display_value_zh,
        },
    )


@router.get("/jobs/{job_id}/download")
def download_invoice(
    job_id: str,
    request: Request,
    session: AuthSession = Depends(current_session),
    db: Session = Depends(get_db),
):
    job = _job(db, session, job_id)
    path = _modified_pdf(job, request)
    invoice = job.invoice_number or "invoice"
    return FileResponse(path, media_type="application/pdf", filename=f"{invoice}-checked.pdf")


@router.get("/jobs/{job_id}/preview")
def preview_invoice(
    job_id: str,
    request: Request,
    session: AuthSession = Depends(current_session),
    db: Session = Depends(get_db),
):
    job = _job(db, session, job_id)
    path = _modified_pdf(job, request)
    return FileResponse(
        path,
        media_type="application/pdf",
        headers={"Content-Disposition": "inline"},
    )
