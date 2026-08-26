from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from app.integrations.customer_price.provider import CustomerPriceRule


class MatchStatus(StrEnum):
    EXACT = "exact"
    PREFIX = "prefix"
    NO_MATCH = "no_match"
    CUSTOMER_AMBIGUOUS = "customer_ambiguous"
    RULE_CONFLICT = "rule_conflict"


class ValidationState(StrEnum):
    VALID = "valid"
    MANUAL_REVIEW = "manual_review"


class VisualIntent(StrEnum):
    REGULAR = "regular"
    CARTON = "carton"
    DISCOUNT = "discount"
    NO_DISCOUNT = "no_discount"
    NOTES = "notes"
    PRICE_ZERO_MANUAL_REVIEW = "price_zero_manual_review"


@dataclass(frozen=True)
class InvoiceContext:
    invoice_number: str
    invoice_date: date
    invoice_customer: str
    explicit_customer: str | None = None
    monetary_quantum: Decimal = Decimal("0.01")


@dataclass(frozen=True)
class InvoiceLine:
    sku: str
    quantity: Decimal
    current_price: Decimal
    current_discount: Decimal | None
    current_amount: Decimal


@dataclass(frozen=True)
class RuleCandidate:
    price_id: str
    source_row: int
    item_rule: str
    match_type: str
    price_type: str
    price: Decimal | None
    no_discount: bool | None
    has_discount: bool | None
    discount_value: Decimal | None
    notes: str | None


@dataclass(frozen=True)
class LineDecision:
    sku: str
    match_status: MatchStatus
    matched_rule_id: str | None
    match_type: str | None
    price_type: str | None
    original_price: Decimal
    final_price: Decimal
    original_discount: Decimal | None
    final_discount: Decimal | None
    original_amount: Decimal
    final_amount: Decimal
    carton: bool
    notes: str | None
    manual_review: bool
    manual_review_reason: str | None
    decision_required: str | None
    candidates: tuple[RuleCandidate, ...]
    price_changed: bool
    discount_changed: bool
    amount_changed: bool
    validation_state: ValidationState
    visual_intents: tuple[VisualIntent, ...]


@dataclass(frozen=True)
class InvoiceDecision:
    invoice_number: str
    invoice_date: date
    resolved_customer: str | None
    retrieved_at: datetime
    lines: tuple[LineDecision, ...]


def _normalized(value: str) -> str:
    return " ".join(value.split()).casefold()


def _candidate(rule: CustomerPriceRule) -> RuleCandidate:
    return RuleCandidate(
        rule.price_id,
        rule.source_row,
        rule.item_rule,
        rule.match_type,
        rule.price_type,
        rule.price,
        rule.no_discount,
        rule.has_discount,
        rule.discount_value,
        rule.notes,
    )


def _rule_key(rule: CustomerPriceRule) -> tuple[str, str, str, int]:
    return (
        _normalized(rule.customer_id),
        _normalized(rule.item_rule),
        rule.price_id,
        rule.source_row,
    )


def _eligible(rule: CustomerPriceRule, invoice_date: date) -> bool:
    return (
        rule.status == "Active"
        and rule.effective_from <= invoice_date
        and (rule.effective_to is None or invoice_date <= rule.effective_to)
    )


def _exact(rule: CustomerPriceRule, sku: str) -> bool:
    return rule.match_type == "Exact" and _normalized(rule.item_rule) == _normalized(sku)


def _prefix(rule: CustomerPriceRule, sku: str) -> bool:
    if rule.match_type != "Prefix":
        return False
    item = rule.item_rule.casefold()
    compared = sku.casefold()
    if compared == item:
        boundary_match = True
    else:
        next_character = compared[len(item) : len(item) + 1]
        boundary_match = compared.startswith(item) and next_character in {"-", "/"}
    if not boundary_match:
        return False
    excluded = {_normalized(value) for value in rule.excluded_items}
    return _normalized(sku) not in excluded


