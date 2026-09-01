# Production Planner Python Guide

## Overview

This Python optimizer solves weekly production planning for a single product/factory with:
- Recurring site demand schedules
- Shutdown week constraints
- Penalty-based early/late fulfillment
- Onboarding recommendation for new sites

The optimizer uses **Dynamic Programming (DP)** to minimize costs while respecting capacity constraints.

---

## Business Rules

| Rule | Value |
|------|-------|
| Batch size range | 2–16 units produced |
| Testing discard | 1 unit per batch |
| Good units per batch | 1–15 |
| Normal capacity | 2 batches/week → 30 good units |
| Overtime capacity | 3 batches/week → 45 good units |
| Penalty rate | $7,000 per unit-week early OR late |

---

## Installation & Requirements

```bash
pip install pandas openpyxl
```

---

## Input File Format

Excel (`.xlsx`) or CSV with these columns (case-insensitive):

| Column | Required | Description | Valid Values |
|--------|----------|-------------|--------------|
| `Site_ID` | Yes | Unique site identifier | Any string |
| `Active` | Yes | Is site active? | Y/N, YES/NO, TRUE/FALSE, 1/0 |
| `Next_Demand_Week` | Yes | First demand due week | 1–52 |
| `Interval_Weeks` | Yes | Weeks between demands | ≥1 |

**Example:**
```
Site_ID,Active,Next_Demand_Week,Interval_Weeks
SITE001,Y,3,6
SITE002,Y,5,4
SITE003,N,10,8
```

---

## Command Line Parameters

### Base Optimization (Penalty Plan)

| Parameter | Flag | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| Input file | `--input` | Yes | — | Path to Excel/CSV with sites |
| Output file | `--output` | Yes | — | Output Excel path |
| Sites sheet | `--sites-sheet` | No | `Sites` | Sheet name (Excel only) |
| Shutdown weeks | `--shutdown-weeks` | No | None | Comma-separated weeks |
| Horizon | `--horizon` | No | `52` | Planning horizon in weeks |
| Penalty rate | `--penalty` | No | `7000` | USD per unit-week |
| Print summary | `--print-summary` | No | Off | Print results to console |

### Onboarding Recommendation (Additional)

| Parameter | Flag | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| Enable | `--recommend-onboarding` | — | Off | Flag to run analysis |
| Interval | `--new-site-interval` | Yes* | `0` | New site demand interval |
| Earliest week | `--new-site-earliest` | No | `1` | First candidate week |
| Latest week | `--new-site-latest` | No | `52` | Last candidate week |
| Units | `--new-site-units` | No | `1` | Number of 1-unit sites |

*Required when `--recommend-onboarding` is set

---

## Usage Examples

### 1. Basic Penalty Plan
```bash
python production_planner_penalty_max16.py \
  --input sites.xlsx \
  --sites-sheet Sites \
  --output plan_out.xlsx \
  --print-summary
```

### 2. With Shutdown Weeks
```bash
python production_planner_penalty_max16.py \
  --input sites.xlsx \
  --shutdown-weeks 10,22,35 \
  --output plan_out.xlsx \
  --print-summary
```

### 3. Onboarding Recommendation
```bash
python production_planner_penalty_max16.py \
  --input sites.xlsx \
  --shutdown-weeks 10,22 \
  --recommend-onboarding \
  --new-site-interval 6 \
  --new-site-earliest 5 \
  --new-site-latest 20 \
  --new-site-units 1 \
  --output plan_out.xlsx \
  --print-summary
```

### 4. CSV Input with Custom Penalty
```bash
python production_planner_penalty_max16.py \
  --input sites.csv \
  --penalty 5000 \
  --horizon 52 \
  --output plan_out.xlsx
```

---

## Output Excel Sheets

| Sheet | Description |
|-------|-------------|
| `Weekly_Plan` | 52-week production schedule with penalties |
| `Sites_Clean` | Validated active sites used in planning |
| `Input_Issues` | Data quality problems found |
| `Model_Params` | Parameters used in the run |
| `Onboarding_Recommendation` | Ranked candidate weeks (if enabled) |

