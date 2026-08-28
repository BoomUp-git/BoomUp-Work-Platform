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
from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy import desc, select, update
from sqlalchemy.orm import Session

from app.auth.dependencies import get_db
from app.integrations.customer_price.diagnostics import source_diagnostics
from app.models import InvoiceJob, InvoiceJobStatus, utc_now
from app.modules.invoice_price_check.rule_engine import VisualIntent
from app.modules.invoice_price_check.review_workflow import (
    ReviewValidationError,
    decision_snapshot,
    resolve_manual_reviews,
    restore_decision,
)

router = APIRouter(prefix="/invoice-price-check")
log = logging.getLogger(__name__)
RULE_ENGINE_VERSION = "phase1c-v2"
RETENTION_DAYS = 30

REVIEW_TEXT_ZH = {
    "Customer cannot be uniquely resolved": "无法唯一确认客户",
    "Confirm the authoritative customer before processing": "请确认正确的客户后再处理",
    "Multiple valid Exact rules match the SKU": "该货号匹配到多条有效的精确规则",
    "Choose the authoritative Exact rule": "请选择正确的精确规则",
    "Multiple valid Prefix rules match the SKU": "该货号匹配到多条有效的前缀规则",
    "Choose the authoritative Prefix rule": "请选择正确的前缀规则",
    "Matched rule contains Notes": "匹配规则包含备注",
    "Matched rule is Carton Price": "匹配规则为整箱价格",
    "Matched rule Price is blank": "匹配规则的价格为空",
    "Matched rule Price is zero": "匹配规则的价格为 0",
    "Review the complete rule and confirm the current Invoice values": (
        "请检查完整规则，并确认当前发票中的价格、折扣和金额"
    ),
    "Confirm whether the customer ordered a full carton and review the current Invoice values": (
        "请确认客户是否购买整箱，并确认当前发票中的价格、折扣和金额"
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

REVIEW_ACTION_ZH = (
    ("contains Notes", "阅读备注，进行修改。"),
    ("Multiple valid Prefix rules", "选择要使用的价格规则。"),
    ("Multiple valid Exact rules", "选择要使用的价格规则。"),
    ("Carton Price", "确认客户是否购买整箱。"),
    ("Price is zero", "确认是否保留当前发票价格。"),
    ("Price is blank", "确认要使用的价格。"),
    ("discount", "确认要使用的折扣。"),
    ("Customer cannot be uniquely resolved", "确认正确的客户。"),
)


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


def _review_action_zh(reason: str | None) -> str:
    if not reason:
        return "检查相关规则并完成确认。"
    reason_casefold = reason.casefold()
    for marker, action in REVIEW_ACTION_ZH:
        if marker.casefold() in reason_casefold:
            return action
    return "检查相关规则并完成确认。"


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
        for reference in (job.original_file_ref, job.output_file_ref, job.final_file_ref):
            if not reference:
                continue
            path = _safe_path(root, reference)
            if path.is_file():
                path.unlink()
            changed = True
        job.original_file_ref = "expired"
        job.output_file_ref = None
        job.final_file_ref = None
    if changed:
        db.commit()


def _visible_jobs(db: Session):
    query = select(InvoiceJob).order_by(desc(InvoiceJob.created_at)).limit(30)
    return db.scalars(query).all()


def _job(db: Session, job_id: str) -> InvoiceJob:
    job = db.get(InvoiceJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Invoice job not found")
    return job


def _csrf_serializer(request: Request) -> URLSafeSerializer:
    return URLSafeSerializer(request.app.state.settings.app_secret, salt="public-upload-csrf")


def _new_csrf(request: Request) -> str:
    return _csrf_serializer(request).dumps(secrets.token_urlsafe(32))


def _valid_csrf(request: Request, submitted: str) -> bool:
    cookie = request.cookies.get("boomup_public_csrf", "")
    if not cookie or not secrets.compare_digest(cookie, submitted):
        return False
    try:
        _csrf_serializer(request).loads(submitted)
    except BadSignature:
        return False
    return True


def _modified_pdf(job: InvoiceJob, request: Request) -> Path:
    if job.status not in {InvoiceJobStatus.SUCCESS, InvoiceJobStatus.MANUAL_REVIEW}:
        raise HTTPException(status_code=404, detail="Modified Invoice is unavailable")
    reference = job.final_file_ref or job.output_file_ref
    if not reference:
        raise HTTPException(status_code=404, detail="Modified Invoice has expired")
    path = _safe_path(_storage_root(request), reference)
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


def _manual_review_category(row: dict) -> tuple[str, str]:
    reason = row.get("reason") or ""
    # Use the concrete review reason as the category. The ordering below also
    # keeps all rows with Notes together when a rule has more than one issue.
    categories = (
        ("Multiple valid Prefix rules", "multiple_prefix", "多条有效前缀规则"),
        ("Multiple valid Exact rules", "multiple_exact", "多条有效精确规则"),
        ("contains Notes", "notes", "规则包含备注"),
        ("Carton Price", "carton", "整箱价格"),
        ("Price is zero", "price_zero", "价格为 0"),
        ("Price is blank", "price_blank", "价格为空"),
        ("discount", "discount", "折扣设置问题"),
        ("Customer cannot be uniquely resolved", "customer", "客户确认"),
    )
    reason_casefold = reason.casefold()
    for marker, key, label in categories:
        if marker.casefold() in reason_casefold:
            return key, label
    translated = _review_text_zh(reason)
    return f"reason:{reason}", translated if translated != "-" else "其他人工审核"


def _manual_review_groups(rows: list[dict]) -> list[dict]:
    grouped_rows: dict[str, dict] = {}
    for row in rows:
        key, label = _manual_review_category(row)
        grouped_rows.setdefault(key, {"key": key, "label": label, "items": []})[
            "items"
        ].append(row)
    return list(grouped_rows.values())


def _manual_review_row(line, index: int) -> dict:
    from app.modules.invoice_price_check.review_workflow import default_notes_price_candidate

    default_price = default_notes_price_candidate(line)
    return {
        "index": index,
        "sku": line.sku,
        "invoice_price": _money(line.original_price),
        "invoice_discount": _discount(line.original_discount),
        "matching_rule": line.match_type or line.match_status.value,
        "reason": line.manual_review_reason,
        "notes": line.notes,
        "decision_required": line.decision_required,
        "default_price_id": None if default_price is None else default_price.price_id,
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
                "raw_price": None if candidate.price is None else str(candidate.price),
                "no_discount": candidate.no_discount,
                "has_discount": candidate.has_discount,
                "raw_discount": (
                    None if candidate.discount_value is None else str(candidate.discount_value)
                ),
            }
            for candidate in line.candidates
        ],
    }


def _payload(result) -> dict:
    lines = result.decision.lines
    manual_lines = [line for line in lines if line.manual_review]
    manual_rows = [
        _manual_review_row(line, index)
        for index, line in enumerate(lines)
        if line.manual_review
    ]
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
        "manual_review": manual_rows,
        "manual_review_groups": _manual_review_groups(manual_rows),
        "_decision_snapshot": decision_snapshot(result.decision),
    }


