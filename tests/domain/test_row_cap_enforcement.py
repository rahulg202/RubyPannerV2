"""Tests for row_cap enforcement via eu_demand in the DP solver (Phase 5).

Verifies:
- eu_demand=None reproduces the original bounds and plan exactly (regression).
- Supplying eu_demand raises the inventory floor ahead of high-EU-demand weeks.
- The enforced plan actually carries enough inventory into those weeks.
"""

import pandas as pd

from domain.params import IntegratedParams
from domain.solver import compute_inventory_bounds, solve_plan_integrated


def _demand(T, spec):
    d = [0] * (T + 1)
    for wk, q in spec.items():
        d[wk] = q
    return d


def test_bounds_unchanged_when_eu_demand_none():
    p = IntegratedParams(horizon_weeks=10)
    demand = _demand(10, {3: 5, 7: 8})
    cap_max = [0] + [45] * 10
    base_lb, base_ub = compute_inventory_bounds(demand, cap_max, p)
    same_lb, same_ub = compute_inventory_bounds(demand, cap_max, p, None, None)
    assert base_lb == same_lb
    assert base_ub == same_ub


def test_bounds_raise_floor_before_high_eu_week():
    p = IntegratedParams(horizon_weeks=10)
    demand = _demand(10, {5: 4})
    cap_max = [0] + [45] * 10
    eu_demand = _demand(10, {5: 4})  # 4 EU units due in week 5
    row_cap = 2
    lb, ub = compute_inventory_bounds(demand, cap_max, p, eu_demand, row_cap)
    # excess = 4 - 2 = 2 must be in inventory at end of week 4
    assert lb[4] >= 2


def test_no_floor_when_eu_within_cap():
    p = IntegratedParams(horizon_weeks=10)
    demand = _demand(10, {5: 2})
    cap_max = [0] + [45] * 10
    eu_demand = _demand(10, {5: 2})   # equals cap, no excess
    base_lb, _ = compute_inventory_bounds(demand, cap_max, p)
    lb, _ = compute_inventory_bounds(demand, cap_max, p, eu_demand, row_cap=2)
    assert lb == base_lb


def test_solver_identical_with_eu_demand_none_on_full_dataset(sites_csv):
    p = IntegratedParams()
    from io_adapters.sites_reader import read_sites
    from domain.demand import clean_sites, build_weekly_demand, build_weekly_row_demand
    raw = read_sites(sites_csv)
    active, _ = clean_sites(raw, p)
    demand = build_weekly_demand(active, p)
    row_demand = build_weekly_row_demand(active, p)

    plan_a, sum_a = solve_plan_integrated(demand, [], [], row_demand, p.row_cap, p)
    plan_b, sum_b = solve_plan_integrated(demand, [], [], row_demand, p.row_cap, p, eu_demand=None)
    assert sum_a == sum_b
    pd.testing.assert_frame_equal(plan_a, plan_b)


def test_enforced_plan_carries_inventory_into_high_eu_week():
    # Craft a feasible scenario: EU demand of 4 in week 6 (cap 2) must be
    # pre-built. Total demand small enough to remain feasible.
    p = IntegratedParams(horizon_weeks=8, w_capacity=0.0)
    demand = _demand(8, {6: 4})
    row_demand = _demand(8, {6: 4})
    eu_demand = _demand(8, {6: 4})

    plan, summary = solve_plan_integrated(
        demand, [], [], row_demand, row_cap=2, params=p, eu_demand=eu_demand
    )
    # Net inventory at end of week 5 must be >= excess (2)
    inv_wk5 = int(plan.loc[plan["Week"] == 5, "Net_Inventory_End"].iloc[0])
    assert inv_wk5 >= 2


def test_enforced_plan_still_meets_terminal_zero():
    p = IntegratedParams(horizon_weeks=8, w_capacity=0.0)
    demand = _demand(8, {6: 4})
    row_demand = _demand(8, {6: 4})
    eu_demand = _demand(8, {6: 4})
    plan, _ = solve_plan_integrated(
        demand, [], [], row_demand, row_cap=2, params=p, eu_demand=eu_demand
    )
    assert int(plan.loc[plan["Week"] == 8, "Net_Inventory_End"].iloc[0]) == 0
