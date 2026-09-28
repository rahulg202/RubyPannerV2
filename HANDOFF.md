# Session Handoff — Ruby Fill Optimizer

**Updated:** 2026-08-31
**State:** Three rounds of business feedback closed, plus a correctness
remediation covering ten defects. 881 tests passing.
Deployed commit `05a51ff` on `origin/main`.

Companion documents:

- **[USER_GUIDE.md](USER_GUIDE.md)** — how to operate every screen (for planners)
- **[TOOL_LOGIC.md](TOOL_LOGIC.md)** — assumptions, business rules, objective
  function, worked scenarios (for validating the model)
- **[README.md](README.md)** — developer orientation
- `.kiro/specs/` — requirements, design and task backlog per feature

---

## 1. Current structure

```
domain/         17 files, 3,893 lines   pure business logic, no I/O
services/        8 files, 1,202 lines   orchestration, depends on ports only
io_adapters/     7 files, 1,605 lines   all file I/O; only place openpyxl appears
ui/              8 files, 2,095 lines   Streamlit only, no business logic
tests/          43 files, 8,374 lines   by layer + integration
app.py                     109 lines    wires adapters into services, renders tabs
legacy/                                 archived one-off scripts and superseded guides
```

**Dependency rule:** `ui → services → domain`, with `services → adapters` only via
the port protocols in `services/ports.py`. `domain` imports nothing above it. Tests
assert each of these and fail if the rule is broken — including one that forbids
`services` from importing `io_adapters`.

Run everything with `pytest`. Run the app with `streamlit run app.py`.

### Tests by layer

| Suite | Tests | Notes |
|---|---|---|
| `tests/domain` | 431 | unit plus property-based |
| `tests/io` | 126 | synthetic fixture workbooks, no customer data |
| `tests/services` | 124 | in-memory fake adapters |
| `tests/ui` | 104 | smoke, layering guards, week-input conversions |
| `tests/integration` | 69 | end-to-end, plus real-Streamlit render tests |
| `tests/` (legacy top level) | 27 | superseded onboarding entry points |
| **Total** | **881** | |

---

## 2. The application

Five tabs, one Settings tab feeding all of them, one export covering every output.

| Tab | Purpose |
|---|---|
| Settings | Every cost, rate, weight and constraint. 31 parameters, nothing hardcoded |
| Import Manual Plan | Derives the optimizer input sheet from the wide Master Planner |
| Cost Optimizer | Runs the DP solver, renders the 52-week plan |
| Onboarding | Best start week for new sites, in either of two modes |
| Comparison | Manual plan versus optimized, component by component |

---

## 3. Business rules as they stand now

| Rule | Value |
|---|---|
| Batch size | 2–16 produced, 1 discarded for QC → 15 sellable |
| Normal week | 2 batches → 30 sellable |
| Overtime week | 3 batches → 45 sellable |
| Three-run weeks | **Curium → BWXT → BWXT** (revised by the business in round 2) |
| Curium's share of a split week | `first_run_allocation` (15), lifted to cover EU demand, capped at one batch |
| QC shipping cap | 2 generators/week for Denmark, UK, Netherlands, Sweden |
| Early holding | $7,000 per generator per week |
| Late delivery | 100× the early rate — effectively forbidden |
| Overtime | $2,000 per week |
| Idle slot | $15,000 per slot per week |
| Interval 0 | A single one-time delivery, no repeat |
| Terminal inventory | Exactly zero at the end of the horizon |

**Two figures are placeholders awaiting the business:** the quarterly quota
(10,000 mCi) and the shortfall rate ($50,000/mCi). Both are flagged in
`TOOL_LOGIC.md` §1 as A12 and A13.

---

## 4. What the three feedback rounds delivered

### Round 1 — deployed
Calibration offset explained in-app and in the guide (it is display-only, and that
was the confusion). Cost Comparison unblocked. Input file generated from the manual
plan. User guide written.

### Round 2 — deployed
Supplier rule changed to Curium/BWXT/BWXT. Site codes made permanent by writing them
into a copy of the Master Planner. Reference week anchoring across the converter and
the reader. Onboarding fixed-plan mode. Row-level match report. Changed Customer
Weeks display fixes. Week-number inputs replaced with dated pickers and date fields.

