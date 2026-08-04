#!/usr/bin/env python3
"""
Find the missing ROW sites by looking at weeks where our identification
doesn't match the master's ROW column.
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

row_demand = window['RoW Demand'].fillna(0).astype(int).values

# Known international sites (from previous analysis)
known_intl = set()
intl_keywords = {
    'canada': ['can (', 'can,', ', can', 'canada', 'montreal', 'montréal', 'toronto', 'ottawa', 'edmonton', 'quebec', 'sudbury', ', ab,', ', on,', ', ont.'],
    'switzerland': ['switzerland', 'swz', 'lausanne', 'genève', 'geneva', 'lucerne', 'bern,', 'fribourg', 'meyrin', 'kantonsspital', 'hirslanden', 'hôpitaux', 'hopital de la tour', 'genolier', 'chuv'],
    'uk': ['uclh'],
    'netherlands': ['netherlands', 'arnhem', 'ziekenhuis'],
    'denmark': ['copenhagen', 'herlev'],
}

for col in site_cols:
    col_lower = str(col).lower()
    for country, keywords in intl_keywords.items():
        if any(kw in col_lower for kw in keywords):
            known_intl.add(col)
            break

# For mismatch weeks, find which non-known-intl sites have demand
mismatch_weeks = [3, 4, 11, 13, 18, 23, 25, 32, 33, 39, 43, 46]

print("=== Investigating mismatch weeks ===")
print("Looking for sites that have demand on mismatch weeks but are NOT in our known intl set\n")

# For each mismatch week, the ROW column is 1 more than our intl count
# So there's 1 extra ROW site per mismatch week that we're missing
# These could be the same site(s) appearing across multiple weeks

suspect_sites = {}
for w in mismatch_weeks:
    w_idx = w - 1  # 0-indexed
    for col in site_cols:
        if col in known_intl:
            continue
        val = window[col].iloc[w_idx]
        if pd.notna(val) and val == 1:
            if col not in suspect_sites:
                suspect_sites[col] = []
            suspect_sites[col].append(w)

# A site that appears on ALL or MOST mismatch weeks is likely the missing ROW site
print(f"Sites with demand on mismatch weeks (not in known intl): {len(suspect_sites)}")
print(f"\nSites appearing on 3+ mismatch weeks:")
for col, weeks in sorted(suspect_sites.items(), key=lambda x: -len(x[1])):
    if len(weeks) >= 3:
        # Get full demand pattern
        all_weeks = []
        for w_idx in range(52):
            val = window[col].iloc[w_idx]
            if pd.notna(val) and val == 1:
                all_weeks.append(w_idx + 1)
        print(f"  {col[:70]}")
        print(f"    Mismatch weeks: {weeks} ({len(weeks)} hits)")
        print(f"    All demand weeks: {all_weeks}")

# Let's also check the WEEKS SUMMARY sheet for ROW info
print("\n\n=== Checking WEEKS SUMMARY sheet ===")
try:
    ws = pd.read_excel(master_path, sheet_name="WEEKS SUMMARY", header=None)
    print(f"Shape: {ws.shape}")
    # Print first few rows
    for i in range(min(10, len(ws))):
        print(f"Row {i}: {list(ws.iloc[i, :10].values)}")
except Exception as e:
    print(f"Error: {e}")

# Let's also check if there's a Denmark site we're missing
# The master has 3 Denmark sites in AI input but only 1 in our master identification
print("\n\n=== Looking for Denmark sites in master ===")
for col in site_cols:
    col_lower = str(col).lower()
    if any(x in col_lower for x in ['dk', 'denmark', 'copenhagen', 'aarhus', 'odense', 'herlev', 'rigshospitalet', 'gentofte']):
        demand_weeks = []
        for w in range(52):
            val = window[col].iloc[w]
            if pd.notna(val) and val == 1:
                demand_weeks.append(w + 1)
        if demand_weeks:
            print(f"  {col[:70]}: {demand_weeks}")
