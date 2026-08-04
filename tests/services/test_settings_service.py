"""Tests for the settings assembly service (services/settings_service.py)."""

from datetime import date

import pytest

from domain.errors import ValidationError
from domain.params import IntegratedParams, SupplierParams
from services.settings_service import (
    DEFAULTS,
    Settings,
    build_settings,
    default_settings,
    parse_week_list,
)


def test_default_settings_valid():
    s = default_settings()
    assert isinstance(s, Settings)
    assert isinstance(s.params, IntegratedParams)
    assert isinstance(s.supplier_params, SupplierParams)
    assert s.sheet == "Sites"
    assert s.shutdown_weeks == ()
    assert s.calibration_offset_days == 4
    assert s.reference_week_date is None


def test_build_from_partial_overrides():
    s = build_settings({"penalty_rate": 8000.0, "shutdown_weeks": "3, 5, 1"})
    assert s.params.penalty_rate == 8000.0
    assert s.shutdown_weeks == (1, 3, 5)  # parsed + sorted


def test_reference_date_and_offset():
    d = date(2026, 1, 5)
    s = build_settings({"reference_week_date": d, "calibration_offset_days": 4})
    assert s.reference_week_date == d
    assert s.calibration_offset_days == 4


def test_supplier_unavailable_weeks_parsed():
    s = build_settings({"curium_unavailable_weeks": "10,12", "bwxt_unavailable_weeks": "7"})
    assert s.supplier_params.curium_unavailable_weeks == (10, 12)
    assert s.supplier_params.bwxt_unavailable_weeks == (7,)


def test_invalid_weight_aggregated_error():
    with pytest.raises(ValidationError) as exc:
        build_settings({"w_penalty": 2.0})
    assert any("Production/cost" in m for m in exc.value.errors)


def test_invalid_supplier_pct_error():
    with pytest.raises(ValidationError) as exc:
        build_settings({"curium_surplus_pct": 5.0})
    assert any("Supplier" in m for m in exc.value.errors)


def test_multiple_errors_collected():
    with pytest.raises(ValidationError) as exc:
        build_settings({
            "w_penalty": 2.0,               # bad IntegratedParams
            "curium_surplus_pct": 9.0,      # bad SupplierParams
            "shutdown_weeks": "a,b",        # bad week list
            "calibration_offset_days": -1,  # bad offset
        })
    # At least the week-list and offset errors surface alongside param errors
    assert len(exc.value.errors) >= 3


def test_negative_calibration_offset_rejected():
    with pytest.raises(ValidationError):
        build_settings({"calibration_offset_days": -5})


def test_bad_reference_date_type_rejected():
    with pytest.raises(ValidationError):
        build_settings({"reference_week_date": "2026-01-05"})  # str, not date


@pytest.mark.parametrize("text,expected", [
    ("", []),
    (None, []),
    ("3,1,2", [1, 2, 3]),
    ("  5 , 7 ", [5, 7]),
    ([4, 2], [2, 4]),
])
def test_parse_week_list_valid(text, expected):
    weeks, err = parse_week_list(text)
    assert err is None
    assert weeks == expected


@pytest.mark.parametrize("text", ["a,2", "1,,2", "-1,2", "0"])
def test_parse_week_list_invalid(text):
    weeks, err = parse_week_list(text)
    assert err is not None
    assert weeks == []


def test_defaults_dict_covers_all_keys():
    # Guard: DEFAULTS must let build_settings run with no input
    s = build_settings({})
    assert s is not None
