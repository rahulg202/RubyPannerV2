"""Domain: single-week composite cost function (pure).

This is the **only** place the weekly cost formula lives. It used to be written
out three times — here, in ``solver._build_plan_df``, and in
``comparison.compute_baseline_cost`` — with the third copy guarded by a runtime
``assert`` cross-checking the first. That assert was the tell: three copies of one
formula need a tripwire precisely because they can drift. Now there is one copy
and the tripwire is unnecessary.

Capacity decomposition
----------------------
The capacity component is ``rate * max(0, ceiling - y)``. Because the
zero-terminal-inventory rule pins total production to total demand, the sum of
that term over the horizon splits exactly into two parts:

    capacity = rate*(sum(ceiling) - demand)  +  rate*sum(max(0, y - ceiling))
               |--------- floor ---------|     |------ levelling ------|

The **floor** is arithmetic: identical for every feasible plan given a demand
total and a shutdown schedule. The **levelling** part is the only portion a plan
can change, and it equals ``capacity_rate`` per unit produced above the normal
weekly ceiling — an undeclared overtime surcharge 7.5x larger than the declared
``overtime_rate``.

Reporting both parts is the point. The levelling term is not wrong — piling
production into overtime weeks genuinely idles slots elsewhere — but it was
invisible, and it meant ``overtime_rate`` could be swept from $0 to $20,000
without changing a single decision. See
`.kiro/specs/correctness-remediation/requirements.md` R1.
"""

from __future__ import annotations

from dataclasses import dataclass

from domain.params import IntegratedParams

SHUTDOWN = "Shutdown"
PARTIAL = "Partial"
NORMAL = "Normal"


@dataclass(frozen=True)
class WeeklyCost:
    """Every cost figure for one week, decomposed.

    ``capacity`` is what has always been published; ``capacity_floor`` and
    ``capacity_levelling`` are its two parts and always sum to it.
    ``composite`` is the DP objective contribution for the week and never
    includes any post-solve advisory such as the quarterly quota shortfall.

    ``idle_slots`` is the physical quantity underneath the capacity cost: how many
    production slots went unfilled. Reported separately from the money because the
    two answer different questions. "How much capacity did we lose?" is a fact
    about the factory and should not change when someone edits a cost rate; "what
    is that worth?" is a pricing judgement. Keeping them apart also means the rate
    can be set to whatever the business actually believes without destroying the
    planner's view of utilisation.
    """

    penalty: float
    overtime: float
    capacity: float
    capacity_floor: float
    capacity_levelling: float
    composite: float
    idle_slots: int = 0

    @property
    def is_overtime(self) -> bool:
        return self.overtime > 0.0


def capacity_ceiling(week_type: str, params: IntegratedParams) -> int | None:
    """Good-unit ceiling the capacity component measures idle slots against.

    The **normal** weekly ceiling: production above it cannot credit back against
    idle slots elsewhere, so uneven plans carry the levelling charge described in
    the module docstring.

    ``None`` for a shutdown week, which carries no capacity cost at all: the
    factory is closed, so there is no slot to have left idle.

    How strongly that charge bites is set by ``capacity_rate`` alone — lower it to
    weaken the surcharge, or set it to zero to remove it and leave
    ``overtime_rate`` as the only overtime lever. An earlier version offered a
    second ceiling (the 3-batch limit) as a `capacity_baseline` mode; it was
    removed because it produced an identical plan to lowering the rate while
    inflating the reported capacity cost by a constant no plan could influence.
    """
    if week_type == SHUTDOWN:
        return None
    if week_type == PARTIAL:
        return params.max_good_per_batch
    return params.normal_max_good_week


