"""yfinance facade with explicit freshness, fallback, and streaming semantics.

This module is the single point of contact with the upstream market data
provider. It exists so the rest of the codebase never has to know whether a
number came from a near-real-time quote, a previous-day close, or nothing at
all. Every external call is logged and returns either a typed ``Quote`` or a
``MarketDataStatus`` describing what happened.

Key resolved defects from BUG_REPORT.md:

- A2: ``Quote.source`` exposes the provenance of every price; the consumer
  decides how to render fallbacks instead of silently using cost basis.
- C3: ``risk_free_rate`` requires ``0 < rate <= 20`` percent to accept the
  upstream value; otherwise it returns the configured fallback.
- C4: ``history`` builds a wide close matrix and forward-fills it instead of
  using ``Series.add(..., fill_value=0.0)``, which produced phantom drawdowns.
- D1/D2: every network failure is logged through ``logging`` and surfaced via
  ``MarketDataStatus`` rather than printed and swallowed.

The ``MarketDataStream`` class implements the pragmatic polling required by
the "current values up to the sec" project requirement: a single background
asyncio task batches calls every ``POLL_SECONDS`` and pushes diffs to
per-portfolio subscriber queues.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Callable, Iterable, Literal, Sequence

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

QuoteSource = Literal["live", "close_fallback", "unavailable"]

DEFAULT_POLL_SECONDS = 5.0
DEFAULT_RISK_FREE_FALLBACK = 0.036  # 3.60%
LIVE_QUOTE_CACHE_TTL_SECONDS = 15.0  # short enough to feel live, kind enough to upstream

# yfinance period strings supported across the app. ``1mo`` is added as part
# of this phase so the new ``1M`` range pill works end-to-end.
SUPPORTED_PERIODS = ("5d", "1mo", "3mo", "6mo", "1y", "2y")


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Quote:
    """A typed view of a single ticker's current price.

    Attributes:
        ticker: Symbol the quote refers to (uppercase).
        price: The current price in the listing currency. ``None`` means the
            upstream had nothing usable for this ticker right now.
        source: Where the number came from. ``"live"`` is near-real-time
            (``fast_info.last_price``), ``"close_fallback"`` is the most
            recent daily close, ``"unavailable"`` means no number at all.
        as_of: UTC timestamp at which the price was observed.
        error: Optional human-readable reason when ``source`` is
            ``"unavailable"``.
    """

    ticker: str
    price: float | None
    source: QuoteSource
    as_of: datetime
    error: str | None = None


@dataclass
class MarketDataStatus:
    """Aggregate status across a batch fetch.

    Used by callers (and by the dashboard banner described in BUG_REPORT.md
    Section D2) to decide whether to warn the user about stale or missing
    data.
    """

    quotes: dict[str, Quote] = field(default_factory=dict)
    as_of: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    missing: list[str] = field(default_factory=list)

    @property
    def any_fallback(self) -> bool:
        return any(q.source != "live" for q in self.quotes.values())

    @property
    def any_unavailable(self) -> bool:
        return any(q.source == "unavailable" for q in self.quotes.values())


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(result) or math.isinf(result):
        return None
    return result


def _normalize_tickers(tickers: Iterable[str]) -> tuple[str, ...]:
    seen: list[str] = []
    for raw in tickers:
        if raw is None:
            continue
        ticker = str(raw).strip().upper()
        if ticker and ticker not in seen:
            seen.append(ticker)
    return tuple(seen)


# ---------------------------------------------------------------------------
# Dependency seams (overridable for tests)
# ---------------------------------------------------------------------------
# These are deliberately function attributes so tests can patch them without
# monkey-patching the yfinance module itself.

def _yf_download(**kwargs: Any) -> pd.DataFrame:
    return yf.download(**kwargs)


def _yf_fast_info(ticker: str) -> dict[str, Any]:
    return dict(getattr(yf.Ticker(ticker), "fast_info", {}) or {})


def _yf_splits(ticker: str) -> pd.Series:
    return yf.Ticker(ticker).splits


def _yf_info(ticker: str) -> dict[str, Any]:
    """Fetch the full fundamentals dict for a ticker. Slow and unreliable on
    Yahoo; called at most once per ticker per build, never on the hot path."""

    try:
        return dict(getattr(yf.Ticker(ticker), "info", {}) or {})
    except Exception as exc:  # pragma: no cover - defensive
        logger.info("yfinance .info raised for %s: %s", ticker, exc)
        return {}


# Public seams that tests assign to. Production code calls through these.
download_fn: Callable[..., pd.DataFrame] = _yf_download
fast_info_fn: Callable[[str], dict[str, Any]] = _yf_fast_info
splits_fn: Callable[[str], pd.Series] = _yf_splits
info_fn: Callable[[str], dict[str, Any]] = _yf_info


# ---------------------------------------------------------------------------
# Live price cache
# ---------------------------------------------------------------------------
# A tiny TTL cache so repeated calls within ~15s don't hammer Yahoo. The
# polling scheduler is the canonical refresh path; everything else benefits
# from the cache as a courtesy to the upstream.

_LIVE_CACHE: dict[str, tuple[float, Quote]] = {}


def _cache_get(ticker: str) -> Quote | None:
    entry = _LIVE_CACHE.get(ticker)
    if entry is None:
        return None
    ts, quote = entry
    if (time.monotonic() - ts) > LIVE_QUOTE_CACHE_TTL_SECONDS:
        _LIVE_CACHE.pop(ticker, None)
        return None
    return quote


def _cache_put(quote: Quote) -> None:
    _LIVE_CACHE[quote.ticker] = (time.monotonic(), quote)


def clear_cache() -> None:
    """Drop all cached quotes. Tests call this between cases."""

    _LIVE_CACHE.clear()


# ---------------------------------------------------------------------------
# Price fetching
# ---------------------------------------------------------------------------
def _extract_latest_close(raw: pd.DataFrame | None, ticker: str) -> float | None:
    """Pull the most recent close for ``ticker`` from a yfinance bulk download."""

    if raw is None or raw.empty:
        return None
    try:
        if isinstance(raw.columns, pd.MultiIndex):
            # ``group_by='ticker'`` -> (ticker, field) MultiIndex.
            if ticker in raw.columns.get_level_values(0):
                col = raw[ticker].get("Close")
                if col is not None:
                    series = col.dropna()
                    if not series.empty:
                        return _safe_float(series.iloc[-1])
            # Fallback: ``yf.download`` sometimes flips to (field, ticker).
            level0 = raw.columns.get_level_values(0)
            if "Close" in level0:
                close = raw["Close"]
                if isinstance(close, pd.DataFrame) and ticker in close.columns:
                    series = close[ticker].dropna()
                    if not series.empty:
                        return _safe_float(series.iloc[-1])
        else:
            if "Close" in raw.columns:
                series = raw["Close"].dropna()
                if not series.empty:
                    return _safe_float(series.iloc[-1])
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("could not extract close for %s: %s", ticker, exc)
    return None


def current_prices(tickers: Sequence[str], *, use_cache: bool = True) -> dict[str, Quote]:
    """Fetch a current ``Quote`` for each ticker.

    Strategy:
      1. ``fast_info.last_price`` per ticker (near-real-time).
      2. If that fails or is missing, fall back to the most recent close from
         a single batched ``yf.download`` call.
      3. If both fail, emit a ``Quote`` with ``source='unavailable'``.

    Returns a dict keyed by uppercase ticker, in the same order as ``tickers``.
    """

    normalized = _normalize_tickers(tickers)
    if not normalized:
        return {}

    now = _now_utc()
    quotes: dict[str, Quote] = {}
    needs_fallback: list[str] = []

    for ticker in normalized:
        if use_cache:
            cached = _cache_get(ticker)
            if cached is not None:
                quotes[ticker] = cached
                continue
        try:
            info = fast_info_fn(ticker) or {}
            price = _safe_float(info.get("last_price") or info.get("lastPrice"))
            if price is not None and price > 0:
                quote = Quote(ticker=ticker, price=price, source="live", as_of=now)
                quotes[ticker] = quote
                _cache_put(quote)
                continue
        except Exception as exc:
            logger.info("fast_info failed for %s: %s", ticker, exc)
        needs_fallback.append(ticker)

    if needs_fallback:
        fallback_closes = _fetch_recent_closes(needs_fallback)
        for ticker in needs_fallback:
            price = fallback_closes.get(ticker)
            if price is not None and price > 0:
                quote = Quote(
                    ticker=ticker,
                    price=price,
                    source="close_fallback",
                    as_of=now,
                )
                quotes[ticker] = quote
                _cache_put(quote)
            else:
                quotes[ticker] = Quote(
                    ticker=ticker,
                    price=None,
                    source="unavailable",
                    as_of=now,
                    error="no live or close price available",
                )

    return quotes


def _fetch_recent_closes(tickers: Sequence[str]) -> dict[str, float]:
    """Batched fallback: most recent daily close from one yf.download call."""

    if not tickers:
        return {}
    try:
        raw = download_fn(
            tickers=list(tickers) if len(tickers) > 1 else tickers[0],
            period="5d",
            interval="1d",
            auto_adjust=False,
            progress=False,
            group_by="ticker",
            threads=True,
        )
    except Exception as exc:
        logger.warning("bulk close fallback failed for %s: %s", tickers, exc)
        return {}

    out: dict[str, float] = {}
    for ticker in tickers:
        price = _extract_latest_close(raw, ticker)
        if price is not None:
            out[ticker] = price
    return out


# ---------------------------------------------------------------------------
# History matrix (resolves C4)
# ---------------------------------------------------------------------------
def history(
    tickers: Sequence[str],
    period: str,
) -> tuple[pd.DataFrame, MarketDataStatus]:
    """Return a wide close-price matrix and a status descriptor.

    The matrix has one column per ticker that returned data, indexed by the
    trading-day timestamp from yfinance. Missing tickers are recorded on the
    status object instead of being silently zero-filled (which produced
    phantom drawdowns in the legacy implementation).
    """

    normalized = _normalize_tickers(tickers)
    status = MarketDataStatus()
    if period not in SUPPORTED_PERIODS:
        raise ValueError(f"unsupported period: {period}")
    if not normalized:
        return pd.DataFrame(), status

    is_multi = len(normalized) > 1
    try:
        raw = download_fn(
            tickers=list(normalized) if is_multi else normalized[0],
            period=period,
            interval="1d",
            auto_adjust=False,
            progress=False,
            group_by="ticker",
            threads=True,
        )
    except Exception as exc:
        logger.warning("history fetch failed for %s/%s: %s", normalized, period, exc)
        status.missing = list(normalized)
        return pd.DataFrame(), status

    if raw is None or getattr(raw, "empty", True):
        status.missing = list(normalized)
        return pd.DataFrame(), status

    columns: dict[str, pd.Series] = {}
    for ticker in normalized:
        series = _extract_close_history(raw, ticker, is_multi)
        if series.empty:
            status.missing.append(ticker)
            continue
        columns[ticker] = series.astype(float)

    if not columns:
        return pd.DataFrame(), status

    matrix = pd.DataFrame(columns).sort_index().ffill()
    return matrix, status


def _extract_close_history(
    raw: pd.DataFrame,
    ticker: str,
    is_multi: bool,
) -> pd.Series:
    if raw is None or raw.empty:
        return pd.Series(dtype="float64")
    try:
        if is_multi or isinstance(raw.columns, pd.MultiIndex):
            if isinstance(raw.columns, pd.MultiIndex):
                if ticker in raw.columns.get_level_values(0):
                    return raw[ticker]["Close"].dropna()
                level0 = raw.columns.get_level_values(0)
                if "Close" in level0:
                    close = raw["Close"]
                    if isinstance(close, pd.DataFrame) and ticker in close.columns:
                        return close[ticker].dropna()
            return pd.Series(dtype="float64")
        if "Close" in raw.columns:
            return raw["Close"].dropna()
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("could not extract history for %s: %s", ticker, exc)
    return pd.Series(dtype="float64")


# ---------------------------------------------------------------------------
# Risk-free rate (resolves C3)
# ---------------------------------------------------------------------------
def risk_free_rate(*, fallback: float = DEFAULT_RISK_FREE_FALLBACK) -> float:
    """Annualized rate from ``^IRX`` (13-week T-bill), bounded to a sane range.

    Yahoo reports ``^IRX`` as percent. The conversion divides by 100. The
    sanity check rejects ``0`` (sometimes returned during data outages) and
    anything above 20% (would indicate a unit-error upstream).
    """

    try:
        info = fast_info_fn("^IRX") or {}
        raw = _safe_float(info.get("lastPrice") or info.get("last_price"))
        if raw is None or not (0 < raw <= 20):
            logger.info("^IRX returned %r; using fallback %.4f", raw, fallback)
            return fallback
        return raw / 100.0
    except Exception as exc:
        logger.warning("^IRX fetch failed (%s); using fallback %.4f", exc, fallback)
        return fallback


# ---------------------------------------------------------------------------
# Corporate actions (resolves A5)
# ---------------------------------------------------------------------------
def apply_corporate_actions(
    as_of: date | datetime,
    ticker: str,
    original_shares: int,
) -> tuple[int, float]:
    """Adjust share count for splits between ``as_of`` and today.

    Returns ``(adjusted_shares, cost_basis_factor)`` so callers can:

        new_shares = adjusted_shares
        new_cost_per_share = original_cost_per_share * cost_basis_factor

    Example: 50 shares bought at 200 USD before a 2-for-1 split becomes
    100 shares at 100 USD; this function returns ``(100, 0.5)``.

    Reverse splits and irrational factors are clamped: anything that would
    drop share count to zero is rejected (returns the original values).
    """

    if original_shares <= 0:
        return original_shares, 1.0

    try:
        splits = splits_fn(ticker)
    except Exception as exc:
        logger.warning("splits fetch failed for %s: %s", ticker, exc)
        return original_shares, 1.0

    if splits is None or getattr(splits, "empty", True):
        return original_shares, 1.0

    try:
        splits = splits.copy()
        splits.index = pd.to_datetime(splits.index)
        if getattr(splits.index, "tz", None) is not None:
            splits.index = splits.index.tz_convert(None)
        cutoff = pd.Timestamp(as_of)
        if getattr(cutoff, "tz", None) is not None:
            cutoff = cutoff.tz_convert(None)
        relevant = splits[splits.index > cutoff]
    except Exception as exc:
        logger.warning("could not filter splits for %s: %s", ticker, exc)
        return original_shares, 1.0

    if relevant.empty:
        return original_shares, 1.0

    try:
        cumulative_factor = float(relevant.astype(float).prod())
    except Exception as exc:
        logger.warning("could not compute split product for %s: %s", ticker, exc)
        return original_shares, 1.0

    if cumulative_factor <= 0:
        return original_shares, 1.0

    adjusted_shares = int(round(original_shares * cumulative_factor))
    if adjusted_shares <= 0:
        return original_shares, 1.0

    basis_factor = 1.0 / cumulative_factor
    return adjusted_shares, basis_factor


# ---------------------------------------------------------------------------
# Bulk fundamental features (used by the strategy scoring engine)
# ---------------------------------------------------------------------------
_FUNDAMENTALS_CACHE: dict[tuple[str, ...], tuple[float, dict[str, dict[str, Any]]]] = {}
FUNDAMENTALS_CACHE_TTL_SECONDS = 300.0


def clear_fundamentals_cache() -> None:
    _FUNDAMENTALS_CACHE.clear()


def fetch_market_features(tickers: Sequence[str]) -> dict[str, dict[str, Any]]:
    """Fetch market and fundamental features used by the scoring engine.

    Returns a dict ``{ticker: {feature_name: value | None}}`` covering:

      * ``latest_close``, ``current_price``
      * ``momentum_1m``, ``momentum_3m``, ``volatility_3m``
      * ``avg_volume``
      * ``market_cap``, ``pe_ratio``, ``roe``, ``debt_to_equity``,
        ``profit_margin``, ``revenue_growth``, ``earnings_growth``
      * ``expense_ratio``
      * ``sector``, ``is_etf``

    The function performs one batched ``yf.download`` call and one per-ticker
    ``yf.Ticker.info`` call. Results are cached for 5 minutes because the
    fundamentals only change at fiscal-period boundaries, not intraday.
    """

    normalized = _normalize_tickers(tickers)
    if not normalized:
        return {}

    cache_key = tuple(normalized)
    cached = _FUNDAMENTALS_CACHE.get(cache_key)
    if cached is not None:
        ts, payload = cached
        if (time.monotonic() - ts) < FUNDAMENTALS_CACHE_TTL_SECONDS:
            return payload

    features: dict[str, dict[str, Any]] = {ticker: {} for ticker in normalized}
    is_multi = len(normalized) > 1

    try:
        market_data_df = download_fn(
            tickers=list(normalized) if is_multi else normalized[0],
            period="6mo",
            interval="1d",
            auto_adjust=False,
            progress=False,
            group_by="ticker",
            threads=True,
        )
    except Exception as exc:
        logger.error("bulk market download failed: %s", exc)
        raise RuntimeError("Market data unavailable. Please try again in a moment.") from exc

    for ticker in normalized:
        closes = _extract_close_history(market_data_df, ticker, is_multi)
        volumes = _extract_volume_history(market_data_df, ticker, is_multi)

        if not closes.empty:
            latest_close = float(closes.iloc[-1])
            features[ticker]["latest_close"] = latest_close
            features[ticker]["momentum_1m"] = (
                (latest_close / float(closes.iloc[-22])) - 1.0 if len(closes) >= 22 else None
            )
            if len(closes) >= 63:
                features[ticker]["momentum_3m"] = (latest_close / float(closes.iloc[-63])) - 1.0
                returns_63 = closes.pct_change().dropna().tail(63)
                features[ticker]["volatility_3m"] = (
                    float(returns_63.std() * math.sqrt(252)) if not returns_63.empty else None
                )
            else:
                features[ticker]["momentum_3m"] = None
                features[ticker]["volatility_3m"] = None
        else:
            features[ticker]["latest_close"] = None
            features[ticker]["momentum_1m"] = None
            features[ticker]["momentum_3m"] = None
            features[ticker]["volatility_3m"] = None

        features[ticker]["avg_volume"] = (
            float(volumes.tail(21).mean()) if not volumes.empty else None
        )

        try:
            fast = fast_info_fn(ticker) or {}
            info = info_fn(ticker) or {}
        except Exception as exc:
            logger.info("fundamentals fetch failed for %s: %s", ticker, exc)
            fast, info = {}, {}

        fast_price = _safe_float(fast.get("last_price") or fast.get("lastPrice"))
        features[ticker]["current_price"] = fast_price or features[ticker].get("latest_close")
        features[ticker]["market_cap"] = _safe_float(
            fast.get("market_cap") or fast.get("marketCap") or info.get("marketCap")
        )
        features[ticker]["pe_ratio"] = _safe_float(info.get("trailingPE") or info.get("forwardPE"))
        features[ticker]["roe"] = _safe_float(info.get("returnOnEquity"))
        features[ticker]["debt_to_equity"] = _safe_float(info.get("debtToEquity"))
        features[ticker]["profit_margin"] = _safe_float(
            info.get("profitMargins") or info.get("operatingMargins")
        )
        features[ticker]["revenue_growth"] = _safe_float(info.get("revenueGrowth"))
        features[ticker]["earnings_growth"] = _safe_float(info.get("earningsGrowth"))
        features[ticker]["expense_ratio"] = _safe_float(
            info.get("annualReportExpenseRatio")
            or info.get("annualHoldingsTurnover")
            or info.get("expenseRatio")
        )
        features[ticker]["sector"] = info.get("sector")
        features[ticker]["is_etf"] = (info.get("quoteType") or "").upper() in {"ETF", "MUTUALFUND"}

    _FUNDAMENTALS_CACHE[cache_key] = (time.monotonic(), features)
    return features


def _extract_volume_history(
    raw: pd.DataFrame,
    ticker: str,
    is_multi: bool,
) -> pd.Series:
    if raw is None or raw.empty:
        return pd.Series(dtype="float64")
    try:
        if is_multi or isinstance(raw.columns, pd.MultiIndex):
            if isinstance(raw.columns, pd.MultiIndex):
                if ticker in raw.columns.get_level_values(0):
                    return raw[ticker]["Volume"].dropna()
                level0 = raw.columns.get_level_values(0)
                if "Volume" in level0:
                    vol = raw["Volume"]
                    if isinstance(vol, pd.DataFrame) and ticker in vol.columns:
                        return vol[ticker].dropna()
            return pd.Series(dtype="float64")
        if "Volume" in raw.columns:
            return raw["Volume"].dropna()
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("could not extract volume for %s: %s", ticker, exc)
    return pd.Series(dtype="float64")


# ---------------------------------------------------------------------------
# Polling scheduler (the live data feed)
# ---------------------------------------------------------------------------
class MarketDataStream:
    """Single background task that broadcasts quotes to subscribers.

    The scheduler is portfolio-agnostic: each subscriber provides the set of
    tickers it cares about, and the loop unions all currently-subscribed
    tickers before calling ``price_fn`` once per cycle. Updates are dispatched
    to per-subscriber queues so a slow consumer cannot back-pressure the
    polling cadence.

    Tests inject a synthetic ``price_fn`` and use ``poll_seconds=0.01`` to
    drive deterministic cycles without hitting yfinance.
    """

    def __init__(
        self,
        *,
        poll_seconds: float = DEFAULT_POLL_SECONDS,
        price_fn: Callable[[Sequence[str]], dict[str, Quote]] | None = None,
        queue_maxsize: int = 64,
    ) -> None:
        self.poll_seconds = max(0.05, float(poll_seconds))
        self.price_fn = price_fn or current_prices
        self.queue_maxsize = queue_maxsize
        self._subscribers: dict[str, set[str]] = {}
        self._queues: dict[str, asyncio.Queue[dict[str, Quote]]] = {}
        self._task: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event | None = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._run(), name="MarketDataStream")

    async def stop(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()
        if self._task is not None:
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        self._stop_event = None

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    # ------------------------------------------------------------------
    # Subscriptions
    # ------------------------------------------------------------------
    async def subscribe(
        self,
        subscriber_id: str,
        tickers: Sequence[str],
    ) -> asyncio.Queue[dict[str, Quote]]:
        """Register a subscriber and return its update queue."""

        async with self._lock:
            self._subscribers[subscriber_id] = set(_normalize_tickers(tickers))
            queue: asyncio.Queue[dict[str, Quote]] = asyncio.Queue(self.queue_maxsize)
            self._queues[subscriber_id] = queue
            return queue

    async def update_subscription(
        self,
        subscriber_id: str,
        tickers: Sequence[str],
    ) -> None:
        async with self._lock:
            if subscriber_id not in self._subscribers:
                raise KeyError(subscriber_id)
            self._subscribers[subscriber_id] = set(_normalize_tickers(tickers))

    async def unsubscribe(self, subscriber_id: str) -> None:
        async with self._lock:
            self._subscribers.pop(subscriber_id, None)
            self._queues.pop(subscriber_id, None)

    # ------------------------------------------------------------------
    # Polling loop
    # ------------------------------------------------------------------
    async def _run(self) -> None:
        assert self._stop_event is not None
        loop = asyncio.get_running_loop()
        while not self._stop_event.is_set():
            await self._cycle(loop)
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.poll_seconds,
                )
            except asyncio.TimeoutError:
                continue

    async def _cycle(self, loop: asyncio.AbstractEventLoop) -> None:
        async with self._lock:
            tickers_union: set[str] = set()
            for tickers in self._subscribers.values():
                tickers_union |= tickers
            subscribers_snapshot = {sid: set(t) for sid, t in self._subscribers.items()}
            queues_snapshot = dict(self._queues)

        if not tickers_union:
            return

        try:
            quotes = await loop.run_in_executor(
                None,
                self.price_fn,
                tuple(sorted(tickers_union)),
            )
        except Exception as exc:
            logger.warning("price_fn raised inside MarketDataStream: %s", exc)
            return

        for sid, tickers in subscribers_snapshot.items():
            queue = queues_snapshot.get(sid)
            if queue is None:
                continue
            relevant = {t: quotes[t] for t in tickers if t in quotes}
            if not relevant:
                continue
            await self._enqueue(queue, relevant)

    @staticmethod
    async def _enqueue(
        queue: asyncio.Queue[dict[str, Quote]],
        payload: dict[str, Quote],
    ) -> None:
        # Drop the oldest payload if the consumer is slow; we prefer
        # freshness over a backlog of stale updates.
        if queue.full():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:  # pragma: no cover - race
                pass
        await queue.put(payload)
