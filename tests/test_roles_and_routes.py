from app.integrations.customer_price.google_sheets import GoogleSheetsCustomerPriceProvider
from tests.test_customer_price_provider import Gateway


def test_admin_shell_is_not_exposed(client):
    assert client.get("/admin").status_code == 404


def test_public_invoice_shell_has_no_account_controls(client):
    invoice = client.get("/invoice-price-check")
    assert invoice.status_code == 200
    assert "你太棒啦！喝口咖啡吧，揉揉眼睛吧~" in invoice.text
    assert "不可用" in invoice.text
    assert "退出" not in invoice.text
    assert 'href="/login"' not in invoice.text


def test_connected_source_diagnostics_are_bounded_and_do_not_expose_data(app, client):
    app.state.customer_price_provider = GoogleSheetsCustomerPriceProvider(Gateway(), "sheet-id")
    invoice = client.get("/invoice-price-check")
    assert "已连接" in invoice.text
    assert "发票价格检查" in invoice.text
    assert '已加载规则</dt><dd class="rule-count">1' in invoice.text
    assert "p1" not in invoice.text
    assert "ANTK Souvenirs" not in invoice.text
