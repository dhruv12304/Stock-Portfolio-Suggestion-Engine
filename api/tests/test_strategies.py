"""Unit tests for api.strategies.

Coverage maps to BUG_REPORT.md:
- C5 combined-basket ETF floor enforcement.
- C6 controlled sector relaxation (no silent cap violation).
- C9 explainability rows are aggregated by ticker across strategies.
- General determinism of factor scoring with synthetic market features.
"""

from __future__ import annotations

import pytest

from api import strategies
from api.strategies import (
    DEFAULT_MIN_ETF_SHARE,
    DEFAULT_SECTOR_CAP,
    _enforce_etf_floor,
    _select_with_constraints,
    combine_strategies,
    get_candidate_tickers,
    get_strategy_names,
    select_dynamic_holdings,
)


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------
def test_get_strategy_names_returns_five():
    names = get_strategy_names()
    assert len(names) == 5
    assert "Growth Investing" in names
    assert "Quality Investing" in names


def test_get_candidate_tickers_is_sorted_and_unique():
    tickers = get_candidate_tickers(["Growth Investing", "Quality Investing"])
    assert tickers == sorted(set(tickers))
    assert "QQQ" in tickers
    assert "AAPL" in tickers


# ---------------------------------------------------------------------------
# Selection: weights, ETF floor, sector cap
# ---------------------------------------------------------------------------
def test_select_with_constraints_respects_strict_cap():
    """All five Growth ETFs vs stocks must still produce <=2 per sector."""

    scored = [
        {"ticker": "T1", "name": "T1", "is_etf": True, "sector": "Tech", "score": 0.99},
        {"ticker": "T2", "name": "T2", "is_etf": True, "sector": "Tech", "score": 0.98},
        {"ticker": "T3", "name": "T3", "is_etf": False, "sector": "Tech", "score": 0.97},
        {"ticker": "T4", "name": "T4", "is_etf": False, "sector": "Tech", "score": 0.96},
        {"ticker": "T5", "name": "T5", "is_etf": False, "sector": "Healthcare", "score": 0.95},
    ]
    selected = _select_with_constraints(
        scored, top_n=5, sector_cap=0.4, min_etf_share=0.3, relax_by=0,
    )
    sector_counts: dict[str, int] = {}
    for row in selected:
        sector_counts[row["sector"]] = sector_counts.get(row["sector"], 0) + 1
    assert max(sector_counts.values()) <= 2  # strict cap = round(5 * 0.4) = 2


def test_select_with_constraints_relaxation_is_controlled_and_tagged():
    """C6: relaxation bumps the cap by 1 and tags affected rows."""

    scored = [
        {"ticker": f"T{i}", "name": f"T{i}", "is_etf": False,
         "sector": "Tech", "score": 1.0 - i * 0.01}
        for i in range(6)
    ]
    selected = _select_with_constraints(
        scored, top_n=5, sector_cap=0.4, min_etf_share=0.0, relax_by=1,
    )
    # Strict cap = 2, +1 relax = 3. Total Tech holdings must be <= 3.
    tech_count = sum(1 for r in selected if r["sector"] == "Tech")
    assert tech_count <= 3
    assert any(r["selection_reason"] == "selected_after_relaxation" for r in selected)


def test_select_with_constraints_returns_smaller_basket_when_unsatisfiable():
    """C6: with relax_by=0 we accept a smaller basket rather than violating the cap."""

    scored = [
        {"ticker": f"T{i}", "name": f"T{i}", "is_etf": False,
         "sector": "Tech", "score": 1.0 - i * 0.01}
        for i in range(6)
    ]
    selected = _select_with_constraints(
        scored, top_n=5, sector_cap=0.4, min_etf_share=0.0, relax_by=0,
    )
    # Only 2 should fit under the strict cap; no violation.
    assert len(selected) == 2
    sectors = [r["sector"] for r in selected]
    assert sectors.count("Tech") == 2


def test_select_with_constraints_fills_etf_floor_first():
    scored = [
        {"ticker": "NVDA", "name": "NVDA", "is_etf": False, "sector": "Tech", "score": 0.99},
        {"ticker": "META", "name": "META", "is_etf": False, "sector": "Comms", "score": 0.98},
        {"ticker": "AMZN", "name": "AMZN", "is_etf": False, "sector": "Cyclical", "score": 0.97},
        {"ticker": "QQQ", "name": "QQQ", "is_etf": True, "sector": "Growth ETF", "score": 0.50},
        {"ticker": "VUG", "name": "VUG", "is_etf": True, "sector": "Growth ETF", "score": 0.49},
    ]
    selected = _select_with_constraints(
        scored, top_n=5, sector_cap=0.6, min_etf_share=0.4,
    )
    etf_count = sum(1 for r in selected if r["is_etf"])
    assert etf_count >= 2  # ceil(5 * 0.4) = 2