### Weekly_Plan Columns

| Column | Description |
|--------|-------------|
| Week | Week number (1–52) |
| Shutdown | Y/N shutdown flag |
| Demand_Due | Units due this week |
| Good_Production | Usable units produced |
| Batch_Count | Number of batches (0–3) |
| Batch1/2/3_Produced | Units per batch (includes test unit) |
| Produced_Total | Total units produced |
| Testing_Discard | Units lost to QA |
| Overtime_Used | Y if production > 30 |
| Net_Inventory_End | Cumulative inventory (+) or backlog (-) |
| Early_Units_Held | Units produced early |
| Late_Units_Backlog | Units delivered late |
| Weekly_Penalty_USD | Penalty for this week |
| Cumulative_Penalty_USD | Running total penalty |

### Onboarding_Recommendation Columns

| Column | Description |
|--------|-------------|
| Candidate_Start_Week | Week being evaluated |
| Total_Penalty_USD | Total penalty with new site |
| Overtime_Weeks | Weeks requiring overtime |
| Overtime_Units | Units produced in overtime |
| Total_Batches | Total batches across horizon |
| Total_Produced_Units | Total units including test discards |
| Delta_Penalty_USD_vs_Base | Change from baseline |
| Delta_Overtime_Weeks_vs_Base | Change from baseline |
| Feasible | True/False |
| Reason | Error message if infeasible |

---

## How the Optimizer Works

### Optimization Objective (Lexicographic)

The DP minimizes these metrics in order:
1. **Total penalty dollars** — $7,000 × |inventory| per week
2. **Overtime weeks** — count of weeks with production > 30
3. **Overtime units** — sum of (production - 30) for overtime weeks
4. **Total batches** — tie-breaker
5. **Total produced units** — tie-breaker

### Shutdown Week Handling

- Shutdown weeks have **zero capacity** (no production allowed)
- Demand due in shutdown weeks must be fulfilled early or late
- The DP automatically redistributes production optimally
- Penalties apply for each week units are held early or delivered late

### State Space

- **State:** Net inventory at end of each week (can be + or -)
- **Transition:** `inv_new = inv_prev + production - demand`
- **Bounds:** Computed to prune infeasible states
- **Terminal:** Must end week 52 with inventory = 0

### Onboarding Logic

For each candidate start week:
1. Add new site's recurring demand to baseline
2. Run full DP optimization
3. Compare cost vs baseline (no new site)
4. Rank candidates by total cost

---

## Troubleshooting

### "No feasible states at week X"
- Total capacity insufficient given shutdown weeks
- **Fix:** Reduce shutdowns or check if demand is realistic

### "No solution ends with zero inventory"
- Cannot satisfy all demand by week 52
- **Fix:** Check input data, reduce shutdowns

### High penalty in output
- Large sustained inventory or backlog
- **Fix:** Consider different shutdown timing or site phasing

### Onboarding shows all infeasible
- Adding new site exceeds capacity
- **Fix:** Try different interval or reduce candidate range

---

## Excel Integration Workflow

1. Maintain sites in Excel `Sites` sheet
2. Export to CSV or use Excel directly as input
3. Run Python optimizer
4. Paste `Weekly_Plan` results into Excel `Penalty_Optimized_Plan` sheet
5. Paste `Onboarding_Recommendation` into Excel `Onboarding_Recommendation` sheet
6. Review Dashboard KPIs

---

## Comparison: Python vs Excel AUTO Mode

| Feature | Python Optimizer | Excel AUTO Mode |
|---------|------------------|-----------------|
| Handles any shutdown pattern | ✅ | Up to 3 consecutive |
| Global penalty optimization | ✅ | ❌ Rule-based only |
| Cascades overflow to earlier weeks | ✅ | ❌ Caps at 45 |
| Considers penalty tradeoffs | ✅ | ❌ |
| Onboarding recommendation | ✅ | ❌ |
| Requires Python | ✅ | ❌ |

**Recommendation:** Use Python optimizer for production planning. Use Excel for validation and KPI reporting.
