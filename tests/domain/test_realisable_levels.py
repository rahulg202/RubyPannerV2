"""R7 — ``min_batch_produced`` must be enforced or removed.

The parameter is exposed in Settings and written into the exported
``Model_Params`` sheet as though it constrained the plan. It did not: the solver
enumerated every integer in ``0..cap_max``. With ``min_batch_produced = 16``
(15 good units minimum per batch) it still planned a one-unit batch, while the
reproducibility sheet asserted a constraint that was never imposed.

See `.kiro/specs/correctness-remediation/requirements.md` R7.
"""

from __future__ import annotations

import pytest

from domain.demand import batches_needed, realisable_good_levels
from domain.params import IntegratedParams
from domain.solver import solve_plan_integrated


def _demand(horizon: int, spec: dict[int, int]) -> list[int]:
    demand = [0] * (horizon + 1)
    for week, quantity in spec.items():
        demand[week] = quantity
    return demand


# ---------------------------------------------------------------------------
# AC7.4 / AC7.2 — defaults admit everything, so nothing moves
# ---------------------------------------------------------------------------

def test_default_params_admit_every_integer_level():
    params = IntegratedParams()          # min 2 produced -> 1 good unit minimum
    levels = realisable_good_levels(params, params.overtime_max_good_week)
    assert levels == list(range(0, params.overtime_max_good_week + 1))


def test_levels_are_capped_by_the_week_ceiling():
    params = IntegratedParams()
    assert realisable_good_levels(params, 0) == [0]
    assert realisable_good_levels(params, 15) == list(range(0, 16))


# ---------------------------------------------------------------------------
# AC7.1 — a real minimum genuinely restricts the plan
# ---------------------------------------------------------------------------

def test_full_batch_minimum_admits_only_whole_batches():
    params = IntegratedParams(min_batch_produced=16)   # 15 good units minimum
    levels = realisable_good_levels(params, params.overtime_max_good_week)
    assert levels == [0, 15, 30, 45]


def test_solver_respects_a_full_batch_minimum():
    """The original defect: a single-unit batch under a 15-unit minimum."""
    params = IntegratedParams(horizon_weeks=8, min_batch_produced=16)
    demand = _demand(8, {5: 15})
    plan, _summary = solve_plan_integrated(
        demand, [], [], [0] * 9, params.row_cap, params
    )
    produced = {int(v) for v in plan["Good_Production"]}
    assert produced <= {0, 15, 30, 45}, (
        f"solver planned an unrealisable batch size: {sorted(produced)}"
    )


def test_partial_minimum_admits_the_expected_windows():
    """min 8 produced -> 7 good per batch, so 7..15, 14..30, 21..45."""
    params = IntegratedParams(min_batch_produced=8)
    levels = set(realisable_good_levels(params, 45))
    assert 0 in levels
    assert 6 not in levels          # below one batch's minimum
    assert 7 in levels
    assert 15 in levels
    assert 16 in levels             # two batches: 14..30
    assert levels == {0} | set(range(7, 16)) | set(range(14, 31)) | set(range(21, 46))


# ---------------------------------------------------------------------------
# R7.2 — batches_needed agrees with the level set
# ---------------------------------------------------------------------------

def test_batches_needed_returns_the_smallest_feasible_count():
    params = IntegratedParams()
    assert batches_needed(0, params) == 0
    assert batches_needed(1, params) == 1
    assert batches_needed(15, params) == 1
    assert batches_needed(16, params) == 2
    assert batches_needed(45, params) == 3


def test_batches_needed_rejects_an_unrealisable_quantity():
    params = IntegratedParams(min_batch_produced=16)
    with pytest.raises(ValueError):
        batches_needed(7, params)


def test_batches_needed_still_rejects_negatives():
    with pytest.raises(ValueError):
        batches_needed(-1, IntegratedParams())


def test_every_realisable_level_has_a_batch_count():
    params = IntegratedParams(min_batch_produced=8)
    for level in realisable_good_levels(params, 45):
        count = batches_needed(level, params)
        if level == 0:
            assert count == 0
        else:
            assert 1 <= count <= params.overtime_max_batches


# ---------------------------------------------------------------------------
# AC7.3 — incoherent batch settings are rejected up front
# ---------------------------------------------------------------------------

def test_min_above_max_is_rejected():
    with pytest.raises(ValueError, match="min_batch_produced"):
        IntegratedParams(min_batch_produced=20, max_batch_produced=16)


def test_discard_leaving_no_good_units_is_rejected():
    with pytest.raises(ValueError, match="test_discard_per_batch"):
        IntegratedParams(min_batch_produced=2, test_discard_per_batch=2)
