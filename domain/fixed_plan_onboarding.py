"""Domain: onboard a customer into an existing plan without re-planning it (pure).

The standard onboarding search re-optimizes all 52 weeks for every candidate start
week, which answers "what is the cheapest week if I am free to re-plan everything".
The planning team asked a different question: "my plan is already agreed — where
does this site fit into it, and what does slotting it in cost me?"

This module answers that one. Weekly production comes from the manual plan and does
not move. Each new generator either drops into a week with a spare slot, forces
overtime, is built earlier and held, or does not fit at all.

Scope note
----------
The idle-capacity cost is deliberately **not** part of the figure reported here.
That cost describes how well a plan uses the factory, and in this mode the plan is
fixed and not up for revision — so charging or crediting it would answer a question
nobody asked and would swamp the numbers that do vary. What is reported is the
marginal cost of accommodating the new site: overtime plus holding.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Sequence, Tuple

from domain.errors import ValidationError
from domain.onboarding import NewCustomer, validate_new_customers
from domain.params import IntegratedParams

# Evaluation here is arithmetic rather than a 52-week solve, so a far larger
# search space is affordable than in the re-optimizing mode.
EXHAUSTIVE_THRESHOLD = 50_000
TOP_N = 5

OUTCOME_FITS = "Fits in a spare slot"
OUTCOME_ABSORBED = "Absorbed by an existing overtime week"
OUTCOME_OVERTIME = "Needs overtime"
OUTCOME_SHIFTED = "Built early and held"
OUTCOME_INFEASIBLE = "Does not fit"


@dataclass
class Placement:
    """Where one required generator ended up, and what that cost."""

    site_id: str
    due_week: int
    produced_week: int | None
    outcome: str
    cost: float = 0.0
    detail: str = ""


@dataclass
class FixedPlanResult:
    """Cost of slotting one set of start weeks into a fixed plan."""

    selected_weeks: Dict[str, int] = field(default_factory=dict)
    feasible: bool = True
    added_cost: float = 0.0
    overtime_cost: float = 0.0
    holding_cost: float = 0.0
    # Generator counts by outcome. These sum to ``generators``, so the report
    # always reconciles — a generator absorbed into a week the plan already runs
    # on overtime costs nothing, but it must still be visible somewhere.
    fits: int = 0
    absorbed: int = 0
    overtime_added: int = 0
    shifted: int = 0
    infeasible: int = 0
    # Distinct weeks newly pushed past the normal ceiling (what is charged).
    overtime_weeks_added: int = 0
    placements: List[Placement] = field(default_factory=list)
    reason: str = ""

    @property
    def generators(self) -> int:
        return len(self.placements)

    @property
    def placed_without_cost(self) -> int:
        """Generators that needed no overtime charge and no holding."""
        return self.fits + self.absorbed

    @property
    def delta_composite(self) -> float:
        """Cost effect of this option, named to match ``CombinationResult``.

        The start-week selection screen is shared between both onboarding modes
        and only needs the weeks, a stable key and a cost effect. Exposing the
        same attribute name keeps that screen mode-agnostic instead of branching
        through it. Always >= 0 here: slotting a site into a fixed plan can add
        cost but never removes any.
        """
        return self.added_cost

    @property
    def fits_cleanly(self) -> bool:
        """True when nothing had to be paid for: no new overtime, no holding."""
        return self.feasible and self.placed_without_cost == self.generators

    def key(self) -> Tuple[int, ...]:
        return tuple(self.selected_weeks[k] for k in sorted(self.selected_weeks))


def delivery_weeks(
    customer: NewCustomer,
    start_week: int,
    horizon_weeks: int,
) -> List[int]:
    """Weeks this customer needs a generator, given a start week."""
    if customer.interval_weeks <= 0:
        return [start_week] if 1 <= start_week <= horizon_weeks else []
    weeks, week = [], start_week
    while 1 <= week <= horizon_weeks:
        weeks.append(week)
        week += customer.interval_weeks
    return weeks


def evaluate_against_fixed_plan(
    fixed_production: Sequence[int],
    customers: Sequence[NewCustomer],
    selected_weeks: Dict[str, int],
    params: IntegratedParams,
    max_shift_weeks: int = 4,
) -> FixedPlanResult:
    """Cost of adding these customers to a plan whose weekly output is fixed.

    Parameters
    ----------
    fixed_production : Sequence[int]
        1-indexed sellable generators per week in the existing plan (index 0
        unused). This is the manual plan's commercial output, excluding QC
        generators, since the weekly ceilings are expressed in sellable units.
    customers : Sequence[NewCustomer]
        The prospective customers.
    selected_weeks : Dict[str, int]
        Start week per ``site_id``.
    params : IntegratedParams
        Supplies the weekly ceilings and the cost rates.
    max_shift_weeks : int
        How far earlier a generator may be built and held before the option is
        abandoned. Beyond a few weeks the holding cost exceeds overtime anyway.

    Returns
    -------
    FixedPlanResult
        ``feasible`` is False when at least one generator could not be placed.

    Notes
    -----
    Spare capacity is consumed as generators are placed, so two customers cannot
    both claim the same free slot. Weeks are processed in order so the result is
    deterministic.

    Each generator takes the cheapest available option:

    1. a spare slot under the normal ceiling that week — no cost;
    2. overtime that week, if there is room under the overtime ceiling. The
       overtime charge applies once per week newly pushed past the normal
       ceiling; a week the existing plan already runs on overtime is not charged
       again, it only needs room;
    3. a spare normal slot up to ``max_shift_weeks`` earlier, held until due, at
       the early-holding rate per week;
    4. otherwise the generator does not fit.
    """
    T = params.horizon_weeks
    normal_cap = params.normal_max_good_week
    overtime_cap = params.overtime_max_good_week

    # Working copy of the plan's weekly output, consumed as we place generators.
    planned = [0] * (T + 2)
    for week in range(1, T + 1):
        if week < len(fixed_production):
            planned[week] = int(fixed_production[week])

    # Weeks the existing plan already runs past the normal ceiling are already
    # overtime weeks; pushing them further costs no additional overtime charge.
    already_overtime = {w for w in range(1, T + 1) if planned[w] > normal_cap}
    charged_overtime: set[int] = set()

    result = FixedPlanResult(selected_weeks=dict(selected_weeks))

    # Flatten to one requirement per generator, earliest first for determinism.
    required: List[Tuple[int, str]] = []
    for customer in customers:
        start = selected_weeks.get(customer.site_id)
        if start is None:
            continue
        for week in delivery_weeks(customer, start, T):
            required.append((week, customer.site_id))
    required.sort()

    for due_week, site_id in required:
        placement = _place_one(
            due_week, site_id, planned, normal_cap, overtime_cap,
            already_overtime, charged_overtime, params, max_shift_weeks, T,
        )
        result.placements.append(placement)

        if placement.outcome == OUTCOME_FITS:
            result.fits += 1
        elif placement.outcome == OUTCOME_ABSORBED:
            result.absorbed += 1
        elif placement.outcome == OUTCOME_OVERTIME:
            result.overtime_cost += placement.cost
            result.overtime_added += 1
            result.overtime_weeks_added += 1
        elif placement.outcome == OUTCOME_SHIFTED:
            result.holding_cost += placement.cost
            result.shifted += 1
        else:
            result.infeasible += 1

    result.added_cost = result.overtime_cost + result.holding_cost
    result.feasible = result.infeasible == 0
    if not result.feasible:
        weeks = sorted({
            p.due_week for p in result.placements
            if p.outcome == OUTCOME_INFEASIBLE
        })
        result.reason = (
            f"{result.infeasible} generator(s) could not be placed in week(s) "
            f"{', '.join(str(w) for w in weeks)}: the plan is already at the "
            f"overtime ceiling there and no earlier week has a spare slot."
        )
    return result


def _place_one(
    due_week: int,
    site_id: str,
    planned: List[int],
    normal_cap: int,
    overtime_cap: int,
    already_overtime: set[int],
    charged_overtime: set[int],
    params: IntegratedParams,
    max_shift_weeks: int,
    horizon: int,
) -> Placement:
    """Place one generator by cheapest option, consuming the capacity it uses."""
    # 1. Spare slot under the normal ceiling.
    if planned[due_week] < normal_cap:
        planned[due_week] += 1
        return Placement(
            site_id, due_week, due_week, OUTCOME_FITS, 0.0,
            f"Week {due_week} had a spare slot.",
        )

    # 2. Overtime in the due week.
    overtime_available = planned[due_week] < overtime_cap
    overtime_cost = 0.0
    if overtime_available:
        newly_charged = (
            due_week not in already_overtime and due_week not in charged_overtime
        )
        overtime_cost = params.overtime_rate if newly_charged else 0.0

    # 3. Build earlier in a week with a spare normal slot, and hold.
    shift_week, shift_cost = None, None
    for back in range(1, max_shift_weeks + 1):
        candidate = due_week - back
        if candidate < 1:
            break
        if planned[candidate] < normal_cap:
            shift_week = candidate
            shift_cost = params.penalty_rate * back
            break

    # Choose the cheaper of the two real options.
    if overtime_available and (shift_cost is None or overtime_cost <= shift_cost):
        before = planned[due_week]
        planned[due_week] += 1
        if overtime_cost > 0:
            charged_overtime.add(due_week)
            return Placement(
                site_id, due_week, due_week, OUTCOME_OVERTIME, overtime_cost,
                f"Week {due_week} was full at {before}; a third batch was run.",
            )
        return Placement(
            site_id, due_week, due_week, OUTCOME_ABSORBED, 0.0,
            f"Week {due_week} already runs {before} on overtime and had room, "
            f"so no extra overtime charge applies.",
        )

    if shift_week is not None:
        planned[shift_week] += 1
        held = due_week - shift_week
        return Placement(
            site_id, due_week, shift_week, OUTCOME_SHIFTED, float(shift_cost),
            f"Week {due_week} is full; built in week {shift_week} and held "
            f"{held} week(s).",
        )

    return Placement(
        site_id, due_week, None, OUTCOME_INFEASIBLE, 0.0,
        f"Week {due_week} is at the overtime ceiling and no week within "
        f"{max_shift_weeks} weeks earlier has a spare slot.",
    )


# ---------------------------------------------------------------------------
# Search across each customer's window
# ---------------------------------------------------------------------------

def _count_combinations(customers: Sequence[NewCustomer]) -> int:
    total = 1
    for c in customers:
        total *= max(1, len(c.window))
    return total


def _rank(results: Iterable[FixedPlanResult], top_n: int) -> List[FixedPlanResult]:
    """Cheapest first; ties prefer clean fits, then the earliest weeks."""
    feasible = [r for r in results if r.feasible]
    return sorted(
        feasible,
        key=lambda r: (r.added_cost, -r.fits, -r.absorbed, r.key()),
    )[:top_n]


def recommend_against_fixed_plan(
    fixed_production: Sequence[int],
    customers: Sequence[NewCustomer],
    params: IntegratedParams,
    max_shift_weeks: int = 4,
    top_n: int = TOP_N,
    progress=None,
) -> dict:
    """Rank start weeks by what they cost to slot into the existing plan.

    Returns a dict with ``rankings`` (cheapest first), ``all_results``,
    ``combinations_evaluated``, ``search_space``, ``used_heuristic``,
    ``infeasible_count`` and ``infeasible_reasons``.

    Raises
    ------
    ValidationError
        If no customers are supplied or any row is invalid.
    """
    if not customers:
        raise ValidationError("At least one new customer is required.")
    errors = validate_new_customers(customers, params)
    if errors:
        raise ValidationError(errors)

    space = _count_combinations(customers)
    site_ids = [c.site_id for c in customers]
    results: List[FixedPlanResult] = []

    def evaluate(selected: Dict[str, int]) -> FixedPlanResult:
        return evaluate_against_fixed_plan(
            fixed_production, customers, selected, params, max_shift_weeks
        )

    if space <= EXHAUSTIVE_THRESHOLD:
        windows = [c.window for c in customers]
        for i, combo in enumerate(itertools.product(*windows), start=1):
            results.append(evaluate(dict(zip(site_ids, combo))))
            if progress and (i % 200 == 0 or i == space):
                progress(i / space, f"Evaluated {i} of {space} combinations")
        used_heuristic = False
    else:
        # Place each customer in its own cheapest week given those already
        # placed, then refine. Cheap enough to repeat until it settles.
        current = {c.site_id: c.window[0] for c in customers}
        for _pass in range(3):
            improved = False
            for customer in customers:
                best_week, best = current[customer.site_id], evaluate(current)
                for week in customer.window:
                    trial = dict(current)
                    trial[customer.site_id] = week
                    candidate = evaluate(trial)
                    results.append(candidate)
                    if candidate.feasible and (
                        not best.feasible or candidate.added_cost < best.added_cost
                    ):
                        best, best_week = candidate, week
                if best_week != current[customer.site_id]:
                    current[customer.site_id] = best_week
                    improved = True
            if not improved:
                break
        used_heuristic = True

    unique: Dict[Tuple[int, ...], FixedPlanResult] = {}
    for r in results:
        unique.setdefault(r.key(), r)
    deduped = list(unique.values())
    infeasible = [r for r in deduped if not r.feasible]

    return {
        "rankings": _rank(deduped, top_n),
        "all_results": deduped,
        "combinations_evaluated": len(deduped),
        "search_space": space,
        "used_heuristic": used_heuristic,
        "infeasible_count": len(infeasible),
        "infeasible_reasons": sorted({r.reason for r in infeasible if r.reason})[:5],
    }
