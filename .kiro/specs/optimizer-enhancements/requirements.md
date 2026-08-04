# Requirements Document — Ruby Fill Optimizer Business Enhancements

## Introduction

This feature covers six enhancement requests raised by the business after reviewing the current Ruby Fill Optimizer. They fall into three themes:

- **Trust and explainability** — planners cannot currently see what the optimizer saved them, or which customer commitments it moved. Requests 1, 3, and 6 address this.
- **Onboarding throughput** — the onboarding tab handles one new customer at a time and its output cannot be fed back into the optimizer. Requests 4 and 5 address this.
- **Input preparation** — converting the Master Planner sheet into the optimizer input file is manual. Request 2 addresses this and is already scheduled for a separate phase-2 in-house application.

Supplier and raw material constraints from the Ruby Fill one-pager are specified separately in `.kiro/specs/supplier-constraints/requirements.md`.

## Glossary

- **Input_Schedule**: The customer demand schedule as supplied in the optimizer input file, defined by each site's `Next_Demand_Week` and `Interval_Weeks`. This defines *when each customer is due* a generator.
- **Manual_Plan**: The production schedule built by hand by the planning team, supplied to the tool as an uploaded file. This defines *when the team intended to produce* generators, before optimization.
- **Baseline_Cost**: The total cost of executing the Manual_Plan, computed with the same cost model, rates, and weights used for the optimized plan.
- **Optimized_Cost**: The total cost of the plan produced by the optimizer's DP solver.
- **Cost_Comparison**: A side-by-side presentation of Baseline_Cost and Optimized_Cost, broken down by cost component, with absolute and percentage savings.
- **Scheduled_Week**: The week in which a given customer's generator is due according to the Input_Schedule.
- **Planned_Week**: The week in which the optimizer's plan produces the generator that serves that customer demand.
- **Changed_Week**: A customer-generator whose Planned_Week differs from its Scheduled_Week. A generator produced in week 8 to serve a week 10 demand **is** a Changed_Week, even though the customer still receives it in week 10.
- **Week_Shift**: The signed difference `Planned_Week - Scheduled_Week`. Negative means produced early, positive means produced late.
- **Delivery_Assignment**: The mapping from each produced Good_Unit to the specific customer site and Scheduled_Week it serves. Required to detect Changed_Weeks, and not currently produced by the aggregate DP solver.
- **Reference_Week**: The anchor that maps planning week number 1 to a real manufacturing week date, allowing every week number in the horizon to be displayed as a date.
- **Calibration_Offset**: The fixed number of days between a week's manufacturing date and its calibration date. Default 4, matching the Master Planner's observed offset.
- **QC_Generator**: A test generator produced for quality control. One is produced per production batch (matching the model's one test discard per batch) and each consumes 10 mCi of Sr-82. The Master Planner tracks these in a separate `QC GEN` column, distinct from `Total Commercial`. Full weekly generator requirement equals commercial demand plus QC generators.
- **Onboarding_Window**: A per-customer pair of an earliest permissible start week and a latest permissible start week. For example, customer A may be onboarded from week 4 onwards but must be onboarded no later than week 9.
- **Onboarding_Combination**: A selection of one start week for each new customer, with every selected week falling inside that customer's Onboarding_Window.
- **Master_Planner**: The `Ruby-Fill- Master Schedule.xlsx` workbook, whose `Schedule` sheet holds one column per customer and one row per week.

## Requirements

### Requirement 1: Manual Plan vs Optimized Schedule Cost Comparison

**User Story:** As a production planner, I want to upload the plan my team built by hand and see its cost next to the optimized plan's cost, so that I can quantify what the tool saved and justify acting on its output.

The Manual_Plan is not a separate file the planner maintains. It lives inside the wide Master Planner workbook (`Ruby-Fill- Master Schedule.xlsx`, `Schedule` sheet), which has one row per week and one column per customer, plus aggregate columns such as `US Demand`, `RoW Demand`, and `Total Commercial`. The tool must read the manual plan directly from that layout.

#### Acceptance Criteria

1. THE Application SHALL allow the planner to upload the Master Planner workbook and SHALL read the Manual_Plan from its `Schedule` sheet.
2. THE Application SHALL allow the planner to select which sheet and which column represent the manually planned weekly production, defaulting to the `Total Commercial` aggregate column, since the exact layout may vary between workbook versions.
3. THE Application SHALL locate the week identifier from the `Weeks #` column and SHALL align each Master Planner row to the corresponding planning week in the horizon.
4. THE Application SHALL derive weekly planned production by summing the per-customer columns for each week, or by reading the aggregate total column, and SHALL document which method was used for a given run.
4a. THE Application SHALL add the QC generators for each week (from the Master Planner `QC GEN` column) to the commercial demand when computing the full weekly generator requirement, since the `Total Commercial` column covers commercial customers only.
4b. THE Application SHALL account for the Sr-82 activity of QC generators at the configurable per-QC-generator rate (default 10 mCi) when the supplier-constraints feature is active, consistent with the supplier-constraints specification.
5. WHEN a Master Planner week within the horizon has no planned value, THE Application SHALL treat that week's planned production as zero.
6. WHEN the Master Planner contains rows outside the planning horizon, THE Application SHALL exclude them and SHALL report how many were excluded.
7. WHEN a parsed planned production value is negative or non-numeric, THE Application SHALL report it as a data quality issue for that week and SHALL treat it as zero.
8. WHEN the Manual_Plan's total planned production does not equal total demand across the horizon, THE Application SHALL warn the planner and SHALL report the difference, but SHALL still compute the Baseline_Cost.
9. THE Application SHALL compute Baseline_Cost by evaluating the Manual_Plan's weekly production against the Input_Schedule's weekly demand, using the same cost rates, weights, and horizon as the optimized run.
10. THE Application SHALL compute the Manual_Plan's weekly net inventory as prior inventory plus planned production minus due demand, applying the early penalty rate to positive inventory and the late penalty rate to negative inventory, identical to the optimized plan's cost model.
11. WHEN a Manual_Plan week's planned production exceeds that week's maximum capacity, THE Application SHALL report a capacity violation for that week and SHALL still compute the cost, so that the planner can see where the manual plan was infeasible.
12. THE Application SHALL report Baseline_Cost broken down into the same components as the optimized plan: penalty cost, overtime cost, capacity utilization cost, and total composite cost.
13. THE Application SHALL display Baseline_Cost and Optimized_Cost side by side for each cost component and for the total.
14. THE Application SHALL display the absolute saving and the percentage saving for each cost component and for the total, computed as `baseline - optimized`.
15. WHEN the optimized cost for a component exceeds the baseline cost for that component, THE Application SHALL display the difference as a negative saving rather than suppressing it.
16. WHEN a Baseline_Cost component is zero, THE Application SHALL display the percentage saving as not applicable rather than as a division error.
17. THE Application SHALL display the count of overtime weeks for both the Manual_Plan and the optimized plan.
18. THE Application SHALL display a week-by-week comparison of Manual_Plan production against optimized production.
19. THE exported workbook SHALL include a dedicated Cost_Comparison sheet with one row per cost component and columns for baseline, optimized, absolute saving, and percentage saving.
20. THE exported workbook SHALL include the week-by-week Manual_Plan versus optimized production comparison.
21. WHEN no Manual_Plan is uploaded, THE Application SHALL run the optimizer normally and SHALL omit the Cost_Comparison rather than failing.

### Requirement 2: Master Planner to Input File Conversion

**User Story:** As a production planner, I want the Master Planner sheet converted into the optimizer input file automatically, so that I do not transcribe 180 customer schedules by hand.

**Status: deferred.** The business has confirmed this is planned as a phase-2 in-house application. This requirement documents the contract the Ruby Fill Optimizer must honour so the phase-2 converter can integrate without rework. No converter implementation is in scope here.

#### Acceptance Criteria

1. THE Optimizer SHALL accept its site input as a file with the columns `Site_ID`, `Active`, `Next_Demand_Week`, `Interval_Weeks`, and `Country`, in either `.xlsx` or `.csv` format.
2. THE Optimizer SHALL document the input file contract, including required columns, accepted values for `Active`, the valid range for `Next_Demand_Week`, the minimum value for `Interval_Weeks`, and the country values that drive customer classification.
3. THE Optimizer SHALL treat any additional columns present in the input file as pass-through and SHALL NOT fail because of them.
4. THE Optimizer SHALL continue to report input data quality problems in the `Input_Issues` sheet so that a generated input file can be validated on load.
5. THE Optimizer SHALL accept an optional `Site_Name` column and SHALL carry it through to all customer-facing output so that generated files can retain Master Planner customer names.
6. THE Optimizer SHALL accept an optional column identifying a site as subject to the Curium-only material restriction, so that the converter can populate it from the Master Planner's blue marking rather than relying on a hardcoded country list.

### Requirement 3: Highlight Changed Customer Weeks in the Output

**User Story:** As a production planner, I want the output file to show me which customers had their production week moved and by how much, so that I know who is affected before committing to the plan.

#### Acceptance Criteria

1. THE Optimizer SHALL produce a Delivery_Assignment mapping every planned Good_Unit to the customer site and Scheduled_Week it serves.
2. THE Optimizer SHALL compute, for each customer-generator in the Delivery_Assignment, the Scheduled_Week, the Planned_Week, and the Week_Shift.
3. THE Optimizer SHALL classify a customer-generator as a Changed_Week when its Week_Shift is non-zero, including cases where the generator is produced early and held in inventory until the Scheduled_Week.
4. THE exported workbook SHALL include a dedicated sheet listing every customer-generator with columns for site identifier, site name, country, Scheduled_Week, Planned_Week, and Week_Shift.
5. THE exported workbook SHALL visually highlight Changed_Week rows, using distinct formatting for early shifts and late shifts.
6. WHEN a Master Planner workbook is provided, THE Optimizer SHALL compare each customer's optimized Planned_Week against that customer's manually planned week read from that customer's own Master Planner column (matched by extracting the leading numeric identifier from the column header and comparing it to the input file's `Site_ID`), in addition to comparing against the Input_Schedule due week, so that a scheduling error made by the schedule holder can be intercepted.
7. THE Optimizer SHALL identify and highlight newly added customers — sites present in the current run but not in the compared Master Planner — as a distinct category in the changed-week report.
8. THE Application SHALL display a summary of the count of unchanged generators, generators produced early, generators produced late, and generators belonging to newly added customers.
9. THE Application SHALL display the distribution of Week_Shift magnitudes so that a planner can see whether changes are small or large.
10. THE Application SHALL allow the planner to filter the changed-week view by site, by country, by newly-added status, and by Week_Shift magnitude.
11. THE Optimizer SHALL apply a documented, deterministic tie-breaking rule when multiple customer-generators could be served by the same produced unit, so that repeated runs on identical input produce identical Delivery_Assignments.
12. THE Optimizer SHALL ensure the total number of customer-generators in the Delivery_Assignment equals the total demand across the horizon.
13. THE Optimizer SHALL ensure the number of customer-generators assigned a given Planned_Week equals that week's Good_Units production in the plan.