# ---------------------------------------------------------------------------
# ETF floor on combined basket (resolves C5)
# ---------------------------------------------------------------------------
def test_enforce_etf_floor_scales_etf_up_when_below_floor():
    holdings = [
        ("QQQ", "Invesco QQQ", 0.15),
        ("NVDA", "NVIDIA", 0.40),
        ("META", "Meta", 0.30),
        ("AMZN", "Amazon", 0.15),
    ]
    is_etf = {"QQQ": True, "NVDA": False, "META": False, "AMZN": False}
    adjusted = _enforce_etf_floor(holdings, is_etf, min_etf_share=0.30)
    etf_total = sum(w for t, _, w in adjusted if is_etf[t])
    non_etf_total = sum(w for t, _, w in adjusted if not is_etf[t])
    assert etf_total == pytest.approx(0.30, abs=1e-4)
    assert (etf_total + non_etf_total) == pytest.approx(1.0, abs=1e-6)


def test_enforce_etf_floor_no_op_when_already_above():
    holdings = [
        ("QQQ", "Invesco QQQ", 0.50),
        ("NVDA", "NVIDIA", 0.50),
    ]
    is_etf = {"QQQ": True, "NVDA": False}
    adjusted = _enforce_etf_floor(holdings, is_etf, min_etf_share=0.30)
    assert dict((t, w) for t, _, w in adjusted) == {"QQQ": 0.50, "NVDA": 0.50}


def test_enforce_etf_floor_handles_no_etfs():
    holdings = [
        ("NVDA", "NVIDIA", 0.6),
        ("META", "Meta", 0.4),
    ]
    is_etf = {"NVDA": False, "META": False}
    adjusted = _enforce_etf_floor(holdings, is_etf, min_etf_share=0.30)
    # Cannot satisfy floor when there are no ETFs; return unchanged.
    assert adjusted == holdings


def test_enforce_etf_floor_handles_all_etfs():
    holdings = [
        ("QQQ", "QQQ", 0.5),
        ("VUG", "VUG", 0.5),
    ]
    is_etf = {"QQQ": True, "VUG": True}
    adjusted = _enforce_etf_floor(holdings, is_etf, min_etf_share=0.30)
    assert adjusted == holdings


# ---------------------------------------------------------------------------
# Dynamic selection end-to-end
# ---------------------------------------------------------------------------
def test_select_dynamic_holdings_produces_weights_summing_to_one():
    holdings, details = select_dynamic_holdings(
        "Growth Investing",
        market_features={},
        top_n=5,
    )
    assert len(holdings) > 0
    assert sum(w for _, _, w in holdings) == pytest.approx(1.0, abs=1e-6)
    assert all(0 < w <= 1 for _, _, w in holdings)
    assert len(details) == len(holdings)


def test_select_dynamic_holdings_with_market_features_changes_order():
    # Without features the picks should follow factor-weight + thematic bonuses;
    # with features tilting one ticker strongly, that ticker should rank higher.
    market_features = {
        "NVDA": {
            "momentum_1m": 0.5, "momentum_3m": 0.6, "revenue_growth": 0.4,
            "earnings_growth": 0.5, "pe_ratio": 30, "roe": 0.4,
            "profit_margin": 0.4, "avg_volume": 1e9,
            "is_etf": False, "sector": "Technology",
        }
    }
    holdings, _ = select_dynamic_holdings(
        "Growth Investing",
        market_features=market_features,
        top_n=5,
    )
    tickers = [t for t, _, _ in holdings]
    assert "NVDA" in tickers


