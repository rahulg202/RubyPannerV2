"""Service: compare the planning team's manual plan against the optimized plan.

Reads the manual plan from the Master Planner workbook, evaluates it with the
same cost model as the optimizer, and returns a component-by-component
comparison with savings.
"""

from __future__ import annotations

from domain.comparison import compare_plans, compute_baseline_cost, weekly_comparison
from domain.site_matching import build_match_report, summarize_match
from services.dtos import ComparisonRequest, ComparisonResult
from services.ports import MasterPlannerReaderPort


def _week_dates(mp, horizon_weeks: int) -> list[tuple]:
    """``(week, mfg_date, cal_date)`` for weeks the Master Planner dates.

    Taken from the workbook rather than derived from a reference week, because the
    comparison is against that plan and those are the dates the planning team
    actually works to. Weeks with no date are omitted rather than guessed.
    """
    mfg = getattr(mp, "mfg_dates", None) or []
    cal = getattr(mp, "cal_dates", None) or []
    out: list[tuple] = []
    for week in range(1, horizon_weeks + 1):
        m = mfg[week] if week < len(mfg) else None
        if m is None:
            continue
        out.append((week, m, cal[week] if week < len(cal) else None))
    return out


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
            request.reference_week_date,
        )
        warnings.extend(mp.issues)
        if mp.rows_excluded:
            warnings.append(
                f"{mp.rows_excluded} Master Planner row(s) fell outside the "
                f"{request.params.horizon_weeks}-week horizon and were excluded."
            )

        # Sellable units only. Demand and the 30/45 weekly ceilings are both
        # expressed in sellable generators: a batch yields 16, one is destroyed
        # for QC, 15 ship. Adding mp.weekly_qc here would compare production
        # including the QC units against a demand and a capacity that exclude
        # them, inventing phantom inventory and phantom capacity breaches.
        # (domain.fixed_plan_onboarding takes weekly_commercial for the same
        # reason.)
        planned = mp.weekly_commercial
        demand = list(request.demand)

        baseline = compute_baseline_cost(
            planned,
            demand,
            request.params,
            request.shutdown_weeks,
            request.partial_shutdown_weeks,
            list(request.eu_demand) or None,
            request.params.row_cap if request.eu_demand else None,
        )

        if baseline.total_planned != baseline.total_demand:
            warnings.append(
                f"Manual plan total ({baseline.total_planned}) does not equal total "
                f"demand ({baseline.total_demand}); difference "
                f"{baseline.total_planned - baseline.total_demand:+d} units."
            )
        # The manual plan is evaluated, not solved, so it can break constraints the
        # optimizer cannot. Those breaches make it *cheaper*, because the
        # constraints are exactly what force stock to be built ahead of time. Say
        # so loudly rather than letting it read as the optimizer performing badly.
        for reason in baseline.infeasibilities:
            warnings.append(f"Manual plan {reason}.")

        components = compare_plans(baseline.summary, request.optimized_summary)
        weekly = weekly_comparison(planned, request.optimized_plan_df, request.params)

        # Row-level reconciliation. Named rows and a suggested fix beat a bare
        # percentage when the planner has to work out why a site went missing.
        match_report = build_match_report(
            list(request.active_sites),
            getattr(mp, "customer_headers", {}) or {},
            mp.customer_schedule.keys(),
        )
        match_summary = summarize_match(match_report)
        missing = match_summary.get("missing_from_manual_plan", 0)
        if missing:
            warnings.append(
                f"{missing} input row(s) had no Master Planner column, so their "
                f"per-customer figures are not comparable. See the site match "
                f"report below for the affected rows and how to fix them."
            )

        return ComparisonResult(
            components=components,
            baseline=baseline,
            overtime_baseline=int(baseline.summary.get("overtime_weeks", 0)),
            overtime_optimized=int(request.optimized_summary.get("overtime_weeks", 0)),
            weekly_comparison=weekly,
            assigned_ids=mp.assigned_ids,
            warnings=warnings,
            match_report=match_report,
            match_summary=match_summary,
            week_dates=_week_dates(mp, request.params.horizon_weeks),
            baseline_infeasibilities=baseline.infeasibilities,
        )
