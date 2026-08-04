"""Domain: single-week composite cost function (pure)."""

from __future__ import annotations

from domain.params import IntegratedParams


def compute_weekly_cost(
    inv_end: int,
    good_prod: int,
    week_type: str,
    params: IntegratedParams,
) -> float:
    """
    Compute the weighted composite cost for a single week.

    Parameters
    ----------
    inv_end : int
        Net inventory at end of week (>= 0 = early units held, < 0 = backlog).
    good_prod : int
        Good units produced this week (after test discard).
    week_type : str
        One of "Normal", "Partial", or "Shutdown".
    params : IntegratedParams
        Model parameters including rates and weights.

    Returns
    -------
    float
        Weighted composite cost = w_penalty × penalty + w_overtime × overtime + w_capacity × capacity.
    """
    # Penalty component (Requirements 2.1, 2.2)
    if inv_end >= 0:
        penalty = params.penalty_rate * inv_end
    else:
        penalty = params.late_penalty_rate * abs(inv_end)

    # Overtime component (Requirements 3.1, 3.4)
    overtime = params.overtime_rate if good_prod > params.normal_max_good_week else 0.0

    # Capacity utilization component (Requirements 4.1, 4.4, 4.5, 4.6)
    if week_type == "Shutdown":
        capacity = 0.0
    elif week_type == "Partial":
        ceiling = params.max_good_per_batch  # 15
        capacity = params.capacity_rate * max(0, ceiling - good_prod)
    else:  # Normal
        ceiling = params.normal_max_good_week  # 30
        capacity = params.capacity_rate * max(0, ceiling - good_prod)

    # Weighted composite (Requirement 1.1)
    return (
        params.w_penalty * penalty
        + params.w_overtime * overtime
        + params.w_capacity * capacity
    )

