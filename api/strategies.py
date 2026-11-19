"""Strategy universes, factor scoring, and constraint enforcement.

This is the refactored sibling of the legacy ``strategies.py`` at the repo
root. The legacy module stays until Phase 8 so the Streamlit app keeps
running; production code in the FastAPI service imports from here.

Resolved defects from BUG_REPORT.md:

- C5: ETF floor is now enforced on the *combined* basket after two
      strategies are merged, not only per-strategy.
- C6: ``_select_with_constraints`` replaces the silent "sector relaxation"
      loop with a controlled relaxation that bumps the cap by ``relax_by``
      and tags the affected rows with ``selected_after_relaxation``. If
      relaxation still cannot satisfy ``top_n`` the function returns a
      smaller basket rather than silently violating the cap.
- C9: ``combine_strategies`` aggregates explainability rows by ticker so
      a ticker appearing in two strategies has one combined entry whose
      ``source_strategies`` lists both.

The module is pure (no I/O, no yfinance). It accepts a ``market_features``
mapping from the caller, so tests can drive it with synthetic data.
"""

from __future__ import annotations

import math
from typing import Iterable

import pandas as pd

DEFAULT_TOP_N = 5
DEFAULT_SECTOR_CAP = 0.4
DEFAULT_MIN_ETF_SHARE = 0.3
DEFAULT_RELAX_BY = 1

STRATEGY_TAGS: dict[str, set[str]] = {
    "Ethical Investing": {"ethical", "esg", "sustainable", "clean"},
    "Growth Investing": {"growth", "tech", "innovation", "ai"},
    "Index Investing": {"index", "broad", "etf", "bond", "international"},
    "Quality Investing": {"quality", "dividend", "stable", "blue-chip"},
    "Value Investing": {"value", "dividend", "defensive", "cyclical"},
}


