"""Tests for onboarding into a plan held fixed (domain.fixed_plan_onboarding)."""

from __future__ import annotations

import pytest

from domain.errors import ValidationError
from domain.fixed_plan_onboarding import (
    OUTCOME_ABSORBED,
    OUTCOME_FITS,
    OUTCOME_INFEASIBLE,
    OUTCOME_OVERTIME,
    OUTCOME_SHIFTED,
    delivery_weeks,
    evaluate_against_fixed_plan,
    recommend_against_fixed_plan,
)
from domain.onboarding import NewCustomer
from domain.params import IntegratedParams

# 12-week horizon keeps the fixtures readable. Normal ceiling 30, overtime 45.
P = IntegratedParams(horizon_weeks=12)


def _plan(**weeks: int) -> list[int]:
    """1-indexed weekly sellable production; unnamed weeks are empty."""
    plan = [0] * (P.horizon_weeks + 1)
    for name, value in weeks.items():
        plan[int(name.lstrip("w"))] = value
    return plan


def _customer(site_id="NEW-1", interval=0, earliest=1, latest=1) -> NewCustomer:
    return NewCustomer(
        site_id=site_id, earliest_week=earliest, latest_week=latest,
        interval_weeks=interval,
    )


# ---------------------------------------------------------------------------
# Delivery weeks
# ---------------------------------------------------------------------------

def test_recurring_delivery_weeks():
    assert delivery_weeks(_customer(interval=7), 1, 20) == [1, 8, 15]


def test_zero_interval_is_a_single_delivery():
    assert delivery_weeks(_customer(interval=0), 5, 20) == [5]


def test_start_outside_the_horizon_yields_nothing():
    assert delivery_weeks(_customer(interval=0), 99, 20) == []


# ---------------------------------------------------------------------------
# Placing one generator
# ---------------------------------------------------------------------------

def test_spare_slot_costs_nothing():
    result = evaluate_against_fixed_plan(_plan(w3=20), [_customer(earliest=3, latest=3)],
                                       {"NEW-1": 3}, P)
    assert result.feasible
    assert result.added_cost == 0
    assert result.fits == 1
    assert result.placements[0].outcome == OUTCOME_FITS
    assert result.fits_cleanly


def test_full_week_triggers_overtime():
    result = evaluate_against_fixed_plan(_plan(w3=30), [_customer(earliest=3, latest=3)],
                                       {"NEW-1": 3}, P)
    assert result.overtime_added == 1
    assert result.added_cost == P.overtime_rate
    assert result.placements[0].outcome == OUTCOME_OVERTIME
    assert not result.fits_cleanly


def test_week_already_on_overtime_absorbs_at_no_extra_charge():
    """A third batch is already being run, so there is nothing more to charge."""
    result = evaluate_against_fixed_plan(_plan(w3=35), [_customer(earliest=3, latest=3)],
                                       {"NEW-1": 3}, P)
    assert result.absorbed == 1
    assert result.added_cost == 0
    assert result.placements[0].outcome == OUTCOME_ABSORBED
    assert result.fits_cleanly


def test_shifting_earlier_is_chosen_when_cheaper_than_overtime():
    cheap_holding = IntegratedParams(horizon_weeks=12, penalty_rate=500.0,
                                    overtime_rate=9_000.0)
    # Week 3 is full at the overtime ceiling is not needed: overtime costs 9,000,
    # holding one week costs 500, so the shift wins.
    result = evaluate_against_fixed_plan(
        _plan(w2=10, w3=30), [_customer(earliest=3, latest=3)],
        {"NEW-1": 3}, cheap_holding,
    )
    assert result.shifted == 1
    assert result.added_cost == 500.0
    placement = result.placements[0]
    assert placement.outcome == OUTCOME_SHIFTED
    assert placement.produced_week == 2


def test_overtime_is_chosen_when_cheaper_than_holding():
    result = evaluate_against_fixed_plan(
        _plan(w2=10, w3=30), [_customer(earliest=3, latest=3)], {"NEW-1": 3}, P,
    )
    # Overtime 2,000 beats holding 7,000.
    assert result.overtime_added == 1
    assert result.added_cost == P.overtime_rate


def test_shift_used_when_the_week_is_at_the_overtime_ceiling():
    result = evaluate_against_fixed_plan(
        _plan(w2=10, w3=45), [_customer(earliest=3, latest=3)], {"NEW-1": 3}, P,
    )
    assert result.shifted == 1
    assert result.placements[0].produced_week == 2
    assert result.added_cost == P.penalty_rate


def test_infeasible_when_nothing_nearby_has_room():
    plan = _plan(w1=45, w2=45, w3=45, w4=45, w5=45)
    result = evaluate_against_fixed_plan(
        plan, [_customer(earliest=5, latest=5)], {"NEW-1": 5}, P,
    )
    assert not result.feasible
    assert result.infeasible == 1
    assert result.placements[0].outcome == OUTCOME_INFEASIBLE
    assert "could not be placed" in result.reason


