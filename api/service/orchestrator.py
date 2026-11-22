"""Orchestration glue between the pure modules and HTTP/WS surfaces.

Each function in this module is *not* pure (it makes yfinance calls and
hits the storage layer), but it is composed entirely of calls into the
pure modules. The HTTP/WS routes only call into this module, never into
yfinance or storage directly.
"""

from __future__ import annotations

import logging
import math
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Iterable, Sequence
from zoneinfo import ZoneInfo

import pandas as pd

from api import market_data, portfolio, storage, strategies
from api.market_data import (
    MarketDataStatus,
    Quote,
    fetch_market_features,
    history as fetch_history,
    risk_free_rate,
)
from api.portfolio import (
    Basket,
    HoldingBlueprint,
    SHARPE_WINDOW_TRADING_DAYS,
    apply_split_adjustments,
    build_basket,
    calculate_sharpe_ratios,
    compute_portfolio_value_series,
    revalue,
)
from api.service import schemas
from api.storage import SnapshotMeta

logger = logging.getLogger(__name__)
NY_TZ = ZoneInfo("America/New_York")

# Per-ticker risk/return scatter + correlation need enough daily rows in the
# *requested* period. Yahoo ~1mo is ~20-22 trading days; requiring 30 blanked
# the chart at 1M (see plan: risk scatter threshold).
MIN_DAILY_ROWS_RISK_CROSS_SECTION = 15


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def list_strategies() -> list[schemas.StrategyDescriptor]:
    return [
        schemas.StrategyDescriptor(name=name, description=strategies.get_description(name))
        for name in strategies.get_strategy_names()
    ]


def list_portfolios() -> schemas.PortfolioListOut:
    index = storage.read_index()
    items = [
        schemas.PortfolioMetaOut(
            id=meta.id,
            name=meta.name,
            created_at=meta.created_at,
            updated_at=meta.updated_at,
            active=(index.active_id == meta.id),
        )
        for meta in index.items
    ]
    return schemas.PortfolioListOut(items=items, active_id=index.active_id)


def build_portfolio(req: schemas.BuildRequest) -> schemas.ValuationOut:
    """Build a new portfolio from a build request.

    Steps:
      1. Score candidate universe using ``fetch_market_features``.
      2. Combine strategies and apply constraints (``combine_strategies``).
      3. Pull live prices for the chosen tickers.
      4. Whole-share allocation via ``build_basket``.
      5. Persist via ``storage.create_snapshot`` (unless ``save=False``).
      6. Revalue from the same quotes and return the dashboard payload.
    """

    candidate_tickers = strategies.get_candidate_tickers(req.strategies)
    features = fetch_market_features(tuple(candidate_tickers))
    composition, selection_details = strategies.combine_strategies(
        req.strategies,
        market_features=features,
        strategy_weights=req.strategy_weights,
    )
    if not composition:
        raise ValueError("strategy combination produced no holdings")

    blueprints = [
        HoldingBlueprint(
            ticker=ticker,
            name=name,
            weight=weight,
            sector=_lookup_sector(ticker, selection_details, features),
            is_etf=_lookup_is_etf(ticker, selection_details, features),
        )
        for ticker, name, weight in composition
    ]

    chosen_tickers = [bp.ticker for bp in blueprints]
    quotes = market_data.current_prices(chosen_tickers, use_cache=False)
    price_map = {ticker: q.price for ticker, q in quotes.items() if q.price is not None}

    basket = build_basket(
        investment_amount=req.investment_amount,
        blueprints=blueprints,
        prices=price_map,
        strategies=req.strategies,
        strategy_weights=req.strategy_weights or [1.0 / len(req.strategies)] * len(req.strategies),
        selection_details=selection_details,
    )

    snapshot_id = None
    snapshot_name = req.name or _default_snapshot_name(basket)
    if req.save:
        meta = storage.create_snapshot(
            name=snapshot_name,
            payload=_basket_to_snapshot_payload(basket),
        )
        snapshot_id = meta.id
        storage.set_active(snapshot_id)

    valuation = revalue(basket, quotes)
    if snapshot_id:
        _record_value_history(snapshot_id, valuation)
    return _valuation_to_out(
        portfolio_id=snapshot_id or "unsaved",
        name=snapshot_name,
        basket=basket,
        valuation=valuation,
    )


