# Requirements Document

## Introduction

The Onboarding Recommendation Engine is a new feature for the Production Cost Optimizer Streamlit application. It helps production planners determine the optimal start week for onboarding a batch of new generators. Given a total number of generators to onboard and a candidate week range, the engine evaluates each possible start week by solving three independent LP optimizations — one per cost objective (penalty, overtime, capacity utilisation). For each objective, the engine ranks all candidates by that objective's cost and presents the top 5 options with complete week-by-week allocation plans and batch metrics. The final output has three sections, one per objective, allowing the planner to compare trade-offs across perspectives.

## Glossary

- **Recommendation_Engine**: The subsystem that evaluates candidate start weeks, solves for optimal weekly allocation across three objectives, ranks results per objective, and presents the top options.
- **Candidate_Start_Week**: A single week within the user-specified range that is evaluated as a potential first week of onboarding production.
- **Horizon_Plan**: The complete week-by-week allocation of generators across the onboarding span for a given candidate start week under a specific objective.
- **Penalty_Cost**: The total cost incurred from early inventory holding, computed as penalty_rate × units × weeks held early.
- **Overtime_Cost**: The total cost incurred from overtime production weeks, computed as overtime_rate × number of weeks using a 3rd batch.
- **Capacity_Cost**: The total cost incurred from unused production capacity, computed as capacity_rate × unused good unit slots per week summed across the Horizon_Plan.
- **Objective**: One of the three independent cost components (Penalty_Cost, Overtime_Cost, Capacity_Cost) that the engine optimizes separately.
- **Batch**: A production run of up to 16 units (max_batch_produced), of which 1 is discarded for testing, yielding up to 15 good units (max_good_per_batch).
- **Normal_Week**: A production week allowing up to 2 batches (normal_max_batches), producing up to 30 good units.
- **Overtime_Week**: A production week allowing up to 3 batches (overtime_max_batches), producing up to 45 good units.
- **Batch_Metrics**: Summary counts of weeks using 1 batch, 2 batches, and 3 batches within a Horizon_Plan.
- **Option**: A ranked result representing one Candidate_Start_Week with its solved Horizon_Plan, Batch_Metrics, and cost for the relevant Objective.
- **IntegratedParams**: The existing parameter dataclass holding all production constraints and cost rates (penalty_rate=7000, overtime_rate=2000, capacity_rate=15000).
- **Onboarding_Span**: The number of weeks from the Candidate_Start_Week to the end of the allocation horizon.

## Requirements

### Requirement 1: User Input Collection

**User Story:** As a production planner, I want to specify the total generators to onboard and a week range, so that the engine knows the scope of the onboarding problem.

#### Acceptance Criteria

1. THE Recommendation_Engine SHALL provide input fields for total generators to onboard (positive integer), start week of the candidate range (positive integer), and end week of the candidate range (positive integer).
2. WHEN the user enters a total generators value less than 1, THE Recommendation_Engine SHALL display a validation error and prevent execution.
3. WHEN the user enters a start week greater than or equal to the end week, THE Recommendation_Engine SHALL display a validation error and prevent execution.
4. WHEN the user enters valid inputs, THE Recommendation_Engine SHALL enable the recommendation run action.

### Requirement 2: Candidate Start Week Enumeration

**User Story:** As a production planner, I want the engine to evaluate every possible start week in my specified range, so that I can find the globally best onboarding schedule.

#### Acceptance Criteria

1. WHEN the user triggers a recommendation run, THE Recommendation_Engine SHALL enumerate every integer week from the user-specified start week to end week (inclusive) as a Candidate_Start_Week.
2. FOR each Candidate_Start_Week, THE Recommendation_Engine SHALL compute a Horizon_Plan that distributes the total generators across weeks from that Candidate_Start_Week through a fixed span, respecting the weekly production capacity of 45 good units (overtime_max_good_week).
3. THE Recommendation_Engine SHALL use the existing IntegratedParams batch constraints (max_good_per_batch=15, normal_max_batches=2, overtime_max_batches=3) for all capacity calculations.

### Requirement 3: Three-Objective Independent Optimization

**User Story:** As a production planner, I want each candidate start week evaluated under three separate cost objectives, so that I can see the best schedule from each cost perspective independently.

#### Acceptance Criteria

1. FOR each Candidate_Start_Week, THE Recommendation_Engine SHALL solve three independent linear programs, one minimizing Penalty_Cost, one minimizing Overtime_Cost, and one minimizing Capacity_Cost.
2. THE Recommendation_Engine SHALL use PuLP as the LP solver, consistent with the existing project dependency.
3. THE Recommendation_Engine SHALL constrain weekly good production to a maximum of 45 units (overtime_max_good_week from IntegratedParams) in all three LP formulations.
4. THE Recommendation_Engine SHALL constrain the sum of all weekly good production across the Horizon_Plan to equal the total generators requested in all three LP formulations.
5. THE Recommendation_Engine SHALL model Penalty_Cost as penalty_rate (7000 USD from IntegratedParams) multiplied by the cumulative early inventory at each week.
6. THE Recommendation_Engine SHALL model Overtime_Cost as overtime_rate (2000 USD from IntegratedParams) multiplied by the number of weeks where production requires a 3rd batch (more than 30 good units).
7. THE Recommendation_Engine SHALL model Capacity_Cost as capacity_rate (15000 USD from IntegratedParams) multiplied by the total unused good unit slots across all weeks of the Horizon_Plan.
8. IF the total generators cannot be feasibly distributed within the Horizon_Plan for a Candidate_Start_Week (capacity exceeded), THEN THE Recommendation_Engine SHALL exclude that candidate from the results across all three objectives and log a warning.

