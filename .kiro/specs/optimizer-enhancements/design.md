# Design Document — Ruby Fill Optimizer Business Enhancements

## Overview

> **Architecture reference:** This feature is built within the layered architecture defined in `.kiro/specs/ARCHITECTURE.md` (domain / service / adapter / presentation, with dependencies pointing inward). Throughout this document, module names like `delivery_assignment.py` and `comparison.py` refer to their **domain-layer** homes (`domain/delivery_assignment.py`, `domain/comparison.py`); parsers and writers like `master_planner_parser.py` and `input_file_writer.py` live in the **adapter layer** (`io/`); orchestration lives in the **service layer** (`services/`); and all Streamlit code lives in the **presentation layer**. The mapping table at the end of this document ties each of the six features to its layers.

This design covers six interrelated features that transform the optimizer from a black-box number cruncher into an explainable, self-service planning tool. The features share data flows and UI state but are implemented as independent modules that compose cleanly.

```
                    ┌───────────────────────────────────────────────┐
                    │         Settings Tab (Requirement 7)          │
                    │  All params configurable, per-session         │
                    └─────────────────────┬─────────────────────────┘
                                          │ params
        ┌─────────────────────────────────┼─────────────────────────────────┐
        │                                 │                                 │
        ▼                                 ▼                                 ▼
┌───────────────────┐         ┌───────────────────────┐         ┌───────────────────┐
│  Cost Optimizer   │         │ Onboarding Recommend. │         │  Cost Comparison  │
│  (existing + fix) │         │  (multi-customer)     │         │  (new)            │
│                   │         │                       │         │                   │
│ • row_cap enforced│         │ • per-customer windows│         │ • Master Planner  │
│ • reference dates │         │ • heuristic search    │         │   baseline parse  │
│ • delivery assign.│         │ • input file gen      │         │ • side-by-side    │
│ • changed-week    │         │                       │         │                   │
└───────────────────┘         └───────────────────────┘         └───────────────────┘
```

## Feature 1: Baseline Cost from Master Planner

### Data Flow

```
Master Planner .xlsx → parse_master_planner() → weekly_planned_production[1..52]
                                                        ↓
Input file → build_weekly_demand() → demand[1..52]  ←──┘
                                                        ↓
                                              compute_baseline_cost()
                                                        ↓
                                              baseline_summary dict
                                                        ↓
                                    compare_plans(baseline_summary, optimized_summary)
                                                        ↓
                                              Cost_Comparison output
```

### Module: `io/master_planner_parser.py` (adapter layer)

#### `parse_master_planner(file_bytes, sheet_name="Schedule") → MasterPlannerData`

Reads the wide Master Planner workbook and extracts:
1. The `Weeks #` column (column A, row 2 header) → week identifiers.
2. The `Total Commercial` column (column I) → weekly planned commercial demand.
3. The `QC GEN` column (column J) → weekly QC generators.
4. Per-customer columns (columns O onwards) → individual customer schedule marks.

**Column identification strategy:**
- The header row is row 2.
- `Weeks #` is identified by header text matching.
- `Total Commercial`, `QC GEN`, `US Demand`, `RoW Demand` are matched by header text (case-insensitive, whitespace-normalized).
- Customer columns are identified as all columns after the aggregate columns that contain schedule marks (values of 1, empty, or 0).

**Customer-to-SiteID matching and ID assignment:**
- Extract the leading numeric token from each customer column header: `re.match(r'(\d+)', header.strip())`.
- 167 of ~220 customer columns have this leading number; it becomes the `Site_ID` directly.
- Columns **without** a leading number (55 in the current dataset — either non-customer columns like "US STAB" or newer customers not yet assigned a serial) are handled deterministically:
  1. Non-customer columns (a configurable ignore-list, e.g. "US STAB", "CAN STAB", blank headers, aggregate columns) are excluded.
  2. Every remaining unnumbered customer column is assigned a **generated stable unique identifier**, derived deterministically from a normalized form of its header text so that the same column always maps to the same ID across runs. Format: `RF-<hash8>` where `hash8` is the first 8 hex chars of a SHA-1 of the normalized header (lowercase, collapsed whitespace, punctuation stripped). Deterministic and collision-checked.
  3. The parser emits an **ID assignment report** (`assigned_ids: dict[str, str]` mapping header → generated ID, plus the customer name) so the planning team can be given the list. The team then uses these identifiers as the `Site_ID` in future input sheets, at which point matching becomes exact by number/ID.
