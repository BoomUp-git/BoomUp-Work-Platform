from __future__ import annotations

import re
from datetime import timedelta
from pathlib import Path

from app.integrations.customer_price.google_sheets import GoogleSheetsCustomerPriceProvider
from app.models import InvoiceJob, InvoiceJobStatus, User, utc_now
from app.modules.invoice_price_check.rule_engine import InvoiceRuleEngine
from app.modules.invoice_price_check.service import InvoicePriceCheckService
from app.pdf.processor import LayoutAwareInvoicePdfProcessor
from tests.conftest import ADMIN_AUTH_SAMPLE, OPERATOR_AUTH_SAMPLE, login
from tests.test_customer_price_provider import HEADERS, Gateway, row
from tests.test_pdf_processor import _write_invoice


def _configure_workflow(app):
    provider = GoogleSheetsCustomerPriceProvider(
        Gateway(
            [
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
                    EffectiveFrom="2026-01-01",
                ),
            ]
        ),
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


def test_authenticated_end_to_end_upload_result_history_and_download(app, client, tmp_path):
    _configure_workflow(app)
    source = tmp_path / "representative.pdf"
    _write_invoice(source)
    login(client, "operator@example.com", OPERATOR_AUTH_SAMPLE)
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
    assert "匹配规则的价格为 0" in result.text
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
    assert "WINV-TEST01" in history.text
    with app.state.SessionLocal() as db:
        job = db.query(InvoiceJob).one()
        assert job.status is InvoiceJobStatus.MANUAL_REVIEW
        assert job.original_filename == "representative.pdf"
        assert job.price_retrieved_at is not None
        assert job.retention_expires_at > job.created_at


def test_upload_rejects_non_pdf_type(app, client):
    _configure_workflow(app)
    login(client, "operator@example.com", OPERATOR_AUTH_SAMPLE)
    response = client.post(
        "/invoice-price-check/process",
        data={"csrf_token": _csrf(client)},
        files={"invoice_pdf": ("payload.exe", b"MZ", "application/octet-stream")},
    )
    assert response.status_code == 400


def test_corrupt_pdf_is_recorded_as_validation_failure(app, client):
    _configure_workflow(app)
    login(client, "operator@example.com", OPERATOR_AUTH_SAMPLE)
    response = client.post(
        "/invoice-price-check/process",
        data={"csrf_token": _csrf(client)},
        files={"invoice_pdf": ("bad.pdf", b"%PDF-not-valid", "application/pdf")},
    )
    assert response.status_code == 303
    result = client.get(response.headers["location"])
    assert "发票处理未通过验证" in result.text
    assert "Traceback" not in result.text


def test_operator_cannot_access_another_users_job(app, client):
    with app.state.SessionLocal() as db:
        admin = db.query(User).filter_by(email="admin@example.com").one()
        job = InvoiceJob(
            operator_id=admin.id,
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
    login(client, "operator@example.com", OPERATOR_AUTH_SAMPLE)
    assert client.get(f"/invoice-price-check/jobs/{job_id}").status_code == 404
    assert client.get(f"/invoice-price-check/jobs/{job_id}/download").status_code == 404
    assert client.get(f"/invoice-price-check/jobs/{job_id}/preview").status_code == 404


def test_unauthenticated_workflow_and_download_are_protected(client):
    assert client.get("/invoice-price-check").status_code == 303
    assert client.get("/invoice-price-check/jobs/not-a-job/download").status_code == 303
    assert client.get("/invoice-price-check/jobs/not-a-job/preview").status_code == 303


def test_admin_can_view_operator_history(app, client, tmp_path):
    _configure_workflow(app)
    source = tmp_path / "invoice.pdf"
    _write_invoice(source)
    login(client, "operator@example.com", OPERATOR_AUTH_SAMPLE)
    response = client.post(
        "/invoice-price-check/process",
        data={"csrf_token": _csrf(client)},
        files={"invoice_pdf": ("invoice.pdf", source.read_bytes(), "application/pdf")},
    )
    client.cookies.clear()
    login(client, "admin@example.com", ADMIN_AUTH_SAMPLE)
    assert client.get(response.headers["location"]).status_code == 200


def test_expired_pdfs_are_deleted_but_audit_metadata_remains(app, client):
    root = app.state.settings.invoice_storage_dir
    with app.state.SessionLocal() as db:
        operator = db.query(User).filter_by(email="operator@example.com").one()
        job = InvoiceJob(
            operator_id=operator.id,
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
    login(client, "operator@example.com", OPERATOR_AUTH_SAMPLE)
    assert client.get("/invoice-price-check").status_code == 200
    assert not original.exists()
    assert not output.exists()
    with app.state.SessionLocal() as db:
        retained = db.get(InvoiceJob, job_id)
        assert retained is not None
        assert retained.invoice_number == "WINV-RETENTION"
        assert retained.original_file_ref == "expired"
        assert retained.output_file_ref is None
