"""Domain: DP solver and plan construction (pure)."""

from __future__ import annotations

from typing import List, Tuple

import pandas as pd

from domain.cost_model import decompose_weekly_cost, split_capacity
from domain.demand import batches_needed, realisable_good_levels, split_good_into_batches
from domain.errors import ShippingCapInfeasibleError
from domain.params import IntegratedParams

NORMAL = "Normal"
PARTIAL = "Partial"
SHUTDOWN = "Shutdown"


def compute_inventory_bounds(
    demand: List[int],
    cap_max: List[int],
    params: IntegratedParams,
    eu_demand: List[int] | None = None,
    row_cap: int | None = None,
) -> Tuple[List[int], List[int]]:
    """
    Compute per-week inventory lower and upper bounds for DP pruning.

    Upper bound: no point holding more inventory than remaining demand.
    Lower bound: can go negative (backlog allowed as last resort) — the minimum
    is how far short we could be if we produce as much as possible from here on.

    row_cap enforcement (optional)
    ------------------------------
    When both ``eu_demand`` and ``row_cap`` are supplied, any week ``t`` whose
    EU-restricted demand exceeds ``row_cap`` must have the excess pre-built into
    inventory beforehand (the QC shipping cap limits how many restricted-country
    units can ship in a single week). This raises the lower bound on inventory
    at the end of week ``t-1`` by ``eu_demand[t] - row_cap``. When either
    argument is ``None`` the bounds are identical to the unconstrained case.

    Week 1 is exempt: there is no earlier week to build in, so no plan could
    satisfy an excess landing there and charging one would be arbitrary.

    Parameters
    ----------
    demand : List[int]
        1-indexed demand array (index 0 unused).
    cap_max : List[int]
        1-indexed max good units per week (index 0 unused).
    params : IntegratedParams
        Model parameters (used for horizon_weeks).
    eu_demand : List[int] | None
        1-indexed EU-restricted demand array (index 0 unused). Optional.
    row_cap : int | None
        Max restricted-country units shippable per week. Optional.

    Returns
    -------
    lb : List[int]
        Lower bound on inventory at end of each week (can be negative).
    ub : List[int]
        Upper bound on inventory at end of each week.

    Raises
    ------
    ShippingCapInfeasibleError
        When a week's pre-build requirement exceeds anything the horizon could
        hold at that point. Previously this surfaced as a generic "insufficient
        capacity" message naming the wrong cause.
    """
    T = params.horizon_weeks
    lb = [0] * (T + 1)
    ub = [0] * (T + 1)

    # Suffix sums: remaining demand and remaining capacity after week t
    suffix_demand = [0] * (T + 2)   # suffix_demand[t] = sum(demand[t..T])
    suffix_cap = [0] * (T + 2)      # suffix_cap[t]    = sum(cap_max[t..T])

    for t in range(T, 0, -1):
        suffix_demand[t] = demand[t] + suffix_demand[t + 1]
        suffix_cap[t] = cap_max[t] + suffix_cap[t + 1]

    for t in range(1, T + 1):
        # Upper bound: remaining demand after this week (no point holding more)
        ub[t] = suffix_demand[t + 1]

        # Lower bound: worst case — we produce max from t+1..T but still can't
        # cover remaining demand → backlog = remaining_demand - remaining_cap
        remaining_demand = suffix_demand[t + 1]
        remaining_cap = suffix_cap[t + 1]
        lb[t] = remaining_demand - remaining_cap  # can be negative

    # Terminal week must end at exactly 0
    lb[T] = 0
    ub[T] = 0

    # row_cap enforcement: pre-build EU-restricted excess into earlier inventory
    if eu_demand is not None and row_cap is not None:
        for t in range(1, T + 1):
            eu_excess = eu_demand[t] - row_cap
            if eu_excess <= 0:
                continue
            previous = t - 1
            if previous < 1 or previous >= T:
                # Week 1 has nothing before it; the terminal week is pinned to 0.
                continue
            if eu_excess > ub[previous]:
                raise ShippingCapInfeasibleError(
                    week=t,
                    cap=row_cap,
                    required=eu_excess,
                    available=ub[previous],
                )
            lb[previous] = max(lb[previous], eu_excess)

    return lb, ub


