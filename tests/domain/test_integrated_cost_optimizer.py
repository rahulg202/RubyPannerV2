"""
Unit tests for integrated_cost_optimizer — input handling (Task 2.2).

Covers Requirements 8.4, 8.5, 8.6:
  8.4 Missing required columns raises ValueError
  8.5 Inactive sites are excluded from the clean output
  8.6 Duplicate Site_IDs are reported as issues and excluded
"""

import pandas as pd
import pytest

from integrated_cost_optimizer import IntegratedParams, clean_sites, read_sites


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DEFAULT_PARAMS = IntegratedParams()


def _make_df(**overrides) -> pd.DataFrame:
    """Build a minimal valid raw sites DataFrame."""
    base = {
        "site_id": ["S1", "S2"],
        "active": ["Y", "Y"],
        "next_demand_week": [1, 4],
        "interval_weeks": [4, 8],
    }
    base.update(overrides)
    return pd.DataFrame(base)


# ---------------------------------------------------------------------------
# Requirement 8.4 — missing required columns raises ValueError
# ---------------------------------------------------------------------------

def test_missing_required_column_raises():
    """read_sites raises ValueError when a required column is absent."""
    df = pd.DataFrame({"site_id": ["S1"], "active": ["Y"], "next_demand_week": [1]})
    # interval_weeks is missing — simulate what read_sites would do after loading
    # We test clean_sites path by calling read_sites on a temp CSV
    import tempfile, os
    with tempfile.NamedTemporaryFile(suffix=".csv", mode="w", delete=False) as f:
        df.to_csv(f, index=False)
        tmp_path = f.name
    try:
        with pytest.raises(ValueError, match="interval_weeks"):
            read_sites(tmp_path)
    finally:
        os.unlink(tmp_path)


def test_all_required_columns_present_does_not_raise():
    """read_sites succeeds when all required columns are present."""
    df = _make_df()
    import tempfile, os
    with tempfile.NamedTemporaryFile(suffix=".csv", mode="w", delete=False) as f:
        df.to_csv(f, index=False)
        tmp_path = f.name
    try:
        result = read_sites(tmp_path)
        assert set(["site_id", "active", "next_demand_week", "interval_weeks"]).issubset(
            result.columns
        )
    finally:
        os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# Requirement 8.5 — inactive sites are excluded
# ---------------------------------------------------------------------------

def test_inactive_sites_excluded():
    """clean_sites excludes rows where Active is not Y/YES/TRUE/1."""
    df = pd.DataFrame({
        "site_id": ["S1", "S2", "S3", "S4", "S5"],
        "active": ["Y", "N", "YES", "FALSE", "1"],
        "next_demand_week": [1, 2, 3, 4, 5],
        "interval_weeks": [4, 4, 4, 4, 4],
    })
    active, issues = clean_sites(df, DEFAULT_PARAMS)
    assert set(active["site_id"]) == {"S1", "S3", "S5"}


def test_all_inactive_returns_empty():
    """clean_sites returns empty DataFrame when all sites are inactive."""
    df = pd.DataFrame({
        "site_id": ["S1", "S2"],
        "active": ["N", "NO"],
        "next_demand_week": [1, 2],
        "interval_weeks": [4, 4],
    })
    active, issues = clean_sites(df, DEFAULT_PARAMS)
    assert len(active) == 0


# ---------------------------------------------------------------------------
# Requirement 8.6 — duplicate Site_IDs reported as issues and excluded
# ---------------------------------------------------------------------------

def test_duplicate_site_ids_reported_as_issues():
    """clean_sites reports duplicate Site_IDs in the issues DataFrame."""
    df = pd.DataFrame({
        "site_id": ["S1", "S1", "S2"],
        "active": ["Y", "Y", "Y"],
        "next_demand_week": [1, 2, 3],
        "interval_weeks": [4, 4, 4],
    })
    active, issues = clean_sites(df, DEFAULT_PARAMS)
    dupe_issues = issues[issues["issue"].str.contains("Duplicate")]
    assert len(dupe_issues) >= 1
    assert "S1" in dupe_issues["site_id"].values


def test_duplicate_site_ids_excluded_from_active():
    """clean_sites excludes all rows with duplicate Site_IDs from the active output."""
    df = pd.DataFrame({
        "site_id": ["S1", "S1", "S2"],
        "active": ["Y", "Y", "Y"],
        "next_demand_week": [1, 2, 3],
        "interval_weeks": [4, 4, 4],
    })
    active, issues = clean_sites(df, DEFAULT_PARAMS)
    assert "S1" not in active["site_id"].values
    assert "S2" in active["site_id"].values


# ---------------------------------------------------------------------------
# Requirement 8.3 — optional country column defaults to empty (non-ROW)
# ---------------------------------------------------------------------------

