# Requirements Document

## Introduction

A Streamlit web application that wraps the existing `integrated_cost_optimizer.py` solver.
The app allows users to upload an Excel input file, configure all optimizer parameters through
an interactive UI, run the optimization, and download the resulting Excel output — without
needing to use the command line.

## Glossary

- **App**: The Streamlit web application described in this document.
- **Optimizer**: The `integrated_cost_optimizer.py` module containing `IntegratedParams`,
  `read_sites`, `clean_sites`, `build_weekly_demand`, `build_weekly_row_demand`,
  `solve_plan_integrated`, and `export_excel`.
- **Params**: An instance of `IntegratedParams` constructed from user-supplied values.
- **Sites_Sheet**: The Excel worksheet name that contains site data (default: `"Sites"`).
- **Shutdown_Weeks**: A comma-separated list of week numbers during which production is fully halted.
- **Partial_Shutdown_Weeks**: A comma-separated list of week numbers during which only partial production is allowed.
- **Plan_DF**: The weekly plan DataFrame returned by `solve_plan_integrated`.
- **Summary**: The cost summary dictionary returned by `solve_plan_integrated`.
- **Output_File**: An in-memory Excel workbook produced by `export_excel` and offered for download.

---

## Requirements

### Requirement 1: Excel File Upload

**User Story:** As a planner, I want to upload an Excel file through the browser, so that I can provide site data without accessing the server filesystem.

#### Acceptance Criteria

1. THE App SHALL provide a file-upload widget that accepts `.xlsx` and `.xls` files.
2. WHEN a file is uploaded, THE App SHALL read the file from memory without writing it to disk.
3. WHEN no file has been uploaded, THE App SHALL display an instructional message and disable the Run button.
4. IF an uploaded file cannot be parsed as a valid Excel workbook, THEN THE App SHALL display a descriptive error message and disable the Run button.

---

### Requirement 2: Sites Sheet Selection

**User Story:** As a planner, I want to specify which sheet in the Excel file contains site data, so that I can use workbooks with multiple sheets.

#### Acceptance Criteria

1. THE App SHALL provide a text input for the Sites_Sheet name with a default value of `"Sites"`.
2. WHEN a valid Excel file is uploaded, THE App SHALL list available sheet names and allow the user to select one from a dropdown.
3. IF the selected sheet name does not exist in the uploaded workbook, THEN THE App SHALL display a descriptive error and disable the Run button.

---

### Requirement 3: Production Constraint Parameters

**User Story:** As a planner, I want to configure production batch constraints, so that the optimizer reflects real manufacturing limits.

#### Acceptance Criteria

1. THE App SHALL provide a numeric input for `horizon_weeks` with a default of `52`, a minimum of `1`, and a maximum of `104`.
2. THE App SHALL provide a numeric input for `min_batch_produced` with a default of `2` and a minimum of `1`.
3. THE App SHALL provide a numeric input for `max_batch_produced` with a default of `16` and a minimum of `1`.
4. THE App SHALL provide a numeric input for `test_discard_per_batch` with a default of `1` and a minimum of `0`.
5. THE App SHALL provide a numeric input for `normal_max_batches` with a default of `2` and a minimum of `1`.
6. THE App SHALL provide a numeric input for `overtime_max_batches` with a default of `3` and a minimum of `1`.
7. THE App SHALL provide a numeric input for `row_cap` with a default of `2` and a minimum of `0`.
8. IF `max_batch_produced` is less than `min_batch_produced`, THEN THE App SHALL display a validation error and disable the Run button.
9. IF `overtime_max_batches` is less than `normal_max_batches`, THEN THE App SHALL display a validation error and disable the Run button.

---

### Requirement 4: Cost Rate Parameters

**User Story:** As a planner, I want to set cost rates, so that the optimizer uses financially accurate penalty and overtime values.

#### Acceptance Criteria

1. THE App SHALL provide a numeric input for `penalty_rate` (USD per unit-week) with a default of `7000.0` and a minimum of `0.0`.
2. THE App SHALL provide a numeric input for `late_penalty_multiplier` with a default of `100.0` and a minimum of `1.0`.
3. THE App SHALL provide a numeric input for `overtime_rate` (USD per overtime week) with a default of `2000.0` and a minimum of `0.0`.
4. THE App SHALL provide a numeric input for `capacity_rate` (USD per unused slot per week) with a default of `15000.0` and a minimum of `0.0`.
5. WHEN any cost rate is changed, THE App SHALL display the derived `late_penalty_rate` (penalty_rate × late_penalty_multiplier) as a read-only computed field.

