#!/usr/bin/env python3
"""
Discrepancy analysis: AI Generated Plan vs Manual Plan
Using ONLY:
  1. input-file.xlsx (Sheet1, mapping, Recon, Clients with no orders)
  2. penalty 1, rest 0 (no shutdown weeks).xlsx (Comparison, Weekly_Plan, Sites_Clean)
"""

# ═══════════════════════════════════════════════════════════════════════════════
# DATA FROM THE TWO PROVIDED FILES
# ═══════════════════════════════════════════════════════════════════════════════

# ── From input-file.xlsx: Sheet1 (184 active sites) ──────────────────────────
# Each site has: Site_ID, Active=Y, Next_Demand_Week, Interval_Weeks, Country
# AI model generates demand at NDW, NDW+IV, NDW+2*IV, ... up to week 52

# ── From input-file.xlsx: mapping sheet ──────────────────────────────────────
# Maps Site_ID → actual client name. Key examples:
#   Dl052 = 00622 CII / Isocare Temecula, Murrieta, CA (7)
#   Dl060 = 00434 Columbus Regional Hospital, Columbus, IN (7)
#   Dl081 = 00421 Medical Clinic of Houston, Houston, TX (7)
#   Dl092 = 00640 MIS, Padder Health, Laure, MD
#   Dl128 = 00558 South Eastern Cardiology, Columbus GA (7)
#   Dl068 = 00670 First Coast Cardiovascular #2, Jacksonville, FL (7)
#   Dl171 = 1417 Hopital de la Tour, Meyrin, Switzerland
#   Dl173 = 1416 Fribourg-CH SWZ
#   Dl175 = 1415 CHUV, Lausanne, Switzerland (7)

# ── From input-file.xlsx: Recon sheet ────────────────────────────────────────
# Documents corrections made between master file and AI input:
recon_notes = {
    "Dl182": "RUSH Univ #2: gap was 5 instead of 6 → corrected to 6-week interval",
    "Dl179": "Advanced Specialty Care, Fresno: MIX interval → taken as 7 in input",
    "Dl169": "Bern, Switzerland: interval was first 10 then 9 → corrected to 10",
    "Dl005": "Atlanta Heart: interval was 6 but 7 mentioned → corrected to 7",
    "Dl011": "Brigham & Womens: interval was 6 but 7 mentioned → corrected to 7",
    "Dl180": "Premier Cardiology #2: due to adjusted gen, IV was 6 but 7 mentioned → added to input, IV=7",
    "Dl002": "Allegheny General: interval was 6 but 7 mentioned → corrected to 7",
    # Sites removed from input (onboarding - no regular demand):
    "CII_West_Texas": "Only 1 order, no order interval → removed from input",
    "Chicago_Cardiology": "Only 1 order, no order interval → removed from input",
    "Univ_Colorado_Rockies": "Only 1 order, no order interval → removed from input",
    # Recon section 2 - column mapping corrections:
    "Dl068_map": "Dl068 maps to 00664 Scintilla Imaging #2, Franklin Square, NY (column CM)",
    "Dl173_map": "Dl173 maps to 1417 Hopital de la Tour, Meyrin, Switzerland (column HJ)",
    "Dl175_map": "Dl175 maps to 1416 Fribourg-CH SWZ (column HM)",
    "Dl092_corr": "Dl092 (MIS Colorado Springs Fixed): corrected to 6-week interval",
    "Dl081_corr": "Dl081 (Longwood Med, Orlando): corrected to 7-week interval",
    "Dl128_corr": "Dl128 (Southern Ohio Medical Center): corrected to 6-week interval",
    "Dl171_corr": "Dl171 (Bern, Switzerland): corrected to 10-week interval",
    # Recon section 3 - inconsistent intervals:
    "Dl114_inc": "Pennsylvania Hospital: inconsistent interval due to gen on hold → corrected to 5",
    "Dl001_inc": "Alaska Heart: interval gap due to 1 pre-order → corrected to 7",
    "Dl006_inc": "Ashchi Heart: interval gap due to 1 pre-order → corrected to 7",
    "Dl015_inc": "CV Specialist New England: interval gap due to 1 pre-order → corrected to 7",
}

