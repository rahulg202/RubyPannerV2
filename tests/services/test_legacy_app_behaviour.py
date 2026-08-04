"""Regression tests preserving the behaviour formerly covered by tests/test_app.py.

The old ``app.py`` held ``parse_week_list``, ``validate_inputs``, ``build_params``
and ``export_excel_bytes``. Those responsibilities moved into the settings service
and the workbook exporter during the layered refactor. These tests keep the same
guarantees at their new homes so the refactor cannot silently regress them.
"""

from __future__ import annotations

import io

import pandas as pd
import pytest
from hypothesis import given, settings as hyp_settings
from hypothesis import strategies as st

from domain.errors import ValidationError
from domain.params import IntegratedParams
from io_adapters.workbook_exporter import WorkbookExporter
from services.settings_service import build_settings, parse_week_list


# ---------------------------------------------------------------------------
# parse_week_list (was app.parse_week_list)
# ---------------------------------------------------------------------------

def test_empty_string_returns_empty_list_no_error():
    assert parse_week_list("") == ([], None)


def test_whitespace_only_returns_empty_list_no_error():
    assert parse_week_list("   ") == ([], None)


def test_valid_single_week():
    assert parse_week_list("5") == ([5], None)


def test_valid_list_sorted():
    weeks, err = parse_week_list("9,1,5")
    assert weeks == [1, 5, 9] and err is None


def test_non_integer_token_returns_error():
    weeks, err = parse_week_list("1,abc,3")
    assert weeks == [] and err is not None


def test_float_token_returns_error():
    weeks, err = parse_week_list("1,2.5")
    assert weeks == [] and err is not None


def test_negative_week_returns_error():
    weeks, err = parse_week_list("-1")
    assert weeks == [] and err is not None


def test_zero_week_returns_error():
    weeks, err = parse_week_list("0")
    assert weeks == [] and err is not None


def test_extra_whitespace_around_tokens():
    weeks, err = parse_week_list("  3 , 1  ")
    assert weeks == [1, 3] and err is None


def test_empty_entry_between_commas_returns_error():
    weeks, err = parse_week_list("1,,3")
    assert weeks == [] and err is not None


# ---------------------------------------------------------------------------
# Settings validation (was app.validate_inputs)
# ---------------------------------------------------------------------------

def test_valid_inputs_produce_no_errors():
    s = build_settings({})
    assert isinstance(s.params, IntegratedParams)


def test_all_zero_weights_produces_error():
    with pytest.raises(ValidationError):
        build_settings({"w_penalty": 0.0, "w_overtime": 0.0, "w_capacity": 0.0})


def test_out_of_range_weight_produces_error():
    with pytest.raises(ValidationError):
        build_settings({"w_penalty": 1.5})


def test_invalid_shutdown_string_produces_error():
    with pytest.raises(ValidationError) as exc:
        build_settings({"shutdown_weeks": "1,abc"})
    assert any("Shutdown weeks" in m for m in exc.value.errors)


def test_invalid_partial_string_produces_error():
    with pytest.raises(ValidationError) as exc:
        build_settings({"partial_shutdown_weeks": "x"})
    assert any("Partial shutdown weeks" in m for m in exc.value.errors)


# ---------------------------------------------------------------------------
# Param assembly (was app.build_params)
# ---------------------------------------------------------------------------

def test_returns_integrated_params_instance():
    assert isinstance(build_settings({}).params, IntegratedParams)


def test_all_fields_mapped_correctly():
    raw = {
        "horizon_weeks": 40, "min_batch_produced": 3, "max_batch_produced": 14,
        "test_discard_per_batch": 2, "normal_max_batches": 1,
        "overtime_max_batches": 2, "penalty_rate": 1234.0,
        "late_penalty_multiplier": 5.0, "overtime_rate": 999.0,
        "capacity_rate": 111.0, "w_penalty": 0.5, "w_overtime": 0.25,
        "w_capacity": 0.75, "row_cap": 7,
    }
    p = build_settings(raw).params
    assert p.horizon_weeks == 40
    assert p.min_batch_produced == 3
    assert p.max_batch_produced == 14
    assert p.test_discard_per_batch == 2
    assert p.normal_max_batches == 1
    assert p.overtime_max_batches == 2
    assert p.penalty_rate == 1234.0
    assert p.late_penalty_multiplier == 5.0
    assert p.overtime_rate == 999.0
    assert p.capacity_rate == 111.0
    assert p.w_penalty == 0.5
    assert p.w_overtime == 0.25
    assert p.w_capacity == 0.75
    assert p.row_cap == 7


