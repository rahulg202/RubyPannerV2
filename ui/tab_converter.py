"""Presentation: the Import Manual Plan tab.

Turns the team's wide Master Planner into the optimizer's input sheet, so nobody
has to retype site IDs, cadences and start weeks. Every site gets a code that is
also written into the mapping sheet, which is what lets the Comparison tab match
the manual plan to the optimized plan afterwards.
"""

from __future__ import annotations

import io

import pandas as pd
import streamlit as st

from domain.errors import RubyFillError, ValidationError
from services.dtos import ConversionRequest
from ui import state as S
from ui.formatting import XLSX_MIME

GENERATED_FILENAME = "sites_from_manual_plan.xlsx"


def _upload_master_planner() -> str:
    """Master Planner uploader, sharing session state with the other tabs."""
    uploaded = st.file_uploader(
        "Upload the Master Planner workbook",
        type=["xlsx", "xls"],
        key="conv_mp_uploader",
        help="The same workbook your team plans in by hand.",
    )
    if uploaded is not None:
        data = uploaded.read()
        st.session_state[S.MP_BYTES] = data
        st.session_state[S.MP_NAME] = uploaded.name
        st.session_state[S.CONV_RESULT] = None
        try:
            sheets = pd.ExcelFile(io.BytesIO(data)).sheet_names
            st.session_state[S.MP_SHEETS] = sheets
            st.success(f"Loaded **{uploaded.name}** ({len(sheets)} sheet(s))")
        except Exception as exc:
            st.session_state[S.MP_SHEETS] = None
            st.error(f"Could not read that workbook: {exc}")
    elif st.session_state.get(S.MP_NAME):
        st.caption(f"Using **{st.session_state[S.MP_NAME]}**, already uploaded.")

    sheets = st.session_state.get(S.MP_SHEETS)
    if sheets:
        index = sheets.index("Schedule") if "Schedule" in sheets else 0
        choice = st.selectbox(
            "Schedule sheet", options=sheets, index=index, key="conv_sheet_select"
        )
        st.session_state[S.MP_SHEET] = choice
        return choice
    return st.session_state.get(S.MP_SHEET) or "Schedule"


def _year_override() -> int | None:
    """Optional year picker. Blank means let the tool choose."""
    with st.expander("Advanced", expanded=False):
        st.caption(
            "Week numbers repeat every year in the Master Planner, so one year "
            "has to be chosen. Left blank, the tool picks the year with the most "
            "fully-planned weeks."
        )
        raw = st.text_input(
            "Year to read (optional)", value="", key="conv_year",
            placeholder="e.g. 2026",
        )
    text = str(raw or "").strip()
    if not text:
        return None
    if not text.isdigit():
        st.warning(f"'{text}' is not a year. Choosing the year automatically.")
        return None
    return int(text)


def _render_metrics(result) -> None:
    cols = st.columns(4)
    cols[0].metric("Sites found", result.site_count)
    cols[1].metric(
        "Active", result.active_count,
        help="Sites with at least one generator scheduled in the year read.",
    )
    cols[2].metric(
        "EU-restricted", result.eu_restricted_count,
        help="Shaded dark blue in the Master Planner — these must ship from Curium.",
    )
    cols[3].metric(
        "Codes generated", result.generated_code_count,
        help="Columns with no account number in the header; the tool assigned a "
             "stable code instead.",
    )

    st.caption(
        f"Read from the **{result.year}** rows. The manual plan schedules "
        f"**{result.scheduled_deliveries}** deliveries that year; the cadences "
        f"derived here imply **{result.implied_deliveries}**. These should be "
        f"close — the manual plan shifts individual weeks for holidays, while the "
        f"optimizer works from a repeating interval."
    )


