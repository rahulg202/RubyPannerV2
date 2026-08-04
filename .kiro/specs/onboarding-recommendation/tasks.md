# Implementation Plan: Onboarding Recommendation Engine

## Overview

Implement the onboarding recommendation engine as a pure-logic Python module (`onboarding_recommendation.py`) with LP-based optimization for three cost objectives, then integrate it into the existing Streamlit app as a new tab. Property-based tests validate all 12 correctness properties from the design.

## Tasks

- [x] 1. Create `onboarding_recommendation.py` with input validation and candidate enumeration
  - [x] 1.1 Create `onboarding_recommendation.py` with `validate_onboarding_inputs()` and `enumerate_candidates()`
    - Import `IntegratedParams`, `batches_needed`, `split_good_into_batches` from `integrated_cost_optimizer`
    - Implement `validate_onboarding_inputs(total_generators, start_week, end_week) -> list[str]` returning error strings for invalid inputs (total_generators < 1, start_week < 1, start_week >= end_week)
    - Implement `enumerate_candidates(start_week, end_week) -> list[int]` returning `list(range(start_week, end_week + 1))`
    - _Requirements: 1.2, 1.3, 1.4, 2.1_

  - [x] 1.2 Write property tests for input validation (Properties 1–3)
    - **Property 1: Invalid inputs are rejected**
    - **Validates: Requirements 1.2, 1.3**
    - **Property 2: Valid inputs produce no errors**
    - **Validates: Requirements 1.4**
    - **Property 3: Candidate enumeration is complete and correct**
    - **Validates: Requirements 2.1**

- [x] 2. Implement LP solver and multi-objective evaluation
  - [x] 2.1 Implement `solve_single_objective_lp()`
    - Formulate PuLP LP with decision variables `y[t]` (continuous, 0 to overtime_max_good_week)
    - Add constraint: `sum(y[t]) == total_generators`
    - Implement penalty objective: minimize `penalty_rate * sum(cumulative_inventory)`
    - Implement overtime objective: minimize `overtime_rate * sum(ot[t])` with binary `ot[t]` variables
    - Implement capacity objective: minimize `capacity_rate * sum(unused[t])` with auxiliary variables
    - Return `None` if infeasible, otherwise return result dict with `candidate_start_week`, `objective`, `cost`, `weekly_production`
    - _Requirements: 2.2, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8_

  - [x] 2.2 Implement `evaluate_all_candidates()`
    - Iterate over all candidate start weeks from `enumerate_candidates()`
    - For each candidate, call `solve_single_objective_lp()` for each of the 3 objectives
    - Exclude infeasible candidates (log warning via `warnings.warn()`)
    - Return dict with keys `"penalty"`, `"overtime"`, `"capacity"`, each mapping to a list of result dicts
    - _Requirements: 2.1, 2.2, 3.1, 3.8_

  - [x] 2.3 Write property tests for LP solver (Properties 4–7)
    - **Property 4: LP solutions satisfy production constraints**
    - **Validates: Requirements 2.2, 3.3, 3.4**
    - **Property 5: All three objectives are evaluated**
    - **Validates: Requirements 3.1**
    - **Property 6: Reported cost matches independent computation**
    - **Validates: Requirements 3.5, 3.6, 3.7**
    - **Property 7: Infeasible candidates are excluded**
    - **Validates: Requirements 3.8**

- [x] 3. Checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 4. Implement ranking, horizon plan, batch metrics, and formatting
  - [x] 4.1 Implement `rank_and_select_top5()`
    - Sort each objective's result list by ascending `cost`
    - Return top 5 (or fewer if less than 5 feasible)
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5_

  - [x] 4.2 Implement `build_horizon_plan_df()`
    - Build a DataFrame with columns: `Week`, `Good_Units_Produced`, `Batch_Count`, `Cumulative_Production`, `Cumulative_Inventory`
    - One row per week from `start_week` to `end_week`; weeks before candidate start show 0
    - Use `batches_needed()` for batch count, `split_good_into_batches()` as needed
    - _Requirements: 5.3, 5.4, 5.5_

  - [x] 4.3 Implement `compute_batch_metrics()` and `format_cost_thousands()`
    - `compute_batch_metrics(plan_df, params)` returns `{"weeks_1_batch": int, "weeks_2_batch": int, "weeks_3_batch": int}`
    - `format_cost_thousands(cost)` returns `"$<integer>K"` string (e.g., `28000.0` → `"$28K"`)
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7_

  - [x] 4.4 Write property tests for ranking and display helpers (Properties 8–11)
    - **Property 8: Rankings are sorted ascending by cost and capped at 5**
    - **Validates: Requirements 4.1, 4.2, 4.3, 4.4, 4.5**
    - **Property 9: Horizon plan DataFrame structure and zero-fill**
    - **Validates: Requirements 5.3, 5.5**
    - **Property 10: Batch metrics match independent computation**
    - **Validates: Requirements 6.1, 6.2, 6.3**
    - **Property 11: Cost formatting produces correct $XK string**
    - **Validates: Requirements 6.4, 6.5, 6.6**

- [x] 5. Implement Excel export
  - [x] 5.1 Implement `export_recommendation_excel()`
    - Write in-memory Excel workbook using openpyxl via pandas ExcelWriter
    - One sheet per objective (`"Penalty"`, `"Overtime"`, `"Capacity"`) with top 5 horizon plans
    - A `"Summary"` sheet with all three objectives' top 5 showing batch metrics and cost side-by-side
    - Return workbook as bytes
    - _Requirements: 8.1, 8.2, 8.3_

  - [x] 5.2 Write property test for Excel export (Property 12)
    - **Property 12: Export produces valid Excel with correct structure**
    - **Validates: Requirements 8.2, 8.3**

- [x] 6. Checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 7. Integrate into Streamlit UI
  - [x] 7.1 Add tab structure to `app.py` and wire onboarding recommendation tab
    - Wrap existing optimizer UI inside `tab_optimizer` using `st.tabs(["Cost Optimizer", "Onboarding Recommendation"])`
    - Under `tab_onboarding`, add input widgets for total generators, start week, end week
    - Add "Run Recommendation" button that calls `validate_onboarding_inputs()`, then `evaluate_all_candidates()`, `rank_and_select_top5()`
    - Display results in three sub-tabs: "Top 5 by Penalty", "Top 5 by Overtime", "Top 5 by Capacity Utilisation"
    - Each sub-tab shows up to 5 columns with horizon plan table and batch metrics via `build_horizon_plan_df()` and `compute_batch_metrics()`
    - Add download button calling `export_recommendation_excel()`
    - _Requirements: 1.1, 5.1, 5.2, 5.3, 5.4, 5.5, 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 7.1, 7.2, 7.3, 7.4, 8.1_

  - [x] 7.2 Write unit tests for onboarding module (`tests/test_onboarding.py`)
    - Test specific examples: e.g., 30 generators from week 1–10 with known feasible results
    - Test edge cases: 1 generator (trivial), generators exactly filling capacity, start_week == 1
    - Verify `batches_needed()` integration within `compute_batch_metrics()`
    - Verify Excel export sheet contents for a known small input
    - _Requirements: 2.2, 3.1, 6.7, 8.2_

- [x] 8. Final checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document
- Unit tests validate specific examples and edge cases
- The engine module (`onboarding_recommendation.py`) has no Streamlit dependency, making it independently testable
