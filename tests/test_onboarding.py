"""Unit tests for the onboarding recommendation engine.

Tests the marginal-cost approach: run the full 52-week optimizer with and
without new sites, then compare costs.
"""

import io
import warnings

import pandas as pd
import pytest

from integrated_cost_optimizer import IntegratedParams, clean_sites, batches_needed
from onboarding_recommendation import (
    validate_onboarding_inputs,
    enumerate_candidates,
    evaluate_all_candidates,
    evaluate_candidate,
    run_baseline,
    rank_and_select_top5,
    add_new_sites_demand,
    add_new_sites_row_demand,
    compute_batch_metrics,
    format_cost_thousands,
    export_recommendation_excel,
)


@pytest.fixture
def default_params() -> IntegratedParams:
    return IntegratedParams()


@pytest.fixture
def small_active_df(default_params) -> pd.DataFrame:
    """A small active sites DataFrame with 3 sites for fast tests."""
    raw = pd.DataFrame({
        "site_id": ["S1", "S2", "S3"],
        "active": ["Y", "Y", "Y"],
        "next_demand_week": [1, 3, 5],
        "interval_weeks": [10, 10, 10],
        "country": ["usa", "usa", "usa"],
    })
    active, _ = clean_sites(raw, default_params)
    return active


# ---------------------------------------------------------------------------
# Demand injection
# ---------------------------------------------------------------------------

class TestDemandInjection:
    """Verify add_new_sites_demand correctly adds recurring demand."""

    def test_single_site_demand(self, default_params):
        base = [0] * (default_params.horizon_weeks + 1)
        sites = [{"interval_weeks": 10, "country": "usa"}]
        d = add_new_sites_demand(base, sites, 5, default_params)
        # Should have demand at weeks 5, 15, 25, 35, 45
        for w in [5, 15, 25, 35, 45]:
            assert d[w] == 1
        # Week 55 is beyond horizon
        assert d[52] == 0 or 52 in [5, 15, 25, 35, 45]

    def test_multiple_sites_stack(self, default_params):
        base = [0] * (default_params.horizon_weeks + 1)
        sites = [
            {"interval_weeks": 10, "country": "usa"},
            {"interval_weeks": 10, "country": "usa"},
        ]
        d = add_new_sites_demand(base, sites, 5, default_params)
        assert d[5] == 2  # both sites demand at week 5

    def test_does_not_mutate_base(self, default_params):
        base = [0] * (default_params.horizon_weeks + 1)
        sites = [{"interval_weeks": 7, "country": "usa"}]
        _ = add_new_sites_demand(base, sites, 1, default_params)
        assert all(v == 0 for v in base)

    def test_row_demand_only_for_row_countries(self, default_params):
        base_row = [0] * (default_params.horizon_weeks + 1)
        sites = [
            {"interval_weeks": 10, "country": "usa"},
            {"interval_weeks": 10, "country": "denmark"},
        ]
        rd = add_new_sites_row_demand(base_row, sites, 5, default_params)
        # Only denmark site adds ROW demand
        assert rd[5] == 1


# ---------------------------------------------------------------------------
# Baseline
# ---------------------------------------------------------------------------

class TestBaseline:
    """Verify run_baseline returns a valid summary."""

    def test_baseline_returns_summary_keys(self, small_active_df, default_params):
        summary = run_baseline(small_active_df, default_params)
        assert "total_penalty_cost" in summary
        assert "total_overtime_cost" in summary
        assert "total_capacity_cost" in summary
        assert "overtime_weeks" in summary


# ---------------------------------------------------------------------------
# Candidate evaluation
# ---------------------------------------------------------------------------

class TestCandidateEvaluation:
    """Verify evaluate_candidate returns marginal costs."""

    def test_returns_delta_keys(self, small_active_df, default_params):
        base = run_baseline(small_active_df, default_params)
        sites = [{"interval_weeks": 7, "country": "usa"}]
        r = evaluate_candidate(small_active_df, sites, 3, default_params, base)
        assert r is not None
        assert "delta_penalty" in r
        assert "delta_overtime" in r
        assert "delta_capacity" in r
        assert "delta_composite" in r
        assert "plan_df" in r

    def test_delta_is_difference(self, small_active_df, default_params):
        base = run_baseline(small_active_df, default_params)
        sites = [{"interval_weeks": 7, "country": "usa"}]
        r = evaluate_candidate(small_active_df, sites, 3, default_params, base)
        assert r is not None
        assert abs(r["delta_penalty"] - (r["total_penalty"] - base["total_penalty_cost"])) < 0.01
        assert abs(r["delta_overtime"] - (r["total_overtime"] - base["total_overtime_cost"])) < 0.01
        assert abs(r["delta_capacity"] - (r["total_capacity"] - base["total_capacity_cost"])) < 0.01

    def test_different_weeks_give_different_costs(self, small_active_df, default_params):
        base = run_baseline(small_active_df, default_params)
        sites = [{"interval_weeks": 7, "country": "usa"}]
        r1 = evaluate_candidate(small_active_df, sites, 1, default_params, base)
        r5 = evaluate_candidate(small_active_df, sites, 5, default_params, base)
        assert r1 is not None and r5 is not None
        # At least one cost component should differ
        assert (r1["delta_penalty"] != r5["delta_penalty"] or
                r1["delta_overtime"] != r5["delta_overtime"] or
                r1["delta_capacity"] != r5["delta_capacity"])


