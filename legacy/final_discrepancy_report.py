#!/usr/bin/env python3
"""
Final comprehensive discrepancy report.
"""

import pandas as pd
import numpy as np
from collections import Counter, defaultdict
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

# ── Master per-site data ─────────────────────────────────────────────────────
master_sites = []
for col in site_cols:
    demand_weeks = []
    for w in range(52):
        val = window[col].iloc[w]
        if pd.notna(val) and val == 1:
            demand_weeks.append(w + 1)
    if not demand_weeks:
        continue
    
    first_week = demand_weeks[0]
    if len(demand_weeks) >= 2:
        intervals = [demand_weeks[i+1] - demand_weeks[i] for i in range(len(demand_weeks)-1)]
        interval = Counter(intervals).most_common(1)[0][0]
        # Check if interval is consistent
        consistent = all(iv == interval for iv in intervals)
    else:
        interval = None
        consistent = True
    
    master_sites.append({
        'name': col,
        'weeks': demand_weeks,
        'first_week': first_week,
        'interval': interval,
        'num_demands': len(demand_weeks),
        'consistent': consistent,
    })

# ── AI per-site data ─────────────────────────────────────────────────────────
ai_input = pd.read_excel("sites_input_new.xlsx", sheet_name="Sheet1")
ai_input.columns = [str(c).strip() for c in ai_input.columns]
active = ai_input[ai_input['Active'].str.upper().str.strip() == 'Y'].copy()

ai_sites = []
for _, row in active.iterrows():
    ndw = int(row['Next_Demand_Week'])
    iv = int(row['Interval_Weeks'])
    country = str(row['Country']).strip().lower()
    weeks = []
    w = ndw
    while w <= 52:
        weeks.append(w)
        w += iv
    ai_sites.append({
        'site_id': str(row['Site_ID']).strip(),
        'ndw': ndw,
        'interval': iv,
        'country': country,
        'weeks': weeks,
        'num_demands': len(weeks),
    })

# ── Build weekly totals ──────────────────────────────────────────────────────
master_weekly = [0] * 53
for s in master_sites:
    for w in s['weeks']:
        master_weekly[w] += 1

ai_weekly = [0] * 53
for s in ai_sites:
    for w in s['weeks']:
        ai_weekly[w] += 1

# ── Pattern matching ─────────────────────────────────────────────────────────
# Group by (first_week, interval) pattern
master_by_pattern = defaultdict(list)
for s in master_sites:
    key = (s['first_week'], s['interval'])
    master_by_pattern[key].append(s)

ai_by_pattern = defaultdict(list)
for s in ai_sites:
    key = (s['ndw'], s['interval'])
    ai_by_pattern[key].append(s)

# ── Find sites in master NOT in AI ──────────────────────────────────────────
# These are patterns where master has more sites than AI
print("=" * 80)
print("DISCREPANCY REPORT: Master Schedule vs AI Input (sites_input_new.xlsx)")
print("=" * 80)

print(f"\n## HIGH-LEVEL SUMMARY")
print(f"Master sites with demand: {len(master_sites)}")
print(f"AI active sites: {len(active)}")
print(f"Site count gap: {len(master_sites) - len(active)} sites missing from AI")
print(f"Master total demand: {sum(s['num_demands'] for s in master_sites)}")
print(f"AI total demand: {sum(s['num_demands'] for s in ai_sites)}")
print(f"Demand gap: {sum(s['num_demands'] for s in master_sites) - sum(s['num_demands'] for s in ai_sites)} demand events missing from AI")

# ── ROOT CAUSE 1: Missing sites ─────────────────────────────────────────────
print(f"\n{'='*80}")
print(f"ROOT CAUSE 1: SITES IN MASTER BUT MISSING FROM AI INPUT")
print(f"{'='*80}")

# Find patterns where master count > AI count
missing_sites = []
for pat, m_sites in master_by_pattern.items():
    a_sites = ai_by_pattern.get(pat, [])
    excess = len(m_sites) - len(a_sites)
    if excess > 0:
        # These master sites don't have AI counterparts
        for s in m_sites[:excess]:  # Take the first 'excess' as unmatched
            missing_sites.append(s)

print(f"\nSites in master with no matching AI entry ({len(missing_sites)} sites):")
total_missing_demand = 0
for s in sorted(missing_sites, key=lambda x: x['first_week']):
    total_missing_demand += s['num_demands']
    iv_str = str(s['interval']) if s['interval'] else "one-time"
    print(f"  {s['name'][:70]}")
    print(f"    Pattern: first_week={s['first_week']}, interval={iv_str}, demands={s['num_demands']}")
    print(f"    Weeks: {s['weeks']}")

print(f"\nTotal missing demand: {total_missing_demand} events")

# ── ROOT CAUSE 2: Extra sites in AI ─────────────────────────────────────────
print(f"\n{'='*80}")
print(f"ROOT CAUSE 2: SITES IN AI INPUT BUT NOT IN MASTER")
print(f"{'='*80}")

extra_sites = []
for pat, a_sites in ai_by_pattern.items():
    m_sites = master_by_pattern.get(pat, [])
    excess = len(a_sites) - len(m_sites)
    if excess > 0:
        for s in a_sites[:excess]:
            extra_sites.append(s)