def solve_plan_integrated(
    demand: List[int],
    shutdown_weeks: List[int],
    partial_shutdown_weeks: List[int],
    eu_demand: List[int],
    row_cap: int,
    params: IntegratedParams,
) -> Tuple["pd.DataFrame", dict]:
    """
    DP forward pass to find the globally optimal 52-week production schedule.

    State: net inventory (integer, can be negative for backlog).
    DP value: (composite_cost, overtime_weeks, total_batches) tuple for tie-breaking.

    Parameters
    ----------
    demand : List[int]
        1-indexed total demand array.
    shutdown_weeks : List[int]
        Weeks with zero production.
    partial_shutdown_weeks : List[int]
        Weeks with max 1 batch (15 good units).
    eu_demand : List[int]
        1-indexed EU-restricted demand array. The QC shipping cap (``row_cap``)
        is enforced by requiring inventory to be pre-built ahead of any week whose
        EU-restricted demand exceeds the cap. Pass an all-zero array to disable.

        This used to be two parameters — ``row_demand`` and ``eu_demand`` — and
        every call site passed the same array to both. The second copy fed a
        cost-free FIFO queue that constrained nothing yet published an
        "unfulfilled" figure contradicting the constraint actually enforced here.
    row_cap : int
        Max EU-restricted units shippable in one week.
    params : IntegratedParams
        Model parameters.

    Returns
    -------
    plan_df : pd.DataFrame
        Weekly plan with all required columns.
    summary : dict
        Cost summary dictionary. ``total_composite_cost`` is the DP objective and
        never includes a post-solve advisory such as the quarterly quota.

    Raises
    ------
    RuntimeError
        If no feasible states exist at any week, or no solution at week T with
        inventory 0.
    ShippingCapInfeasibleError
        If the QC shipping cap cannot be met (raised from bound computation).
    """
    T = params.horizon_weeks
    shutdown_set = set(shutdown_weeks)
    partial_set = set(partial_shutdown_weeks)

    # Build cap_max per week
    cap_max = [0] * (T + 1)
    week_types = [""] * (T + 1)
    for t in range(1, T + 1):
        if t in shutdown_set:
            cap_max[t] = 0
            week_types[t] = SHUTDOWN
        elif t in partial_set:
            cap_max[t] = params.max_good_per_batch  # 15
            week_types[t] = PARTIAL
        else:
            cap_max[t] = params.overtime_max_good_week  # 45
            week_types[t] = NORMAL

    lb, ub = compute_inventory_bounds(demand, cap_max, params, eu_demand, row_cap)

    # Production levels the factory can physically hit, per week ceiling.
    levels = [realisable_good_levels(params, cap_max[t]) for t in range(T + 1)]

    # dp[inv] = (composite_cost, overtime_weeks, total_batches)
    # We use a dict to only track reachable states
    dp: dict[int, tuple] = {0: (0.0, 0, 0)}
    # One dict per week. Built per index, not `[{}] * (T + 1)`, which would alias
    # a single dict across every slot.
    prev: list[dict[int, tuple]] = [{} for _ in range(T + 1)]

    for t in range(1, T + 1):
        new_dp: dict[int, tuple] = {}
        new_prev: dict[int, tuple] = {}

        wt = week_types[t]
        d_t = demand[t]
        c_max = cap_max[t]
        week_levels = levels[t]

        for inv_prev, val_prev in dp.items():
            cost_prev, ot_prev, bat_prev = val_prev

            # Enforce minimum production: never create avoidable backlog.
            # The solver must produce at least enough so inv_new >= lb[t],
            # but also at least enough to cover demand when capacity allows.
            # y_min = max(0, d_t - inv_prev) ensures inv_new >= 0 when possible.
            # We only enforce this when capacity is available (c_max > 0).
            if c_max > 0:
                y_min = max(0, d_t - inv_prev)
                # Cap y_min at c_max — can't produce more than capacity allows
                y_min = min(y_min, c_max)
            else:
                y_min = 0

            # Enumerate only production levels the factory can actually hit.
            # A week runs whole batches, each yielding min_good..max_good units,
            # so not every integer is reachable once min_batch_produced is raised
            # above its default. At the default (1 good unit minimum) this is the
            # same range the solver used to walk blindly.
            for y in week_levels:
                if y < y_min:
                    continue
                inv_new = inv_prev + y - d_t

                # Prune: outside inventory bounds
                if inv_new < lb[t] or inv_new > ub[t]:
                    continue

                # Compute cost for this week
                cost_week = decompose_weekly_cost(inv_new, y, wt, params).composite

                # Overtime and batch tracking for tie-breaking
                ot_flag = 1 if y > params.normal_max_good_week else 0
                batches = batches_needed(y, params)

                new_cost = cost_prev + cost_week
                new_ot = ot_prev + ot_flag
                new_bat = bat_prev + batches
                candidate = (new_cost, new_ot, new_bat)

                if inv_new not in new_dp or candidate < new_dp[inv_new]:
                    new_dp[inv_new] = candidate
                    new_prev[inv_new] = (inv_prev, y)

        if not new_dp:
            raise RuntimeError(
                f"No feasible production states at week {t}. "
                "Check shutdown weeks and demand — total capacity may be insufficient."
            )

        dp = new_dp
        prev[t] = new_prev

    # Check terminal condition: inv must be 0 at week T
    if 0 not in dp:
        raise RuntimeError(
            f"No feasible solution with Net_Inventory_End = 0 at week {T}. "
            "Total demand cannot be satisfied within the planning horizon."
        )

    # Backward reconstruction
    y_plan = [0] * (T + 1)
    inv_plan = [0] * (T + 1)

    inv_cur = 0
    for t in range(T, 0, -1):
        inv_prev_val, y_t = prev[t][inv_cur]
        y_plan[t] = y_t
        inv_plan[t] = inv_cur
        inv_cur = inv_prev_val

    # Build plan DataFrame
    plan_df, summary = _build_plan_df(
        y_plan, inv_plan, demand, eu_demand, row_cap,
        week_types, cap_max, params
    )
    return plan_df, summary


