Here’s the penalty-only Excel model (overtime is allowed and not part of the optimization objective) plus the Word user guide.
Downloads
Excel model: Production_Planner_PenaltyOnly_Max16.xlsx
User guide (Word): Excel_User_Guide_PenaltyOnly_Max16.docx
User guide (PDF): Excel_User_Guide_PenaltyOnly_Max16.pdf
What this version does (important)
Max batch produced = 16, testing discard = 1 → max good per batch = 15
Overtime is OK (up to 3 batches/week) and is not penalized
The plan focuses on minimizing early/late penalties by shifting production around shutdown/maintenance weeks
In the Penalty_Plan tab:
Set Plan Mode = AUTO (cell B3)
Mark shutdown weeks in column B (Y/N)
AutoPlan will pre-build demand for up to 3 consecutive upcoming shutdown weeks (subject to the 45 good/week limit)
Penalty is calculated as 7000 × ABS(Net Inventory End) each week
If downloads still don’t work in your chat client, tell me what you’re using (browser, Teams, desktop app, etc.) and I’ll paste the guide content and/or provide a copy/paste patch approach.
 
Below is a copy/paste-ready user guide specifically for the Excel workbook Production_Planner_Penalty_Max16.xlsx (the one with sheets: README, Dashboard, Penalty_Optimized_Plan, Onboarding_Recommendation, Sites, Calendar, Weekly_Demand, Production_Plan, Demand_Matrix).
Excel User Guide — Production Planner (Max Batch = 16) + Penalty Optimization
1) What this Excel model is for
This workbook plans weekly production for a single product and shows:
Recurring site demand schedules (160+ sites)
A 52-week weekly demand forecast
A penalty calculator that allows production to shift across weeks (early/late) and computes penalties
A place to paste an optimized plan (from Python) that minimizes penalties and overtime
A place to paste a recommended onboarding week ranking for a new site (from Python)
Key operating rules reflected in the workbook
Batch produced size: integer 2–16
Testing: exactly 1 unit discarded per batch
“Good/usable” units per batch = 1–15
Weekly capacity:
Normal: up to 2 batches ⇒ up to 30 good units/week
Overtime: up to 3 batches ⇒ up to 45 good units/week
In the penalty model, early/late is allowed with penalty.
2) Quick Start (recommended workflow)
Set calendar + parameters in README
Enter/update sites in Sites
Review demand in Weekly_Demand and Dashboard
If you want optimized shifting, use:
Penalty_Optimized_Plan (Excel penalty calc)
Paste an optimized weekly production plan into it (typically from Python)
For new site onboarding recommendation, use Onboarding_Recommendation
3) Sheet-by-sheet instructions
A) README (setup + parameters)
Purpose: global settings and key parameters used across the workbook.
What you edit
Week 1 start date: README!B5
Current week number (for skip-notice checks on the legacy sheet): README!B6
Penalty rate (USD per unit-week early OR late): README!B15
Default is 7000
Key parameters (already set, but you can change if the business rules change)
Normal max batches/week: B8 (default 2)
Overtime max batches/week: B9 (default 3)
Batch min produced: B10 (2)
Batch max produced: B11 (16)
Test units per batch: B12 (1)
Derived values (calculated automatically):
Max good/batch = 15
Max good/week normal = 30
Max good/week overtime = 45
B) Sites (your main input table)
Purpose: define each recurring 1-unit demand stream.
Rules
One row = one unit per occurrence
If a real-world site needs multiple units in the same due week, create multiple rows (e.g., SITE10_A, SITE10_B, ...)
Columns (row 5 header)
A: Site_ID* (must be unique for active rows)
B: Site_Name (optional)
C: Active (Y/N)*
D: Delivery_Day (optional; reporting only)
E: Next_Demand_Week* (1–52)
F: Interval_Weeks* (>=1)
G: Notes (optional)
H: Row_Check (auto validation)
Add a new site
Insert a new row under the table (starting at row 6).
Fill Site_ID, set Active = Y, set Next_Demand_Week, set Interval_Weeks.
Confirm Row_Check shows OK.
Change a site’s schedule (interval changes over time)
To change starting “from now”:
Update Interval_Weeks
Set Next_Demand_Week to the next required due week under the new interval
Recheck Weekly_Demand spikes and rerun optimization if used
C) Demand_Matrix (auto-generated schedule grid)
Purpose: expands each active site into a 52-week occurrence pattern (0/1).

