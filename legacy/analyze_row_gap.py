#!/usr/bin/env python3
"""
Analyze the ROW country classification gap.
Master ROW demand = 187, AI ROW demand = 51.
Which sites in the master contribute to ROW demand?
"""

import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings("ignore")

master_path = "Ruby-Fill- Master Schedule (1).xlsx"
sched = pd.read_excel(master_path, sheet_name="Schedule", header=1)

mfg_col = 'MFG Date \n(Holidays)'
dates = pd.to_datetime(sched[mfg_col], errors='coerce')

target_start = pd.Timestamp("2026-02-02")
target_end = pd.Timestamp("2027-01-25")
mask = (dates >= target_start) & (dates <= target_end)
window = sched[mask].copy().reset_index(drop=True)

# Get US Demand and RoW Demand columns per week
us_demand = window['US Demand'].fillna(0).astype(int).tolist()
row_demand = window['RoW Demand'].fillna(0).astype(int).tolist()
total_commercial = window['Total Commercial'].fillna(0).astype(int).tolist()

print("Week-by-week US vs ROW demand from master:")
print(f"{'Week':>4} {'US':>4} {'ROW':>4} {'Total':>5} {'TC_col':>6}")
for w in range(52):
    tc = total_commercial[w]
    print(f"{w+1:>4} {us_demand[w]:>4} {row_demand[w]:>4} {us_demand[w]+row_demand[w]:>5} {tc:>6}")

print(f"\nUS total: {sum(us_demand)}")
print(f"ROW total: {sum(row_demand)}")
print(f"Grand total: {sum(us_demand) + sum(row_demand)}")

# Now identify which site columns are ROW sites
# ROW sites typically have country indicators in their names
all_cols = list(sched.columns)
site_cols = all_cols[15:233]  # from previous analysis

# Check for international indicators
row_keywords = ['switzerland', 'swz', 'uk', 'london', 'denmark', 'netherlands',
                'sweden', 'canada', 'can', 'ont.', 'quebec', 'qc', 'europe',
                'bern', 'lucerne', 'genève', 'geneva', 'lausanne', 'fribourg',
                'meyrin', 'aarau', 'baden', 'sudbury', 'ont', 'hirslanden',
                'kantonsspital', 'hôpitaux', 'hopital', 'uclh', 'chuv']

print("\n=== Sites with ROW indicators in name ===")
row_site_cols = []
for col in site_cols:
    col_lower = str(col).lower()
    for kw in row_keywords:
        if kw in col_lower:
            # Check if this site has demand in our window
            demand_weeks = []
            for w in range(52):
                val = window[col].iloc[w]
                if pd.notna(val) and val == 1:
                    demand_weeks.append(w + 1)
            if demand_weeks:
                row_site_cols.append((col, demand_weeks, kw))
            break

print(f"ROW sites found: {len(row_site_cols)}")
total_row_from_sites = 0
for col, weeks, kw in row_site_cols:
    total_row_from_sites += len(weeks)
    print(f"  {col[:70]}")
    print(f"    Matched keyword: '{kw}', Demand weeks: {weeks} ({len(weeks)} events)")

print(f"\nTotal ROW demand from identified sites: {total_row_from_sites}")
print(f"Master ROW demand column total: {sum(row_demand)}")
print(f"Difference: {total_row_from_sites - sum(row_demand)}")

# Now check AI input ROW classification
ai_input = pd.read_excel("sites_input_new.xlsx", sheet_name="Sheet1")
ai_input.columns = [str(c).strip() for c in ai_input.columns]
active = ai_input[ai_input['Active'].str.upper().str.strip() == 'Y'].copy()

# ROW countries in AI
row_countries = {'denmark', 'uk', 'netherlands', 'sweden'}
ai_row = active[active['Country'].str.lower().str.strip().isin(row_countries)]
print(f"\n=== AI ROW sites (denmark, uk, netherlands, sweden) ===")
print(f"Count: {len(ai_row)}")
for _, r in ai_row.iterrows():
    ndw = int(r['Next_Demand_Week'])
    iv = int(r['Interval_Weeks'])
    weeks = []
    w = ndw
    while w <= 52:
        weeks.append(w)
        w += iv
    print(f"  {r['Site_ID']} ({r['Country']}): NDW={ndw}, IV={iv}, demands={len(weeks)}")

# Also check Switzerland, Canada, Europe sites in AI
other_intl = active[active['Country'].str.lower().str.strip().isin(['switzerland', 'canada', 'europe'])]
print(f"\n=== AI other international sites (switzerland, canada, europe) ===")
print(f"Count: {len(other_intl)}")
for _, r in other_intl.iterrows():
    ndw = int(r['Next_Demand_Week'])
    iv = int(r['Interval_Weeks'])
    weeks = []
    w = ndw
    while w <= 52:
        weeks.append(w)
        w += iv
    print(f"  {r['Site_ID']} ({r['Country']}): NDW={ndw}, IV={iv}, demands={len(weeks)}")
