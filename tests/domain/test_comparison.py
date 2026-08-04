"""Unit tests for baseline cost and plan comparison (domain/comparison.py)."""

import pandas as pd
import pytest

from domain.comparison import (
    compare_plans,
    compute_baseline_cost,
    weekly_comparison,
)
from domain.params import IntegratedParams
from domain.solver import solve_plan_integrated


def _arr(T, spec):
    a = [0] * (T + 1)
    for k, v in spec.items():
        a[k] = v
    return a


def test_perfect_just_in_time_has_zero_penalty():
    p = IntegratedParams(horizon_weeks=4, w_capacity=0.0)
    demand = _arr(4, {1: 10, 2: 10, 3: 10, 4: 10})
    planned = _arr(4, {1: 10, 2: 10, 3: 10, 4: 10})
    res = compute_baseline_cost(planned, demand, p)
    assert res.summary["total_penalty_cost"] == 0.0
    assert res.capacity_violations == []


def test_early_production_incurs_penalty():
    p = IntegratedParams(horizon_weeks=2, w_capacity=0.0)
    demand = _arr(2, {2: 10})
    planned = _arr(2, {1: 10})       # produced a week early
    res = compute_baseline_cost(planned, demand, p)
    assert res.summary["total_penalty_cost"] == p.penalty_rate * 10


def test_late_production_uses_late_rate():
    p = IntegratedParams(horizon_weeks=2, w_capacity=0.0)
    demand = _arr(2, {1: 10})
    planned = _arr(2, {2: 10})       # a week late
    res = compute_baseline_cost(planned, demand, p)
    assert res.summary["total_penalty_cost"] == p.late_penalty_rate * 10


def test_capacity_violation_reported_not_rejected():
    p = IntegratedParams(horizon_weeks=2, w_capacity=0.0)
    demand = _arr(2, {1: 60})
    planned = _arr(2, {1: 60})       # exceeds 45 overtime max
    res = compute_baseline_cost(planned, demand, p)
    assert res.capacity_violations == [(1, 60, 45)]
    assert res.summary["total_composite_cost"] >= 0


def test_shutdown_week_capacity_zero():
    p = IntegratedParams(horizon_weeks=2, w_capacity=0.0)
    demand = _arr(2, {2: 5})
    planned = _arr(2, {1: 5})
    res = compute_baseline_cost(planned, demand, p, shutdown_weeks=[1])
    # producing in a shutdown week is a violation (cap 0)
    assert res.capacity_violations == [(1, 5, 0)]


def test_overtime_weeks_counted():
    p = IntegratedParams(horizon_weeks=2, w_capacity=0.0)
    demand = _arr(2, {1: 40})
    planned = _arr(2, {1: 40})       # > 30 normal max -> overtime
    res = compute_baseline_cost(planned, demand, p)
    assert res.summary["overtime_weeks"] == 1
    assert res.summary["total_overtime_cost"] == p.overtime_rate


def test_totals_tracked():
    p = IntegratedParams(horizon_weeks=3, w_capacity=0.0)
    demand = _arr(3, {1: 5, 2: 5})
    planned = _arr(3, {1: 4, 2: 6})
    res = compute_baseline_cost(planned, demand, p)
    assert res.total_planned == 10
    assert res.total_demand == 10


# ---------------------------------------------------------------------------
# compare_plans
# ---------------------------------------------------------------------------

def test_compare_plans_savings_and_pct():
    base = {"total_penalty_cost": 1000.0, "total_overtime_cost": 200.0,
            "total_capacity_cost": 0.0, "total_composite_cost": 1200.0}
    opt = {"total_penalty_cost": 400.0, "total_overtime_cost": 200.0,
           "total_capacity_cost": 0.0, "total_composite_cost": 600.0}
    rows = compare_plans(base, opt)
    penalty = [r for r in rows if r["Component"] == "Penalty"][0]
    assert penalty["Saving_Abs"] == 600.0
    assert penalty["Saving_Pct"] == pytest.approx(60.0)


def test_compare_plans_zero_baseline_pct_is_none():
    base = {"total_penalty_cost": 0.0, "total_composite_cost": 0.0}
    opt = {"total_penalty_cost": 0.0, "total_composite_cost": 0.0}
    rows = compare_plans(base, opt)
    assert all(r["Saving_Pct"] is None for r in rows)


def test_compare_plans_negative_saving_preserved():
    base = {"total_penalty_cost": 100.0, "total_composite_cost": 100.0}
    opt = {"total_penalty_cost": 300.0, "total_composite_cost": 300.0}
    rows = compare_plans(base, opt)
    penalty = [r for r in rows if r["Component"] == "Penalty"][0]
    assert penalty["Saving_Abs"] == -200.0
    assert penalty["Saving_Pct"] == pytest.approx(-200.0)


def test_compare_plans_has_all_components():
    rows = compare_plans({}, {})
    labels = {r["Component"] for r in rows}
    assert labels == {"Penalty", "Overtime", "Capacity Utilization", "Total Composite"}


# ---------------------------------------------------------------------------
# weekly_comparison
# ---------------------------------------------------------------------------

def test_weekly_comparison_difference():
    p = IntegratedParams(horizon_weeks=3)
    planned = _arr(3, {1: 5, 2: 10, 3: 0})
    opt_df = pd.DataFrame({"Week": [1, 2, 3], "Good_Production": [7, 8, 0]})
    wc = weekly_comparison(planned, opt_df, p)
    assert list(wc["Manual_Production"]) == [5, 10, 0]
    assert list(wc["Optimized_Production"]) == [7, 8, 0]
    assert list(wc["Difference"]) == [2, -2, 0]


def test_baseline_matches_optimizer_cost_model_on_same_plan():
    """Evaluating the optimizer's own plan must reproduce its summary."""
    p = IntegratedParams(horizon_weeks=12, w_capacity=0.0)
    demand = _arr(12, {3: 10, 6: 20, 9: 15})
    row = [0] * 13
    plan_df, summary = solve_plan_integrated(demand, [], [], row, p.row_cap, p)
    planned = [0] + plan_df["Good_Production"].tolist()
    res = compute_baseline_cost(planned, demand, p)
    assert res.summary["total_penalty_cost"] == pytest.approx(summary["total_penalty_cost"])
    assert res.summary["total_overtime_cost"] == pytest.approx(summary["total_overtime_cost"])
    assert res.summary["total_composite_cost"] == pytest.approx(summary["total_composite_cost"])
