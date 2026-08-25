from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from threading import RLock
from typing import Any, Protocol

from app.config import Settings
from app.integrations.customer_price.provider import (
    CustomerPriceProvider,
    CustomerPriceResult,
    CustomerPriceRule,
    CustomerPriceSnapshot,
    SourceHealth,
    SourceState,
    ValidationIssue,
    ValidationLevel,
)

READ_ONLY_SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"
PRICE_RULES_RANGE = "PriceRules!A:V"
REQUIRED_HEADERS = (
    "PriceID", "CustomerID", "ItemRule", "MatchType", "ExcludedItems", "PriceType",
    "Price", "NoDiscount", "EffectiveFrom", "EffectiveTo", "Status", "HasDiscount",
    "DiscountValue", "Notes",
)
MATCH_TYPES = {"Exact", "Prefix"}
PRICE_TYPES = {"Regular", "Carton", "Clearance"}
STATUSES = {"Active", "Inactive"}


class SourceUnavailableError(RuntimeError):
    pass


class BlockingSourceError(RuntimeError):
    pass


class SheetsGateway(Protocol):
    def read_values(self, spreadsheet_id: str, range_name: str) -> list[list[Any]]: ...


class GoogleSheetsGateway:
    """Google Sheets v4 client restricted to the spreadsheets.readonly scope."""

    def __init__(self, settings: Settings):
        try:
            import google.auth
            from google.oauth2 import service_account
            from googleapiclient.discovery import build
        except ImportError as exc:  # pragma: no cover - packaging guard
            raise SourceUnavailableError("Google client libraries are not installed") from exc

        if settings.google_auth_mode == "service_account":
            path = settings.google_service_account_file
            if not path:
                raise SourceUnavailableError(
                    "Google service-account credential path is not configured"
                )
            credential_path = Path(path)
            if not credential_path.is_file():
                raise SourceUnavailableError(
                    "Google service-account credential file is unavailable"
                )
            credentials = service_account.Credentials.from_service_account_file(
                credential_path, scopes=[READ_ONLY_SCOPE]
            )
        else:
            credentials, _ = google.auth.default(scopes=[READ_ONLY_SCOPE])
        self._service = build("sheets", "v4", credentials=credentials, cache_discovery=False)

    def read_values(self, spreadsheet_id: str, range_name: str) -> list[list[Any]]:
        response = (
            self._service.spreadsheets()
            .values()
            .get(spreadsheetId=spreadsheet_id, range=range_name)
            .execute()
        )
        return response.get("values", [])


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _decimal(value: Any) -> Decimal:
    normalized = _text(value).replace("$", "").replace(",", "")
    if not normalized:
        raise ValueError("value is empty")
    try:
        return Decimal(normalized)
    except InvalidOperation as exc:
        raise ValueError("value is not a decimal") from exc


def _discount_decimal(value: Any) -> Decimal:
    normalized = _text(value)
    if normalized.endswith("%"):
        normalized = normalized[:-1].strip()
    return _decimal(normalized)


def _bool(value: Any) -> bool:
    normalized = _text(value).casefold()
    if normalized in {"true", "yes", "1"}:
        return True
    if normalized in {"false", "no", "0"}:
        return False
    raise ValueError("value is not a boolean")


def _date(value: Any) -> date:
    normalized = _text(value)
    for pattern in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(normalized, pattern).date()
        except ValueError:
            pass
    raise ValueError("value is not a supported business date")


def _field(row: Sequence[Any], indexes: dict[str, int], name: str) -> Any:
    return row[indexes[name]]


