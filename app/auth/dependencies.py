from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.auth.service import resolve_session
from app.models import AuthSession, User, UserRole


def get_db(request: Request):
    db = request.app.state.SessionLocal()
    try:
        yield db
    finally:
        db.close()


def current_session(request: Request, db: Session = Depends(get_db)) -> AuthSession:
    settings = request.app.state.settings
    session = resolve_session(db, request.cookies.get(settings.session_cookie_name), settings)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required"
        )
    return session


def current_user(session: AuthSession = Depends(current_session)) -> User:
    return session.user


def admin_user(user: User = Depends(current_user)) -> User:
    if user.role is not UserRole.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return user
