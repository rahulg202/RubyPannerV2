"""Domain: multi-customer onboarding recommendation (pure).

Each new customer has its own permissible start-week window. The engine selects a
start week per customer and ranks the resulting combinations by marginal cost
against a single shared baseline (existing sites only).

Search strategy
---------------
The number of combinations is the product of the window widths, so exhaustive
evaluation only scales for small problems. Below ``EXHAUSTIVE_THRESHOLD``
combinations we enumerate everything and the result is provably optimal. Above
it we run coordinate descent from diversified seeds: a strong candidate, not a
proven optimum (disclosed to the planner).

See .kiro/specs/optimizer-enhancements/design.md, Feature 4. No I/O, no UI.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Dict, List, Sequence, Tuple

import pandas as pd

from domain.demand import ROW_COUNTRIES, build_weekly_demand, build_weekly_row_demand
from domain.errors import InfeasibleAllocationError, ValidationError
from domain.params import IntegratedParams, SupplierParams
from domain.supplier_solve import solve_with_suppliers

EXHAUSTIVE_THRESHOLD = 500
TOP_N = 5
OBJECTIVES = ("penalty", "overtime", "capacity")
_OBJECTIVE_KEY = {
    "penalty": "delta_penalty",
    "overtime": "delta_overtime",
    "capacity": "delta_capacity",
}

ProgressCallback = Callable[[float, str], None]


# ---------------------------------------------------------------------------
# Inputs and results
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class NewCustomer:
    """A prospective customer with its permissible onboarding window."""

    site_id: str
    site_name: str = ""
    earliest_week: int = 1
    latest_week: int = 1
    interval_weeks: int = 7
    country: str = ""
    eu_restricted: bool = False

    @property
    def window(self) -> List[int]:
        return list(range(self.earliest_week, self.latest_week + 1))

    @property
    def is_eu_restricted(self) -> bool:
        """Explicit flag, or fallback to the restricted-country list."""
        return self.eu_restricted or self.country.strip().lower() in ROW_COUNTRIES


@dataclass
class CombinationResult:
    """One evaluated assignment of start weeks to new customers."""

    selected_weeks: Dict[str, int]
    feasible: bool = True
    delta_penalty: float = 0.0
    delta_overtime: float = 0.0
    delta_capacity: float = 0.0
    delta_composite: float = 0.0
    total_penalty: float = 0.0
    total_overtime: float = 0.0
    total_capacity: float = 0.0
    total_composite: float = 0.0
    overtime_weeks: int = 0
    reason: str = ""
    plan_df: pd.DataFrame | None = None

    def key(self) -> Tuple[int, ...]:
        """Stable identity for de-duplication (weeks in sorted site order)."""
        return tuple(self.selected_weeks[k] for k in sorted(self.selected_weeks))


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_new_customers(
    customers: Sequence[NewCustomer],
    params: IntegratedParams,
) -> List[str]:
    """Return per-row validation errors (empty when all rows are valid)."""
    errors: List[str] = []
    seen: Dict[str, int] = {}
    for i, c in enumerate(customers, start=1):
        label = f"Row {i} ({c.site_id or 'no id'})"
        if not str(c.site_id).strip():
            errors.append(f"{label}: Site_ID is required.")
        elif c.site_id in seen:
            errors.append(
                f"{label}: duplicate Site_ID, already used in row {seen[c.site_id]}."
            )
        else:
            seen[c.site_id] = i

        if c.earliest_week < 1:
            errors.append(f"{label}: earliest start week must be >= 1.")
        if c.latest_week < c.earliest_week:
            errors.append(
                f"{label}: latest start week ({c.latest_week}) must be >= "
                f"earliest ({c.earliest_week})."
            )
        if c.latest_week > params.horizon_weeks:
            errors.append(
                f"{label}: latest start week ({c.latest_week}) exceeds the "
                f"horizon ({params.horizon_weeks})."
            )
        if c.interval_weeks < 1:
            errors.append(f"{label}: interval weeks must be >= 1.")
    return errors


# ---------------------------------------------------------------------------
# Demand injection
# ---------------------------------------------------------------------------

def inject_customer_demand(
    base_demand: Sequence[int],
    customers: Sequence[NewCustomer],
    selected_weeks: Dict[str, int],
    params: IntegratedParams,
    eu_only: bool = False,
) -> List[int]:
    """Return a new demand array with each customer's recurring demand added.

    Each customer's first generator is due at its selected start week, repeating
    every ``interval_weeks`` through the horizon. When ``eu_only`` is True, only
    EU-restricted customers contribute (used to build the EU/ROW demand array).
    """
    d = list(base_demand)
    T = params.horizon_weeks
    for c in customers:
        if eu_only and not c.is_eu_restricted:
            continue
        start = selected_weeks.get(c.site_id)
        if start is None:
            continue
        week = start
        while 1 <= week <= T:
            d[week] += 1
            week += c.interval_weeks
    return d


# ---------------------------------------------------------------------------
# Search-space estimation
# ---------------------------------------------------------------------------

def count_combinations(customers: Sequence[NewCustomer]) -> int:
    """Product of the window widths — the exhaustive search space size."""
    total = 1
    for c in customers:
        total *= max(1, len(c.window))
    return total


def estimate_search(customers: Sequence[NewCustomer]) -> dict:
    """Describe the search space and which strategy will be used."""
    combos = count_combinations(customers)
    return {
        "combinations": combos,
        "exhaustive": combos <= EXHAUSTIVE_THRESHOLD,
        "threshold": EXHAUSTIVE_THRESHOLD,
        "window_widths": [len(c.window) for c in customers],
    }


# ---------------------------------------------------------------------------
# Baseline and single-combination evaluation
# ---------------------------------------------------------------------------

def run_baseline(
    active_df: pd.DataFrame,
    params: IntegratedParams,
    supplier_params: SupplierParams,
    shutdown_weeks: Sequence[int] = (),
    partial_shutdown_weeks: Sequence[int] = (),
    reference_week_date: date | None = None,
) -> dict:
    """Solve with existing sites only — the shared comparison baseline."""
    demand = build_weekly_demand(active_df, params)
    eu_demand = build_weekly_row_demand(active_df, params)
    _plan, summary, _quota = solve_with_suppliers(
        demand, list(shutdown_weeks), list(partial_shutdown_weeks),
        eu_demand, params.row_cap, params, eu_demand, supplier_params,
        reference_week_date,
    )
    return summary


def evaluate_combination(
    active_df: pd.DataFrame,
    customers: Sequence[NewCustomer],
    selected_weeks: Dict[str, int],
    params: IntegratedParams,
    supplier_params: SupplierParams,
    base_summary: dict,
    shutdown_weeks: Sequence[int] = (),
    partial_shutdown_weeks: Sequence[int] = (),
    reference_week_date: date | None = None,
    keep_plan: bool = False,
) -> CombinationResult:
    """Evaluate one start-week assignment, returning marginal costs vs baseline."""
    base_demand = build_weekly_demand(active_df, params)
    base_eu = build_weekly_row_demand(active_df, params)

    demand = inject_customer_demand(base_demand, customers, selected_weeks, params)
    eu_demand = inject_customer_demand(
        base_eu, customers, selected_weeks, params, eu_only=True
    )

    try:
        plan_df, summary, _quota = solve_with_suppliers(
            demand, list(shutdown_weeks), list(partial_shutdown_weeks),
            eu_demand, params.row_cap, params, eu_demand, supplier_params,
            reference_week_date,
        )
    except (RuntimeError, InfeasibleAllocationError) as exc:
        return CombinationResult(
            selected_weeks=dict(selected_weeks),
            feasible=False,
            reason=str(exc),
        )

    return CombinationResult(
        selected_weeks=dict(selected_weeks),
        feasible=True,
        delta_penalty=summary["total_penalty_cost"] - base_summary["total_penalty_cost"],
        delta_overtime=summary["total_overtime_cost"] - base_summary["total_overtime_cost"],
        delta_capacity=summary["total_capacity_cost"] - base_summary["total_capacity_cost"],
        delta_composite=summary["total_composite_cost"] - base_summary["total_composite_cost"],
        total_penalty=summary["total_penalty_cost"],
        total_overtime=summary["total_overtime_cost"],
        total_capacity=summary["total_capacity_cost"],
        total_composite=summary["total_composite_cost"],
        overtime_weeks=summary["overtime_weeks"],
        plan_df=plan_df if keep_plan else None,
    )


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------

def rank_top_n(
    results: Sequence[CombinationResult],
    top_n: int = TOP_N,
) -> Dict[str, List[CombinationResult]]:
    """Rank feasible results per objective, ascending, with deterministic ties.

    Ties on the primary delta break on ``delta_composite``, then on the selected
    weeks tuple, so repeated runs produce identical ordering.
    """
    feasible = [r for r in results if r.feasible]
    rankings: Dict[str, List[CombinationResult]] = {}
    for obj in OBJECTIVES:
        field_name = _OBJECTIVE_KEY[obj]
        ordered = sorted(
            feasible,
            key=lambda r, f=field_name: (
                getattr(r, f), r.delta_composite, r.key()
            ),
        )
        rankings[obj] = ordered[:top_n]
    return rankings


# ---------------------------------------------------------------------------
# Search strategies
# ---------------------------------------------------------------------------

def _exhaustive_search(
    active_df, customers, params, supplier_params, base_summary,
    shutdown_weeks, partial_shutdown_weeks, reference_week_date,
    progress: ProgressCallback | None,
) -> List[CombinationResult]:
    windows = [c.window for c in customers]
    site_ids = [c.site_id for c in customers]
    total = count_combinations(customers)
    results: List[CombinationResult] = []

    for i, combo in enumerate(itertools.product(*windows), start=1):
        selected = dict(zip(site_ids, combo))
        results.append(evaluate_combination(
            active_df, customers, selected, params, supplier_params,
            base_summary, shutdown_weeks, partial_shutdown_weeks,
            reference_week_date,
        ))
        if progress and (i % 10 == 0 or i == total):
            progress(i / total, f"Evaluated {i} of {total} combinations")
    return results


def _coordinate_descent(
    active_df, customers, params, supplier_params, base_summary,
    shutdown_weeks, partial_shutdown_weeks, reference_week_date,
    seed: Dict[str, int], max_passes: int,
    cache: Dict[Tuple[int, ...], CombinationResult],
) -> List[CombinationResult]:
    """Refine one seed: optimize each customer's week with others fixed."""
    site_ids = [c.site_id for c in customers]
    current = dict(seed)
    visited: List[CombinationResult] = []

    def evaluate(sel: Dict[str, int]) -> CombinationResult:
        key = tuple(sel[k] for k in sorted(sel))
        if key not in cache:
            cache[key] = evaluate_combination(
                active_df, customers, sel, params, supplier_params,
                base_summary, shutdown_weeks, partial_shutdown_weeks,
                reference_week_date,
            )
            visited.append(cache[key])
        return cache[key]

    best = evaluate(current)
    for _ in range(max_passes):
        improved = False
        for c in customers:
            for week in c.window:
                if week == current[c.site_id]:
                    continue
                trial = dict(current)
                trial[c.site_id] = week
                candidate = evaluate(trial)
                if not candidate.feasible:
                    continue
                if not best.feasible or candidate.delta_composite < best.delta_composite:
                    best = candidate
                    current = trial
                    improved = True
        if not improved:
            break  # converged
    return visited


