"""Pydantic schemas for HTTP request/response and WebSocket payloads."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from api.market_data import SUPPORTED_PERIODS

PeriodLiteral = Literal["5d", "1mo", "3mo", "6mo", "1y", "2y"]
SharpeWindowLiteral = Literal["3m", "6m", "1y", "2y"]
HistorySourceLiteral = Literal["recorded", "backcast"]


# ---------------------------------------------------------------------------
# Build / portfolios
# ---------------------------------------------------------------------------
class BuildRequest(BaseModel):
    """Request body for ``POST /portfolios/build``."""

    name: str | None = None
    investment_amount: float = Field(..., ge=5000)
    strategies: list[str] = Field(..., min_length=1, max_length=2)
    strategy_weights: list[float] | None = None
    save: bool = True

    @field_validator("strategy_weights")
    @classmethod
    def _matches_strategies(cls, v: list[float] | None, info):
        if v is None:
            return v
        strategies = info.data.get("strategies", [])
        if len(v) != len(strategies):
            raise ValueError("strategy_weights length must match strategies length")
        return v


class StrategyDescriptor(BaseModel):
    name: str
    description: str


class HoldingRowOut(BaseModel):
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
    quote_source: Literal["live", "close_fallback", "unavailable"]
    quote_as_of: str
    sector: str
    is_etf: bool


class SummaryOut(BaseModel):
    investment_amount: float
    total_invested: float
    leftover_cash: float
    current_portfolio_value: float
    total_portfolio_value: float
    unrealized_pnl: float | None
    unrealized_pnl_pct: float | None


class MarketDataStatusOut(BaseModel):
    as_of: str
    any_fallback: bool
    any_unavailable: bool
    missing: list[str] = []


class ValuationOut(BaseModel):
    portfolio_id: str
    name: str
    built_at: str
    investment_amount: float
    strategies: list[str]
    strategy_weights: list[float]
    rows: list[HoldingRowOut]
    summary: SummaryOut
    status: MarketDataStatusOut
    selection_details: list[dict[str, Any]] = []


class PortfolioMetaOut(BaseModel):
    id: str
    name: str
    created_at: str
    updated_at: str
    active: bool


class PortfolioListOut(BaseModel):
    items: list[PortfolioMetaOut]
    active_id: str | None


class RenameRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)


# ---------------------------------------------------------------------------
# History / risk
# ---------------------------------------------------------------------------
class HistoryPoint(BaseModel):
    date: str
    value: float


class HistoryOut(BaseModel):
    portfolio_id: str
    period: PeriodLiteral
    points: list[HistoryPoint]
    source: HistorySourceLiteral = "backcast"
    missing: list[str] = []


class SharpeWindowOut(BaseModel):
    window: SharpeWindowLiteral
    sharpe: float | None
    annual_return: float | None
    annual_volatility: float | None
    samples: int | None


class RiskTickerStat(BaseModel):
    ticker: str
    annual_return: float
    annual_volatility: float
    weight: float


class RiskOut(BaseModel):
    portfolio_id: str
    period: PeriodLiteral
    risk_free_rate: float
    sharpe: dict[str, SharpeWindowOut]
    tickers: list[RiskTickerStat]
    correlation_tickers: list[str]
    correlation_matrix: list[list[float]]
    note: str | None = None


# ---------------------------------------------------------------------------
# WebSocket frames
# ---------------------------------------------------------------------------
class StreamFrame(BaseModel):
    type: Literal["valuation", "error", "ping"]
    valuation: ValuationOut | None = None
    message: str | None = None


__all__ = [
    "BuildRequest",
    "HistoryOut",
    "HistoryPoint",
    "HoldingRowOut",
    "MarketDataStatusOut",
    "PortfolioListOut",
    "PortfolioMetaOut",
    "RenameRequest",
    "RiskOut",
    "RiskTickerStat",
    "SharpeWindowOut",
    "StrategyDescriptor",
    "StreamFrame",
    "SummaryOut",
    "ValuationOut",
    "SUPPORTED_PERIODS",
]
