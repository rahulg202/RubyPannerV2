"""End-to-end integration tests across the whole stack.

These wire the real adapters into the real services — no fakes — and assert that
every feature produces mutually consistent output in one result and one export.
Fixtures are synthesised in-test so the suite never depends on the large
production workbooks.
"""

from __future__ import annotations

import io
from datetime import date, datetime, timedelta

import openpyxl
import pandas as pd
import pytest

from domain.demand import build_weekly_demand, clean_sites
from domain.params import IntegratedParams, SupplierParams
from io_adapters.input_file_writer import InputFileWriter
from io_adapters.master_planner_parser import MasterPlannerParser
from io_adapters.sites_reader import ExcelSitesReader
from io_adapters.workbook_exporter import (
    ExportBundle,
    WorkbookExporter,
    write_unified_workbook,
)
from services.comparison_service import ComparisonService
from services.dtos import ComparisonRequest, OptimizeRequest
from services.optimizer_service import OptimizerService

HORIZON = 26
REF_DATE = date(2026, 1, 5)
CAL_OFFSET = 4

PARAMS = IntegratedParams(horizon_weeks=HORIZON, w_capacity=0.0)

# The fixture below is deliberately small (a handful of sites), so quarterly Sr-82
# orders are far below the production 10,000 mCi quota. Quotas are set to 0 here
# so the integration assertions exercise allocation and cost logic rather than a
# fixture-driven shortfall penalty. Quota shortfall behaviour has its own
# dedicated tests in tests/domain/test_quota.py.
SUPPLIER = SupplierParams(
    curium_quarterly_quota_mci=0.0,
    bwxt_quarterly_quota_mci=0.0,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SITE_SPECS = [
    # (site_id, next_demand_week, interval, country)
    ("00449", 2, 7, "usa"),
    ("00438", 4, 8, "usa"),
    ("00411", 6, 9, "usa"),
    ("1401", 3, 7, "denmark"),   # EU-restricted
    ("1405", 9, 10, "uk"),       # EU-restricted
]


@pytest.fixture
def sites_csv() -> bytes:
    lines = ["Site_ID,Active,Next_Demand_Week,Interval_Weeks,Country"]
    for sid, ndw, interval, country in SITE_SPECS:
        lines.append(f"{sid},Y,{ndw},{interval},{country}")
    return ("\n".join(lines) + "\n").encode()


@pytest.fixture
def master_planner_xlsx() -> bytes:
    """A Master Planner-shaped workbook matching the site IDs above."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Schedule"
    ws.append([None] * 12)
    header = [
        "Weeks #", "MFG Date \n(Holidays)", "Calibration date", "Problem?",
        "Month", "FY", "US Demand", "RoW Demand", "Total Commercial", "QC GEN",
        "US\nSTAB",
    ]
    customer_ids = [sid for sid, _n, _i, _c in SITE_SPECS]
    header += [f"{sid}    Customer {sid}" for sid in customer_ids]
    ws.append(header)

    # A plausible manual plan: each site served in its own due weeks.
    demand_by_week: dict[int, list[str]] = {}
    for sid, ndw, interval, _c in SITE_SPECS:
        week = ndw
        while week <= HORIZON:
            demand_by_week.setdefault(week, []).append(sid)
            week += interval

    for week in range(1, HORIZON + 1):
        served = demand_by_week.get(week, [])
        mfg = datetime(2026, 1, 5) + timedelta(days=7 * (week - 1))
        cal = mfg + timedelta(days=CAL_OFFSET)
        row = [
            week, mfg, cal, None, "Jan", 2026,
            len(served), 0, len(served), 1 if served else 0, None,
        ]
        row += [1 if sid in served else None for sid in customer_ids]
        ws.append(row)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.fixture
def services():
    sites_reader = ExcelSitesReader()
    mp_reader = MasterPlannerParser()
    return {
        "optimizer": OptimizerService(sites_reader, WorkbookExporter(), mp_reader),
        "comparison": ComparisonService(mp_reader),
        "sites_reader": sites_reader,
        "mp_reader": mp_reader,
    }


def _optimize(services, sites_csv, mp_bytes=None) -> object:
    return services["optimizer"].run(OptimizeRequest(
        file_bytes=sites_csv, filename="sites.csv", sheet="Sites",
        params=PARAMS, supplier_params=SUPPLIER,
        reference_week_date=REF_DATE, calibration_offset_days=CAL_OFFSET,
        master_planner_bytes=mp_bytes,
    ))


# ---------------------------------------------------------------------------
# Full pipeline consistency
# ---------------------------------------------------------------------------

def test_pipeline_produces_all_sections(services, sites_csv, master_planner_xlsx):
    result = _optimize(services, sites_csv, master_planner_xlsx)
    assert len(result.plan_df) == HORIZON
    assert result.quota_status, "supplier quota must be evaluated"
    assert result.assignments, "deliveries must be assigned to customers"
    assert len(result.week_dates) == HORIZON
    assert result.xlsx_bytes


def test_demand_equals_assignment_count(services, sites_csv):
    result = _optimize(services, sites_csv)
    demand = build_weekly_demand(result.active_df, PARAMS)
    assert len(result.assignments) == sum(demand)
    assert result.change_summary["total"] == sum(demand)


def test_assignments_reconcile_with_plan_per_week(services, sites_csv):
    result = _optimize(services, sites_csv)
    produced = result.plan_df.set_index("Week")["Good_Production"].to_dict()
    counted: dict[int, int] = {}
    for record in result.assignments:
        counted[record.planned_week] = counted.get(record.planned_week, 0) + 1
    for week, qty in produced.items():
        assert counted.get(week, 0) == qty


def test_supplier_allocation_sums_to_production(services, sites_csv):
    result = _optimize(services, sites_csv)
    total = result.plan_df["Curium_Good"] + result.plan_df["BWXT_Good"]
    assert (total == result.plan_df["Good_Production"]).all()


def test_eu_demand_always_covered_by_curium(services, sites_csv):
    result = _optimize(services, sites_csv)
    covered = result.plan_df["Curium_Good"] >= result.plan_df["EU_Restricted_Demand"]
    assert covered.all()


def test_row_cap_respected_for_eu_demand(services, sites_csv):
    """EU demand above the QC cap must be pre-built into inventory."""
    result = _optimize(services, sites_csv)
    plan = result.plan_df.set_index("Week")
    for week in range(2, HORIZON + 1):
        excess = int(plan.loc[week, "EU_Restricted_Demand"]) - PARAMS.row_cap
        if excess > 0:
            assert int(plan.loc[week - 1, "Net_Inventory_End"]) >= excess


def test_activity_matches_formula(services, sites_csv):
    from domain.supplier_allocation import compute_activity
    result = _optimize(services, sites_csv)
    for _i, row in result.plan_df.iterrows():
        expected_c = compute_activity(
            int(row["Curium_Good"]), SUPPLIER.curium_surplus_pct, SUPPLIER
        )
        expected_b = compute_activity(
            int(row["BWXT_Good"]), SUPPLIER.bwxt_surplus_pct, SUPPLIER
        )
        assert row["Curium_Activity_mCi"] == pytest.approx(expected_c)
        assert row["BWXT_Activity_mCi"] == pytest.approx(expected_b)
        assert row["Total_Sr82_mCi"] == pytest.approx(expected_c + expected_b)


def test_quota_ordered_matches_plan_activity(services, sites_csv):
    result = _optimize(services, sites_csv)
    plan = result.plan_df.set_index("Week")
    for status in result.quota_status:
        column = ("Curium_Activity_mCi" if status.supplier == "Curium"
                  else "BWXT_Activity_mCi")
        expected = sum(float(plan.loc[w, column]) for w in status.weeks
                       if w in plan.index)
        assert status.ordered_mci == pytest.approx(expected)


def test_week_dates_consistent_with_reference(services, sites_csv):
    result = _optimize(services, sites_csv)
    for week, mfg, cal in result.week_dates:
        assert (mfg - REF_DATE).days == 7 * (week - 1)
        assert (cal - mfg).days == CAL_OFFSET


def test_three_run_weeks_follow_curium_bwxt_curium(services, sites_csv):
    result = _optimize(services, sites_csv)
    for _i, row in result.plan_df.iterrows():
        sequence = [s for s in str(row["Run_Sequence"]).split(", ") if s]
        if len(sequence) == 3:
            assert sequence == ["Curium", "BWXT", "Curium"]
        if len(sequence) >= 2:
            assert sequence[0] == "Curium"


# ---------------------------------------------------------------------------
# Master Planner comparison consistency
# ---------------------------------------------------------------------------

def test_master_planner_matches_sites_no_false_new_customers(
    services, sites_csv, master_planner_xlsx
):
    """When IDs align, no site should be misreported as new."""
    result = _optimize(services, sites_csv, master_planner_xlsx)
    assert result.change_summary["new_customers"] == 0
    assert not any("Site_ID conventions" in w for w in result.warnings)


def test_mismatched_ids_trigger_low_match_warning(services, master_planner_xlsx):
    """Different ID conventions must be flagged rather than silently reported."""
    other = (
        "Site_ID,Active,Next_Demand_Week,Interval_Weeks,Country\n"
        "ZZZ1,Y,2,7,usa\nZZZ2,Y,5,8,usa\n"
    ).encode()
    result = _optimize(services, other, master_planner_xlsx)
    assert any("Site_ID conventions" in w for w in result.warnings)


def test_comparison_savings_are_internally_consistent(
    services, sites_csv, master_planner_xlsx
):
    result = _optimize(services, sites_csv, master_planner_xlsx)
    demand = build_weekly_demand(result.active_df, PARAMS)
    comparison = services["comparison"].run(ComparisonRequest(
        master_planner_bytes=master_planner_xlsx, master_planner_sheet="Schedule",
        optimized_summary=result.summary, optimized_plan_df=result.plan_df,
        demand=tuple(demand), params=PARAMS,
    ))
    for component in comparison.components:
        assert component["Saving_Abs"] == pytest.approx(
            component["Baseline"] - component["Optimized"]
        )
    assert len(comparison.weekly_comparison) == HORIZON


def test_optimizer_never_worse_than_manual_on_weighted_objective(
    services, sites_csv, master_planner_xlsx
):
    """The solver is optimal, so its composite cost cannot exceed the manual plan's."""
    result = _optimize(services, sites_csv, master_planner_xlsx)
    demand = build_weekly_demand(result.active_df, PARAMS)
    comparison = services["comparison"].run(ComparisonRequest(
        master_planner_bytes=master_planner_xlsx, master_planner_sheet="Schedule",
        optimized_summary=result.summary, optimized_plan_df=result.plan_df,
        demand=tuple(demand), params=PARAMS,
    ))
    total = next(c for c in comparison.components
                 if c["Component"] == "Total Composite")
    assert total["Optimized"] <= total["Baseline"] + 1e-6


# ---------------------------------------------------------------------------
# Unified export
# ---------------------------------------------------------------------------

def test_unified_export_contains_every_section(
    services, sites_csv, master_planner_xlsx
):
    result = _optimize(services, sites_csv, master_planner_xlsx)
    demand = build_weekly_demand(result.active_df, PARAMS)
    comparison = services["comparison"].run(ComparisonRequest(
        master_planner_bytes=master_planner_xlsx, master_planner_sheet="Schedule",
        optimized_summary=result.summary, optimized_plan_df=result.plan_df,
        demand=tuple(demand), params=PARAMS,
    ))
    data = write_unified_workbook(ExportBundle(
        plan_df=result.plan_df, sites_df=result.active_df,
        issues_df=result.issues_df, params=PARAMS, summary=result.summary,
        supplier_params=SUPPLIER, quota_status=result.quota_status,
        assignments=result.assignments, week_dates=result.week_dates,
        comparison_components=comparison.components,
        weekly_comparison=comparison.weekly_comparison,
        reference_week_date=REF_DATE, calibration_offset_days=CAL_OFFSET,
    ))
    names = set(pd.ExcelFile(io.BytesIO(data)).sheet_names)
    assert {
        "Weekly_Plan", "Sites_Clean", "Input_Issues", "Model_Params",
        "Changed_Weeks", "Quota_Status", "Cost_Comparison", "Weekly_Comparison",
    } <= names

    plan = pd.read_excel(io.BytesIO(data), sheet_name="Weekly_Plan")
    assert list(plan.columns)[:3] == ["Week", "MFG_Date", "Cal_Date"]
    changed = pd.read_excel(io.BytesIO(data), sheet_name="Changed_Weeks")
    assert len(changed) == len(result.assignments)
