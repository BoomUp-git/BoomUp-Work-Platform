from app.integrations.customer_price.google_sheets import GoogleSheetsCustomerPriceProvider
from tests.conftest import ADMIN_AUTH_SAMPLE, OPERATOR_AUTH_SAMPLE, login
from tests.test_customer_price_provider import Gateway


def test_admin_role_can_access_admin_shell(client):
    login(client, "admin@example.com", ADMIN_AUTH_SAMPLE)
    response = client.get("/admin")
    assert response.status_code == 200
    assert "Work Platform Admin" in response.text


def test_operator_role_cannot_access_admin_shell(client):
    login(client, "operator@example.com", OPERATOR_AUTH_SAMPLE)
    assert client.get("/admin").status_code == 403


def test_operator_can_access_dashboard_and_invoice_shell(client):
    login(client, "operator@example.com", OPERATOR_AUTH_SAMPLE)
    dashboard = client.get("/")
    assert dashboard.status_code == 200
    assert "Invoice Price Check" in dashboard.text
    assert "Not configured" in dashboard.text
    invoice = client.get("/invoice-price-check")
    assert invoice.status_code == 200
    assert "根据最新批准的客户价格检查并处理发票" in invoice.text
    assert "不可用" in invoice.text


def test_connected_source_diagnostics_are_bounded_and_do_not_expose_data(app, client):
    app.state.customer_price_provider = GoogleSheetsCustomerPriceProvider(Gateway(), "sheet-id")
    login(client, "admin@example.com", ADMIN_AUTH_SAMPLE)
    invoice = client.get("/invoice-price-check")
    admin = client.get("/admin")
    assert "已连接" in invoice.text
    assert "发票价格检查" in invoice.text
    assert "已加载规则</dt><dd>1" in invoice.text
    assert "Connected" in admin.text
    assert "p1" not in admin.text
    assert "ANTK Souvenirs" not in admin.text


def test_external_cards_use_configured_urls(app, settings):
    configured = settings.__class__(
        **{
            **settings.__dict__,
            "warehouse_app_url": "https://warehouse.example.test",
            "inventory_app_url": "https://inventory.example.test",
        }
    )
    configured_app = app
    configured_app.state.settings = configured
    from fastapi.testclient import TestClient

    with TestClient(configured_app, follow_redirects=False) as configured_client:
        login(configured_client, "operator@example.com", OPERATOR_AUTH_SAMPLE)
        page = configured_client.get("/")
        assert 'href="https://warehouse.example.test"' in page.text
        assert 'href="https://inventory.example.test"' in page.text
