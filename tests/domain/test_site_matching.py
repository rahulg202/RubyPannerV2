"""Tests for input-file / manual-plan reconciliation (domain.site_matching)."""

from __future__ import annotations

from domain.site_matching import (
    STATUS_MATCHED,
    STATUS_NO_COLUMN,
    STATUS_NO_INPUT_ROW,
    build_match_report,
    match_report_frame,
    summarize_match,
)

HEADERS = {
    "00449": "00449    Alpha Specialty Care, Fresno, CA (7)",
    "00438": "00438    Beta Cardiology, Anchorage, AK (7)",
    "1405": "1405    Gamma UK, London UK (7)",
}


def test_matched_rows_carry_the_manual_plan_header():
    rows = build_match_report([("00449", "Alpha Specialty Care")], HEADERS)
    assert len(rows) == 3          # 1 matched + 2 planned-but-absent
    matched = rows[0]
    assert matched.status == STATUS_MATCHED
    assert matched.matched
    assert matched.manual_plan_header.startswith("00449")
    assert matched.issue == ""


def test_input_row_with_no_column_is_named_with_a_fix():
    rows = build_match_report([("ZZ999", "Unknown Clinic")], HEADERS)
    row = next(r for r in rows if r.site_id == "ZZ999")
    assert row.status == STATUS_NO_COLUMN
    assert not row.matched
    assert "ZZ999" in row.issue
    assert row.how_to_fix


def test_close_name_is_offered_as_a_suggestion_not_applied():
    # Same site, different identifier convention.
    rows = build_match_report([("INT-1", "Alpha Specialty Care, Fresno, CA")], HEADERS)
    row = next(r for r in rows if r.site_id == "INT-1")

    assert row.status == STATUS_NO_COLUMN      # still unmatched, deliberately
    assert "00449" in row.suggestion
    assert "00449" in row.how_to_fix


def test_no_suggestion_when_nothing_is_similar():
    rows = build_match_report([("INT-9", "Completely Different Organisation")],
                             HEADERS)
    row = next(r for r in rows if r.site_id == "INT-9")
    assert row.suggestion == ""
    assert row.how_to_fix          # generic guidance is still given


def test_suggestion_ignores_a_leading_account_number():
    # The candidate header carries a code; the input name does not.
    rows = build_match_report([("X1", "Gamma UK, London UK")], HEADERS)
    row = next(r for r in rows if r.site_id == "X1")
    assert "1405" in row.suggestion


def test_planned_sites_missing_from_the_input_file_are_reported():
    rows = build_match_report([("00449", "Alpha")], HEADERS)
    missing = [r for r in rows if r.status == STATUS_NO_INPUT_ROW]
    assert {r.site_id for r in missing} == {"00438", "1405"}
    assert all(r.issue and r.how_to_fix for r in missing)


def test_reverse_direction_is_sorted_for_stable_output():
    rows = build_match_report([], HEADERS)
    ids = [r.site_id for r in rows if r.status == STATUS_NO_INPUT_ROW]
    assert ids == sorted(ids)


def test_site_ids_are_trimmed_before_comparison():
    rows = build_match_report([("  00449  ", "Alpha")], HEADERS)
    assert rows[0].status == STATUS_MATCHED
    assert rows[0].site_id == "00449"


def test_manual_codes_can_differ_from_the_header_mapping():
    # A code present in the plan but with no header recorded still counts.
    rows = build_match_report(
        [("00700", "Delta")], HEADERS, manual_codes={"00700"}
    )
    assert rows[0].status == STATUS_MATCHED


def test_summary_counts_and_rate():
    rows = build_match_report(
        [("00449", "Alpha"), ("ZZ999", "Unknown")], HEADERS
    )
    s = summarize_match(rows)
    assert s["matched"] == 1
    assert s["input_rows"] == 2
    assert s["missing_from_manual_plan"] == 1
    assert s["missing_from_input_file"] == 2      # 00438 and 1405
    assert s["match_rate"] == 0.5


def test_summary_handles_no_input_rows():
    s = summarize_match(build_match_report([], HEADERS))
    assert s["input_rows"] == 0
    assert s["match_rate"] == 0.0


def test_total_mismatch_reports_a_zero_rate():
    rows = build_match_report([("A", "One"), ("B", "Two")], {})
    s = summarize_match(rows)
    assert s["matched"] == 0
    assert s["match_rate"] == 0.0
    assert all(r.status == STATUS_NO_COLUMN for r in rows)


def test_frame_has_the_columns_the_ui_renders():
    frame = match_report_frame(build_match_report([("00449", "Alpha")], HEADERS))
    assert list(frame.columns) == [
        "Site_ID", "Site_Name", "Status", "Manual_Plan_Header",
        "Issue", "Suggested_Match", "How_To_Fix",
    ]
    assert len(frame) == 3
