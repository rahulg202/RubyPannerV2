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
    # (week, eu_due, cap, shortfall) for weeks whose EU-restricted deliveries
    # exceed the shipping cap without enough stock built beforehand.
    eu_cap_violations: List[tuple] = field(default_factory=list)
    terminal_inventory: int = 0

    @property
    def infeasibilities(self) -> List[str]:
        """Hard constraints the manual plan breaks, in plain language.

        The optimizer cannot break any of these, so while this list is non-empty
        the manual plan's cost is not a like-for-like baseline: it is the cost of
        a plan that could not actually be run.
        """
        reasons: List[str] = []
        if self.capacity_violations:
            weeks = ", ".join(str(w) for w, _p, _c in self.capacity_violations[:8])
            more = "..." if len(self.capacity_violations) > 8 else ""
            cap = self.capacity_violations[0][2]
            reasons.append(
                f"produces more than the {cap}-unit weekly maximum in "
                f"{len(self.capacity_violations)} week(s): {weeks}{more}"
            )
        if self.eu_cap_violations:
            weeks = ", ".join(str(w) for w, _d, _c, _s in self.eu_cap_violations[:8])
            more = "..." if len(self.eu_cap_violations) > 8 else ""
            cap = self.eu_cap_violations[0][2]
            reasons.append(
                f"ships more than {cap} EU-restricted unit(s) in a week without "
                f"building stock beforehand, in {len(self.eu_cap_violations)} "
                f"week(s): {weeks}{more}"
            )
        if self.terminal_inventory < 0:
            reasons.append(
                f"ends the horizon {abs(self.terminal_inventory)} unit(s) short of "
                f"demand"
            )
        elif self.terminal_inventory > 0:
            reasons.append(
                f"ends the horizon with {self.terminal_inventory} unit(s) of "
                f"unsold stock"
            )
        return reasons

    @property
    def is_feasible(self) -> bool:
        """True when the manual plan obeys every constraint the optimizer obeys."""
        return not self.infeasibilities


def compute_baseline_cost(
    planned_production: Sequence[int],
    demand: Sequence[int],
    params: IntegratedParams,
    shutdown_weeks: Sequence[int] = (),
    partial_shutdown_weeks: Sequence[int] = (),
    eu_demand: Sequence[int] | None = None,
    row_cap: int | None = None,
) -> BaselineResult:
    """Evaluate a given weekly production plan against demand.

    Uses the identical cost model as the optimizer (``compute_weekly_cost``) so
    the resulting summary is directly comparable. Infeasibilities are *reported*
    rather than rejected, so a planner can see where the manual plan could not
    actually have been run.

    Reporting is not the same as charging for them, and that matters: a plan that
    breaks a constraint is usually *cheaper* under this cost model than any legal
    plan, because the constraints are what force stock to be built early. Callers
    must check :attr:`BaselineResult.is_feasible` before presenting the cost as a
    baseline the optimizer is beaten against. See ``infeasibilities``.

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
    eu_demand : Sequence[int] | None
        1-indexed EU-restricted demand. Supply together with ``row_cap`` to check
        the manual plan against the QC shipping cap, which the solver enforces via
        ``domain.solver.compute_inventory_bounds``. Omitting it leaves the check
        off and the plan will look cheaper than any plan the optimizer can build.
    row_cap : int | None
        Max EU-restricted units shippable in one week.

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
    inv_series = [0] * (T + 1)   # end-of-week net inventory, for the EU cap check

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
        inv_series[t] = inv

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

    # QC shipping cap. Mirrors domain.solver.compute_inventory_bounds exactly: a
    # week delivering more EU-restricted units than can ship at once needs the
    # excess already in stock at the end of the previous week. Any other rule here
    # would re-introduce the asymmetry this check exists to remove.
    #
    # Week 1 is skipped, as it is in the solver -- there is no earlier week to
    # build in, so neither plan can satisfy it and charging only one would mislead.
    eu_violations: List[tuple] = []
    if eu_demand is not None and row_cap is not None:
        for t in range(2, T + 1):
            eu_due = int(eu_demand[t]) if t < len(eu_demand) else 0
            excess = eu_due - row_cap
            if excess > 0 and inv_series[t - 1] < excess:
                eu_violations.append(
                    (t, eu_due, row_cap, excess - max(0, inv_series[t - 1]))
                )

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
        eu_cap_violations=eu_violations,
        terminal_inventory=inv_series[T] if T >= 1 else 0,
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
