"""Unit tests for multi-customer onboarding (domain/onboarding.py)."""

import pandas as pd
import pytest

from domain.errors import ValidationError
from domain.onboarding import (
    EXHAUSTIVE_THRESHOLD,
    CombinationResult,
    NewCustomer,
    count_combinations,
    estimate_search,
    evaluate_multi_customer,
    inject_customer_demand,
    rank_top_n,
    validate_new_customers,
)
from domain.params import IntegratedParams, SupplierParams

P = IntegratedParams(horizon_weeks=20, w_capacity=0.0)
SP = SupplierParams()


def _sites(n=3):
    """A small existing-site table."""
    return pd.DataFrame({
        "site_id": [f"S{i}" for i in range(1, n + 1)],
        "next_demand_week": [2, 5, 9][:n],
        "interval_weeks": [8, 9, 10][:n],
        "country": ["usa"] * n,
        "is_row": [False] * n,
    })


# ---------------------------------------------------------------------------
# NewCustomer / windows
# ---------------------------------------------------------------------------

def test_window_inclusive():
    c = NewCustomer("A", earliest_week=4, latest_week=9)
    assert c.window == [4, 5, 6, 7, 8, 9]


def test_single_week_window():
    c = NewCustomer("A", earliest_week=3, latest_week=3)
    assert c.window == [3]


def test_eu_restricted_from_country_fallback():
    assert NewCustomer("A", country="denmark").is_eu_restricted is True
    assert NewCustomer("A", country="usa").is_eu_restricted is False


def test_eu_restricted_explicit_flag_wins():
    assert NewCustomer("A", country="usa", eu_restricted=True).is_eu_restricted is True


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_validation_accepts_valid_rows():
    cs = [NewCustomer("A", earliest_week=1, latest_week=5, interval_weeks=7)]
    assert validate_new_customers(cs, P) == []


def test_validation_rejects_missing_site_id():
    errs = validate_new_customers([NewCustomer("", latest_week=3)], P)
    assert any("Site_ID is required" in e for e in errs)


def test_validation_rejects_duplicate_site_id():
    cs = [NewCustomer("A", latest_week=3), NewCustomer("A", latest_week=4)]
    errs = validate_new_customers(cs, P)
    assert any("duplicate Site_ID" in e for e in errs)


def test_validation_rejects_latest_before_earliest():
    errs = validate_new_customers([NewCustomer("A", earliest_week=5, latest_week=2)], P)
    assert any("must be >=" in e for e in errs)


def test_validation_rejects_beyond_horizon():
    errs = validate_new_customers([NewCustomer("A", latest_week=99)], P)
    assert any("exceeds the" in e for e in errs)


def test_validation_reports_row_numbers():
    cs = [NewCustomer("A", latest_week=3), NewCustomer("", latest_week=3)]
    errs = validate_new_customers(cs, P)
    assert any("Row 2" in e for e in errs)


# ---------------------------------------------------------------------------
# Demand injection
# ---------------------------------------------------------------------------

def test_inject_demand_recurring():
    base = [0] * (P.horizon_weeks + 1)
    cs = [NewCustomer("A", interval_weeks=7)]
    out = inject_customer_demand(base, cs, {"A": 3}, P)
    assert [w for w in range(1, 21) if out[w] > 0] == [3, 10, 17]


def test_inject_demand_does_not_mutate_base():
    base = [0] * (P.horizon_weeks + 1)
    cs = [NewCustomer("A", interval_weeks=7)]
    inject_customer_demand(base, cs, {"A": 3}, P)
    assert sum(base) == 0


def test_inject_eu_only_filters_non_eu():
    base = [0] * (P.horizon_weeks + 1)
    cs = [NewCustomer("A", interval_weeks=7, country="usa"),
          NewCustomer("B", interval_weeks=7, country="denmark")]
    out = inject_customer_demand(base, cs, {"A": 2, "B": 2}, P, eu_only=True)
    # Only B contributes
    assert out[2] == 1


def test_inject_multiple_customers_accumulate():
    base = [0] * (P.horizon_weeks + 1)
    cs = [NewCustomer("A", interval_weeks=10), NewCustomer("B", interval_weeks=10)]
    out = inject_customer_demand(base, cs, {"A": 4, "B": 4}, P)
    assert out[4] == 2


# ---------------------------------------------------------------------------
# Search-space estimation
# ---------------------------------------------------------------------------

def test_count_combinations_product():
    cs = [NewCustomer("A", earliest_week=1, latest_week=3),   # 3
          NewCustomer("B", earliest_week=2, latest_week=5)]   # 4
    assert count_combinations(cs) == 12


def test_estimate_flags_exhaustive_for_small_space():
    cs = [NewCustomer("A", earliest_week=1, latest_week=3)]
    est = estimate_search(cs)
    assert est["exhaustive"] is True
    assert est["combinations"] == 3


def test_estimate_flags_heuristic_for_large_space():
    cs = [NewCustomer(f"C{i}", earliest_week=1, latest_week=13) for i in range(5)]
    est = estimate_search(cs)
    assert est["combinations"] > EXHAUSTIVE_THRESHOLD
    assert est["exhaustive"] is False


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------

def _res(weeks, pen, ot, cap, comp):
    return CombinationResult(
        selected_weeks=weeks, feasible=True,
        delta_penalty=pen, delta_overtime=ot,
        delta_capacity=cap, delta_composite=comp,
    )


