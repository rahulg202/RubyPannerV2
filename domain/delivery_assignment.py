"""Domain: map produced units to customer demands (pure).

The DP solver optimizes aggregate weekly quantities and never tracks *whose*
generator is produced when. This module performs the disaggregation: it assigns
each produced good unit to a specific customer demand event, so the plan can
report which customers had their production week moved.

Business rule (confirmed): a generator due in week 10 but produced in week 8 and
held in stock **is** a changed week. Week_Shift is measured on the production
week versus the scheduled (due) week.

See .kiro/specs/optimizer-enhancements/design.md, Feature 3. No I/O, no UI.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Sequence

import pandas as pd

from domain.params import IntegratedParams


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DemandEvent:
    """One customer's single generator requirement in a specific week."""

    scheduled_week: int
    site_id: str
    site_name: str = ""
    country: str = ""


@dataclass
class DeliveryRecord:
    """One customer generator across the manual plan and the optimized plan.

    The change that matters to the business is against the **manual plan** — the
    week the schedule holder intended to produce this customer's generator. That
    lives in ``manual_week`` and drives ``week_shift``.

    ``due_week`` (from the input file's Next_Demand_Week / Interval_Weeks) is kept
    as context, and ``due_week_shift`` is available for diagnostics, but it is not
    the headline comparison.
    """

    site_id: str
    site_name: str
    country: str
    due_week: int                       # when the customer is due, from the input file
    planned_week: int                   # week the optimizer produces this generator
    due_week_shift: int                 # planned - due (context only)
    manual_week: int | None = None      # week the manual plan intended to produce
    week_shift: int | None = None       # planned - manual  (the headline change)
    is_early: bool = False              # relative to the manual plan
    is_late: bool = False               # relative to the manual plan
    is_new_customer: bool = False       # absent from the manual plan
    compared: bool = False              # True once a manual plan has been applied

    # Backward-compatible aliases for the previous field names.
    @property
    def scheduled_week(self) -> int:
        """Deprecated alias for :attr:`due_week`."""
        return self.due_week

    @property
    def master_planner_week(self) -> int | None:
        """Deprecated alias for :attr:`manual_week`."""
        return self.manual_week

    @property
    def mp_week_shift(self) -> int | None:
        """Deprecated alias for :attr:`week_shift`."""
        return self.week_shift


# ---------------------------------------------------------------------------
# Demand events
# ---------------------------------------------------------------------------

def build_demand_events(
    active_df: pd.DataFrame,
    params: IntegratedParams,
) -> List[DemandEvent]:
    """Expand cleaned active sites into one DemandEvent per required generator.

    Mirrors ``build_weekly_demand``: each site needs a generator at
    ``next_demand_week`` and every ``interval_weeks`` thereafter, within the
    horizon. Ordered by (scheduled_week, site_id) for determinism.
    """
    events: List[DemandEvent] = []
    has_name = "site_name" in active_df.columns
    for _, row in active_df.iterrows():
        site_id = str(row["site_id"])
        name = str(row["site_name"]) if has_name else ""
        country = str(row.get("country", ""))
        week = int(row["next_demand_week"])
        interval = int(row["interval_weeks"])
        if interval < 0:
            continue
        if interval == 0:
            # One-time delivery
            if 1 <= week <= params.horizon_weeks:
                events.append(DemandEvent(week, site_id, name, country))
        else:
            while week <= params.horizon_weeks:
                events.append(DemandEvent(week, site_id, name, country))
                week += interval
    events.sort(key=lambda e: (e.scheduled_week, e.site_id))
    return events


# ---------------------------------------------------------------------------
# Assignment
# ---------------------------------------------------------------------------

def assign_deliveries(
    y_plan: Sequence[int],
    demand_events: Sequence[DemandEvent],
    params: IntegratedParams,
) -> List[DeliveryRecord]:
    """Assign each produced unit to a customer demand, deterministically.

    Algorithm
    ---------
    Supply tokens are the produced units, ``y_plan[t]`` of them available from
    week ``t``. Demand events are processed earliest-scheduled first (ties broken
    by ``site_id``). For each event we prefer the **latest** production week that
    is still on or before the scheduled week — this minimises inventory holding
    and yields the smallest changed-week set. If no such unit exists (backlog),
    the earliest available later unit is used.

    Determinism: demand events are sorted, and supply is consumed by an explicit
    rule, so repeated runs on identical input produce identical output.

    Parameters
    ----------
    y_plan : Sequence[int]
        1-indexed good units produced per week (index 0 unused).
    demand_events : Sequence[DemandEvent]
        All customer generator requirements.
    params : IntegratedParams
        Model parameters (horizon).

    Returns
    -------
    List[DeliveryRecord]
        One record per demand event, ordered as the (sorted) events.
    """
    T = params.horizon_weeks
    # Remaining supply per week
    remaining = [0] * (T + 2)
    for t in range(1, T + 1):
        if t < len(y_plan):
            remaining[t] = int(y_plan[t])

    events = sorted(demand_events, key=lambda e: (e.scheduled_week, e.site_id))
    records: List[DeliveryRecord] = []

    for event in events:
        sw = event.scheduled_week
        chosen: int | None = None

        # Prefer the latest production week <= scheduled week (least holding).
        for t in range(min(sw, T), 0, -1):
            if remaining[t] > 0:
                chosen = t
                break

        # Backlog: earliest available week after the scheduled week.
        if chosen is None:
            for t in range(sw + 1, T + 1):
                if remaining[t] > 0:
                    chosen = t
                    break

        if chosen is None:
            # No supply anywhere (should not happen for a feasible plan).
            raise ValueError(
                f"No produced unit available for site {event.site_id} "
                f"scheduled in week {sw}."
            )

        remaining[chosen] -= 1
        records.append(
            DeliveryRecord(
                site_id=event.site_id,
                site_name=event.site_name,
                country=event.country,
                due_week=sw,
                planned_week=chosen,
                due_week_shift=chosen - sw,
            )
        )

    return records


