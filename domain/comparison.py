"""Domain: manual-plan baseline cost and optimized-vs-baseline comparison (pure).

The baseline is the planning team's manual plan (read from the Master Planner).
It is *evaluated*, not optimized: we replay it against demand using exactly the
same cost model as the optimizer, so the comparison is apples-to-apples.

See .kiro/specs/optimizer-enhancements/design.md, Feature 1. No I/O, no UI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import List, Sequence

import pandas as pd

from domain.cost_model import decompose_weekly_cost, split_capacity
from domain.delivery_assignment import (
    DemandEvent,
    assign_deliveries,
    eu_restricted_production,
)
from domain.params import IntegratedParams, SupplierParams
from domain.quota import check_quarterly_quota, compute_quarter_boundaries
from domain.supplier_allocation import allocate_suppliers_weekly
from domain.supplier_solve import quota_summary

# Component rows, then the total. The parts must sum to the total on **both**
# sides of the comparison, which is what went wrong before: the quota shortfall
# was folded into the optimized total and never computed for the baseline, so a
# plan compared against itself reported a $2bn loss. The total is now
# ``total_cost_with_quota`` and the quota is a declared component.
COMPONENT_ROWS = (
    ("Penalty", "total_penalty_cost"),
    ("Overtime", "total_overtime_cost"),
    ("Capacity Utilization", "total_capacity_cost"),
    ("Quota Shortfall (advisory)", "total_quota_penalty_cost"),
)

TOTAL_ROW = ("Total", "total_cost_with_quota")

COST_COMPONENTS = COMPONENT_ROWS + (TOTAL_ROW,)


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
    # Per-supplier per-quarter quota status for the *manual* plan, computed only
    # when supplier params were supplied. Present so the comparison can show the
    # same advisory on both sides.
    quota_status: List = field(default_factory=list)

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

    @property
    def totals_reconcile(self) -> bool:
        """Whether the plan produces exactly the horizon's demand.

        When it does not, this cost must not be published at any scale. Every
        unreconciled unit sits in backlog for the rest of the horizon and is
        charged ``late_penalty_rate`` -- 100x the holding rate -- which exists to
        stop the *solver* choosing backlog, not to price anything a business pays.
        A 30-unit gap has produced a billion-dollar "cost" on real data.

        The gap is usually not a planning error. Demand is modelled as each site
        repeating a fixed interval, while the Master Planner is irregular, so the
        two totals only agree for windows where the model happens to line up.
        """
        return self.terminal_inventory == 0


def compute_baseline_cost(
    planned_production: Sequence[int],
    demand: Sequence[int],
    params: IntegratedParams,
    shutdown_weeks: Sequence[int] = (),
    partial_shutdown_weeks: Sequence[int] = (),
    eu_demand: Sequence[int] | None = None,
    row_cap: int | None = None,
    supplier_params: SupplierParams | None = None,
    reference_week_date: date | None = None,
    demand_events: Sequence[DemandEvent] | None = None,
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
    supplier_params : SupplierParams | None
        Supply to compute the quarterly quota advisory for the manual plan, by the
        same code path the optimized plan uses. **Omitting it makes the comparison
        asymmetric**: the optimized side reports a quota shortfall and this side
        reports nothing, which reads as the optimizer being billions worse.
    reference_week_date : date | None
        Anchors calendar quarters for the quota calculation. Must match what the
        optimized side used.
    demand_events : Sequence[DemandEvent] | None
        Customer demand events. Supply together with ``supplier_params`` so the
        manual plan's EU-restricted volume is attributed to production weeks the
        same way the optimized plan's is.

    Returns
    -------
    BaselineResult
    """
    T = params.horizon_weeks
    shutdown = set(shutdown_weeks)
    partial = set(partial_shutdown_weeks)

    inv = 0
    total_penalty = total_overtime = total_capacity = total_composite = 0.0
    total_idle_slots = 0
    total_overtime_units = 0
    total_good = 0
    nominal_slots = 0
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

        # One decomposition, shared with the solver. This block used to
        # reimplement the formula and then assert it matched — three copies of one
        # rule needing a runtime tripwire to stay in step.
        cost = decompose_weekly_cost(inv, y, week_type, params)

        total_penalty += cost.penalty
        total_overtime += cost.overtime
        total_capacity += cost.capacity
        total_composite += cost.composite
        cumulative += cost.composite
        total_idle_slots += cost.idle_slots
        total_overtime_units += max(0, y - params.normal_max_good_week)
        total_good += y
        if week_type != "Shutdown":
            nominal_slots += (
                params.max_good_per_batch if week_type == "Partial"
                else params.normal_max_good_week
            )
        if cost.overtime > 0:
            overtime_weeks += 1

        rows.append({
            "Week": t,
            "Week_Type": week_type,
            "Demand_Due": d,
            "Good_Production": y,
            "Net_Inventory_End": inv,
            "Idle_Slots": cost.idle_slots,
            "Overtime_Units": max(0, y - params.normal_max_good_week),
            "Penalty_Cost_USD": cost.penalty,
            "Overtime_Cost_USD": cost.overtime,
            "Capacity_Utilization_Cost_USD": cost.capacity,
            "Composite_Cost_USD": cost.composite,
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

    split = split_capacity(
        nominal_slots=nominal_slots,
        produced=total_good,
        overtime_units=total_overtime_units,
        total_idle_slots=total_idle_slots,
    )
    floor_cost, surcharge_cost = split.costs(params)

    summary = {
        "total_composite_cost": total_composite,
        "total_penalty_cost": total_penalty,
        "total_overtime_cost": total_overtime,
        "total_capacity_cost": total_capacity,
        "total_capacity_floor_cost": floor_cost,
        "total_capacity_levelling_cost": surcharge_cost,
        "total_idle_slots": split.total_idle_slots,
        "unavoidable_idle_slots": split.unavoidable_idle_slots,
        "forced_overtime_units": split.forced_overtime_units,
        "discretionary_overtime_units": split.discretionary_overtime_units,
        "overtime_units": split.overtime_units,
        "nominal_slots": split.nominal_slots,
        "demand_exceeds_capacity": split.demand_exceeds_capacity,
        "overtime_weeks": overtime_weeks,
        "w_penalty": params.w_penalty,
        "w_overtime": params.w_overtime,
        "w_capacity": params.w_capacity,
    }

    # Quarterly quota for the manual plan, by the same path the optimized plan
    # uses, so the comparison has the same components on both sides. Without this
    # the optimized side carries a shortfall the baseline never computes.
    quota_status: List = []
    if supplier_params is not None:
        quota_status, quota_keys = _baseline_quota(
            planned_production, eu_demand, params, supplier_params,
            reference_week_date, demand_events, total_composite,
        )
        summary.update(quota_keys)
    else:
        # Still define the keys so the component table reconciles; a caller that
        # omits supplier params is declaring "no quota model", not "unknown".
        summary["total_quota_penalty_cost"] = 0.0
        summary["total_cost_with_quota"] = total_composite

    return BaselineResult(
        summary=summary,
        plan_df=pd.DataFrame(rows),
        capacity_violations=violations,
        total_planned=sum(int(planned_production[t]) for t in range(1, min(T + 1, len(planned_production)))),
        total_demand=sum(int(demand[t]) for t in range(1, min(T + 1, len(demand)))),
        eu_cap_violations=eu_violations,
        terminal_inventory=inv_series[T] if T >= 1 else 0,
        quota_status=quota_status,
    )


def _baseline_quota(
    planned_production: Sequence[int],
    eu_demand: Sequence[int] | None,
    params: IntegratedParams,
    supplier_params: SupplierParams,
    reference_week_date: date | None,
    demand_events: Sequence[DemandEvent] | None,
    total_composite: float,
) -> tuple[List, dict]:
    """Quarterly quota status and summary keys for the manual plan.

    Deliberately routed through the same ``allocate_suppliers_weekly`` →
    ``check_quarterly_quota`` → ``quota_summary`` chain the optimized plan uses.
    Any divergence here reappears as a phantom saving in the comparison, so the
    shared path is the point rather than a convenience.

    EU-restricted volume is attributed to production weeks when demand events are
    available, matching the optimized side. Without them it falls back to due
    weeks; that only changes the Curium lift, which is inactive while EU volume
    stays at or below ``first_run_allocation`` (15 by default, against a current
    peak of 4), so quota output is unaffected in the operating regime.
    """
    T = params.horizon_weeks
    y_plan = [0] * (T + 1)
    for t in range(1, T + 1):
        if t < len(planned_production):
            y_plan[t] = int(planned_production[t])

    eu_array = list(eu_demand) if eu_demand is not None else [0] * (T + 1)

    eu_production = None
    if demand_events:
        try:
            records = assign_deliveries(y_plan, demand_events, params)
            eu_production = eu_restricted_production(records, T)
        except ValueError:
            # The manual plan need not reconcile with modelled demand, so the
            # assignment can run out of supply. Fall back rather than fail: the
            # quota figure does not depend on this in the documented regime.
            eu_production = None

    allocations = allocate_suppliers_weekly(
        y_plan, eu_array, params, supplier_params, eu_production=eu_production
    )
    boundaries = compute_quarter_boundaries(
        T, supplier_params.quarter_start_month, reference_week_date
    )
    quota_status = check_quarterly_quota(allocations, supplier_params, boundaries)

    keys = quota_summary(
        quota_status,
        allocations,
        supplier_params,
        boundaries,
        {"total_composite_cost": total_composite},
    )
    return quota_status, keys


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

    Every row carries ``Reconciles``: False when the component rows do not sum to
    the total row for that side. A mismatch means one side is carrying a cost the
    other never computed, which is precisely the defect this structure exists to
    prevent — so it surfaces as data rather than as a silently wrong saving.
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

    reconciles = _reconciles(rows)
    for row in rows:
        row["Reconciles"] = reconciles
    return rows


def _reconciles(rows: List[dict], tolerance: float = 0.01) -> bool:
    """Whether the component rows sum to the total row, on both sides."""
    total_label = TOTAL_ROW[0]
    parts = [r for r in rows if r["Component"] != total_label]
    total = next((r for r in rows if r["Component"] == total_label), None)
    if total is None:
        return False
    return all(
        abs(sum(r[side] for r in parts) - total[side]) <= tolerance
        for side in ("Baseline", "Optimized")
    )


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
