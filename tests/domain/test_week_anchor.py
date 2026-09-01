"""Tests for anchoring planning week 1 to a real date (domain.week_anchor)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from domain.week_anchor import DatedRow, anchor_window, describe_anchor

MONDAY = date(2026, 1, 5)


def _rows(count: int, start: date = MONDAY, first_position: int = 0):
    """Weekly rows, one per Monday, positions ascending from ``first_position``."""
    return [
        DatedRow(position=first_position + i, mfg_date=start + timedelta(days=7 * i))
        for i in range(count)
    ]


# ---------------------------------------------------------------------------
# Locating the anchor
# ---------------------------------------------------------------------------

def test_exact_date_becomes_week_one():
    window = anchor_window(_rows(10), MONDAY, horizon_weeks=5)
    assert window.resolved
    assert window.anchor_date == MONDAY
    assert window.weeks[0] == (1, 0)
    assert window.warnings == []


@pytest.mark.parametrize("offset", [0, 1, 3, 6])
def test_any_day_inside_a_week_selects_that_week(offset):
    """A planner picking 'the week of the 8th' should not need the exact date."""
    window = anchor_window(_rows(10), MONDAY + timedelta(days=offset), horizon_weeks=3)
    assert window.anchor_date == MONDAY
    assert not window.snapped
    assert window.warnings == []


def test_mid_horizon_reference_shifts_week_one():
    window = anchor_window(_rows(20), MONDAY + timedelta(days=7 * 5), horizon_weeks=4)
    assert window.anchor_date == MONDAY + timedelta(days=35)
    assert window.weeks == [(1, 5), (2, 6), (3, 7), (4, 8)]


def test_weeks_are_numbered_from_one_regardless_of_position():
    window = anchor_window(_rows(6, first_position=100), MONDAY, horizon_weeks=3)
    assert window.weeks == [(1, 100), (2, 101), (3, 102)]


# ---------------------------------------------------------------------------
# Snapping and edges
# ---------------------------------------------------------------------------

def test_reference_in_a_gap_snaps_back_and_says_so():
    rows = [_rows(1)[0], DatedRow(position=5, mfg_date=MONDAY + timedelta(days=70))]
    window = anchor_window(rows, MONDAY + timedelta(days=40), horizon_weeks=3)

    assert window.anchor_date == MONDAY          # the closest earlier week
    assert window.snapped
    assert any("closest earlier week" in w for w in window.warnings)


def test_reference_before_every_row_starts_at_the_first():
    window = anchor_window(_rows(5), MONDAY - timedelta(days=30), horizon_weeks=3)
    assert window.anchor_date == MONDAY
    assert window.snapped
    assert any("before the first week" in w for w in window.warnings)


def test_reference_after_every_row_is_unresolved():
    window = anchor_window(_rows(5), MONDAY + timedelta(days=365), horizon_weeks=3)
    assert not window.resolved
    assert window.weeks == []
    assert any("falls after the last week" in w for w in window.warnings)


def test_short_window_is_reported():
    window = anchor_window(_rows(5), MONDAY, horizon_weeks=52)
    assert window.rows_available == 5
    assert len(window.weeks) == 5
    assert any("Only 5 of 52 weeks" in w for w in window.warnings)


def test_no_dated_rows_is_unresolved():
    window = anchor_window([], MONDAY, horizon_weeks=52)
    assert not window.resolved
    assert any("No manufacturing dates" in w for w in window.warnings)


# ---------------------------------------------------------------------------
# Robustness
# ---------------------------------------------------------------------------

def test_rows_out_of_order_are_sorted_by_date():
    rows = list(reversed(_rows(6)))
    window = anchor_window(rows, MONDAY, horizon_weeks=3)
    assert window.weeks == [(1, 0), (2, 1), (3, 2)]


def test_duplicate_dates_keep_the_first_row():
    rows = _rows(3) + [DatedRow(position=99, mfg_date=MONDAY)]
    window = anchor_window(rows, MONDAY, horizon_weeks=3)
    assert window.weeks[0] == (1, 0)
    assert 99 not in [pos for _w, pos in window.weeks]


def test_window_crosses_a_calendar_year_boundary():
    """The whole point: the horizon must not stop at 31 December."""
    window = anchor_window(_rows(40, start=date(2026, 7, 27)),
                           date(2026, 7, 27), horizon_weeks=30)
    last_week, last_pos = window.weeks[-1]
    assert last_week == 30
    # 29 weeks after 27 Jul 2026 lands in 2027.
    assert (date(2026, 7, 27) + timedelta(days=7 * 29)).year == 2027


def test_week_for_maps_positions_back_to_weeks():
    window = anchor_window(_rows(5), MONDAY, horizon_weeks=3)
    assert window.week_for(0) == 1
    assert window.week_for(2) == 3
    assert window.week_for(4) is None      # outside the window


# ---------------------------------------------------------------------------
# Description shown to the planner
# ---------------------------------------------------------------------------

def test_description_states_the_first_and_last_week():
    window = anchor_window(_rows(52), MONDAY, horizon_weeks=52)
    text = describe_anchor(window, 52)
    assert "05 Jan 2026" in text
    assert "52 of 52 weeks" in text


def test_description_when_unresolved():
    window = anchor_window([], MONDAY)
    assert "could not be anchored" in describe_anchor(window)
