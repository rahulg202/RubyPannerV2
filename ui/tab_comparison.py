"""Presentation: the Comparison tab.

Compares the planning team's manual plan (read from the Master Planner workbook)
against the optimized plan, component by component.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from domain.delivery_assignment import build_demand_events
from domain.demand import build_weekly_demand, build_weekly_row_demand
from domain.errors import RubyFillError, StaleResultError, ValidationError
from domain.site_matching import STATUS_MATCHED, match_report_frame
from services.dtos import ComparisonRequest
from ui import sidebar
from ui import state as S
from ui.formatting import (
    add_week_dates,
    comparison_frame,
    usd,
    usd_signed,
    week_date_map,
    week_label,
)


def _render_unreconciled_baseline(result) -> None:
    """Totals disagree, so no cost is shown at all.

    Deliberately publishes no dollar figure. Any unreconciled unit sits in backlog
    for the remainder of the horizon at ``late_penalty_rate``, which is 100x the
    holding rate and exists to stop the solver choosing backlog -- it prices
    nothing real. On the reference workbook a 30-unit gap produced a $1.03bn
    "cost", which is an artefact of that multiplier and not a number to show a
    planner, let alone put in front of management.
    """
    b = result.baseline
    manual = b.total_planned if b else 0
    demand = b.total_demand if b else 0
    gap = manual - demand

    st.error(
        f"**The manual plan and the demand model do not describe the same total, "
        f"so no cost comparison is shown.** The plan produces {manual:,} "
        f"generators over the horizon while demand totals {demand:,} — a gap of "
        f"{gap:+,}."
    )

    cols = st.columns(3)
    cols[0].metric("Manual plan", f"{manual:,}")
    cols[1].metric("Demand model", f"{demand:,}")
    cols[2].metric("Gap", f"{gap:+,}")

    st.markdown(
        "**Why this usually happens.** Demand is modelled as each site repeating a "
        "fixed interval from its first delivery. Your Master Planner is smoothed by "
        "hand and is not perfectly regular, so over a given 52-week window the two "
        "totals need not agree. Changing the reference week moves the window and "
        "changes the gap."
    )
    st.markdown(
        "**Why no cost is shown.** A missing generator is treated as a late "
        "delivery, and late delivery is priced at 100 times the cost of holding "
        "stock early. That rate exists to stop the optimizer ever choosing to run "
        "late; it is not a price anyone pays. Applied to a whole-horizon gap it "
        "produces figures in the hundreds of millions that mean nothing, so the "
        "cost is withheld rather than shown with a caveat."
    )
    st.info(
        "**To get a comparison:** clear the reference week and let the tool pick "
        "the year that reconciles, or try a reference week whose window does "
        "reconcile. The week-by-week table below shows where the plan and the "
        "demand model diverge."
    )


def _render_infeasible_baseline(result, total) -> None:
    """Explain why the two costs are not comparable, instead of showing a saving.

    The manual plan is evaluated, not solved, so it can break limits the optimizer
    cannot. Those breaches make it look *cheaper*, because the limits are exactly
    what force stock to be built ahead of time. Reporting that gap as a negative
    saving reads as the optimizer performing badly, which is the wrong conclusion.
    """
    st.error(
        "**The manual plan cannot be run as written, so these two costs are not "
        "directly comparable.** The optimizer is held to limits the manual plan "
        "breaks, and meeting those limits is what its extra cost buys."
    )

    st.markdown("Reading the Master Planner as written, the manual plan:")
    for reason in result.baseline_infeasibilities:
        st.markdown(f"- {reason}")

    extra = -float(total["Saving_Abs"]) if total else 0.0
    cols = st.columns(2)
    cols[0].metric(
        "Cost of an executable plan",
        usd(extra) if extra > 0 else usd_signed(-extra),
        help="What the optimized plan costs above the manual plan. This is the "
             "price of staying inside the weekly maximum and the shipping cap, "
             "not money the manual plan saves.",
    )
    cols[1].metric(
        "Overtime weeks",
        f"{result.overtime_optimized} vs {result.overtime_baseline}",
        help="Optimized versus manual plan.",
    )
    st.caption(
        "To get a like-for-like saving, correct the weeks listed above in the "
        "Master Planner and run the comparison again."
    )


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

    # Infeasibilities get their own block below, so keep them out of the general
    # warning list rather than saying the same thing twice.
    infeasible_msgs = {f"Manual plan {r}." for r in result.baseline_infeasibilities}
    for warning in result.warnings:
        if warning not in infeasible_msgs:
            st.warning(warning)

    total = next(
        (c for c in result.components if c["Component"] == "Total"), None
    )
    penalty = next((c for c in result.components if c["Component"] == "Penalty"), None)

    # The components must sum to the total on both sides. A False here means one
    # side is carrying a cost the other never computed, which is exactly the
    # defect that once reported a $2bn loss for a plan compared against itself.
    if result.components and not result.components[0].get("Reconciles", True):
        st.error(
            "**These figures do not add up and must not be used.** The cost "
            "components do not sum to the total on at least one side, which means "
            "the two plans are being costed with different rules. Please report "
            "this."
        )

    if not result.baseline_totals_reconcile:
        _render_unreconciled_baseline(result)
    elif not result.baseline_is_comparable:
        _render_infeasible_baseline(result, total)
    elif total and penalty:
        cols = st.columns(3)
        cols[0].metric("Total saving", usd_signed(total["Saving_Abs"]))
        cols[1].metric("Penalty saving", usd_signed(penalty["Saving_Abs"]))
        cols[2].metric(
            "Overtime weeks",
            f"{result.overtime_optimized} vs {result.overtime_baseline}",
            help="Optimized versus manual plan.",
        )

    # The cost table is withheld when the totals do not reconcile: every figure in
    # the baseline column carries the late-delivery deterrent rate and would be
    # read as money. The week-by-week table and the site report below stay, since
    # they are what a planner needs to find the discrepancy.
    if result.baseline_totals_reconcile:
        st.dataframe(
            comparison_frame(result.components), width="stretch", hide_index=True
        )
        st.caption(
            "Penalty and overtime are where the optimizer earns its keep. The "
            "capacity component is largely a fixed floor set by demand versus "
            "factory capacity, so it moves little between plans and can even "
            "worsen slightly. The quota row is an advisory: supplier allocation "
            "happens after the plan is solved, so neither plan was optimized "
            "against it — but it is now computed the same way on both sides, so "
            "the totals are comparable."
        )

    dates = week_date_map(result.week_dates)

    if result.baseline and result.baseline.capacity_violations:
        with st.expander(
            f"Manual plan capacity violations "
            f"({len(result.baseline.capacity_violations)})"
        ):
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Week": week_label(week, dates),
                            "Planned": planned,
                            "Weekly capacity": capacity,
                        }
                        for week, planned, capacity
                        in result.baseline.capacity_violations
                    ]
                ),
                width="stretch", hide_index=True,
            )

    if result.baseline and result.baseline.eu_cap_violations:
        with st.expander(
            f"Manual plan shipping-cap violations "
            f"({len(result.baseline.eu_cap_violations)})"
        ):
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Week": week_label(week, dates),
                            "EU-restricted units due": due,
                            "Can ship per week": cap,
                            "Short by": short,
                        }
                        for week, due, cap, short
                        in result.baseline.eu_cap_violations
                    ]
                ),
                width="stretch", hide_index=True,
            )
            st.caption(
                "Denmark, the UK, the Netherlands and Sweden take at most "
                f"{result.baseline.eu_cap_violations[0][2]} generator(s) a week "
                "between them. To deliver more in one week, the extra units have "
                "to be made earlier and held, which is the stock the optimizer "
                "builds and the manual plan does not."
            )

    if result.weekly_comparison is not None:
        with st.expander("Week-by-week production", expanded=False):
            table = add_week_dates(result.weekly_comparison, result.week_dates)
            st.dataframe(table, width="stretch", hide_index=True)
            if not dates:
                st.caption(
                    "Manufacturing dates were not found in the Master Planner, so "
                    "weeks are shown by number."
                )

            # Plot against dates when they are known, so the axis reads as a
            # calendar rather than as an index the reader has to translate.
            columns = ["Manual_Production", "Optimized_Production"]
            if "MFG_Date" in table.columns and table["MFG_Date"].notna().any():
                chart = table.dropna(subset=["MFG_Date"]).set_index("MFG_Date")
            else:
                chart = table.set_index("Week")
            st.line_chart(chart[columns])

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

    sheet = sidebar.planner_sheet()

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
    have_mp = sidebar.require(planner=True)

    if opt_result is None:
        st.info("Run the Cost Optimizer first — the comparison needs its result.")

    ready = opt_result is not None and have_mp
    if st.button("Run comparison", type="primary", key="cmp_run", disabled=not ready):
        demand = build_weekly_demand(opt_result.active_df, settings.params)
        # Same EU-restricted demand the optimizer solved against, so the manual
        # plan is held to the same QC shipping cap.
        eu_demand = build_weekly_row_demand(opt_result.active_df, settings.params)
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
            # Supplier params make the quota advisory appear on the manual-plan
            # side too. Without them the optimized side carries a shortfall this
            # side never computes, and the gap reads as the optimizer being worse.
            supplier_params=settings.supplier_params,
            shutdown_weeks=settings.shutdown_weeks,
            partial_shutdown_weeks=settings.partial_shutdown_weeks,
            reference_week_date=settings.reference_week_date,
            active_sites=active_sites,
            eu_demand=tuple(eu_demand),
            # Same events both sides, so EU-restricted volume is attributed to
            # production weeks identically.
            demand_events=tuple(build_demand_events(opt_result.active_df,
                                                    settings.params)),
            settings_fingerprint=settings.fingerprint,
            optimized_settings_fingerprint=getattr(
                opt_result, "settings_fingerprint", ""
            ),
        )
        with st.spinner("Reading the Master Planner and evaluating the manual plan..."):
            try:
                st.session_state[S.CMP_RESULT] = comparison_service.run(request)
            except StaleResultError as exc:
                st.error(str(exc))
                st.info(
                    "The plan currently loaded was solved under different "
                    "settings. Comparing it against a manual plan costed with "
                    "today's settings would attribute the difference to the "
                    "optimizer rather than to the settings change. Re-run the "
                    "**Cost Optimizer** tab, then come back."
                )
                return
            except RubyFillError as exc:
                st.error(str(exc))
                return
            except ValueError as exc:
                st.error(f"Could not parse the Master Planner: {exc}")
                return

    result = st.session_state.get(S.CMP_RESULT)
    if result is not None:
        _render_result(result)
