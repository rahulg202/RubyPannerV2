"""Both onboarding modes must report costs in one vocabulary.

The two modes answer different questions — re-plan everything, versus price a site
into a plan held still — so their column sets differ. But they measure the same two
cost components, and they used to name them differently: re-planning mode showed
"Δ Marginal / Δ Penalty / Δ Overtime" while fixed-plan mode showed only a single
"Added cost" with no breakdown at all. A planner switching modes had to work out
that "Added cost" and "Δ Marginal" were the same quantity.

Now both use `Added cost`, `Δ Penalty`, `Δ Overtime` and `Deliveries`.

Fixed-plan mode genuinely cannot report `Δ Capacity` or `Δ Composite`: both are
whole-plan figures and in that mode the plan does not move, so there is nothing to
compare against. That difference is deliberate and asserted here, so nobody
"completes" the alignment by inventing those two.
"""

from __future__ import annotations

import pandas as pd
import pytest

from domain.fixed_plan_onboarding import evaluate_against_fixed_plan
from domain.onboarding import CombinationResult, NewCustomer
from domain.params import IntegratedParams
from ui.formatting import rankings_frame

SHARED_COST_COLUMNS = ("Added cost", "Δ Penalty", "Δ Overtime", "Deliveries")

# Whole-plan columns that only re-planning mode can have.
REPLAN_ONLY_COLUMNS = ("Δ Capacity", "Δ Composite")


# ---------------------------------------------------------------------------
# Re-planning mode
# ---------------------------------------------------------------------------

def _combination(week: int = 5) -> CombinationResult:
    return CombinationResult(
        selected_weeks={"NEW1": week},
        feasible=True,
        delta_penalty=-4_000.0,
        delta_overtime=2_000.0,
        delta_capacity=-105_000.0,
        delta_composite=-107_000.0,
        delta_marginal=-2_000.0,
        generators_added=7,
        overtime_weeks=1,
    )


def test_replanning_table_uses_the_shared_names():
    frame = rankings_frame([_combination()], ["NEW1"])
    for column in SHARED_COST_COLUMNS:
        assert column in frame.columns, column


def test_replanning_table_keeps_its_whole_plan_columns():
    frame = rankings_frame([_combination()], ["NEW1"])
    for column in REPLAN_ONLY_COLUMNS:
        assert column in frame.columns, column


def test_replanning_added_cost_shows_the_marginal_figure():
    """Added cost must be penalty + overtime, not the whole-plan total."""
    frame = rankings_frame([_combination()], ["NEW1"])
    assert frame.loc[0, "Added cost"] == "-$2,000"
    assert frame.loc[0, "Δ Composite"] == "-$107,000"


def test_week_column_is_named_consistently():
    frame = rankings_frame([_combination()], ["NEW1"])
    assert "Week for NEW1" in frame.columns


def test_old_column_names_are_gone():
    frame = rankings_frame([_combination()], ["NEW1"])
    for retired in ("Δ Marginal", "Deliveries in horizon", "NEW1 week"):
        assert retired not in frame.columns, retired


# ---------------------------------------------------------------------------
# Fixed-plan mode
# ---------------------------------------------------------------------------

def _fixed_result(start: int = 9):
    """A scenario that incurs both holding and overtime, so neither is zero."""
    params = IntegratedParams(horizon_weeks=52)
    plan = [0] + [
        45 if week == 9 else 30 if week in (17, 25) else 28
        for week in range(1, 53)
    ]
    customer = NewCustomer(
        site_id="NEW1", earliest_week=start, latest_week=start, interval_weeks=8,
    )
    return params, evaluate_against_fixed_plan(
        plan, [customer], {"NEW1": start}, params
    )


def test_fixed_mode_exposes_both_cost_components():
    _params, result = _fixed_result()
    assert result.holding_cost > 0, "scenario should force a build-early"
    assert result.overtime_cost > 0, "scenario should force a third batch"


def test_fixed_mode_components_sum_to_added_cost():
    """The invariant behind showing them as a split. Cannot be allowed to drift."""
    _params, result = _fixed_result()
    assert result.holding_cost + result.overtime_cost == pytest.approx(
        result.added_cost
    )


def test_fixed_mode_outcome_counts_still_sum_to_deliveries():
    """Renaming Generators -> Deliveries must not break the reconciliation."""
    _params, result = _fixed_result()
    tallied = (
        result.fits + result.absorbed + result.overtime_added
        + result.shifted + result.infeasible
    )
    assert tallied == result.generators


def test_fixed_mode_has_no_capacity_delta():
    """Deliberate asymmetry: the plan does not move, so utilisation cannot change.

    Guards against someone "finishing" the alignment by inventing this one.
    """
    _params, result = _fixed_result()
    assert not hasattr(result, "delta_capacity")


def test_fixed_mode_delta_composite_is_only_an_alias_for_added_cost():
    """It exists so the shared selection screen can stay mode-agnostic.

    It is *not* a whole-plan delta, despite the name it borrows from
    ``CombinationResult`` — in this mode there is no re-optimized plan to compare
    against. Pinned so nobody starts reading it as one.
    """
    _params, result = _fixed_result()
    assert result.delta_composite == pytest.approx(result.added_cost)


def test_fixed_mode_table_omits_the_whole_plan_columns():
    """The UI-level claim: those two columns must not appear in this mode."""
    import inspect

    from ui import tab_onboarding

    source = inspect.getsource(tab_onboarding._render_fixed_rankings)
    for absent in REPLAN_ONLY_COLUMNS:
        assert f'"{absent}"' not in source, absent
    # ...while the shared cost names are all present.
    for shared in SHARED_COST_COLUMNS:
        assert f'"{shared}"' in source, shared


def test_both_modes_agree_on_the_delivery_count():
    """Same customer, same horizon: the two code paths must count alike."""
    params = IntegratedParams(horizon_weeks=52)
    start, interval = 5, 8
    customer = NewCustomer(
        site_id="NEW1", earliest_week=start, latest_week=start,
        interval_weeks=interval,
    )

    _p, fixed = _fixed_result(start=start)
    from domain.onboarding import generators_added

    assert fixed.generators == generators_added(
        [customer], {"NEW1": start}, params
    )
