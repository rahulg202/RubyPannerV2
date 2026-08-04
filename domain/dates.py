"""Domain: reference-week calendar date derivation (pure).

Maps planning week numbers to real manufacturing and calibration dates, anchored
by a configurable reference week. See .kiro/specs/optimizer-enhancements/design.md
Feature 6. No I/O, no UI.
"""

from __future__ import annotations

from datetime import date, timedelta

DAYS_PER_WEEK = 7


def derive_week_dates(
    reference_week_date: date,
    calibration_offset_days: int,
    horizon_weeks: int,
) -> list[tuple[int, date, date]]:
    """Return ``(week_number, mfg_date, cal_date)`` for each week in the horizon.

    Week 1 is anchored to ``reference_week_date`` (a manufacturing date). Each
    subsequent week's manufacturing date is 7 days later, and its calibration
    date is the manufacturing date plus ``calibration_offset_days``.

    Parameters
    ----------
    reference_week_date : date
        Manufacturing date of planning week 1.
    calibration_offset_days : int
        Days from manufacturing date to calibration date (>= 0).
    horizon_weeks : int
        Number of weeks to generate (>= 1).

    Returns
    -------
    list[tuple[int, date, date]]
        One tuple per week, ``week`` running 1..horizon_weeks.

    Raises
    ------
    ValueError
        If ``calibration_offset_days`` is negative or ``horizon_weeks`` < 1.
    """
    if calibration_offset_days < 0:
        raise ValueError(
            f"calibration_offset_days must be >= 0, got {calibration_offset_days}."
        )
    if horizon_weeks < 1:
        raise ValueError(f"horizon_weeks must be >= 1, got {horizon_weeks}.")

    result: list[tuple[int, date, date]] = []
    for week in range(1, horizon_weeks + 1):
        mfg = reference_week_date + timedelta(days=DAYS_PER_WEEK * (week - 1))
        cal = mfg + timedelta(days=calibration_offset_days)
        result.append((week, mfg, cal))
    return result


def current_planning_week(
    reference_week_date: date,
    today: date,
    horizon_weeks: int = 52,
) -> int | None:
    """Return the planning week containing ``today``, or None if outside horizon.

    Week 1 spans ``reference_week_date`` .. +6 days; week 2 the following 7 days,
    and so on. Dates before week 1 or after the final week return ``None``.
    """
    delta_days = (today - reference_week_date).days
    if delta_days < 0:
        return None
    week = delta_days // DAYS_PER_WEEK + 1
    if week > horizon_weeks:
        return None
    return week
