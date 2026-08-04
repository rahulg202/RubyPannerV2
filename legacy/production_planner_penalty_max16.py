#!/usr/bin/env python3
"""Penalty-optimized weekly production planner (max batch produced = 16).

What it solves
--------------
Single product, single factory. Demand is due in specific weeks with recurring
intervals per site. Production happens weekly, in 0..3 batches:

* Each batch produces an integer number of units between 2 and 16.
* If a batch runs, exactly 1 unit is reserved for quality testing and discarded.
  Therefore each batch yields 1..15 usable ("good") units.
* Weeks normally allow up to 2 batches (<=30 good units); a 3rd batch is overtime
  (<=45 good units).

Unlike a strict JIT model, this planner allows early or late fulfillment. Early
units create inventory; late units create backlog. Both are penalized at
USD 7,000 per unit per week. The optimizer shifts production across weeks to
minimize:

1) Total penalty dollars (sum over weeks of 7000 * abs(net_inventory_end))
2) Number of overtime weeks
3) Overtime units (sum over weeks of max(0, good_production - 30))
4) Total batch count (tie-breaker)
5) Total produced units incl. test discards (tie-breaker)

It also supports recommending the best onboarding week (phase) for a new site
given a candidate start-week range.

Inputs
------
Excel or CSV with these columns (case-insensitive):
    Site_ID, Active (Y/N), Next_Demand_Week (1..52), Interval_Weeks (>=1)

Outputs
-------
Writes an Excel file with:
* Weekly_Plan
* Sites_Clean
* Input_Issues
* Model_Params
* Onboarding_Recommendation (optional)

Usage examples
--------------
  python production_planner_penalty_max16.py --input model.xlsx --sites-sheet Sites \
      --shutdown-weeks 10,22 --output plan_out.xlsx --print-summary

  python production_planner_penalty_max16.py --input sites.csv --output plan_out.xlsx

  python production_planner_penalty_max16.py --input model.xlsx --sites-sheet Sites \
      --recommend-onboarding --new-site-interval 6 --new-site-earliest 5 --new-site-latest 20 \
      --shutdown-weeks 10,22 --output plan_out.xlsx
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd


# -----------------------------
# Parameters
# -----------------------------


@dataclass(frozen=True)
class Params:
    horizon_weeks: int = 52

    # Batch produced bounds
    min_batch_produced: int = 2
    max_batch_produced: int = 16

    # Testing discard per batch
    test_discard_per_batch: int = 1

    # Batch limits per week
    normal_max_batches: int = 2
    overtime_max_batches: int = 3

    # Penalty per unit-week early or late
    penalty_per_unit_week: int = 7000

    @property
    def max_good_per_batch(self) -> int:
        return self.max_batch_produced - self.test_discard_per_batch  # 15

    @property
    def normal_max_good_week(self) -> int:
        return self.normal_max_batches * self.max_good_per_batch  # 30

    @property
    def overtime_max_good_week(self) -> int:
        return self.overtime_max_batches * self.max_good_per_batch  # 45


REQUIRED_COLS = ["site_id", "active", "next_demand_week", "interval_weeks"]
ROW_COUNTRIES = {"denmark", "uk", "netherlands", "sweden"}  # case-insensitive
DEFAULT_ROW_CAP_PER_WEEK = 2


# -----------------------------
# IO
# -----------------------------


def _norm_cols(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]
    return df


def read_sites(path: str, sites_sheet: str = "Sites") -> pd.DataFrame:
    if path.lower().endswith(".csv"):
        df = pd.read_csv(path)
    else:
        df = pd.read_excel(path, sheet_name=sites_sheet)
    df = _norm_cols(df)

    missing = [c for c in REQUIRED_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}. Found: {list(df.columns)}")
    return df


def clean_sites(df: pd.DataFrame, params: Params) -> Tuple[pd.DataFrame, pd.DataFrame]:
    d = df.copy()

    d["site_id"] = d["site_id"].astype(str).str.strip()
    d["active"] = d["active"].astype(str).str.strip().str.upper()
    d["is_active"] = d["active"].isin(["Y", "YES", "TRUE", "1"])

    # Handle country column (optional, default to empty)
    if "country" in d.columns:
        d["country"] = d["country"].astype(str).str.strip().str.lower()
    else:
        d["country"] = ""

    d["next_demand_week_num"] = pd.to_numeric(d["next_demand_week"], errors="coerce")
    d["interval_weeks_num"] = pd.to_numeric(d["interval_weeks"], errors="coerce")

    issues: List[Tuple[int, str, str]] = []
    active = d.loc[d["is_active"]].copy()

    for idx, r in active.iterrows():
        sid = r["site_id"]
        ndw = r["next_demand_week_num"]
        itv = r["interval_weeks_num"]

        if not sid or str(sid).lower() == "nan":
            issues.append((idx, str(sid), "Missing Site_ID"))
            continue
        if pd.isna(ndw) or pd.isna(itv):
            issues.append((idx, str(sid), "Missing Next_Demand_Week or Interval_Weeks"))
            continue
        if ndw < 1 or ndw > params.horizon_weeks:
            issues.append((idx, str(sid), f"Next_Demand_Week out of range 1..{params.horizon_weeks}"))
        if itv < 1:
            issues.append((idx, str(sid), "Interval_Weeks must be >= 1"))

    dupes = active["site_id"][active["site_id"].duplicated(keep=False)]
    if not dupes.empty:
        for sid in sorted(dupes.unique()):
            issues.append((-1, str(sid), "Duplicate Site_ID among active rows"))
        active = active[~active["site_id"].isin(dupes.unique())].copy()

    issues_df = pd.DataFrame(issues, columns=["row_index", "site_id", "issue"]).sort_values(
        ["issue", "site_id", "row_index"]
    )

    active["next_demand_week"] = active["next_demand_week_num"].astype(int)
    active["interval_weeks"] = active["interval_weeks_num"].astype(int)
    active["is_row"] = active["country"].isin(ROW_COUNTRIES)

    keep = ["site_id", "next_demand_week", "interval_weeks", "country", "is_row"]
    active = active[keep].reset_index(drop=True)
    return active, issues_df.reset_index(drop=True)


# -----------------------------
# Demand building
# -----------------------------


def build_weekly_demand(sites: pd.DataFrame, params: Params) -> List[int]:
    """Return demand array d[1..T] (index 0 unused)."""
    T = params.horizon_weeks
    d = [0] * (T + 1)
    for _, r in sites.iterrows():
        start = int(r["next_demand_week"])
        itv = int(r["interval_weeks"])
        w = start
        while 1 <= w <= T:
            d[w] += 1
            w += itv
    return d


def build_weekly_row_demand(sites: pd.DataFrame, params: Params) -> List[int]:
    """Return ROW demand array row_d[1..T] for ROW countries only."""
    T = params.horizon_weeks
    row_d = [0] * (T + 1)
    row_sites = sites[sites["is_row"] == True]
    for _, r in row_sites.iterrows():
        start = int(r["next_demand_week"])
        itv = int(r["interval_weeks"])
        w = start
        while 1 <= w <= T:
            row_d[w] += 1
            w += itv
    return row_d


def add_new_site_demand(
    base_d: List[int],
    start_week: int,
    interval: int,
    params: Params,
    units: int = 1,
) -> List[int]:
    T = params.horizon_weeks
    d = base_d.copy()
    w = start_week
    while 1 <= w <= T:
        d[w] += units
        w += interval
    return d


def add_new_site_row_demand(
    base_row_d: List[int],
    start_week: int,
    interval: int,
    params: Params,
    units: int = 1,
    is_row: bool = False,
) -> List[int]:
    """Add new site demand to ROW demand if site is ROW."""
    if not is_row:
        return base_row_d.copy()
    T = params.horizon_weeks
    row_d = base_row_d.copy()
    w = start_week
    while 1 <= w <= T:
        row_d[w] += units
        w += interval
    return row_d


# -----------------------------
# Batch utilities
# -----------------------------


def batches_needed(good_units: int, params: Params) -> int:
    if good_units <= 0:
        return 0
    return math.ceil(good_units / params.max_good_per_batch)  # 15


def split_good_into_batches(good_units: int, params: Params) -> List[int]:
    """Convert planned GOOD units into produced batch sizes (2..16)."""
    if good_units <= 0:
        return []

    b = batches_needed(good_units, params)
    if b > params.overtime_max_batches:
        raise ValueError("Requested good units require >3 batches, which is not allowed.")

    # Each batch must have 1..15 good units. Fill earliest batches first.
    good = [1] * b
    rem = good_units - b
    for i in range(b):
        if rem <= 0:
            break
        add = min(rem, params.max_good_per_batch - 1)  # up to 14
        good[i] += add
        rem -= add

    produced = [g + params.test_discard_per_batch for g in good]
    for p in produced:
        if p < params.min_batch_produced or p > params.max_batch_produced:
            raise ValueError("Batch size out of bounds after split.")
    if sum(p - params.test_discard_per_batch for p in produced) != good_units:
        raise ValueError("Split sanity check failed.")
    return produced


# -----------------------------
# Optimization (Dynamic Programming)
# -----------------------------


Cost = Tuple[int, int, int, int, int]
# (penalty_dollars, overtime_weeks, overtime_units, total_batches, total_produced_units)


def cost_add(inv_end: int, good_prod: int, params: Params) -> Cost:
    penalty = params.penalty_per_unit_week * abs(inv_end)
    ot_week = 1 if good_prod > params.normal_max_good_week else 0
    ot_units = max(0, good_prod - params.normal_max_good_week)
    b = batches_needed(good_prod, params)
    produced_units = good_prod + b  # 1 test discard per batch
    return (penalty, ot_week, ot_units, b, produced_units)


def cost_sum(a: Cost, b: Cost) -> Cost:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2], a[3] + b[3], a[4] + b[4])


def compute_inventory_bounds(d: List[int], cap_max: List[int], params: Params) -> Tuple[List[int], List[int]]:
    """Compute tight inventory bounds to keep DP states compact."""
    T = params.horizon_weeks
    rem_demand = [0] * (T + 2)
    rem_cap = [0] * (T + 2)
    for t in range(T, 0, -1):
        rem_demand[t] = rem_demand[t + 1] + d[t]
        rem_cap[t] = rem_cap[t + 1] + cap_max[t]

    lb = [0] * (T + 1)
    ub = [0] * (T + 1)
    for t in range(0, T + 1):
        ub[t] = rem_demand[t + 1]
        lb[t] = rem_demand[t + 1] - rem_cap[t + 1]

    lb[T] = ub[T] = 0
    return lb, ub


def solve_plan(
    d: List[int],
    shutdown_weeks: Iterable[int],
    params: Params,
    partial_shutdown_weeks: Iterable[int] = None,
    row_demand: List[int] = None,
    row_cap_per_week: int = DEFAULT_ROW_CAP_PER_WEEK,
) -> Tuple[pd.DataFrame, Cost]:
    """Solve for weekly good production y[t].
    
    Args:
        d: Total demand per week
        shutdown_weeks: Weeks with zero production allowed
        params: Model parameters
        partial_shutdown_weeks: Weeks with max 1 batch (15 good units)
        row_demand: ROW demand per week (for ROW constraint tracking)
        row_cap_per_week: Max ROW units that can be fulfilled per week
    """
    T = params.horizon_weeks
    shutdown = set(int(w) for w in shutdown_weeks if str(w).strip() != "")
    partial_shutdown = set(int(w) for w in (partial_shutdown_weeks or []) if str(w).strip() != "")

    # Initialize ROW demand tracking
    if row_demand is None:
        row_demand = [0] * (T + 1)

    cap_max = [0] * (T + 1)
    for t in range(1, T + 1):
        if t in shutdown:
            cap_max[t] = 0
        elif t in partial_shutdown:
            cap_max[t] = params.max_good_per_batch  # 15 (1 batch max)
        else:
            cap_max[t] = params.overtime_max_good_week  # 45

    lb, ub = compute_inventory_bounds(d, cap_max, params)

    dp: Dict[int, Cost] = {0: (0, 0, 0, 0, 0)}
    prev: List[Dict[int, Tuple[int, int]]] = [dict() for _ in range(T + 1)]

    # Track ROW inventory separately for constraint checking
    # ROW units can also be early/late, same penalty applies
    row_inv_tracker: Dict[int, int] = {0: 0}  # inv_state -> row_inv

    for t in range(1, T + 1):
        new_dp: Dict[int, Cost] = {}
        new_row_inv: Dict[int, int] = {}
        demand_t = d[t]
        row_demand_t = row_demand[t]
        cap_t = cap_max[t]

        for inv_prev, c_prev in dp.items():
            row_inv_prev = row_inv_tracker.get(inv_prev, 0)
            
            y_min = max(0, lb[t] - inv_prev + demand_t)
            y_max = min(cap_t, ub[t] - inv_prev + demand_t)
            if y_min > y_max:
                continue

            for y in range(y_min, y_max + 1):
                inv_new = inv_prev + y - demand_t
                
                # Calculate ROW fulfillment for this week
                # ROW units fulfilled = min(row_demand + row_backlog, row_cap, production)
                row_available = row_demand_t + max(-row_inv_prev, 0)  # demand + backlog
                row_fulfilled = min(row_available, row_cap_per_week, y)
                row_inv_new = row_inv_prev + row_fulfilled - row_demand_t
                
                # Penalty for ROW is included in overall inventory penalty
                # (ROW backlog contributes to overall backlog)
                
                c = cost_sum(c_prev, cost_add(inv_new, y, params))
                if (inv_new not in new_dp) or (c < new_dp[inv_new]):
                    new_dp[inv_new] = c
                    prev[t][inv_new] = (inv_prev, y)
                    new_row_inv[inv_new] = row_inv_new

        dp = new_dp
        row_inv_tracker = new_row_inv
        if not dp:
            raise RuntimeError(
                f"No feasible states at week {t}. Total capacity may be insufficient given shutdown weeks."
            )

    if 0 not in dp:
        raise RuntimeError("No solution ends with zero inventory/backlog at horizon end.")
    best_cost = dp[0]

    # Reconstruct
    y = [0] * (T + 1)
    inv = [0] * (T + 1)
    inv[T] = 0
    for t in range(T, 0, -1):
        inv_prev, y_t = prev[t][inv[t]]
        y[t] = y_t
        inv[t - 1] = inv_prev

    rows = []
    cum_penalty = 0
    row_inv_running = 0
    for t in range(1, T + 1):
        inv_end = inv[t]
        weekly_pen = params.penalty_per_unit_week * abs(inv_end)
        cum_penalty += weekly_pen
        batches = split_good_into_batches(y[t], params) if y[t] > 0 else []
        bcount = len(batches)
        produced_total = sum(batches)
        testing = bcount * params.test_discard_per_batch
        overtime = "Y" if y[t] > params.normal_max_good_week else "N"
        
        # Track ROW for reporting
        row_demand_t = row_demand[t]
        row_available = row_demand_t + max(-row_inv_running, 0)
        row_fulfilled = min(row_available, row_cap_per_week, y[t])
        row_inv_running = row_inv_running + row_fulfilled - row_demand_t
        
        # Determine week type
        if t in shutdown:
            week_type = "Shutdown"
        elif t in partial_shutdown:
            week_type = "Partial"
        else:
            week_type = "Normal"

        rows.append(
            {
                "Week": t,
                "Week_Type": week_type,
                "Demand_Due": d[t],
                "ROW_Demand_Due": row_demand_t,
                "Good_Production": y[t],
                "ROW_Fulfilled": row_fulfilled,
                "ROW_Inventory": row_inv_running,
                "Batch_Count": bcount,
                "Batch1_Produced": batches[0] if bcount >= 1 else "",
                "Batch2_Produced": batches[1] if bcount >= 2 else "",
                "Batch3_Produced": batches[2] if bcount >= 3 else "",
                "Produced_Total": produced_total,
                "Testing_Discard": testing,
                "Overtime_Used": overtime,
                "Net_Inventory_End": inv_end,
                "Early_Units_Held": max(inv_end, 0),
                "Late_Units_Backlog": max(-inv_end, 0),
                "Weekly_Penalty_USD": weekly_pen,
                "Cumulative_Penalty_USD": cum_penalty,
            }
        )

    return pd.DataFrame(rows), best_cost


# -----------------------------
# Onboarding recommendation
# -----------------------------


def recommend_onboarding(
    base_d: List[int],
    shutdown_weeks: Iterable[int],
    params: Params,
    interval: int,
    earliest: int,
    latest: int,
    units: int = 1,
    partial_shutdown_weeks: Iterable[int] = None,
    row_demand: List[int] = None,
    row_cap_per_week: int = DEFAULT_ROW_CAP_PER_WEEK,
    new_site_is_row: bool = False,
) -> pd.DataFrame:
    base_plan, base_cost = solve_plan(
        base_d, shutdown_weeks, params,
        partial_shutdown_weeks=partial_shutdown_weeks,
        row_demand=row_demand,
        row_cap_per_week=row_cap_per_week,
    )
    _ = base_plan  # silence unused

    results = []
    for start_week in range(earliest, latest + 1):
        d2 = add_new_site_demand(base_d, start_week, interval, params, units=units)
        row_d2 = add_new_site_row_demand(
            row_demand or [0] * (params.horizon_weeks + 1),
            start_week, interval, params, units=units, is_row=new_site_is_row
        )
        try:
            _, cost2 = solve_plan(
                d2, shutdown_weeks, params,
                partial_shutdown_weeks=partial_shutdown_weeks,
                row_demand=row_d2,
                row_cap_per_week=row_cap_per_week,
            )
            results.append(
                {
                    "Candidate_Start_Week": start_week,
                    "Total_Penalty_USD": cost2[0],
                    "Overtime_Weeks": cost2[1],
                    "Overtime_Units": cost2[2],
                    "Total_Batches": cost2[3],
                    "Total_Produced_Units": cost2[4],
                    "Delta_Penalty_USD_vs_Base": cost2[0] - base_cost[0],
                    "Delta_Overtime_Weeks_vs_Base": cost2[1] - base_cost[1],
                    "Feasible": True,
                    "Reason": "",
                }
            )
        except RuntimeError as e:
            results.append(
                {
                    "Candidate_Start_Week": start_week,
                    "Total_Penalty_USD": "",
                    "Overtime_Weeks": "",
                    "Overtime_Units": "",
                    "Total_Batches": "",
                    "Total_Produced_Units": "",
                    "Delta_Penalty_USD_vs_Base": "",
                    "Delta_Overtime_Weeks_vs_Base": "",
                    "Feasible": False,
                    "Reason": str(e),
                }
            )

    df = pd.DataFrame(results)
    feasible = df[df["Feasible"] == True].copy()
    infeasible = df[df["Feasible"] == False].copy()

    if not feasible.empty:
        feasible = feasible.sort_values(
            [
                "Total_Penalty_USD",
                "Overtime_Weeks",
                "Overtime_Units",
                "Total_Batches",
                "Total_Produced_Units",
            ],
            ascending=True,
        )
    return pd.concat([feasible, infeasible], ignore_index=True)


# -----------------------------
# Export
# -----------------------------


def export_excel(
    out_path: str,
    sites_clean: pd.DataFrame,
    issues: pd.DataFrame,
    plan: pd.DataFrame,
    onboarding: Optional[pd.DataFrame],
    params: Params,
) -> None:
    with pd.ExcelWriter(out_path, engine="openpyxl") as w:
        plan.to_excel(w, sheet_name="Weekly_Plan", index=False)
        sites_clean.to_excel(w, sheet_name="Sites_Clean", index=False)
        issues.to_excel(w, sheet_name="Input_Issues", index=False)
        model = pd.DataFrame(
            [
                ["Horizon_Weeks", params.horizon_weeks],
                ["Batch_Produced_Min", params.min_batch_produced],
                ["Batch_Produced_Max", params.max_batch_produced],
                ["Testing_Discard_Per_Batch", params.test_discard_per_batch],
                ["Good_Per_Batch_Max", params.max_good_per_batch],
                ["Normal_Max_Good_Per_Week", params.normal_max_good_week],
                ["Overtime_Max_Good_Per_Week", params.overtime_max_good_week],
                ["Penalty_USD_Per_Unit_Week", params.penalty_per_unit_week],
            ],
            columns=["Parameter", "Value"],
        )
        model.to_excel(w, sheet_name="Model_Params", index=False)
        if onboarding is not None:
            onboarding.to_excel(w, sheet_name="Onboarding_Recommendation", index=False)


# -----------------------------
# CLI
# -----------------------------


def parse_weeks_list(s: str) -> List[int]:
    if not s.strip():
        return []
    out = []
    for p in s.split(","):
        p = p.strip()
        if not p:
            continue
        out.append(int(p))
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="Sites input: .xlsx or .csv")
    ap.add_argument("--sites-sheet", default="Sites", help="Sheet name if Excel (default Sites)")
    ap.add_argument("--output", required=True, help="Output Excel file (e.g., plan_out.xlsx)")
    ap.add_argument("--shutdown-weeks", default="", help="Comma-separated full shutdown weeks (0 production)")
    ap.add_argument("--partial-shutdown-weeks", default="", help="Comma-separated partial shutdown weeks (max 1 batch = 15 units)")
    ap.add_argument("--horizon", type=int, default=52, help="Planning horizon in weeks")
    ap.add_argument("--penalty", type=int, default=7000, help="USD per unit-week early/late")
    ap.add_argument("--row-cap", type=int, default=DEFAULT_ROW_CAP_PER_WEEK, help="Max ROW units per week (default 2)")
    ap.add_argument("--recommend-onboarding", action="store_true", help="Run onboarding recommendation")
    ap.add_argument("--new-site-interval", type=int, default=0, help="New site interval weeks")
    ap.add_argument("--new-site-earliest", type=int, default=1, help="Earliest candidate start week")
    ap.add_argument("--new-site-latest", type=int, default=52, help="Latest candidate start week")
    ap.add_argument("--new-site-units", type=int, default=1, help="Number of identical new 1-unit sites")
    ap.add_argument("--new-site-is-row", action="store_true", help="New site is ROW country (Denmark/UK/Netherlands/Sweden)")
    ap.add_argument("--print-summary", action="store_true", help="Print summary")
    args = ap.parse_args(argv)

    params = Params(horizon_weeks=args.horizon, penalty_per_unit_week=args.penalty)
    shutdown_weeks = parse_weeks_list(args.shutdown_weeks)
    partial_shutdown_weeks = parse_weeks_list(args.partial_shutdown_weeks)

    sites_raw = read_sites(args.input, sites_sheet=args.sites_sheet)
    sites_clean, issues = clean_sites(sites_raw, params)
    d = build_weekly_demand(sites_clean, params)
    row_d = build_weekly_row_demand(sites_clean, params)

    plan, best_cost = solve_plan(
        d, shutdown_weeks, params,
        partial_shutdown_weeks=partial_shutdown_weeks,
        row_demand=row_d,
        row_cap_per_week=args.row_cap,
    )
    onboarding_df = None

    if args.recommend_onboarding:
        if args.new_site_interval <= 0:
            raise SystemExit("--new-site-interval must be >= 1 when --recommend-onboarding is set")
        onboarding_df = recommend_onboarding(
            base_d=d,
            shutdown_weeks=shutdown_weeks,
            params=params,
            interval=args.new_site_interval,
            earliest=args.new_site_earliest,
            latest=args.new_site_latest,
            units=args.new_site_units,
            partial_shutdown_weeks=partial_shutdown_weeks,
            row_demand=row_d,
            row_cap_per_week=args.row_cap,
            new_site_is_row=args.new_site_is_row,
        )

    export_excel(args.output, sites_clean, issues, plan, onboarding_df, params)

    if args.print_summary:
        total_penalty, ot_weeks, ot_units, total_batches, total_produced = best_cost
        print("=== Optimized Plan Summary (Penalty + Overtime) ===")
        print(f"Horizon weeks: {params.horizon_weeks}")
        print(f"Penalty rate: ${params.penalty_per_unit_week} per unit-week")
        print(f"Good units per batch: 1..{params.max_good_per_batch}")
        print(f"Normal max good/week: {params.normal_max_good_week}")
        print(f"Overtime max good/week: {params.overtime_max_good_week}")
        print(f"Full shutdown weeks: {shutdown_weeks}")
        print(f"Partial shutdown weeks (1 batch max): {partial_shutdown_weeks}")
        print(f"ROW cap per week: {args.row_cap}")
        print(f"Active sites: {len(sites_clean)}")
        print(f"ROW sites: {sites_clean['is_row'].sum()}")
        print(f"Total penalty: ${total_penalty:,}")
        print(f"Overtime weeks: {ot_weeks}")
        print(f"Overtime units: {ot_units}")
        print(f"Total batches: {total_batches}")
        print(f"Total produced units (incl. test discards): {total_produced}")
        if not issues.empty:
            print("\nInput issues (first 20):")
            print(issues.head(20).to_string(index=False))
        if onboarding_df is not None:
            print("\nTop onboarding candidates (first 10 feasible):")
            top = onboarding_df[onboarding_df["Feasible"] == True].head(10)
            if top.empty:
                print("No feasible candidates.")
            else:
                print(top.to_string(index=False))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
