"""Pinned default output — the guard on principle P1.

The correctness remediation (`.kiro/specs/correctness-remediation/`) is mostly a
disclosure exercise: it exposes and labels mechanisms that were already there. The
one thing it must not do is quietly move numbers the planning team has already
reviewed.

These figures were recorded from the pre-remediation code on the deterministic
synthetic fixture. Any change to them is either a bug introduced by the
remediation or a deliberate decision that has to be argued for here in writing
before the constant is edited.

Recorded 2026-09-22 against commit-time behaviour of ``solve_plan_integrated``
with ``IntegratedParams()`` defaults and EU enforcement active.
"""

from __future__ import annotations

import hashlib
import io

import pandas as pd
import pytest

from domain.demand import build_weekly_demand, build_weekly_row_demand, clean_sites
from domain.params import IntegratedParams, SupplierParams
from domain.solver import solve_plan_integrated
from domain.supplier_solve import solve_with_suppliers
from tests.fixtures import sites_csv_text

# --- pre-remediation figures, do not edit without a stated reason -------------

PINNED_TOTAL_DEMAND = 1120
PINNED_EU_DEMAND_TOTAL = 171
PINNED_MAX_WEEKLY_EU = 6          # > row_cap, so EU enforcement is exercised

PINNED_PENALTY = 889_000.0
PINNED_OVERTIME = 0.0
PINNED_CAPACITY = 6_600_000.0
PINNED_COMPOSITE = 7_489_000.0     # the DP objective: penalty + overtime + capacity
PINNED_OVERTIME_WEEKS = 0

PINNED_Y_PLAN_SHA1 = "10faaf66bccea08064bb1a8141bc3f95afb51e3f"
PINNED_Y_PLAN_HEAD = (12, 16, 18, 18, 18, 18, 21, 13, 26, 21, 29, 23)

# solve_with_suppliers used to fold the quota penalty into total_composite_cost.
# After remediation the composite is the DP objective alone and this figure is
# reported as total_cost_with_quota instead. Both are pinned so the split is
# proven to be a re-labelling and not a change of arithmetic.
PINNED_QUOTA_PENALTY = 122_450_000.0
PINNED_COST_WITH_QUOTA = 129_939_000.0


@pytest.fixture(scope="module")
def fixture_demand():
    raw = pd.read_csv(io.StringIO(sites_csv_text()), dtype=str)
    raw.columns = [c.strip().lower() for c in raw.columns]
    params = IntegratedParams()
    active, _issues = clean_sites(raw, params)
    return (
        params,
        build_weekly_demand(active, params),
        build_weekly_row_demand(active, params),
    )


def test_fixture_shape_is_what_the_pins_assume(fixture_demand):
    """If the fixture changes, the pinned costs below are meaningless."""
    _params, demand, eu_demand = fixture_demand
    assert sum(demand) == PINNED_TOTAL_DEMAND
    assert sum(eu_demand) == PINNED_EU_DEMAND_TOTAL
    assert max(eu_demand) == PINNED_MAX_WEEKLY_EU


def test_default_plan_costs_are_unchanged(fixture_demand):
    params, demand, eu_demand = fixture_demand
    _plan, summary = solve_plan_integrated(
        demand, [], [], eu_demand, params.row_cap, params
    )
    assert summary["total_penalty_cost"] == PINNED_PENALTY
    assert summary["total_overtime_cost"] == PINNED_OVERTIME
    assert summary["total_capacity_cost"] == PINNED_CAPACITY
    assert summary["total_composite_cost"] == PINNED_COMPOSITE
    assert summary["overtime_weeks"] == PINNED_OVERTIME_WEEKS


def test_default_production_schedule_is_unchanged(fixture_demand):
    """The plan itself, not just its cost. Catches a re-ordering that ties on cost."""
    params, demand, eu_demand = fixture_demand
    plan, _summary = solve_plan_integrated(
        demand, [], [], eu_demand, params.row_cap, params
    )
    y_plan = tuple(int(v) for v in plan["Good_Production"])
    assert y_plan[:12] == PINNED_Y_PLAN_HEAD
    assert sum(y_plan) == PINNED_TOTAL_DEMAND
    digest = hashlib.sha1(repr(y_plan).encode()).hexdigest()
    assert digest == PINNED_Y_PLAN_SHA1


def test_capacity_identity_holds_on_the_fixture(fixture_demand):
    """capacity == rate*(nominal slots - demand) + rate*overtime units.

    This is the identity R1 exposes. Pinned here so the decomposition added in
    Phase 1 is checked against arithmetic that was true beforehand.
    """
    params, demand, eu_demand = fixture_demand
    plan, summary = solve_plan_integrated(
        demand, [], [], eu_demand, params.row_cap, params
    )
    y_plan = [int(v) for v in plan["Good_Production"]]
    overtime_units = sum(
        max(0, y - params.normal_max_good_week) for y in y_plan
    )
    nominal_slots = params.normal_max_good_week * params.horizon_weeks
    predicted = params.capacity_rate * (nominal_slots - sum(demand)) \
        + params.capacity_rate * overtime_units
    assert predicted == pytest.approx(summary["total_capacity_cost"])


def test_supplier_solve_splits_the_old_composite_without_changing_it(fixture_demand):
    """R2: the quota penalty moves out of the composite but is not lost.

    Pre-remediation ``total_composite_cost`` was 129,939,000 because the quota
    advisory was folded in. After remediation the composite is the DP objective
    (7,489,000) and the old figure is available as ``total_cost_with_quota``.
    """
    params, demand, eu_demand = fixture_demand
    _plan, summary, _quota, _assignments = solve_with_suppliers(
        demand, [], [], eu_demand, params.row_cap, params,
        SupplierParams(), None,
    )
    assert summary["total_quota_penalty_cost"] == PINNED_QUOTA_PENALTY
    assert summary["total_composite_cost"] == PINNED_COMPOSITE
    assert summary["total_cost_with_quota"] == PINNED_COST_WITH_QUOTA
