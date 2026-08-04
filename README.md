# Ruby Fill Optimizer

Plans 52 weeks of Ruby Fill generator production for ~180 hospital customers,
minimising a weighted cost of early/late penalties, overtime, unused capacity,
and raw-material supplier quota shortfalls.

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

The application is one Streamlit app with four tabs. Settings apply everywhere.

| Tab | What it does |
|---|---|
| **Settings** | Every cost, rate, weight, and constraint — production limits, cost rates and weights, QC shipping cap, supplier parameters, and the reference week. Values last for the session; use *Restore all defaults* to reset. |
| **Cost Optimizer** | Upload the sites file and run. Shows the weekly plan with manufacturing and calibration dates, supplier allocation, quarterly quota status, and which customers had their production week moved. Downloads a single workbook containing everything. |
| **Onboarding** | Add several new customers at once, each with its own earliest and latest permissible start week. Ranks the options and hands back a ready-to-use input file with the new customers included. |
| **Comparison** | Upload the Master Planner workbook to see what the optimizer saved against the plan your team built by hand. |

### Typical sequence

1. **Settings** — set the reference week so the plan shows real dates; confirm cost rates.
2. **Cost Optimizer** — upload the sites file, run, review the plan and changed weeks.
3. **Comparison** — upload the Master Planner to quantify the saving.
4. **Onboarding** — evaluate new customers, download the updated input file, then
   re-run the Cost Optimizer with it.

## Input file contract

Required columns (header names are case- and space-insensitive):

| Column | Notes |
|---|---|
| `Site_ID` | Unique per active row. Read as text, so account codes keep leading zeros (`00449` stays `00449`). |
| `Active` | `Y`/`YES`/`TRUE`/`1` means active; anything else is ignored. |
| `Next_Demand_Week` | 1..horizon. |
| `Interval_Weeks` | >= 1. Weeks between replacements. |

Optional: `Country`, `Site_Name`, `EU_Restricted`. Any other columns pass through
untouched. Data-quality problems are reported in the `Input_Issues` sheet rather
than failing the run.

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

## Things worth knowing

- **Unused-capacity cost is mostly a fixed floor.** Annual demand sits well below
  annual capacity, so some weeks are always under-filled regardless of the plan.
  A high capacity weight can therefore mask large penalty savings. Read the
  penalty and overtime components when judging the optimizer's value.
- **A generator produced early still counts as a changed week.** A unit due in
  week 10 but made in week 8 and held in stock appears in `Changed_Weeks`, because
  the production week moved even though the customer is served on time.
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

## Architecture

Four layers, dependencies pointing inward. See `.kiro/specs/ARCHITECTURE.md`.

```
ui/            Streamlit only — no business logic
services/      use-case orchestration, depends on port protocols
domain/        pure business logic: solver, costs, suppliers, assignment, dates
io_adapters/   all file I/O; the only place openpyxl is imported
```

- `domain/` is deterministic and side-effect free, so it is unit- and
  property-testable without files or a UI.
- `services/` depends on the interfaces in `services/ports.py`, never on concrete
  adapters. `app.py` is the single place adapters are injected.
- `integrated_cost_optimizer.py` and `onboarding_recommendation.py` remain as
  backward-compatible re-export shims plus the CLI.
- `legacy/` holds superseded one-off analysis scripts, excluded from the package.

## Tests

```bash
pytest
```

Organised by layer: `tests/domain` (unit + property), `tests/services` (with
in-memory fake adapters), `tests/io` (fixture files), `tests/ui` (smoke and
layering guards), and `tests/integration` (end-to-end, onboarding timing, and
backward compatibility).
