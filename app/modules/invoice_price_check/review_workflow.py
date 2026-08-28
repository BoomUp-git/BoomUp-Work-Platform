from __future__ import annotations

from dataclasses import replace
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from app.modules.invoice_price_check.rule_engine import (
    InvoiceDecision,
    LineDecision,
    MatchStatus,
    RuleCandidate,
    ValidationState,
    VisualIntent,
)


class ReviewValidationError(ValueError):
    pass


def decision_snapshot(decision: InvoiceDecision) -> dict:
    return {
        "invoice_number": decision.invoice_number,
        "invoice_date": decision.invoice_date.isoformat(),
        "resolved_customer": decision.resolved_customer,
        "retrieved_at": decision.retrieved_at.isoformat(),
        "lines": [_line_snapshot(index, line) for index, line in enumerate(decision.lines)],
    }


def _line_snapshot(index: int, line: LineDecision) -> dict:
    return {
        "index": index,
        "sku": line.sku,
        "match_status": line.match_status.value,
        "matched_rule_id": line.matched_rule_id,
        "match_type": line.match_type,
        "price_type": line.price_type,
        "original_price": str(line.original_price),
        "final_price": str(line.final_price),
        "original_discount": _optional_decimal(line.original_discount),
        "final_discount": _optional_decimal(line.final_discount),
        "original_amount": str(line.original_amount),
        "final_amount": str(line.final_amount),
        "carton": line.carton,
        "notes": line.notes,
        "manual_review": line.manual_review,
        "manual_review_reason": line.manual_review_reason,
        "decision_required": line.decision_required,
        "candidates": [_candidate_snapshot(candidate) for candidate in line.candidates],
        "price_changed": line.price_changed,
        "discount_changed": line.discount_changed,
        "amount_changed": line.amount_changed,
        "validation_state": line.validation_state.value,
        "visual_intents": [intent.value for intent in line.visual_intents],
    }


def _candidate_snapshot(candidate: RuleCandidate) -> dict:
    return {
        "price_id": candidate.price_id,
        "source_row": candidate.source_row,
        "item_rule": candidate.item_rule,
        "match_type": candidate.match_type,
        "price_type": candidate.price_type,
        "price": _optional_decimal(candidate.price),
        "no_discount": candidate.no_discount,
        "has_discount": candidate.has_discount,
        "discount_value": _optional_decimal(candidate.discount_value),
        "notes": candidate.notes,
    }


