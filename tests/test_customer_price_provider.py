from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from app.integrations.customer_price.google_sheets import (
    GoogleSheetsCustomerPriceProvider,
    SourceUnavailableError,
    normalize_price_rules,
)
from app.integrations.customer_price.provider import SourceState, ValidationLevel

HEADERS = [
    "PriceID",
    "CustomerID",
    "ItemRule",
    "MatchType",
    "ExcludedItems",
    "PriceType",
    "Price",
    "NoDiscount",
    "EffectiveFrom",
    "EffectiveTo",
    "Status",
    "CreatedBy",
    "CreatedAt",
    "BatchID",
    "HasDiscount",
    "DiscountValue",
    "Notes",
    "EntrySource",
    "NeedsClearanceReview",
    "ReviewFrequencyMonths",
    "NextReviewDate",
    "LastReviewedAt",
]


def row(**changes):
    values = {
        "PriceID": "p1",
        "CustomerID": "ANTK Souvenirs",
        "ItemRule": "ABC",
        "MatchType": "Exact",
        "ExcludedItems": "",
        "PriceType": "Regular",
        "Price": "$12.30",
        "NoDiscount": "FALSE",
        "EffectiveFrom": "2026-07-28",
        "EffectiveTo": "",
        "Status": "Active",
        "HasDiscount": "FALSE",
        "DiscountValue": "",
        "Notes": "",
        "CreatedBy": "",
        "CreatedAt": "",
        "BatchID": "",
        "EntrySource": "Manual",
        "NeedsClearanceReview": "",
        "ReviewFrequencyMonths": "",
        "NextReviewDate": "",
        "LastReviewedAt": "",
    }
    values.update(changes)
    return [values.get(header, "") for header in HEADERS]


class Gateway:
    def __init__(self, values=None, error=None):
        self.values = values or [HEADERS, row()]
        self.error = error
        self.calls = 0

    def read_values(self, spreadsheet_id, range_name):
        self.calls += 1
        assert spreadsheet_id == "sheet-id"
        assert range_name == "PriceRules!A:V"
        if self.error:
            raise self.error
        return self.values


@pytest.mark.parametrize("match_type", ["Exact", "Prefix"])
def test_normalizes_supported_match_types(match_type):
    snapshot = normalize_price_rules([HEADERS, row(MatchType=match_type)])
    assert snapshot.rules[0].match_type == match_type
    assert snapshot.blocking_count == 0


@pytest.mark.parametrize("price_type", ["Regular", "Carton", "Clearance"])
def test_normalizes_supported_price_types(price_type):
    snapshot = normalize_price_rules([HEADERS, row(PriceType=price_type)])
    assert snapshot.rules[0].price_type == price_type


def test_decimal_is_exact_and_currency_is_removed():
    result = normalize_price_rules([HEADERS, row(Price="$1,234.50")])
    assert result.rules[0].price == Decimal("1234.50")


def test_explicit_zero_discount_is_distinct_from_empty():
    snapshot = normalize_price_rules([HEADERS, row(DiscountValue="0")])
    assert snapshot.rules[0].discount_value == Decimal("0")
    assert snapshot.warning_count == 1
    empty = normalize_price_rules([HEADERS, row()])
    assert empty.rules[0].discount_value is None


def test_percentage_discount_is_normalized_to_decimal_percentage_points():
    snapshot = normalize_price_rules([HEADERS, row(HasDiscount="TRUE", DiscountValue="10%")])
    assert snapshot.rules[0].discount_value == Decimal("10")
    assert snapshot.warning_count == 0


def test_boolean_normalization_is_deterministic():
    rule = normalize_price_rules([HEADERS, row(NoDiscount="YES", HasDiscount="1")]).rules[0]
    assert rule.no_discount is True
    assert rule.has_discount is True


def test_invalid_boolean_is_blocking():
    snapshot = normalize_price_rules([HEADERS, row(NoDiscount="maybe")])
    assert snapshot.blocking_count == 1
    assert not snapshot.rules


def test_business_date_formats_are_supported():
    rule = normalize_price_rules([HEADERS, row(EffectiveFrom="2026/7/28")]).rules[0]
    assert rule.effective_from == date(2026, 7, 28)


