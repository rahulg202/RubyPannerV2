# Requirements Document — Raw Material Supplier Constraints

## Introduction

The central factory producing Ruby Fill generators sources its raw material (Strontium-82) from two suppliers: **Curium** and **BWXT**. Either supplier, or both, may be used in a given production week. The two suppliers differ in surplus yield, customer eligibility, and quarterly minimum ordering quota, and each of these differences constrains both the weekly production schedule and the onboarding of new customers.

Today these calculations are performed manually in the Master Planner sheet (columns `Sr-82 Req (extra 1%)` and `Strontium Supplier`), with order quantities reconciled against SAP reports to track quarterly quota fulfilment. This feature moves that logic into the Ruby Fill Optimizer so that supplier selection, Sr-82 activity requirements, and quota compliance are part of the optimization rather than a manual post-check.

Source: `rubyfill/Ruby Fill Optimizer- additional changes one pager.docx`, `rubyfill/Ruby Fill strontium requirement calculation.xlsx`.

## Glossary

- **Supplier**: A raw material vendor. Exactly two exist: `Curium` and `BWXT`.
- **Sr-82**: Strontium-82, the raw material. Quantities are expressed in millicuries (mCi) of **Activity**.
- **Activity**: The mCi of Sr-82 required to produce a given number of generators from a given Supplier, including per-generator content, per-batch overhead, and Supplier-specific surplus.
- **Surplus_Percentage**: The Supplier-specific proportion of additional raw material required on top of the base requirement. Curium = 5%, BWXT = 2%.
- **Minimum_Surplus**: The floor applied to the surplus term, in mCi. Value = 20.
- **Split_Week**: A production week in which both Suppliers provide raw material.
- **Single_Supplier_Week**: A production week in which exactly one Supplier provides raw material.
- **First_Run_Allocation**: The number of generators produced in the first production run of a Split_Week. This run always uses Curium raw material. Default = 15 generators (1 batch), yielding 1586 mCi of Curium Activity.
- **Supplier_Availability**: A per-week, per-Supplier flag indicating whether that Supplier can supply raw material in that week.
- **Quarterly_Minimum_Quota**: The minimum total Activity (mCi) that must be ordered from a Supplier within a calendar quarter. Reference value = 10,000 mCi per Supplier per quarter.
- **Quota_Shortfall_Penalty**: The charge levied by a Supplier when the ordered Activity for a quarter falls below its Quarterly_Minimum_Quota. Charged on the difference in quantity. No penalty applies to ordering above quota.
- **EU_Restricted_Customer**: A customer in Europe excluding Switzerland. These customers are marked in blue (fill `FF002060`) in the Master Planner `Schedule` sheet and **cannot** receive generators produced from BWXT raw material. In the current dataset this cohort comprises 10 sites across Denmark, the Netherlands, and the United Kingdom.
- **Quarter**: A three-month grouping of the 52-week planning horizon used for quota accounting. The month on which the first quarter begins is configurable, defaulting to January.
- **Quarter_Start_Month**: The configurable month on which quarter 1 begins. Default January, giving quarters of January–March, April–June, July–September, and October–December.
- **QC_Shipping_Cap**: The existing `row_cap` constraint limiting how many generators may ship to the restricted European countries in a single week, for quality control throughput reasons. This is a **separate and independent** constraint from the BWXT material restriction, even though it affects the same customers.
- **IntegratedParams**: The existing parameter dataclass in `integrated_cost_optimizer.py` holding all production constraints and cost rates.
- **Good_Units**: Generators produced that are usable (after the 1-unit-per-batch test discard). Max 15 per batch.

## Verified Activity Formula

The following formula was reverse-engineered from `Ruby Fill strontium requirement calculation.xlsx` and reproduces all six sample weeks exactly.

For `G` good generators sourced from a Supplier with surplus fraction `p`:

```
base(G)      = 100 * G + 10 * ceil(G / 15)
surplus(G,p) = max( ceil( p * base(G) ), 20 )
activity(G,p) = base(G) + surplus(G,p)          for G > 0
activity(0,p) = 0
```

- `100` mCi is the per-generator Sr-82 content.
- `10` mCi is the per-batch overhead; `ceil(G / 15)` is the batch count and is identical to the existing `batches_needed()` function.
- The surplus term rounds **up to the nearest whole mCi** and is floored at `Minimum_Surplus` (20 mCi).

Worked verification against the source workbook:

| Week | Curium gens | BWXT gens | Curium mCi | BWXT mCi | Total Sr-82 Req |
|---|---|---|---|---|---|
| 1 | 15 | 0 | 1586 | 0 | 1586 |
| 2 | 15 | 22 | 1586 | 2265 | 3851 |
| 3 | 0 | 25 | 0 | 2571 | 2571 |
| 4 | 15 | 0 | 1586 | 0 | 1586 |
| 5 | 15 | 23 | 1586 | 2367 | 3953 |
| 6 | 22 | 0 | 2331 | 0 | 2331 |

Note: an older Master Planner column was labelled `Sr-82 Req (extra 1%)`. The business has confirmed the "1%" was a stale title, now removed in the latest Master Schedule; the only surplus percentages are 5% (Curium) and 2% (BWXT), and no other margin applies. The formula above is authoritative.

### QC generators and the per-batch term

The `10 * ceil(G / 15)` term is the Sr-82 for the QC (test) generators. The business has confirmed each QC generator consumes 10 mCi, and one QC generator is produced per batch — matching the existing `test_discard_per_batch = 1` in the production model. Full weekly Sr-82 requirement therefore equals the activity for the commercial good generators plus 10 mCi per QC generator, which the formula already captures via the per-batch term. The `QC GEN` column in the Master Planner only records these standard one-per-batch test units; no additional QC generators beyond that are scheduled.

## Requirements

### Requirement 1: Supplier Parameter Configuration

**User Story:** As a production planner, I want to configure each supplier's yield, quota, and availability, so that the optimizer reflects current commercial terms without a code change.

#### Acceptance Criteria

1. THE Optimizer SHALL expose a configurable `Surplus_Percentage` per Supplier, defaulting to 0.05 for Curium and 0.02 for BWXT.
2. THE Optimizer SHALL expose a configurable `Minimum_Surplus` parameter in mCi, defaulting to 20.
3. THE Optimizer SHALL expose a configurable per-generator Sr-82 content parameter in mCi, defaulting to 100.
4. THE Optimizer SHALL expose a configurable per-batch Sr-82 overhead parameter in mCi, defaulting to 10.
5. THE Optimizer SHALL expose a configurable `Quarterly_Minimum_Quota` per Supplier in mCi, defaulting to 10000 for both Curium and BWXT.
6. THE Optimizer SHALL expose a configurable `First_Run_Allocation` parameter in generators, defaulting to 15.
7. THE Optimizer SHALL expose a configurable `Quota_Shortfall_Penalty` rate in USD per mCi of shortfall.
8. THE Optimizer SHALL expose a `Supplier_Availability` input allowing the planner to mark, for each Supplier, the set of weeks in which that Supplier is unavailable.
9. WHEN the planner supplies a `Surplus_Percentage` outside the range [0.0, 1.0], THE Optimizer SHALL reject the input with a validation error and SHALL NOT run.
10. WHEN the planner supplies a negative `Quarterly_Minimum_Quota`, negative `Minimum_Surplus`, or `First_Run_Allocation` outside the range [0, max_good_per_batch], THE Optimizer SHALL reject the input with a validation error and SHALL NOT run.
11. THE Optimizer SHALL persist all supplier parameters into the exported workbook's `Model_Params` sheet alongside the existing parameters.

### Requirement 2: Sr-82 Activity Calculation

**User Story:** As a production planner, I want the tool to compute the exact Sr-82 activity required per supplier per week, so that I no longer maintain that calculation by hand.

#### Acceptance Criteria

1. THE Optimizer SHALL compute per-Supplier weekly Activity as `base(G) + max(ceil(p * base(G)), Minimum_Surplus)` where `base(G) = per_generator_mCi * G + per_batch_mCi * batches_needed(G)`.
2. WHEN a Supplier supplies 0 generators in a week, THE Optimizer SHALL compute that Supplier's Activity for that week as 0 and SHALL NOT apply the `Minimum_Surplus` floor.
3. THE Optimizer SHALL compute the batch count used in the Activity formula via the existing `batches_needed()` function, ensuring consistency with production batching.
4. THE Optimizer SHALL compute total weekly Sr-82 requirement as the sum of the two Suppliers' Activity values for that week.
5. THE Optimizer SHALL round the surplus term up to the nearest whole mCi.
6. THE Optimizer SHALL account for QC generators at the configurable per-batch Sr-82 overhead (default 10 mCi), one QC generator per batch, when computing each Supplier's Activity, consistent with the production model's one test discard per batch.
7. FOR the six reference weeks in `Ruby Fill strontium requirement calculation.xlsx`, THE Optimizer SHALL reproduce the Curium Activity, BWXT Activity, and total Sr-82 requirement values exactly.

