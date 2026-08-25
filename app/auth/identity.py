from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.models import UserRole


@dataclass(frozen=True)
class Identity:
    user_id: str
    email: str
    display_name: str
    role: UserRole


class IdentityProvider(ABC):
    """Stable boundary for local auth now and a future OIDC/SSO provider."""

    @abstractmethod
    def authenticate(self, email: str, password: str) -> Identity | None:
        raise NotImplementedError