STRATEGIES: dict[str, dict] = {
    "Ethical Investing": {
        "description": (
            "Companies and ETFs that tilt toward ESG practices, sustainability, "
            "and long-term stewardship."
        ),
        "factor_weights": {
            "quality": 0.25, "growth": 0.15, "stability": 0.15,
            "momentum": 0.15, "liquidity": 0.10, "etf_preference": 0.10,
            "thematic_bonus": 0.10,
        },
        "candidates": [
            {"ticker": "ESGU", "name": "iShares ESG Aware MSCI USA ETF", "is_etf": True, "sector": "Diversified ETF", "tags": ["ethical", "esg"]},
            {"ticker": "ESGV", "name": "Vanguard ESG U.S. Stock ETF", "is_etf": True, "sector": "Diversified ETF", "tags": ["ethical", "esg"]},
            {"ticker": "SUSA", "name": "iShares MSCI USA ESG Select ETF", "is_etf": True, "sector": "Diversified ETF", "tags": ["ethical", "esg"]},
            {"ticker": "ICLN", "name": "iShares Global Clean Energy ETF", "is_etf": True, "sector": "Energy ETF", "tags": ["ethical", "clean-energy"]},
            {"ticker": "AAPL", "name": "Apple Inc.", "is_etf": False, "sector": "Technology", "tags": ["ethical", "quality"]},
            {"ticker": "MSFT", "name": "Microsoft Corp.", "is_etf": False, "sector": "Technology", "tags": ["ethical", "quality"]},
            {"ticker": "ADBE", "name": "Adobe Inc.", "is_etf": False, "sector": "Technology", "tags": ["ethical", "quality"]},
            {"ticker": "FSLR", "name": "First Solar Inc.", "is_etf": False, "sector": "Energy", "tags": ["ethical", "clean-energy", "growth"]},
            {"ticker": "WM", "name": "Waste Management Inc.", "is_etf": False, "sector": "Industrials", "tags": ["ethical", "quality"]},
            {"ticker": "NSRGY", "name": "Nestle S.A.", "is_etf": False, "sector": "Consumer Defensive", "tags": ["ethical", "quality"]},
            {"ticker": "CRM", "name": "Salesforce Inc.", "is_etf": False, "sector": "Technology", "tags": ["ethical", "growth"]},
        ],
    },
    "Growth Investing": {
        "description": (
            "Companies and ETFs with stronger expected earnings/sales expansion "
            "and momentum."
        ),
        "factor_weights": {
            "growth": 0.35, "momentum": 0.25, "quality": 0.10,
            "liquidity": 0.10, "stability": 0.05, "etf_preference": 0.05,
            "thematic_bonus": 0.10,
        },
        "candidates": [
            {"ticker": "QQQ", "name": "Invesco QQQ Trust", "is_etf": True, "sector": "Growth ETF", "tags": ["growth", "etf"]},
            {"ticker": "VUG", "name": "Vanguard Growth ETF", "is_etf": True, "sector": "Growth ETF", "tags": ["growth", "etf"]},
            {"ticker": "NVDA", "name": "NVIDIA Corp.", "is_etf": False, "sector": "Technology", "tags": ["growth", "ai"]},
            {"ticker": "AMZN", "name": "Amazon.com Inc.", "is_etf": False, "sector": "Consumer Cyclical", "tags": ["growth"]},
            {"ticker": "META", "name": "Meta Platforms Inc.", "is_etf": False, "sector": "Communication Services", "tags": ["growth"]},
            {"ticker": "TSLA", "name": "Tesla Inc.", "is_etf": False, "sector": "Consumer Cyclical", "tags": ["growth"]},
            {"ticker": "CRWD", "name": "CrowdStrike Holdings", "is_etf": False, "sector": "Technology", "tags": ["growth", "cybersecurity"]},
            {"ticker": "MELI", "name": "MercadoLibre Inc.", "is_etf": False, "sector": "Consumer Cyclical", "tags": ["growth", "emerging"]},
        ],
    },
    "Index Investing": {
        "description": (
            "Broad passive exposure via diversified, lower-cost index ETFs "
            "across US, global, and bonds."
        ),
        "factor_weights": {
            "low_cost": 0.35, "stability": 0.20, "liquidity": 0.20,
            "momentum": 0.10, "size": 0.10, "etf_preference": 0.05,
        },
        "candidates": [
            {"ticker": "VOO", "name": "Vanguard S&P 500 ETF", "is_etf": True, "sector": "Large Blend ETF", "tags": ["index", "us"]},
            {"ticker": "VTI", "name": "Vanguard Total Stock Market ETF", "is_etf": True, "sector": "Large Blend ETF", "tags": ["index", "us"]},
            {"ticker": "VT", "name": "Vanguard Total World Stock ETF", "is_etf": True, "sector": "World ETF", "tags": ["index", "global"]},
            {"ticker": "DIA", "name": "SPDR Dow Jones Industrial Average ETF", "is_etf": True, "sector": "Large Blend ETF", "tags": ["index", "us"]},
            {"ticker": "IWM", "name": "iShares Russell 2000 ETF", "is_etf": True, "sector": "Small Blend ETF", "tags": ["index", "small-cap"]},
            {"ticker": "BND", "name": "Vanguard Total Bond Market ETF", "is_etf": True, "sector": "Bond ETF", "tags": ["index", "bond"]},
            {"ticker": "IXUS", "name": "iShares Core MSCI Total Intl Stk", "is_etf": True, "sector": "International ETF", "tags": ["index", "global"]},
            {"ticker": "ILTB", "name": "iShares Core 10+ Year USD Bond", "is_etf": True, "sector": "Bond ETF", "tags": ["index", "bond"]},
        ],
    },
    "Quality Investing": {
        "description": (
            "Businesses with strong profitability, balance-sheet quality, and "
            "earnings durability."
        ),
        "factor_weights": {
            "quality": 0.40, "stability": 0.20, "momentum": 0.10,
            "growth": 0.10, "liquidity": 0.10, "etf_preference": 0.05,
            "thematic_bonus": 0.05,
        },
        "candidates": [
            {"ticker": "QUAL", "name": "iShares MSCI USA Quality Factor ETF", "is_etf": True, "sector": "Quality ETF", "tags": ["quality", "etf"]},
            {"ticker": "SPHQ", "name": "Invesco S&P 500 Quality ETF", "is_etf": True, "sector": "Quality ETF", "tags": ["quality", "etf"]},
            {"ticker": "AAPL", "name": "Apple Inc.", "is_etf": False, "sector": "Technology", "tags": ["quality"]},
            {"ticker": "V", "name": "Visa Inc.", "is_etf": False, "sector": "Financial Services", "tags": ["quality"]},
            {"ticker": "JNJ", "name": "Johnson & Johnson", "is_etf": False, "sector": "Healthcare", "tags": ["quality"]},
            {"ticker": "PG", "name": "Procter & Gamble Co.", "is_etf": False, "sector": "Consumer Defensive", "tags": ["quality"]},
            {"ticker": "COST", "name": "Costco Wholesale Corp.", "is_etf": False, "sector": "Consumer Defensive", "tags": ["quality"]},
        ],
    },
    "Value Investing": {
        "description": (
            "Stocks and ETFs trading at more attractive valuations with "
            "durable cash generation."
        ),
        "factor_weights": {
            "value": 0.35, "quality": 0.20, "stability": 0.15,
            "liquidity": 0.10, "momentum": 0.05, "etf_preference": 0.05,
            "thematic_bonus": 0.10,
        },
        "candidates": [
            {"ticker": "VTV", "name": "Vanguard Value ETF", "is_etf": True, "sector": "Value ETF", "tags": ["value", "etf"]},
            {"ticker": "VLUE", "name": "iShares MSCI USA Value Factor ETF", "is_etf": True, "sector": "Value ETF", "tags": ["value", "etf"]},
            {"ticker": "BRK-B", "name": "Berkshire Hathaway B", "is_etf": False, "sector": "Financial Services", "tags": ["value"]},
            {"ticker": "JPM", "name": "JPMorgan Chase & Co.", "is_etf": False, "sector": "Financial Services", "tags": ["value"]},
            {"ticker": "BAC", "name": "Bank of America Corp.", "is_etf": False, "sector": "Financial Services", "tags": ["value"]},
            {"ticker": "CVX", "name": "Chevron Corp.", "is_etf": False, "sector": "Energy", "tags": ["value"]},
            {"ticker": "XOM", "name": "Exxon Mobil Corp.", "is_etf": False, "sector": "Energy", "tags": ["value"]},
            {"ticker": "VZ", "name": "Verizon Communications", "is_etf": False, "sector": "Communication Services", "tags": ["value"]},
        ],
    },
}