### Requirement 4: Multiple Customers in Onboarding Recommendation

**User Story:** As a production planner, I want to onboard several new customers at once, each with its own permissible week window, so that I can plan a batch of onboardings together and give each customer its own best start week.

#### Acceptance Criteria

1. THE Application SHALL allow the planner to define two or more new customers in a single onboarding recommendation run.
2. FOR each new customer, THE Application SHALL accept an earliest start week, a latest start week, an interval in weeks, a country, an EU_Restricted_Customer indicator, an optional site name, and a Site_ID entered by the planner (the elution system serial number for that site).
3. THE Application SHALL treat each customer's earliest and latest start week as that customer's Onboarding_Window, and SHALL allow different customers to have different windows.
4. THE Application SHALL allow the planner to add a new customer row, edit any existing row, and remove any row before running.
5. THE Application SHALL validate each new customer independently and SHALL report which row each validation error belongs to.
6. WHEN any new customer row fails validation, THE Application SHALL prevent the run and SHALL display all row-level errors together.
7. THE Recommendation_Engine SHALL select a start week for each new customer independently, subject to that customer's Onboarding_Window.
8. THE Recommendation_Engine SHALL evaluate Onboarding_Combinations against a single shared baseline computed from the existing sites only.
9. THE Recommendation_Engine SHALL report marginal penalty cost, marginal overtime cost, marginal capacity cost, and marginal composite cost for each recommended Onboarding_Combination, relative to that shared baseline.
10. THE Recommendation_Engine SHALL rank recommended Onboarding_Combinations separately by marginal penalty, marginal overtime, and marginal capacity, and SHALL present the top 5 for each objective.
11. FOR each recommended Onboarding_Combination, THE Application SHALL display the selected start week for every new customer.
12. WHEN no Onboarding_Combination is feasible, THE Application SHALL report that no feasible onboarding schedule exists and SHALL indicate which customer windows caused the infeasibility.
13. THE Application SHALL support a bulk entry path allowing the planner to paste or upload multiple new customer definitions rather than entering each by hand.
14. THE Recommendation_Engine SHALL complete a run for a realistic problem size within a time budget acceptable for interactive use, and SHALL display progress while running.
15. WHEN the number of new customers and the widths of their Onboarding_Windows imply a search space too large to evaluate exhaustively, THE Recommendation_Engine SHALL apply a documented heuristic search rather than failing or running unbounded.
16. WHEN a heuristic search is used, THE Application SHALL disclose that the result is a strong candidate rather than a proven global optimum.
17. THE exported recommendation workbook SHALL identify which new customers each ranking applies to and SHALL record each customer's selected start week.