def _optional_decimal(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def restore_decision(snapshot: dict) -> InvoiceDecision:
    from datetime import date, datetime

    return InvoiceDecision(
        snapshot["invoice_number"],
        date.fromisoformat(snapshot["invoice_date"]),
        snapshot.get("resolved_customer"),
        datetime.fromisoformat(snapshot["retrieved_at"]),
        tuple(_restore_line(row) for row in snapshot["lines"]),
    )


def _restore_line(row: dict) -> LineDecision:
    return LineDecision(
        sku=row["sku"],
        match_status=MatchStatus(row["match_status"]),
        matched_rule_id=row.get("matched_rule_id"),
        match_type=row.get("match_type"),
        price_type=row.get("price_type"),
        original_price=Decimal(row["original_price"]),
        final_price=Decimal(row["final_price"]),
        original_discount=_decimal_or_none(row.get("original_discount")),
        final_discount=_decimal_or_none(row.get("final_discount")),
        original_amount=Decimal(row["original_amount"]),
        final_amount=Decimal(row["final_amount"]),
        carton=bool(row["carton"]),
        notes=row.get("notes"),
        manual_review=bool(row["manual_review"]),
        manual_review_reason=row.get("manual_review_reason"),
        decision_required=row.get("decision_required"),
        candidates=tuple(_restore_candidate(item) for item in row.get("candidates", [])),
        price_changed=bool(row["price_changed"]),
        discount_changed=bool(row["discount_changed"]),
        amount_changed=bool(row["amount_changed"]),
        validation_state=ValidationState(row["validation_state"]),
        visual_intents=tuple(VisualIntent(value) for value in row["visual_intents"]),
    )


def _restore_candidate(row: dict) -> RuleCandidate:
    return RuleCandidate(
        row["price_id"], row["source_row"], row["item_rule"], row["match_type"],
        row["price_type"], _decimal_or_none(row.get("price")), row.get("no_discount"),
        row.get("has_discount"), _decimal_or_none(row.get("discount_value")), row.get("notes"),
    )


def _decimal_or_none(value: str | None) -> Decimal | None:
    return None if value is None else Decimal(value)


def resolve_manual_reviews(
    decision: InvoiceDecision,
    quantities: tuple[Decimal, ...],
    selections: dict[int, dict[str, str | bool]],
) -> tuple[InvoiceDecision, list[dict]]:
    if len(quantities) != len(decision.lines):
        raise ReviewValidationError("发票商品行与审核快照不一致，请重新处理发票。")
    resolved: list[LineDecision] = []
    audit: list[dict] = []
    for index, (line, quantity) in enumerate(zip(decision.lines, quantities, strict=True)):
        if not line.manual_review:
            resolved.append(line)
            continue
        choice = selections.get(index)
        if choice is None:
            raise ReviewValidationError(f"{line.sku} 尚未完成审核。")
        final_line, record = _resolve_line(index, line, quantity, choice)
        resolved.append(final_line)
        audit.append(record)
    return replace(decision, lines=tuple(resolved)), audit


def default_notes_price_candidate(line: LineDecision) -> RuleCandidate | None:
    """Return the sole safe price-table price for a Notes review.

    Price-zero and carton rules retain their higher-priority review semantics.
    An ambiguous set of usable prices must also remain a genuine price choice.
    """
    if (
        not line.notes
        or VisualIntent.PRICE_ZERO_MANUAL_REVIEW in line.visual_intents
    ):
        return None
    candidates = tuple(
        candidate
        for candidate in line.candidates
        if candidate.price not in {None, Decimal("0")}
        and candidate.price_type != "Carton"
    )
    return candidates[0] if len(candidates) == 1 else None


def _resolve_line(index, line, quantity, choice):
    candidate_by_id = {candidate.price_id: candidate for candidate in line.candidates}
    price_choice = str(choice.get("price_choice", ""))
    selected_candidate = None
    if price_choice == "invoice":
        price = line.original_price
    elif price_choice.startswith("candidate:"):
        selected_candidate = candidate_by_id.get(price_choice.split(":", 1)[1])
        if selected_candidate is None or selected_candidate.price in {None, Decimal("0")}:
            raise ReviewValidationError(f"{line.sku} 选择的价格规则无效。")
        price = selected_candidate.price
    elif price_choice == "custom":
        price = _validated_number(choice.get("custom_price"), f"{line.sku} 的确认价格")
        if price < 0:
            raise ReviewValidationError(f"{line.sku} 的确认价格不能小于 0。")
    else:
        raise ReviewValidationError(f"{line.sku} 必须选择一个价格。")

    notes_price = default_notes_price_candidate(line)
    if notes_price is not None and price_choice != f"candidate:{notes_price.price_id}":
        raise ReviewValidationError(f"{line.sku} 包含备注，最终价格必须使用价格表价格。")

    discount_choice = str(choice.get("discount_choice", ""))
    if discount_choice == "retain":
        discount = line.original_discount
    elif discount_choice == "none":
        discount = None
    elif discount_choice.startswith("candidate:"):
        discount_candidate = candidate_by_id.get(discount_choice.split(":", 1)[1])
        if discount_candidate is None:
            raise ReviewValidationError(f"{line.sku} 选择的折扣规则无效。")
        if discount_candidate.no_discount:
            discount = None
        elif discount_candidate.has_discount and discount_candidate.discount_value is not None:
            discount = discount_candidate.discount_value
        else:
            raise ReviewValidationError(f"{line.sku} 的候选规则没有明确折扣。")
    elif discount_choice == "custom":
        discount = _validated_number(choice.get("custom_discount"), f"{line.sku} 的确认折扣")
        if not Decimal("0") <= discount <= Decimal("100"):
            raise ReviewValidationError(f"{line.sku} 的确认折扣必须在 0% 到 100% 之间。")
    else:
        raise ReviewValidationError(f"{line.sku} 必须选择一个折扣处理方式。")

    if line.notes and choice.get("notes_ack") is not True:
        raise ReviewValidationError(f"{line.sku} 必须勾选“我已阅读备注并确认”。")

    multiplier = Decimal("1") - ((discount or Decimal("0")) / Decimal("100"))
    amount = (quantity * price * multiplier).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    intents: list[VisualIntent] = []
    if selected_candidate is not None:
        intents.append(
            VisualIntent.CARTON
            if selected_candidate.price_type == "Carton"
            else VisualIntent.REGULAR
        )
    if line.notes:
        intents.append(VisualIntent.NOTES)
    if discount_choice == "none":
        intents.append(VisualIntent.NO_DISCOUNT)
    elif discount is not None and discount_choice != "retain":
        intents.append(VisualIntent.DISCOUNT)
    final = replace(
        line,
        matched_rule_id=selected_candidate.price_id if selected_candidate else None,
        match_type=selected_candidate.match_type if selected_candidate else line.match_type,
        price_type=selected_candidate.price_type if selected_candidate else None,
        final_price=price,
        final_discount=discount,
        final_amount=amount,
        carton=VisualIntent.CARTON in intents,
        manual_review=False,
        manual_review_reason=None,
        decision_required=None,
        price_changed=price != line.original_price,
        discount_changed=discount != line.original_discount,
        amount_changed=amount != line.original_amount,
        validation_state=ValidationState.VALID,
        visual_intents=tuple(intents),
    )
    return final, {
        "index": index,
        "sku": line.sku,
        "price_choice": price_choice,
        "final_price": str(price),
        "discount_choice": discount_choice,
        "final_discount": _optional_decimal(discount),
        "notes_ack": bool(choice.get("notes_ack")),
    }


def _validated_number(value, label: str) -> Decimal:
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, AttributeError):
        raise ReviewValidationError(f"{label}格式不正确。") from None