You typically don’t edit this sheet.
D) Weekly_Demand (auto-generated demand summary)
Purpose: totals all site occurrences into weekly demand.
Column D gives Total Demand per week
Optional weekday totals appear in columns E onward (based on Delivery_Day)
Use this to spot:
weeks above 30 (overtime likely needed if meeting in-week),
weeks above 45 (would require shifting if you try to cover everything that week).
E) Dashboard (KPIs + quick signals)
Purpose: headline indicators and quick checks.
Includes:
Weeks requiring overtime (based on raw demand > 30)
Weeks infeasible (raw demand > 45) — this is for same-week capacity only
Total penalty (USD) from the Penalty_Optimized_Plan
Overtime weeks (optimized plan)
Max backlog and max early inventory (optimized plan)
If you paste a plan into Penalty_Optimized_Plan, these KPIs update automatically.
F) Penalty_Optimized_Plan (core penalty calculator + where to paste optimized plan)
Purpose: This is the operational “execution view” for penalty-based shifting.
What you edit
There are two user input columns:
Shutdown flag (maintenance/downtime weeks)
Column E: Shutdown? (Y/N)
Input range: E6:E57 (Week 1 is row 6, Week 52 is row 57)
Planned Good Production (usable units produced that week)
Column F: Planned Good Production
Input range: F6:F57
Important: By default, column F is pre-filled to match demand in-week (a “JIT” placeholder).

If you are using the optimizer, you overwrite those values with the optimizer’s weekly good production.
What the sheet calculates automatically
Batch count (0–3) based on planned good production
Batch produced sizes (adds 1 per batch for testing discard)
Net inventory end (cumulative): Column L
Positive = early inventory carried
Negative = backlog (late)
Weekly penalty: Column M
PenaltyRate × ABS(NetInventoryEnd)
Cumulative penalty: Column N
Overtime used?: Column O (YES if production > 30)
Validation notes: Column P
End-of-horizon check:
Cell L59 shows OK only if Week 52 net inventory ends at 0

(meaning all demand is satisfied by end of horizon)
How to use it (step-by-step)
Mark shutdown weeks:
Put Y in column E for weeks where production must be 0.
Create a plan:
Either manually type good production in column F, or
Paste the optimized good production schedule from Python into F6:F57
Validate:
Column P should not show errors
Cell L59 should show OK
Review penalty and overtime:
Column M/N for penalties
Column O for overtime weeks
Notes on “no storage”
The penalty model effectively treats early production as inventory (with a cost). If your real-world process truly cannot ship early (or cannot store), you can:
keep penalty extremely high to discourage early production, or
add stricter operational rules (that’s a Python change).
G) Onboarding_Recommendation (paste optimizer results here)
Purpose: help decide which week to onboard a new site to minimize penalties/overtime.
Inputs shown for reference (editable)
B4: new site interval (weeks)
B5: candidate start range (example “5-20”)
B6: units (number of 1-unit sites you’re onboarding)
What you do
Run the Python onboarding recommendation
Paste its output table starting at row 10, cell A10 (includes headers)
The table is sorted best-first by:
Total penalty
Overtime weeks
Overtime units
total batches (tie-break)
H) Production_Plan (legacy strict “same-week” plan)
Purpose: This sheet is the original strict model where demand must be met exactly in-week (no shifting). It still provides:
batch sizing validation
skip policy checks (max 3 skips, 2-week notice)
If you are using the penalty shifting approach, treat this as a reference/validation tool only.
4) Updating the model over time (weekly replanning)
Each week:
Update README!B6 current week number (optional but good practice)
Update Sites (new sites, interval changes, deactivations)
Review Weekly_Demand and Dashboard
Update shutdown weeks in Penalty_Optimized_Plan!E6:E57
Update the plan:
If you use optimization: rerun Python and paste new Good_Production into F6:F57
Confirm Penalty_Optimized_Plan!L59 is OK
5) Common troubleshooting
“ERROR: shutdown week but production > 0”
You marked a week shutdown in column E but planned production in column F.

Fix: set production to 0 in that week, or unmark shutdown.
End-of-horizon check is NOT OK (cell L59)
Your plan ends with leftover inventory or backlog at week 52.

Fix: adjust production in later weeks (or rerun optimizer) so net inventory ends at 0.
Overtime looks too high
Any week with good production > 30 triggers overtime (3rd batch).

Fix: shift some production to adjacent weeks (penalty tradeoff), or rerun optimizer.
Penalty looks too high
High penalty means large sustained inventory/backlog.

Fix: produce closer to due weeks (more overtime may be required), or onboard new sites in a different phase.
6) Practical tips
Keep Sites clean: unique IDs, correct intervals, correct next-demand week.
Use Weekly_Demand to spot spikes early.
Use Onboarding_Recommendation before committing to onboarding dates—phase matters a lot.
Use Penalty_Optimized_Plan as the “single source” for penalty + overtime KPI reporting.
If you want, paste (1) how many shutdown weeks you typically have and (2) the onboarding interval/range you care about, and I’ll tell you exactly which cells to fill and what to expect on the Dashboard after pasting results.
 
production_planner_penalty_max16.py
 