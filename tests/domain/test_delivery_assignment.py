"""Unit tests for delivery assignment (domain/delivery_assignment.py)."""

import pandas as pd
import pytest

from domain.delivery_assignment import (
    DemandEvent,
    assign_deliveries,
    build_demand_events,
    compare_against_manual_plan,
    summarize_changes,
    to_dataframe,
)
from domain.params import IntegratedParams


def _y(T, spec):
    y = [0] * (T + 1)
    for wk, q in spec.items():
        y[wk] = q
    return y


# ---------------------------------------------------------------------------
# build_demand_events
# ---------------------------------------------------------------------------

def test_build_demand_events_recurring():
    p = IntegratedParams(horizon_weeks=20)
    df = pd.DataFrame({
        "site_id": ["A"],
        "next_demand_week": [3],
        "interval_weeks": [7],
        "country": ["usa"],
    })
    events = build_demand_events(df, p)
    assert [e.scheduled_week for e in events] == [3, 10, 17]
    assert all(e.site_id == "A" for e in events)


def test_build_demand_events_sorted_deterministic():
    p = IntegratedParams(horizon_weeks=10)
    df = pd.DataFrame({
        "site_id": ["B", "A"],
        "next_demand_week": [5, 5],
        "interval_weeks": [20, 20],
        "country": ["usa", "usa"],
    })
    events = build_demand_events(df, p)
    # same week -> sorted by site_id
    assert [e.site_id for e in events] == ["A", "B"]


def test_build_demand_events_matches_weekly_demand_total():
    from domain.demand import build_weekly_demand
    p = IntegratedParams(horizon_weeks=52)
    df = pd.DataFrame({
        "site_id": ["A", "B", "C"],
        "next_demand_week": [1, 4, 9],
        "interval_weeks": [7, 8, 6],
        "country": ["usa"] * 3,
    })
    events = build_demand_events(df, p)
    assert len(events) == sum(build_weekly_demand(df, p))


# ---------------------------------------------------------------------------
# assign_deliveries
# ---------------------------------------------------------------------------

def test_produced_in_due_week_has_zero_due_shift():
    p = IntegratedParams(horizon_weeks=5)
    events = [DemandEvent(2, "A"), DemandEvent(4, "B")]
    y = _y(5, {2: 1, 4: 1})
    recs = assign_deliveries(y, events, p)
    assert [r.due_week_shift for r in recs] == [0, 0]
    # No manual plan applied yet, so nothing is classified as changed.
    assert all(r.week_shift is None and not r.compared for r in recs)


def test_early_production_recorded_against_due_week():
    # Due week 4, but only produced in week 2 -> due_week_shift = -2
    p = IntegratedParams(horizon_weeks=5)
    events = [DemandEvent(4, "A")]
    y = _y(5, {2: 1})
    recs = assign_deliveries(y, events, p)
    assert recs[0].planned_week == 2
    assert recs[0].due_week_shift == -2
    # Early/late are only meaningful against the manual plan.
    assert not recs[0].is_early and not recs[0].is_late


def test_backlog_recorded_against_due_week():
    # Due week 2, produced only in week 5 -> due_week_shift = +3
    p = IntegratedParams(horizon_weeks=5)
    events = [DemandEvent(2, "A")]
    y = _y(5, {5: 1})
    recs = assign_deliveries(y, events, p)
    assert recs[0].planned_week == 5
    assert recs[0].due_week_shift == 3


def test_prefers_latest_available_on_or_before_due():
    # Supply in weeks 1 and 3; demand due week 3 -> should take week 3, not 1.
    p = IntegratedParams(horizon_weeks=5)
    events = [DemandEvent(3, "A")]
    y = _y(5, {1: 1, 3: 1})
    recs = assign_deliveries(y, events, p)
    assert recs[0].planned_week == 3
    assert recs[0].due_week_shift == 0


def test_per_week_counts_match_y_plan():
    p = IntegratedParams(horizon_weeks=6)
    events = [DemandEvent(3, "A"), DemandEvent(3, "B"), DemandEvent(6, "C")]
    y = _y(6, {2: 1, 3: 1, 6: 1})
    recs = assign_deliveries(y, events, p)
    counts = {}
    for r in recs:
        counts[r.planned_week] = counts.get(r.planned_week, 0) + 1
    assert counts == {2: 1, 3: 1, 6: 1}


def test_total_assignments_equals_demand():
    p = IntegratedParams(horizon_weeks=6)
    events = [DemandEvent(2, "A"), DemandEvent(4, "B"), DemandEvent(6, "C")]
    y = _y(6, {2: 1, 4: 1, 6: 1})
    recs = assign_deliveries(y, events, p)
    assert len(recs) == len(events)