### Requirement 5: Generate Optimizer Input File Including New Customers

**User Story:** As a production planner, I want the tool to hand me a ready-to-use optimizer input file that includes the newly onboarded customers, so that I can re-run the optimizer without editing spreadsheets.

#### Acceptance Criteria

1. WHEN the planner has selected an Onboarding_Combination, THE Application SHALL generate an optimizer input file combining the existing sites with the new customers.
2. THE generated input file SHALL use the same column contract as the optimizer's own input format, so that it can be uploaded back into the Cost Optimizer tab without modification.
3. THE Application SHALL set each new customer's `Next_Demand_Week` to that customer's selected start week and `Interval_Weeks` to the interval entered for that customer.
4. THE Application SHALL use the `Site_ID` (elution system serial number) entered by the planner for each new customer, and SHALL NOT auto-generate it, since these serials are allocated manually.
4a. WHEN a planner-entered `Site_ID` collides with an existing site identifier or with another new customer's identifier, THE Application SHALL report a validation error and SHALL NOT generate the file until the collision is resolved.
5. THE Application SHALL preserve every existing site row unchanged, including inactive rows and any pass-through columns.
6. THE Application SHALL mark each new customer as active.
7. THE Application SHALL include a column flagging newly added customers so they can be distinguished from pre-existing ones.
8. THE Application SHALL carry each new customer's country and, where applicable, its material restriction indicator into the generated file.
9. THE Application SHALL provide the generated input file as a download.
10. WHEN the generated input file is loaded back into the optimizer, THE Optimizer SHALL report zero new data quality issues attributable to the generated rows.
11. THE Application SHALL make the generated input file available only after a start week has been selected for every new customer.

