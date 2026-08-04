#!/usr/bin/env python3
"""
Precisely identify which master sites are ROW by checking if their demand
contributes to the RoW Demand column (not the US Demand column).
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

all_cols = list(sched.columns)
site_cols = all_cols[15:233]

# Get US and ROW demand per week from master
us_demand = window['US Demand'].fillna(0).astype(int).values
row_demand = window['RoW Demand'].fillna(0).astype(int).values

# For each site, determine if it's US or ROW by checking:
# Sum of site's 1s per week should match either US or ROW pattern
# Actually, let's just check which sites are clearly international by name

# True international sites (not false positives from keyword matching)
intl_sites = {}
for col in site_cols:
    col_str = str(col)
    col_lower = col_str.lower()
    
    # Check for clear international indicators
    is_intl = False
    country = None
    
    # Canada
    if 'can' in col_lower and (',' in col_str) and any(x in col_lower for x in ['can (', 'can,', ', can', 'canada', 'montreal', 'toronto', 'ottawa', 'edmonton', 'quebec', 'sudbury']):
        is_intl = True
        country = 'canada'
    # Check for Canadian province codes
    elif any(x in col_lower for x in [', on,', ', ont.', ', ab,', 'quebec', 'montréal']):
        is_intl = True
        country = 'canada'
    
    # Switzerland
    if any(x in col_lower for x in ['switzerland', 'swz', 'lausanne', 'genève', 'geneva', 'lucerne', 'bern,', 'fribourg', 'meyrin', 'kantonsspital', 'hirslanden', 'hôpitaux', 'hopital de la tour', 'genolier', 'chuv']):
        is_intl = True
        country = 'switzerland'
    
    # UK
    if 'uclh' in col_lower or ('uk' in col_lower and 'london' in col_lower):
        is_intl = True
        country = 'uk'
    
    # Netherlands
    if any(x in col_lower for x in ['netherlands', 'arnhem', 'ziekenhuis']):
        is_intl = True
        country = 'netherlands'
    
    # Denmark
    if 'denmark' in col_lower or 'copenhagen' in col_lower or 'rigshospitalet' in col_lower:
        is_intl = True
        country = 'denmark'
    
    if is_intl:
        demand_weeks = []
        for w in range(52):
            val = window[col].iloc[w]
            if pd.notna(val) and val == 1:
                demand_weeks.append(w + 1)
        if demand_weeks:
            intl_sites[col] = {'country': country, 'weeks': demand_weeks, 'count': len(demand_weeks)}

print("=== Master International Sites (precise matching) ===")
total_intl_demand = 0
by_country = {}
for col, info in sorted(intl_sites.items(), key=lambda x: x[1]['country']):
    total_intl_demand += info['count']
    country = info['country']
    if country not in by_country:
        by_country[country] = []
    by_country[country].append((col, info))

for country in sorted(by_country.keys()):
    sites = by_country[country]
    country_demand = sum(info['count'] for _, info in sites)
    print(f"\n{country.upper()} ({len(sites)} sites, {country_demand} demand events):")
    for col, info in sites:
        print(f"  {col[:70]}")
        print(f"    Weeks: {info['weeks']} ({info['count']} events)")

print(f"\nTotal international demand from sites: {total_intl_demand}")
print(f"Master ROW demand column: {sum(row_demand)}")

# Now check: the master ROW column = 187, but we need to see if Canada/Switzerland
# are counted as ROW or not
# Let's verify by summing site demands per week and comparing to US/ROW columns

# Sum all international site demands per week
intl_weekly = [0] * 52
for col, info in intl_sites.items():
    for w in info['weeks']:
        intl_weekly[w-1] += 1

# Sum all site demands per week (all sites)
all_site_weekly = [0] * 52
for col in site_cols:
    for w in range(52):
        val = window[col].iloc[w]
        if pd.notna(val) and val == 1:
            all_site_weekly[w] += 1

# US sites = all - international
us_site_weekly = [all_site_weekly[w] - intl_weekly[w] for w in range(52)]

print(f"\n=== Weekly verification ===")
print(f"{'Week':>4} {'All':>4} {'US_sites':>8} {'Intl_sites':>10} {'US_col':>6} {'ROW_col':>7}")
mismatch_weeks = []
for w in range(52):
    all_s = all_site_weekly[w]
    us_s = us_site_weekly[w]
    intl_s = intl_weekly[w]
    us_c = us_demand[w]
    row_c = row_demand[w]
    match = "✓" if us_s == us_c and intl_s == row_c else "✗"
    if match == "✗":
        mismatch_weeks.append(w+1)
    print(f"{w+1:>4} {all_s:>4} {us_s:>8} {intl_s:>10} {us_c:>6} {row_c:>7}  {match}")

if mismatch_weeks:
    print(f"\nMismatch weeks: {mismatch_weeks}")
    print("This means our international site identification is incomplete or incorrect")
else:
    print("\nAll weeks match - international site identification is correct")