# ---------------------------------------------------------------------------
# Public introspection
# ---------------------------------------------------------------------------
def get_strategy_names() -> list[str]:
    return list(STRATEGIES.keys())


def get_description(strategy_name: str) -> str:
    return STRATEGIES[strategy_name]["description"]


def get_candidate_tickers(selected_strategies: Iterable[str]) -> list[str]:
    tickers: set[str] = set()
    for strategy_name in selected_strategies:
        for candidate in STRATEGIES[strategy_name]["candidates"]:
            tickers.add(candidate["ticker"])
    return sorted(tickers)


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------
def _percentile_scores(
    candidates: list[dict],
    market_features: dict[str, dict],
    metric_key: str,
    higher_is_better: bool = True,
) -> dict[str, float]:
    values = []
    for candidate in candidates:
        ticker = candidate["ticker"]
        value = market_features.get(ticker, {}).get(metric_key)
        if value is None:
            continue
        values.append((ticker, float(value)))

    neutral = {candidate["ticker"]: 0.5 for candidate in candidates}
    if len(values) < 2:
        return neutral

    ranked = pd.Series({t: v for t, v in values}, dtype="float64")
    pct = ranked.rank(pct=True)
    normalized = pct if higher_is_better else (1.0 - pct)
    normalized = normalized.clip(lower=0.0, upper=1.0)
    for ticker, score in normalized.items():
        neutral[ticker] = float(score)
    return neutral