class InvoiceRuleEngine:
    """Pure deterministic decision engine. It performs no I/O and never edits a PDF."""

    def evaluate(
        self,
        context: InvoiceContext,
        lines: tuple[InvoiceLine, ...],
        rules: tuple[CustomerPriceRule, ...],
        *,
        retrieved_at: datetime,
    ) -> InvoiceDecision:
        rules = tuple(sorted(rules, key=_rule_key))
        requested = context.explicit_customer or context.invoice_customer
        customer_names = {
            rule.customer_id
            for rule in rules
            if _normalized(rule.customer_id) == _normalized(requested)
        }
        if len(customer_names) != 1:
            decisions = tuple(
                self._manual(
                    line,
                    MatchStatus.CUSTOMER_AMBIGUOUS,
                    (),
                    "Customer cannot be uniquely resolved",
                    "Confirm the authoritative customer before processing",
                )
                for line in lines
            )
            return InvoiceDecision(
                context.invoice_number, context.invoice_date, None, retrieved_at, decisions
            )

        customer = next(iter(customer_names))
        customer_rules = tuple(
            rule
            for rule in rules
            if _normalized(rule.customer_id) == _normalized(customer)
            and _eligible(rule, context.invoice_date)
        )
        decisions = tuple(self._evaluate_line(context, line, customer_rules) for line in lines)
        return InvoiceDecision(
            context.invoice_number, context.invoice_date, customer, retrieved_at, decisions
        )

    def _evaluate_line(
        self,
        context: InvoiceContext,
        line: InvoiceLine,
        rules: tuple[CustomerPriceRule, ...],
    ) -> LineDecision:
        exact = tuple(rule for rule in rules if _exact(rule, line.sku))
        if exact:
            if len(exact) != 1:
                return self._manual(
                    line,
                    MatchStatus.RULE_CONFLICT,
                    exact,
                    "Multiple valid Exact rules match the SKU",
                    "Choose the authoritative Exact rule",
                )
            return self._apply(context, line, exact[0], MatchStatus.EXACT)

        prefix = tuple(rule for rule in rules if _prefix(rule, line.sku))
        if not prefix:
            return self._no_match(line)
        if len(prefix) != 1:
            return self._manual(
                line,
                MatchStatus.RULE_CONFLICT,
                prefix,
                "Multiple valid Prefix rules match the SKU",
                "Choose the authoritative Prefix rule",
            )
        return self._apply(context, line, prefix[0], MatchStatus.PREFIX)

    def _apply(
        self,
        context: InvoiceContext,
        line: InvoiceLine,
        rule: CustomerPriceRule,
        status: MatchStatus,
    ) -> LineDecision:
        pre_gate_reasons: list[str] = []
        pre_gate_visuals: list[VisualIntent] = []
        if rule.notes:
            pre_gate_reasons.append("Matched rule contains Notes")
            pre_gate_visuals.append(VisualIntent.NOTES)
        if rule.price is None:
            pre_gate_reasons.append("Matched rule Price is blank")
        elif rule.price == 0:
            pre_gate_reasons.append("Matched rule Price is zero")
            pre_gate_visuals.append(VisualIntent.PRICE_ZERO_MANUAL_REVIEW)
        if pre_gate_reasons:
            return self._manual(
                line,
                status,
                (rule,),
                "; ".join(pre_gate_reasons),
                "Review the complete rule and confirm the current Invoice values",
                visual=tuple(pre_gate_visuals),
            )

        discount_error = self._discount_conflict(rule)
        if discount_error:
            return self._manual(
                line,
                status,
                (rule,),
                discount_error,
                "Correct or confirm the structured discount fields",
            )

        final_price = rule.price
        intents = [VisualIntent.CARTON if rule.price_type == "Carton" else VisualIntent.REGULAR]
        if rule.no_discount:
            final_discount: Decimal | None = Decimal("0")
            intents.append(VisualIntent.NO_DISCOUNT)
        elif rule.has_discount:
            final_discount = rule.discount_value
            intents.append(VisualIntent.DISCOUNT)
        else:
            final_discount = line.current_discount

        rate = (final_discount or Decimal("0")) / Decimal("100")
        unrounded = line.quantity * final_price * (Decimal("1") - rate)
        final_amount = unrounded.quantize(context.monetary_quantum, rounding=ROUND_HALF_UP)
        return LineDecision(
            sku=line.sku,
            match_status=status,
            matched_rule_id=rule.price_id,
            match_type=rule.match_type,
            price_type=rule.price_type,
            original_price=line.current_price,
            final_price=final_price,
            original_discount=line.current_discount,
            final_discount=final_discount,
            original_amount=line.current_amount,
            final_amount=final_amount,
            carton=rule.price_type == "Carton",
            notes=None,
            manual_review=False,
            manual_review_reason=None,
            decision_required=None,
            candidates=(_candidate(rule),),
            price_changed=final_price != line.current_price,
            discount_changed=final_discount != line.current_discount,
            amount_changed=final_amount != line.current_amount,
            validation_state=ValidationState.VALID,
            visual_intents=tuple(intents),
        )

    @staticmethod
    def _discount_conflict(rule: CustomerPriceRule) -> str | None:
        conflicts: list[str] = []
        if rule.no_discount is None or rule.has_discount is None:
            conflicts.append("A structured discount boolean is blank")
        if rule.no_discount and rule.has_discount:
            conflicts.append("NoDiscount and HasDiscount are both true")
        if rule.no_discount and rule.discount_value is not None:
            conflicts.append("NoDiscount conflicts with DiscountValue")
        if rule.has_discount and rule.discount_value is None:
            conflicts.append("HasDiscount is true but DiscountValue is blank")
        if not rule.has_discount and rule.discount_value is not None:
            conflicts.append("DiscountValue is present while HasDiscount is false")
        return "; ".join(conflicts) or None

    @staticmethod
    def _no_match(line: InvoiceLine) -> LineDecision:
        return LineDecision(
            sku=line.sku,
            match_status=MatchStatus.NO_MATCH,
            matched_rule_id=None,
            match_type=None,
            price_type=None,
            original_price=line.current_price,
            final_price=line.current_price,
            original_discount=line.current_discount,
            final_discount=line.current_discount,
            original_amount=line.current_amount,
            final_amount=line.current_amount,
            carton=False,
            notes=None,
            manual_review=False,
            manual_review_reason=None,
            decision_required=None,
            candidates=(),
            price_changed=False,
            discount_changed=False,
            amount_changed=False,
            validation_state=ValidationState.VALID,
            visual_intents=(),
        )

    @staticmethod
    def _manual(
        line: InvoiceLine,
        status: MatchStatus,
        rules: tuple[CustomerPriceRule, ...],
        reason: str,
        question: str,
        *,
        visual: tuple[VisualIntent, ...] = (),
    ) -> LineDecision:
        first = rules[0] if len(rules) == 1 else None
        return LineDecision(
            sku=line.sku,
            match_status=status,
            matched_rule_id=first.price_id if first else None,
            match_type=first.match_type if first else None,
            price_type=first.price_type if first else None,
            original_price=line.current_price,
            final_price=line.current_price,
            original_discount=line.current_discount,
            final_discount=line.current_discount,
            original_amount=line.current_amount,
            final_amount=line.current_amount,
            carton=bool(first and first.price_type == "Carton"),
            notes=first.notes if first else None,
            manual_review=True,
            manual_review_reason=reason,
            decision_required=question,
            candidates=tuple(_candidate(rule) for rule in rules),
            price_changed=False,
            discount_changed=False,
            amount_changed=False,
            validation_state=ValidationState.MANUAL_REVIEW,
            visual_intents=visual,
        )
