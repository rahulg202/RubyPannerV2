"""Presentation: the Cost Optimizer tab.

Uploads the sites file, runs the optimizer service, and renders the plan with
calendar dates, supplier allocation, quota status, and changed customer weeks.
Contains no business logic — everything comes from the service result.
"""

from __future__ import annotations

from datetime import date

import streamlit as st

from domain.dates import current_planning_week
from domain.errors import InfeasiblePlanError, RubyFillError, ValidationError
from services.dtos import OptimizeRequest
from ui import sidebar
from ui import state as S
from ui.formatting import (
    XLSX_MIME,
    add_week_dates,
    changed_weeks_frame,
    mark_current_week,
    quota_frame,
    usd,
    usd_md,
)


def _render_stale_banner(result, settings) -> None:
    """Say so when the displayed plan predates the current settings.

    Settings changes do not clear a stored result — only uploading a file or
    restoring defaults does. Without this the tab keeps presenting a plan solved
    under different cost rates as though it were current.
    """
    stamped = getattr(result, "settings_fingerprint", "")
    if not stamped or stamped == settings.fingerprint:
        return
    st.warning(
        "**Settings have changed since this plan was solved.** The figures below "
        "still reflect the earlier settings. Run the optimizer again to bring them "
        "up to date — the Comparison tab will refuse to use a plan from a "
        "different set of settings."
    )


def _render_metrics(summary: dict) -> None:
    cols = st.columns(5)
    cols[0].metric(
        "Optimized cost", usd(summary.get("total_composite_cost")),
        help="Penalty + overtime + capacity: the objective the solver minimised. "
             "The supplier quota advisory is reported separately because the "
             "solver could not act on it.",
    )
    cols[1].metric("Penalty", usd(summary.get("total_penalty_cost")))
    cols[2].metric("Overtime", usd(summary.get("total_overtime_cost")))
    cols[3].metric(
        "Unused capacity", usd(summary.get("total_capacity_cost")),
        help="Cost of production slots the plan did not fill. Broken into its two "
             "parts below — most of it is arithmetic you cannot change.",
    )
    cols[4].metric("Overtime weeks", summary.get("overtime_weeks", 0))

    # Split the unused-capacity figure. The bulk of it is fixed by demand versus
    # factory size and no plan can move it; what is left is charged per overtime
    # generator and is what actually decides overtime versus holding stock. Lumping
    # the two together is how the overtime lever stayed hidden.
    if "total_capacity_floor_cost" in summary:
        surcharge = summary.get("total_capacity_levelling_cost", 0.0)
        split = st.columns(2)
        split[0].metric(
            "…of which unavoidable",
            usd(summary["total_capacity_floor_cost"]),
            help="Your customers order less than the factory can make, so some "
                 "slots sit empty every year no matter how the plan is arranged. "
                 "This figure is the same for every possible plan. It is not a "
                 "planning failure and the optimizer cannot reduce it.",
        )
        split[1].metric(
            "…of which overtime surcharge", usd(surcharge),
            help="Generators made on a 3rd batch do not use up your standing "
                 "2-batch capacity, so each one leaves a normal slot empty "
                 "elsewhere in the year — and that empty slot is charged. This is "
                 "the extra cost of overtime on top of the overtime rate, and it "
                 "is the only part of unused capacity that the plan can change.",
        )

    _render_utilisation(summary)


