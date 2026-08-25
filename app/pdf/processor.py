from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class InvoicePdfMetadata:
    invoice_number: str
    customer_name: str
    page_count: int


class InvoicePdfProcessor(ABC):
    """PDF extraction/rendering boundary. Phase 1A deliberately has no implementation."""

    @abstractmethod
    def inspect(self, source: Path) -> InvoicePdfMetadata:
        raise NotImplementedError

    @abstractmethod
    def render(self, source: Path, destination: Path, decisions: object) -> Path:
        raise NotImplementedError