@router.get("", response_class=HTMLResponse)
def invoice_page(
    request: Request,
    db: Session = Depends(get_db),
):
    root = _storage_root(request)
    _cleanup_expired(db, root)
    csrf_token = _new_csrf(request)
    response = request.app.state.templates.TemplateResponse(
        request=request,
        name="invoice_price_check.html",
        context={
            "user": None,
            "csrf_token": csrf_token,
            "source_health": source_diagnostics(request),
            "jobs": _visible_jobs(db),
            "max_mb": request.app.state.settings.invoice_max_upload_bytes // (1024 * 1024),
            "zh_ui": True,
        },
    )
    response.set_cookie(
        "boomup_public_csrf", csrf_token, httponly=True,
        secure=request.app.state.settings.app_env == "production", samesite="strict",
    )
    return response


@router.post("/process")
async def process_invoice(
    request: Request,
    csrf_token: str = Form(...),
    invoice_pdf: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    if not _valid_csrf(request, csrf_token):
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
        operator_id=None,
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
    db: Session = Depends(get_db),
):
    job = _job(db, job_id)
    result = json.loads(job.result_json) if job.result_json else None
    if result is not None:
        # Historical jobs predate the default Notes price field. Enrich them
        # at display time so users do not need to upload the invoice again.
        for row in result.get("manual_review", []):
            if "default_price_id" not in row:
                usable = [
                    candidate for candidate in row.get("candidates", [])
                    if candidate.get("raw_price") not in {None, "0", "0.0", "0.00"}
                    and candidate.get("price_type") != "Carton"
                ]
                row["default_price_id"] = (
                    usable[0].get("price_id")
                    if row.get("notes") and len(usable) == 1
                    else None
                )
        # Rebuild groups at display time so historical jobs immediately use
        # the current reason-based grouping without rewriting stored results.
        result["manual_review_groups"] = _manual_review_groups(
            result.get("manual_review", [])
        )
    csrf_token = _new_csrf(request)
    response = request.app.state.templates.TemplateResponse(
        request=request,
        name="invoice_result.html",
        context={
            "user": None,
            "job": job,
            "result": result,
            "zh_ui": True,
            "review_zh": _review_text_zh,
            "review_action_zh": _review_action_zh,
            "value_zh": _display_value_zh,
            "csrf_token": csrf_token,
        },
    )
    response.set_cookie(
        "boomup_public_csrf", csrf_token, httponly=True,
        secure=request.app.state.settings.app_env == "production", samesite="strict",
    )
    return response


