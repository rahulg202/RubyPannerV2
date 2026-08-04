"""Unit tests for quarterly quota accounting (domain/quota.py)."""

from datetime import date

import pytest

from domain.params import IntegratedParams, SupplierParams
from domain.quota import check_quarterly_quota, compute_quarter_boundaries
from domain.supplier_allocation import allocate_suppliers_weekly


def test_fallback_13_week_quarters():
    q = compute_quarter_boundaries(52, quarter_start_month=1, reference_week_date=None)
    assert len(q) == 4
    assert q[0].quarter == 1
    assert list(q[0].weeks) == list(range(1, 14))
    assert list(q[3].weeks) == list(range(40, 53))
    assert not any(s.is_partial for s in q)


def test_fallback_short_tail_block_is_partial():
    q = compute_quarter_boundaries(20, quarter_start_month=1, reference_week_date=None)
    assert len(q) == 2
    assert q[0].is_partial is False          # weeks 1-13
    assert q[1].is_partial is True           # weeks 14-20 only
    assert q[1].weeks_covered if hasattr(q[1], "weeks_covered") else True


def test_calendar_quarters_january_start_all_full():
    """A quarter-aligned reference week must give four complete quarters."""
    q = compute_quarter_boundaries(52, quarter_start_month=1,
                                   reference_week_date=date(2026, 1, 5))
    assert len(q) == 4
    assert 1 in q[0].weeks
    assert not any(s.is_partial for s in q), \
        "a quarter-aligned start should produce no partial quarters"


def test_mid_year_reference_creates_partial_quarters():
    q = compute_quarter_boundaries(52, quarter_start_month=1,
                                   reference_week_date=date(2026, 7, 27))
    partial = [s for s in q if s.is_partial]
    assert len(q) == 5
    assert len(partial) == 2, "first and last quarters should be partial"
    assert partial[0].quarter == 1
    assert partial[-1].quarter == 5


def test_interior_quarters_never_partial():
    q = compute_quarter_boundaries(52, quarter_start_month=1,
                                   reference_week_date=date(2026, 7, 27))
    for span in q[1:-1]:
        assert span.is_partial is False


def test_coverage_fraction():
    q = compute_quarter_boundaries(52, quarter_start_month=1,
                                   reference_week_date=date(2026, 7, 27))
    last = q[-1]
    assert 0 < last.coverage < 1
    assert last.coverage == len(last.weeks) / last.expected_weeks


def test_quota_no_shortfall_when_met():
    sp = SupplierParams(curium_quarterly_quota_mci=1000.0, bwxt_quarterly_quota_mci=0.0)
    params = IntegratedParams(horizon_weeks=13)
    # Produce plenty of Curium each week
    y = [0] + [30] * 13
    eu = [0] * 14
    allocs = allocate_suppliers_weekly(y, eu, params, sp)
    boundaries = compute_quarter_boundaries(13, 1, None)
    statuses = check_quarterly_quota(allocs, sp, boundaries)
    curium = [s for s in statuses if s.supplier == "Curium"][0]
    assert curium.ordered_mci >= 1000.0
    assert curium.shortfall_mci == 0.0
    assert curium.penalty_usd == 0.0


def test_quota_shortfall_and_penalty():
    sp = SupplierParams(
        curium_quarterly_quota_mci=100000.0,   # unreachably high
        bwxt_quarterly_quota_mci=0.0,
        quota_shortfall_penalty_rate=2.0,
    )
    params = IntegratedParams(horizon_weeks=13)
    y = [0] + [15] * 13     # modest Curium production
    eu = [0] * 14
    allocs = allocate_suppliers_weekly(y, eu, params, sp)
    boundaries = compute_quarter_boundaries(13, 1, None)
    statuses = check_quarterly_quota(allocs, sp, boundaries)
    curium = [s for s in statuses if s.supplier == "Curium"][0]
    assert curium.shortfall_mci > 0
    assert curium.penalty_usd == curium.shortfall_mci * 2.0
    # Under quota: remaining = quota - ordered is positive and equals the shortfall.
    assert curium.remaining_mci > 0
    assert curium.remaining_mci == curium.shortfall_mci


def test_status_has_both_suppliers_per_quarter():
    sp = SupplierParams()
    params = IntegratedParams(horizon_weeks=13)
    y = [0] + [20] * 13
    eu = [0] * 14
    allocs = allocate_suppliers_weekly(y, eu, params, sp)
    boundaries = compute_quarter_boundaries(13, 1, None)
    statuses = check_quarterly_quota(allocs, sp, boundaries)
    suppliers = {s.supplier for s in statuses}
    assert suppliers == {"Curium", "BWXT"}


# ---------------------------------------------------------------------------
# Partial quarters must never be penalised
# ---------------------------------------------------------------------------

