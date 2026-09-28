# Implementation Tasks — Correctness Remediation

> **Status: complete.** All phases delivered. 881 tests passing (from 742 before
> the remediation). Default-configuration output is unchanged and pinned by
> `tests/domain/test_pinned_defaults.py`.

Ordered bottom-up: pure domain first, then services, adapters, UI, docs. Each
phase ends in a green suite, so the work can be stopped at any phase boundary
without leaving the tree broken.

Requirement references are `[Rn.m]` against
[`requirements.md`](requirements.md).

---

## Phase 0 — Regression tests that fail first

Written before the fixes, so each defect is pinned by a test that goes red on the
current code. [R-P5]

- [x] 0.1 `tests/domain/test_capacity_decomposition.py` — floor + levelling identity;
      floor invariant across plans of equal demand [R1, AC1.1–AC1.3]
- [x] 0.2 `tests/domain/test_self_comparison.py` — a plan compared against itself
      saves exactly zero on every component and on the total [R2, AC2.1]
- [x] 0.3 `tests/domain/test_realisable_levels.py` — `min_batch_produced = 16`
      admits only `{0, 15, 30, 45}` [R7, AC7.1]
- [x] 0.4 `tests/io/test_reader_code_agreement.py` — converter and parser agree on
      codes for a duplicated account number [R4, AC4.1–AC4.3]
- [x] 0.5 `tests/domain/test_onboarding_ranking.py` — candidates with equal
      penalty and overtime deltas rank equal, not by start week [R5, AC5.1]
- [x] 0.6 `tests/services/test_settings_fingerprint.py` — fingerprint sensitivity
      and stale-result rejection [R6, AC6.1–AC6.2]
- [x] 0.7 `tests/domain/test_pinned_defaults.py` — default output for a fixed
      input, recorded before any change [P1, AC1.5, AC7.2]

## Phase 1 — Shared cost decomposition and realisable production

- [x] 1.1 Rewrite `domain/cost_model.py` around `WeeklyCost` and
      `decompose_weekly_cost`; keep `compute_weekly_cost` as a delegating shim
      [R1.1, R10.1]
- [x] 1.2 Add `capacity_baseline` to `IntegratedParams` with `"normal"` default;
      add `effective_overtime_deterrent` [R1.2, R1.5]
- [x] 1.3 Add batch validation to `IntegratedParams` [R7.3]
- [x] 1.4 Add `realisable_good_levels` and rewrite `batches_needed` in
      `domain/demand.py` [R7.1, R7.2]
- [x] 1.5 Point `solver._build_plan_df` at `decompose_weekly_cost`; delete the
      inline formula [R10.1]
- [x] 1.6 Point `comparison.compute_baseline_cost` at `decompose_weekly_cost`;
      delete the inline formula and the `assert` [R10.1]
- [x] 1.7 Surface floor/levelling totals in both summaries [R1.1]
- [x] **Checkpoint:** 0.1, 0.3, 0.7 green; full suite green.

## Phase 2 — Solver: one EU array, realisable levels, typed infeasibility

- [x] 2.1 Drop `row_demand` from `solve_plan_integrated` and `_build_plan_df`
      [R8.1]
- [x] 2.2 Replace the `ROW_*` plan columns with the four `EU_*` columns [R8.2, R8.3]
- [x] 2.3 Enumerate `realisable_good_levels` instead of `range` [R7.1]
- [x] 2.4 Raise `ShippingCapInfeasibleError` from `compute_inventory_bounds` when
      an EU lower bound exceeds its upper bound [R10.3]
- [x] 2.5 `prev = [{} for _ in range(T + 1)]` [R10.5]
- [x] 2.6 Add `StaleResultError` and `ShippingCapInfeasibleError` to
      `domain/errors.py` [R6, R10.3]
- [x] **Checkpoint:** AC8.3 invariant test green; full suite green.

## Phase 3 — Supplier allocation, quota, and the composite contract

- [x] 3.1 `DemandEvent.eu_restricted`; populate from `is_row` in
      `build_demand_events` [R9.1]
- [x] 3.2 `allocate_suppliers_weekly(..., eu_production=None)`; record
      `eu_restricted_production` on the allocation [R9.1, R9.3]
- [x] 3.3 Reorder `solve_with_suppliers` to solve → assign → derive
      `eu_production` → allocate; return the assignment records [R9.2]
- [x] 3.4 Stop folding quota into `total_composite_cost`; add
      `total_quota_penalty_cost` and `total_cost_with_quota` [R2.1, R2.3, AC2.3]
