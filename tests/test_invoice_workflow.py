from __future__ import annotations

import re
from datetime import timedelta
from pathlib import Path

from app.integrations.customer_price.google_sheets import GoogleSheetsCustomerPriceProvider
from app.models import InvoiceJob, InvoiceJobStatus, utc_now
from app.modules.invoice_price_check.rule_engine import InvoiceRuleEngine
from app.modules.invoice_price_check.routes import _manual_review_groups
from app.modules.invoice_price_check.service import InvoicePriceCheckService
from app.pdf.processor import LayoutAwareInvoicePdfProcessor
from tests.test_customer_price_provider import HEADERS, Gateway, row
from tests.test_pdf_processor import _write_invoice


def _configure_workflow(app, *, zero_notes=None, include_carton=False):
    rules = [
        HEADERS,
        row(
            PriceID="regular",
            CustomerID="Example Customer",
            ItemRule="REG-1",
            MatchType="Exact",
            Price="$5.00",
            EffectiveFrom="2026-01-01",
        ),
        row(
            PriceID="zero",
            CustomerID="Example Customer",
            ItemRule="ZERO-1",
            MatchType="Exact",
            Price="$0.00",
            Notes=zero_notes,
            EffectiveFrom="2026-01-01",
        ),
    ]
    if include_carton:
        rules[1] = row(
            PriceID="carton",
            CustomerID="Example Customer",
            ItemRule="REG-1",
            MatchType="Exact",
            PriceType="Carton",
            Price="$5.00",
            EffectiveFrom="2026-01-01",
        )
    provider = GoogleSheetsCustomerPriceProvider(
        Gateway(rules),
        "sheet-id",
    )
    app.state.customer_price_provider = provider
    app.state.invoice_price_check_service = InvoicePriceCheckService(
        provider, LayoutAwareInvoicePdfProcessor(), InvoiceRuleEngine()
    )


def _csrf(client):
    page = client.get("/invoice-price-check")
    match = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    assert match
    return match.group(1)


def test_invoice_page_is_localized_and_uses_custom_file_picker(app, client):
    _configure_workflow(app)
    page = client.get("/invoice-price-check")
    assert page.status_code == 200
    assert "发票价格检查" in page.text
    assert "选择 PDF 文件" in page.text
    assert "尚未选择文件" in page.text
    assert "发票历史记录" in page.text
    assert "今天完成了" in page.text
    assert "你太棒啦！喝口咖啡吧，揉揉眼睛吧~" in page.text
    assert 'id="today-job-count"' in page.text
    assert "Check &amp; Modify Invoice" not in page.text
    assert '/static/koala-mascot-v2.png' in page.text
    assert '/static/boomup-favicon.png?v=20260827-3' in page.text
    assert '/static/app.css?v=20260828-6' in page.text
    assert '/static/invoice.js?v=20260828-4' in page.text
    assert 'id="invoice-dropzone"' in page.text
    assert "把发票 PDF 拖到这里" in page.text
    assert '/static/mascot.js' in page.text
    mascot = client.get("/static/koala-mascot-v2.png")
    assert mascot.status_code == 200
    assert mascot.headers["content-type"] == "image/png"
    favicon = client.get("/static/boomup-favicon.png")
    assert favicon.status_code == 200
    assert favicon.headers["content-type"] == "image/png"
    invoice_script = client.get("/static/invoice.js")
    assert invoice_script.status_code == 200
    assert 'event.persisted || navigation?.type === "back_forward"' in invoice_script.text
    assert 'dropzone.addEventListener("drop"' in invoice_script.text
    assert "new DataTransfer()" in invoice_script.text
    assert "每次请只拖入一个 PDF 文件" in invoice_script.text
    css = client.get("/static/app.css")
    assert "@media (max-width: 640px)" in css.text
    assert "min-width: 1024px" not in css.text


