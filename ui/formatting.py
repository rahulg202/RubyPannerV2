"""Presentation: display formatting helpers (pure, no Streamlit calls)."""

from __future__ import annotations

from datetime import date
from typing import Sequence

import pandas as pd

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def usd(value: float | None) -> str:
    """Format a value as whole US dollars."""
    if value is None:
        return "—"
    return f"${value:,.0f}"


def usd_signed(value: float | None) -> str:
    """Format a saving, making a negative (worse) value explicit."""
    if value is None:
        return "—"
    return f"{'-' if value < 0 else ''}${abs(value):,.0f}"


def pct(value: float | None) -> str:
    """Format a percentage, or 'n/a' when undefined (zero baseline)."""
    if value is None:
        return "n/a"
    return f"{value:.1f}%"


def thousands(value: float | None) -> str:
    """Compact cost display, e.g. 28000 -> '$28K'."""
    if value is None:
        return "—"
    return f"${round(value / 1000):,.0f}K"


def add_week_dates(
    df: pd.DataFrame,
    week_dates: Sequence[tuple],
    week_col: str = "Week",
) -> pd.DataFrame:
    """Insert MFG_Date / Cal_Date after the week column when dates are known."""
    if not week_dates or week_col not in df.columns:
        return df
    mfg = {w: m for w, m, _c in week_dates}
    cal = {w: c for w, _m, c in week_dates}
    out = df.copy()
    pos = list(out.columns).index(week_col) + 1
    out.insert(pos, "MFG_Date", out[week_col].map(mfg))
    out.insert(pos + 1, "Cal_Date", out[week_col].map(cal))
    return out


def mark_current_week(
    df: pd.DataFrame,
    current_week: int | None,
    week_col: str = "Week",
) -> pd.DataFrame:
    """Prefix the current week's row with an indicator column."""
    if current_week is None or week_col not in df.columns:
        return df
    out = df.copy()
    out.insert(0, "Now", ["▶" if w == current_week else "" for w in out[week_col]])
    return out


def quota_frame(quota_status: Sequence) -> pd.DataFrame:
    """Render quota status objects for display.

    Partial quarters show a pro-rated target as a run-rate reference and carry no
    penalty, because part of the real quarter falls outside the plan.
    """
    rows = []
    for q in quota_status:
        target = q.prorated_quota_mci if q.is_partial else q.quota_mci
        gap = q.prorated_shortfall_mci if q.is_partial else q.shortfall_mci
        rows.append({
            "Supplier": q.supplier,
            "Quarter": f"Q{q.quarter}",
            "Weeks": f"{min(q.weeks)}–{max(q.weeks)}" if q.weeks else "",
            "Coverage": f"{q.weeks_covered}/{q.expected_weeks} wks",
            "Quota (mCi)": round(q.quota_mci, 1),
            "Target (mCi)": round(target, 1),
            "Ordered (mCi)": round(q.ordered_mci, 1),
            "Gap (mCi)": round(gap, 1),
            "Penalty": usd(q.penalty_usd),
            "Status": q.status,
        })
    return pd.DataFrame(rows)


def _change_label(record) -> str:
    """Classify a generator's change against the manual plan."""
    if record.is_new_customer:
        return "New customer"
    if record.week_shift is None:
        return "No counterpart"
    if record.week_shift < 0:
        return "Moved earlier"
    if record.week_shift > 0:
        return "Moved later"
    return "Same as manual"


def changed_weeks_frame(assignments: Sequence) -> pd.DataFrame:
    """Render delivery records for the changed-week view.

    The headline comparison is the optimizer's production week against the manual
    plan's week for the same customer generator. The due week from the input file
    is included as context.
    """
    return pd.DataFrame([
        {
            "Site_ID": r.site_id,
            "Site_Name": r.site_name,
            "Country": r.country,
            "Manual_Plan_Week": r.manual_week,
            "Optimized_Week": r.planned_week,
            "Week_Shift": r.week_shift,
            "Shift": _change_label(r),
            "Due_Week": r.due_week,
            "New_Customer": "Yes" if r.is_new_customer else "No",
        }
        for r in assignments
    ])


def comparison_frame(components: Sequence[dict]) -> pd.DataFrame:
    """Render the baseline-vs-optimized comparison for display."""
    return pd.DataFrame([
        {
            "Component": c["Component"],
            "Manual Plan": usd(c["Baseline"]),
            "Optimized": usd(c["Optimized"]),
            "Saving": usd_signed(c["Saving_Abs"]),
            "Saving %": pct(c["Saving_Pct"]),
        }
        for c in components
    ])


def rankings_frame(options: Sequence, site_ids: Sequence[str]) -> pd.DataFrame:
    """Render ranked onboarding combinations, one column per new customer."""
    rows = []
    for rank, opt in enumerate(options, start=1):
        row = {"Rank": rank}
        for sid in site_ids:
            row[f"{sid} week"] = opt.selected_weeks.get(sid)
        row.update({
            "Δ Penalty": usd_signed(opt.delta_penalty),
            "Δ Overtime": usd_signed(opt.delta_overtime),
            "Δ Capacity": usd_signed(opt.delta_capacity),
            "Δ Composite": usd_signed(opt.delta_composite),
            "OT Weeks": opt.overtime_weeks,
        })
        rows.append(row)
    return pd.DataFrame(rows)