### Requirement 3: Weekly Supplier Allocation

**User Story:** As a production planner, I want the optimizer to decide which supplier or suppliers to use each week, so that supplier constraints are satisfied by construction rather than corrected afterwards.

#### Acceptance Criteria

1. FOR each week in the planning horizon, THE Optimizer SHALL determine an allocation of that week's Good_Units between Curium and BWXT such that the two allocations sum to the week's total Good_Units production.
2. THE Optimizer SHALL treat a week with a non-zero allocation to both Suppliers as a Split_Week and a week with a non-zero allocation to exactly one Supplier as a Single_Supplier_Week.
3. IF a Supplier is marked unavailable for a week, THEN THE Optimizer SHALL allocate 0 generators to that Supplier in that week.
4. IF both Suppliers are marked unavailable for a week, THEN THE Optimizer SHALL treat that week as having zero production capacity, equivalent to the existing Shutdown week type.
5. THE Optimizer SHALL enforce the existing weekly capacity limits (30 Good_Units normal, 45 Good_Units overtime) on the combined allocation across both Suppliers.
6. THE Optimizer SHALL report the chosen allocation per week in the output plan.

### Requirement 4: Split Week Run Sequencing

**User Story:** As a production planner, I want split weeks to follow the physical run order on the production line, so that the plan matches how the factory actually runs.

Confirmed run structure: a production week has up to three runs (batches), each producing up to 15 good generators — up to two runs in a normal week and three in an overtime week. The first run of any split week is always Curium. A second Curium run is only permitted when the week has more than two runs; the canonical three-run split is Curium, then BWXT, then Curium.

#### Acceptance Criteria

1. THE Optimizer SHALL model each production week as an ordered sequence of runs, each run being one batch of up to `max_good_per_batch` (15) good generators.
2. WHEN a week uses both Suppliers, THE Optimizer SHALL assign the first run to Curium.
3. WHEN a week uses both Suppliers, THE Optimizer SHALL allocate exactly `First_Run_Allocation` generators (default 15) to the first Curium run.
4. WHEN `First_Run_Allocation` is 15 and Curium supplies exactly one run in the week, THE Optimizer SHALL compute Curium Activity for that week as 1586 mCi.
5. THE Optimizer SHALL assign the second run of a split week to BWXT.
6. THE Optimizer SHALL permit a second Curium run only in weeks with three runs, in which case the run order is Curium, BWXT, Curium.
7. IF a week's total Good_Units production is less than or equal to `First_Run_Allocation`, THEN THE Optimizer SHALL NOT treat that week as a Split_Week.
8. THE Optimizer SHALL compute each Supplier's weekly Activity from the total generators assigned to that Supplier across all of its runs in the week, using the Activity formula, independent of the interleaving order of runs.
9. THE Optimizer SHALL report the per-run supplier sequence for each week in the output plan.

### Requirement 5: EU Customer BWXT Restriction

**User Story:** As a production planner, I want European customers excluding Switzerland to never be served from BWXT material, so that the plan is compliant with their material restriction.

#### Acceptance Criteria

1. THE Optimizer SHALL accept an explicit per-site column in the input file identifying each site as an EU_Restricted_Customer or not, so that Switzerland (excluded from the restriction) and future European sites are classified correctly without relying on a hardcoded country list.
2. WHEN the explicit EU_Restricted_Customer column is absent, THE Optimizer SHALL default the indicator to true for sites whose country is Denmark, the Netherlands, the United Kingdom, or Sweden, and false otherwise, matching the blue-marked cohort in the Master Planner.
3. FOR each week, THE Optimizer SHALL ensure the number of generators allocated to Curium is greater than or equal to that week's EU_Restricted_Customer demand served that week.
4. IF a week's EU_Restricted_Customer demand exceeds the generators that can be allocated to Curium in that week, THEN THE Optimizer SHALL treat that allocation as infeasible and select a different production or allocation plan.
5. THE Optimizer SHALL treat the physical provenance of held inventory (whether a stored generator was made from Curium or BWXT material) as managed by Quality outside the tool, and SHALL NOT attempt to track material provenance through inventory.
6. THE Optimizer SHALL report per-week EU_Restricted_Customer demand and the Curium generators allocated against it in the output plan.

### Requirement 6: Quarterly Minimum Quota and Shortfall Penalty

