# Unified Implementation Plan — Ruby Fill Optimizer

This is a **single coordinated build**. The layered refactor, the supplier-constraints feature, and the six optimizer-enhancement features all land in **one unified Streamlit application** with **one Settings tab** feeding every workflow and **one export** covering every output.

References:
- Architecture: `.kiro/specs/ARCHITECTURE.md`
- Supplier requirements/design: `.kiro/specs/supplier-constraints/{requirements,design}.md`
- Enhancement requirements/design: `.kiro/specs/optimizer-enhancements/{requirements,design}.md`

Requirement references use the prefix **[S-n.m]** for supplier-constraints and **[E-n.m]** for optimizer-enhancements.

## Ordering principle

Foundation first (non-breaking refactor into layers, tests stay green), then the shared configuration, then domain logic for every feature, then adapters and services, then the single unified UI, then the unified export, then end-to-end verification. The UI is built **once**, near the end, after all services exist — so there is one integrated interface rather than per-feature screens.

---

## Phase 0 — Foundation: package skeleton (non-breaking)

- [ ] 0.1 Create the layered package skeleton
  - Create empty packages `domain/`, `services/`, `io/`, `ui/`, each with `__init__.py`
  - Create `tests/domain/`, `tests/services/`, `tests/io/` directories
  - Add `pyproject.toml` with `[tool.pytest.ini_options] testpaths = ["tests"]` and project metadata; remove unused `pulp` from `requirements.txt`
  - Add `.gitignore` for `__pycache__`, `.pytest_cache`, `.hypothesis`, generated `*.xlsx` outputs, `results/`
  - Move one-off scripts (`discrepancy_analysis.py`, `final_discrepancy_report.py`, `analyze_row_*.py`, `find_missing_row.py`) and `production_planner_penalty_max16.py` into `legacy/`
  - _Ref: ARCHITECTURE.md Migration Phase 0_

- [ ] 0.2 Checkpoint — full existing test suite still passes (112 tests green)

## Phase 1 — Extract the domain layer (non-breaking)

- [ ] 1.1 Extract pure domain modules from `integrated_cost_optimizer.py`
  - Move `IntegratedParams` → `domain/params.py`
  - Move `compute_weekly_cost` → `domain/cost_model.py`
  - Move `solve_plan_integrated`, `compute_inventory_bounds` → `domain/solver.py`
  - Move `build_weekly_demand`, `build_weekly_row_demand`, `batches_needed`, `split_good_into_batches`, `clean_sites`, `_norm_cols`, constants → `domain/demand.py` (or split cleanly)
  - _Ref: ARCHITECTURE.md Migration Phase 1_

- [ ] 1.2 Keep `integrated_cost_optimizer.py` as a re-export shim
  - `from domain.solver import solve_plan_integrated` etc., so all existing imports and tests resolve unchanged
  - Keep the CLI `main()` in place, now delegating to domain
  - _Ref: [E-8.1], [E-8.4]_

- [ ] 1.3 Add `domain/errors.py`
  - `RubyFillError` base, `ValidationError`, `InfeasiblePlanError`, `InfeasibleAllocationError`

- [ ] 1.4 Move existing root test file into `tests/domain/`
  - Relocate `test_integrated_cost_optimizer.py` → `tests/domain/test_solver.py` (and split if convenient); ensure conftest stub still applies where needed

- [ ] 1.5 Checkpoint — full test suite passes against the new domain layout

## Phase 2 — Adapters and ports (non-breaking)

- [ ] 2.1 Define port protocols in `services/ports.py`
  - `SitesReaderPort`, `MasterPlannerReaderPort`, `ResultExporterPort`, `InputFileWriterPort`
  - _Ref: ARCHITECTURE.md Dependency Inversion_

- [ ] 2.2 Implement `io/sites_reader.py`
  - Move file reading/normalization out of `app.py`/optimizer into `ExcelSitesReader` implementing `SitesReaderPort`
  - Accept `.xlsx`/`.csv`; pass-through extra columns; optional `Site_Name`, `EU_Restricted` columns
  - _Ref: [E-2.1], [E-2.3], [E-2.5], [E-2.6]_

