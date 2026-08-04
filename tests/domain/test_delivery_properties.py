"""Property tests for delivery assignment (Phase 7).

Maps to design properties 1-4 in
.kiro/specs/optimizer-enhancements/design.md (Testing Strategy).
"""

from hypothesis import given, settings
from hypothesis import strategies as st

from domain.delivery_assignment import DemandEvent, assign_deliveries
from domain.params import IntegratedParams

T = 12
PARAMS = IntegratedParams(horizon_weeks=T)


@st.composite
def _plan_and_events(draw):
    """Generate a y_plan and a demand list whose totals match (feasible)."""
    n = draw(st.integers(min_value=1, max_value=10))
    # Distribute n units across weeks 1..T
    weeks = draw(st.lists(st.integers(min_value=1, max_value=T),
                          min_size=n, max_size=n))
    y = [0] * (T + 1)
    for w in weeks:
        y[w] += 1
    # n demand events at arbitrary weeks
    ev_weeks = draw(st.lists(st.integers(min_value=1, max_value=T),
                             min_size=n, max_size=n))
    events = [DemandEvent(w, f"S{i}") for i, w in enumerate(ev_weeks)]
    return y, events


# Property 1: per-week assignment counts equal y_plan[t]
@settings(max_examples=100)
@given(_plan_and_events())
def test_property_per_week_counts_match(data):
    y, events = data
    recs = assign_deliveries(y, events, PARAMS)
    counts = {}
    for r in recs:
        counts[r.planned_week] = counts.get(r.planned_week, 0) + 1
    for t in range(1, T + 1):
        assert counts.get(t, 0) == y[t]


# Property 2: total assignments equal total demand
@settings(max_examples=100)
@given(_plan_and_events())
def test_property_total_equals_demand(data):
    y, events = data
    recs = assign_deliveries(y, events, PARAMS)
    assert len(recs) == len(events)


# Property 3: planned_week always inside the horizon
@settings(max_examples=100)
@given(_plan_and_events())
def test_property_planned_week_in_range(data):
    y, events = data
    recs = assign_deliveries(y, events, PARAMS)
    for r in recs:
        assert 1 <= r.planned_week <= T


# Property 4: determinism — identical input yields identical output
@settings(max_examples=100)
@given(_plan_and_events())
def test_property_determinism(data):
    y, events = data
    a = assign_deliveries(list(y), list(events), PARAMS)
    b = assign_deliveries(list(y), list(events), PARAMS)
    assert [(r.site_id, r.scheduled_week, r.planned_week) for r in a] == \
           [(r.site_id, r.scheduled_week, r.planned_week) for r in b]


# Property 5: due_week_shift is arithmetically consistent
@settings(max_examples=100)
@given(_plan_and_events())
def test_property_due_shift_consistent(data):
    y, events = data
    recs = assign_deliveries(y, events, PARAMS)
    for r in recs:
        assert r.due_week_shift == r.planned_week - r.due_week
        # Change flags stay unset until a manual plan is compared.
        assert r.week_shift is None
        assert r.is_early is False and r.is_late is False


# Property 6: after comparing to a manual plan, flags follow week_shift
@settings(max_examples=100)
@given(_plan_and_events())
def test_property_manual_shift_flags_consistent(data):
    from domain.delivery_assignment import compare_against_manual_plan
    y, events = data
    recs = assign_deliveries(y, events, PARAMS)
    # Manual plan: every site produced in week 1
    schedule = {e.site_id: [0] + [1] + [0] * T for e in events}
    out = compare_against_manual_plan(recs, schedule)
    for r in out:
        assert r.compared is True
        if r.week_shift is None:
            continue
        assert r.is_early == (r.week_shift < 0)
        assert r.is_late == (r.week_shift > 0)
