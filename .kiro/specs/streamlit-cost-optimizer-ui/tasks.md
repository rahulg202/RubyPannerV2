# Tasks

- [x] 1. Project scaffold and dependencies
  - [x] 1.1 Create `app.py` with Streamlit boilerplate and page config
  - [x] 1.2 Create `requirements.txt` (or verify existing) with `streamlit`, `pandas`, `openpyxl`, `hypothesis`

- [x] 2. Core helper functions in `app.py`
  - [x] 2.1 Implement `parse_week_list(text) -> tuple[list[int], str | None]`
  - [x] 2.2 Implement `validate_inputs(...)  -> tuple[list[str], list[str]]` (errors, warnings)
  - [x] 2.3 Implement `build_params(widget_values) -> IntegratedParams`
  - [x] 2.4 Implement `export_excel_bytes(plan_df, active_df, issues_df, params, summary) -> bytes`
  - [x] 2.5 Implement `run_optimizer(file_bytes, sheet_name, params, shutdown_weeks, partial_weeks)`

- [x] 3. File upload and sheet selection UI (Requirements 1, 2)
  - [x] 3.1 Add `st.file_uploader` accepting `.xlsx`/`.xls`; store bytes in session state
  - [x] 3.2 On valid upload, read sheet names with `pd.ExcelFile` and populate a `st.selectbox`
  - [x] 3.3 Show instructional message when no file is uploaded

- [x] 4. Parameter widgets (Requirements 3, 4, 5, 6)
  - [x] 4.1 Add numeric inputs for all production constraint params (horizon_weeks, batch sizes, etc.)
  - [x] 4.2 Add numeric inputs for all cost rate params; show computed `late_penalty_rate` as read-only
  - [x] 4.3 Add sliders for `w_penalty`, `w_overtime`, `w_capacity`
  - [x] 4.4 Add text inputs for `shutdown_weeks` and `partial_shutdown_weeks`

- [x] 5. Validation feedback and Run button (Requirements 7, 10)
  - [x] 5.1 Call `validate_inputs` on every re-run; display each error with `st.error`
  - [x] 5.2 Display warnings with `st.warning`
  - [x] 5.3 Render "Run Optimization" button with `disabled=bool(errors)`

- [x] 6. Run logic and results display (Requirements 7, 8, 9)
  - [x] 6.1 On button click, clear previous results from session state, show `st.spinner`
  - [x] 6.2 Call `run_optimizer`; on exception display `st.error` and stop
  - [x] 6.3 On success, store results in session state and call `render_results`
  - [x] 6.4 Implement `render_results`: metric cards for summary, `st.dataframe` for plan, collapsible issues section, download button named `plan_output.xlsx`

- [x] 7. Unit tests (`tests/test_app.py`)
  - [x] 7.1 Test `parse_week_list`: empty string, valid list, non-integer token, negative, zero
  - [x] 7.2 Test `validate_inputs`: each error condition in isolation
  - [x] 7.3 Test `build_params`: correct field mapping
  - [x] 7.4 Test `export_excel_bytes`: output is valid Excel with expected sheet names
  - [x] 7.5 Test download filename is `"plan_output.xlsx"` and results cleared on second run

- [x] 8. Property-based tests (`tests/test_app_properties.py`)
  - [x] 8.1 Property 1 — invalid file bytes rejected by `validate_inputs`
  - [x] 8.2 Property 2 — missing sheet name rejected by `validate_inputs`
  - [x] 8.3 Property 3 — `max_batch < min_batch` produces validation error
  - [x] 8.4 Property 4 — `overtime_batches < normal_batches` produces validation error
  - [x] 8.5 Property 5 — `late_penalty_rate == penalty_rate * late_penalty_multiplier` for all valid inputs
  - [x] 8.6 Property 6 — non-integer shutdown week strings produce parse errors
  - [x] 8.7 Property 7 — week numbers exceeding `horizon_weeks` produce warnings
  - [x] 8.8 Property 8 — successful run summary contains all required numeric fields
  - [x] 8.9 Property 9 — `export_excel_bytes` returns valid parseable Excel for any valid run
  - [x] 8.10 Property 10 — all violated constraints appear as distinct entries in the error list
