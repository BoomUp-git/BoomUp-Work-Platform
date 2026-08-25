from tests.conftest import ADMIN_AUTH_SAMPLE, OPERATOR_AUTH_SAMPLE, login


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
    assert "Invoice Price Check is being prepared." in invoice.text


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
