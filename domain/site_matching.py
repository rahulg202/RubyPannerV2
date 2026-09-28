"""Domain: reconcile input-file sites against the manual plan (pure).

Every per-customer figure the tool reports — changed weeks, new customers, the
cost comparison — depends on matching a row in the input file to a column in the
Master Planner. When that matching fails the tool previously reported only a
percentage, which told the planner something was wrong but nothing about which
row or what to do.

This module produces a row-per-site reconciliation naming the problem and the
likely fix, in both directions: input rows with no Master Planner column, and
Master Planner columns with no input row.

A close name match is only ever offered as a **suggestion**. Silent fuzzy
matching would hide precisely the discrepancy the planner needs to see.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

import pandas as pd

# A suggestion is offered only above this similarity on the normalized name.
SUGGESTION_THRESHOLD = 0.6

STATUS_MATCHED = "Matched"
STATUS_NO_COLUMN = "Not in the manual plan"
STATUS_NO_INPUT_ROW = "Not in the input file"

_LEADING_NUM = re.compile(r"^\s*\d+")


def _normalize_name(text: str) -> str:
    """Lowercase, drop a leading account number and punctuation, collapse spaces."""
    without_code = _LEADING_NUM.sub("", str(text).strip())
    no_punct = re.sub(r"[^\w\s]", " ", without_code.lower())
    return re.sub(r"\s+", " ", no_punct).strip()


@dataclass
class MatchRow:
    """One site's reconciliation between the input file and the manual plan."""

    site_id: str
    site_name: str = ""
    status: str = STATUS_MATCHED
    manual_plan_header: str = ""
    issue: str = ""
    suggestion: str = ""
    how_to_fix: str = ""

    @property
    def matched(self) -> bool:
        return self.status == STATUS_MATCHED


def _suggest(name: str, candidates: Mapping[str, str]) -> tuple[str, str]:
    """Return (suggested_code, suggested_header) for the closest name, or ("","").

    ``candidates`` maps code to header text. Matching is on the normalized name,
    so a differing account number prefix does not prevent a suggestion.
    """
    target = _normalize_name(name)
    if not target or not candidates:
        return "", ""

    lookup = {code: _normalize_name(header) for code, header in candidates.items()}
    best_code, best_score = "", 0.0
    for code, candidate in lookup.items():
        if not candidate:
            continue
        score = difflib.SequenceMatcher(None, target, candidate).ratio()
        if score > best_score:
            best_code, best_score = code, score

    if best_score < SUGGESTION_THRESHOLD:
        return "", ""
    return best_code, candidates[best_code]


def build_match_report(
    input_sites: Sequence[tuple[str, str]],
    manual_headers: Mapping[str, str],
    manual_codes: Iterable[str] | None = None,
) -> list[MatchRow]:
    """Reconcile input-file sites against Master Planner columns.

    Parameters
    ----------
    input_sites : Sequence[tuple[str, str]]
        ``(site_id, site_name)`` per active row in the input file. The name may
        be empty; it is only used to suggest a counterpart.
    manual_headers : Mapping[str, str]
        Master Planner ``site_id`` to column header text.
    manual_codes : Iterable[str] | None
        Codes present in the manual plan, when that set differs from
        ``manual_headers`` keys. Defaults to the mapping's keys.

    Returns
    -------
    list[MatchRow]
        Matched rows first (input-file order), then input rows with no column,
        then Master Planner columns with no input row.
    """
    codes = set(manual_codes) if manual_codes is not None else set(manual_headers)
    all_input_ids = {str(site_id).strip() for site_id, _name in input_sites}
    # Columns no input row claims. Computed once, from the whole input set, so a
    # suggestion can never point at a column a *later* row matches.
    #
    # This was previously narrowed by the rows seen so far, which made the output
    # depend on row order: re-sorting the input file changed which counterpart was
    # suggested, and a column could be offered to one row while belonging to
    # another further down.
    unmatched_headers = {
        code: header for code, header in manual_headers.items()
        if code not in all_input_ids
    }

    seen_input: set[str] = set()
    rows: list[MatchRow] = []

    for site_id, site_name in input_sites:
        sid = str(site_id).strip()
        seen_input.add(sid)
        if sid in codes:
            rows.append(MatchRow(
                site_id=sid,
                site_name=site_name,
                status=STATUS_MATCHED,
                manual_plan_header=manual_headers.get(sid, ""),
            ))
            continue

        # Unmatched: name the problem and offer the likeliest counterpart.
        code, header = _suggest(site_name or sid, unmatched_headers)
        if code:
            suggestion = f"{code} — {header}"
            fix = (
                f"If this is the same site, change its Site_ID to '{code}', or add "
                f"'{sid}' to the front of that Master Planner column header."
            )
        else:
            suggestion = ""
            fix = (
                "Add this code to the front of the site's Master Planner column "
                "header, or generate the input file from Import Manual Plan so "
                "both files share the same codes."
            )
        rows.append(MatchRow(
            site_id=sid,
            site_name=site_name,
            status=STATUS_NO_COLUMN,
            issue=f"No Master Planner column has the code '{sid}'.",
            suggestion=suggestion,
            how_to_fix=fix,
        ))

    # Reverse direction: planned sites absent from the input file.
    for code in sorted(codes - seen_input):
        rows.append(MatchRow(
            site_id=code,
            site_name=manual_headers.get(code, ""),
            status=STATUS_NO_INPUT_ROW,
            manual_plan_header=manual_headers.get(code, ""),
            issue=(
                f"The manual plan schedules '{code}' but the input file has no "
                f"active row for it."
            ),
            how_to_fix=(
                "Add the site to the input file, or set Active to Y if the row "
                "exists but is switched off. Until then its generators are "
                "absent from the optimized plan."
            ),
        ))

    return rows


def summarize_match(rows: Sequence[MatchRow]) -> dict:
    """Counts and the match rate, for a headline the planner can read at a glance."""
    matched = sum(1 for r in rows if r.status == STATUS_MATCHED)
    no_column = sum(1 for r in rows if r.status == STATUS_NO_COLUMN)
    no_input = sum(1 for r in rows if r.status == STATUS_NO_INPUT_ROW)
    input_rows = matched + no_column
    return {
        "matched": matched,
        "input_rows": input_rows,
        "missing_from_manual_plan": no_column,
        "missing_from_input_file": no_input,
        "match_rate": (matched / input_rows) if input_rows else 0.0,
    }


def match_report_frame(rows: Sequence[MatchRow]) -> pd.DataFrame:
    """Render the reconciliation for display and export."""
    return pd.DataFrame([
        {
            "Site_ID": r.site_id,
            "Site_Name": r.site_name,
            "Status": r.status,
            "Manual_Plan_Header": r.manual_plan_header,
            "Issue": r.issue,
            "Suggested_Match": r.suggestion,
            "How_To_Fix": r.how_to_fix,
        }
        for r in rows
    ], columns=[
        "Site_ID", "Site_Name", "Status", "Manual_Plan_Header",
        "Issue", "Suggested_Match", "How_To_Fix",
    ])
