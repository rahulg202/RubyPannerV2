"""Tests for the onboarding editor coercion (ui/tab_onboarding.py).

``st.data_editor`` fills newly-added rows with NaN. These tests exercise that
real frame shape, which the earlier UI smoke tests missed because the Streamlit
stub returns a MagicMock rather than a DataFrame.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ui.tab_onboarding import (
    BLANK_ROW,
    _as_bool,
    _as_int,
    _as_text,
    _to_customers,
)


# ---------------------------------------------------------------------------
# Scalar coercion
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    (5, 5),
    (5.0, 5),
    ("7", 7),
    (None, 3),
    (float("nan"), 3),
    (np.nan, 3),
    ("", 3),
    ("abc", 3),
])
def test_as_int_handles_missing_and_bad_values(value, expected):
    assert _as_int(value, 3) == expected


def test_as_int_nan_is_not_truthy_trap():
    """NaN is truthy, so `value or default` silently passes NaN through."""
    nan = float("nan")
    assert bool(nan) is True          # the trap
    assert _as_int(nan, 1) == 1       # handled correctly


@pytest.mark.parametrize("value,expected", [
    ("Acme", "Acme"),
    ("  Acme  ", "Acme"),
    (None, ""),
    (float("nan"), ""),
    ("nan", ""),
    ("None", ""),
    (123, "123"),
])
def test_as_text_handles_missing_values(value, expected):
    assert _as_text(value) == expected


@pytest.mark.parametrize("value,expected", [
    (True, True),
    (False, False),
    (None, False),
    (float("nan"), False),
    (1, True),
    (0, False),
])
def test_as_bool_handles_missing_values(value, expected):
    assert _as_bool(value) == expected


# ---------------------------------------------------------------------------
# Editor frame -> customers
# ---------------------------------------------------------------------------

def test_blank_row_default_yields_no_customers():
    """The default single blank row must not crash or produce a customer."""
    df = pd.DataFrame([dict(BLANK_ROW)])
    df.loc[0, "Site_ID"] = ""
    assert _to_customers(df) == []


def test_newly_added_nan_row_is_skipped_not_crashing():
    """Reproduces the crash: adding a row gives NaN cells across the board."""
    df = pd.DataFrame([
        {"Site_ID": "SN-1", "Site_Name": "Alpha", "Earliest_Week": 2,
         "Latest_Week": 6, "Interval_Weeks": 7, "Country": "usa",
         "EU_Restricted": False},
        {"Site_ID": np.nan, "Site_Name": np.nan, "Earliest_Week": np.nan,
         "Latest_Week": np.nan, "Interval_Weeks": np.nan, "Country": np.nan,
         "EU_Restricted": np.nan},
    ])
    customers = _to_customers(df)          # must not raise
    assert len(customers) == 1
    assert customers[0].site_id == "SN-1"


def test_partially_filled_row_uses_defaults_for_missing_numbers():
    df = pd.DataFrame([
        {"Site_ID": "SN-2", "Site_Name": np.nan, "Earliest_Week": np.nan,
         "Latest_Week": np.nan, "Interval_Weeks": np.nan, "Country": np.nan,
         "EU_Restricted": np.nan},
    ])
    customers = _to_customers(df)
    assert len(customers) == 1
    c = customers[0]
    assert c.site_id == "SN-2"
    assert c.site_name == ""
    assert c.earliest_week == 1
    assert c.latest_week == 1
    assert c.interval_weeks == 7
    assert c.country == ""
    assert c.eu_restricted is False


def test_country_lowercased_and_stripped():
    df = pd.DataFrame([{
        "Site_ID": "SN-3", "Site_Name": "Beta", "Earliest_Week": 1,
        "Latest_Week": 4, "Interval_Weeks": 7, "Country": "  DENMARK ",
        "EU_Restricted": False,
    }])
    assert _to_customers(df)[0].country == "denmark"


def test_multiple_valid_rows_all_converted():
    df = pd.DataFrame([
        {"Site_ID": "A", "Site_Name": "", "Earliest_Week": 1, "Latest_Week": 5,
         "Interval_Weeks": 7, "Country": "usa", "EU_Restricted": False},
        {"Site_ID": "B", "Site_Name": "", "Earliest_Week": 3, "Latest_Week": 9,
         "Interval_Weeks": 8, "Country": "uk", "EU_Restricted": True},
    ])
    customers = _to_customers(df)
    assert [c.site_id for c in customers] == ["A", "B"]
    assert customers[1].eu_restricted is True


def test_float_weeks_from_editor_coerced_to_int():
    """Numeric editor columns can arrive as floats."""
    df = pd.DataFrame([{
        "Site_ID": "SN-4", "Site_Name": "", "Earliest_Week": 2.0,
        "Latest_Week": 6.0, "Interval_Weeks": 7.0, "Country": "usa",
        "EU_Restricted": False,
    }])
    c = _to_customers(df)[0]
    assert (c.earliest_week, c.latest_week, c.interval_weeks) == (2, 6, 7)


def test_missing_columns_do_not_crash():
    """A frame lacking optional columns must still work."""
    df = pd.DataFrame([{"Site_ID": "SN-5"}])
    customers = _to_customers(df)
    assert len(customers) == 1
    assert customers[0].interval_weeks == 7


def test_none_and_non_dataframe_return_empty():
    assert _to_customers(None) == []
    assert _to_customers("not a frame") == []


def test_all_blank_frame_returns_empty():
    df = pd.DataFrame([
        {"Site_ID": np.nan, "Earliest_Week": np.nan},
        {"Site_ID": "", "Earliest_Week": np.nan},
    ])
    assert _to_customers(df) == []


# ---------------------------------------------------------------------------
# Plain-language cost effect shown in the selection dropdown
# ---------------------------------------------------------------------------

def test_cost_effect_negative_reads_as_saving():
    from ui.tab_onboarding import _cost_effect
    # Negative delta means the plan gets cheaper; never show a raw minus sign.
    assert _cost_effect(-105000) == "saves $105,000"


def test_cost_effect_positive_reads_as_extra_cost():
    from ui.tab_onboarding import _cost_effect
    assert _cost_effect(42000) == "costs $42,000 more"


def test_cost_effect_zero_is_explicit():
    from ui.tab_onboarding import _cost_effect
    assert _cost_effect(0) == "no change in cost"


def test_cost_effect_never_shows_delta_jargon():
    from ui.tab_onboarding import _cost_effect
    for value in (-5000, 0, 5000):
        text = _cost_effect(value)
        assert "Δ" not in text
        assert "composite" not in text
