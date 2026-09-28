"""R10.2 — match suggestions must not depend on input row order.

``_suggest`` was given the headers not yet claimed *by rows seen so far*, so a
suggestion could point at a Master Planner column that a **later** input row
matches exactly. Re-sorting the input file changed the advice the planner was
given, and the same column could be offered to one row while belonging to another.

See `.kiro/specs/correctness-remediation/requirements.md` R10.2.
"""

from __future__ import annotations

import itertools

from domain.site_matching import (
    STATUS_MATCHED,
    STATUS_NO_COLUMN,
    build_match_report,
    match_report_frame,
    summarize_match,
)

MANUAL_HEADERS = {
    "00449": "00449 Alpha Specialty Care, Fresno, CA (7)",
    "00460": "00460 Mercy Hospital, Columbus, OH (4)",
    "00777": "00777 Riverside Cardiology, Austin, TX (7)",
}

# The middle row has a wrong code but a name matching a column that the *third*
# row claims exactly. Order therefore used to decide whether it was suggested.
INPUT_SITES = [
    ("00449", "Alpha Specialty Care, Fresno, CA"),
    ("BAD-1", "Riverside Cardiology, Austin, TX"),
    ("00777", "Riverside Cardiology, Austin, TX"),
]


def _report(rows):
    return build_match_report(rows, MANUAL_HEADERS, MANUAL_HEADERS.keys())


def _fingerprint(report):
    """Order-insensitive view of the report's content."""
    return sorted(
        (r.site_id, r.status, r.suggestion, r.manual_plan_header)
        for r in report
    )


def test_report_content_is_identical_under_any_row_permutation():
    """AC10.2 — the finding, stated as an invariant over all 6 orderings."""
    baseline = _fingerprint(_report(INPUT_SITES))
    for permutation in itertools.permutations(INPUT_SITES):
        assert _fingerprint(_report(list(permutation))) == baseline, permutation


def test_a_claimed_column_is_never_suggested_to_another_row():
    """00777 is matched exactly by its own row, so it cannot be offered to BAD-1."""
    report = _report(INPUT_SITES)
    unmatched = [r for r in report if r.status == STATUS_NO_COLUMN]
    assert len(unmatched) == 1
    assert unmatched[0].site_id == "BAD-1"
    assert "00777" not in unmatched[0].suggestion, (
        f"suggested a column another input row already matches: "
        f"{unmatched[0].suggestion!r}"
    )


def test_reversed_order_gives_the_same_suggestion_as_forward_order():
    forward = _report(INPUT_SITES)
    reverse = _report(list(reversed(INPUT_SITES)))
    by_id_forward = {r.site_id: r.suggestion for r in forward}
    by_id_reverse = {r.site_id: r.suggestion for r in reverse}
    assert by_id_forward == by_id_reverse


def test_a_genuinely_free_column_is_still_suggested():
    """The fix must not suppress useful suggestions, only wrong ones."""
    sites = [("BAD-2", "Mercy Hospital, Columbus, OH")]
    report = build_match_report(sites, MANUAL_HEADERS, MANUAL_HEADERS.keys())
    unmatched = [r for r in report if r.status == STATUS_NO_COLUMN]
    assert len(unmatched) == 1
    assert "00460" in unmatched[0].suggestion


def test_summary_counts_are_order_independent():
    baseline = summarize_match(_report(INPUT_SITES))
    for permutation in itertools.permutations(INPUT_SITES):
        assert summarize_match(_report(list(permutation))) == baseline


def test_matched_rows_keep_their_header_in_every_order():
    for permutation in itertools.permutations(INPUT_SITES):
        report = _report(list(permutation))
        matched = {r.site_id: r for r in report if r.status == STATUS_MATCHED}
        assert matched["00449"].manual_plan_header == MANUAL_HEADERS["00449"]
        assert matched["00777"].manual_plan_header == MANUAL_HEADERS["00777"]


def test_frame_renders_without_error():
    frame = match_report_frame(_report(INPUT_SITES))
    assert list(frame.columns)[:3] == ["Site_ID", "Site_Name", "Status"]
    assert len(frame) == len(_report(INPUT_SITES))
