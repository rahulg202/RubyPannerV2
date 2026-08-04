"""Presentation: the single Settings tab.

Every cost, rate, weight, and constraint the business may need to change lives
here (Requirement E-7). Values are held in session state and validated by
``services.settings_service`` on each run — nothing is hardcoded downstream.
"""

from __future__ import annotations

from datetime import date

import streamlit as st

from domain.quota import compute_quarter_boundaries
from services.settings_service import DEFAULTS
from ui.state import cfg_key, clear_results


def _num(label: str, name: str, *, help_text: str, step=None, min_value=None,
         max_value=None, fmt: str | None = None) -> None:
    """Render a number input bound to a namespaced settings key."""
    default = DEFAULTS[name]
    st.number_input(
        label,
        key=cfg_key(name),
        value=st.session_state.get(cfg_key(name), default),
        help=f"{help_text} (default: {default})",
        step=step,
        min_value=min_value,
        max_value=max_value,
        format=fmt,
    )


def _text(label: str, name: str, *, help_text: str) -> None:
    default = DEFAULTS[name]
    st.text_input(
        label,
        key=cfg_key(name),
        value=st.session_state.get(cfg_key(name), default),
        help=f"{help_text} (default: {'empty' if default == '' else default})",
    )


def _render_quarter_alignment_note() -> None:
    """Warn when the chosen reference week produces partial quarters."""
    ref = st.session_state.get(cfg_key("reference_week_date"))
    if not isinstance(ref, date):
        st.caption("No reference week set — quarters default to 13-week blocks.")
        return

    try:
        horizon = int(st.session_state.get(cfg_key("horizon_weeks"),
                                          DEFAULTS["horizon_weeks"]))
        start_month = int(st.session_state.get(cfg_key("quarter_start_month"),
                                              DEFAULTS["quarter_start_month"]))
        spans = compute_quarter_boundaries(horizon, start_month, ref)
    except Exception:
        return

    partial = [s for s in spans if s.is_partial]
    if not partial:
        st.caption(f"{len(spans)} fully-covered quarters — all quota-checked.")
        return

    detail = ", ".join(
        f"Q{s.quarter} {len(s.weeks)}/{s.expected_weeks} wks" for s in partial
    )
    st.warning(
        f"{detail} are partial — reported but not quota-penalised. "
        "Start the reference week on a quarter boundary to check all four."
    )


def restore_defaults() -> None:
    """Reset every settings key to its documented default."""
    for name, value in DEFAULTS.items():
        st.session_state[cfg_key(name)] = value
    clear_results(st.session_state)


