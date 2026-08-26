from __future__ import annotations

from dataclasses import dataclass

from app.integrations.customer_price.google_sheets import BlockingSourceError
from app.integrations.customer_price.provider import CustomerPriceProvider
from app.modules.invoice_price_check.rule_engine import (
    InvoiceContext,
    InvoiceDecision,
    InvoiceLine,
    InvoiceRuleEngine,
)
from app.pdf.processor import InvoicePdfProcessor


@dataclass
class InvoicePriceCheckService:
    """Provider-to-rule-engine boundary. PDF rendering remains disabled in Phase 1C."""

    customer_prices: CustomerPriceProvider
    pdf_processor: InvoicePdfProcessor
    rule_engine: InvoiceRuleEngine

    def evaluate(self, context: InvoiceContext, lines: tuple[InvoiceLine, ...]) -> InvoiceDecision:
        snapshot = self.customer_prices.refresh()
        if snapshot.source_blocking_count:
            raise BlockingSourceError("Authoritative Customer Price source is structurally invalid")
        return self.rule_engine.evaluate(
            context, lines, snapshot.rules, retrieved_at=snapshot.retrieved_at
        )

    def process(self) -> None:
        raise NotImplementedError("PDF processing is scheduled for Phase 1D")
