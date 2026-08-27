def test_public_home_redirects_to_invoice_price_check(client):
    response = client.get("/")
    assert response.status_code == 303
    assert response.headers["location"] == "/invoice-price-check"


def test_login_and_logout_endpoints_are_not_exposed(client):
    assert client.get("/login").status_code == 404
    assert client.post("/logout").status_code == 404


def test_invoice_page_is_public(client):
    assert client.get("/invoice-price-check").status_code == 200