def test_ranking_sorted_per_objective():
    rs = [
        _res({"A": 1}, 300, 10, 5, 315),
        _res({"A": 2}, 100, 30, 1, 131),
        _res({"A": 3}, 200, 20, 3, 223),
    ]
    ranked = rank_top_n(rs)
    assert [r.selected_weeks["A"] for r in ranked["penalty"]] == [2, 3, 1]
    assert [r.selected_weeks["A"] for r in ranked["overtime"]] == [1, 3, 2]
    assert [r.selected_weeks["A"] for r in ranked["capacity"]] == [2, 3, 1]


def test_ranking_caps_at_top_n():
    rs = [_res({"A": i}, i, i, i, i) for i in range(1, 12)]
    ranked = rank_top_n(rs)
    assert all(len(v) == 5 for v in ranked.values())


def test_ranking_excludes_infeasible():
    rs = [_res({"A": 1}, 10, 10, 10, 10),
          CombinationResult(selected_weeks={"A": 2}, feasible=False, reason="bad")]
    ranked = rank_top_n(rs)
    assert len(ranked["penalty"]) == 1


def test_ranking_deterministic_on_ties():
    rs = [_res({"A": 3}, 100, 5, 5, 110), _res({"A": 1}, 100, 5, 5, 110)]
    a = rank_top_n(rs)["penalty"]
    b = rank_top_n(list(reversed(rs)))["penalty"]
    assert [r.selected_weeks for r in a] == [r.selected_weeks for r in b]


# ---------------------------------------------------------------------------
# End-to-end evaluation
# ---------------------------------------------------------------------------

def test_single_customer_exhaustive():
    cs = [NewCustomer("N1", earliest_week=2, latest_week=5, interval_weeks=9)]
    out = evaluate_multi_customer(_sites(), cs, P, SP)
    assert out["used_heuristic"] is False
    assert out["search_space"] == 4
    assert len(out["rankings"]["penalty"]) >= 1
    # every ranked result assigns a week inside the window
    for r in out["rankings"]["penalty"]:
        assert 2 <= r.selected_weeks["N1"] <= 5


def test_two_customers_independent_weeks():
    cs = [
        NewCustomer("N1", earliest_week=2, latest_week=4, interval_weeks=9),
        NewCustomer("N2", earliest_week=6, latest_week=8, interval_weeks=9),
    ]
    out = evaluate_multi_customer(_sites(), cs, P, SP)
    assert out["search_space"] == 9
    best = out["rankings"]["penalty"][0]
    assert 2 <= best.selected_weeks["N1"] <= 4
    assert 6 <= best.selected_weeks["N2"] <= 8


def test_empty_customers_raises():
    with pytest.raises(ValidationError):
        evaluate_multi_customer(_sites(), [], P, SP)


def test_invalid_customer_raises_validation_error():
    cs = [NewCustomer("N1", earliest_week=5, latest_week=2)]
    with pytest.raises(ValidationError):
        evaluate_multi_customer(_sites(), cs, P, SP)


def test_progress_callback_invoked():
    calls = []
    cs = [NewCustomer("N1", earliest_week=2, latest_week=4, interval_weeks=9)]
    evaluate_multi_customer(_sites(), cs, P, SP, progress=lambda f, m: calls.append((f, m)))
    assert calls, "progress callback should be called"


def test_baseline_shared_and_deltas_relative():
    cs = [NewCustomer("N1", earliest_week=2, latest_week=3, interval_weeks=9)]
    out = evaluate_multi_customer(_sites(), cs, P, SP)
    base = out["base_summary"]
    for r in out["rankings"]["penalty"]:
        assert r.delta_penalty == pytest.approx(r.total_penalty - base["total_penalty_cost"])


def test_heuristic_matches_exhaustive_on_small_space(monkeypatch):
    """With the threshold forced low, the heuristic should find the exhaustive optimum."""
    import domain.onboarding as ob

    cs = [
        NewCustomer("N1", earliest_week=2, latest_week=4, interval_weeks=9),
        NewCustomer("N2", earliest_week=6, latest_week=8, interval_weeks=9),
    ]
    exhaustive = evaluate_multi_customer(_sites(), cs, P, SP)
    assert exhaustive["used_heuristic"] is False
    best_exhaustive = exhaustive["rankings"]["penalty"][0].delta_composite

    monkeypatch.setattr(ob, "EXHAUSTIVE_THRESHOLD", 1)
    heuristic = ob.evaluate_multi_customer(_sites(), cs, P, SP)
    assert heuristic["used_heuristic"] is True
    best_heuristic = heuristic["rankings"]["penalty"][0].delta_composite
    assert best_heuristic == pytest.approx(best_exhaustive)


def test_heuristic_evaluates_fewer_than_full_space(monkeypatch):
    import domain.onboarding as ob
    monkeypatch.setattr(ob, "EXHAUSTIVE_THRESHOLD", 1)
    cs = [
        NewCustomer("N1", earliest_week=1, latest_week=6, interval_weeks=9),
        NewCustomer("N2", earliest_week=1, latest_week=6, interval_weeks=9),
    ]
    out = ob.evaluate_multi_customer(_sites(), cs, P, SP, max_seeds=2, max_passes=2)
    assert out["used_heuristic"] is True
    assert out["combinations_evaluated"] <= out["search_space"]
