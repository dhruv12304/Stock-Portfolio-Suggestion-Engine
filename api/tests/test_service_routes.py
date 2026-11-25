"""HTTP-level tests for the FastAPI service.

Uses ``fastapi.testclient.TestClient`` for the REST surface and the
``websocket_connect`` context manager for the streaming endpoint. Every
yfinance call is mocked so the tests are network-free and deterministic.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Iterable

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from fastapi import WebSocketDisconnect

from api import market_data, storage, strategies as strategies_module
from api.market_data import Quote
from api.service.main import create_app
from api.service import schemas
from api.service.routes import stream as stream_routes


# ---------------------------------------------------------------------------
# Synthetic upstream data
# ---------------------------------------------------------------------------
@pytest.fixture
def synthetic_market(monkeypatch):
    """Replace every yfinance entry point with deterministic fakes."""

    prices = {
        "QQQ": 480.0, "VUG": 380.0, "NVDA": 950.0, "AMZN": 200.0,
        "META": 510.0, "TSLA": 220.0, "CRWD": 320.0, "MELI": 1700.0,
        "AAPL": 220.0, "V": 290.0, "JNJ": 160.0, "PG": 170.0, "COST": 880.0,
        "QUAL": 180.0, "SPHQ": 90.0,
        "VOO": 540.0, "VTI": 290.0, "VT": 130.0, "DIA": 410.0,
        "IWM": 230.0, "BND": 73.0, "IXUS": 70.0, "ILTB": 55.0,
        "VTV": 175.0, "VLUE": 100.0, "BRK-B": 460.0, "JPM": 215.0,
        "BAC": 38.0, "CVX": 165.0, "XOM": 115.0, "VZ": 42.0,
        "ESGU": 130.0, "ESGV": 95.0, "SUSA": 110.0, "ICLN": 14.0,
        "MSFT": 415.0, "ADBE": 540.0, "FSLR": 230.0, "WM": 220.0,
        "NSRGY": 90.0, "CRM": 270.0,
    }

    def fake_current_prices(tickers, *, use_cache=True):
        now = datetime.now(timezone.utc)
        return {
            t.upper(): Quote(
                ticker=t.upper(),
                price=prices.get(t.upper(), 100.0),
                source="live",
                as_of=now,
            )
            for t in tickers
        }

    def fake_features(tickers):
        return {
            t.upper(): {
                "latest_close": prices.get(t.upper(), 100.0),
                "current_price": prices.get(t.upper(), 100.0),
                "momentum_1m": 0.04, "momentum_3m": 0.10,
                "volatility_3m": 0.25, "avg_volume": 1e7,
                "market_cap": 1e12, "pe_ratio": 30.0,
                "roe": 0.25, "debt_to_equity": 0.5,
                "profit_margin": 0.2, "revenue_growth": 0.15,
                "earnings_growth": 0.2, "expense_ratio": 0.002,
                "sector": "Technology",
                "is_etf": t.upper() in {"QQQ", "VUG", "VOO", "VTI", "VT",
                                        "DIA", "IWM", "BND", "IXUS", "ILTB",
                                        "VTV", "VLUE", "ESGU", "ESGV", "SUSA",
                                        "ICLN", "QUAL", "SPHQ"},
            }
            for t in tickers
        }

    def fake_history(tickers, period):
        from api.market_data import MarketDataStatus

        dates = pd.date_range("2024-01-01", periods=260, freq="B")
        matrix = pd.DataFrame(
            {
                t.upper(): [prices.get(t.upper(), 100.0) * (1 + i * 0.001) for i in range(260)]
                for t in tickers
            },
            index=dates,
        )
        return matrix, MarketDataStatus(quotes={}, as_of=datetime.now(timezone.utc))

    monkeypatch.setattr(market_data, "current_prices", fake_current_prices)
    monkeypatch.setattr(market_data, "fetch_market_features", fake_features)
    monkeypatch.setattr(market_data, "history", fake_history)
    monkeypatch.setattr(market_data, "risk_free_rate", lambda **kw: 0.05)

    from api.service import orchestrator

    monkeypatch.setattr(orchestrator.market_data, "current_prices", fake_current_prices)
    monkeypatch.setattr(orchestrator, "fetch_market_features", fake_features)
    monkeypatch.setattr(orchestrator, "fetch_history", fake_history)
    monkeypatch.setattr(orchestrator, "risk_free_rate", lambda **kw: 0.05)

    return prices


@pytest.fixture
def app_and_client(synthetic_market, monkeypatch):
    # 0.1s polling keeps the WebSocket test fast without flooding upstream.
    monkeypatch.setenv("PORTFOLIO_POLL_SECONDS", "0.1")
    app = create_app()
    with TestClient(app) as client:
        yield app, client


# ---------------------------------------------------------------------------
# Strategy + portfolio CRUD
# ---------------------------------------------------------------------------
def test_strategies_endpoint(app_and_client):
    _, client = app_and_client
    r = client.get("/api/v1/strategies")
    assert r.status_code == 200
    payload = r.json()
    assert {row["name"] for row in payload} == set(strategies_module.get_strategy_names())


def test_healthz(app_and_client):
    _, client = app_and_client
    assert client.get("/healthz").json() == {"status": "ok"}


def test_build_and_get_portfolio(app_and_client):
    _, client = app_and_client
    build = client.post(
        "/api/v1/portfolios/build",
        json={
            "name": "Demo",
            "investment_amount": 25000,
            "strategies": ["Growth Investing"],
            "strategy_weights": [1.0],
        },
    )
    assert build.status_code == 200, build.text
    payload = build.json()
    assert payload["name"] == "Demo"
    assert payload["summary"]["investment_amount"] == 25000
    assert payload["summary"]["current_portfolio_value"] > 0

    portfolio_id = payload["portfolio_id"]
    assert portfolio_id != "unsaved"

    listing = client.get("/api/v1/portfolios").json()
    assert any(item["id"] == portfolio_id and item["name"] == "Demo" for item in listing["items"])
    assert listing["active_id"] == portfolio_id

    fetched = client.get(f"/api/v1/portfolios/{portfolio_id}")
    assert fetched.status_code == 200
    assert fetched.json()["portfolio_id"] == portfolio_id


def test_build_rejects_below_minimum(app_and_client):
    _, client = app_and_client
    r = client.post(
        "/api/v1/portfolios/build",
        json={"investment_amount": 100, "strategies": ["Growth Investing"]},
    )
    assert r.status_code == 422


def test_build_rejects_too_many_strategies(app_and_client):
    _, client = app_and_client
    r = client.post(
        "/api/v1/portfolios/build",
        json={"investment_amount": 10000, "strategies": ["a", "b", "c"]},
    )
    assert r.status_code == 422


def test_rename_and_duplicate(app_and_client):
    _, client = app_and_client
    portfolio_id = client.post(
        "/api/v1/portfolios/build",
        json={"investment_amount": 10000, "strategies": ["Index Investing"]},
    ).json()["portfolio_id"]

    renamed = client.patch(
        f"/api/v1/portfolios/{portfolio_id}", json={"name": "Renamed"}
    ).json()
    assert renamed["name"] == "Renamed"

    dup = client.post(
        f"/api/v1/portfolios/{portfolio_id}/duplicate", json={}
    ).json()
    assert dup["id"] != portfolio_id
    assert "(copy)" in dup["name"]


def test_activate_and_delete(app_and_client):
    _, client = app_and_client
    a = client.post(
        "/api/v1/portfolios/build",
        json={"investment_amount": 10000, "strategies": ["Index Investing"]},
    ).json()["portfolio_id"]
    b = client.post(
        "/api/v1/portfolios/build",
        json={"investment_amount": 10000, "strategies": ["Quality Investing"]},
    ).json()["portfolio_id"]

    assert client.post(f"/api/v1/portfolios/{a}/activate").status_code == 204
    assert client.get("/api/v1/portfolios").json()["active_id"] == a

    assert client.delete(f"/api/v1/portfolios/{a}").status_code == 204
    listing = client.get("/api/v1/portfolios").json()
    assert listing["active_id"] == b
    assert all(item["id"] != a for item in listing["items"])


def test_get_portfolio_404_for_unknown_id(app_and_client):
    _, client = app_and_client
    r = client.get("/api/v1/portfolios/does-not-exist")
    assert r.status_code == 404