- The ID assignment report is surfaced in the UI and included as a sheet in the export, so the mapping is auditable and shareable.

```python
@dataclass
class AssignedId:
    generated_id: str      # e.g. "RF-3f2a9c1b"
    column_header: str     # raw Master Planner header
    customer_name: str     # cleaned name portion
    normalized_key: str    # the key the hash was derived from

def assign_stable_id(header: str) -> str:
    norm = re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", header.strip().lower())).strip()
    digest = hashlib.sha1(norm.encode("utf-8")).hexdigest()[:8]
    return f"RF-{digest}"
```

- Deterministic: the same header always yields the same ID, so re-parsing the same Master Planner (or the team reusing the shared ID) is stable.
- Collision handling: if two distinct normalized keys hash to the same 8 chars (astronomically unlikely), extend to 10 chars and re-check.

```python
@dataclass
class MasterPlannerData:
    weekly_planned_production: list[int]   # 1-indexed, total = commercial + QC
    weekly_commercial: list[int]           # 1-indexed, from Total Commercial
    weekly_qc: list[int]                   # 1-indexed, from QC GEN
    customer_schedule: dict[str, list[int]]  # site_id → [0]*53, 1 in weeks where due
    assigned_ids: list[AssignedId]         # generated IDs for unnumbered customer columns
    ignored_columns: list[str]             # non-customer columns excluded from matching
    week_to_row: dict[int, int]            # planning week → Excel row number
    mfg_dates: list[date | None]           # 1-indexed, from MFG Date column
    cal_dates: list[date | None]           # 1-indexed, from Calibration date column
```

#### `compute_baseline_cost(planned_production, demand, params) → dict`

Evaluates the manual plan using the exact same cost model as the optimizer:

```python
def compute_baseline_cost(
    planned_production: list[int],  # 1-indexed weekly production from manual plan
    demand: list[int],              # 1-indexed weekly demand
    params: IntegratedParams,
    shutdown_weeks: list[int],
    partial_shutdown_weeks: list[int],
) -> dict:
    """Compute cost of the manual plan using the same cost model as the optimizer.

    Does NOT optimize — just evaluates the given plan against demand.
    Returns a summary dict with the same keys as solve_plan_integrated's summary.
    Also returns per-week capacity violations.
    """
    T = params.horizon_weeks
    inv = 0
    total_penalty = total_overtime = total_capacity = 0.0
    overtime_weeks = 0
    capacity_violations = []

    for t in range(1, T + 1):
        y = planned_production[t]
        d = demand[t]

        # Determine week type and max capacity
        if t in set(shutdown_weeks):
            wt, cap = "Shutdown", 0
        elif t in set(partial_shutdown_weeks):
            wt, cap = "Partial", params.max_good_per_batch
        else:
            wt, cap = "Normal", params.overtime_max_good_week

        # Report capacity violations (don't reject — just flag)
        if y > cap:
            capacity_violations.append((t, y, cap))

        inv = inv + y - d
        cost = compute_weekly_cost(inv, y, wt, params)
        # decompose...
        ...

    return {
        "total_composite_cost": ...,
        "total_penalty_cost": ...,
        "total_overtime_cost": ...,
        "total_capacity_cost": ...,
        "overtime_weeks": ...,
        "capacity_violations": capacity_violations,
    }
```

#### `compare_plans(baseline, optimized) → ComparisonResult`

```python
@dataclass
class ComparisonResult:
    components: list[dict]  # one per cost component + total
    # Each: {name, baseline, optimized, saving_abs, saving_pct}
    overtime_baseline: int
    overtime_optimized: int
    weekly_comparison: pd.DataFrame  # Week, Manual_Production, Optimized_Production, Diff
```

## Feature 2: row_cap Enforcement in the DP Solver

### The Problem

Currently `row_cap = 2` is computed post-hoc in `_build_plan_df` but never constrains the solver. The DP enumerates all `y` in `[y_min, cap_max]` without considering how many units serve EU-restricted customers.

### Solution: Constrain y_min Based on ROW Demand

