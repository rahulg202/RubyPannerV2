"""Service-layer tests using in-memory fake adapters.

These verify orchestration and DTO shape without touching the filesystem, which
is the point of depending on port protocols rather than concrete adapters.
"""

from __future__ import annotations

import io
from datetime import date

import pandas as pd
import pytest

from domain.comparison import BaselineResult
from domain.errors import InfeasiblePlanError, ValidationError
from domain.onboarding import NewCustomer
from domain.params import IntegratedParams, SupplierParams
from services.comparison_service import ComparisonService
from services.dtos import (
    ComparisonRequest,
    OnboardingRequest,
    OptimizationResult,
    OptimizeRequest,
)
from services.onboarding_service import OnboardingService
from services.optimizer_service import OptimizerService

PARAMS = IntegratedParams(horizon_weeks=20, w_capacity=0.0)
SUPPLIER = SupplierParams()


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeSitesReader:
    """Returns a canned sites frame; records how it was called."""

    def __init__(self, df: pd.DataFrame | None = None):
        self.df = df if df is not None else pd.DataFrame({
            "site_id": ["S1", "S2", "S3"],
            "active": ["Y", "Y", "Y"],
            "next_demand_week": [2, 5, 9],
            "interval_weeks": [8, 9, 10],
            "country": ["usa", "usa", "denmark"],
        })
        self.calls: list[tuple] = []

    def read(self, source, sheet="Sites", *, is_csv=False):
        self.calls.append((sheet, is_csv))
        return self.df.copy()


class FakeExporter:
    """Captures export arguments and returns sentinel bytes."""

    def __init__(self):
        self.called_with = None
        self.extras = None

    def export(self, plan_df, sites_df, issues_df, params, summary, **extras):
        self.called_with = (plan_df, sites_df, issues_df, params, summary)
        self.extras = extras
        return b"FAKE_XLSX"


class FakeMasterPlannerData:
    def __init__(self, planned, schedule=None):
        self.weekly_planned_production = planned
        self.weekly_commercial = planned
        self.weekly_qc = [0] * len(planned)
        self.customer_schedule = schedule or {}
        self.assigned_ids = []
        self.ignored_columns = []
        self.issues = []
        self.rows_excluded = 0
        self.week_to_row = {}
        self.mfg_dates = [None] * len(planned)
        self.cal_dates = [None] * len(planned)


class FakeMasterPlannerReader:
    def __init__(self, planned=None, schedule=None, raise_exc: Exception | None = None):
        T = PARAMS.horizon_weeks
        self.planned = planned if planned is not None else [0] * (T + 1)
        self.schedule = schedule or {}
        self.raise_exc = raise_exc
        self.calls = 0

    def parse(self, source, sheet="Schedule", horizon_weeks=52, year=None):
        self.calls += 1
        if self.raise_exc:
            raise self.raise_exc
        return FakeMasterPlannerData(self.planned, self.schedule)


class FakeInputFileWriter:
    def __init__(self):
        self.called_with = None

    def write(self, existing_file_bytes, existing_filename, sheet,
              new_customers, selected_weeks):
        self.called_with = (existing_filename, sheet, list(new_customers),
                            dict(selected_weeks))
        return b"FAKE_INPUT_FILE"


def _optimize_request(**over):
    base = dict(
        file_bytes=b"x", filename="sites.csv", sheet="Sites",
        params=PARAMS, supplier_params=SUPPLIER,
    )
    base.update(over)
    return OptimizeRequest(**base)


# ---------------------------------------------------------------------------
# OptimizerService
# ---------------------------------------------------------------------------

def test_optimizer_service_happy_path():
    reader, exporter = FakeSitesReader(), FakeExporter()
    svc = OptimizerService(reader, exporter)
    result = svc.run(_optimize_request())

    assert isinstance(result, OptimizationResult)
    assert len(result.plan_df) == PARAMS.horizon_weeks
    assert result.summary["total_composite_cost"] >= 0
    assert result.xlsx_bytes == b"FAKE_XLSX"
    assert not result.active_df.empty


