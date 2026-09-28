"""R2 — a plan compared against itself must save exactly nothing.

This is the test that finds asymmetry in the cost comparison, and it is how the
original defect was caught: ``solve_with_suppliers`` folded the quota penalty into
``total_composite_cost`` while ``compute_baseline_cost`` never computed a quota
term at all, so comparing the optimizer's own output against itself reported a
two-billion-dollar loss:

    Component              Baseline       Optimized          Saving
    Penalty                      $0              $0              $0
    Overtime                     $0              $0              $0
    Capacity Utilization $15,600,000    $15,600,000              $0
    Total Composite      $15,600,000 $2,015,600,000 $-2,000,000,000

Every component reconciled; the total did not. Any future component added to one
side and not the other fails here.

See `.kiro/specs/correctness-remediation/requirements.md` R2.
"""

from __future__ import annotations

import io

import pandas as pd
import pytest

from domain.comparison import COST_COMPONENTS, compare_plans, compute_baseline_cost
from domain.delivery_assignment import build_demand_events
from domain.demand import build_weekly_demand, build_weekly_row_demand, clean_sites
from domain.params import IntegratedParams, SupplierParams
from domain.supplier_solve import solve_with_suppliers
from tests.fixtures import sites_csv_text

TOTAL_ROW = "Total"


def _scenario(interval: int = 4, sites: int = 40):
    """A demand profile whose plan leaves BWXT unserved, so quota fires."""
    params = IntegratedParams()
    supplier_params = SupplierParams()
    active = pd.DataFrame([
        {
            "site_id": f"S{i:03d}",
            "next_demand_week": (i % interval) + 1,
            "interval_weeks": interval,
            "country": "usa",
            "is_row": False,
        }
        for i in range(sites)
    ])
    demand = build_weekly_demand(active, params)
    eu_demand = build_weekly_row_demand(active, params)
    return params, supplier_params, active, demand, eu_demand


@pytest.fixture(scope="module")
def realistic_scenario():
    """The synthetic fixture, which does exercise EU enforcement."""
    raw = pd.read_csv(io.StringIO(sites_csv_text()), dtype=str)
    raw.columns = [c.strip().lower() for c in raw.columns]
    params = IntegratedParams()
    active, _issues = clean_sites(raw, params)
    return (
        params,
        SupplierParams(),
        active,
        build_weekly_demand(active, params),
        build_weekly_row_demand(active, params),
    )


def _self_compare(params, supplier_params, active, demand, eu_demand):
    """Solve, then evaluate the resulting plan as if it were the manual plan.

    Both sides receive the same demand events and supplier params, which is the
    symmetry the comparison depends on.
    """
    events = build_demand_events(active, params)
    plan_df, optimized, _quota, _assignments = solve_with_suppliers(
        demand, [], [], eu_demand, params.row_cap, params,
        supplier_params, None, events,
    )
    planned = [0] + [int(v) for v in plan_df["Good_Production"]]
    baseline = compute_baseline_cost(
        planned,
        demand,
        params,
        (),
        (),
        eu_demand,
        params.row_cap,
        supplier_params=supplier_params,
        demand_events=events,
    )
    return baseline, optimized, compare_plans(baseline.summary, optimized)


def test_every_component_saving_is_zero():
    params, supplier_params, active, demand, eu_demand = _scenario()
    _baseline, _optimized, components = _self_compare(
        params, supplier_params, active, demand, eu_demand
    )
    for row in components:
        assert row["Saving_Abs"] == pytest.approx(0.0), (
            f"{row['Component']} saved {row['Saving_Abs']:,.2f} comparing a plan "
            f"against itself"
        )


def test_the_total_row_saving_is_zero():
    """The specific figure that was $-2,000,000,000."""
    params, supplier_params, active, demand, eu_demand = _scenario()
    _baseline, _optimized, components = _self_compare(
        params, supplier_params, active, demand, eu_demand
    )
    total = next(r for r in components if r["Component"].startswith(TOTAL_ROW))
    assert total["Saving_Abs"] == pytest.approx(0.0)
    assert total["Baseline"] == pytest.approx(total["Optimized"])


def test_quota_is_computed_on_both_sides():
    """The baseline must carry the same advisory the optimized side carries."""
    params, supplier_params, active, demand, eu_demand = _scenario()
    baseline, optimized, _components = _self_compare(
        params, supplier_params, active, demand, eu_demand
    )
    assert "total_quota_penalty_cost" in baseline.summary
    assert baseline.summary["total_quota_penalty_cost"] == pytest.approx(
        optimized["total_quota_penalty_cost"]
    )
    # ...and it is non-zero in this scenario, so the assertion above has teeth.
    assert optimized["total_quota_penalty_cost"] > 0


def test_components_sum_to_the_total_on_both_sides():
    """P3 — anything shown to a planner must add up."""
    params, supplier_params, active, demand, eu_demand = _scenario()
    _baseline, _optimized, components = _self_compare(
        params, supplier_params, active, demand, eu_demand
    )
    parts = [r for r in components if not r["Component"].startswith(TOTAL_ROW)]
    total = next(r for r in components if r["Component"].startswith(TOTAL_ROW))
    for side in ("Baseline", "Optimized"):
        assert sum(r[side] for r in parts) == pytest.approx(total[side]), (
            f"{side} components do not sum to the {side} total"
        )


def test_self_comparison_is_zero_on_the_realistic_fixture(realistic_scenario):
    params, supplier_params, active, demand, eu_demand = realistic_scenario
    _baseline, _optimized, components = _self_compare(
        params, supplier_params, active, demand, eu_demand
    )
    for row in components:
        assert row["Saving_Abs"] == pytest.approx(0.0), row["Component"]


def test_composite_no_longer_includes_the_quota_advisory():
    """AC2.3 — the DP objective means the DP objective."""
    from domain.solver import solve_plan_integrated

    params, supplier_params, _active, demand, eu_demand = _scenario()
    _plan_a, plain = solve_plan_integrated(
        demand, [], [], eu_demand, params.row_cap, params
    )
    _plan_b, with_suppliers, _quota, _assignments = solve_with_suppliers(
        demand, [], [], eu_demand, params.row_cap, params,
        supplier_params, None,
    )
    assert with_suppliers["total_composite_cost"] == pytest.approx(
        plain["total_composite_cost"]
    )
    assert with_suppliers["total_cost_with_quota"] == pytest.approx(
        plain["total_composite_cost"]
        + supplier_params.w_quota * with_suppliers["total_quota_penalty_cost"]
    )


def test_quota_component_is_declared_in_the_component_list():
    keys = [key for _label, key in COST_COMPONENTS]
    assert "total_quota_penalty_cost" in keys
