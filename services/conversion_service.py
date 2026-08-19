"""Service: build an optimizer input file from the team's manual plan.

The planning team already maintains the Master Planner by hand. Re-typing it into
the optimizer's input sheet took significant time and, because the two files
shared no site key, the Comparison tool could not line them up afterwards.

This service reads the manual plan, derives one row per site, and hands back both
the generated workbook and the checks that go with it — including a sanity check
that the derived delivery cadences reproduce roughly the same number of
deliveries the manual plan actually schedules.
"""

from __future__ import annotations

import pandas as pd

from domain.demand import _norm_cols, build_weekly_demand, clean_sites
from domain.params import IntegratedParams
from domain.site_derivation import mapping_frame, notes_frame, sites_frame
from services.dtos import ConversionRequest, ConversionResult
from services.ports import MasterPlannerConverterPort

# Flag the cadence model when implied deliveries drift more than this from the
# manual plan. Some drift is expected: the optimizer models a repeating cadence,
# while the manual plan shifts individual weeks for holidays and shutdowns.
DELIVERY_DRIFT_TOLERANCE = 0.10


class ConversionService:
    """Orchestrates Master Planner -> optimizer input file conversion."""

    def __init__(self, converter: MasterPlannerConverterPort) -> None:
        self._converter = converter

    def run(self, request: ConversionRequest) -> ConversionResult:
        """Convert the manual plan into an uploadable input file.

        Raises
        ------
        ValueError
            If the Master Planner sheet cannot be located or parsed.
        """
        derived = self._converter.convert(
            request.master_planner_bytes,
            request.master_planner_sheet,
            request.horizon_weeks,
            request.master_planner_year,
        )

        sites_df = sites_frame(derived.sites)
        warnings = list(derived.warnings)

        # Validate the generated sheet the same way the optimizer will, so
        # problems surface here rather than after the planner uploads it.
        # ``_norm_cols`` is the same normalization the sites reader applies, so
        # this sees exactly what the optimizer would.
        params = IntegratedParams(horizon_weeks=request.horizon_weeks)
        issues_df: pd.DataFrame | None = None
        implied = 0
        try:
            active, issues_df = clean_sites(_norm_cols(sites_df), params)
            implied = sum(build_weekly_demand(active, params)[1:])
        except ValueError as exc:  # pragma: no cover - defensive
            warnings.append(f"Generated sheet failed validation: {exc}")

        if issues_df is not None and not issues_df.empty:
            warnings.append(
                f"{len(issues_df)} generated row(s) would be skipped by the "
                f"optimizer. See the data-quality list below."
            )

        scheduled = sum(s.deliveries for s in derived.sites)
        if scheduled and abs(implied - scheduled) > scheduled * DELIVERY_DRIFT_TOLERANCE:
            warnings.append(
                f"The manual plan schedules {scheduled} deliveries this year, but "
                f"the derived cadences imply {implied}. Check the intervals in the "
                f"Site_Mapping sheet before optimizing."
            )

        return ConversionResult(
            sites_df=sites_df,
            mapping_df=mapping_frame(derived.sites),
            notes_df=notes_frame(derived.sites),
            xlsx_bytes=self._converter.write(derived),
            year=derived.year,
            site_count=len(derived.sites),
            active_count=len(derived.active_sites),
            generated_code_count=len(derived.generated_codes),
            eu_restricted_count=len(derived.eu_sites),
            scheduled_deliveries=scheduled,
            implied_deliveries=implied,
            issues_df=issues_df,
            warnings=warnings,
        )
