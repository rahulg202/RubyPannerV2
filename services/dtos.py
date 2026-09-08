"""Data Transfer Objects for the service layer.

Layers communicate through these explicit dataclasses rather than loosely-typed
dicts crossing boundaries. Requests into services are frozen; results are plain
dataclasses that may carry DataFrames.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from domain.comparison import BaselineResult
from domain.delivery_assignment import DeliveryRecord
from domain.onboarding import CombinationResult, NewCustomer
from domain.params import IntegratedParams, SupplierParams
from domain.quota import QuarterlyQuotaStatus

# Re-exported so the presentation layer imports customer definitions from one place.
__all__ = [
    "NewCustomer",
    "OptimizeRequest",
    "OptimizationResult",
    "OnboardingRequest",
    "OnboardingResult",
    "ComparisonRequest",
    "ComparisonResult",
    "ConversionRequest",
    "ConversionResult",
]


# ---------------------------------------------------------------------------
# Optimizer workflow
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OptimizeRequest:
    """Everything needed to run one optimization."""

    file_bytes: bytes
    filename: str
    sheet: str
    params: IntegratedParams
    supplier_params: SupplierParams
    shutdown_weeks: tuple[int, ...] = ()
    partial_shutdown_weeks: tuple[int, ...] = ()
    reference_week_date: date | None = None
    calibration_offset_days: int = 4
    # Optional Master Planner, used to compare per-customer weeks and flag new sites
    master_planner_bytes: bytes | None = None
    master_planner_sheet: str = "Schedule"


@dataclass
class OptimizationResult:
    """Everything produced by one optimization run."""

    plan_df: pd.DataFrame
    summary: dict
    issues_df: pd.DataFrame
    active_df: pd.DataFrame
    quota_status: list[QuarterlyQuotaStatus] = field(default_factory=list)
    assignments: list[DeliveryRecord] = field(default_factory=list)
    change_summary: dict = field(default_factory=dict)
    week_dates: list[tuple] = field(default_factory=list)  # (week, mfg_date, cal_date)
    xlsx_bytes: bytes | None = None
    warnings: list[str] = field(default_factory=list)
    # Row-level reconciliation against the manual plan (domain.site_matching).
    match_report: list = field(default_factory=list)
    match_summary: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Onboarding workflow
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OnboardingRequest:
    """Inputs for a multi-customer onboarding recommendation."""

    file_bytes: bytes
    filename: str
    sheet: str
    new_customers: tuple[NewCustomer, ...]
    params: IntegratedParams
    supplier_params: SupplierParams
    shutdown_weeks: tuple[int, ...] = ()
    partial_shutdown_weeks: tuple[int, ...] = ()
    reference_week_date: date | None = None
    max_seeds: int = 10
    max_passes: int = 3
    # "optimize" re-plans all 52 weeks for every candidate week (the default).
    # "fixed" holds the existing plan still and prices slotting the site in.
    mode: str = "optimize"
    master_planner_bytes: bytes | None = None
    master_planner_sheet: str = "Schedule"
    max_shift_weeks: int = 4


@dataclass
class OnboardingResult:
    """Ranked onboarding combinations plus the shared baseline."""

    base_summary: dict
    rankings: dict[str, list[CombinationResult]] = field(default_factory=dict)
    used_heuristic: bool = False
    combinations_evaluated: int = 0
    search_space: int = 0
    infeasible_count: int = 0
    infeasible_reasons: list[str] = field(default_factory=list)
    week_dates: list[tuple] = field(default_factory=list)
    # Fixed-plan mode results (domain.fixed_plan_onboarding.FixedPlanResult),
    # kept separate rather than shoehorned into ``rankings`` because they answer
    # a different question and carry different fields.
    mode: str = "optimize"
    fixed_rankings: list = field(default_factory=list)
    fixed_plan_weeks: int = 0
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Comparison workflow
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ComparisonRequest:
    """Inputs for the manual-plan vs optimized comparison."""

    master_planner_bytes: bytes
    master_planner_sheet: str
    optimized_summary: dict
    optimized_plan_df: pd.DataFrame
    demand: tuple[int, ...]
    params: IntegratedParams
    shutdown_weeks: tuple[int, ...] = ()
    partial_shutdown_weeks: tuple[int, ...] = ()
    master_planner_year: int | None = None
    # Must match what the input file was anchored to, or per-customer weeks are
    # offset between the two files.
    reference_week_date: date | None = None
    # (site_id, site_name) per active input row, for the row-level match report.
    active_sites: tuple[tuple[str, str], ...] = ()
    # EU-restricted demand per week. Needed to hold the manual plan to the same
    # QC shipping cap the optimizer obeys; without it the manual plan is scored
    # under a weaker rule set and can beat any plan the optimizer may build.
    eu_demand: tuple[int, ...] = ()


@dataclass
class ComparisonResult:
    """Baseline vs optimized, component-by-component."""

    components: list[dict] = field(default_factory=list)
    baseline: BaselineResult | None = None
    overtime_baseline: int = 0
    overtime_optimized: int = 0
    weekly_comparison: pd.DataFrame | None = None
    assigned_ids: list = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # Row-level reconciliation against the manual plan (domain.site_matching).
    match_report: list = field(default_factory=list)
    match_summary: dict = field(default_factory=dict)
    # (week, mfg_date, cal_date) taken from the Master Planner itself, so weeks
    # can be shown as dates even when no reference week has been set.
    week_dates: list[tuple] = field(default_factory=list)
    # Hard constraints the manual plan breaks. While this is non-empty the cost
    # difference is not a saving the optimizer earned -- it is what the manual
    # plan avoided paying by being unexecutable. The UI must say so.
    baseline_infeasibilities: list[str] = field(default_factory=list)
    # False when the manual plan's total differs from total demand. The baseline
    # cost is then meaningless rather than merely optimistic, because the shortfall
    # is charged at the solver's late-delivery deterrent rate. Suppress the money.
    baseline_totals_reconcile: bool = True

    @property
    def baseline_is_comparable(self) -> bool:
        """True when the manual plan obeys every constraint the optimizer obeys."""
        return not self.baseline_infeasibilities


# ---------------------------------------------------------------------------
# Conversion workflow (Master Planner -> optimizer input file)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ConversionRequest:
    """Inputs for building an input file out of the manual plan."""

    master_planner_bytes: bytes
    master_planner_sheet: str = "Schedule"
    horizon_weeks: int = 52
    master_planner_year: int | None = None
    # Anchors planning week 1 to a real manufacturing date. Takes precedence
    # over master_planner_year.
    reference_week_date: date | None = None


@dataclass
class ConversionResult:
    """A generated input file, with the mapping and checks that go with it."""

    sites_df: pd.DataFrame
    mapping_df: pd.DataFrame
    notes_df: pd.DataFrame
    xlsx_bytes: bytes
    year: int | None = None
    site_count: int = 0
    active_count: int = 0
    generated_code_count: int = 0
    eu_restricted_count: int = 0
    # Deliveries marked in the manual plan vs implied by the derived cadence —
    # they should be close; a wide gap means the cadences need review.
    scheduled_deliveries: int = 0
    implied_deliveries: int = 0
    issues_df: pd.DataFrame | None = None
    warnings: list[str] = field(default_factory=list)
    # What planning week 1 was anchored to, and how many weeks were found.
    anchor_date: date | None = None
    anchor_description: str = ""
    weeks_available: int = 0
    # How many columns would gain a code if the annotated copy were requested.
    codes_written: int = 0
    # Derived sites, kept so the annotated Master Planner copy can be produced on
    # demand rather than on every conversion.
    derived_sites: list = field(default_factory=list)