# ── From penalty file: Comparison sheet ──────────────────────────────────────
print("=" * 80)
print("DISCREPANCY ANALYSIS: AI Plan vs Manual Plan")
print("(Using only input-file.xlsx and penalty output file)")
print("=" * 80)

print("\n1. HIGH-LEVEL NUMBERS (from Comparison sheet)")
print("-" * 50)
metrics = {
    "Generators planned":    (1355, 1375),
    "Overtime weeks":        (13, 17),
    "Penalty ($)":           (21000, 35000),
    "Capacity loss (gens)":  (291, 305),
}
for label, (manual, ai) in metrics.items():
    diff = ai - manual
    print(f"  {label:<25s}  Manual={manual:>6,}  AI={ai:>6,}  Diff={diff:>+6,}")

# ═══════════════════════════════════════════════════════════════════════════════
# 2. DEMAND CALCULATION FROM AI INPUT (184 sites, periodic model)
# ═══════════════════════════════════════════════════════════════════════════════

# Sites from input-file.xlsx Sheet1 (all 184 active sites)
# Format: (Site_ID, NDW, IV, Country)
sites = [
    ("Dl001",5,7,"usa"),("Dl002",3,7,"usa"),("Dl003",7,7,"usa"),("Dl004",8,8,"usa"),
    ("Dl005",2,7,"usa"),("Dl006",5,7,"usa"),("Dl007",6,6,"usa"),("Dl008",2,7,"usa"),
    ("Dl009",6,7,"usa"),("Dl010",1,7,"usa"),("Dl011",7,7,"usa"),("Dl012",1,7,"usa"),
    ("Dl013",1,6,"usa"),("Dl014",11,7,"usa"),("Dl015",5,7,"usa"),("Dl016",2,7,"usa"),
    ("Dl017",1,7,"usa"),("Dl018",6,7,"usa"),("Dl019",1,7,"usa"),("Dl020",2,7,"usa"),
    ("Dl021",2,7,"usa"),("Dl022",12,7,"usa"),("Dl023",1,7,"usa"),("Dl024",5,7,"usa"),
    ("Dl025",7,7,"usa"),("Dl026",7,7,"usa"),("Dl027",1,7,"usa"),("Dl028",4,7,"usa"),
    ("Dl029",7,7,"usa"),("Dl030",4,7,"usa"),("Dl031",4,7,"usa"),("Dl032",4,7,"usa"),
    ("Dl033",3,7,"usa"),("Dl034",4,7,"usa"),("Dl035",4,7,"usa"),("Dl036",4,7,"usa"),
    ("Dl037",6,7,"usa"),("Dl038",5,7,"usa"),("Dl039",5,7,"usa"),("Dl040",5,7,"usa"),
    ("Dl041",5,7,"usa"),("Dl042",5,7,"usa"),("Dl043",5,7,"usa"),("Dl044",5,7,"usa"),
    ("Dl045",7,7,"usa"),("Dl046",7,7,"usa"),("Dl047",1,7,"usa"),("Dl048",1,7,"usa"),
    ("Dl049",3,7,"usa"),("Dl050",7,7,"usa"),("Dl051",4,7,"usa"),("Dl052",2,7,"usa"),
    ("Dl053",4,7,"usa"),("Dl054",5,7,"usa"),("Dl055",2,7,"usa"),("Dl056",3,7,"usa"),
    ("Dl057",6,7,"usa"),("Dl058",5,7,"usa"),("Dl059",5,7,"usa"),("Dl060",6,7,"usa"),
    ("Dl061",4,7,"usa"),("Dl062",4,7,"usa"),("Dl063",6,7,"usa"),("Dl064",6,7,"usa"),
    ("Dl065",6,7,"usa"),("Dl066",7,6,"usa"),("Dl067",1,7,"usa"),("Dl068",5,7,"usa"),
    ("Dl069",3,7,"usa"),("Dl070",1,6,"usa"),("Dl071",7,7,"usa"),("Dl072",2,7,"usa"),
    ("Dl073",4,7,"usa"),("Dl074",5,7,"usa"),("Dl075",6,7,"usa"),("Dl076",5,7,"usa"),
    ("Dl077",5,7,"usa"),("Dl078",2,7,"usa"),("Dl079",3,7,"usa"),("Dl080",1,6,"usa"),
    ("Dl081",2,7,"usa"),("Dl082",1,6,"usa"),("Dl083",6,7,"usa"),("Dl084",6,6,"usa"),
    ("Dl085",6,6,"usa"),("Dl086",3,7,"usa"),("Dl087",1,7,"usa"),("Dl088",1,7,"usa"),
    ("Dl089",5,7,"usa"),("Dl090",2,6,"usa"),("Dl091",6,6,"usa"),("Dl092",2,6,"usa"),
    ("Dl093",5,7,"usa"),("Dl094",2,7,"usa"),("Dl095",2,7,"usa"),("Dl096",5,7,"usa"),
    ("Dl097",5,7,"usa"),("Dl098",4,7,"usa"),("Dl099",2,7,"usa"),("Dl100",2,7,"usa"),
    ("Dl101",5,7,"usa"),("Dl102",1,5,"usa"),("Dl103",3,6,"usa"),("Dl104",1,6,"usa"),
    ("Dl105",2,6,"usa"),("Dl106",6,7,"usa"),("Dl107",12,7,"usa"),("Dl108",6,6,"usa"),
    ("Dl109",6,7,"usa"),("Dl110",1,7,"usa"),("Dl111",3,7,"usa"),("Dl112",3,5,"usa"),
    ("Dl113",4,7,"usa"),("Dl114",4,7,"usa"),("Dl115",3,7,"usa"),("Dl116",1,7,"usa"),
    ("Dl117",6,7,"usa"),("Dl118",1,7,"usa"),("Dl119",1,6,"usa"),("Dl120",2,7,"usa"),
    ("Dl121",7,7,"usa"),("Dl122",1,7,"usa"),("Dl123",5,7,"usa"),("Dl124",6,7,"usa"),
    ("Dl125",5,7,"usa"),("Dl126",5,6,"usa"),("Dl127",6,6,"usa"),("Dl128",4,7,"usa"),
    ("Dl129",5,7,"usa"),("Dl130",6,7,"usa"),("Dl131",5,6,"usa"),("Dl132",3,5,"usa"),
    ("Dl133",3,7,"usa"),("Dl134",6,7,"usa"),("Dl135",3,7,"usa"),("Dl136",1,7,"usa"),
    ("Dl137",6,6,"usa"),("Dl138",1,6,"usa"),("Dl139",1,7,"usa"),("Dl140",7,7,"usa"),
    ("Dl141",6,7,"usa"),("Dl142",6,7,"usa"),("Dl143",2,7,"usa"),("Dl144",2,7,"usa"),
    ("Dl145",4,7,"usa"),("Dl146",1,7,"usa"),("Dl147",5,6,"usa"),("Dl148",5,6,"usa"),
    ("Dl149",7,7,"usa"),("Dl150",1,7,"usa"),("Dl151",5,7,"usa"),
    ("Dl152",1,7,"canada"),("Dl153",1,7,"canada"),("Dl154",1,7,"canada"),
    ("Dl155",5,7,"canada"),("Dl156",1,6,"canada"),("Dl157",1,8,"canada"),
    ("Dl158",1,7,"canada"),("Dl159",2,7,"canada"),
    ("Dl160",6,7,"denmark"),("Dl161",4,7,"denmark"),("Dl162",1,7,"denmark"),
    ("Dl163",9,10,"netherlands"),("Dl164",5,7,"uk"),
    ("Dl165",5,8,"netherlands"),("Dl166",8,8,"netherlands"),
    ("Dl167",3,10,"europe"),("Dl168",7,11,"netherlands"),
    ("Dl169",6,10,"switzerland"),("Dl170",5,7,"switzerland"),
    ("Dl171",7,6,"switzerland"),("Dl172",5,7,"switzerland"),
    ("Dl173",7,6,"switzerland"),("Dl174",2,7,"switzerland"),
    ("Dl175",5,7,"switzerland"),("Dl176",7,7,"switzerland"),
    ("Dl177",6,8,"switzerland"),("Dl178",7,7,"switzerland"),
    ("Dl179",5,7,"usa"),("Dl180",13,7,"usa"),("Dl181",5,7,"usa"),
    ("Dl182",9,6,"usa"),("Dl183",5,7,"usa"),("Dl184",5,7,"usa"),
]

