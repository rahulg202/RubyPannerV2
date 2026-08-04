# Session Handoff — Ruby Fill Optimizer

**Date:** 2026-07-27
**State:** All 14 planned phases complete. 479 tests passing. App runs clean.

---

## 1. What this session did

Two things, in order:

1. **Analysed the existing codebase** and wrote requirements + design + task specs
   for two new feature sets from the business (supplier constraints, and six
   optimizer enhancements).
2. **Implemented all of it**, including a non-breaking refactor of the original
   monolith into a layered architecture.

Starting point: `app.py` at 993 lines, `integrated_cost_optimizer.py` at 1013
lines, 112 tests, specs that described a PuLP LP engine the code no longer used.

Ending point: layered packages, `app.py` at 84 lines of pure wiring, 479 tests.

---

## 2. Current structure

```
domain/         13 files, 2,631 lines   pure business logic, no I/O
services/        7 files,   827 lines   orchestration, depends on ports only
io_adapters/     5 files, 1,072 lines   all file I/O; only place openpyxl appears
ui/              7 files, 1,369 lines   Streamlit only, no business logic
tests/                    6,014 lines   by layer + integration
app.py                       84 lines   wires adapters into services, renders tabs
legacy/                                 archived one-off scripts
```

**Dependency rule:** `ui → services → domain`, and `services → adapters` via port
protocols in `services/ports.py`. `domain` imports nothing above it. There are
tests asserting each of these; they will fail if the rule is broken.

Run everything with `pytest`. Run the app with `streamlit run app.py`.

### Naming deviation worth knowing

The adapter package is **`io_adapters/`**, not `io/`. A top-level `io` package
shadows Python's standard-library `io` module, which the codebase uses via
`io.BytesIO`. All spec documents that say "the `io/` layer" mean `io_adapters/`.

---

## 3. Features delivered

| Feature | Where |
|---|---|
| Supplier constraints (Curium/BWXT, Sr-82 activity, run sequencing, quotas) | `domain/supplier_allocation.py`, `domain/quota.py`, `domain/supplier_solve.py` |
| Manual-plan vs optimized cost comparison | `domain/comparison.py`, `io_adapters/master_planner_parser.py` |
| Changed customer weeks | `domain/delivery_assignment.py` |
| Multi-customer onboarding, independent windows | `domain/onboarding.py` |
| Generated optimizer input file | `io_adapters/input_file_writer.py` |
| Reference week / calendar dates | `domain/dates.py` |
| QC shipping cap (`row_cap`) enforcement | `domain/solver.py` |
| Full parameter configurability | `services/settings_service.py`, `ui/tab_settings.py` |
| Unified 9-sheet export | `io_adapters/workbook_exporter.py` |

The UI is four tabs: Settings, Cost Optimizer, Onboarding, Comparison.

---

## 4. Decisions the business made (do not silently revisit)

| Decision | Answer given |
|---|---|
| Comparison baseline | The planner's **manual plan** from the Master Planner — not the input sheet's due dates |
| What counts as a changed week | The **production** week moving. A unit due wk 10, made wk 8, held in stock **is** changed |
| Quarter definition | Configurable start month, default January |
| `row_cap` vs BWXT rule | **Two independent constraints** that happen to hit the same customers. `row_cap` is QC throughput; BWXT is material sourcing |
| Multi-customer onboarding | **Independent** start weeks, each customer with its own earliest/latest window |
| Parameters | Everything configurable from the front end, nothing hardcoded |
| Surplus percentages | 5% Curium, 2% BWXT. The old "extra 1%" label was stale |
| Split-week first run | Always Curium, 15 generators (1,586 mCi) |
| Three-run weeks | **Only** Curium → BWXT → Curium |
| QC generators | One per batch (the test discard) at 10 mCi. No extras |
| Inventory provenance | Tracked by Quality outside the tool. Not modelled |
| Site_ID for new customers | Planner enters it manually — it's the elution system serial, never auto-generated |
| Master Planner matching | Leading account number in the column header (e.g. `00449`) |
| Partial quarters | Excluded from the quota penalty, still reported with a pro-rated run-rate target |

---

## 5. Bugs found and fixed (with why they mattered)

**Leading zeros stripped from `Site_ID`.** pandas read `00449` as integer `449`.
Since the Master Planner identifies customers by zero-padded account codes, every
per-customer comparison silently failed to match and reported all existing
customers as new. Fixed in `io_adapters/sites_reader.py` and
`input_file_writer.py` by forcing identifier columns to text. Found only during
integration testing — unit tests couldn't see it.

