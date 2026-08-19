# Ruby Fill Optimizer — User Guide

A plain-language guide to every screen, every setting, and every column in the output.

**Live app:** https://rubypannerv2-hexy3fj6xdshhns2ggsrbx.streamlit.app

---

## Contents

1. [What the tool does](#1-what-the-tool-does)
2. [Quick start](#2-quick-start)
3. [Tab: Settings](#3-tab-settings)
4. [Tab: Import Manual Plan](#4-tab-import-manual-plan)
5. [Tab: Cost Optimizer](#5-tab-cost-optimizer)
6. [Tab: Onboarding](#6-tab-onboarding)
7. [Tab: Comparison](#7-tab-comparison)
8. [Column definitions](#8-column-definitions)
9. [Output files](#9-output-files)
10. [Troubleshooting](#10-troubleshooting)
11. [Still to be confirmed](#11-still-to-be-confirmed)

---

## 1. What the tool does

You plan generator production 52 weeks ahead. Every site is due a generator on a
repeating cycle, the factory can only make so many per week, and Sr-82 comes from
two suppliers with their own rules. Balancing all that by hand is slow, and it is
easy to end up with 18 generators one week and 41 the next.

The tool does four things:

| You want to | Use this tab |
|---|---|
| Turn your manual plan into an optimizer input file | **Import Manual Plan** |
| Get a balanced 52-week production plan | **Cost Optimizer** |
| Find the best week to start a new site | **Onboarding** |
| See what the optimizer saved versus your manual plan | **Comparison** |

Everything is configurable in **Settings**. Nothing is hardcoded.

**Nothing is saved.** Settings and results live in your browser session only. Close
the tab and they are gone, so download anything you want to keep.

---

## 2. Quick start

The shortest path from your manual plan to an optimized plan:

1. **Settings → Reference dates.** Tick *Use a reference week* and enter the
   manufacturing date your Master Planner shows for week 1. This makes every result
   show real dates instead of week numbers.
2. **Import Manual Plan.** Upload the Master Planner workbook, press
   *Build input file*, then press *Use it in the Cost Optimizer now*.
3. **Cost Optimizer.** Press *Run optimizer*. Read the plan, download the workbook.
4. **Comparison.** Press *Run comparison* to see the saving against your manual plan.

That is the whole loop. Everything below is detail.

---

## 3. Tab: Settings

Change a value here and it applies to the Optimizer, Onboarding and Comparison
alike. *Restore all defaults* puts everything back.

### Reference dates

| Setting | What it means |
|---|---|
| **Use a reference week** | Off = results show week numbers only. On = results show real dates. |
| **Week 1 manufacturing date** | The manufacturing date your Master Planner shows for planning week 1. Week 2 is 7 days later, and so on. |
| **Calibration offset (days)** | How many days after manufacturing the calibration date falls. Default 4. See below. |

#### What the calibration offset actually is

**Short answer: it is a display setting. It turns week numbers into calibration
dates. It has no effect on the plan, the costs, or which week anything is made in.**

The optimizer works in week numbers — week 1 to week 52. Week numbers on their own
are hard to read, so the tool converts them to dates using two settings:

```
MFG date for week N  =  Week 1 manufacturing date  +  7 days × (N − 1)
Cal date for week N  =  that week's MFG date  +  calibration offset
```

So if week 1 manufactures on Monday 5 January and the offset is 4 days, week 1
calibrates on Friday 9 January, week 2 manufactures Monday 12 January and
calibrates Friday 16 January, and so on down the plan.

**Why it is one number rather than one per week.** The tool steps forward in whole
7-day weeks, so every week's manufacturing date lands on the same weekday as week 1.
Anchor week 1 to a Monday and every week is a Monday. A single offset of 4 then puts
every calibration on a Friday — which is why 4 is the default.

**What to set it to.** Take the manufacturing date you entered for week 1 and count
the days forward to its calibration Friday:

| Week 1 manufactures on | Set the offset to |
|---|---|
| Monday | 4 |
| Tuesday | 3 |
| Wednesday | 2 |
| Thursday | 1 |
| Friday | 0 |

**The case where it will look wrong.** In real life a manufacturing date sometimes
slips to a different weekday — a holiday, a shutdown recovery week. The tool does
not model those slips: it keeps adding 7 days. For those specific weeks the
`Cal_Date` column will be a few days out.

That is worth knowing, but it changes nothing that matters. `MFG_Date` and
`Cal_Date` are labels printed next to the plan. The production quantities, the
costs, the supplier split and the week each site is served are all decided from week
numbers before any date is applied. If a week's real manufacturing day has moved,
read that row's calibration date off the Master Planner instead.

Set the offset to 0 if you would rather the calibration date just mirror the
manufacturing date.

### Production constraints

| Setting | Default | What it means |
|---|---|---|
| **Horizon (weeks)** | 52 | How many weeks to plan. |
| **Min batch produced** | 2 | Fewest units in a batch, counting the QC unit. |
| **Max batch produced** | 16 | Most units in a batch, counting the QC unit. |
| **Test discard per batch** | 1 | Units pulled from each batch for QC testing. These are made but never sold. |
| **Normal max batches/week** | 2 | Batches in an ordinary week. 2 × (16 − 1) = **30 sellable generators**. |
| **Overtime max batches/week** | 3 | Batches when overtime is used. 3 × 15 = **45 sellable generators**. |
| **Shutdown weeks** | empty | Weeks with no production at all. Comma-separated, e.g. `1,2,26`. |
| **Partial shutdown weeks** | empty | Weeks limited to a single batch (15 generators). |

### Costs and weights

The optimizer adds up four costs and finds the cheapest plan. These set the prices.

| Setting | Default | What it means |
|---|---|---|
| **Early penalty rate** | $7,000 | Cost of holding one finished generator in the warehouse for one week because it was made before it was needed. |
| **Late penalty multiplier** | 100 | How much worse late is than early. At 100, being one week late costs $700,000 per generator — deliberately extreme so the optimizer treats late delivery as a last resort. |
| **Overtime rate** | $2,000 | Cost of running a third batch in a week. |
| **Capacity rate** | $15,000 | Charged per unused generator slot per week, to discourage leaving the factory idle. |

The four **weights** (0 to 1) scale each cost in the objective. Set one to 0 to make
the optimizer ignore that cost entirely. At least one must be above 0.

> **Worth knowing about the capacity weight.** On your demand levels the capacity
> cost is mostly a fixed floor — demand simply does not fill the factory every week,
> and no plan can change that. Leaving the capacity weight at 1 means a large,
> mostly-unavoidable number sits in the total and can hide the penalty savings the
> optimizer actually found. If you want to see the improvement clearly, set the
> capacity weight to 0 and compare.

### QC shipping cap

| Setting | Default | What it means |
|---|---|---|
| **Max restricted-country units per week** | 2 | How many generators for Denmark, the UK, the Netherlands and Sweden can clear QC in one week. |

This is a **throughput limit in your own QC process**. It is not the same rule as the
Curium supply restriction below, even though it applies to the same countries.

### Raw material suppliers (Curium / BWXT)

Sr-82 for each supplier run is calculated as:

```
100 × generators  +  10 × batches  +  max(surplus % × base, 20)   mCi
```

| Setting | Default | What it means |
|---|---|---|
| **Sr-82 per generator (mCi)** | 100 | Activity one sellable generator consumes. |
| **Sr-82 per batch / QC generator (mCi)** | 10 | Activity the discarded QC generator in each batch consumes. |
| **Minimum surplus (mCi)** | 20 | Floor on the surplus term, however small the run. |
| **Curium surplus fraction** | 0.05 | Extra 5% required on Curium orders. |
| **BWXT surplus fraction** | 0.02 | Extra 2% required on BWXT orders. |
| **First Curium run (generators)** | 15 | In a three-run week the order is Curium → BWXT → Curium. This is how many generators the first Curium run makes. |
| **Curium quarterly quota (mCi)** | 10,000 | Minimum you must order from Curium per quarter. |
| **BWXT quarterly quota (mCi)** | 10,000 | Minimum you must order from BWXT per quarter. |
| **Quota shortfall penalty (USD per mCi)** | 50,000 | Charge per mCi below a quarterly minimum. **This is a placeholder** — see [section 11](#11-still-to-be-confirmed). |
| **Quarter start month** | 1 (January) | Which month quarter 1 begins in. |
| **Curium / BWXT unavailable weeks** | empty | Weeks a supplier cannot supply. Comma-separated. |

Generators for the restricted European countries must come from Curium material, so
the optimizer always fills that demand from the Curium runs first.

---

## 4. Tab: Import Manual Plan

**This is the tab that saves you the most time.** It reads your Master Planner and
writes the optimizer's input file for you — no retyping site IDs, start weeks or
intervals.

### How to use it

1. Upload the Master Planner workbook.
2. Confirm the sheet is **Schedule**.
3. Press **Build input file**.
4. Either **Download input file**, or press **Use it in the Cost Optimizer now** to
   load it straight into the next tab.

### What it reads

Your Master Planner has one column per site and a `1` in every week that site is due
a generator. From each column the tool works out:

| Field | Where it comes from |
|---|---|
| **Site_ID** | The account number at the start of the header, e.g. `00449`. If there isn't one, the tool generates a stable code like `RF-4e40dfd2`. |
| **Site_Name** | The header text with the account number removed. |
| **Active** | `Y` if the column has at least one `1` in the year read, otherwise `N`. |
| **Next_Demand_Week** | The first week with a `1`. |
| **Interval_Weeks** | The number in brackets in the header, e.g. `(7)`. If the header has no number, the tool measures the gaps between the `1`s and uses the most common gap. |
| **Country** | Read from the header text — `(DK)`, `Netherlands`, `London UK`, `CAN`, `Switzerland` and so on. Anything with no country hint is treated as USA. |
| **EU_Restricted** | `Y` if the column header is shaded dark blue in the Master Planner. |

### Why the site codes matter

This is what fixes the Comparison tab. Previously the input sheet and the Master
Planner had no field in common, so the tool could not tell which spreadsheet column
belonged to which site, and the comparison had nothing to match on.

Now every site carries the same code in both places, because the input file is
generated *from* the Master Planner. The **Site code mapping** table lists each code
next to the Master Planner column it came from.

**One thing to do once:** for any site where *Code_Source* says `generated`, paste
that code into the front of the Master Planner column header. From then on the site
has a permanent ID that survives columns being moved or renamed.

### Reading the summary line

> *Read from the **2026** rows. The manual plan schedules **1383** deliveries that
> year; the cadences derived here imply **1397**.*

These two numbers should be close, not identical. Your manual plan nudges individual
weeks for holidays and shutdowns; the optimizer works from a clean repeating
interval. A gap of a few percent is normal. If the tool warns you the gap is large,
check the *Interval_Weeks* column in the mapping table before optimizing.

### Things to check

The **Things to check** list is advisory — none of it stops the optimizer. Typical
entries:

| Note | What to do |
|---|---|
| No account number in the header | Add the generated code to the Master Planner header. |
| No scheduled generators in the selected year | Site is marked inactive. If it should be planned, set *Active* to `Y` and give it a start week. |
| Header says every 7 weeks, but the scheduled weeks are 9 weeks apart | Decide which is right. The tool used the header. |
| Shaded as EU-restricted but the header reads as 'usa' | Put the country in the header so the supply rule applies. |
| More than one column with code `00460` | Two Master Planner columns share an account number. The second was renamed `00460-2`. Fix the header. |

### Advanced: year

Week numbers repeat every year in the Master Planner, so one year has to be chosen.
Left blank, the tool picks the year with the most fully-planned weeks. Enter a year
to override it.

---

## 5. Tab: Cost Optimizer

Upload a sites file (or arrive here from *Import Manual Plan*) and press **Run
optimizer**.

Optionally upload the Master Planner under *Manual plan (optional)* as well. You
don't need it to get a plan, but without it the **Changed customer weeks** table
can't be shown, because there is nothing to compare against.

### The headline numbers

| Metric | Meaning |
|---|---|
| **Total cost** | The four components below, added up with their weights applied. Use it to compare two runs, not as a real budget figure. |
| **Penalty** | Cost of making generators earlier or later than they were due. |
| **Overtime** | Cost of the weeks that needed a third batch. |
| **Capacity** | Cost charged for unused factory slots. Largely a fixed floor — see the note in Settings. |
| **Overtime weeks** | How many weeks ran a third batch. |
| **Supplier quota penalty** | Charge for quarters that fell below a supplier minimum. |

### Supplier quota status

One row per supplier per quarter, with three possible statuses:

- **OK** — the quarter is fully inside the plan and met its minimum.
- **SHORTFALL** — fully inside the plan and below its minimum. Charged.
- **Partial — not penalised** — only part of that quarter falls inside the 52 weeks.

**About partial quarters.** If your reference week isn't the first week of a quarter,
the 52 weeks straddle five quarters and the first and last are only partly covered.
The missing weeks are real — the early ones already happened and sit in SAP, the
late ones fall beyond week 52 — the planner just cannot see them. Charging a full
quarter's minimum against a few visible weeks would invent a shortfall that does not
exist, and because the shortfall charge is very high by design, that invented number
would swamp every real cost and distort the plan.

So partial quarters are shown but never charged. Read them as a **run-rate check**:
the *Target* column scales the minimum down to the weeks actually covered, so you can
see whether ordering is tracking at about the right pace. A gap there is worth a
glance, not an alarm.

To remove partial quarters entirely, set the reference week to the first week of a
quarter. The 52 weeks then line up with four complete quarters.

### Changed customer weeks

Only appears when you have uploaded the Master Planner. Each row is one generator,
comparing the week your manual plan made it against the week the optimizer makes it.

- **Moved earlier** — made sooner and held in the warehouse until due.
- **Moved later** — made closer to its due date.
- **Same as manual** — unchanged.
- **New customer** — in the input file but not in the manual plan.
- **No counterpart** — the optimizer schedules more generators for this site than the
  manual plan did, so this one has nothing to compare to.

A large *Moved later* count is not a problem in itself. It usually means the manual
plan was building stock earlier than it needed to, and the optimizer is holding less
inventory.

---

## 6. Tab: Onboarding

Finds the best start week for one or more new sites without breaking the existing
plan.

1. Add a row per new site: its ID, name, country, interval, and the **earliest** and
   **latest** week it could start. (A site that could go live in week 4 but must be
   live by week 9 gets earliest 4, latest 9.)
2. Upload the current sites file.
3. Run the recommendation.

You get a ranked list of week combinations with the added cost of each, so you can
pick a week that suits the customer rather than only the cheapest one. Once you have
chosen, the tool generates an updated input file with the new sites already in it,
ready for the Cost Optimizer.

Existing rows are copied through untouched. New rows are flagged `Is_New = Y`.

---

## 7. Tab: Comparison

Run the Cost Optimizer first, then upload the Master Planner here and press **Run
comparison**. You get your manual plan and the optimized plan costed with exactly
the same model, component by component.

| Column | Meaning |
|---|---|
| **Baseline** | Cost of your manual plan. |
| **Optimized** | Cost of the optimizer's plan. |
| **Saving_Abs** | Baseline − Optimized, in dollars. Negative means the optimizer's plan is worse on that component. |
| **Saving_Pct** | The same saving as a percentage of baseline. |

**Where to look.** Penalty and overtime are where the optimizer earns its keep.
Capacity barely moves between plans and can even come out slightly worse, because
it is driven by demand versus factory size rather than by scheduling.

If you see *"Manual plan total does not equal total demand"*, the generator count in
the Master Planner doesn't match the demand implied by the input file. That is
expected when the two come from different snapshots, and it is also why generating
the input file from the Master Planner is the cleanest way to run this comparison.

---

## 8. Column definitions

### Weekly production plan

| Column | Meaning |
|---|---|
| **Now** | `▶` marks the week containing today's date. |
| **Week** | Planning week number, 1 to 52. |
| **MFG_Date** | Manufacturing date for that week. Display only. |
| **Cal_Date** | Calibration date for that week. Display only. See [the calibration offset](#what-the-calibration-offset-actually-is). |
| **Week_Type** | `Normal`, `Partial` (one batch only) or `Shutdown` (no production). |
| **Demand_Due** | Generators due to customers this week. |
| **Good_Production** | Sellable generators made this week, after QC discards. |
| **Batch_Count** | Batches run this week (0–3). |
| **Batch1/2/3_Produced** | Units produced in each batch, including its QC unit. |
| **Produced_Total** | All units produced, including QC units. |
| **Testing_Discard** | Units pulled for QC testing. One per batch. |
| **Overtime_Used** | `1` if a third batch ran. |
| **Net_Inventory_End** | Stock at week end. Positive = finished generators waiting. Negative = unmet demand. |
| **Early_Units_Held** | Finished generators held in the warehouse this week, waiting for their due date. Drives the penalty cost. |
| **Late_Units_Backlog** | Generators that should have shipped and haven't. Charged at the much higher late rate. |
| **ROW_Demand_Due** | Demand this week from Denmark, the UK, the Netherlands and Sweden. |
| **ROW_Fulfilled** | How much of that was served, capped by the QC shipping cap. |
| **ROW_Inventory** | Restricted-country stock carried into next week. |
| **Penalty_Cost_USD** | Early and late penalty for this week. |
| **Overtime_Cost_USD** | Overtime cost for this week. |
| **Capacity_Utilization_Cost_USD** | Unused-slot cost for this week. |
| **Composite_Cost_USD** | The three above, with weights applied. |
| **Cumulative_Composite_Cost_USD** | Running total from week 1. |
| **Curium_Good** | Sellable generators from Curium material this week. |
| **BWXT_Good** | Sellable generators from BWXT material this week. |
| **Run_Sequence** | Order of supplier runs, e.g. `Curium, BWXT, Curium`. |
| **Supplier_Label** | Which suppliers were used: `Curium`, `BWXT`, or `Curium / BWXT`. |
| **Curium_Activity_mCi** | Sr-82 ordered from Curium this week, surplus included. |
| **BWXT_Activity_mCi** | Sr-82 ordered from BWXT this week, surplus included. |
| **Total_Sr82_mCi** | Both suppliers combined. |
| **EU_Restricted_Demand** | Restricted-country generators this week. Must come from Curium. |

### Supplier quota status

| Column | Meaning |
|---|---|
| **Supplier** | Curium or BWXT. |
| **Quarter** | Quarter number within the horizon. |
| **Weeks** | Week range the quarter covers. |
| **Coverage** | Weeks inside the plan versus weeks in a full quarter, e.g. `3/13 wks`. |
| **Quota (mCi)** | The supplier's minimum for a full quarter. |
| **Target (mCi)** | What to expect over the weeks this plan covers. Same as Quota for a full quarter; scaled down for a partial one. |
| **Ordered (mCi)** | Sr-82 the plan actually buys. |
| **Gap (mCi)** | How far Ordered falls below Target. |
| **Penalty** | Charge for the gap. Always $0 for a partial quarter. |
| **Status** | `OK`, `SHORTFALL`, or `Partial — not penalised`. |

### Changed customer weeks

| Column | Meaning |
|---|---|
| **Site_ID** | Site code. |
| **Site_Name** | Site name. |
| **Country** | Site country. |
| **Manual_Plan_Week** | Week your manual plan made this generator. |
| **Optimized_Week** | Week the optimizer makes it. |
| **Week_Shift** | Optimized − Manual. Negative = earlier, positive = later. |
| **Shift** | Plain-language label for the shift. |
| **Due_Week** | Week the customer is due their generator. |
| **New_Customer** | `Y` if the site isn't in the manual plan. |

### Site code mapping (Import Manual Plan)

| Column | Meaning |
|---|---|
| **Site_ID** | The code assigned to this site. |
| **Site_Name** | Name read from the header. |
| **Master_Planner_Column** | Spreadsheet column letter, so you can find it again. |
| **Master_Planner_Header** | The header text exactly as it appears. |
| **Code_Source** | `account number` (taken from the header) or `generated` (the tool made one). |
| **Interval_Weeks** | Weeks between deliveries. `0` = a single delivery. |
| **Interval_Source** | `header` (from the brackets), `schedule gaps` (measured from the `1`s), or `one-time delivery`. |
| **Scheduled_Deliveries** | How many `1`s the column has in the year read. |
| **First_Scheduled_Week** | Week of the first `1`. Blank when inactive. |

### Data quality issues

| Column | Meaning |
|---|---|
| **row_index** | Row in your input file. |
| **site_id** | The site involved. |
| **issue** | What was wrong. That row was skipped. |

---

## 9. Output files

### Results workbook (Cost Optimizer)

| Sheet | Contents |
|---|---|
| **Weekly_Plan** | The 52-week plan. Every column in the table above. |
| **Sites_Clean** | The sites actually used, after removing inactive and invalid rows. |
| **Input_Issues** | Rows that were skipped, and why. |
| **Model_Params** | Every setting used for this run. Keep this with the plan so a result can always be reproduced. |
| **Changed_Weeks** | Per-generator comparison against the manual plan. Shifts are colour-highlighted. |
| **Quota_Status** | Supplier quota position per quarter. Shortfalls highlighted. |
| **Cost_Comparison** | Manual versus optimized, component by component. |
| **Weekly_Comparison** | Manual versus optimized production, week by week. |
| **Assigned_IDs** | Codes generated for Master Planner columns that had no account number. |

Sheets only appear when the run produced that data — no manual plan means no
`Changed_Weeks` sheet.

### Input file (Import Manual Plan)

| Sheet | Contents |
|---|---|
| **Sites** | Upload this to the Cost Optimizer as-is. |
| **Site_Mapping** | Each site code beside its Master Planner column. Share with the planning team. |
| **Conversion_Notes** | Everything from *Things to check*. |

---

## 10. Troubleshooting

**"Missing required columns"**
Your sites file needs `Site_ID`, `Active`, `Next_Demand_Week` and `Interval_Weeks`.
`Country` is optional but without it no site counts as restricted. Check you picked
the right sheet.

**Comparison says it can't match sites**
The Master Planner and the input file have no site codes in common. Generate the
input file from *Import Manual Plan* and the codes will match automatically.

**Site IDs lost their leading zeros**
The app preserves `00449` throughout. If your source file shows `449`, Excel
converted it to a number before the app saw it. Format the column as Text.

**Total cost looks enormous**
Almost always the supplier quota penalty. The $50,000 per mCi default is a
deliberately high placeholder, so any shortfall dwarfs every real cost. Check the
*Supplier quota status* table, and see [section 11](#11-still-to-be-confirmed).

**Every quarter shows as partial**
Your reference week isn't on a quarter boundary. Either accept it — partial quarters
aren't charged — or set the reference week to the first week of a quarter.

**A site is missing from the plan**
Check *Data quality issues*. Common causes: `Active` isn't `Y`, a duplicate
`Site_ID`, or a `Next_Demand_Week` outside 1–52.

**The app is slow or unresponsive**
Onboarding with several new sites explores many combinations. Give it time rather
than reloading. Reloading loses your settings.

---

## 11. Still to be confirmed

Four numbers in the model are still placeholders. The tool works, but these figures
should be replaced with your real ones before the output is used for decisions.

| Item | Current value | What we need |
|---|---|---|
| **Supplier shortfall penalty** | $50,000 per mCi | The real commercial cost of missing a quarterly minimum. The current value was set deliberately high to make shortfalls effectively unacceptable; it dominates every other cost. |
| **Quarterly quota** | 10,000 mCi each | Confirmation of the actual minimum for Curium and for BWXT. |
| **Split-week allocation** | 15 generators in the first Curium run | Whether the split is a fixed count or a percentage of the week's output. |
| **Late penalty multiplier** | 100× the early rate | Whether late delivery should really be treated as this close to forbidden. |

All four are editable in **Settings**, so you can try your own figures immediately.

---

*Questions or something that doesn't match how you work? Send it over — the model is
configurable and the assumptions above are all changeable.*
