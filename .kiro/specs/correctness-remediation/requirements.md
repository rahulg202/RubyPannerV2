# Requirements Document — Correctness Remediation

## Introduction

A full review of the Ruby Fill Optimizer found ten correctness defects. Every one
of them was reproduced by running the code, not inferred from reading it. The
full test suite (742 tests) passes both before and after each defect was
demonstrated, because the suite asserts that the code does what it does.

The defects cluster in three places:

1. **The objective function does not mean what the parameters claim.** The
   declared overtime rate cannot move a single decision, while a parameter
   documented as "unused capacity" silently drives the overtime/holding
   trade-off.
2. **The comparison arithmetic is not like-for-like.** The optimized side of the
   cost comparison carries a cost component the manual-plan side never computes,
   so a plan compared against itself reports a two-billion-dollar loss.
3. **Two readers of the same workbook disagree.** The converter that writes the
   input file and the parser that reads the manual plan assign different site
   codes to the same column, so sites are reported as new when they are not.

This document specifies the corrected behaviour. Each requirement names the
observed defect, the required behaviour, and acceptance criteria that fail
against the current code.

## Guiding principles

- **P1 — Disclosure over silent change.** Where a defect is a mischaracterised
  but defensible model choice, the fix is to expose and label the mechanism and
  make it controllable, not to silently change every plan the team has already
  reviewed. Defaults preserve existing numeric output unless a requirement
  states otherwise.
- **P2 — One implementation per rule.** Where two code paths implement the same
  business rule, they must call one shared function. This project has already
  been burned by this once (`domain/week_anchor.py`); R4 is the same class of
  defect in a different rule.
- **P3 — Published components must reconcile.** Any cost table shown to a
  planner must have its parts sum to its total, on both sides of a comparison.
- **P4 — A parameter that cannot affect the result must not be presented as
  though it can.** Either enforce it or remove it.
- **P5 — Regression-first.** Every requirement below gets a test that fails
  against the pre-remediation code.

## Glossary

- **DP objective**: the quantity the dynamic-programming solver actually
  minimises. After this change, exactly `w_penalty·penalty + w_overtime·overtime
  + w_capacity·capacity`, summed over the horizon.
- **Advisory cost**: a cost computed after the solve which the solver therefore
  could not have minimised. The quarterly quota shortfall is the only one.
- **Capacity floor**: the portion of the capacity-utilisation cost that is
  identical for every feasible plan given a demand total and a shutdown
  schedule. Arithmetic, not a planning outcome.
- **Levelling cost**: the portion of the capacity-utilisation cost that varies
  with the plan. Proven below to equal `capacity_rate × (units produced above
  the normal weekly ceiling)`.
- **Effective overtime deterrent**: the true marginal cost the solver sees for
  pushing a week past the normal ceiling: `overtime_rate + capacity_rate ×
  units above the ceiling`.
- **EU-restricted site**: a customer that may only receive generators made from
  Curium raw material. Marked by the `FF002060` header fill in the Master
  Planner.
- **QC shipping cap** (`row_cap`): the maximum number of units shippable to
  EU-restricted sites in one week. A throughput limit, independent of the
  material-source rule even though it applies to the same cohort.
- **Settings fingerprint**: a stable digest of every validated setting, used to
  detect that a stored result was produced under different settings than are now
  in force.

---

## Requirement 1 — The capacity component must disclose its two parts, and the overtime parameter must be controllable

**Observed defect.** Capacity cost is `capacity_rate × max(0, normal_ceiling − y)`
per week. Because the zero-terminal-inventory rule pins total production to total
demand, this sum decomposes exactly into a constant plus a term proportional to
overtime units:

```
Σ capacity = rate·(15·n_partial + 30·n_normal − D)  +  rate·Σ max(0, y_t − 30)
             └────────── constant given D ─────────┘     └── varies with plan ──┘
```

Verified against real solves: predicted equals actual to the cent. The
consequence measured on identical demand:

| Configuration | Overtime weeks chosen |
|---|---|
| `capacity_rate = 0`, `overtime_rate` swept $0 → $20,000 | 26, 26, 26 — no response |
| `capacity_rate = 0`, `overtime_rate = $200,000` | 1 |
| `overtime_rate` default, `capacity_rate` $0 → $15,000 | 26 → 1 |

`overtime_rate` must reach 100× its default before it changes any decision.
`capacity_rate` alone suppressed 25 of 26 overtime weeks, buying $1.75M of
holding stock to do it. Meanwhile `TOOL_LOGIC.md` describes the capacity
component as "mostly a fixed floor" and advises judging the tool on "penalty and
overtime" — the one figure the overtime parameter cannot move.

The levelling term is **not wrong**: concentrating production into overtime weeks
genuinely leaves slots idle in other weeks. The defect is that it is
undisclosed, that nobody chose $15,000/unit as an overtime deterrent, and that
the parameter documented as the overtime control is inert.

**Required behaviour.**

1.1 The weekly cost decomposition SHALL report the capacity component split into
`capacity_floor_cost` and `capacity_levelling_cost`, whose sum equals the
existing `capacity_utilization_cost` exactly.

1.2 The strength of the surcharge SHALL be controllable through the existing
`capacity_rate` setting alone. Lowering it reduces the surcharge proportionally;
setting it to zero removes it entirely and leaves `overtime_rate` as the sole
overtime lever.

> **Amended after review.** An earlier draft of this requirement added a
> `capacity_baseline` setting with `"normal"` and `"available"` modes, where
> `"available"` measured idle slots against the 3-batch ceiling. It was
> implemented and then removed, because it was strictly worse than simply
> lowering `capacity_rate`:
>
> | Configuration | Plan chosen | Idle slots | Capacity cost |
> |---|---|---|---|
> | rate $15,000, `"normal"` | 0 overtime weeks | 440 | $6,600,000 |
> | rate $15,000, `"available"` | 6 overtime weeks | 485 | **$18,300,000** |
> | rate $1 | 6 overtime weeks | 485 | $485 |
> | rate $0 | 6 overtime weeks | 485 | $0 |
>
> The mode produced an identical plan to lowering the rate, while inflating the
> reported capacity cost by $11.7M of constant that no plan can influence — and
> therefore required a UI warning telling planners to disregard the total. A
> setting whose output must be accompanied by an instruction to ignore it is the
> wrong setting. `capacity_rate` already expresses the same intent on a
> continuous scale, in units a planner can reason about.

1.3 Total cost for any given plan SHALL be numerically identical to the
pre-remediation code at default settings.

1.4 Setting `capacity_rate` to zero SHALL make `capacity_levelling_cost` zero for
every week, and sweeping `overtime_rate` SHALL then change the number of overtime
weeks chosen.

1.5 The effective overtime deterrent SHALL be reported in the exported
`Model_Params` sheet as a derived value (`real_cost_of_one_overtime_week`), so the
figure is on the record for any run. `IntegratedParams.effective_overtime_deterrent`
SHALL remain available for callers that want it.

> **Amended after review.** An earlier version also rendered this as a permanent
> info panel in the Settings tab. It was removed. The panel reads well at the
> $15,000 default — "$227,000, not $2,000" is a genuine surprise worth stating —
> but degrades badly once the rate is lowered, which is the configuration the
> remediation actively recommends considering. At `capacity_rate = $1` it rendered:
>
> > One full overtime week really costs **$2,015**. …$15 — 15 normal slots left
> > empty elsewhere, at $1 each. So the tool will build up to **0 generator-weeks**
> > of early stock before it agrees to run one overtime shift.
>
> Three paragraphs of warning about a $15 surcharge, and a "0 generator-weeks"
> sentence that says nothing. A disclosure that is only correct at one setting is
> not a disclosure; it is a footgun that fires when someone takes the advice. The
> mechanism is documented in `TOOL_LOGIC.md` §7.3 and `USER_GUIDE.md`, the figure
> is in every export, and the Cost Optimizer tab shows the forced/chosen overtime
> split on real numbers — which is the same information attached to an actual plan
> rather than to a settings form.