# ---------------------------------------------------------------------------
# Full evaluation and ranking
# ---------------------------------------------------------------------------

class TestEvaluateAllAndRank:
    """Test evaluate_all_candidates and rank_and_select_top5."""

    def test_returns_base_and_results(self, small_active_df, default_params):
        sites = [{"interval_weeks": 7, "country": "usa"}]
        base, results = evaluate_all_candidates(
            small_active_df, sites, 1, 5, default_params,
        )
        assert "total_penalty_cost" in base
        assert len(results) == 5  # weeks 1-5

    def test_top5_has_three_objectives(self, small_active_df, default_params):
        sites = [{"interval_weeks": 7, "country": "usa"}]
        _, results = evaluate_all_candidates(
            small_active_df, sites, 1, 5, default_params,
        )
        top5 = rank_and_select_top5(results)
        assert set(top5.keys()) == {"penalty", "overtime", "capacity"}

    def test_top5_sorted_by_delta(self, small_active_df, default_params):
        sites = [{"interval_weeks": 7, "country": "usa"}]
        _, results = evaluate_all_candidates(
            small_active_df, sites, 1, 5, default_params,
        )
        top5 = rank_and_select_top5(results)
        for key in ("penalty", "overtime", "capacity"):
            deltas = [r[f"delta_{key}"] for r in top5[key]]
            assert deltas == sorted(deltas)

    def test_top5_capped_at_5(self, small_active_df, default_params):
        sites = [{"interval_weeks": 7, "country": "usa"}]
        _, results = evaluate_all_candidates(
            small_active_df, sites, 1, 10, default_params,
        )
        top5 = rank_and_select_top5(results)
        for key in ("penalty", "overtime", "capacity"):
            assert len(top5[key]) <= 5


# ---------------------------------------------------------------------------
# Batch metrics
# ---------------------------------------------------------------------------

class TestBatchMetrics:
    """Verify compute_batch_metrics works with full optimizer plan_df."""

    def test_metrics_from_plan_df(self, small_active_df, default_params):
        base = run_baseline(small_active_df, default_params)
        sites = [{"interval_weeks": 7, "country": "usa"}]
        r = evaluate_candidate(small_active_df, sites, 3, default_params, base)
        assert r is not None
        metrics = compute_batch_metrics(r["plan_df"], default_params)
        assert "weeks_1_batch" in metrics
        assert "weeks_2_batch" in metrics
        assert "weeks_3_batch" in metrics
        total = metrics["weeks_1_batch"] + metrics["weeks_2_batch"] + metrics["weeks_3_batch"]
        assert total > 0  # at least some production weeks


# ---------------------------------------------------------------------------
# Excel export
# ---------------------------------------------------------------------------

class TestExcelExport:
    """Verify Excel export structure for the new marginal-cost format."""

    def test_export_has_correct_sheets(self, small_active_df, default_params):
        sites = [{"interval_weeks": 7, "country": "usa"}]
        base, results = evaluate_all_candidates(
            small_active_df, sites, 1, 3, default_params,
        )
        top5 = rank_and_select_top5(results)
        xlsx = export_recommendation_excel(top5, base, default_params)

        xf = pd.ExcelFile(io.BytesIO(xlsx))
        assert "Summary" in xf.sheet_names
        assert "By_Penalty" in xf.sheet_names
        assert "By_Overtime" in xf.sheet_names
        assert "By_Capacity" in xf.sheet_names

    def test_summary_has_baseline_row(self, small_active_df, default_params):
        sites = [{"interval_weeks": 7, "country": "usa"}]
        base, results = evaluate_all_candidates(
            small_active_df, sites, 1, 3, default_params,
        )
        top5 = rank_and_select_top5(results)
        xlsx = export_recommendation_excel(top5, base, default_params)

        summary = pd.read_excel(io.BytesIO(xlsx), sheet_name="Summary")
        assert "BASELINE" in summary["Ranked_By"].values

    def test_summary_has_delta_columns(self, small_active_df, default_params):
        sites = [{"interval_weeks": 7, "country": "usa"}]
        base, results = evaluate_all_candidates(
            small_active_df, sites, 1, 3, default_params,
        )
        top5 = rank_and_select_top5(results)
        xlsx = export_recommendation_excel(top5, base, default_params)

        summary = pd.read_excel(io.BytesIO(xlsx), sheet_name="Summary")
        assert "Delta_Penalty" in summary.columns
        assert "Delta_Overtime" in summary.columns
        assert "Delta_Capacity" in summary.columns
