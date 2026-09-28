"""R3 — an advisory cost must be labelled as one, and its cause surfaced.

Supplier allocation runs *after* the solve, so a quota shortfall cannot influence
any decision. It was nonetheless added to the figure reported as the optimum.

Worse, the run-sequencing rule gives Curium the whole week whenever weekly output
is at or below ``first_run_allocation`` (15), because a single-run week has no
second run for BWXT to serve. On a plan averaging 10 generators a week, BWXT
received **zero** orders in all 52 weeks: four full-quarter shortfalls, $2bn of
penalty, none of it reachable by any plan the solver could pick. The number
appeared with no explanation.

See `.kiro/specs/correctness-remediation/requirements.md` R3.
"""

from __future__ import annotations

import pandas as pd
import pytest

from domain.demand import build_weekly_demand, build_weekly_row_demand
from domain.params import IntegratedParams, SupplierParams
from domain.quota import STATUS_PARTIAL, compute_quarter_boundaries
from domain.supplier_allocation import (
    allocate_suppliers_weekly,
    zero_allocation_warnings,
)
from domain.supplier_solve import QUOTA_ADVISORY_LABEL, solve_with_suppliers


def _low_volume_scenario(sites: int = 40, interval: int = 4):
    """~10 generators a week: every week is a single Curium run."""
    params = IntegratedParams()
    active = pd.DataFrame([
        {"site_id": f"S{i:03d}", "next_demand_week": (i % interval) + 1,
         "interval_weeks": interval, "country": "usa", "is_row": False}
        for i in range(sites)
    ])
    return (
        params,
        SupplierParams(),
        build_weekly_demand(active, params),
        build_weekly_row_demand(active, params),
    )


@pytest.fixture(scope="module")
def low_volume():
    params, supplier_params, demand, eu_demand = _low_volume_scenario()
    plan, summary, quota, _records = solve_with_suppliers(
        demand, [], [], eu_demand, params.row_cap, params, supplier_params, None,
    )
    return params, supplier_params, plan, summary, quota


# ---------------------------------------------------------------------------
# R3.1 — labelled as advisory, and kept out of the optimized objective
# ---------------------------------------------------------------------------

def test_quota_is_flagged_as_advisory(low_volume):
    _params, _sp, _plan, summary, _quota = low_volume
    assert summary["quota_is_advisory"] is True
    assert summary["quota_advisory_note"] == QUOTA_ADVISORY_LABEL
    assert "after" in QUOTA_ADVISORY_LABEL.lower()


def test_advisory_is_not_inside_the_optimized_objective(low_volume):
    _params, sp, _plan, summary, _quota = low_volume
    assert summary["total_quota_penalty_cost"] > 0
    assert summary["total_cost_with_quota"] == pytest.approx(
        summary["total_composite_cost"]
        + sp.w_quota * summary["total_quota_penalty_cost"]
    )
    assert summary["total_composite_cost"] < summary["total_cost_with_quota"]


# ---------------------------------------------------------------------------
# R3.2 / AC3.1 — the structural cause is named
# ---------------------------------------------------------------------------

def test_starved_supplier_produces_a_structural_warning(low_volume):
    _params, _sp, plan, summary, _quota = low_volume
    assert plan["BWXT_Good"].sum() == 0, "precondition: BWXT gets nothing"

    warnings = summary["quota_structural_warnings"]
    assert warnings, "a fully starved supplier must be explained"

    joined = " ".join(warnings)
    assert "BWXT" in joined
    assert "first_run_allocation" in joined
    assert "after the solve" in joined
    # It must name the quarters rather than just asserting a problem exists.
    assert any(f"Q{q}" in joined for q in (1, 2, 3, 4))


def test_no_structural_warning_when_both_suppliers_are_served():
    """High enough volume splits every week, so neither supplier is starved."""
    params = IntegratedParams()
    supplier_params = SupplierParams()
    active = pd.DataFrame([
        {"site_id": f"S{i:03d}", "next_demand_week": (i % 2) + 1,
         "interval_weeks": 2, "country": "usa", "is_row": False}
        for i in range(60)
    ])
    demand = build_weekly_demand(active, params)
    eu_demand = build_weekly_row_demand(active, params)

    plan, summary, _quota, _records = solve_with_suppliers(
        demand, [], [], eu_demand, params.row_cap, params, supplier_params, None,
    )
    assert plan["BWXT_Good"].sum() > 0, "precondition: weeks must be split"
    assert summary["quota_structural_warnings"] == []


def test_zero_allocation_warning_is_pure_and_quarter_scoped():
    params = IntegratedParams(horizon_weeks=8)
    supplier_params = SupplierParams()
    y_plan = [0] + [10] * 8          # every week a single Curium run
    eu_demand = [0] * 9

    allocations = allocate_suppliers_weekly(
        y_plan, eu_demand, params, supplier_params
    )
    quarters = [(1, tuple(range(1, 5))), (2, tuple(range(5, 9)))]
    warnings = zero_allocation_warnings(allocations, supplier_params, quarters)

    assert len(warnings) == 1
    assert "BWXT" in warnings[0]
    assert "Q1" in warnings[0] and "Q2" in warnings[0]


def test_partial_quarters_are_excluded_from_the_diagnosis():
    """A fragment of a quarter proves nothing about ordering behaviour."""
    params = IntegratedParams(horizon_weeks=8)
    supplier_params = SupplierParams()
    allocations = allocate_suppliers_weekly(
        [0] + [10] * 8, [0] * 9, params, supplier_params
    )
    # No quarters supplied at all, mirroring a horizon that is entirely partial.
    assert zero_allocation_warnings(allocations, supplier_params, []) == []


# ---------------------------------------------------------------------------
# R3.3 — partial-quarter handling is untouched
# ---------------------------------------------------------------------------

def test_partial_quarters_still_reported_and_never_penalised():
    from datetime import date

    params = IntegratedParams()
    supplier_params = SupplierParams()
    active = pd.DataFrame([
        {"site_id": f"S{i:03d}", "next_demand_week": (i % 4) + 1,
         "interval_weeks": 4, "country": "usa", "is_row": False}
        for i in range(40)
    ])
    demand = build_weekly_demand(active, params)
    eu_demand = build_weekly_row_demand(active, params)

    # A reference week mid-quarter guarantees partial quarters at both ends.
    _plan, summary, quota, _records = solve_with_suppliers(
        demand, [], [], eu_demand, params.row_cap, params, supplier_params,
        date(2026, 7, 27),
    )
    partial = [q for q in quota if q.is_partial]
    assert partial, "this reference week should produce partial quarters"
    assert all(q.penalty_usd == 0.0 for q in partial)
    assert all(q.status == STATUS_PARTIAL for q in partial)
    assert summary["partial_quarters"]
    assert summary["partial_quarter_note"]
