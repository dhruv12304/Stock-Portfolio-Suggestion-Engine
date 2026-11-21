"""Unit tests for api.market_data with mocked yfinance.

Coverage maps to BUG_REPORT.md:
- A2 every quote carries a source (live / close_fallback / unavailable);
     fallback is never silent.
- C3 risk-free rate rejects 0, negative, or absurdly large values from ^IRX.
- C4 history matrix is wide+ffill, never zero-filled.
- D2 missing tickers are reported on MarketDataStatus instead of being silent.
- D3 single-ticker call path no longer has special-case branches that misalign.
- Section E scenarios E3, E4, E11, E13 (failure / delisted / rate-limit / bad rf).
"""

from __future__ import annotations

import asyncio
from datetime import date

import pandas as pd
import pytest

from api import market_data
from api.market_data import (
    MarketDataStream,
    Quote,
    apply_corporate_actions,
    clear_cache,
    current_prices,
    history,
    risk_free_rate,
)


# ---------------------------------------------------------------------------
# Helpers for synthesizing yfinance responses
# ---------------------------------------------------------------------------
def _make_grouped_history(closes_by_ticker: dict[str, list[float]], days: int = 5) -> pd.DataFrame:
    """Build a yfinance-style group_by='ticker' multi-index DataFrame."""

    dates = pd.date_range(end="2026-05-16", periods=days, freq="B")
    frames = {}
    for ticker, closes in closes_by_ticker.items():
        padded = (closes + [closes[-1]] * days)[:days] if closes else [None] * days
        frame = pd.DataFrame(
            {
                "Open": padded,
                "High": padded,
                "Low": padded,
                "Close": padded,
                "Adj Close": padded,
                "Volume": [1_000_000] * days,
            },
            index=dates,
        )
        frames[ticker] = frame
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, axis=1)


def _make_single_history(closes: list[float], days: int = 5) -> pd.DataFrame:
    dates = pd.date_range(end="2026-05-16", periods=days, freq="B")
    padded = (closes + [closes[-1]] * days)[:days] if closes else [None] * days
    return pd.DataFrame(
        {
            "Open": padded,
            "High": padded,
            "Low": padded,
            "Close": padded,
            "Adj Close": padded,
            "Volume": [1_000_000] * days,
        },
        index=dates,
    )


# ---------------------------------------------------------------------------
# current_prices
# ---------------------------------------------------------------------------
def test_current_prices_returns_live_quotes(monkeypatch):
    def fake_fast_info(ticker):
        prices = {"AAPL": 220.5, "MSFT": 410.1}
        return {"last_price": prices[ticker]}

    monkeypatch.setattr(market_data, "fast_info_fn", fake_fast_info)
    monkeypatch.setattr(market_data, "download_fn", lambda **kw: pd.DataFrame())

    quotes = current_prices(["AAPL", "MSFT"], use_cache=False)

    assert set(quotes.keys()) == {"AAPL", "MSFT"}
    assert quotes["AAPL"].price == pytest.approx(220.5)
    assert quotes["AAPL"].source == "live"
    assert quotes["MSFT"].source == "live"
    assert quotes["AAPL"].error is None


def test_current_prices_normalizes_ticker_casing(monkeypatch):
    captured = []

    def fake_fast_info(ticker):
        captured.append(ticker)
        return {"last_price": 100.0}

    monkeypatch.setattr(market_data, "fast_info_fn", fake_fast_info)
    monkeypatch.setattr(market_data, "download_fn", lambda **kw: pd.DataFrame())

    quotes = current_prices([" aapl ", "MSFT", "msft", None])
    assert list(quotes.keys()) == ["AAPL", "MSFT"]
    assert captured == ["AAPL", "MSFT"]