### Round 3 — delivered
`TOOL_LOGIC.md`: assumptions, business rules, constraint-conflict resolution,
optimization methodology, objective function, full parameter reference, worked
scenarios on the customer's own files, development methodology and roadmap.

### Correctness remediation — delivered

Spec: `.kiro/specs/correctness-remediation/`. Ten defects, each reproduced by
running the code before being fixed, each now pinned by a regression test that
fails on the previous commit. **Default output is unchanged** —
`tests/domain/test_pinned_defaults.py` holds the pre-remediation figures and
guards them.

| # | Defect | Fix |
|---|---|---|
| R1 | `overtime_rate` could not move a single decision; the capacity component was secretly the overtime lever at 7.5x the declared rate | Capacity split into `floor` + `levelling`, both reported. `capacity_rate` documented as the actual dial (0 removes the surcharge). Effective deterrent shown in Settings and `Model_Params`. Utilisation also reported as a slot count, immune to pricing |
| R2 | A plan compared against itself reported a **$2bn loss**: quota folded into the optimized total, never computed for the baseline | `total_composite_cost` is the DP objective only. Quota is a declared component computed on both sides. Components must sum to the total |
| R3 | An unreachable advisory sat inside the reported optimum, with BWXT starved all year and no explanation | Advisory labelled as such; `zero_allocation_warnings` names the structural cause |
| R4 | Converter renamed duplicate account numbers, parser merged them — renamed sites reported as new customers | One `domain/site_codes.py` used by both readers |
| R5 | Onboarding ranked on `-15,000 x deliveries_before_week_52`, always recommending week 1 | Ranks on `delta_marginal`; `generators_added` surfaced |
| R6 | Changing a setting did not invalidate a stored plan; comparisons silently mixed generations | `Settings.fingerprint` stamped on results; `StaleResultError`; staleness banner |
| R7 | `min_batch_produced` exposed, exported, enforced nowhere | Solver enumerates `realisable_good_levels` only |
| R8 | `row_demand`/`eu_demand` duplicated; `ROW_*` columns contradicted the enforced rule | One EU array; `ROW_*` replaced by four `EU_*` columns |
| R9 | EU material rule compared production-week Curium against due-week demand | Checked against the production week via the delivery assignment |
| R10 | Runtime `assert` in a cost path, triplicated cost formula, order-dependent match suggestions, misleading infeasibility message, float schedule marks, list aliasing, duplicate counter, fixed-plan mode ignoring the QC cap | All addressed; see the spec |

**Breaking API changes** (all in-repo callers updated):

1. `solve_plan_integrated` / `solve_with_suppliers` lost the `row_demand`
   parameter; `solve_with_suppliers` now returns a 4-tuple including the delivery
   assignment.
2. `Weekly_Plan` lost `ROW_Demand_Due`, `ROW_Fulfilled`, `ROW_Inventory` and
   gained `EU_Restricted_Due`, `EU_Shipping_Cap`, `EU_Prebuild_Required`,
   `EU_Prebuild_Available`.
3. `summary["total_composite_cost"]` no longer includes the quota penalty — read
   `total_cost_with_quota` for the previous value.
4. `onboarding.OBJECTIVES` is `("marginal", "penalty", "overtime")`; `"capacity"`
   is gone as a ranking objective.
5. Comparison component labels: `"Total Composite"` is now `"Total"`, and
   `"Quota Shortfall (advisory)"` is a new row.

A `capacity_baseline` setting was built during this work and then **removed** before
completion. It offered a second idle-capacity ceiling, produced an identical plan to
simply lowering `capacity_rate`, and inflated the reported capacity total by $11.7M
of constant that no plan could influence — which forced a UI warning telling
planners to disregard the total. A setting whose output needs an instruction to
ignore it is the wrong setting. The reasoning is recorded in
`.kiro/specs/correctness-remediation/requirements.md` R1.2 so it is not
re-proposed.

---

## 5. Things a successor should know

