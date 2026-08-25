from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.orm import Session

from app.auth.dependencies import current_session, get_db
from app.auth.service import (
    authenticate_user,
    clear_login_failures,
    create_session,
    normalize_email,
    rate_limit_status,
    record_login_failure,
    revoke_session,
)
from app.models import AuthSession

router = APIRouter()


def _serializer(request: Request) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(request.app.state.settings.app_secret, salt="login-csrf")


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _login_page(request: Request, error: str | None = None, status_code: int = 200):
    nonce = secrets.token_urlsafe(24)
    csrf_token = _serializer(request).dumps(nonce)
    response = request.app.state.templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"csrf_token": csrf_token, "error": error},
        status_code=status_code,
    )
    response.set_cookie(
        "boomup_login_csrf",
        csrf_token,
        httponly=True,
        secure=request.app.state.settings.production,
        samesite="strict",
        max_age=600,
    )
    return response


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return _login_page(request)


@router.post("/login")
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    cookie_token = request.cookies.get("boomup_login_csrf")
    if not cookie_token or not secrets.compare_digest(cookie_token, csrf_token):
        return _login_page(request, "Your login form expired. Please try again.", 400)
    try:
        _serializer(request).loads(csrf_token, max_age=600)
    except (BadSignature, SignatureExpired):
        return _login_page(request, "Your login form expired. Please try again.", 400)

    normalized_email = normalize_email(email)
    client_key = _client_key(request)
    blocked, retry_after = rate_limit_status(
        db, normalized_email, client_key, request.app.state.settings
    )
    if blocked:
        response = _login_page(request, "Too many failed attempts. Please wait and try again.", 429)
        response.headers["Retry-After"] = str(retry_after)
        return response

    user = authenticate_user(db, normalized_email, password)
    if user is None:
        record_login_failure(db, normalized_email, client_key, request.app.state.settings)
        return _login_page(request, "Email or password is incorrect.", 401)

    clear_login_failures(db, normalized_email, client_key)
    raw_token, _ = create_session(db, user, request.app.state.settings)
    response = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        request.app.state.settings.session_cookie_name,
        raw_token,
        httponly=True,
        secure=request.app.state.settings.production,
        samesite="strict",
        max_age=request.app.state.settings.session_ttl_hours * 3600,
        path="/",
    )
    response.delete_cookie("boomup_login_csrf")
    return response


@router.post("/logout")
def logout(
    request: Request,
    csrf_token: str = Form(...),
    session: AuthSession = Depends(current_session),
    db: Session = Depends(get_db),
):
    if not secrets.compare_digest(session.csrf_token, csrf_token):
        raise HTTPException(status_code=400, detail="Invalid CSRF token")
    revoke_session(db, session)
    response = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(request.app.state.settings.session_cookie_name, path="/")
    return response
