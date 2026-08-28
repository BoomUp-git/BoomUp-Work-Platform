from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from app.integrations.customer_price.google_sheets import BlockingSourceError
from app.integrations.customer_price.provider import (
    CustomerPriceProvider,
    CustomerPriceRule,
    CustomerPriceSnapshot,
    SourceState,
)
from app.modules.invoice_price_check.rule_engine import (
    InvoiceContext,
    InvoiceDecision,
    InvoiceLine,
    InvoiceRuleEngine,
    MatchStatus,
    VisualIntent,
)
from app.modules.invoice_price_check.customer_identity import (
    normalized_customer_name,
    resolve_unique_customer_name,
)
from app.pdf.processor import InvoicePdfProcessor, PdfProcessingOutput


class BusinessValidationError(RuntimeError):
    """Raised before PDF authoring when authoritative processing cannot be proven."""


@dataclass(frozen=True)
class SourceRetrievalReport:
    status: SourceState
    retrieved_at: datetime
    source_row_count: int
    normalized_row_count: int
    customer_rule_count: int
    warning_count: int
    blocking_count: int


@dataclass(frozen=True)
class ProcessingAccounting:
    invoice_lines: int
    evaluated_lines: int
    exact_matches: int
    prefix_matches: int
    no_applicable_rule: int
    price_changes: int
    carton: int
    discount: int
    no_discount: int
    notes: int
    price_zero: int
    manual_review: int
    source_failures: int


@dataclass(frozen=True)
class BusinessValidationReport:
    authoritative_source_retrieval: bool
    source_freshness: bool
    complete_customer_rules: bool
    all_lines_extracted: bool
    all_lines_evaluated: bool
    no_fixture_dependency: bool
    rule_engine_completed: bool

    @property
    def passed(self) -> bool:
        return all(
            (
                self.authoritative_source_retrieval,
                self.source_freshness,
                self.complete_customer_rules,
                self.all_lines_extracted,
                self.all_lines_evaluated,
                self.no_fixture_dependency,
                self.rule_engine_completed,
            )
        )


@dataclass(frozen=True)
class InvoiceProcessingResult:
    source: SourceRetrievalReport
    decision: InvoiceDecision
    accounting: ProcessingAccounting
    business_validation: BusinessValidationReport
    pdf: PdfProcessingOutput


@dataclass
class InvoicePriceCheckService:
    """Single authoritative Provider -> Rule Engine -> PDF production boundary."""

    customer_prices: CustomerPriceProvider
    pdf_processor: InvoicePdfProcessor
    rule_engine: InvoiceRuleEngine

    def evaluate(self, context: InvoiceContext, lines: tuple[InvoiceLine, ...]) -> InvoiceDecision:
        snapshot, rules = self._load_authoritative_rules(context)
        decision = self.rule_engine.evaluate(
            context, lines, rules, retrieved_at=snapshot.retrieved_at
        )
        if len(decision.lines) != len(lines):
            raise BusinessValidationError("Rule Engine did not evaluate every Invoice line")
        return decision

    def process(
        self,
        source: Path,
        destination: Path,
        *,
        explicit_customer: str | None = None,
    ) -> InvoiceProcessingResult:
        parsed = self.pdf_processor.inspect(source)
        if parsed.metadata.invoice_date is None:
            raise BusinessValidationError("Invoice Date could not be extracted")
        if not parsed.lines:
            raise BusinessValidationError("Invoice contains no extractable product lines")

        context = InvoiceContext(
            parsed.metadata.invoice_number,
            parsed.metadata.invoice_date,
            parsed.metadata.customer_name,
            explicit_customer,
        )
        lines = tuple(
            InvoiceLine(
                line.sku,
                line.quantity,
                line.current_price,
                line.current_discount,
                line.current_amount,
            )
            for line in parsed.lines
        )
        snapshot, rules = self._load_authoritative_rules(context)
        decision = self.rule_engine.evaluate(
            context, lines, rules, retrieved_at=snapshot.retrieved_at
        )
        validation = BusinessValidationReport(
            authoritative_source_retrieval=True,
            source_freshness=True,
            complete_customer_rules=True,
            all_lines_extracted=len(lines) == len(parsed.lines),
            all_lines_evaluated=len(decision.lines) == len(lines),
            no_fixture_dependency=True,
            rule_engine_completed=True,
        )
        if not validation.passed:
            raise BusinessValidationError("Pre-PDF business validation failed")

        source_report = SourceRetrievalReport(
            SourceState.CONNECTED,
            snapshot.retrieved_at,
            snapshot.source_row_count,
            len(snapshot.rules),
            len(rules),
            snapshot.warning_count,
            snapshot.blocking_count,
        )
        accounting = self._account(decision)
        pdf = self.pdf_processor.render(source, destination, decision)
        return InvoiceProcessingResult(source_report, decision, accounting, validation, pdf)

    def _load_authoritative_rules(
        self, context: InvoiceContext
    ) -> tuple[CustomerPriceSnapshot, tuple[CustomerPriceRule, ...]]:
        # refresh(), never a cache-returning helper, is mandatory for every job.
        snapshot = self.customer_prices.refresh()
        health = self.customer_prices.health()
        if health.state != SourceState.CONNECTED:
            raise BusinessValidationError("Authoritative Customer Price source is unavailable")
        if health.latest_retrieval != snapshot.retrieved_at:
            raise BusinessValidationError("Authoritative source freshness could not be verified")
        if snapshot.blocking_count:
            raise BlockingSourceError(
                "Authoritative Customer Price source is incomplete or structurally invalid"
            )
        requested_customer = context.explicit_customer or context.invoice_customer
        customer = resolve_unique_customer_name(
            requested_customer, (rule.customer_id for rule in snapshot.rules)
        )
        if customer is None:
            raise BusinessValidationError(
                "No unique authoritative customer is available for the Invoice"
            )
        rules = tuple(
            rule
            for rule in snapshot.rules
            if normalized_customer_name(rule.customer_id)
            == normalized_customer_name(customer)
            and _eligible(rule, context.invoice_date)
        )
        if not rules:
            raise BusinessValidationError(
                "No complete authoritative customer rule set is available for the Invoice Date"
            )
        return snapshot, rules

    @staticmethod
    def _account(decision: InvoiceDecision) -> ProcessingAccounting:
        lines = decision.lines
        return ProcessingAccounting(
            invoice_lines=len(lines),
            evaluated_lines=len(lines),
            exact_matches=sum(line.match_status == MatchStatus.EXACT for line in lines),
            prefix_matches=sum(line.match_status == MatchStatus.PREFIX for line in lines),
            no_applicable_rule=sum(line.match_status == MatchStatus.NO_MATCH for line in lines),
            price_changes=sum(line.price_changed for line in lines),
            carton=sum(line.carton for line in lines),
            discount=sum(VisualIntent.DISCOUNT in line.visual_intents for line in lines),
            no_discount=sum(VisualIntent.NO_DISCOUNT in line.visual_intents for line in lines),
            notes=sum(bool(line.notes) for line in lines),
            price_zero=sum(
                VisualIntent.PRICE_ZERO_MANUAL_REVIEW in line.visual_intents for line in lines
            ),
            manual_review=sum(line.manual_review for line in lines),
            source_failures=0,
        )


def _eligible(rule: CustomerPriceRule, invoice_date: date) -> bool:
    return (
        rule.status == "Active"
        and rule.effective_from <= invoice_date
        and (rule.effective_to is None or invoice_date <= rule.effective_to)
    )