def decompose_weekly_cost(
    inv_end: int,
    good_prod: int,
    week_type: str,
    params: IntegratedParams,
) -> WeeklyCost:
    """Full cost breakdown for a single week.

    Parameters
    ----------
    inv_end : int
        Net inventory at end of week. ``>= 0`` is stock held early, ``< 0`` is
        backlog.
    good_prod : int
        Good units produced this week, after the test discard.
    week_type : str
        ``"Normal"``, ``"Partial"`` or ``"Shutdown"``.
    params : IntegratedParams
        Rates, weights and ceilings.

    Returns
    -------
    WeeklyCost
    """
    # Penalty: early holding, or backlog at the deterrent rate. The late rate is
    # deliberately punitive (100x) to stop the solver ever choosing to run late;
    # it is not a price anyone pays, which is why callers must not publish a cost
    # built on an unreconciled plan.
    if inv_end >= 0:
        penalty = params.penalty_rate * inv_end
    else:
        penalty = params.late_penalty_rate * abs(inv_end)

    overtime = (
        params.overtime_rate if good_prod > params.normal_max_good_week else 0.0
    )

    ceiling = capacity_ceiling(week_type, params)
    # Physical idle slots, measured against the standing (normal) ceiling and
    # independent of every cost rate. This is a fact about how full the factory
    # was, so it must not move when a price is edited.
    if week_type == SHUTDOWN:
        idle_slots = 0
    elif week_type == PARTIAL:
        idle_slots = max(0, params.max_good_per_batch - good_prod)
    else:
        idle_slots = max(0, params.normal_max_good_week - good_prod)

    if ceiling is None:
        capacity_floor = 0.0
        capacity_levelling = 0.0
    else:
        # floor may be negative for a single week (this week used more than the
        # baseline); across the horizon the floor sums to a constant, which is
        # the property that makes the split meaningful.
        capacity_floor = params.capacity_rate * (ceiling - good_prod)
        capacity_levelling = params.capacity_rate * max(0, good_prod - ceiling)
    capacity = capacity_floor + capacity_levelling

    composite = (
        params.w_penalty * penalty
        + params.w_overtime * overtime
        + params.w_capacity * capacity
    )

    return WeeklyCost(
        penalty=penalty,
        overtime=overtime,
        capacity=capacity,
        capacity_floor=capacity_floor,
        capacity_levelling=capacity_levelling,
        composite=composite,
        idle_slots=idle_slots,
    )


def compute_weekly_cost(
    inv_end: int,
    good_prod: int,
    week_type: str,
    params: IntegratedParams,
) -> float:
    """Weighted composite cost for a single week.

    Retained as the solver's hot-path entry point and for existing callers;
    delegates to :func:`decompose_weekly_cost` so there is still one formula.
    """
    return decompose_weekly_cost(inv_end, good_prod, week_type, params).composite


# ---------------------------------------------------------------------------
# Horizon-level capacity decomposition
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CapacitySplit:
    """How the horizon's idle slots and capacity cost divide up.

    This is a **horizon-level** concept, not a per-week one. It relies on total
    production equalling total demand, which only holds across the whole plan, so
    computing it per week produces figures that are individually meaningless — a
    single overtime week would show a negative "unavoidable" component.

    Four quantities, all non-negative:

    ``unavoidable_idle_slots``
        Slots that stay empty because customers order less than standing capacity
        can make. No plan can fill them; there is no order to fill them with.
    ``forced_overtime_units``
        Units that *must* come from overtime because demand exceeds standing
        capacity. No plan can avoid them.
    ``discretionary_overtime_units``
        Overtime beyond the forced minimum. This is the part a plan chooses, and
        each unit leaves one standing slot empty elsewhere.
    ``total_idle_slots``
        ``unavoidable_idle_slots + discretionary_overtime_units``.

    Exactly one of ``unavoidable_idle_slots`` and ``forced_overtime_units`` can be
    non-zero: either demand is below standing capacity or it is above it.
    """

    nominal_slots: int
    produced: int
    unavoidable_idle_slots: int
    forced_overtime_units: int
    discretionary_overtime_units: int
    total_idle_slots: int
    overtime_units: int

    @property
    def demand_exceeds_capacity(self) -> bool:
        """True when overtime is mandatory rather than chosen."""
        return self.forced_overtime_units > 0

    def costs(self, params: IntegratedParams) -> tuple[float, float]:
        """``(unavoidable_idle_cost, overtime_surcharge)`` for this split.

        The two always sum to ``capacity_rate * total_idle_slots``, which is the
        published capacity total.
        """
        return (
            params.capacity_rate * self.unavoidable_idle_slots,
            params.capacity_rate * self.discretionary_overtime_units,
        )


def split_capacity(
    nominal_slots: int,
    produced: int,
    overtime_units: int,
    total_idle_slots: int,
) -> CapacitySplit:
    """Divide idle slots into the part nobody can avoid and the part a plan chose.

    An earlier version computed ``unavoidable = total_idle - overtime_units``,
    which is algebraically ``nominal_slots - produced``. That is correct
    arithmetic but it was *labelled* as unavoidable idle capacity, and the label
    only holds while demand sits below standing capacity. Above it the figure goes
    negative and means something entirely different — the minimum overtime demand
    forces — so the UI reported "-20 idle slot-weeks fixed by demand", which is not
    a thing that can exist.

    Both regimes are now named explicitly.
    """
    slack = nominal_slots - produced
    unavoidable_idle = max(0, slack)
    forced_overtime = max(0, -slack)
    # Overtime beyond the forced minimum is the part the plan chose. Clamped
    # because a plan can be forced into overtime it then exceeds.
    discretionary = max(0, overtime_units - forced_overtime)

    return CapacitySplit(
        nominal_slots=nominal_slots,
        produced=produced,
        unavoidable_idle_slots=unavoidable_idle,
        forced_overtime_units=forced_overtime,
        discretionary_overtime_units=discretionary,
        total_idle_slots=total_idle_slots,
        overtime_units=overtime_units,
    )
