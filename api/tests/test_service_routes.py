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


# ---------------------------------------------------------------------------
# History + risk
# ---------------------------------------------------------------------------
def test_history_endpoint_supports_1mo(app_and_client):
    _, client = app_and_client
    portfolio_id = client.post(
        "/api/v1/portfolios/build",
        json={"investment_amount": 10000, "strategies": ["Index Investing"]},
    ).json()["portfolio_id"]

    r = client.get(f"/api/v1/portfolios/{portfolio_id}/history?period=1mo")
    assert r.status_code == 200
    assert len(r.json()["points"]) > 0
    assert r.json()["source"] == "backcast"


def test_history_rejects_unsupported_period(app_and_client):
    _, client = app_and_client
    portfolio_id = client.post(
        "/api/v1/portfolios/build",
        json={"investment_amount": 10000, "strategies": ["Index Investing"]},
    ).json()["portfolio_id"]
    r = client.get(f"/api/v1/portfolios/{portfolio_id}/history?period=10y")
    assert r.status_code == 400


def test_history_5d_prefers_recorded_value_history(app_and_client):
    _, client = app_and_client
    portfolio_id = client.post(
        "/api/v1/portfolios/build",
        json={"investment_amount": 10000, "strategies": ["Index Investing"]},
    ).json()["portfolio_id"]

    storage.upsert_value_history(portfolio_id, date="2026-05-12", value=10050.0)
    storage.upsert_value_history(portfolio_id, date="2026-05-13", value=10100.0)

    r = client.get(f"/api/v1/portfolios/{portfolio_id}/history?period=5d")
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "recorded"
    assert len(body["points"]) >= 2
    assert any(
        point["date"] == "2026-05-12" and point["value"] == pytest.approx(10050.0)
        for point in body["points"]
    )
    assert any(
        point["date"] == "2026-05-13" and point["value"] == pytest.approx(10100.0)
        for point in body["points"]
    )


def test_risk_endpoint_returns_sharpe_and_correlation(app_and_client):
    _, client = app_and_client
    portfolio_id = client.post(
        "/api/v1/portfolios/build",
        json={"investment_amount": 25000, "strategies": ["Growth Investing"]},
    ).json()["portfolio_id"]
    r = client.get(f"/api/v1/portfolios/{portfolio_id}/risk?period=1y")
    assert r.status_code == 200
    body = r.json()
    assert "sharpe" in body
    # synthetic series has 260 samples -> 3m / 6m / 1y / 2y all reportable.
    assert body["sharpe"]["1y"]["sharpe"] is not None
    assert body["correlation_matrix"]
    assert body["risk_free_rate"] == pytest.approx(0.05)


def test_risk_1mo_succeeds_with_typical_trading_day_count(app_and_client, monkeypatch):
    """1M Yahoo history is ~20-22 rows; threshold must not require 30."""
    from api.service import orchestrator
    from api.market_data import MarketDataStatus

    _, client = app_and_client

    def short_history(tickers: Iterable[str], period: str):
        n = 22
        dates = pd.date_range("2024-01-01", periods=n, freq="B")
        matrix = pd.DataFrame(
            {t.upper(): [100.0 * (1.001**i) for i in range(n)] for t in tickers},
            index=dates,
        )
        return matrix, MarketDataStatus(quotes={}, as_of=datetime.now(timezone.utc))

    monkeypatch.setattr(orchestrator, "fetch_history", short_history)

    portfolio_id = client.post(
        "/api/v1/portfolios/build",
        json={"investment_amount": 25000, "strategies": ["Growth Investing"]},
    ).json()["portfolio_id"]
    r = client.get(f"/api/v1/portfolios/{portfolio_id}/risk?period=1mo")
    assert r.status_code == 200
    body = r.json()
    assert body.get("note") != "insufficient_history"
    assert len(body["tickers"]) >= 1
    assert body["correlation_matrix"]