def _review_selections(form, manual_rows: list[dict]) -> dict[int, dict]:
    selections = {}
    for row in manual_rows:
        index = int(row["index"])
        selections[index] = {
            "price_choice": form.get(f"price_choice_{index}", ""),
            "custom_price": form.get(f"custom_price_{index}", ""),
            "discount_choice": form.get(f"discount_choice_{index}", ""),
            "custom_discount": form.get(f"custom_discount_{index}", ""),
            "notes_ack": form.get(f"notes_ack_{index}") == "yes",
        }
    return selections


@router.post("/jobs/{job_id}/finalize")
async def finalize_invoice(
    job_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    form = await request.form()
    if not _valid_csrf(request, str(form.get("csrf_token", ""))):
        raise HTTPException(status_code=400, detail="Invalid CSRF token")
    job = _job(db, job_id)
    if not job.result_json or not job.output_file_ref:
        raise HTTPException(status_code=409, detail="Invoice review is unavailable")
    if job.final_file_ref:
        return RedirectResponse(url=f"/invoice-price-check/jobs/{job.id}", status_code=303)
    try:
        submitted_revision = int(str(form.get("review_revision", "-1")))
    except ValueError:
        raise HTTPException(status_code=409, detail="Review version is invalid") from None
    claimed = db.execute(
        update(InvoiceJob)
        .where(
            InvoiceJob.id == job.id,
            InvoiceJob.review_revision == submitted_revision,
            InvoiceJob.finalizing.is_(False),
            InvoiceJob.final_file_ref.is_(None),
        )
        .values(finalizing=True, review_revision=InvoiceJob.review_revision + 1)
    )
    db.commit()
    if claimed.rowcount != 1:
        raise HTTPException(
            status_code=409,
            detail="这张发票已在其他页面提交，请刷新后检查最新状态。",
        )

    root = _storage_root(request)
    source = _safe_path(root, job.original_file_ref)
    final_path = source.parent / "final-confirmed.pdf"
    final_path.unlink(missing_ok=True)
    payload = json.loads(job.result_json)
    try:
        decision = restore_decision(payload["_decision_snapshot"])
        parsed = request.app.state.invoice_price_check_service.pdf_processor.inspect(source)
        selections = _review_selections(form, payload.get("manual_review", []))
        final_decision, audit = resolve_manual_reviews(
            decision,
            tuple(line.quantity for line in parsed.lines),
            selections,
        )
        request.app.state.invoice_price_check_service.pdf_processor.render(
            source, final_path, final_decision
        )
        job = _job(db, job_id)
        job.final_file_ref = str(final_path.relative_to(root))
        job.review_json = json.dumps(audit, separators=(",", ":"))
        job.finalized_at = utc_now()
        job.finalizing = False
        job.manual_review = False
        job.status = InvoiceJobStatus.SUCCESS
        job.safe_error = None
        db.commit()
    except ReviewValidationError as exc:
        final_path.unlink(missing_ok=True)
        job = _job(db, job_id)
        job.finalizing = False
        job.safe_error = str(exc)[:500]
        db.commit()
        return RedirectResponse(
            url=f"/invoice-price-check/jobs/{job.id}?review_error=1", status_code=303
        )
    except Exception:
        log.exception("Final invoice generation failed for job %s", job.id)
        final_path.unlink(missing_ok=True)
        job = _job(db, job_id)
        job.finalizing = False
        job.safe_error = "最终确认版生成失败，请刷新后重试。"
        db.commit()
        return RedirectResponse(
            url=f"/invoice-price-check/jobs/{job.id}?review_error=1", status_code=303
        )
    return RedirectResponse(url=f"/invoice-price-check/jobs/{job.id}", status_code=303)


@router.get("/jobs/{job_id}/preview-initial")
def preview_initial_invoice(
    job_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    job = _job(db, job_id)
    if not job.output_file_ref:
        raise HTTPException(status_code=404, detail="Initial Invoice is unavailable")
    path = _safe_path(_storage_root(request), job.output_file_ref)
    return FileResponse(path, media_type="application/pdf", headers={"Content-Disposition": "inline"})


@router.get("/jobs/{job_id}/download")
def download_invoice(
    job_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    job = _job(db, job_id)
    path = _modified_pdf(job, request)
    invoice = job.invoice_number or "invoice"
    return FileResponse(path, media_type="application/pdf", filename=f"{invoice}-checked.pdf")


@router.get("/jobs/{job_id}/preview")
def preview_invoice(
    job_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    job = _job(db, job_id)
    path = _modified_pdf(job, request)
    return FileResponse(
        path,
        media_type="application/pdf",
        headers={"Content-Disposition": "inline"},
    )
