from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.integrations.customer_price.provider import CustomerPriceRule
from app.modules.invoice_price_check.rule_engine import (
    InvoiceContext,
    InvoiceLine,
    InvoiceRuleEngine,
    MatchStatus,
    ValidationState,
    VisualIntent,
)

NOW = datetime(2026, 8, 26, tzinfo=UTC)


def rule(**changes) -> CustomerPriceRule:
    values = {
        "source_row": 2,
        "price_id": "rule-1",
        "customer_id": "Example Customer",
        "item_rule": "ABC001",
        "match_type": "Exact",
        "excluded_items": (),
        "price_type": "Regular",
        "price": Decimal("5.00"),
        "no_discount": False,
        "has_discount": False,
        "discount_value": None,
        "notes": None,
        "effective_from": date(2026, 1, 1),
        "effective_to": None,
        "status": "Active",
    }
    values.update(changes)
    return CustomerPriceRule(**values)


def line(**changes) -> InvoiceLine:
    values = {
        "sku": "ABC001",
        "quantity": Decimal("2"),
        "current_price": Decimal("4.00"),
        "current_discount": None,
        "current_amount": Decimal("8.00"),
    }
    values.update(changes)
    return InvoiceLine(**values)


def context(**changes) -> InvoiceContext:
    values = {
        "invoice_number": "SYNTHETIC-001",
        "invoice_date": date(2026, 8, 1),
        "invoice_customer": "Example Customer",
    }
    values.update(changes)
    return InvoiceContext(**values)


def decide(rules, invoice_line=None, invoice_context=None):
    result = InvoiceRuleEngine().evaluate(
        invoice_context or context(),
        (invoice_line or line(),),
        tuple(rules),
        retrieved_at=NOW,
    )
    return result.lines[0]


def assert_preserved(decision):
    assert decision.final_price == decision.original_price
    assert decision.final_discount == decision.original_discount
    assert decision.final_amount == decision.original_amount
    assert not decision.price_changed
    assert not decision.discount_changed
    assert not decision.amount_changed


def test_one_exact_rule_is_applied():
    decision = decide([rule()])
    assert decision.match_status == MatchStatus.EXACT
    assert decision.final_price == Decimal("5.00")
    assert decision.final_amount == Decimal("10.00")


def test_unique_reordered_customer_name_is_resolved_before_sku_matching():
    customer_rule = rule(customer_id="Solly's (TOBYZEB PL Cairns)")
    result = InvoiceRuleEngine().evaluate(
        context(invoice_customer="Solly's (Cairns)TOBYZEB P/L"),
        (line(),),
        (customer_rule,),
        retrieved_at=NOW,
    )
    assert result.resolved_customer == "Solly's (TOBYZEB PL Cairns)"
    assert result.lines[0].match_status == MatchStatus.EXACT


def test_multiple_exact_rules_preserve_all_candidates_for_manual_review():
    decision = decide([rule(price_id="b", price=Decimal("6")), rule(price_id="a")])
    assert decision.manual_review
    assert decision.match_status == MatchStatus.RULE_CONFLICT
    assert [candidate.price_id for candidate in decision.candidates] == ["a", "b"]
    assert_preserved(decision)


def test_candidate_order_is_deterministic_independent_of_input_order():
    rules = [rule(price_id="z"), rule(price_id="a")]
    forward = decide(rules)
    reverse = decide(list(reversed(rules)))
    assert forward.candidates == reverse.candidates
    assert [candidate.price_id for candidate in forward.candidates] == ["a", "z"]


def test_exact_stops_prefix_evaluation():
    exact = rule(price_id="exact")
    prefix = rule(price_id="prefix", item_rule="ABC001", match_type="Prefix", price=Decimal("9"))
    decision = decide([prefix, exact])
    assert decision.matched_rule_id == "exact"
    assert decision.final_price == Decimal("5.00")