def test_determinism_repeated_runs_identical():
    p = IntegratedParams(horizon_weeks=8)
    events = [DemandEvent(4, "B"), DemandEvent(4, "A"), DemandEvent(7, "C")]
    y = _y(8, {2: 1, 4: 1, 7: 1})
    a = assign_deliveries(y, events, p)
    b = assign_deliveries(y, list(reversed(events)), p)  # input order shuffled
    assert [(r.site_id, r.planned_week) for r in a] == [(r.site_id, r.planned_week) for r in b]


def test_no_supply_raises():
    p = IntegratedParams(horizon_weeks=3)
    with pytest.raises(ValueError):
        assign_deliveries(_y(3, {}), [DemandEvent(2, "A")], p)


# ---------------------------------------------------------------------------
# Master Planner comparison
# ---------------------------------------------------------------------------

def test_manual_plan_comparison_drives_week_shift():
    p = IntegratedParams(horizon_weeks=6)
    events = [DemandEvent(3, "A")]
    recs = assign_deliveries(_y(6, {3: 1}), events, p)
    schedule = {"A": [0, 0, 1, 0, 0, 0, 0]}  # manual plan produced it in week 2
    out = compare_against_manual_plan(recs, schedule)
    assert out[0].manual_week == 2
    assert out[0].week_shift == 1          # optimizer wk3 vs manual wk2
    assert out[0].is_late and not out[0].is_early
    assert out[0].compared is True
    assert out[0].is_new_customer is False


def test_manual_plan_earlier_marks_moved_earlier():
    p = IntegratedParams(horizon_weeks=6)
    recs = assign_deliveries(_y(6, {2: 1}), [DemandEvent(2, "A")], p)
    out = compare_against_manual_plan(recs, {"A": [0, 0, 0, 0, 1, 0, 0]})
    assert out[0].manual_week == 4
    assert out[0].week_shift == -2
    assert out[0].is_early and not out[0].is_late


def test_same_week_as_manual_is_unchanged():
    p = IntegratedParams(horizon_weeks=6)
    recs = assign_deliveries(_y(6, {3: 1}), [DemandEvent(3, "A")], p)
    out = compare_against_manual_plan(recs, {"A": [0, 0, 0, 1, 0, 0, 0]})
    assert out[0].week_shift == 0
    assert not out[0].is_early and not out[0].is_late


def test_new_customer_flagged_when_absent_from_manual_plan():
    p = IntegratedParams(horizon_weeks=6)
    recs = assign_deliveries(_y(6, {3: 1}), [DemandEvent(3, "NEW1")], p)
    out = compare_against_manual_plan(recs, {"A": [0, 1, 0, 0, 0, 0, 0]})
    assert out[0].is_new_customer is True
    assert out[0].manual_week is None
    assert out[0].week_shift is None


def test_extra_generator_beyond_manual_plan_has_no_counterpart():
    """The optimizer may schedule more for a site than the manual plan did."""
    p = IntegratedParams(horizon_weeks=10)
    events = [DemandEvent(2, "A"), DemandEvent(6, "A")]
    recs = assign_deliveries(_y(10, {2: 1, 6: 1}), events, p)
    out = compare_against_manual_plan(recs, {"A": [0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0]})
    assert out[0].manual_week == 2
    assert out[1].manual_week is None      # no second manual week
    assert out[1].week_shift is None


def test_summarize_reports_not_compared_without_manual_plan():
    p = IntegratedParams(horizon_weeks=8)
    recs = assign_deliveries(_y(8, {2: 1}), [DemandEvent(2, "A")], p)
    s = summarize_changes(recs)
    assert s["compared"] is False
    assert s["total"] == 1
    assert s["early"] == s["late"] == s["unchanged"] == 0


def test_summarize_counts_against_manual_plan():
    p = IntegratedParams(horizon_weeks=10)
    events = [DemandEvent(3, "A"), DemandEvent(5, "B"), DemandEvent(7, "C")]
    recs = assign_deliveries(_y(10, {3: 1, 5: 1, 7: 1}), events, p)
    # Index i in the marks list is week i (index 0 unused).
    schedule = {
        "A": [0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0],   # manual wk4, optimized wk3 -> earlier
        "B": [0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0],   # manual wk5, optimized wk5 -> same
        # C absent -> new customer
    }
    s = summarize_changes(compare_against_manual_plan(recs, schedule))
    assert s["compared"] is True
    assert s["total"] == 3
    assert s["early"] == 1
    assert s["unchanged"] == 1
    assert s["new_customers"] == 1


def test_to_dataframe_columns():
    p = IntegratedParams(horizon_weeks=4)
    recs = assign_deliveries(_y(4, {2: 1}), [DemandEvent(2, "A", "Acme", "usa")], p)
    df = to_dataframe(recs)
    for col in ["Site_ID", "Site_Name", "Country", "Manual_Plan_Week",
                "Optimized_Week", "Week_Shift", "Due_Week", "Is_New_Customer"]:
        assert col in df.columns