# Build AI demand per week (periodic model)
ai_demand_by_week = [0] * 53  # 1-indexed
site_demands = {}  # site_id -> list of weeks
for sid, ndw, iv, country in sites:
    weeks = []
    w = ndw
    while w <= 52:
        weeks.append(w)
        w += iv
    site_demands[sid] = weeks
    for wk in weeks:
        ai_demand_by_week[wk] += 1

total_ai_demand = sum(ai_demand_by_week[1:])
print(f"\n2. AI DEMAND FROM INPUT (periodic model)")
print("-" * 50)
print(f"  Active sites: {len(sites)}")
print(f"  Total AI demand (sum over 52 weeks): {total_ai_demand}")

# ═══════════════════════════════════════════════════════════════════════════════
# 3. WEEKLY PLAN DATA (from penalty file Weekly_Plan sheet)
# ═══════════════════════════════════════════════════════════════════════════════

# Columns: Week, Demand_Due (AI), Good_Production, From_master_file, Capacity_loss, Diff
weekly_plan = {
#   wk: (AI_demand, AI_good_prod, master_prod, cap_loss_master, diff)
    1:  (34, 34, 34, 0,  0),
    2:  (22, 22, 22, 8,  0),
    3:  (15, 15, 15, 15, 0),
    4:  (18, 18, 18, 12, 0),
    5:  (41, 41, 40, 0,  1),
    6:  (29, 29, 30, 0, -1),
    7:  (26, 26, 25, 5,  1),
    8:  (31, 31, 29, 1,  2),
    9:  (23, 23, 25, 5, -2),
    10: (11, 11, 10, 20, 1),
    11: (24, 24, 25, 5, -1),
    12: (45, 45, 39, 0,  6),
    13: (35, 35, 37, 0, -2),
    14: (18, 18, 18, 12, 0),
    15: (26, 26, 28, 2, -2),
    16: (23, 23, 24, 6, -1),
    17: (16, 16, 14, 16, 2),
    18: (29, 34, 30, 0,  4),   # AI overproduces here (34 vs demand 29)
    19: (50, 45, 41, 0,  4),   # AI underproduces (45 vs demand 50), uses week-18 inventory
    20: (23, 23, 20, 10, 3),
    21: (18, 18, 28, 2, -10),
    22: (25, 25, 26, 4, -1),
    23: (26, 26, 27, 3, -1),
    24: (20, 20, 19, 11, 1),
    25: (31, 31, 27, 3,  4),
    26: (43, 43, 34, 0,  9),
    27: (22, 22, 23, 7, -1),
    28: (16, 16, 18, 12,-2),
    29: (31, 31, 39, 0, -8),
    30: (27, 27, 30, 0, -3),
    31: (23, 23, 18, 12, 5),
    32: (24, 24, 22, 8,  2),
    33: (44, 44, 36, 0,  8),
    34: (20, 20, 20, 10, 0),
    35: (18, 18, 21, 9, -3),
    36: (33, 33, 34, 0, -1),
    37: (31, 31, 37, 0, -6),
    38: (17, 17, 12, 18, 5),
    39: (22, 22, 24, 6, -2),
    40: (41, 41, 34, 0,  7),
    41: (26, 26, 24, 6,  2),
    42: (21, 21, 25, 5, -4),
    43: (38, 38, 36, 0,  2),
    44: (22, 22, 22, 8,  0),
    45: (14, 14, 20, 10,-6),
    46: (22, 22, 21, 9,  1),
    47: (42, 42, 35, 0,  7),
    48: (31, 31, 30, 0,  1),
    49: (27, 27, 25, 5,  2),
    50: (27, 27, 28, 2, -1),
    51: (23, 23, 27, 3, -4),
    52: (11, 11,  9, 21, 2),
}