def test_missing_country_column_defaults_to_non_row():
    """clean_sites treats all sites as non-ROW when country column is absent."""
    df = _make_df()
    active, _ = clean_sites(df, DEFAULT_PARAMS)
    assert (active["is_row"] == False).all()
    assert (active["country"] == "").all()


def test_row_countries_identified_correctly():
    """clean_sites marks Denmark/UK/Netherlands/Sweden sites as ROW."""
    df = pd.DataFrame({
        "site_id": ["S1", "S2", "S3", "S4", "S5"],
        "active": ["Y", "Y", "Y", "Y", "Y"],
        "next_demand_week": [1, 2, 3, 4, 5],
        "interval_weeks": [4, 4, 4, 4, 4],
        "country": ["Denmark", "UK", "Netherlands", "Sweden", "France"],
    })
    active, _ = clean_sites(df, DEFAULT_PARAMS)
    row_sites = active[active["is_row"]]
    non_row_sites = active[~active["is_row"]]
    assert set(row_sites["site_id"]) == {"S1", "S2", "S3", "S4"}
    assert set(non_row_sites["site_id"]) == {"S5"}


# ---------------------------------------------------------------------------
# Requirement 8.7 — out-of-range Next_Demand_Week reported as issues
# ---------------------------------------------------------------------------

def test_out_of_range_demand_week_reported():
    """clean_sites reports Next_Demand_Week outside 1..52 as an issue."""
    df = pd.DataFrame({
        "site_id": ["S1", "S2"],
        "active": ["Y", "Y"],
        "next_demand_week": [0, 53],
        "interval_weeks": [4, 4],
    })
    _, issues = clean_sites(df, DEFAULT_PARAMS)
    assert len(issues) == 2
    assert all("out of range" in msg for msg in issues["issue"])


# ---------------------------------------------------------------------------
# Task 3.1 — build_weekly_demand and build_weekly_row_demand
# Requirements: 7.1, 7.5
# ---------------------------------------------------------------------------

from integrated_cost_optimizer import build_weekly_demand, build_weekly_row_demand


def _make_active(**overrides) -> pd.DataFrame:
    """Build a minimal valid active sites DataFrame (post clean_sites)."""
    base = {
        "site_id": ["S1"],
        "next_demand_week": [1],
        "interval_weeks": [4],
        "country": [""],
        "is_row": [False],
    }
    base.update(overrides)
    return pd.DataFrame(base)


def test_build_weekly_demand_single_site():
    """A single site with interval 4 starting week 1 hits weeks 1,5,9,..."""
    active = _make_active()
    demand = build_weekly_demand(active, DEFAULT_PARAMS)
    assert len(demand) == DEFAULT_PARAMS.horizon_weeks + 1
    assert demand[0] == 0  # index 0 unused
    assert demand[1] == 1
    assert demand[5] == 1
    assert demand[9] == 1
    assert demand[2] == 0  # no demand on off-weeks


def test_build_weekly_demand_total_matches_expected():
    """Total demand across horizon equals number of demand events."""
    active = _make_active(
        site_id=["S1", "S2"],
        next_demand_week=[1, 2],
        interval_weeks=[4, 8],
        country=["", ""],
        is_row=[False, False],
    )
    demand = build_weekly_demand(active, DEFAULT_PARAMS)
    # S1: weeks 1,5,9,...,49 → 13 events; S2: weeks 2,10,18,...,50 → 7 events
    assert sum(demand) == 13 + 7


def test_build_weekly_demand_index_zero_always_zero():
    """Index 0 of demand array is always 0 (1-indexed convention)."""
    active = _make_active()
    demand = build_weekly_demand(active, DEFAULT_PARAMS)
    assert demand[0] == 0


def test_build_weekly_demand_no_sites_returns_zeros():
    """Empty active DataFrame produces all-zero demand."""
    active = pd.DataFrame(columns=["site_id", "next_demand_week", "interval_weeks", "country", "is_row"])
    demand = build_weekly_demand(active, DEFAULT_PARAMS)
    assert sum(demand) == 0
    assert len(demand) == DEFAULT_PARAMS.horizon_weeks + 1


def test_build_weekly_row_demand_only_row_sites():
    """ROW demand only counts Denmark/UK/Netherlands/Sweden sites."""
    active = pd.DataFrame({
        "site_id": ["S1", "S2", "S3"],
        "next_demand_week": [1, 1, 1],
        "interval_weeks": [4, 4, 4],
        "country": ["denmark", "france", "uk"],
        "is_row": [True, False, True],
    })
    row_demand = build_weekly_row_demand(active, DEFAULT_PARAMS)
    # S1 and S3 are ROW, S2 is not — week 1 should have 2
    assert row_demand[1] == 2
    assert row_demand[5] == 2