def test_public_end_to_end_upload_result_history_and_download(app, client, tmp_path):
    _configure_workflow(app)
    source = tmp_path / "representative.pdf"
    _write_invoice(source)
    response = client.post(
        "/invoice-price-check/process",
        data={"csrf_token": _csrf(client)},
        files={"invoice_pdf": ("../../representative.pdf", source.read_bytes(), "application/pdf")},
    )
    assert response.status_code == 303
    result = client.get(response.headers["location"])
    assert result.status_code == 200
    assert "WINV-TEST01" in result.text
    assert "处理完成 — 需要人工审核" in result.text
    assert "处理后发票预览" in result.text
    assert 'class="pdf-preview"' in result.text
    assert 'class="panel result-disclosure manual-panel"' in result.text
    assert 'class="panel result-disclosure processing-details"' in result.text
    assert "备注 0 · 整箱价格 0 · 价格变更 1 · 折扣 0" in result.text
    assert "Matching rule" not in result.text
    assert "匹配规则包含备注" not in result.text
    assert "价格为 0" in result.text
    assert "确认是否保留当前发票价格。" in result.text
    assert "(zero)" not in result.text
    assert "MANUAL REVIEW" not in result.text  # UI uses the business section, not debug intents.
    css = client.get("/static/app.css")
    assert ".result-disclosure[open] > summary" in css.text
    assert "position: sticky" in css.text
    download = client.get(f'{response.headers["location"]}/download')
    preview = client.get(f'{response.headers["location"]}/preview')
    assert download.status_code == 200
    assert preview.status_code == 200
    assert download.content.startswith(b"%PDF-")
    assert preview.content == download.content
    assert preview.headers["content-disposition"] == "inline"
    assert preview.headers["x-frame-options"] == "SAMEORIGIN"
    assert "frame-ancestors 'self'" in preview.headers["content-security-policy"]
    history = client.get("/invoice-price-check")
    assert "representative.pdf" in history.text
    assert re.search(r'<time class="local-time" datetime="\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z">', history.text)
    assert re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC</time>", history.text)
    assert not re.search(r"\d{2}:\d{2}:\d{2}(?:\.\d+)?</td>", history.text)
    invoice_script = client.get("/static/invoice.js")
    assert 'document.querySelectorAll("time.local-time")' in invoice_script.text
    assert 'timeZoneName: "short"' in invoice_script.text
    with app.state.SessionLocal() as db:
        job = db.query(InvoiceJob).one()
        assert job.status is InvoiceJobStatus.MANUAL_REVIEW
        assert job.original_filename == "representative.pdf"
        assert job.price_retrieved_at is not None
        assert job.retention_expires_at > job.created_at


def test_manual_review_can_generate_a_frozen_final_invoice(app, client, tmp_path):
    _configure_workflow(app)
    source = tmp_path / "two-stage.pdf"
    _write_invoice(source)
    response = client.post(
        "/invoice-price-check/process",
        data={"csrf_token": _csrf(client)},
        files={"invoice_pdf": ("two-stage.pdf", source.read_bytes(), "application/pdf")},
    )
    result_url = response.headers["location"]
    page = client.get(result_url)
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    revision = re.search(r'name="review_revision" value="(\d+)"', page.text)
    assert csrf and revision
    assert "处理后发票预览 1" in page.text
    assert "生成最终 Invoice" in page.text

    finalized = client.post(
        f"{result_url}/finalize",
        data={
            "csrf_token": csrf.group(1),
            "review_revision": revision.group(1),
            "price_choice_1": "invoice",
            "discount_choice_1": "retain",
        },
    )
    assert finalized.status_code == 303
    final_page = client.get(result_url)
    assert "最终确认版已生成" in final_page.text
    assert "最终确认版发票预览" in final_page.text
    assert "生成最终 Invoice" not in final_page.text
    final_pdf = client.get(f"{result_url}/download")
    initial_pdf = client.get(f"{result_url}/preview-initial")
    assert final_pdf.status_code == initial_pdf.status_code == 200
    assert final_pdf.content.startswith(b"%PDF-")
    with app.state.SessionLocal() as db:
        job = db.query(InvoiceJob).one()
        assert job.final_file_ref
        assert job.finalized_at is not None
        assert not job.manual_review
        assert job.review_json


def test_manual_review_displays_matching_rule_notes(app, client, tmp_path):
    _configure_workflow(app, zero_notes="NO DISCOUNT — 请人工确认客户要求")
    source = tmp_path / "representative-notes.pdf"
    _write_invoice(source)

    response = client.post(
        "/invoice-price-check/process",
        data={"csrf_token": _csrf(client)},
        files={"invoice_pdf": ("representative-notes.pdf", source.read_bytes(), "application/pdf")},
    )

    assert response.status_code == 303
    result = client.get(response.headers["location"])
    assert result.status_code == 200
    assert "规则包含备注" in result.text
    assert "完整备注" in result.text
    assert "NO DISCOUNT — 请人工确认客户要求" in result.text
    assert "阅读备注，进行修改。" in result.text
    assert "错误原因" not in result.text
    assert "需要人工确认</dt>" not in result.text


def test_carton_price_is_preserved_and_manual_review_is_grouped(app, client, tmp_path):
    _configure_workflow(app, include_carton=True)
    source = tmp_path / "representative-carton.pdf"
    _write_invoice(source)

    response = client.post(
        "/invoice-price-check/process",
        data={"csrf_token": _csrf(client)},
        files={"invoice_pdf": ("representative-carton.pdf", source.read_bytes(), "application/pdf")},
    )

    assert response.status_code == 303
    result = client.get(response.headers["location"])
    assert result.status_code == 200
    assert "整箱价格 <span>1</span>" in result.text
    assert "确认客户是否购买整箱。" in result.text
    assert "原价 $4.00，已变更" not in result.text