The `row_cap` constraint says: in any week, at most `row_cap` units may be delivered to restricted-country customers. Since deliveries happen in the week of demand (or from prior inventory), the binding interpretation is:

**The solver must produce enough total units that EU-restricted demand in any week can be served without exceeding `row_cap` fulfilled per week.**

However, the solver already tracks aggregate inventory, not per-customer delivery. The true enforcement requires the delivery assignment (Feature 3). A practical approach:

**Approach: Add EU-restricted demand as a hard floor on per-week fulfillment capacity.**

Since `row_cap` limits EU fulfillment per week, EU demand exceeding `row_cap` in a given week must be served from inventory built in earlier weeks. This creates a **minimum inventory** requirement:

```python
# For each week t where eu_demand[t] > row_cap:
#   We need at least (eu_demand[t] - row_cap) units in inventory at end of week t-1
#   to pre-fill those excess EU deliveries.
```

**Implementation:** Modify `compute_inventory_bounds` to raise `lb[t-1]` when `eu_demand[t] > row_cap`:

```python
for t in range(1, T + 1):
    eu_excess = max(0, eu_demand[t] - row_cap)
    if eu_excess > 0:
        # Must have at least eu_excess in inventory entering week t
        # That means inv_end[t-1] >= eu_excess
        lb[t-1] = max(lb[t-1], eu_excess)
```

This is a minimal, correct change that forces the DP to build up inventory before high-EU-demand weeks, without expanding the state space. On the current dataset where max EU demand per week is 4 and `row_cap` is 2, this raises `lb` by at most 2 in a few weeks — negligible impact on state space.

**New parameter in DP interface:**

```python
def solve_plan_integrated(
    demand, shutdown_weeks, partial_shutdown_weeks,
    row_demand, row_cap, params,
    eu_demand=None,  # NEW: 1-indexed EU-restricted demand for row_cap enforcement
) -> ...:
```

When `eu_demand` is provided, the inventory bounds incorporate the `row_cap` floor. When `None`, behaviour is unchanged (backward compatible).

## Feature 3: Delivery Assignment and Changed-Week Detection

### The Problem

The DP solver produces aggregate `y_plan[t]` (how many units to make per week) but never decides *whose* generator is made when. We need a deterministic assignment to detect changed weeks.

### Algorithm: Greedy Assignment with Priority Queue

After the solver produces `y_plan` and `inv_plan`, assign units to customers:

```
Input:
  - y_plan[1..T]: good units produced each week
  - demand_events: list of (scheduled_week, site_id) sorted by scheduled_week, then site_id
  - inv_plan[1..T]: net inventory at end of each week

Output:
  - assignments: list of (site_id, scheduled_week, planned_week)

Algorithm:
  1. Build a supply pool: for each week t, add y_plan[t] "supply tokens" available from week t.
  2. Process demand events in order (earliest scheduled_week first, tie-break by site_id alphabetically).
  3. For each demand event (sw, site):
     a. Find the earliest supply token with planned_week <= sw (prefer on-time, then earliest early).
     b. If none found (backlog case), find the earliest supply token after sw.
     c. Assign: (site, sw, token's production_week).
  4. Week_Shift = planned_week - scheduled_week.
```

**Tie-breaking rule (deterministic):** When multiple supply tokens are available for the same demand event, choose the one from the latest production week that is still ≤ scheduled_week. This minimizes inventory holding and produces the most conservative (smallest) changed-week set. If still tied, choose the lower-indexed token.

**Complexity:** O(D log D) for sorting + O(D) for assignment with a pointer, where D = total demand (~1,346).

### Module: `domain/delivery_assignment.py` (domain layer)

```python
def assign_deliveries(
    y_plan: list[int],
    demand_events: list[tuple[int, str]],  # (scheduled_week, site_id)
    params: IntegratedParams,
) -> list[DeliveryRecord]:
    """Deterministic assignment of produced units to customer demands."""
    ...

@dataclass
class DeliveryRecord:
    site_id: str
    site_name: str
    country: str
    scheduled_week: int
    planned_week: int
    week_shift: int
    is_early: bool
    is_late: bool
    is_new_customer: bool
```

### Changed-Week Comparison Against Master Planner

When a Master Planner workbook is provided, the changed-week report adds a second comparison:

```python
def compare_against_master_planner(
    assignments: list[DeliveryRecord],
    master_customer_schedule: dict[str, list[int]],  # site_id → weeks with 1
) -> list[DeliveryRecord]:
    """Add master_planner_week and mp_week_shift to each record."""
    for rec in assignments:
        if rec.site_id in master_customer_schedule:
            # Find the Master Planner week closest to this assignment's scheduled_week
            mp_weeks = [w for w in range(1,53) if master_customer_schedule[rec.site_id][w] == 1]
            # Match by occurrence order...
```

**Newly added customers:** sites in the current run's input file that have no matching Master Planner column are flagged as `is_new_customer = True`.

## Feature 4: Multi-Customer Onboarding with Independent Windows

### The Combinatorial Problem

With N customers having windows of width W₁, W₂, ..., Wₙ, the exhaustive search evaluates W₁ × W₂ × ... × Wₙ combinations, each requiring a full DP solve (0.3s).

| Scenario | Combinations | Time (exhaustive) |
|---|---|---|
| 3 × 6-week | 216 | ~65s |
| 5 × 8-week | 32,768 | ~2.7 hours |
| 10 × 13-week | 1.4×10¹¹ | impossible |

### Algorithm: Greedy-then-Refine with Exhaustive Threshold

```
EXHAUSTIVE_THRESHOLD = 500  # combinations

Phase 1: Estimate search space
  total_combinations = product(window_width_i for each customer i)
  if total_combinations <= EXHAUSTIVE_THRESHOLD:
      → exhaustive_search()  # guaranteed optimal
  else:
      → heuristic_search()   # strong candidate, not proven optimal

Phase 2a: Exhaustive Search
  For each combination in the Cartesian product of all windows:
      Inject all customers' demand at their selected weeks.
      Run solver. Record (combination, delta_cost).
  Return top 5 by each objective.

Phase 2b: Heuristic Search (coordinate descent)
  1. INDEPENDENT PHASE: For each customer i alone (others not onboarded):
       Run evaluate_all_candidates() for customer i.
       Record their top-3 weeks by composite cost.
  2. SEED: Form an initial combination by picking each customer's best week
       from the independent phase (lowest delta_composite).
  3. REFINE (coordinate descent):
       repeat until no improvement:
           for each customer i:
               Fix all other customers at their current selected week.
               Try all weeks in customer i's window.
               If a better week is found for customer i, update.
       This converges because the objective is bounded below.
  4. DIVERSIFY: Repeat steps 2-3 with different seeds (e.g., top-3 from
       each customer, giving up to 3^N seeds, capped at 10 seeds).
  5. Return top 5 distinct combinations across all seeds and all objectives.
```

**Performance of heuristic:** Coordinate descent does at most `N × max_W × num_iterations` solves. With N=10, max_W=13, and typically 2–3 iterations to convergence, that's ~390 solves per seed × 10 seeds = ~3,900 solves ≈ 20 minutes. Plus the independent phase: N × max_W = 130 solves ≈ 40s.

For the UI to remain usable, the heuristic displays progress (a progress bar showing seeds completed).

### Module: Updates to `onboarding_recommendation.py`

The multi-customer onboarding engine replaces the existing single-customer flow. Internally it calls `solve_with_suppliers` from `supplier_allocation.py` (see `.kiro/specs/supplier-constraints/design.md`) so that supplier constraints, quota penalties, and `row_cap` enforcement are all active during onboarding evaluation.

```python
def evaluate_multi_customer(
    active_df: pd.DataFrame,
    new_customers: list[NewCustomerDef],
    params: IntegratedParams,
    shutdown_weeks: list[int],
    partial_shutdown_weeks: list[int],
    progress_callback: Callable[[float, str], None] | None = None,
) -> tuple[dict, list[CombinationResult]]:
    """Evaluate onboarding combinations for multiple customers.

    Returns (base_summary, results) where results contains the top candidates
    per objective, each with a selected_week per customer.
    """
    ...

@dataclass
class NewCustomerDef:
    site_id: str
    site_name: str
    earliest_week: int
    latest_week: int
    interval_weeks: int
    country: str
    eu_restricted: bool

@dataclass
class CombinationResult:
    selected_weeks: dict[str, int]  # site_id → selected start week
    feasible: bool
    delta_penalty: float
    delta_overtime: float
    delta_capacity: float
    delta_composite: float
    plan_df: pd.DataFrame | None
```