def get_portfolio(portfolio_id: str) -> schemas.ValuationOut:
    """Load a snapshot, apply split adjustments, and revalue from live quotes."""

    snapshot = storage.read_snapshot(portfolio_id)
    basket = _snapshot_to_basket(snapshot)
    basket = apply_split_adjustments(basket)
    tickers = list(basket.tickers)
    quotes = market_data.current_prices(tickers) if tickers else {}
    valuation = revalue(basket, quotes)
    _record_value_history(portfolio_id, valuation)
    return _valuation_to_out(
        portfolio_id=portfolio_id,
        name=snapshot["name"],
        basket=basket,
        valuation=valuation,
    )


def revalue_portfolio_from_quotes(
    portfolio_id: str,
    quotes: dict[str, Quote],
) -> schemas.ValuationOut:
    """Used by the WebSocket loop: revalue without re-fetching prices."""

    snapshot = storage.read_snapshot(portfolio_id)
    basket = _snapshot_to_basket(snapshot)
    basket = apply_split_adjustments(basket)
    valuation = revalue(basket, quotes)
    _record_value_history(portfolio_id, valuation)
    return _valuation_to_out(
        portfolio_id=portfolio_id,
        name=snapshot["name"],
        basket=basket,
        valuation=valuation,
    )


def rename_portfolio(portfolio_id: str, new_name: str) -> schemas.PortfolioMetaOut:
    meta = storage.rename_snapshot(portfolio_id, new_name)
    index = storage.read_index()
    return schemas.PortfolioMetaOut(
        id=meta.id,
        name=meta.name,
        created_at=meta.created_at,
        updated_at=meta.updated_at,
        active=(index.active_id == meta.id),
    )


def duplicate_portfolio(portfolio_id: str, new_name: str | None = None) -> schemas.PortfolioMetaOut:
    meta = storage.duplicate_snapshot(portfolio_id, new_name)
    index = storage.read_index()
    return schemas.PortfolioMetaOut(
        id=meta.id,
        name=meta.name,
        created_at=meta.created_at,
        updated_at=meta.updated_at,
        active=(index.active_id == meta.id),
    )


def activate_portfolio(portfolio_id: str) -> None:
    storage.set_active(portfolio_id)


def delete_portfolio(portfolio_id: str) -> None:
    storage.delete_snapshot(portfolio_id)


# ---------------------------------------------------------------------------
# History and risk
# ---------------------------------------------------------------------------
def get_history(portfolio_id: str, period: str) -> schemas.HistoryOut:
    snapshot = storage.read_snapshot(portfolio_id)
    basket = _snapshot_to_basket(snapshot)
    basket = apply_split_adjustments(basket)
    funded_tickers = basket.funded_tickers
    if not funded_tickers:
        return schemas.HistoryOut(
            portfolio_id=portfolio_id,
            period=period,
            points=[],
            source="backcast",
        )

    if period == "5d":
        quotes = market_data.current_prices(funded_tickers)
        valuation = revalue(basket, quotes)
        _record_value_history(portfolio_id, valuation)
        recorded = storage.read_value_history(portfolio_id)
        if len(recorded) >= 2:
            return schemas.HistoryOut(
                portfolio_id=portfolio_id,
                period=period,
                points=[
                    schemas.HistoryPoint(date=str(row["date"]), value=float(row["value"]))
                    for row in recorded[-5:]
                ],
                source="recorded",
                missing=[],
            )

    matrix, status = fetch_history(funded_tickers, period)
    series = compute_portfolio_value_series(basket, matrix)
    points = [
        schemas.HistoryPoint(date=str(ts.date()), value=float(value))
        for ts, value in series.items()
    ]
    return schemas.HistoryOut(
        portfolio_id=portfolio_id,
        period=period,
        points=points,
        source="backcast",
        missing=status.missing,
    )


