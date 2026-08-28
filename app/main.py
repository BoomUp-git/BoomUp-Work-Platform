from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import models  # noqa: F401 - registers SQLAlchemy metadata
from app.config import Settings
from app.database import Base, build_engine, build_session_factory
from app.integrations.customer_price.google_sheets import (
    SourceUnavailableError,
    build_customer_price_provider,
)
from app.modules.invoice_price_check.routes import router as invoice_router
from app.modules.invoice_price_check.rule_engine import InvoiceRuleEngine
from app.modules.invoice_price_check.service import InvoicePriceCheckService
from app.pdf.processor import LayoutAwareInvoicePdfProcessor

BASE_DIR = Path(__file__).resolve().parent


def create_app(settings: Settings | None = None, *, create_schema: bool = False) -> FastAPI:
    resolved = settings or Settings.from_env()
    app = FastAPI(title="BoomUp Work Platform", docs_url=None, redoc_url=None)
    engine = build_engine(resolved.database_url)
    if create_schema:
        Base.metadata.create_all(engine)

    app.state.settings = resolved
    app.state.engine = engine
    app.state.SessionLocal = build_session_factory(engine)
    app.state.templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
    app.state.customer_price_provider = None
    app.state.invoice_price_check_service = None
    if resolved.google_customer_price_spreadsheet_id:
        try:
            app.state.customer_price_provider = build_customer_price_provider(resolved)
            app.state.invoice_price_check_service = InvoicePriceCheckService(
                app.state.customer_price_provider,
                LayoutAwareInvoicePdfProcessor(),
                InvoiceRuleEngine(),
            )
        except SourceUnavailableError:
            # Diagnostics report unavailable without exposing credential or filesystem details.
            pass

    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
    app.include_router(invoice_router)

    @app.get("/", include_in_schema=False)
    def public_home():
        return RedirectResponse(url="/invoice-price-check", status_code=303)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if "/preview" in request.url.path:
            response.headers["X-Frame-Options"] = "SAMEORIGIN"
            response.headers["Content-Security-Policy"] = (
                "default-src 'none'; frame-ancestors 'self'; sandbox"
            )
        else:
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; style-src 'self'; img-src 'self' data:; "
                "form-action 'self'; frame-src 'self'; frame-ancestors 'none'; "
                "base-uri 'self'"
            )
        return response

    @app.exception_handler(HTTPException)
    async def http_error(_request: Request, exc: HTTPException):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    return app


app = create_app()
