from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.integrations.customer_price.google_sheets import BlockingSourceError
from app.integrations.customer_price.provider import (
    CustomerPriceSnapshot,
    SourceHealth,
    SourceState,
    ValidationIssue,
    ValidationLevel,
)
from app.modules.invoice_price_check.rule_engine import InvoiceRuleEngine
from app.modules.invoice_price_check.service import InvoicePriceCheckService
from tests.test_invoice_rule_engine import NOW, context, line, rule


class Provider:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.refresh_calls = 0

    def refresh(self):
        self.refresh_calls += 1
        return self.snapshot

    def fetch_rules(self, customer_name, invoice_date):
        raise AssertionError("Rule engine orchestration must use one authoritative snapshot")

    def health(self):
        return SourceHealth(SourceState.CONNECTED, NOW, 1, 0, 0, "ok")


def test_service_gets_rule_input_only_through_provider_snapshot():
    provider = Provider(CustomerPriceSnapshot(NOW, 1, (rule(),), ()))
    service = InvoicePriceCheckService(provider, object(), InvoiceRuleEngine())
    decision = service.evaluate(context(), (line(),))
    assert provider.refresh_calls == 1
    assert decision.retrieved_at == NOW
    assert decision.lines[0].matched_rule_id == "rule-1"


def test_service_stops_on_source_level_structural_failure():
    issue = ValidationIssue(
        ValidationLevel.BLOCKING,
        "missing_headers",
        "schema unavailable",
        source_level=True,
    )
    snapshot = CustomerPriceSnapshot(datetime.now(UTC), 0, (), (issue,))
    service = InvoicePriceCheckService(Provider(snapshot), object(), InvoiceRuleEngine())
    with pytest.raises(BlockingSourceError):
        service.evaluate(context(), (line(),))


def test_service_does_not_render_or_modify_pdf_in_phase_1c():
    provider = Provider(CustomerPriceSnapshot(NOW, 1, (rule(),), ()))
    service = InvoicePriceCheckService(provider, object(), InvoiceRuleEngine())
    with pytest.raises(NotImplementedError, match="Phase 1D"):
        service.process()
