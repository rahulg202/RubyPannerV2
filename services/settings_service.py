"""Service: assemble and validate all model settings from raw UI values.

One place builds the validated ``IntegratedParams`` + ``SupplierParams`` plus
reference date, calibration offset, quarter start, and shutdown/partial weeks,
so a single settings change flows consistently to every workflow (Requirement
[E-7.9]). Invalid values raise a single aggregated ``ValidationError``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from domain.errors import ValidationError
from domain.params import IntegratedParams, SupplierParams


# ---------------------------------------------------------------------------
# Settings bundle
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Settings:
    """Fully-validated configuration shared across all workflows."""

    params: IntegratedParams
    supplier_params: SupplierParams
    sheet: str = "Sites"
    shutdown_weeks: tuple[int, ...] = ()
    partial_shutdown_weeks: tuple[int, ...] = ()
    reference_week_date: date | None = None
    calibration_offset_days: int = 4


# ---------------------------------------------------------------------------
# Defaults (single source of truth for the UI's "restore defaults")
# ---------------------------------------------------------------------------

DEFAULTS: dict = {
    # IntegratedParams
    "horizon_weeks": 52,
    "min_batch_produced": 2,
    "max_batch_produced": 16,
    "test_discard_per_batch": 1,
    "normal_max_batches": 2,
    "overtime_max_batches": 3,
    "penalty_rate": 7000.0,
    "late_penalty_multiplier": 100.0,
    "overtime_rate": 2000.0,
    "capacity_rate": 15000.0,
    "w_penalty": 1.0,
    "w_overtime": 1.0,
    "w_capacity": 1.0,
    "row_cap": 2,
    # SupplierParams
    "per_generator_mci": 100.0,
    "per_batch_mci": 10.0,
    "minimum_surplus_mci": 20.0,
    "curium_surplus_pct": 0.05,
    "bwxt_surplus_pct": 0.02,
    "first_run_allocation": 15,
    "curium_quarterly_quota_mci": 10000.0,
    "bwxt_quarterly_quota_mci": 10000.0,
    "quota_shortfall_penalty_rate": 50000.0,
    "w_quota": 1.0,
    "quarter_start_month": 1,
    "curium_unavailable_weeks": "",
    "bwxt_unavailable_weeks": "",
    # Scheduling / dates
    "sheet": "Sites",
    "shutdown_weeks": "",
    "partial_shutdown_weeks": "",
    "reference_week_date": None,
    "calibration_offset_days": 4,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_week_list(value) -> tuple[list[int], str | None]:
    """Parse a comma-separated week string. Returns (weeks, error_or_None)."""
    if value is None:
        return [], None
    if isinstance(value, (list, tuple)):
        try:
            weeks = [int(w) for w in value]
        except (TypeError, ValueError):
            return [], "Week values must be integers."
    else:
        text = str(value).strip()
        if not text:
            return [], None
        weeks = []
        for token in (t.strip() for t in text.split(",")):
            if not token:
                return [], "Invalid format: empty entry between commas."
            try:
                weeks.append(int(token))
            except ValueError:
                return [], f"'{token}' is not a valid integer week number."
    for w in weeks:
        if w <= 0:
            return [], f"Week number must be positive, got {w}."
    return sorted(weeks), None


def _get(raw: dict, key: str):
    return raw.get(key, DEFAULTS[key])


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

def build_settings(raw: dict | None = None) -> Settings:
    """Assemble a validated :class:`Settings` from raw UI values.

    Missing keys fall back to :data:`DEFAULTS`. All validation failures are
    collected and raised together as a single :class:`ValidationError`.
    """
    raw = dict(DEFAULTS if raw is None else {**DEFAULTS, **raw})
    errors: list[str] = []

    # --- week lists ---
    shutdown, e1 = parse_week_list(raw["shutdown_weeks"])
    if e1:
        errors.append(f"Shutdown weeks: {e1}")
    partial, e2 = parse_week_list(raw["partial_shutdown_weeks"])
    if e2:
        errors.append(f"Partial shutdown weeks: {e2}")
    curium_unavail, e3 = parse_week_list(raw["curium_unavailable_weeks"])
    if e3:
        errors.append(f"Curium unavailable weeks: {e3}")
    bwxt_unavail, e4 = parse_week_list(raw["bwxt_unavailable_weeks"])
    if e4:
        errors.append(f"BWXT unavailable weeks: {e4}")

    # --- calibration offset ---
    try:
        cal_offset = int(raw["calibration_offset_days"])
        if cal_offset < 0:
            errors.append("Calibration offset must be >= 0 days.")
    except (TypeError, ValueError):
        errors.append("Calibration offset must be an integer number of days.")
        cal_offset = DEFAULTS["calibration_offset_days"]

    # --- reference date ---
    ref_date = raw["reference_week_date"]
    if ref_date is not None and not isinstance(ref_date, date):
        errors.append("Reference week must be a date or None.")
        ref_date = None

    # --- IntegratedParams (domain validation via __post_init__) ---
    params = None
    try:
        params = IntegratedParams(
            horizon_weeks=int(raw["horizon_weeks"]),
            min_batch_produced=int(raw["min_batch_produced"]),
            max_batch_produced=int(raw["max_batch_produced"]),
            test_discard_per_batch=int(raw["test_discard_per_batch"]),
            normal_max_batches=int(raw["normal_max_batches"]),
            overtime_max_batches=int(raw["overtime_max_batches"]),
            penalty_rate=float(raw["penalty_rate"]),
            late_penalty_multiplier=float(raw["late_penalty_multiplier"]),
            overtime_rate=float(raw["overtime_rate"]),
            capacity_rate=float(raw["capacity_rate"]),
            w_penalty=float(raw["w_penalty"]),
            w_overtime=float(raw["w_overtime"]),
            w_capacity=float(raw["w_capacity"]),
            row_cap=int(raw["row_cap"]),
        )
    except (ValueError, TypeError) as exc:
        errors.append(f"Production/cost parameters: {exc}")

    # --- SupplierParams (domain validation via __post_init__) ---
    supplier_params = None
    try:
        supplier_params = SupplierParams(
            per_generator_mci=float(raw["per_generator_mci"]),
            per_batch_mci=float(raw["per_batch_mci"]),
            minimum_surplus_mci=float(raw["minimum_surplus_mci"]),
            curium_surplus_pct=float(raw["curium_surplus_pct"]),
            bwxt_surplus_pct=float(raw["bwxt_surplus_pct"]),
            first_run_allocation=int(raw["first_run_allocation"]),
            curium_quarterly_quota_mci=float(raw["curium_quarterly_quota_mci"]),
            bwxt_quarterly_quota_mci=float(raw["bwxt_quarterly_quota_mci"]),
            quota_shortfall_penalty_rate=float(raw["quota_shortfall_penalty_rate"]),
            w_quota=float(raw["w_quota"]),
            quarter_start_month=int(raw["quarter_start_month"]),
            curium_unavailable_weeks=tuple(curium_unavail),
            bwxt_unavailable_weeks=tuple(bwxt_unavail),
        )
    except (ValueError, TypeError) as exc:
        errors.append(f"Supplier parameters: {exc}")

    if errors:
        raise ValidationError(errors)

    return Settings(
        params=params,
        supplier_params=supplier_params,
        sheet=str(raw["sheet"]),
        shutdown_weeks=tuple(shutdown),
        partial_shutdown_weeks=tuple(partial),
        reference_week_date=ref_date,
        calibration_offset_days=cal_offset,
    )


def default_settings() -> Settings:
    """Return the all-defaults settings bundle (for 'restore defaults')."""
    return build_settings(None)
