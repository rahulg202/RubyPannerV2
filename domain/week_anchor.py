"""Domain: anchor planning week 1 to a real manufacturing date (pure).

The Master Planner numbers its weeks 1..52 and restarts every January. Reading it
by calendar year therefore forces the plan to start on the first week of January,
which is not how the team plans — and, worse, leaves the anchor unstated, so
``Next_Demand_Week = 3`` could not be traced back to any real date.

Anchoring solves both. The planner names the manufacturing date to start from; the
row holding that date becomes planning week 1, and the horizon runs forward from
there, crossing the year boundary as a matter of course.

The same anchoring must be applied wherever the Master Planner is read. If the
input file were anchored to a July reference week while the comparison still read
calendar-year week numbers, every per-customer week would be offset by however far
into the year the reference sits — a worse failure than not anchoring at all,
because it would look plausible. Both readers call this module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Sequence

DAYS_PER_WEEK = 7


@dataclass(frozen=True)
class DatedRow:
    """One candidate Master Planner row: its position and manufacturing date."""

    position: int          # opaque index the caller uses to find the row again
    mfg_date: date


@dataclass
class AnchoredWindow:
    """The horizon window resolved against a reference week."""

    # (planning_week, row_position) for each week in the window, week 1 first.
    weeks: list[tuple[int, int]] = field(default_factory=list)
    anchor_date: date | None = None
    anchor_position: int | None = None
    last_date: date | None = None
    rows_available: int = 0
    snapped: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def resolved(self) -> bool:
        return bool(self.weeks)

    def week_for(self, position: int) -> int | None:
        """Planning week for a row position, or None if outside the window."""
        for week, pos in self.weeks:
            if pos == position:
                return week
        return None


def anchor_window(
    rows: Sequence[DatedRow],
    reference_week_date: date,
    horizon_weeks: int = 52,
) -> AnchoredWindow:
    """Resolve the planning horizon starting at ``reference_week_date``.

    Parameters
    ----------
    rows : Sequence[DatedRow]
        Candidate rows with manufacturing dates. Order does not matter; rows are
        sorted by date and duplicate dates are dropped, keeping the first.
    reference_week_date : date
        The manufacturing date planning week 1 should start from.
    horizon_weeks : int
        How many weeks to take, starting at the anchor.

    Returns
    -------
    AnchoredWindow
        ``resolved`` is False when the reference falls after every dated row, in
        which case ``warnings`` explains why and the caller should not proceed.

    Notes
    -----
    The anchor is the row whose own week *contains* the reference date, treating
    each row as covering its manufacturing date plus the following six days. An
    exact match is not required, because a planner picking "the week of the 27th"
    should not have to know the precise manufacturing date on file. When the
    reference lands between rows the window snaps to the row before it and says so,
    rather than silently rounding either way.
    """
    result = AnchoredWindow()

    ordered: list[DatedRow] = []
    seen: set[date] = set()
    for row in sorted(rows, key=lambda r: r.mfg_date):
        if row.mfg_date in seen:
            continue
        seen.add(row.mfg_date)
        ordered.append(row)

    if not ordered:
        result.warnings.append(
            "No manufacturing dates were found, so the reference week could not "
            "be located. Reading by calendar year instead."
        )
        return result

    first, last = ordered[0].mfg_date, ordered[-1].mfg_date
    result.last_date = last

    if reference_week_date > last + timedelta(days=DAYS_PER_WEEK - 1):
        result.warnings.append(
            f"The reference week ({reference_week_date:%d %b %Y}) falls after the "
            f"last week in the Master Planner ({last:%d %b %Y}), so there is "
            f"nothing to import. Choose an earlier reference week, or extend the "
            f"workbook."
        )
        return result

    anchor_index = None
    for index, row in enumerate(ordered):
        window_end = row.mfg_date + timedelta(days=DAYS_PER_WEEK - 1)
        if row.mfg_date <= reference_week_date <= window_end:
            anchor_index = index
            break

    if anchor_index is None:
        if reference_week_date < first:
            anchor_index = 0
            result.snapped = True
            result.warnings.append(
                f"The reference week ({reference_week_date:%d %b %Y}) is before "
                f"the first week in the Master Planner, so the import starts at "
                f"{first:%d %b %Y}."
            )
        else:
            # Falls in a gap between rows: take the row before it.
            candidates = [i for i, r in enumerate(ordered)
                          if r.mfg_date <= reference_week_date]
            anchor_index = candidates[-1]
            result.snapped = True
            result.warnings.append(
                f"No Master Planner week starts on {reference_week_date:%d %b %Y}. "
                f"The import starts from the closest earlier week, "
                f"{ordered[anchor_index].mfg_date:%d %b %Y}."
            )

    window = ordered[anchor_index:anchor_index + horizon_weeks]
    result.weeks = [(week, row.position) for week, row in enumerate(window, start=1)]
    result.anchor_date = ordered[anchor_index].mfg_date
    result.anchor_position = ordered[anchor_index].position
    result.rows_available = len(window)

    if len(window) < horizon_weeks:
        result.warnings.append(
            f"Only {len(window)} of {horizon_weeks} weeks are available after "
            f"{result.anchor_date:%d %b %Y} — the Master Planner ends at "
            f"{last:%d %b %Y}. The remaining weeks have no scheduled demand."
        )

    return result


def describe_anchor(window: AnchoredWindow, horizon_weeks: int = 52) -> str:
    """One line stating what week 1 is, for display next to the result."""
    if not window.resolved or window.anchor_date is None:
        return "Planning week 1 could not be anchored to a date."
    last_week, _pos = window.weeks[-1]
    end = window.anchor_date + timedelta(days=DAYS_PER_WEEK * (last_week - 1))
    return (
        f"Planning week 1 = week starting {window.anchor_date:%d %b %Y}; "
        f"week {last_week} = week starting {end:%d %b %Y} "
        f"({window.rows_available} of {horizon_weeks} weeks found)."
    )