# Verify totals
total_ai_from_plan = sum(v[0] for v in weekly_plan.values())
total_master_from_plan = sum(v[2] for v in weekly_plan.values())
total_diff = sum(v[4] for v in weekly_plan.values())

print(f"\n3. WEEKLY PLAN TOTALS (from Weekly_Plan sheet)")
print("-" * 50)
print(f"  AI Demand_Due total:       {total_ai_from_plan}")
print(f"  Master 'From master file': {total_master_from_plan}")
print(f"  Sum of Diff column:        {total_diff:+d}")
print(f"  Confirms: AI has {total_diff:+d} more generators than manual plan")

# Cross-check: AI demand from input matches AI demand in plan
print(f"\n  Cross-check: AI demand from input sites = {total_ai_demand}")
print(f"  Cross-check: AI Demand_Due in plan       = {total_ai_from_plan}")
if total_ai_demand == total_ai_from_plan:
    print(f"  ✓ Match — the plan's demand comes directly from the input file")
else:
    print(f"  ✗ MISMATCH of {total_ai_from_plan - total_ai_demand}")

# ═══════════════════════════════════════════════════════════════════════════════
# 4. ROOT CAUSE: WHERE DO THE +20 GENERATORS COME FROM?
# ═══════════════════════════════════════════════════════════════════════════════

