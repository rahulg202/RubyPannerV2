"""Domain: DP solver and plan construction (pure)."""

from __future__ import annotations

import math
from typing import List, Tuple

import pandas as pd

from domain.params import IntegratedParams
from domain.cost_model import compute_weekly_cost


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
    inventory beforehand (QC shipping cap limits how many restricted-country
    units can ship in a single week). This raises the lower bound on inventory
    at the end of week ``t-1`` by ``eu_demand[t] - row_cap``. When either
    argument is ``None`` the bounds are identical to the original behaviour.

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
            if eu_excess > 0 and (t - 1) >= 1 and (t - 1) < T:
                # Inventory at end of week t-1 must cover the excess.
                lb[t - 1] = max(lb[t - 1], eu_excess)

    return lb, ub



def solve_plan_integrated(
    demand: List[int],
    shutdown_weeks: List[int],
    partial_shutdown_weeks: List[int],
    row_demand: List[int],
    row_cap: int,
    params: IntegratedParams,
    eu_demand: List[int] | None = None,
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
    row_demand : List[int]
        1-indexed ROW demand array.
    row_cap : int
        Max ROW units fulfilled per week.
    params : IntegratedParams
        Model parameters.
    eu_demand : List[int] | None
        1-indexed EU-restricted demand array. When supplied, the QC shipping
        cap (``row_cap``) is enforced by requiring inventory to be pre-built
        ahead of any week whose EU-restricted demand exceeds ``row_cap``. When
        ``None`` (default), behaviour is identical to the original solver.

    Returns
    -------
    plan_df : pd.DataFrame
        Weekly plan with all required columns.
    summary : dict
        Cost summary dictionary.

    Raises
    ------
    RuntimeError
        If no feasible states exist at any week, or no solution at week 52 with inv=0.
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
            week_types[t] = "Shutdown"
        elif t in partial_set:
            cap_max[t] = params.max_good_per_batch  # 15
            week_types[t] = "Partial"
        else:
            cap_max[t] = params.overtime_max_good_week  # 45
            week_types[t] = "Normal"

    lb, ub = compute_inventory_bounds(demand, cap_max, params, eu_demand, row_cap)

    INF = (float("inf"), float("inf"), float("inf"))

    # dp[inv] = (composite_cost, overtime_weeks, total_batches)
    # We use a dict to only track reachable states
    dp: dict[int, tuple] = {0: (0.0, 0, 0)}
    prev: list[dict[int, tuple]] = [{}] * (T + 1)  # prev[t][inv] = (prev_inv, y)

    for t in range(1, T + 1):
        new_dp: dict[int, tuple] = {}
        new_prev: dict[int, tuple] = {}

        wt = week_types[t]
        d_t = demand[t]
        c_max = cap_max[t]

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

            # Enumerate all feasible production levels for this week.
            # Each batch yields 1..15 good units (batch size 2..16 minus 1 test discard).
            # With up to 3 batches, y can be any integer in [0, cap_max[t]].
            for y in range(y_min, c_max + 1):
                inv_new = inv_prev + y - d_t

                # Prune: outside inventory bounds
                if inv_new < lb[t] or inv_new > ub[t]:
                    continue

                # Compute cost for this week
                cost_week = compute_weekly_cost(inv_new, y, wt, params)

                # Overtime and batch tracking for tie-breaking
                ot_flag = 1 if y > params.normal_max_good_week else 0
                batches = math.ceil(y / params.max_good_per_batch) if y > 0 else 0

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
        y_plan, inv_plan, demand, row_demand, row_cap,
        week_types, cap_max, params
    )
    return plan_df, summary



def _build_plan_df(
    y_plan: List[int],
    inv_plan: List[int],
    demand: List[int],
    row_demand: List[int],
    row_cap: int,
    week_types: List[str],
    cap_max: List[int],
    params: IntegratedParams,
) -> Tuple["pd.DataFrame", dict]:
    """
    Build the weekly plan DataFrame from reconstructed y and inv arrays.

    Parameters
    ----------
    y_plan : List[int]
        Good units produced per week (1-indexed).
    inv_plan : List[int]
        Net inventory at end of each week (1-indexed).
    demand : List[int]
        Total demand per week (1-indexed).
    row_demand : List[int]
        ROW demand per week (1-indexed).
    row_cap : int
        Max ROW units fulfilled per week.
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
    row_inv = 0  # ROW inventory carried forward

    total_penalty = 0.0
    total_overtime = 0.0
    total_capacity = 0.0
    total_composite = 0.0
    total_ot_weeks = 0

    for t in range(1, T + 1):
        y = y_plan[t]
        inv = inv_plan[t]
        wt = week_types[t]
        d = demand[t]
        rd = row_demand[t]

        # Batch breakdown — each batch yields 1..15 good units
        batches = math.ceil(y / params.max_good_per_batch) if y > 0 else 0
        # Distribute good units across batches (fill earlier batches first)
        rem = y
        batch_goods = []
        for _ in range(batches):
            alloc = min(rem, params.max_good_per_batch)
            batch_goods.append(alloc)
            rem -= alloc
        while len(batch_goods) < 3:
            batch_goods.append(0)
        batch1, batch2, batch3 = batch_goods[0], batch_goods[1], batch_goods[2]
        produced_total = y + batches * params.test_discard_per_batch
        testing_discard = batches * params.test_discard_per_batch
        overtime_used = 1 if y > params.normal_max_good_week else 0

        # Inventory split
        early_held = max(0, inv)
        late_backlog = max(0, -inv)

        # ROW fulfillment
        row_fulfilled = min(rd + row_inv, row_cap)
        row_inv = max(0, row_inv + rd - row_fulfilled)

        # Cost breakdown
        if inv >= 0:
            penalty_cost = params.penalty_rate * inv
        else:
            penalty_cost = params.late_penalty_rate * abs(inv)

        overtime_cost = params.overtime_rate if y > params.normal_max_good_week else 0.0

        if wt == "Shutdown":
            capacity_cost = 0.0
        elif wt == "Partial":
            capacity_cost = params.capacity_rate * max(0, params.max_good_per_batch - y)
        else:
            capacity_cost = params.capacity_rate * max(0, params.normal_max_good_week - y)

        composite_cost = (
            params.w_penalty * penalty_cost
            + params.w_overtime * overtime_cost
            + params.w_capacity * capacity_cost
        )
        cumulative_cost += composite_cost

        total_penalty += penalty_cost
        total_overtime += overtime_cost
        total_capacity += capacity_cost
        total_composite += composite_cost
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
            "ROW_Demand_Due": rd,
            "ROW_Fulfilled": row_fulfilled,
            "ROW_Inventory": row_inv,
            "Penalty_Cost_USD": penalty_cost,
            "Overtime_Cost_USD": overtime_cost,
            "Capacity_Utilization_Cost_USD": capacity_cost,
            "Composite_Cost_USD": composite_cost,
            "Cumulative_Composite_Cost_USD": cumulative_cost,
        })

    plan_df = pd.DataFrame(rows)

    summary = {
        "total_composite_cost": total_composite,
        "total_penalty_cost": total_penalty,
        "total_overtime_cost": total_overtime,
        "total_capacity_cost": total_capacity,
        "overtime_weeks": total_ot_weeks,
        "w_penalty": params.w_penalty,
        "w_overtime": params.w_overtime,
        "w_capacity": params.w_capacity,
    }

    return plan_df, summary

