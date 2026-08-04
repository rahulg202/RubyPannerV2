"""Run the onboarding recommendation engine with sites_input_new.xlsx.

Demonstrates the full flow:
1. Run onboarding recommendation (top 5 per objective)
2. Pick the best candidate start week (lowest penalty)
3. Generate the full 52-week plan with original sites + new onboarded sites
   (each new site's first replacement demand is staggered based on when
   the generator was produced by the onboarding LP)
"""

import os
import pandas as pd
from integrated_cost_optimizer import (
    IntegratedParams,
    read_sites,
    clean_sites,
    build_weekly_demand,
    build_weekly_row_demand,
    solve_plan_integrated,
    export_excel,
    _norm_cols,
)
from onboarding_recommendation import (
    validate_onboarding_inputs,
    evaluate_all_candidates,
    rank_and_select_top5,
    build_horizon_plan_df,
    compute_batch_metrics,
    format_cost_thousands,
    export_recommendation_excel,
)

# --- Configuration ---
SITES_FILE = "sites_input_new.xlsx"
SITES_SHEET = "Sheet1"
RESULTS_DIR = "results"

TOTAL_NEW_GENERATORS = 180
START_WEEK = 1
END_WEEK = 12
NEW_SITE_INTERVAL = 7
NEW_SITE_COUNTRY = "usa"

params = IntegratedParams()
os.makedirs(RESULTS_DIR, exist_ok=True)

# --- Step 1: Run onboarding recommendation ---
errors = validate_onboarding_inputs(TOTAL_NEW_GENERATORS, START_WEEK, END_WEEK)
if errors:
    for e in errors:
        print(f"  ERROR: {e}")
    raise SystemExit(1)

print(f"=== Step 1: Onboarding Recommendation ===")
print(f"  {TOTAL_NEW_GENERATORS} new generators, weeks {START_WEEK}-{END_WEEK}")

results = evaluate_all_candidates(TOTAL_NEW_GENERATORS, START_WEEK, END_WEEK, params)
top5 = rank_and_select_top5(results)

rec_xlsx = export_recommendation_excel(top5, START_WEEK, END_WEEK, params)
rec_path = os.path.join(RESULTS_DIR, "onboarding_recommendation.xlsx")
with open(rec_path, "wb") as f:
    f.write(rec_xlsx)
print(f"  Recommendation saved: {rec_path}")

for obj_key in ["penalty", "overtime", "capacity"]:
    options = top5.get(obj_key, [])
    print(f"\n  Top {len(options)} by {obj_key.upper()}:")
    for idx, opt in enumerate(options):
        plan = build_horizon_plan_df(opt, START_WEEK, END_WEEK, params)
        metrics = compute_batch_metrics(plan, params)
        print(f"    #{idx+1} Week {opt['candidate_start_week']}: "
              f"{format_cost_thousands(opt['cost'])} | "
              f"1b={metrics['weeks_1_batch']} 2b={metrics['weeks_2_batch']} 3b={metrics['weeks_3_batch']}")

# --- Step 2: Pick best candidate and generate full 52-week plan ---
best = top5["penalty"][0]
selected_week = best["candidate_start_week"]
weekly_prod = best["weekly_production"]

print(f"\n=== Step 2: Full 52-Week Plan (onboarding from week {selected_week}) ===")

# Load original sites
raw_df = read_sites(SITES_FILE, sites_sheet=SITES_SHEET)
raw_df = _norm_cols(raw_df)

# Create new site rows staggered by the LP's weekly production schedule.
# Each generator produced in week W becomes a site whose first replacement
# demand is at W + interval (the initial generator was just produced).
new_rows = []
site_counter = 0
for week_idx, prod in enumerate(weekly_prod):
    prod_week = selected_week + week_idx
    n_units = int(round(prod))
    for _ in range(n_units):
        site_counter += 1
        first_replacement = prod_week + NEW_SITE_INTERVAL
        if first_replacement <= params.horizon_weeks:
            new_rows.append({
                "site_id": f"NEW_{site_counter:04d}",
                "active": "Y",
                "next_demand_week": first_replacement,
                "interval_weeks": NEW_SITE_INTERVAL,
                "country": NEW_SITE_COUNTRY,
            })

new_df = pd.DataFrame(new_rows)
combined_df = pd.concat([raw_df, new_df], ignore_index=True)
active_df, issues_df = clean_sites(combined_df, params)

print(f"  Original sites: {len(raw_df)}, New sites added to plan: {len(new_df)}, "
      f"Combined active: {len(active_df)}")

demand = build_weekly_demand(active_df, params)
row_demand = build_weekly_row_demand(active_df, params)

plan_df, summary = solve_plan_integrated(
    demand=demand,
    shutdown_weeks=[],
    partial_shutdown_weeks=[],
    row_demand=row_demand,
    row_cap=params.row_cap,
    params=params,
)

full_path = os.path.join(RESULTS_DIR, f"full_plan_onboarding_week{selected_week}.xlsx")
export_excel(full_path, plan_df, active_df, issues_df, params, summary)

print(f"\n  Total composite cost: ${summary['total_composite_cost']:,.0f}")
print(f"  Penalty cost:         ${summary['total_penalty_cost']:,.0f}")
print(f"  Overtime cost:        ${summary['total_overtime_cost']:,.0f}")
print(f"  Capacity cost:        ${summary['total_capacity_cost']:,.0f}")
print(f"  Overtime weeks:       {summary['overtime_weeks']}")
print(f"  Full plan saved: {full_path}")
print(f"\nAll results in: {RESULTS_DIR}/")