### Search Space Estimation UI

Before running, the app shows:
```
Search space: 5 customers × [6, 8, 4, 10, 6] week windows = 11,520 combinations.
This exceeds the exhaustive threshold (500). A heuristic search will be used.
Estimated time: ~3 minutes. Results are strong candidates but not proven optimal.
[Run Recommendation]
```

## Feature 5: Generate Optimizer Input File

### Simple — Post-Selection Assembly

After the planner selects a start week for each new customer:

```python
def generate_input_file(
    existing_file_bytes: bytes,
    existing_filename: str,
    sheet_name: str,
    new_customers: list[NewCustomerDef],
    selected_weeks: dict[str, int],  # site_id → selected start week
) -> bytes:
    """Generate an optimizer-ready input file combining existing sites + new customers."""

    # Read existing file preserving all columns
    existing_df = pd.read_excel(io.BytesIO(existing_file_bytes), sheet_name=sheet_name)

    # Build new rows
    new_rows = []
    for cust in new_customers:
        new_rows.append({
            "Site_ID": cust.site_id,
            "Active": "Y",
            "Next_Demand_Week": selected_weeks[cust.site_id],
            "Interval_Weeks": cust.interval_weeks,
            "Country": cust.country,
            "Site_Name": cust.site_name,
            "EU_Restricted": "Y" if cust.eu_restricted else "N",
            "Is_New": "Y",  # Flag column
        })

    new_df = pd.DataFrame(new_rows)
    combined = pd.concat([existing_df, new_df], ignore_index=True)

    # Write to bytes
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        combined.to_excel(writer, sheet_name=sheet_name, index=False)
    return buf.getvalue()
```

Validation: before generating, check `Site_ID` uniqueness across existing + new.

## Feature 6: Reference Week and Calendar Dates

### Date Derivation

```python
def derive_week_dates(
    reference_week_date: date,
    calibration_offset_days: int,
    horizon_weeks: int,
) -> list[tuple[int, date, date]]:
    """Return (week_number, mfg_date, cal_date) for each week in the horizon."""
    result = []
    for w in range(1, horizon_weeks + 1):
        mfg = reference_week_date + timedelta(days=7 * (w - 1))
        cal = mfg + timedelta(days=calibration_offset_days)
        result.append((w, mfg, cal))
    return result
```

### Current-Week Indicator

```python
def current_planning_week(reference_week_date: date, today: date) -> int | None:
    """Return the planning week number that contains today, or None if outside horizon."""
    delta = (today - reference_week_date).days
    if delta < 0:
        return None
    week = delta // 7 + 1
    return week if week <= 52 else None
```

### UI Integration

- Settings tab: date picker for Reference_Week, number input for Calibration_Offset.
- All tables: columns `Week`, `MFG_Date`, `Cal_Date` added left-aligned.
- Current week: highlighted row with a "▶" indicator.
- When Reference_Week is None: `MFG_Date` and `Cal_Date` columns are omitted.

## Cross-Feature Data Flow

**Integration between the two specs:** The optimizer-enhancements and supplier-constraints features compose as layers around the same DP solver. The execution order is:

1. `solve_plan_integrated` (enhanced with optional `eu_demand` for `row_cap` enforcement) — produces the aggregate plan.
2. `supplier_allocation.allocate_suppliers_weekly` — deterministic post-solve allocation of units to Curium/BWXT.
3. `delivery_assignment.assign_deliveries` — deterministic post-solve mapping of units to customers.
4. Both layers operate on the same `y_plan` and never conflict because supplier allocation decides *material origin* while delivery assignment decides *customer destination* — orthogonal concerns.

The `eu_demand` array (1-indexed, one entry per week) is shared between both designs:
- In the supplier design: used by `allocate_suppliers_weekly` to verify Curium allocation ≥ EU demand.
- In the enhancements design: used by `compute_inventory_bounds` to enforce `row_cap` as an inventory floor.
- Built from the same source: `build_weekly_row_demand(active_df, params)` from the existing code, which already computes per-week demand from EU-restricted sites.

