"""Tests for the Master Planner parser (io_adapters.master_planner_parser).

Uses a synthetic fixture workbook mirroring the real sheet's layout so the tests
do not depend on the (large, changing) production workbook.
"""

import io

import openpyxl
import pytest

from io_adapters.master_planner_parser import (
    MasterPlannerParser,
    assign_stable_id,
    parse_master_planner,
)


def _fixture_workbook() -> bytes:
    """Build a small Master Planner-shaped workbook.

    Row 1 blank-ish, row 2 headers, rows 3+ data. Customer cells hold 1 to mark
    a scheduled generator.
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Schedule"
    ws.append([None] * 12)  # row 1 (spacer, like the real sheet)
    ws.append([
        "Weeks #", "MFG Date \n(Holidays)", "Calibration date", "Problem?",
        "Month", "FY", "US Demand", "RoW Demand", "Total Commercial", "QC GEN",
        "US\nSTAB",                                  # non-customer marker
        "00449    Acme Cardiology, Fresno, CA",      # numbered customer
        "Apex Cardiology, Jackson, TN (7 weeks)",    # unnumbered customer
    ])
    from datetime import datetime
    rows = [
        (1, datetime(2026, 1, 5), datetime(2026, 1, 9), None, "Jan", 2026, 2, 0, 2, 1, None, 1, 1),
        (2, datetime(2026, 1, 12), datetime(2026, 1, 16), None, "Jan", 2026, 1, 0, 1, 0, None, 1, None),
        (3, datetime(2026, 1, 19), datetime(2026, 1, 23), None, "Jan", 2026, 1, 0, 1, 1, None, None, 1),
    ]
    for r in rows:
        ws.append(list(r))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.fixture
def fixture_bytes():
    return _fixture_workbook()


# ---------------------------------------------------------------------------
# Stable ID assignment
# ---------------------------------------------------------------------------

def test_stable_id_is_deterministic():
    h = "Apex Cardiology, Jackson, TN (7 weeks)"
    assert assign_stable_id(h) == assign_stable_id(h)


def test_stable_id_format():
    sid = assign_stable_id("Some Customer, TX (7)")
    assert sid.startswith("RF-") and len(sid) == 11


def test_stable_id_ignores_punctuation_and_case():
    a = assign_stable_id("Apex Cardiology, Jackson, TN (7)")
    b = assign_stable_id("apex cardiology  jackson tn 7")
    assert a == b


def test_stable_id_differs_for_different_customers():
    assert assign_stable_id("Customer A") != assign_stable_id("Customer B")


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def test_parses_weeks_and_aggregates(fixture_bytes):
    d = parse_master_planner(fixture_bytes, horizon_weeks=52)
    assert d.weekly_commercial[1] == 2
    assert d.weekly_commercial[2] == 1
    assert d.weekly_qc[1] == 1
    assert d.weekly_qc[3] == 1


def test_planned_production_adds_qc(fixture_bytes):
    d = parse_master_planner(fixture_bytes, horizon_weeks=52)
    # week 1: commercial 2 + QC 1 = 3
    assert d.weekly_planned_production[1] == 3
    assert d.weekly_planned_production[2] == 1   # QC 0


def test_dates_extracted(fixture_bytes):
    from datetime import date
    d = parse_master_planner(fixture_bytes, horizon_weeks=52)
    assert d.mfg_dates[1] == date(2026, 1, 5)
    assert d.cal_dates[1] == date(2026, 1, 9)


def test_numbered_customer_matched_by_leading_number(fixture_bytes):
    d = parse_master_planner(fixture_bytes, horizon_weeks=52)
    assert "00449" in d.customer_schedule
    assert d.customer_schedule["00449"][1] == 1
    assert d.customer_schedule["00449"][2] == 1
    assert d.customer_schedule["00449"][3] == 0


def test_unnumbered_customer_gets_generated_id(fixture_bytes):
    d = parse_master_planner(fixture_bytes, horizon_weeks=52)
    gen = [a for a in d.assigned_ids
           if a.customer_name.startswith("Apex Cardiology")]
    assert len(gen) == 1
    sid = gen[0].generated_id
    assert sid in d.customer_schedule
    assert d.customer_schedule[sid][1] == 1
    assert d.customer_schedule[sid][3] == 1


def test_non_customer_columns_ignored(fixture_bytes):
    d = parse_master_planner(fixture_bytes, horizon_weeks=52)
    assert any("STAB" in c for c in d.ignored_columns)
    # "Problem?" is an aggregate header, not a customer
    assert not any(a.customer_name == "Problem?" for a in d.assigned_ids)


def test_customer_marks_reconcile_with_total_commercial(fixture_bytes):
    """The count of schedule marks must equal Total Commercial per week."""
    d = parse_master_planner(fixture_bytes, horizon_weeks=52)
    for wk in (1, 2, 3):
        marks = sum(sched[wk] for sched in d.customer_schedule.values())
        assert marks == d.weekly_commercial[wk]


def test_weeks_outside_horizon_excluded(fixture_bytes):
    d = parse_master_planner(fixture_bytes, horizon_weeks=2)
    assert 3 not in d.week_to_row
    assert d.rows_excluded >= 1


def test_missing_sheet_raises(fixture_bytes):
    with pytest.raises(ValueError, match="not found"):
        parse_master_planner(fixture_bytes, sheet="NoSuchSheet")


def test_parser_class_and_function_agree(fixture_bytes):
    a = MasterPlannerParser().parse(fixture_bytes, "Schedule", 52, None)
    b = parse_master_planner(fixture_bytes, "Schedule", 52, None)
    assert a.weekly_planned_production == b.weekly_planned_production
    assert a.customer_schedule == b.customer_schedule
