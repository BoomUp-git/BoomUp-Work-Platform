from __future__ import annotations

import os

import pytest

from app.config import Settings
from app.integrations.customer_price.google_sheets import (
    PRICE_RULES_RANGE,
    GoogleSheetsGateway,
    normalize_price_rules,
)


@pytest.mark.integration
def test_real_customer_price_manager_read_only_contract():
    if not os.getenv("GOOGLE_CUSTOMER_PRICE_SPREADSHEET_ID"):
        pytest.skip("Production Google credentials are not configured in this test process")
    settings = Settings.from_env()
    values = GoogleSheetsGateway(settings).read_values(
        settings.google_customer_price_spreadsheet_id, PRICE_RULES_RANGE
    )
    snapshot = normalize_price_rules(values)
    assert snapshot.source_row_count > 0
    assert snapshot.rules
