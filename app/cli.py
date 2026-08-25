from __future__ import annotations

import argparse
from getpass import getpass

from email_validator import EmailNotValidError, validate_email
from sqlalchemy import select

from app.auth.service import hash_password, normalize_email
from app.config import Settings
from app.database import build_engine, build_session_factory
from app.models import User, UserRole


def create_admin(email: str, display_name: str) -> int:
    try:
        normalized = normalize_email(validate_email(email, check_deliverability=False).normalized)
    except EmailNotValidError as exc:
        raise SystemExit(f"Invalid email: {exc}") from exc
    password = getpass("Initial admin password (12+ characters): ")
    confirmation = getpass("Confirm password: ")
    if password != confirmation:
        raise SystemExit("Passwords do not match")

    settings = Settings.from_env()
    SessionLocal = build_session_factory(build_engine(settings.database_url))
    with SessionLocal() as db:
        if db.scalar(select(User).where(User.email == normalized)):
            raise SystemExit("A user with that email already exists")
        db.add(
            User(
                email=normalized,
                display_name=display_name.strip(),
                password_hash=hash_password(password),
                role=UserRole.ADMIN,
            )
        )
        db.commit()
    print(f"Created initial admin: {normalized}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="BoomUp Work Platform administration")
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create-admin", help="Create the initial local admin")
    create.add_argument("--email", required=True)
    create.add_argument("--display-name", required=True)
    args = parser.parse_args()
    if args.command == "create-admin":
        return create_admin(args.email, args.display_name)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
