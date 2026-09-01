# Ruby Fill Optimizer

Plans 52 weeks of Ruby Fill generator production for ~180 hospital customers,
minimising a weighted cost of early/late penalties, overtime, unused capacity,
and raw-material supplier quota shortfalls.

**For planners:** see **[USER_GUIDE.md](USER_GUIDE.md)** — every screen, every
parameter, and every output column in plain language.

**For validating the model:** see **[TOOL_LOGIC.md](TOOL_LOGIC.md)** — assumptions,
business rules, the objective function, the optimization method, worked scenarios on
real data, and the development process. Section 1 lists every assumption that needs
business confirmation.

The rest of this README is for developers.

## Running the app

```bash
pip install -r requirements.txt
streamlit run app.py
```

The command-line optimizer is also available:

```bash
python integrated_cost_optimizer.py --input sites.xlsx --output plan.xlsx --print-summary
```

## The unified workflow

The application is one Streamlit app with five tabs. Settings apply everywhere.

| Tab | What it does |
|---|---|
| **Settings** | Every cost, rate, weight, and constraint — production limits, cost rates and weights, QC shipping cap, supplier parameters, and the reference week. Values last for the session; use *Restore all defaults* to reset. |
| **Import Manual Plan** | Reads the wide Master Planner and writes the optimizer's input sheet: one row per site with its code, first demand week, interval, country, and EU-restricted flag. Also emits a code-to-column mapping, which is what lets the Comparison tab match the two files. |
| **Cost Optimizer** | Upload the sites file and run. Shows the weekly plan with manufacturing and calibration dates, supplier allocation, quarterly quota status, and which customers had their production week moved. Downloads a single workbook containing everything. |
| **Onboarding** | Add several new customers at once, each with its own permissible start window. Two modes: re-plan all 52 weeks, or hold the existing plan fixed and price slotting the site in. Hands back a ready-to-use input file with the new customers included. |
| **Comparison** | Upload the Master Planner workbook to see what the optimizer saved against the plan your team built by hand. |

### Typical sequence

1. **Settings** — set the reference week so the plan shows real dates; confirm cost rates.
2. **Import Manual Plan** — upload the Master Planner, build the input file, push it
   straight into the Cost Optimizer.
3. **Cost Optimizer** — run, review the plan and changed weeks.
4. **Comparison** — quantify the saving against the manual plan.
5. **Onboarding** — evaluate new customers, download the updated input file, then
   re-run the Cost Optimizer with it.

## Input file contract

Required columns (header names are case- and space-insensitive):

| Column | Notes |
|---|---|
| `Site_ID` | Unique per active row. Read as text, so account codes keep leading zeros (`00449` stays `00449`). |
| `Active` | `Y`/`YES`/`TRUE`/`1` means active; anything else is ignored. |
| `Next_Demand_Week` | 1..horizon. |
| `Interval_Weeks` | Weeks between replacements. `0` means a single one-time delivery at `Next_Demand_Week`. |

Optional: `Country`, `Site_Name`, `EU_Restricted`. Any other columns pass through
untouched. Data-quality problems are reported in the `Input_Issues` sheet rather
than failing the run.

The **Import Manual Plan** tab generates a conforming file straight from the
Master Planner, so this contract rarely has to be satisfied by hand.

## Output workbook

| Sheet | Contents |
|---|---|
| `Weekly_Plan` | Week, manufacturing and calibration dates, production, batches, inventory, supplier allocation, Sr-82 activity, costs |
| `Sites_Clean` | The validated active sites used |
| `Input_Issues` | Data-quality problems found on load |
| `Model_Params` | Every parameter used, so a run is reproducible |
| `Changed_Weeks` | Per-customer scheduled vs planned week (green = early, red = late, blue = new customer) |
| `Quota_Status` | Supplier quota per quarter; shortfall rows highlighted |
| `Cost_Comparison` | Manual plan vs optimized, per cost component |
| `Weekly_Comparison` | Manual vs optimized production per week |
| `Assigned_IDs` | Generated identifiers for Master Planner customers without an account number |

Sheets appear only when the run produced that data — no manual plan means no
`Changed_Weeks`.

## Supplier run sequencing

Curium runs first and exactly once; every later run in the week is BWXT.

| Runs | Sequence | Curium | BWXT |
|---|---|---|---|
| 1 | `Curium` | all | 0 |
| 2 | `Curium, BWXT` | `first_run_allocation` (15) | remainder |
| 3 | `Curium, BWXT, BWXT` | 15 | remainder |

Curium's share is lifted above 15 only when a week's EU-restricted demand exceeds it,
capped at one batch — a larger share would need a second Curium run, breaking the
agreed pattern and exceeding the run count the solver planned. Beyond one batch the
week is flagged via `eu_constraint_satisfied` rather than the allocator inventing a
run. A supplier marked unavailable for a week yields the whole week to the other one.