print(f"\nSites in AI with no matching master entry ({len(extra_sites)} sites):")
total_extra_demand = 0
for s in sorted(extra_sites, key=lambda x: x['ndw']):
    total_extra_demand += s['num_demands']
    print(f"  {s['site_id']} ({s['country']}): NDW={s['ndw']}, IV={s['interval']}, demands={s['num_demands']}")
    print(f"    Weeks: {s['weeks']}")

print(f"\nTotal extra demand: {total_extra_demand} events")

# ── ROOT CAUSE 3: Wrong parameters ──────────────────────────────────────────
print(f"\n{'='*80}")
print(f"ROOT CAUSE 3: SITES WITH INCONSISTENT INTERVALS IN MASTER")
print(f"{'='*80}")

inconsistent = [s for s in master_sites if not s['consistent']]
print(f"\nSites with non-uniform intervals ({len(inconsistent)} sites):")
for s in inconsistent:
    intervals = [s['weeks'][i+1] - s['weeks'][i] for i in range(len(s['weeks'])-1)]
    print(f"  {s['name'][:70]}")
    print(f"    Weeks: {s['weeks']}")
    print(f"    Intervals: {intervals}")
    print(f"    Most common interval: {s['interval']}")

# ── ROOT CAUSE 4: ROW country classification ────────────────────────────────
print(f"\n{'='*80}")
print(f"ROOT CAUSE 4: ROW COUNTRY CLASSIFICATION ISSUES")
print(f"{'='*80}")

row_demand_col = window['RoW Demand'].fillna(0).astype(int)
us_demand_col = window['US Demand'].fillna(0).astype(int)

print(f"\nMaster ROW demand: {row_demand_col.sum()}")
print(f"Master US demand: {us_demand_col.sum()}")

# AI ROW (only denmark, uk, netherlands, sweden are ROW in the optimizer)
row_set = {'denmark', 'uk', 'netherlands', 'sweden'}
ai_row = [s for s in ai_sites if s['country'] in row_set]
ai_non_row_intl = [s for s in ai_sites if s['country'] in {'switzerland', 'canada', 'europe'}]
ai_us = [s for s in ai_sites if s['country'] == 'usa']

print(f"\nAI ROW sites (dk/uk/nl/se): {len(ai_row)} sites, {sum(s['num_demands'] for s in ai_row)} demand")
print(f"AI non-ROW intl (ch/ca/eu): {len(ai_non_row_intl)} sites, {sum(s['num_demands'] for s in ai_non_row_intl)} demand")
print(f"AI US sites: {len(ai_us)} sites, {sum(s['num_demands'] for s in ai_us)} demand")

# The master's ROW column includes Canada, Switzerland, etc.
# But the optimizer's ROW only includes Denmark, UK, Netherlands, Sweden
# This means the ROW cap in the optimizer won't match the master's ROW tracking
print(f"\nIMPORTANT: The master's 'RoW Demand' column ({row_demand_col.sum()}) includes")
print(f"Canada ({sum(s['num_demands'] for s in ai_sites if s['country']=='canada')} demand),")
print(f"Switzerland ({sum(s['num_demands'] for s in ai_sites if s['country']=='switzerland')} demand),")
print(f"and other international sites.")
print(f"But the optimizer's ROW definition only includes: Denmark, UK, Netherlands, Sweden.")
print(f"This creates a ROW demand gap of {row_demand_col.sum() - sum(s['num_demands'] for s in ai_row)} events.")

# ── DEMAND RECONCILIATION ────────────────────────────────────────────────────
print(f"\n{'='*80}")
print(f"DEMAND RECONCILIATION")
print(f"{'='*80}")
print(f"\nMaster total demand:     {sum(s['num_demands'] for s in master_sites):>6}")
print(f"AI total demand:         {sum(s['num_demands'] for s in ai_sites):>6}")
print(f"                         ------")
print(f"Gap:                     {sum(s['num_demands'] for s in master_sites) - sum(s['num_demands'] for s in ai_sites):>6}")
print(f"")
print(f"Missing from AI:         +{total_missing_demand:>5} (sites in master, not in AI)")
print(f"Extra in AI:             -{total_extra_demand:>5} (sites in AI, not in master)")
print(f"Net explained gap:       {total_missing_demand - total_extra_demand:>6}")
print(f"Actual gap:              {sum(s['num_demands'] for s in master_sites) - sum(s['num_demands'] for s in ai_sites):>6}")

# ── WEEKLY COMPARISON TABLE ──────────────────────────────────────────────────
print(f"\n{'='*80}")
print(f"WEEKLY DEMAND COMPARISON")
print(f"{'='*80}")
print(f"{'Week':>4} {'Master':>7} {'AI':>5} {'Diff':>5} {'Status'}")
print("-" * 35)
weeks_over = 0
weeks_under = 0
weeks_match = 0
for w in range(1, 53):
    m = master_weekly[w]
    a = ai_weekly[w]
    d = a - m
    if d > 0:
        status = f"AI OVER by {d}"
        weeks_over += 1
    elif d < 0:
        status = f"AI UNDER by {abs(d)}"
        weeks_under += 1
    else:
        status = "MATCH"
        weeks_match += 1
    print(f"{w:>4} {m:>7} {a:>5} {d:>+5} {status}")

print(f"\nWeeks matching: {weeks_match}")
print(f"Weeks AI over: {weeks_over}")
print(f"Weeks AI under: {weeks_under}")