# ---------------------------------------------------------------------------
# Master Planner comparison and new-customer detection
# ---------------------------------------------------------------------------

def compare_against_manual_plan(
    records: Sequence[DeliveryRecord],
    manual_customer_schedule: dict[str, List[int]],
) -> List[DeliveryRecord]:
    """Compare each optimized production week against the manual plan.

    This is the comparison the business cares about: what the schedule holder
    intended versus what the optimizer proposes. A difference either reflects an
    improvement the optimizer found, or a mistake in the manual schedule.

    ``manual_customer_schedule`` maps ``site_id`` to a 1-indexed list where a
    non-zero entry marks a week the manual plan produced a generator for that
    customer. A site's Nth optimized generator is compared to its Nth manually
    planned week. Sites absent from the manual plan are flagged as new customers.

    Returns the same records, mutated in place, for convenience.
    """
    manual_weeks: dict[str, List[int]] = {}
    for site_id, marks in manual_customer_schedule.items():
        manual_weeks[site_id] = [
            week for week, value in enumerate(marks) if week >= 1 and value
        ]

    seen_count: dict[str, int] = {}
    for rec in records:
        rec.compared = True
        if rec.site_id not in manual_weeks:
            # Not in the manual plan at all — a newly added customer.
            rec.is_new_customer = True
            rec.manual_week = None
            rec.week_shift = None
            rec.is_early = rec.is_late = False
            continue

        index = seen_count.get(rec.site_id, 0)
        seen_count[rec.site_id] = index + 1
        weeks = manual_weeks[rec.site_id]
        if index < len(weeks):
            rec.manual_week = weeks[index]
            rec.week_shift = rec.planned_week - weeks[index]
            rec.is_early = rec.week_shift < 0
            rec.is_late = rec.week_shift > 0
        else:
            # The optimizer schedules more generators for this site than the
            # manual plan did; the extras have no counterpart to compare against.
            rec.manual_week = None
            rec.week_shift = None
            rec.is_early = rec.is_late = False
    return list(records)


# Backward-compatible alias for the previous function name.
compare_against_master_planner = compare_against_manual_plan


def summarize_changes(records: Iterable[DeliveryRecord]) -> dict:
    """Summarise changes against the manual plan.

    Returns ``compared=False`` when no manual plan has been applied, so callers
    can say so rather than presenting a comparison that was never made.
    """
    records = list(records)
    total = len(records)
    compared = any(r.compared for r in records)

    if not compared:
        return {
            "compared": False,
            "total": total,
            "unchanged": 0,
            "early": 0,
            "late": 0,
            "new_customers": 0,
            "uncomparable": total,
            "max_early_shift": 0,
            "max_late_shift": 0,
        }

    unchanged = early = late = new_customer = uncomparable = 0
    shifts: List[int] = []
    for rec in records:
        if rec.is_new_customer:
            new_customer += 1
            continue
        if rec.week_shift is None:
            uncomparable += 1
            continue
        shifts.append(rec.week_shift)
        if rec.week_shift == 0:
            unchanged += 1
        elif rec.week_shift < 0:
            early += 1
        else:
            late += 1

    return {
        "compared": True,
        "total": total,
        "unchanged": unchanged,
        "early": early,
        "late": late,
        "new_customers": new_customer,
        "uncomparable": uncomparable,
        "max_early_shift": min(shifts) if shifts else 0,
        "max_late_shift": max(shifts) if shifts else 0,
    }


def to_dataframe(records: Sequence[DeliveryRecord]) -> pd.DataFrame:
    """Render delivery records as a DataFrame for display and export."""
    return pd.DataFrame([
        {
            "Site_ID": r.site_id,
            "Site_Name": r.site_name,
            "Country": r.country,
            "Manual_Plan_Week": r.manual_week,
            "Optimized_Week": r.planned_week,
            "Week_Shift": r.week_shift,
            "Due_Week": r.due_week,
            "Is_New_Customer": r.is_new_customer,
        }
        for r in records
    ])