```
                         ┌──────────────────┐
                         │  Upload Sites    │
                         │  + Master Planner│
                         └────────┬─────────┘
                                  │
                    ┌─────────────┼─────────────────┐
                    ▼             ▼                  ▼
           parse sites    parse master planner   parse reference dates
                    │             │                  │
                    ▼             ▼                  ▼
              active_df    MasterPlannerData    week_dates[]
                    │             │                  │
                    ├─────────────┤                  │
                    ▼             ▼                  │
            build_demand   compute_baseline ◄───────┘
                    │             │
                    ▼             ▼
         solve_plan_integrated   baseline_summary
                    │
                    ▼
              y_plan, inv_plan
                    │
          ┌─────────┼──────────┐
          ▼         ▼          ▼
   supplier_alloc  assign_deliveries  compare_plans
          │         │                  │
          ▼         ▼                  ▼
   plan_df++  changed_weeks[]    ComparisonResult
          │         │                  │
          └─────────┼──────────────────┘
                    ▼
            export_excel (extended)
```

## UI Structure Changes

The existing three-tab layout becomes:

```
⚙️ Settings | 📊 Cost Optimizer | 🆕 Onboarding | 📋 Comparison
```

**Settings tab** (enhanced):
- All existing parameters (grouped: Production, Costs, Weights).
- New group: Reference Dates (Reference_Week date picker, Calibration_Offset).
- New group: Supplier Parameters (delegated to supplier_allocation module).
- "Restore Defaults" button.

**Cost Optimizer tab** (enhanced):
- Existing file upload + run.
- Results section gains:
  - Week/MFG_Date/Cal_Date columns in the plan table.
  - "Changed Weeks" expander with summary counts + filterable table.
  - Current-week indicator.

**Onboarding tab** (enhanced):
- Multi-customer input table (add/edit/remove rows + bulk paste).
- Per-customer: Site_ID, Site_Name, Earliest, Latest, Interval, Country, EU_Restricted.
- Search space estimate shown before run.
- Progress bar during heuristic search.
- Results: top-5 per objective with per-customer start weeks shown.
- "Generate Input File" button (enabled after selection).
- "Generate Full Plan" button (existing, enhanced with supplier + dates).

**Comparison tab** (new):
- Master Planner upload.
- Side-by-side cost table: Baseline vs Optimized per component.
- Savings summary (absolute + percentage).
- Week-by-week production comparison chart.
- Overtime weeks comparison.

## Export Workbook Structure (Extended)

| Sheet | Contents |
|---|---|
| Weekly_Plan | Existing 22 cols + MFG_Date, Cal_Date, Supplier cols, EU_Restricted_Demand |
| Sites_Clean | Existing + Site_Name, EU_Restricted |
| Input_Issues | Existing |
| Model_Params | Existing + Reference_Week, Cal_Offset, all supplier params |
| Changed_Weeks | site_id, site_name, country, scheduled_week, planned_week, week_shift, is_new |
| Cost_Comparison | Component, Baseline, Optimized, Saving_Abs, Saving_Pct |
| Weekly_Comparison | Week, MFG_Date, Manual_Prod, Optimized_Prod, Difference |
| Quota_Status | Supplier, Quarter, Quota, Ordered, Remaining, Shortfall, Penalty |
| Assigned_IDs | Generated_ID, Customer_Name, Master_Planner_Header — shareable mapping for unnumbered customers |

## Changed-Week Highlighting (Excel Formatting)