def _render_utilisation(summary: dict) -> None:
    """How full the factory was, in slots rather than money.

    Deliberately independent of every cost rate. "How much capacity did we lose"
    is a fact about the factory; "what is that worth" is a pricing judgement. Tying
    the two together meant a planner could not see utilisation without first
    agreeing a price for it — and meant editing a rate appeared to change how busy
    the factory had been.
    """
    if "total_idle_slots" not in summary:
        return

    idle = summary["total_idle_slots"]
    unavoidable = summary.get("unavoidable_idle_slots", 0)
    forced = summary.get("forced_overtime_units", 0)
    discretionary = summary.get("discretionary_overtime_units", 0)
    nominal = summary.get("nominal_slots", 0)

    st.markdown("**Factory utilisation** (slot counts, unaffected by cost settings)")
    cols = st.columns(3)
    cols[0].metric(
        "Idle slot-weeks", f"{idle:,}",
        help="Standing production slots that went unfilled across the horizon, "
             "counting a normal week as 30 slots.",
    )

    # Either demand sits below standing capacity or above it. Never both, so the
    # middle metric switches rather than showing one of them as a negative — which
    # is what it used to do, reporting "-20 idle slot-weeks fixed by demand".
    if forced:
        cols[1].metric(
            "Overtime forced by demand", f"{forced:,}",
            help=f"Demand ({summary.get('produced', nominal + forced):,}) exceeds "
                 f"standing capacity ({nominal:,} slots), so this many generators "
                 f"must come from a 3rd batch. No plan can avoid them.",
        )
    else:
        cols[1].metric(
            "Idle fixed by demand", f"{unavoidable:,}",
            help="Slots empty purely because customers order less than the factory "
                 "can make. No plan can reduce this — filling a slot needs an "
                 "order, not a better schedule.",
        )
    cols[2].metric(
        "Overtime by choice", f"{discretionary:,}",
        help="Overtime beyond the forced minimum. Each unit leaves one standing "
             "slot empty elsewhere, so this is the only part of the idle total "
             "that responds to the plan.",
    )

    if forced:
        st.caption(
            f"**Demand exceeds standing capacity.** You need "
            f"{nominal + forced:,} generators against {nominal:,} standing slots, "
            f"so at least {forced:,} must come from overtime whatever the plan. "
            f"The {discretionary:,} further overtime unit(s) were a choice, and "
            f"they are what leaves {idle:,} standing slot-week(s) empty elsewhere."
        )
    elif discretionary == 0 and idle:
        st.caption(
            f"All {idle:,} idle slot-weeks are fixed by demand versus factory size. "
            f"**No plan can improve on this**, whatever the cost settings say — "
            f"filling a slot needs an order, not a better schedule."
        )
    elif discretionary:
        st.caption(
            f"Of {idle:,} idle slot-weeks, {unavoidable:,} are fixed by demand and "
            f"cannot be improved. Only the {discretionary:,} caused by chosen "
            f"overtime respond to the plan — and removing them means holding stock "
            f"earlier instead."
        )

    if "total_quota_penalty_cost" in summary:
        st.metric(
            "Supplier quota shortfall (advisory)",
            usd(summary["total_quota_penalty_cost"]),
            help=summary.get("quota_advisory_note", ""),
        )

    _render_overtime_explainer(summary)


