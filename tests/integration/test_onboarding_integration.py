"""Integration tests for the onboarding workflow, including timing.

Covers Requirement E-4.14: a realistic problem must finish within a time budget
acceptable for interactive use, and the generated input file must round-trip.
"""

from __future__ import annotations

import time

import pandas as pd
import pytest

from domain.demand import build_weekly_demand, clean_sites
from domain.onboarding import NewCustomer
from domain.params import IntegratedParams, SupplierParams
from io_adapters.input_file_writer import InputFileWriter
from io_adapters.sites_reader import ExcelSitesReader
from services.dtos import OnboardingRequest
from services.onboarding_service import OnboardingService

HORIZON = 26
PARAMS = IntegratedParams(horizon_weeks=HORIZON, w_capacity=0.0)
SUPPLIER = SupplierParams(
    curium_quarterly_quota_mci=0.0, bwxt_quarterly_quota_mci=0.0
)

EXISTING_CSV = (
    "Site_ID,Active,Next_Demand_Week,Interval_Weeks,Country\n"
    "00449,Y,2,7,usa\n"
    "00438,Y,4,8,usa\n"
    "00411,Y,6,9,usa\n"
    "1401,Y,3,7,denmark\n"
).encode()


@pytest.fixture
def service():
    return OnboardingService(ExcelSitesReader(), InputFileWriter())


def _request(customers, **over):
    base = dict(
        file_bytes=EXISTING_CSV, filename="sites.csv", sheet="Sites",
        new_customers=tuple(customers), params=PARAMS, supplier_params=SUPPLIER,
    )
    base.update(over)
    return OnboardingRequest(**base)


def _three_customers():
    return [
        NewCustomer("SN-1", site_name="Alpha", earliest_week=2, latest_week=7,
                    interval_weeks=7, country="usa"),
        NewCustomer("SN-2", site_name="Beta", earliest_week=4, latest_week=9,
                    interval_weeks=8, country="usa"),
        NewCustomer("SN-3", site_name="Gamma", earliest_week=6, latest_week=11,
                    interval_weeks=9, country="uk", eu_restricted=True),
    ]


# ---------------------------------------------------------------------------
# Exhaustive path and timing
# ---------------------------------------------------------------------------

def test_three_customers_six_week_windows_is_exhaustive(service):
    """3 customers x 6-week windows = 216 combinations, under the 500 threshold."""
    customers = _three_customers()
    est = service.estimate(customers)
    assert est["combinations"] == 216
    assert est["exhaustive"] is True


def test_three_customers_completes_within_time_budget(service):
    customers = _three_customers()
    start = time.monotonic()
    result = service.run(_request(customers))
    elapsed = time.monotonic() - start
    assert result.used_heuristic is False
    assert result.combinations_evaluated == 216
    # Generous ceiling: the point is that it is interactive, not a fixed number.
    assert elapsed < 120, f"took {elapsed:.1f}s, expected well under 2 minutes"


def test_rankings_present_for_every_objective(service):
    result = service.run(_request(_three_customers()))
    for objective in ("penalty", "overtime", "capacity"):
        assert result.rankings[objective], f"no options ranked for {objective}"
        assert len(result.rankings[objective]) <= 5


def test_ranked_weeks_inside_each_window(service):
    customers = _three_customers()
    result = service.run(_request(customers))
    windows = {c.site_id: (c.earliest_week, c.latest_week) for c in customers}
    for options in result.rankings.values():
        for option in options:
            for site_id, week in option.selected_weeks.items():
                low, high = windows[site_id]
                assert low <= week <= high


def test_rankings_sorted_ascending_by_objective(service):
    result = service.run(_request(_three_customers()))
    for objective, field in (
        ("penalty", "delta_penalty"),
        ("overtime", "delta_overtime"),
        ("capacity", "delta_capacity"),
    ):
        values = [getattr(o, field) for o in result.rankings[objective]]
        assert values == sorted(values)