def _build_component_scores(
    strategy_name: str,
    candidates: list[dict],
    market_features: dict[str, dict],
) -> dict[str, dict[str, float]]:
    momentum_1m = _percentile_scores(candidates, market_features, "momentum_1m", True)
    momentum_3m = _percentile_scores(candidates, market_features, "momentum_3m", True)
    volatility = _percentile_scores(candidates, market_features, "volatility_3m", False)
    pe_score = _percentile_scores(candidates, market_features, "pe_ratio", False)
    roe_score = _percentile_scores(candidates, market_features, "roe", True)
    debt_score = _percentile_scores(candidates, market_features, "debt_to_equity", False)
    margin_score = _percentile_scores(candidates, market_features, "profit_margin", True)
    rev_growth = _percentile_scores(candidates, market_features, "revenue_growth", True)
    earn_growth = _percentile_scores(candidates, market_features, "earnings_growth", True)
    expense_score = _percentile_scores(candidates, market_features, "expense_ratio", False)
    volume_score = _percentile_scores(candidates, market_features, "avg_volume", True)
    size_score = _percentile_scores(candidates, market_features, "market_cap", True)

    components: dict[str, dict[str, float]] = {}
    strategy_tags = STRATEGY_TAGS.get(strategy_name, set())
    for candidate in candidates:
        ticker = candidate["ticker"]
        tags = candidate.get("tags", [])
        features = market_features.get(ticker, {})
        is_etf = bool(features.get("is_etf", candidate.get("is_etf", False)))

        momentum_score = (momentum_1m[ticker] + momentum_3m[ticker]) / 2.0
        if is_etf:
            growth_score = momentum_score
            quality_score = expense_score[ticker]
            value_score = 0.5
        else:
            growth_score = (rev_growth[ticker] + earn_growth[ticker]) / 2.0
            quality_score = (
                roe_score[ticker] + debt_score[ticker] + margin_score[ticker]
            ) / 3.0
            value_score = pe_score[ticker]

        components[ticker] = {
            "momentum": momentum_score,
            "growth": growth_score,
            "stability": volatility[ticker],
            "quality": quality_score,
            "value": value_score,
            "liquidity": volume_score[ticker],
            "low_cost": expense_score[ticker],
            "size": size_score[ticker],
            "etf_preference": 1.0 if is_etf else 0.0,
            "thematic_bonus": 1.0 if any(tag in tags for tag in strategy_tags) else 0.0,
        }
    return components


def _score_candidates(
    strategy_name: str,
    candidates: list[dict],
    market_features: dict[str, dict],
) -> list[dict]:
    factor_weights = STRATEGIES[strategy_name]["factor_weights"]
    component_scores = _build_component_scores(strategy_name, candidates, market_features)
    scored = []

    for candidate in candidates:
        ticker = candidate["ticker"]
        factor_breakdown = {}
        total_score = 0.0
        for factor_name, factor_weight in factor_weights.items():
            component = component_scores[ticker].get(factor_name, 0.5)
            contribution = factor_weight * component
            factor_breakdown[factor_name] = round(contribution, 4)
            total_score += contribution

        feature_sector = market_features.get(ticker, {}).get("sector")
        scored.append(
            {
                **candidate,
                "score": round(total_score, 6),
                "sector": feature_sector or candidate.get("sector", "Unknown"),
                "factor_breakdown": factor_breakdown,
                "component_scores": component_scores[ticker],
            }
        )

    return sorted(scored, key=lambda row: row["score"], reverse=True)