def test_build_weekly_row_demand_no_row_sites_returns_zeros():
    """When no ROW sites exist, ROW demand is all zeros."""
    active = _make_active(country=["france"], is_row=[False])
    row_demand = build_weekly_row_demand(active, DEFAULT_PARAMS)
    assert sum(row_demand) == 0


def test_build_weekly_row_demand_subset_of_total_demand():
    """ROW demand at each week is always <= total demand at that week."""
    active = pd.DataFrame({
        "site_id": ["S1", "S2", "S3"],
        "next_demand_week": [1, 1, 2],
        "interval_weeks": [4, 4, 4],
        "country": ["denmark", "france", "uk"],
        "is_row": [True, False, True],
    })
    demand = build_weekly_demand(active, DEFAULT_PARAMS)
    row_demand = build_weekly_row_demand(active, DEFAULT_PARAMS)
    for t in range(1, DEFAULT_PARAMS.horizon_weeks + 1):
        assert row_demand[t] <= demand[t]


# ---------------------------------------------------------------------------
# Task 4 — Property-based tests for cost functions
# Uses the `hypothesis` library (pip install hypothesis)
# ---------------------------------------------------------------------------

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from integrated_cost_optimizer import compute_weekly_cost

# Strategies
_week_types = st.sampled_from(["Normal", "Partial", "Shutdown"])
_inv = st.integers(min_value=-100, max_value=200)
_good_prod = st.integers(min_value=0, max_value=45)
_rate = st.floats(min_value=0.0, max_value=50000.0, allow_nan=False, allow_infinity=False)
_weight = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)


def _params_with(
    w_penalty=1.0, w_overtime=1.0, w_capacity=0.0,
    penalty_rate=7000.0, late_penalty_multiplier=10.0,
    overtime_rate=2000.0, capacity_rate=0.0,
):
    """Helper to build IntegratedParams. Caller must ensure at least one weight is non-zero."""
    return IntegratedParams(
        w_penalty=w_penalty,
        w_overtime=w_overtime,
        w_capacity=w_capacity,
        penalty_rate=penalty_rate,
        late_penalty_multiplier=late_penalty_multiplier,
        overtime_rate=overtime_rate,
        capacity_rate=capacity_rate,
    )


# ---------------------------------------------------------------------------
# Property 1: Composite cost formula correctness
# Feature: integrated-cost-optimization, Property 1: Composite cost formula correctness
# Validates: Requirements 1.1
# ---------------------------------------------------------------------------

@settings(max_examples=200)
@given(
    inv=_inv,
    good_prod=_good_prod,
    week_type=_week_types,
    w_penalty=_weight,
    w_overtime=_weight,
    w_capacity=_weight,
    penalty_rate=_rate,
    late_penalty_multiplier=st.floats(min_value=1.0, max_value=20.0, allow_nan=False, allow_infinity=False),
    overtime_rate=_rate,
    capacity_rate=_rate,
)
def test_property1_composite_cost_formula(
    inv, good_prod, week_type,
    w_penalty, w_overtime, w_capacity,
    penalty_rate, late_penalty_multiplier, overtime_rate, capacity_rate,
):
    """
    Property 1: Composite cost formula correctness.
    For any inputs, composite = w_p * penalty + w_o * overtime + w_c * capacity.
    Validates: Requirements 1.1
    """
    # Skip the invalid all-zero-weights case (raises ValueError by design)
    assume(w_penalty > 0.0 or w_overtime > 0.0 or w_capacity > 0.0)

    params = _params_with(
        w_penalty=w_penalty, w_overtime=w_overtime, w_capacity=w_capacity,
        penalty_rate=penalty_rate, late_penalty_multiplier=late_penalty_multiplier,
        overtime_rate=overtime_rate, capacity_rate=capacity_rate,
    )

    # Compute each component independently
    if inv >= 0:
        expected_penalty = penalty_rate * inv
    else:
        expected_penalty = (penalty_rate * late_penalty_multiplier) * abs(inv)

    expected_overtime = overtime_rate if good_prod > params.normal_max_good_week else 0.0

    if week_type == "Shutdown":
        expected_capacity = 0.0
    elif week_type == "Partial":
        expected_capacity = capacity_rate * max(0, params.max_good_per_batch - good_prod)
    else:
        expected_capacity = capacity_rate * max(0, params.normal_max_good_week - good_prod)

    expected = (
        w_penalty * expected_penalty
        + w_overtime * expected_overtime
        + w_capacity * expected_capacity
    )

    result = compute_weekly_cost(inv, good_prod, week_type, params)
    assert abs(result - expected) < 1e-6, (
        f"Composite mismatch: got {result}, expected {expected}"
    )


# ---------------------------------------------------------------------------
# Property 2: Zero weight excludes component
# Feature: integrated-cost-optimization, Property 2: Zero weight excludes component
# Validates: Requirements 1.3
# ---------------------------------------------------------------------------

