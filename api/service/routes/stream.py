"""WebSocket endpoint: live valuation pushes for a single portfolio.

Each connected client subscribes to the singleton ``MarketDataStream``
with the portfolio's funded tickers. On every poll cycle the server
revalues the basket from the new quotes and pushes a full
``ValuationOut`` payload to the client. The client only needs to render;
no client-side aggregation is required.

The connection lifecycle is:

1. Client connects with the portfolio id in the URL.
2. Server sends one ``valuation`` frame as the initial snapshot.
3. Server pushes ``valuation`` frames on every polling cycle that
   produces a relevant update.
4. Client disconnect (clean or otherwise) unsubscribes from the stream.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from api import market_data
from api.service import orchestrator
from api.service.dependencies import get_market_stream
from api.service.schemas import StreamFrame
from api.storage import SnapshotNotFoundError, StorageError

logger = logging.getLogger(__name__)

router = APIRouter()


@router.websocket("/api/v1/portfolios/{portfolio_id}/stream")
async def portfolio_stream(websocket: WebSocket, portfolio_id: str) -> None:
    await websocket.accept()

    try:
        valuation = orchestrator.get_portfolio(portfolio_id)
    except SnapshotNotFoundError:
        await _send(websocket, StreamFrame(type="error", message="portfolio_not_found"))
        await websocket.close(code=1008)
        return
    except StorageError as exc:
        await _send(websocket, StreamFrame(type="error", message=f"storage_error:{exc}"))
        await websocket.close(code=1011)
        return

    if not await _send(websocket, StreamFrame(type="valuation", valuation=valuation)):
        return

    tickers = [row.ticker for row in valuation.rows if row.shares > 0]
    if not tickers:
        # Nothing to subscribe to; idle until the client disconnects.
        await _idle_until_disconnect(websocket)
        return

    stream = get_market_stream(websocket)
    subscriber_id = f"{portfolio_id}:{uuid.uuid4().hex[:8]}"
    queue = await stream.subscribe(subscriber_id, tickers)

    receive_task = asyncio.create_task(_drain_client_messages(websocket))
    try:
        while True:
            try:
                quotes = await asyncio.wait_for(queue.get(), timeout=30.0)
            except asyncio.TimeoutError:
                if not await _send(websocket, StreamFrame(type="ping")):
                    break
                continue

            try:
                valuation = orchestrator.revalue_portfolio_from_quotes(
                    portfolio_id, quotes
                )
            except SnapshotNotFoundError:
                await _send(websocket, StreamFrame(type="error", message="portfolio_deleted"))
                break
            if not await _send(websocket, StreamFrame(type="valuation", valuation=valuation)):
                break
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("WebSocket loop crashed for %s: %s", portfolio_id, exc)
    finally:
        receive_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await receive_task
        await stream.unsubscribe(subscriber_id)
        with contextlib.suppress(RuntimeError):
            await websocket.close()


def _is_closed_socket_error(exc: RuntimeError) -> bool:
    message = str(exc)
    return (
        "Unexpected ASGI message 'websocket.send'" in message
        or "Cannot call 'send' once a close message has been sent" in message
    )


async def _send(websocket: WebSocket, frame: StreamFrame) -> bool:
    payload = frame.model_dump(mode="json")
    try:
        await websocket.send_text(json.dumps(payload))
    except WebSocketDisconnect:
        return False
    except RuntimeError as exc:
        if _is_closed_socket_error(exc):
            return False
        raise
    return True


async def _drain_client_messages(websocket: WebSocket) -> None:
    """Drain any messages the client sends (we don't need them, but reading
    keeps the connection healthy and surfaces disconnects promptly)."""

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        return


async def _idle_until_disconnect(websocket: WebSocket) -> None:
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        return
