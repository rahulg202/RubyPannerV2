"""R1 — the capacity component must disclose its two parts.

Capacity cost is ``rate * max(0, ceiling - y)`` per week. Because the
zero-terminal-inventory rule pins total production to total demand, that sum
decomposes exactly into a constant floor plus a term proportional to overtime
units:

    capacity = rate*(sum(ceiling) - demand)  +  rate*sum(max(0, y - ceiling))
               |--------- floor ---------|     |------ levelling ------|

The floor is arithmetic — identical for every feasible plan. The levelling part
is the only thing a plan can change, which makes it an *undeclared overtime
surcharge of ``capacity_rate`` per unit*, 7.5x the declared ``overtime_rate``.

These tests pin the identity and prove the surcharge is controllable through
``capacity_rate`` alone: at its default the overtime rate is inert, and at zero the
surcharge disappears and the overtime rate decides.

See `.kiro/specs/correctness-remediation/requirements.md` R1.
"""

from __future__ import annotations

import pytest
from hypothesis import given, strategies as st

from domain.cost_model import decompose_weekly_cost
from domain.params import IntegratedParams
from domain.solver import solve_plan_integrated


def _demand(horizon: int, spec: dict[int, int]) -> list[int]:
    demand = [0] * (horizon + 1)
    for week, quantity in spec.items():
        demand[week] = quantity
    return demand


def _solve(demand, params, eu_demand=None):
    horizon = params.horizon_weeks
    return solve_plan_integrated(
        demand, [], [], eu_demand or [0] * (horizon + 1), params.row_cap, params
    )


# ---------------------------------------------------------------------------
# AC1.1 — the parts sum to the published whole
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("week_type", ["Normal", "Partial", "Shutdown"])
@pytest.mark.parametrize("good_prod", [0, 1, 14, 15, 16, 29, 30, 31, 44, 45])
def test_floor_plus_levelling_equals_capacity(week_type, good_prod):
    params = IntegratedParams()
    if week_type == "Shutdown":
        good_prod = 0
    if week_type == "Partial" and good_prod > params.max_good_per_batch:
        pytest.skip("not reachable in a partial week")

    cost = decompose_weekly_cost(0, good_prod, week_type, params)
    assert cost.capacity_floor + cost.capacity_levelling == pytest.approx(
        cost.capacity
    )


def test_composite_still_equals_the_weighted_sum():
    params = IntegratedParams(w_penalty=1.0, w_overtime=0.5, w_capacity=0.25)
    cost = decompose_weekly_cost(7, 33, "Normal", params)
    expected = (
        params.w_penalty * cost.penalty
        + params.w_overtime * cost.overtime
        + params.w_capacity * cost.capacity
    )
    assert cost.composite == pytest.approx(expected)


# ---------------------------------------------------------------------------
# AC1.2 / AC1.3 — the floor is invariant, the levelling part is the surcharge
# ---------------------------------------------------------------------------

def test_capacity_floor_is_identical_across_plans_of_equal_demand():
    """Two very different production profiles, same demand total, same floor."""
    params = IntegratedParams(horizon_weeks=52)
    flat = _demand(52, {t: 20 for t in range(1, 53)})
    spiky = _demand(52, {t: 40 for t in range(1, 53, 2)})
    assert sum(flat) == sum(spiky)

    _plan_a, summary_a = _solve(flat, params)
    _plan_b, summary_b = _solve(spiky, params)

    assert summary_a["total_capacity_floor_cost"] == pytest.approx(
        summary_b["total_capacity_floor_cost"]
    )
    # ...while the published capacity totals differ, purely by the levelling part.
    assert summary_a["total_capacity_cost"] != summary_b["total_capacity_cost"]


def test_levelling_cost_equals_rate_times_overtime_units():
    """AC1.3 — names the surcharge exactly."""
    params = IntegratedParams(horizon_weeks=52)
    spiky = _demand(52, {t: 40 for t in range(1, 53, 2)})
    plan, summary = _solve(spiky, params)

    overtime_units = sum(
        max(0, int(y) - params.normal_max_good_week)
        for y in plan["Good_Production"]
    )
    assert summary["total_capacity_levelling_cost"] == pytest.approx(
        params.capacity_rate * overtime_units
    )


def test_totals_reconcile_on_a_solved_plan():
    params = IntegratedParams(horizon_weeks=52)
    spiky = _demand(52, {t: 40 for t in range(1, 53, 2)})
    _plan, summary = _solve(spiky, params)
    assert (
        summary["total_capacity_floor_cost"]
        + summary["total_capacity_levelling_cost"]
    ) == pytest.approx(summary["total_capacity_cost"])


@given(
    level=st.integers(min_value=5, max_value=25),
    horizon=st.integers(min_value=8, max_value=16),
)
def test_decomposition_identity_is_a_property(level, horizon):
    params = IntegratedParams(horizon_weeks=horizon)
    demand = _demand(horizon, {t: level for t in range(1, horizon + 1)})
    _plan, summary = _solve(demand, params)
    assert (
        summary["total_capacity_floor_cost"]
        + summary["total_capacity_levelling_cost"]
    ) == pytest.approx(summary["total_capacity_cost"])