1.6 Physical factory utilisation SHALL be reported as a **slot count**,
independent of every cost rate, so that "how much capacity did we lose" is
answerable without first agreeing a price for it and does not appear to change
when a rate is edited. The count SHALL be split into the part fixed by demand and
the part caused by overtime.

**Acceptance criteria.**

- AC1.1 For any solved plan, `capacity_floor_cost + capacity_levelling_cost ==
  capacity_utilization_cost` to within floating-point tolerance.
- AC1.2 `capacity_floor_cost` is identical across two plans with the same demand
  total, the same shutdown schedule and different production profiles.
- AC1.3 `capacity_levelling_cost == capacity_rate × Σ max(0, y_t − normal_ceiling)`.
- AC1.4 With `capacity_rate = 0`, a sweep of `overtime_rate` over $0, $2,000,
  $20,000, $200,000 yields a non-increasing overtime-week count with at least one
  change; with `capacity_rate` at its default the same sweep changes nothing.
- AC1.5 A test asserts the total cost at defaults equals a recorded
  pre-remediation figure for a fixed input.
- AC1.6 `total_idle_slots`, `unavoidable_idle_slots` and `overtime_units` are
  reported, and `total_idle_slots` is unchanged by any edit to `capacity_rate`,
  `overtime_rate` or `penalty_rate` that does not change the plan.
- AC1.7 No `capacity_baseline` setting exists.

---

## Requirement 2 — Published cost components must reconcile, on both sides

**Observed defect.** `solve_with_suppliers` folds `w_quota × quota_penalty` into
`total_composite_cost`. `compute_baseline_cost` never computes a quota term.
`compare_plans` subtracts one from the other. Feeding the optimizer's own output
back in as the "manual plan" — where the true saving must be exactly zero —
produces:

```
Component              Baseline       Optimized          Saving
Penalty                      $0              $0              $0
Overtime                     $0              $0              $0
Capacity Utilization $15,600,000    $15,600,000              $0
Total Composite      $15,600,000 $2,015,600,000 $-2,000,000,000
```

Every component reconciles; the total does not. The three published rows also no
longer sum to the published total, in the same table.

**Required behaviour.**

2.1 `total_composite_cost` SHALL mean exactly the DP objective and SHALL NOT
include any advisory cost.

2.2 The quota shortfall SHALL be reported as its own named component,
`total_quota_penalty_cost`, and SHALL be computed for the manual plan by the same
code path that computes it for the optimized plan.

2.3 A combined figure `total_cost_with_quota = total_composite_cost + w_quota ×
total_quota_penalty_cost` SHALL be available, computed identically on both sides.

2.4 The comparison component list SHALL include the quota component, and the
total row SHALL equal the sum of the component rows on both the baseline and the
optimized side.

**Acceptance criteria.**

- AC2.1 Comparing a plan against itself yields a saving of exactly zero on every
  component **and** on the total.
- AC2.2 For both baseline and optimized summaries, the sum of the weighted
  component values equals the reported total, to within tolerance.
- AC2.3 `total_composite_cost` returned by `solve_with_suppliers` equals
  `total_composite_cost` returned by `solve_plan_integrated` for the same plan.

---

## Requirement 3 — An advisory cost must be labelled as one, and its structural causes surfaced

**Observed defect.** Supplier allocation runs after the solve, so the quota
penalty cannot influence any decision — yet it was added to the number reported
as the optimum. Worse, the allocation rule gives Curium the entire week whenever
weekly output is at or below `first_run_allocation` (15). On a plan averaging
10 generators/week, BWXT received **zero** orders in all 52 weeks: four
full-quarter shortfalls, $2bn of penalty, none of it reachable by the solver.

**Required behaviour.**

3.1 The quota shortfall SHALL be labelled in the UI and the export as a
post-solve advisory that the optimizer could not act on.

3.2 When a supplier receives zero allocation across an entire quarter, the result
SHALL carry a warning naming the structural cause — that weekly output never
exceeded `first_run_allocation`, so no second run existed for that supplier to
serve.