def _render_result(result) -> None:
    st.divider()
    st.markdown("### Generated input file")

    for warning in result.warnings:
        st.warning(warning)

    _render_metrics(result)

    st.download_button(
        "Download input file",
        data=result.xlsx_bytes,
        file_name=GENERATED_FILENAME,
        mime=XLSX_MIME,
        key="conv_download",
        type="primary",
    )
    if st.button("Use it in the Cost Optimizer now", key="conv_use"):
        st.session_state[S.SITES_BYTES] = result.xlsx_bytes
        st.session_state[S.SITES_NAME] = GENERATED_FILENAME
        st.session_state[S.SITES_SHEETS] = ["Sites", "Site_Mapping", "Conversion_Notes"]
        st.session_state[S.SITES_SHEET] = "Sites"
        st.session_state[S.OPT_RESULT] = None
        st.session_state[S.CMP_RESULT] = None
        st.success(
            "Loaded into the Cost Optimizer tab. Open it and press Run optimizer."
        )

    with st.expander(f"Sites ({len(result.sites_df)})", expanded=True):
        st.caption(
            "This is the sheet the optimizer reads. **Next_Demand_Week** is the "
            "first week the manual plan schedules that site; **Interval_Weeks** is "
            "how often it repeats. An interval of 0 means a single delivery."
        )
        st.dataframe(result.sites_df, width="stretch", hide_index=True)

    with st.expander("Site code mapping — share this with the planning team"):
        st.caption(
            "Each site's code next to the Master Planner column it came from. Add "
            "these codes to the Master Planner headers and both files stay linked, "
            "so the Comparison tab can match them without any manual work."
        )
        st.dataframe(result.mapping_df, width="stretch", hide_index=True)

    label = f"Things to check ({len(result.notes_df)})"
    with st.expander(label, expanded=False):
        if result.notes_df.empty:
            st.success("Nothing to flag — every column converted cleanly.")
        else:
            st.caption(
                "None of these stop the optimizer running. They are places where "
                "the Master Planner was ambiguous and the tool had to make a call."
            )
            st.dataframe(result.notes_df, width="stretch", hide_index=True)

    if result.issues_df is not None and not result.issues_df.empty:
        with st.expander(
            f"Rows the optimizer would skip ({len(result.issues_df)})", expanded=True
        ):
            st.dataframe(result.issues_df, width="stretch", hide_index=True)


def render(conversion_service, settings_or_error) -> None:
    """Draw the Import Manual Plan tab."""
    st.subheader("Import Manual Plan")
    st.caption(
        "Build the optimizer's input file straight from your Master Planner — no "
        "retyping site IDs or intervals."
    )
    st.info(
        "**How it works.** Your Master Planner has a column per site and a `1` in "
        "every week that site is due a generator. This reads those columns and "
        "writes one row per site: its code, its first delivery week, how often it "
        "repeats, its country, and whether it is EU-restricted. Each site also gets "
        "a code recorded against its Master Planner column, which is what lets the "
        "Comparison tab line the two plans up."
    )

    sheet = _upload_master_planner()
    year = _year_override()

    if isinstance(settings_or_error, ValidationError):
        st.error("Fix these settings before converting:")
        for message in settings_or_error.errors:
            st.write(f"- {message}")
        return
    settings = settings_or_error

    have_mp = st.session_state.get(S.MP_BYTES) is not None
    if not have_mp:
        st.info("Upload the Master Planner workbook to begin.")

    if st.button("Build input file", type="primary", key="conv_run",
                 disabled=not have_mp):
        request = ConversionRequest(
            master_planner_bytes=st.session_state[S.MP_BYTES],
            master_planner_sheet=sheet,
            horizon_weeks=settings.params.horizon_weeks,
            master_planner_year=year,
        )
        with st.spinner("Reading the manual plan and deriving sites..."):
            try:
                st.session_state[S.CONV_RESULT] = conversion_service.run(request)
            except RubyFillError as exc:
                st.error(str(exc))
                return
            except ValueError as exc:
                st.error(f"Could not read the Master Planner: {exc}")
                return

    result = st.session_state.get(S.CONV_RESULT)
    if result is not None:
        _render_result(result)