@settings(max_examples=200)
@given(
    inv=_inv,
    good_prod=_good_prod,
    week_type=_week_types,
    penalty_rate=_rate,
    overtime_rate=_rate,
    capacity_rate=_rate,
    late_penalty_multiplier=st.floats(min_value=1.0, max_value=20.0, allow_nan=False, allow_infinity=False),
    zero_component=st.sampled_from(["penalty", "overtime", "capacity"]),
)
def test_property2_zero_weight_excludes_component(
    inv, good_prod, week_type,
    penalty_rate, overtime_rate, capacity_rate,
    late_penalty_multiplier, zero_component,
):
    """
    Property 2: Zero weight excludes component.
    When a weight is 0.0, that component contributes exactly 0 to composite cost.
    Validates: Requirements 1.3
    """
    # Build params with one weight zeroed, others non-zero
    w_penalty = 0.0 if zero_component == "penalty" else 1.0
    w_overtime = 0.0 if zero_component == "overtime" else 1.0
    w_capacity = 0.0 if zero_component == "capacity" else 1.0

    params = _params_with(
        w_penalty=w_penalty, w_overtime=w_overtime, w_capacity=w_capacity,
        penalty_rate=penalty_rate, late_penalty_multiplier=late_penalty_multiplier,
        overtime_rate=overtime_rate, capacity_rate=capacity_rate,
    )

    # Build params with the same zero weight but zero rate for that component
    if zero_component == "penalty":
        params_zero_rate = _params_with(
            w_penalty=0.0, w_overtime=w_overtime, w_capacity=w_capacity,
            penalty_rate=0.0, late_penalty_multiplier=late_penalty_multiplier,
            overtime_rate=overtime_rate, capacity_rate=capacity_rate,
        )
    elif zero_component == "overtime":
        params_zero_rate = _params_with(
            w_penalty=w_penalty, w_overtime=0.0, w_capacity=w_capacity,
            penalty_rate=penalty_rate, late_penalty_multiplier=late_penalty_multiplier,
            overtime_rate=0.0, capacity_rate=capacity_rate,
        )
    else:
        params_zero_rate = _params_with(
            w_penalty=w_penalty, w_overtime=w_overtime, w_capacity=0.0,
            penalty_rate=penalty_rate, late_penalty_multiplier=late_penalty_multiplier,
            overtime_rate=overtime_rate, capacity_rate=0.0,
        )

    cost_with_zero_weight = compute_weekly_cost(inv, good_prod, week_type, params)
    cost_with_zero_rate = compute_weekly_cost(inv, good_prod, week_type, params_zero_rate)

    assert abs(cost_with_zero_weight - cost_with_zero_rate) < 1e-6, (
        f"Zero weight for '{zero_component}' should exclude that component. "
        f"Got {cost_with_zero_weight} vs {cost_with_zero_rate}"
    )


# ---------------------------------------------------------------------------
# Property 4: Penalty cost formula correctness
# Feature: integrated-cost-optimization, Property 4: Penalty cost formula correctness
# Validates: Requirements 2.1, 2.2
# ---------------------------------------------------------------------------

@settings(max_examples=200)
@given(
    inv=_inv,
    penalty_rate=_rate,
    late_penalty_multiplier=st.floats(min_value=1.0, max_value=20.0, allow_nan=False, allow_infinity=False),
)
def test_property4_penalty_cost_formula(inv, penalty_rate, late_penalty_multiplier):
    """
    Property 4: Penalty cost formula correctness.
    penalty = penalty_rate * inv when inv >= 0,
              late_penalty_rate * |inv| when inv < 0.
    Validates: Requirements 2.1, 2.2
    """
    # Use w_overtime=0, w_capacity=0 to isolate penalty
    params = _params_with(
        w_penalty=1.0, w_overtime=0.0, w_capacity=0.0,
        penalty_rate=penalty_rate, late_penalty_multiplier=late_penalty_multiplier,
        overtime_rate=0.0, capacity_rate=0.0,
    )

    result = compute_weekly_cost(inv, 0, "Normal", params)

    if inv >= 0:
        expected = penalty_rate * inv
    else:
        expected = (penalty_rate * late_penalty_multiplier) * abs(inv)

    assert abs(result - expected) < 1e-6, (
        f"Penalty mismatch for inv={inv}: got {result}, expected {expected}"
    )


# ---------------------------------------------------------------------------
# Property 5: Overtime cost formula correctness
# Feature: integrated-cost-optimization, Property 5: Overtime cost formula correctness
# Validates: Requirements 3.1, 3.4
# ---------------------------------------------------------------------------