- [x] 3.5 Add `zero_allocation_warnings` and wire into the summary [R3.2, AC3.1]
- [x] 3.6 Label the quota component as advisory in the summary [R3.1]
- [x] **Checkpoint:** AC2.3, AC3.1 green; full suite green.

## Phase 4 — Comparison symmetry

- [x] 4.1 `compute_baseline_cost` gains `supplier_params`, `reference_week_date`,
      `demand_events`; computes the quota term for the manual plan by the same
      path [R2.2]
- [x] 4.2 Add the quota row to `COST_COMPONENTS`; total becomes
      `total_cost_with_quota` [R2.4]
- [x] 4.3 Add the reconciliation guard to `compare_plans` [P3, AC2.2]
- [x] **Checkpoint:** 0.2 green — self-comparison saves exactly zero.

## Phase 5 — Shared site codes

- [x] 5.1 New `domain/site_codes.py` with `CodeDeduplicator` and the code
      extractors [R4.1]
- [x] 5.2 `site_derivation` delegates to it; `deduplicate_codes` becomes a wrapper
      [R4.1]
- [x] 5.3 `master_planner_parser` assigns codes through the same module in sheet
      order, keying schedule and headers by the deduplicated code, and reports
      renames [R4.2, R4.3]
- [x] 5.4 `_is_schedule_mark` accepts any non-bool numeric equal to 1 [R10.4]
- [x] **Checkpoint:** 0.4 green — both readers agree.

## Phase 6 — Onboarding

- [x] 6.1 `CombinationResult.generators_added` and `delta_marginal` [R5.1, R5.2]
- [x] 6.2 Remove `"capacity"` from `OBJECTIVES`; rank on `delta_marginal` with the
      stated tie-break [R5.1, R5.3]
- [x] 6.3 `evaluate_against_fixed_plan` takes `eu_due` + `row_cap` and marks
      cap-breaching placements [R10.7]
- [x] 6.4 `overtime_weeks_added` becomes a derived property [R10.6]
- [x] **Checkpoint:** 0.5 green; full suite green.

## Phase 7 — Services and staleness

- [x] 7.1 `Settings.fingerprint` over `dataclasses.fields` [R6.1]
- [x] 7.2 Thread the fingerprint through `OptimizeRequest` → `OptimizationResult`
      → `ComparisonRequest` [R6.2]
- [x] 7.3 `ComparisonService.run` raises `StaleResultError` on mismatch [R6.4,
      AC6.2]
- [x] 7.4 `optimizer_service` consumes the records returned by
      `solve_with_suppliers` instead of recomputing the assignment [R9.2]
- [x] 7.5 `comparison_service` passes supplier params and demand events to the
      baseline [R2.2]
- [x] **Checkpoint:** 0.6 green; full suite green.

## Phase 8 — Adapters

- [x] 8.1 Exporter: new `EU_*` columns, quota advisory labelling, effective
      overtime deterrent in `Model_Params` [R1.5, R3.1, R8.3]
- [x] 8.2 `site_matching._suggest` excludes all matched codes [R10.2, AC10.2]
- [x] **Checkpoint:** full suite green.

## Phase 9 — UI

- [x] 9.1 Settings: effective overtime deterrent readout next to `overtime_rate`;
      `capacity_baseline` selector with its consequence stated [R1.2, R1.5]
- [x] 9.2 Optimizer: staleness banner; quota shown as advisory [R3.1, R6.3]
- [x] 9.3 Comparison: staleness block; quota component in the table [R2.4, R6.4]
- [x] 9.4 Onboarding: `generators_added` column and the horizon-edge caption
      [R5.4]
- [x] **Checkpoint:** real-Streamlit render test green.

## Phase 10 — Documentation

- [x] 10.1 `README.md` — composite contract, `EU_*` columns, capacity
      disclosure, duplicate-code behaviour, onboarding ranking [R10.8]
- [x] 10.2 `TOOL_LOGIC.md` — objective function section corrected; effective
      overtime deterrent documented; advisory quota [R10.8]
- [x] 10.3 `HANDOFF.md` — remediation summary, refreshed test count, revised
      open-items list [R10.8]
- [x] 10.4 `USER_GUIDE.md` — Settings and Comparison screens where behaviour
      changed [R10.8]
- [x] **Checkpoint:** full suite green; no doc claim contradicted by code.

---

## Definition of done

1. Full suite green, with at least one new test per requirement that fails on the
   pre-remediation commit.
2. Default-configuration output pinned and unchanged (Phase 0.7).
3. Every cost table published to a planner reconciles: parts sum to total, both
   sides (P3).
4. No parameter is exposed or exported that the code does not use (P4).
5. No business rule has two implementations (P2).