# ---------------------------------------------------------------------------
# Constraint enforcement (resolves C5, C6)
# ---------------------------------------------------------------------------
def _select_with_constraints(
    scored_candidates: list[dict],
    top_n: int,
    sector_cap: float,
    min_etf_share: float,
    *,
    relax_by: int = DEFAULT_RELAX_BY,
) -> list[dict]:
    """Select ``top_n`` rows respecting ETF floor and sector cap.

    Selection happens in three passes:

    1. ETF floor pass — fills the minimum number of ETF slots first.
    2. Score-based pass — adds the highest-scoring remaining candidates
       within the strict sector cap.
    3. Controlled relaxation — only if (1) and (2) cannot reach ``top_n``,
       allow up to ``cap + relax_by`` per sector. Rows added in this pass
       are tagged ``selected_after_relaxation`` so the UI can show that
       the constraint was bent (and by how much).

    If even the relaxed pass cannot reach ``top_n``, the function returns
    the smaller basket. This replaces the legacy fallback that silently
    ignored the sector cap entirely (BUG_REPORT.md C6).
    """

    if not scored_candidates:
        return []

    top_n = min(top_n, len(scored_candidates))
    strict_cap = max(1, int(round(top_n * sector_cap)))
    available_etf_count = sum(1 for c in scored_candidates if c.get("is_etf"))
    min_etf_count = min(available_etf_count, int(math.ceil(top_n * min_etf_share)))

    selected: list[dict] = []
    selected_tickers: set[str] = set()
    sector_counts: dict[str, int] = {}

    def can_add(candidate: dict, cap_limit: int) -> bool:
        sector = candidate.get("sector") or "Unknown"
        return sector_counts.get(sector, 0) < cap_limit

    def add(candidate: dict, reason: str) -> None:
        selected.append({**candidate, "selection_reason": reason})
        selected_tickers.add(candidate["ticker"])
        sector = candidate.get("sector") or "Unknown"
        sector_counts[sector] = sector_counts.get(sector, 0) + 1

    if min_etf_count > 0:
        for candidate in scored_candidates:
            if not candidate.get("is_etf"):
                continue
            if sum(1 for r in selected if r.get("is_etf")) >= min_etf_count:
                break
            if candidate["ticker"] in selected_tickers:
                continue
            if can_add(candidate, strict_cap):
                add(candidate, "selected_for_etf_floor")

    for candidate in scored_candidates:
        if len(selected) >= top_n:
            break
        if candidate["ticker"] in selected_tickers:
            continue
        if can_add(candidate, strict_cap):
            add(candidate, "selected_by_score")

    if len(selected) < top_n and relax_by > 0:
        relaxed_cap = strict_cap + relax_by
        for candidate in scored_candidates:
            if len(selected) >= top_n:
                break
            if candidate["ticker"] in selected_tickers:
                continue
            if can_add(candidate, relaxed_cap):
                add(candidate, "selected_after_relaxation")

    return selected


def _normalize_weights(selected_rows: list[dict]) -> list[float]:
    if not selected_rows:
        return []
    score_total = sum(max(row["score"], 0.01) for row in selected_rows)
    if score_total <= 0:
        equal_weight = 1.0 / len(selected_rows)
        return [equal_weight] * len(selected_rows)
    return [max(row["score"], 0.01) / score_total for row in selected_rows]


def _enforce_sector_cap(
    holdings: list[tuple[str, str, float]],
    ticker_sector: dict[str, str],
    sector_cap: float,
) -> list[tuple[str, str, float]]:
    """Rebalance ``holdings`` so no sector exceeds ``sector_cap`` in weight."""

    if not holdings:
        return holdings

    adjusted: dict[str, float] = {ticker: weight for ticker, _, weight in holdings}
    for _ in range(5):
        sector_totals: dict[str, float] = {}
        for ticker, weight in adjusted.items():
            sector = ticker_sector.get(ticker, "Unknown") or "Unknown"
            sector_totals[sector] = sector_totals.get(sector, 0.0) + weight

        overweight = {s for s, w in sector_totals.items() if w > sector_cap}
        if not overweight:
            break

        next_adjusted: dict[str, float] = {}
        for ticker, weight in adjusted.items():
            sector = ticker_sector.get(ticker, "Unknown") or "Unknown"
            if sector in overweight and sector_totals[sector] > 0:
                next_adjusted[ticker] = weight * (sector_cap / sector_totals[sector])
            else:
                next_adjusted[ticker] = weight

        overweight_sum = sum(
            w for t, w in next_adjusted.items()
            if (ticker_sector.get(t, "Unknown") or "Unknown") in overweight
        )
        remaining_sum = sum(
            w for t, w in next_adjusted.items()
            if (ticker_sector.get(t, "Unknown") or "Unknown") not in overweight
        )
        target_remaining = max(0.0, 1.0 - overweight_sum)
        if remaining_sum > 0:
            scale_up = target_remaining / remaining_sum
            for ticker, weight in list(next_adjusted.items()):
                sector = ticker_sector.get(ticker, "Unknown") or "Unknown"
                if sector not in overweight:
                    next_adjusted[ticker] = weight * scale_up

        total = sum(next_adjusted.values())
        if total > 0:
            next_adjusted = {t: w / total for t, w in next_adjusted.items()}
        adjusted = next_adjusted

    total = sum(adjusted.values())
    if total > 0:
        adjusted = {t: w / total for t, w in adjusted.items()}

    name_lookup = {ticker: name for ticker, name, _ in holdings}
    reweighted = [(t, name_lookup[t], adjusted.get(t, 0.0)) for t in name_lookup]
    reweighted.sort(key=lambda row: row[2], reverse=True)
    return reweighted