@settings(max_examples=200)
@given(
    good_prod=_good_prod,
    overtime_rate=_rate,
)
def test_property5_overtime_cost_formula(good_prod, overtime_rate):
    """
    Property 5: Overtime cost formula correctness.
    overtime = overtime_rate if good_prod > 30, else 0.
    Validates: Requirements 3.1, 3.4
    """
    # Use w_penalty=0, w_capacity=0 to isolate overtime
    params = _params_with(
        w_penalty=0.0, w_overtime=1.0, w_capacity=0.0,
        penalty_rate=0.0, overtime_rate=overtime_rate, capacity_rate=0.0,
    )

    result = compute_weekly_cost(0, good_prod, "Normal", params)

    expected = overtime_rate if good_prod > params.normal_max_good_week else 0.0

    assert abs(result - expected) < 1e-6, (
        f"Overtime mismatch for good_prod={good_prod}: got {result}, expected {expected}"
    )


# ---------------------------------------------------------------------------
# Property 6: Capacity utilization cost formula correctness
# Feature: integrated-cost-optimization, Property 6: Capacity utilization cost formula correctness
# Validates: Requirements 4.1, 4.4, 4.5, 4.6
# ---------------------------------------------------------------------------

@settings(max_examples=200)
@given(
    good_prod=_good_prod,
    week_type=_week_types,
    capacity_rate=_rate,
)
def test_property6_capacity_cost_formula(good_prod, week_type, capacity_rate):
    """
    Property 6: Capacity utilization cost formula correctness.
    capacity = capacity_rate * max(0, ceiling - good_prod)
    where ceiling = 30 (Normal), 15 (Partial), 0 (Shutdown).
    Validates: Requirements 4.1, 4.4, 4.5, 4.6
    """
    # Use w_penalty=0, w_overtime=0 to isolate capacity
    params = _params_with(
        w_penalty=0.0, w_overtime=0.0, w_capacity=1.0,
        penalty_rate=0.0, overtime_rate=0.0, capacity_rate=capacity_rate,
    )

    result = compute_weekly_cost(0, good_prod, week_type, params)

    if week_type == "Shutdown":
        expected = 0.0
    elif week_type == "Partial":
        expected = capacity_rate * max(0, params.max_good_per_batch - good_prod)
    else:
        expected = capacity_rate * max(0, params.normal_max_good_week - good_prod)

    assert abs(result - expected) < 1e-6, (
        f"Capacity mismatch for good_prod={good_prod}, week_type={week_type}: "
        f"got {result}, expected {expected}"
    )


# ---------------------------------------------------------------------------
# Task 6 — Property-based tests for DP solver
# ---------------------------------------------------------------------------

from integrated_cost_optimizer import (
    compute_inventory_bounds,
    solve_plan_integrated,
)


def _make_simple_demand(total: int, horizon: int = 52) -> list:
    """Spread `total` units of demand evenly across the horizon."""
    demand = [0] * (horizon + 1)
    per_week = total // horizon
    remainder = total % horizon
    for t in range(1, horizon + 1):
        demand[t] = per_week + (1 if t <= remainder else 0)
    return demand


def _run_solver(demand, shutdown_weeks=None, partial_weeks=None, row_demand=None, params=None):
    """Helper to run the solver with sensible defaults."""
    if params is None:
        params = IntegratedParams()
    if shutdown_weeks is None:
        shutdown_weeks = []
    if partial_weeks is None:
        partial_weeks = []
    if row_demand is None:
        row_demand = [0] * (params.horizon_weeks + 1)
    return solve_plan_integrated(demand, shutdown_weeks, partial_weeks, row_demand, params.row_cap, params)


# ---------------------------------------------------------------------------
# Property 7: Production constraints satisfied
# Feature: integrated-cost-optimization, Property 7: Production constraints satisfied
# Validates: Requirements 6.1, 6.2, 6.3, 6.4, 6.5, 6.6
# ---------------------------------------------------------------------------