def get_risk(portfolio_id: str, period: str) -> schemas.RiskOut:
    """Risk pack: per-window Sharpe + per-ticker scatter + correlation matrix."""

    snapshot = storage.read_snapshot(portfolio_id)
    basket = _snapshot_to_basket(snapshot)
    basket = apply_split_adjustments(basket)
    funded_tickers = basket.funded_tickers
    rf = risk_free_rate()

    sharpe_map: dict[str, schemas.SharpeWindowOut] = {
        key: schemas.SharpeWindowOut(
            window=key,  # type: ignore[arg-type]
            sharpe=None,
            annual_return=None,
            annual_volatility=None,
            samples=None,
        )
        for key in SHARPE_WINDOW_TRADING_DAYS
    }

    ticker_rows: list[schemas.RiskTickerStat] = []
    corr_matrix: list[list[float]] = []
    corr_tickers: list[str] = []
    note: str | None = None

    if not funded_tickers:
        note = "no_funded_positions"
        return schemas.RiskOut(
            portfolio_id=portfolio_id,
            period=period,
            risk_free_rate=rf,
            sharpe=sharpe_map,
            tickers=[],
            correlation_tickers=[],
            correlation_matrix=[],
            note=note,
        )

    # Longest reasonable lookback for Sharpe and analytics: try 2y first,
    # fall back to whatever the upstream returned.
    long_period = "2y" if period in ("5d", "1mo", "3mo", "6mo") else period
    long_matrix, _ = fetch_history(funded_tickers, long_period)
    long_series = compute_portfolio_value_series(basket, long_matrix)

    sharpe_calc = calculate_sharpe_ratios(long_series, risk_free=rf)
    for key, payload in sharpe_calc.items():
        if payload is None:
            continue
        sharpe_map[key] = schemas.SharpeWindowOut(
            window=key,  # type: ignore[arg-type]
            sharpe=payload["sharpe"],
            annual_return=payload["return"],
            annual_volatility=payload["volatility"],
            samples=payload["samples"],
        )

    # Per-ticker risk/return and correlation from the *requested* period.
    period_matrix, _ = fetch_history(funded_tickers, period)
    if not period_matrix.empty:
        returns = period_matrix.pct_change().dropna()
        if not returns.empty:
            weights_map = {
                row.ticker: row.weight
                for row in basket.holdings
                if row.shares > 0 and row.ticker in returns.columns
            }
            for ticker in returns.columns:
                series = returns[ticker].dropna()
                if series.empty or len(series) < 2:
                    continue
                ticker_rows.append(
                    schemas.RiskTickerStat(
                        ticker=ticker,
                        annual_return=float(series.mean() * 252),
                        annual_volatility=float(series.std() * math.sqrt(252)),
                        weight=float(weights_map.get(ticker, 0.0)),
                    )
                )
            if len(returns.columns) >= 2:
                corr = returns.corr().fillna(0.0)
                corr_tickers = list(corr.columns)
                corr_matrix = [list(map(float, row)) for row in corr.values]

    if not ticker_rows or len(period_matrix) < MIN_DAILY_ROWS_RISK_CROSS_SECTION:
        note = note or "insufficient_history"

    return schemas.RiskOut(
        portfolio_id=portfolio_id,
        period=period,
        risk_free_rate=rf,
        sharpe=sharpe_map,
        tickers=ticker_rows,
        correlation_tickers=corr_tickers,
        correlation_matrix=corr_matrix,
        note=note,
    )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------
def _basket_to_snapshot_payload(basket: Basket) -> dict:
    """Serialise a basket for storage. Mirrors the snapshot schema."""

    return {
        "investment_amount": basket.investment_amount,
        "built_at": basket.built_at.isoformat(),
        "strategies": list(basket.strategies),
        "strategy_weights": list(basket.strategy_weights),
        "holdings": [
            {
                "ticker": h.ticker,
                "name": h.name,
                "shares": h.shares,
                "initial_price_unadjusted": h.initial_price_unadjusted,
                "weight": h.weight,
                "sector": h.sector,
                "is_etf": h.is_etf,
                "as_of": h.as_of.isoformat(),
            }
            for h in basket.holdings
        ],
        "selection_details": list(basket.selection_details),
    }


