"""R8 + R9 + R10.3 — the EU-restricted rules, checked where they actually bind.

Three defects lived here.

**R8.** Every call site passed the same array to both ``row_demand`` and
``eu_demand``. The duplicate fed a cost-free FIFO queue that published
``ROW_Fulfilled`` / ``ROW_Inventory`` in the plan sheet, constrained nothing, and
could show an unfulfilled backlog the enforced constraint had already ruled out —
two contradictory accounts of one rule in one sheet.

**R9.** ``eu_constraint_satisfied`` compared Curium's allocation in production
week ``t`` against EU demand *due* in week ``t``. But the QC shipping cap
deliberately forces those units to be built earlier, so the two figures described
different populations of generators.

**R10.3.** An EU-induced infeasibility surfaced as "check shutdown weeks and
demand — total capacity may be insufficient", pointing at the wrong input.

See `.kiro/specs/correctness-remediation/requirements.md` R8, R9, R10.3.
"""

from __future__ import annotations

import pandas as pd
import pytest

from domain.delivery_assignment import (
    assign_deliveries,
    build_demand_events,
    eu_restricted_production,
)
from domain.errors import ShippingCapInfeasibleError
from domain.params import IntegratedParams, SupplierParams
from domain.solver import compute_inventory_bounds, solve_plan_integrated
from domain.supplier_allocation import allocate_suppliers_weekly
from domain.supplier_solve import solve_with_suppliers


def _arr(horizon: int, spec: dict[int, int]) -> list[int]:
    out = [0] * (horizon + 1)
    for week, qty in spec.items():
        out[week] = qty
    return out


# ---------------------------------------------------------------------------
# R8 — one array, and columns describing the enforced rule
# ---------------------------------------------------------------------------

def test_plan_has_no_decorative_row_columns():
    """AC8.2 — the parallel FIFO queue and its columns are gone."""
    params = IntegratedParams(horizon_weeks=8)
    demand = _arr(8, {3: 10, 6: 12})
    plan, _summary = solve_plan_integrated(
        demand, [], [], _arr(8, {}), params.row_cap, params
    )
    for column in ("ROW_Demand_Due", "ROW_Fulfilled", "ROW_Inventory"):
        assert column not in plan.columns


def test_plan_publishes_the_enforced_prebuild_requirement():
    """AC8.3 — the published figure is the rule the solver actually applied."""
    params = IntegratedParams(horizon_weeks=8, w_capacity=0.0)
    demand = _arr(8, {3: 5, 6: 4})
    eu_demand = _arr(8, {6: 4})          # 4 due, cap 2 -> 2 must be pre-built

    plan, _summary = solve_plan_integrated(
        demand, [], [], eu_demand, params.row_cap, params
    )
    rows = plan.set_index("Week")

    for week in range(1, 9):
        expected = max(0, eu_demand[week] - params.row_cap)
        assert int(rows.at[week, "EU_Prebuild_Required"]) == expected, week
        assert int(rows.at[week, "EU_Shipping_Cap"]) == params.row_cap
        assert int(rows.at[week, "EU_Restricted_Due"]) == eu_demand[week]

    # And the plan genuinely carries that stock. Week 1 is exempt: there is no
    # earlier week to build in, which is why the solver does not enforce it there.
    for week in range(2, 9):
        required = int(rows.at[week, "EU_Prebuild_Required"])
        if required:
            assert int(rows.at[week - 1, "Net_Inventory_End"]) >= required, week


def test_prebuild_available_reports_previous_week_inventory():
    params = IntegratedParams(horizon_weeks=8, w_capacity=0.0)
    demand = _arr(8, {3: 5, 6: 4})
    eu_demand = _arr(8, {6: 4})
    plan, _summary = solve_plan_integrated(
        demand, [], [], eu_demand, params.row_cap, params
    )
    rows = plan.set_index("Week")
    for week in range(2, 9):
        assert int(rows.at[week, "EU_Prebuild_Available"]) == max(
            0, int(rows.at[week - 1, "Net_Inventory_End"])
        )
    assert int(rows.at[1, "EU_Prebuild_Available"]) == 0


def test_solver_takes_a_single_eu_argument():
    """AC8.1 — the duplicated parameter cannot be passed any more."""
    import inspect

    names = list(inspect.signature(solve_plan_integrated).parameters)
    assert "row_demand" not in names
    assert names.count("eu_demand") == 1


# ---------------------------------------------------------------------------
# R9 — the material rule binds in the production week
# ---------------------------------------------------------------------------