**`row_cap` was never enforced.** It was printed in the output but the solver
ignored it; 17 weeks in the real data breached it. Now enforced via an inventory
floor. This **raised the reported penalty from $581k to $910k** — the old figure
was cheaper because the plan was quietly infeasible.

**Partial-quarter phantom penalty.** A 52-week horizon that doesn't start on a
quarter boundary produced partial quarters charged a full quarterly quota,
generating a **$499.7M penalty on a $4.33M plan**. Now excluded from cost.

**Master Planner customer cells are flags, not quantities.** They hold the integer
`1` to mark a scheduled generator. Summing them gave 67,139 against 1,383 actual
demand. Counting cells equal to `1` reconciles exactly.

**`NaN` is truthy.** `int(row.get(x, 1) or 1)` crashed the onboarding tab when a
new editor row arrived with `NaN` cells, because `NaN or 1` returns `NaN`. Fixed
with explicit coercion helpers.

**Year-picker chose an incomplete year.** The Master Planner repeats week numbers
per fiscal year; the parser initially picked a partly-filled 2027 (24 weeks). Now
selects the year with the most weeks carrying demand.

**`Saving_Pct` written as a bare value** became a blank `NaN` cell for a zero
baseline. Now written as text (`"n/a"` / `"60.00%"`). Note: pandas `read_excel`
treats the literal string `"n/a"` as a missing value, so tests assert at cell
level via openpyxl.

---

## 6. Open items — needs the business, not code

**Blocking nothing right now, but values are placeholders:**

1. **Quota shortfall rate.** `quota_shortfall_penalty_rate` defaults to
   **$50,000/mCi — a placeholder**, set high so a breach reads as unacceptable.
   The real charge per mCi is still outstanding. Any genuine shortfall will
   currently dwarf every other cost.
2. **Quarterly quota values.** 10,000 mCi per supplier is from a 6-week sample
   sheet. Unconfirmed.
3. **Split-week allocation: percentage vs count.** The one-pager asked for an
   "allocated **percentage**"; it is implemented as a **generator count**
   (`first_run_allocation`, default 15) because the business confirmed "always
   Curium 15 gen". These are not equivalent — at 20 generators a count gives
   Curium 15/BWXT 5, a 50% share gives 10/10. **Awaiting a decision:** keep the
   count, switch to percentage, or support both with a mode toggle.
4. **`Assigned_IDs` mapping.** The parser generates stable `RF-<hash>` identifiers
   for the ~37 Master Planner columns lacking an account number. That sheet is
   meant to be shared with the planning team so those IDs can be used as
   `Site_ID` in future input sheets. Until that happens, per-customer comparison
   against the Master Planner will not match.

---

## 7. Known tech debt

**Two onboarding implementations coexist.**
`onboarding_recommendation.py` (380 lines, root) is the original single-customer
marginal-cost engine. `domain/onboarding.py` is the new multi-customer engine that
the app actually uses. The old module is **not referenced by the live app** but is
still imported by `tests/test_onboarding.py` and
`tests/test_onboarding_properties.py` at the tests root.

Recommendation: fold the old module into a re-export shim over
`domain/onboarding.py`, or delete it and retire those two test files after
checking nothing in them covers behaviour the new engine lacks. Precedent for
doing this properly exists — see
`tests/services/test_legacy_app_behaviour.py`, where the old `app.py` tests were
retargeted to their new homes rather than deleted.

**Other smaller items:**
- Root-level `tests/test_onboarding*.py` should move under `tests/domain/` or be
  retired, for consistency with the by-layer layout.
- Generated output workbooks (`plan_out*.xlsx`, `onboarding_*.xlsx`) sit in the
  repo root. `.gitignore` covers them but the repo is **not** a git repository
  yet, so they were left in place rather than deleted.
- No CI configuration and no linter config.

---

## 8. Domain facts worth carrying forward

These came out of analysis and shaped several decisions.

- **Unused-capacity cost is mostly a fixed floor.** On the real dataset it is 81%
  of total composite cost, and ~95% of that is unavoidable: annual demand (1,346
  units) sits well below annual normal capacity (1,560). A high `w_capacity` masks
  real penalty savings. The CLI defaults it to 0.0 while the UI defaults it to 1.0
  — **that inconsistency still exists** and is worth resolving.
- **Onboarding usually looks "free" or better.** Deltas come out negative because
  new demand fills slots already being paid for. Expect the tool to recommend
  onboarding as early as the window allows.
