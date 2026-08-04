"""Property-based tests for onboarding_recommendation.py using Hypothesis.

Updated for the marginal-cost approach (full optimizer with/without new sites).
"""

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from onboarding_recommendation import validate_onboarding_inputs, enumerate_candidates


# ---------------------------------------------------------------------------
# Property 1: Invalid inputs are rejected
# ---------------------------------------------------------------------------

@given(
    total_generators=st.integers(min_value=-100, max_value=500),
    start_week=st.integers(min_value=-100, max_value=52),
    end_week=st.integers(min_value=-100, max_value=52),
)
@settings(max_examples=100)
def test_property1_invalid_inputs_are_rejected(total_generators, start_week, end_week):
    assume(total_generators < 1 or start_week < 1 or start_week >= end_week)
    errors = validate_onboarding_inputs(total_generators, start_week, end_week)
    assert len(errors) > 0


# ---------------------------------------------------------------------------
# Property 2: Valid inputs produce no errors
# ---------------------------------------------------------------------------

@given(
    total_generators=st.integers(min_value=1, max_value=500),
    start_week=st.integers(min_value=1, max_value=52),
    end_week=st.integers(min_value=1, max_value=52),
)
@settings(max_examples=100)
def test_property2_valid_inputs_produce_no_errors(total_generators, start_week, end_week):
    assume(end_week > start_week)
    errors = validate_onboarding_inputs(total_generators, start_week, end_week)
    assert len(errors) == 0


# ---------------------------------------------------------------------------
# Property 3: Candidate enumeration is complete and correct
# ---------------------------------------------------------------------------

@given(
    start_week=st.integers(min_value=1, max_value=52),
    end_week=st.integers(min_value=1, max_value=52),
)
@settings(max_examples=100)
def test_property3_candidate_enumeration_complete_and_correct(start_week, end_week):
    assume(end_week > start_week)
    candidates = enumerate_candidates(start_week, end_week)
    assert len(candidates) == end_week - start_week + 1
    for c in candidates:
        assert start_week <= c <= end_week
    assert candidates == sorted(candidates)
    assert len(candidates) == len(set(candidates))


import pandas as pd
import warnings
from integrated_cost_optimizer import IntegratedParams, clean_sites
from onboarding_recommendation import (
    evaluate_all_candidates,
    rank_and_select_top5,
    add_new_sites_demand,
    compute_batch_metrics,
    format_cost_thousands,
    export_recommendation_excel,
)


def _make_active_df(n_sites: int, params: IntegratedParams) -> pd.DataFrame:
    """Create a small active_df with n_sites for property tests."""
    rows = []
    for i in range(n_sites):
        rows.append({
            "site_id": f"T{i+1:03d}",
            "active": "Y",
            "next_demand_week": (i % params.horizon_weeks) + 1,
            "interval_weeks": 10,
            "country": "usa",
        })
    raw = pd.DataFrame(rows)
    active, _ = clean_sites(raw, params)
    return active


# ---------------------------------------------------------------------------
# Property 4: Demand injection adds correct recurring demand
# ---------------------------------------------------------------------------

@given(
    candidate_week=st.integers(min_value=1, max_value=52),
    interval=st.integers(min_value=1, max_value=26),
    n_sites=st.integers(min_value=1, max_value=5),
)
@settings(max_examples=100)
def test_property4_demand_injection_correct(candidate_week, interval, n_sites):
    """New sites add exactly n_sites demand at candidate_week and every
    interval weeks thereafter."""
    params = IntegratedParams()
    base = [0] * (params.horizon_weeks + 1)
    sites = [{"interval_weeks": interval, "country": "usa"} for _ in range(n_sites)]
    d = add_new_sites_demand(base, sites, candidate_week, params)

    # Check demand at expected weeks
    w = candidate_week
    while w <= params.horizon_weeks:
        assert d[w] == n_sites, f"Expected {n_sites} at week {w}, got {d[w]}"
        w += interval

    # Base not mutated
    assert all(v == 0 for v in base)


# ---------------------------------------------------------------------------
# Property 5: All three objectives are evaluated in ranking
# ---------------------------------------------------------------------------

@given(
    n_sites=st.integers(min_value=1, max_value=3),
    start_week=st.integers(min_value=1, max_value=5),
)
@settings(max_examples=20, deadline=None)
def test_property5_all_three_objectives_in_ranking(n_sites, start_week):
    """rank_and_select_top5 returns keys for all three objectives."""
    params = IntegratedParams()
    active = _make_active_df(3, params)
    sites = [{"interval_weeks": 10, "country": "usa"} for _ in range(n_sites)]
    end_week = min(start_week + 3, params.horizon_weeks)
    assume(end_week > start_week)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        _, results = evaluate_all_candidates(
            active, sites, start_week, end_week, params,
        )

    if results:
        top5 = rank_and_select_top5(results)
        assert set(top5.keys()) == {"penalty", "overtime", "capacity"}


# ---------------------------------------------------------------------------
# Property 6: Marginal costs are consistent
# ---------------------------------------------------------------------------

