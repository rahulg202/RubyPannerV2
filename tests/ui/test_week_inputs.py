"""Tests for entering weeks as dates rather than numbers.

Covers the pure conversion helpers behind the week pickers and the onboarding
window. The Streamlit stub accepts any argument, so these assert on the data
transformations rather than on the widget calls; the real widget API is exercised
by tests/integration/test_app_real_streamlit.py.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest
import streamlit as st

from services.settings_service import build_settings, parse_week_list
from ui import state as S
from ui.formatting import week_option_label

MONDAY = date(2026, 1, 5)


@pytest.fixture(autouse=True)
def clean_session():
    st.session_state.clear()
    yield
    st.session_state.clear()


# ---------------------------------------------------------------------------
# Week labels
# ---------------------------------------------------------------------------

def test_week_label_includes_the_date_when_known():
    assert week_option_label(1, {1: MONDAY}) == "Week 1 — 05 Jan 2026"


def test_week_label_falls_back_to_the_number():
    assert week_option_label(7, {}) == "Week 7"
    assert week_option_label(7, {1: MONDAY}) == "Week 7"


# ---------------------------------------------------------------------------
# Legacy comma-separated values must keep working
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("stored,expected", [
    ("1,2,26", [1, 2, 26]),
    ("", []),
    ([3, 4], [3, 4]),
    ((5,), [5]),
    (None, []),
])
def test_stored_week_values_parse_into_a_list(stored, expected):
    weeks, error = parse_week_list(stored)
    assert weeks == expected
    assert error is None


def test_a_saved_comma_string_still_builds_valid_settings():
    """A session saved before the picker existed must not break on load."""
    settings = build_settings({"shutdown_weeks": "1,2,26"})
    assert settings.shutdown_weeks == (1, 2, 26)


def test_a_picked_list_builds_valid_settings():
    settings = build_settings({"shutdown_weeks": [26, 1, 2]})
    assert settings.shutdown_weeks == (1, 2, 26)      # sorted by the service


# ---------------------------------------------------------------------------
# Onboarding window: dates <-> weeks
# ---------------------------------------------------------------------------

def _frame(**over) -> pd.DataFrame:
    row = {
        "Site_ID": "NEW-1", "Site_Name": "Trial", "Earliest_Week": 3,
        "Latest_Week": 9, "Interval_Weeks": 7, "Country": "usa",
        "EU_Restricted": False,
    }
    row.update(over)
    return pd.DataFrame([row])


def test_frame_migrates_week_numbers_to_dates():
    from ui.tab_onboarding import _shape_frame

    out = _shape_frame(_frame(), MONDAY, 52)

    assert "Earliest_Date" in out.columns and "Latest_Date" in out.columns
    assert "Earliest_Week" not in out.columns
    # Week 3 is two weeks after week 1.
    assert out.loc[0, "Earliest_Date"] == date(2026, 1, 19)
    assert out.loc[0, "Latest_Date"] == date(2026, 3, 2)


def test_frame_migrates_dates_back_to_week_numbers():
    from ui.tab_onboarding import _shape_frame

    dated = _frame().assign(
        Earliest_Date=[date(2026, 1, 19)], Latest_Date=[date(2026, 3, 2)],
    ).drop(columns=["Earliest_Week", "Latest_Week"])

    out = _shape_frame(dated, None, 52)
    assert "Earliest_Week" in out.columns
    assert "Earliest_Date" not in out.columns


def test_switching_modes_does_not_lose_rows():
    from ui.tab_onboarding import _shape_frame

    with_dates = _shape_frame(_frame(Site_ID="KEEP-ME"), MONDAY, 52)
    back_to_weeks = _shape_frame(with_dates, None, 52)

    assert list(back_to_weeks["Site_ID"]) == ["KEEP-ME"]
    assert len(back_to_weeks) == 1


def test_column_order_is_stable():
    from ui.tab_onboarding import _shape_frame

    assert list(_shape_frame(_frame(), MONDAY, 52)) == [
        "Site_ID", "Site_Name", "Earliest_Date", "Latest_Date",
        "Interval_Weeks", "Country", "EU_Restricted",
    ]


# ---------------------------------------------------------------------------
# The editor's input must stay stable, or its edits get replayed twice
# ---------------------------------------------------------------------------

def test_seed_is_not_rebuilt_on_every_rerun():
    """Regression: adding a second row used to wipe the first.

    Streamlit stores data_editor changes as a positional delta and replays them
    onto whatever data it is handed. The seed was being rebuilt from the editor's
    own output each rerun, so "add a row" and "row 0 was edited" were applied a
    second time against data that already contained them.
    """
    from ui.tab_onboarding import _editor_seed

    S.init_state(st.session_state)
    first = _editor_seed(None, 52)
    st.session_state[S.OB_VIEW] = _frame(Site_ID="TYPED-BY-USER")

    # A later rerun, same mode, must hand back the very same object.
    assert _editor_seed(None, 52) is first


def test_seed_is_reshaped_only_when_the_mode_changes():
    from ui.tab_onboarding import _editor_seed

    S.init_state(st.session_state)
    st.session_state[S.OB_CUSTOMERS] = _frame(Site_ID="KEEP-ME")
    weeks_seed = _editor_seed(None, 52)
    assert "Earliest_Week" in weeks_seed.columns

    # The planner has since typed into the editor; that is what must carry over.
    st.session_state[S.OB_VIEW] = _frame(Site_ID="TYPED-LATER")
    dates_seed = _editor_seed(MONDAY, 52)

    assert "Earliest_Date" in dates_seed.columns
    assert list(dates_seed["Site_ID"]) == ["TYPED-LATER"]


def test_switching_mode_drops_the_outgoing_editor_state():
    """A stale delta would otherwise replay against columns that no longer exist."""
    from ui.tab_onboarding import _editor_seed

    S.init_state(st.session_state)
    _editor_seed(None, 52)
    st.session_state["ob_editor_weeks"] = {"edited_rows": {0: {"Earliest_Week": 9}}}

    _editor_seed(MONDAY, 52)
    assert "ob_editor_weeks" not in st.session_state


def test_seed_recovers_from_an_empty_frame():
    from ui.tab_onboarding import _editor_seed

    S.init_state(st.session_state)
    st.session_state[S.OB_CUSTOMERS] = pd.DataFrame()
    seed = _editor_seed(None, 52)
    assert len(seed) == 1
    assert "Site_ID" in seed.columns


def test_customers_are_built_from_dates():
    from ui.tab_onboarding import _to_customers

    frame = _frame().assign(
        Earliest_Date=[date(2026, 1, 19)], Latest_Date=[date(2026, 3, 2)],
    ).drop(columns=["Earliest_Week", "Latest_Week"])

    customers = _to_customers(frame, MONDAY, 52)
    assert len(customers) == 1
    assert customers[0].earliest_week == 3
    assert customers[0].latest_week == 9


def test_a_mid_week_date_snaps_to_its_planning_week():
    from ui.tab_onboarding import _to_customers

    # 21 Jan 2026 is a Wednesday inside week 3 (starts Monday 19 Jan).
    frame = _frame().assign(
        Earliest_Date=[date(2026, 1, 21)], Latest_Date=[date(2026, 1, 21)],
    ).drop(columns=["Earliest_Week", "Latest_Week"])

    assert _to_customers(frame, MONDAY, 52)[0].earliest_week == 3


def test_pandas_timestamps_from_the_editor_are_accepted():
    """st.data_editor hands back Timestamps, not dates."""
    from ui.tab_onboarding import _to_customers

    frame = _frame().assign(
        Earliest_Date=[pd.Timestamp("2026-01-19")],
        Latest_Date=[pd.Timestamp("2026-03-02")],
    ).drop(columns=["Earliest_Week", "Latest_Week"])

    customers = _to_customers(frame, MONDAY, 52)
    assert (customers[0].earliest_week, customers[0].latest_week) == (3, 9)


def test_dates_outside_the_horizon_are_clamped_not_dropped():
    from ui.tab_onboarding import _to_customers

    frame = _frame().assign(
        Earliest_Date=[date(2020, 1, 1)],       # long before week 1
        Latest_Date=[date(2030, 1, 1)],         # long after the horizon
    ).drop(columns=["Earliest_Week", "Latest_Week"])

    customer = _to_customers(frame, MONDAY, 52)[0]
    assert customer.earliest_week == 1
    assert customer.latest_week == 52


def test_week_numbers_are_used_when_no_reference_week_is_set():
    from ui.tab_onboarding import _to_customers

    customers = _to_customers(_frame(), None, 52)
    assert (customers[0].earliest_week, customers[0].latest_week) == (3, 9)


def test_blank_rows_are_still_skipped():
    from ui.tab_onboarding import _to_customers

    frame = pd.concat([_frame(), _frame(Site_ID="")], ignore_index=True)
    assert len(_to_customers(frame, None, 52)) == 1


# ---------------------------------------------------------------------------
# Week numbers in output tables carry their date
# ---------------------------------------------------------------------------

def test_week_date_map_from_result_triples():
    from ui.formatting import week_date_map

    triples = [(1, MONDAY, date(2026, 1, 9)), (2, date(2026, 1, 12), None)]
    assert week_date_map(triples) == {1: MONDAY, 2: date(2026, 1, 12)}


def test_week_date_map_is_empty_without_a_reference_week():
    from ui.formatting import week_date_map

    assert week_date_map([]) == {}
    assert week_date_map(None) == {}


def test_week_label_shows_the_date_when_known():
    from ui.formatting import week_label

    assert week_label(1, {1: MONDAY}) == "1 — 05 Jan 2026"


def test_week_label_falls_back_to_the_bare_number():
    """Without an anchor there is no date, and none is invented."""
    from ui.formatting import week_label

    assert week_label(5, {}) == "5"
    assert week_label(5, {1: MONDAY}) == "5"


def test_week_label_handles_a_missing_week():
    from ui.formatting import week_label

    assert week_label(None, {1: MONDAY}) == "—"


class _Option:
    """Minimal stand-in for a ranked onboarding combination."""

    def __init__(self, week):
        self.selected_weeks = {"NEW-1": week}
        self.delta_penalty = 0.0
        self.delta_overtime = 0.0
        self.delta_capacity = -15000.0
        self.delta_composite = -15000.0
        self.overtime_weeks = 0


def test_rankings_frame_dates_the_week_column():
    from ui.formatting import rankings_frame

    frame = rankings_frame([_Option(3)], ["NEW-1"],
                           [(3, date(2026, 1, 19), None)])
    assert frame.loc[0, "NEW-1 week"] == "3 — 19 Jan 2026"


def test_rankings_frame_without_dates_shows_the_number():
    from ui.formatting import rankings_frame

    frame = rankings_frame([_Option(3)], ["NEW-1"], None)
    assert frame.loc[0, "NEW-1 week"] == "3"