def _heuristic_search(
    active_df, customers, params, supplier_params, base_summary,
    shutdown_weeks, partial_shutdown_weeks, reference_week_date,
    progress: ProgressCallback | None,
    max_seeds: int, max_passes: int,
) -> List[CombinationResult]:
    """Independent-phase seeding, then coordinate descent from several seeds."""
    site_ids = [c.site_id for c in customers]
    cache: Dict[Tuple[int, ...], CombinationResult] = {}
    all_results: List[CombinationResult] = []

    # Phase 1 — evaluate each customer alone to find promising weeks.
    per_customer_best: Dict[str, List[int]] = {}
    for ci, c in enumerate(customers, start=1):
        scored: List[Tuple[float, int]] = []
        for week in c.window:
            selected = {c.site_id: week}
            r = evaluate_combination(
                active_df, [c], selected, params, supplier_params,
                base_summary, shutdown_weeks, partial_shutdown_weeks,
                reference_week_date,
            )
            if r.feasible:
                scored.append((r.delta_composite, week))
        scored.sort()
        per_customer_best[c.site_id] = [w for _, w in scored[:3]] or list(c.window[:1])
        if progress:
            progress(
                0.4 * ci / max(1, len(customers)),
                f"Screened customer {ci} of {len(customers)}",
            )

    # Phase 2 — build diversified seeds from the per-customer shortlists.
    seed_candidates: List[Dict[str, int]] = []
    ranked_options = [per_customer_best[s] for s in site_ids]
    for combo in itertools.islice(itertools.product(*ranked_options), max_seeds):
        seed_candidates.append(dict(zip(site_ids, combo)))
    if not seed_candidates:
        seed_candidates = [{c.site_id: c.window[0] for c in customers}]

    # Phase 3 — coordinate descent from each seed.
    for si, seed in enumerate(seed_candidates, start=1):
        all_results.extend(_coordinate_descent(
            active_df, customers, params, supplier_params, base_summary,
            shutdown_weeks, partial_shutdown_weeks, reference_week_date,
            seed, max_passes, cache,
        ))
        if progress:
            progress(
                0.4 + 0.6 * si / len(seed_candidates),
                f"Refined seed {si} of {len(seed_candidates)}",
            )

    # De-duplicate by selected weeks
    unique: Dict[Tuple[int, ...], CombinationResult] = {}
    for r in all_results:
        unique.setdefault(r.key(), r)
    return list(unique.values())


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def evaluate_multi_customer(
    active_df: pd.DataFrame,
    customers: Sequence[NewCustomer],
    params: IntegratedParams,
    supplier_params: SupplierParams,
    shutdown_weeks: Sequence[int] = (),
    partial_shutdown_weeks: Sequence[int] = (),
    reference_week_date: date | None = None,
    progress: ProgressCallback | None = None,
    max_seeds: int = 10,
    max_passes: int = 3,
) -> dict:
    """Recommend start weeks for one or more new customers.

    Returns a dict with the shared ``base_summary``, per-objective ``rankings``,
    whether a heuristic was used, and how many combinations were evaluated.

    Single-customer onboarding is simply the N=1 case.

    Raises
    ------
    ValidationError
        If any new customer row is invalid.
    """
    if not customers:
        raise ValidationError("At least one new customer is required.")

    errors = validate_new_customers(customers, params)
    if errors:
        raise ValidationError(errors)

    base_summary = run_baseline(
        active_df, params, supplier_params,
        shutdown_weeks, partial_shutdown_weeks, reference_week_date,
    )

    estimate = estimate_search(customers)
    if progress:
        progress(0.0, f"Search space: {estimate['combinations']} combinations")

    if estimate["exhaustive"]:
        results = _exhaustive_search(
            active_df, customers, params, supplier_params, base_summary,
            shutdown_weeks, partial_shutdown_weeks, reference_week_date, progress,
        )
        used_heuristic = False
    else:
        results = _heuristic_search(
            active_df, customers, params, supplier_params, base_summary,
            shutdown_weeks, partial_shutdown_weeks, reference_week_date,
            progress, max_seeds, max_passes,
        )
        used_heuristic = True

    infeasible = [r for r in results if not r.feasible]
    rankings = rank_top_n(results)

    return {
        "base_summary": base_summary,
        "rankings": rankings,
        "used_heuristic": used_heuristic,
        "combinations_evaluated": len(results),
        "search_space": estimate["combinations"],
        "infeasible_count": len(infeasible),
        "infeasible_reasons": sorted({r.reason for r in infeasible if r.reason})[:5],
        "all_results": results,
    }