3.3 Partial-quarter handling SHALL be unchanged: reported, never penalised.

**Acceptance criteria.**

- AC3.1 A plan whose weekly output never exceeds `first_run_allocation` produces
  a warning that names BWXT, the affected quarters, and
  `first_run_allocation` as the cause.
- AC3.2 The advisory label is present in `Quota_Status` output and in the
  summary.

---

## Requirement 4 — The two Master Planner readers must assign identical site codes

**Observed defect.** `domain.site_derivation.deduplicate_codes` renames a
repeated account number to `00460-2`. `io_adapters.master_planner_parser` uses
`setdefault` and `+= 1`, silently merging both columns into one entry. On a
workbook with two columns carrying code `00460`:

```
input file (converter):  00460 (3 deliveries), 00460-2 (3 deliveries), 00777
manual plan (parser):    00460 → weeks [1,3,5,7,9,11] (6 marks, merged), 00777
→ 00460-2 reported as a NEW CUSTOMER
→ 00460's Nth generator compared against the wrong week
```

`README.md` states this case is "renamed and reported rather than silently
merged". Half of it is. This is the same class of defect as the week-anchoring
bug that `HANDOFF.md` §5 warns about, in a rule where the sharing was never done.

**Required behaviour.**

4.1 Site-code assignment, including duplicate suffixing, SHALL live in one pure
module used by both the converter and the parser (principle P2).

4.2 Both readers SHALL produce the same code for the same column of the same
workbook, including the `-2`, `-3` suffixes, in sheet order.

4.3 The parser SHALL report a renamed duplicate as an issue rather than merging
silently.

**Acceptance criteria.**

- AC4.1 For a workbook with a duplicated account number, the set of site codes
  produced by the converter equals the set produced by the parser.
- AC4.2 No site produced by the converter is classified as a new customer purely
  because of duplicate-code handling.
- AC4.3 Each site's mark count in the parser matches its delivery count in the
  converter.

---

## Requirement 5 — Onboarding must rank on cost that varies with the decision

**Observed defect.** `delta_composite` includes the capacity floor, so adding
demand always shows a saving — each extra generator cancels a $15,000 idle
charge. One customer, interval 8, window weeks 1–12:

```
start 1-4  → 7 generators in horizon → delta_composite −$105,000
start 5-12 → 6 generators in horizon → delta_composite  −$90,000
```

Penalty and overtime deltas are $0 across the entire window. The ranking is
`−15,000 × generators_that_fit_before_week_52` and nothing else: it recommends
week 1 because week 1 is furthest from the horizon boundary. `rank_top_n` also
offers a `"capacity"` objective, ranking directly on this artefact.

`domain/fixed_plan_onboarding.py` already excludes idle capacity for exactly this
reason, documented in its module docstring. The two modes of one feature treat it
oppositely.

**Required behaviour.**

5.1 The re-planning onboarding mode SHALL rank on the marginal cost that varies
with the start-week decision: penalty plus overtime, plus the quota advisory
where computed. The capacity floor SHALL NOT drive the ranking.

5.2 `CombinationResult` SHALL report `generators_added` so a planner can see that
candidate weeks differ in how many deliveries fall inside the horizon.

5.3 The `"capacity"` ranking objective SHALL be removed. Capacity remains a
reported figure.

5.4 The UI SHALL disclose that later start weeks fit fewer deliveries inside the
52-week window, and that this is a horizon-edge effect rather than a saving.

**Acceptance criteria.**

- AC5.1 On a scenario where penalty and overtime deltas are zero for every
  candidate week, all candidates rank as equal-cost rather than ordered by start
  week.
- AC5.2 `generators_added` is present and correct for every candidate.
- AC5.3 `OBJECTIVES` no longer contains `"capacity"`.
- AC5.4 Ranking is unchanged from pre-remediation behaviour on a scenario where
  penalty or overtime genuinely differ between candidate weeks.

---

## Requirement 6 — A stored result must not be compared against different settings