@given(
    n_sites=st.integers(min_value=1, max_value=3),
    start_week=st.integers(min_value=1, max_value=5),
)
@settings(max_examples=20, deadline=None)
def test_property6_marginal_costs_consistent(n_sites, start_week):
    """delta = total - baseline for each cost component."""
    params = IntegratedParams()
    active = _make_active_df(3, params)
    sites = [{"interval_weeks": 10, "country": "usa"} for _ in range(n_sites)]
    end_week = min(start_week + 2, params.horizon_weeks)
    assume(end_week > start_week)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        base, results = evaluate_all_candidates(
            active, sites, start_week, end_week, params,
        )

    for r in results:
        assert abs(r["delta_penalty"] - (r["total_penalty"] - base["total_penalty_cost"])) < 0.01
        assert abs(r["delta_overtime"] - (r["total_overtime"] - base["total_overtime_cost"])) < 0.01
        assert abs(r["delta_capacity"] - (r["total_capacity"] - base["total_capacity_cost"])) < 0.01


# ---------------------------------------------------------------------------
# Property 7: Rankings are sorted ascending by delta and capped at 5
# ---------------------------------------------------------------------------

@given(
    n_sites=st.integers(min_value=1, max_value=3),
    start_week=st.integers(min_value=1, max_value=5),
)
@settings(max_examples=20, deadline=None)
def test_property7_rankings_sorted_and_capped(n_sites, start_week):
    params = IntegratedParams()
    active = _make_active_df(3, params)
    sites = [{"interval_weeks": 10, "country": "usa"} for _ in range(n_sites)]
    end_week = min(start_week + 6, params.horizon_weeks)
    assume(end_week > start_week)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        _, results = evaluate_all_candidates(
            active, sites, start_week, end_week, params,
        )

    if results:
        top5 = rank_and_select_top5(results)
        for key in ("penalty", "overtime", "capacity"):
            ranked = top5[key]
            assert len(ranked) <= 5
            deltas = [r[f"delta_{key}"] for r in ranked]
            assert deltas == sorted(deltas)


# ---------------------------------------------------------------------------
# Property 8: Each result contains a valid plan_df
# ---------------------------------------------------------------------------

@given(
    n_sites=st.integers(min_value=1, max_value=3),
    start_week=st.integers(min_value=1, max_value=5),
)
@settings(max_examples=20, deadline=None)
def test_property8_results_contain_valid_plan_df(n_sites, start_week):
    """Each result should have a plan_df with the right number of rows."""
    params = IntegratedParams()
    active = _make_active_df(3, params)
    sites = [{"interval_weeks": 10, "country": "usa"} for _ in range(n_sites)]
    end_week = min(start_week + 2, params.horizon_weeks)
    assume(end_week > start_week)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        _, results = evaluate_all_candidates(
            active, sites, start_week, end_week, params,
        )

    for r in results:
        pdf = r["plan_df"]
        assert len(pdf) == params.horizon_weeks
        assert "Good_Production" in pdf.columns
        assert "Week" in pdf.columns


# ---------------------------------------------------------------------------
# Property 9: Batch metrics match independent computation
# ---------------------------------------------------------------------------

@given(
    n_sites=st.integers(min_value=1, max_value=3),
    start_week=st.integers(min_value=1, max_value=5),
)
@settings(max_examples=20, deadline=None)
def test_property9_batch_metrics_match(n_sites, start_week):
    from integrated_cost_optimizer import batches_needed as bn

    params = IntegratedParams()
    active = _make_active_df(3, params)
    sites = [{"interval_weeks": 10, "country": "usa"} for _ in range(n_sites)]
    end_week = min(start_week + 2, params.horizon_weeks)
    assume(end_week > start_week)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        _, results = evaluate_all_candidates(
            active, sites, start_week, end_week, params,
        )

    for r in results:
        metrics = compute_batch_metrics(r["plan_df"], params)
        exp_1 = exp_2 = exp_3 = 0
        for good in r["plan_df"]["Good_Production"]:
            nb = bn(int(good), params)
            if nb == 1: exp_1 += 1
            elif nb == 2: exp_2 += 1
            elif nb >= 3: exp_3 += 1
        assert metrics["weeks_1_batch"] == exp_1
        assert metrics["weeks_2_batch"] == exp_2
        assert metrics["weeks_3_batch"] == exp_3


# ---------------------------------------------------------------------------
# Property 10: Cost formatting produces correct $XK string
# ---------------------------------------------------------------------------

@given(
    cost=st.floats(min_value=0.0, max_value=1e9, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=100)
def test_property10_cost_formatting_correct(cost):
    import re
    formatted = format_cost_thousands(cost)
    assert re.fullmatch(r"\$-?\d+K", formatted)
    num_str = formatted[1:-1]
    assert int(num_str) == round(cost / 1000)


# ---------------------------------------------------------------------------
# Property 11: Export produces valid Excel with correct structure
# ---------------------------------------------------------------------------

@given(
    n_sites=st.integers(min_value=1, max_value=3),
    start_week=st.integers(min_value=1, max_value=5),
)
@settings(max_examples=10, deadline=None)
def test_property11_export_valid_excel(n_sites, start_week):
    import io
    import openpyxl

    params = IntegratedParams()
    active = _make_active_df(3, params)
    sites = [{"interval_weeks": 10, "country": "usa"} for _ in range(n_sites)]
    end_week = min(start_week + 2, params.horizon_weeks)
    assume(end_week > start_week)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        base, results = evaluate_all_candidates(
            active, sites, start_week, end_week, params,
        )

    if not results:
        return

    top5 = rank_and_select_top5(results)
    xlsx = export_recommendation_excel(top5, base, params)

    assert len(xlsx) > 0
    wb = openpyxl.load_workbook(io.BytesIO(xlsx))
    expected = {"Summary", "By_Penalty", "By_Overtime", "By_Capacity"}
    assert expected.issubset(set(wb.sheetnames))
