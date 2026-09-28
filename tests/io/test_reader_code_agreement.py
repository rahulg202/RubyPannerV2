"""R4 — the two Master Planner readers must assign identical site codes.

``domain.site_derivation.deduplicate_codes`` renames a repeated account number to
``00460-2``. ``io_adapters.master_planner_parser`` used ``setdefault`` and ``+= 1``,
silently merging both columns into one entry. On a workbook carrying code
``00460`` twice:

    input file (converter):  00460 (3 deliveries), 00460-2 (3 deliveries), 00777
    manual plan (parser):    00460 -> weeks [1,3,5,7,9,11] (6 marks, merged)
    -> 00460-2 reported as a NEW CUSTOMER
    -> 00460's Nth generator compared against the wrong week

``README.md`` claimed this case was "renamed and reported rather than silently
merged". Half of it was. This is the same class of defect as the week-anchoring
bug ``HANDOFF.md`` warns about, in a rule where the sharing was never done.

See `.kiro/specs/correctness-remediation/requirements.md` R4.
"""

from __future__ import annotations

import io
from datetime import date, timedelta

import openpyxl
import pytest

from io_adapters.master_planner_converter import convert_master_planner
from io_adapters.master_planner_parser import parse_master_planner

AGGREGATE_HEADERS = [
    "Weeks #", "MFG Date \n(Holidays)", "Calibration date",
    "Total Commercial", "QC GEN",
]

# Two columns share account number 00460. A third is unique.
DUPLICATED_A = "00460    Mercy Hospital, Columbus, OH (4)"
DUPLICATED_B = "00460    Mercy Hospital NORTH, Columbus, OH (4)"
UNIQUE = "00777    Unique Clinic, Austin, TX (4)"
CUSTOMER_HEADERS = [DUPLICATED_A, DUPLICATED_B, UNIQUE]

REFERENCE_WEEK = date(2026, 1, 5)
WEEKS = 12

MARKS = {
    DUPLICATED_A: [1, 5, 9],
    DUPLICATED_B: [3, 7, 11],
    UNIQUE: [2, 6, 10],
}


@pytest.fixture(scope="module")
def workbook_bytes() -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Schedule"
    ws.append([None] * (len(AGGREGATE_HEADERS) + len(CUSTOMER_HEADERS)))  # spacer
    ws.append(AGGREGATE_HEADERS + CUSTOMER_HEADERS)

    for week in range(1, WEEKS + 1):
        mfg = REFERENCE_WEEK + timedelta(days=7 * (week - 1))
        marked = [header for header, weeks in MARKS.items() if week in weeks]
        row = [week, mfg, mfg + timedelta(days=4), len(marked), 1 if marked else 0]
        row += [1 if header in marked else None for header in CUSTOMER_HEADERS]
        ws.append(row)

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


@pytest.fixture(scope="module")
def converted(workbook_bytes):
    return convert_master_planner(
        workbook_bytes, horizon_weeks=52, reference_week_date=REFERENCE_WEEK
    )


@pytest.fixture(scope="module")
def parsed(workbook_bytes):
    return parse_master_planner(
        workbook_bytes, horizon_weeks=52, reference_week_date=REFERENCE_WEEK
    )


# ---------------------------------------------------------------------------
# AC4.1 — same workbook, same codes
# ---------------------------------------------------------------------------

def test_both_readers_produce_the_same_code_set(converted, parsed):
    converter_codes = {site.site_code for site in converted.sites}
    parser_codes = set(parsed.customer_schedule)
    assert converter_codes == parser_codes


def test_the_duplicate_is_suffixed_by_both_readers(converted, parsed):
    assert {"00460", "00460-2"} <= {s.site_code for s in converted.sites}
    assert {"00460", "00460-2"} <= set(parsed.customer_schedule)


# ---------------------------------------------------------------------------
# AC4.2 — no phantom new customers
# ---------------------------------------------------------------------------

def test_no_converted_site_is_missing_from_the_manual_plan(converted, parsed):
    """Every site in the generated input file must match a parsed column."""
    missing = {
        site.site_code for site in converted.sites if site.active
    } - set(parsed.customer_schedule)
    assert missing == set(), (
        f"{sorted(missing)} would be reported as new customers despite being "
        f"scheduled in the manual plan"
    )


# ---------------------------------------------------------------------------
# AC4.3 — marks are not merged
# ---------------------------------------------------------------------------

def test_marks_are_not_merged_across_duplicate_columns(parsed):
    first = [w for w, v in enumerate(parsed.customer_schedule["00460"]) if w >= 1 and v]
    second = [w for w, v in enumerate(parsed.customer_schedule["00460-2"]) if w >= 1 and v]
    assert first == MARKS[DUPLICATED_A]
    assert second == MARKS[DUPLICATED_B]


def test_mark_counts_match_delivery_counts(converted, parsed):
    by_code = {site.site_code: site for site in converted.sites}
    for code, marks in parsed.customer_schedule.items():
        assert sum(marks) == by_code[code].deliveries, code


def test_rename_is_reported_by_the_parser(parsed):
    """R4.3 — silently merging was the defect; silently renaming is not the fix."""
    assert any("00460" in issue for issue in parsed.issues), parsed.issues


def test_unique_column_is_untouched(converted, parsed):
    assert "00777" in parsed.customer_schedule
    weeks = [w for w, v in enumerate(parsed.customer_schedule["00777"]) if w >= 1 and v]
    assert weeks == MARKS[UNIQUE]