@pytest.mark.parametrize("sku", ["ABC001", "ABC001-RED", "ABC001/RED"])
def test_prefix_accepts_sku_end_or_separator(sku):
    decision = decide([rule(match_type="Prefix")], invoice_line=line(sku=sku))
    assert decision.match_status == MatchStatus.PREFIX
    assert not decision.manual_review


@pytest.mark.parametrize("sku", ["ABC001A", "ABC0012"])
def test_prefix_rejects_direct_letter_or_number_continuation(sku):
    decision = decide([rule(match_type="Prefix")], invoice_line=line(sku=sku))
    assert decision.match_status == MatchStatus.NO_MATCH
    assert not decision.manual_review
    assert decision.validation_state == ValidationState.VALID
    assert_preserved(decision)


def test_prefix_excluded_item_is_not_eligible():
    decision = decide(
        [rule(match_type="Prefix", excluded_items=("ABC001-RED",))],
        invoice_line=line(sku="abc001-red"),
    )
    assert decision.match_status == MatchStatus.NO_MATCH
    assert not decision.manual_review
    assert_preserved(decision)


def test_no_valid_rule_preserves_invoice_values_without_manual_review():
    decision = decide([rule(item_rule="OTHER")])
    assert decision.match_status == MatchStatus.NO_MATCH
    assert not decision.manual_review
    assert decision.manual_review_reason is None
    assert decision.decision_required is None
    assert decision.candidates == ()
    assert_preserved(decision)


def test_multiple_prefix_rules_require_manual_review():
    rules = [
        rule(price_id="a", match_type="Prefix", item_rule="ABC001"),
        rule(price_id="b", match_type="Prefix", item_rule="ABC001"),
    ]
    decision = decide(rules, invoice_line=line(sku="ABC001-RED"))
    assert decision.manual_review
    assert len(decision.candidates) == 2


def test_unique_longest_prefix_rule_wins_over_parent_prefix():
    rules = [
        rule(
            price_id="parent",
            match_type="Prefix",
            item_rule="ASH003",
            price=Decimal("2.80"),
        ),
        rule(
            price_id="child",
            match_type="Prefix",
            item_rule="ASH003-KA",
            price=Decimal("2.30"),
            price_type="Clearance",
        ),
    ]

    decision = decide(rules, invoice_line=line(sku="ASH003-KA/P"))

    assert not decision.manual_review
    assert decision.matched_rule_id == "child"
    assert decision.final_price == Decimal("2.30")
    assert decision.price_type == "Clearance"
    assert [candidate.price_id for candidate in decision.candidates] == ["child"]


def test_longest_prefix_still_obeys_carton_manual_review_gate():
    rules = [
        rule(price_id="parent", match_type="Prefix", item_rule="PEN016"),
        rule(
            price_id="child",
            match_type="Prefix",
            item_rule="PEN016-NA",
            price=Decimal("1.40"),
            price_type="Carton",
        ),
    ]

    decision = decide(rules, invoice_line=line(sku="PEN016-NA/Koala"))

    assert decision.manual_review
    assert decision.matched_rule_id == "child"
    assert decision.carton
    assert_preserved(decision)


def test_duplicate_longest_prefix_rules_remain_manual_review():
    rules = [
        rule(price_id="parent", match_type="Prefix", item_rule="PEN016"),
        rule(
            price_id="child-a",
            match_type="Prefix",
            item_rule="PEN016-NA",
            price=Decimal("1.30"),
        ),
        rule(
            price_id="child-b",
            match_type="Prefix",
            item_rule="PEN016-NA",
            price=Decimal("1.40"),
        ),
    ]

    decision = decide(rules, invoice_line=line(sku="PEN016-NA/Koala"))

    assert decision.manual_review
    assert [candidate.price_id for candidate in decision.candidates] == [
        "child-a",
        "child-b",
    ]
    assert_preserved(decision)


