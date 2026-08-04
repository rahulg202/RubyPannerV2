"""Unit + property tests for domain/dates.py."""

from datetime import date, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from domain.dates import current_planning_week, derive_week_dates


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------

def test_derive_basic():
    rows = derive_week_dates(date(2026, 1, 5), 4, 3)
    assert rows == [
        (1, date(2026, 1, 5), date(2026, 1, 9)),
        (2, date(2026, 1, 12), date(2026, 1, 16)),
        (3, date(2026, 1, 19), date(2026, 1, 23)),
    ]


def test_derive_length_matches_horizon():
    rows = derive_week_dates(date(2026, 1, 5), 4, 52)
    assert len(rows) == 52
    assert rows[0][0] == 1 and rows[-1][0] == 52


def test_zero_offset_cal_equals_mfg():
    rows = derive_week_dates(date(2026, 1, 5), 0, 2)
    for _, mfg, cal in rows:
        assert mfg == cal


@pytest.mark.parametrize("bad_offset", [-1, -10])
def test_negative_offset_rejected(bad_offset):
    with pytest.raises(ValueError):
        derive_week_dates(date(2026, 1, 5), bad_offset, 5)


@pytest.mark.parametrize("bad_h", [0, -1])
def test_bad_horizon_rejected(bad_h):
    with pytest.raises(ValueError):
        derive_week_dates(date(2026, 1, 5), 4, bad_h)


def test_current_week_inside_horizon():
    ref = date(2026, 1, 5)
    assert current_planning_week(ref, date(2026, 1, 5)) == 1
    assert current_planning_week(ref, date(2026, 1, 11)) == 1   # within week 1
    assert current_planning_week(ref, date(2026, 1, 12)) == 2
    assert current_planning_week(ref, date(2026, 1, 5) + timedelta(days=7 * 51)) == 52


def test_current_week_before_horizon_is_none():
    ref = date(2026, 1, 5)
    assert current_planning_week(ref, date(2026, 1, 4)) is None


def test_current_week_after_horizon_is_none():
    ref = date(2026, 1, 5)
    after = ref + timedelta(days=7 * 52)  # start of week 53
    assert current_planning_week(ref, after, horizon_weeks=52) is None


# ---------------------------------------------------------------------------
# Property tests
# ---------------------------------------------------------------------------

_dates = st.dates(min_value=date(2000, 1, 1), max_value=date(2100, 12, 31))


@settings(max_examples=100)
@given(ref=_dates, offset=st.integers(min_value=0, max_value=30),
       h=st.integers(min_value=1, max_value=104))
def test_property_spacing_and_offset(ref, offset, h):
    rows = derive_week_dates(ref, offset, h)
    assert len(rows) == h
    for i, (week, mfg, cal) in enumerate(rows):
        assert week == i + 1
        assert mfg == ref + timedelta(days=7 * i)          # 7-day spacing
        assert cal == mfg + timedelta(days=offset)         # cal = mfg + offset


@settings(max_examples=100)
@given(ref=_dates, week=st.integers(min_value=1, max_value=52))
def test_property_roundtrip_week(ref, week):
    # The mfg date of week N must map back to week N.
    rows = derive_week_dates(ref, 4, 52)
    mfg = rows[week - 1][1]
    assert current_planning_week(ref, mfg, horizon_weeks=52) == week


@settings(max_examples=100)
@given(ref=_dates, days_before=st.integers(min_value=1, max_value=3650))
def test_property_before_ref_is_none(ref, days_before):
    assert current_planning_week(ref, ref - timedelta(days=days_before)) is None
