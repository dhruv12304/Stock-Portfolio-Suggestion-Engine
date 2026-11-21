"""Unit tests for api.portfolio.

Coverage maps to BUG_REPORT.md:
- A1 / A3 revalue is pure; current value comes from quotes every time.
- A2 missing quote yields ``current_price=None`` + ``quote_source='unavailable'``
        rather than silently using cost basis.
- A5 apply_split_adjustments rescales shares and cost basis.
- C1 / C2 calculate_sharpe_ratios returns None for too-short or zero-vol windows.
- C4 compute_portfolio_value_series tolerates missing tickers in the close matrix.
- C10 total_portfolio_value = equity + cash.
- E2 split between save and resume does not produce phantom P&L.
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from api import market_data, portfolio
from api.market_data import Quote
from api.portfolio import (
    HoldingBlueprint,
    apply_split_adjustments,
    build_basket,
    calculate_sharpe_ratios,
    compute_portfolio_value_series,
    revalue,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------
def _quote(ticker: str, price: float | None, source: str = "live") -> Quote:
    return Quote(
        ticker=ticker,
        price=price,
        source=source,  # type: ignore[arg-type]
        as_of=datetime.now(timezone.utc),
    )


def _two_holding_basket(prices=None, *, amount: float = 10_000.0):
    prices = prices or {"AAPL": 200.0, "MSFT": 400.0}
    blueprints = [
        HoldingBlueprint("AAPL", "Apple Inc.", weight=0.6, sector="Tech", is_etf=False),
        HoldingBlueprint("MSFT", "Microsoft Corp.", weight=0.4, sector="Tech", is_etf=False),
    ]
    return build_basket(
        investment_amount=amount,
        blueprints=blueprints,
        prices=prices,
        strategies=["Quality Investing"],
        strategy_weights=[1.0],
    )


# ---------------------------------------------------------------------------
# build_basket
# ---------------------------------------------------------------------------
def test_build_basket_whole_share_rounding():
    basket = _two_holding_basket(amount=10_000)
    holdings = {h.ticker: h for h in basket.holdings}
    # 10000 * 0.6 = 6000 / 200 = 30 shares of AAPL
    assert holdings["AAPL"].shares == 30
    # 10000 * 0.4 = 4000 / 400 = 10 shares of MSFT
    assert holdings["MSFT"].shares == 10


def test_build_basket_missing_price_yields_zero_shares():
    basket = build_basket(
        investment_amount=10_000,
        blueprints=[
            HoldingBlueprint("AAPL", "Apple", 0.5),
            HoldingBlueprint("XXX", "Mystery", 0.5),
        ],
        prices={"AAPL": 200.0},
    )
    by_ticker = {h.ticker: h for h in basket.holdings}
    assert by_ticker["AAPL"].shares == 25
    assert by_ticker["XXX"].shares == 0
    assert by_ticker["XXX"].initial_price_unadjusted == 0.0


def test_build_basket_negative_amount_raises():
    with pytest.raises(ValueError):
        build_basket(
            investment_amount=-1,
            blueprints=[HoldingBlueprint("AAPL", "Apple", 1.0)],
            prices={"AAPL": 100.0},
        )


def test_build_basket_empty_blueprints_returns_empty_basket():
    basket = build_basket(10_000, [], {})
    assert basket.holdings == ()
    assert basket.investment_amount == 10_000.0


# ---------------------------------------------------------------------------
# revalue (A1, A2, A3, C10)
# ---------------------------------------------------------------------------
def test_revalue_recomputes_current_value_from_quotes():
    basket = _two_holding_basket()  # AAPL 30 @ 200, MSFT 10 @ 400
    # AAPL up to 220, MSFT down to 380
    quotes = {"AAPL": _quote("AAPL", 220.0), "MSFT": _quote("MSFT", 380.0)}
    valuation = revalue(basket, quotes)

    assert valuation.summary.current_portfolio_value == pytest.approx(30 * 220 + 10 * 380)
    assert valuation.summary.total_invested == pytest.approx(30 * 200 + 10 * 400)
    assert valuation.summary.unrealized_pnl == pytest.approx(30 * 20 + 10 * -20)
    assert valuation.summary.unrealized_pnl_pct == pytest.approx(
        valuation.summary.unrealized_pnl / valuation.summary.total_invested
    )


def test_revalue_is_pure_repeat_yields_same_numbers():
    """A3: no hidden state. Same quotes -> same valuation each time."""

    basket = _two_holding_basket()
    quotes = {"AAPL": _quote("AAPL", 210.0), "MSFT": _quote("MSFT", 410.0)}
    a = revalue(basket, quotes)
    b = revalue(basket, quotes)
    assert a.summary == b.summary


def test_revalue_missing_quote_marks_unavailable_not_cost():
    """A2: when quote is missing, the row must NOT silently use cost basis."""

    basket = _two_holding_basket()
    quotes = {"AAPL": _quote("AAPL", 220.0)}  # MSFT missing
    valuation = revalue(basket, quotes)

    rows = {r.ticker: r for r in valuation.rows}
    assert rows["MSFT"].current_price is None
    assert rows["MSFT"].quote_source == "unavailable"
    # Current value contribution from MSFT is unknown, not cost basis or zero.
    assert rows["MSFT"].current_value is None
    assert rows["MSFT"].unrealized_pnl is None
    assert valuation.summary.current_portfolio_value == pytest.approx(30 * 220)
    assert valuation.summary.unrealized_pnl is None
    assert valuation.summary.unrealized_pnl_pct is None


def test_revalue_quote_with_none_price_marks_unavailable():
    basket = _two_holding_basket()
    quotes = {
        "AAPL": _quote("AAPL", 220.0),
        "MSFT": _quote("MSFT", None, source="unavailable"),
    }
    valuation = revalue(basket, quotes)
    rows = {r.ticker: r for r in valuation.rows}
    assert rows["MSFT"].quote_source == "unavailable"
    assert rows["MSFT"].current_value is None
    assert rows["MSFT"].unrealized_pnl is None


def test_revalue_total_portfolio_value_includes_cash():
    """C10: equity + leftover cash."""

    basket = build_basket(
        investment_amount=10_000,
        blueprints=[HoldingBlueprint("AAPL", "Apple", 0.5)],  # only 50% allocated
        prices={"AAPL": 200.0},
    )
    quotes = {"AAPL": _quote("AAPL", 220.0)}
    v = revalue(basket, quotes)

    assert v.summary.current_portfolio_value == pytest.approx(25 * 220)  # equity
    assert v.summary.leftover_cash == pytest.approx(10_000 - 25 * 200)
    assert v.summary.total_portfolio_value == pytest.approx(
        v.summary.current_portfolio_value + max(v.summary.leftover_cash, 0)
    )


def test_revalue_unrealized_pct_is_none_when_nothing_invested():
    basket = build_basket(
        investment_amount=10_000,
        blueprints=[HoldingBlueprint("XXX", "Mystery", 1.0)],
        prices={},
    )
    quotes = {"XXX": _quote("XXX", None, source="unavailable")}
    v = revalue(basket, quotes)
    assert v.summary.total_invested == 0
    assert v.summary.unrealized_pnl_pct is None


# ---------------------------------------------------------------------------
# apply_split_adjustments (A5)
# ---------------------------------------------------------------------------
def test_apply_split_adjustments_preserves_cost_basis(monkeypatch):
    """E2 scenario from BUG_REPORT.md: 2-for-1 split must not show a 50% loss."""

    split_series = pd.Series(
        [2.0],
        index=pd.to_datetime(["2026-05-10"]),
    )
    monkeypatch.setattr(market_data, "splits_fn", lambda t: split_series)

    basket = build_basket(
        investment_amount=10_000,
        blueprints=[HoldingBlueprint("NVDA", "NVIDIA", 1.0)],
        prices={"NVDA": 200.0},
        built_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
    )

    from datetime import date
    adjusted = apply_split_adjustments(basket, today=date(2026, 5, 16))
    holding = adjusted.holdings[0]
    assert holding.shares == 100  # 50 -> 100 after 2-for-1
    assert holding.initial_price_unadjusted == pytest.approx(100.0)
    # Cost basis preserved.
    assert holding.shares * holding.initial_price_unadjusted == pytest.approx(10_000)


def test_apply_split_adjustments_no_op_when_today_equal_built_at():
    basket = build_basket(
        investment_amount=10_000,
        blueprints=[HoldingBlueprint("NVDA", "NVIDIA", 1.0)],
        prices={"NVDA": 200.0},
        built_at=datetime(2026, 5, 16, tzinfo=timezone.utc),
    )
    from datetime import date
    adjusted = apply_split_adjustments(basket, today=date(2026, 5, 16))
    assert adjusted is basket or adjusted.holdings == basket.holdings


# ---------------------------------------------------------------------------
# Portfolio value series (C4)
# ---------------------------------------------------------------------------
def test_compute_portfolio_value_series_basic():
    basket = _two_holding_basket()  # 30 AAPL, 10 MSFT
    dates = pd.date_range("2026-05-12", periods=5, freq="B")
    matrix = pd.DataFrame(
        {
            "AAPL": [200, 201, 202, 203, 204],
            "MSFT": [400, 402, 404, 406, 408],
        },
        index=dates,
    )
    series = compute_portfolio_value_series(basket, matrix)
    assert len(series) == 5
    assert series.iloc[0] == pytest.approx(30 * 200 + 10 * 400)
    assert series.iloc[-1] == pytest.approx(30 * 204 + 10 * 408)


def test_compute_portfolio_value_series_tolerates_missing_columns():
    basket = _two_holding_basket()  # AAPL + MSFT
    dates = pd.date_range("2026-05-12", periods=3, freq="B")
    matrix = pd.DataFrame({"AAPL": [200, 201, 202]}, index=dates)
    series = compute_portfolio_value_series(basket, matrix)
    # MSFT missing -> contribution is 0; series is AAPL-only.
    assert (series == matrix["AAPL"] * 30).all()


def test_compute_portfolio_value_series_empty_when_no_holdings():
    basket = build_basket(10_000, [], {})
    matrix = pd.DataFrame({"AAPL": [1, 2, 3]})
    assert compute_portfolio_value_series(basket, matrix).empty


# ---------------------------------------------------------------------------
# Sharpe ratios (C1, C2)
# ---------------------------------------------------------------------------
def _drifting_series(n: int, daily_mean: float = 0.0005, daily_vol: float = 0.01, seed: int = 42) -> pd.Series:
    rng = np.random.default_rng(seed)
    returns = rng.normal(daily_mean, daily_vol, size=n)
    values = 10_000 * np.cumprod(1 + returns)
    return pd.Series(values, index=pd.date_range("2024-01-01", periods=n, freq="B"))


def test_sharpe_returns_none_for_insufficient_samples():
    series = _drifting_series(10)
    result = calculate_sharpe_ratios(series, risk_free=0.04)
    assert result["3m"] is None
    assert result["6m"] is None
    assert result["1y"] is None
    assert result["2y"] is None


def test_sharpe_returns_dict_when_samples_sufficient():
    series = _drifting_series(260)
    result = calculate_sharpe_ratios(series, risk_free=0.04)
    assert result["3m"] is not None
    assert result["6m"] is not None
    assert result["1y"] is not None
    # 260 samples >= 240 (the 2y threshold) so 2y is now reportable too.
    assert result["2y"] is not None
    assert "sharpe" in result["1y"]
    assert result["1y"]["samples"] >= 120


def test_sharpe_returns_none_just_below_2y_threshold():
    series = _drifting_series(200)
    result = calculate_sharpe_ratios(series, risk_free=0.04)
    # 200 samples is below the 240 minimum for 2y.
    assert result["2y"] is None
    # ...but above the 1y minimum of 120, so 1y is reportable.
    assert result["1y"] is not None


def test_sharpe_returns_none_when_volatility_zero():
    """C2: a flat series has zero volatility; Sharpe must be None, not 0.0."""

    series = pd.Series(
        [10_000.0] * 200,
        index=pd.date_range("2024-01-01", periods=200, freq="B"),
    )
    result = calculate_sharpe_ratios(series, risk_free=0.04)
    for key in ("3m", "6m"):
        assert result[key] is None


def test_sharpe_handles_none_input():
    result = calculate_sharpe_ratios(None, risk_free=0.04)
    assert all(v is None for v in result.values())


def test_sharpe_handles_empty_series():
    result = calculate_sharpe_ratios(pd.Series(dtype="float64"), risk_free=0.04)
    assert all(v is None for v in result.values())
