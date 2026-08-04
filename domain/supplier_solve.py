"""Domain: supplier-constrained solve orchestration (pure composition).

Wraps the DP solver with post-solve supplier allocation and quarterly quota
accounting. Kept separate from ``supplier_allocation`` to avoid an import cycle
with ``quota`` (which depends on ``supplier_allocation``).

See .kiro/specs/supplier-constraints/design.md ("Integration with Existing Solver").
"""

from __future__ import annotations

from datetime import date
from typing import List, Sequence, Tuple

import pandas as pd

from domain.errors import InfeasibleAllocationError
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
)


def solve_with_suppliers(
    demand: List[int],
    shutdown_weeks: List[int],
    partial_shutdown_weeks: List[int],
    row_demand: List[int],
    row_cap: int,
    params: IntegratedParams,
    eu_demand: List[int],
    supplier_params: SupplierParams,
    reference_week_date: date | None = None,
) -> Tuple[pd.DataFrame, dict, List[QuarterlyQuotaStatus]]:
    """Run the DP solver, then allocate suppliers and account for quota.

    Returns ``(plan_df, summary, quota_status)`` where ``plan_df`` gains supplier
    columns, ``summary`` gains the quota-penalty component, and ``quota_status``
    lists per-supplier per-quarter results.

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
        row_demand, row_cap, params, eu_demand=eu_demand,
    )

    # Post-solve supplier allocation
    y_plan = [0] + plan_df["Good_Production"].tolist()
    allocations = allocate_suppliers_weekly(y_plan, eu_demand, params, supplier_params)

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

    # Quota accounting
    boundaries = compute_quarter_boundaries(
        params.horizon_weeks, supplier_params.quarter_start_month, reference_week_date
    )
    quota_status = check_quarterly_quota(allocations, supplier_params, boundaries)

    # Fold quota penalty into the composite cost. Partial quarters carry a zero
    # penalty by construction (see domain.quota), so they cannot distort the
    # objective with a shortfall the plan has no visibility to judge.
    total_quota_penalty = sum(qs.penalty_usd for qs in quota_status)
    summary = dict(summary)
    summary["total_quota_penalty_cost"] = total_quota_penalty
    summary["w_quota"] = supplier_params.w_quota
    summary["total_composite_cost"] = (
        summary["total_composite_cost"] + supplier_params.w_quota * total_quota_penalty
    )
    summary["partial_quarters"] = sorted({
        qs.quarter for qs in quota_status if qs.is_partial
    })
    summary["partial_quarter_note"] = partial_quarter_note(quota_status)

    return plan_df, summary, quota_status
