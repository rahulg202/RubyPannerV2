"""Unit tests for supplier allocation and activity (domain/supplier_allocation.py)."""

import pytest

from domain.errors import InfeasibleAllocationError
from domain.params import IntegratedParams, SupplierParams
from domain.supplier_allocation import (
    BWXT,
    CURIUM,
    allocate_suppliers_weekly,
    compute_activity,
    validate_supplier_feasibility,
)


SP = SupplierParams()


# ---------------------------------------------------------------------------
# Activity formula — the six reference weeks
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("gens,pct,expected", [
    (15, SP.curium_surplus_pct, 1586.0),
    (22, SP.bwxt_surplus_pct, 2265.0),
    (25, SP.bwxt_surplus_pct, 2571.0),
    (23, SP.bwxt_surplus_pct, 2367.0),
    (22, SP.curium_surplus_pct, 2331.0),
    (0, SP.curium_surplus_pct, 0.0),
])
def test_activity_reference_values(gens, pct, expected):
    assert compute_activity(gens, pct, SP) == expected


def test_activity_zero_no_floor():
    assert compute_activity(0, SP.curium_surplus_pct, SP) == 0.0


# ---------------------------------------------------------------------------
# Weekly allocation scenarios
# ---------------------------------------------------------------------------

def _yplan(T, spec):
    y = [0] * (T + 1)
    for wk, q in spec.items():
        y[wk] = q
    return y


def _eu(T, spec=None):
    e = [0] * (T + 1)
    for wk, q in (spec or {}).items():
        e[wk] = q
    return e


def test_zero_production_week():
    allocs = allocate_suppliers_weekly(_yplan(3, {}), _eu(3), IntegratedParams(horizon_weeks=3), SP)
    a = allocs[0]
    assert a.curium_good == 0 and a.bwxt_good == 0
    assert a.run_sequence == [] and a.supplier_label == ""


def test_single_curium_run_when_within_first_run():
    allocs = allocate_suppliers_weekly(_yplan(2, {1: 12}), _eu(2), IntegratedParams(horizon_weeks=2), SP)
    a = allocs[0]
    assert (a.curium_good, a.bwxt_good) == (12, 0)
    assert a.run_sequence == [CURIUM]


def test_two_run_split_curium_then_bwxt():
    allocs = allocate_suppliers_weekly(_yplan(2, {1: 20}), _eu(2), IntegratedParams(horizon_weeks=2), SP)
    a = allocs[0]
    assert (a.curium_good, a.bwxt_good) == (15, 5)
    assert a.run_sequence == [CURIUM, BWXT]


@pytest.mark.parametrize("y,exp_curium,exp_bwxt", [
    (31, 16, 15),
    (35, 20, 15),
    (45, 30, 15),
])
def test_three_run_split_is_c_b_c(y, exp_curium, exp_bwxt):
    allocs = allocate_suppliers_weekly(_yplan(2, {1: y}), _eu(2), IntegratedParams(horizon_weeks=2), SP)
    a = allocs[0]
    assert a.run_sequence == [CURIUM, BWXT, CURIUM]
    assert (a.curium_good, a.bwxt_good) == (exp_curium, exp_bwxt)
    assert a.curium_good + a.bwxt_good == y


def test_curium_unavailable_all_bwxt_when_no_eu():
    sp = SupplierParams(curium_unavailable_weeks=(1,))
    allocs = allocate_suppliers_weekly(_yplan(2, {1: 20}), _eu(2), IntegratedParams(horizon_weeks=2), sp)
    a = allocs[0]
    assert (a.curium_good, a.bwxt_good) == (0, 20)
    assert set(a.run_sequence) == {BWXT}


def test_curium_unavailable_with_eu_demand_raises():
    sp = SupplierParams(curium_unavailable_weeks=(1,))
    with pytest.raises(InfeasibleAllocationError):
        allocate_suppliers_weekly(_yplan(2, {1: 20}), _eu(2, {1: 1}), IntegratedParams(horizon_weeks=2), sp)


def test_bwxt_unavailable_all_curium():
    sp = SupplierParams(bwxt_unavailable_weeks=(1,))
    allocs = allocate_suppliers_weekly(_yplan(2, {1: 30}), _eu(2), IntegratedParams(horizon_weeks=2), sp)
    a = allocs[0]
    assert (a.curium_good, a.bwxt_good) == (30, 0)
    assert set(a.run_sequence) == {CURIUM}


def test_both_unavailable_with_production_raises():
    sp = SupplierParams(curium_unavailable_weeks=(1,), bwxt_unavailable_weeks=(1,))
    with pytest.raises(InfeasibleAllocationError):
        allocate_suppliers_weekly(_yplan(2, {1: 10}), _eu(2), IntegratedParams(horizon_weeks=2), sp)


def test_eu_covered_by_curium_in_split_week():
    # 3-run split: curium >= eu demand (eu max small)
    allocs = allocate_suppliers_weekly(_yplan(2, {1: 45}), _eu(2, {1: 4}), IntegratedParams(horizon_weeks=2), SP)
    a = allocs[0]
    assert a.eu_constraint_satisfied is True
    assert a.curium_good >= 4


# ---------------------------------------------------------------------------
# Feasibility pre-check
# ---------------------------------------------------------------------------

def test_feasibility_flags_eu_with_curium_unavailable():
    sp = SupplierParams(curium_unavailable_weeks=(5,))
    demand = _yplan(10, {5: 3})
    eu = _eu(10, {5: 2})
    errs = validate_supplier_feasibility(demand, eu, [], sp, 10)
    assert any("Curium" in e for e in errs)


def test_feasibility_flags_both_unavailable_non_shutdown():
    sp = SupplierParams(curium_unavailable_weeks=(5,), bwxt_unavailable_weeks=(5,))
    demand = _yplan(10, {5: 3})
    eu = _eu(10)
    errs = validate_supplier_feasibility(demand, eu, [], sp, 10)
    assert any("both suppliers" in e for e in errs)


def test_feasibility_ok_when_both_unavailable_but_shutdown():
    sp = SupplierParams(curium_unavailable_weeks=(5,), bwxt_unavailable_weeks=(5,))
    demand = _yplan(10, {5: 0})  # no demand that week
    errs = validate_supplier_feasibility(demand, _eu(10), [5], sp, 10)
    assert errs == []