def test_optimizer_service_passes_csv_flag():
    reader = FakeSitesReader()
    OptimizerService(reader).run(_optimize_request(filename="sites.csv"))
    assert reader.calls[0][1] is True
    reader2 = FakeSitesReader()
    OptimizerService(reader2).run(_optimize_request(filename="sites.xlsx"))
    assert reader2.calls[0][1] is False


def test_optimizer_service_includes_supplier_columns():
    svc = OptimizerService(FakeSitesReader())
    result = svc.run(_optimize_request())
    for col in ["Curium_Good", "BWXT_Good", "Supplier_Label", "Total_Sr82_mCi"]:
        assert col in result.plan_df.columns
    assert result.quota_status, "quota status should be populated"


def test_optimizer_service_produces_assignments_reconciling_with_plan():
    svc = OptimizerService(FakeSitesReader())
    result = svc.run(_optimize_request())
    per_week = {}
    for r in result.assignments:
        per_week[r.planned_week] = per_week.get(r.planned_week, 0) + 1
    y = result.plan_df.set_index("Week")["Good_Production"].to_dict()
    for wk, count in per_week.items():
        assert count == y[wk]
    assert result.change_summary["total"] == len(result.assignments)


def test_optimizer_service_week_dates_when_reference_set():
    svc = OptimizerService(FakeSitesReader())
    result = svc.run(_optimize_request(
        reference_week_date=date(2026, 1, 5), calibration_offset_days=4
    ))
    assert len(result.week_dates) == PARAMS.horizon_weeks
    wk, mfg, cal = result.week_dates[0]
    assert (wk, mfg, cal) == (1, date(2026, 1, 5), date(2026, 1, 9))


def test_optimizer_service_no_week_dates_without_reference():
    result = OptimizerService(FakeSitesReader()).run(_optimize_request())
    assert result.week_dates == []


def test_optimizer_service_empty_sites_raises_infeasible():
    empty = pd.DataFrame({
        "site_id": ["S1"], "active": ["N"],
        "next_demand_week": [2], "interval_weeks": [8], "country": ["usa"],
    })
    svc = OptimizerService(FakeSitesReader(empty))
    with pytest.raises(InfeasiblePlanError):
        svc.run(_optimize_request())


def test_optimizer_service_without_exporter_returns_no_bytes():
    result = OptimizerService(FakeSitesReader()).run(_optimize_request())
    assert result.xlsx_bytes is None


def test_optimizer_service_master_planner_comparison_applied():
    mp_reader = FakeMasterPlannerReader(schedule={"S1": [0] * 21})
    svc = OptimizerService(FakeSitesReader(), None, mp_reader)
    result = svc.run(_optimize_request(master_planner_bytes=b"mp"))
    assert mp_reader.calls == 1
    # S1 present in the master planner -> not flagged new; S2/S3 absent -> new
    by_site = {r.site_id: r for r in result.assignments}
    assert by_site["S2"].is_new_customer is True


def test_optimizer_service_master_planner_failure_is_warning_not_error():
    mp_reader = FakeMasterPlannerReader(raise_exc=ValueError("bad sheet"))
    svc = OptimizerService(FakeSitesReader(), None, mp_reader)
    result = svc.run(_optimize_request(master_planner_bytes=b"mp"))
    assert any("manual plan" in w.lower() for w in result.warnings)
    assert result.plan_df is not None      # run still succeeded


def test_optimizer_service_without_manual_plan_reports_not_compared():
    """Changed weeks must not be invented from due dates."""
    result = OptimizerService(FakeSitesReader()).run(_optimize_request())
    assert result.change_summary["compared"] is False
    assert result.change_summary["early"] == 0
    assert result.change_summary["late"] == 0
    assert any("No manual plan supplied" in w for w in result.warnings)


