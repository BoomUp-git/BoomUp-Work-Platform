from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from app.auth.service import hash_password
from app.config import Settings
from app.main import create_app
from app.models import User, UserRole

ADMIN_AUTH_SAMPLE = "correct horse battery staple"
OPERATOR_AUTH_SAMPLE = "operator secure sample value"
INVALID_AUTH_SAMPLE = "invalid sample value"
TEST_SIGNING_VALUE = "test-signing-value-at-least-thirty-two-characters"


@pytest.fixture()
def settings(tmp_path):
    return Settings(
        app_env="test",
        app_secret=TEST_SIGNING_VALUE,
        database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        warehouse_app_url=None,
        inventory_app_url=None,
        session_cookie_name="test_session",
        session_ttl_hours=12,
        login_max_failures=5,
        login_window_minutes=15,
        login_block_minutes=15,
    )


@pytest.fixture()
def app(settings):
    application = create_app(settings, create_schema=True)
    with application.state.SessionLocal() as db:
        db.add_all(
            [
                User(
                    email="admin@example.com",
                    display_name="Admin User",
                    password_hash=hash_password(ADMIN_AUTH_SAMPLE),
                    role=UserRole.ADMIN,
                ),
                User(
                    email="operator@example.com",
                    display_name="Operator User",
                    password_hash=hash_password(OPERATOR_AUTH_SAMPLE),
                    role=UserRole.OPERATOR,
                ),
            ]
        )
        db.commit()
    yield application
    application.state.engine.dispose()


@pytest.fixture()
def client(app):
    with TestClient(app, follow_redirects=False) as test_client:
        yield test_client


def login(client: TestClient, email: str, password: str):
    page = client.get("/login")
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    assert csrf
    return client.post(
        "/login",
        data={"email": email, "password": password, "csrf_token": csrf.group(1)},
    )


def logout_csrf(html: str) -> str:
    match = re.search(r'action="/logout".*?name="csrf_token" value="([^"]+)"', html, re.S)
    assert match
    return match.group(1)