@pytest.mark.parametrize(
    ("changes", "invoice_date", "eligible"),
    [
        ({"status": "Inactive"}, date(2026, 8, 1), False),
        ({"effective_from": date(2026, 8, 2)}, date(2026, 8, 1), False),
        ({"effective_from": date(2026, 8, 1)}, date(2026, 8, 1), True),
        ({"effective_to": date(2026, 7, 31)}, date(2026, 8, 1), False),
        ({"effective_to": date(2026, 8, 1)}, date(2026, 8, 1), True),
    ],
)
def test_invoice_date_and_status_eligibility(changes, invoice_date, eligible):
    decision = decide([rule(**changes)], invoice_context=context(invoice_date=invoice_date))
    assert (decision.match_status == MatchStatus.EXACT) is eligible


def test_explicit_customer_overrides_invoice_customer():
    decision = decide(
        [rule(customer_id="Chosen Customer")],
        invoice_context=context(
            invoice_customer="Wrong Customer", explicit_customer="  chosen   customer "
        ),
    )
    assert not decision.manual_review


def test_unresolved_customer_is_invoice_safe_manual_review():
    decision = decide([rule()], invoice_context=context(invoice_customer="Unknown"))
    assert decision.match_status == MatchStatus.CUSTOMER_AMBIGUOUS
    assert_preserved(decision)


def test_customer_case_variants_are_ambiguous_not_guessed():
    decision = decide(
        [
            rule(price_id="a", customer_id="Example Customer"),
            rule(price_id="b", customer_id="EXAMPLE CUSTOMER"),
        ]
    )
    assert decision.match_status == MatchStatus.CUSTOMER_AMBIGUOUS


@pytest.mark.parametrize("notes", ["NO DISCOUNT", "ordinary text", "完整内容"])
def test_any_non_empty_notes_force_manual_review_and_are_preserved(notes):
    decision = decide([rule(notes=notes)])
    assert decision.manual_review
    assert decision.notes == notes
    assert decision.visual_intents == (VisualIntent.NOTES,)
    assert_preserved(decision)


def test_blank_notes_do_not_force_review():
    assert not decide([rule(notes=None)]).manual_review


def test_blank_price_forces_manual_review():
    decision = decide([rule(price=None)])
    assert decision.manual_review
    assert "blank" in decision.manual_review_reason
    assert_preserved(decision)


def test_zero_price_forces_manual_review_with_visual_intent():
    decision = decide([rule(price=Decimal("0"))])
    assert decision.manual_review
    assert decision.visual_intents == (VisualIntent.PRICE_ZERO_MANUAL_REVIEW,)
    assert_preserved(decision)


def test_notes_and_zero_price_preserve_both_manual_review_intents():
    decision = decide([rule(price=Decimal("0"), notes="Keep this complete")])
    assert decision.visual_intents == (
        VisualIntent.NOTES,
        VisualIntent.PRICE_ZERO_MANUAL_REVIEW,
    )
    assert "Notes" in decision.manual_review_reason
    assert "zero" in decision.manual_review_reason


@pytest.mark.parametrize(
    "changes",
    [
        {"no_discount": True, "has_discount": True},
        {"no_discount": True, "discount_value": Decimal("10")},
        {"has_discount": True, "discount_value": None},
        {"has_discount": False, "discount_value": Decimal("10")},
        {"no_discount": None},
        {"has_discount": None},
    ],
)
def test_structured_discount_conflicts_force_manual_review(changes):
    decision = decide([rule(**changes)])
    assert decision.manual_review
    assert_preserved(decision)


def test_no_discount_cancels_invoice_discount_and_recalculates():
    decision = decide(
        [rule(no_discount=True)],
        invoice_line=line(current_discount=Decimal("10"), current_amount=Decimal("7.20")),
    )
    assert decision.final_discount == Decimal("0")
    assert decision.final_amount == Decimal("10.00")
    assert VisualIntent.NO_DISCOUNT in decision.visual_intents