**User Story:** As a production planner, I want the optimizer to respect each supplier's quarterly minimum order quota, so that we avoid shortfall charges that the business considers unacceptable.

#### Acceptance Criteria

1. THE Optimizer SHALL expose a configurable `Quarter_Start_Month` parameter, defaulting to January, that determines where quarter boundaries fall within the planning horizon.
2. THE Optimizer SHALL group the planning horizon into Quarters based on the `Quarter_Start_Month` and the calendar dates derived from the Reference_Week, and SHALL accumulate each Supplier's ordered Activity within each Quarter.
3. WHEN no Reference_Week is configured, THE Optimizer SHALL fall back to grouping the horizon into consecutive 13-week Quarters starting at week 1, and SHALL disclose that this fallback was used.
4. FOR each Supplier and each fully-covered Quarter, THE Optimizer SHALL compute the shortfall as `max(0, Quarterly_Minimum_Quota - ordered Activity)`.
5. THE Optimizer SHALL apply a Quota_Shortfall_Penalty proportional to the shortfall quantity for each fully-covered Supplier-Quarter pair.
6. THE Optimizer SHALL NOT apply any penalty when a Supplier's ordered Activity for a Quarter meets or exceeds its Quarterly_Minimum_Quota.
6a. WHERE a Quarter is only partially covered by the planning horizon, THE Optimizer SHALL exclude it from the Quota_Shortfall_Penalty and SHALL contribute zero cost for it, because part of that commercial quarter falls outside the plan — earlier weeks whose orders are already placed, or later weeks beyond the horizon where ordering continues — so compliance cannot be determined from the plan.
6b. THE Optimizer SHALL still report every partial Quarter, including the weeks covered, the weeks in the full Quarter, the Activity ordered within the horizon, and a pro-rated target scaled to the coverage, identified as a run-rate reference rather than a compliance result.
6c. THE Application SHALL label partial Quarters distinctly and SHALL NOT present them as quota breaches.
6d. THE Application SHALL notify the planner when the configured Reference_Week produces partial Quarters, and SHALL state that aligning the Reference_Week to the start of a Quarter yields fully-covered Quarters.
6e. THE Optimizer SHALL treat interior Quarters as fully covered, since planning weeks run continuously and only the first and last Quarters can be partial.
7. THE Optimizer SHALL set the default Quota_Shortfall_Penalty rate high enough that the solver treats quota shortfall as effectively unacceptable relative to penalty, overtime, and capacity costs.
8. THE Optimizer SHALL include the quota shortfall cost as a component of the objective function alongside the existing penalty, overtime, and capacity components.
9. THE Optimizer SHALL expose a weight for the quota shortfall cost component consistent with the existing `w_penalty` / `w_overtime` / `w_capacity` weighting scheme.
10. THE Optimizer SHALL expose the `Quarterly_Minimum_Quota`, the `Quota_Shortfall_Penalty` rate, and the `Quarter_Start_Month` as parameters editable from the front end.

### Requirement 7: Quota Status Display

**User Story:** As a production planner, I want to see quota consumption and remaining balance per supplier per quarter in the application, so that I can confirm compliance without opening SAP or the Master Planner.

#### Acceptance Criteria

1. THE Application SHALL display, for each Supplier and each Quarter in the horizon, the Quarterly_Minimum_Quota, the ordered Activity, and the remaining balance.
2. THE Application SHALL compute the remaining balance as `Quarterly_Minimum_Quota - ordered Activity` and SHALL display negative values where the quota is exceeded.
3. WHEN a Supplier-Quarter pair has a shortfall, THE Application SHALL visually distinguish that row from compliant rows.
4. THE Application SHALL display the total Quota_Shortfall_Penalty cost as a summary metric alongside the existing cost metrics.
5. THE Optimizer SHALL include the per-Supplier per-Quarter quota status in the exported workbook as a dedicated sheet.

### Requirement 8: Supplier Detail in Output Plan

**User Story:** As a production planner, I want the weekly plan export to show supplier allocation and activity, so that it can replace the manual Master Planner columns.

#### Acceptance Criteria

1. THE exported `Weekly_Plan` sheet SHALL include, per week, the generators allocated to Curium and the generators allocated to BWXT.
2. THE exported `Weekly_Plan` sheet SHALL include, per week, the Curium Activity in mCi, the BWXT Activity in mCi, and the total Sr-82 requirement in mCi.
3. THE exported `Weekly_Plan` sheet SHALL include, per week, a Supplier label indicating `Curium`, `BWXT`, `Curium / BWXT`, or blank for zero-production weeks, matching the Master Planner `Strontium Supplier` column convention.
4. THE exported `Weekly_Plan` sheet SHALL include, per week, the EU_Restricted_Customer demand.
5. THE Optimizer SHALL preserve all existing `Weekly_Plan` columns.

