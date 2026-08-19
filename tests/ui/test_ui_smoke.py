"""Smoke tests for the presentation layer.

The Streamlit stub in tests/conftest.py makes these importable and renderable
without a Streamlit runtime. The UI is intentionally thin, so these verify it
wires up, renders without error, and contains no business logic.
"""

from __future__ import annotations

import pandas as pd
import pytest
import streamlit as st

from domain.errors import ValidationError
from ui import formatting as F
from ui import state as S


@pytest.fixture(autouse=True)
def clean_session():
    st.session_state.clear()
    yield
    st.session_state.clear()


# ---------------------------------------------------------------------------
# state helpers
# ---------------------------------------------------------------------------

def test_init_state_creates_all_keys():
    S.init_state(st.session_state)
    for key in S.ALL_KEYS:
        assert key in st.session_state


def test_cfg_key_namespacing():
    assert S.cfg_key("penalty_rate") == "cfg_penalty_rate"


def test_raw_settings_falls_back_to_defaults():
    defaults = {"penalty_rate": 7000.0, "row_cap": 2}
    st.session_state[S.cfg_key("row_cap")] = 5
    raw = S.raw_settings(st.session_state, defaults)
    assert raw["penalty_rate"] == 7000.0   # default
    assert raw["row_cap"] == 5             # overridden


def test_clear_results_resets_derived_keys():
    S.init_state(st.session_state)
    st.session_state[S.OPT_RESULT] = "something"
    S.clear_results(st.session_state)
    assert st.session_state[S.OPT_RESULT] is None


# ---------------------------------------------------------------------------
# formatting helpers
# ---------------------------------------------------------------------------

def test_usd_and_signed():
    assert F.usd(1234567) == "$1,234,567"
    assert F.usd(None) == "—"
    assert F.usd_signed(-500) == "-$500"
    assert F.usd_signed(500) == "$500"


def test_pct_handles_none():
    assert F.pct(None) == "n/a"
    assert F.pct(60.0) == "60.0%"


def test_thousands():
    assert F.thousands(28000) == "$28K"


def test_add_week_dates_inserts_columns():
    from datetime import date
    df = pd.DataFrame({"Week": [1, 2], "Good_Production": [10, 20]})
    dates = [(1, date(2026, 1, 5), date(2026, 1, 9)),
             (2, date(2026, 1, 12), date(2026, 1, 16))]
    out = F.add_week_dates(df, dates)
    assert list(out.columns) == ["Week", "MFG_Date", "Cal_Date", "Good_Production"]


def test_add_week_dates_noop_without_dates():
    df = pd.DataFrame({"Week": [1], "Good_Production": [10]})
    assert list(F.add_week_dates(df, []).columns) == ["Week", "Good_Production"]


def test_mark_current_week():
    df = pd.DataFrame({"Week": [1, 2, 3]})
    out = F.mark_current_week(df, 2)
    assert list(out["Now"]) == ["", "▶", ""]


def test_mark_current_week_noop_when_none():
    df = pd.DataFrame({"Week": [1, 2]})
    assert "Now" not in F.mark_current_week(df, None).columns


def test_comparison_frame_renders_na():
    rows = [{"Component": "Penalty", "Baseline": 0.0, "Optimized": 0.0,
             "Saving_Abs": 0.0, "Saving_Pct": None}]
    out = F.comparison_frame(rows)
    assert out.iloc[0]["Saving %"] == "n/a"


def test_changed_weeks_frame_labels_shift():
    from domain.delivery_assignment import DeliveryRecord
    recs = [
        DeliveryRecord("A", "", "usa", due_week=5, planned_week=3,
                       due_week_shift=-2, manual_week=5, week_shift=-2,
                       is_early=True, compared=True),
        DeliveryRecord("B", "", "usa", due_week=3, planned_week=5,
                       due_week_shift=2, manual_week=3, week_shift=2,
                       is_late=True, compared=True),
        DeliveryRecord("C", "", "usa", due_week=4, planned_week=4,
                       due_week_shift=0, manual_week=4, week_shift=0,
                       compared=True),
        DeliveryRecord("D", "", "usa", due_week=4, planned_week=4,
                       due_week_shift=0, is_new_customer=True, compared=True),
        DeliveryRecord("E", "", "usa", due_week=4, planned_week=4,
                       due_week_shift=0, manual_week=None, week_shift=None,
                       compared=True),
    ]
    out = F.changed_weeks_frame(recs)
    assert list(out["Shift"]) == [
        "Moved earlier", "Moved later", "Same as manual",
        "New customer", "No counterpart",
    ]


def test_quota_frame_flags_shortfall():
    from domain.quota import QuarterlyQuotaStatus
    q = [QuarterlyQuotaStatus(
        "Curium", 1, (1, 2), 100.0, 40.0, 60.0, 60.0, 600.0,
        weeks_covered=13, expected_weeks=13, status="SHORTFALL",
    )]
    out = F.quota_frame(q)
    assert out.iloc[0]["Status"] == "SHORTFALL"
    assert out.iloc[0]["Coverage"] == "13/13 wks"
    assert out.iloc[0]["Penalty"] == "$600"


