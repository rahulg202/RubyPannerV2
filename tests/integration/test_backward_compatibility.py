"""Backward-compatibility guarantees (Requirement E-8).

The layered refactor must not break anything that worked before: legacy-format
input files, the documented public API of the original modules, and the CLI.
"""

from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# The public surface the original integrated_cost_optimizer exposed.
LEGACY_API = [
    "IntegratedParams",
    "REQUIRED_COLS",
    "ROW_COUNTRIES",
    "_norm_cols",
    "_validate_weights",
    "read_sites",
    "clean_sites",
    "build_weekly_demand",
    "build_weekly_row_demand",
    "compute_weekly_cost",
    "compute_inventory_bounds",
    "solve_plan_integrated",
    "_build_plan_df",
    "batches_needed",
    "split_good_into_batches",
    "export_excel",
    "print_summary",
    "_parse_week_list",
    "main",
]


# ---------------------------------------------------------------------------
# Legacy module API
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", LEGACY_API)
def test_legacy_api_still_exported(name):
    import integrated_cost_optimizer as legacy
    assert hasattr(legacy, name), f"integrated_cost_optimizer.{name} disappeared"


def test_legacy_onboarding_module_importable():
    import onboarding_recommendation
    assert onboarding_recommendation is not None


def test_legacy_solver_signature_accepts_original_positional_args():
    """The original six-positional-argument call must still work."""
    from integrated_cost_optimizer import (
        IntegratedParams,
        solve_plan_integrated,
    )
    params = IntegratedParams(horizon_weeks=8, w_capacity=0.0)
    demand = [0] * 9
    demand[4] = 10
    row_demand = [0] * 9
    plan_df, summary = solve_plan_integrated(
        demand, [], [], row_demand, params.row_cap, params
    )
    assert len(plan_df) == 8
    assert "total_composite_cost" in summary


def test_legacy_plan_columns_unchanged():
    """Every original Weekly_Plan column must still be produced."""
    from integrated_cost_optimizer import IntegratedParams, solve_plan_integrated
    params = IntegratedParams(horizon_weeks=8, w_capacity=0.0)
    demand = [0] * 9
    demand[4] = 10
    plan_df, _ = solve_plan_integrated(demand, [], [], [0] * 9, 2, params)
    expected = [
        "Week", "Week_Type", "Demand_Due", "Good_Production", "Batch_Count",
        "Batch1_Produced", "Batch2_Produced", "Batch3_Produced",
        "Produced_Total", "Testing_Discard", "Overtime_Used",
        "Net_Inventory_End", "Early_Units_Held", "Late_Units_Backlog",
        "ROW_Demand_Due", "ROW_Fulfilled", "ROW_Inventory",
        "Penalty_Cost_USD", "Overtime_Cost_USD",
        "Capacity_Utilization_Cost_USD", "Composite_Cost_USD",
        "Cumulative_Composite_Cost_USD",
    ]
    for column in expected:
        assert column in plan_df.columns, f"lost column {column}"


def test_legacy_summary_keys_unchanged():
    from integrated_cost_optimizer import IntegratedParams, solve_plan_integrated
    params = IntegratedParams(horizon_weeks=8, w_capacity=0.0)
    demand = [0] * 9
    demand[4] = 10
    _plan, summary = solve_plan_integrated(demand, [], [], [0] * 9, 2, params)
    for key in ("total_composite_cost", "total_penalty_cost",
                "total_overtime_cost", "total_capacity_cost", "overtime_weeks",
                "w_penalty", "w_overtime", "w_capacity"):
        assert key in summary


# ---------------------------------------------------------------------------
# Minimal legacy-format input
# ---------------------------------------------------------------------------

MINIMAL_CSV = (
    "Site_ID,Active,Next_Demand_Week,Interval_Weeks\n"   # no Country column
    "S1,Y,3,7\n"
    "S2,Y,6,8\n"
)


def test_minimal_input_without_country_runs():
    """Country is optional; its absence must not break the pipeline."""
    from domain.demand import build_weekly_demand, clean_sites
    from domain.params import IntegratedParams
    from io_adapters.sites_reader import ExcelSitesReader

    params = IntegratedParams(horizon_weeks=20, w_capacity=0.0)
    raw = ExcelSitesReader().read(MINIMAL_CSV.encode(), is_csv=True)
    active, issues = clean_sites(raw, params)
    assert len(active) == 2
    assert issues.empty
    assert sum(build_weekly_demand(active, params)) > 0


def test_minimal_input_through_optimizer_service():
    from domain.params import IntegratedParams, SupplierParams
    from io_adapters.sites_reader import ExcelSitesReader
    from io_adapters.workbook_exporter import WorkbookExporter
    from services.dtos import OptimizeRequest
    from services.optimizer_service import OptimizerService

    service = OptimizerService(ExcelSitesReader(), WorkbookExporter())
    result = service.run(OptimizeRequest(
        file_bytes=MINIMAL_CSV.encode(), filename="sites.csv", sheet="Sites",
        params=IntegratedParams(horizon_weeks=20, w_capacity=0.0),
        supplier_params=SupplierParams(
            curium_quarterly_quota_mci=0.0, bwxt_quarterly_quota_mci=0.0
        ),
    ))
    assert len(result.plan_df) == 20
    assert result.xlsx_bytes


def test_extra_unknown_columns_pass_through():
    from domain.demand import clean_sites
    from domain.params import IntegratedParams
    from io_adapters.sites_reader import ExcelSitesReader
    csv = (
        "Site_ID,Active,Next_Demand_Week,Interval_Weeks,Country,Notes,Delivery_Day\n"
        "S1,Y,3,7,usa,keep,Mon\n"
    )
    raw = ExcelSitesReader().read(csv.encode(), is_csv=True)
    assert "notes" in raw.columns and "delivery_day" in raw.columns
    active, issues = clean_sites(raw, IntegratedParams())
    assert len(active) == 1
    assert issues.empty


def test_inactive_rows_still_excluded():
    from domain.demand import clean_sites
    from domain.params import IntegratedParams
    from io_adapters.sites_reader import ExcelSitesReader
    csv = (
        "Site_ID,Active,Next_Demand_Week,Interval_Weeks,Country\n"
        "S1,Y,3,7,usa\nS2,N,6,8,usa\n"
    )
    raw = ExcelSitesReader().read(csv.encode(), is_csv=True)
    active, _ = clean_sites(raw, IntegratedParams())
    assert set(active["site_id"]) == {"S1"}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def test_cli_runs_and_writes_legacy_four_sheets(tmp_path, sites_csv):
    output = tmp_path / "cli_out.xlsx"
    completed = subprocess.run(
        [sys.executable, "integrated_cost_optimizer.py",
         "--input", str(sites_csv), "--output", str(output)],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=600,
    )
    assert completed.returncode == 0, completed.stderr
    assert output.exists()
    sheets = set(pd.ExcelFile(output).sheet_names)
    assert sheets == {"Weekly_Plan", "Sites_Clean", "Input_Issues", "Model_Params"}


def test_cli_print_summary_flag(tmp_path, sites_csv):
    output = tmp_path / "cli_out2.xlsx"
    completed = subprocess.run(
        [sys.executable, "integrated_cost_optimizer.py",
         "--input", str(sites_csv), "--output", str(output), "--print-summary"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=600,
    )
    assert completed.returncode == 0, completed.stderr
    assert "Total composite cost" in completed.stdout
