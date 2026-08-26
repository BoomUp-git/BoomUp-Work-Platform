from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.integrations.customer_price.google_sheets import (
    BlockingSourceError,
    SourceUnavailableError,
)
from app.integrations.customer_price.provider import (
    CustomerPriceSnapshot,
    SourceHealth,
    SourceState,
    ValidationIssue,
    ValidationLevel,
)
from app.modules.invoice_price_check.rule_engine import InvoiceRuleEngine
from app.modules.invoice_price_check.service import (
    BusinessValidationError,
    InvoicePriceCheckService,
)
from app.pdf.processor import (
    Box,
    InvoicePdfMetadata,
    LayoutAwareInvoicePdfProcessor,
    ParsedInvoice,
    ParsedInvoiceLine,
    PdfOutputMetadata,
    PdfProcessingOutput,
    RenderingReport,
    ValidationReport,
)
from tests.test_invoice_rule_engine import NOW, context, line, rule
from tests.test_pdf_processor import _rule as pdf_rule
from tests.test_pdf_processor import _write_invoice


class Provider:
    def __init__(
        self, snapshot, *, error=None, state=SourceState.CONNECTED, latest_retrieval=NOW
    ):
        self.snapshot = snapshot
        self.error = error
        self.state = state
        self.latest_retrieval = latest_retrieval
        self.refresh_calls = 0

    def refresh(self):
        self.refresh_calls += 1
        if self.error:
            raise self.error
        return self.snapshot

    def fetch_rules(self, customer_name, invoice_date):
        raise AssertionError("Production processing must force one complete refresh")

    def health(self):
        return SourceHealth(self.state, self.latest_retrieval, 1, 0, 0, "ok")


class Processor:
    def __init__(self, count=1):
        metadata = InvoicePdfMetadata(
            "SYNTHETIC-001", "Example Customer", date(2026, 8, 1), 1, ((595.0, 842.0),)
        )
        template = ParsedInvoiceLine(
            0,
            0,
            0,
            "ABC001",
            "Description",
            "9335803000000",
            Decimal("2"),
            Decimal("4"),
            None,
            Decimal("8"),
            Box(70, 300, 110, 308),
            Box(180, 300, 230, 308),
            Box(320, 300, 345, 308),
            Box(440, 300, 470, 308),
            Box(490, 300, 520, 308),
        )
        lines = tuple(
            replace(template, index=index, page_line_index=index) for index in range(count)
        )
        self.parsed = ParsedInvoice(metadata, lines, (), "source-hash")
        self.render_calls = 0

    def inspect(self, source):
        return self.parsed

    def render(self, source, destination, decisions):
        self.render_calls += 1
        assert len(decisions.lines) == len(self.parsed.lines)
        return PdfProcessingOutput(
            Path(destination),
            RenderingReport(len(decisions.lines), 0, 0, ()),
            ValidationReport(True, ()),
            PdfOutputMetadata("source-hash", "output-hash", NOW, NOW),
        )


def snapshot(*rules, issues=()):
    return CustomerPriceSnapshot(NOW, len(rules), tuple(rules), tuple(issues))


def test_service_gets_rule_input_only_through_fresh_provider_snapshot():
    provider = Provider(snapshot(rule()))
    service = InvoicePriceCheckService(provider, Processor(), InvoiceRuleEngine())
    decision = service.evaluate(context(), (line(),))
    assert provider.refresh_calls == 1
    assert decision.retrieved_at == NOW
    assert decision.lines[0].matched_rule_id == "rule-1"


def test_source_unavailable_stops_before_pdf_and_never_uses_stale_snapshot(tmp_path):
    provider = Provider(snapshot(rule()), error=SourceUnavailableError("offline"))
    processor = Processor()
    service = InvoicePriceCheckService(provider, processor, InvoiceRuleEngine())
    with pytest.raises(SourceUnavailableError):
        service.process(tmp_path / "source.pdf", tmp_path / "output.pdf")
    assert processor.render_calls == 0