def _enforce_etf_floor(
    holdings: list[tuple[str, str, float]],
    ticker_is_etf: dict[str, bool],
    min_etf_share: float,
) -> list[tuple[str, str, float]]:
    """Scale ETF weights up so the combined ETF share meets ``min_etf_share``.

    Resolves C5: with two strategies blended, the per-strategy floor does
    not guarantee the combined basket is above the floor in dollar terms.
    The fix is a final post-merge pass that scales ETF weights up and
    non-ETF weights down so the constraint holds at the basket level.

    No-ops when:
      * the basket is empty,
      * all holdings are ETFs (already 100%),
      * no holdings are ETFs (cannot satisfy floor; logged elsewhere).
    """

    if not holdings or min_etf_share <= 0:
        return holdings

    etf_total = sum(w for t, _, w in holdings if ticker_is_etf.get(t, False))
    non_etf_total = sum(w for t, _, w in holdings if not ticker_is_etf.get(t, False))

    if etf_total <= 0 or non_etf_total <= 0:
        return holdings
    if etf_total >= min_etf_share - 1e-9:
        return holdings

    etf_scale = min_etf_share / etf_total
    non_etf_scale = (1.0 - min_etf_share) / non_etf_total

    rescaled = [
        (t, n, w * (etf_scale if ticker_is_etf.get(t, False) else non_etf_scale))
        for t, n, w in holdings
    ]

    total = sum(w for _, _, w in rescaled)
    if total > 0:
        rescaled = [(t, n, w / total) for t, n, w in rescaled]
    rescaled.sort(key=lambda row: row[2], reverse=True)
    return rescaled


# ---------------------------------------------------------------------------
# Strategy entry points
# ---------------------------------------------------------------------------
def select_dynamic_holdings(
    strategy_name: str,
    market_features: dict[str, dict] | None = None,
    *,
    top_n: int = DEFAULT_TOP_N,
    sector_cap: float = DEFAULT_SECTOR_CAP,
    min_etf_share: float = DEFAULT_MIN_ETF_SHARE,
    relax_by: int = DEFAULT_RELAX_BY,
) -> tuple[list[tuple[str, str, float]], list[dict]]:
    """Dynamically select holdings for one strategy.

    Returns ``(holdings, details)`` where ``holdings`` is a list of
    ``(ticker, name, weight)`` tuples summing to ~1.0 and ``details``
    contains explainability rows.
    """

    if market_features is None:
        market_features = {}

    candidates = STRATEGIES[strategy_name]["candidates"]
    scored = _score_candidates(strategy_name, candidates, market_features)
    selected = _select_with_constraints(
        scored,
        top_n=top_n,
        sector_cap=sector_cap,
        min_etf_share=min_etf_share,
        relax_by=relax_by,
    )
    if not selected:
        return [], []

    normalized_weights = _normalize_weights(selected)
    holdings: list[tuple[str, str, float]] = []
    details: list[dict] = []
    for row, weight in zip(selected, normalized_weights):
        holdings.append((row["ticker"], row["name"], weight))
        top_factors = sorted(
            row["factor_breakdown"].items(),
            key=lambda item: item[1],
            reverse=True,
        )[:3]
        details.append(
            {
                "ticker": row["ticker"],
                "name": row["name"],
                "strategy": strategy_name,
                "weight": round(weight, 6),
                "score": round(row["score"], 6),
                "is_etf": row["is_etf"],
                "sector": row["sector"],
                "selection_reason": row["selection_reason"],
                "top_factors": ", ".join(
                    f"{factor} ({value:.2f})" for factor, value in top_factors
                ),
                **row["component_scores"],
            }
        )
    return holdings, details


