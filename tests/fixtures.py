"""Deterministic synthetic test data.

The test suite deliberately does **not** ship a sites file derived from real
customer records. This module generates an equivalent one instead: same shape and
comparable characteristics, but entirely fabricated identifiers.

The generated data is tuned to exercise the behaviour the tests care about:

* enough sites and lumpy enough demand that different objective weights produce
  genuinely different production plans;
* peak weeks above normal weekly capacity, so the solver must build early;
* restricted-country (EU) demand that exceeds the QC shipping cap in some weeks,
  so ``row_cap`` enforcement is exercised;
* total demand comfortably inside annual capacity, so every scenario stays
  feasible across the 52-week horizon.
"""

from __future__ import annotations

from pathlib import Path

# Countries treated as EU-restricted by the domain layer.
EU_COUNTRIES = ("denmark", "uk", "netherlands", "sweden")
OTHER_COUNTRIES = ("usa", "usa", "usa", "usa", "canada")

SITE_COUNT = 176
EU_SITE_COUNT = 26

# Replacement intervals, cycled across sites. The mix of 6..11 weeks creates
# natural clustering, so weekly demand is uneven rather than flat.
INTERVALS = (6, 7, 7, 8, 8, 9, 10, 11)


def build_sites_rows() -> list[dict]:
    """Return synthetic site rows, deterministic across runs."""
    rows: list[dict] = []

    for index in range(SITE_COUNT):
        site_id = f"SN{index + 1:04d}"
        interval = INTERVALS[index % len(INTERVALS)]

        if index < EU_SITE_COUNT:
            # Cluster the EU sites onto a few start weeks so that several weeks
            # carry more restricted-country demand than the QC cap allows.
            country = EU_COUNTRIES[index % len(EU_COUNTRIES)]
            next_week = 3 + (index % 5)
        else:
            country = OTHER_COUNTRIES[index % len(OTHER_COUNTRIES)]
            next_week = 1 + (index % 12)

        rows.append({
            "site_id": site_id,
            "active": "Y",
            "next_demand_week": next_week,
            "interval_weeks": interval,
            "country": country,
        })

    return rows


def sites_csv_text() -> str:
    """Return the synthetic sites file as CSV text."""
    rows = build_sites_rows()
    header = "Site_ID,Active,Next_Demand_Week,Interval_Weeks,Country"
    lines = [header] + [
        f"{r['site_id']},{r['active']},{r['next_demand_week']},"
        f"{r['interval_weeks']},{r['country']}"
        for r in rows
    ]
    return "\n".join(lines) + "\n"


def write_sites_csv(path: str | Path) -> str:
    """Write the synthetic sites CSV to *path* and return the path as a string."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(sites_csv_text())
    return str(target)