def _snapshot_to_basket(snapshot: dict) -> Basket:
    holdings = tuple(
        portfolio.Holding(
            ticker=h["ticker"],
            name=h.get("name", h["ticker"]),
            shares=int(h.get("shares", 0)),
            initial_price_unadjusted=float(
                h.get("initial_price_unadjusted")
                if h.get("initial_price_unadjusted") is not None
                else h.get("initial_price") or 0.0
            ),
            weight=float(h.get("weight", 0.0)),
            sector=h.get("sector", "Unknown") or "Unknown",
            is_etf=bool(h.get("is_etf", False)),
            as_of=_parse_dt(h.get("as_of")) or _parse_dt(snapshot.get("built_at")) or datetime.now(timezone.utc),
        )
        for h in snapshot.get("holdings", [])
    )

    return Basket(
        investment_amount=float(snapshot.get("investment_amount", 0.0)),
        holdings=holdings,
        strategies=tuple(snapshot.get("strategies", [])),
        strategy_weights=tuple(snapshot.get("strategy_weights", [])),
        built_at=_parse_dt(snapshot.get("built_at")) or datetime.now(timezone.utc),
        selection_details=tuple(snapshot.get("selection_details", []) or []),
    )


def _valuation_to_out(
    *,
    portfolio_id: str,
    name: str,
    basket: Basket,
    valuation: portfolio.Valuation,
) -> schemas.ValuationOut:
    rows = [
        schemas.HoldingRowOut(**asdict(row)) for row in valuation.rows
    ]
    summary = schemas.SummaryOut(**asdict(valuation.summary))
    status = schemas.MarketDataStatusOut(
        as_of=valuation.status.as_of.isoformat(),
        any_fallback=valuation.status.any_fallback,
        any_unavailable=valuation.status.any_unavailable,
        missing=list(valuation.status.missing),
    )
    return schemas.ValuationOut(
        portfolio_id=portfolio_id,
        name=name,
        built_at=basket.built_at.isoformat(),
        investment_amount=basket.investment_amount,
        strategies=list(basket.strategies),
        strategy_weights=list(basket.strategy_weights),
        rows=rows,
        summary=summary,
        status=status,
        selection_details=list(basket.selection_details),
    )


def _lookup_sector(
    ticker: str,
    selection_details: Iterable[dict],
    features: dict[str, dict],
) -> str:
    for row in selection_details:
        if row.get("ticker") == ticker and row.get("sector"):
            return str(row["sector"])
    sector = features.get(ticker, {}).get("sector")
    return str(sector) if sector else "Unknown"


def _lookup_is_etf(
    ticker: str,
    selection_details: Iterable[dict],
    features: dict[str, dict],
) -> bool:
    for row in selection_details:
        if row.get("ticker") == ticker and "is_etf" in row:
            return bool(row["is_etf"])
    return bool(features.get(ticker, {}).get("is_etf", False))


def _parse_dt(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _record_value_history(portfolio_id: str, valuation: portfolio.Valuation) -> None:
    """Persist one daily value row for saved portfolios."""

    if not portfolio_id or portfolio_id == "unsaved":
        return
    if valuation.status.any_unavailable:
        return
    value = valuation.summary.current_portfolio_value
    if value <= 0:
        return

    as_of = valuation.status.as_of.astimezone(NY_TZ)
    trading_date = _effective_trading_date(as_of).isoformat()
    storage.upsert_value_history(
        portfolio_id,
        date=trading_date,
        value=float(value),
        as_of=as_of.isoformat(),
    )


def _effective_trading_date(as_of: datetime):
    """Best-effort NY trading date; weekends roll back to Friday."""

    local = as_of.astimezone(NY_TZ)
    weekday = local.weekday()
    date_value = local.date()
    if weekday == 5:
        return date_value.fromordinal(date_value.toordinal() - 1)
    if weekday == 6:
        return date_value.fromordinal(date_value.toordinal() - 2)
    return date_value


def _default_snapshot_name(basket: Basket) -> str:
    label = " + ".join(basket.strategies) if basket.strategies else "Portfolio"
    return f"{label} {basket.built_at.strftime('%Y-%m-%d %H:%M')}"
