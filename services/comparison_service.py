"""Service: compare the planning team's manual plan against the optimized plan.

Reads the manual plan from the Master Planner workbook, evaluates it with the
same cost model as the optimizer, and returns a component-by-component
comparison with savings.
"""

from __future__ import annotations

from domain.comparison import compare_plans, compute_baseline_cost, weekly_comparison
from services.dtos import ComparisonRequest, ComparisonResult
from services.ports import MasterPlannerReaderPort


class ComparisonService:
    """Orchestrates the manual-vs-optimized cost comparison."""

    def __init__(self, master_planner_reader: MasterPlannerReaderPort) -> None:
        self._reader = master_planner_reader

    def run(self, request: ComparisonRequest) -> ComparisonResult:
        """Parse the Master Planner, evaluate it, and compare against the optimum.

        Raises
        ------
        ValueError
            If the Master Planner sheet cannot be located or parsed.
        """
        warnings: list[str] = []

        mp = self._reader.parse(
            request.master_planner_bytes,
            request.master_planner_sheet,
            request.params.horizon_weeks,
            request.master_planner_year,
        )
        warnings.extend(mp.issues)
        if mp.rows_excluded:
            warnings.append(
                f"{mp.rows_excluded} Master Planner row(s) fell outside the "
                f"{request.params.horizon_weeks}-week horizon and were excluded."
            )

        planned = mp.weekly_planned_production
        demand = list(request.demand)

        baseline = compute_baseline_cost(
            planned,
            demand,
            request.params,
            request.shutdown_weeks,
            request.partial_shutdown_weeks,
        )

        if baseline.total_planned != baseline.total_demand:
            warnings.append(
                f"Manual plan total ({baseline.total_planned}) does not equal total "
                f"demand ({baseline.total_demand}); difference "
                f"{baseline.total_planned - baseline.total_demand:+d} units."
            )
        if baseline.capacity_violations:
            weeks = ", ".join(str(w) for w, _p, _c in baseline.capacity_violations[:8])
            warnings.append(
                f"Manual plan exceeds weekly capacity in "
                f"{len(baseline.capacity_violations)} week(s): {weeks}"
                + ("..." if len(baseline.capacity_violations) > 8 else "")
            )

        components = compare_plans(baseline.summary, request.optimized_summary)
        weekly = weekly_comparison(planned, request.optimized_plan_df, request.params)

        return ComparisonResult(
            components=components,
            baseline=baseline,
            overtime_baseline=int(baseline.summary.get("overtime_weeks", 0)),
            overtime_optimized=int(request.optimized_summary.get("overtime_weeks", 0)),
            weekly_comparison=weekly,
            assigned_ids=mp.assigned_ids,
            warnings=warnings,
        )
