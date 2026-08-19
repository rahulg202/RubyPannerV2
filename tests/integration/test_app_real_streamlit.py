"""Render the app against the real Streamlit API, not the test stub.

Why this exists
---------------
``tests/conftest.py`` replaces Streamlit with a ``MagicMock`` so the UI can be
imported without a runtime. That stub accepts *any* argument, which is exactly
how ``st.data_editor(..., width="stretch")`` passed 574 tests and then crashed in
production with ``TypeError: 'str' object cannot be interpreted as an integer``:
the deployed Streamlit accepted the literal, the locally-installed one wanted an
int, and the stub cared about neither.

These tests run the app through ``streamlit.testing.v1.AppTest``, which executes
the script against the genuine widget API and reports any exception the script
raised. They run in a **subprocess** so the conftest stub — installed globally at
import time — cannot leak in.

Scope: rendering only. No uploads, no solver runs. That is enough to catch widget
signatures drifting away from the pinned Streamlit version, which is the failure
this guards against.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# Marker the child prints on success; anything else means the render failed.
OK = "APPTEST_OK"


def _run(script: str) -> subprocess.CompletedProcess:
    """Execute a snippet in a clean interpreter rooted at the repo."""
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=600,
    )


def _assert_rendered(result: subprocess.CompletedProcess) -> None:
    combined = f"{result.stdout}\n{result.stderr}"
    assert result.returncode == 0 and OK in result.stdout, combined


# Shared preamble: build the AppTest and a reporter for script exceptions.
_HEADER = f"""
import sys
from streamlit.testing.v1 import AppTest

def report(at):
    if at.exception:
        for exc in at.exception:
            sys.stderr.write(str(getattr(exc, "value", exc)) + "\\n")
            sys.stderr.write(str(getattr(exc, "stack_trace", "")) + "\\n")
        raise SystemExit(1)
    print("{OK}")
"""


def test_app_renders_every_tab():
    """A cold start must render all five tabs without raising."""
    _assert_rendered(_run(_HEADER + """
at = AppTest.from_file("app.py", default_timeout=300)
at.run()
report(at)
"""))


def test_app_renders_with_the_settings_expanders_touched():
    """Non-default settings must not break any widget."""
    _assert_rendered(_run(_HEADER + """
from datetime import date

at = AppTest.from_file("app.py", default_timeout=300)
at.session_state["cfg_use_reference"] = True
at.session_state["cfg_reference_week_date"] = date(2026, 1, 5)
at.session_state["cfg_calibration_offset_days"] = 2
at.session_state["cfg_w_capacity"] = 0.0
at.session_state["cfg_shutdown_weeks"] = "10,11"
at.run()
report(at)
"""))


def test_optimizer_result_panel_renders():
    """The result tables (plan, quota, changed weeks) use the real dataframe API."""
    _assert_rendered(_run(_HEADER + """
from datetime import date
import pandas as pd

from domain.delivery_assignment import DeliveryRecord
from domain.quota import QuarterlyQuotaStatus
from services.dtos import OptimizationResult
from ui import state as S

plan = pd.DataFrame({
    "Week": [1, 2],
    "Week_Type": ["Normal", "Partial"],
    "Demand_Due": [10, 12],
    "Good_Production": [15, 15],
    "Composite_Cost_USD": [1000.0, 2000.0],
})
result = OptimizationResult(
    plan_df=plan,
    summary={
        "total_composite_cost": 3000.0, "total_penalty_cost": 1000.0,
        "total_overtime_cost": 2000.0, "total_capacity_cost": 0.0,
        "overtime_weeks": 1, "total_quota_penalty_cost": 0.0,
        "partial_quarter_note": "Q1 3/13 wks fall partly outside the plan.",
    },
    issues_df=pd.DataFrame(columns=["row_index", "site_id", "issue"]),
    active_df=pd.DataFrame({"site_id": ["00449"]}),
    quota_status=[
        QuarterlyQuotaStatus(
            supplier="Curium", quarter=1, weeks=(1, 2), quota_mci=10000.0,
            ordered_mci=1500.0, remaining_mci=8500.0, shortfall_mci=0.0,
            penalty_usd=0.0, is_partial=True, weeks_covered=2,
            expected_weeks=13, prorated_quota_mci=1538.0,
            prorated_shortfall_mci=38.0, status="Partial — not penalised",
        ),
    ],
    assignments=[
        DeliveryRecord(
            site_id="00449", site_name="Alpha", country="usa", due_week=10,
            planned_week=8, due_week_shift=-2, manual_week=10, week_shift=-2,
            is_early=True, compared=True,
        ),
    ],
    change_summary={
        "compared": True, "total": 1, "unchanged": 0, "early": 1, "late": 0,
        "new_customers": 0, "uncomparable": 0,
    },
    week_dates=[(1, date(2026, 1, 5), date(2026, 1, 9)),
                (2, date(2026, 1, 12), date(2026, 1, 16))],
    xlsx_bytes=b"XLSX",
    warnings=["a warning"],
)

at = AppTest.from_file("app.py", default_timeout=300)
at.session_state[S.OPT_RESULT] = result
at.run()
report(at)
"""))


def test_converter_result_panel_renders():
    """The Import Manual Plan result tables render against the real API."""
    _assert_rendered(_run(_HEADER + """
import pandas as pd

from domain.site_derivation import (
    PlannerColumn, derive_sites, mapping_frame, notes_frame, sites_frame,
)
from services.dtos import ConversionResult
from ui import state as S

columns = [
    PlannerColumn("00449  Alpha Care, CA (7)", marks=(1, 8, 15), column_letter="L"),
    PlannerColumn("Beta Cardiology, TN", marks=(), column_letter="M"),
]
sites, warnings = derive_sites(columns, lambda h: "RF-0001")

at = AppTest.from_file("app.py", default_timeout=300)
at.session_state[S.CONV_RESULT] = ConversionResult(
    sites_df=sites_frame(sites),
    mapping_df=mapping_frame(sites),
    notes_df=notes_frame(sites),
    xlsx_bytes=b"XLSX",
    year=2026, site_count=2, active_count=1,
    generated_code_count=1, eu_restricted_count=0,
    scheduled_deliveries=3, implied_deliveries=8,
    issues_df=pd.DataFrame(columns=["row_index", "site_id", "issue"]),
    warnings=warnings + ["check the intervals"],
)
at.run()
report(at)
"""))


def test_comparison_result_panel_renders():
    """The Comparison tables render against the real API."""
    _assert_rendered(_run(_HEADER + """
import pandas as pd

from domain.comparison import BaselineResult
from io_adapters.master_planner_parser import AssignedId
from services.dtos import ComparisonResult
from ui import state as S

components = [
    {"Component": "Penalty", "Baseline": 2000.0, "Optimized": 500.0,
     "Saving_Abs": 1500.0, "Saving_Pct": 75.0},
    {"Component": "Total Composite", "Baseline": 3000.0, "Optimized": 1000.0,
     "Saving_Abs": 2000.0, "Saving_Pct": 66.7},
]

at = AppTest.from_file("app.py", default_timeout=300)
at.session_state[S.CMP_RESULT] = ComparisonResult(
    components=components,
    overtime_baseline=3, overtime_optimized=1,
    weekly_comparison=pd.DataFrame({
        "Week": [1, 2], "Manual_Production": [30, 20],
        "Optimized_Production": [25, 25],
    }),
    assigned_ids=[AssignedId("RF-0001", "Beta Cardiology, TN",
                             "Beta Cardiology, TN", "beta cardiology tn")],
    warnings=["a warning"],
)
at.run()
report(at)
"""))