def test_quota_frame_partial_quarter_shows_prorated_target_and_no_penalty():
    from domain.quota import STATUS_PARTIAL, QuarterlyQuotaStatus
    q = [QuarterlyQuotaStatus(
        "BWXT", 5, (50, 51, 52), 10000.0, 1200.0, 8800.0, 0.0, 0.0,
        is_partial=True, weeks_covered=3, expected_weeks=13,
        prorated_quota_mci=10000.0 * 3 / 13,
        prorated_shortfall_mci=10000.0 * 3 / 13 - 1200.0,
        status=STATUS_PARTIAL,
    )]
    out = F.quota_frame(q).iloc[0]
    assert out["Status"] == STATUS_PARTIAL
    assert out["Coverage"] == "3/13 wks"
    assert out["Penalty"] == "$0"
    # Target is pro-rated, not the full quota
    assert out["Target (mCi)"] == round(10000.0 * 3 / 13, 1)
    assert out["Quota (mCi)"] == 10000.0


def test_rankings_frame_has_column_per_customer():
    from domain.onboarding import CombinationResult
    opts = [CombinationResult(selected_weeks={"N1": 3, "N2": 7}, feasible=True,
                              delta_composite=-100.0)]
    out = F.rankings_frame(opts, ["N1", "N2"])
    assert "N1 week" in out.columns and "N2 week" in out.columns


# ---------------------------------------------------------------------------
# Tab modules import and render
# ---------------------------------------------------------------------------

def test_ui_modules_import():
    from ui import tab_comparison, tab_onboarding, tab_optimizer, tab_settings
    assert all([tab_settings, tab_optimizer, tab_onboarding, tab_comparison])


def test_settings_tab_renders():
    from ui import tab_settings
    tab_settings.render()   # must not raise under the stub


def test_settings_restore_defaults_populates_session():
    from services.settings_service import DEFAULTS
    from ui import tab_settings
    tab_settings.restore_defaults()
    assert st.session_state[S.cfg_key("penalty_rate")] == DEFAULTS["penalty_rate"]


def test_workflow_tabs_render_with_validation_error():
    """A settings error must be shown, not raised, in every workflow tab."""
    from ui import tab_comparison, tab_converter, tab_onboarding, tab_optimizer
    S.init_state(st.session_state)
    err = ValidationError(["bad weight"])
    tab_optimizer.render(object(), err)
    tab_onboarding.render(object(), err)
    tab_comparison.render(object(), err)
    tab_converter.render(object(), err)


def test_converter_tab_renders_without_an_upload():
    """With valid settings but no workbook, the tab prompts instead of running."""
    from services.settings_service import DEFAULTS, build_settings
    from ui import tab_converter
    S.init_state(st.session_state)
    tab_converter.render(object(), build_settings(dict(DEFAULTS)))


def test_converter_tab_renders_a_result():
    from services.dtos import ConversionResult
    from services.settings_service import DEFAULTS, build_settings
    from ui import tab_converter

    S.init_state(st.session_state)
    st.session_state[S.CONV_RESULT] = ConversionResult(
        sites_df=pd.DataFrame({
            "Site_ID": ["00449"], "Site_Name": ["Alpha"], "Active": ["Y"],
            "Next_Demand_Week": [3], "Interval_Weeks": [7],
            "Country": ["usa"], "EU_Restricted": ["N"],
        }),
        mapping_df=pd.DataFrame({"Site_ID": ["00449"]}),
        notes_df=pd.DataFrame(columns=["Site_ID", "Note"]),
        xlsx_bytes=b"XLSX",
        year=2026, site_count=1, active_count=1,
        scheduled_deliveries=7, implied_deliveries=7,
        warnings=["check something"],
    )
    tab_converter.render(object(), build_settings(dict(DEFAULTS)))


def test_app_module_imports_and_builds_services():
    import app
    optimizer, onboarding, comparison, conversion = app.build_services()
    assert optimizer and onboarding and comparison and conversion


def test_app_current_settings_returns_settings_or_error():
    import app
    from services.settings_service import Settings
    result = app.current_settings()
    assert isinstance(result, (Settings, ValidationError))


# ---------------------------------------------------------------------------
# Layering: the UI must not hold business logic
# ---------------------------------------------------------------------------

def test_ui_does_not_import_solver_or_adapters_directly():
    import pathlib
    import re
    offenders = []
    for path in pathlib.Path("ui").glob("*.py"):
        text = path.read_text()
        if re.search(r"^\s*(from|import)\s+domain\.solver", text, re.M):
            offenders.append(f"{path}: imports domain.solver")
        if re.search(r"^\s*(from|import)\s+io_adapters", text, re.M):
            offenders.append(f"{path}: imports io_adapters")
    assert offenders == [], offenders