**Observed defect.** `ui.state.clear_results` is called on file upload and on
*Restore all defaults*, never on an individual setting change, despite a
docstring reading "called when inputs or settings change". Changing
`penalty_rate` and then running Comparison evaluates the manual plan under the
new rates against an `opt_result.summary` computed under the old ones. No
warning; the optimizer tab keeps displaying the stale plan as current.

**Required behaviour.**

6.1 `Settings` SHALL expose a stable `fingerprint` over every validated value.

6.2 Every stored result SHALL carry the fingerprint of the settings that
produced it.

6.3 A tab displaying a result whose fingerprint differs from the settings now in
force SHALL say so.

6.4 The Comparison tab SHALL refuse to produce a cost comparison across a
fingerprint mismatch, directing the planner to re-run the optimizer.

**Acceptance criteria.**

- AC6.1 Two `Settings` differing in any single field have different fingerprints;
  two built from equal values have equal fingerprints.
- AC6.2 The comparison service raises a typed error when the optimized summary's
  fingerprint does not match the request's settings.
- AC6.3 A UI test asserts the staleness warning renders.

---

## Requirement 7 — `min_batch_produced` must be enforced or removed

**Observed defect.** `min_batch_produced` is exposed in Settings, written into
the `Model_Params` sheet as if applied, and enforced nowhere: the solver
enumerates every integer `y` in `0..cap_max`. With `min_batch_produced = 16`
(15 good units minimum per batch) the solver still planned a 1-unit batch. A
reproducibility sheet asserting a constraint that was never imposed is worse than
omitting the parameter.

**Required behaviour.**

7.1 The solver SHALL only consider production levels `y` that are physically
realisable: `y = 0`, or there exists a batch count `k` in `1..overtime_max_batches`
with `k × min_good_per_batch ≤ y ≤ k × max_good_per_batch`.

7.2 `batches_needed` SHALL return the smallest such `k`, and SHALL raise for a
`y` that no batch count can produce.

7.3 `IntegratedParams` SHALL reject `min_batch_produced > max_batch_produced`
and a `test_discard_per_batch` that leaves fewer than one good unit per batch.

7.4 With the default `min_batch_produced = 2` (one good unit minimum), every
integer `y` remains realisable, so default behaviour is unchanged (P1).

**Acceptance criteria.**

- AC7.1 With `min_batch_produced = 16`, no week in a solved plan has a
  `Good_Production` value outside `{0} ∪ {15, 30, 45}`.
- AC7.2 With defaults, a solved plan is byte-identical to the pre-remediation
  plan for a fixed input.
- AC7.3 `IntegratedParams(min_batch_produced=20, max_batch_produced=16)` raises
  `ValueError`.

---

## Requirement 8 — One EU-restricted demand array, and output columns that reflect the enforced rule

**Observed defect.** All three call sites pass `build_weekly_row_demand(...)`
into both the `row_demand` and `eu_demand` parameters of the solver.
`_build_plan_df` then runs a second, parallel, cost-free FIFO queue over that
same array and publishes `ROW_Fulfilled` / `ROW_Inventory` in `Weekly_Plan`.
Those columns constrain nothing and can show unbounded unfulfilled backlog while
the actually-enforced constraint — the inventory lower bound — forced the units
to be pre-built. Two contradictory accounts of one rule, in one sheet.

**Required behaviour.**

8.1 The solver SHALL take a single EU-restricted demand array.

8.2 The cost-free FIFO queue and the `ROW_Fulfilled` / `ROW_Inventory` columns
SHALL be removed.

8.3 `Weekly_Plan` SHALL instead publish columns describing the enforced rule:
EU-restricted units due, the shipping cap, and the units required to have been
pre-built.

8.4 The published pre-build requirement SHALL agree with the inventory lower
bound the solver enforced.

**Acceptance criteria.**

- AC8.1 `solve_plan_integrated` accepts one EU demand argument; passing the old
  positional `row_demand` is no longer possible.
- AC8.2 `Weekly_Plan` contains no `ROW_Fulfilled` or `ROW_Inventory` column.
- AC8.3 For every week, the published pre-build requirement equals
  `max(0, eu_demand[t] − row_cap)`, and end-of-previous-week inventory in the
  solved plan is at least that value.

