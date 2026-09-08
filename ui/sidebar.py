"""Presentation: one place to load the files the whole application works from.

Every workflow reads the same two files — the sites input and the Master Planner —
and each tab used to carry its own uploader. Three separate Master Planner
uploaders and three separate sheet dropdowns all wrote to the same session slots,
so the planner had to guess where to upload, and Onboarding had neither an uploader
nor a dropdown of its own and simply read whatever another tab had left behind.

That was not only confusing, it was a defect: Streamlit renders every tab on each
interaction, so the last dropdown to render won. Changing the sheet in the
Comparison tab silently left Onboarding parsing a summary sheet.

Loading files once, in the sidebar, fixes both. The sidebar stays visible whichever
tab is open, so what is loaded is always in view, and there is exactly one place
that can set the sheet.
"""

from __future__ import annotations

import io

import pandas as pd
import streamlit as st

from ui import state as S

SITES_TYPES = ["xlsx", "xls", "csv"]
PLANNER_TYPES = ["xlsx", "xls"]


def _read_sheet_names(data: bytes) -> list[str] | None:
    """Worksheet names, or None when the workbook cannot be opened."""
    try:
        return pd.ExcelFile(io.BytesIO(data)).sheet_names
    except Exception:  # noqa: BLE001 - reported to the planner by the caller
        return None


def _sites_section() -> None:
    """Upload the sites input file and choose its worksheet."""
    st.markdown("**Sites input file**")
    uploaded = st.file_uploader(
        "Sites file",
        type=SITES_TYPES,
        key="sb_sites_uploader",
        label_visibility="collapsed",
        help="One row per site: Site_ID, Active, Next_Demand_Week, "
             "Interval_Weeks, Country.",
    )

    if uploaded is not None:
        data = uploaded.read()
        st.session_state[S.SITES_BYTES] = data
        st.session_state[S.SITES_NAME] = uploaded.name
        # A new file invalidates everything derived from the old one.
        S.clear_results(st.session_state)

        if uploaded.name.lower().endswith(".csv"):
            st.session_state[S.SITES_SHEETS] = None
            st.session_state[S.SITES_SHEET] = "Sites"
        else:
            sheets = _read_sheet_names(data)
            st.session_state[S.SITES_SHEETS] = sheets
            if sheets is None:
                st.error("Could not read that workbook.")

    name = st.session_state.get(S.SITES_NAME)
    if not name:
        st.caption("Not loaded. The Cost Optimizer and Onboarding need this.")
        return

    sheets = st.session_state.get(S.SITES_SHEETS)
    if sheets:
        current = st.session_state.get(S.SITES_SHEET)
        default = current if current in sheets else (
            "Sites" if "Sites" in sheets else sheets[0]
        )
        st.session_state[S.SITES_SHEET] = st.selectbox(
            "Sheet", options=sheets, index=sheets.index(default),
            key="sb_sites_sheet",
        )
    else:
        st.session_state.setdefault(S.SITES_SHEET, "Sites")
    st.caption(f"✓ **{name}**")


def _planner_section() -> None:
    """Upload the Master Planner workbook and choose its schedule sheet."""
    st.markdown("**Master Planner workbook**")
    uploaded = st.file_uploader(
        "Master Planner",
        type=PLANNER_TYPES,
        key="sb_planner_uploader",
        label_visibility="collapsed",
        help="Your hand-built plan. Used to import an input file, to compare "
             "against, and to onboard into a fixed plan.",
    )

    if uploaded is not None:
        data = uploaded.read()
        st.session_state[S.MP_BYTES] = data
        st.session_state[S.MP_NAME] = uploaded.name
        S.clear_results(st.session_state)
        sheets = _read_sheet_names(data)
        st.session_state[S.MP_SHEETS] = sheets
        if sheets is None:
            st.error("Could not read that workbook.")

    name = st.session_state.get(S.MP_NAME)
    if not name:
        st.caption(
            "Not loaded. Needed for Import Manual Plan, Comparison, changed "
            "customer weeks, and fixed-plan onboarding."
        )
        return

    sheets = st.session_state.get(S.MP_SHEETS)
    if sheets:
        current = st.session_state.get(S.MP_SHEET)
        default = current if current in sheets else (
            "Schedule" if "Schedule" in sheets else sheets[0]
        )
        st.session_state[S.MP_SHEET] = st.selectbox(
            "Schedule sheet", options=sheets, index=sheets.index(default),
            key="sb_planner_sheet",
            help="The sheet holding the week-by-customer grid. If you pick the "
                 "wrong one the tool finds the right one and tells you.",
        )
    else:
        st.session_state.setdefault(S.MP_SHEET, "Schedule")
    st.caption(f"✓ **{name}**")


def render() -> None:
    """Draw the shared file loader. Call once, before the tabs."""
    with st.sidebar:
        st.header("Files")
        st.caption(
            "Load each file once here. Every tab uses what is loaded, so there is "
            "nothing to upload again."
        )
        _sites_section()
        st.divider()
        _planner_section()

        if st.session_state.get(S.SITES_BYTES) or st.session_state.get(S.MP_BYTES):
            st.divider()
            if st.button("Clear loaded files", key="sb_clear",
                         help="Removes both files and every result derived "
                              "from them."):
                for key in (S.SITES_BYTES, S.SITES_NAME, S.SITES_SHEETS,
                            S.SITES_SHEET, S.MP_BYTES, S.MP_NAME, S.MP_SHEETS,
                            S.MP_SHEET):
                    st.session_state[key] = None
                S.clear_results(st.session_state)
                st.success("Cleared.")


# ---------------------------------------------------------------------------
# What the tabs ask instead of carrying their own uploaders
# ---------------------------------------------------------------------------

def sites_loaded() -> bool:
    return st.session_state.get(S.SITES_BYTES) is not None


def planner_loaded() -> bool:
    return st.session_state.get(S.MP_BYTES) is not None


def sites_sheet() -> str:
    return st.session_state.get(S.SITES_SHEET) or "Sites"


def planner_sheet() -> str:
    return st.session_state.get(S.MP_SHEET) or "Schedule"


def require(*, sites: bool = False, planner: bool = False) -> bool:
    """Prompt for whatever is missing; True when the tab can proceed."""
    missing = []
    if sites and not sites_loaded():
        missing.append("**sites input file**")
    if planner and not planner_loaded():
        missing.append("**Master Planner workbook**")
    if not missing:
        return True
    st.info(
        f"Load the {' and the '.join(missing)} in the sidebar to use this tab."
    )
    return False