def test_optimizer_service_with_manual_plan_compares_against_it():
    schedule = {sid: [0] * 21 for sid in ("S1", "S2", "S3")}
    for sid in schedule:
        schedule[sid][1] = 1          # manual plan produced each in week 1
    mp_reader = FakeMasterPlannerReader(schedule=schedule)
    svc = OptimizerService(FakeSitesReader(), None, mp_reader)
    result = svc.run(_optimize_request(master_planner_bytes=b"mp"))
    assert result.change_summary["compared"] is True
    # Every compared generator's shift is measured against manual week 1
    compared = [r for r in result.assignments if r.week_shift is not None]
    assert compared
    for record in compared:
        assert record.manual_week == 1
        assert record.week_shift == record.planned_week - 1


# ---------------------------------------------------------------------------
# ComparisonService
# ---------------------------------------------------------------------------

def _comparison_request(planned_total=None, **over):
    T = PARAMS.horizon_weeks
    demand = [0] * (T + 1)
    demand[3] = 10
    demand[8] = 10
    opt_plan = pd.DataFrame({
        "Week": list(range(1, T + 1)),
        "Good_Production": [0] * T,
    })
    base = dict(
        master_planner_bytes=b"mp", master_planner_sheet="Schedule",
        optimized_summary={
            "total_penalty_cost": 100.0, "total_overtime_cost": 0.0,
            "total_capacity_cost": 0.0, "total_composite_cost": 100.0,
            "overtime_weeks": 0,
        },
        optimized_plan_df=opt_plan,
        demand=tuple(demand),
        params=PARAMS,
    )
    base.update(over)
    return ComparisonRequest(**base)


def test_comparison_service_returns_components():
    T = PARAMS.horizon_weeks
    planned = [0] * (T + 1)
    planned[3] = 10
    planned[8] = 10
    svc = ComparisonService(FakeMasterPlannerReader(planned))
    result = svc.run(_comparison_request())
    labels = {c["Component"] for c in result.components}
    assert labels == {"Penalty", "Overtime", "Capacity Utilization", "Total Composite"}
    assert isinstance(result.baseline, BaselineResult)


def test_comparison_service_weekly_frame_shape():
    svc = ComparisonService(FakeMasterPlannerReader())
    result = svc.run(_comparison_request())
    assert len(result.weekly_comparison) == PARAMS.horizon_weeks
    assert list(result.weekly_comparison.columns) == [
        "Week", "Manual_Production", "Optimized_Production", "Difference"
    ]


def test_comparison_service_warns_on_total_mismatch():
    # Manual plan produces nothing while demand is 20 -> mismatch warning
    svc = ComparisonService(FakeMasterPlannerReader())
    result = svc.run(_comparison_request())
    assert any("does not equal total" in w for w in result.warnings)


def test_comparison_service_warns_on_capacity_violation():
    T = PARAMS.horizon_weeks
    planned = [0] * (T + 1)
    planned[1] = 999          # far beyond weekly capacity
    svc = ComparisonService(FakeMasterPlannerReader(planned))
    result = svc.run(_comparison_request())
    assert any("exceeds weekly capacity" in w for w in result.warnings)


def test_comparison_service_overtime_counts_reported():
    svc = ComparisonService(FakeMasterPlannerReader())
    result = svc.run(_comparison_request())
    assert result.overtime_optimized == 0
    assert isinstance(result.overtime_baseline, int)


# ---------------------------------------------------------------------------
# OnboardingService
# ---------------------------------------------------------------------------

def test_onboarding_service_estimate():
    svc = OnboardingService(FakeSitesReader())
    est = svc.estimate([NewCustomer("N1", earliest_week=1, latest_week=4)])
    assert est["combinations"] == 4
    assert est["exhaustive"] is True


def _onboarding_request(**over):
    base = dict(
        file_bytes=b"x", filename="sites.csv", sheet="Sites",
        new_customers=(NewCustomer("N1", earliest_week=2, latest_week=4,
                                   interval_weeks=9, country="usa"),),
        params=PARAMS, supplier_params=SUPPLIER,
    )
    base.update(over)
    return OnboardingRequest(**base)


def test_onboarding_service_returns_rankings():
    svc = OnboardingService(FakeSitesReader())
    result = svc.run(_onboarding_request())
    assert result.search_space == 3
    assert result.used_heuristic is False
    assert set(result.rankings) == {"penalty", "overtime", "capacity"}
    assert result.rankings["penalty"], "expected at least one ranked option"


