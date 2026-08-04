"""Backward-compatibility shim + CLI entry point.

Pure logic now lives in the ``domain`` package and file I/O in ``io_adapters``.
This module re-exports the public API so existing imports keep working, and
retains the argparse ``main`` CLI.
"""

from __future__ import annotations

from typing import List

from domain.params import IntegratedParams, _validate_weights
from domain.cost_model import compute_weekly_cost
from domain.demand import (
    REQUIRED_COLS,
    ROW_COUNTRIES,
    _norm_cols,
    clean_sites,
    build_weekly_demand,
    build_weekly_row_demand,
    batches_needed,
    split_good_into_batches,
)
from domain.solver import (
    compute_inventory_bounds,
    solve_plan_integrated,
    _build_plan_df,
)
from io_adapters.sites_reader import read_sites
from io_adapters.workbook_exporter import export_excel


def _parse_week_list(value: str) -> List[int]:
    """Parse a comma-separated string of week numbers into a sorted list of ints."""
    if not value or not value.strip():
        return []
    return sorted(int(w.strip()) for w in value.split(",") if w.strip())


def print_summary(summary: dict, active_count: int) -> None:
    """
    Print a console summary of the optimization run (Requirement 9.7).

    Parameters
    ----------
    summary : dict
        Cost summary dict returned by :func:`solve_plan_integrated`.
    active_count : int
        Number of active sites used in the plan.
    """
    print("\n=== Integrated Cost Optimization — Summary ===")
    print(f"  Active sites          : {active_count}")
    print(f"  Weights               : w_penalty={summary['w_penalty']:.3f}  "
          f"w_overtime={summary['w_overtime']:.3f}  "
          f"w_capacity={summary['w_capacity']:.3f}")
    print(f"  Total composite cost  : ${summary['total_composite_cost']:,.2f}")
    print(f"    Penalty component   : ${summary['total_penalty_cost']:,.2f}")
    print(f"    Overtime component  : ${summary['total_overtime_cost']:,.2f}")
    print(f"    Capacity component  : ${summary['total_capacity_cost']:,.2f}")
    print(f"  Overtime weeks        : {summary['overtime_weeks']}")
    print("==============================================\n")


def main() -> None:
    """
    CLI entry point for the integrated cost optimizer.

    All parameters are configurable via command-line arguments (Requirement 10.1).
    Validates weights before running the solver (Requirements 1.4, 1.5, 1.6, 10.2).
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="Integrated Cost Optimization Model — minimize weighted composite cost."
    )

    # Required arguments
    parser.add_argument("--input", required=True, help="Path to sites Excel or CSV file.")
    parser.add_argument("--output", required=True, help="Path to output Excel file.")

    # Optional input arguments
    parser.add_argument("--sites-sheet", default="Sites",
                        help="Sheet name for Excel input (default: Sites).")
    parser.add_argument("--shutdown-weeks", default="",
                        help="Comma-separated full shutdown week numbers (e.g. '1,2,3').")
    parser.add_argument("--partial-shutdown-weeks", default="",
                        help="Comma-separated partial shutdown week numbers (e.g. '4,5').")

    # Weight arguments
    parser.add_argument("--w-penalty", type=float, default=1.0,
                        help="Weight for penalty cost component [0.0–1.0] (default: 1.0).")
    parser.add_argument("--w-overtime", type=float, default=1.0,
                        help="Weight for overtime cost component [0.0–1.0] (default: 1.0).")
    parser.add_argument("--w-capacity", type=float, default=0.0,
                        help="Weight for capacity utilization cost component [0.0–1.0] (default: 0.0).")

    # Cost rate arguments
    parser.add_argument("--penalty-rate", type=float, default=7000.0,
                        help="USD per unit-week early inventory (default: 7000).")
    parser.add_argument("--late-penalty-multiplier", type=float, default=10.0,
                        help="Multiplier on penalty-rate for backlog weeks (default: 10).")
    parser.add_argument("--overtime-rate", type=float, default=2000.0,
                        help="USD per overtime week (default: 2000).")
    parser.add_argument("--capacity-rate", type=float, default=0.0,
                        help="USD per unused good unit slot per week (default: 0).")

    # Other optional arguments
    parser.add_argument("--row-cap", type=int, default=2,
                        help="Max ROW units fulfilled per week (default: 2).")
    parser.add_argument("--horizon", type=int, default=52,
                        help="Planning horizon in weeks (default: 52).")
    parser.add_argument("--print-summary", action="store_true",
                        help="Print a console summary after optimization.")

    args = parser.parse_args()

    # Validate weights before constructing params (Requirements 1.4, 1.5, 1.6, 10.2)
    _validate_weights(args.w_penalty, args.w_overtime, args.w_capacity)

    # Build params dataclass (will also validate via __post_init__)
    params = IntegratedParams(
        horizon_weeks=args.horizon,
        penalty_rate=args.penalty_rate,
        late_penalty_multiplier=args.late_penalty_multiplier,
        overtime_rate=args.overtime_rate,
        capacity_rate=args.capacity_rate,
        w_penalty=args.w_penalty,
        w_overtime=args.w_overtime,
        w_capacity=args.w_capacity,
        row_cap=args.row_cap,
    )

    # Parse shutdown week lists
    shutdown_weeks = _parse_week_list(args.shutdown_weeks)
    partial_shutdown_weeks = _parse_week_list(args.partial_shutdown_weeks)

    # Load and clean sites
    raw_df = read_sites(args.input, sites_sheet=args.sites_sheet)
    active_df, issues_df = clean_sites(raw_df, params)

    # Build demand arrays
    demand = build_weekly_demand(active_df, params)
    row_demand = build_weekly_row_demand(active_df, params)

    # Run solver
    plan_df, summary = solve_plan_integrated(
        demand, shutdown_weeks, partial_shutdown_weeks,
        row_demand, params.row_cap, params
    )

    # Export results
    export_excel(args.output, plan_df, active_df, issues_df, params, summary)
    print(f"Output written to: {args.output}")

    # Optional console summary (Requirement 9.7)
    if args.print_summary:
        print_summary(summary, active_count=len(active_df))


if __name__ == "__main__":
    main()