This replaced `Curium, BWXT, Curium` in round 2 at the business's request. On the
reference workbook it moved ~5,700 mCi from Curium to BWXT and left every quarter
comfortably above quota, so no shortfall penalty was triggered.

## Things worth knowing

- **Unused-capacity cost is mostly a fixed floor.** Annual demand sits well below
  annual capacity, so some weeks are always under-filled regardless of the plan.
  A high capacity weight can therefore mask large penalty savings. Read the
  penalty and overtime components when judging the optimizer's value.
- **A generator produced early still counts as a changed week.** A unit due in
  week 10 but made in week 8 and held in stock appears in `Changed_Weeks`, because
  the production week moved even though the customer is served on time.
- **Which site is recorded as moved is a modelling choice.** The solver picks weekly
  totals; `delivery_assignment` then hands anonymous generators to customers. Among
  sites due the same week, the attribution is deterministic but arbitrary. Totals are
  reliable; a single row is indicative.
- **Supplier allocation happens after the solve.** `solve_with_suppliers` runs the DP
  first, then applies the run-sequencing rule. So changing that rule alters the
  Curium/BWXT split and the Sr-82 totals but leaves the production plan and the total
  cost untouched. If supplier economics should influence *which* weeks produce what,
  that is a change of scope.
- **Interval 0 means one delivery.** Handled in `demand`, `delivery_assignment`,
  `onboarding` and the converter. Onboarding validation used to require `>= 1`, which
  silently made those customers impossible to evaluate.
- **Onboarding search.** Up to 500 combinations are evaluated exhaustively and the
  result is the true optimum. Above that a heuristic runs and the app says so —
  the answer is a strong candidate, not a proven optimum.

### Partial quarters and supplier quotas

Supplier quotas are commitments over real calendar quarters. A 52-week plan only
lines up with whole quarters when it **starts on a quarter boundary**. Start
anywhere else and the first and last quarters are cut short:

```
reference week 2026-01-05   ->  Q1..Q4, each 13/13 weeks      (all complete)
reference week 2026-07-27   ->  Q1 10/13 ... Q5 3/13 weeks    (two partial)
```

**A partial quarter is not judged for quota compliance, and carries no penalty.**
The reason is that the missing weeks are real but invisible to the plan:

- the **leading** partial quarter is missing weeks in the *past*, where orders
  have already been placed — that history lives in SAP, not in the planner;
- the **trailing** partial quarter is missing weeks in the *future*, beyond
  week 52, where ordering will continue.

Charging a full quarterly quota against a three-week fragment would invent a
shortfall that does not exist commercially. Worse, because the shortfall penalty
is deliberately punitive, that phantom figure would dominate the objective and
push the optimizer toward decisions taken for a made-up reason. So partial
quarters contribute **zero** to cost.

They are still shown, never hidden. The Quota Status table reports coverage
(`3/13 wks`), the activity ordered inside the horizon, and a **pro-rated target**
scaled to the weeks covered — a run-rate reference only, not a compliance test.
Such rows are labelled `Partial — not penalised` and are not highlighted as
breaches in the export.

**What this means for you as a planner.** Quota figures for partial quarters are
indicative; judge them on run rate, not on the gap. To get four fully-covered,
fully-checked quarters, set the reference week to the first week of a quarter —
the Settings tab tells you when your chosen week produces partial quarters. For
reference, current demand runs at roughly twice the quota rate (~1,600 mCi/week
against a 769 mCi/week requirement), so the quota check behaves as an exception
detector for unusual situations — a long supplier outage, a demand trough — rather
than a routine cost.

> The shortfall rate itself (`quota_shortfall_penalty_rate`, default
> $50,000/mCi) is a **placeholder** set high to make a breach effectively
> unacceptable, pending the real charge from the business.
- **Master Planner matching.** Customers are matched by the leading account number
  in each Schedule-sheet column. Columns without one receive a stable generated
  identifier (`RF-…`) listed in `Assigned_IDs`; share that mapping with the
  planning team so the same IDs can be used in future input sheets. If few sites
  match, the app warns rather than reporting every customer as new.
- **Generating the input file guarantees a match.** The Import Manual Plan tab
  derives site codes with the same scheme the Master Planner parser uses, so a
  generated input file lines up with the manual plan without any manual step. On
  the reference workbook, 202 of 203 columns match; the one exception is a
  duplicated account number, which is renamed and reported rather than silently
  merged.

## Deriving the input file from the Master Planner

`Import Manual Plan` inverts the wide sheet (a row per week, a column per site,
`1` marking a scheduled generator) into a row per site.

| Field | Derivation |
|---|---|
| `Site_ID` | Leading account number, else a stable `RF-<sha1_8>` code. Duplicates get a `-2` suffix and a warning. |
| `Next_Demand_Week` | First week carrying a mark. |
| `Interval_Weeks` | The `(N)` in the header; else the modal gap between marks; else `0` (one-time). Header wins, and a disagreement with the observed gaps is reported. |
| `Country` | Word-boundary match on country and city tokens in the header, defaulting to `usa`. Word boundaries matter — "Swedish Medical Center, Bronx, NY" is a US site. |
| `EU_Restricted` | Header cell shaded `FF002060`. Authoritative; a shaded column whose text implies a non-restricted country is flagged. |
| `Active` | Whether the column has any mark in the selected year. |