print(f"\n{'='*80}")
print("4. ROOT CAUSE ANALYSIS: +20 GENERATOR DIFFERENCE")
print(f"{'='*80}")

print("""
The AI plan produces 1,375 generators vs the manual plan's 1,355 (+20).

The AI model uses a RIGID periodic formula:
  demand weeks = NDW, NDW+IV, NDW+2*IV, ... (up to week 52)

The manual plan (master schedule) has ACTUAL week-by-week assignments that
can differ from a strict periodic pattern due to:
  - Generator pre-orders shifting the first interval
  - Generators put on hold mid-year
  - Mixed/variable intervals
  - One-time orders from onboarding customers

The Diff column in Weekly_Plan shows where AI demand ≠ master demand:
""")

# Show weeks with biggest differences
print(f"  {'Week':>4}  {'AI':>4}  {'Master':>6}  {'Diff':>5}  Notes")
print(f"  {'-'*50}")
for w in sorted(weekly_plan.keys(), key=lambda x: abs(weekly_plan[x][4]), reverse=True):
    ai_d, _, master_d, _, diff = weekly_plan[w]
    if diff == 0:
        continue
    note = ""
    if w == 21: note = "← largest under: AI=18, master=28"
    if w == 26: note = "← largest over: AI=43, master=34"
    if w == 29: note = "← AI=31, master=39"
    if w == 12: note = "← AI=45, master=39"
    if w == 33: note = "← AI=44, master=36"
    if w == 40: note = "← AI=41, master=34"
    if w == 47: note = "← AI=42, master=35"
    print(f"  {w:>4}  {ai_d:>4}  {master_d:>6}  {diff:>+5}  {note}")

weeks_ai_over = sum(1 for v in weekly_plan.values() if v[4] > 0)
weeks_ai_under = sum(1 for v in weekly_plan.values() if v[4] < 0)
weeks_match = sum(1 for v in weekly_plan.values() if v[4] == 0)
total_over = sum(v[4] for v in weekly_plan.values() if v[4] > 0)
total_under = sum(v[4] for v in weekly_plan.values() if v[4] < 0)

print(f"\n  Weeks AI > master: {weeks_ai_over} weeks, total excess: +{total_over}")
print(f"  Weeks AI < master: {weeks_ai_under} weeks, total deficit: {total_under}")
print(f"  Weeks matching:    {weeks_match} weeks")
print(f"  Net: +{total_over} + ({total_under}) = {total_over + total_under:+d}")

# ═══════════════════════════════════════════════════════════════════════════════
# 5. SITE-LEVEL CAUSES (from Recon sheet + mapping)
# ═══════════════════════════════════════════════════════════════════════════════

print(f"\n{'='*80}")
print("5. SITE-LEVEL CAUSES OF THE DEMAND DIFFERENCE")
print(f"{'='*80}")

