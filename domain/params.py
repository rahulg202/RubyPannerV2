"""Domain: model parameters and validation (pure)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class IntegratedParams:
    """All configuration for the integrated cost optimizer."""

    # Production constraints
    horizon_weeks: int = 52
    min_batch_produced: int = 2
    max_batch_produced: int = 16
    test_discard_per_batch: int = 1
    normal_max_batches: int = 2
    overtime_max_batches: int = 3

    # Cost rates
    penalty_rate: float = 7000.0          # USD per unit-week early inventory
    late_penalty_multiplier: float = 100.0  # multiplier for backlog penalty
    overtime_rate: float = 2000.0          # USD per overtime week (3rd batch)
    capacity_rate: float = 15000.0             # USD per unused good unit slot per week

    # Weights (0.0 to 1.0)
    w_penalty: float = 1.0
    w_overtime: float = 1.0
    w_capacity: float = 1.0

    # ROW constraint
    row_cap: int = 2

    def __post_init__(self) -> None:
        _validate_weights(self.w_penalty, self.w_overtime, self.w_capacity)

    # ------------------------------------------------------------------
    # Derived properties
    # ------------------------------------------------------------------

    @property
    def late_penalty_rate(self) -> float:
        """Penalty rate applied to backlog (last-resort late delivery)."""
        return self.penalty_rate * self.late_penalty_multiplier

    @property
    def max_good_per_batch(self) -> int:
        """Good units produced per batch (after discarding 1 test unit)."""
        return self.max_batch_produced - self.test_discard_per_batch  # 15

    @property
    def normal_max_good_week(self) -> int:
        """Maximum good units in a normal week (2 batches × 15)."""
        return self.normal_max_batches * self.max_good_per_batch  # 30

    @property
    def overtime_max_good_week(self) -> int:
        """Maximum good units in an overtime week (3 batches × 15)."""
        return self.overtime_max_batches * self.max_good_per_batch  # 45



def _validate_weights(w_penalty: float, w_overtime: float, w_capacity: float) -> None:
    """
    Validate that all weights are in [0.0, 1.0] and at least one is non-zero.

    Raises
    ------
    ValueError
        If any weight is outside [0.0, 1.0] or all weights are 0.0.
    """
    weights = {"w_penalty": w_penalty, "w_overtime": w_overtime, "w_capacity": w_capacity}
    for name, value in weights.items():
        if not (0.0 <= value <= 1.0):
            raise ValueError(
                f"Weight '{name}' must be in [0.0, 1.0], got {value}."
            )
    if w_penalty == 0.0 and w_overtime == 0.0 and w_capacity == 0.0:
        raise ValueError(
            "All weights (w_penalty, w_overtime, w_capacity) are 0.0. "
            "At least one weight must be non-zero to define an optimization objective."
        )

@dataclass(frozen=True)
class SupplierParams:
    """All raw-material supplier configuration for the Curium/BWXT model.

    See .kiro/specs/supplier-constraints/design.md. All fields are configurable
    from the front end; defaults reproduce the reference Master Planner workbook.
    """

    # Sr-82 activity formula parameters
    per_generator_mci: float = 100.0       # mCi per good generator
    per_batch_mci: float = 10.0            # mCi per batch (one QC generator)
    minimum_surplus_mci: float = 20.0      # Floor on the surplus term
    curium_surplus_pct: float = 0.05       # 5% surplus for Curium
    bwxt_surplus_pct: float = 0.02         # 2% surplus for BWXT

    # Run sequencing
    first_run_allocation: int = 15         # Generators in first Curium run of a split week

    # Quarterly quota
    curium_quarterly_quota_mci: float = 10000.0
    bwxt_quarterly_quota_mci: float = 10000.0
    quota_shortfall_penalty_rate: float = 50000.0  # USD per mCi shortfall (high default)
    w_quota: float = 1.0                   # Weight for quota shortfall in composite

    # Quarter boundaries (1 = January)
    quarter_start_month: int = 1

    # Availability: weeks where a supplier cannot supply (empty = always available)
    curium_unavailable_weeks: tuple[int, ...] = ()
    bwxt_unavailable_weeks: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        _validate_supplier_params(self)

    @property
    def max_good_per_batch(self) -> int:
        """Good units per batch, mirroring IntegratedParams (16 - 1 test discard)."""
        return 15


def _validate_supplier_params(p: "SupplierParams") -> None:
    """Validate supplier parameters. Raises ValueError with a clear message."""
    for name, value in (
        ("curium_surplus_pct", p.curium_surplus_pct),
        ("bwxt_surplus_pct", p.bwxt_surplus_pct),
    ):
        if not (0.0 <= value <= 1.0):
            raise ValueError(f"'{name}' must be in [0.0, 1.0], got {value}.")

    if p.minimum_surplus_mci < 0:
        raise ValueError(
            f"'minimum_surplus_mci' must be >= 0, got {p.minimum_surplus_mci}."
        )
    if p.per_generator_mci < 0:
        raise ValueError(
            f"'per_generator_mci' must be >= 0, got {p.per_generator_mci}."
        )
    if p.per_batch_mci < 0:
        raise ValueError(f"'per_batch_mci' must be >= 0, got {p.per_batch_mci}.")

    if p.curium_quarterly_quota_mci < 0:
        raise ValueError(
            f"'curium_quarterly_quota_mci' must be >= 0, got {p.curium_quarterly_quota_mci}."
        )
    if p.bwxt_quarterly_quota_mci < 0:
        raise ValueError(
            f"'bwxt_quarterly_quota_mci' must be >= 0, got {p.bwxt_quarterly_quota_mci}."
        )
    if p.quota_shortfall_penalty_rate < 0:
        raise ValueError(
            f"'quota_shortfall_penalty_rate' must be >= 0, got {p.quota_shortfall_penalty_rate}."
        )

    if not (0.0 <= p.w_quota <= 1.0):
        raise ValueError(f"'w_quota' must be in [0.0, 1.0], got {p.w_quota}.")

    if not (0 <= p.first_run_allocation <= p.max_good_per_batch):
        raise ValueError(
            f"'first_run_allocation' must be in [0, {p.max_good_per_batch}], "
            f"got {p.first_run_allocation}."
        )

    if not (1 <= p.quarter_start_month <= 12):
        raise ValueError(
            f"'quarter_start_month' must be in [1, 12], got {p.quarter_start_month}."
        )

    for name, weeks in (
        ("curium_unavailable_weeks", p.curium_unavailable_weeks),
        ("bwxt_unavailable_weeks", p.bwxt_unavailable_weeks),
    ):
        for w in weeks:
            if w < 1:
                raise ValueError(
                    f"'{name}' must contain positive week numbers, got {w}."
                )
