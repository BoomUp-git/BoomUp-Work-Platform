from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse


def _positive_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _optional_http_url(name: str) -> str | None:
    value = os.getenv(name, "").strip()
    if not value:
        return None
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{name} must be an absolute http(s) URL")
    return value


@dataclass(frozen=True)
class Settings:
    app_env: str
    app_secret: str
    database_url: str
    warehouse_app_url: str | None
    inventory_app_url: str | None
    session_cookie_name: str
    session_ttl_hours: int
    login_max_failures: int
    login_window_minutes: int
    login_block_minutes: int
    google_auth_mode: str = "service_account"
    google_service_account_file: str | None = None
    google_customer_price_spreadsheet_id: str = ""
    customer_price_cache_ttl_seconds: int = 60
    invoice_storage_dir: str = "storage/invoices"
    invoice_max_upload_bytes: int = 20 * 1024 * 1024
    work_platform_version: str = "development"

    @property
    def production(self) -> bool:
        return self.app_env.lower() == "production"

    @classmethod
    def from_env(cls) -> Settings:
        app_env = os.getenv("APP_ENV", "development").strip().lower()
        secret = os.getenv("APP_SECRET", "development-only-change-before-production")
        if app_env == "production" and (
            len(secret) < 32 or secret.startswith("replace-") or "development" in secret
        ):
            raise ValueError(
                "APP_SECRET must be a non-placeholder secret of at least 32 characters"
            )
        google_auth_mode = os.getenv("GOOGLE_AUTH_MODE", "service_account").strip().lower()
        if google_auth_mode not in {"service_account", "adc"}:
            raise ValueError("GOOGLE_AUTH_MODE must be service_account or adc")
        return cls(
            app_env=app_env,
            app_secret=secret,
            database_url=os.getenv("DATABASE_URL", "sqlite:///./data/work-platform.db"),
            warehouse_app_url=_optional_http_url("WAREHOUSE_APP_URL"),
            inventory_app_url=_optional_http_url("INVENTORY_APP_URL"),
            session_cookie_name=os.getenv("SESSION_COOKIE_NAME", "boomup_work_session"),
            session_ttl_hours=_positive_int("SESSION_TTL_HOURS", 12),
            login_max_failures=_positive_int("LOGIN_MAX_FAILURES", 5),
            login_window_minutes=_positive_int("LOGIN_WINDOW_MINUTES", 15),
            login_block_minutes=_positive_int("LOGIN_BLOCK_MINUTES", 15),
            google_auth_mode=google_auth_mode,
            google_service_account_file=(
                os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip() or None
            ),
            google_customer_price_spreadsheet_id=os.getenv(
                "GOOGLE_CUSTOMER_PRICE_SPREADSHEET_ID", ""
            ).strip(),
            customer_price_cache_ttl_seconds=_positive_int("CUSTOMER_PRICE_CACHE_TTL_SECONDS", 60),
            invoice_storage_dir=os.getenv("INVOICE_STORAGE_DIR", "storage/invoices").strip(),
            invoice_max_upload_bytes=_positive_int("INVOICE_MAX_UPLOAD_BYTES", 20 * 1024 * 1024),
            work_platform_version=os.getenv("WORK_PLATFORM_VERSION", "development").strip(),
        )