def test_current_prices_falls_back_to_close_with_explicit_source(monkeypatch):
    def failing_fast_info(ticker):
        raise RuntimeError("rate limit")

    def fake_download(**kwargs):
        return _make_grouped_history({"AAPL": [218.0, 219.0]})

    monkeypatch.setattr(market_data, "fast_info_fn", failing_fast_info)
    monkeypatch.setattr(market_data, "download_fn", fake_download)

    quotes = current_prices(["AAPL"], use_cache=False)
    assert quotes["AAPL"].price == pytest.approx(219.0)
    assert quotes["AAPL"].source == "close_fallback"
    assert quotes["AAPL"].error is None


def test_current_prices_marks_unavailable_when_everything_fails(monkeypatch):
    """E3 (delisted) + E4 (outage): never silently return cost basis."""

    def failing_fast_info(ticker):
        return {}

    def failing_download(**kwargs):
        return pd.DataFrame()

    monkeypatch.setattr(market_data, "fast_info_fn", failing_fast_info)
    monkeypatch.setattr(market_data, "download_fn", failing_download)

    quotes = current_prices(["GHOST"], use_cache=False)
    quote = quotes["GHOST"]
    assert quote.price is None
    assert quote.source == "unavailable"
    assert quote.error is not None


def test_current_prices_rejects_zero_or_negative_live(monkeypatch):
    def fake_fast_info(ticker):
        return {"last_price": 0.0}

    def fake_download(**kwargs):
        return _make_grouped_history({"AAPL": [218.0]})

    monkeypatch.setattr(market_data, "fast_info_fn", fake_fast_info)
    monkeypatch.setattr(market_data, "download_fn", fake_download)

    quotes = current_prices(["AAPL"], use_cache=False)
    # A zero live price must not be accepted; close fallback should kick in.
    assert quotes["AAPL"].source == "close_fallback"
    assert quotes["AAPL"].price == pytest.approx(218.0)


def test_current_prices_uses_cache_on_second_call(monkeypatch):
    calls = []

    def fake_fast_info(ticker):
        calls.append(ticker)
        return {"last_price": 100.0}

    monkeypatch.setattr(market_data, "fast_info_fn", fake_fast_info)
    monkeypatch.setattr(market_data, "download_fn", lambda **kw: pd.DataFrame())

    current_prices(["AAPL"])
    current_prices(["AAPL"])
    assert calls == ["AAPL"]


def test_current_prices_ignores_nan_or_inf(monkeypatch):
    monkeypatch.setattr(
        market_data, "fast_info_fn", lambda t: {"last_price": float("nan")}
    )

    def fake_download(**kwargs):
        return _make_grouped_history({"AAPL": [218.0]})

    monkeypatch.setattr(market_data, "download_fn", fake_download)

    quotes = current_prices(["AAPL"], use_cache=False)
    assert quotes["AAPL"].source == "close_fallback"


# ---------------------------------------------------------------------------
# history
# ---------------------------------------------------------------------------
def test_history_builds_wide_matrix_and_ffill(monkeypatch):
    def fake_download(**kwargs):
        return _make_grouped_history(
            {"AAPL": [200, 201, 202, None, 205], "MSFT": [400, 401, None, 404, 405]},
            days=5,
        )

    monkeypatch.setattr(market_data, "download_fn", fake_download)

    matrix, status = history(["AAPL", "MSFT"], period="5d")
    assert list(matrix.columns) == ["AAPL", "MSFT"]
    assert matrix.shape == (5, 2)
    # ffill carried 202 forward into the day where AAPL was None.
    assert matrix["AAPL"].iloc[3] == 202
    assert matrix["MSFT"].iloc[2] == 401
    assert status.missing == []


def test_history_reports_missing_instead_of_zero_filling(monkeypatch):
    """C4 regression: a missing ticker must not become zero in the sum."""

    def fake_download(**kwargs):
        return _make_grouped_history({"AAPL": [200, 201, 202]}, days=3)

    monkeypatch.setattr(market_data, "download_fn", fake_download)

    matrix, status = history(["AAPL", "NEWIPO"], period="5d")
    assert list(matrix.columns) == ["AAPL"]
    assert status.missing == ["NEWIPO"]


def test_history_rejects_unsupported_period():
    with pytest.raises(ValueError):
        history(["AAPL"], period="bogus")


