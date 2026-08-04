"""Presentation: the Cost Optimizer tab.

Uploads the sites file, runs the optimizer service, and renders the plan with
calendar dates, supplier allocation, quota status, and changed customer weeks.
Contains no business logic — everything comes from the service result.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from domain.dates import current_planning_week
from domain.errors import InfeasiblePlanError, RubyFillError, ValidationError
from services.dtos import OptimizeRequest
from ui import state as S
from ui.formatting import (
    XLSX_MIME,
    add_week_dates,
    changed_weeks_frame,
    mark_current_week,
    quota_frame,
    usd,
)


def _upload_sites() -> None:
    """Sites file uploader; stores bytes and sheet names in session state."""
    uploaded = st.file_uploader(
        "Upload your sites file",
        type=["xlsx", "xls", "csv"],
        key="opt_sites_uploader",
        help="Columns required: Site_ID, Active, Next_Demand_Week, Interval_Weeks, Country.",
    )
    if uploaded is None:
        return

    data = uploaded.read()
    st.session_state[S.SITES_BYTES] = data
    st.session_state[S.SITES_NAME] = uploaded.name

    if uploaded.name.lower().endswith(".csv"):
        st.session_state[S.SITES_SHEETS] = None
        st.session_state[S.SITES_SHEET] = "Sites"
        st.success(f"Loaded **{uploaded.name}** (CSV)")
        return

    try:
        import io
        sheets = pd.ExcelFile(io.BytesIO(data)).sheet_names
        st.session_state[S.SITES_SHEETS] = sheets
        st.success(f"Loaded **{uploaded.name}** ({len(sheets)} sheet(s))")
    except Exception as exc:
        st.session_state[S.SITES_SHEETS] = None
        st.error(f"Could not read that workbook: {exc}")


def _sheet_selector() -> str:
    sheets = st.session_state.get(S.SITES_SHEETS)
    if not sheets:
        return st.session_state.get(S.SITES_SHEET) or "Sites"
    index = sheets.index("Sites") if "Sites" in sheets else 0
    choice = st.selectbox(
        "Sites sheet", options=sheets, index=index, key="opt_sheet_select"
    )
    st.session_state[S.SITES_SHEET] = choice
    return choice


def _upload_manual_plan() -> None:
    """Optional Master Planner upload, used to compare customer weeks."""
    with st.expander("Manual plan (optional, enables changed customer weeks)",
                     expanded=False):
        st.caption(
            "Upload the Master Planner workbook to compare the optimizer's "
            "production weeks against the plan your team built by hand. Without "
            "it the plan still runs, but changed customer weeks cannot be shown."
        )
        uploaded = st.file_uploader(
            "Master Planner workbook",
            type=["xlsx", "xls"],
            key="opt_mp_uploader",
        )
        if uploaded is not None:
            import io as _io
            data = uploaded.read()
            st.session_state[S.MP_BYTES] = data
            st.session_state[S.MP_NAME] = uploaded.name
            try:
                sheets = pd.ExcelFile(_io.BytesIO(data)).sheet_names
                st.session_state[S.MP_SHEETS] = sheets
                st.success(f"Loaded **{uploaded.name}**")
            except Exception as exc:
                st.session_state[S.MP_SHEETS] = None
                st.error(f"Could not read that workbook: {exc}")

        sheets = st.session_state.get(S.MP_SHEETS)
        if sheets:
            index = sheets.index("Schedule") if "Schedule" in sheets else 0
            st.session_state[S.MP_SHEET] = st.selectbox(
                "Schedule sheet", options=sheets, index=index,
                key="opt_mp_sheet_select",
            )
        elif st.session_state.get(S.MP_BYTES) is None:
            st.info("No manual plan uploaded.")


def _render_metrics(summary: dict) -> None:
    cols = st.columns(5)
    cols[0].metric("Total cost", usd(summary.get("total_composite_cost")))
    cols[1].metric("Penalty", usd(summary.get("total_penalty_cost")))
    cols[2].metric("Overtime", usd(summary.get("total_overtime_cost")))
    cols[3].metric("Capacity", usd(summary.get("total_capacity_cost")))
    cols[4].metric("Overtime weeks", summary.get("overtime_weeks", 0))
    if "total_quota_penalty_cost" in summary:
        st.metric("Supplier quota penalty", usd(summary["total_quota_penalty_cost"]))


def _render_plan(result, settings) -> None:
    st.markdown("**Weekly production plan**")
    plan = add_week_dates(result.plan_df, result.week_dates)
    cur = None
    if settings.reference_week_date is not None:
        cur = current_planning_week(
            settings.reference_week_date, date.today(), settings.params.horizon_weeks
        )
        if cur is not None:
            st.caption(f"Today falls in planning week {cur}.")
        plan = mark_current_week(plan, cur)
    st.dataframe(plan, use_container_width=True, hide_index=True)


def _render_quota(result) -> None:
    if not result.quota_status:
        return
    penalised = [q for q in result.quota_status if q.penalty_usd > 0]
    partial = [q for q in result.quota_status if q.is_partial]

    label = "Supplier quota status"
    if penalised:
        label += f" — {len(penalised)} quarter(s) short"
    elif partial:
        label += f" — {len({q.quarter for q in partial})} partial quarter(s)"

    with st.expander(label, expanded=bool(penalised)):
        if penalised:
            st.warning(
                "One or more fully-covered supplier quarters fall below the "
                "minimum order quota. That shortfall penalty is included in the "
                "total cost."
            )

        note = result.summary.get("partial_quarter_note")
        if note:
            st.info(note)

        st.dataframe(quota_frame(result.quota_status), use_container_width=True,
                     hide_index=True)

        st.caption(
            "**Quota** = the supplier's minimum for a full quarter. "
            "**Target** = what to expect over the weeks this plan covers. "
            "**Ordered** = Sr-82 the plan actually buys. "
            "**Gap** = how far Ordered falls below Target."
        )

        if partial:
            with st.expander("What does “Partial — not penalised” mean?"):
                st.markdown(
                    "**What's happening.** Your suppliers set a minimum order per "
                    "calendar quarter. This plan runs for 52 weeks, but it doesn't "
                    "start on the first week of a quarter — so the first and last "
                    "quarters are only *partly* inside the plan.\n\n"
                    "**Why they aren't charged.** The weeks missing from those "
                    "quarters are real, just invisible here:\n\n"
                    "- The **first** quarter is missing earlier weeks. You already "
                    "placed those orders — that history sits in SAP, not in this "
                    "planner.\n"
                    "- The **last** quarter is missing later weeks, beyond week 52. "
                    "You'll keep ordering in them.\n\n"
                    "Either way the planner can only see part of the quarter, so it "
                    "cannot tell whether you met the minimum. Charging a full "
                    "quarter's minimum against a few weeks would invent a shortfall "
                    "that doesn't exist — and because the shortfall charge is very "
                    "high by design, that made-up number would swamp every real "
                    "cost and push the plan around for no good reason.\n\n"
                    "**How to read these rows.** Treat them as a *run-rate check*, "
                    "not a pass or fail. The **Target** column scales the quarterly "
                    "minimum down to the weeks actually covered, so you can see "
                    "whether ordering is tracking at roughly the right pace. A gap "
                    "here is worth a glance, not an alarm.\n\n"
                    "**To remove them entirely,** set the reference week in Settings "
                    "to the first week of a quarter. The 52 weeks then line up with "
                    "four complete quarters and every one is fully checked."
                )


def _render_changed_weeks(result) -> None:
    if not result.assignments:
        return
    cs = result.change_summary

    # Without a manual plan there is no comparison to show.
    if not cs.get("compared"):
        with st.expander("Changed customer weeks — not available", expanded=False):
            st.info(
                "Changed weeks are measured against your manual plan. Upload the "
                "Master Planner workbook below (or in the Comparison tab) and "
                "re-run to see which customers the optimizer moved."
            )
        return

    with st.expander(
        f"Changed customer weeks vs manual plan — {cs.get('early', 0)} earlier, "
        f"{cs.get('late', 0)} later of {cs.get('total', 0)}",
        expanded=False,
    ):
        cols = st.columns(4)
        cols[0].metric("Same week as manual", cs.get("unchanged", 0))
        cols[1].metric("Moved earlier", cs.get("early", 0))
        cols[2].metric("Moved later", cs.get("late", 0))
        cols[3].metric("New customers", cs.get("new_customers", 0))
        st.caption(
            "Each row compares the week your manual plan produced a customer's "
            "generator against the week the optimizer produces it. Differences are "
            "either an improvement the optimizer found or a mistake in the manual "
            "schedule worth checking."
        )
        if cs.get("uncomparable"):
            st.caption(
                f"{cs['uncomparable']} generator(s) had no counterpart in the "
                "manual plan (the optimizer schedules more for that site), so they "
                "are listed without a shift."
            )

        df = changed_weeks_frame(result.assignments)
        col1, col2, col3 = st.columns(3)
        with col1:
            shift_filter = st.multiselect(
                "Change", options=["Moved earlier", "Moved later",
                                   "Same as manual", "New customer",
                                   "No counterpart"],
                default=["Moved earlier", "Moved later"], key="cw_shift_filter",
            )
        with col2:
            countries = sorted(c for c in df["Country"].unique() if c)
            country_filter = st.multiselect(
                "Country", options=countries, default=[], key="cw_country_filter"
            )
        with col3:
            min_mag = st.number_input(
                "Min |shift| weeks", min_value=0, value=0, step=1, key="cw_min_shift"
            )

        view = df
        if shift_filter:
            view = view[view["Shift"].isin(shift_filter)]
        if country_filter:
            view = view[view["Country"].isin(country_filter)]
        if min_mag:
            view = view[view["Week_Shift"].abs().fillna(0) >= min_mag]

        st.caption(f"{len(view)} of {len(df)} generators shown.")
        st.dataframe(view, use_container_width=True, hide_index=True)


def render(optimizer_service, settings_or_error) -> None:
    """Draw the Cost Optimizer tab.

    ``settings_or_error`` is either a validated ``Settings`` or a ``ValidationError``
    raised while assembling it — the tab surfaces the error rather than running.
    """
    st.subheader("Cost Optimizer")
    st.caption("Upload your sites file and run the optimizer.")

    _upload_sites()
    sheet = _sheet_selector()
    _upload_manual_plan()

    if isinstance(settings_or_error, ValidationError):
        st.error("Fix these settings before running:")
        for message in settings_or_error.errors:
            st.write(f"- {message}")
        return
    settings = settings_or_error

    have_file = st.session_state.get(S.SITES_BYTES) is not None
    if not have_file:
        st.info("Upload a sites file to get started.")

    if st.button("Run optimizer", type="primary", key="opt_run", disabled=not have_file):
        request = OptimizeRequest(
            file_bytes=st.session_state[S.SITES_BYTES],
            filename=st.session_state[S.SITES_NAME],
            sheet=sheet,
            params=settings.params,
            supplier_params=settings.supplier_params,
            shutdown_weeks=settings.shutdown_weeks,
            partial_shutdown_weeks=settings.partial_shutdown_weeks,
            reference_week_date=settings.reference_week_date,
            calibration_offset_days=settings.calibration_offset_days,
            master_planner_bytes=st.session_state.get(S.MP_BYTES),
            master_planner_sheet=st.session_state.get(S.MP_SHEET) or "Schedule",
        )
        with st.spinner("Optimizing 52 weeks of production..."):
            try:
                st.session_state[S.OPT_RESULT] = optimizer_service.run(request)
                st.session_state[S.CMP_RESULT] = None  # comparison must be re-run
            except InfeasiblePlanError as exc:
                st.error(f"No feasible plan: {exc}")
                return
            except RubyFillError as exc:
                st.error(f"Could not complete the run: {exc}")
                return
            except ValueError as exc:
                st.error(f"Input problem: {exc}")
                return

    result = st.session_state.get(S.OPT_RESULT)
    if result is None:
        return

    st.divider()
    st.markdown("### Results")
    for warning in result.warnings:
        st.warning(warning)

    _render_metrics(result.summary)
    _render_plan(result, settings)
    _render_quota(result)
    _render_changed_weeks(result)

    with st.expander(f"Data quality issues ({len(result.issues_df)})", expanded=False):
        if result.issues_df.empty:
            st.info("No data quality issues found.")
        else:
            st.dataframe(result.issues_df, use_container_width=True, hide_index=True)

    if result.xlsx_bytes:
        st.download_button(
            "Download results workbook",
            data=result.xlsx_bytes,
            file_name="ruby_fill_plan.xlsx",
            mime=XLSX_MIME,
            key="opt_download",
        )
