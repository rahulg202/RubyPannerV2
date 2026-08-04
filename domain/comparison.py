"""Domain: manual-plan baseline cost and optimized-vs-baseline comparison (pure).

The baseline is the planning team's manual plan (read from the Master Planner).
It is *evaluated*, not optimized: we replay it against demand using exactly the
same cost model as the optimizer, so the comparison is apples-to-apples.

See .kiro/specs/optimizer-enhancements/design.md, Feature 1. No I/O, no UI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Sequence

import pandas as pd

from domain.cost_model import compute_weekly_cost
from domain.params import IntegratedParams

COST_COMPONENTS = (
    ("Penalty", "total_penalty_cost"),
    ("Overtime", "total_overtime_cost"),
    ("Capacity Utilization", "total_capacity_cost"),
    ("Total Composite", "total_composite_cost"),
)


@dataclass
class BaselineResult:
    """Cost of executing the manual plan, plus any infeasibilities found."""

    summary: dict = field(default_factory=dict)
    plan_df: pd.DataFrame | None = None
    capacity_violations: List[tuple] = field(default_factory=list)  # (week, planned, cap)
    total_planned: int = 0
    total_demand: int = 0


def compute_baseline_cost(
    planned_production: Sequence[int],
    demand: Sequence[int],
    params: IntegratedParams,
    shutdown_weeks: Sequence[int] = (),
    partial_shutdown_weeks: Sequence[int] = (),
) -> BaselineResult:
    """Evaluate a given weekly production plan against demand.

    Uses the identical cost model as the optimizer (``compute_weekly_cost``) so
    the resulting summary is directly comparable. Capacity violations are
    reported rather than rejected, so a planner can see where the manual plan was
    physically infeasible.

    Parameters
    ----------
    planned_production : Sequence[int]
        1-indexed weekly good units from the manual plan (index 0 unused).
    demand : Sequence[int]
        1-indexed weekly demand (index 0 unused).
    params : IntegratedParams
        Cost rates, weights, and capacity limits.
    shutdown_weeks, partial_shutdown_weeks : Sequence[int]
        Weeks with zero / single-batch capacity.

    Returns
    -------
    BaselineResult
    """
    T = params.horizon_weeks
    shutdown = set(shutdown_weeks)
    partial = set(partial_shutdown_weeks)

    inv = 0
    total_penalty = total_overtime = total_capacity = total_composite = 0.0
    overtime_weeks = 0
    violations: List[tuple] = []
    rows = []
    cumulative = 0.0

    for t in range(1, T + 1):
        y = int(planned_production[t]) if t < len(planned_production) else 0
        d = int(demand[t]) if t < len(demand) else 0

        if t in shutdown:
            week_type, cap = "Shutdown", 0
        elif t in partial:
            week_type, cap = "Partial", params.max_good_per_batch
        else:
            week_type, cap = "Normal", params.overtime_max_good_week

        if y > cap:
            violations.append((t, y, cap))

        inv = inv + y - d

        # Component decomposition (mirrors the solver's plan builder)
        if inv >= 0:
            penalty = params.penalty_rate * inv
        else:
            penalty = params.late_penalty_rate * abs(inv)
        overtime = params.overtime_rate if y > params.normal_max_good_week else 0.0
        if week_type == "Shutdown":
            capacity = 0.0
        elif week_type == "Partial":
            capacity = params.capacity_rate * max(0, params.max_good_per_batch - y)
        else:
            capacity = params.capacity_rate * max(0, params.normal_max_good_week - y)

        composite = (
            params.w_penalty * penalty
            + params.w_overtime * overtime
            + params.w_capacity * capacity
        )
        # Cross-check against the shared cost function
        assert abs(composite - compute_weekly_cost(inv, y, week_type, params)) < 1e-6

        total_penalty += penalty
        total_overtime += overtime
        total_capacity += capacity
        total_composite += composite
        cumulative += composite
        if y > params.normal_max_good_week:
            overtime_weeks += 1

        rows.append({
            "Week": t,
            "Week_Type": week_type,
            "Demand_Due": d,
            "Good_Production": y,
            "Net_Inventory_End": inv,
            "Penalty_Cost_USD": penalty,
            "Overtime_Cost_USD": overtime,
            "Capacity_Utilization_Cost_USD": capacity,
            "Composite_Cost_USD": composite,
            "Cumulative_Composite_Cost_USD": cumulative,
        })

    summary = {
        "total_composite_cost": total_composite,
        "total_penalty_cost": total_penalty,
        "total_overtime_cost": total_overtime,
        "total_capacity_cost": total_capacity,
        "overtime_weeks": overtime_weeks,
        "w_penalty": params.w_penalty,
        "w_overtime": params.w_overtime,
        "w_capacity": params.w_capacity,
    }

    return BaselineResult(
        summary=summary,
        plan_df=pd.DataFrame(rows),
        capacity_violations=violations,
        total_planned=sum(int(planned_production[t]) for t in range(1, min(T + 1, len(planned_production)))),
        total_demand=sum(int(demand[t]) for t in range(1, min(T + 1, len(demand)))),
    )


def _pct_saving(baseline: float, optimized: float) -> float | None:
    """Percentage saving, or None when the baseline is zero (undefined)."""
    if baseline == 0:
        return None
    return (baseline - optimized) / baseline * 100.0


def compare_plans(
    baseline_summary: dict,
    optimized_summary: dict,
) -> List[dict]:
    """Return one row per cost component with absolute and percentage savings.

    Saving is ``baseline - optimized``; a negative value means the optimized plan
    costs more for that component and is reported as-is rather than suppressed.
    """
    rows: List[dict] = []
    for label, key in COST_COMPONENTS:
        base = float(baseline_summary.get(key, 0.0))
        opt = float(optimized_summary.get(key, 0.0))
        rows.append({
            "Component": label,
            "Baseline": base,
            "Optimized": opt,
            "Saving_Abs": base - opt,
            "Saving_Pct": _pct_saving(base, opt),
        })
    return rows


def weekly_comparison(
    planned_production: Sequence[int],
    optimized_plan_df: pd.DataFrame,
    params: IntegratedParams,
) -> pd.DataFrame:
    """Week-by-week manual vs optimized production, with the difference."""
    T = params.horizon_weeks
    optimized = optimized_plan_df.set_index("Week")["Good_Production"].to_dict()
    rows = []
    for t in range(1, T + 1):
        manual = int(planned_production[t]) if t < len(planned_production) else 0
        opt = int(optimized.get(t, 0))
        rows.append({
            "Week": t,
            "Manual_Production": manual,
            "Optimized_Production": opt,
            "Difference": opt - manual,
        })
    return pd.DataFrame(rows)
