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
    assert labels == {
        "Penalty", "Overtime", "Capacity Utilization",
        "Quota Shortfall (advisory)", "Total",
    }


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


# ---------------------------------------------------------------------------
# Feasibility of the manual plan.
#
# The manual plan is evaluated, not solved, so it can break limits the optimizer
# cannot -- and breaking them makes it *cheaper*, because those limits are what
# force stock to be built ahead of time. Left unchecked, the manual plan beats
# every plan the optimizer is allowed to build and the tool reports a negative
# saving. These tests hold both sides to the same rule set.
# ---------------------------------------------------------------------------

def test_eu_shipping_cap_violation_detected():
    """Just-in-time delivery of more EU units than can ship in a week is illegal."""
    p = IntegratedParams(horizon_weeks=3, w_capacity=0.0)
    demand = _arr(3, {2: 10})
    eu = _arr(3, {2: 4})              # 4 EU units due in week 2, only 2 can ship
    planned = _arr(3, {2: 10})        # made in the week they are due

    res = compute_baseline_cost(planned, demand, p, eu_demand=eu, row_cap=2)

    assert res.eu_cap_violations == [(2, 4, 2, 2)]
    assert not res.is_feasible
    assert any("EU-restricted" in r for r in res.infeasibilities)


def test_eu_shipping_cap_satisfied_by_prebuilding():
    """Building the excess a week early is exactly how the cap is met."""
    p = IntegratedParams(horizon_weeks=3, w_capacity=0.0)
    demand = _arr(3, {2: 10})
    eu = _arr(3, {2: 4})
    planned = _arr(3, {1: 2, 2: 8})   # 2 units held at the end of week 1

    res = compute_baseline_cost(planned, demand, p, eu_demand=eu, row_cap=2)

    assert res.eu_cap_violations == []
    assert res.is_feasible
    # ...and it is not free: the pre-build carries a holding penalty.
    assert res.summary["total_penalty_cost"] == p.penalty_rate * 2


def test_eu_cap_unchecked_when_arguments_omitted():
    """Callers that pass no EU data get the previous behaviour, not a false pass."""
    p = IntegratedParams(horizon_weeks=3, w_capacity=0.0)
    demand = _arr(3, {2: 10})
    planned = _arr(3, {2: 10})
    res = compute_baseline_cost(planned, demand, p)
    assert res.eu_cap_violations == []


def test_week_one_eu_excess_is_not_charged_to_either_plan():
    """No week exists before week 1 to build in, so neither plan can satisfy it.

    The solver skips week 1 for the same reason
    (``domain.solver.compute_inventory_bounds``). Flagging it here would penalise
    the manual plan for something the optimizer is also excused.
    """
    p = IntegratedParams(horizon_weeks=3, w_capacity=0.0)
    demand = _arr(3, {1: 10})
    eu = _arr(3, {1: 9})
    res = compute_baseline_cost(_arr(3, {1: 10}), demand, p, eu_demand=eu, row_cap=2)
    assert res.eu_cap_violations == []


def test_terminal_imbalance_reported():
    p = IntegratedParams(horizon_weeks=2, w_capacity=0.0)
    demand = _arr(2, {1: 10, 2: 10})

    short = compute_baseline_cost(_arr(2, {1: 10, 2: 5}), demand, p)
    assert short.terminal_inventory == -5
    assert any("short of demand" in r for r in short.infeasibilities)

    over = compute_baseline_cost(_arr(2, {1: 10, 2: 15}), demand, p)
    assert over.terminal_inventory == 5
    assert any("unsold stock" in r for r in over.infeasibilities)


def test_feasible_plan_reports_no_infeasibilities():
    p = IntegratedParams(horizon_weeks=3, w_capacity=0.0)
    demand = _arr(3, {1: 10, 2: 10, 3: 10})
    res = compute_baseline_cost(
        _arr(3, {1: 10, 2: 10, 3: 10}), demand, p,
        eu_demand=_arr(3, {1: 1, 2: 2, 3: 1}), row_cap=2,
    )
    assert res.is_feasible
    assert res.infeasibilities == []


def test_optimizer_own_plan_is_judged_feasible_by_the_baseline():
    """The baseline's rules must not be stricter than the solver's.

    Feeding the optimizer's own plan back through ``compute_baseline_cost`` must
    report it as feasible and cost it identically. If this fails, the two sides
    are being held to different rules again -- in the opposite direction.
    """
    p = IntegratedParams(horizon_weeks=8)
    demand = _arr(8, {2: 20, 3: 28, 4: 12, 5: 30, 6: 25, 7: 18, 8: 22})
    eu = _arr(8, {3: 4, 6: 3})        # two weeks that force a pre-build

    plan_df, summary = solve_plan_integrated(
        demand, [], [], eu, p.row_cap, p
    )
    y = [0] + plan_df["Good_Production"].tolist()

    res = compute_baseline_cost(y, demand, p, eu_demand=eu, row_cap=p.row_cap)

    assert res.infeasibilities == [], res.infeasibilities
    assert res.is_feasible
    assert res.summary["total_composite_cost"] == pytest.approx(
        summary["total_composite_cost"]
    )


def test_a_feasible_manual_plan_never_beats_the_optimum():
    """The invariant the whole comparison rests on.

    Any plan the baseline calls feasible is a plan the DP was free to choose, so
    the DP's cost must be less than or equal to it. A feasible manual plan showing
    a negative saving would mean the optimizer is not finding the optimum.
    """
    p = IntegratedParams(horizon_weeks=8)
    demand = _arr(8, {2: 20, 3: 28, 4: 12, 5: 30, 6: 25, 7: 18, 8: 22})
    eu = _arr(8, {3: 4, 6: 3})

    _plan_df, summary = solve_plan_integrated(
        demand, [], [], eu, p.row_cap, p
    )

    # A hand-built plan, legal by construction: 2 units held at the end of week 2
    # for the 4 EU units due in week 3, and 1 at the end of week 5 for week 6.
    manual = _arr(8, {2: 22, 3: 26, 4: 12, 5: 31, 6: 25, 7: 17, 8: 22})
    res = compute_baseline_cost(manual, demand, p, eu_demand=eu, row_cap=p.row_cap)

    assert res.is_feasible, res.infeasibilities
    assert summary["total_composite_cost"] <= res.summary["total_composite_cost"]


def test_infeasible_manual_plan_is_what_makes_a_saving_negative():
    """Documents the bug this check exists to catch.

    The illegal just-in-time plan is *cheaper* than the legal pre-built one. That
    is precisely why an unchecked baseline produced a negative saving.
    """
    p = IntegratedParams(horizon_weeks=3, w_capacity=0.0)
    demand = _arr(3, {2: 10})
    eu = _arr(3, {2: 4})

    illegal = compute_baseline_cost(
        _arr(3, {2: 10}), demand, p, eu_demand=eu, row_cap=2)
    legal = compute_baseline_cost(
        _arr(3, {1: 2, 2: 8}), demand, p, eu_demand=eu, row_cap=2)

    assert not illegal.is_feasible
    assert legal.is_feasible
    assert illegal.summary["total_composite_cost"] < legal.summary["total_composite_cost"]
