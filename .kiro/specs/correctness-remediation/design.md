# Design Document — Correctness Remediation

## Overview

Ten defects, one guiding constraint: **default output must not move** except where
a requirement says it must. That rules out rewriting the objective function and
rules in disclosing what it already does. The work therefore splits into three
kinds of change:

| Kind | Requirements | Effect on existing numbers |
|---|---|---|
| **Disclosure** — expose and label a real but hidden mechanism | R1, R3 | none at defaults |
| **Arithmetic correction** — the code computes the wrong number | R2, R4, R5, R9 | changes affected figures; that is the point |
| **Hygiene** — enforce, share, or delete | R6, R7, R8, R10 | none at defaults |

Layering is unchanged: `ui → services → domain`, adapters injected only in
`app.py`. Two new pure domain modules are added; no new dependency directions.

## Architecture changes

```
domain/
  cost_model.py          REWRITTEN  one decomposition, used by all three callers
  site_codes.py          NEW        shared site-code assignment + dedup (R4)
  params.py              capacity_baseline, batch validation, fingerprint inputs
  solver.py              feasible-y enumeration, one eu_demand, new plan columns
  comparison.py          quota on the baseline side, assert removed
  supplier_allocation.py eu_production, zero-allocation detection
  supplier_solve.py      composite excludes quota; assignment before allocation
  onboarding.py          marginal ranking, generators_added
  fixed_plan_onboarding.py  QC cap check, derived counter
  delivery_assignment.py    DemandEvent.eu_restricted
  site_matching.py       order-independent suggestions
  errors.py              StaleResultError, ShippingCapInfeasibleError
services/
  settings_service.py    Settings.fingerprint
  dtos.py                fingerprints on requests/results, demand_events
  optimizer_service.py   pass-through of fingerprint and events
  comparison_service.py  staleness guard, symmetric quota
io_adapters/
  master_planner_parser.py     shared codes, numeric marks
  master_planner_converter.py  shared codes
  workbook_exporter.py         new columns, advisory labels, derived params
ui/
  tab_settings.py   effective overtime deterrent readout
  tab_optimizer.py  staleness banner
  tab_comparison.py staleness block, quota component
  tab_onboarding.py generators_added disclosure
```

---

## R1 — Capacity decomposition and `capacity_baseline`

### The identity being exposed

For week `t` with capacity baseline ceiling `C_t` and good production `y_t`:

```
capacity_t = rate · max(0, C_t − y_t)
           = rate · (C_t − y_t)  +  rate · max(0, y_t − C_t)
             └── floor_t ──┘        └── levelling_t ──┘
```

Summed over the horizon, with `Σ y_t = D` pinned by the zero-terminal rule:

```
Σ floor_t = rate · (Σ C_t − D)          constant for a given demand + week types
Σ levelling_t = rate · Σ max(0, y_t − C_t)   the only part a plan can change
```

Shutdown weeks are excluded (`capacity = 0`, `y = 0`), so they contribute to
neither part.

### One dial, not two

`C_t` is the normal weekly ceiling: 30 in a normal week, 15 in a partial-shutdown
week, and shutdown weeks are excluded entirely.

The levelling term is not a bug — concentrating production into overtime weeks
genuinely idles slots elsewhere. The defect was that it was undisclosed and that
it dwarfs the declared `overtime_rate` by 7.5×. So the fix is disclosure plus a
control, and **`capacity_rate` is already that control**:

| `capacity_rate` | Surcharge per overtime unit | Effect |
|---|---|---|
| $15,000 (default) | $15,000 | today's behaviour; overtime effectively forbidden |
| $1 | $1 | capacity becomes a readable slot count; `overtime_rate` decides |
| $0 | $0 | capacity term vanishes; `overtime_rate` is the only lever |

An earlier draft of this design added a `capacity_baseline` enum with an
`"available"` mode measuring idle slots against the 3-batch ceiling. It was built
and then removed. It produced an *identical plan* to simply lowering the rate,
while inflating the reported capacity cost from $6.6M to $18.3M — a constant no
plan can influence — which forced a UI warning instructing planners to ignore the
total. **A setting whose output has to be accompanied by an instruction to
disregard it is the wrong setting.** The rate already expresses the same intent,
continuously, in units that mean something.

### Utilisation is a count, not a cost

`WeeklyCost.idle_slots` carries the physical quantity, measured against the normal
ceiling and independent of `capacity_rate` and of any baseline. Summed into
`total_idle_slots`, `unavoidable_idle_slots` and `overtime_units`.