def test_derived_late_penalty_rate():
    p = build_settings({"penalty_rate": 100.0, "late_penalty_multiplier": 10.0}).params
    assert p.late_penalty_rate == 1000.0


# ---------------------------------------------------------------------------
# Export to bytes (was app.export_excel_bytes)
# ---------------------------------------------------------------------------

def _sample():
    params = IntegratedParams()
    plan_df = pd.DataFrame({
        "Week": [1, 2], "Good_Production": [15, 30], "Net_Inventory_End": [0, 0],
    })
    sites_df = pd.DataFrame({"site_id": ["S1"], "next_demand_week": [1],
                             "interval_weeks": [7]})
    issues_df = pd.DataFrame(columns=["row_index", "site_id", "issue"])
    summary = {
        "total_composite_cost": 1000.0, "total_penalty_cost": 500.0,
        "total_overtime_cost": 300.0, "total_capacity_cost": 200.0,
        "overtime_weeks": 1, "w_penalty": 1.0, "w_overtime": 1.0, "w_capacity": 1.0,
    }
    return plan_df, sites_df, issues_df, params, summary


def test_returns_bytes():
    data = WorkbookExporter().export(*_sample())
    assert isinstance(data, bytes) and len(data) > 0


def test_output_is_valid_excel():
    data = WorkbookExporter().export(*_sample())
    assert pd.ExcelFile(io.BytesIO(data)).sheet_names


def test_expected_sheet_names_present():
    data = WorkbookExporter().export(*_sample())
    names = set(pd.ExcelFile(io.BytesIO(data)).sheet_names)
    assert {"Weekly_Plan", "Sites_Clean", "Input_Issues", "Model_Params"} <= names


def test_weekly_plan_sheet_has_data():
    data = WorkbookExporter().export(*_sample())
    df = pd.read_excel(io.BytesIO(data), sheet_name="Weekly_Plan")
    assert len(df) == 2


def test_empty_issues_df_still_produces_valid_excel():
    plan_df, sites_df, _issues, params, summary = _sample()
    empty = pd.DataFrame(columns=["row_index", "site_id", "issue"])
    data = WorkbookExporter().export(plan_df, sites_df, empty, params, summary)
    df = pd.read_excel(io.BytesIO(data), sheet_name="Input_Issues")
    assert df.empty


def test_summary_contains_all_required_fields():
    from domain.demand import build_weekly_demand, clean_sites
    from domain.solver import solve_plan_integrated
    params = IntegratedParams(horizon_weeks=12, w_capacity=0.0)
    sites = pd.DataFrame({
        "site_id": ["S1"], "active": ["Y"], "next_demand_week": [3],
        "interval_weeks": [7], "country": ["usa"],
    })
    active, _ = clean_sites(sites, params)
    demand = build_weekly_demand(active, params)
    _plan, summary = solve_plan_integrated(demand, [], [], [0] * 13, 2, params)
    for key in ("total_composite_cost", "total_penalty_cost", "total_overtime_cost",
                "total_capacity_cost", "overtime_weeks", "w_penalty",
                "w_overtime", "w_capacity"):
        assert key in summary


# ---------------------------------------------------------------------------
# Property tests (were tests/test_app_properties.py)
# ---------------------------------------------------------------------------

@hyp_settings(max_examples=100)
@given(st.text(alphabet="abcdefghij", min_size=1, max_size=6))
def test_property_non_integer_shutdown_strings_produce_errors(text):
    weeks, err = parse_week_list(text)
    assert err is not None and weeks == []


@hyp_settings(max_examples=100)
@given(st.lists(st.integers(min_value=1, max_value=52), min_size=1, max_size=8))
def test_property_valid_week_lists_round_trip(values):
    weeks, err = parse_week_list(",".join(str(v) for v in values))
    assert err is None
    assert weeks == sorted(values)


@hyp_settings(max_examples=100)
@given(st.floats(min_value=1.0, max_value=100000.0),
       st.floats(min_value=1.0, max_value=100.0))
def test_property_late_penalty_rate_derived_correctly(rate, multiplier):
    p = build_settings({"penalty_rate": rate,
                        "late_penalty_multiplier": multiplier}).params
    assert p.late_penalty_rate == pytest.approx(rate * multiplier)


@hyp_settings(max_examples=50)
@given(st.floats(min_value=1.01, max_value=5.0))
def test_property_out_of_range_weights_rejected(weight):
    with pytest.raises(ValidationError):
        build_settings({"w_penalty": weight})
