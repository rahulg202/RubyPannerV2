"""Domain: raw-material supplier allocation (pure).

Given an aggregate weekly production plan (``y_plan`` from the DP solver), this
module deterministically allocates each week's good generators between the two
suppliers (Curium, BWXT), following the physical run-sequencing rules, computes
each supplier's Sr-82 activity, and verifies the EU-restricted material rule.

See .kiro/specs/supplier-constraints/design.md. No I/O, no UI.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List

from domain.errors import InfeasibleAllocationError
from domain.params import IntegratedParams, SupplierParams

CURIUM = "Curium"
BWXT = "BWXT"


# ---------------------------------------------------------------------------
# Activity formula
# ---------------------------------------------------------------------------

def compute_activity(
    generators: int,
    surplus_pct: float,
    params: SupplierParams,
) -> float:
    """Return the Sr-82 activity (mCi) for ``generators`` from one supplier.

    activity = base + max(ceil(surplus_pct * base), minimum_surplus)
    base      = per_generator_mci * G + per_batch_mci * ceil(G / max_good_per_batch)

    Zero generators consume zero activity (the minimum-surplus floor does not
    apply). Reproduces the six reference weeks in the strontium workbook exactly.
    """
    if generators <= 0:
        return 0.0
    batch_count = math.ceil(generators / params.max_good_per_batch)
    base = params.per_generator_mci * generators + params.per_batch_mci * batch_count
    surplus = max(math.ceil(surplus_pct * base), params.minimum_surplus_mci)
    return float(base + surplus)


# ---------------------------------------------------------------------------
# Per-week allocation result
# ---------------------------------------------------------------------------

@dataclass
class WeeklySupplierAllocation:
    """Supplier allocation and Sr-82 activity for one production week."""

    week: int
    total_good: int
    curium_good: int
    bwxt_good: int
    run_sequence: List[str] = field(default_factory=list)
    curium_activity_mci: float = 0.0
    bwxt_activity_mci: float = 0.0
    total_activity_mci: float = 0.0
    supplier_label: str = ""
    eu_restricted_demand: int = 0
    eu_constraint_satisfied: bool = True


def _supplier_label(curium_good: int, bwxt_good: int) -> str:
    if curium_good > 0 and bwxt_good > 0:
        return f"{CURIUM} / {BWXT}"
    if curium_good > 0:
        return CURIUM
    if bwxt_good > 0:
        return BWXT
    return ""


# ---------------------------------------------------------------------------
# Weekly allocation
# ---------------------------------------------------------------------------

def allocate_suppliers_weekly(
    y_plan: List[int],
    eu_demand: List[int],
    params: IntegratedParams,
    supplier_params: SupplierParams,
) -> List[WeeklySupplierAllocation]:
    """Allocate each week's production to Curium/BWXT per the run-sequencing rules.

    Rules
    -----
    - The first run of any split week is always Curium.
    - A second Curium run is only permitted in three-run weeks: the pattern is
      Curium, BWXT, Curium.
    - If a supplier is unavailable, it receives 0 generators that week.
    - EU-restricted demand must be covered by Curium generators; a BWXT-only
      week with EU-restricted demand is infeasible.

    Parameters
    ----------
    y_plan : List[int]
        1-indexed good units produced per week (index 0 unused).
    eu_demand : List[int]
        1-indexed EU-restricted demand per week (index 0 unused).
    params : IntegratedParams
        Production parameters (horizon, max_good_per_batch).
    supplier_params : SupplierParams
        Supplier configuration (availability, first_run_allocation, surplus).

    Returns
    -------
    List[WeeklySupplierAllocation]
        One entry per week 1..horizon_weeks.

    Raises
    ------
    InfeasibleAllocationError
        If a week cannot be allocated (both suppliers unavailable with demand,
        or BWXT-only week with EU-restricted demand).
    """
    T = params.horizon_weeks
    mgpb = params.max_good_per_batch
    first_run = supplier_params.first_run_allocation
    curium_unavail = set(supplier_params.curium_unavailable_weeks)
    bwxt_unavail = set(supplier_params.bwxt_unavailable_weeks)

    allocations: List[WeeklySupplierAllocation] = []

    for t in range(1, T + 1):
        y = int(y_plan[t]) if t < len(y_plan) else 0
        eu = int(eu_demand[t]) if eu_demand is not None and t < len(eu_demand) else 0

        curium_avail = t not in curium_unavail
        bwxt_avail = t not in bwxt_unavail

        if y == 0:
            allocations.append(
                WeeklySupplierAllocation(
                    week=t, total_good=0, curium_good=0, bwxt_good=0,
                    run_sequence=[], supplier_label="", eu_restricted_demand=eu,
                    eu_constraint_satisfied=(eu == 0),
                )
            )
            continue

        if not curium_avail and not bwxt_avail:
            raise InfeasibleAllocationError(
                "Both suppliers unavailable but production is required.", week=t
            )

        batches = math.ceil(y / mgpb)

        if not curium_avail:
            # BWXT only — impossible if EU-restricted demand exists this week.
            if eu > 0:
                raise InfeasibleAllocationError(
                    "EU-restricted demand requires Curium, but Curium is unavailable.",
                    week=t,
                )
            curium_good, bwxt_good = 0, y
            sequence = [BWXT] * batches
        elif not bwxt_avail:
            curium_good, bwxt_good = y, 0
            sequence = [CURIUM] * batches
        elif y <= first_run:
            # Single Curium run covers the week.
            curium_good, bwxt_good = y, 0
            sequence = [CURIUM]
        elif batches == 2:
            curium_good = first_run
            bwxt_good = y - first_run
            sequence = [CURIUM, BWXT]
        elif batches == 3:
            # Only permitted three-run split: Curium, BWXT, Curium.
            bwxt_good = min(y - first_run, mgpb)
            curium_good = y - bwxt_good
            sequence = [CURIUM, BWXT, CURIUM]
        else:
            # batches == 1 with y > first_run (only if first_run < mgpb): one Curium run.
            curium_good, bwxt_good = y, 0
            sequence = [CURIUM]

        curium_activity = compute_activity(
            curium_good, supplier_params.curium_surplus_pct, supplier_params
        )
        bwxt_activity = compute_activity(
            bwxt_good, supplier_params.bwxt_surplus_pct, supplier_params
        )

        allocations.append(
            WeeklySupplierAllocation(
                week=t,
                total_good=y,
                curium_good=curium_good,
                bwxt_good=bwxt_good,
                run_sequence=sequence,
                curium_activity_mci=curium_activity,
                bwxt_activity_mci=bwxt_activity,
                total_activity_mci=curium_activity + bwxt_activity,
                supplier_label=_supplier_label(curium_good, bwxt_good),
                eu_restricted_demand=eu,
                eu_constraint_satisfied=(curium_good >= eu),
            )
        )

    return allocations


# ---------------------------------------------------------------------------
# Pre-solve feasibility check
# ---------------------------------------------------------------------------

def validate_supplier_feasibility(
    demand: List[int],
    eu_demand: List[int],
    shutdown_weeks: List[int],
    supplier_params: SupplierParams,
    horizon_weeks: int,
) -> List[str]:
    """Return a list of feasibility errors (empty if none) before solving.

    Flags:
    - A week with EU-restricted demand where Curium is unavailable.
    - A week with demand where both suppliers are unavailable and it is not a
      declared shutdown week.
    """
    errors: List[str] = []
    curium_unavail = set(supplier_params.curium_unavailable_weeks)
    bwxt_unavail = set(supplier_params.bwxt_unavailable_weeks)
    shutdown_set = set(shutdown_weeks)

    for t in range(1, horizon_weeks + 1):
        d = demand[t] if t < len(demand) else 0
        eu = eu_demand[t] if eu_demand is not None and t < len(eu_demand) else 0

        if eu > 0 and t in curium_unavail:
            errors.append(
                f"Week {t}: EU-restricted demand ({eu}) requires Curium, "
                "but Curium is marked unavailable."
            )
        if (
            d > 0
            and t in curium_unavail
            and t in bwxt_unavail
            and t not in shutdown_set
        ):
            errors.append(
                f"Week {t}: demand ({d}) is due but both suppliers are unavailable "
                "and the week is not a declared shutdown."
            )

    return errors