- **Supplier quota rarely binds.** Curium runs ~1,600 mCi/week against a quota
  rate of 769 mCi/week. The quota check is an exception detector (supplier outage,
  demand trough), not a routine cost.
- **Every week comes out as a Curium/BWXT split** on real data, because average
  demand (~26/week) exceeds one Curium batch (15).
- **Onboarding search scales as designed:** 3 customers × 4-week windows = 64
  combinations exhaustively in ~22s; 5 customers × 8-week windows = 32,768 space
  reduced to 92 evaluations in ~44s by the heuristic.

---

## 9. Next feature: procurement planner (scoped, not started)

The user asked to build a **Sr-82 procurement planner** on top of the production
plan. Investigation done, requirements gathered, **nothing implemented**.

What already exists: the optimizer computes Sr-82 activity per supplier per week
and **matches the Master Planner exactly** (week 2 of 2026 = Curium 1,586 mCi in
both). So the quantity side is solved.

What the Master Planner revealed:
- **One PO covers several weeks**, not one. `4100082946` spans weeks 2–6 for
  Curium, then `4100083425` takes over. Procurement is consolidated orders with
  scheduled call-offs.
- **PO numbers stop after ~week 10** — read as the commitment horizon.
- There is a **`Fcst DOE` sheet** (Weeks # / Delivery date / Calibration date /
  Sr-82 Req / PO#) which is essentially the output format this feature should
  produce.
- **ASA** is tracked too (`ASA demand (30% failure)`, `ASA Stock`, movement types
  261/201) — a second consumable. Scope question raised, Sr-82 suggested first.

**The critical unknown is radioactive decay.** Sr-82 has a ~25-day half-life,
roughly 17% activity loss per week. So ordered activity ≠ available activity. The
blocking question is whether the agreed mCi figure is guaranteed **at our
calibration date**, at **delivery**, or at the supplier's **dispatch**. If the
last, orders must be inflated for transit and holding — around 35% more for two
weeks of holding. This single answer determines the whole model.

A requirements email covering all of this was drafted for the business (four
blocking questions, six defaultable ones, one scope question). It has not been
sent as far as this session knows.

**Design sketch:** `domain/procurement.py` (pure — decay maths, netting, order
timing), a `ProcurementService`, and a Procurement tab. The production plan is the
input, so nothing existing needs to change.

**Tension to flag:** at ~17% weekly decay, holding Sr-82 is expensive. The QC
shipping cap currently forces early production in 17 weeks. The procurement
planner may show that the raw-material cost of that early build exceeds the
penalty saving — a genuine conflict between two real constraints, better surfaced
than hidden.

---

## 10. Where the documentation lives

| Document | Contents |
|---|---|
| `README.md` | How to run, workflow, input contract, output sheets, caveats |
| `.kiro/specs/ARCHITECTURE.md` | Layered architecture, dependency rules, migration phases |
| `.kiro/specs/tasks.md` | The unified 14-phase implementation plan (all complete) |
| `.kiro/specs/supplier-constraints/` | requirements / design / tasks for the supplier feature |
| `.kiro/specs/optimizer-enhancements/` | requirements / design / tasks for the six enhancements |
| `.kiro/specs/onboarding-recommendation/` | **Stale.** Describes the superseded PuLP LP design. Historical only |

The two active spec folders each carry a **Resolved Decisions** table recording
what the business answered and why, plus any remaining open questions. Read those
before changing behaviour in those areas.

---

## 11. Practical notes for whoever picks this up

- **Test file to use:** `sites_input.xlsx`, sheet `Sites` (176 sites, no data
  issues, covers all four restricted European countries). `sites_input_new.xlsx`
  (sheet `Sheet1`, 180 sites) also works. `RUBY Production_Planner_v5.xlsx` and
  `Onboarding Recommendation_Ruby Generator.xlsx` are **not** valid inputs.
- **Set the reference week to `2026-01-05`** when testing, or you will hit partial
  quarters (now handled, but the notes will appear).
- **The Streamlit test stub cannot catch every UI bug.** `tests/conftest.py`
  installs a MagicMock streamlit; it made `data_editor` return a mock rather than
  a DataFrame, which is exactly how the `NaN` crash slipped through. **Launch the
  real app** (`streamlit run app.py`) as a final check on UI changes.
- **Expect Master Planner comparison to report everything as new** with the
  current test files, because their `Site_ID` conventions differ from the Master
  Planner's account codes. The app warns about this explicitly; it is not a bug.
