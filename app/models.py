from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def new_id() -> str:
    return str(uuid.uuid4())


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class UserRole(StrEnum):
    ADMIN = "admin"
    OPERATOR = "operator"


class UserStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(512))
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), default=UserRole.OPERATOR)
    status: Mapped[UserStatus] = mapped_column(Enum(UserStatus), default=UserStatus.ACTIVE)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    sessions: Mapped[list[AuthSession]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    csrf_token: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped[User] = relationship(back_populates="sessions")


class LoginAttempt(Base):
    __tablename__ = "login_attempts"
    __table_args__ = (UniqueConstraint("email", "client_key", name="uq_login_attempt_subject"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320))
    client_key: Mapped[str] = mapped_column(String(128))
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    window_started_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    blocked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