def test_deltas_relative_to_shared_baseline(service):
    result = service.run(_request(_three_customers()))
    base = result.base_summary
    for option in result.rankings["penalty"]:
        assert option.delta_penalty == pytest.approx(
            option.total_penalty - base["total_penalty_cost"]
        )


# ---------------------------------------------------------------------------
# Heuristic path
# ---------------------------------------------------------------------------

def test_large_space_switches_to_heuristic_and_stays_bounded(service, monkeypatch):
    import domain.onboarding as onboarding
    monkeypatch.setattr(onboarding, "EXHAUSTIVE_THRESHOLD", 20)
    customers = _three_customers()
    result = service.run(_request(customers), progress=None)
    assert result.used_heuristic is True
    assert result.combinations_evaluated < result.search_space


def test_heuristic_finds_the_exhaustive_optimum(service, monkeypatch):
    """On a space small enough to check, the heuristic must not lose the optimum."""
    customers = [
        NewCustomer("SN-1", earliest_week=2, latest_week=5, interval_weeks=7,
                    country="usa"),
        NewCustomer("SN-2", earliest_week=4, latest_week=7, interval_weeks=8,
                    country="usa"),
    ]
    exhaustive = service.run(_request(customers))
    assert exhaustive.used_heuristic is False
    best_exhaustive = exhaustive.rankings["penalty"][0].delta_composite

    import domain.onboarding as onboarding
    monkeypatch.setattr(onboarding, "EXHAUSTIVE_THRESHOLD", 1)
    heuristic = service.run(_request(customers))
    assert heuristic.used_heuristic is True
    assert heuristic.rankings["penalty"][0].delta_composite == pytest.approx(
        best_exhaustive
    )


# ---------------------------------------------------------------------------
# Generated input file round-trip
# ---------------------------------------------------------------------------

def test_generated_file_round_trips_cleanly(service):
    customers = _three_customers()
    result = service.run(_request(customers))
    best = result.rankings["penalty"][0]

    data = service.generate_input_file(
        EXISTING_CSV, "sites.csv", "Sites", customers, best.selected_weeks
    )

    raw = ExcelSitesReader().read(data, sheet="Sites")
    active, issues = clean_sites(raw, PARAMS)

    # All new customers present and active, with no issues attributable to them
    new_ids = {c.site_id for c in customers}
    assert new_ids <= set(active["site_id"])
    assert issues[issues["site_id"].isin(new_ids)].empty

    # Existing sites preserved, including the zero-padded account codes
    assert {"00449", "00438", "00411", "1401"} <= set(active["site_id"])


def test_generated_file_demand_reflects_selected_weeks(service):
    customers = _three_customers()
    result = service.run(_request(customers))
    best = result.rankings["penalty"][0]
    data = service.generate_input_file(
        EXISTING_CSV, "sites.csv", "Sites", customers, best.selected_weeks
    )
    raw = ExcelSitesReader().read(data, sheet="Sites")
    active, _ = clean_sites(raw, PARAMS)
    by_id = active.set_index("site_id")
    for site_id, week in best.selected_weeks.items():
        assert int(by_id.loc[site_id, "next_demand_week"]) == week


def test_generated_file_increases_total_demand(service):
    customers = _three_customers()
    result = service.run(_request(customers))
    best = result.rankings["penalty"][0]

    before, _ = clean_sites(ExcelSitesReader().read(EXISTING_CSV, is_csv=True), PARAMS)
    baseline_demand = sum(build_weekly_demand(before, PARAMS))

    data = service.generate_input_file(
        EXISTING_CSV, "sites.csv", "Sites", customers, best.selected_weeks
    )
    after, _ = clean_sites(ExcelSitesReader().read(data, sheet="Sites"), PARAMS)
    assert sum(build_weekly_demand(after, PARAMS)) > baseline_demand
    assert len(after) == len(before) + len(customers)