- [ ] 2.3 Implement `io/workbook_exporter.py`
  - Move Excel writing out of `app.py`/optimizer into `WorkbookExporter` implementing `ResultExporterPort`
  - Preserve existing sheets (Weekly_Plan, Sites_Clean, Input_Issues, Model_Params)
  - _Ref: [E-8.2]_

- [ ] 2.4 Unit tests for adapters with small fixture files (`tests/io/`)

- [ ] 2.5 Checkpoint — tests pass

## Phase 3 — Shared configuration (one Settings model)

- [ ] 3.1 Add `SupplierParams` to `domain/params.py`
  - Frozen dataclass with all fields + validation in `__post_init__`
  - _Ref: [S-1.1]–[S-1.10]_

- [ ] 3.2 Build `services/settings_service.py`
  - Single function assembling validated `IntegratedParams` + `SupplierParams` + reference date + calibration offset + quarter start month + shutdown/partial weeks + row_cap from one set of raw UI values
  - Per-field validation with explanatory `ValidationError`; defaults documented; restore-defaults support
  - _Ref: [E-7.1]–[E-7.14], [S-1.9], [S-1.10], [S-6.10]_

- [ ] 3.3 Define shared DTOs in `services/dtos.py`
  - `OptimizeRequest`, `OptimizationResult`, `OnboardingRequest`, `OnboardingResult`, `ComparisonRequest`, `ComparisonResult`, `NewCustomerDef`
  - _Ref: ARCHITECTURE.md DTOs_

- [ ] 3.4 Unit tests for settings validation (valid, each invalid field, restore defaults)

## Phase 4 — Reference dates (domain, small, unblocks UI everywhere)

- [ ] 4.1 Implement `domain/dates.py`
  - `derive_week_dates(reference_week_date, calibration_offset_days, horizon_weeks)`
  - `current_planning_week(reference_week_date, today)`
  - _Ref: [E-6.1]–[E-6.5], [E-6.12]_

- [ ] 4.2 Property + unit tests for dates
  - 7-day spacing, cal = mfg + offset, current-week None outside horizon
  - _Ref: [E-6.4], design property 9, 10_

## Phase 5 — row_cap enforcement in the solver (domain)

- [ ] 5.1 Add optional `eu_demand` parameter to `compute_inventory_bounds` / `solve_plan_integrated`
  - Raise `lb[t-1]` by `max(0, eu_demand[t] - row_cap)`; default `None` preserves current behaviour
  - _Ref: [E-7.4], design Feature 2_

- [ ] 5.2 Tests: with `eu_demand=None` results unchanged (regression); with EU excess, inventory floor enforced; a case where cap would be violated is now prevented
  - _Ref: [E-7.4]_

## Phase 6 — Supplier constraints (domain)

- [ ] 6.1 Implement `domain/supplier_allocation.py`
  - `compute_activity(generators, surplus_pct, params)` — exact formula
  - `allocate_suppliers_weekly(y_plan, eu_demand, params, supplier_params, ref_date)` — availability, run sequencing (Curium first; 3-run = Curium/BWXT/Curium only), EU check
  - `WeeklySupplierAllocation` dataclass
  - _Ref: [S-2.1]–[S-2.7], [S-3.1]–[S-3.6], [S-4.1]–[S-4.9], [S-5.1]–[S-5.6]_

- [ ] 6.2 Implement `domain/quota.py`
  - `compute_quarter_boundaries(horizon, quarter_start_month, ref_date)` (with 13-week fallback)
  - `check_quarterly_quota(allocations, supplier_params, boundaries)` → `QuarterlyQuotaStatus[]`
  - _Ref: [S-6.1]–[S-6.9]_

- [ ] 6.3 Implement `solve_with_suppliers(...)` orchestration in `domain/supplier_allocation.py`
  - Pre-solve `validate_supplier_feasibility`; call solver with `eu_demand`; allocate; quota; add quota penalty into composite; merge supplier columns
  - _Ref: [S-3.4], [S-5.4], [S-6.8], [S-8.1]–[S-8.5]_

- [ ] 6.4 Unit tests (`tests/domain/test_supplier_allocation.py`)
  - 6 reference weeks exact; all allocation scenarios; quarter mapping; quota shortfall/penalty
  - _Ref: [S-2.7]_

