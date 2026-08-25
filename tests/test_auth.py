from tests.conftest import ADMIN_AUTH_SAMPLE, INVALID_AUTH_SAMPLE, login, logout_csrf


def test_login_success_and_dashboard(client):
    response = login(client, "ADMIN@example.com", ADMIN_AUTH_SAMPLE)
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    dashboard = client.get("/")
    assert dashboard.status_code == 200
    assert "Welcome, Admin User" in dashboard.text


def test_login_failure(client):
    response = login(client, "admin@example.com", INVALID_AUTH_SAMPLE)
    assert response.status_code == 401
    assert "Email or password is incorrect" in response.text
    assert client.get("/").status_code == 303


def test_protected_route_without_authentication(client):
    response = client.get("/")
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert client.get("/invoice-price-check").status_code == 303


def test_logout_revokes_session(client):
    assert login(client, "admin@example.com", ADMIN_AUTH_SAMPLE).status_code == 303
    dashboard = client.get("/")
    csrf = logout_csrf(dashboard.text)
    response = client.post("/logout", data={"csrf_token": csrf})
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert client.get("/").status_code == 303


def test_login_csrf_is_required(client):
    response = client.post(
        "/login",
        data={
            "email": "admin@example.com",
            "password": ADMIN_AUTH_SAMPLE,
            "csrf_token": "bad",
        },
    )
    assert response.status_code == 400


def test_repeated_login_failures_are_rate_limited(client):
    for _ in range(5):
        assert login(client, "admin@example.com", INVALID_AUTH_SAMPLE).status_code == 401
    response = login(client, "admin@example.com", INVALID_AUTH_SAMPLE)
    assert response.status_code == 429
    assert int(response.headers["retry-after"]) > 0