def test_stream_sends_snapshot_and_tick(app_and_client):
    """The stream must deliver an initial snapshot AND at least one
    polling-driven update within a reasonable window."""

    _, client = app_and_client
    portfolio_id = client.post(
        "/api/v1/portfolios/build",
        json={"investment_amount": 25000, "strategies": ["Growth Investing"]},
    ).json()["portfolio_id"]

    with client.websocket_connect(f"/api/v1/portfolios/{portfolio_id}/stream") as ws:
        first = json.loads(ws.receive_text())
        assert first["type"] == "valuation"
        assert first["valuation"]["portfolio_id"] == portfolio_id

        # The polling loop should emit at least one more valuation frame
        # quickly (poll cadence is 0.1s in the fixture). Skip pings if any.
        seen_tick = False
        for _ in range(30):
            frame = json.loads(ws.receive_text())
            if frame["type"] == "valuation":
                seen_tick = True
                assert frame["valuation"]["portfolio_id"] == portfolio_id
                break
        assert seen_tick, "did not receive a tick-driven valuation frame"


def test_stream_rejects_unknown_portfolio(app_and_client):
    _, client = app_and_client
    with client.websocket_connect("/api/v1/portfolios/missing/stream") as ws:
        frame = json.loads(ws.receive_text())
        assert frame["type"] == "error"
        assert "portfolio_not_found" in frame["message"]


@pytest.mark.asyncio
async def test_stream_disconnect_during_send_does_not_log_crash(monkeypatch, caplog):
    class FakeStream:
        def __init__(self):
            self.unsubscribed: list[str] = []

        async def subscribe(self, subscriber_id: str, tickers: list[str]):
            queue: asyncio.Queue[dict[str, Quote]] = asyncio.Queue()
            await queue.put({"NVDA": Quote("NVDA", 950.0, "live", datetime.now(timezone.utc))})
            return queue

        async def unsubscribe(self, subscriber_id: str):
            self.unsubscribed.append(subscriber_id)

    class FakeWebSocket:
        def __init__(self, stream):
            self.app = SimpleNamespace(state=SimpleNamespace(market_stream=stream))
            self.sent_count = 0
            self.closed = False

        async def accept(self):
            return None

        async def send_text(self, _payload: str):
            self.sent_count += 1
            if self.sent_count >= 2:
                self.closed = True
                raise RuntimeError(
                    "Unexpected ASGI message 'websocket.send', after sending "
                    "'websocket.close' or response already completed."
                )

        async def receive_text(self):
            raise WebSocketDisconnect()

        async def close(self, code: int = 1000):
            self.closed = True
            return None

    def make_valuation(portfolio_id: str) -> schemas.ValuationOut:
        return schemas.ValuationOut(
            portfolio_id=portfolio_id,
            name="Demo",
            built_at=datetime.now(timezone.utc).isoformat(),
            investment_amount=25000.0,
            strategies=["Growth Investing"],
            strategy_weights=[1.0],
            rows=[
                schemas.HoldingRowOut(
                    ticker="NVDA",
                    name="NVIDIA Corp.",
                    weight=1.0,
                    shares=10,
                    allocated=9500.0,
                    initial_price=950.0,
                    current_price=950.0,
                    actual_cost=9500.0,
                    current_value=9500.0,
                    unrealized_pnl=0.0,
                    unrealized_pnl_pct=0.0,
                    quote_source="live",
                    quote_as_of=datetime.now(timezone.utc).isoformat(),
                    sector="Technology",
                    is_etf=False,
                )
            ],
            summary=schemas.SummaryOut(
                investment_amount=25000.0,
                total_invested=9500.0,
                leftover_cash=15500.0,
                current_portfolio_value=9500.0,
                total_portfolio_value=25000.0,
                unrealized_pnl=0.0,
                unrealized_pnl_pct=0.0,
            ),
            status=schemas.MarketDataStatusOut(
                as_of=datetime.now(timezone.utc).isoformat(),
                any_fallback=False,
                any_unavailable=False,
                missing=[],
            ),
            selection_details=[],
        )

    fake_stream = FakeStream()
    fake_socket = FakeWebSocket(fake_stream)

    monkeypatch.setattr(stream_routes.orchestrator, "get_portfolio", lambda _: make_valuation("demo"))
    monkeypatch.setattr(
        stream_routes.orchestrator,
        "revalue_portfolio_from_quotes",
        lambda _portfolio_id, _quotes: make_valuation("demo"),
    )

    caplog.set_level(logging.WARNING)

    await stream_routes.portfolio_stream(fake_socket, "demo")

    assert fake_socket.closed
    assert len(fake_stream.unsubscribed) == 1
    assert not any(
        "WebSocket loop crashed for demo" in record.getMessage()
        for record in caplog.records
    )