@settings(max_examples=100)
@given(
    demand_batches=st.integers(min_value=1, max_value=13),
    n_shutdown=st.integers(min_value=0, max_value=5),
    n_partial=st.integers(min_value=0, max_value=5),
)
def test_property7_production_constraints_satisfied(demand_batches, n_shutdown, n_partial):
    """
    Property 7: Production constraints satisfied.
    For any week in the output plan, good_units must be within [0, cap_max[t]].
    Shutdown weeks produce 0, partial weeks produce <= 15, normal weeks produce <= 45.
    Validates: Requirements 6.1, 6.2, 6.3, 6.4, 6.5, 6.6
    """
    import random
    total_demand = demand_batches * 15  # multiples of 15 so terminal inv=0 is achievable
    rng = random.Random(total_demand + n_shutdown * 100 + n_partial * 10)

    # Pick non-overlapping shutdown and partial weeks
    all_weeks = list(range(1, 53))
    rng.shuffle(all_weeks)
    shutdown_weeks = sorted(all_weeks[:n_shutdown])
    partial_weeks = sorted(all_weeks[n_shutdown:n_shutdown + n_partial])

    # Compute available capacity to ensure feasibility
    shutdown_set = set(shutdown_weeks)
    partial_set = set(partial_weeks)
    cap_per_week = []
    for t in range(1, 53):
        if t in shutdown_set:
            cap_per_week.append(0)
        elif t in partial_set:
            cap_per_week.append(15)
        else:
            cap_per_week.append(45)
    total_cap = sum(cap_per_week)

    # Skip infeasible scenarios
    assume(total_demand <= total_cap)

    demand = _make_simple_demand(total_demand)
    params = IntegratedParams(w_penalty=1.0, w_overtime=1.0, w_capacity=0.0)

    plan_df, _ = _run_solver(demand, shutdown_weeks, partial_weeks, params=params)

    shutdown_set = set(shutdown_weeks)
    partial_set = set(partial_weeks)

    for _, row in plan_df.iterrows():
        t = int(row["Week"])
        y = int(row["Good_Production"])
        wt = row["Week_Type"]

        if t in shutdown_set:
            assert y == 0, f"Week {t} is Shutdown but produced {y} units"
            assert wt == "Shutdown"
        elif t in partial_set:
            assert 0 <= y <= 15, f"Week {t} is Partial but produced {y} units"
            assert wt == "Partial"
        else:
            assert 0 <= y <= 45, f"Week {t} is Normal but produced {y} units"
            assert wt == "Normal"


# ---------------------------------------------------------------------------
# Property 8: Terminal inventory constraint
# Feature: integrated-cost-optimization, Property 8: Terminal inventory constraint
# Validates: Requirements 5.5
# ---------------------------------------------------------------------------

@settings(max_examples=100)
@given(
    total_demand=st.integers(min_value=1, max_value=200),
)
def test_property8_terminal_inventory_zero(total_demand):
    """
    Property 8: Terminal inventory constraint.
    For any valid output plan, Net_Inventory_End at week 52 must equal exactly 0.
    Validates: Requirements 5.5
    """
    demand = _make_simple_demand(total_demand)
    params = IntegratedParams(w_penalty=1.0, w_overtime=1.0, w_capacity=0.0)

    plan_df, _ = _run_solver(demand, params=params)

    last_row = plan_df[plan_df["Week"] == 52].iloc[0]
    assert last_row["Net_Inventory_End"] == 0, (
        f"Terminal inventory is {last_row['Net_Inventory_End']}, expected 0"
    )


# ---------------------------------------------------------------------------
# Property 9: No backlog when early production is feasible
# Feature: integrated-cost-optimization, Property 9: No backlog when early production is feasible
# Validates: Requirements 5.6, 2.3
# ---------------------------------------------------------------------------

@settings(max_examples=100)
@given(
    demand_batches=st.integers(min_value=1, max_value=10),
)
def test_property9_no_backlog_when_feasible(demand_batches):
    """
    Property 9: No backlog when early production is feasible.
    When all demand is concentrated at week 52, the optimizer can always produce
    ahead without any holding cost disadvantage, so no backlog should occur.
    Validates: Requirements 5.6, 2.3
    """
    # Put all demand in week 52 — the optimizer can always produce ahead
    # with no holding cost pressure forcing backlog
    total_demand = demand_batches * 15
    demand = [0] * 53
    demand[52] = total_demand  # all demand due at week 52

    params = IntegratedParams(w_penalty=1.0, w_overtime=1.0, w_capacity=0.0)

    plan_df, _ = _run_solver(demand, params=params)

    # With all demand at week 52 and ample capacity, no week should have backlog
    backlog_weeks = plan_df[plan_df["Net_Inventory_End"] < 0]
    assert len(backlog_weeks) == 0, (
        f"Expected no backlog but found {len(backlog_weeks)} weeks with backlog: "
        f"{backlog_weeks[['Week', 'Net_Inventory_End']].to_dict('records')}"
    )



# ---------------------------------------------------------------------------
# Task 8 — Batch utilities unit tests (Requirement 6.6)
# ---------------------------------------------------------------------------

from integrated_cost_optimizer import batches_needed, split_good_into_batches


def test_batches_needed_zero():
    assert batches_needed(0, DEFAULT_PARAMS) == 0


def test_batches_needed_one_batch():
    assert batches_needed(15, DEFAULT_PARAMS) == 1


def test_batches_needed_one_batch_partial():
    """Any value 1..15 requires exactly 1 batch."""
    assert batches_needed(7, DEFAULT_PARAMS) == 1


def test_batches_needed_two_batches():
    assert batches_needed(30, DEFAULT_PARAMS) == 2


def test_batches_needed_two_batches_partial():
    """Any value 16..30 requires exactly 2 batches."""
    assert batches_needed(22, DEFAULT_PARAMS) == 2