---

### Requirement 5: Objective Weight Parameters

**User Story:** As a planner, I want to set the three objective weights, so that I can balance penalty, overtime, and capacity costs in the optimization.

#### Acceptance Criteria

1. THE App SHALL provide a slider for `w_penalty` in the range `[0.0, 1.0]` with a default of `1.0` and a step of `0.05`.
2. THE App SHALL provide a slider for `w_overtime` in the range `[0.0, 1.0]` with a default of `1.0` and a step of `0.05`.
3. THE App SHALL provide a slider for `w_capacity` in the range `[0.0, 1.0]` with a default of `1.0` and a step of `0.05`.
4. IF all three weights are `0.0`, THEN THE App SHALL display a validation error stating that at least one weight must be non-zero and disable the Run button.

---

### Requirement 6: Shutdown Week Configuration

**User Story:** As a planner, I want to specify full and partial shutdown weeks, so that the optimizer respects planned production halts.

#### Acceptance Criteria

1. THE App SHALL provide a text input for Shutdown_Weeks accepting a comma-separated list of integers (e.g. `"1,2,3"`).
2. THE App SHALL provide a text input for Partial_Shutdown_Weeks accepting a comma-separated list of integers.
3. WHEN the Shutdown_Weeks input is empty, THE App SHALL treat it as an empty list with no shutdown weeks.
4. IF a Shutdown_Weeks or Partial_Shutdown_Weeks entry is not a valid positive integer, THEN THE App SHALL display a descriptive parse error and disable the Run button.
5. IF a week number in Shutdown_Weeks or Partial_Shutdown_Weeks exceeds `horizon_weeks`, THEN THE App SHALL display a warning message.

---

### Requirement 7: Run Optimization

**User Story:** As a planner, I want to trigger the optimization with a single button click, so that I can generate the production plan without using the command line.

#### Acceptance Criteria

1. THE App SHALL provide a "Run Optimization" button that is enabled only when all inputs are valid and a file has been uploaded.
2. WHEN the Run button is clicked, THE App SHALL construct a Params instance from the current UI values and invoke the Optimizer.
3. WHILE the Optimizer is running, THE App SHALL display a progress spinner with a status message.
4. IF the Optimizer raises an exception, THEN THE App SHALL display the exception message in an error box and not offer a download.
5. WHEN the Optimizer completes successfully, THE App SHALL display the Summary metrics and enable the download button.

---

### Requirement 8: Results Display

**User Story:** As a planner, I want to see a summary of the optimization results in the browser, so that I can quickly assess the plan quality without opening the output file.

#### Acceptance Criteria

1. WHEN the Optimizer completes successfully, THE App SHALL display the total composite cost in USD.
2. WHEN the Optimizer completes successfully, THE App SHALL display the penalty, overtime, and capacity cost components individually.
3. WHEN the Optimizer completes successfully, THE App SHALL display the number of overtime weeks.
4. WHEN the Optimizer completes successfully, THE App SHALL display the number of active sites used.
5. WHEN the Optimizer completes successfully, THE App SHALL render the Plan_DF as an interactive table in the browser.

---

### Requirement 9: Output File Download

**User Story:** As a planner, I want to download the output Excel file from the browser, so that I can share and archive the production plan.

#### Acceptance Criteria

1. WHEN the Optimizer completes successfully, THE App SHALL generate the Output_File in memory using `export_excel`.
2. THE App SHALL provide a "Download Results" button that triggers a browser download of the Output_File as a `.xlsx` file.
3. THE App SHALL name the downloaded file `plan_output.xlsx` by default.
4. WHEN the Run button is clicked again, THE App SHALL clear the previous Output_File and results before starting a new run.

---

### Requirement 10: Input Validation Feedback

**User Story:** As a planner, I want clear validation messages for all inputs, so that I can correct mistakes before running the optimizer.

#### Acceptance Criteria

1. THE App SHALL validate all parameter inputs in real time as values are changed.
2. WHEN one or more validation errors exist, THE App SHALL display each error message distinctly and keep the Run button disabled.
3. WHEN all validation errors are resolved, THE App SHALL enable the Run button automatically.
4. THE App SHALL display the data-quality issues found by `clean_sites` (the Issues_DF) in a collapsible section after a successful run.
