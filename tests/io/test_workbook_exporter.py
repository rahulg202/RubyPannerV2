"""Tests for the WorkbookExporter adapter (io_adapters.workbook_exporter)."""

import io

import pandas as pd
import pytest

from domain.params import IntegratedParams
from io_adapters.workbook_exporter import WorkbookExporter, export_excel
from services.ports import ResultExporterPort


@pytest.fixture
def sample_run():
    params = IntegratedParams()
    plan_df = pd.DataFrame(
        {
            "Week": [1, 2],
            "Good_Production": [15, 30],
            "Net_Inventory_End": [0, 0],
        }
    )
    sites_df = pd.DataFrame(
        {"site_id": ["S1"], "next_demand_week": [1], "interval_weeks": [7]}
    )
    issues_df = pd.DataFrame(columns=["row_index", "site_id", "issue"])
    summary = {
        "total_composite_cost": 1000.0,
        "total_penalty_cost": 500.0,
        "total_overtime_cost": 300.0,
        "total_capacity_cost": 200.0,
        "overtime_weeks": 1,
        "w_penalty": 1.0,
        "w_overtime": 1.0,
        "w_capacity": 1.0,
    }
    return params, plan_df, sites_df, issues_df, summary


EXPECTED_SHEETS = {"Weekly_Plan", "Sites_Clean", "Input_Issues", "Model_Params"}


def test_exporter_satisfies_port():
    assert isinstance(WorkbookExporter(), ResultExporterPort)


def test_export_returns_valid_workbook_bytes(sample_run):
    params, plan_df, sites_df, issues_df, summary = sample_run
    data = WorkbookExporter().export(plan_df, sites_df, issues_df, params, summary)
    assert isinstance(data, bytes) and len(data) > 0
    xl = pd.ExcelFile(io.BytesIO(data))
    assert set(xl.sheet_names) == EXPECTED_SHEETS


def test_weekly_plan_roundtrips(sample_run):
    params, plan_df, sites_df, issues_df, summary = sample_run
    data = WorkbookExporter().export(plan_df, sites_df, issues_df, params, summary)
    back = pd.read_excel(io.BytesIO(data), sheet_name="Weekly_Plan")
    assert list(back["Week"]) == [1, 2]
    assert list(back["Good_Production"]) == [15, 30]


def test_model_params_sheet_contains_key_rows(sample_run):
    params, plan_df, sites_df, issues_df, summary = sample_run
    data = WorkbookExporter().export(plan_df, sites_df, issues_df, params, summary)
    mp = pd.read_excel(io.BytesIO(data), sheet_name="Model_Params")
    names = set(mp["Parameter"])
    assert {"horizon_weeks", "penalty_rate", "row_cap", "overtime_weeks"} <= names


def test_export_excel_path_matches_bytes(sample_run, tmp_path):
    params, plan_df, sites_df, issues_df, summary = sample_run
    path = tmp_path / "out.xlsx"
    export_excel(str(path), plan_df, sites_df, issues_df, params, summary)
    from_path = pd.ExcelFile(path).sheet_names
    from_bytes = pd.ExcelFile(
        io.BytesIO(WorkbookExporter().export(plan_df, sites_df, issues_df, params, summary))
    ).sheet_names
    assert set(from_path) == set(from_bytes) == EXPECTED_SHEETS
