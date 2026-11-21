"""Pytest fixtures shared across the api/ test suite."""

from __future__ import annotations

from pathlib import Path

import pytest

from api import market_data, storage


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point storage at a temporary directory for every test.

    Without this, tests would clobber the developer's real data/ folder.
    """

    storage.set_data_dir(tmp_path)
    yield tmp_path
    storage.set_data_dir(storage.DATA_DIR)


@pytest.fixture(autouse=True)
def clear_market_data_cache() -> None:
    """Ensure every test starts with an empty live-quote cache."""

    market_data.clear_cache()
    yield
    market_data.clear_cache()


@pytest.fixture
def sample_payload() -> dict:
    """A minimal valid snapshot payload for storage tests."""

    return {
        "investment_amount": 25000.0,
        "strategies": ["Growth Investing"],
        "holdings": [
            {
                "ticker": "AAPL",
                "name": "Apple Inc.",
                "shares": 50,
                "initial_price": 200.0,
                "weight": 0.4,
            }
        ],
        "selection_details": [],
    }
