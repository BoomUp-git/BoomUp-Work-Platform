from __future__ import annotations

from dataclasses import dataclass

from app.integrations.customer_price.provider import CustomerPriceProvider
from app.pdf.processor import InvoicePdfProcessor


@dataclass
class InvoicePriceCheckService:
    """Composition boundary only. No matching, pricing, discount, amount, or PDF logic."""

    customer_prices: CustomerPriceProvider
    pdf_processor: InvoicePdfProcessor

    def process(self) -> None:
        raise NotImplementedError("Invoice processing is scheduled for a later phase")