- [ ] 6.5 Property tests (`tests/domain/test_supplier_properties.py`)
  - Allocation sums to y; sequence length == batches; Curium-first; 3-run pattern; EU-by-construction; activity monotonic/zero; shortfall ≥ 0; penalty proportional; no penalty when met
  - _Ref: supplier design properties 1–10_

- [ ] 6.6 Checkpoint — tests pass

## Phase 7 — Delivery assignment & changed weeks (domain)

- [ ] 7.1 Implement `domain/delivery_assignment.py`
  - `assign_deliveries(y_plan, demand_events, params)` with deterministic latest-available-supply tie-break
  - `DeliveryRecord` dataclass (scheduled/planned/shift/early/late/new)
  - _Ref: [E-3.1]–[E-3.3], [E-3.11]–[E-3.13], design Feature 3_

- [ ] 7.2 Implement Master-Planner per-customer comparison + new-customer detection
  - `compare_against_master_planner(assignments, master_customer_schedule)`
  - _Ref: [E-3.6], [E-3.7]_

- [ ] 7.3 Property + unit tests
  - Per-week assignment count == y_plan[t]; total == demand; planned_week in range; determinism; early/backlog cases
  - _Ref: design properties 1–4_

## Phase 8 — Master Planner parsing & baseline comparison

