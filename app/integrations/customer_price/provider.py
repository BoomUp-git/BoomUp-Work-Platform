from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal


@dataclass(frozen=True)
class CustomerPriceRule:
    customer_id: str
    item_rule: str
    match_type: str
    excluded_items: tuple[str, ...]
    price_type: str
    price: Decimal
    no_discount: bool
    has_discount: bool
    discount_value: Decimal | None
    notes: str | None
    effective_from: date
    effective_to: date | None
    status: str


@dataclass(frozen=True)
class CustomerPriceResult:
    customer_name: str
    retrieved_at: datetime
    rules: tuple[CustomerPriceRule, ...]


class CustomerPriceProvider(ABC):
    """Read-only authoritative price source boundary. No Phase 1A implementation."""

    @abstractmethod
    def fetch_rules(self, customer_name: str, invoice_date: date) -> CustomerPriceResult:
        raise NotImplementedError
