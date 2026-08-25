from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum


@dataclass(frozen=True)
class CustomerPriceRule:
    source_row: int
    price_id: str
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


class SourceState(StrEnum):
    CONNECTED = "connected"
    UNAVAILABLE = "unavailable"


class ValidationLevel(StrEnum):
    WARNING = "warning"
    BLOCKING = "blocking"


@dataclass(frozen=True)
class ValidationIssue:
    level: ValidationLevel
    code: str
    message: str
    source_row: int | None = None
    field: str | None = None


@dataclass(frozen=True)
class CustomerPriceSnapshot:
    retrieved_at: datetime
    source_row_count: int
    rules: tuple[CustomerPriceRule, ...]
    issues: tuple[ValidationIssue, ...]

    @property
    def blocking_count(self) -> int:
        return sum(issue.level == ValidationLevel.BLOCKING for issue in self.issues)

    @property
    def warning_count(self) -> int:
        return sum(issue.level == ValidationLevel.WARNING for issue in self.issues)


@dataclass(frozen=True)
class SourceHealth:
    state: SourceState
    latest_retrieval: datetime | None
    row_count: int | None
    warning_count: int
    blocking_count: int
    message: str


class CustomerPriceProvider(ABC):
    """Read-only authoritative price source boundary."""

    @abstractmethod
    def fetch_rules(self, customer_name: str, invoice_date: date) -> CustomerPriceResult:
        raise NotImplementedError

    @abstractmethod
    def refresh(self) -> CustomerPriceSnapshot:
        raise NotImplementedError

    @abstractmethod
    def health(self) -> SourceHealth:
        raise NotImplementedError
