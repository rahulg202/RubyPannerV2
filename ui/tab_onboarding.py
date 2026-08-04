"""Presentation: the Onboarding Recommendation tab.

Accepts several new customers at once, each with its own permissible start-week
window, shows the search-space estimate before running, and offers a ready-to-use
optimizer input file once the planner confirms a selection.
"""

from __future__ import annotations

import math

import pandas as pd
import streamlit as st

from domain.errors import RubyFillError, ValidationError
from domain.onboarding import NewCustomer
from services.dtos import OnboardingRequest
from ui import state as S
from ui.formatting import XLSX_MIME, rankings_frame, usd_signed

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
    "penalty": "Lowest Δ penalty",
    "overtime": "Lowest Δ overtime",
    "capacity": "Lowest Δ capacity",
}


def _editor_frame() -> pd.DataFrame:
    existing = st.session_state.get(S.OB_CUSTOMERS)
    if isinstance(existing, pd.DataFrame) and not existing.empty:
        return existing
    return pd.DataFrame([dict(BLANK_ROW)])


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


def _to_customers(df: pd.DataFrame) -> list[NewCustomer]:
    """Build NewCustomer objects from the editor frame, skipping blank rows."""
    customers: list[NewCustomer] = []
    if df is None or not isinstance(df, pd.DataFrame):
        return customers
    for _i, row in df.iterrows():
        site_id = _as_text(row.get("Site_ID"))
        if not site_id:
            continue  # a blank row is still being filled in; ignore it
        customers.append(NewCustomer(
            site_id=site_id,
            site_name=_as_text(row.get("Site_Name")),
            earliest_week=_as_int(row.get("Earliest_Week"), 1),
            latest_week=_as_int(row.get("Latest_Week"), 1),
            interval_weeks=_as_int(row.get("Interval_Weeks"), 7),
            country=_as_text(row.get("Country")).lower(),
            eu_restricted=_as_bool(row.get("EU_Restricted")),
        ))
    return customers


def _render_editor() -> list[NewCustomer]:
    st.markdown("**New customers to onboard**")
    st.caption(
        "One row per customer. Site_ID is the elution system serial number for "
        "that site — enter it as your team assigns it. Each customer may have its "
        "own earliest and latest permissible start week."
    )
    edited = st.data_editor(
        _editor_frame(),
        num_rows="dynamic",
        use_container_width=True,
        key="ob_editor",
        column_config={
            "Site_ID": st.column_config.TextColumn(
                "Site_ID", help="Elution system serial number (assigned manually)."),
            "Site_Name": st.column_config.TextColumn("Site_Name"),
            "Earliest_Week": st.column_config.NumberColumn(
                "Earliest week", min_value=1, step=1,
                help="Earliest week this customer can be onboarded."),
            "Latest_Week": st.column_config.NumberColumn(
                "Must onboard by", min_value=1, step=1,
                help="Last week by which this customer must be onboarded."),
            "Interval_Weeks": st.column_config.NumberColumn(
                "Interval", min_value=1, step=1,
                help="Weeks between generator replacements."),
            "Country": st.column_config.TextColumn("Country"),
            "EU_Restricted": st.column_config.CheckboxColumn(
                "Curium only",
                help="European customer (excluding Switzerland) that cannot "
                     "receive BWXT-sourced material."),
        },
    )
    st.session_state[S.OB_CUSTOMERS] = edited
    return _to_customers(edited)


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


