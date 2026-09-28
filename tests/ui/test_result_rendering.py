"""Exercise the panels that only render once a result exists.

The Streamlit stub makes ``st.button`` return ``False``, so every block guarded by
"if the user pressed Run" is dead in the test suite — and those blocks are where
the result panels live. That is how three undefined names shipped past 906 passing
tests.

Importing a module is not enough either: a missing import inside a function body is
invisible until the function runs. So these tests call the result-rendering helpers
directly, with a summary and result built from a real solve, which is the closest
thing to "the user pressed Run" that does not need a browser.

``tests/ui/test_no_undefined_names.py`` covers undefined names statically across
the whole codebase. This file covers what static analysis cannot: wrong dictionary
keys, wrong attribute names, and type errors in the same paths.
"""

from __future__ import annotations

import pandas as pd
import pytest

from domain.delivery_assignment import build_demand_events
from domain.demand import build_weekly_demand, build_weekly_row_demand
from domain.params import IntegratedParams, SupplierParams
from domain.supplier_solve import solve_with_suppliers
from services.dtos import OptimizationResult
from services.settings_service import build_settings
from ui import tab_comparison, tab_onboarding, tab_optimizer


# ---------------------------------------------------------------------------
# Fixtures: a genuine solved plan, not a hand-written dict
# ---------------------------------------------------------------------------

def _active(sites: int = 30, interval: int = 4) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "site_id": f"S{i:03d}",
            "site_name": f"Site {i}",
            "next_demand_week": (i % interval) + 1,
            "interval_weeks": interval,
            "country": "denmark" if i % 11 == 0 else "usa",
            "is_row": i % 11 == 0,
        }
        for i in range(sites)
    ])


def _solved(params: IntegratedParams):
    active = _active()
    demand = build_weekly_demand(active, params)
    eu_demand = build_weekly_row_demand(active, params)
    events = build_demand_events(active, params)
    plan, summary, quota, assignments = solve_with_suppliers(
        demand, [], [], eu_demand, params.row_cap, params,
        SupplierParams(), None, events,
    )
    return active, plan, summary, quota, assignments


@pytest.fixture(scope="module")
def settings():
    return build_settings(None)


@pytest.fixture(scope="module")
def result(settings):
    active, plan, summary, quota, assignments = _solved(settings.params)
    return OptimizationResult(
        plan_df=plan,
        summary=summary,
        issues_df=pd.DataFrame(columns=["row_index", "site_id", "issue"]),
        active_df=active,
        quota_status=quota,
        assignments=assignments,
        change_summary={"compared": False, "total": len(assignments)},
        week_dates=[],
        xlsx_bytes=None,
        warnings=[],
        settings_fingerprint=settings.fingerprint,
    )


# ---------------------------------------------------------------------------
# The optimizer tab's result panels
# ---------------------------------------------------------------------------

def test_render_metrics_runs(result):
    """Covers the metric row, the capacity split and everything it calls."""
    tab_optimizer._render_metrics(result.summary)


def test_render_utilisation_runs(result):
    tab_optimizer._render_utilisation(result.summary)


def test_render_overtime_explainer_runs(result):
    tab_optimizer._render_overtime_explainer(result.summary)


def test_render_overtime_explainer_runs_when_overtime_is_used():
    """The other branch: a plan that does run overtime.

    Needs a genuinely lumpy profile. The flat fixture above never approaches the
    weekly ceiling, so no cost setting would make it run a 3rd batch.
    """
    params = IntegratedParams(capacity_rate=0.0)
    spike = pd.DataFrame([
        {
            "site_id": f"K{i:03d}",
            "site_name": f"Spike {i}",
            "next_demand_week": 5 if i < 40 else 9,
            "interval_weeks": 0,
            "country": "usa",
            "is_row": False,
        }
        for i in range(80)
    ])
    demand = build_weekly_demand(spike, params)
    eu_demand = build_weekly_row_demand(spike, params)
    _plan, summary, _quota, _assign = solve_with_suppliers(
        demand, [], [], eu_demand, params.row_cap, params, SupplierParams(), None,
        build_demand_events(spike, params),
    )
    assert summary["overtime_weeks"] > 0, "expected this config to run overtime"
    tab_optimizer._render_overtime_explainer(summary)


def test_render_plan_runs(result, settings):
    tab_optimizer._render_plan(result, settings)


def test_render_quota_runs(result):
    tab_optimizer._render_quota(result)


def test_render_changed_weeks_runs(result):
    tab_optimizer._render_changed_weeks(result)


def test_render_stale_banner_runs_both_ways(result, settings):
    tab_optimizer._render_stale_banner(result, settings)

    stale = OptimizationResult(
        plan_df=result.plan_df,
        summary=result.summary,
        issues_df=result.issues_df,
        active_df=result.active_df,
        settings_fingerprint="a-different-fingerprint",
    )
    tab_optimizer._render_stale_banner(stale, settings)


def test_metrics_survive_a_summary_without_the_new_keys(settings):
    """Older stored results must not crash the tab."""
    minimal = {
        "total_composite_cost": 1.0,
        "total_penalty_cost": 1.0,
        "total_overtime_cost": 0.0,
        "total_capacity_cost": 1.0,
        "overtime_weeks": 0,
    }
    tab_optimizer._render_metrics(minimal)
    tab_optimizer._render_utilisation(minimal)
    tab_optimizer._render_overtime_explainer(minimal)


# ---------------------------------------------------------------------------
# The onboarding tab's cost panel
# ---------------------------------------------------------------------------

def _combination(**overrides):
    from domain.onboarding import CombinationResult

    fields = dict(
        selected_weeks={"NEW1": 5},
        feasible=True,
        delta_penalty=0.0,
        delta_overtime=0.0,
        delta_capacity=-105_000.0,
        delta_composite=-105_000.0,
        delta_marginal=0.0,
        generators_added=7,
        overtime_weeks=0,
    )
    fields.update(overrides)
    return CombinationResult(**fields)


@pytest.mark.parametrize("marginal", [0.0, 25_000.0, -25_000.0])
def test_onboarding_cost_panel_runs_for_every_branch(marginal):
    """Zero, positive and negative marginal cost each take a different branch."""
    tab_onboarding._render_selection_metrics(
        _combination(delta_marginal=marginal), fixed_mode=False
    )


def test_onboarding_fixed_mode_panel_runs():
    """Fixed-plan mode reports added_cost rather than the marginal delta."""
    from domain.fixed_plan_onboarding import FixedPlanResult

    result = FixedPlanResult(
        selected_weeks={"NEW1": 5}, feasible=True,
        added_cost=14_000.0, overtime_cost=2_000.0, holding_cost=12_000.0,
        fits=3, absorbed=1, overtime_added=1, shifted=2,
    )
    tab_onboarding._render_selection_metrics(result, fixed_mode=True)


# ---------------------------------------------------------------------------
# The comparison tab's result panels
# ---------------------------------------------------------------------------

def test_comparison_unreconciled_panel_runs():
    from domain.comparison import BaselineResult

    baseline = BaselineResult(
        summary={}, plan_df=None, total_planned=1000, total_demand=1030,
        terminal_inventory=-30,
    )
    fake = type("R", (), {"baseline": baseline})()
    tab_comparison._render_unreconciled_baseline(fake)
