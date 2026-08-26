from __future__ import annotations

import hashlib
import re
from abc import ABC, abstractmethod
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pymupdf

from app.modules.invoice_price_check.rule_engine import InvoiceDecision, LineDecision, VisualIntent

YELLOW = (1.0, 242 / 255, 140 / 255)
GREEN = (205 / 255, 236 / 255, 207 / 255)
PURPLE = (225 / 255, 199 / 255, 246 / 255)
RED = (1.0, 199 / 255, 206 / 255)
BLACK = (0.0, 0.0, 0.0)
RETENTION_DAYS = 30
PDF_FONT_NAME = "Phase1DHelvetica"
PDF_FONT = pymupdf.Font("helv")


@dataclass(frozen=True)
class Box:
    x0: float
    y0: float
    x1: float
    y1: float

    def rect(self, padding: float = 0) -> pymupdf.Rect:
        return pymupdf.Rect(
            self.x0 - padding, self.y0 - padding, self.x1 + padding, self.y1 + padding
        )


@dataclass(frozen=True)
class InvoicePdfMetadata:
    invoice_number: str
    customer_name: str
    invoice_date: date | None
    page_count: int
    page_sizes: tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class ParsedInvoiceLine:
    index: int
    page_index: int
    page_line_index: int
    sku: str
    quantity: Decimal
    current_price: Decimal
    current_discount: Decimal | None
    current_amount: Decimal
    item_box: Box
    price_box: Box
    discount_box: Box
    amount_box: Box


@dataclass(frozen=True)
class ParsedInvoice:
    metadata: InvoicePdfMetadata
    lines: tuple[ParsedInvoiceLine, ...]
    protected_totals: tuple[str, ...]
    source_sha256: str


@dataclass(frozen=True)
class LabelPlacement:
    page_index: int
    line_index: int
    label: str
    box: Box


@dataclass(frozen=True)
class RenderingReport:
    line_count: int
    modified_line_count: int
    manual_review_count: int
    labels: tuple[LabelPlacement, ...]


@dataclass(frozen=True)
class ValidationReport:
    valid: bool
    findings: tuple[str, ...]


@dataclass(frozen=True)
class PdfOutputMetadata:
    source_sha256: str
    output_sha256: str
    created_at: datetime
    delete_after: datetime
    retention_days: int = RETENTION_DAYS


@dataclass(frozen=True)
class PdfProcessingOutput:
    path: Path
    rendering: RenderingReport
    validation: ValidationReport
    metadata: PdfOutputMetadata


@dataclass(frozen=True)
class _Columns:
    qty: float
    item: float
    description: float
    price: float
    barcode: float
    discount: float
    amount: float
    tax: float
    header_bottom: float


class InvoicePdfProcessor(ABC):
    @abstractmethod
    def inspect(self, source: Path) -> ParsedInvoice:
        raise NotImplementedError

    @abstractmethod
    def render(
        self, source: Path, destination: Path, decisions: InvoiceDecision
    ) -> PdfProcessingOutput:
        raise NotImplementedError