**The optimizer is a proven optimum of the objective it is given.** Dynamic
programming over weekly inventory. Deterministic — same input, same plan, byte for
byte. Two honest caveats: the DP prunes states that would create avoidable backlog
before searching, and the quarterly quota is computed *after* the solve, so it is
not part of what was optimised (it is reported separately — see R2/R3 above).
Onboarding's re-planning mode is exhaustive below 500 combinations and heuristic
above, and says which it used.

**Capacity cost is mostly structural — and the rest is the overtime lever.**
Demand is ~86% of annual capacity, so idle slots are arithmetic, not a planning
failure; that part is now reported as `total_capacity_floor_cost`. The remainder,
`total_capacity_levelling_cost`, is exactly `capacity_rate` per unit produced above
the normal weekly ceiling — so it, not `overtime_rate`, is what decides how much
overtime the plan runs. Sweeping `overtime_rate` from $0 to $20,000 changes
nothing; `capacity_rate` alone flips 25 of 26 overtime weeks. See `TOOL_LOGIC.md`
§7.3. Do **not** repeat the old advice to "judge the tool on penalty and overtime"
without saying that overtime is governed by the capacity rate.

**Site matching is the load-bearing weak point.** Every per-customer figure depends
on an input row matching a Master Planner column. Codes are the leading account
number where present, else `RF-<sha1_8>`. Both the parser and the converter recognise
a code already written into a header and read it verbatim — without that, annotating
a workbook changes the very codes it writes. All of that logic, **including duplicate
`-2` suffixing**, now lives in `domain/site_codes.py` and is called by both readers;
they previously disagreed about duplicates, which turned a renamed site into a
phantom new customer.

**Anything read from the Master Planner must be read the same way by both readers.**
Anchoring on one side only reported 1,186 fabricated per-customer changes against 60
real ones, which is why `domain/week_anchor.py` is shared. Duplicate site codes were
the identical mistake in a different rule, which is why `domain/site_codes.py` now
exists. If you add a third rule that both readers need, share it from the start —
this project has paid for the lesson twice.

**Changed-week attribution to a specific site is a modelling choice.** The solver
picks weekly totals; a second step assigns anonymous generators to customers. Totals
are reliable, individual rows are indicative. Stated on the panel itself.

**Dependencies are pinned deliberately.** An open range meant local and Streamlit
Cloud ran different widget APIs, and a call that worked locally crashed in
production. Bump `requirements.txt` on purpose, then re-run the suite.

**The Streamlit stub in `tests/conftest.py` accepts any argument.** That is why 574
tests once passed against a broken app. `tests/integration/test_app_real_streamlit.py`
renders against the real API in a subprocess; keep new widget work covered there.

**Hypothesis runs without a per-example deadline.** A single 52-week solve takes
220–300ms, over the 200ms default, so solver-backed properties were failing at
random. Profile registered in `tests/conftest.py`.

---

## 6. Open items

| Item | Owner | Notes |
|---|---|---|
| Confirm the 17 assumptions in `TOOL_LOGIC.md` §1 | Business | The fastest way to establish whether the model still matches operations |
| Real quarterly quota (A12) | Business | Currently 10,000 mCi, never confirmed |
| Real shortfall rate (A13) | Business | Currently $50,000/mCi, deliberately punitive |
| Raw material procurement planner | Not started | Discussed as a separate phase |
| Carrying inventory across the horizon boundary | Not started | Depends on confirming the zero-terminal rule |
| Enforcing the EU material rule rather than flagging it | Not started | Never triggers on current data: EU demand peaks at 4 against Curium's 15 |
| Supplier economics influencing the production plan | Scope question | Allocation is currently post-solve, so it cannot feed back |

---

## 7. Watch on deploy

Streamlit Cloud pulls rather than rebuilds when `requirements.txt` is unchanged, and
has twice served a stale module after new files arrived — presenting as an
`ImportError` naming a symbol that plainly exists in the commit. If that happens,
reboot from *Manage app* before investigating.

Customer data is gitignored and must stay that way: the Master Planner workbook,
`sites_input.xlsx`, `sites_clean.csv` and generated PDFs. Test fixtures are
synthetic; `tests/fixtures.py` generates a sites file rather than shipping one.