def test_batches_needed_three_batches():
    assert batches_needed(45, DEFAULT_PARAMS) == 3


def test_batches_needed_three_batches_partial():
    """Any value 31..45 requires exactly 3 batches."""
    assert batches_needed(36, DEFAULT_PARAMS) == 3


def test_batches_needed_invalid_raises():
    with pytest.raises(ValueError):
        batches_needed(-1, DEFAULT_PARAMS)


def test_split_good_into_batches_zero():
    assert split_good_into_batches(0, DEFAULT_PARAMS) == []


def test_split_good_into_batches_two():
    result = split_good_into_batches(30, DEFAULT_PARAMS)
    assert result == [15, 15]


def test_split_good_into_batches_three():
    result = split_good_into_batches(45, DEFAULT_PARAMS)
    assert result == [15, 15, 15]


def test_split_good_into_batches_partial():
    """22 good units → [15, 7]."""
    result = split_good_into_batches(22, DEFAULT_PARAMS)
    assert result == [15, 7]
    assert sum(result) == 22


# ---------------------------------------------------------------------------
# Property 10: Output contains all required cost columns
# Feature: integrated-cost-optimization, Property 10: Output contains all required cost columns
# Validates: Requirements 9.3
# ---------------------------------------------------------------------------

REQUIRED_COST_COLUMNS = [
    "Penalty_Cost_USD",
    "Overtime_Cost_USD",
    "Capacity_Utilization_Cost_USD",
    "Composite_Cost_USD",
    "Cumulative_Composite_Cost_USD",
]

REQUIRED_PLAN_COLUMNS = [
    "Week", "Week_Type", "Demand_Due", "Good_Production",
    "Batch_Count", "Batch1_Produced", "Batch2_Produced", "Batch3_Produced",
    "Produced_Total", "Testing_Discard", "Overtime_Used", "Net_Inventory_End",
    "Early_Units_Held", "Late_Units_Backlog",
    "ROW_Demand_Due", "ROW_Fulfilled", "ROW_Inventory",
]


@settings(max_examples=100)
@given(
    demand_batches=st.integers(min_value=1, max_value=13),
    w_penalty=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
    w_overtime=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
    w_capacity=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
)
def test_property10_output_contains_required_cost_columns(
    demand_batches, w_penalty, w_overtime, w_capacity
):
    """
    Property 10: Output contains all required cost columns.
    For any output plan DataFrame, all required columns must be present including
    Penalty_Cost_USD, Overtime_Cost_USD, Capacity_Utilization_Cost_USD,
    Composite_Cost_USD, Cumulative_Composite_Cost_USD.
    Validates: Requirements 9.3
    """
    # Ensure at least one weight is non-zero
    assume(w_penalty > 0.0 or w_overtime > 0.0 or w_capacity > 0.0)

    total_demand = demand_batches * 15
    demand = _make_simple_demand(total_demand)
    row_demand = [0] * (DEFAULT_PARAMS.horizon_weeks + 1)

    params = IntegratedParams(
        w_penalty=w_penalty,
        w_overtime=w_overtime,
        w_capacity=w_capacity,
    )

    plan_df, _ = solve_plan_integrated(
        demand, [], [], row_demand, params.row_cap, params
    )

    for col in REQUIRED_COST_COLUMNS + REQUIRED_PLAN_COLUMNS:
        assert col in plan_df.columns, f"Missing required column: {col}"


# ---------------------------------------------------------------------------
# Property 3: Weight validation rejects out-of-range values
# Feature: integrated-cost-optimization, Property 3: Weight validation rejects out-of-range values
# Validates: Requirements 1.5, 1.6
# ---------------------------------------------------------------------------

from integrated_cost_optimizer import _validate_weights

# Strategy: floats strictly outside [0.0, 1.0]
_out_of_range_weight = st.one_of(
    st.floats(max_value=-0.0001, allow_nan=False, allow_infinity=False),
    st.floats(min_value=1.0001, allow_nan=False, allow_infinity=False),
)
_valid_weight = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)


@settings(max_examples=200)
@given(
    bad_weight=_out_of_range_weight,
    which=st.sampled_from(["penalty", "overtime", "capacity"]),
    other1=_valid_weight,
    other2=_valid_weight,
)
def test_property3_weight_validation_rejects_out_of_range(bad_weight, which, other1, other2):
    """
    Property 3: Weight validation rejects out-of-range values.
    For any weight value outside [0.0, 1.0], _validate_weights must raise ValueError.
    Validates: Requirements 1.5, 1.6
    """
    # Ensure the two "other" weights are not both zero (to avoid the all-zero error
    # masking the out-of-range error — we want to isolate the range check)
    assume(other1 > 0.0 or other2 > 0.0)

    if which == "penalty":
        w_p, w_o, w_c = bad_weight, other1, other2
    elif which == "overtime":
        w_p, w_o, w_c = other1, bad_weight, other2
    else:
        w_p, w_o, w_c = other1, other2, bad_weight

    with pytest.raises(ValueError):
        _validate_weights(w_p, w_o, w_c)


