from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import AuthSession, LoginAttempt, User, UserStatus, utc_now

password_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)


def normalize_email(value: str) -> str:
    return value.strip().casefold()


def hash_password(password: str) -> str:
    if len(password) < 12:
        raise ValueError("Password must be at least 12 characters")
    return password_hasher.hash(password)


def verify_password(stored_hash: str, candidate: str) -> bool:
    try:
        return password_hasher.verify(stored_hash, candidate)
    except (VerifyMismatchError, InvalidHashError):
        return False


def token_hash(token: str, secret: str) -> str:
    return hmac.new(secret.encode(), token.encode(), hashlib.sha256).hexdigest()


def rate_limit_status(
    db: Session, email: str, client_key: str, settings: Settings
) -> tuple[bool, int]:
    now = utc_now()
    attempt = db.scalar(
        select(LoginAttempt).where(
            LoginAttempt.email == email, LoginAttempt.client_key == client_key
        )
    )
    if not attempt or not attempt.blocked_until or attempt.blocked_until <= now:
        return False, 0
    return True, max(1, int((attempt.blocked_until - now).total_seconds()))


def record_login_failure(db: Session, email: str, client_key: str, settings: Settings) -> None:
    now = utc_now()
    attempt = db.scalar(
        select(LoginAttempt).where(
            LoginAttempt.email == email, LoginAttempt.client_key == client_key
        )
    )
    window = timedelta(minutes=settings.login_window_minutes)
    if attempt is None:
        attempt = LoginAttempt(email=email, client_key=client_key, failure_count=1)
        db.add(attempt)
    elif now - attempt.window_started_at > window:
        attempt.failure_count = 1
        attempt.window_started_at = now
        attempt.blocked_until = None
    else:
        attempt.failure_count += 1
    if attempt.failure_count >= settings.login_max_failures:
        attempt.blocked_until = now + timedelta(minutes=settings.login_block_minutes)
    db.commit()


def clear_login_failures(db: Session, email: str, client_key: str) -> None:
    attempt = db.scalar(
        select(LoginAttempt).where(
            LoginAttempt.email == email, LoginAttempt.client_key == client_key
        )
    )
    if attempt:
        db.delete(attempt)
        db.commit()


def authenticate_user(db: Session, email: str, password: str) -> User | None:
    user = db.scalar(select(User).where(User.email == normalize_email(email)))
    if user is None or user.status is not UserStatus.ACTIVE:
        return None
    if not verify_password(user.password_hash, password):
        return None
    return user


def create_session(db: Session, user: User, settings: Settings) -> tuple[str, AuthSession]:
    raw_token = secrets.token_urlsafe(48)
    session = AuthSession(
        user_id=user.id,
        token_hash=token_hash(raw_token, settings.app_secret),
        csrf_token=secrets.token_urlsafe(32),
        expires_at=utc_now() + timedelta(hours=settings.session_ttl_hours),
    )
    user.last_login_at = utc_now()
    db.add(session)
    db.commit()
    db.refresh(session)
    return raw_token, session


def resolve_session(db: Session, raw_token: str | None, settings: Settings) -> AuthSession | None:
    if not raw_token:
        return None
    session = db.scalar(
        select(AuthSession).where(
            AuthSession.token_hash == token_hash(raw_token, settings.app_secret)
        )
    )
    if not session or session.revoked_at is not None or session.expires_at <= utc_now():
        return None
    if session.user.status is not UserStatus.ACTIVE:
        return None
    return session


def revoke_session(db: Session, session: AuthSession) -> None:
    managed_session = db.merge(session)
    managed_session.revoked_at = utc_now()
    db.commit()