def test_onboarding_service_progress_callback():
    calls = []
    svc = OnboardingService(FakeSitesReader())
    svc.run(_onboarding_request(), progress=lambda f, m: calls.append(m))
    assert calls


def test_onboarding_service_invalid_customer_raises():
    svc = OnboardingService(FakeSitesReader())
    bad = (NewCustomer("N1", earliest_week=9, latest_week=2),)
    with pytest.raises(ValidationError):
        svc.run(_onboarding_request(new_customers=bad))


def test_onboarding_service_generates_input_file():
    writer = FakeInputFileWriter()
    svc = OnboardingService(FakeSitesReader(), writer)
    customers = [NewCustomer("SN-1", interval_weeks=7, country="usa")]
    out = svc.generate_input_file(b"x", "sites.csv", "Sites", customers, {"SN-1": 4})
    assert out == b"FAKE_INPUT_FILE"
    assert writer.called_with[3] == {"SN-1": 4}


def test_onboarding_service_without_writer_raises():
    svc = OnboardingService(FakeSitesReader())
    with pytest.raises(RuntimeError, match="No input file writer"):
        svc.generate_input_file(b"x", "sites.csv", "Sites", [], {})


# ---------------------------------------------------------------------------
# Real adapters satisfy the ports the services depend on
# ---------------------------------------------------------------------------

def test_real_adapters_are_port_compatible():
    from io_adapters.input_file_writer import InputFileWriter
    from io_adapters.master_planner_parser import MasterPlannerParser
    from io_adapters.sites_reader import ExcelSitesReader
    from io_adapters.workbook_exporter import WorkbookExporter
    from services.ports import (
        InputFileWriterPort,
        MasterPlannerReaderPort,
        ResultExporterPort,
        SitesReaderPort,
    )
    assert isinstance(ExcelSitesReader(), SitesReaderPort)
    assert isinstance(WorkbookExporter(), ResultExporterPort)
    assert isinstance(MasterPlannerParser(), MasterPlannerReaderPort)
    assert isinstance(InputFileWriter(), InputFileWriterPort)


# ---------------------------------------------------------------------------
# Master Planner match-rate guard
# ---------------------------------------------------------------------------

def test_low_match_rate_produces_warning():
    """Mismatched Site_ID conventions must be flagged, not silently reported."""
    mp_reader = FakeMasterPlannerReader(schedule={"00449": [0] * 21})  # no overlap
    svc = OptimizerService(FakeSitesReader(), None, mp_reader)
    result = svc.run(_optimize_request(master_planner_bytes=b"mp"))
    assert any("Site_ID conventions" in w for w in result.warnings)


def test_full_match_rate_produces_no_warning():
    schedule = {sid: [0] * 21 for sid in ("S1", "S2", "S3")}
    mp_reader = FakeMasterPlannerReader(schedule=schedule)
    svc = OptimizerService(FakeSitesReader(), None, mp_reader)
    result = svc.run(_optimize_request(master_planner_bytes=b"mp"))
    assert not any("Site_ID conventions" in w for w in result.warnings)


def test_optimizer_service_passes_unified_export_sections():
    """The service must hand the exporter every section it produced."""
    exporter = FakeExporter()
    svc = OptimizerService(FakeSitesReader(), exporter)
    svc.run(_optimize_request(reference_week_date=date(2026, 1, 5)))
    extras = exporter.extras
    assert extras["supplier_params"] is SUPPLIER
    assert extras["quota_status"], "quota status should be forwarded"
    assert extras["assignments"], "assignments should be forwarded"
    assert extras["week_dates"], "week dates should be forwarded"
    assert extras["calibration_offset_days"] == 4


# ---------------------------------------------------------------------------
# Layering: services orchestrate through ports, never concrete adapters
# ---------------------------------------------------------------------------

def test_services_do_not_import_adapters():
    import pathlib
    import re

    offenders = [
        str(path)
        for path in pathlib.Path("services").glob("*.py")
        if re.search(r"^\s*(from|import)\s+io_adapters", path.read_text(), re.M)
    ]
    assert offenders == [], offenders
