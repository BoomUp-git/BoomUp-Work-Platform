from __future__ import annotations

from fastapi import Request

from app.integrations.customer_price.google_sheets import SourceUnavailableError
from app.integrations.customer_price.provider import SourceHealth, SourceState


def source_diagnostics(request: Request) -> SourceHealth:
    provider = request.app.state.customer_price_provider
    if provider is None:
        return SourceHealth(
            SourceState.UNAVAILABLE, None, None, 0, 0, "Source connection is not configured"
        )
    try:
        provider.refresh()
    except SourceUnavailableError:
        pass
    return provider.health()
