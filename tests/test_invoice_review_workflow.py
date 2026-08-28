from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.integrations.customer_price.provider import CustomerPriceRule
from app.modules.invoice_price_check.review_workflow import (
    ReviewValidationError,
    decision_snapshot,
    resolve_manual_reviews,
    restore_decision,
)
from app.modules.invoice_price_check.rule_engine import (
    InvoiceContext,
    InvoiceLine,
    InvoiceRuleEngine,
    VisualIntent,
)


def _notes_decision():
    rule = CustomerPriceRule(
        2, "notes-rule", "Example Customer", "ABC-1", "Exact", (), "Regular",
        Decimal("3.20"), False, False, None, "CALL CUSTOMER", date(2026, 1, 1),
        None, "Active",
    )
    return InvoiceRuleEngine().evaluate(
        InvoiceContext("INV-1", date(2026, 8, 1), "Example Customer"),
        (InvoiceLine("ABC-1", Decimal("3"), Decimal("3.30"), Decimal("10"), Decimal("8.91")),),
        (rule,),
        retrieved_at=datetime(2026, 8, 28, tzinfo=UTC),
    )


def test_snapshot_round_trip_and_notes_ack_are_mandatory():
    decision = restore_decision(decision_snapshot(_notes_decision()))
    with pytest.raises(ReviewValidationError, match="已阅读备注"):
        resolve_manual_reviews(
            decision,
            (Decimal("3"),),
            {0: {"price_choice": "invoice", "discount_choice": "custom", "custom_discount": "15", "notes_ack": False}},
        )


def test_notes_require_invoice_price_and_an_explicit_discount():
    decision = restore_decision(decision_snapshot(_notes_decision()))
    with pytest.raises(ReviewValidationError, match="保留发票价格"):
        resolve_manual_reviews(
            decision,
            (Decimal("3"),),
            {0: {"price_choice": "candidate:notes-rule", "discount_choice": "custom", "custom_discount": "0", "notes_ack": True}},
        )
    with pytest.raises(ReviewValidationError, match="人工确认后的折扣"):
        resolve_manual_reviews(
            decision,
            (Decimal("3"),),
            {0: {"price_choice": "invoice", "discount_choice": "retain", "notes_ack": True}},
        )


def test_review_resolution_recalculates_amount_round_half_up():
    decision = restore_decision(decision_snapshot(_notes_decision()))
    final, audit = resolve_manual_reviews(
        decision,
        (Decimal("3"),),
        {0: {"price_choice": "invoice", "discount_choice": "custom", "custom_discount": "15", "notes_ack": True}},
    )
    line = final.lines[0]
    assert line.final_price == Decimal("3.30")
    assert line.final_discount == Decimal("15")
    assert line.final_amount == Decimal("8.42")
    assert not line.manual_review
    assert VisualIntent.NOTES in line.visual_intents
    assert VisualIntent.DISCOUNT in line.visual_intents
    assert audit[0]["notes_ack"] is True