### Requirement 9: Onboarding Recommendation with Supplier Constraints

**User Story:** As a production planner, I want new customer onboarding recommendations to account for supplier constraints, so that a recommended start week is actually achievable.

#### Acceptance Criteria

1. THE Recommendation_Engine SHALL evaluate each candidate start week using the supplier-constrained optimizer.
2. THE Recommendation_Engine SHALL report the marginal quota shortfall cost for each candidate alongside the existing marginal penalty, overtime, and capacity costs.
3. WHEN a candidate start week is infeasible because of a supplier constraint, THE Recommendation_Engine SHALL exclude that candidate and SHALL report the reason.
4. WHEN a new customer is an EU_Restricted_Customer, THE Recommendation_Engine SHALL enforce the Curium-only restriction for that customer's demand in every candidate evaluation.
5. THE Recommendation_Engine SHALL accept the EU_Restricted_Customer indicator as part of each new site's definition.

## Out of Scope

- SAP integration for reconciling actual ordered quantities against planned quantities. Quota accounting is based on the optimizer's own plan.
- Purchase order generation or tracking (the Master Planner `PO#` column).
- Supplier lead times, order placement timing, and raw material inventory ageing or decay.
- Material provenance of held inventory — managed by Quality outside the tool (Requirement 5.5).
- Per-week minimum supplier order size — the business confirms actual ordering is consistently well above this floor and the planner-buyer monitors it manually, so it is not modelled.
- More than two suppliers.

## Resolved Decisions

| # | Question | Decision |
|---|---|---|
| Quarter definition | Calendar or fiscal quarters? | Configurable `Quarter_Start_Month`, editable in the front end, defaulting to January. Quarters are derived from the Reference_Week's calendar dates. See Requirement 6.1–6.3. |
| ROW / BWXT overlap | Is `row_cap = 2` the same rule as the BWXT restriction? | No. `row_cap` is a QC throughput cap; the BWXT rule is a raw material sourcing restriction. They are independent constraints that happen to affect the same customers. Both are modelled. |
| Parameter configurability | Should supplier parameters be hardcoded or editable? | All supplier parameters — surplus percentages, quotas, shortfall penalty rate, first-run allocation, quarter start month, availability — must be editable from the front end. See Requirement 1 and 6.10. |
| Quarterly quota value | Is 10,000 mCi the real quota? | The planner sets the quota per supplier per quarter on the configuration page. 10,000 mCi is only the default. See Requirement 6.10. |
| Shortfall penalty amount | What is the shortfall charge in USD? | The planner sets the `Quota_Shortfall_Penalty` rate (USD per mCi of shortfall) on the configuration page. The model computes shortfall cost as `shortfall_mCi × rate`. See Requirement 6.5 and 6.10. |
| Surplus percentage | Is there a "1%" margin beyond 5%/2%? | No. Only 5% (Curium) and 2% (BWXT). The "extra 1%" was a stale title, now removed from the Master Schedule. |
| Split-week first run | Fixed batch, and can Curium run twice? | The first run is always Curium at 15 generators (1586 mCi). A second Curium run occurs only in three-run weeks, in the order Curium, BWXT, Curium. See Requirement 4. |
| Inventory provenance | Must the tool track Curium vs BWXT held stock? | No. Provenance is tracked by Quality outside the tool. See Requirement 5.5. |
| Weekly minimum order | Is there a per-week minimum order size? | Yes, but ordering is consistently well above it and the planner-buyer monitors it manually. Not modelled. |
| Partial quarters | How should a quarter only partly inside the horizon be treated? | Excluded from the penalty (zero cost), but still reported with coverage and a pro-rated run-rate target, clearly labelled. Charging a full quota against a fragment invented a shortfall that dominated the objective. See Requirement 6a–6e. |
| QC generators | Does the `QC GEN` column schedule extra QC beyond the one-per-batch test? | No. Only one QC generator per batch (the test discard). The formula's per-batch term already accounts for it. |
| Three-run split pattern | Is Curium→BWXT→Curium the only pattern, or also Curium→BWXT→BWXT? | Curium→BWXT→Curium is the **only** allowed pattern for three-run weeks. See Requirement 4.6. |

## Open Questions

All open questions have been resolved. No outstanding items remain for this spec.