### Requirement 4: Per-Objective Ranking and Top-5 Selection

**User Story:** As a production planner, I want the results ranked separately for each cost objective, so that I can quickly identify the best start weeks from each perspective.

#### Acceptance Criteria

1. WHEN all Candidate_Start_Weeks have been evaluated, THE Recommendation_Engine SHALL sort the feasible Options in ascending order of Penalty_Cost and select the top 5 for the penalty ranking.
2. WHEN all Candidate_Start_Weeks have been evaluated, THE Recommendation_Engine SHALL sort the feasible Options in ascending order of Overtime_Cost and select the top 5 for the overtime ranking.
3. WHEN all Candidate_Start_Weeks have been evaluated, THE Recommendation_Engine SHALL sort the feasible Options in ascending order of Capacity_Cost and select the top 5 for the capacity utilisation ranking.
4. IF fewer than 5 feasible Options exist for a given Objective, THE Recommendation_Engine SHALL present all feasible Options for that Objective.
5. IF zero feasible Options exist, THE Recommendation_Engine SHALL display a message indicating no feasible onboarding schedule was found for the given inputs.

### Requirement 5: Three-Section Side-by-Side Horizon Plan Display

**User Story:** As a production planner, I want to see three separate display sections (one per objective), each showing the top 5 candidates side-by-side with full week-by-week plans, so that I can compare schedules within and across objectives.

#### Acceptance Criteria

1. THE Recommendation_Engine SHALL display three distinct sections or tabs labeled "Top 5 by Penalty", "Top 5 by Overtime", and "Top 5 by Capacity Utilisation".
2. WITHIN each section, THE Recommendation_Engine SHALL display the top Options side-by-side, with each Option occupying a distinct column.
3. FOR each Option, THE Recommendation_Engine SHALL display the Horizon_Plan as a table with one row per week showing: week number and good units produced that week.
4. FOR each Option, THE Recommendation_Engine SHALL display the Candidate_Start_Week as a column header or label.
5. WHEN an Option has weeks with zero production (before the Candidate_Start_Week or after allocation completes), THE Recommendation_Engine SHALL display 0 for those weeks.

### Requirement 6: Batch Metrics Display per Objective

**User Story:** As a production planner, I want to see batch breakdown metrics and the relevant cost for each option within each objective section, so that I can assess overtime and workload implications.

#### Acceptance Criteria

1. FOR each Option within each Objective section, THE Recommendation_Engine SHALL compute and display the count of weeks with exactly 1 batch (1–15 good units).
2. FOR each Option within each Objective section, THE Recommendation_Engine SHALL compute and display the count of weeks with exactly 2 batches (16–30 good units).
3. FOR each Option within each Objective section, THE Recommendation_Engine SHALL compute and display the count of weeks with exactly 3 batches (31–45 good units).
4. FOR each Option in the penalty section, THE Recommendation_Engine SHALL display the total Penalty_Cost formatted in thousands of USD (e.g., "$28K").
5. FOR each Option in the overtime section, THE Recommendation_Engine SHALL display the total Overtime_Cost formatted in thousands of USD (e.g., "$6K").
6. FOR each Option in the capacity utilisation section, THE Recommendation_Engine SHALL display the total Capacity_Cost formatted in thousands of USD (e.g., "$120K").
7. THE Recommendation_Engine SHALL use the existing batches_needed() function from integrated_cost_optimizer.py to determine batch counts per week.

### Requirement 7: Integration with Existing Application

**User Story:** As a production planner, I want the onboarding recommendation engine accessible within the existing Streamlit app, so that I have a unified tool.

#### Acceptance Criteria

1. THE Recommendation_Engine SHALL be accessible as a new section or tab within the existing app.py Streamlit application.
2. THE Recommendation_Engine SHALL reuse IntegratedParams for all production constraint and cost rate values.
3. THE Recommendation_Engine SHALL reuse the batches_needed() and split_good_into_batches() utility functions from integrated_cost_optimizer.py.
4. WHEN the Recommendation_Engine is active, THE existing optimizer functionality SHALL remain fully operational and unaffected.

### Requirement 8: Results Export

**User Story:** As a production planner, I want to download the recommendation results covering all three objectives, so that I can share the full analysis with stakeholders.

#### Acceptance Criteria

1. THE Recommendation_Engine SHALL provide a download button for the recommendation results as an Excel file.
2. THE exported Excel file SHALL contain one sheet per Objective ("Penalty", "Overtime", "Capacity"), each listing the top 5 Options with their complete Horizon_Plan including week number, good units produced, batch count, and cumulative inventory.
3. THE exported Excel file SHALL contain a summary sheet with all three Objectives' top 5 Options showing Batch_Metrics and the relevant cost side-by-side.