| Condition | Fill Color | Meaning |
|---|---|---|
| week_shift < 0 (early) | Light green (#C6EFCE) | Produced earlier than scheduled |
| week_shift > 0 (late) | Light red (#FFC7CE) | Produced later than scheduled |
| is_new_customer | Light blue (#BDD7EE) | Newly added, no prior schedule |
| week_shift == 0 | No fill | On schedule |

These colours are chosen to not conflict with the Master Planner's existing blue (#002060, dark), yellow (#FFFF00), orange (#FFC000), and red (#FF0000).

## File Structure (mapped to layers)

Per `.kiro/specs/ARCHITECTURE.md`, the new modules are placed by layer:

```
domain/
    delivery_assignment.py     # Greedy unit-to-customer assignment (pure)
    comparison.py              # Baseline cost computation + compare (pure)
    dates.py                   # Reference week date derivation (pure)
    solver.py                  # compute_inventory_bounds gains eu_demand/row_cap (pure)
    supplier_allocation.py     # (from supplier-constraints design)
services/
    comparison_service.py      # orchestrates Master Planner parse → baseline → compare
    onboarding_service.py      # orchestrates multi-customer evaluation + file gen
    optimizer_service.py       # attaches delivery assignment + dates to the run
io/
    master_planner_parser.py   # Parse Master Planner wide format, assign stable IDs
    input_file_writer.py       # Generate optimizer-ready input file with new customers
    workbook_exporter.py       # Adds Changed_Weeks, Cost_Comparison, Assigned_IDs sheets + formatting
ui/
    tab_comparison.py          # new Comparison tab
    tab_onboarding.py          # enhanced multi-customer onboarding
    tab_settings.py            # reference date + all-parameter widgets
```

Changes to existing files (via the migration in ARCHITECTURE.md):
- `domain/solver.py` (extracted from `integrated_cost_optimizer.py`): `compute_inventory_bounds` gains optional `eu_demand` + `row_cap` enforcement.
- `onboarding_recommendation.py`: becomes a re-export shim; `evaluate_multi_customer()` (in the onboarding domain/service) is the primary entry point, with single-customer as the N=1 case.
- `app.py`: shrinks to wiring adapters into services and rendering tabs; multi-customer input table and Comparison tab move into `ui/`.
- `integrated_cost_optimizer.py` / `onboarding_recommendation.py` remain as backward-compatible re-export shims so existing tests keep passing.

## Testing Strategy

### Unit Tests

| Module | Layer | Key Tests |
|---|---|---|
| `io/master_planner_parser.py` | adapter | Column detection, header matching, week alignment, QC addition, stable-ID assignment + determinism |
| `domain/delivery_assignment.py` | domain | All-on-time case, early production case, backlog case, tie-breaking determinism, total count invariant |
| `domain/comparison.py` | domain | Zero-cost baseline, infeasible manual plan, savings calculation, percentage edge cases |
| `domain/dates.py` | domain | Reference date derivation, current week calculation, edge cases (outside horizon) |
| `domain` onboarding | domain | Exhaustive vs heuristic threshold, single-customer regression, coordinate descent convergence |
| `services/*` | service | Each service with fake adapter ports; verify orchestration and DTO shape |

### Property Tests

| # | Property | Module |
|---|---|---|
| 1 | sum(assignments by planned_week t) == y_plan[t] for all t | delivery_assignment |
| 2 | len(assignments) == total_demand | delivery_assignment |
| 3 | Each assignment's planned_week is in [1, T] | delivery_assignment |
| 4 | For determinism: same input → same output | delivery_assignment |
| 5 | baseline_cost >= 0 for any valid plan | domain/comparison |
| 6 | savings = baseline - optimized (signs consistent) | domain/comparison |
| 7 | Exhaustive search on small space finds same result as heuristic | onboarding |
| 8 | Generated input file round-trips through the optimizer without issues | input file gen |
| 9 | week_dates are 7 days apart and cal = mfg + offset | domain/dates |
| 10 | current_week returns None when today is outside horizon | domain/dates |

### Integration Tests

1. Full pipeline: upload sites + Master Planner → run optimizer → verify comparison + changed weeks + dates are all consistent.
2. Onboarding: 3 customers × 6-week windows → verify exhaustive completes in < 2 minutes and results are ranked correctly.
3. Input file generation → re-upload → verify zero new issues and plan runs successfully.

## Correctness Properties (from Requirements)

| Req | Property | Implementation |
|---|---|---|
| 1.9 | Baseline uses same cost model as optimizer | `compute_baseline_cost` calls `compute_weekly_cost` |
| 3.12 | Total assignments == total demand | Assignment loop assertion |
| 3.13 | Assignments per week == y_plan per week | Assignment loop assertion |
| 3.11 | Deterministic tie-breaking | Sorted demand events + latest-available-supply rule |
| 4.15 | Heuristic used above threshold | `total_combinations > EXHAUSTIVE_THRESHOLD` check |
| 5.10 | Generated file round-trips cleanly | Integration test |
| 6.4 | Dates are 7 days apart | `derive_week_dates` arithmetic |
| 7.4 | row_cap enforced | Inventory bounds modification |
| 8.1 | Backward compatible | Existing tests continue to pass |
