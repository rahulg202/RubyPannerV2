"""Presentation: the Comparison tab.

Compares the planning team's manual plan (read from the Master Planner workbook)
against the optimized plan, component by component.
"""

from __future__ import annotations

import io

import pandas as pd
import streamlit as st

from domain.demand import build_weekly_demand
from domain.errors import RubyFillError, ValidationError
from domain.site_matching import STATUS_MATCHED, match_report_frame
from services.dtos import ComparisonRequest
from ui import state as S
from ui.formatting import comparison_frame, usd, usd_signed


def _upload_master_planner() -> str:
    uploaded = st.file_uploader(
        "Upload the Master Planner workbook",
        type=["xlsx", "xls"],
        key="cmp_mp_uploader",
        help="The manual plan is read from its Schedule sheet.",
    )
    if uploaded is not None:
        data = uploaded.read()
        st.session_state[S.MP_BYTES] = data
        st.session_state[S.MP_NAME] = uploaded.name
        try:
            sheets = pd.ExcelFile(io.BytesIO(data)).sheet_names
            st.session_state[S.MP_SHEETS] = sheets
            st.success(f"Loaded **{uploaded.name}** ({len(sheets)} sheet(s))")
        except Exception as exc:
            st.session_state[S.MP_SHEETS] = None
            st.error(f"Could not read that workbook: {exc}")

    sheets = st.session_state.get(S.MP_SHEETS)
    if sheets:
        index = sheets.index("Schedule") if "Schedule" in sheets else 0
        choice = st.selectbox(
            "Schedule sheet", options=sheets, index=index, key="cmp_sheet_select"
        )
        st.session_state[S.MP_SHEET] = choice
        return choice
    return st.session_state.get(S.MP_SHEET) or "Schedule"


def _render_match_report(result) -> None:
    """Row-level reconciliation between the input file and the manual plan."""
    if not result.match_report:
        return

    summary = result.match_summary or {}
    matched = summary.get("matched", 0)
    input_rows = summary.get("input_rows", 0)
    missing_manual = summary.get("missing_from_manual_plan", 0)
    missing_input = summary.get("missing_from_input_file", 0)
    rate = summary.get("match_rate", 0.0)

    label = f"Site match report — {matched} of {input_rows} matched ({rate:.0%})"
    if missing_manual or missing_input:
        label += f", {missing_manual + missing_input} to review"

    with st.expander(label, expanded=bool(missing_manual)):
        cols = st.columns(3)
        cols[0].metric("Matched", f"{matched} / {input_rows}")
        cols[1].metric(
            "Not in the manual plan", missing_manual,
            help="Input rows with no Master Planner column. Their per-customer "
                 "figures cannot be compared.",
        )
        cols[2].metric(
            "Not in the input file", missing_input,
            help="Master Planner columns with no active input row. Their "
                 "generators are missing from the optimized plan.",
        )

        if matched == 0 and input_rows:
            st.error(
                "Nothing matched. The two files use different Site_ID "
                "conventions, so every per-customer comparison is meaningless. "
                "Generate the input file from **Import Manual Plan** and both "
                "files will share the same codes."
            )
        elif missing_manual or missing_input:
            st.caption(
                "Each row below names the problem and how to fix it. Suggested "
                "matches are based on the site name and are **suggestions only** "
                "— nothing is matched automatically, because a wrong automatic "
                "match would hide the discrepancy rather than show it."
            )
        else:
            st.success("Every input row matched a Master Planner column.")

        view = match_report_frame(result.match_report)
        show_all = st.checkbox(
            "Include matched rows", value=False, key="cmp_match_show_all"
        )
        if not show_all:
            view = view[view["Status"] != STATUS_MATCHED]
        if view.empty:
            st.info("No rows to review.")
        else:
            st.dataframe(view, width="stretch", hide_index=True)


