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
    # EU-restricted units *due* this week. Kept for reporting.
    eu_restricted_demand: int = 0
    # EU-restricted units *made* this week, from the delivery assignment. This is
    # what the material rule actually binds on: a unit shipping in week 10 but
    # built in week 8 must come from a Curium run in week 8. The two differ
    # whenever the QC shipping cap forces early building.
    eu_restricted_production: int = 0
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
    eu_production: List[int] | None = None,
) -> List[WeeklySupplierAllocation]:
    """Allocate each week's production to Curium/BWXT per the run-sequencing rules.

    Rules
    -----
    - The first run of any split week is always Curium; every later run is BWXT.
      A two-run week is Curium, BWXT and a three-run week is Curium, BWXT, BWXT.
    - Curium's run produces ``first_run_allocation`` generators, raised to cover
      EU-restricted volume when that is larger. BWXT takes the remainder.
    - If a supplier is unavailable, it receives 0 generators that week.
    - EU-restricted volume must be covered by Curium generators; a BWXT-only
      week with EU-restricted volume is infeasible.

    Which EU figure binds
    ---------------------
    The material rule is about the week a generator is *made*, so when
    ``eu_production`` is supplied it drives both the Curium lift and
    ``eu_constraint_satisfied``. ``eu_demand`` (units *due*) is still recorded for
    reporting. Without an assignment the caller has no production figure, so
    ``eu_demand`` is used for both and the result matches the historical
    behaviour.

    The distinction only changes numbers when the QC shipping cap forces units to
    be built ahead of their due week — which is precisely what that cap does, so
    comparing Curium's week-``t`` allocation against week-``t`` *demand* was
    comparing two different populations of generators.

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
    eu_production : List[int] | None
        1-indexed EU-restricted units produced per week, from
        ``domain.delivery_assignment.eu_restricted_production``. Optional.

    Returns
    -------
    List[WeeklySupplierAllocation]
        One entry per week 1..horizon_weeks.

    Raises
    ------
    InfeasibleAllocationError
        If a week cannot be allocated (both suppliers unavailable with demand,
        or BWXT-only week with EU-restricted volume).
    """
    T = params.horizon_weeks
    mgpb = params.max_good_per_batch
    first_run = supplier_params.first_run_allocation
    curium_unavail = set(supplier_params.curium_unavailable_weeks)
    bwxt_unavail = set(supplier_params.bwxt_unavailable_weeks)

    allocations: List[WeeklySupplierAllocation] = []

    for t in range(1, T + 1):
        y = int(y_plan[t]) if t < len(y_plan) else 0
        eu_due = int(eu_demand[t]) if eu_demand is not None and t < len(eu_demand) else 0
        if eu_production is not None:
            eu_made = int(eu_production[t]) if t < len(eu_production) else 0
        else:
            eu_made = eu_due
        # The binding figure: what must come out of a Curium run this week.
        eu = eu_made

        curium_avail = t not in curium_unavail
        bwxt_avail = t not in bwxt_unavail

        if y == 0:
            allocations.append(
                WeeklySupplierAllocation(
                    week=t, total_good=0, curium_good=0, bwxt_good=0,
                    run_sequence=[], supplier_label="",
                    eu_restricted_demand=eu_due,
                    eu_restricted_production=eu_made,
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
            # BWXT only — impossible if EU-restricted units are made this week.
            if eu > 0:
                raise InfeasibleAllocationError(
                    "EU-restricted generators require Curium material, but Curium "
                    "is unavailable.",
                    week=t,
                )
            curium_good, bwxt_good = 0, y
            sequence = [BWXT] * batches
        elif not bwxt_avail:
            curium_good, bwxt_good = y, 0
            sequence = [CURIUM] * batches
        else:
            # Curium runs first and exactly once; every later run is BWXT.
            #
            # Curium's share is first_run_allocation, lifted to cover
            # EU-restricted demand when that is larger, since only Curium
            # material may serve those sites. The lift stops at one batch: a
            # bigger Curium share would need a second Curium run, which would
            # both break the agreed pattern and exceed the run count the solver
            # planned for the week. If EU-restricted demand ever exceeds one
            # batch, ``eu_constraint_satisfied`` goes false and says so rather
            # than the allocation quietly inventing an extra run.
            curium_good = min(y, max(first_run, min(eu, mgpb)))
            bwxt_good = y - curium_good
            if bwxt_good == 0:
                sequence = [CURIUM]
            else:
                sequence = [CURIUM] + [BWXT] * math.ceil(bwxt_good / mgpb)

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
                eu_restricted_demand=eu_due,
                eu_restricted_production=eu_made,
                eu_constraint_satisfied=(curium_good >= eu),
            )
        )

    return allocations


# ---------------------------------------------------------------------------
# Structural diagnostics
# ---------------------------------------------------------------------------

def zero_allocation_warnings(
    allocations: List[WeeklySupplierAllocation],
    supplier_params: SupplierParams,
    quarters: List[tuple[int, tuple[int, ...]]],
) -> List[str]:
    """Explain a supplier receiving nothing across a whole quarter.

    The run-sequencing rule gives Curium the entire week whenever weekly output is
    at or below ``first_run_allocation``: a single-run week has no second run for
    BWXT to serve. On a plan averaging 10 generators a week, BWXT therefore
    receives zero orders in every week of the year and breaches its quota in every
    quarter — and because allocation happens after the solve, no plan the
    optimizer can reach would change that.

    The resulting figure used to appear in the objective with no explanation. This
    names the cause so a planner can see it is structural rather than a planning
    failure, and that the lever is ``first_run_allocation`` or the commercial
    quota, not the schedule.

    Parameters
    ----------
    allocations : List[WeeklySupplierAllocation]
        Per-week allocation, as returned by :func:`allocate_suppliers_weekly`.
    supplier_params : SupplierParams
        Supplies ``first_run_allocation`` for the diagnosis.
    quarters : List[tuple[int, tuple[int, ...]]]
        ``(quarter_number, weeks)`` pairs. Partial quarters should be omitted by
        the caller, since they carry no penalty.

    Returns
    -------
    List[str]
        One message per starved supplier, naming the quarters. Empty when both
        suppliers are served.
    """
    by_week = {a.week: a for a in allocations}
    first_run = supplier_params.first_run_allocation
    messages: List[str] = []

    for supplier, getter in ((CURIUM, lambda a: a.curium_good),
                             (BWXT, lambda a: a.bwxt_good)):
        starved: List[int] = []
        for quarter, weeks in quarters:
            present = [by_week[w] for w in weeks if w in by_week]
            if not present:
                continue
            if any(getter(a) > 0 for a in present):
                continue
            starved.append(quarter)

        if not starved:
            continue

        quarter_list = ", ".join(f"Q{q}" for q in starved)
        message = (
            f"{supplier} received no allocation at all in {quarter_list}, so its "
            f"full quarterly quota is recorded as a shortfall."
        )
        if supplier == BWXT:
            producing = [
                a for a in allocations
                if a.week in {w for _q, weeks in quarters for w in weeks}
                and a.total_good > 0
            ]
            if producing and all(a.total_good <= first_run for a in producing):
                busiest = max(a.total_good for a in producing)
                message += (
                    f" This is structural, not a scheduling choice: no week in "
                    f"those quarters produces more than {busiest} generators, and "
                    f"Curium's first run alone covers up to "
                    f"{first_run}, so no second run exists for {BWXT} to supply. "
                    f"Supplier allocation happens after the solve, so the "
                    f"optimizer cannot act on this. Adjust "
                    f"'first_run_allocation' or the agreed quota."
                )
        messages.append(message)

    return messages


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
