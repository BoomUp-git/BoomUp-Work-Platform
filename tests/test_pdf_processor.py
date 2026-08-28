from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

import pymupdf
import pytest

from app.integrations.customer_price.provider import CustomerPriceRule
from app.modules.invoice_price_check.rule_engine import (
    InvoiceContext,
    InvoiceLine,
    InvoiceRuleEngine,
)
from app.pdf.processor import (
    GREEN,
    PURPLE,
    RED,
    RETENTION_DAYS,
    YELLOW,
    Box,
    LayoutAwareInvoicePdfProcessor,
)


def _write_invoice(path):
    document = pymupdf.open()
    for page_number, rows in enumerate(
        ((("REG-1", "2", "$4.00", "$8.00"),), (("ZERO-1", "1", "$7.00", "$7.00"),)), start=1
    ):
        page = document.new_page(width=595.276, height=841.89)
        page.insert_text((40, 50), "Invoice No. :")
        page.insert_text((180, 50), "WINV-TEST01")
        page.insert_text((40, 80), "Bill To:")
        page.insert_text((40, 95), "Example Customer")
        page.insert_text((400, 80), "24/08/2026")
        page.insert_text((400, 95), f"Page {page_number} of 2")
        headers = (
            (41, "QTY"),
            (104, "ITEM"),
            (215, "DESCRIPTION"),
            (320, "PRICE"),
            (373, "BARCODE"),
            (443, "DISC"),
            (487, "AMOUNT"),
            (536, "TAX"),
        )
        for x, text in headers:
            page.insert_text((x, 260), text, fontsize=8)
        for offset, (sku, qty, price, amount) in enumerate(rows):
            y = 310 + offset * 50
            for x, text in (
                (45, qty),
                (76, sku),
                (180, "Description"),
                (323, price),
                (365, "9335803000000"),
                (492, amount),
                (536, "GST"),
            ):
                page.insert_text((x, y), text, fontsize=8)
        if page_number == 2:
            page.insert_text((410, 650), "Total:")
            page.insert_text((500, 650), "$15.00")
    document.save(path)
    document.close()


def _rule(
    item,
    price,
    *,
    notes=None,
    price_type="Regular",
    no_discount=False,
    has_discount=False,
    discount_value=None,
):
    return CustomerPriceRule(
        source_row=2,
        price_id=f"rule-{item}",
        customer_id="Example Customer",
        item_rule=item,
        match_type="Exact",
        excluded_items=(),
        price_type=price_type,
        price=Decimal(price),
        no_discount=no_discount,
        has_discount=has_discount,
        discount_value=discount_value,
        notes=notes,
        effective_from=date(2026, 1, 1),
        effective_to=None,
        status="Active",
    )


def test_multi_page_parse_render_and_protected_fields(tmp_path):
    source, output = tmp_path / "source.pdf", tmp_path / "output.pdf"
    _write_invoice(source)
    processor = LayoutAwareInvoicePdfProcessor()
    parsed = processor.inspect(source)
    assert parsed.metadata.invoice_number == "WINV-TEST01"
    assert parsed.metadata.customer_name == "Example Customer"
    assert [line.sku for line in parsed.lines] == ["REG-1", "ZERO-1"]

    invoice_lines = tuple(
        InvoiceLine(
            line.sku, line.quantity, line.current_price, line.current_discount, line.current_amount
        )
        for line in parsed.lines
    )
    decision = InvoiceRuleEngine().evaluate(
        InvoiceContext("WINV-TEST01", date(2026, 8, 24), "Example Customer"),
        invoice_lines,
        (_rule("REG-1", "5.00"), _rule("ZERO-1", "0", notes="human confirmation")),
        retrieved_at=datetime(2026, 8, 26, tzinfo=UTC),
    )
    result = processor.render(source, output, decision)
    reparsed = processor.inspect(output)

    assert output.exists()
    assert result.validation.valid
    assert result.metadata.retention_days == RETENTION_DAYS == 30
    assert reparsed.metadata.page_count == 2
    assert reparsed.protected_totals == parsed.protected_totals
    assert reparsed.lines[0].current_price == Decimal("5.00")
    assert reparsed.lines[0].current_amount == Decimal("10.00")
    assert reparsed.lines[1].current_price == Decimal("7.00")
    assert reparsed.lines[1].current_amount == Decimal("7.00")
    assert [label.label for label in result.rendering.labels] == ["NOTE", "MANUAL REVIEW"]


def test_refuses_source_overwrite(tmp_path):
    source = tmp_path / "source.pdf"
    _write_invoice(source)
    processor = LayoutAwareInvoicePdfProcessor()
    try:
        processor.render(source, source, object())
    except ValueError as error:
        assert "immutable" in str(error)
    else:
        raise AssertionError("source overwrite was not rejected")


def test_validation_uses_protected_boxes_when_overlay_splits_extracted_rows(
    tmp_path, monkeypatch
):
    source, output = tmp_path / "source.pdf", tmp_path / "output.pdf"
    _write_invoice(source)
    processor = LayoutAwareInvoicePdfProcessor()
    parsed = processor.inspect(source)
    output.write_bytes(source.read_bytes())
    original_inspect = processor.inspect

    def inspect_with_split_rows(path):
        result = original_inspect(path)
        if path == output:
            return replace(result, lines=result.lines[:-1])
        return result

    monkeypatch.setattr(processor, "inspect", inspect_with_split_rows)
    report = processor.validate(source, output, parsed)
    assert report.valid


