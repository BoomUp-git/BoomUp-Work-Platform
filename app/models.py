from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
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


class InvoiceJobStatus(StrEnum):
    PROCESSING = "processing"
    SUCCESS = "success"
    MANUAL_REVIEW = "manual_review"
    FAILED = "failed"


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
    invoice_jobs: Mapped[list[InvoiceJob]] = relationship(back_populates="operator")


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


class InvoiceJob(Base):
    __tablename__ = "invoice_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    operator_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    original_filename: Mapped[str] = mapped_column(String(255))
    original_file_ref: Mapped[str] = mapped_column(String(512))
    output_file_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    invoice_number: Mapped[str | None] = mapped_column(String(100), index=True, nullable=True)
    customer: Mapped[str | None] = mapped_column(String(255), nullable=True)
    invoice_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    price_retrieved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    rule_engine_version: Mapped[str] = mapped_column(String(64))
    platform_version: Mapped[str] = mapped_column(String(64))
    status: Mapped[InvoiceJobStatus] = mapped_column(Enum(InvoiceJobStatus), index=True)
    manual_review: Mapped[bool] = mapped_column(Boolean, default=False)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    safe_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    retention_expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    operator: Mapped[User] = relationship(back_populates="invoice_jobs")