### Requirement 6: Reference Week Display

**User Story:** As a production planner, I want each line of the plan to show the week number, the manufacturing week date, and the calibration date, so that I can relate the plan to real dates without consulting the Master Planner.

The business has confirmed all three are important on every line: (A) the week number, (B) the manufacturing week date, and (C) the calibration date. In the Master Planner the calibration date falls a fixed offset after the manufacturing date (four days in the observed data).

#### Acceptance Criteria

1. THE Application SHALL accept a configurable Reference_Week input that anchors planning week 1 to a manufacturing week date.
2. THE Application SHALL accept a configurable Calibration_Offset in days, defaulting to 4, giving the calibration date as the manufacturing date plus the offset.
3. THE Application SHALL display the Reference_Week prominently, so that the planner can confirm the anchor before interpreting any result.
4. THE Application SHALL derive the manufacturing date of every week in the horizon from the Reference_Week by adding seven days per week.
5. THE Application SHALL derive the calibration date of every week as its manufacturing date plus the Calibration_Offset.
6. THE Application SHALL display the week number, the manufacturing date, and the calibration date together on each line of the weekly production plan table.
7. THE Application SHALL display the week number, the manufacturing date, and the calibration date in the onboarding recommendation results.
8. THE Application SHALL display the week number, the manufacturing date, and the calibration date in the changed-week view.
9. THE exported workbook SHALL include the week number, the manufacturing date, and the calibration date in the `Weekly_Plan` sheet.
10. THE exported workbook SHALL record the Reference_Week and the Calibration_Offset in the `Model_Params` sheet.
11. WHEN no Reference_Week is configured, THE Application SHALL display week numbers only and SHALL NOT display fabricated dates.
12. WHEN the current date falls inside the horizon, THE Application SHALL indicate which planning week corresponds to it.

## Cross-Cutting Requirements

