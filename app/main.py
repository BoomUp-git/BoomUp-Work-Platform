from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import models  # noqa: F401 - registers SQLAlchemy metadata
from app.auth.routes import router as auth_router
from app.config import Settings
from app.database import Base, build_engine, build_session_factory
from app.modules.invoice_price_check.routes import router as invoice_router
from app.routes import router as platform_router

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

    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
    app.include_router(auth_router)
    app.include_router(platform_router)
    app.include_router(invoice_router)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self'; img-src 'self' data:; "
            "form-action 'self'; frame-ancestors 'none'; base-uri 'self'"
        )
        return response

    @app.exception_handler(HTTPException)
    async def auth_redirect(request: Request, exc: HTTPException):
        if exc.status_code == 401 and request.method == "GET":
            return RedirectResponse(url="/login", status_code=303)
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    return app


app = create_app()