class LayoutAwareInvoicePdfProcessor(InvoicePdfProcessor):
    """Maps Phase 1C decisions onto text PDFs without re-evaluating price rules."""

    def inspect(self, source: Path) -> ParsedInvoice:
        source = source.resolve()
        if not source.is_file():
            raise FileNotFoundError(source)
        with pymupdf.open(source) as document:
            metadata = self._metadata(document)
            lines: list[ParsedInvoiceLine] = []
            totals: list[str] = []
            for page_index, page in enumerate(document):
                lines.extend(self._page_lines(page, page_index, len(lines)))
                totals.extend(
                    str(w[4]) for w in page.get_text("words") if w[0] > 400 and w[1] > 625
                )
        return ParsedInvoice(metadata, tuple(lines), tuple(totals), _sha256(source))

    def render(
        self, source: Path, destination: Path, decisions: InvoiceDecision
    ) -> PdfProcessingOutput:
        source, destination = source.resolve(), destination.resolve()
        if source == destination:
            raise ValueError("Source PDF is immutable; destination must be a new path")
        if destination.exists():
            raise FileExistsError(f"Refusing to overwrite existing output: {destination}")
        parsed = self.inspect(source)
        if decisions.invoice_number.casefold() != parsed.metadata.invoice_number.casefold():
            raise ValueError("InvoiceDecision invoice number does not match the PDF")
        mapped = self._map_decisions(parsed.lines, decisions.lines)
        destination.parent.mkdir(parents=True, exist_ok=True)
        labels: list[LabelPlacement] = []
        modified = manual = 0
        try:
            with pymupdf.open(source) as document:
                for line, decision in mapped:
                    page = document[line.page_index]
                    manual += int(decision.manual_review)
                    intents = set(decision.visual_intents)
                    fill = self._fill(intents)
                    if fill is not None:
                        self._replace_text(page, line.item_box, line.sku, fill, align=0)
                        modified += 1
                    if VisualIntent.PRICE_ZERO_MANUAL_REVIEW in intents:
                        self._replace_text(
                            page, line.price_box, _money(line.current_price), RED, align=2
                        )
                    elif not decision.manual_review:
                        if decision.price_changed:
                            self._replace_text(
                                page, line.price_box, _money(decision.final_price), None, align=2
                            )
                        if decision.discount_changed:
                            value = (
                                ""
                                if decision.final_discount is None
                                else _percent(decision.final_discount)
                            )
                            self._replace_text(page, line.discount_box, value, None, align=2)
                        if decision.amount_changed:
                            self._replace_text(
                                page, line.amount_box, _money(decision.final_amount), None, align=2
                            )
                    for placement, color in self._place_labels(page, line, self._labels(decision)):
                        rect = placement.box.rect()
                        page.draw_rect(rect, color=BLACK, fill=color, width=0.4, overlay=True)
                        page.insert_font(fontname=PDF_FONT_NAME, fontbuffer=PDF_FONT.buffer)
                        page.insert_textbox(
                            rect,
                            placement.label,
                            fontsize=5.5,
                            fontname=PDF_FONT_NAME,
                            color=BLACK,
                            align=1,
                            overlay=True,
                        )
                        labels.append(placement)
                document.save(destination, garbage=0, deflate=True)
            validation = self.validate(source, destination, parsed, decisions)
            if not validation.valid:
                raise ValueError(
                    "Generated PDF failed validation: " + "; ".join(validation.findings)
                )
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        now = datetime.now().astimezone()
        return PdfProcessingOutput(
            destination,
            RenderingReport(len(parsed.lines), modified, manual, tuple(labels)),
            validation,
            PdfOutputMetadata(
                parsed.source_sha256,
                _sha256(destination),
                now,
                now + timedelta(days=RETENTION_DAYS),
            ),
        )

    def validate(
        self,
        source: Path,
        output: Path,
        original: ParsedInvoice | None = None,
        decisions: InvoiceDecision | None = None,
    ) -> ValidationReport:
        before, after = original or self.inspect(source), self.inspect(output)
        findings: list[str] = []
        checks = (
            (before.metadata.page_count, after.metadata.page_count, "page count changed"),
            (before.metadata.page_sizes, after.metadata.page_sizes, "page dimensions changed"),
            (
                before.metadata.invoice_number,
                after.metadata.invoice_number,
                "invoice number changed",
            ),
            (before.metadata.customer_name, after.metadata.customer_name, "customer changed"),
            (before.protected_totals, after.protected_totals, "protected invoice totals changed"),
        )
        findings.extend(message for old, new, message in checks if old != new)
        if len(before.lines) != len(after.lines):
            findings.append("invoice line count changed")
        else:
            for old, new in zip(before.lines, after.lines, strict=True):
                if (old.sku, old.quantity) != (new.sku, new.quantity):
                    findings.append(f"protected SKU/QTY changed at line {old.index + 1}")
        if decisions and len(decisions.lines) != len(before.lines):
            findings.append("decision count does not match PDF line count")
        return ValidationReport(not findings, tuple(findings))

    def _metadata(self, document: pymupdf.Document) -> InvoicePdfMetadata:
        first, text = document[0], document[0].get_text("text")
        number = re.search(r"\b(?:(?:WINV|INV)-[A-Z0-9]+|(?:WINV|INV)\d+)\b", text, re.I)
        found_date = re.search(r"\b(\d{1,2}/\d{1,2}/\d{4})\b", text)
        if not number:
            raise ValueError("Invoice number not found")
        invoice_date = (
            datetime.strptime(found_date.group(1), "%d/%m/%Y").date() if found_date else None
        )
        customer = self._customer_from_words(
            [w for w in first.get_text("words") if w[0] < first.rect.width / 2]
        )
        return InvoicePdfMetadata(
            number.group(0),
            customer,
            invoice_date,
            document.page_count,
            tuple((round(p.rect.width, 3), round(p.rect.height, 3)) for p in document),
        )

    @staticmethod
    def _customer_from_words(words: list[tuple]) -> str:
        bill = next((w for w in words if str(w[4]).casefold() == "bill"), None)
        if bill:
            grouped: dict[float, list[tuple]] = defaultdict(list)
            for word in words:
                if bill[1] + 5 < word[1] < bill[1] + 55 and word[0] < 280:
                    grouped[round(word[1], 1)].append(word)
            for row in sorted(grouped):
                value = " ".join(str(w[4]) for w in sorted(grouped[row], key=lambda w: w[0]))
                if value.strip():
                    return value.strip()
        raise ValueError("Bill To customer not found")

    def _page_lines(
        self, page: pymupdf.Page, page_index: int, start: int
    ) -> list[ParsedInvoiceLine]:
        words = page.get_text("words")
        c = self._columns(words)
        grouped: dict[float, list[tuple]] = defaultdict(list)
        for word in sorted(words, key=lambda value: value[1]):
            if word[1] > c.header_bottom + 1:
                key = next(
                    (existing for existing in grouped if abs(existing - float(word[1])) <= 2.5),
                    round(float(word[1]), 1),
                )
                grouped[key].append(word)
        result: list[ParsedInvoiceLine] = []
        for group in sorted(grouped.values(), key=lambda row: (row[0][1], row[0][0])):
            item, qty = self._field(group, c.item, c.description), self._field(group, c.qty, c.item)
            price, amount = (
                self._field(group, c.price, c.barcode),
                self._field(group, c.amount, c.tax),
            )
            if not item or not qty or not price or not amount:
                continue
            try:
                quantity, current_price, current_amount = (
                    _decimal(qty[0]),
                    _decimal(price[0]),
                    _decimal(amount[0]),
                )
            except InvalidOperation:
                continue
            discount = self._field(group, c.discount, c.amount)
            current_discount = _decimal(discount[0]) if discount else None
            y0, y1 = min(w[1] for w in group), max(w[3] for w in group)
            result.append(
                ParsedInvoiceLine(
                    start + len(result),
                    page_index,
                    len(result),
                    item[0],
                    quantity,
                    current_price,
                    current_discount,
                    current_amount,
                    _box(item[1]),
                    _box(price[1]),
                    _box(discount[1]) if discount else Box(c.discount, y0, c.amount - 2, y1),
                    _box(amount[1]),
                )
            )
        return result

    @staticmethod
    def _columns(words: list[tuple]) -> _Columns:
        qty = next((w for w in words if str(w[4]).upper() == "QTY"), None)
        if not qty:
            raise ValueError("Repeated invoice table header QTY not found")
        header = [w for w in words if abs(w[1] - qty[1]) < 2.5]
        anchors: dict[str, float] = {}
        for name in ("QTY", "ITEM", "DESCRIPTION", "PRICE", "BARCODE", "DISC", "AMOUNT", "TAX"):
            word = next((w for w in header if str(w[4]).upper().rstrip(".") == name), None)
            if not word:
                raise ValueError(f"Invoice table header {name} not found")
            anchors[name] = float(word[0])
        # Header labels are centred while row values are right/left aligned.  Convert
        # those anchors to stable left boundaries for the actual row fields.
        return _Columns(
            max(0.0, anchors["QTY"] - 8),
            (anchors["QTY"] + anchors["ITEM"]) / 2,
            anchors["ITEM"] + 60,
            anchors["PRICE"] - 10,
            anchors["BARCODE"] - 15,
            anchors["DISC"] - 10,
            anchors["AMOUNT"] - 8,
            anchors["TAX"] - 8,
            max(float(w[3]) for w in header),
        )

    @staticmethod
    def _field(group: list[tuple], left: float, right: float) -> tuple[str, tuple] | None:
        selected = sorted((w for w in group if left - 2 <= w[0] < right - 1), key=lambda w: w[0])
        if not selected:
            return None
        y_values = [float(w[1]) for w in selected]
        if max(y_values) - min(y_values) > 0.5:
            visible_y = max(y_values)
            selected = [w for w in selected if abs(float(w[1]) - visible_y) < 0.25]
        # Overlay replacements are appended as later text blocks.  Prefer that
        # block so extraction sees the visible value rather than the covered one.
        latest_block = max(int(w[5]) for w in selected)
        if any(int(w[5]) != latest_block for w in selected):
            selected = [w for w in selected if int(w[5]) == latest_block]
        return " ".join(str(w[4]) for w in selected), (
            min(w[0] for w in selected),
            min(w[1] for w in selected),
            max(w[2] for w in selected),
            max(w[3] for w in selected),
        )

    @staticmethod
    def _map_decisions(
        lines: tuple[ParsedInvoiceLine, ...], decisions: tuple[LineDecision, ...]
    ) -> list[tuple[ParsedInvoiceLine, LineDecision]]:
        by_sku: dict[str, deque[LineDecision]] = defaultdict(deque)
        for decision in decisions:
            by_sku[decision.sku.casefold()].append(decision)
        mapped = []
        for line in lines:
            if not by_sku[line.sku.casefold()]:
                raise ValueError(f"No Phase 1C decision for PDF line {line.index + 1}: {line.sku}")
            decision = by_sku[line.sku.casefold()].popleft()
            if (decision.original_price, decision.original_discount, decision.original_amount) != (
                line.current_price,
                line.current_discount,
                line.current_amount,
            ):
                raise ValueError(f"Phase 1C original values do not match PDF line {line.index + 1}")
            mapped.append((line, decision))
        leftovers = [d.sku for queue in by_sku.values() for d in queue]
        if leftovers:
            raise ValueError(f"Unmapped Phase 1C decisions: {', '.join(leftovers[:5])}")
        return mapped

    @staticmethod
    def _fill(intents: set[VisualIntent]) -> tuple[float, float, float] | None:
        if VisualIntent.PRICE_ZERO_MANUAL_REVIEW in intents:
            return RED
        if VisualIntent.NOTES in intents:
            return PURPLE
        if VisualIntent.CARTON in intents:
            return GREEN
        if VisualIntent.REGULAR in intents:
            return YELLOW
        return None

    @staticmethod
    def _replace_text(
        page: pymupdf.Page,
        box: Box,
        text: str,
        fill: tuple[float, float, float] | None,
        *,
        align: int,
    ) -> None:
        rect = box.rect(0.8)
        width = PDF_FONT.text_length(text, fontsize=7.5)
        if width > rect.width and align != 0:
            rect.x0 = rect.x1 - width - 1
        if width > rect.width:
            raise ValueError(f"Replacement text does not fit: {text}")
        page.draw_rect(
            rect,
            color=fill if fill else (1, 1, 1),
            fill=fill if fill else (1, 1, 1),
            width=0,
            overlay=True,
        )
        page.insert_font(fontname=PDF_FONT_NAME, fontbuffer=PDF_FONT.buffer)
        x = rect.x0 if align == 0 else rect.x1 - width
        page.insert_text(
            (x, box.y1 - 2.0),
            text,
            fontsize=7.5,
            fontname=PDF_FONT_NAME,
            color=BLACK,
            overlay=True,
        )

    @staticmethod
    def _labels(decision: LineDecision) -> tuple[tuple[str, tuple[float, float, float]], ...]:
        intents, values = set(decision.visual_intents), []
        if VisualIntent.CARTON in intents:
            values.append(("CARTON", GREEN))
        if VisualIntent.NOTES in intents:
            values.append(("NOTE", PURPLE))
        if VisualIntent.PRICE_ZERO_MANUAL_REVIEW in intents:
            values.append(("MANUAL REVIEW", RED))
        if VisualIntent.NO_DISCOUNT in intents:
            values.append(("NO DISC", YELLOW))
        elif VisualIntent.DISCOUNT in intents:
            values.append((f"DISC {_percent(decision.final_discount or Decimal('0'))}", YELLOW))
        return tuple(values)

    @staticmethod
    def _place_labels(
        page: pymupdf.Page,
        line: ParsedInvoiceLine,
        labels: tuple[tuple[str, tuple[float, float, float]], ...],
    ) -> list[tuple[LabelPlacement, tuple[float, float, float]]]:
        gap, height, x = 2.0, 8.0, line.item_box.x1 + 3
        y = max(2.0, line.item_box.y0 - height - 1)
        result = []
        for label, color in labels:
            width = max(28.0, len(label) * 3.8 + 7)
            if x + width > line.price_box.x0 - 5:
                raise ValueError(f"Labels cannot be placed safely for line {line.index + 1}")
            box = Box(x, y, x + width, y + height)
            if box.x1 > page.rect.width - 5 or box.y1 > page.rect.height - 5:
                raise ValueError(f"Label placement is outside page for line {line.index + 1}")
            placement = LabelPlacement(line.page_index, line.index, label, box)
            result.append((placement, color))
            x += width + gap
        return result


def _decimal(value: str) -> Decimal:
    cleaned = value.strip().replace("$", "").replace(",", "").replace("%", "")
    if cleaned in {"", "-"}:
        raise InvalidOperation
    return Decimal(cleaned)


def _money(value: Decimal) -> str:
    return f"${value:.2f}"


def _percent(value: Decimal) -> str:
    return f"{value.normalize():f}%"


def _box(values: tuple) -> Box:
    return Box(*(float(value) for value in values))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
