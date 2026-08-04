# Requirements Document

## Introduction

A new standalone Python optimization model that plans weekly production by minimizing a
weighted composite cost function combining three components: early/late delivery penalty,
overtime cost, and capacity utilization cost. Users assign weights (0–1) to each component,
allowing any component to be effectively disabled by setting its weight to zero. The model
builds on the existing planner's data model (sites CSV/Excel, shutdown weeks, partial
shutdown weeks, ROW constraint) and produces an Excel output with full cost breakdown.

## Glossary

- **Good_Units**: Usable units produced in a week (after discarding 1 test unit per batch)
- **Batch**: A production run producing 2–16 units, of which 1 is discarded for QA testing
- **Overtime_Batch**: The 3rd batch in a week (triggered when good units > 30)
- **Penalty_Cost**: USD cost for holding early inventory (no late delivery permitted)
- **Overtime_Cost**: USD cost incurred each week a 3rd batch is run
- **Capacity_Utilization_Cost**: USD cost for unused good unit capacity in a week
- **Normal_Capacity**: Maximum good units without overtime = 30 (2 batches × 15)
- **Max_Capacity**: Maximum good units with overtime = 45 (3 batches × 15)
- **Unused_Capacity**: Normal_Capacity minus Good_Units produced, floored at 0
- **Early_Inventory**: Units produced before their demand week (Net_Inventory_End > 0)
- **Backlog**: Units not yet fulfilled past their demand week (Net_Inventory_End < 0), allowed only as last resort
- **Late_Penalty_Multiplier**: Multiplier applied to penalty_rate for backlog (default 10×) to discourage late delivery
- **Weight**: A user-defined 0–1 scalar multiplier applied to each cost component
- **Composite_Cost**: Weighted sum of all three cost components used as optimization objective
- **ROW**: Rest of World — sites in Denmark, UK, Netherlands, Sweden
- **Shutdown_Week**: Week with zero production allowed
- **Partial_Shutdown_Week**: Week with max 1 batch (15 good units) allowed
- **Planner**: The integrated cost optimization model described in this document

---

## Requirements

### Requirement 1: Composite Cost Function

**User Story:** As a production planner, I want to optimize production using a weighted
combination of penalty, overtime, and capacity utilization costs, so that I can tune the
model to reflect my operational priorities.

#### Acceptance Criteria

1. THE Planner SHALL compute a weekly Composite_Cost as:
   `Composite_Cost = (w_penalty × Penalty_Cost) + (w_overtime × Overtime_Cost) + (w_capacity × Capacity_Utilization_Cost)`
   where w_penalty, w_overtime, and w_capacity are user-defined weights in range [0.0, 1.0].

2. THE Planner SHALL minimize the sum of Composite_Cost across all 52 weeks as the primary optimization objective.

3. WHEN a weight is set to 0.0, THE Planner SHALL exclude that cost component from the optimization objective entirely.

4. WHEN all weights are set to 0.0, THE Planner SHALL raise a descriptive error and halt.

5. THE Planner SHALL accept weights as floating point values between 0.0 and 1.0 inclusive.

6. IF any weight is outside the range [0.0, 1.0], THEN THE Planner SHALL raise a descriptive validation error.

---

### Requirement 2: Penalty Cost Component

**User Story:** As a production planner, I want early delivery to be strongly preferred
over late delivery, so that the model always tries to produce ahead of demand and only
falls back to late delivery as a last resort when capacity constraints make early
fulfillment impossible.

#### Acceptance Criteria

1. THE Planner SHALL compute weekly Penalty_Cost as:
   `Penalty_Cost = penalty_rate × Net_Inventory_End` when Net_Inventory_End >= 0 (early inventory)
   `Penalty_Cost = late_penalty_rate × |Net_Inventory_End|` when Net_Inventory_End < 0 (backlog, last resort)

2. THE Planner SHALL set late_penalty_rate to a multiple of penalty_rate (default 10×) to strongly
   discourage backlog while still allowing it when no feasible early solution exists.

3. THE Planner SHALL treat early inventory as the preferred outcome and backlog as a last resort.

4. WHEN capacity and shutdown constraints make early fulfillment impossible for a week,
   THE Planner SHALL allow backlog for that week with the elevated late_penalty_rate applied.