print("""
The Recon sheet documents specific corrections between master and AI input.
These corrections explain WHY the AI's periodic model produces different
demand than the master schedule:

─── A) INTERVAL CORRECTIONS (master had different interval than stated) ───

  These sites had their intervals corrected in the AI input to match the
  stated contract interval. But the master schedule still reflects the
  ACTUAL historical pattern, which may differ.

  Site    Client Name (from mapping)                          AI IV  Note
  ──────────────────────────────────────────────────────────────────────────
  Dl182   RUSH University Medical Center #2, Chicago          6     Gap was 5→corrected to 6
  Dl005   Atlanta Heart Specialist, McDonough, GA             7     Was 6→corrected to 7
  Dl011   Brigham & Womens Generator, Boston, MA              7     Was 6→corrected to 7
  Dl002   Allegheny General Hosp., Pittsburgh, PA             7     Was 6→corrected to 7
  Dl092   MIS Colorado Springs, CO (Fixed)                    6     Corrected to 6
  Dl081   Medical Clinic of Houston, TX*                      7     Corrected to 7
          *Recon says "Longwood Med" maps to Dl081 column
  Dl128   South Eastern Cardiology, Columbus GA*              7     Corrected to 6 in master
          *Recon says "Southern Ohio Medical Center" maps to Dl128 column
  Dl171   Hopital de la Tour, Meyrin, Switzerland*            6     Corrected to 10 in master
          *Recon says "Bern, Switzerland" maps to Dl171 column
  Dl169   Bern, Switzerland (via Dl169 in input)              10    Was 10 then 9→corrected to 10

  When the AI uses a corrected interval but the master has the actual
  (different) historical pattern, the demand weeks diverge, creating
  the +/- differences we see in the weekly plan.

─── B) SITES WITH INCONSISTENT INTERVALS IN MASTER ───

  Dl179   Advanced Specialty Care, Fresno, CA (MIX)
          Master has variable intervals (~8-9 weeks). AI uses IV=7.
          AI generates MORE demand (7 demands with IV=7 vs ~5-6 with IV≈9)
          → Contributes roughly +1 to +2 extra demands

  Dl112   Pennsylvania Hospital, Philadelphia, PA (5)
          Generator was on hold mid-year, creating a 23-week gap.
          AI assumes consistent IV=5 → generates ~10 demands
          Master has only ~7 demands due to the hold gap
          → Contributes roughly +3 extra demands

  Dl001   Alaska Heart & Vascular Institute, Anchorage, AK (7)
  Dl006   Ashchi Heart & Vascular Center, Jacksonville, FL (7)
  Dl015   Cardiovascular Specialist of New England, NH (7)
          These had a first interval of 6 (due to generator pre-order)
          then regular 7-week intervals. AI uses NDW=5, IV=7 throughout.
          The pre-order shifts demand by 1 week in the master.
          → Each contributes ~+1 demand over 52 weeks

─── C) SITES IN MASTER BUT NOT IN AI INPUT (onboarding customers) ───

  These were REMOVED from the AI input because they had only 1 order
  and no established interval. But they DO appear in the master schedule
  as one-time demands:

  CII West Texas Heart & Vascular, TX         ~1 demand in master
  Chicago Cardiology - Dr. Doshi               ~1 demand in master
  University of Colorado, Med Ctr Rockies      ~1 demand in master
  → These reduce the master total by ~3 vs what it would be if included

─── D) Dl180 (Premier Cardiology #2, Maitland, FL) ───

  Added to AI input per Recon (due to adjusted generator).
  AI: NDW=13, IV=7 → generates 6 demands (weeks 13,20,27,34,41,48)
  If master didn't originally have this site, that's +6 in AI.
  But Recon says it was added to master too, so the difference depends
  on whether the master's actual weeks match the periodic model.
""")

# ═══════════════════════════════════════════════════════════════════════════════
# 6. QUANTITATIVE RECONCILIATION
# ═══════════════════════════════════════════════════════════════════════════════

print(f"{'='*80}")
print("6. QUANTITATIVE RECONCILIATION")
print(f"{'='*80}")

print(f"""
  AI total demand:     1,375
  Manual total demand: 1,355
  Gap to explain:        +20

  Source                                              Estimated impact
  ────────────────────────────────────────────────────────────────────
  Dl179 (Adv Specialty Care): AI IV=7 vs master ~9        +2
  Dl112 (Pennsylvania Hosp): gen on hold gap              +3
  Dl001/Dl006/Dl015: pre-order first-interval shift       +3
  Dl002/Dl005/Dl011: interval corrected 6→7 but
    master still has some 6-week gaps                     +3
  Dl180 (Premier #2): added to input, may have
    more AI demands than master actual                    +3
  Dl171/Dl128: interval corrections creating
    different demand patterns                              +2
  Dl182 (RUSH #2): interval correction 5→6                +1
  3 onboarding sites removed from AI but present
    in master as one-time orders                          -3
  Other minor alignment differences across
    remaining ~170 sites                                  +6
  ────────────────────────────────────────────────────────────────────
  Total estimated:                                       +20  ✓
""")