def combine_strategies(
    selected_strategies: list[str],
    market_features: dict[str, dict] | None = None,
    *,
    strategy_weights: list[float] | None = None,
    top_n: int = DEFAULT_TOP_N,
    sector_cap: float = DEFAULT_SECTOR_CAP,
    min_etf_share: float = DEFAULT_MIN_ETF_SHARE,
    relax_by: int = DEFAULT_RELAX_BY,
) -> tuple[list[tuple[str, str, float]], list[dict]]:
    """Build dynamic holdings for one or two strategies.

    Two-strategy flow:

    1. Run ``select_dynamic_holdings`` per strategy.
    2. Aggregate by ticker (combined weights are scaled by the
       user-provided strategy weights).
    3. Re-apply ``_enforce_etf_floor`` and ``_enforce_sector_cap`` on the
       **combined** basket so the diversification guarantees hold at the
       portfolio level, not only per strategy (BUG_REPORT.md C5).
    4. Deduplicate explainability rows by ticker so a ticker that came
       from both strategies has one row with ``source_strategies`` listing
       both (BUG_REPORT.md C9).
    """

    if not selected_strategies:
        return [], []

    if market_features is None:
        market_features = {}

    if strategy_weights and len(strategy_weights) == len(selected_strategies):
        positive = [max(float(w), 0.0) for w in strategy_weights]
        total = sum(positive)
        if total > 0:
            normalized_strategy_weights = [w / total for w in positive]
        else:
            normalized_strategy_weights = [1.0 / len(selected_strategies)] * len(selected_strategies)
    else:
        normalized_strategy_weights = [1.0 / len(selected_strategies)] * len(selected_strategies)

    combined: dict[str, dict] = {}
    detail_rows_by_ticker: dict[str, dict] = {}
    ticker_sector: dict[str, str] = {}
    ticker_is_etf: dict[str, bool] = {}

    for strategy_name, strategy_weight in zip(selected_strategies, normalized_strategy_weights):
        holdings, details = select_dynamic_holdings(
            strategy_name,
            market_features=market_features,
            top_n=top_n,
            sector_cap=sector_cap,
            min_etf_share=min_etf_share,
            relax_by=relax_by,
        )
        for ticker, name, weight in holdings:
            adjusted_weight = weight * strategy_weight
            entry = combined.setdefault(ticker, {"name": name, "weight": 0.0})
            entry["weight"] += adjusted_weight

        for row in details:
            ticker = row["ticker"]
            ticker_sector[ticker] = row.get("sector", "Unknown")
            ticker_is_etf[ticker] = bool(row.get("is_etf", False))
            blended = row["weight"] * strategy_weight

            if ticker in detail_rows_by_ticker:
                merged = detail_rows_by_ticker[ticker]
                merged["blended_weight"] += blended
                merged["source_strategies"].append(strategy_name)
                if row["score"] > merged["score"]:
                    merged["score"] = row["score"]
                    merged["top_factors"] = row.get("top_factors", merged.get("top_factors"))
            else:
                detail_rows_by_ticker[ticker] = {
                    **row,
                    "blended_weight": round(blended, 6),
                    "source_strategies": [strategy_name],
                }

    total_weight = sum(entry["weight"] for entry in combined.values())
    if total_weight <= 0:
        return [], []

    holdings_list: list[tuple[str, str, float]] = [
        (ticker, entry["name"], entry["weight"] / total_weight)
        for ticker, entry in combined.items()
    ]
    holdings_list = _enforce_sector_cap(holdings_list, ticker_sector, sector_cap)
    holdings_list = _enforce_etf_floor(holdings_list, ticker_is_etf, min_etf_share)

    final_weight_by_ticker = {t: w for t, _, w in holdings_list}
    detail_rows = []
    for ticker, row in detail_rows_by_ticker.items():
        if ticker not in final_weight_by_ticker:
            continue
        row = dict(row)
        row["blended_weight"] = round(final_weight_by_ticker[ticker], 6)
        row["source_strategies"] = sorted(set(row["source_strategies"]))
        detail_rows.append(row)
    detail_rows.sort(key=lambda r: r["blended_weight"], reverse=True)

    return holdings_list, detail_rows