def test_history_supports_1mo(monkeypatch):
    """The plan adds 1M as a first-class period."""

    def fake_download(**kwargs):
        return _make_grouped_history({"AAPL": [200, 205]}, days=22)

    monkeypatch.setattr(market_data, "download_fn", fake_download)

    matrix, status = history(["AAPL"], period="1mo")
    assert not matrix.empty
    assert status.missing == []


def test_history_returns_empty_when_download_fails(monkeypatch):
    def fake_download(**kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(market_data, "download_fn", fake_download)

    matrix, status = history(["AAPL", "MSFT"], period="5d")
    assert matrix.empty
    assert status.missing == ["AAPL", "MSFT"]


# ---------------------------------------------------------------------------
# risk_free_rate
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw, expected",
    [
        (5.21, 0.0521),
        (4.0, 0.04),
        (0, 0.036),  # zero rejected -> fallback
        (-1.0, 0.036),  # negative rejected
        (250.0, 0.036),  # absurd value rejected
        (None, 0.036),
    ],
)
def test_risk_free_rate_sanity_bounds(monkeypatch, raw, expected):
    def fake_fast_info(ticker):
        assert ticker == "^IRX"
        return {"lastPrice": raw}

    monkeypatch.setattr(market_data, "fast_info_fn", fake_fast_info)
    rate = risk_free_rate()
    assert rate == pytest.approx(expected, rel=1e-6)


def test_risk_free_rate_handles_exception(monkeypatch):
    def raising_fast_info(ticker):
        raise RuntimeError("rate limit")

    monkeypatch.setattr(market_data, "fast_info_fn", raising_fast_info)
    assert risk_free_rate() == pytest.approx(0.036)


# ---------------------------------------------------------------------------
# apply_corporate_actions
# ---------------------------------------------------------------------------
def test_apply_corporate_actions_handles_2_for_1_split(monkeypatch):
    """E2: a 2-for-1 split between buy date and today must not look like a loss."""

    split_series = pd.Series(
        [2.0],
        index=pd.to_datetime(["2026-05-15"]),
    )
    monkeypatch.setattr(market_data, "splits_fn", lambda t: split_series)

    shares, basis_factor = apply_corporate_actions(
        as_of=date(2026, 5, 1), ticker="NVDA", original_shares=50
    )
    assert shares == 100
    assert basis_factor == pytest.approx(0.5)


def test_apply_corporate_actions_ignores_splits_before_buy(monkeypatch):
    split_series = pd.Series(
        [2.0],
        index=pd.to_datetime(["2024-05-15"]),
    )
    monkeypatch.setattr(market_data, "splits_fn", lambda t: split_series)

    shares, basis_factor = apply_corporate_actions(
        as_of=date(2026, 5, 1), ticker="NVDA", original_shares=50
    )
    assert shares == 50
    assert basis_factor == 1.0


def test_apply_corporate_actions_compounds_multiple(monkeypatch):
    split_series = pd.Series(
        [2.0, 3.0],
        index=pd.to_datetime(["2026-05-10", "2026-05-15"]),
    )
    monkeypatch.setattr(market_data, "splits_fn", lambda t: split_series)

    shares, basis_factor = apply_corporate_actions(
        as_of=date(2026, 5, 1), ticker="X", original_shares=10
    )
    assert shares == 60  # 10 * 2 * 3
    assert basis_factor == pytest.approx(1 / 6)


def test_apply_corporate_actions_empty_or_failing(monkeypatch):
    monkeypatch.setattr(market_data, "splits_fn", lambda t: pd.Series(dtype="float64"))
    shares, basis_factor = apply_corporate_actions(date(2026, 5, 1), "X", 10)
    assert (shares, basis_factor) == (10, 1.0)

    def raising(_):
        raise RuntimeError("boom")

    monkeypatch.setattr(market_data, "splits_fn", raising)
    assert apply_corporate_actions(date(2026, 5, 1), "X", 10) == (10, 1.0)


