"""R5 — onboarding must rank on cost that varies with the decision.

``delta_composite`` included the capacity floor, so adding demand always showed a
saving: each extra generator cancels a $15,000 idle-slot charge. On one customer
with interval 8 and a window of weeks 1-12:

    start 1-4  -> 7 generators in horizon -> delta_composite -$105,000
    start 5-12 -> 6 generators in horizon -> delta_composite  -$90,000

Penalty and overtime deltas were $0 for every candidate. The ranking was
``-15,000 x generators_that_fit_before_week_52`` and nothing else: it recommended
week 1 because week 1 is furthest from the horizon boundary. ``rank_top_n`` even
offered a ``"capacity"`` objective, ranking directly on the artefact.

``domain/fixed_plan_onboarding.py`` already excluded idle capacity for exactly
this reason. The two modes of one feature treated it oppositely.

See `.kiro/specs/correctness-remediation/requirements.md` R5.
"""

from __future__ import annotations

import pandas as pd
import pytest

from domain.onboarding import (
    OBJECTIVES,
    NewCustomer,
    evaluate_multi_customer,
)
from domain.params import IntegratedParams, SupplierParams


def _existing_sites(count: int = 40, interval: int = 4) -> pd.DataFrame:
    """Staggered US sites, well under capacity, so no candidate forces overtime."""
    return pd.DataFrame([
        {
            "site_id": f"S{i:03d}",
            "next_demand_week": (i % interval) + 1,
            "interval_weeks": interval,
            "country": "usa",
            "is_row": False,
        }
        for i in range(count)
    ])


@pytest.fixture(scope="module")
def flat_scenario():
    """A scenario where no start week has any genuine cost consequence."""
    params = IntegratedParams()
    customer = NewCustomer(
        site_id="NEW1", site_name="New Clinic",
        earliest_week=1, latest_week=12, interval_weeks=8,
    )
    result = evaluate_multi_customer(
        _existing_sites(), [customer], params, SupplierParams(),
    )
    return customer, result


# ---------------------------------------------------------------------------
# AC5.1 — indifference must be reported as indifference
# ---------------------------------------------------------------------------

def test_candidates_with_no_real_cost_difference_rank_equal(flat_scenario):
    _customer, result = flat_scenario
    candidates = result["all_results"]
    assert len(candidates) == 12

    # Precondition: the decision genuinely has no penalty or overtime effect.
    assert {round(c.delta_penalty, 6) for c in candidates} == {0.0}
    assert {round(c.delta_overtime, 6) for c in candidates} == {0.0}

    marginals = {round(c.delta_marginal, 6) for c in candidates}
    assert marginals == {0.0}, (
        f"start week still drives the ranking: {sorted(marginals)}"
    )


def test_ranking_does_not_simply_order_by_start_week(flat_scenario):
    _customer, result = flat_scenario
    ranked = result["rankings"]["marginal"]
    assert all(r.delta_marginal == pytest.approx(0.0) for r in ranked)


# ---------------------------------------------------------------------------
# AC5.2 — the horizon-edge effect is reported, not hidden
# ---------------------------------------------------------------------------

def test_generators_added_is_reported(flat_scenario):
    _customer, result = flat_scenario
    by_week = {
        r.selected_weeks["NEW1"]: r.generators_added
        for r in result["all_results"]
    }
    # interval 8 over a 52-week horizon: weeks 1-4 fit 7 deliveries, 5-12 fit 6.
    assert [by_week[w] for w in range(1, 5)] == [7, 7, 7, 7]
    assert [by_week[w] for w in range(5, 13)] == [6] * 8


def test_capacity_delta_still_reported_but_not_ranked_on(flat_scenario):
    """The artefact is disclosed rather than deleted."""
    _customer, result = flat_scenario
    params = IntegratedParams()
    for candidate in result["all_results"]:
        assert candidate.delta_capacity == pytest.approx(
            -params.capacity_rate * candidate.generators_added
        )


# ---------------------------------------------------------------------------
# AC5.3 — the meaningless objective is gone
# ---------------------------------------------------------------------------

def test_capacity_is_not_a_ranking_objective():
    assert "capacity" not in OBJECTIVES


def test_marginal_is_a_ranking_objective(flat_scenario):
    _customer, result = flat_scenario
    assert "marginal" in OBJECTIVES
    assert "marginal" in result["rankings"]


# ---------------------------------------------------------------------------
# AC5.4 — a genuine cost difference still drives the answer
# ---------------------------------------------------------------------------

def test_a_real_cost_difference_still_ranks_first():
    """Load one week heavily; a candidate landing there must rank worse."""
    params = IntegratedParams()
    existing = _existing_sites(count=40, interval=4)
    # Pile extra demand onto week 6 so a new delivery there forces early build.
    crowd = pd.DataFrame([
        {
            "site_id": f"C{i:03d}",
            "next_demand_week": 6,
            "interval_weeks": 0,
            "country": "usa",
            "is_row": False,
        }
        for i in range(26)
    ])
    existing = pd.concat([existing, crowd], ignore_index=True)

    customer = NewCustomer(
        site_id="NEW1", earliest_week=5, latest_week=7, interval_weeks=0,
    )
    result = evaluate_multi_customer(
        existing, [customer], params, SupplierParams(),
    )
    by_week = {
        r.selected_weeks["NEW1"]: r.delta_marginal for r in result["all_results"]
    }
    # Week 6 is the crowded one; it must not be cheaper than its neighbours.
    assert by_week[6] >= min(by_week[5], by_week[7])
    best = result["rankings"]["marginal"][0]
    assert best.delta_marginal == pytest.approx(min(by_week.values()))