def test_label_prefers_safe_space_immediately_left_of_price(tmp_path):
    source = tmp_path / "source.pdf"
    _write_invoice(source)
    processor = LayoutAwareInvoicePdfProcessor()
    line = processor.inspect(source).lines[0]
    with pymupdf.open(source) as document:
        placed = processor._place_labels(document[0], line, (("CARTON", GREEN),))
    label = placed[0][0].box
    assert label.x0 > line.description_box.x1
    assert label.x1 == line.price_box.x0 - 1


def test_carton_label_stays_price_adjacent_in_narrow_safe_gap(tmp_path):
    source = tmp_path / "source.pdf"
    _write_invoice(source)
    processor = LayoutAwareInvoicePdfProcessor()
    line = processor.inspect(source).lines[0]
    price_x0 = line.description_box.x1 + 29.65
    narrow = replace(
        line,
        price_box=Box(
            price_x0,
            line.price_box.y0,
            price_x0 + line.price_box.x1 - line.price_box.x0,
            line.price_box.y1,
        ),
    )
    with pymupdf.open(source) as document:
        placed = processor._place_labels(document[0], narrow, (("CARTON", GREEN),))
    label = placed[0][0].box
    assert label.x0 > narrow.description_box.x1
    assert label.x1 == narrow.price_box.x0 - 1


def test_carton_label_overlaps_description_instead_of_falling_back_to_item_number(tmp_path):
    source = tmp_path / "source.pdf"
    _write_invoice(source)
    processor = LayoutAwareInvoicePdfProcessor()
    line = processor.inspect(source).lines[0]
    narrow = replace(
        line,
        price_box=Box(
            line.description_box.x1 + 20,
            line.price_box.y0,
            line.description_box.x1 + 20 + line.price_box.x1 - line.price_box.x0,
            line.price_box.y1,
        ),
    )
    with pymupdf.open(source) as document:
        placement = processor._place_labels(
            document[0], narrow, (("CARTON", GREEN),)
        )[0][0]
    assert placement.box.x0 < narrow.description_box.x1
    assert placement.box.x0 >= narrow.description_box.x0
    assert placement.box.x1 == narrow.price_box.x0 - 1
    assert placement.font_size == 5.5


def test_label_never_falls_back_to_item_number_when_price_slot_is_too_narrow(tmp_path):
    source = tmp_path / "source.pdf"
    _write_invoice(source)
    processor = LayoutAwareInvoicePdfProcessor()
    line = processor.inspect(source).lines[0]
    blocked = replace(
        line,
        description_box=Box(
            line.price_box.x0 - 10,
            line.description_box.y0,
            line.price_box.x0 - 2,
            line.description_box.y1,
        ),
    )
    with pymupdf.open(source) as document, pytest.raises(
        ValueError, match="Labels cannot be placed safely"
    ):
        processor._place_labels(document[0], blocked, (("CARTON", GREEN),))


def test_label_groups_share_price_aligned_right_edge_despite_description_width(tmp_path):
    source = tmp_path / "source.pdf"
    _write_invoice(source)
    processor = LayoutAwareInvoicePdfProcessor()
    line = processor.inspect(source).lines[0]
    shorter = replace(
        line,
        description_box=Box(
            line.description_box.x0,
            line.description_box.y0,
            line.description_box.x1 - 20,
            line.description_box.y1,
        ),
    )
    with pymupdf.open(source) as document:
        first = processor._place_labels(document[0], line, (("CARTON", GREEN),))[0][0]
        second = processor._place_labels(document[0], shorter, (("CARTON", GREEN),))[0][0]
    assert first.box.x1 == second.box.x1 == line.price_box.x0 - 1


def test_visual_intents_have_fixed_colors_and_atomic_label_order():
    processor = LayoutAwareInvoicePdfProcessor()
    base = InvoiceLine("LABEL-1", Decimal("1"), Decimal("5"), None, Decimal("5"))
    context = InvoiceContext("WINV-LABEL", date(2026, 8, 24), "Example Customer")

    carton = (
        InvoiceRuleEngine()
        .evaluate(
            context,
            (base,),
            (_rule("LABEL-1", "5.00", price_type="Carton", no_discount=True),),
            retrieved_at=datetime(2026, 8, 26, tzinfo=UTC),
        )
        .lines[0]
    )
    discount = (
        InvoiceRuleEngine()
        .evaluate(
            context,
            (base,),
            (_rule("LABEL-1", "5.00", has_discount=True, discount_value=Decimal("10")),),
            retrieved_at=datetime(2026, 8, 26, tzinfo=UTC),
        )
        .lines[0]
    )
    notes_zero = (
        InvoiceRuleEngine()
        .evaluate(
            context,
            (base,),
            (_rule("LABEL-1", "0", notes="review"),),
            retrieved_at=datetime(2026, 8, 26, tzinfo=UTC),
        )
        .lines[0]
    )

    # Carton rules now stop at Manual Review before discount treatment.
    assert [label for label, _ in processor._labels(carton)] == ["CARTON"]
    assert [label for label, _ in processor._labels(discount)] == ["DISC 10%"]
    assert [label for label, _ in processor._labels(notes_zero)] == ["NOTE", "MANUAL REVIEW"]
    assert processor._fill(set(carton.visual_intents)) == GREEN
    assert processor._price_fill(set(carton.visual_intents)) == GREEN
    assert processor._fill(set(discount.visual_intents)) == PURPLE
    assert processor._price_fill(set(discount.visual_intents)) == YELLOW
    assert processor._fill(set(notes_zero.visual_intents)) == RED
    assert PURPLE != RED
