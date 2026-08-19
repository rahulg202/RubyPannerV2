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
