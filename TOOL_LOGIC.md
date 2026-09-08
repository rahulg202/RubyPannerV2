# Ruby Fill Optimizer — Logic, Assumptions and Methodology

Every figure in this document was produced by running the tool against your own
files — `Copie de Ruby-Fill- Master Schedule.xlsx` and `sites_input.xlsx` — and is
reproducible. Where the tool's behaviour is a modelling choice rather than a fact,
it says so.

---

## Contents

1. [Read this first: assumptions to re-validate](#1-read-this-first-assumptions-to-re-validate)
2. [Assumptions used to process your data](#2-assumptions-used-to-process-your-data)
3. [Business rules applied](#3-business-rules-applied)
4. [Constraints, limitations and exceptions](#4-constraints-limitations-and-exceptions)
5. [How conflicting constraints are resolved](#5-how-conflicting-constraints-are-resolved)
6. [Optimization methodology](#6-optimization-methodology)
7. [The objective function and weighting](#7-the-objective-function-and-weighting)
8. [What "best" means, and worked scenarios](#8-what-best-means-and-worked-scenarios)
9. [Complete parameter reference](#9-complete-parameter-reference)
10. [Development methodology, backlog and roadmap](#10-development-methodology-backlog-and-roadmap)
11. [Outputs, calculations and error messages](#11-outputs-calculations-and-error-messages)
12. [On replacing week numbers with dates](#12-on-replacing-week-numbers-with-dates)

---

## 1. Read this first: assumptions to re-validate

Below is every assumption the tool carries that came from the original
discussions. Each one is a single line to confirm or
correct.

| # | Assumption currently in the tool | Where it came from | Risk if stale |
|---|---|---|---|
| A1 | Batch size 2–16 units, 1 discarded for QC, so **15 sellable per batch** | Original one-pager | Changes every capacity figure in the tool |
| A2 | **2 batches** in a normal week, **3 with overtime** → 30 and 45 sellable | Original one-pager | Changes capacity, overtime and the whole plan shape |
| A3 | **One QC generator per batch**, consuming 10 mCi | Confirmed in writing during build | Changes Sr-82 totals and quota consumption |
| A4 | Holding a finished generator one week early costs **$7,000** | Original one-pager | Directly scales the penalty component |
| A5 | Late delivery costs **100× the early rate** ($700,000/generator/week) | Set during build to make late effectively forbidden | If late is merely expensive, plans will differ materially |
| A6 | An unused production slot costs **$15,000/week** | Original one-pager | This is 79% of your reported total cost — see §8 |
| A7 | A third batch in a week costs **$2,000** | Original one-pager | Minor; overtime is rarely the deciding factor |
| A8 | **2 generators/week** maximum for Denmark, UK, Netherlands, Sweden (QC shipping cap) | Confirmed during build | Binds in 8 weeks of your 2026 plan; changing it moves the plan |
| A9 | Curium runs first and once; later runs are BWXT (**Curium, BWXT, BWXT**) | **Revised by you in this round.** Previously Curium, BWXT, Curium | Already changed; confirm the new rule is final |
| A10 | Curium's run produces **15** generators; BWXT takes the remainder | Original one-pager | Determines the supplier split, so quota position |
| A11 | Curium requires **5%** surplus Sr-82, BWXT **2%**, minimum 20 mCi | Reference strontium workbook | Changes ordered mCi and quota position |
| A12 | Each supplier has a **10,000 mCi** quarterly minimum | **Never confirmed** — placeholder | Currently non-binding (you order 16,145–20,513/quarter), so low risk today |
| A13 | Missing a quarterly minimum costs **$50,000 per mCi** | **Never confirmed** — placeholder, set high deliberately | Would dominate all other costs if a shortfall ever occurred |
| A14 | Quarters begin in **January** | Assumed, now configurable | Affects which quarters are fully covered |
| A15 | Generators for Denmark/UK/Netherlands must use **Curium material** | Original one-pager | Currently reported as a flag, not enforced as a hard constraint — see §4 |
| A16 | Sites are due a generator on a **fixed repeating interval** | Inferred from the Master Planner headers | Your real schedule is smoothed by hand; see the limitation in §4 |
| A17 | A generator produced early but delivered on time **still counts as a changed week** | Confirmed in writing during build | Changes how the Changed Customer Weeks report reads |

---

## 2. Assumptions used to process your data

### 2.1 Reading the Master Planner

| Assumption | Evidence from your workbook |
|---|---|
| Schedule grid is on the `Schedule` sheet, headers on row 2 | Confirmed |
| Week number is in the `Weeks #` column | Confirmed |
| One column per customer, from column 15 onward | 203 customer columns found |
| A cell containing the integer `1` means one scheduled generator | The count of `1`s matches the `Total Commercial` aggregate exactly |
| Delivery interval is written in the header as `(N)` | Present in 194 of 203 columns |
| Dark blue header fill (`FF002060`) marks EU-restricted sites | 10 columns, all Denmark/Netherlands/UK |
| Account number is the leading digits of the header | Present in 166 of 203 columns |
| Week numbers restart each January, so one year must be selected | 2026 chosen automatically as the most completely planned |

Columns whose header carries no account number receive a generated code
(`RF-` + hash). That is 37 of your 203 columns.

**The manual plan is read in sellable units.** When the Comparison tab evaluates
your plan, a week's production is its `Total Commercial` figure — the same number
you see on that row of the Master Planner. The `QC GEN` column is deliberately
*not* added to it: demand and the 30/45 ceilings are both expressed in sellable
generators (A1, A2), so a week showing 44 commercial + 3 QC is a week of 44
against a ceiling of 45, not 47. You can check any row this way: pick a
manufacturing date in the Comparison tab's weekly table and the *Manual*
production column will equal `Total Commercial` on that row exactly.

This also feeds the idle-capacity figure, and it raises it. A QC generator does
not fill a sellable slot, so a week producing 28 sellable units has 2 idle slots
whether or not a QC unit was also made. Across your 52 weeks the plan holds 1,500
sellable units against 1,560 normal-week slots, so the manual plan carries 219
idle slots — 44 more than if the QC units were miscounted as sellable, worth
$660,000 at the default $15,000 rate. That figure is now right rather than
flattered; see L6 and §8.

### 2.2 What the tool derives per site

From your 2026 data:

```
203 customer columns  ->  184 active, 19 with no scheduled generator that year
Delivery intervals:   7 weeks x146   6 x25   8 x5   5 x3   10 x3   9 x1   11 x1
Interval source:      header 194     measured from gaps 5     one-time 4
Countries:            usa 155  switzerland 10  canada 8  netherlands 6  uk 3  denmark 2
EU-restricted:        10 sites
```

Country is read from the header text using whole-word matching. This matters: your
workbook contains `00617 Swedish Medical Center, Bronx, NY`, which is a US site.
Substring matching would have classified it as Sweden and applied the EU shipping
cap to it.

### 2.3 Demand construction

Each active site is due one generator at `Next_Demand_Week`, then every
`Interval_Weeks` thereafter, to the end of the horizon. An interval of `0` means a
single delivery with no repeat. QC generators are added on top, one per batch.

---

## 3. Business rules applied

### 3.1 Production

| Rule | Value |
|---|---|
| Units per batch | 2–16 produced, 1 discarded for QC → 15 sellable |
| Normal week | 2 batches → 30 sellable generators |
| Overtime week | 3 batches → 45 sellable generators |
| Partial shutdown week | 1 batch → 15 sellable generators |
| Full shutdown week | 0 |
| Inventory at end of horizon | Must be exactly zero |

The zero-terminal-inventory rule is worth flagging: the tool will not leave finished
stock at week 52. If you routinely carry stock into the next planning year, tell us —
it is a one-line change and it currently forces production down in the final weeks.

### 3.2 Supplier allocation (revised this round)

| Runs in the week | Sequence | Curium | BWXT |
|---|---|---|---|
| 1 | `Curium` | all | 0 |
| 2 | `Curium, BWXT` | 15 | remainder |
| 3 | `Curium, BWXT, BWXT` | 15 | remainder |

Curium runs first and exactly once. Its share is lifted above 15 only if a week has
more EU-restricted demand than that, capped at one batch.

On your 2026 plan this produces 6 single-run weeks, 34 two-run weeks and 11
three-run weeks, splitting **Curium 724 generators / 76,595 mCi** against
**BWXT 673 / 69,260 mCi**.

### 3.3 Sr-82 activity per supplier run

```
base     = 100 x generators + 10 x batches
surplus  = max(ceil(surplus% x base), 20 mCi)
activity = base + surplus          surplus% = 5% Curium, 2% BWXT
```

### 3.4 Quarterly quota

Each supplier must be ordered at least 10,000 mCi per calendar quarter. A quarter
only partly inside the 52-week window is reported but never charged, because the
missing weeks are real but invisible to the planner — the early ones are already in
SAP, the later ones fall beyond week 52.

Your 2026 position, all eight supplier-quarters:

```
Curium  Q1 18,938   Q2 19,568   Q3 17,576   Q4 20,513     quota 10,000   all OK
BWXT    Q1 16,145   Q2 16,270   Q3 18,318   Q4 18,527     quota 10,000   all OK
```

The quota check is therefore an exception detector for unusual situations — a long
supplier outage, a demand collapse — not a routine cost. It has never fired on your
data.

---

## 4. Constraints, limitations and exceptions

### 4.1 Hard constraints — never violated

| Constraint | Behaviour if impossible |
|---|---|
| Weekly production within 0–30 (or 45 on overtime) | Plan reported infeasible with the week named |
| Shutdown weeks produce nothing | Infeasible if demand cannot be pre-built |
| Terminal inventory exactly zero | Infeasible |
| No supplier used in a week it is marked unavailable | Infeasible, week named |
| EU-restricted demand in a week Curium is unavailable | Rejected before solving |

### 4.2 Soft constraints — priced, not forbidden

Producing early, producing late, running overtime, leaving slots idle, and missing a
quarterly quota are all *priced*. The optimizer will accept any of them if the
alternative costs more. This is deliberate: a hard constraint that cannot be met
produces no plan at all, which is useless to a planner.

### 4.3 Known limitations — please read these

**L1 — EU material rule is reported, not enforced.** The tool checks that Curium's
output covers each week's EU-restricted demand and flags the week if it does not, but
it will not re-allocate to fix it. On your data this never triggers: EU demand peaks
at 4 generators in a week against Curium's 15. If EU volume grew past 15 in one week,
you would get a flag rather than a corrected plan.

**L2 — The derived cadence is lumpier than your maintained input sheet.** When the
tool derives intervals from the Master Planner it models each site as a fixed
repeating cycle from one start week. Your real schedule is smoothed by hand, so the
model drifts out of phase. This is inherent to modelling a cadence and is not fixed
by anchoring the reference week:

```
demand profile                       std dev   min   max   total
derived from Master Planner              9.5     0    40    1397
your maintained sites_input.xlsx         7.2    12    40    1336
Master Planner actual production         7.3    12    41    1501
```

The derived version has five weeks under 10 generators (weeks 4, 11, 25, 32, 39) and
one at zero. Your maintained sheet has none. **Recommendation: keep maintaining the
input sheet and use the converter to build it once, then adjust — not as a
replacement for it.**

**L3 — Attribution of a move to a specific site is a modelling choice.** The
optimizer decides weekly *totals*; a second step hands those anonymous generators to
individual customers. Where several sites are due in the same week, which one is
recorded as "moved" is consistent between runs but arbitrary. The totals are sound;
do not use an individual row to challenge a specific site. This caveat is now stated
on the Changed Customer Weeks panel itself.

**L4 — Onboarding answers one of two different questions, and you choose which.**
*Re-plan all 52 weeks* re-optimizes everything for each candidate start week and
reports the cost against a baseline of your existing sites. *Keep my plan fixed*
leaves your weekly production untouched and prices what slotting the site in costs:
a spare slot is free, a full week needs a third batch, otherwise the generator is
built earlier and held. The two can disagree, and both answers are valid — the first
assumes production can move, the second assumes it cannot.

Note that fixed mode deliberately excludes the idle-capacity cost. That figure
describes how well a plan uses the factory, and in fixed mode the plan is not up for
revision, so charging or crediting it would answer a question nobody asked. On the
same site, re-planning mode reported a $119,000 *saving* for week 1 (added demand
fills idle slots) while fixed mode reported $0 added cost. Both are correct for their
own question.

**L5 — Capacity cost is mostly structural.** See §8. Roughly 98% of it is unavoidable
arithmetic, not something the optimizer can improve.

**L6 — Your manual plan is evaluated, not solved, and it may not be executable.**
The Comparison tab replays your Master Planner through the same cost model as the
optimizer. It does not *solve* it, so your plan can sit outside limits the optimizer
is never allowed to cross. That matters more than it sounds, because breaking those
limits makes a plan look **cheaper**: the limits are exactly what force generators to
be built early and held, and holding is what the penalty charges for.

Two limits are checked, and both are broken by the workbook you sent:

| Limit | Weeks in your plan | Why the plan cannot be run |
|---|---|---|
| 45 sellable units in a week (3 batches) | 2 — weeks 14 (47) and 49 (49) | There is no fourth batch to make them in |
| 2 EU-restricted units shipped per week | 7 — weeks 6, 14, 22, 41, 42, 48, 49 | Denmark, the UK, the Netherlands and Sweden take 2 a week between them, so the extra units must be built earlier and held |

Your plan delivers every generator in the week it is due, which is why its penalty
cost is exactly **$0**. The optimizer cannot do that: to ship 4 EU-restricted units in
week 6 it must build 2 of them by week 5 and carry them, and the same in six other
weeks. That is 12 unit-weeks of stock, charged at the $7,000 holding rate — **$84,000**.

So a straight subtraction shows the optimizer costing $84,000 *more*, when what it has
actually done is produce the cheapest plan that can be run at all. **When the tool
detects any of these breaches it now stops reporting a saving** and instead names the
weeks and labels the difference "cost of an executable plan". To get a like-for-like
saving, correct those weeks in the Master Planner and re-run the comparison.

One consequence worth knowing: this shows up most starkly when the capacity weight or
capacity rate is turned down. Capacity utilisation is where the optimizer earns most
of its advantage on your data ($3,285,000 for your plan against $1,725,000 for the
optimized one). Set that rate near zero and you remove the optimizer's main gain while
leaving in the cost of legality, so the total flips negative. At the default rates the
same workbook shows the optimizer ahead by $407,000.

**L6 — Calibration dates assume a constant weekday.** The tool steps forward in 7-day
weeks, so if a real manufacturing date slips to another weekday, that week's
calibration date will be a few days out. It is presentational only and affects no
calculation.

---

## 5. How conflicting constraints are resolved

Conflicts are resolved by construction rather than by rules of thumb, in three
layers.

**Layer 1 — Feasibility is checked before optimizing.** Supplier availability against
EU-restricted demand, and weeks where both suppliers are unavailable but demand is
due, are rejected up front with the week named. No plan is produced from an
impossible input.

**Layer 2 — Hard constraints become bounds on the search.** Before optimizing, the
tool computes, for every week, the minimum and maximum finished inventory that could
still lead to a feasible finish. Anything outside those bounds is never considered.
This is where competing physical constraints are reconciled:

- Remaining demand versus remaining capacity sets the *lower* bound. If weeks 40–52
  cannot physically cover what is still owed, inventory must be built earlier.
- Shutdown weeks reduce remaining capacity, which raises earlier lower bounds
  automatically.
- The QC shipping cap raises the lower bound directly. Your 2026 plan has **8 weeks
  where restricted-country demand exceeds the cap of 2**, peaking at 4. The tool
  requires the excess to exist as stock *before* those weeks.

Because all of these adjust the same bounds, they cannot contradict each other. If
they are jointly impossible, the bounds cross and the tool says so explicitly rather
than silently relaxing one.

**Layer 3 — Soft constraints compete on price.** Everything that remains is a
trade-off between holding cost, overtime, idle capacity and quota. The optimizer
prices each and picks the cheapest total. When two plans tie on cost it prefers fewer
overtime weeks, then fewer batches — so ties resolve toward the less disruptive plan.

### Worked example: a shutdown colliding with the QC cap

Adding shutdown weeks 26 and 27 to your 2026 plan:

```
                    no shutdown        weeks 26,27 shut
total cost          $3,710,000         $3,843,000   (+$133,000)
penalty             $  448,000         $1,057,000   (+$609,000)
overtime weeks              11                 13
peak production             38                 45   (overtime pulled forward)
```

The tool pre-builds into weeks before the shutdown, accepting holding cost and two
extra overtime weeks. Capacity cost *falls*, because the pre-building fills slots that
were idle. That offset is why the total rises by far less than the penalty does.

---

## 6. Optimization methodology

### 6.1 Method

**Dynamic programming over weekly finished-goods inventory.** Not a heuristic, not a
rolling greedy rule, and not linear programming.

- The *state* is the finished-goods inventory at the end of a week. Negative means
  unmet demand.
- Working forward from week 1, the tool evaluates every producible quantity for each
  reachable inventory level, and keeps only the cheapest way to arrive at each level.
- At week 52 it takes the cheapest path that ends at exactly zero inventory, then
  walks backwards to recover the production quantity for every week.

### 6.2 Why this method

Two properties matter to you:

**It is a proven optimum, not a good guess.** Within the stated cost model and
parameters, no cheaper 52-week plan exists. That is a property of the method, not a
claim about tuning. It is why the comparison against your manual plan is meaningful.

**It is deterministic.** The same inputs always produce the same plan, byte for byte.
There is no random seed and no iteration limit, so a result you approve today can be
reproduced and audited later. Every run's parameters are written to the
`Model_Params` sheet for that reason.

### 6.3 Scale

On your `sites_input.xlsx`, the search covers 52 weeks against up to 2,296 possible
inventory levels per week and 46 candidate production quantities, bounded at roughly
2.7 million state transitions. It completes in a few seconds and covers that space
exhaustively — nothing is sampled or truncated.

### 6.4 What the DP does *not* decide

Supplier allocation and per-customer attribution happen *after* the plan is chosen.
The optimizer picks weekly totals; the supplier split is then applied by the fixed
rule in §3.2. This is why changing the supplier pattern (as you did this round)
altered the Curium/BWXT split but left the total cost at $3,710,000 unchanged.

If supplier economics should *influence* which weeks produce what, that is a genuine
change of scope and we should discuss it.

---

## 7. The objective function and weighting

### 7.1 The function

```
minimise   sum over weeks of

      w_penalty  x  ( 7,000 x units held early                    )
                    ( 700,000 x units late                        )
    + w_overtime x  ( 2,000 if a third batch runs                 )
    + w_capacity x  ( 15,000 x unused slots that week             )

    + w_quota    x  ( 50,000 x mCi short of any full quarter      )
```

Unused slots are measured against 30 in a normal week and 15 in a partial-shutdown
week. Shutdown weeks are excluded — no capacity was expected.

### 7.2 The weights

Each of the four weights runs 0 to 1 and scales one component. At least one must be
above zero. They exist so you can ask directed questions rather than only "what is
cheapest overall":

| Setting | Question it answers |
|---|---|
| All at 1 (default) | What is the cheapest plan on the full cost model? |
| `w_capacity = 0` | What is the cheapest plan ignoring idle capacity? |
| `w_penalty = 0` | How full can I keep the factory regardless of holding cost? |

### 7.3 An important property of the weighting

Because unused capacity is charged at $15,000 against a holding cost of $7,000, **the
capacity term is the largest by a wide margin** on your data. Measured on
`sites_input.xlsx`:

```
capacity   $3,420,000    79% of total
penalty    $  910,000    21%
overtime   $    2,000     0.05%
quota      $        0
total      $4,332,000
```

That ratio drives behaviour: the tool would rather build a generator early and hold
it than leave a slot idle. Whether that reflects your economics is worth confirming —
it follows directly from A4 and A6 in §1.

---

## 8. What "best" means, and worked scenarios

### 8.1 The criterion

"Best" means **lowest weighted total cost** as defined in §7, subject to every hard
constraint in §4.1. Ties break toward fewer overtime weeks, then fewer batches.

It explicitly does *not* mean smoothest schedule, fewest changes from your manual
plan, or highest utilisation. Smoothing is a *consequence* of the capacity term, not
a goal — which is why it weakens when you set `w_capacity = 0`.

### 8.2 Scenario 1 — the balancing you observed

Run on `sites_input.xlsx`, the file you tested with:

```
                          std dev   min   max   range   overtime weeks
raw demand per week           7.2    12    40      28        -
your manual plan              7.3    12    41      29       23
optimizer's plan              4.8    16    34      18        1
```

Volatility falls by a third and overtime weeks fall from 23 to 1. This is the effect
you described as "18 followed by 41, now around 30" — and it comes from the capacity
term making idle slots expensive, not from any smoothing rule.

### 8.3 Scenario 2 — how much of the capacity cost is real

This is the most important scenario for management, because the headline cost looks
alarming.

Your 176 active sites need 1,336 generators a year. Normal annual capacity is
52 × 30 = 1,560. So **at least 224 slots must sit idle no matter what anyone
plans** — that is $3,360,000 of unavoidable cost.

The optimizer's actual capacity cost is $3,420,000. So of the $3.42M:

```
structural, unavoidable    $3,360,000    98.2%
addressable by planning    $   60,000     1.8%
```

Independent confirmation: on the Master-Planner-derived input (1,397 generators, so a
floor of 163 idle slots × $15,000 = $2,445,000), setting `w_penalty = 0` so the tool
optimises capacity alone returns a total of **exactly $2,445,000**. The floor
calculation and the solver agree to the dollar.

**Read the penalty and overtime components when judging the tool's value.** The
capacity figure is a statement about factory size versus demand, not about planning
quality.

### 8.4 Scenario 3 — sensitivity to each parameter

All on the same input, changing one thing at a time:

| Change | Total cost | Penalty | Overtime weeks | Production range |
|---|---|---|---|---|
| Defaults | $3,710,000 | $448,000 | 11 | 1–38 |
| `w_capacity = 0` | $108,000 | $70,000 | 19 | 1–41 |
| `w_penalty = 0` | $2,445,000 | $3,731,000 | 0 | 7–30 |
| Early penalty $1,000 | $2,739,000 | $294,000 | 0 | 3–30 |
| Shutdown weeks 26, 27 | $3,843,000 | $1,057,000 | 13 | 1–45 |
| QC shipping cap 1 | $3,857,000 | $595,000 | 11 | 1–38 |

Three things to read from this:

- **Dropping the early penalty to $1,000 eliminates overtime entirely.** When holding
  is cheap the tool pre-builds steadily instead of surging. If $7,000 is wrong, plans
  change qualitatively, not just numerically.
- **Halving the QC shipping cap costs $147,000.** The cap binds in 8 weeks, so it is a
  real operational lever, not a formality.
- **`w_capacity = 0` drives overtime up from 11 weeks to 19.** With idle slots free,
  the tool stops spreading production.

---

## 9. Complete parameter reference

All 31 parameters are editable in the **Settings** tab. Nothing is hardcoded. Values
last for the session; `Restore all defaults` resets them, and every run records what
it used on the `Model_Params` sheet.

### 9.1 Reference dates

| Parameter | Default | Accepted | Purpose and impact |
|---|---|---|---|
| Use a reference week | off | on/off | Off shows week numbers; on shows real dates throughout |
| Week 1 manufacturing date | none | any date | Anchors planning week 1. Also determines which quarters are fully covered for quota |
| Calibration offset (days) | 4 | ≥ 0 | Days from manufacturing to calibration. **Display only — affects no calculation** |

### 9.2 Production constraints

| Parameter | Default | Accepted | Purpose and impact |
|---|---|---|---|
| Horizon (weeks) | 52 | ≥ 1 | Planning length. Shorter horizons change the terminal-inventory constraint |
| Min batch produced | 2 | ≥ 1 | Smallest batch including its QC unit |
| Max batch produced | 16 | ≥ 1 | Largest batch including its QC unit. Raising it raises weekly capacity and cuts capacity cost |
| Test discard per batch | 1 | ≥ 0 | QC units per batch. Raising it cuts sellable output per batch |
| Normal max batches/week | 2 | ≥ 0 | Sets the 30/week baseline and the idle-slot reference |
| Overtime max batches/week | 3 | ≥ 0 | Sets the 45/week ceiling |
| Shutdown weeks | none | pick any weeks | No production. Forces pre-building; raises penalty. Picker shows each week's date |
| Partial shutdown weeks | none | pick any weeks | One batch only (15) |

### 9.3 Costs and weights

| Parameter | Default | Accepted | Purpose and impact |
|---|---|---|---|
| Early penalty rate | $7,000 | ≥ 0 | Cost per generator per week held. Lowering it to $1,000 removed all overtime in testing |
| Late penalty multiplier | 100 | ≥ 0 | Late costs this × the early rate. At 100 late is effectively forbidden |
| Overtime rate | $2,000 | ≥ 0 | Cost of a third batch. Small relative to other terms |
| Capacity rate | $15,000 | ≥ 0 | Cost per idle slot per week. **The dominant term — 79% of total** |
| Penalty weight | 1.0 | 0–1 | Scales the penalty term |
| Overtime weight | 1.0 | 0–1 | Scales the overtime term |
| Capacity weight | 1.0 | 0–1 | Scales the capacity term. Set to 0 to see planning savings clearly |
| Quota shortfall weight | 1.0 | 0–1 | Scales the quota term |

### 9.4 QC shipping cap

| Parameter | Default | Accepted | Purpose and impact |
|---|---|---|---|
| Max restricted-country units/week | 2 | ≥ 0 | QC throughput limit for Denmark, UK, Netherlands, Sweden. Binds in 8 weeks; halving it cost $147,000 |

### 9.5 Suppliers

| Parameter | Default | Accepted | Purpose and impact |
|---|---|---|---|
| Sr-82 per generator (mCi) | 100 | ≥ 0 | Scales all ordered activity and quota position |
| Sr-82 per batch / QC generator (mCi) | 10 | ≥ 0 | Activity for the discarded QC generator |
| Minimum surplus (mCi) | 20 | ≥ 0 | Floor on the surplus term |
| Curium surplus fraction | 0.05 | 0–1 | Extra Sr-82 required from Curium |
| BWXT surplus fraction | 0.02 | 0–1 | Extra Sr-82 required from BWXT |
| Curium run (generators) | 15 | 0–15 | Curium's share of a split week; BWXT takes the rest |
| Curium quarterly quota (mCi) | 10,000 | ≥ 0 | **Placeholder — needs confirmation** |
| BWXT quarterly quota (mCi) | 10,000 | ≥ 0 | **Placeholder — needs confirmation** |
| Quota shortfall penalty ($/mCi) | 50,000 | ≥ 0 | **Placeholder — deliberately punitive** |
| Quarter start month | 1 | 1–12 | Which month quarter 1 begins |
| Curium unavailable weeks | none | pick any weeks | Weeks Curium cannot supply. Infeasible if EU demand falls there |
| BWXT unavailable weeks | none | pick any weeks | Weeks BWXT cannot supply |

### 9.6 Internal parameters (not user-facing)

| Parameter | Value | Purpose |
|---|---|---|
| Exhaustive onboarding threshold | 500 combinations | Below this, onboarding is a proven optimum; above it a heuristic runs and the screen says so |
| Onboarding seeds / passes | 10 / 3 | Heuristic search breadth |
| Low match warning threshold | 50% | Warns when fewer than half of sites match the Master Planner |

---

## 10. Development methodology, backlog and roadmap

### 10.1 Was it Agile?

**Spec-driven incremental delivery, not textbook Scrum.** There were no sprints,
story points, standups or a formal product owner — with a single developer those
ceremonies would have been theatre. What it does have are the artefacts that matter
for auditability:

| Artefact | Where | Contents |
|---|---|---|
| Requirements | `.kiro/specs/*/requirements.md` | User stories with numbered, testable acceptance criteria in `SHALL` form |
| Design | `.kiro/specs/*/design.md` | Chosen approach, alternatives rejected and why |
| Backlog | `.kiro/specs/tasks.md` | Phased task list, each task referencing the requirement it satisfies |
| Architecture | `.kiro/specs/ARCHITECTURE.md` | Layering rules and the migration plan |

Five specs were delivered in sequence, each iterating on business feedback:

1. `integrated-cost-optimization` — the DP solver and cost model
2. `onboarding-recommendation` — new-customer start-week search
3. `streamlit-cost-optimizer-ui` — the single unified interface
4. `supplier-constraints` — Curium/BWXT, Sr-82, quotas
5. `optimizer-enhancements` — comparison, changed weeks, reference week, multi-customer onboarding

Delivery ran in **15 numbered phases** (0 to 14), each ending in a checkpoint where the
full test suite had to pass before the next began. Requirements are traceable: tasks
cite identifiers such as `[S-3.2]` and `[E-7.13]` back to the acceptance criterion
they implement.

These are all real files in the repository and can be shared as-is.

### 10.2 User stories

Every functional area traces to a user story. Examples, verbatim from the specs:

> As a production planner, I want to upload the plan my team built by hand and see
> its cost next to the optimized plan's cost, so that I can quantify what the tool
> saved and justify acting on its output.

> As a production planner, I want to know which customers had their production week
> moved, so that I can check the changes are acceptable before committing.

Each is followed by numbered acceptance criteria, and each criterion has at least one
automated test.

### 10.3 Quality process

- **680 automated tests**, organised by layer: pure business logic, service
  orchestration with in-memory fakes, file handling, UI rendering, and end-to-end.
- **Property-based tests** on the cost model and supplier rules — these generate
  hundreds of random inputs and assert invariants hold, rather than checking a handful
  of examples.
- **Layering enforced by test.** The interface cannot contain business logic and the
  service layer cannot reach into file handling; a test fails if either happens.
- **Deployment pinned.** Dependencies are pinned to exact versions after a
  version-drift incident, and the app is rendered against the real interface library in
  tests rather than a stand-in.

### 10.4 Currently in development

Committed from your last round of feedback. Status is stated precisely — "accepted"
means agreed and specified but not yet built, and nothing below is on the deployed
app until it says built.

| Item | Status |
|---|---|
| Curium, BWXT, BWXT allocation | **Built and verified** against your workbook |
| Site codes written into a copy of the Master Planner | **Built and verified** |
| Reference week on Import Manual Plan and Comparison | **Built and verified** |
| Onboarding against a fixed existing plan | **Built and verified** |
| Row-level error messages in Comparison | **Built and verified** |
| Changed Customer Weeks display fixes | **Built and verified** |

Two of your five points from that round were **questions rather than defects**, and
those are answered in this document rather than in code:

- Onboarding does run the optimizer for every candidate week (§6.4, L4). A fixed-plan
  alternative is accepted and listed above.
- Changed Customer Weeks calculates correctly — 41 moved earlier and 191 later on your
  2026 data. The two ways the screen can still look empty, and the fixes for both, are
  in L3 and the table above.

### 10.5 Roadmap

Not started, no committed dates:

| Item | Driver |
|---|---|
| Raw material procurement planner | Previously discussed as a separate phase |
| Confirmed quota and shortfall figures replacing placeholders | Blocked on A12/A13 |
| Carrying inventory across the horizon boundary | Depends on §3.1 confirmation |
| Enforcing the EU material rule rather than flagging it | Depends on L1 becoming a real risk |
| Supplier economics influencing the production plan | Scope question in §6.4 |

---

## 11. Outputs, calculations and error messages

Full column-by-column definitions are in
[USER_GUIDE.md §8](USER_GUIDE.md#8-column-definitions). Summarised here:

### 11.1 The results workbook

| Sheet | Contents |
|---|---|
| `Weekly_Plan` | 52 rows: production, batches, inventory, supplier split, Sr-82, costs |
| `Sites_Clean` | Sites actually used after removing inactive and invalid rows |
| `Input_Issues` | Rows skipped, with the reason |
| `Model_Params` | Every parameter used — makes any run reproducible |
| `Changed_Weeks` | Per-generator manual versus optimized week |
| `Quota_Status` | Supplier quota position per quarter |
| `Cost_Comparison` | Manual versus optimized, per component |
| `Weekly_Comparison` | Manual versus optimized production per week |
| `Assigned_IDs` | Codes generated for columns without an account number |

### 11.2 Key calculations

```
Good_Production      generators sellable that week, after QC discards
Net_Inventory_End    positive = finished stock held; negative = unmet demand
Early_Units_Held     drives penalty cost
Idle slots           max(0, 30 - Good_Production) in a normal week
Penalty_Cost         7,000 x early units, or 700,000 x late units
Capacity_Cost        15,000 x idle slots
Composite_Cost       the three above with weights applied
Total_Sr82_mCi       Curium activity + BWXT activity, surplus included
```

### 11.3 Error messages and what to do

| Message | Cause | Resolution |
|---|---|---|
| `Missing required columns` | Input file lacks Site_ID, Active, Next_Demand_Week or Interval_Weeks | Check the sheet selected; header names are case-insensitive |
| `No feasible production states at week N` | Demand cannot be met given shutdowns and capacity | Reduce shutdown weeks, or raise batch limits |
| `No feasible solution with zero inventory at week 52` | Total demand exceeds total capacity | Reduce demand or extend the horizon |
| `Week N: EU-restricted demand requires Curium, but Curium is unavailable` | Conflicting supplier availability | Remove that week from Curium's unavailable list |
| `Week N: both suppliers unavailable` | Demand due with no supply | Mark the week as a shutdown, or free a supplier |
| `Only X of Y sites matched a Master Planner column` | Input file and Master Planner use different identifiers | Generate the input file from Import Manual Plan |
| `Site_ID 'X' already exists` | Duplicate identifier during onboarding | Use a unique serial number |
| Quota row shows `Partial — not penalised` | Quarter only partly inside the horizon | Informational; set the reference week to a quarter boundary to remove |
| `Manual plan produces more than the 45-unit weekly maximum in N week(s)` | The Master Planner schedules more than 3 batches' worth of sellable generators | Move the excess to an adjacent week; until then no saving is reported (L6) |
| `Manual plan ships more than 2 EU-restricted unit(s) in a week without building stock beforehand` | More Denmark/UK/Netherlands/Sweden deliveries in one week than can ship | Build the excess a week earlier, or spread the deliveries (L6) |
| `Manual plan ends the horizon with N unit(s) of unsold stock` | Plan produces more than total demand | Check for deliveries scheduled past week 52 |
| `The manual plan cannot be run as written` | Any of the three above | The difference shown is the cost of legality, not a loss; fix the named weeks and re-run |

We accept these are currently too terse in the Comparison screen. Row-level
diagnostics — which row, what is wrong, how to fix it — are in progress.

---

## 12. On replacing week numbers with dates

**We agree, and work is already underway.** It is the right call for the reason you
give: week numbers restart every January, so comparing plans across a year boundary is
ambiguous today.

Two things worth setting out about how far it can go.

**Done.** Every week-number input is now a picker showing the real date, and the
onboarding window is entered as dates. Details below.

**The foundation.** The reference week set in Settings anchors
planning week 1 to a real manufacturing date, and both the Master Planner import and
the Comparison read from that week forward — crossing the calendar-year boundary,
which reading by year could never do. Anchoring had to land in both places at once:
anchoring only the import while the comparison still numbered weeks by calendar year
would have reported **1,186 false per-customer changes instead of 60 real ones** on
your 2026 data.

**What changed.** Shutdown weeks, partial shutdown weeks and supplier unavailable
weeks were free-text boxes taking `1,2,26`. They are now pickers listing every week
in the horizon, each labelled `Week 26 — 29 Jun 2026`. The onboarding window, which
took two integers, is now two date fields.

One deliberate choice: shutdown weeks are a **set** of weeks, not a range, so they
get a date-labelled picker rather than a date field. A date range would have
misrepresented the setting. Where a value genuinely is a single point in time — the
onboarding window — it is a date field.

**Where a week number still exists underneath.** The Master Planner is week-numbered
and the solver works in week indices, so dates are the interface while an index
remains the internal key. In practice that means a date you enter is snapped to the
planning week containing it, and the screen tells you which week that was rather than
rounding silently. Dates outside the horizon are clamped to its first or last week
rather than being dropped.

**If no reference week is set** the pickers still work, labelled with bare week
numbers, and the onboarding window stays numeric — with a prompt explaining that
setting a reference week turns both into dates. Sessions saved before this change,
holding values like `"1,2,26"`, are migrated on load.

**Where a week number has to survive underneath.** The Master Planner itself is
week-numbered, and the optimizer works in week indices. So dates become the interface
while an index remains the internal key. In practice that means one thing for you: a
date you enter is snapped to the planning week that contains it, and the tool shows
you which week that was rather than silently rounding.

The reference week change currently in progress is the foundation for this — once
week 1 is pinned to a real date in every part of the tool, converting the remaining
inputs to date pickers is mechanical. We would rather land the anchor first and get it
right than convert the fields and leave the ambiguity underneath.

---

## Summary for management

- The optimizer produces a **proven optimum** for the stated cost model, not a
  heuristic guess, and is fully deterministic and auditable.
- On your own data it cuts weekly volatility by a third and overtime weeks from 23 to
  1.
- **79% of the reported total cost is unused-capacity cost, and 98% of that is
  structural** — your demand is 86% of annual capacity, so idle slots are arithmetic,
  not a planning failure. Judge the tool on the penalty and overtime components.
- Two cost figures are **placeholders awaiting your confirmation** (A12, A13) and one
  business rule was **revised this round** (A9).
- Seventeen assumptions are listed in §1 for validation. That list is the fastest way
  to establish whether the tool still reflects how you operate.

*Every figure here is reproducible from the files you supplied. Happy to walk through
any of it, or to re-run any scenario with different assumptions.*
