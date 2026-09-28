"""The capacity split must hold when demand EXCEEDS standing capacity.

Reported from the running app: "Idle slot-weeks 152, Fixed by demand **-20**,
Caused by overtime 172". A negative count of idle slots is not a thing that can
exist.

The cause was a correct piece of arithmetic wearing the wrong label. The old code
computed ``unavoidable_idle = total_idle - overtime_units``, which is algebraically
``nominal_slots - produced``. That is "unavoidable idle" only while demand sits
*below* standing capacity. Above it the value goes negative and means something
else entirely — the minimum overtime that demand forces. Every test written for
this feature used demand well below capacity, so the whole regime was unexercised.

Both regimes are now named, and neither figure can be negative.

See `.kiro/specs/correctness-remediation/requirements.md` R1.6.
"""

from __future__ import annotations

import pandas as pd
import pytest
from hypothesis import given, strategies as st

from domain.cost_model import split_capacity
from domain.demand import build_weekly_demand, build_weekly_row_demand
from domain.params import IntegratedParams, SupplierParams
from domain.solver import solve_plan_integrated
from domain.supplier_solve import solve_with_suppliers


# ---------------------------------------------------------------------------
# The pure split
# ---------------------------------------------------------------------------

def test_demand_below_capacity_gives_unavoidable_idle():
    split = split_capacity(
        nominal_slots=1560, produced=1120, overtime_units=0, total_idle_slots=440
    )
    assert split.unavoidable_idle_slots == 440
    assert split.forced_overtime_units == 0
    assert split.discretionary_overtime_units == 0
    assert not split.demand_exceeds_capacity


def test_demand_above_capacity_gives_forced_overtime_not_negative_idle():
    """The reported bug, as a unit test."""
    split = split_capacity(
        nominal_slots=1560, produced=1580, overtime_units=172, total_idle_slots=152
    )
    assert split.unavoidable_idle_slots == 0, "idle can never be negative"
    assert split.forced_overtime_units == 20
    assert split.discretionary_overtime_units == 152
    assert split.demand_exceeds_capacity


def test_split_reconciles_to_the_idle_total():
    """unavoidable idle + discretionary overtime == total idle, in both regimes."""
    for nominal, produced, overtime, idle in (
        (1560, 1120, 0, 440),
        (1560, 1580, 172, 152),
        (1560, 1560, 0, 0),
        (1560, 1580, 20, 0),
    ):
        split = split_capacity(nominal, produced, overtime, idle)
        assert (
            split.unavoidable_idle_slots + split.discretionary_overtime_units
            == split.total_idle_slots
        ), (nominal, produced, overtime, idle)


def test_exactly_one_regime_is_active():
    for produced in (1000, 1560, 2000):
        split = split_capacity(1560, produced, overtime_units=100, total_idle_slots=0)
        assert not (
            split.unavoidable_idle_slots and split.forced_overtime_units
        ), "demand cannot be both under and over capacity"


@given(
    nominal=st.integers(min_value=0, max_value=3000),
    produced=st.integers(min_value=0, max_value=3000),
    overtime=st.integers(min_value=0, max_value=1500),
    idle=st.integers(min_value=0, max_value=3000),
)
def test_no_component_is_ever_negative(nominal, produced, overtime, idle):
    split = split_capacity(nominal, produced, overtime, idle)
    assert split.unavoidable_idle_slots >= 0
    assert split.forced_overtime_units >= 0
    assert split.discretionary_overtime_units >= 0


def test_costs_sum_to_the_published_capacity_total():
    params = IntegratedParams(capacity_rate=1.0)
    split = split_capacity(1560, 1580, overtime_units=172, total_idle_slots=152)
    floor, surcharge = split.costs(params)
    assert floor >= 0 and surcharge >= 0
    assert floor + surcharge == pytest.approx(
        params.capacity_rate * split.total_idle_slots
    )


# ---------------------------------------------------------------------------
# End to end, on a plan whose demand really does exceed standing capacity
# ---------------------------------------------------------------------------

def _over_capacity_scenario(total: int = 1580):
    """One-off deliveries spread across the year, summing above 52 x 30."""
    params = IntegratedParams(capacity_rate=1.0)
    active = pd.DataFrame([
        {"site_id": f"S{i:04d}", "next_demand_week": (i % params.horizon_weeks) + 1,
         "interval_weeks": 0, "country": "usa", "is_row": False}
        for i in range(total)
    ])
    return (
        params,
        build_weekly_demand(active, params),
        build_weekly_row_demand(active, params),
    )


def test_solved_plan_reports_no_negative_utilisation():
    params, demand, eu_demand = _over_capacity_scenario()
    assert sum(demand) > params.normal_max_good_week * params.horizon_weeks

    _plan, summary = solve_plan_integrated(
        demand, [], [], eu_demand, params.row_cap, params
    )
    for key in (
        "total_idle_slots", "unavoidable_idle_slots",
        "forced_overtime_units", "discretionary_overtime_units", "overtime_units",
    ):
        assert summary[key] >= 0, f"{key} = {summary[key]}"


def test_solved_plan_names_the_forced_overtime():
    params, demand, eu_demand = _over_capacity_scenario()
    nominal = params.normal_max_good_week * params.horizon_weeks
    _plan, summary = solve_plan_integrated(
        demand, [], [], eu_demand, params.row_cap, params
    )
    assert summary["demand_exceeds_capacity"] is True
    assert summary["forced_overtime_units"] == sum(demand) - nominal
    assert summary["unavoidable_idle_slots"] == 0


def test_capacity_costs_are_non_negative_and_reconcile():
    params, demand, eu_demand = _over_capacity_scenario()
    _plan, summary = solve_plan_integrated(
        demand, [], [], eu_demand, params.row_cap, params
    )
    floor = summary["total_capacity_floor_cost"]
    surcharge = summary["total_capacity_levelling_cost"]
    assert floor >= 0, f"unavoidable idle cost was {floor}"
    assert surcharge >= 0
    assert floor + surcharge == pytest.approx(summary["total_capacity_cost"])


def test_shutdown_weeks_reduce_standing_capacity():
    """A shut week offers no slots, so it cannot contribute idle ones either."""
    params = IntegratedParams(horizon_weeks=10)
    demand = [0] + [20] * 10
    _plan, summary = solve_plan_integrated(
        demand, [3], [], [0] * 11, params.row_cap, params
    )
    # 9 open weeks x 30 slots, not 10.
    assert summary["nominal_slots"] == 9 * params.normal_max_good_week


def test_partial_week_counts_one_batch_of_standing_capacity():
    params = IntegratedParams(horizon_weeks=10)
    demand = [0] + [12] * 10
    _plan, summary = solve_plan_integrated(
        demand, [], [4], [0] * 11, params.row_cap, params
    )
    expected = 9 * params.normal_max_good_week + params.max_good_per_batch
    assert summary["nominal_slots"] == expected


def test_supplier_solve_passes_the_split_through():
    params, demand, eu_demand = _over_capacity_scenario()
    _plan, summary, _quota, _assign = solve_with_suppliers(
        demand, [], [], eu_demand, params.row_cap, params, SupplierParams(), None
    )
    assert summary["unavoidable_idle_slots"] >= 0
    assert summary["forced_overtime_units"] > 0