# ---------------------------------------------------------------------------
# MarketDataStream
# ---------------------------------------------------------------------------
def _fake_quote(ticker: str, price: float, source="live") -> Quote:
    return Quote(
        ticker=ticker,
        price=price,
        source=source,
        as_of=pd.Timestamp.now("UTC").to_pydatetime(),
    )


@pytest.mark.asyncio
async def test_stream_delivers_quotes_to_subscriber():
    tick_count = {"n": 0}

    def fake_prices(tickers):
        tick_count["n"] += 1
        return {t: _fake_quote(t, 100.0 + tick_count["n"]) for t in tickers}

    stream = MarketDataStream(poll_seconds=0.02, price_fn=fake_prices)
    await stream.start()
    try:
        queue = await stream.subscribe("port-1", ["AAPL"])
        first = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert "AAPL" in first
        assert first["AAPL"].price >= 101.0
    finally:
        await stream.stop()


@pytest.mark.asyncio
async def test_stream_batches_across_subscribers():
    """A single price_fn call covers the union of all subscribed tickers."""

    seen_batches: list[set[str]] = []

    def fake_prices(tickers):
        seen_batches.append(set(tickers))
        return {t: _fake_quote(t, 1.0) for t in tickers}

    stream = MarketDataStream(poll_seconds=0.02, price_fn=fake_prices)
    await stream.start()
    try:
        await stream.subscribe("port-a", ["AAPL", "MSFT"])
        await stream.subscribe("port-b", ["MSFT", "GOOG"])
        await asyncio.sleep(0.08)
    finally:
        await stream.stop()

    # The polling loop should have called price_fn with the union at least once.
    assert any(batch == {"AAPL", "GOOG", "MSFT"} for batch in seen_batches)


@pytest.mark.asyncio
async def test_stream_unsubscribe_stops_pushes():
    def fake_prices(tickers):
        return {t: _fake_quote(t, 1.0) for t in tickers}

    stream = MarketDataStream(poll_seconds=0.02, price_fn=fake_prices)
    await stream.start()
    try:
        queue = await stream.subscribe("port-c", ["AAPL"])
        await asyncio.wait_for(queue.get(), timeout=1.0)
        await stream.unsubscribe("port-c")
        await asyncio.sleep(0.05)
        # New ticks must not be delivered to a queue that has been removed.
        assert queue.qsize() <= 1
    finally:
        await stream.stop()


@pytest.mark.asyncio
async def test_stream_resilient_to_price_fn_failure():
    failures = {"n": 0}

    def flaky(tickers):
        failures["n"] += 1
        if failures["n"] < 3:
            raise RuntimeError("transient")
        return {t: _fake_quote(t, 99.0) for t in tickers}

    stream = MarketDataStream(poll_seconds=0.02, price_fn=flaky)
    await stream.start()
    try:
        queue = await stream.subscribe("port-d", ["AAPL"])
        msg = await asyncio.wait_for(queue.get(), timeout=2.0)
        assert msg["AAPL"].price == 99.0
    finally:
        await stream.stop()


@pytest.mark.asyncio
async def test_stream_start_is_idempotent():
    stream = MarketDataStream(poll_seconds=0.02, price_fn=lambda t: {})
    await stream.start()
    first_task = stream._task  # noqa: SLF001 - introspecting for the test
    await stream.start()
    assert stream._task is first_task  # noqa: SLF001
    await stream.stop()


@pytest.mark.asyncio
async def test_stream_drops_oldest_when_queue_full():
    def fake_prices(tickers):
        return {t: _fake_quote(t, 1.0) for t in tickers}

    stream = MarketDataStream(
        poll_seconds=0.02, price_fn=fake_prices, queue_maxsize=2
    )
    await stream.start()
    try:
        queue = await stream.subscribe("slow", ["AAPL"])
        # Sleep without consuming so the queue overflows; should never block.
        await asyncio.sleep(0.12)
        assert queue.qsize() <= 2
    finally:
        await stream.stop()
