"""Singleton accessors for app-scoped resources (the polling stream)."""

from __future__ import annotations

from fastapi import Request

from api.market_data import MarketDataStream


def get_market_stream(request: Request) -> MarketDataStream:
    """Resolve the singleton ``MarketDataStream`` attached to the app state."""

    stream: MarketDataStream | None = getattr(request.app.state, "market_stream", None)
    if stream is None:
        raise RuntimeError("MarketDataStream is not initialised on app.state")
    return stream