def _render_rankings(result, customers) -> None:
    site_ids = [c.site_id for c in customers]
    tabs = st.tabs([OBJECTIVE_LABELS[k] for k in ("penalty", "overtime", "capacity")])
    for tab, objective in zip(tabs, ("penalty", "overtime", "capacity")):
        with tab:
            options = result.rankings.get(objective, [])
            if not options:
                st.info("No feasible options for this objective.")
                continue
            st.dataframe(
                rankings_frame(options, site_ids),
                use_container_width=True, hide_index=True,
            )
            st.caption(
                "Each row is one set of start weeks. The **Δ** columns show how "
                "the whole 52-week plan's cost changes if you onboard on those "
                "weeks, compared with today's plan of existing customers only. "
                "**Negative means cheaper.** Rows are ordered best-first for this "
                "objective."
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

    # Gather every distinct combination that appeared in any ranking.
    seen: dict[tuple, object] = {}
    for options in result.rankings.values():
        for opt in options:
            seen.setdefault(opt.key(), opt)
    combos = list(seen.values())
    if not combos:
        st.info("No feasible combination to select.")
        return

    def _label(opt) -> str:
        weeks = ", ".join(
            f"{sid} starts week {opt.selected_weeks[sid]}"
            for sid in sorted(opt.selected_weeks)
        )
        return f"{weeks}  —  {_cost_effect(opt.delta_composite)}"

    choice = st.selectbox(
        "Start weeks to apply", options=list(range(len(combos))),
        format_func=lambda i: _label(combos[i]), key="ob_selection_choice",
        help="Each option is a set of start weeks, one per new customer, with its "
             "effect on the total cost of the 52-week plan.",
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
            "First generator due": f"week {week}",
            "Then every": f"{cust.interval_weeks} weeks" if cust else "—",
            "Country": (cust.country if cust else "") or "—",
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    cols = st.columns(4)
    cols[0].metric("Effect on total cost", usd_signed(selected.delta_composite),
                   help="Change in the whole plan's cost versus today, with these "
                        "customers added. Negative means the plan gets cheaper.")
    cols[1].metric("Δ Penalty", usd_signed(selected.delta_penalty),
                   help="Change in early/late inventory cost.")
    cols[2].metric("Δ Overtime", usd_signed(selected.delta_overtime),
                   help="Change in overtime cost.")
    cols[3].metric("Δ Capacity", usd_signed(selected.delta_capacity),
                   help="Change in unused-capacity cost.")

    if selected.delta_composite < 0:
        st.caption(
            f"Adding these customers **{_cost_effect(selected.delta_composite)}** "
            "overall. That is normal here: new demand fills production slots the "
            "factory is already paying for, so unused-capacity cost falls by more "
            "than the extra inventory and overtime cost."
        )
    elif selected.delta_composite > 0:
        st.caption(
            f"Adding these customers **{_cost_effect(selected.delta_composite)}**. "
            "The extra demand pushes production past comfortable capacity in some "
            "weeks, so overtime or early build-up is needed."
        )

    if st.button("Generate optimizer input file", type="primary", key="ob_generate"):
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

    have_file = st.session_state.get(S.SITES_BYTES) is not None
    if not have_file:
        st.info(
            "Upload a sites file in the Cost Optimizer tab first — the engine "
            "needs your existing demand as the baseline."
        )

    customers = _render_editor()
    if not customers:
        st.info("Add at least one new customer above.")
        return

    _render_estimate(onboarding_service, customers)

    if st.button("Run recommendation", type="primary", key="ob_run",
                 disabled=not have_file):
        progress_bar = st.progress(0.0, text="Starting...")

        def on_progress(fraction: float, message: str) -> None:
            progress_bar.progress(min(max(fraction, 0.0), 1.0), text=message)

        request = OnboardingRequest(
            file_bytes=st.session_state[S.SITES_BYTES],
            filename=st.session_state[S.SITES_NAME],
            sheet=st.session_state.get(S.SITES_SHEET) or "Sites",
            new_customers=tuple(customers),
            params=settings.params,
            supplier_params=settings.supplier_params,
            shutdown_weeks=settings.shutdown_weeks,
            partial_shutdown_weeks=settings.partial_shutdown_weeks,
            reference_week_date=settings.reference_week_date,
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

    _render_rankings(result, customers)
    _render_selection_and_file(onboarding_service, result, customers)
