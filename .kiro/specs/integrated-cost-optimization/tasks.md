# Implementation Plan: Integrated Cost Optimization Model

## Overview

Build `integrated_cost_optimizer.py` as a standalone script implementing the weighted
composite cost DP optimizer. Tasks are ordered to build incrementally — core data
structures first, then cost logic, then DP solver, then output, then CLI wiring.

## Tasks

- [x] 1. Set up project structure and core data classes
  - Create `integrated_cost_optimizer.py` with `IntegratedParams` dataclass
  - Include all cost rates, weights, and derived properties (late_penalty_rate, normal_max_good_week, etc.)
  - Add weight validation: raise ValueError if any weight outside [0,1] or all weights are 0
  - _Requirements: 1.4, 1.5, 1.6, 10.1_

- [x] 2. Implement input handling (reuse from existing planner)
  - [x] 2.1 Implement `read_sites` and `clean_sites` functions
    - Accept .xlsx and .csv, normalize column names
    - Handle optional `country` column, default to empty if absent
    - Validate required columns, report issues for duplicates and out-of-range weeks
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7_

  - [x] 2.2 Write unit test for input validation
    - Test missing required columns raises error
    - Test inactive sites are excluded
    - Test duplicate Site_IDs reported as issues
    - _Requirements: 8.4, 8.5, 8.6_

- [x] 3. Implement demand building
  - [x] 3.1 Implement `build_weekly_demand` and `build_weekly_row_demand`
    - Expand each active site's recurring demand across 52 weeks
    - ROW demand array tracks only Denmark/UK/Netherlands/Sweden sites
    - _Requirements: 7.1, 7.5_

- [x] 4. Implement cost functions
  - [x] 4.1 Implement `compute_weekly_cost(inv_end, good_prod, week_type, params)` → float
    - Penalty: `penalty_rate × inv` if inv >= 0, else `late_penalty_rate × |inv|`
    - Overtime: `overtime_rate` if good_prod > 30, else 0
    - Capacity: `capacity_rate × max(0, ceiling - good_prod)` where ceiling depends on week_type
    - Return weighted composite: `w_p × penalty + w_o × overtime + w_c × capacity`
    - _Requirements: 1.1, 2.1, 2.2, 3.1, 3.4, 4.1, 4.4, 4.5, 4.6_

  - [x] 4.2 Write property test for composite cost formula (Property 1)
    - **Property 1: Composite cost formula correctness**
    - **Validates: Requirements 1.1**

  - [x] 4.3 Write property test for zero weight exclusion (Property 2)
    - **Property 2: Zero weight excludes component**
    - **Validates: Requirements 1.3**

  - [x] 4.4 Write property test for penalty cost formula (Property 4)
    - **Property 4: Penalty cost formula correctness**
    - **Validates: Requirements 2.1, 2.2**

  - [x] 4.5 Write property test for overtime cost formula (Property 5)
    - **Property 5: Overtime cost formula correctness**
    - **Validates: Requirements 3.1, 3.4**

  - [x] 4.6 Write property test for capacity utilization cost formula (Property 6)
    - **Property 6: Capacity utilization cost formula correctness**
    - **Validates: Requirements 4.1, 4.4, 4.5, 4.6**

- [x] 5. Checkpoint — Ensure all cost function tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 6. Implement DP solver
  - [x] 6.1 Implement `compute_inventory_bounds` with backlog support
    - lb[t] can be negative (backlog allowed as last resort)
    - ub[t] = remaining demand (no point holding more)
    - _Requirements: 5.1, 5.6_

  - [x] 6.2 Implement `solve_plan_integrated` DP forward pass
    - State: net inventory (integer, can be negative)
    - DP value: (composite_cost, overtime_weeks, total_batches) tuple for tie-breaking
    - Apply cap_max per week: 0 (shutdown), 15 (partial), 45 (normal)
    - Track ROW fulfillment and inventory separately
    - Raise RuntimeError if no feasible states at any week
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.7, 6.1, 6.2, 6.3, 6.4, 6.5, 7.2, 7.3_

  - [x] 6.3 Implement backward reconstruction and plan DataFrame builder
    - Reconstruct y[t] and inv[t] from prev pointers
    - Build rows with all required columns including cost breakdown per week
    - Compute Cumulative_Composite_Cost_USD running total
    - _Requirements: 9.1, 9.2, 9.3_

  - [x] 6.4 Write property test for production constraints (Property 7)
    - **Property 7: Production constraints satisfied**
    - **Validates: Requirements 6.1, 6.2, 6.3, 6.4, 6.5, 6.6**

  - [x] 6.5 Write property test for terminal inventory constraint (Property 8)
    - **Property 8: Terminal inventory constraint**
    - **Validates: Requirements 5.5**

  - [x] 6.6 Write property test for no backlog when feasible (Property 9)
    - **Property 9: No backlog when early production is feasible**
    - **Validates: Requirements 5.6, 2.3**

- [x] 7. Checkpoint — Ensure all solver tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 8. Implement batch utilities and output
  - [x] 8.1 Implement `batches_needed` and `split_good_into_batches`
    - Batch sizes 2–16, 1 test discard per batch
    - _Requirements: 6.6_

  - [x] 8.2 Implement `export_excel`
    - Write sheets: Weekly_Plan, Sites_Clean, Input_Issues, Model_Params
    - Model_Params sheet includes all weights and rates used
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6_

  - [x] 8.3 Write property test for output columns (Property 10)
    - **Property 10: Output contains all required cost columns**
    - **Validates: Requirements 9.3**

- [x] 9. Implement CLI and wire everything together
  - [x] 9.1 Implement `main()` with argparse
    - All CLI parameters from Requirement 10.1
    - Validate weights before running solver
    - Pass all params through to solver and export
    - Print summary when --print-summary flag set
    - _Requirements: 10.1, 10.2, 1.4, 9.7_

  - [x] 9.2 Write property test for weight validation (Property 3)
    - **Property 3: Weight validation rejects out-of-range values**
    - **Validates: Requirements 1.5, 1.6**

- [x] 10. Final checkpoint — Run full integration test
  - Run script against `sites_clean.csv` with various weight combinations
  - Verify penalty-only, overtime-only, and balanced scenarios produce different plans
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- All tasks including tests are required
- Property tests use the `hypothesis` library (install with `pip install hypothesis`)
- Each property test runs minimum 100 iterations
- The script is fully standalone — no imports from `production_planner_penalty_max16.py`
- Input files compatible with existing planner (same CSV/Excel format)
