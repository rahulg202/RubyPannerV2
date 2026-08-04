"""Service: multi-customer onboarding recommendation and input-file generation.

Recommends a start week per new customer (within each customer's own window),
then — once the planner confirms a selection — produces an optimizer-ready input
file containing the new customers.
"""

from __future__ import annotations

from typing import Callable, Sequence

from domain.dates import derive_week_dates
from domain.demand import clean_sites
from domain.onboarding import NewCustomer, estimate_search, evaluate_multi_customer
from services.dtos import OnboardingRequest, OnboardingResult
from services.ports import InputFileWriterPort, SitesReaderPort

ProgressCallback = Callable[[float, str], None]


class OnboardingService:
    """Orchestrates onboarding evaluation and input-file generation."""

    def __init__(
        self,
        sites_reader: SitesReaderPort,
        input_file_writer: InputFileWriterPort | None = None,
    ) -> None:
        self._sites_reader = sites_reader
        self._writer = input_file_writer

    # ------------------------------------------------------------------

    def estimate(self, customers: Sequence[NewCustomer]) -> dict:
        """Describe the search space so the UI can warn before a long run."""
        return estimate_search(customers)

    def run(
        self,
        request: OnboardingRequest,
        progress: ProgressCallback | None = None,
    ) -> OnboardingResult:
        """Evaluate onboarding combinations and return ranked recommendations.

        Raises
        ------
        ValidationError
            If any new customer row is invalid (propagated from the domain).
        ValueError
            If the sites file is missing required columns (from the reader).
        """
        is_csv = str(request.filename).lower().endswith(".csv")
        raw_df = self._sites_reader.read(request.file_bytes, request.sheet, is_csv=is_csv)
        active_df, _issues = clean_sites(raw_df, request.params)

        outcome = evaluate_multi_customer(
            active_df,
            list(request.new_customers),
            request.params,
            request.supplier_params,
            request.shutdown_weeks,
            request.partial_shutdown_weeks,
            request.reference_week_date,
            progress,
            request.max_seeds,
            request.max_passes,
        )

        week_dates: list[tuple] = []
        if request.reference_week_date is not None:
            week_dates = derive_week_dates(
                request.reference_week_date, 4, request.params.horizon_weeks
            )

        return OnboardingResult(
            base_summary=outcome["base_summary"],
            rankings=outcome["rankings"],
            used_heuristic=outcome["used_heuristic"],
            combinations_evaluated=outcome["combinations_evaluated"],
            search_space=outcome["search_space"],
            infeasible_count=outcome["infeasible_count"],
            infeasible_reasons=outcome["infeasible_reasons"],
            week_dates=week_dates,
        )

    # ------------------------------------------------------------------

    def generate_input_file(
        self,
        file_bytes: bytes,
        filename: str,
        sheet: str,
        customers: Sequence[NewCustomer],
        selected_weeks: dict[str, int],
    ) -> bytes:
        """Produce an optimizer-ready sites file including the new customers.

        Raises
        ------
        RuntimeError
            If no input-file writer was injected.
        ValidationError
            If a Site_ID collides or a start week is missing.
        """
        if self._writer is None:
            raise RuntimeError("No input file writer configured for this service.")
        return self._writer.write(
            file_bytes, filename, sheet, list(customers), dict(selected_weeks)
        )