### Requirement 7: Full Parameter Configurability

**User Story:** As a production planner, I want to set every cost, penalty, and constraint value from the application, so that I can adjust the model to changing commercial terms without a code change or a developer.

#### Acceptance Criteria

1. THE Application SHALL expose every cost rate used by the model as an editable field in the user interface, including the early penalty rate, the late penalty multiplier, the overtime rate, and the capacity utilization rate.
2. THE Application SHALL expose every objective weight as an editable field, including the weights for penalty, overtime, capacity utilization, and any weight introduced by the supplier constraints feature.
3. THE Application SHALL expose every production constraint as an editable field, including the horizon length, minimum and maximum batch size, test discard per batch, maximum batches in a normal week, and maximum batches in an overtime week.
4. THE Application SHALL expose the QC shipping cap (`row_cap`) for restricted-country customers as an editable field, and THE Optimizer SHALL enforce this cap in the solver — the plan SHALL NOT schedule more than `row_cap` generators to restricted-country customers in any single week.
5. THE Application SHALL expose the quarter start month as an editable field, defaulting to January.
6. THE Application SHALL expose the Reference_Week as an editable field.
7. THE Application SHALL expose shutdown weeks and partial shutdown weeks as editable fields.
8. THE Application SHALL present all configurable parameters in a single settings area, grouped by theme, with each field labelled and accompanied by an explanation of its effect.
9. THE Application SHALL apply the same parameter values consistently across the Cost Optimizer, the Onboarding Recommendation, and the Cost_Comparison, so that a single settings change affects every result.
10. THE Application SHALL validate each parameter on entry and SHALL display an explanatory error rather than running with an invalid value.
11. THE Application SHALL display the default value for every parameter and SHALL allow the planner to restore all defaults.
12. THE Application SHALL record every parameter value used in a run in the exported workbook's `Model_Params` sheet.
13. THE Application SHALL retain parameter values for the duration of the session and SHALL allow the planner to adjust them before each run without requiring a persistent save/reload mechanism.
14. THE Application SHALL NOT hardcode any cost, penalty, rate, weight, or capacity value that the business may need to change.

### Requirement 8: Backward Compatibility

**User Story:** As a production planner, I want my existing input files and workflow to keep working, so that these enhancements do not disrupt current use.

#### Acceptance Criteria

1. THE Optimizer SHALL continue to accept input files that contain only the existing required columns.
2. THE Optimizer SHALL preserve all existing `Weekly_Plan`, `Sites_Clean`, `Input_Issues`, and `Model_Params` sheet columns.
3. WHEN an optional new input is absent, THE Optimizer SHALL apply a documented default and SHALL run without error.
4. THE existing Cost Optimizer and Onboarding Recommendation flows SHALL remain operational.

## Resolved Decisions

These questions were raised during requirements analysis and have been answered by the business.