# ---------------------------------------------------------------------------
# AC1.4 / AC1.7 — capacity_rate is the only dial, and there is no second one
# ---------------------------------------------------------------------------

def test_zero_capacity_rate_removes_the_surcharge():
    params = IntegratedParams(horizon_weeks=52, capacity_rate=0.0)
    spiky = _demand(52, {t: 40 for t in range(1, 53, 2)})
    _plan, summary = _solve(spiky, params)
    assert summary["total_capacity_levelling_cost"] == pytest.approx(0.0)
    assert summary["total_capacity_cost"] == pytest.approx(0.0)


def test_surcharge_scales_with_the_rate():
    """Halve the rate, halve the surcharge. One continuous dial, not a mode."""
    spiky = _demand(52, {t: 40 for t in range(1, 53, 2)})
    seen = {}
    for rate in (0.0, 1.0, 100.0):
        params = IntegratedParams(horizon_weeks=52, capacity_rate=rate)
        plan, summary = _solve(spiky, params)
        units = sum(
            max(0, int(y) - params.normal_max_good_week)
            for y in plan["Good_Production"]
        )
        seen[rate] = (summary["total_capacity_levelling_cost"], units)

    # Same plan at these low rates, so the surcharge is purely rate x units.
    assert seen[0.0][1] == seen[1.0][1] == seen[100.0][1]
    units = seen[1.0][1]
    assert units > 0, "this demand profile should require overtime"
    assert seen[1.0][0] == pytest.approx(1.0 * units)
    assert seen[100.0][0] == pytest.approx(100.0 * units)


def test_overtime_rate_becomes_the_lever_once_the_surcharge_is_gone():
    """The defect, and its remedy, in one assertion.

    At the default capacity rate the $15,000-per-unit surcharge dominates, so
    sweeping ``overtime_rate`` from $0 to $20,000 changes nothing. Drop the
    capacity rate to zero and the declared rate decides.
    """
    spiky = _demand(52, {t: 40 for t in range(1, 53, 2)})

    def overtime_weeks(capacity_rate: float, overtime_rate: float) -> int:
        params = IntegratedParams(
            horizon_weeks=52,
            capacity_rate=capacity_rate,
            overtime_rate=overtime_rate,
        )
        _plan, summary = _solve(spiky, params)
        return summary["overtime_weeks"]

    # Inert at the default capacity rate.
    assert overtime_weeks(15_000.0, 0.0) == overtime_weeks(15_000.0, 20_000.0)

    # Responsive once the surcharge is removed.
    cheap = overtime_weeks(0.0, 0.0)
    dear = overtime_weeks(0.0, 200_000.0)
    assert cheap > dear


def test_there_is_no_capacity_baseline_setting():
    """AC1.7 — the removed mode must not come back.

    It produced an identical plan to lowering the rate while inflating the
    reported capacity cost by a constant no plan could influence, which forced a
    UI warning telling planners to ignore the total.
    """
    import dataclasses

    names = {f.name for f in dataclasses.fields(IntegratedParams)}
    assert "capacity_baseline" not in names
    with pytest.raises(TypeError):
        IntegratedParams(capacity_baseline="available")


# ---------------------------------------------------------------------------
# AC1.6 — utilisation is a count, immune to pricing
# ---------------------------------------------------------------------------

def test_idle_slot_count_is_unaffected_by_cost_rates():
    """"How full was the factory" must not move when a price is edited."""
    flat = _demand(52, {t: 20 for t in range(1, 53)})
    counts = set()
    for rate in (0.0, 1.0, 15_000.0, 100_000.0):
        params = IntegratedParams(horizon_weeks=52, capacity_rate=rate)
        _plan, summary = _solve(flat, params)
        counts.add((summary["total_idle_slots"], summary["overtime_units"]))
    assert len(counts) == 1, f"idle count moved with the price: {counts}"


def test_idle_slots_split_into_fixed_and_overtime_caused():
    params = IntegratedParams(horizon_weeks=52)
    flat = _demand(52, {t: 20 for t in range(1, 53)})
    _plan, summary = _solve(flat, params)

    nominal = params.normal_max_good_week * params.horizon_weeks
    assert summary["unavoidable_idle_slots"] == nominal - sum(flat)
    assert summary["total_idle_slots"] == (
        summary["unavoidable_idle_slots"] + summary["overtime_units"]
    )


# ---------------------------------------------------------------------------
# R1.5 — the effective deterrent is discoverable
# ---------------------------------------------------------------------------

def test_effective_overtime_deterrent_names_the_real_cost():
    params = IntegratedParams()
    # A full overtime week adds 15 units above the normal ceiling.
    assert params.effective_overtime_deterrent(15) == pytest.approx(
        params.overtime_rate + 15 * params.capacity_rate
    )


def test_effective_overtime_deterrent_with_no_surcharge():
    params = IntegratedParams(capacity_rate=0.0)
    assert params.effective_overtime_deterrent(15) == pytest.approx(
        params.overtime_rate
    )