def test_shift_respects_the_maximum_look_back():
    # Only week 1 has room; weeks 2-4 are at the overtime ceiling, as is week 5.
    plan = _plan(w1=10, w2=45, w3=45, w4=45, w5=45)

    limited = evaluate_against_fixed_plan(
        plan, [_customer(earliest=5, latest=5)], {"NEW-1": 5}, P, max_shift_weeks=2,
    )
    assert not limited.feasible         # week 1 is 4 weeks back, beyond the limit

    generous = evaluate_against_fixed_plan(
        plan, [_customer(earliest=5, latest=5)], {"NEW-1": 5}, P, max_shift_weeks=4,
    )
    assert generous.feasible
    assert generous.placements[0].produced_week == 1
    assert generous.added_cost == P.penalty_rate * 4      # held four weeks


# ---------------------------------------------------------------------------
# Capacity is consumed, so customers cannot share one slot
# ---------------------------------------------------------------------------

def test_two_customers_cannot_claim_the_same_single_spare_slot():
    customers = [
        _customer("A", earliest=3, latest=3),
        _customer("B", earliest=3, latest=3),
    ]
    result = evaluate_against_fixed_plan(
        _plan(w3=29), customers, {"A": 3, "B": 3}, P,
    )
    # One fits into the last spare slot; the other has to be paid for.
    assert result.fits == 1
    assert result.added_cost > 0
    assert result.generators == 2


def test_outcome_counts_always_reconcile():
    customers = [_customer("A", interval=3, earliest=1, latest=1)]
    result = evaluate_against_fixed_plan(
        _plan(w1=30, w4=35, w7=10, w10=45), customers, {"A": 1}, P,
    )
    total = (result.fits + result.absorbed + result.overtime_added
             + result.shifted + result.infeasible)
    assert total == result.generators


def test_evaluation_is_deterministic():
    customers = [_customer("A", interval=4, earliest=2, latest=2),
                 _customer("B", interval=5, earliest=3, latest=3)]
    plan = _plan(w2=28, w3=30, w6=35, w7=20, w8=45, w10=29)
    first = evaluate_against_fixed_plan(plan, customers, {"A": 2, "B": 3}, P)
    second = evaluate_against_fixed_plan(plan, customers, {"A": 2, "B": 3}, P)
    assert first.added_cost == second.added_cost
    assert [p.outcome for p in first.placements] == [p.outcome for p in second.placements]


def test_delta_composite_mirrors_added_cost():
    """The shared selection screen reads delta_composite for both modes."""
    result = evaluate_against_fixed_plan(
        _plan(w3=30), [_customer(earliest=3, latest=3)], {"NEW-1": 3}, P,
    )
    assert result.delta_composite == result.added_cost >= 0


# ---------------------------------------------------------------------------
# Search across the window
# ---------------------------------------------------------------------------

def test_recommendation_prefers_a_week_that_fits_free():
    # Week 2 is full, week 4 has room.
    plan = _plan(w2=45, w3=45, w4=5)
    out = recommend_against_fixed_plan(
        plan, [_customer(earliest=2, latest=4)], P,
    )
    best = out["rankings"][0]
    assert best.selected_weeks["NEW-1"] == 4
    assert best.added_cost == 0
    assert out["search_space"] == 3
    assert not out["used_heuristic"]


def test_recommendation_reports_infeasible_options():
    plan = [45] * (P.horizon_weeks + 1)
    out = recommend_against_fixed_plan(
        plan, [_customer(earliest=2, latest=4)], P,
    )
    assert out["rankings"] == []
    assert out["infeasible_count"] == 3
    assert out["infeasible_reasons"]


def test_rankings_are_ordered_cheapest_first():
    plan = _plan(w2=45, w3=30, w4=5)
    out = recommend_against_fixed_plan(
        plan, [_customer(earliest=2, latest=4)], P,
    )
    costs = [r.added_cost for r in out["rankings"]]
    assert costs == sorted(costs)


def test_recommendation_rejects_an_empty_customer_list():
    with pytest.raises(ValidationError):
        recommend_against_fixed_plan(_plan(), [], P)


def test_recommendation_validates_customer_rows():
    bad = NewCustomer(site_id="X", earliest_week=9, latest_week=3, interval_weeks=7)
    with pytest.raises(ValidationError):
        recommend_against_fixed_plan(_plan(), [bad], P)


def test_multiple_customers_are_searched_together():
    plan = _plan(w1=5, w2=5, w3=5)
    out = recommend_against_fixed_plan(
        plan,
        [_customer("A", earliest=1, latest=3), _customer("B", earliest=1, latest=3)],
        P,
    )
    assert out["search_space"] == 9
    best = out["rankings"][0]
    assert set(best.selected_weeks) == {"A", "B"}
    assert best.added_cost == 0


def test_one_time_delivery_customers_can_be_onboarded():
    """Regression: interval 0 was rejected by validation.

    The demand builders have always treated 0 as a single delivery with no
    repeat, but the onboarding validator required >= 1 — so a one-time-delivery
    customer could not be evaluated at all.
    """
    out = recommend_against_fixed_plan(
        _plan(w2=5), [_customer(interval=0, earliest=2, latest=2)], P,
    )
    best = out["rankings"][0]
    assert best.generators == 1
    assert best.added_cost == 0


def test_negative_interval_is_still_rejected():
    bad = NewCustomer(site_id="X", earliest_week=1, latest_week=1, interval_weeks=-1)
    with pytest.raises(ValidationError):
        recommend_against_fixed_plan(_plan(), [bad], P)