---

## Requirement 9 — The EU material rule must be checked in the week the units are made

**Observed defect.** `eu_constraint_satisfied` compares Curium's allocation in
*production* week `t` against EU-restricted demand *due* in week `t`. The QC
shipping cap deliberately forces those units to be built earlier, so the two
rules contradict each other by construction. It does not fire on current data
(EU peak 4 against Curium's 15), so the defect is latent rather than active.

**Required behaviour.**

9.1 EU-restricted coverage SHALL be evaluated against the production week of the
units that will serve EU-restricted sites, derived from the delivery assignment.

9.2 The pipeline order SHALL be: solve → assign deliveries → derive
EU-restricted production per week → allocate suppliers.

9.3 `WeeklySupplierAllocation` SHALL record the EU-restricted units *produced*
that week alongside those *due*, so the distinction is visible.

**Acceptance criteria.**

- AC9.1 A scenario where EU units are pre-built one week ahead reports
  `eu_constraint_satisfied` against the production week, not the due week.
- AC9.2 With no pre-building, behaviour matches the pre-remediation result.

---

## Requirement 10 — Remaining defects

10.1 **Runtime assert in a cost path.** `compute_baseline_cost` carries a live
`assert` cross-checking duplicated cost logic. `tab_comparison` catches only
`RubyFillError` and `ValueError`, so a mismatch crashes the tab with a raw
traceback, and `python -O` removes the check entirely. The weekly cost
decomposition is written out three times (`cost_model`, `solver._build_plan_df`,
`comparison`). All three SHALL call one shared function; the assert SHALL be
removed as redundant.

10.2 **Order-dependent match suggestions.** `site_matching._suggest` excludes
only codes seen *so far*, so a suggestion can point at a column already claimed
by a later input row. It SHALL exclude every matched code, making output
independent of row order.

10.3 **Misleading infeasibility message.** An EU-cap lower bound exceeding its
upper bound surfaces as "check shutdown weeks and demand". The solver SHALL
detect this case and name the QC shipping cap and the offending week.

10.4 **Schedule marks typed as floats.** `_is_schedule_mark` accepts `int` 1 but
rejects `1.0`, while `Total Commercial` accepts both, so a float-typed mark
under-counts one customer while the aggregate still counts it. It SHALL accept
any numeric value equal to 1 while still rejecting booleans.

10.5 **List aliasing.** `prev: list[dict] = [{}] * (T + 1)` creates `T+1`
references to one dict. Currently harmless because each index is rebound, but it
is a trap. It SHALL be built per-index.

10.6 **Duplicate counter.** `FixedPlanResult.overtime_weeks_added` is always
equal to `overtime_added` by construction. It SHALL be derived rather than
separately maintained.

10.7 **Fixed-plan mode ignores the QC shipping cap.** A new EU-restricted
customer can be slotted into a week already at the cap with no flag. Fixed-plan
evaluation SHALL check the cap and mark affected placements.

10.8 **Documentation.** `README.md`, `HANDOFF.md` and `TOOL_LOGIC.md` SHALL be
corrected where the review found their claims contradicted by the code, and the
test count refreshed.

**Acceptance criteria.**

- AC10.1 No `assert` statement remains in `domain/comparison.py`; one shared
  decomposition function is called by solver, comparison and cost model.
- AC10.2 `build_match_report` output is identical under any permutation of the
  input rows.
- AC10.3 An EU-cap-induced infeasibility raises an error naming the week and the
  cap.
- AC10.4 A schedule mark of `1.0` is counted; `True` is not.
- AC10.7 A fixed-plan placement breaching the QC cap is reported.

---

## Out of scope

- Confirming the 17 business assumptions in `TOOL_LOGIC.md` §1, including the
  placeholder quarterly quota (A12) and shortfall rate (A13).
- Making supplier economics influence *which* weeks produce what. Allocation
  stays post-solve; R3 makes that consequence explicit rather than removing it.
- Raw-material procurement planning.
- Carrying inventory across the horizon boundary.
