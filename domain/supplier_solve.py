"""Domain: supplier-constrained solve orchestration (pure composition).

Wraps the DP solver with delivery assignment, post-solve supplier allocation and
quarterly quota accounting. Kept separate from ``supplier_allocation`` to avoid an
import cycle with ``quota`` (which depends on ``supplier_allocation``).

What ``total_composite_cost`` means
-----------------------------------
It is the DP objective and nothing else: ``w_penalty*penalty +
w_overtime*overtime + w_capacity*capacity``. The quarterly quota shortfall is
**not** in it.

That matters because supplier allocation runs *after* the solve, so the solver
cannot act on a quota shortfall — yet the shortfall used to be added to the figure
reported as the optimum, and the manual-plan baseline never computed it at all.
The result was a comparison that reported a $2bn loss for a plan compared against
itself. The quota is now carried separately as an advisory, computed the same way
on both sides:

    total_composite_cost      the DP objective
    total_quota_penalty_cost  post-solve advisory the solver could not act on
    total_cost_with_quota     composite + w_quota * quota

See .kiro/specs/correctness-remediation/requirements.md R2, R3.
"""

from __future__ import annotations

from datetime import date
from typing import List, Sequence, Tuple

import pandas as pd

from domain.delivery_assignment import (
    DeliveryRecord,
    DemandEvent,
    assign_deliveries,
    eu_restricted_production,
)
from domain.params import IntegratedParams, SupplierParams
from domain.quota import (
    QuarterlyQuotaStatus,
    check_quarterly_quota,
    compute_quarter_boundaries,
    partial_quarter_note,
)
from domain.solver import solve_plan_integrated
from domain.supplier_allocation import (
    allocate_suppliers_weekly,
    validate_supplier_feasibility,
    zero_allocation_warnings,
)
from domain.errors import InfeasibleAllocationError

QUOTA_ADVISORY_LABEL = (
    "Quarterly quota shortfall is computed after the production plan is solved, "
    "so the optimizer could not trade it against the other costs. It is reported "
    "as an advisory, not as part of the optimized objective."
)


def solve_with_suppliers(
    demand: List[int],
    shutdown_weeks: List[int],
    partial_shutdown_weeks: List[int],
    eu_demand: List[int],
    row_cap: int,
    params: IntegratedParams,
    supplier_params: SupplierParams,
    reference_week_date: date | None = None,
    demand_events: Sequence[DemandEvent] | None = None,
) -> Tuple[pd.DataFrame, dict, List[QuarterlyQuotaStatus], List[DeliveryRecord]]:
    """Run the DP solver, assign deliveries, then allocate suppliers and quota.

    Order matters. The EU-restricted material rule binds in the week a generator
    is *made*, which is only knowable once deliveries have been assigned to
    production weeks — so assignment comes before allocation rather than after.

    Parameters
    ----------
    demand, shutdown_weeks, partial_shutdown_weeks, eu_demand, row_cap, params
        Passed through to :func:`domain.solver.solve_plan_integrated`.
    supplier_params : SupplierParams
        Supplier configuration.
    reference_week_date : date | None
        Anchors calendar quarters for quota accounting.
    demand_events : Sequence[DemandEvent] | None
        Customer demand events. When supplied, deliveries are assigned and the
        EU material rule is checked against production weeks. When omitted, the
        rule falls back to due weeks and the returned record list is empty.

    Returns
    -------
    (plan_df, summary, quota_status, assignments)

    Raises
    ------
    InfeasibleAllocationError
        If a pre-solve feasibility check fails.
    """
    # Pre-solve feasibility check
    errors = validate_supplier_feasibility(
        demand, eu_demand, shutdown_weeks, supplier_params, params.horizon_weeks
    )
    if errors:
        raise InfeasibleAllocationError(errors)

    # Solve with row_cap enforcement (eu_demand raises the inventory floor)
    plan_df, summary = solve_plan_integrated(
        demand, shutdown_weeks, partial_shutdown_weeks,
        eu_demand, row_cap, params,
    )

    y_plan = [0] + plan_df["Good_Production"].tolist()

    # Disaggregate to customers, so the material rule can be checked against the
    # week each EU-restricted generator is produced rather than the week it ships.
    assignments: List[DeliveryRecord] = []
    eu_production: List[int] | None = None
    if demand_events:
        assignments = assign_deliveries(y_plan, demand_events, params)
        eu_production = eu_restricted_production(assignments, params.horizon_weeks)

    allocations = allocate_suppliers_weekly(
        y_plan, eu_demand, params, supplier_params, eu_production=eu_production
    )

    # Merge supplier columns (allocations are ordered by week 1..T like plan_df)
    plan_df = plan_df.copy()
    plan_df["Curium_Good"] = [a.curium_good for a in allocations]
    plan_df["BWXT_Good"] = [a.bwxt_good for a in allocations]
    plan_df["Run_Sequence"] = [", ".join(a.run_sequence) for a in allocations]
    plan_df["Supplier_Label"] = [a.supplier_label for a in allocations]
    plan_df["Curium_Activity_mCi"] = [a.curium_activity_mci for a in allocations]
    plan_df["BWXT_Activity_mCi"] = [a.bwxt_activity_mci for a in allocations]
    plan_df["Total_Sr82_mCi"] = [a.total_activity_mci for a in allocations]
    plan_df["EU_Restricted_Demand"] = [a.eu_restricted_demand for a in allocations]
    plan_df["EU_Restricted_Produced"] = [
        a.eu_restricted_production for a in allocations
    ]

    # Quota accounting
    boundaries = compute_quarter_boundaries(
        params.horizon_weeks, supplier_params.quarter_start_month, reference_week_date
    )
    quota_status = check_quarterly_quota(allocations, supplier_params, boundaries)

    summary = dict(summary)
    summary.update(
        quota_summary(quota_status, allocations, supplier_params, boundaries, summary)
    )

    return plan_df, summary, quota_status, assignments


def quota_summary(
    quota_status: Sequence[QuarterlyQuotaStatus],
    allocations: Sequence,
    supplier_params: SupplierParams,
    boundaries: Sequence,
    solved_summary: dict,
) -> dict:
    """Build the quota-related summary keys.

    Separated out so ``domain.comparison`` can produce the identical keys for the
    manual plan. Symmetry here is the whole point: any key present on one side of
    the cost comparison and absent from the other shows up as a phantom saving.
    """
    total_quota_penalty = sum(qs.penalty_usd for qs in quota_status)
    composite = solved_summary["total_composite_cost"]

    # Partial quarters carry no penalty by construction (see domain.quota), so
    # they cannot distort the objective with a shortfall the plan has no
    # visibility to judge. They are also excluded from the starvation diagnosis,
    # since a fragment of a quarter proves nothing about ordering behaviour.
    full_quarters = [
        (span.quarter, tuple(span.weeks))
        for span in boundaries
        if not span.is_partial
    ]

    return {
        "total_quota_penalty_cost": total_quota_penalty,
        "w_quota": supplier_params.w_quota,
        "total_cost_with_quota": composite
        + supplier_params.w_quota * total_quota_penalty,
        "quota_is_advisory": True,
        "quota_advisory_note": QUOTA_ADVISORY_LABEL,
        "quota_structural_warnings": zero_allocation_warnings(
            list(allocations), supplier_params, full_quarters
        ),
        "partial_quarters": sorted({
            qs.quarter for qs in quota_status if qs.is_partial
        }),
        "partial_quarter_note": partial_quarter_note(quota_status),
    }
