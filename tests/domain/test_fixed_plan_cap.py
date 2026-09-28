"""R10.6 + R10.7 — fixed-plan onboarding must honour the QC shipping cap.

**R10.7.** The re-planning mode enforces the QC shipping cap by forcing stock to
be pre-built. Fixed-plan mode ignored it entirely, so a new
Denmark/UK/Netherlands/Sweden site could be slotted into a week already at the cap
and reported as a clean fit. The two modes of one feature disagreed about whether
an option was even legal.

**R10.6.** ``overtime_weeks_added`` was maintained by hand alongside
``overtime_added`` and was necessarily equal to it, because a second generator in
an already-charged week comes back as ``OUTCOME_ABSORBED``. Two names for one
number.

See `.kiro/specs/correctness-remediation/requirements.md` R10.6, R10.7.
"""

from __future__ import annotations

import pytest

from domain.fixed_plan_onboarding import (
    OUTCOME_ABSORBED,
    OUTCOME_FITS,
    OUTCOME_OVERTIME,
    evaluate_against_fixed_plan,
)
from domain.onboarding import NewCustomer
from domain.params import IntegratedParams

HORIZON = 12


def _plan(level: int) -> list[int]:
    """A flat existing plan at ``level`` generators a week."""
    return [0] + [level] * HORIZON


def _eu_customer(site_id: str) -> NewCustomer:
    return NewCustomer(
        site_id=site_id, earliest_week=5, latest_week=5,
        interval_weeks=0, country="denmark", eu_restricted=True,
    )


def _us_customer(site_id: str) -> NewCustomer:
    return NewCustomer(
        site_id=site_id, earliest_week=5, latest_week=5,
        interval_weeks=0, country="usa",
    )


# ---------------------------------------------------------------------------
# R10.7 — the cap is checked
# ---------------------------------------------------------------------------

def test_eu_site_slotted_into_a_week_already_at_the_cap_is_reported():
    """AC10.7 — the defect: previously a clean fit, cap silently breached."""
    params = IntegratedParams(horizon_weeks=HORIZON)
    eu_due = [0] * (HORIZON + 1)
    eu_due[5] = params.row_cap          # week 5 is already full

    result = evaluate_against_fixed_plan(
        _plan(10), [_eu_customer("NEW_EU")], {"NEW_EU": 5}, params,
        eu_due=eu_due,
    )
    assert result.cap_breaches == 1
    assert not result.feasible
    assert "QC shipping cap" in result.reason
    # The generator still had room in the factory — it is the shipping limit that
    # blocks it, and the message must say which.
    assert result.placements[0].outcome == OUTCOME_FITS


def test_eu_site_with_cap_headroom_is_accepted():
    params = IntegratedParams(horizon_weeks=HORIZON)
    eu_due = [0] * (HORIZON + 1)
    eu_due[5] = params.row_cap - 1      # one slot free

    result = evaluate_against_fixed_plan(
        _plan(10), [_eu_customer("NEW_EU")], {"NEW_EU": 5}, params,
        eu_due=eu_due,
    )
    assert result.cap_breaches == 0
    assert result.feasible


def test_two_new_eu_sites_cannot_both_claim_the_last_slot():
    """Cap consumption must accumulate across the customers being evaluated."""
    params = IntegratedParams(horizon_weeks=HORIZON)
    eu_due = [0] * (HORIZON + 1)
    eu_due[5] = params.row_cap - 1      # exactly one slot free

    customers = [_eu_customer("EU_A"), _eu_customer("EU_B")]
    result = evaluate_against_fixed_plan(
        _plan(10), customers, {"EU_A": 5, "EU_B": 5}, params, eu_due=eu_due,
    )
    assert result.cap_breaches == 1, "the second site must not reuse the slot"
    assert not result.feasible


def test_non_eu_site_is_unaffected_by_the_cap():
    params = IntegratedParams(horizon_weeks=HORIZON)
    eu_due = [0] * (HORIZON + 1)
    eu_due[5] = params.row_cap          # full for EU, irrelevant for a US site

    result = evaluate_against_fixed_plan(
        _plan(10), [_us_customer("NEW_US")], {"NEW_US": 5}, params,
        eu_due=eu_due,
    )
    assert result.cap_breaches == 0
    assert result.feasible


def test_omitting_eu_due_leaves_the_check_off():
    """Backwards compatible: callers without EU data behave as before."""
    params = IntegratedParams(horizon_weeks=HORIZON)
    result = evaluate_against_fixed_plan(
        _plan(10), [_eu_customer("NEW_EU")], {"NEW_EU": 5}, params,
    )
    assert result.cap_breaches == 0
    assert result.feasible


def test_cap_breach_detail_names_the_week_and_the_limit():
    params = IntegratedParams(horizon_weeks=HORIZON)
    eu_due = [0] * (HORIZON + 1)
    eu_due[5] = params.row_cap

    result = evaluate_against_fixed_plan(
        _plan(10), [_eu_customer("NEW_EU")], {"NEW_EU": 5}, params,
        eu_due=eu_due,
    )
    detail = result.placements[0].detail
    assert "Week 5" in detail
    assert str(params.row_cap) in detail


# ---------------------------------------------------------------------------
# R10.6 — the counter is derived
# ---------------------------------------------------------------------------

def test_overtime_weeks_added_counts_distinct_weeks():
    params = IntegratedParams(horizon_weeks=HORIZON)
    normal_cap = params.normal_max_good_week

    # Week 5 already full at the normal ceiling; three new generators land there.
    plan = [0] * (HORIZON + 2)
    for week in range(1, HORIZON + 1):
        plan[week] = 10
    plan[5] = normal_cap

    customers = [
        NewCustomer(site_id=f"N{i}", earliest_week=5, latest_week=5,
                    interval_weeks=0, country="usa")
        for i in range(3)
    ]
    result = evaluate_against_fixed_plan(
        plan, customers, {c.site_id: 5 for c in customers}, params,
    )

    charged = [p for p in result.placements if p.outcome == OUTCOME_OVERTIME]
    absorbed = [p for p in result.placements if p.outcome == OUTCOME_ABSORBED]

    # One week newly pushed onto overtime, charged once, however many units land.
    assert result.overtime_weeks_added == 1
    assert len(charged) == 1
    assert len(absorbed) == 2
    assert result.overtime_cost == pytest.approx(params.overtime_rate)


def test_overtime_weeks_added_is_a_derived_property():
    """It must not be settable state that can drift from the placements."""
    import dataclasses

    from domain.fixed_plan_onboarding import FixedPlanResult

    field_names = {f.name for f in dataclasses.fields(FixedPlanResult)}
    assert "overtime_weeks_added" not in field_names
    assert isinstance(
        getattr(type(FixedPlanResult()), "overtime_weeks_added"), property
    )


def test_counts_still_reconcile_to_the_generator_total():
    params = IntegratedParams(horizon_weeks=HORIZON)
    customers = [
        NewCustomer(site_id=f"N{i}", earliest_week=5, latest_week=5,
                    interval_weeks=0, country="usa")
        for i in range(4)
    ]
    result = evaluate_against_fixed_plan(
        _plan(10), customers, {c.site_id: 5 for c in customers}, params,
    )
    tallied = (
        result.fits + result.absorbed + result.overtime_added
        + result.shifted + result.infeasible
    )
    assert tallied == result.generators
