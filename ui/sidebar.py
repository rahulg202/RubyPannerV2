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

from domain.errors import RubyFillError, ValidationError
from services.dtos import ConversionRequest
from ui import state as S
from ui.formatting import XLSX_MIME

SITES_TYPES = ["xlsx", "xls", "csv"]
PLANNER_TYPES = ["xlsx", "xls"]
DERIVED_FILENAME = "sites_from_manual_plan.xlsx"


def _read_sheet_names(data: bytes) -> list[str] | None:
    """Worksheet names, or None when the workbook cannot be opened."""
    try:
        return pd.ExcelFile(io.BytesIO(data)).sheet_names
    except Exception:  # noqa: BLE001 - reported to the planner by the caller
        return None


def _fingerprint(uploaded) -> str:
    """Identify an upload, so the same file is not treated as a new one."""
    file_id = getattr(uploaded, "file_id", None)
    if file_id:
        return str(file_id)
    return f"{uploaded.name}:{getattr(uploaded, 'size', '')}"


def _newly_uploaded(uploaded, fingerprint_key: str) -> bytes | None:
    """Bytes when this is a *different* file from the one already loaded.

    ``st.file_uploader`` keeps returning its file on every rerun, not only on the
    run where it was chosen. Acting on it unconditionally meant every interaction
    re-read the file and cleared every result — so running the optimizer and then
    pressing anything discarded the plan, and the Comparison tab reported that the
    optimizer had not been run.
    """
    if uploaded is None:
        return None
    fingerprint = _fingerprint(uploaded)
    if st.session_state.get(fingerprint_key) == fingerprint:
        return None
    st.session_state[fingerprint_key] = fingerprint
    return uploaded.read()


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

    data = _newly_uploaded(uploaded, S.SITES_FP)
    if data is not None:
        st.session_state[S.SITES_BYTES] = data
        st.session_state[S.SITES_NAME] = uploaded.name
        # A new file invalidates everything derived from the old one.
        S.clear_results(st.session_state)

        st.session_state[S.SITES_FROM_PLANNER] = False

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

    if st.session_state.get(S.SITES_FROM_PLANNER):
        st.caption(f"✓ **{name}** — built from your manual plan")
        st.download_button(
            "Download this input file",
            data=st.session_state[S.SITES_BYTES],
            file_name=name,
            mime=XLSX_MIME,
            key="sb_download_derived",
            help="The input file derived from your Master Planner, so you can "
                 "keep it, check it, or edit it outside the tool.",
        )
        sheets_note = st.session_state.get(S.SITES_SHEETS) or []
        if sheets_note:
            st.caption(
                "Its **Site_Mapping** sheet lists each code against the Master "
                "Planner column it came from."
            )

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
    if not st.session_state.get(S.SITES_FROM_PLANNER):
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

    data = _newly_uploaded(uploaded, S.MP_FP)
    if data is not None:
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


def _derive_section(conversion_service, settings) -> None:
    """Build the sites input file from the Master Planner, on request.

    Deliberately a button rather than automatic. Deriving models each site as a
    fixed repeating interval from one start week, which is lumpier than a sheet the
    team maintains by hand — on the reference workbook the derived demand has a
    standard deviation of 9.5 against 7.2, with five weeks under ten generators.
    Substituting it silently would change the plan without the planner knowing why.
    """
    if not planner_loaded():
        return

    st.divider()
    st.markdown("**Or build the sites file from the plan**")

    if isinstance(settings, ValidationError):
        st.caption("Fix the settings errors first.")
        return
    if conversion_service is None:
        return

    already = st.session_state.get(S.SITES_FROM_PLANNER)
    label = "Rebuild sites from the manual plan" if already else \
            "Derive sites from the manual plan"
    st.caption(
        "Reads your Master Planner and writes the input file the optimizer needs, "
        "so you can plan without maintaining a separate sites file. Replaces "
        "whatever is loaded above."
    )

    if st.button(label, key="sb_derive"):
        with st.spinner("Reading the manual plan..."):
            try:
                result = conversion_service.run(ConversionRequest(
                    master_planner_bytes=st.session_state[S.MP_BYTES],
                    master_planner_sheet=planner_sheet(),
                    horizon_weeks=settings.params.horizon_weeks,
                    reference_week_date=settings.reference_week_date,
                ))
            except (RubyFillError, ValueError) as exc:
                st.error(f"Could not build the input file: {exc}")
                return

        st.session_state[S.SITES_BYTES] = result.xlsx_bytes
        st.session_state[S.SITES_NAME] = DERIVED_FILENAME
        st.session_state[S.SITES_SHEETS] = ["Sites", "Site_Mapping",
                                            "Conversion_Notes"]
        st.session_state[S.SITES_SHEET] = "Sites"
        st.session_state[S.SITES_FROM_PLANNER] = True
        # The uploader's fingerprint is deliberately left alone. Clearing it would
        # make the file still sitting in the uploader look new on the rerun below,
        # and it would immediately overwrite what was just derived.
        # The sheet dropdown owns its own widget state and would otherwise
        # reassert a sheet name belonging to the previous file.
        st.session_state.pop("sb_sites_sheet", None)
        S.clear_results(st.session_state)
        st.session_state[S.SIDEBAR_FLASH] = (
            f"Built from {result.active_count} active site(s). Open the Cost "
            f"Optimizer and press Run optimizer."
        )
        # Rerun so the sections above — which have already rendered this pass —
        # pick up the new file. Without it the download button for the derived
        # file would not appear until the planner touched something else.
        st.rerun()


def render(conversion_service=None, settings=None) -> None:
    """Draw the shared file loader. Call once; the sidebar renders in place."""
    with st.sidebar:
        st.header("Files")
        st.caption(
            "Load each file once here. Every tab uses what is loaded, so there is "
            "nothing to upload again."
        )
        flash = st.session_state.pop(S.SIDEBAR_FLASH, None)
        if flash:
            st.success(flash)
        _sites_section()
        st.divider()
        _planner_section()
        _derive_section(conversion_service, settings)

        if st.session_state.get(S.SITES_BYTES) or st.session_state.get(S.MP_BYTES):
            st.divider()
            if st.button("Clear loaded files", key="sb_clear",
                         help="Removes both files and every result derived "
                              "from them."):
                for key in (S.SITES_BYTES, S.SITES_NAME, S.SITES_SHEETS,
                            S.SITES_SHEET, S.SITES_FROM_PLANNER, S.SITES_FP,
                            S.MP_BYTES, S.MP_NAME, S.MP_SHEETS, S.MP_SHEET,
                            S.MP_FP):
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