def _render_result(result) -> None:
    st.divider()
    st.markdown("### Manual plan vs optimized")

    for warning in result.warnings:
        st.warning(warning)

    total = next(
        (c for c in result.components if c["Component"] == "Total Composite"), None
    )
    penalty = next((c for c in result.components if c["Component"] == "Penalty"), None)
    if total and penalty:
        cols = st.columns(3)
        cols[0].metric("Total saving", usd_signed(total["Saving_Abs"]))
        cols[1].metric("Penalty saving", usd_signed(penalty["Saving_Abs"]))
        cols[2].metric(
            "Overtime weeks",
            f"{result.overtime_optimized} vs {result.overtime_baseline}",
            help="Optimized versus manual plan.",
        )

    st.dataframe(
        comparison_frame(result.components), width="stretch", hide_index=True
    )
    st.caption(
        "Penalty and overtime are where the optimizer earns its keep. The capacity "
        "component is largely a fixed floor set by demand versus factory capacity, "
        "so it moves little between plans and can even worsen slightly."
    )

    if result.baseline and result.baseline.capacity_violations:
        with st.expander(
            f"Manual plan capacity violations "
            f"({len(result.baseline.capacity_violations)})"
        ):
            st.dataframe(
                pd.DataFrame(
                    result.baseline.capacity_violations,
                    columns=["Week", "Planned", "Weekly capacity"],
                ),
                width="stretch", hide_index=True,
            )

    if result.weekly_comparison is not None:
        with st.expander("Week-by-week production", expanded=False):
            st.dataframe(
                result.weekly_comparison, width="stretch", hide_index=True
            )
            chart = result.weekly_comparison.set_index("Week")[
                ["Manual_Production", "Optimized_Production"]
            ]
            st.line_chart(chart)

    _render_match_report(result)

    if result.assigned_ids:
        with st.expander(
            f"Generated identifiers for unnumbered customers "
            f"({len(result.assigned_ids)})"
        ):
            st.caption(
                "These Master Planner columns had no account number. Share this "
                "mapping with the planning team so the same identifiers can be "
                "used as Site_ID in future input sheets."
            )
            st.dataframe(
                pd.DataFrame([
                    {
                        "Generated_ID": a.generated_id,
                        "Customer_Name": a.customer_name,
                        "Master_Planner_Header": a.column_header,
                    }
                    for a in result.assigned_ids
                ]),
                width="stretch", hide_index=True,
            )


def render(comparison_service, settings_or_error) -> None:
    """Draw the Comparison tab."""
    st.subheader("Comparison")
    st.caption(
        "See what the optimizer saved against the plan your team built by hand."
    )

    sheet = _upload_master_planner()

    if isinstance(settings_or_error, ValidationError):
        st.error("Fix these settings before running:")
        for message in settings_or_error.errors:
            st.write(f"- {message}")
        return
    settings = settings_or_error

    if settings.reference_week_date is not None:
        st.caption(
            f"Comparing from **week 1 = {settings.reference_week_date:%d %b %Y}**, "
            f"the reference week set in Settings. The manual plan is read from that "
            f"week forward, so both plans line up on the same weeks."
        )
    else:
        st.caption(
            "No reference week set, so the manual plan is read by calendar year "
            "and week 1 is whatever the Master Planner calls week 1. Set a "
            "reference week in **Settings** to compare from a chosen starting "
            "point."
        )

    opt_result = st.session_state.get(S.OPT_RESULT)
    have_mp = st.session_state.get(S.MP_BYTES) is not None

    if opt_result is None:
        st.info("Run the Cost Optimizer first — the comparison needs its result.")
    if not have_mp:
        st.info("Upload the Master Planner workbook to compare against.")

    ready = opt_result is not None and have_mp
    if st.button("Run comparison", type="primary", key="cmp_run", disabled=not ready):
        demand = build_weekly_demand(opt_result.active_df, settings.params)
        active = opt_result.active_df
        has_name = "site_name" in active.columns
        active_sites = tuple(
            (str(r["site_id"]).strip(), str(r["site_name"]) if has_name else "")
            for _, r in active.iterrows()
        )
        request = ComparisonRequest(
            master_planner_bytes=st.session_state[S.MP_BYTES],
            master_planner_sheet=sheet,
            optimized_summary=opt_result.summary,
            optimized_plan_df=opt_result.plan_df,
            demand=tuple(demand),
            params=settings.params,
            shutdown_weeks=settings.shutdown_weeks,
            partial_shutdown_weeks=settings.partial_shutdown_weeks,
            reference_week_date=settings.reference_week_date,
            active_sites=active_sites,
        )
        with st.spinner("Reading the Master Planner and evaluating the manual plan..."):
            try:
                st.session_state[S.CMP_RESULT] = comparison_service.run(request)
            except RubyFillError as exc:
                st.error(str(exc))
                return
            except ValueError as exc:
                st.error(f"Could not parse the Master Planner: {exc}")
                return

    result = st.session_state.get(S.CMP_RESULT)
    if result is not None:
        _render_result(result)