- [ ] 8.1 Implement `io/master_planner_parser.py`
  - Header detection (Weeks #, Total Commercial, QC GEN, MFG/Calibration dates)
  - Customer column matching by leading number; **stable ID assignment** (`RF-<hash8>`) for unnumbered columns; ignore-list for non-customer columns; `AssignedId` report
  - `MasterPlannerData` DTO
  - _Ref: [E-1.1]–[E-1.7], [E-2.6]; design "stable ID assignment"_

- [ ] 8.2 Implement `domain/comparison.py`
  - `compute_baseline_cost(planned_production, demand, params, shutdowns)` reusing `compute_weekly_cost`; capacity-violation reporting
  - `compare_plans(baseline, optimized)` → component table + weekly comparison
  - _Ref: [E-1.8]–[E-1.18]_

- [ ] 8.3 Tests
  - Parser: column detection, QC addition, stable-ID determinism, week alignment, unmatched reporting (`tests/io/`)
  - Comparison: zero-baseline %, infeasible manual plan, savings signs (`tests/domain/`)
  - _Ref: [E-1.14], design properties 5, 6_

## Phase 9 — Multi-customer onboarding (domain + service)

- [ ] 9.1 Implement `evaluate_multi_customer(...)` (onboarding domain)
  - `NewCustomerDef`; exhaustive search below `EXHAUSTIVE_THRESHOLD` (500); coordinate-descent heuristic with diversified seeds above it; independent per-customer windows; shared baseline; `CombinationResult`
  - Calls `solve_with_suppliers` so supplier constraints are active during onboarding
  - Progress callback; infeasibility reporting with offending windows
  - _Ref: [E-4.1]–[E-4.17], [S-9.1]–[S-9.5]; design Feature 4_

- [ ] 9.2 Ranking: top-5 per objective (penalty/overtime/capacity) across combinations
  - _Ref: [E-4.10]_

- [ ] 9.3 Tests
  - Exhaustive == heuristic on small space; single-customer (N=1) regression vs old behaviour; convergence; threshold switch
  - _Ref: design property 7_

## Phase 10 — Input file generation (adapter)

- [ ] 10.1 Implement `io/input_file_writer.py`
  - Combine existing rows (unchanged) + new customers; planner-entered `Site_ID` (elution serial), no auto-generate; uniqueness validation; `Is_New` flag; country + EU flag carried through
  - _Ref: [E-5.1]–[E-5.11]_

- [ ] 10.2 Round-trip test: generated file re-parsed by `sites_reader` yields zero new issues
  - _Ref: [E-5.10], design property 8_

## Phase 11 — Services (orchestration)

- [ ] 11.1 `services/optimizer_service.py`
  - Wire reader → clean → demand+eu_demand → `solve_with_suppliers` → `assign_deliveries` → attach dates → build `OptimizationResult`
  - _Ref: [S-*], [E-3.*], [E-6.*], [E-7.4]_

- [ ] 11.2 `services/comparison_service.py`
  - Master Planner parse → baseline → compare → `ComparisonResult`; graceful when no Master Planner
  - _Ref: [E-1.19]_

- [ ] 11.3 `services/onboarding_service.py`
  - `evaluate_multi_customer` → ranking → input-file generation on selection
  - _Ref: [E-4.*], [E-5.*]_

- [ ] 11.4 Service tests with fake adapter ports (in-memory)
  - _Ref: ARCHITECTURE.md testability_

## Phase 12 — Unified export (adapter)

- [ ] 12.1 Extend `io/workbook_exporter.py` with all sheets
  - Weekly_Plan (+ MFG/Cal dates, supplier cols, EU demand), Model_Params (+ ref week, cal offset, all supplier params), Changed_Weeks (+ highlight formatting: green early / red late / blue new), Cost_Comparison, Weekly_Comparison, Quota_Status, Assigned_IDs
  - _Ref: [E-1.17], [E-1.18], [E-3.4], [E-3.5], [E-6.7], [E-6.10], [S-7.5], [S-8.*]; design export table_

- [ ] 12.2 Export tests: every sheet present, formatting applied, round-trips as valid workbook

## Phase 13 — The one unified UI

Build the single Streamlit app once, on top of the finished services. **One app, one Settings tab, four workflow tabs, one export.**

- [ ] 13.1 `ui/tab_settings.py` — single settings surface
  - Grouped, labelled, explained fields for **all** params: production, costs, weights, supplier params, reference week + calibration offset, quarter start month, row_cap, shutdown/partial weeks; restore-defaults; per-session
  - _Ref: [E-7.1]–[E-7.14], [S-1.*], [S-6.10]_

- [ ] 13.2 `ui/tab_optimizer.py` — run + results
  - Upload sites; run via `optimizer_service`; plan table with Week/MFG/Cal date columns + current-week indicator; Quota Status expander; Changed-Weeks summary + filterable table; download unified export
  - _Ref: [E-6.6], [E-6.8], [E-6.9], [E-3.6]–[E-3.10], [S-7.1]–[S-7.4]_

- [ ] 13.3 `ui/tab_onboarding.py` — multi-customer
  - Editable multi-row input (add/edit/remove) + bulk paste; per-customer Site_ID/name/earliest/latest/interval/country/EU flag; search-space estimate + heuristic disclosure; progress bar; top-5 per objective with per-customer weeks + dates; "Generate Input File" download; "Generate Full Plan"
  - _Ref: [E-4.1]–[E-4.17], [E-5.*], [S-9.*]_

- [ ] 13.4 `ui/tab_comparison.py` — manual vs optimized
  - Master Planner upload; side-by-side component table; absolute + % savings; overtime comparison; weekly production comparison; Assigned_IDs display
  - _Ref: [E-1.11]–[E-1.16]_

- [ ] 13.5 `app.py` — entry point
  - Wire concrete adapters into services once; render the five tabs (Settings, Cost Optimizer, Onboarding, Comparison); no business logic in UI
  - _Ref: ARCHITECTURE.md presentation layer_

## Phase 14 — Verification & cleanup

- [ ] 14.1 End-to-end integration test
  - Upload sites + Master Planner → run → assert comparison, changed weeks, dates, supplier allocation, quota all consistent in one result and one export
  - _Ref: design integration tests_

- [ ] 14.2 Onboarding integration test
  - 3 customers × 6-week windows completes exhaustively < 2 min; ranking correct; generated input file round-trips
  - _Ref: [E-4.14]_

- [ ] 14.3 Backward-compatibility check
  - Existing-format input (only required columns) runs; all pre-existing behaviours intact; every prior test green
  - _Ref: [E-8.1]–[E-8.4]_

- [ ] 14.4 Final: run full suite (domain, services, io, integration), remove temp files, update `guide.md`/README with the unified workflow

---

## Notes

- Each Checkpoint requires the full test suite green before proceeding.
- Domain modules are pure and property-tested; services are tested with fake ports; adapters with fixture files; the UI stays thin.
- Supplier constraints and enhancements share the same `eu_demand` array, the same solver, and the same export — they are one build, not two.
- Single-customer onboarding is the N=1 case of multi-customer; there is no separate single-customer path.
