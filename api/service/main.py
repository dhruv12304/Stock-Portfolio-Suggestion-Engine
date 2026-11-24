"""FastAPI application entry point.

Run locally with::

    uvicorn api.service.main:app --reload --port 8000

The startup hook migrates legacy persistence to the new snapshot layout
and starts the singleton ``MarketDataStream``. The shutdown hook stops
the stream cleanly so background polling never outlives the process.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api import storage
from api.market_data import DEFAULT_POLL_SECONDS, MarketDataStream
from api.service.routes import portfolios as portfolios_router
from api.service.routes import stream as stream_router

logger = logging.getLogger(__name__)


def _poll_seconds() -> float:
    """Read the polling cadence from env, with a 0.1s floor.

    The 0.1s floor keeps tests responsive while still safeguarding the
    upstream yfinance endpoint from accidental flood-polling.
    """

    raw = os.environ.get("PORTFOLIO_POLL_SECONDS")
    if raw is None:
        return DEFAULT_POLL_SECONDS
    try:
        return max(0.1, float(raw))
    except ValueError:
        return DEFAULT_POLL_SECONDS


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=logging.INFO)

    storage.ensure_dirs()
    migrated = storage.migrate_legacy_if_needed()
    if migrated is not None:
        logger.info("imported legacy portfolio as snapshot %s", migrated.id)

    poll_seconds = _poll_seconds()
    stream = MarketDataStream(poll_seconds=poll_seconds)
    await stream.start()
    app.state.market_stream = stream
    logger.info("MarketDataStream started at %.1fs cadence", poll_seconds)

    try:
        yield
    finally:
        await stream.stop()
        logger.info("MarketDataStream stopped")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Portfolio engine API",
        version="0.1.0",
        lifespan=lifespan,
    )

    allow_origins_raw = os.environ.get(
        "PORTFOLIO_CORS_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",
    )
    allow_origins = [origin.strip() for origin in allow_origins_raw.split(",") if origin.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(portfolios_router.router)
    app.include_router(stream_router.router)

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    return app


app = create_app()