Separating the count from the money is what actually answers "how much capacity
are we losing". Previously that fact was only visible through a dollar figure, so
it appeared to change whenever someone edited a price — and it could not be seen
at all without first agreeing what a slot is worth. The count also makes the
hard ceiling visible: `unavoidable_idle_slots` is fixed by demand versus factory
size, so no plan and no pricing can improve it.

### Derived disclosure

`IntegratedParams.effective_overtime_deterrent(units_above_normal)` returns
`overtime_rate + capacity_rate × units_above_normal`. Exported in `Model_Params`
as `real_cost_of_one_overtime_week` for a full overtime week (15 units above the
ceiling), so every run carries the figure.

It is **not** rendered as a Settings panel. That was tried and removed: the text
only reads sensibly at a high `capacity_rate`, and collapsed into noise ("a $15
surcharge", "0 generator-weeks of early stock") exactly when a planner follows the
advice to lower it. Disclosure that is only valid at one setting is worse than
none, because it misfires precisely when acted upon.

Where the disclosure lives instead:

| Surface | What it shows |
|---|---|
| `TOOL_LOGIC.md` §7.3 | the mechanism, the sweep table, why `overtime_rate` is inert at the default |
| `USER_GUIDE.md` | the $2,000 / $225,000 / $227,000 breakdown in plain language |
| `Model_Params` sheet | `real_cost_of_one_overtime_week` for the run's actual settings |
| Cost Optimizer tab | forced vs chosen overtime, and the idle slots the choice cost — on a real plan |

The last one is the important one: the same insight attached to a plan the planner
is looking at, rather than to a form they are filling in.

### Shared decomposition

`cost_model.py` is rewritten around one function and one record:

```python
@dataclass(frozen=True)
class WeeklyCost:
    penalty: float
    overtime: float
    capacity: float           # = capacity_floor + capacity_levelling
    capacity_floor: float
    capacity_levelling: float
    composite: float          # w_penalty·penalty + w_overtime·overtime + w_capacity·capacity

def decompose_weekly_cost(inv_end, good_prod, week_type, params) -> WeeklyCost
def compute_weekly_cost(inv_end, good_prod, week_type, params) -> float   # retained, delegates
```

`solver._build_plan_df` and `comparison.compute_baseline_cost` both call
`decompose_weekly_cost` and stop reimplementing the formula. That deletes the
third copy of the logic and makes the `assert` in `comparison.py` redundant
(R10.1). `compute_weekly_cost` is kept so existing callers and tests continue to
work.

---

## R7 — Realisable production levels

A week runs `k` batches; each batch yields between `min_good = min_batch_produced
− test_discard_per_batch` and `max_good = max_batch_produced −
test_discard_per_batch` good units. So `y` is realisable iff `y == 0` or

```
∃ k ∈ 1..overtime_max_batches :  k · min_good ≤ y ≤ k · max_good
```

New in `domain/demand.py`:

```python
def realisable_good_levels(params, cap_max) -> list[int]   # sorted, cached per (params, cap_max)
def batches_needed(good_units, params) -> int              # smallest feasible k; raises if none
```

The solver replaces `for y in range(y_min, c_max + 1)` with iteration over
`realisable_good_levels(params, c_max)` filtered to `>= y_min`. With the default
`min_good = 1` every integer in `0..c_max` is realisable, so the enumeration —
and therefore every plan — is unchanged (AC7.2).

`_validate_batch_params` in `params.py` rejects `min_batch_produced >
max_batch_produced` and `test_discard_per_batch >= min_batch_produced`.

Note: `min_batch_produced` is currently the *only* parameter that is exposed,
exported and inert. Enforcing it rather than deleting it is the smaller change
and matches the documented business rule ("batch size 2–16 produced, 1 discarded").

---

## R2 + R3 — What `total_composite_cost` means

### Contract

| Key | Meaning | Includes quota? |
|---|---|---|
| `total_composite_cost` | the DP objective, exactly | no |
| `total_quota_penalty_cost` | post-solve advisory | — |
| `total_cost_with_quota` | `composite + w_quota · quota` | yes |

`solve_with_suppliers` stops mutating `total_composite_cost`. It adds the two new
keys. This restores AC2.3: the value from `solve_with_suppliers` equals the value
from `solve_plan_integrated` for the same plan.

### Symmetry

`compute_baseline_cost` gains optional `supplier_params` and
`reference_week_date`. When supplied it runs the *same* `allocate_suppliers_weekly`
→ `check_quarterly_quota` path against the manual plan's weekly production, so the
baseline summary carries the same three keys. `COST_COMPONENTS` gains
`("Quota Shortfall (advisory)", "total_quota_penalty_cost")` and the total row
becomes `total_cost_with_quota`, so the components sum to the total on both sides
(P3, AC2.2).

`compare_plans` gains a reconciliation guard: it computes the component sum and
the reported total and records the discrepancy on the returned rows, so a future
divergence surfaces as data rather than as a silent wrong number.

### Advisory labelling and structural cause

`check_quarterly_quota` is unchanged in its arithmetic. A new pure function

```python
def zero_allocation_warnings(allocations, quota_status, supplier_params) -> list[str]
```

detects any supplier with zero ordered activity across a whole non-partial
quarter and, when every producing week in that quarter had
`total_good <= first_run_allocation`, names that as the cause. This is the BWXT
case: a single-run week has no second run for BWXT to serve, so no plan the
solver can reach would order any. The warning says so instead of leaving a $2bn
figure unexplained.

---

## R8 + R9 — One EU array, and the rule checked in the production week

### Signature

```python
def solve_plan_integrated(demand, shutdown_weeks, partial_shutdown_weeks,
                          eu_demand, row_cap, params) -> (plan_df, summary)
```

The duplicated `row_demand` parameter is gone. This is a breaking change to a
domain function with three in-repo callers, all updated.

### Plan columns

`ROW_Demand_Due` / `ROW_Fulfilled` / `ROW_Inventory` are replaced by:

| Column | Meaning |
|---|---|
| `EU_Restricted_Due` | EU-restricted units due this week |
| `EU_Shipping_Cap` | `row_cap` |
| `EU_Prebuild_Required` | `max(0, EU_Restricted_Due − row_cap)` |
| `EU_Prebuild_Available` | inventory at end of the previous week |

`EU_Prebuild_Required ≤ EU_Prebuild_Available` for every week of a solved plan,
by construction of the lower bound — asserted as a test invariant (AC8.3) rather
than as a runtime assert.

### Pipeline order

`solve_with_suppliers` becomes:

```
validate feasibility
solve  ──────────────────────────────────►  y_plan
assign_deliveries(y_plan, demand_events)  ─►  records          (when events supplied)
eu_production[p] = |{r : r.eu_restricted and r.planned_week == p}|
allocate_suppliers_weekly(y_plan, eu_demand, ..., eu_production=eu_production)
check_quarterly_quota
```

`DemandEvent` gains `eu_restricted: bool = False`, populated by
`build_demand_events` from the cleaned `is_row` column. `supplier_solve` returns
the assignment records so `optimizer_service` stops recomputing them.

`allocate_suppliers_weekly(..., eu_production=None)` uses `eu_production` for both
the Curium lift and `eu_constraint_satisfied` when given, and falls back to
`eu_demand` when not — preserving current behaviour for callers that have no
assignment (AC9.2). `WeeklySupplierAllocation` records both
`eu_restricted_demand` (due) and `eu_restricted_production` (made), so the
distinction is visible rather than conflated.

**Quota symmetry caveat.** The quota figure depends on `eu_*` only through the
Curium lift, which is inactive while EU volume stays at or below
`first_run_allocation` (15). Current data peaks at 4. A test pins this: quota
output is identical whether the lift is fed due-week or production-week volumes,
for any input inside that regime. Outside it, the baseline side falls back to
due-week and the summary records that it did.

---

## R4 — One site-code implementation

New pure module `domain/site_codes.py`:

```python
GENERATED_PREFIX = "RF-"

def leading_account_code(header) -> str | None      # "00631/0105MAK ..." -> "00631"
def leading_generated_code(header) -> str | None    # "RF-4e40dfd2 ..."   -> "RF-4e40dfd2"
def generated_code(header, digest_len=8) -> str     # stable sha1 of the normalised header
def assign_code(header, generated=None) -> CodeAssignment   # (code, source)

class CodeDeduplicator:
    """Suffixes repeats -2, -3 ... in call order. One instance per workbook read."""
    def assign(self, base: str) -> tuple[str, bool]   # (code, was_renamed)
```

`site_derivation` and `master_planner_parser` both import from here. The parser
iterates header cells left to right — the same order the converter uses — and
feeds each column's base code through a `CodeDeduplicator`, keying
`customer_schedule` and `customer_headers` by the deduplicated code and appending
an issue for each rename. `deduplicate_codes` in `site_derivation` becomes a thin
wrapper over the shared class so there is one suffixing rule (P2).

This is the fix the project already learned to make for week anchoring. The
module docstring says so, to make the pattern explicit for the next rule that
needs it.

---

## R5 — Onboarding ranks on what varies

`CombinationResult` gains:

```python
generators_added: int     # deliveries this start week places inside the horizon
delta_marginal: float     # w_penalty·Δpenalty + w_overtime·Δovertime + w_quota·Δquota
```

`delta_marginal` excludes capacity. `OBJECTIVES` becomes
`("marginal", "penalty", "overtime")` — `"capacity"` is removed because ranking on
it ranks on `−capacity_rate × generators_added`, which is the horizon edge and
nothing else. `rank_top_n` sorts on `delta_marginal` for the `"marginal"`
objective and tie-breaks on `(delta_marginal, −generators_added, key())` so that
where marginal costs tie, the option serving *more* deliveries wins — a defensible
tie-break, stated rather than emergent.

`delta_capacity` and `delta_composite` remain on the record and remain displayed,
labelled as dominated by the horizon-edge effect. `tab_onboarding` shows
`generators_added` per candidate and a caption explaining that later start weeks
fit fewer deliveries inside 52 weeks, so their lower total is arithmetic rather
than a saving.

This aligns the re-planning mode with `fixed_plan_onboarding`, whose docstring
already excluded idle capacity for this exact reason.

---

## R6 — Settings fingerprint

```python
@dataclass(frozen=True)
class Settings:
    ...
    @property
    def fingerprint(self) -> str:   # sha1 over a canonical sorted serialisation
```

Serialisation walks both param dataclasses via `dataclasses.fields`, so a newly
added parameter is covered automatically rather than needing the hash updated —
the failure mode a hand-maintained list would have.

Flow: `OptimizeRequest.settings_fingerprint` → `OptimizationResult.settings_fingerprint`
→ `ComparisonRequest.optimized_settings_fingerprint` + `ComparisonRequest.settings_fingerprint`.
`ComparisonService.run` raises `StaleResultError` when they differ.
`tab_optimizer` renders a banner when the displayed result's fingerprint differs
from current settings; `tab_comparison` blocks the comparison and directs the
planner to re-run.

`ui.state.clear_results` keeps its existing call sites; the fingerprint is the
mechanism that makes a missed invalidation *visible* rather than silent, which is
more robust than trying to hook every one of 31 widgets.

---

## R10 — Hygiene

| Item | Change |
|---|---|
| 10.1 assert + triplicated cost logic | one `decompose_weekly_cost`; assert deleted |
| 10.2 order-dependent suggestions | `_suggest` excludes all matched codes, computed up front |
| 10.3 misleading infeasibility | bounds check raises `ShippingCapInfeasibleError(week, cap, required)`; `solve_with_suppliers` lets it propagate as a typed domain error |
| 10.4 float marks | `_is_schedule_mark` accepts any non-bool `Real` equal to 1 |
| 10.5 list aliasing | `prev = [{} for _ in range(T + 1)]` |
| 10.6 duplicate counter | `overtime_weeks_added` becomes a property over placements |
| 10.7 fixed-plan QC cap | `evaluate_against_fixed_plan` takes `eu_due` + `row_cap`; placements breaching the cap are marked and counted |

`errors.py` gains `StaleResultError` and `ShippingCapInfeasibleError`, both
subclasses of the existing `RubyFillError` so the UI's existing handlers catch
them.

---

## Testing strategy

Each requirement gets a regression test that fails against pre-remediation code.
The three that matter most are characterisation tests, because they are how the
defects were found:

- **`test_self_comparison_is_zero`** — solve, feed the plan back as the manual
  plan, assert every component saving *and* the total are zero. This single test
  would have caught R2 and will catch any future asymmetry.
- **`test_capacity_decomposition_identity`** — assert floor + levelling equals the
  published capacity cost, and that floor is invariant across plans with equal
  demand. Property-based over demand profiles.
- **`test_both_readers_agree_on_codes`** — build a synthetic workbook with a
  duplicated account number, assert the converter's and the parser's code sets
  are equal.

Plus: a pinned-numbers test asserting default output is unchanged (P1, AC1.5,
AC7.2), so the disclosure work cannot quietly move results.

Hypothesis keeps the no-deadline profile already registered in
`tests/conftest.py`; the new solver enumeration is not slower, since the default
level set is the same `range` it replaced.

## Migration notes

Breaking changes to in-repo APIs, all callers updated in this change:

1. `solve_plan_integrated` / `solve_with_suppliers` lose the `row_demand`
   parameter.
2. `Weekly_Plan` loses three `ROW_*` columns and gains four `EU_*` columns.
   Anything downstream reading the export by column name needs updating; the CLI
   and the exporter are the only readers in-repo.
3. `summary["total_composite_cost"]` no longer includes the quota penalty. Read
   `total_cost_with_quota` for the previous value.
4. `onboarding.OBJECTIVES` loses `"capacity"`.

The backward-compatible re-export shims in `integrated_cost_optimizer.py` and
`onboarding_recommendation.py` are preserved.