def _build_plan_df(
    y_plan: List[int],
    inv_plan: List[int],
    demand: List[int],
    eu_demand: List[int],
    row_cap: int,
    week_types: List[str],
    cap_max: List[int],
    params: IntegratedParams,
) -> Tuple["pd.DataFrame", dict]:
    """
    Build the weekly plan DataFrame from reconstructed y and inv arrays.

    Cost figures come from ``domain.cost_model.decompose_weekly_cost`` — the one
    implementation of the formula. This function used to reimplement it, which is
    why ``domain.comparison`` carried a runtime assert cross-checking the two.

    Parameters
    ----------
    y_plan : List[int]
        Good units produced per week (1-indexed).
    inv_plan : List[int]
        Net inventory at end of each week (1-indexed).
    demand : List[int]
        Total demand per week (1-indexed).
    eu_demand : List[int]
        EU-restricted demand per week (1-indexed).
    row_cap : int
        Max EU-restricted units shippable per week.
    week_types : List[str]
        Week type per week (1-indexed).
    cap_max : List[int]
        Max good units per week (1-indexed).
    params : IntegratedParams
        Model parameters.

    Returns
    -------
    plan_df : pd.DataFrame
    summary : dict
    """
    T = params.horizon_weeks
    rows = []
    cumulative_cost = 0.0

    total_penalty = 0.0
    total_overtime = 0.0
    total_capacity = 0.0
    total_composite = 0.0
    total_ot_weeks = 0
    total_idle_slots = 0
    total_overtime_units = 0
    total_good = 0
    nominal_slots = 0

    for t in range(1, T + 1):
        y = y_plan[t]
        inv = inv_plan[t]
        wt = week_types[t]
        d = demand[t]
        eu_due = int(eu_demand[t]) if t < len(eu_demand) else 0

        # Batch breakdown — one shared splitter, so a raised min_batch_produced
        # cannot produce a batch smaller than it allows.
        batches = batches_needed(y, params)
        batch_goods = split_good_into_batches(y, params)
        while len(batch_goods) < 3:
            batch_goods.append(0)
        batch1, batch2, batch3 = batch_goods[0], batch_goods[1], batch_goods[2]
        produced_total = y + batches * params.test_discard_per_batch
        testing_discard = batches * params.test_discard_per_batch
        overtime_used = 1 if y > params.normal_max_good_week else 0

        # Inventory split
        early_held = max(0, inv)
        late_backlog = max(0, -inv)

        # QC shipping cap. These four columns describe the rule the solver
        # actually enforced, replacing a parallel FIFO queue that charged nothing
        # and could report an "unfulfilled" backlog the constraint had already
        # ruled out. Week 1 has no earlier week, so its pre-build requirement is
        # reported but was exempt from enforcement (see compute_inventory_bounds).
        prebuild_required = max(0, eu_due - row_cap)
        prebuild_available = max(0, inv_plan[t - 1]) if t >= 2 else 0

        cost = decompose_weekly_cost(inv, y, wt, params)
        cumulative_cost += cost.composite

        total_penalty += cost.penalty
        total_overtime += cost.overtime
        total_capacity += cost.capacity
        total_composite += cost.composite
        total_idle_slots += cost.idle_slots
        total_overtime_units += max(0, y - params.normal_max_good_week)
        total_good += y
        # Standing capacity this week offered: nothing in a shutdown, one batch in
        # a partial week, the normal ceiling otherwise.
        if wt != SHUTDOWN:
            nominal_slots += (
                params.max_good_per_batch if wt == PARTIAL
                else params.normal_max_good_week
            )
        if overtime_used:
            total_ot_weeks += 1

        rows.append({
            "Week": t,
            "Week_Type": wt,
            "Demand_Due": d,
            "Good_Production": y,
            "Batch_Count": batches,
            "Batch1_Produced": batch1,
            "Batch2_Produced": batch2,
            "Batch3_Produced": batch3,
            "Produced_Total": produced_total,
            "Testing_Discard": testing_discard,
            "Overtime_Used": overtime_used,
            "Net_Inventory_End": inv,
            "Early_Units_Held": early_held,
            "Late_Units_Backlog": late_backlog,
            "EU_Restricted_Due": eu_due,
            "EU_Shipping_Cap": row_cap,
            "EU_Prebuild_Required": prebuild_required,
            "EU_Prebuild_Available": prebuild_available,
            "Idle_Slots": cost.idle_slots,
            "Overtime_Units": max(0, y - params.normal_max_good_week),
            "Penalty_Cost_USD": cost.penalty,
            "Overtime_Cost_USD": cost.overtime,
            "Capacity_Utilization_Cost_USD": cost.capacity,
            "Composite_Cost_USD": cost.composite,
            "Cumulative_Composite_Cost_USD": cumulative_cost,
        })

    plan_df = pd.DataFrame(rows)

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
        # The two halves of the capacity component, and the slot counts behind
        # them. Computed at horizon level because the split depends on total
        # production equalling total demand, which is only true across the whole
        # plan. See domain.cost_model.split_capacity for why a per-week version
        # produced a negative "unavoidable" figure.
        "total_capacity_floor_cost": floor_cost,
        "total_capacity_levelling_cost": surcharge_cost,
        # Physical utilisation, independent of every cost rate. Reported so
        # "how much capacity did we lose" survives any repricing.
        "total_idle_slots": split.total_idle_slots,
        "unavoidable_idle_slots": split.unavoidable_idle_slots,
        "forced_overtime_units": split.forced_overtime_units,
        "discretionary_overtime_units": split.discretionary_overtime_units,
        "overtime_units": split.overtime_units,
        "nominal_slots": split.nominal_slots,
        "demand_exceeds_capacity": split.demand_exceeds_capacity,
        "overtime_weeks": total_ot_weeks,
        "w_penalty": params.w_penalty,
        "w_overtime": params.w_overtime,
        "w_capacity": params.w_capacity,
    }

    return plan_df, summary
