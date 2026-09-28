"""Domain: input cleaning, demand construction, and batch utilities (pure)."""

from __future__ import annotations

import math
from functools import lru_cache
from typing import List, Tuple

import pandas as pd

from domain.params import IntegratedParams


REQUIRED_COLS = ["site_id", "active", "next_demand_week", "interval_weeks"]
ROW_COUNTRIES = {"denmark", "uk", "netherlands", "sweden"}  # case-insensitive


def _norm_cols(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize column names to lowercase stripped strings."""
    df = df.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]
    return df



def clean_sites(
    df: pd.DataFrame, params: IntegratedParams
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Validate and clean the raw sites DataFrame.

    Rules applied
    -------------
    - Only rows where Active is Y/YES/TRUE/1 are kept.
    - The ``country`` column is optional; absent → empty string (non-ROW).
    - Duplicate Site_IDs among active rows are reported as issues and excluded.
    - Next_Demand_Week values outside 1..horizon_weeks are reported as issues.

    Parameters
    ----------
    df : pd.DataFrame
        Raw DataFrame returned by :func:`read_sites`.
    params : IntegratedParams
        Model parameters (used for horizon_weeks).

    Returns
    -------
    active : pd.DataFrame
        Cleaned active sites with columns:
        site_id, next_demand_week, interval_weeks, country, is_row
    issues_df : pd.DataFrame
        Data-quality issues with columns: row_index, site_id, issue
    """
    d = df.copy()

    d["site_id"] = d["site_id"].astype(str).str.strip()
    d["active"] = d["active"].astype(str).str.strip().str.upper()
    d["is_active"] = d["active"].isin(["Y", "YES", "TRUE", "1"])

    # Optional country column — default to empty (non-ROW) if absent
    if "country" in d.columns:
        d["country"] = d["country"].astype(str).str.strip().str.lower()
    else:
        d["country"] = ""

    d["next_demand_week_num"] = pd.to_numeric(d["next_demand_week"], errors="coerce")
    d["interval_weeks_num"] = pd.to_numeric(d["interval_weeks"], errors="coerce")

    issues: List[Tuple[int, str, str]] = []
    active = d.loc[d["is_active"]].copy()

    bad_ids: set = set()

    for idx, r in active.iterrows():
        sid = r["site_id"]
        ndw = r["next_demand_week_num"]
        itv = r["interval_weeks_num"]

        if not sid or str(sid).lower() == "nan":
            issues.append((idx, str(sid), "Missing Site_ID"))
            bad_ids.add(str(sid))
            continue
        if pd.isna(ndw) or pd.isna(itv):
            issues.append((idx, str(sid), "Missing Next_Demand_Week or Interval_Weeks"))
            bad_ids.add(str(sid))
            continue
        if ndw < 1 or ndw > params.horizon_weeks:
            issues.append(
                (idx, str(sid), f"Next_Demand_Week out of range 1..{params.horizon_weeks}")
            )
            bad_ids.add(str(sid))
        if itv < 0:
            issues.append((idx, str(sid), "Interval_Weeks must be >= 0"))
            bad_ids.add(str(sid))

    # Exclude rows with data-quality problems
    if bad_ids:
        active = active[~active["site_id"].isin(bad_ids)].copy()

    # Report and exclude duplicate Site_IDs
    dupes = active["site_id"][active["site_id"].duplicated(keep=False)]
    if not dupes.empty:
        for sid in sorted(dupes.unique()):
            issues.append((-1, str(sid), "Duplicate Site_ID among active rows"))
        active = active[~active["site_id"].isin(dupes.unique())].copy()

    active["next_demand_week"] = active["next_demand_week_num"].astype(int)
    active["interval_weeks"] = active["interval_weeks_num"].astype(int)

    # Shipping-restricted status: prefer the explicit column over inferring it.
    #
    # The Master Planner marks these sites by hand with a dark blue header fill,
    # which the converter writes out as EU_Restricted. That marking is a business
    # fact the planning team owns. Inferring the same thing from the site name is
    # guesswork that cannot distinguish "New England, NH" from England, and it
    # wrongly restricted two New Hampshire sites.
    #
    # Inference is kept only as a fallback for hand-maintained sheets that have no
    # such column. Where both exist and disagree, the explicit value wins and the
    # disagreement is reported, so a missed marking surfaces instead of silently
    # dropping the constraint.
    inferred = active["country"].isin(ROW_COUNTRIES)
    if "eu_restricted" in active.columns:
        explicit = (
            active["eu_restricted"].astype(str).str.strip().str.upper()
            .isin(["Y", "YES", "TRUE", "1"])
        )
        active["is_row"] = explicit
        for idx, sid, exp, inf in zip(
            active.index, active["site_id"], explicit, inferred
        ):
            if exp != inf:
                issues.append((
                    int(idx), str(sid),
                    f"EU_Restricted is '{'Y' if exp else 'N'}' but the site name "
                    f"reads as {'a restricted' if inf else 'a non-restricted'} "
                    f"country; the explicit column was used. Confirm which is right"
                ))
    else:
        active["is_row"] = inferred

    issues_df = pd.DataFrame(
        issues, columns=["row_index", "site_id", "issue"]
    ).sort_values(["issue", "site_id", "row_index"])

    keep = ["site_id", "next_demand_week", "interval_weeks", "country", "is_row"]
    active = active[keep].reset_index(drop=True)
    return active, issues_df.reset_index(drop=True)



def build_weekly_demand(
    active: pd.DataFrame, params: IntegratedParams
) -> List[int]:
    """
    Build a 1-indexed demand array across the planning horizon.

    Each active site contributes 1 unit of demand at its ``next_demand_week``
    and then every ``interval_weeks`` thereafter, wrapping within the horizon.

    Parameters
    ----------
    active : pd.DataFrame
        Cleaned active sites from :func:`clean_sites`.
        Must have columns: next_demand_week, interval_weeks.
    params : IntegratedParams
        Model parameters (used for horizon_weeks).

    Returns
    -------
    List[int]
        demand[t] for t = 1..horizon_weeks (index 0 unused, index 1 = week 1).
        demand[0] is always 0.
    """
    demand = [0] * (params.horizon_weeks + 1)  # 1-indexed; index 0 unused

    for _, row in active.iterrows():
        week = int(row["next_demand_week"])
        interval = int(row["interval_weeks"])
        if interval < 0:
            continue  # safety: skip malformed rows
        if interval == 0:
            # One-time delivery: demand only at next_demand_week, no recurrence
            if 1 <= week <= params.horizon_weeks:
                demand[week] += 1
        else:
            while week <= params.horizon_weeks:
                demand[week] += 1
                week += interval

    return demand



def build_weekly_row_demand(
    active: pd.DataFrame, params: IntegratedParams
) -> List[int]:
    """
    Build a 1-indexed ROW demand array across the planning horizon.

    Only sites where ``is_row`` is True (Denmark, UK, Netherlands, Sweden)
    contribute to this array.  The structure mirrors :func:`build_weekly_demand`.

    Parameters
    ----------
    active : pd.DataFrame
        Cleaned active sites from :func:`clean_sites`.
        Must have columns: next_demand_week, interval_weeks, is_row.
    params : IntegratedParams
        Model parameters (used for horizon_weeks).

    Returns
    -------
    List[int]
        row_demand[t] for t = 1..horizon_weeks (index 0 unused, index 1 = week 1).
        row_demand[0] is always 0.
    """
    row_demand = [0] * (params.horizon_weeks + 1)  # 1-indexed; index 0 unused

    row_sites = active[active["is_row"]]
    for _, row in row_sites.iterrows():
        week = int(row["next_demand_week"])
        interval = int(row["interval_weeks"])
        if interval < 0:
            continue
        if interval == 0:
            if 1 <= week <= params.horizon_weeks:
                row_demand[week] += 1
        else:
            while week <= params.horizon_weeks:
                row_demand[week] += 1
                week += interval

    return row_demand



@lru_cache(maxsize=256)
def _realisable_levels(
    min_good: int, max_good: int, max_batches: int, cap_max: int
) -> tuple[int, ...]:
    """Cached level set, keyed on scalars so the cache key is hashable."""
    levels = {0}
    for batches in range(1, max_batches + 1):
        low = batches * min_good
        high = min(batches * max_good, cap_max)
        if low > cap_max:
            break
        levels.update(range(low, high + 1))
    return tuple(sorted(level for level in levels if level <= cap_max))


def realisable_good_levels(
    params: IntegratedParams, cap_max: int
) -> List[int]:
    """Good-unit totals a week can physically produce, up to ``cap_max``.

    A week runs some whole number of batches, and each batch yields between
    ``min_good_per_batch`` and ``max_good_per_batch`` good units. So ``y`` is
    realisable when ``y == 0``, or when some batch count ``k`` in
    ``1..overtime_max_batches`` satisfies::

        k * min_good_per_batch  <=  y  <=  k * max_good_per_batch

    With the default ``min_batch_produced = 2`` a batch can yield a single good
    unit, so every integer in ``0..cap_max`` is realisable and this returns the
    same range the solver used to enumerate blindly. Raising
    ``min_batch_produced`` is what makes the set sparse — and that setting was
    previously ignored entirely, so the solver would happily plan a one-unit batch
    under a fifteen-unit minimum while the exported parameter sheet claimed
    otherwise.

    Note the windows overlap once ``max_good`` exceeds ``2 * min_good``: with a
    7-unit minimum, 14 is reachable as one batch of 14 or two of 7. Overlap is
    fine — the set is what matters, not which batch count produced it.

    Parameters
    ----------
    params : IntegratedParams
        Supplies the batch bounds and the overtime batch ceiling.
    cap_max : int
        The week's good-unit ceiling (0 for shutdown, one batch for partial,
        ``overtime_max_good_week`` otherwise).

    Returns
    -------
    List[int]
        Ascending, always starting with 0.
    """
    if cap_max <= 0:
        return [0]
    return list(
        _realisable_levels(
            params.min_good_per_batch,
            params.max_good_per_batch,
            params.overtime_max_batches,
            int(cap_max),
        )
    )


def batches_needed(good_units: int, params: IntegratedParams) -> int:
    """
    Return the fewest batches that can produce exactly ``good_units`` good units.

    Each batch yields between ``min_good_per_batch`` and ``max_good_per_batch``
    good units. 0 good units → 0 batches.

    Parameters
    ----------
    good_units : int
        Target good units (must be non-negative).
    params : IntegratedParams
        Model parameters.

    Returns
    -------
    int
        Number of batches, between 1 and ``overtime_max_batches``.

    Raises
    ------
    ValueError
        If ``good_units`` is negative, or no batch count can produce it. The
        latter is reachable only with a raised ``min_batch_produced``: 7 good
        units cannot come from batches that each yield at least 15.
    """
    if good_units < 0:
        raise ValueError(f"good_units must be >= 0, got {good_units}.")
    if good_units == 0:
        return 0

    minimum = math.ceil(good_units / params.max_good_per_batch)
    for batches in range(minimum, params.overtime_max_batches + 1):
        if batches * params.min_good_per_batch <= good_units:
            return batches

    raise ValueError(
        f"{good_units} good units cannot be produced: batches yield "
        f"{params.min_good_per_batch}..{params.max_good_per_batch} good units "
        f"each and at most {params.overtime_max_batches} run per week."
    )



def split_good_into_batches(
    good_units: int, params: IntegratedParams
) -> List[int]:
    """
    Split ``good_units`` into individual batch sizes (good units per batch).

    Every batch is given its minimum yield first, then the remainder is
    front-loaded up to the per-batch maximum. Earlier batches therefore run
    fullest, which is what the plan sheet shows.

    Seeding the minimum matters once ``min_batch_produced`` is raised above its
    default. Filling greedily from the front would split 16 good units into
    ``[15, 1]``, and a 1-unit batch is impossible when every batch must yield at
    least 7. Seeding gives ``[9, 7]``. At the default minimum of one good unit the
    seed is all ones and the result is identical to plain front-loading, so the
    documented examples below still hold.

    Parameters
    ----------
    good_units : int
        Total good units to split (non-negative).
    params : IntegratedParams
        Model parameters.

    Returns
    -------
    List[int]
        List of good-unit counts per batch, e.g. [15, 15] for 30 good units,
        [15, 7] for 22 good units.
        Empty list when good_units == 0.

    Raises
    ------
    ValueError
        If good_units is negative, or no batch count can produce it
        (see :func:`batches_needed`).
    """
    n = batches_needed(good_units, params)
    if n == 0:
        return []

    minimum = params.min_good_per_batch
    maximum = params.max_good_per_batch
    result = [minimum] * n
    remainder = good_units - minimum * n

    for index in range(n):
        if remainder <= 0:
            break
        room = maximum - result[index]
        added = min(room, remainder)
        result[index] += added
        remainder -= added

    return result