def test_invalid_date_is_blocking():
    assert normalize_price_rules([HEADERS, row(EffectiveFrom="28/7/26")]).blocking_count == 1


def test_end_date_before_start_is_blocking():
    snapshot = normalize_price_rules([HEADERS, row(EffectiveTo="2026-07-27")])
    assert snapshot.blocking_count == 1


def test_missing_headers_are_blocking():
    snapshot = normalize_price_rules([["PriceID"], ["p1"]])
    assert snapshot.issues[0].code == "missing_headers"
    assert snapshot.issues[0].level == ValidationLevel.BLOCKING


def test_residual_row_without_rule_identity_is_ignored():
    snapshot = normalize_price_rules([HEADERS, row(PriceID="", CustomerID="", ItemRule="")])
    assert snapshot.source_row_count == 0
    assert snapshot.blocking_count == 0


def test_exclusions_are_trimmed_and_normalized():
    rule = normalize_price_rules([HEADERS, row(ExcludedItems=" A, B\nC ")]).rules[0]
    assert rule.excluded_items == ("A", "B", "C")


def test_rules_have_deterministic_order():
    snapshot = normalize_price_rules(
        [HEADERS, row(PriceID="z", CustomerID="B"), row(PriceID="a", CustomerID="A")]
    )
    assert [rule.customer_id for rule in snapshot.rules] == ["A", "B"]


def test_true_discount_without_value_is_warning_not_blocking():
    snapshot = normalize_price_rules([HEADERS, row(HasDiscount="TRUE")])
    assert snapshot.warning_count == 1
    assert snapshot.blocking_count == 0


def test_refresh_reports_connected_health():
    provider = GoogleSheetsCustomerPriceProvider(Gateway(), "sheet-id")
    provider.refresh()
    health = provider.health()
    assert health.state == SourceState.CONNECTED
    assert health.row_count == 1


def test_retrieval_failure_is_fail_closed_and_health_is_unavailable():
    gateway = Gateway(error=OSError("secret detail"))
    provider = GoogleSheetsCustomerPriceProvider(gateway, "sheet-id")
    with pytest.raises(SourceUnavailableError, match="retrieval failed"):
        provider.refresh()
    assert provider.health().state == SourceState.UNAVAILABLE
    assert "secret detail" not in provider.health().message


def test_expired_cache_failure_does_not_return_stale_data():
    now = datetime(2026, 8, 26, tzinfo=UTC)
    gateway = Gateway()
    provider = GoogleSheetsCustomerPriceProvider(
        gateway, "sheet-id", cache_ttl_seconds=10, clock=lambda: now
    )
    provider.refresh()
    gateway.error = OSError("offline")
    provider._clock = lambda: now + timedelta(seconds=11)
    with pytest.raises(SourceUnavailableError):
        provider.fetch_rules("ANTK Souvenirs", date(2026, 8, 1))


def test_valid_cache_avoids_second_retrieval():
    gateway = Gateway()
    provider = GoogleSheetsCustomerPriceProvider(gateway, "sheet-id", cache_ttl_seconds=60)
    provider.refresh()
    provider.fetch_rules("ANTK Souvenirs", date(2026, 8, 1))
    assert gateway.calls == 1


def test_blank_price_is_available_for_rule_level_manual_review():
    provider = GoogleSheetsCustomerPriceProvider(Gateway([HEADERS, row(Price="")]), "sheet-id")
    provider.refresh()
    result = provider.fetch_rules("ANTK Souvenirs", date(2026, 8, 1))
    assert result.rules[0].price is None


def test_fetch_only_selects_customer_status_and_effective_window():
    gateway = Gateway(
        [
            HEADERS,
            row(PriceID="keep", EffectiveTo="2026-08-31"),
            row(PriceID="other", CustomerID="Other"),
            row(PriceID="inactive", Status="Inactive"),
            row(PriceID="future", EffectiveFrom="2026-09-01"),
        ]
    )
    result = GoogleSheetsCustomerPriceProvider(gateway, "sheet-id").fetch_rules(
        "antk souvenirs", date(2026, 8, 1)
    )
    assert [rule.price_id for rule in result.rules] == ["keep"]