def _allocs(weeks: int, per_week: int = 15):
    sp = SupplierParams()
    params = IntegratedParams(horizon_weeks=weeks)
    y = [0] + [per_week] * weeks
    eu = [0] * (weeks + 1)
    return allocate_suppliers_weekly(y, eu, params, sp)


def test_partial_quarter_carries_zero_penalty():
    """The core fix: a fragment of a quarter cannot be charged a full quota."""
    from domain.quota import STATUS_PARTIAL
    sp = SupplierParams(curium_quarterly_quota_mci=100000.0,   # unreachable
                        bwxt_quarterly_quota_mci=100000.0,
                        quota_shortfall_penalty_rate=50000.0)
    # 20 weeks -> weeks 1-13 full, weeks 14-20 partial
    allocs = _allocs(20)
    spans = compute_quarter_boundaries(20, 1, None)
    statuses = check_quarterly_quota(allocs, sp, spans)

    partial = [s for s in statuses if s.is_partial]
    full = [s for s in statuses if not s.is_partial]
    assert partial, "expected a partial quarter in a 20-week horizon"
    for s in partial:
        assert s.penalty_usd == 0.0
        assert s.shortfall_mci == 0.0
        assert s.status == STATUS_PARTIAL
    # The complete quarter is still judged normally
    assert any(s.penalty_usd > 0 for s in full)


def test_partial_quarter_reports_prorated_target():
    sp = SupplierParams(curium_quarterly_quota_mci=13000.0,
                        bwxt_quarterly_quota_mci=13000.0)
    allocs = _allocs(20)
    spans = compute_quarter_boundaries(20, 1, None)
    statuses = check_quarterly_quota(allocs, sp, spans)
    partial = [s for s in statuses if s.is_partial][0]
    # 7 of 13 weeks covered -> target is 7/13 of the quota
    assert partial.weeks_covered == 7
    assert partial.expected_weeks == 13
    assert partial.prorated_quota_mci == pytest.approx(13000.0 * 7 / 13)


def test_full_quarter_behaviour_unchanged():
    """Regression: complete quarters must still be penalised as before."""
    from domain.quota import STATUS_OK, STATUS_SHORTFALL
    sp = SupplierParams(curium_quarterly_quota_mci=100000.0,
                        bwxt_quarterly_quota_mci=0.0,
                        quota_shortfall_penalty_rate=2.0)
    allocs = _allocs(13)
    spans = compute_quarter_boundaries(13, 1, None)
    statuses = check_quarterly_quota(allocs, sp, spans)
    curium = [s for s in statuses if s.supplier == "Curium"][0]
    bwxt = [s for s in statuses if s.supplier == "BWXT"][0]
    assert curium.is_partial is False
    assert curium.shortfall_mci > 0
    assert curium.penalty_usd == curium.shortfall_mci * 2.0
    assert curium.status == STATUS_SHORTFALL
    assert bwxt.status == STATUS_OK          # zero quota is always met


def test_quarter_aligned_reference_produces_no_partial_penalty():
    """The user-facing fix: aligning the reference week removes the phantom cost."""
    sp = SupplierParams(curium_quarterly_quota_mci=10000.0,
                        bwxt_quarterly_quota_mci=10000.0)
    allocs = _allocs(52, per_week=30)
    aligned = check_quarterly_quota(
        allocs, sp, compute_quarter_boundaries(52, 1, date(2026, 1, 5))
    )
    assert all(not s.is_partial for s in aligned)
    assert sum(s.penalty_usd for s in aligned) == 0.0


def test_mid_year_reference_penalty_is_zero_after_fix():
    """Previously this produced a phantom multi-hundred-million penalty."""
    sp = SupplierParams(curium_quarterly_quota_mci=10000.0,
                        bwxt_quarterly_quota_mci=10000.0,
                        quota_shortfall_penalty_rate=50000.0)
    allocs = _allocs(52, per_week=30)
    statuses = check_quarterly_quota(
        allocs, sp, compute_quarter_boundaries(52, 1, date(2026, 7, 27))
    )
    partial = [s for s in statuses if s.is_partial]
    assert partial, "mid-year start should yield partial quarters"
    assert sum(s.penalty_usd for s in partial) == 0.0


def test_partial_quarter_note_explains_exclusion():
    from domain.quota import partial_quarter_note
    sp = SupplierParams()
    allocs = _allocs(20)
    statuses = check_quarterly_quota(allocs, sp, compute_quarter_boundaries(20, 1, None))
    note = partial_quarter_note(statuses)
    assert note is not None
    assert "outside the 52-week plan" in note
    assert "no penalty is charged" in note
    # Keep it short enough to read in a UI callout.
    assert len(note) < 260, f"note is too long for the app ({len(note)} chars)"


def test_partial_quarter_note_absent_when_all_full():
    from domain.quota import partial_quarter_note
    sp = SupplierParams()
    allocs = _allocs(13)
    statuses = check_quarterly_quota(allocs, sp, compute_quarter_boundaries(13, 1, None))
    assert partial_quarter_note(statuses) is None
