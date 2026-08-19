"""Tests for the conversion service (services.conversion_service).

Uses a fake converter so these verify orchestration — frame assembly, validation
of the generated sheet, and the delivery-count sanity check — without touching
openpyxl or the filesystem.
"""

from __future__ import annotations

import pytest

from domain.site_derivation import PlannerColumn, derive_sites
from io_adapters.master_planner_converter import DerivedSiteSet
from services.conversion_service import ConversionService
from services.dtos import ConversionRequest


def _code(header: str) -> str:
    return f"RF-{abs(hash(header)) % 10_000:04d}"


def _derived(columns: list[PlannerColumn], year: int = 2026) -> DerivedSiteSet:
    sites, warnings = derive_sites(columns, _code, horizon_weeks=52)
    return DerivedSiteSet(
        sites=sites, warnings=warnings, year=year, horizon_weeks=52,
        ignored_columns=["US STAB"],
    )


class FakeConverter:
    """Returns a canned DerivedSiteSet; records how it was called."""

    def __init__(self, derived: DerivedSiteSet):
        self.derived = derived
        self.calls: list[tuple] = []
        self.written: list[DerivedSiteSet] = []

    def convert(self, source, sheet="Schedule", horizon_weeks=52, year=None):
        self.calls.append((source, sheet, horizon_weeks, year))
        return self.derived

    def write(self, result):
        self.written.append(result)
        return b"WORKBOOK"


# Two US sites on a 7-week cadence starting weeks 1 and 3.
STANDARD_COLUMNS = [
    PlannerColumn("00449  Alpha Care, Fresno, CA (7)",
                  marks=tuple(range(1, 52, 7)), column_letter="L"),
    PlannerColumn("Beta Cardiology, Jackson, TN (7)",
                  marks=tuple(range(3, 52, 7)), column_letter="M"),
]


@pytest.fixture
def service_and_fake():
    fake = FakeConverter(_derived(STANDARD_COLUMNS))
    return ConversionService(converter=fake), fake


def test_passes_the_request_through_to_the_converter(service_and_fake):
    service, fake = service_and_fake
    service.run(ConversionRequest(
        master_planner_bytes=b"XLSX", master_planner_sheet="Plan2026",
        horizon_weeks=40, master_planner_year=2025,
    ))
    assert fake.calls == [(b"XLSX", "Plan2026", 40, 2025)]


def test_returns_the_written_workbook_bytes(service_and_fake):
    service, fake = service_and_fake
    result = service.run(ConversionRequest(master_planner_bytes=b"XLSX"))
    assert result.xlsx_bytes == b"WORKBOOK"
    assert fake.written == [fake.derived]


def test_builds_all_three_frames(service_and_fake):
    service, _ = service_and_fake
    result = service.run(ConversionRequest(master_planner_bytes=b"XLSX"))

    assert list(result.sites_df["Site_ID"]) == ["00449",
                                               _code(STANDARD_COLUMNS[1].header)]
    assert len(result.mapping_df) == 2
    assert "Note" in result.notes_df.columns


def test_reports_counts_and_the_year(service_and_fake):
    service, _ = service_and_fake
    result = service.run(ConversionRequest(master_planner_bytes=b"XLSX"))

    assert result.year == 2026
    assert result.site_count == 2
    assert result.active_count == 2
    assert result.generated_code_count == 1
    assert result.eu_restricted_count == 0


def test_counts_eu_restricted_sites():
    columns = STANDARD_COLUMNS + [
        PlannerColumn("1405  Gamma UK, London UK (7)",
                      marks=(2, 9, 16), eu_restricted=True),
    ]
    service = ConversionService(converter=FakeConverter(_derived(columns)))
    result = service.run(ConversionRequest(master_planner_bytes=b"XLSX"))
    assert result.eu_restricted_count == 1


def test_validates_the_generated_sheet(service_and_fake):
    service, _ = service_and_fake
    result = service.run(ConversionRequest(master_planner_bytes=b"XLSX"))
    # A clean conversion produces a sheet the optimizer accepts outright.
    assert result.issues_df is not None
    assert result.issues_df.empty


def test_scheduled_and_implied_deliveries_agree_for_a_clean_cadence(service_and_fake):
    service, _ = service_and_fake
    result = service.run(ConversionRequest(master_planner_bytes=b"XLSX"))

    assert result.scheduled_deliveries == 15  # 8 marks + 7 marks
    # The cadence also lands on week 52, which the fixture's marks stop short of.
    # One delivery of drift is well inside tolerance, so nothing is flagged.
    assert result.implied_deliveries == 16
    assert not any("Check the intervals" in w for w in result.warnings)


def test_wide_gap_between_manual_and_derived_deliveries_is_flagged():
    # Header claims a 7-week cadence but only two generators are scheduled, so
    # the derived cadence implies far more deliveries than the plan contains.
    columns = [
        PlannerColumn("00449  Alpha Care, Fresno, CA (7)", marks=(1, 8)),
    ]
    service = ConversionService(converter=FakeConverter(_derived(columns)))
    result = service.run(ConversionRequest(master_planner_bytes=b"XLSX"))

    assert result.scheduled_deliveries == 2
    assert result.implied_deliveries > 2
    assert any("Check the intervals" in w for w in result.warnings)


def test_inactive_sites_are_excluded_from_the_active_count():
    columns = STANDARD_COLUMNS + [
        PlannerColumn("00443  Delta Heart Center, GA (7)", marks=()),
    ]
    service = ConversionService(converter=FakeConverter(_derived(columns)))
    result = service.run(ConversionRequest(master_planner_bytes=b"XLSX"))

    assert result.site_count == 3
    assert result.active_count == 2
    assert set(result.sites_df["Active"]) == {"Y", "N"}


def test_converter_warnings_are_carried_through():
    columns = [
        PlannerColumn("00460  Alpha Med., FL (7)", marks=(1, 8)),
        PlannerColumn("00460  Duplicate, FL (7)", marks=(2, 9)),
    ]
    service = ConversionService(converter=FakeConverter(_derived(columns)))
    result = service.run(ConversionRequest(master_planner_bytes=b"XLSX"))
    assert any("more than one column" in w for w in result.warnings)


def test_horizon_defaults_to_52(service_and_fake):
    service, fake = service_and_fake
    service.run(ConversionRequest(master_planner_bytes=b"XLSX"))
    assert fake.calls[0][2] == 52