5. THE Planner SHALL allow the penalty_rate to be overridden via CLI parameter.

6. THE Planner SHALL allow the late_penalty_multiplier to be overridden via CLI parameter (default 10).

---

### Requirement 3: Overtime Cost Component

**User Story:** As a production planner, I want overtime shifts to be costed, so that the
model avoids unnecessary 3rd batch runs.

#### Acceptance Criteria

1. THE Planner SHALL compute weekly Overtime_Cost as:
   `Overtime_Cost = overtime_rate × overtime_flag`
   where overtime_flag = 1 if Good_Units > 30 (3rd batch triggered), else 0.

2. THE overtime_rate SHALL default to USD 2,000 per overtime week.

3. THE Planner SHALL allow the overtime_rate to be overridden via CLI parameter.

4. WHEN Good_Units is exactly 30 or less, THE Planner SHALL set Overtime_Cost to 0 for that week.

---

### Requirement 4: Capacity Utilization Cost Component

**User Story:** As a production planner, I want unused capacity to be costed, so that the
model is incentivized to smooth production and avoid wasteful idle weeks.

#### Acceptance Criteria

1. THE Planner SHALL compute weekly Capacity_Utilization_Cost as:
   `Capacity_Utilization_Cost = capacity_rate × max(0, Normal_Capacity - Good_Units)`
   where Normal_Capacity = 30 and capacity_rate is a user-defined cost per unused good unit slot.

2. THE capacity_rate SHALL default to USD 0 per unused unit (disabled by default).

3. THE Planner SHALL allow the capacity_rate to be overridden via CLI parameter.

4. WHEN Good_Units equals or exceeds Normal_Capacity (30), THE Planner SHALL set Capacity_Utilization_Cost to 0 for that week.

5. WHEN a week is a Shutdown_Week, THE Planner SHALL set Capacity_Utilization_Cost to 0 for that week.

6. WHEN a week is a Partial_Shutdown_Week, THE Planner SHALL compute Capacity_Utilization_Cost based on max(0, 15 - Good_Units) to reflect the reduced capacity ceiling.

---

### Requirement 5: Optimization Engine

**User Story:** As a production planner, I want the model to find the globally optimal
production schedule, so that I get the best possible plan given my cost weights.

#### Acceptance Criteria

1. THE Planner SHALL use Dynamic Programming to find the globally optimal weekly production schedule.

2. THE Planner SHALL minimize total Composite_Cost across the 52-week horizon as the primary objective.

3. WHEN the primary objective is tied, THE Planner SHALL use total overtime weeks as a secondary tie-breaker.

4. WHEN the secondary objective is also tied, THE Planner SHALL use total batches as a final tie-breaker.

5. THE Planner SHALL enforce that Net_Inventory_End at week 52 equals zero (all demand satisfied by end of horizon).

6. THE Planner SHALL strongly prefer non-negative Net_Inventory_End at every week via the elevated late penalty rate.

7. IF no feasible solution exists even with backlog allowed, THEN THE Planner SHALL raise a descriptive error identifying the infeasible week.

---

### Requirement 6: Production Constraints

**User Story:** As a production planner, I want all physical production constraints enforced,
so that the output plan is operationally executable.

#### Acceptance Criteria

1. THE Planner SHALL allow 0 to 3 batches per week.

2. THE Planner SHALL cap Good_Units at 30 for normal weeks (2 batches).

3. THE Planner SHALL cap Good_Units at 45 for overtime weeks (3 batches).

4. WHEN a week is a Shutdown_Week, THE Planner SHALL set Good_Units to 0.

5. WHEN a week is a Partial_Shutdown_Week, THE Planner SHALL cap Good_Units at 15 (1 batch).

6. THE Planner SHALL produce batch sizes between 2 and 16 units (including 1 test discard).

7. THE Planner SHALL accept Shutdown_Weeks and Partial_Shutdown_Weeks as user-defined comma-separated week numbers via CLI.

---

### Requirement 7: ROW Constraint

**User Story:** As a production planner, I want ROW country demand to be capped per week,
so that QC limitations for Denmark, UK, Netherlands, and Sweden are respected.

