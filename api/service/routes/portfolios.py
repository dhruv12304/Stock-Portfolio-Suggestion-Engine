"""REST endpoints for portfolios, strategies, history, and risk."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from api.market_data import SUPPORTED_PERIODS
from api.portfolio import SHARPE_WINDOW_TRADING_DAYS
from api.service import orchestrator, schemas
from api.storage import SnapshotNotFoundError, StorageError

router = APIRouter(prefix="/api/v1", tags=["portfolios"])


# ---------------------------------------------------------------------------
# Strategy catalog
# ---------------------------------------------------------------------------
@router.get("/strategies", response_model=list[schemas.StrategyDescriptor])
def get_strategies():
    return orchestrator.list_strategies()


# ---------------------------------------------------------------------------
# Portfolio CRUD
# ---------------------------------------------------------------------------
@router.get("/portfolios", response_model=schemas.PortfolioListOut)
def list_portfolios():
    return orchestrator.list_portfolios()


@router.post("/portfolios/build", response_model=schemas.ValuationOut)
def build_portfolio(req: schemas.BuildRequest):
    try:
        return orchestrator.build_portfolio(req)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        # Upstream market-data failure: 503 communicates "try again".
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/portfolios/{portfolio_id}", response_model=schemas.ValuationOut)
def get_portfolio(portfolio_id: str):
    try:
        return orchestrator.get_portfolio(portfolio_id)
    except SnapshotNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"portfolio {portfolio_id} not found") from exc
    except StorageError as exc:
        raise HTTPException(status_code=410, detail=str(exc)) from exc


@router.patch("/portfolios/{portfolio_id}", response_model=schemas.PortfolioMetaOut)
def rename_portfolio(portfolio_id: str, req: schemas.RenameRequest):
    try:
        return orchestrator.rename_portfolio(portfolio_id, req.name)
    except SnapshotNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


class DuplicateRequest(BaseModel):
    name: str | None = Field(default=None, max_length=120)


@router.post("/portfolios/{portfolio_id}/duplicate", response_model=schemas.PortfolioMetaOut)
def duplicate_portfolio(portfolio_id: str, req: DuplicateRequest):
    try:
        return orchestrator.duplicate_portfolio(portfolio_id, req.name)
    except SnapshotNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/portfolios/{portfolio_id}/activate", status_code=204)
def activate_portfolio(portfolio_id: str):
    try:
        orchestrator.activate_portfolio(portfolio_id)
    except SnapshotNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return None


@router.delete("/portfolios/{portfolio_id}", status_code=204)
def delete_portfolio(portfolio_id: str):
    orchestrator.delete_portfolio(portfolio_id)
    return None


# ---------------------------------------------------------------------------
# History / risk
# ---------------------------------------------------------------------------
@router.get("/portfolios/{portfolio_id}/history", response_model=schemas.HistoryOut)
def get_history(
    portfolio_id: str,
    period: str = Query("5d"),
):
    if period not in SUPPORTED_PERIODS:
        raise HTTPException(status_code=400, detail=f"unsupported period: {period}")
    try:
        return orchestrator.get_history(portfolio_id, period)
    except SnapshotNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/portfolios/{portfolio_id}/risk", response_model=schemas.RiskOut)
def get_risk(
    portfolio_id: str,
    period: str = Query("1y"),
):
    if period not in SUPPORTED_PERIODS:
        raise HTTPException(status_code=400, detail=f"unsupported period: {period}")
    try:
        return orchestrator.get_risk(portfolio_id, period)
    except SnapshotNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
