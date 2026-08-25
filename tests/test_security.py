from app.auth.service import hash_password, verify_password
from app.config import Settings


def test_passwords_are_argon2id_hashed():
    value = hash_password("a sufficiently long test password")
    assert value.startswith("$argon2id$")
    assert "sufficiently long" not in value
    assert verify_password(value, "a sufficiently long test password")
    assert not verify_password(value, "incorrect password")


def test_security_headers(client):
    response = client.get("/login")
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_production_rejects_placeholder_secret(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("APP_SECRET", "replace-with-at-least-32-random-characters")
    try:
        Settings.from_env()
    except ValueError as exc:
        assert "APP_SECRET" in str(exc)
    else:
        raise AssertionError("Production placeholder secret was accepted")