#### Acceptance Criteria

1. THE Planner SHALL identify ROW sites by the country column values: Denmark, UK, Netherlands, Sweden (case-insensitive).

2. THE Planner SHALL cap ROW unit fulfillment at a configurable maximum per week (default 2).

3. WHEN ROW demand exceeds the weekly cap, THE Planner SHALL carry excess as ROW backlog subject to the same penalty_rate.

4. THE Planner SHALL allow the ROW cap to be overridden via CLI parameter.

5. IF the country column is absent from the input file, THEN THE Planner SHALL treat all sites as non-ROW.

---

### Requirement 8: Input Handling

**User Story:** As a production planner, I want to load site data from Excel or CSV,
so that I can use the same input files as the existing planner.

#### Acceptance Criteria

1. THE Planner SHALL accept input files in .xlsx or .csv format.

2. THE Planner SHALL require columns: Site_ID, Active, Next_Demand_Week, Interval_Weeks (case-insensitive).

3. THE Planner SHALL treat the country column as optional, defaulting to empty (non-ROW) if absent.

4. IF required columns are missing, THEN THE Planner SHALL raise a descriptive error listing the missing columns.

5. THE Planner SHALL skip inactive sites (Active != Y/YES/TRUE/1).

6. THE Planner SHALL report duplicate Site_IDs as input issues and exclude them from planning.

7. THE Planner SHALL report Next_Demand_Week values outside 1–52 as input issues.

---

### Requirement 9: Output

**User Story:** As a production planner, I want a detailed Excel output with cost breakdowns,
so that I can understand how each cost component contributes each week.

#### Acceptance Criteria

1. THE Planner SHALL write an Excel output file with a Weekly_Plan sheet containing one row per week.

2. THE Weekly_Plan sheet SHALL include columns: Week, Week_Type, Demand_Due, Good_Production,
   Batch_Count, Batch1_Produced, Batch2_Produced, Batch3_Produced, Produced_Total,
   Testing_Discard, Overtime_Used, Net_Inventory_End, Early_Units_Held, Late_Units_Backlog,
   ROW_Demand_Due, ROW_Fulfilled, ROW_Inventory.

3. THE Weekly_Plan sheet SHALL include cost breakdown columns: Penalty_Cost_USD,
   Overtime_Cost_USD, Capacity_Utilization_Cost_USD, Composite_Cost_USD, Cumulative_Composite_Cost_USD.

4. THE Planner SHALL write a Model_Params sheet listing all parameters and weights used.

5. THE Planner SHALL write a Sites_Clean sheet with validated active sites.

6. THE Planner SHALL write an Input_Issues sheet listing any data quality problems.

7. WHEN --print-summary is set, THE Planner SHALL print a console summary including total
   composite cost, each component's total cost, weights used, overtime weeks, and active site count.

---

### Requirement 10: CLI Interface

**User Story:** As a production planner, I want all parameters configurable via command line,
so that I can run different scenarios without modifying code.

#### Acceptance Criteria

1. THE Planner SHALL accept the following CLI parameters:
   - --input (required): path to sites Excel or CSV
   - --output (required): path to output Excel
   - --sites-sheet (optional, default "Sites"): sheet name for Excel input
   - --shutdown-weeks (optional): comma-separated full shutdown week numbers
   - --partial-shutdown-weeks (optional): comma-separated partial shutdown week numbers
   - --w-penalty (optional, default 1.0): weight for penalty cost component
   - --w-overtime (optional, default 1.0): weight for overtime cost component
   - --w-capacity (optional, default 0.0): weight for capacity utilization cost component
   - --penalty-rate (optional, default 7000): USD per unit-week early inventory
   - --late-penalty-multiplier (optional, default 10): multiplier on penalty-rate for backlog weeks
   - --overtime-rate (optional, default 2000): USD per overtime week
   - --capacity-rate (optional, default 0): USD per unused good unit slot per week
   - --row-cap (optional, default 2): max ROW units per week
   - --horizon (optional, default 52): planning horizon in weeks
   - --print-summary (optional flag): print console summary

2. IF --w-penalty, --w-overtime, and --w-capacity are all 0.0, THEN THE Planner SHALL raise an error.