def render() -> None:
    """Draw the Settings tab."""
    st.subheader("Settings")
    st.caption(
        "Every cost, rate, weight, and constraint used by the model. "
        "Changes apply to the Cost Optimizer, Onboarding, and Comparison alike. "
        "Values last for this session only."
    )

    if st.button("Restore all defaults", key="btn_restore_defaults"):
        restore_defaults()
        st.success("All parameters restored to their defaults.")

    # ---------------- Reference dates ----------------
    with st.expander("Reference dates", expanded=True):
        st.caption(
            "Anchors planning week 1 to a real manufacturing date so every week "
            "can be shown as a date. Leave the anchor unset to work in week "
            "numbers only."
        )
        col1, col2 = st.columns(2)
        with col1:
            use_ref = st.checkbox(
                "Use a reference week",
                key="cfg_use_reference",
                value=st.session_state.get("cfg_use_reference", False),
                help="When off, the app shows week numbers without dates.",
            )
            if use_ref:
                st.date_input(
                    "Week 1 manufacturing date",
                    key=cfg_key("reference_week_date"),
                    value=st.session_state.get(cfg_key("reference_week_date")) or date.today(),
                    help="The MFG date the Master Planner shows for planning week 1.",
                )
            else:
                st.session_state[cfg_key("reference_week_date")] = None
        with col2:
            _num("Calibration offset (days)", "calibration_offset_days",
                 help_text="Days from the manufacturing date to the calibration date",
                 step=1, min_value=0)

    # ---------------- Production constraints ----------------
    with st.expander("Production constraints", expanded=False):
        col1, col2, col3 = st.columns(3)
        with col1:
            _num("Horizon (weeks)", "horizon_weeks",
                 help_text="Length of the planning horizon", step=1, min_value=1)
            _num("Min batch produced", "min_batch_produced",
                 help_text="Minimum units produced per batch, including the test unit",
                 step=1, min_value=1)
        with col2:
            _num("Max batch produced", "max_batch_produced",
                 help_text="Maximum units produced per batch, including the test unit",
                 step=1, min_value=1)
            _num("Test discard per batch", "test_discard_per_batch",
                 help_text="Units discarded for QC testing in each batch",
                 step=1, min_value=0)
        with col3:
            _num("Normal max batches/week", "normal_max_batches",
                 help_text="Batches allowed in a normal week", step=1, min_value=0)
            _num("Overtime max batches/week", "overtime_max_batches",
                 help_text="Batches allowed when overtime is used", step=1, min_value=0)

        _text("Shutdown weeks", "shutdown_weeks",
              help_text="Comma-separated weeks with no production, e.g. 1,2,3")
        _text("Partial shutdown weeks", "partial_shutdown_weeks",
              help_text="Comma-separated weeks limited to a single batch")

    # ---------------- Cost rates and weights ----------------
    with st.expander("Costs and weights", expanded=False):
        col1, col2 = st.columns(2)
        with col1:
            _num("Early penalty rate (USD per unit-week)", "penalty_rate",
                 help_text="Cost of holding one finished unit one week early",
                 step=100.0)
            _num("Late penalty multiplier", "late_penalty_multiplier",
                 help_text="Multiplier applied to the penalty rate for late (backlog) units",
                 step=1.0)
            _num("Overtime rate (USD per week)", "overtime_rate",
                 help_text="Cost of running a third batch in a week", step=100.0)
            _num("Capacity rate (USD per unused slot)", "capacity_rate",
                 help_text="Cost charged per unused good-unit slot per week",
                 step=100.0)
        with col2:
            st.caption(
                "Weights scale each cost component in the objective (0–1). "
                "Note: on typical demand the capacity component is largely a "
                "fixed floor, so a high capacity weight can mask penalty savings."
            )
            for name, label in (
                ("w_penalty", "Penalty weight"),
                ("w_overtime", "Overtime weight"),
                ("w_capacity", "Capacity weight"),
                ("w_quota", "Quota shortfall weight"),
            ):
                st.slider(
                    label, min_value=0.0, max_value=1.0, step=0.05,
                    key=cfg_key(name),
                    value=float(st.session_state.get(cfg_key(name), DEFAULTS[name])),
                    help=f"Weight for the {label.lower()} component (default {DEFAULTS[name]})",
                )

    # ---------------- QC shipping cap ----------------
    with st.expander("QC shipping cap", expanded=False):
        st.caption(
            "Quality-control throughput limit on generators shipped to the "
            "restricted European countries in a single week. This is enforced by "
            "the solver and is a separate rule from the BWXT material restriction."
        )
        _num("Max restricted-country units per week", "row_cap",
             help_text="QC shipping cap", step=1, min_value=0)

    # ---------------- Supplier parameters ----------------
    with st.expander("Raw material suppliers (Curium / BWXT)", expanded=False):
        st.caption(
            "Sr-82 activity is computed as "
            "`100·G + 10·batches + max(ceil(surplus·base), 20)` mCi per supplier."
        )
        col1, col2 = st.columns(2)
        with col1:
            _num("Curium surplus fraction", "curium_surplus_pct",
                 help_text="Extra Sr-82 required from Curium (0.05 = 5%)",
                 step=0.01, min_value=0.0, max_value=1.0, fmt="%.3f")
            _num("Sr-82 per generator (mCi)", "per_generator_mci",
                 help_text="Activity consumed by one good generator", step=1.0)
            _num("Minimum surplus (mCi)", "minimum_surplus_mci",
                 help_text="Floor applied to the surplus term", step=1.0, min_value=0.0)
            _num("Curium quarterly quota (mCi)", "curium_quarterly_quota_mci",
                 help_text="Minimum Curium order per quarter", step=500.0, min_value=0.0)
        with col2:
            _num("BWXT surplus fraction", "bwxt_surplus_pct",
                 help_text="Extra Sr-82 required from BWXT (0.02 = 2%)",
                 step=0.01, min_value=0.0, max_value=1.0, fmt="%.3f")
            _num("Sr-82 per batch / QC generator (mCi)", "per_batch_mci",
                 help_text="Activity consumed by the QC generator in each batch", step=1.0)
            _num("First Curium run (generators)", "first_run_allocation",
                 help_text="Generators in the first Curium run of a split week",
                 step=1, min_value=0)
            _num("BWXT quarterly quota (mCi)", "bwxt_quarterly_quota_mci",
                 help_text="Minimum BWXT order per quarter", step=500.0, min_value=0.0)

        _num("Quota shortfall penalty (USD per mCi)", "quota_shortfall_penalty_rate",
             help_text="Charge per mCi short of a quarterly minimum", step=1000.0,
             min_value=0.0)
        _num("Quarter start month", "quarter_start_month",
             help_text="Month on which quarter 1 begins (1 = January)",
             step=1, min_value=1, max_value=12)
        _render_quarter_alignment_note()
        col3, col4 = st.columns(2)
        with col3:
            _text("Curium unavailable weeks", "curium_unavailable_weeks",
                  help_text="Comma-separated weeks Curium cannot supply")
        with col4:
            _text("BWXT unavailable weeks", "bwxt_unavailable_weeks",
                  help_text="Comma-separated weeks BWXT cannot supply")
