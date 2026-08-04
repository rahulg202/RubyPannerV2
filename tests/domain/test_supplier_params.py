"""Tests for SupplierParams validation (domain/params.py)."""

import pytest

from domain.params import SupplierParams


def test_defaults_are_valid():
    p = SupplierParams()
    assert p.curium_surplus_pct == 0.05
    assert p.bwxt_surplus_pct == 0.02
    assert p.first_run_allocation == 15
    assert p.max_good_per_batch == 15


@pytest.mark.parametrize("field,value", [
    ("curium_surplus_pct", -0.1),
    ("curium_surplus_pct", 1.5),
    ("bwxt_surplus_pct", 2.0),
    ("minimum_surplus_mci", -1.0),
    ("per_generator_mci", -1.0),
    ("per_batch_mci", -1.0),
    ("curium_quarterly_quota_mci", -1.0),
    ("bwxt_quarterly_quota_mci", -1.0),
    ("quota_shortfall_penalty_rate", -1.0),
    ("w_quota", 1.5),
    ("first_run_allocation", -1),
    ("first_run_allocation", 16),   # > max_good_per_batch
    ("quarter_start_month", 0),
    ("quarter_start_month", 13),
])
def test_invalid_field_rejected(field, value):
    with pytest.raises(ValueError):
        SupplierParams(**{field: value})


def test_negative_unavailable_week_rejected():
    with pytest.raises(ValueError):
        SupplierParams(curium_unavailable_weeks=(0,))
    with pytest.raises(ValueError):
        SupplierParams(bwxt_unavailable_weeks=(-3,))


def test_boundary_values_accepted():
    SupplierParams(curium_surplus_pct=0.0, bwxt_surplus_pct=1.0)
    SupplierParams(first_run_allocation=0)
    SupplierParams(first_run_allocation=15)
    SupplierParams(quarter_start_month=1)
    SupplierParams(quarter_start_month=12)


def test_frozen_immutable():
    p = SupplierParams()
    with pytest.raises(Exception):
        p.curium_surplus_pct = 0.9  # type: ignore[misc]