def test_any_incomplete_rule_loading_is_source_failure_not_manual_review(tmp_path):
    issue = ValidationIssue(
        ValidationLevel.BLOCKING,
        "invalid_value",
        "bad row",
        source_row=3,
    )
    processor = Processor()
    service = InvoicePriceCheckService(
        Provider(snapshot(rule(), issues=(issue,))), processor, InvoiceRuleEngine()
    )
    with pytest.raises(BlockingSourceError, match="incomplete"):
        service.process(tmp_path / "source.pdf", tmp_path / "output.pdf")
    assert processor.render_calls == 0


def test_disconnected_health_stops_before_pdf(tmp_path):
    processor = Processor()
    service = InvoicePriceCheckService(
        Provider(snapshot(rule()), state=SourceState.UNAVAILABLE), processor, InvoiceRuleEngine()
    )
    with pytest.raises(BusinessValidationError, match="unavailable"):
        service.process(tmp_path / "source.pdf", tmp_path / "output.pdf")
    assert processor.render_calls == 0


def test_unverified_source_freshness_stops_before_pdf(tmp_path):
    processor = Processor()
    service = InvoicePriceCheckService(
        Provider(snapshot(rule()), latest_retrieval=None), processor, InvoiceRuleEngine()
    )
    with pytest.raises(BusinessValidationError, match="freshness"):
        service.process(tmp_path / "source.pdf", tmp_path / "output.pdf")
    assert processor.render_calls == 0


def test_complete_authoritative_rules_are_required_for_customer_and_date(tmp_path):
    inactive = rule(status="Inactive")
    processor = Processor()
    service = InvoicePriceCheckService(
        Provider(snapshot(inactive)), processor, InvoiceRuleEngine()
    )
    with pytest.raises(BusinessValidationError, match="complete authoritative"):
        service.process(tmp_path / "source.pdf", tmp_path / "output.pdf")
    assert processor.render_calls == 0


def test_all_209_lines_use_single_authoritative_full_path_and_metadata_is_recorded(tmp_path):
    provider = Provider(snapshot(rule()))
    processor = Processor(count=209)
    result = InvoicePriceCheckService(provider, processor, InvoiceRuleEngine()).process(
        tmp_path / "source.pdf", tmp_path / "output.pdf"
    )
    assert provider.refresh_calls == 1
    assert result.business_validation.passed
    assert result.accounting.invoice_lines == 209
    assert result.accounting.evaluated_lines == 209
    assert result.accounting.exact_matches == 209
    assert result.source.retrieved_at == NOW
    assert result.source.source_row_count == 1
    assert result.source.normalized_row_count == 1
    assert result.source.customer_rule_count == 1
    assert processor.render_calls == 1


def test_genuine_no_applicable_rule_is_accounted_after_complete_source_load(tmp_path):
    provider = Provider(snapshot(rule(item_rule="OTHER")))
    result = InvoicePriceCheckService(provider, Processor(), InvoiceRuleEngine()).process(
        tmp_path / "source.pdf", tmp_path / "output.pdf"
    )
    assert result.accounting.no_applicable_rule == 1
    assert result.accounting.manual_review == 0
    assert result.accounting.source_failures == 0


def test_production_process_api_has_no_rules_or_fixture_parameter():
    parameters = InvoicePriceCheckService.process.__annotations__
    assert "rules" not in parameters
    assert "fixture" not in parameters


def test_first_representative_pdf_uses_same_authoritative_service_path(tmp_path):
    source, output = tmp_path / "first-source.pdf", tmp_path / "first-output.pdf"
    _write_invoice(source)
    provider = Provider(snapshot(pdf_rule("REG-1", "5"), pdf_rule("ZERO-1", "0")))
    result = InvoicePriceCheckService(
        provider, LayoutAwareInvoicePdfProcessor(), InvoiceRuleEngine()
    ).process(source, output)
    assert provider.refresh_calls == 1
    assert result.accounting.invoice_lines == 2
    assert result.accounting.evaluated_lines == 2
    assert result.pdf.validation.valid
