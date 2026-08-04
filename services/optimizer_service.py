"""Service: run one full optimization.

Composes the domain layer and I/O adapters into the primary user workflow:
read sites, clean, build demand, solve with supplier constraints, disaggregate
deliveries to customers, attach calendar dates, and export.

Depends on port protocols, not concrete adapters — the presentation layer injects
implementations. See .kiro/specs/ARCHITECTURE.md.
"""

from __future__ import annotations

from domain.dates import derive_week_dates
from domain.delivery_assignment import (
    assign_deliveries,
    build_demand_events,
    compare_against_manual_plan,
    summarize_changes,
)
from domain.demand import build_weekly_demand, build_weekly_row_demand, clean_sites
from domain.errors import InfeasiblePlanError
from domain.supplier_solve import solve_with_suppliers
from services.dtos import OptimizeRequest, OptimizationResult
from services.ports import (
    MasterPlannerReaderPort,
    ResultExporterPort,
    SitesReaderPort,
)


LOW_MATCH_THRESHOLD = 0.5


def _match_rate_warnings(active_df, customer_schedule: dict) -> list[str]:
    """Warn when few sites match the Master Planner, which distorts change stats.

    A near-zero match rate usually means the two files use different identifier
    conventions (e.g. internal codes vs Master Planner account numbers), not that
    every customer is genuinely new.
    """
    site_ids = {str(s).strip() for s in active_df["site_id"].tolist()}
    if not site_ids or not customer_schedule:
        return []
    matched = site_ids & set(customer_schedule)
    rate = len(matched) / len(site_ids)
    if rate >= LOW_MATCH_THRESHOLD:
        return []
    return [
        f"Only {len(matched)} of {len(site_ids)} sites matched a Master Planner "
        f"column ({rate:.0%}). Changed-week and new-customer figures compared "
        "against the Master Planner are unreliable — the two files may use "
        "different Site_ID conventions."
    ]


class OptimizerService:
    """Orchestrates a single optimization run."""

    def __init__(
        self,
        sites_reader: SitesReaderPort,
        exporter: ResultExporterPort | None = None,
        master_planner_reader: MasterPlannerReaderPort | None = None,
    ) -> None:
        self._sites_reader = sites_reader
        self._exporter = exporter
        self._master_planner_reader = master_planner_reader

    def run(self, request: OptimizeRequest) -> OptimizationResult:
        """Execute the optimization and return a fully-populated result.

        Raises
        ------
        InfeasiblePlanError
            If the solver cannot produce a feasible plan.
        InfeasibleAllocationError
            If supplier allocation is impossible (propagated from the domain).
        ValueError
            If the input file is missing required columns (from the reader).
        """
        warnings: list[str] = []

        # --- Load and clean ---
        is_csv = str(request.filename).lower().endswith(".csv")
        raw_df = self._sites_reader.read(
            request.file_bytes, request.sheet, is_csv=is_csv
        )
        active_df, issues_df = clean_sites(raw_df, request.params)
        if active_df.empty:
            raise InfeasiblePlanError(
                "No active sites found in the input file after validation."
            )

        # --- Demand ---
        demand = build_weekly_demand(active_df, request.params)
        eu_demand = build_weekly_row_demand(active_df, request.params)

        # --- Solve (supplier-constrained, with row_cap enforcement) ---
        try:
            plan_df, summary, quota_status = solve_with_suppliers(
                demand,
                list(request.shutdown_weeks),
                list(request.partial_shutdown_weeks),
                eu_demand,
                request.params.row_cap,
                request.params,
                eu_demand,
                request.supplier_params,
                request.reference_week_date,
            )
        except RuntimeError as exc:
            # The DP solver signals infeasibility with RuntimeError; translate it.
            raise InfeasiblePlanError(str(exc)) from exc

        # --- Disaggregate deliveries to customers ---
        events = build_demand_events(active_df, request.params)
        y_plan = [0] + plan_df["Good_Production"].tolist()
        assignments = assign_deliveries(y_plan, events, request.params)

        # --- Compare against the manual plan (the change that matters) ---
        # Without a manual plan there is nothing to compare against, so the
        # changed-week report stays empty rather than substituting due dates.
        if request.master_planner_bytes and self._master_planner_reader:
            try:
                manual = self._master_planner_reader.parse(
                    request.master_planner_bytes, request.master_planner_sheet
                )
                assignments = compare_against_manual_plan(
                    assignments, manual.customer_schedule
                )
                warnings.extend(
                    _match_rate_warnings(active_df, manual.customer_schedule)
                )
            except Exception as exc:  # adapter/parse problem must not fail the run
                warnings.append(f"Could not compare against the manual plan: {exc}")
        else:
            warnings.append(
                "No manual plan supplied, so changed customer weeks were not "
                "computed. Upload the Master Planner workbook to see which "
                "customers the optimizer moved."
            )

        change_summary = summarize_changes(assignments)

        # --- Calendar dates ---
        week_dates: list[tuple] = []
        if request.reference_week_date is not None:
            week_dates = derive_week_dates(
                request.reference_week_date,
                request.calibration_offset_days,
                request.params.horizon_weeks,
            )

        # --- Export (unified workbook with every available section) ---
        xlsx_bytes = None
        if self._exporter is not None:
            xlsx_bytes = self._exporter.export(
                plan_df,
                active_df,
                issues_df,
                request.params,
                summary,
                supplier_params=request.supplier_params,
                quota_status=quota_status,
                assignments=assignments,
                week_dates=week_dates,
                reference_week_date=request.reference_week_date,
                calibration_offset_days=request.calibration_offset_days,
            )

        return OptimizationResult(
            plan_df=plan_df,
            summary=summary,
            issues_df=issues_df,
            active_df=active_df,
            quota_status=quota_status,
            assignments=assignments,
            change_summary=change_summary,
            week_dates=week_dates,
            xlsx_bytes=xlsx_bytes,
            warnings=warnings,
        )
