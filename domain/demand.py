"""Domain: input cleaning, demand construction, and batch utilities (pure)."""

from __future__ import annotations

import math
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

    issues_df = pd.DataFrame(
        issues, columns=["row_index", "site_id", "issue"]
    ).sort_values(["issue", "site_id", "row_index"])

    active["next_demand_week"] = active["next_demand_week_num"].astype(int)
    active["interval_weeks"] = active["interval_weeks_num"].astype(int)
    active["is_row"] = active["country"].isin(ROW_COUNTRIES)

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



def batches_needed(good_units: int, params: IntegratedParams) -> int:
    """
    Return the number of batches required to produce ``good_units`` good units.

    Each batch yields 1..15 good units (batch size 2..16 minus 1 test discard).
    0 good units → 0 batches.

    Parameters
    ----------
    good_units : int
        Target good units (must be non-negative).
    params : IntegratedParams
        Model parameters.

    Returns
    -------
    int
        Number of batches (0, 1, 2, or 3).

    Raises
    ------
    ValueError
        If good_units is negative.
    """
    if good_units < 0:
        raise ValueError(f"good_units must be >= 0, got {good_units}.")
    if good_units == 0:
        return 0
    return math.ceil(good_units / params.max_good_per_batch)



def split_good_into_batches(
    good_units: int, params: IntegratedParams
) -> List[int]:
    """
    Split ``good_units`` into individual batch sizes (good units per batch).

    Each batch yields 1..15 good units. Earlier batches are filled to the
    maximum (15) first; the last batch takes the remainder.
    Returns a list of length ``batches_needed(good_units, params)``.

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
        If good_units is negative (see :func:`batches_needed`).
    """
    n = batches_needed(good_units, params)
    if n == 0:
        return []
    result = []
    rem = good_units
    for _ in range(n):
        alloc = min(rem, params.max_good_per_batch)
        result.append(alloc)
        rem -= alloc
    return result