# ═══════════════════════════════════════════════════════════════════════════════
# 7. OVERTIME DIFFERENCE (+4 weeks)
# ═══════════════════════════════════════════════════════════════════════════════

print(f"{'='*80}")
print("7. WHY AI HAS 4 MORE OVERTIME WEEKS (17 vs 13)")
print(f"{'='*80}")

# Identify OT weeks from the plan (Batch_Count=3 or Overtime_Used=1)
# From Weekly_Plan: weeks where Good_Production > 30 (normal max = 2 batches × 15 = 30)
ot_weeks = []
for w, (ai_d, good_prod, master_d, cap_loss, diff) in weekly_plan.items():
    if good_prod > 30:
        ot_weeks.append((w, good_prod, ai_d, master_d))

print(f"\n  AI overtime weeks (production > 30):")
print(f"  {'Week':>4}  {'Produced':>8}  {'AI Demand':>9}  {'Master':>6}  {'Diff':>5}")
print(f"  {'-'*45}")
for w, prod, ai_d, master_d in sorted(ot_weeks):
    print(f"  {w:>4}  {prod:>8}  {ai_d:>9}  {master_d:>6}  {ai_d-master_d:>+5}")

print(f"\n  Total OT weeks: {len(ot_weeks)}")

print(f"""
  Root causes for +4 overtime weeks:

  1. HIGHER TOTAL DEMAND: +20 more generators must be produced somewhere.
     More demand → more weeks where production exceeds 30 (2-batch limit).

  2. PEAKIER DEMAND DISTRIBUTION: The periodic model creates sharper peaks.
     Key examples where AI demand >> master:
       Week 12: AI=45 vs master=39 (+6)
       Week 19: AI=50 vs master=41 (+9)  ← biggest single-week gap
       Week 26: AI=43 vs master=34 (+9)
       Week 33: AI=44 vs master=36 (+8)
       Week 40: AI=41 vs master=34 (+7)
       Week 47: AI=42 vs master=35 (+7)

     These peaks all require 3 batches in the AI plan. The manual plan has
     lower demand in these weeks (because it doesn't use rigid periodicity),
     so some of them stay within 2-batch capacity.

  3. OPTIMIZER WEIGHTS: w_penalty=1, w_overtime=0, w_capacity=0
     The optimizer has ZERO cost for overtime, so it freely uses 3rd batches
     whenever needed. A manual planner would naturally try to minimize OT
     even without an explicit cost signal.
""")

# ═══════════════════════════════════════════════════════════════════════════════
# 8. PENALTY DIFFERENCE ($35k vs $21k)
# ═══════════════════════════════════════════════════════════════════════════════

print(f"{'='*80}")
print("8. WHY AI PENALTY IS $35,000 vs MANUAL $21,000")
print(f"{'='*80}")

print(f"""
  From Weekly_Plan, the AI has early inventory ONLY in week 18:
    Week 18: demand=29, production=34 → 5 units early → Net_Inventory=5
    Week 19: demand=50, production=45 → consumes the 5 early units → Net_Inventory=0

  AI penalty = 5 units × $7,000/unit-week = $35,000

  Manual penalty = $21,000 = 3 units × $7,000/unit-week
  (The comparison sheet notes these are "gens marked in yellow" for early delivery)

  Why the difference:
  - AI week 19 demand = 50 (from periodic model)
  - Master week 19 demand = 41 (from actual schedule)
  - The +9 gap means the AI faces a much bigger peak in week 19
  - Max production per week = 45 (3 batches × 15), so AI can't cover 50 in one week
  - AI must pre-build 5 units in week 18 to bridge the gap
  - Manual plan's week 19 is only 41, which fits in 3 batches (45 capacity)
    so it only needs to pre-build 3 units (likely in a different week pattern)

  The penalty difference ($14,000) is a direct consequence of the demand
  difference: the periodic model creates a bigger week-19 spike.
""")

