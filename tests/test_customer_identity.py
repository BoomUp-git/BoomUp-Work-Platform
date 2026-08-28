from app.modules.invoice_price_check.customer_identity import (
    customer_name_signature,
    resolve_unique_customer_name,
)


def test_sollys_customer_name_reorders_to_one_authoritative_name():
    invoice_name = "Solly's (Cairns)TOBYZEB P/L"
    authoritative = "Solly's (TOBYZEB PL Cairns)"
    assert customer_name_signature(invoice_name) == customer_name_signature(authoritative)
    assert resolve_unique_customer_name(
        invoice_name,
        ["Silly Solly's (Townsville) Arasar Pty Ltd", authoritative],
    ) == authoritative


def test_signature_collision_is_never_guessed():
    assert resolve_unique_customer_name(
        "Alpha Cairns P/L",
        ["Alpha (PL Cairns)", "Cairns Alpha PL"],
    ) is None
