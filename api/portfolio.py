"""Pure portfolio composition, valuation, and risk math.

Replaces the conjoined ``build_portfolio`` /
``calculate_historical_sharpe_ratios`` flow in the legacy ``portfolio.py``
with three small, side-effect-free building blocks:

* ``build_basket`` — allocate dollars across blueprint holdings using
  whole-share rounding, with prices supplied by the caller.
* ``revalue`` — recompute current valuation from a fresh quotes map.
  Resolves BUG_REPORT.md A1 + A3: ``current_portfolio_value`` is always
  derived from the latest quotes, never stored alongside cost basis.
* ``apply_split_adjustments`` — adjust shares and cost basis for splits
  that occurred between build time and today. Resolves A5.
* ``calculate_sharpe_ratios`` — enforce minimum-sample thresholds per
  window and return ``None`` rather than a numeric ``0.0`` when the
  estimator is unreliable. Resolves C1 + C2.
* ``compute_portfolio_value_series`` — daily portfolio value from a wide
  close-price matrix; consumes the output of ``market_data.history`` so
  callers never re-invent the cross-ticker alignment that C4 fixed.

All functions are pure: no streamlit, no disk, no yfinance. Inputs are
plain Python types and pandas objects; outputs are dataclasses and dicts.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from typing import Sequence

import pandas as pd

from .market_data import (
    MarketDataStatus,
    Quote,
    QuoteSource,
    apply_corporate_actions,
    risk_free_rate as fetch_risk_free_rate,
)

logger = logging.getLogger(__name__)

# Annualisation constant used for daily-return based metrics. Trading days,
# not calendar days, because the price series excludes weekends/holidays.
TRADING_DAYS_PER_YEAR = 252

SHARPE_WINDOW_TRADING_DAYS: dict[str, int] = {
    "3m": 63,
    "6m": 126,
    "1y": 252,
    "2y": 504,
}

# Minimum sample sizes required before we trust the estimator (BUG_REPORT.md
# C1). Below the threshold the helper returns ``None`` for that window and
# the UI renders ``N/A``.
SHARPE_WINDOW_MIN_SAMPLES: dict[str, int] = {
    "3m": 30,
    "6m": 60,
    "1y": 120,
    "2y": 240,
}


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class HoldingBlueprint:
    """A planned holding before dollar allocation."""

    ticker: str
    name: str
    weight: float
    sector: str = "Unknown"
    is_etf: bool = False


@dataclass(frozen=True)
class Holding:
    """A funded holding inside a built basket."""

    ticker: str
    name: str
    shares: int
    initial_price_unadjusted: float
    weight: float
    sector: str = "Unknown"
    is_etf: bool = False
    as_of: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class Basket:
    """A built portfolio composition. Immutable; valuation is computed lazily."""

    investment_amount: float
    holdings: tuple[Holding, ...]
    strategies: tuple[str, ...]
    strategy_weights: tuple[float, ...]
    built_at: datetime
    selection_details: tuple[dict, ...] = ()

    @property
    def tickers(self) -> tuple[str, ...]:
        return tuple(h.ticker for h in self.holdings)

    @property
    def funded_tickers(self) -> tuple[str, ...]:
        return tuple(h.ticker for h in self.holdings if h.shares > 0)


@dataclass(frozen=True)
class HoldingRow:
    """A priced row produced by ``revalue``."""

    ticker: str
    name: str
    weight: float
    shares: int
    allocated: float
    initial_price: float
    current_price: float | None
    actual_cost: float
    current_value: float | None
    unrealized_pnl: float | None
    unrealized_pnl_pct: float | None
    quote_source: QuoteSource
    quote_as_of: str
    sector: str
    is_etf: bool


@dataclass(frozen=True)
class ValuationSummary:
    """Aggregate totals shown in the dashboard KPI strip."""

    investment_amount: float
    total_invested: float
    leftover_cash: float
    current_portfolio_value: float  # equity only
    total_portfolio_value: float  # equity + leftover cash (C10)
    unrealized_pnl: float | None
    unrealized_pnl_pct: float | None


@dataclass(frozen=True)
class Valuation:
    """The composition + a current valuation snapshot."""

    basket: Basket
    rows: tuple[HoldingRow, ...]
    summary: ValuationSummary
    status: MarketDataStatus

    @property
    def total_invested(self) -> float:
        return self.summary.total_invested

    @property
    def current_value(self) -> float:
        return self.summary.current_portfolio_value

    @property
    def unrealized_pnl(self) -> float | None:
        return self.summary.unrealized_pnl


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------
def build_basket(
    investment_amount: float,
    blueprints: Sequence[HoldingBlueprint],
    prices: dict[str, float],
    *,
    strategies: Sequence[str] = (),
    strategy_weights: Sequence[float] = (),
    selection_details: Sequence[dict] = (),
    built_at: datetime | None = None,
) -> Basket:
    """Allocate dollars across blueprint holdings using whole-share rounding.

    ``prices`` is a ``{ticker: float}`` map of usable prices (typically
    produced by ``market_data.current_prices``). Tickers with no price get
    ``shares = 0`` so the row is preserved for transparency — the caller
    can show a "no price available" badge instead of silently dropping
    the line.
    """

    if investment_amount <= 0:
        raise ValueError("investment_amount must be positive")

    now = built_at or datetime.now(timezone.utc)
    if not blueprints:
        return Basket(
            investment_amount=float(investment_amount),
            holdings=(),
            strategies=tuple(strategies),
            strategy_weights=tuple(strategy_weights),
            built_at=now,
            selection_details=tuple(selection_details),
        )

    holdings: list[Holding] = []
    for blueprint in blueprints:
        price = prices.get(blueprint.ticker)
        if price is None or price <= 0:
            shares = 0
            initial_price = 0.0
        else:
            allocated = float(investment_amount) * float(blueprint.weight)
            shares = int(allocated // price)
            initial_price = float(price)
        holdings.append(
            Holding(
                ticker=blueprint.ticker,
                name=blueprint.name,
                shares=shares,
                initial_price_unadjusted=initial_price,
                weight=float(blueprint.weight),
                sector=blueprint.sector,
                is_etf=blueprint.is_etf,
                as_of=now,
            )
        )

    return Basket(
        investment_amount=float(investment_amount),
        holdings=tuple(holdings),
        strategies=tuple(strategies),
        strategy_weights=tuple(strategy_weights),
        built_at=now,
        selection_details=tuple(selection_details),
    )


# ---------------------------------------------------------------------------
# Valuation (resolves A1, A2, A3, C10)
# ---------------------------------------------------------------------------
def revalue(basket: Basket, quotes: dict[str, Quote]) -> Valuation:
    """Recompute a current valuation snapshot for ``basket`` from ``quotes``.

    Pure function: same inputs always yield the same output. ``rows`` is
    always rebuilt from scratch — there is no stored ``current_value`` to
    go stale (BUG_REPORT.md A1, A3).

    A missing or unavailable quote produces a ``HoldingRow`` whose
    ``current_price`` and value/P&L fields are ``None``. Aggregate equity
    totals exclude those rows rather than silently filling with cost basis
    or pretending the holding is worth zero (BUG_REPORT.md A2).
    """

    now = datetime.now(timezone.utc)
    rows: list[HoldingRow] = []
    total_invested = 0.0
    current_value_equity = 0.0
    any_unavailable = False

    for holding in basket.holdings:
        actual_cost = holding.shares * holding.initial_price_unadjusted
        total_invested += actual_cost

        quote = quotes.get(holding.ticker)
        if quote is None:
            current_price: float | None = None
            quote_source: QuoteSource = "unavailable"
            quote_as_of = now
            holding_value: float | None = None
            holding_pnl: float | None = None
            any_unavailable = True
        elif quote.price is None:
            current_price = None
            quote_source = "unavailable"
            quote_as_of = quote.as_of
            holding_value = None
            holding_pnl = None
            any_unavailable = True
        else:
            current_price = float(quote.price)
            quote_source = quote.source
            quote_as_of = quote.as_of
            holding_value = float(holding.shares) * current_price
            holding_pnl = holding_value - actual_cost

        if holding_value is not None:
            current_value_equity += holding_value
        rows.append(
            HoldingRow(
                ticker=holding.ticker,
                name=holding.name,
                weight=holding.weight,
                shares=holding.shares,
                allocated=basket.investment_amount * holding.weight,
                initial_price=holding.initial_price_unadjusted,
                current_price=current_price,
                actual_cost=actual_cost,
                current_value=holding_value,
                unrealized_pnl=holding_pnl,
                unrealized_pnl_pct=(
                    holding_pnl / actual_cost
                    if holding_pnl is not None and actual_cost > 0
                    else None
                ),
                quote_source=quote_source,
                quote_as_of=quote_as_of.isoformat(),
                sector=holding.sector,
                is_etf=holding.is_etf,
            )
        )

    leftover_cash = basket.investment_amount - total_invested
    if any_unavailable:
        pnl = None
        pnl_pct = None
    else:
        pnl = current_value_equity - total_invested
        pnl_pct = pnl / total_invested if total_invested > 0 else None

    summary = ValuationSummary(
        investment_amount=basket.investment_amount,
        total_invested=total_invested,
        leftover_cash=leftover_cash,
        current_portfolio_value=current_value_equity,
        total_portfolio_value=current_value_equity + max(leftover_cash, 0.0),
        unrealized_pnl=pnl,
        unrealized_pnl_pct=pnl_pct,
    )
    status = MarketDataStatus(quotes=dict(quotes), as_of=now)
    return Valuation(basket=basket, rows=tuple(rows), summary=summary, status=status)


# ---------------------------------------------------------------------------
# Corporate actions (resolves A5)
# ---------------------------------------------------------------------------
def apply_split_adjustments(
    basket: Basket,
    *,
    today: date | None = None,
) -> Basket:
    """Adjust ``basket`` for splits between ``built_at`` and ``today``.

    For each holding, fetches splits via ``market_data.apply_corporate_actions``
    and rescales ``shares`` (up) and ``initial_price_unadjusted`` (down) so
    cost basis is preserved.

    Returns a new ``Basket``; the input is not mutated.
    """

    if not basket.holdings:
        return basket

    today_date = today or datetime.now(timezone.utc).date()
    as_of_date = basket.built_at.date()
    if as_of_date >= today_date:
        return basket

    new_holdings: list[Holding] = []
    for holding in basket.holdings:
        if holding.shares <= 0:
            new_holdings.append(holding)
            continue
        new_shares, basis_factor = apply_corporate_actions(
            as_of=as_of_date,
            ticker=holding.ticker,
            original_shares=holding.shares,
        )
        if new_shares == holding.shares and basis_factor == 1.0:
            new_holdings.append(holding)
            continue
        new_holdings.append(
            replace(
                holding,
                shares=new_shares,
                initial_price_unadjusted=holding.initial_price_unadjusted * basis_factor,
            )
        )

    return replace(basket, holdings=tuple(new_holdings))


# ---------------------------------------------------------------------------
# History (resolves C4)
# ---------------------------------------------------------------------------
def compute_portfolio_value_series(
    basket: Basket,
    close_matrix: pd.DataFrame,
) -> pd.Series:
    """Daily portfolio value series from a wide close-price matrix.

    ``close_matrix`` must come from ``market_data.history`` (or a tested
    equivalent): a DataFrame with a date index, one column per ticker,
    already forward-filled to handle ragged trading-day data.

    The series is calculated as ``Σ shares_i * close_i,t``. Tickers that
    are not in the matrix contribute zero. If no funded ticker has data,
    the function returns an empty series rather than zero-filling
    (BUG_REPORT.md C4).
    """

    if close_matrix.empty or not basket.holdings:
        return pd.Series(dtype="float64", name="portfolio_value")

    shares_map = {h.ticker: float(h.shares) for h in basket.holdings if h.shares > 0}
    if not shares_map:
        return pd.Series(dtype="float64", name="portfolio_value")

    columns = [t for t in shares_map if t in close_matrix.columns]
    if not columns:
        return pd.Series(dtype="float64", name="portfolio_value")

    weights = pd.Series({t: shares_map[t] for t in columns}, dtype="float64")
    series = close_matrix[columns].mul(weights, axis=1).sum(axis=1, min_count=1)
    series.name = "portfolio_value"
    return series.dropna()


# ---------------------------------------------------------------------------
# Sharpe (resolves C1, C2)
# ---------------------------------------------------------------------------
def calculate_sharpe_ratios(
    portfolio_series: pd.Series,
    *,
    risk_free: float | None = None,
) -> dict[str, dict[str, float] | None]:
    """Annualised return, volatility, and Sharpe per window.

    Each window returns either a dict ``{return, volatility, sharpe, samples}``
    or ``None``. ``None`` means the window is not reportable, either
    because the sample is below the minimum threshold (BUG_REPORT.md C1)
    or because the volatility is zero (BUG_REPORT.md C2). Callers should
    render ``N/A`` rather than coercing ``None`` to a numeric.
    """

    if risk_free is None:
        risk_free = fetch_risk_free_rate()

    results: dict[str, dict[str, float] | None] = {key: None for key in SHARPE_WINDOW_TRADING_DAYS}

    if portfolio_series is None:
        return results
    if not isinstance(portfolio_series, pd.Series):
        portfolio_series = pd.Series(portfolio_series, dtype="float64")

    clean = portfolio_series.dropna().astype(float)
    if clean.empty:
        return results

    for key, trading_days in SHARPE_WINDOW_TRADING_DAYS.items():
        window = clean.tail(trading_days)
        min_samples = SHARPE_WINDOW_MIN_SAMPLES[key]
        if len(window) < min_samples:
            results[key] = None
            continue
        daily_returns = window.pct_change().dropna()
        if daily_returns.empty:
            results[key] = None
            continue
        annual_return = float(daily_returns.mean() * TRADING_DAYS_PER_YEAR)
        annual_vol = float(daily_returns.std() * math.sqrt(TRADING_DAYS_PER_YEAR))
        if annual_vol <= 0 or math.isnan(annual_vol):
            results[key] = None
            continue
        sharpe = (annual_return - risk_free) / annual_vol
        if math.isnan(sharpe) or math.isinf(sharpe):
            results[key] = None
            continue
        results[key] = {
            "return": annual_return,
            "volatility": annual_vol,
            "sharpe": float(sharpe),
            "samples": int(len(window)),
        }
    return results
