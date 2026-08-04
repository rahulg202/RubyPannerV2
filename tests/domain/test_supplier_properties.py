"""Property tests for supplier allocation, activity, and quota (Phase 6).

Maps to the correctness properties in
.kiro/specs/supplier-constraints/design.md (Testing Strategy).
"""

from hypothesis import given, settings
from hypothesis import strategies as st

from domain.params import IntegratedParams, SupplierParams
from domain.quota import check_quarterly_quota, compute_quarter_boundaries
from domain.supplier_allocation import (
    BWXT,
    CURIUM,
    allocate_suppliers_weekly,
    compute_activity,
)

PARAMS = IntegratedParams(horizon_weeks=1)
SP = SupplierParams()

# A single-week y_plan with production in [0, 45]
_y = st.integers(min_value=0, max_value=45)
_eu = st.integers(min_value=0, max_value=4)


def _alloc(y, eu):
    return allocate_suppliers_weekly([0, y], [0, eu], PARAMS, SP)[0]


# Property 1: allocation sums to y
@settings(max_examples=100)
@given(y=_y, eu=_eu)
def test_allocation_sums_to_y(y, eu):
    a = _alloc(y, eu)
    assert a.curium_good + a.bwxt_good == y


# Property 2: run sequence length == batches_needed(y)
@settings(max_examples=100)
@given(y=_y)
def test_sequence_length_matches_batches(y):
    a = _alloc(y, 0)
    import math
    expected = 0 if y == 0 else math.ceil(y / SP.max_good_per_batch)
    assert len(a.run_sequence) == expected


# Property 3: Curium first (when both available and split)
@settings(max_examples=100)
@given(y=st.integers(min_value=1, max_value=45))
def test_curium_first(y):
    a = _alloc(y, 0)
    if a.run_sequence:
        assert a.run_sequence[0] == CURIUM


# Property 4: three-run pattern is exactly C-B-C
@settings(max_examples=100)
@given(y=st.integers(min_value=31, max_value=45))
def test_three_run_pattern(y):
    a = _alloc(y, 0)
    assert a.run_sequence == [CURIUM, BWXT, CURIUM]


# Property 5: EU covered by Curium (when Curium available)
@settings(max_examples=100)
@given(y=st.integers(min_value=1, max_value=45), eu=_eu)
def test_eu_by_construction(y, eu):
    a = _alloc(y, eu)
    # Curium always gets at least min(y, first_run)=min(y,15) >= eu (<=4) when y>=eu
    if y >= eu:
        assert a.curium_good >= eu
        assert a.eu_constraint_satisfied


# Property 6: activity monotonic non-decreasing in generators
@settings(max_examples=100)
@given(g1=st.integers(min_value=0, max_value=44))
def test_activity_monotonic(g1):
    a1 = compute_activity(g1, SP.curium_surplus_pct, SP)
    a2 = compute_activity(g1 + 1, SP.curium_surplus_pct, SP)
    assert a2 >= a1


# Property 7: activity zero iff zero generators
@settings(max_examples=100)
@given(g=st.integers(min_value=0, max_value=45))
def test_activity_zero_iff_zero(g):
    a = compute_activity(g, SP.curium_surplus_pct, SP)
    assert (a == 0.0) == (g == 0)


# Property 8/9/10: shortfall >= 0, penalty proportional, no penalty when met
@settings(max_examples=50)
@given(
    quota=st.floats(min_value=0.0, max_value=50000.0),
    rate=st.floats(min_value=0.0, max_value=100.0),
    prod=st.integers(min_value=0, max_value=45),
)
def test_quota_shortfall_and_penalty(quota, rate, prod):
    sp = SupplierParams(
        curium_quarterly_quota_mci=quota,
        bwxt_quarterly_quota_mci=0.0,
        quota_shortfall_penalty_rate=rate,
    )
    params = IntegratedParams(horizon_weeks=1)
    allocs = allocate_suppliers_weekly([0, prod], [0, 0], params, sp)
    boundaries = compute_quarter_boundaries(1, 1, None)
    statuses = check_quarterly_quota(allocs, sp, boundaries)
    curium = [s for s in statuses if s.supplier == CURIUM][0]
    assert curium.shortfall_mci >= 0.0
    assert curium.penalty_usd == curium.shortfall_mci * rate
    if curium.ordered_mci >= quota:
        assert curium.shortfall_mci == 0.0