def _eu_scenario():
    """4 EU-restricted sites due the same week, so 2 units must be pre-built."""
    params = IntegratedParams(horizon_weeks=8, w_capacity=0.0)
    active = pd.DataFrame(
        [
            {"site_id": f"EU{i}", "next_demand_week": 6, "interval_weeks": 0,
             "country": "denmark", "is_row": True}
            for i in range(4)
        ]
        + [
            {"site_id": f"US{i}", "next_demand_week": 3, "interval_weeks": 0,
             "country": "usa", "is_row": False}
            for i in range(5)
        ]
    )
    demand = _arr(8, {3: 5, 6: 4})
    eu_demand = _arr(8, {6: 4})
    return params, active, demand, eu_demand


def test_demand_events_carry_the_eu_flag():
    params, active, _demand, _eu = _eu_scenario()
    events = build_demand_events(active, params)
    eu_events = [e for e in events if e.eu_restricted]
    assert len(eu_events) == 4
    assert all(e.scheduled_week == 6 for e in eu_events)
    assert all(not e.eu_restricted for e in events if e.site_id.startswith("US"))


def test_eu_production_is_attributed_to_the_week_units_are_made():
    """AC9.1 — pre-built EU units count against their production week."""
    params, active, demand, eu_demand = _eu_scenario()
    plan, _summary = solve_plan_integrated(
        demand, [], [], eu_demand, params.row_cap, params
    )
    y_plan = [0] + plan["Good_Production"].tolist()
    events = build_demand_events(active, params)
    records = assign_deliveries(y_plan, events, params)
    produced = eu_restricted_production(records, params.horizon_weeks)

    # All four EU units are still accounted for...
    assert sum(produced) == 4
    # ...but not all in week 6, because the shipping cap forced early building.
    assert produced[6] < 4, (
        "the cap should have pushed some EU production before the due week"
    )


def test_allocation_records_both_due_and_produced():
    """R9.3 — the distinction is visible rather than conflated."""
    params, active, demand, eu_demand = _eu_scenario()
    plan, _summary, _quota, records = solve_with_suppliers(
        demand, [], [], eu_demand, params.row_cap, params,
        SupplierParams(), None, build_demand_events(active, params),
    )
    rows = plan.set_index("Week")
    assert int(rows.at[6, "EU_Restricted_Demand"]) == 4
    assert rows["EU_Restricted_Produced"].sum() == 4
    # The two columns disagree somewhere, which is the whole point.
    assert list(rows["EU_Restricted_Demand"]) != list(rows["EU_Restricted_Produced"])


def test_allocation_falls_back_to_due_weeks_without_an_assignment():
    """AC9.2 — behaviour is unchanged for callers that have no assignment."""
    params = IntegratedParams(horizon_weeks=4)
    y_plan = [0, 10, 10, 10, 10]
    eu_demand = _arr(4, {2: 3})

    without = allocate_suppliers_weekly(
        y_plan, eu_demand, params, SupplierParams()
    )
    explicit = allocate_suppliers_weekly(
        y_plan, eu_demand, params, SupplierParams(), eu_production=eu_demand
    )
    assert [a.curium_good for a in without] == [a.curium_good for a in explicit]
    assert [a.eu_constraint_satisfied for a in without] == [
        a.eu_constraint_satisfied for a in explicit
    ]


# ---------------------------------------------------------------------------
# R10.3 — a shipping-cap infeasibility names the shipping cap
# ---------------------------------------------------------------------------

def test_shipping_cap_infeasibility_is_typed_and_specific():
    params = IntegratedParams(horizon_weeks=3)
    demand = _arr(3, {3: 5})
    cap_max = [0] + [45] * 3
    # Week 2 needs 10 EU units shipped against a cap of 2, so 8 must already be in
    # stock — more than the whole remaining horizon could ever hold.
    eu_demand = _arr(3, {2: 10})

    with pytest.raises(ShippingCapInfeasibleError) as excinfo:
        compute_inventory_bounds(demand, cap_max, params, eu_demand, row_cap=2)

    error = excinfo.value
    assert error.week == 2
    assert error.cap == 2
    assert error.required == 8
    message = str(error)
    assert "week 2" in message.lower()
    assert "shipping cap" in message.lower()
    # The old message blamed shutdown weeks and capacity, which is the wrong input.
    assert "shutdown" not in message.lower()


def test_feasible_eu_excess_does_not_raise():
    params = IntegratedParams(horizon_weeks=8)
    demand = _arr(8, {3: 5, 6: 4})
    cap_max = [0] + [45] * 8
    eu_demand = _arr(8, {6: 4})
    lb, _ub = compute_inventory_bounds(demand, cap_max, params, eu_demand, row_cap=2)
    assert lb[5] >= 2