def _render_overtime_explainer(summary: dict) -> None:
    """Explain the overtime-versus-hold-stock trade in plain terms.

    This is the choice the plan turns on whenever demand is uneven, and until the
    unused-capacity figure was split it was invisible: the deciding number sat
    inside a line the guide told planners to ignore as a fixed floor.
    """
    if "total_capacity_floor_cost" not in summary:
        return

    surcharge = summary.get("total_capacity_levelling_cost", 0.0)
    overtime_weeks = summary.get("overtime_weeks", 0)
    holding = summary.get("total_penalty_cost", 0.0)

    with st.expander("Why this plan runs the overtime it does"):
        st.markdown(
            "When customers in one week need more than the factory comfortably "
            "makes, the plan has two options:\n\n"
            "1. **Run a 3rd batch that week** — pay the overtime rate, plus the "
            "cost of a normal slot left empty somewhere else in the year.\n"
            "2. **Make the generators earlier and hold them** — pay the holding "
            "rate for every week they wait.\n\n"
            "The tool takes whichever is cheaper, week by week."
        )
        if overtime_weeks == 0:
            st.success(
                f"**This plan chose to hold stock instead of running any "
                f"overtime.** It is paying {usd_md(holding)} in holding cost to keep "
                f"every week inside normal capacity. Overtime surcharge is "
                f"{usd_md(surcharge)} because no week runs a 3rd batch."
            )
        else:
            st.info(
                f"**This plan runs {overtime_weeks} overtime week(s).** In those "
                f"weeks, building early would have cost more than the overtime "
                f"did. The {usd_md(surcharge)} surcharge is the empty normal slots "
                f"those overtime generators left behind elsewhere in the year."
            )
        st.caption(
            "Which way this tips is controlled by the unused-slot cost in "
            "**Settings**, not by the overtime rate — see the note beside those "
            "two fields."
        )


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
    st.dataframe(plan, width="stretch", hide_index=True)


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
                "minimum order quota. **This is an advisory.** Supplier allocation "
                "runs after the production plan is solved, so the optimizer could "
                "not trade this shortfall against the other costs — it is reported "
                "separately from the optimized total rather than folded into it."
            )

        # Structural causes the planner can act on where the optimizer cannot.
        for structural in result.summary.get("quota_structural_warnings", []):
            st.warning(structural)

        note = result.summary.get("partial_quarter_note")
        if note:
            st.info(note)

        st.dataframe(quota_frame(result.quota_status), width="stretch",
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

    ms = result.match_summary or {}
    everything_unmatched = (
        cs.get("new_customers", 0) == cs.get("total", 0) and cs.get("total", 0) > 0
    )

    with st.expander(
        f"Changed customer weeks vs manual plan — {cs.get('early', 0)} earlier, "
        f"{cs.get('late', 0)} later of {cs.get('total', 0)}",
        expanded=everything_unmatched,
    ):
        # Site matching decides whether any of this is meaningful, so it goes
        # first rather than sitting in a warning the planner may not scroll to.
        if ms.get("input_rows"):
            st.caption(
                f"Matched **{ms.get('matched', 0)} of {ms['input_rows']}** input "
                f"rows to a Master Planner column "
                f"({ms.get('match_rate', 0.0):.0%}). Only matched sites can show a "
                f"week shift. The Comparison tab lists any that did not match and "
                f"how to fix them."
            )

        if everything_unmatched:
            st.error(
                "Every generator is listed as a new customer, which means no site "
                "in your input file matched a Master Planner column. The two files "
                "use different Site_ID conventions, so no week shift can be "
                "calculated. Generate the input file from **Import Manual Plan** "
                "and both files will share the same codes."
            )

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
        st.caption(
            "One caveat worth knowing: the optimizer decides weekly *totals*, then "
            "a second step assigns those generators to customers. Where several "
            "sites are due in the same week, which one is recorded as moved is "
            "consistent between runs but arbitrary. The totals above are reliable; "
            "treat an individual row as indicative."
        )
        if cs.get("uncomparable"):
            st.caption(
                f"{cs['uncomparable']} generator(s) had no counterpart in the "
                "manual plan (the optimizer schedules more for that site), so they "
                "are listed without a shift."
            )

        df = changed_weeks_frame(result.assignments)
        all_changes = ["Moved earlier", "Moved later", "Same as manual",
                       "New customer", "No counterpart"]
        col1, col2, col3 = st.columns(3)
        with col1:
            # Default to every category. Filtering to moves only used to render an
            # empty table whenever nothing matched, which read as a broken feature.
            shift_filter = st.multiselect(
                "Change", options=all_changes,
                default=all_changes, key="cw_shift_filter",
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
        st.dataframe(view, width="stretch", hide_index=True)


def render(optimizer_service, settings_or_error) -> None:
    """Draw the Cost Optimizer tab.

    ``settings_or_error`` is either a validated ``Settings`` or a ``ValidationError``
    raised while assembling it — the tab surfaces the error rather than running.
    """
    st.subheader("Cost Optimizer")
    st.caption("Runs the optimizer on the sites file loaded in the sidebar.")

    if isinstance(settings_or_error, ValidationError):
        st.error("Fix these settings before running:")
        for message in settings_or_error.errors:
            st.write(f"- {message}")
        return
    settings = settings_or_error

    sheet = sidebar.sites_sheet()
    have_file = sidebar.sites_loaded()
    if not have_file:
        if sidebar.planner_loaded():
            st.info(
                "No sites file loaded. You do not need one — press **Derive sites "
                "from the manual plan** in the sidebar and the tool will build it "
                "from your Master Planner."
            )
        else:
            sidebar.require(sites=True)
    if have_file and not sidebar.planner_loaded():
        st.caption(
            "No Master Planner loaded. The plan will still run, but changed "
            "customer weeks cannot be shown — load it in the sidebar for that."
        )

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
            # Stamped onto the result so a later comparison can tell whether the
            # settings still match the ones this plan was solved under.
            settings_fingerprint=settings.fingerprint,
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
    _render_stale_banner(result, settings)
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
            st.dataframe(result.issues_df, width="stretch", hide_index=True)

    if result.xlsx_bytes:
        st.download_button(
            "Download results workbook",
            data=result.xlsx_bytes,
            file_name="ruby_fill_plan.xlsx",
            mime=XLSX_MIME,
            key="opt_download",
        )