Rules live in `domain/site_derivation.py` (pure), workbook access in
`io_adapters/master_planner_converter.py`, orchestration in
`services/conversion_service.py`.

### Generated input file

| Sheet | Contents |
|---|---|
| `Sites` | Conforms to the input contract above; upload as-is |
| `Site_Mapping` | Site code, Master Planner column letter and header, code and interval provenance |
| `Conversion_Notes` | One row per thing the planner should check |

### Making the codes permanent

Codes for columns without an account number are derived by hashing the header text,
so editing a header changes the code and the site silently stops matching.
`MasterPlannerAnnotator` returns a **copy** of the workbook with those codes written
into the headers, after which they are read verbatim. The copy is opened without
`data_only`, so formulas survive; the original is never modified.

Both `master_planner_parser` and `site_derivation` recognise a leading
`RF-<hex>` and use it as-is. That matters more than it looks: without it, annotating
a workbook re-hashed the header text it had just changed and produced codes matching
nothing — the operation broke exactly what it was meant to fix.

## Anchoring planning week 1

The Master Planner restarts its week numbering every January, so reading by calendar
year forces the plan to start in January and leaves the anchor unstated. Supplying a
reference week makes the row holding that date planning week 1, and the horizon runs
forward from it across the year boundary.

`domain/week_anchor.py` is the single implementation, called by both the converter and
the parser. **Keep it shared.** Anchoring one side only offsets every per-customer
week by however far into the year the reference sits; measured on the reference
workbook, that produced 1,186 fabricated changed weeks against 60 real ones.

A date inside a week selects that week. A date in a gap snaps to the closest earlier
week and says so. A date past the last row leaves the window unresolved and the caller
falls back to reading by year rather than returning nothing.

## Architecture

Four layers, dependencies pointing inward. See `.kiro/specs/ARCHITECTURE.md`.

```
ui/            Streamlit only — no business logic
services/      use-case orchestration, depends on port protocols
domain/        pure business logic: solver, costs, suppliers, assignment, dates
io_adapters/   all file I/O; the only place openpyxl is imported
```

Notable domain modules:

| Module | Responsibility |
|---|---|
| `solver.py` | DP over weekly inventory; the optimizer proper |
| `supplier_allocation.py` | Curium/BWXT run sequencing and Sr-82 activity |
| `quota.py` | Quarterly quota status, including partial-quarter handling |
| `delivery_assignment.py` | Disaggregates weekly totals to individual customers |
| `site_derivation.py` | Master Planner columns → site rows (pure rules) |
| `site_matching.py` | Reconciles input rows against Master Planner columns |
| `week_anchor.py` | Anchors planning week 1 to a real date; shared by both readers |
| `fixed_plan_onboarding.py` | Onboarding against a plan held fixed |
| `dates.py` | Week number ↔ calendar date |

- `domain/` is deterministic and side-effect free, so it is unit- and
  property-testable without files or a UI.
- `services/` depends on the interfaces in `services/ports.py`, never on concrete
  adapters. `app.py` is the single place adapters are injected.
- `integrated_cost_optimizer.py` and `onboarding_recommendation.py` remain as
  backward-compatible re-export shims plus the CLI.
- `legacy/` holds superseded scripts and two obsolete guides, excluded from the
  package. See `legacy/README.md`; nothing there describes the current application.

## Tests

```bash
pytest
```

680 tests, organised by layer:

| Suite | Tests | Covers |
|---|---|---|
| `tests/domain` | 311 | unit plus property-based |
| `tests/io` | 114 | synthetic fixture workbooks — no customer data |
| `tests/services` | 86 | orchestration against in-memory fake adapters |
| `tests/ui` | 79 | smoke, layering guards, week-input conversions |
| `tests/integration` | 63 | end-to-end, plus real-Streamlit render tests |

Two things worth knowing before adding tests.

**The Streamlit stub accepts anything.** `tests/conftest.py` swaps Streamlit for a
`MagicMock` so the UI imports without a runtime. That is convenient and it is also
how 574 tests once passed against an app that crashed on load —
`st.data_editor(width="stretch")` was valid on the deployed version and invalid on
the local one, and the stub cared about neither. Widget work must also be covered by
`tests/integration/test_app_real_streamlit.py`, which renders `app.py` through
`streamlit.testing.v1.AppTest` in a subprocess against the genuine API.

**Hypothesis runs with no per-example deadline.** A single 52-week solve takes
220–300ms, over the 200ms default, so solver-backed property tests were passing or
failing depending on machine load. The profile is registered in `tests/conftest.py`.

Dependencies in `requirements.txt` are pinned exactly, for the reason above. Bump
them deliberately and re-run the suite.
