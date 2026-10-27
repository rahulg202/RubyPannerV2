"""Presentation: the Onboarding Recommendation tab.

Accepts several new customers at once, each with its own permissible start-week
window, shows the search-space estimate before running, and offers a ready-to-use
optimizer input file once the planner confirms a selection.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

import pandas as pd
import streamlit as st

from domain.dates import current_planning_week
from domain.errors import RubyFillError, ValidationError
from domain.onboarding import OBJECTIVES, NewCustomer
from services.dtos import OnboardingRequest
from services.onboarding_service import MODE_FIXED, MODE_OPTIMIZE
from ui import sidebar
from ui import state as S
from ui.formatting import (
    XLSX_MIME,
    rankings_frame,
    usd_signed,
    usd_signed_md,
    week_date_map,
    week_label,
)

BLANK_ROW = {
    "Site_ID": "",
    "Site_Name": "",
    "Earliest_Week": 1,
    "Latest_Week": 8,
    "Interval_Weeks": 7,
    "Country": "usa",
    "EU_Restricted": False,
}

OBJECTIVE_LABELS = {
    # Keys must track domain.onboarding.OBJECTIVES. "capacity" was removed as a
    # ranking objective: ranking on Δ capacity ranked on how many deliveries
    # happened to fit inside the horizon, which always favoured week 1 regardless
    # of cost. It remains a displayed column.
    "marginal": "Lowest added cost",
    "penalty": "Lowest Δ penalty",
    "overtime": "Lowest Δ overtime",
}


WEEK_COLUMNS = ("Earliest_Week", "Latest_Week")
DATE_COLUMNS = ("Earliest_Date", "Latest_Date")


def _editor_seed(reference: date | None, horizon: int) -> pd.DataFrame:
    """The frame handed to the editor, kept stable across reruns.

    The editor owns the rows once seeded. This only rebuilds the seed when there
    is nothing yet, or when switching between week numbers and dates changes the
    columns — in which case the outgoing widget's stored edits are dropped, since
    they refer to columns that no longer exist.
    """
    mode = "dates" if reference is not None else "weeks"
    previous = st.session_state.get(S.OB_EDITOR_MODE)

    if previous != mode:
        # Carry across whatever the planner has already typed.
        source = st.session_state.get(S.OB_VIEW)
        if not isinstance(source, pd.DataFrame) or source.empty:
            source = st.session_state.get(S.OB_CUSTOMERS)
        st.session_state[S.OB_CUSTOMERS] = _shape_frame(source, reference, horizon)
        if previous:
            st.session_state.pop(f"ob_editor_{previous}", None)
        st.session_state[S.OB_EDITOR_MODE] = mode

    seed = st.session_state.get(S.OB_CUSTOMERS)
    if not isinstance(seed, pd.DataFrame) or seed.empty:
        seed = _shape_frame(None, reference, horizon)
        st.session_state[S.OB_CUSTOMERS] = seed
    return seed


def _date_for_week(reference: date, week: int) -> date:
    return reference + timedelta(days=7 * (max(1, int(week)) - 1))


def _week_for_date(reference: date, value, horizon: int, default: int) -> int:
    """Planning week containing ``value``, clamped into the horizon."""
    if isinstance(value, pd.Timestamp):
        value = value.date()
    if not isinstance(value, date):
        return default
    week = current_planning_week(reference, value, horizon)
    if week is None:
        # Before week 1, or past the horizon: clamp rather than silently drop.
        return 1 if value < reference else horizon
    return week


def _shape_frame(
    existing: pd.DataFrame | None,
    reference: date | None,
    horizon: int,
) -> pd.DataFrame:
    """Put the editor rows into the shape the current mode needs.

    Switching the reference week on or off must not cost the planner their rows,
    so existing values are converted rather than reset.

    Called only when seeding the editor or when the mode changes — never on every
    rerun. Reshaping a live editor's input makes Streamlit replay its stored edit
    delta against data that already contains those edits, which duplicates and
    shifts rows.
    """
    frame = (
        existing.copy()
        if isinstance(existing, pd.DataFrame) and not existing.empty
        else pd.DataFrame([dict(BLANK_ROW)])
    )

    if reference is not None:
        for week_col, date_col in zip(WEEK_COLUMNS, DATE_COLUMNS):
            if date_col not in frame.columns:
                source = frame[week_col] if week_col in frame.columns else None
                frame[date_col] = [
                    _date_for_week(reference, _as_int(v, 1))
                    for v in (source if source is not None else [1] * len(frame))
                ]
        frame = frame.drop(columns=[c for c in WEEK_COLUMNS if c in frame.columns])
    else:
        for week_col, date_col in zip(WEEK_COLUMNS, DATE_COLUMNS):
            if week_col not in frame.columns:
                if date_col in frame.columns:
                    frame[week_col] = [
                        _week_for_date(reference, v, horizon, 1)
                        if reference else _as_int(v, 1)
                        for v in frame[date_col]
                    ]
                else:
                    frame[week_col] = BLANK_ROW[week_col]
        frame = frame.drop(columns=[c for c in DATE_COLUMNS if c in frame.columns])

    order = ["Site_ID", "Site_Name"]
    order += list(DATE_COLUMNS if reference is not None else WEEK_COLUMNS)
    order += ["Interval_Weeks", "Country", "EU_Restricted"]
    return frame[[c for c in order if c in frame.columns]]


def _as_int(value, default: int) -> int:
    """Coerce an editor cell to int, tolerating None, NaN, and blank text.

    ``st.data_editor`` fills newly-added rows with NaN. A plain ``value or default``
    guard is not enough because NaN is truthy in Python, so ``NaN or 1`` is NaN and
    ``int(NaN)`` raises.
    """
    if value is None:
        return default
    if isinstance(value, float) and math.isnan(value):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_text(value) -> str:
    """Coerce an editor cell to a stripped string, treating NaN/None as empty."""
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none"} else text


def _as_bool(value) -> bool:
    """Coerce an editor checkbox cell to bool, treating NaN/None as False."""
    if value is None:
        return False
    if isinstance(value, float) and math.isnan(value):
        return False
    return bool(value)


def _to_customers(
    df: pd.DataFrame,
    reference: date | None = None,
    horizon: int = 52,
) -> list[NewCustomer]:
    """Build NewCustomer objects from the editor frame, skipping blank rows.

    The onboarding window is entered as dates when a reference week is set and as
    week numbers otherwise; either way it reaches the domain as week numbers.
    """
    customers: list[NewCustomer] = []
    if df is None or not isinstance(df, pd.DataFrame):
        return customers
    for _i, row in df.iterrows():
        site_id = _as_text(row.get("Site_ID"))
        if not site_id:
            continue  # a blank row is still being filled in; ignore it
        if reference is not None and "Earliest_Date" in df.columns:
            earliest = _week_for_date(reference, row.get("Earliest_Date"), horizon, 1)
            latest = _week_for_date(reference, row.get("Latest_Date"), horizon, earliest)
        else:
            earliest = _as_int(row.get("Earliest_Week"), 1)
            latest = _as_int(row.get("Latest_Week"), 1)
        customers.append(NewCustomer(
            site_id=site_id,
            site_name=_as_text(row.get("Site_Name")),
            earliest_week=earliest,
            latest_week=latest,
            interval_weeks=_as_int(row.get("Interval_Weeks"), 7),
            country=_as_text(row.get("Country")).lower(),
            eu_restricted=_as_bool(row.get("EU_Restricted")),
        ))
    return customers


def _render_editor(reference: date | None = None,
                   horizon: int = 52) -> list[NewCustomer]:
    st.markdown("**New customers to onboard**")
    st.caption(
        "One row per customer. Site_ID is the elution system serial number for "
        "that site — enter it as your team assigns it. Each customer may have its "
        "own earliest and latest permissible start date."
    )

    use_dates = reference is not None
    if use_dates:
        last = reference + timedelta(days=7 * (horizon - 1))
        st.caption(
            f"Dates are snapped to the planning week containing them. The plan "
            f"runs {reference:%d %b %Y} to {last:%d %b %Y}."
        )
    else:
        st.caption(
            "Set a reference week in **Settings** to enter these as dates instead "
            "of week numbers."
        )

    window = (
        {
            "Earliest_Date": st.column_config.DateColumn(
                "Can start from", format="DD MMM YYYY",
                help="Earliest date this customer could be onboarded."),
            "Latest_Date": st.column_config.DateColumn(
                "Must start by", format="DD MMM YYYY",
                help="Latest date by which this customer must be onboarded."),
        }
        if use_dates else
        {
            "Earliest_Week": st.column_config.NumberColumn(
                "Earliest week", min_value=1, step=1,
                help="Earliest week this customer can be onboarded."),
            "Latest_Week": st.column_config.NumberColumn(
                "Must onboard by", min_value=1, step=1,
                help="Last week by which this customer must be onboarded."),
        }
    )

    edited = st.data_editor(
        _editor_seed(reference, horizon),
        num_rows="dynamic",
        width="stretch",
        # Separate keys per mode: the columns differ, and reusing one key would
        # leave Streamlit holding editor state for columns that no longer exist.
        key=f"ob_editor_{'dates' if use_dates else 'weeks'}",
        column_config={
            "Site_ID": st.column_config.TextColumn(
                "Site_ID", help="Elution system serial number (assigned manually)."),
            "Site_Name": st.column_config.TextColumn("Site_Name"),
            **window,
            "Interval_Weeks": st.column_config.NumberColumn(
                "Interval", min_value=0, step=1,
                help="Weeks between generator replacements. 0 means a single "
                     "one-time delivery."),
            "Country": st.column_config.TextColumn("Country"),
            "EU_Restricted": st.column_config.CheckboxColumn(
                "Curium only",
                help="European customer (excluding Switzerland) that cannot "
                     "receive BWXT-sourced material."),
        },
    )
    # Mirrored for migration and for the rest of this run only. Deliberately not
    # written back into the seed the editor was given: Streamlit stores edits as a
    # positional delta and replays them onto whatever data it receives, so feeding
    # its own output back applies every edit twice — which is what made adding a
    # second row wipe the first.
    st.session_state[S.OB_VIEW] = edited
    return _to_customers(edited, reference, horizon)


def _render_estimate(service, customers) -> bool:
    est = service.estimate(customers)
    combos = est["combinations"]
    if est["exhaustive"]:
        st.info(
            f"Search space: {combos:,} combination(s) across "
            f"{len(customers)} customer(s). Every combination will be evaluated, "
            "so the result is the true optimum."
        )
    else:
        st.warning(
            f"Search space: {combos:,} combinations exceeds the exhaustive limit "
            f"of {est['threshold']:,}. A heuristic search will run instead — it "
            "returns a strong candidate, not a proven optimum. This may take a "
            "few minutes."
        )
    return est["exhaustive"]


def _render_mode() -> str:
    """Choose between re-planning and slotting into the existing plan."""
    choice = st.radio(
        "How should the best week be judged?",
        options=[MODE_OPTIMIZE, MODE_FIXED],
        format_func=lambda m: (
            "Re-plan all 52 weeks (cheapest week if production can move)"
            if m == MODE_OPTIMIZE
            else "Keep my plan fixed (where does this site fit into it?)"
        ),
        key="ob_mode",
        horizontal=False,
    )
    if choice == MODE_FIXED:
        st.caption(
            "Weekly production is taken from your Master Planner and does not "
            "move. Each generator either drops into a week with a spare slot, "
            "needs a third batch, or is built earlier and held. The cost shown is "
            "what accommodating the site adds — nothing else in the plan changes."
        )
    else:
        st.caption(
            "The whole 52 weeks are re-optimized for every candidate start week, "
            "and the cost is compared against a baseline of your existing sites. "
            "This finds the cheapest week when production is free to move."
        )
    return choice


def _render_fixed_rankings(result, customers) -> None:
    """Ranked start weeks measured against a plan held fixed."""
    site_ids = [c.site_id for c in customers]
    options = result.fixed_rankings

    st.caption(
        f"Read from your Master Planner, which has production in "
        f"{result.fixed_plan_weeks} week(s)."
    )
    for warning in getattr(result, "warnings", [])[:4]:
        st.warning(warning)

    if not options:
        st.error(
            "No start week worked. Every candidate week left at least one "
            "generator with nowhere to go — the plan is at the overtime ceiling "
            "in those weeks and no nearby week has a spare slot. Widen the "
            "onboarding window, or free up capacity."
        )
        return

    dates = week_date_map(result.week_dates)
    rows = []
    for option in options:
        row = {
            f"Week for {sid}": week_label(option.selected_weeks.get(sid), dates)
            for sid in site_ids
        }
        row.update({
            "Added cost": usd_signed(option.added_cost),
            # The same two components the re-planning mode reports as deltas, so
            # the two modes read in one vocabulary. Here they are not deltas
            # against a re-optimized plan -- the plan does not move -- but they
            # measure the same two things: stock held early, and extra batches.
            # They always sum to Added cost.
            "Δ Penalty": usd_signed(option.holding_cost),
            "Δ Overtime": usd_signed(option.overtime_cost),
            "Deliveries": option.generators,
            "Fit a spare slot": option.fits,
            "Absorbed by existing overtime": option.absorbed,
            "Needed new overtime": option.overtime_added,
            "Built early and held": option.shifted,
            "No disruption": "Yes" if option.fits_cleanly else "No",
        })
        rows.append(row)

    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    if not dates:
        st.caption(
            "Set a reference week in **Settings** to see the date beside each "
            "week number."
        )
    st.caption(
        "Each row is one candidate start week. **Added cost** is what slotting "
        "the site in costs on top of your current plan, and splits into "
        "**Δ Penalty** (stock built early and held) and **Δ Overtime** (third "
        "batches in weeks that were already full). Zero means it fits with no "
        "disruption. **Absorbed by existing overtime** means the week already "
        "runs a third batch and had room, so there is no extra charge. "
        "Idle-capacity cost is not included here — your plan is fixed, so its "
        "utilisation does not change, and there is no Δ Capacity or Δ Composite "
        "for the same reason."
    )

    best = options[0]
    with st.expander(f"Where each generator would go — best option "
                     f"({usd_signed(best.added_cost)})"):
        st.dataframe(
            pd.DataFrame([
                {
                    "Site_ID": p.site_id,
                    "Due week": week_label(p.due_week, dates),
                    "Made in week": week_label(p.produced_week, dates),
                    "Outcome": p.outcome,
                    "Cost": usd_signed(p.cost),
                    "Detail": p.detail,
                }
                for p in best.placements
            ]),
            width="stretch", hide_index=True,
        )


def _render_selection_metrics(selected, fixed_mode: bool) -> None:
    """Summarise the chosen option, in the terms of the mode that produced it.

    The two modes measure different things, so they cannot share one set of
    metrics. Re-planning mode reports deltas against a re-optimized baseline;
    fixed mode reports what accommodating the site adds to an unchanged plan and
    has no capacity delta at all, because the plan's utilisation does not move.
    """
    if fixed_mode:
        cols = st.columns(4)
        cols[0].metric("Added cost", usd_signed(selected.added_cost),
                       help="What slotting these customers into your existing "
                            "plan costs. Zero means they fit with no disruption.")
        cols[1].metric("Δ Penalty", usd_signed(selected.holding_cost),
                       help="Cost of building a generator early and holding it "
                            "because its own week was full.")
        cols[2].metric("Δ Overtime", usd_signed(selected.overtime_cost),
                       help="Third batches needed in weeks that were already full.")
        cols[3].metric(
            "Fits with no disruption",
            "Yes" if selected.fits_cleanly else "No",
            help="Yes when every generator dropped into spare capacity without "
                 "extra overtime or holding.",
        )
        if selected.fits_cleanly:
            st.caption(
                "Every generator fits into capacity your plan already has, so "
                "nothing else in the plan needs to move."
            )
        else:
            st.caption(
                f"Accommodating these customers costs "
                f"**{usd_signed_md(selected.added_cost)}** on top of your current "
                f"plan. Unused-capacity cost is not included — your plan is fixed, "
                f"so its utilisation does not change."
            )
        return

    marginal = getattr(selected, "delta_marginal", 0.0)
    added = getattr(selected, "generators_added", 0)

    cols = st.columns(4)
    cols[0].metric(
        "Added cost", usd_signed(marginal),
        help="Penalty plus overtime: the cost that actually changes with the start "
             "weeks you pick. Idle-capacity cost is excluded, because it moves only "
             "with how many deliveries land inside the 52-week window rather than "
             "with whether those weeks are good ones.",
    )
    cols[1].metric("Δ Penalty", usd_signed(selected.delta_penalty),
                   help="Change in early/late inventory cost.")
    cols[2].metric("Δ Overtime", usd_signed(selected.delta_overtime),
                   help="Change in overtime cost.")
    cols[3].metric(
        "Deliveries", added,
        help="Generators these start weeks need before week 52. Later start weeks "
             "fit fewer, which is the only reason the whole-plan total moves "
             "between candidates.",
    )

    if marginal == 0:
        st.caption(
            "**These start weeks cost nothing extra.** The factory absorbs the new "
            "demand with no additional overtime and no early build-up, so every "
            "week in the window is equally workable on cost grounds — decide on "
            "commercial or clinical grounds instead."
        )
    elif marginal > 0:
        st.caption(
            f"Accommodating these customers **adds {usd_signed_md(marginal)}** in "
            f"penalty and overtime. The extra demand pushes production past "
            f"comfortable capacity in some weeks, so overtime or early build-up is "
            f"needed."
        )
    else:
        st.caption(
            f"These start weeks **reduce** penalty and overtime by "
            f"{usd_signed_md(-marginal)}, by filling weeks that were previously uneven."
        )

    with st.expander("Why the whole-plan total shows a saving"):
        st.markdown(
            f"The whole-plan figure is **{usd_signed_md(selected.delta_composite)}**, "
            f"of which **{usd_signed_md(selected.delta_capacity)}** is unused-capacity "
            f"cost. Each of the {added} deliveries these customers need takes a "
            f"production slot that would otherwise have sat idle and been charged "
            f"for, so the total drops by roughly the capacity rate per delivery.\n\n"
            "That is arithmetic rather than money saved, and it is why the ranking "
            "above uses the added-cost figure: ranking on the whole-plan total just "
            "picks whichever start week fits the most deliveries in before week 52."
        )


def _render_rankings(result, customers) -> None:
    site_ids = [c.site_id for c in customers]
    objectives = tuple(k for k in OBJECTIVES if k in OBJECTIVE_LABELS)
    tabs = st.tabs([OBJECTIVE_LABELS[k] for k in objectives])
    for tab, objective in zip(tabs, objectives):
        with tab:
            options = result.rankings.get(objective, [])
            if not options:
                st.info("No feasible options for this objective.")
                continue
            st.dataframe(
                rankings_frame(options, site_ids, result.week_dates),
                width="stretch", hide_index=True,
            )
            st.caption(
                "Each row is one set of start weeks. The **Δ** columns show how "
                "the whole 52-week plan's cost changes if you onboard on those "
                "weeks, compared with today's plan of existing customers only. "
                "They are **year totals, not figures for the start week named in "
                "the row.** Negative means cheaper. Rows are ordered best-first "
                "for this objective."
            )
            # The horizon-edge effect, stated wherever the ranking is read. Δ
            # Composite and Δ Capacity are dominated by it, which is why neither
            # is ranked on any more.
            st.caption(
                "**Read Added cost, not Δ Composite.** Δ Capacity falls by roughly "
                "the capacity rate for every delivery the new site needs, because "
                "each one takes a slot that would otherwise have sat idle and been "
                "charged for. Since a later start week fits fewer deliveries inside "
                "the 52 weeks — see *Deliveries* — earlier weeks always look "
                "cheaper on Δ Composite, for reasons of where the horizon ends "
                "rather than anything operational. Added cost leaves that out and "
                "shows only what genuinely responds to the choice."
            )


def _cost_effect(delta: float) -> str:
    """Plain-language effect of a combination on total plan cost."""
    if delta < 0:
        return f"saves {usd_signed(abs(delta))}"
    if delta > 0:
        return f"costs {usd_signed(delta)} more"
    return "no change in cost"


def _render_selection_and_file(service, result, customers) -> None:
    st.divider()
    st.markdown("### Confirm a selection and generate the input file")
    st.caption(
        "Pick the start weeks you want to commit to. The tool then writes a new "
        "sites file with these customers added, ready to upload in the Cost "
        "Optimizer tab."
    )

    # Gather every distinct combination that appeared in any ranking. Fixed-plan
    # mode ranks on a single measure, so its options are already a flat list.
    fixed_mode = getattr(result, "mode", MODE_OPTIMIZE) == MODE_FIXED
    seen: dict[tuple, object] = {}
    if fixed_mode:
        for opt in result.fixed_rankings:
            seen.setdefault(opt.key(), opt)
    else:
        for options in result.rankings.values():
            for opt in options:
                seen.setdefault(opt.key(), opt)
    combos = list(seen.values())
    if not combos:
        st.info("No feasible combination to select.")
        return

    dates = week_date_map(result.week_dates)

    def _label(opt) -> str:
        weeks = ", ".join(
            f"{sid} starts week {week_label(opt.selected_weeks[sid], dates)}"
            for sid in sorted(opt.selected_weeks)
        )
        effect = (
            f"adds {usd_signed(opt.added_cost)}" if fixed_mode
            else _cost_effect(opt.delta_composite)
        )
        return f"{weeks}  —  {effect}"

    choice = st.selectbox(
        "Start weeks to apply", options=list(range(len(combos))),
        format_func=lambda i: _label(combos[i]),
        key=f"ob_selection_choice_{'fixed' if fixed_mode else 'optimize'}",
        help="Each option is a set of start weeks, one per new customer, with its "
             "cost effect.",
    )
    selected = combos[choice]
    st.session_state[S.OB_SELECTION] = selected.selected_weeks

    # Spell out what the chosen option actually means.
    st.markdown("**This selection**")
    rows = []
    by_id = {c.site_id: c for c in customers}
    for site_id in sorted(selected.selected_weeks):
        week = selected.selected_weeks[site_id]
        cust = by_id.get(site_id)
        rows.append({
            "Customer": site_id,
            "Name": (cust.site_name if cust else "") or "—",
            "First generator due": f"week {week_label(week, dates)}",
            "Then every": f"{cust.interval_weeks} weeks" if cust else "—",
            "Country": (cust.country if cust else "") or "—",
        })
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

    _render_selection_metrics(selected, fixed_mode)

    have_sites = st.session_state.get(S.SITES_BYTES) is not None
    if not have_sites:
        # Fixed mode only requires the Master Planner to produce a ranking, but
        # writing the input file needs the existing sites to append to.
        st.info(
            "To generate an input file, upload your current sites file in the "
            "**Cost Optimizer** tab. The recommendation above does not need it, "
            "but the generated file is your existing sites plus the new ones."
        )

    if st.button("Generate optimizer input file", type="primary",
                 key="ob_generate", disabled=not have_sites):
        try:
            data = service.generate_input_file(
                st.session_state[S.SITES_BYTES],
                st.session_state[S.SITES_NAME],
                st.session_state.get(S.SITES_SHEET) or "Sites",
                customers,
                selected.selected_weeks,
            )
            st.session_state[S.OB_GENERATED] = data
            st.success(
                f"Input file ready — your existing sites plus "
                f"{len(customers)} new customer(s) at the start weeks above. "
                "Download it, then upload it in the Cost Optimizer tab to plan "
                "with these customers included."
            )
        except ValidationError as exc:
            for message in exc.errors:
                st.error(message)
        except RubyFillError as exc:
            st.error(str(exc))

    if st.session_state.get(S.OB_GENERATED):
        st.download_button(
            "Download optimizer input file",
            data=st.session_state[S.OB_GENERATED],
            file_name="sites_with_new_customers.xlsx",
            mime=XLSX_MIME,
            key="ob_download",
        )


def render(onboarding_service, settings_or_error) -> None:
    """Draw the Onboarding Recommendation tab."""
    st.subheader("Onboarding Recommendation")
    st.caption(
        "Find the best start week for each new customer, then hand back an input "
        "file with them included."
    )

    if isinstance(settings_or_error, ValidationError):
        st.error("Fix these settings before running:")
        for message in settings_or_error.errors:
            st.write(f"- {message}")
        return
    settings = settings_or_error

    mode = _render_mode()
    if mode == MODE_FIXED:
        # Fixed mode reads existing weekly production from the manual plan.
        ready = sidebar.require(planner=True)
    else:
        # Re-planning mode needs existing demand as the baseline.
        ready = sidebar.sites_loaded()
        if not ready:
            if sidebar.planner_loaded():
                st.info(
                    "No sites file loaded. Press **Derive sites from the manual "
                    "plan** in the sidebar to build one from your Master Planner."
                )
            else:
                sidebar.require(sites=True)

    customers = _render_editor(
        settings.reference_week_date, settings.params.horizon_weeks
    )
    if not customers:
        st.info("Add at least one new customer above.")
        return

    _render_estimate(onboarding_service, customers)

    if st.button("Run recommendation", type="primary", key="ob_run",
                 disabled=not ready):
        progress_bar = st.progress(0.0, text="Starting...")

        def on_progress(fraction: float, message: str) -> None:
            progress_bar.progress(min(max(fraction, 0.0), 1.0), text=message)

        request = OnboardingRequest(
            file_bytes=st.session_state.get(S.SITES_BYTES) or b"",
            filename=st.session_state.get(S.SITES_NAME) or "sites.xlsx",
            sheet=st.session_state.get(S.SITES_SHEET) or "Sites",
            new_customers=tuple(customers),
            params=settings.params,
            supplier_params=settings.supplier_params,
            shutdown_weeks=settings.shutdown_weeks,
            partial_shutdown_weeks=settings.partial_shutdown_weeks,
            reference_week_date=settings.reference_week_date,
            mode=mode,
            master_planner_bytes=st.session_state.get(S.MP_BYTES),
            master_planner_sheet=st.session_state.get(S.MP_SHEET) or "Schedule",
        )
        try:
            st.session_state[S.OB_RESULT] = onboarding_service.run(request, on_progress)
            st.session_state[S.OB_GENERATED] = None
        except ValidationError as exc:
            for message in exc.errors:
                st.error(message)
            return
        except RubyFillError as exc:
            st.error(str(exc))
            return
        except ValueError as exc:
            st.error(f"Input problem: {exc}")
            return

    result = st.session_state.get(S.OB_RESULT)
    if result is None:
        return

    st.divider()
    st.markdown("### Recommendations")
    cols = st.columns(3)
    cols[0].metric("Combinations evaluated", f"{result.combinations_evaluated:,}")
    cols[1].metric("Search space", f"{result.search_space:,}")
    cols[2].metric("Infeasible", result.infeasible_count)

    if result.used_heuristic:
        st.warning(
            "A heuristic search was used, so these are strong candidates rather "
            "than a proven global optimum."
        )
    if result.infeasible_reasons:
        with st.expander("Why some combinations were infeasible"):
            for reason in result.infeasible_reasons:
                st.write(f"- {reason}")

    if getattr(result, "mode", MODE_OPTIMIZE) == MODE_FIXED:
        _render_fixed_rankings(result, customers)
    else:
        _render_rankings(result, customers)
    _render_selection_and_file(onboarding_service, result, customers)
