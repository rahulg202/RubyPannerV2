"""R6 — a stored result must not be compared against different settings.

``ui.state.clear_results`` is called on file upload and on *Restore all defaults*,
never on an individual setting change, despite a docstring reading "called when
inputs or settings change". So: run the optimizer, change ``penalty_rate``, open
Comparison. The manual plan is evaluated under the new rates against an
``opt_result.summary`` computed under the old ones, with no warning, while the
optimizer tab keeps showing the stale plan as current.

Hooking all 31 widgets would be fragile. Stamping each result with a fingerprint
of the settings that produced it makes a missed invalidation *visible* instead.

See `.kiro/specs/correctness-remediation/requirements.md` R6.
"""

from __future__ import annotations

import dataclasses

import pandas as pd
import pytest

from domain.errors import StaleResultError
from domain.params import IntegratedParams, SupplierParams
from services.comparison_service import ComparisonService
from services.dtos import ComparisonRequest
from services.settings_service import DEFAULTS, build_settings, default_settings


# ---------------------------------------------------------------------------
# AC6.1 — sensitivity and stability
# ---------------------------------------------------------------------------

def test_equal_settings_have_equal_fingerprints():
    assert build_settings(None).fingerprint == build_settings(None).fingerprint


def test_fingerprint_is_stable_across_construction_order():
    a = build_settings({"penalty_rate": 8000.0, "overtime_rate": 2500.0})
    b = build_settings({"overtime_rate": 2500.0, "penalty_rate": 8000.0})
    assert a.fingerprint == b.fingerprint


@pytest.mark.parametrize("field_name,value", [
    ("penalty_rate", 8000.0),
    ("overtime_rate", 2500.0),
    ("capacity_rate", 1000.0),
    ("w_penalty", 0.5),
    ("row_cap", 3),
    ("horizon_weeks", 40),
    ("min_batch_produced", 4),
    ("first_run_allocation", 10),
    ("curium_quarterly_quota_mci", 5000.0),
    ("quota_shortfall_penalty_rate", 1234.0),
    ("w_quota", 0.25),
    ("quarter_start_month", 4),
    ("shutdown_weeks", "10,11"),
    ("partial_shutdown_weeks", "20"),
    ("curium_unavailable_weeks", "5"),
    ("bwxt_unavailable_weeks", "6"),
    ("calibration_offset_days", 7),
    ("sheet", "Other"),
])
def test_changing_any_setting_changes_the_fingerprint(field_name, value):
    baseline = build_settings(None).fingerprint
    changed = build_settings({field_name: value}).fingerprint
    assert changed != baseline, f"{field_name} does not affect the fingerprint"


def test_every_defaults_key_is_covered_by_the_fingerprint():
    """A new parameter must be picked up automatically, not by hand.

    Walking ``dataclasses.fields`` rather than a hand-written list is what makes
    this hold; the test exists so the guarantee cannot silently regress.
    """
    baseline = build_settings(None).fingerprint
    uncovered = []
    skipped = []

    for key, current in DEFAULTS.items():
        for probe in _candidates(key, current):
            try:
                changed = build_settings({key: probe}).fingerprint
            except Exception:
                continue          # out of range for this field; try the next
            if changed != baseline:
                break
            uncovered.append(key)
            break
        else:
            # No candidate was accepted, so this key could not be exercised here.
            skipped.append(key)

    assert uncovered == [], f"settings absent from the fingerprint: {uncovered}"
    # Guard the guard. The loop above swallows validation errors so it can try the
    # next candidate, which means a helper that produced nothing legal would sail
    # through having tested nothing at all. Every key must actually have been
    # exercised, not merely not-failed.
    assert skipped == [], f"settings that could not be perturbed: {skipped}"
    assert len(DEFAULTS) > 25, "DEFAULTS shrank unexpectedly; is this still a real sweep?"


def _candidates(key: str, current):
    """Candidate replacement values for a settings key, best first.

    Several fields are range-bound (weights in [0,1], ``first_run_allocation`` at
    most one batch) or bound to each other (``min_batch_produced`` against
    ``max_batch_produced``), so a single blind ``+1`` is not usable. Offering
    several candidates and taking the first the validator accepts keeps this
    generic, which is the point: it must keep working for parameters nobody has
    added yet.
    """
    import datetime

    if key == "reference_week_date":
        return [datetime.date(2026, 3, 2)]
    if key in {"shutdown_weeks", "partial_shutdown_weeks",
               "curium_unavailable_weeks", "bwxt_unavailable_weeks"}:
        return ["7"]
    if key == "sheet":
        return ["Different"]
    if key == "quarter_start_month":
        return [4, 7]
    if key == "horizon_weeks":
        return [40]
    if isinstance(current, bool):
        return [not current]
    if isinstance(current, float):
        return [current + 1.0, current / 2.0, current * 2.0, 0.5]
    if isinstance(current, int):
        return [current + 1, current - 1]
    return [f"{current}-x"]


def test_fingerprint_is_a_short_hex_digest():
    fingerprint = default_settings().fingerprint
    assert isinstance(fingerprint, str)
    assert len(fingerprint) >= 8
    int(fingerprint, 16)      # raises if not hex


# ---------------------------------------------------------------------------
# AC6.2 — the comparison refuses to mix generations
# ---------------------------------------------------------------------------

class _StubReader:
    """Never reached: the staleness guard must fire before any parsing."""

    def parse(self, *args, **kwargs):     # pragma: no cover - must not run
        raise AssertionError("staleness must be detected before parsing")


def _request(optimized_fingerprint: str, settings) -> ComparisonRequest:
    return ComparisonRequest(
        master_planner_bytes=b"not read",
        master_planner_sheet="Schedule",
        optimized_summary={"total_composite_cost": 1.0},
        optimized_plan_df=pd.DataFrame({"Week": [1], "Good_Production": [0]}),
        demand=(0, 0),
        params=settings.params,
        supplier_params=settings.supplier_params,
        settings_fingerprint=settings.fingerprint,
        optimized_settings_fingerprint=optimized_fingerprint,
    )


def test_comparison_rejects_a_result_from_different_settings():
    settings = build_settings({"penalty_rate": 9000.0})
    service = ComparisonService(master_planner_reader=_StubReader())
    with pytest.raises(StaleResultError):
        service.run(_request("a-stale-fingerprint", settings))


def test_stale_result_error_names_the_remedy():
    settings = build_settings(None)
    service = ComparisonService(master_planner_reader=_StubReader())
    with pytest.raises(StaleResultError) as excinfo:
        service.run(_request("mismatch", settings))
    assert "Cost Optimizer" in str(excinfo.value)


def test_matching_fingerprints_pass_the_guard():
    """The guard must not block the normal path; parsing is reached and fails."""
    settings = build_settings(None)
    service = ComparisonService(master_planner_reader=_StubReader())
    with pytest.raises(AssertionError, match="before parsing"):
        service.run(_request(settings.fingerprint, settings))


def test_empty_optimized_fingerprint_is_treated_as_unknown_not_matching():
    """An un-stamped legacy result must not silently pass as current."""
    settings = build_settings(None)
    service = ComparisonService(master_planner_reader=_StubReader())
    with pytest.raises(StaleResultError):
        service.run(_request("", settings))