| # | Question | Decision |
|---|---|---|
| 1 | What is the baseline for the cost comparison? | The planner uploads the manually created plan. The tool compares against that, not against a synthetic naive schedule. See Requirement 1. |
| 2 | When has a customer's week changed? | The production week is what matters. A generator due in week 10 but produced in week 8 and held in stock **is** a changed week and must be highlighted. See Requirement 3.3. |
| 3 | How are quarters defined? | Configurable in the front end as a quarter start month, defaulting to January. See Requirement 7.5 and the supplier-constraints spec. |
| 4 | Is `row_cap = 2` the same rule as the BWXT restriction? | No. They are two independent constraints that happen to affect the same customers. `row_cap` is a QC throughput limit; the BWXT rule is a raw material sourcing restriction. Both must be modelled. |
| 5 | Multi-customer onboarding: shared or independent start weeks? | Independent. Each new customer gets its own earliest and latest permissible start week, and the tool picks a start week per customer within that window. See Requirement 4. |
| 6 | Should parameters be configurable? | Yes. Every cost, penalty, rate, weight, and constraint must be settable from the front end. This includes the supplier shortfall penalty rate and the quarterly quota values. See Requirement 7. |
| 7 | What format is the manual plan in? | It lives inside the wide Master Planner workbook (`Schedule` sheet), one row per week and one column per customer. The tool reads it from there rather than from a separate simple table. See Requirement 1.1–1.4. |
| 8 | Which column is the weekly quantity? | `Total Commercial` for commercial demand, plus the `QC GEN` column, since QC generators (10 mCi each) must be added to get the full weekly requirement. See Requirement 1.4a. |
| 9 | Should the changed-week report compare per customer? | Yes — compare each customer's optimized week against that customer's own Master Planner column, and also highlight newly added customers. See Requirement 3.6–3.7. |
| 10 | How many customers are onboarded at once, and how wide are the windows? | Typically 1–5, occasionally up to 10 (e.g. January 2027). Windows range from 2 weeks to 2–3 months. This mandates a heuristic search; see Design Considerations. |
| 11 | Anchor to manufacturing or calibration date? | Both dates matter and both must appear on every line, alongside the week number. Week 1 anchors to the manufacturing date; calibration = manufacturing + Calibration_Offset (default 4 days). See Requirement 6. |
| 12 | What ID convention for new customers? | The planner enters the Site_ID manually — it is the elution system serial number for that site, not an account number and not auto-generated. See Requirement 5.4. |
| 13 | How to match Master Planner columns to Site_IDs? | The leading number in the column header (e.g. `00449` in `"00449    Advanced Specialty Care, Fresno"`) is the stable identifier and matches the input file's `Site_ID`. The design should extract and match on this leading number. See Requirement 3.6. |
| 14 | Should `row_cap = 2` QC shipping cap be enforced? | Yes — enforce it as part of this work. The solver must respect the cap rather than merely reporting it. See Requirement 7.4. |
| 15 | Highlight colour convention? | Free to choose any colours that don't conflict with the Master Planner's existing usage. |
| 16 | How should parameter sets be saved? | The planner sets parameters each time they want to run. No persistent saved-parameter-set feature is needed; the settings are ephemeral per session. Requirement 7.13 is satisfied by allowing the planner to configure values on each run. |

## Design Considerations Arising From These Decisions

These are not open questions but constraints the design phase must resolve.

1. **Independent start weeks make exhaustive search infeasible at the stated scale.** The business onboards 1–5 customers typically, up to 10 in peak cases (January 2027), with windows from 2 weeks to 2–3 months (roughly 2–13 weeks). The number of Onboarding_Combinations is the product of the window widths. Concretely, at roughly 0.3 seconds per DP solve:
   - 3 customers × 6-week windows = 216 solves ≈ 1 minute (borderline exhaustive).
   - 5 customers × 13-week windows = 371,293 solves ≈ 31 hours (impossible).
   - 10 customers × 13-week windows ≈ 1.4 × 10¹¹ solves (utterly impossible).

   Exhaustive evaluation is therefore only viable for the smallest cases. The design must define an exhaustive threshold (a maximum combination count, e.g. a few hundred solves, below which the true optimum is guaranteed) and, above it, a heuristic — for example evaluating each customer independently to find its promising weeks, seeding a combination, then refining by coordinate descent (fix all but one customer, optimize that customer's week, iterate to convergence). Requirements 4.14 through 4.16 capture the behaviour; the algorithm choice is a design decision. A faster incremental solve (adding one customer's demand to a cached baseline rather than re-solving from scratch) should also be explored to raise the exhaustive threshold.

2. **Changed-week detection requires a new disaggregation step.** The DP solver optimizes aggregate weekly quantities and never tracks which customer a unit serves. A separate assignment step must map produced units to customer demands, with a deterministic rule so results are reproducible. Requirement 3.9 captures this.

3. **Capacity cost will mask savings in the comparison.** On the current dataset the capacity utilization component is 81% of the total composite cost, and roughly 95% of that is an unavoidable floor arising from total annual demand (1,346 units) being well below total normal capacity (1,560 units). A headline baseline-versus-optimized total will therefore look nearly unchanged even when penalty savings are large. The Cost_Comparison should lead with the penalty and overtime components. Note also that the command-line entry point currently defaults the capacity weight to 0.0 while the application defaults it to 1.0; these must be reconciled.

## Out of Scope

- The Master Planner conversion tool itself (Requirement 2 is deferred to the phase-2 in-house application; only the input contract is specified here).
- Customer notification or communication workflow arising from Changed_Weeks.
- Editing the Master Planner sheet or writing results back into it.
- Multi-user collaboration, authentication, or server-side persistence of runs.
- Raw material supplier constraints, specified in `.kiro/specs/supplier-constraints/requirements.md`.

## Open Questions

All open questions have been resolved. No outstanding items remain for this spec.