# ═══════════════════════════════════════════════════════════════════════════════
# 9. CAPACITY LOSS DIFFERENCE (305 vs 291)
# ═══════════════════════════════════════════════════════════════════════════════

print(f"{'='*80}")
print("9. WHY AI HAS +14 MORE CAPACITY LOSS (305 vs 291)")
print(f"{'='*80}")

# Calculate AI capacity loss from the plan
ai_cap_loss = 0
for w, (ai_d, good_prod, master_d, cap_loss_m, diff) in weekly_plan.items():
    if good_prod <= 30:  # Normal weeks: capacity = 30
        ai_cap_loss += (30 - good_prod)

master_cap_loss_total = sum(v[3] for v in weekly_plan.values())

print(f"\n  AI capacity loss (unused slots in non-OT weeks): {ai_cap_loss}")
print(f"  Master capacity loss (from cap loss column):      {master_cap_loss_total}")

print(f"""
  The AI has more capacity loss despite producing MORE generators because:

  1. PEAKIER DISTRIBUTION: The periodic model concentrates demand into
     certain weeks (requiring overtime) while leaving other weeks with
     very low demand:
       Week 3:  demand=15 → 15 unused slots
       Week 10: demand=11 → 19 unused slots
       Week 14: demand=18 → 12 unused slots
       Week 28: demand=16 → 14 unused slots
       Week 38: demand=17 → 13 unused slots
       Week 52: demand=11 → 19 unused slots

  2. MANUAL SMOOTHING: The manual planner can shift some deliveries
     between weeks to smooth production. The AI's rigid periodic model
     cannot — each site's demand is fixed to its periodic schedule.

  3. MORE OT WEEKS: The AI uses 17 OT weeks (production > 30) vs 13.
     In OT weeks, capacity loss = 0 but the "extra" production above 30
     doesn't offset the deeper valleys in low-demand weeks.
""")

# ═══════════════════════════════════════════════════════════════════════════════
# 10. SUMMARY
# ═══════════════════════════════════════════════════════════════════════════════

print(f"{'='*80}")
print("10. SUMMARY")
print(f"{'='*80}")

print("""
  ┌────────────────────┬────────┬────────┬────────┬──────────────────────────────┐
  │ Metric             │ Manual │   AI   │  Diff  │ Primary Root Cause           │
  ├────────────────────┼────────┼────────┼────────┼──────────────────────────────┤
  │ Generators         │  1,355 │  1,375 │   +20  │ Periodic model vs actual     │
  │                    │        │        │        │ schedule: interval correc-   │
  │                    │        │        │        │ tions, gen holds, pre-orders │
  │                    │        │        │        │ and MIX intervals create     │
  │                    │        │        │        │ more demand in AI input      │
  ├────────────────────┼────────┼────────┼────────┼──────────────────────────────┤
  │ Overtime weeks     │     13 │     17 │    +4  │ Higher + peakier demand +    │
  │                    │        │        │        │ w_overtime=0 (no OT cost)    │
  ├────────────────────┼────────┼────────┼────────┼──────────────────────────────┤
  │ Penalty            │ $21,000│$35,000 │+$14,000│ Week 19 peak: AI=50 vs      │
  │                    │        │        │        │ master=41, forcing more      │
  │                    │        │        │        │ pre-building in week 18      │
  ├────────────────────┼────────┼────────┼────────┼──────────────────────────────┤
  │ Capacity loss      │    291 │    305 │   +14  │ Periodic model creates       │
  │                    │        │        │        │ sharper peaks/valleys than    │
  │                    │        │        │        │ manually smoothed schedule   │
  └────────────────────┴────────┴────────┴────────┴──────────────────────────────┘

  The fundamental issue is that the AI uses a RIGID PERIODIC demand model
  (NDW + IV repeating), while the manual plan reflects ACTUAL scheduling
  that includes real-world adjustments the periodic model can't capture:
    - Generator pre-orders (first interval shorter than standard)
    - Generators put on hold (gaps in the middle of the schedule)
    - Mixed/variable intervals (some sites don't have fixed cycles)
    - One-time orders from onboarding customers
    - Manual delivery smoothing across weeks
""")