# ---------------------------------------------------------------------------
# Task 10 — Integration tests: verify different weight scenarios produce
# different plans when run against sites_clean.csv
# ---------------------------------------------------------------------------

from integrated_cost_optimizer import export_excel


def _run_full(sites_csv, w_penalty, w_overtime, w_capacity, capacity_rate=100.0):
    """Run the full pipeline against the synthetic sites file with given weights."""
    params = IntegratedParams(
        w_penalty=w_penalty,
        w_overtime=w_overtime,
        w_capacity=w_capacity,
        capacity_rate=capacity_rate,
    )
    raw_df = read_sites(sites_csv)
    active_df, _ = clean_sites(raw_df, params)
    demand = build_weekly_demand(active_df, params)
    row_demand = build_weekly_row_demand(active_df, params)
    plan_df, summary = solve_plan_integrated(
        demand, [], [], row_demand, params.row_cap, params
    )
    return plan_df, summary


def test_integration_penalty_only_runs(sites_csv):
    """Penalty-only scenario (w_penalty=1, w_overtime=0, w_capacity=0) completes without error."""
    plan_df, summary = _run_full(sites_csv, w_penalty=1.0, w_overtime=0.0, w_capacity=0.0)
    assert len(plan_df) == 52
    assert plan_df.iloc[-1]["Net_Inventory_End"] == 0
    assert summary["total_composite_cost"] >= 0


def test_integration_overtime_only_runs(sites_csv):
    """Overtime-only scenario (w_penalty=0, w_overtime=1, w_capacity=0) completes without error."""
    plan_df, summary = _run_full(sites_csv, w_penalty=0.0, w_overtime=1.0, w_capacity=0.0)
    assert len(plan_df) == 52
    assert plan_df.iloc[-1]["Net_Inventory_End"] == 0
    assert summary["total_composite_cost"] >= 0


def test_integration_balanced_runs(sites_csv):
    """Balanced scenario (w_penalty=1, w_overtime=1, w_capacity=1) completes without error."""
    plan_df, summary = _run_full(sites_csv, w_penalty=1.0, w_overtime=1.0, w_capacity=1.0)
    assert len(plan_df) == 52
    assert plan_df.iloc[-1]["Net_Inventory_End"] == 0
    assert summary["total_composite_cost"] >= 0


def test_integration_scenarios_produce_different_plans(sites_csv):
    """Penalty-only, overtime-only, and balanced scenarios produce different production plans."""
    plan_penalty, _ = _run_full(sites_csv, w_penalty=1.0, w_overtime=0.0, w_capacity=0.0)
    plan_overtime, _ = _run_full(sites_csv, w_penalty=0.0, w_overtime=1.0, w_capacity=0.0)
    plan_balanced, _ = _run_full(sites_csv, w_penalty=1.0, w_overtime=1.0, w_capacity=1.0)

    prod_penalty = list(plan_penalty["Good_Production"])
    prod_overtime = list(plan_overtime["Good_Production"])
    prod_balanced = list(plan_balanced["Good_Production"])

    # At least two of the three scenarios must differ
    all_same = (prod_penalty == prod_overtime == prod_balanced)
    assert not all_same, (
        "All three weight scenarios produced identical production plans — "
        "expected at least two to differ."
    )


def test_integration_all_required_columns_present(sites_csv):
    """Output plan contains all required columns."""
    plan_df, _ = _run_full(sites_csv, w_penalty=1.0, w_overtime=1.0, w_capacity=0.0)
    for col in REQUIRED_COST_COLUMNS + REQUIRED_PLAN_COLUMNS:
        assert col in plan_df.columns, f"Missing column: {col}"


def test_integration_output_excel(tmp_path, sites_csv):
    """export_excel writes a valid Excel file with all required sheets."""
    params = IntegratedParams(w_penalty=1.0, w_overtime=1.0, w_capacity=0.0)
    raw_df = read_sites(sites_csv)
    active_df, issues_df = clean_sites(raw_df, params)
    demand = build_weekly_demand(active_df, params)
    row_demand = build_weekly_row_demand(active_df, params)
    plan_df, summary = solve_plan_integrated(
        demand, [], [], row_demand, params.row_cap, params
    )

    out_path = str(tmp_path / "test_output.xlsx")
    export_excel(out_path, plan_df, active_df, issues_df, params, summary)

    xl = pd.ExcelFile(out_path)
    assert "Weekly_Plan" in xl.sheet_names
    assert "Sites_Clean" in xl.sheet_names
    assert "Input_Issues" in xl.sheet_names
    assert "Model_Params" in xl.sheet_names