# ---------------------------------------------------------------------------
# Combine strategies (resolves C9 dedup, C5 enforcement, C6 propagation)
# ---------------------------------------------------------------------------
def test_combine_two_strategies_deduplicates_explainability(monkeypatch):
    """C9: a ticker appearing in both strategies must produce ONE detail row."""

    fake_holdings = {
        "S1": ([("MSFT", "Microsoft", 0.5), ("NVDA", "NVIDIA", 0.5)],
               [{"ticker": "MSFT", "name": "Microsoft", "strategy": "S1",
                 "weight": 0.5, "score": 0.9, "is_etf": False,
                 "sector": "Technology", "selection_reason": "selected_by_score",
                 "top_factors": "growth (0.40)",
                 "momentum": 0.5, "growth": 0.7, "stability": 0.4, "quality": 0.6,
                 "value": 0.3, "liquidity": 0.7, "low_cost": 0.2,
                 "size": 0.9, "etf_preference": 0, "thematic_bonus": 1.0,
                },
                {"ticker": "NVDA", "name": "NVIDIA", "strategy": "S1",
                 "weight": 0.5, "score": 0.95, "is_etf": False,
                 "sector": "Technology", "selection_reason": "selected_by_score",
                 "top_factors": "growth (0.45)",
                 "momentum": 0.9, "growth": 0.95, "stability": 0.2, "quality": 0.5,
                 "value": 0.2, "liquidity": 0.9, "low_cost": 0.1,
                 "size": 1.0, "etf_preference": 0, "thematic_bonus": 1.0,
                },
                ]),
        "S2": ([("MSFT", "Microsoft", 0.6), ("AAPL", "Apple", 0.4)],
               [{"ticker": "MSFT", "name": "Microsoft", "strategy": "S2",
                 "weight": 0.6, "score": 0.85, "is_etf": False,
                 "sector": "Technology", "selection_reason": "selected_by_score",
                 "top_factors": "quality (0.35)",
                 "momentum": 0.5, "growth": 0.6, "stability": 0.5, "quality": 0.9,
                 "value": 0.4, "liquidity": 0.7, "low_cost": 0.2,
                 "size": 0.9, "etf_preference": 0, "thematic_bonus": 1.0,
                },
                {"ticker": "AAPL", "name": "Apple", "strategy": "S2",
                 "weight": 0.4, "score": 0.80, "is_etf": False,
                 "sector": "Technology", "selection_reason": "selected_by_score",
                 "top_factors": "quality (0.32)",
                 "momentum": 0.7, "growth": 0.5, "stability": 0.5, "quality": 0.95,
                 "value": 0.5, "liquidity": 0.95, "low_cost": 0.2,
                 "size": 1.0, "etf_preference": 0, "thematic_bonus": 1.0,
                },
                ]),
    }

    def fake_select(strategy_name, market_features=None, **kwargs):
        return fake_holdings[strategy_name]

    monkeypatch.setattr(strategies, "select_dynamic_holdings", fake_select)

    holdings, details = combine_strategies(
        ["S1", "S2"],
        market_features={},
        strategy_weights=[0.5, 0.5],
    )

    tickers = [t for t, _, _ in holdings]
    assert tickers.count("MSFT") == 1  # combined into one line

    msft_details = [r for r in details if r["ticker"] == "MSFT"]
    assert len(msft_details) == 1
    assert sorted(msft_details[0]["source_strategies"]) == ["S1", "S2"]
    # Higher score wins the merged row.
    assert msft_details[0]["score"] == 0.9


def test_combine_single_strategy_passes_through():
    holdings, details = combine_strategies(
        ["Index Investing"],
        market_features={},
        strategy_weights=[1.0],
    )
    assert sum(w for _, _, w in holdings) == pytest.approx(1.0, abs=1e-6)
    for row in details:
        assert row["source_strategies"] == ["Index Investing"]


def test_combine_normalizes_user_weights():
    holdings_60, _ = combine_strategies(
        ["Growth Investing", "Quality Investing"],
        market_features={},
        strategy_weights=[0.6, 0.4],
    )
    holdings_30, _ = combine_strategies(
        ["Growth Investing", "Quality Investing"],
        market_features={},
        strategy_weights=[30, 20],  # same ratio, different scale -> same result
    )
    weights_60 = {t: w for t, _, w in holdings_60}
    weights_30 = {t: w for t, _, w in holdings_30}
    assert weights_60.keys() == weights_30.keys()
    for ticker in weights_60:
        assert weights_60[ticker] == pytest.approx(weights_30[ticker], abs=1e-6)


def test_combine_empty_inputs():
    holdings, details = combine_strategies([], market_features={})
    assert holdings == []
    assert details == []


def test_combine_enforces_etf_floor_on_combined_basket():
    """C5 regression: Growth + Quality has very few ETFs; the combined ETF
    weight must be >= the floor after merging."""

    holdings, _ = combine_strategies(
        ["Growth Investing", "Quality Investing"],
        market_features={},
        strategy_weights=[0.5, 0.5],
    )
    # Reverse-lookup ETF status from STRATEGIES candidates.
    is_etf_lookup: dict[str, bool] = {}
    for strat in ["Growth Investing", "Quality Investing"]:
        for c in strategies.STRATEGIES[strat]["candidates"]:
            is_etf_lookup[c["ticker"]] = c["is_etf"]

    etf_share = sum(w for t, _, w in holdings if is_etf_lookup.get(t, False))
    assert etf_share + 1e-6 >= DEFAULT_MIN_ETF_SHARE