@pytest.mark.parametrize("discount", [Decimal("0"), Decimal("20")])
def test_explicit_discount_value_is_applied_including_zero(discount):
    decision = decide([rule(has_discount=True, discount_value=discount)])
    assert decision.final_discount == discount
    assert VisualIntent.DISCOUNT in decision.visual_intents


def test_no_structured_discount_override_preserves_invoice_discount():
    decision = decide([rule()], invoice_line=line(current_discount=Decimal("5")))
    assert decision.final_discount == Decimal("5")


@pytest.mark.parametrize("price_type", ["Regular", "Clearance", "Special"])
def test_non_carton_prices_have_regular_visual_intent(price_type):
    decision = decide([rule(price_type=price_type)])
    assert decision.visual_intents[0] == VisualIntent.REGULAR
    assert not decision.carton


def test_carton_price_always_requires_manual_review_and_preserves_invoice_values():
    decision = decide([rule(price_type="Carton", price=Decimal("4.00"))])
    assert decision.carton
    assert decision.manual_review
    assert not decision.price_changed
    assert decision.visual_intents == (VisualIntent.CARTON,)
    assert "Carton Price" in decision.manual_review_reason
    assert_preserved(decision)


def test_carton_with_notes_preserves_both_review_intents():
    decision = decide([rule(price_type="Carton", notes="Confirm carton quantity")])
    assert decision.manual_review
    assert decision.visual_intents == (VisualIntent.CARTON, VisualIntent.NOTES)
    assert "Carton Price" in decision.manual_review_reason
    assert "Notes" in decision.manual_review_reason
    assert_preserved(decision)


def test_amount_uses_quantity_discount_and_full_decimal_precision():
    decision = decide(
        [rule(price=Decimal("3.333"), has_discount=True, discount_value=Decimal("10"))],
        invoice_line=line(quantity=Decimal("3")),
    )
    assert decision.final_amount == Decimal("9.00")


def test_amount_rounds_half_up_only_at_final_monetary_precision():
    decision = decide([rule(price=Decimal("1.005"))], invoice_line=line(quantity=Decimal("1")))
    assert decision.final_amount == Decimal("1.01")


def test_custom_invoice_monetary_precision_is_preserved():
    decision = decide(
        [rule(price=Decimal("1.2345"))],
        invoice_context=context(monetary_quantum=Decimal("0.001")),
    )
    assert decision.final_amount == Decimal("2.469")


def test_decision_model_has_no_invoice_bottom_total_field():
    decision = InvoiceRuleEngine().evaluate(context(), (line(),), (rule(),), retrieved_at=NOW)
    assert not hasattr(decision, "new_invoice_total")
    assert not hasattr(decision, "subtotal")
    assert not hasattr(decision, "gst")


def test_synthetic_representative_invoice_decision_fixture():
    lines = (
        line(sku="REG-1", current_price=Decimal("4"), current_amount=Decimal("8")),
        line(sku="BOX-1", current_price=Decimal("8"), current_amount=Decimal("16")),
        line(sku="NOTE-1", current_price=Decimal("2"), current_amount=Decimal("4")),
    )
    rules = (
        rule(price_id="regular", item_rule="REG-1", price=Decimal("5")),
        rule(
            price_id="carton",
            item_rule="BOX-1",
            price=Decimal("8"),
            price_type="Carton",
            no_discount=True,
        ),
        rule(price_id="notes", item_rule="NOTE-1", price=Decimal("3"), notes="Review me"),
    )
    result = InvoiceRuleEngine().evaluate(context(), lines, rules, retrieved_at=NOW)
    assert [item.matched_rule_id for item in result.lines] == ["regular", "carton", "notes"]
    assert result.lines[0].final_amount == Decimal("10.00")
    assert result.lines[1].carton
    assert result.lines[2].manual_review
    assert result.lines[2].validation_state == ValidationState.MANUAL_REVIEW