def normalize_price_rules(
    values: Sequence[Sequence[Any]], *, retrieved_at: datetime | None = None
) -> CustomerPriceSnapshot:
    retrieved_at = retrieved_at or datetime.now(UTC)
    if not values:
        issue = ValidationIssue(
            ValidationLevel.BLOCKING, "empty_source", "PriceRules returned no rows"
        )
        return CustomerPriceSnapshot(retrieved_at, 0, (), (issue,))

    headers = tuple(_text(value) for value in values[0])
    missing = [header for header in REQUIRED_HEADERS if header not in headers]
    if missing:
        issue = ValidationIssue(
            ValidationLevel.BLOCKING,
            "missing_headers",
            f"Required source columns are missing: {', '.join(missing)}",
        )
        return CustomerPriceSnapshot(retrieved_at, max(len(values) - 1, 0), (), (issue,))

    indexes = {header: headers.index(header) for header in REQUIRED_HEADERS}
    rules: list[CustomerPriceRule] = []
    issues: list[ValidationIssue] = []
    source_row_count = 0
    for row_number, row in enumerate(values[1:], start=2):
        padded = list(row) + [""] * max(0, len(headers) - len(row))
        if not any(_text(value) for value in padded):
            continue
        source_row_count += 1

        required = ("PriceID", "CustomerID", "ItemRule", "MatchType", "PriceType", "Price",
                    "NoDiscount", "EffectiveFrom", "Status", "HasDiscount")
        missing_values = [
            field for field in required if not _text(_field(padded, indexes, field))
        ]
        if missing_values:
            issues.append(ValidationIssue(
                ValidationLevel.BLOCKING, "missing_required_value",
                f"Required values are missing: {', '.join(missing_values)}", row_number
            ))
            continue
        try:
            match_type = _text(_field(padded, indexes, "MatchType")).title()
            price_type = _text(_field(padded, indexes, "PriceType")).title()
            status = _text(_field(padded, indexes, "Status")).title()
            if match_type not in MATCH_TYPES:
                raise ValueError("unsupported MatchType")
            if price_type not in PRICE_TYPES:
                raise ValueError("unsupported PriceType")
            if status not in STATUSES:
                raise ValueError("unsupported Status")
            discount_text = _text(_field(padded, indexes, "DiscountValue"))
            effective_to_text = _text(_field(padded, indexes, "EffectiveTo"))
            rule = CustomerPriceRule(
                source_row=row_number,
                price_id=_text(_field(padded, indexes, "PriceID")),
                customer_id=_text(_field(padded, indexes, "CustomerID")),
                item_rule=_text(_field(padded, indexes, "ItemRule")),
                match_type=match_type,
                excluded_items=tuple(
                    part.strip()
                    for part in _text(_field(padded, indexes, "ExcludedItems"))
                    .replace("\n", ",")
                    .split(",")
                    if part.strip()
                ),
                price_type=price_type,
                price=_decimal(_field(padded, indexes, "Price")),
                no_discount=_bool(_field(padded, indexes, "NoDiscount")),
                has_discount=_bool(_field(padded, indexes, "HasDiscount")),
                discount_value=_discount_decimal(discount_text) if discount_text else None,
                notes=_text(_field(padded, indexes, "Notes")) or None,
                effective_from=_date(_field(padded, indexes, "EffectiveFrom")),
                effective_to=_date(effective_to_text) if effective_to_text else None,
                status=status,
            )
            if rule.effective_to and rule.effective_to < rule.effective_from:
                raise ValueError("EffectiveTo is before EffectiveFrom")
            if rule.has_discount and rule.discount_value is None:
                issues.append(ValidationIssue(
                    ValidationLevel.WARNING, "discount_value_missing",
                    "HasDiscount is true but DiscountValue is empty", row_number, "DiscountValue"
                ))
            if not rule.has_discount and rule.discount_value is not None:
                issues.append(ValidationIssue(
                    ValidationLevel.WARNING, "unexpected_discount_value",
                    "DiscountValue is set while HasDiscount is false", row_number, "DiscountValue"
                ))
            rules.append(rule)
        except ValueError as exc:
            issues.append(ValidationIssue(
                ValidationLevel.BLOCKING, "invalid_value", str(exc), row_number
            ))
    rules.sort(
        key=lambda item: (item.customer_id.casefold(), item.item_rule.casefold(), item.price_id)
    )
    return CustomerPriceSnapshot(retrieved_at, source_row_count, tuple(rules), tuple(issues))


class GoogleSheetsCustomerPriceProvider(CustomerPriceProvider):
    def __init__(
        self,
        gateway: SheetsGateway,
        spreadsheet_id: str,
        *,
        cache_ttl_seconds: int = 60,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        if not spreadsheet_id:
            raise SourceUnavailableError("Customer Price Manager spreadsheet is not configured")
        self._gateway = gateway
        self._spreadsheet_id = spreadsheet_id
        self._ttl = timedelta(seconds=cache_ttl_seconds)
        self._clock = clock
        self._snapshot: CustomerPriceSnapshot | None = None
        self._last_error: str | None = None
        self._lock = RLock()

    def refresh(self) -> CustomerPriceSnapshot:
        with self._lock:
            try:
                values = self._gateway.read_values(self._spreadsheet_id, PRICE_RULES_RANGE)
                snapshot = normalize_price_rules(values, retrieved_at=self._clock())
            except Exception as exc:
                self._last_error = "Customer Price Manager retrieval failed"
                raise SourceUnavailableError(self._last_error) from exc
            self._snapshot = snapshot
            self._last_error = None
            return snapshot

    def _current(self) -> CustomerPriceSnapshot:
        with self._lock:
            if self._snapshot and self._clock() - self._snapshot.retrieved_at < self._ttl:
                return self._snapshot
        return self.refresh()

    def fetch_rules(self, customer_name: str, invoice_date: date) -> CustomerPriceResult:
        snapshot = self._current()
        if snapshot.blocking_count:
            raise BlockingSourceError("Customer Price Manager contains blocking source errors")
        # Source selection only. Match precedence and invoice business logic are Phase 1C.
        rules = tuple(
            rule for rule in snapshot.rules
            if rule.customer_id.casefold() == customer_name.strip().casefold()
            and rule.status == "Active"
            and rule.effective_from <= invoice_date
            and (rule.effective_to is None or invoice_date <= rule.effective_to)
        )
        return CustomerPriceResult(customer_name.strip(), snapshot.retrieved_at, rules)

    def health(self) -> SourceHealth:
        if self._last_error:
            snapshot = self._snapshot
            return SourceHealth(
                SourceState.UNAVAILABLE,
                snapshot.retrieved_at if snapshot else None,
                snapshot.source_row_count if snapshot else None,
                snapshot.warning_count if snapshot else 0,
                snapshot.blocking_count if snapshot else 0,
                self._last_error,
            )
        if self._snapshot is None:
            return SourceHealth(SourceState.UNAVAILABLE, None, None, 0, 0, "Not checked")
        snapshot = self._snapshot
        return SourceHealth(
            SourceState.CONNECTED,
            snapshot.retrieved_at,
            snapshot.source_row_count,
            snapshot.warning_count,
            snapshot.blocking_count,
            "Source validation blocked" if snapshot.blocking_count else "Source validation passed",
        )


def build_customer_price_provider(settings: Settings) -> GoogleSheetsCustomerPriceProvider:
    gateway = GoogleSheetsGateway(settings)
    return GoogleSheetsCustomerPriceProvider(
        gateway,
        settings.google_customer_price_spreadsheet_id,
        cache_ttl_seconds=settings.customer_price_cache_ttl_seconds,
    )