def test_manual_review_groups_use_concrete_reason_titles():
    rows = [
        {"sku": "PREFIX-1", "reason": "Multiple valid Prefix rules match the SKU"},
        {"sku": "PREFIX-2", "reason": "Multiple valid Prefix rules match the SKU"},
        {"sku": "NOTE-1", "reason": "Matched rule contains Notes"},
        {
            "sku": "NOTE-CARTON",
            "reason": "Matched rule is Carton Price; Matched rule contains Notes",
        },
        {"sku": "CARTON-1", "reason": "Matched rule is Carton Price"},
    ]

    groups = _manual_review_groups(rows)

    assert [(group["label"], len(group["items"])) for group in groups] == [
        ("多条有效前缀规则", 2),
        ("规则包含备注", 2),
        ("整箱价格", 1),
    ]


def test_manual_review_category_titles_have_distinct_visual_classes(app, client):
    _configure_workflow(app)
    css = client.get("/static/app.css")
    assert css.status_code == 200
    assert ".review-category-multiple_prefix" in css.text
    assert ".review-category-notes" in css.text
    assert ".review-category-carton" in css.text


def test_upload_rejects_non_pdf_type(app, client):
    _configure_workflow(app)
    response = client.post(
        "/invoice-price-check/process",
        data={"csrf_token": _csrf(client)},
        files={"invoice_pdf": ("payload.exe", b"MZ", "application/octet-stream")},
    )
    assert response.status_code == 400


def test_corrupt_pdf_is_recorded_as_validation_failure(app, client):
    _configure_workflow(app)
    response = client.post(
        "/invoice-price-check/process",
        data={"csrf_token": _csrf(client)},
        files={"invoice_pdf": ("bad.pdf", b"%PDF-not-valid", "application/pdf")},
    )
    assert response.status_code == 303
    result = client.get(response.headers["location"])
    assert "发票处理未通过验证" in result.text
    assert "Traceback" not in result.text


def test_public_can_access_existing_job_result(app, client):
    with app.state.SessionLocal() as db:
        job = InvoiceJob(
            operator_id=None,
            original_filename="private.pdf",
            original_file_ref="expired",
            rule_engine_version="phase1c-v1",
            platform_version="test",
            status=InvoiceJobStatus.FAILED,
            retention_expires_at=utc_now() + timedelta(days=30),
        )
        db.add(job)
        db.commit()
        job_id = job.id
    assert client.get(f"/invoice-price-check/jobs/{job_id}").status_code == 200
    assert client.get(f"/invoice-price-check/jobs/{job_id}/download").status_code == 404
    assert client.get(f"/invoice-price-check/jobs/{job_id}/preview").status_code == 404


def test_public_workflow_rejects_missing_or_invalid_job(client):
    assert client.get("/invoice-price-check").status_code == 200
    assert client.get("/invoice-price-check/jobs/not-a-job/download").status_code == 404
    assert client.get("/invoice-price-check/jobs/not-a-job/preview").status_code == 404


def test_public_can_view_shared_history(app, client, tmp_path):
    _configure_workflow(app)
    source = tmp_path / "invoice.pdf"
    _write_invoice(source)
    response = client.post(
        "/invoice-price-check/process",
        data={"csrf_token": _csrf(client)},
        files={"invoice_pdf": ("invoice.pdf", source.read_bytes(), "application/pdf")},
    )
    assert client.get(response.headers["location"]).status_code == 200


def test_expired_pdfs_are_deleted_but_audit_metadata_remains(app, client):
    root = app.state.settings.invoice_storage_dir
    with app.state.SessionLocal() as db:
        job = InvoiceJob(
            operator_id=None,
            original_filename="retained-audit.pdf",
            original_file_ref="placeholder",
            output_file_ref="placeholder",
            invoice_number="WINV-RETENTION",
            rule_engine_version="phase1c-v1",
            platform_version="test",
            status=InvoiceJobStatus.SUCCESS,
            retention_expires_at=utc_now() - timedelta(seconds=1),
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        directory = Path(root) / job.id
        directory.mkdir(parents=True)
        original, output = directory / "original.pdf", directory / "modified.pdf"
        original.write_bytes(b"%PDF-original")
        output.write_bytes(b"%PDF-output")
        job.original_file_ref = f"{job.id}/original.pdf"
        job.output_file_ref = f"{job.id}/modified.pdf"
        db.commit()
        job_id = job.id
    assert client.get("/invoice-price-check").status_code == 200
    assert not original.exists()
    assert not output.exists()
    with app.state.SessionLocal() as db:
        retained = db.get(InvoiceJob, job_id)
        assert retained is not None
        assert retained.invoice_number == "WINV-RETENTION"
        assert retained.original_file_ref == "expired"
        assert retained.output_file_ref is None
