# Design Document — Raw Material Supplier Constraints

## Overview

> **Architecture reference:** This feature is built within the layered architecture defined in `.kiro/specs/ARCHITECTURE.md`. The components below map to layers as follows: `supplier_allocation.py` and `quota.py` live in the **domain layer** (pure, no I/O); the `solve_with_suppliers` orchestration is invoked by the **optimizer service**; the new Quota_Status export lives in the **adapter layer** (`io/workbook_exporter.py`); and the supplier settings widgets live in the **presentation layer**. The module paths in this document (`supplier_allocation.py`) refer to their domain-layer homes (`domain/supplier_allocation.py`).

This design adds Strontium-82 supplier constraints to the existing Ruby Fill production optimizer. The central question is whether supplier allocation (Curium vs BWXT) should be embedded in the DP solver's state space or handled as a deterministic post-solve layer. We choose the **post-solve allocation** approach because:

1. The current DP state is a single integer (net inventory). Adding a per-supplier cumulative-activity dimension would multiply the state space by the range of possible activity values per quarter (~10,000+ states), making the solver 10,000× slower — from 0.3s to 50+ minutes.
2. The EU-restricted demand (max 4 generators/week) is always well below one Curium batch (15), so the Curium-first rule never creates infeasibility in the current dataset.
3. Quarterly quota targets (10,000 mCi each) are far below typical quarterly production (~35,000 mCi/quarter) so they almost never bind either.
4. When they do bind (unusual supplier unavailability patterns), we detect and report infeasibility rather than silently producing a bad plan.

The design therefore keeps the DP solver unchanged (preserving its proven correctness and speed) and adds three new layers around it:

```
Input → [Existing DP Solver] → y_plan (aggregate weekly production)
                                    ↓
                            [Supplier Allocator]  ← EU demand, availability, run rules
                                    ↓
                            [Quota Checker]       ← quarterly accumulation, penalty
                                    ↓
                            [Activity Calculator] ← Sr-82 mCi per supplier per week
                                    ↓
                            [Extended Plan Builder] → plan_df with new columns
```

If the Quota Checker finds a shortfall, it injects a penalty cost into the summary. In extreme cases (supplier unavailability creating an infeasible allocation), the system reports the issue before output.

## Architecture

```mermaid
graph TD
    subgraph Existing (unchanged)
        A[IntegratedParams] --> B[clean_sites / build_demand]
        B --> C[solve_plan_integrated DP]
        C --> D[y_plan, inv_plan]
    end

    subgraph New: supplier_allocation.py
        E[SupplierParams dataclass]
        F[allocate_suppliers_weekly]
        G[compute_activity]
        H[check_quarterly_quota]
        I[build_supplier_plan_columns]
    end

    subgraph New: Extended UI in app.py
        J[Supplier Settings widgets]
        K[Quota Status display]
    end

    D --> F
    E --> F
    E --> G
    F --> G
    G --> H
    H --> I
    I --> L[Extended plan_df + summary]
    J --> E
    L --> K
```

### Key Design Decision: Post-Solve Allocation

The supplier allocation is **deterministic given the weekly production plan**. For each week `t` with `y[t]` good units produced:

1. Determine which suppliers are available.
2. Apply the run-sequencing rule (Curium first, BWXT second, Curium third if overtime).
3. Verify that Curium allocation ≥ EU-restricted demand for that week.
4. Compute Sr-82 activity per supplier.
5. Accumulate activity per quarter for quota checking.

This is O(T) per plan — negligible compared to the DP solve.

### Infeasibility Detection

The only scenario where the post-solve approach fails is when:
- Curium is unavailable in a week **and** that week has non-zero EU-restricted demand **and** there is no way to rearrange production to cover it.

Since the DP solver doesn't know about supplier availability, the design adds a **pre-solve feasibility check**: if any week has EU-restricted demand > 0 and Curium is unavailable, the system either (a) converts that week to a shutdown week and reruns the solver, or (b) reports infeasibility with explanation.

## Data Models

### `SupplierParams` Dataclass

```python
@dataclass(frozen=True)
class SupplierParams:
    """All supplier-related configuration."""

    # Sr-82 formula parameters
    per_generator_mci: float = 100.0       # mCi per good generator
    per_batch_mci: float = 10.0            # mCi per batch (QC generator)
    minimum_surplus_mci: float = 20.0      # Floor on surplus term
    curium_surplus_pct: float = 0.05       # 5% surplus for Curium
    bwxt_surplus_pct: float = 0.02         # 2% surplus for BWXT

    # Run sequencing
    first_run_allocation: int = 15         # Generators in first Curium run of a split week

    # Quarterly quota
    curium_quarterly_quota_mci: float = 10000.0
    bwxt_quarterly_quota_mci: float = 10000.0
    quota_shortfall_penalty_rate: float = 50000.0  # USD per mCi shortfall (high default)
    w_quota: float = 1.0                   # Weight for quota shortfall in composite

    # Quarter boundaries
    quarter_start_month: int = 1           # 1=January, 4=April, etc.

    # Availability: weeks where a supplier is unavailable
    curium_unavailable_weeks: tuple[int, ...] = ()
    bwxt_unavailable_weeks: tuple[int, ...] = ()
```

### `WeeklySupplierAllocation` (per-week output)

```python
@dataclass
class WeeklySupplierAllocation:
    week: int
    total_good: int              # y[t] from the DP plan
    curium_good: int             # Good units from Curium runs
    bwxt_good: int               # Good units from BWXT runs
    run_sequence: list[str]      # e.g. ["Curium", "BWXT", "Curium"]
    curium_activity_mci: float   # Sr-82 mCi ordered from Curium
    bwxt_activity_mci: float     # Sr-82 mCi ordered from BWXT
    total_activity_mci: float    # Sum of both
    supplier_label: str          # "Curium", "BWXT", "Curium / BWXT", or ""
    eu_restricted_demand: int    # EU-restricted customer demand this week
    eu_constraint_satisfied: bool
```

### `QuarterlyQuotaStatus` (per-supplier per-quarter)

```python
@dataclass
class QuarterlyQuotaStatus:
    supplier: str           # "Curium" or "BWXT"
    quarter: int            # 1, 2, 3, or 4
    quarter_weeks: range    # Week range for this quarter
    quota_mci: float        # The minimum quota
    ordered_mci: float      # Actual mCi ordered in this quarter
    remaining_mci: float    # quota - ordered (negative = exceeded)
    shortfall_mci: float    # max(0, quota - ordered)
    penalty_usd: float      # shortfall * rate
```

## Component Interfaces

### Module: `supplier_allocation.py`

#### `allocate_suppliers_weekly(y_plan, eu_demand, params, supplier_params, reference_week) → list[WeeklySupplierAllocation]`

Allocates each week's production to suppliers based on availability and run-sequencing rules.

**Algorithm:**

```python
for t in 1..T:
    y = y_plan[t]
    if y == 0:
        allocation = (0, 0), sequence = []
        continue

    curium_avail = t not in supplier_params.curium_unavailable_weeks
    bwxt_avail = t not in supplier_params.bwxt_unavailable_weeks

    if not curium_avail and not bwxt_avail:
        # Should have been caught by pre-solve check
        raise InfeasibleAllocationError(t)

    batches = batches_needed(y, params)

    if not curium_avail:
        # All BWXT
        if eu_demand[t] > 0:
            raise InfeasibleAllocationError(t, "EU demand but Curium unavailable")
        curium_good, bwxt_good = 0, y
        sequence = ["BWXT"] * batches

    elif not bwxt_avail:
        # All Curium
        curium_good, bwxt_good = y, 0
        sequence = ["Curium"] * batches

    elif y <= supplier_params.first_run_allocation:
        # Single-supplier week: all Curium (fits in one run)
        curium_good, bwxt_good = y, 0
        sequence = ["Curium"]

    else:
        # Split week: apply run-sequencing rules
        first_run = supplier_params.first_run_allocation  # 15
        remaining = y - first_run

        if batches == 2:
            # Curium, BWXT
            curium_good = first_run
            bwxt_good = remaining
            sequence = ["Curium", "BWXT"]
        elif batches == 3:
            # Curium, BWXT, Curium (ONLY allowed pattern)
            # BWXT gets the second run (up to 15 good)
            bwxt_good = min(remaining, params.max_good_per_batch)
            curium_good = y - bwxt_good  # first + third run
            sequence = ["Curium", "BWXT", "Curium"]
        else:
            # batches == 1 but y > first_run_allocation shouldn't happen
            # since first_run_allocation defaults to max_good_per_batch
            curium_good, bwxt_good = y, 0
            sequence = ["Curium"]

    # Verify EU constraint
    eu_constraint_ok = curium_good >= eu_demand[t]
```

**Complexity:** O(T) — one pass over 52 weeks.

#### `compute_activity(generators: int, surplus_pct: float, params: SupplierParams) → float`

```python
def compute_activity(generators: int, surplus_pct: float, params: SupplierParams) -> float:
    if generators == 0:
        return 0.0
    batch_count = math.ceil(generators / 15)  # or use batches_needed()
    base = params.per_generator_mci * generators + params.per_batch_mci * batch_count
    surplus = max(math.ceil(surplus_pct * base), params.minimum_surplus_mci)
    return base + surplus
```

#### `compute_quarter_boundaries(horizon_weeks, quarter_start_month, reference_week_date) → list[range]`

Maps each week number to a quarter based on the reference date and configured quarter start month.

```python
def compute_quarter_boundaries(
    horizon_weeks: int,
    quarter_start_month: int,
    reference_week_date: date | None,
) -> list[range]:
    """Return a list of 4 ranges, each covering the week numbers in that quarter.

    If reference_week_date is None, falls back to 13-week blocks.
    """
    if reference_week_date is None:
        # Fallback: 4 equal quarters of 13 weeks
        return [range(1, 14), range(14, 27), range(27, 40), range(40, 53)]

    # Map each week to a calendar date, determine its quarter
    quarter_months = [
        (quarter_start_month + 3*q - 1) % 12 + 1
        for q in range(4)
    ]
    # ... assign each week to the quarter whose start month ≤ week's month < next quarter's start
```

#### `check_quarterly_quota(allocations, supplier_params, quarter_boundaries) → list[QuarterlyQuotaStatus]`

Accumulates mCi per supplier per quarter, computes shortfall and penalty.

#### `build_supplier_plan_columns(allocations) → pd.DataFrame`

Produces the additional columns to merge into the existing `plan_df`.

### Pre-Solve Feasibility Check

#### `validate_supplier_feasibility(demand, eu_demand, shutdown_weeks, supplier_params) → list[str]`

Called before the DP solver. Returns errors if:
1. Any week has EU demand > 0 and Curium is unavailable.
2. Both suppliers are unavailable in a non-shutdown week that has non-zero demand.

If errors are found, the UI reports them and offers the planner options (e.g., marking those weeks as shutdowns).

## Integration with Existing Solver

The existing `solve_plan_integrated` function receives one minimal enhancement from the optimizer-enhancements feature: an optional `eu_demand` parameter for `row_cap` enforcement (see `.kiro/specs/optimizer-enhancements/design.md`, Feature 2). The supplier layer's `solve_with_suppliers` passes `eu_demand` through:

```python
def solve_with_suppliers(
    demand, shutdown_weeks, partial_shutdown_weeks,
    row_demand, row_cap, params,
    eu_demand, supplier_params, reference_week_date=None,
) -> tuple[pd.DataFrame, dict, list[QuarterlyQuotaStatus]]:
    """Run the DP solver, then apply supplier allocation and quota checking."""

    # Pre-solve check
    errors = validate_supplier_feasibility(demand, eu_demand, shutdown_weeks, supplier_params)
    if errors:
        raise InfeasibleAllocationError(errors)

    # Run existing solver (with row_cap enforcement via eu_demand)
    plan_df, summary = solve_plan_integrated(
        demand, shutdown_weeks, partial_shutdown_weeks,
        row_demand, row_cap, params,
        eu_demand=eu_demand,  # Passes through for inventory bound enforcement
    )

    # Post-solve: allocate suppliers
    y_plan = [0] + plan_df["Good_Production"].tolist()
    allocations = allocate_suppliers_weekly(y_plan, eu_demand, params, supplier_params, reference_week_date)

    # Compute activities
    for alloc in allocations:
        alloc.curium_activity_mci = compute_activity(
            alloc.curium_good, supplier_params.curium_surplus_pct, supplier_params)
        alloc.bwxt_activity_mci = compute_activity(
            alloc.bwxt_good, supplier_params.bwxt_surplus_pct, supplier_params)
        alloc.total_activity_mci = alloc.curium_activity_mci + alloc.bwxt_activity_mci

    # Check quotas
    quarter_bounds = compute_quarter_boundaries(
        params.horizon_weeks, supplier_params.quarter_start_month, reference_week_date)
    quota_status = check_quarterly_quota(allocations, supplier_params, quarter_bounds)

    # Compute total quota penalty
    total_quota_penalty = sum(qs.penalty_usd for qs in quota_status)
    summary["total_quota_penalty_cost"] = total_quota_penalty
    summary["total_composite_cost"] += supplier_params.w_quota * total_quota_penalty

    # Merge supplier columns into plan_df
    supplier_cols_df = build_supplier_plan_columns(allocations)
    plan_df = pd.concat([plan_df, supplier_cols_df], axis=1)

    return plan_df, summary, quota_status
```

## Allocation Logic for the Three-Run Week

The only non-trivial allocation is when `batches == 3` (overtime week, 31–45 good units):

| Run | Supplier | Good units |
|---|---|---|
| 1 | Curium | `first_run_allocation` (15) |
| 2 | BWXT | `min(remaining, max_good_per_batch)` = min(y-15, 15) |
| 3 | Curium | `y - first_run_allocation - bwxt_good` |

Examples:
- y=31: Run1=Curium(15), Run2=BWXT(15), Run3=Curium(1). Curium total=16, BWXT=15.
- y=35: Run1=Curium(15), Run2=BWXT(15), Run3=Curium(5). Curium total=20, BWXT=15.
- y=45: Run1=Curium(15), Run2=BWXT(15), Run3=Curium(15). Curium total=30, BWXT=15.

In all three-run cases, BWXT gets exactly one batch (up to 15 gens) and Curium gets two batches. This satisfies the Curium→BWXT→Curium rule and ensures EU demand (max 4/week) is always covered by Curium's 16–30 generators.

## EU Constraint Verification

Given the allocation logic:
- Single-supplier Curium week: Curium gets all y ≥ 1. EU demand satisfied trivially.
- Two-run split: Curium gets 15. EU demand max is 4. Satisfied.
- Three-run split: Curium gets 16–30. EU demand max is 4. Satisfied.
- BWXT-only week: Curium = 0. EU demand must be 0 — enforced by pre-solve check.

Therefore the EU constraint is satisfied **by construction** for any feasible plan, as long as the pre-solve check ensures Curium is available in every week with EU demand. No solver modification needed.

## Partial Quarters

A 52-week horizon only aligns with whole calendar quarters when it begins on a
quarter boundary. Otherwise the first and last quarters are partially covered.

`compute_quarter_boundaries` returns `QuarterSpan` objects carrying `weeks`,
`expected_weeks` (the size of the real calendar quarter, obtained by extending the
weekly grid past both ends of the horizon) and `is_partial`. Only the first and
last spans can be partial — interior quarters are necessarily complete because
planning weeks run continuously.

`check_quarterly_quota` treats a partial quarter as **not judgeable**:

| | Full quarter | Partial quarter |
|---|---|---|
| `shortfall_mci` | `max(0, quota − ordered)` | `0` |
| `penalty_usd` | `shortfall × rate` | `0` |
| `prorated_quota_mci` | = quota | `quota × coverage` |
| `prorated_shortfall_mci` | = shortfall | run-rate gap (display only) |
| `status` | `OK` / `SHORTFALL` | `Partial — not penalised` |

**Rationale.** The leading partial quarter is missing weeks in the past, whose
orders are already placed and are not visible to the planner; the trailing one is
missing weeks beyond the horizon where ordering continues. Charging a full quota
against a fragment fabricates a shortfall. Because the shortfall rate is
deliberately punitive, that phantom figure would dominate the objective and steer
production decisions for a reason that does not exist — observed in practice as a
$499.7M penalty on an otherwise $4.33M plan.

Excluding them from cost while still reporting them keeps the objective sound and
the planner informed. `partial_quarter_note()` produces the explanatory text shown
in the UI and recorded in `summary["partial_quarter_note"]`.

## Quota Penalty as Soft Constraint

The quota shortfall penalty rate defaults to 50,000 USD/mCi. At a minimum quota of 10,000 mCi, a complete quarterly shortfall would cost $500M — astronomically higher than any penalty/overtime cost (~$4M total). This makes quota violation effectively unacceptable without using a hard constraint that would require modifying the solver.

If the planner sets a lower penalty rate to allow some shortfall tolerance, the marginal quota cost is still additive and transparent in the summary.

## New Output Columns

Added to `Weekly_Plan` sheet:

| Column | Type | Description |
|---|---|---|
| Curium_Good | int | Good units from Curium this week |
| BWXT_Good | int | Good units from BWXT this week |
| Run_Sequence | str | e.g. "Curium, BWXT, Curium" |
| Supplier_Label | str | "Curium", "BWXT", "Curium / BWXT", "" |
| Curium_Activity_mCi | float | Sr-82 activity ordered from Curium |
| BWXT_Activity_mCi | float | Sr-82 activity ordered from BWXT |
| Total_Sr82_mCi | float | Sum of both activities |
| EU_Restricted_Demand | int | EU-restricted demand this week |

New export sheet: `Quota_Status` with columns: Supplier, Quarter, Quota_mCi, Ordered_mCi, Remaining_mCi, Shortfall_mCi, Penalty_USD.

## UI Integration

The existing Settings tab gains a "Supplier Parameters" section:

| Widget | Parameter | Default |
|---|---|---|
| Number input | Curium surplus % | 5.0 |
| Number input | BWXT surplus % | 2.0 |
| Number input | Min surplus (mCi) | 20 |
| Number input | Per-generator mCi | 100 |
| Number input | Per-batch mCi | 10 |
| Number input | First run allocation | 15 |
| Number input | Curium quarterly quota (mCi) | 10000 |
| Number input | BWXT quarterly quota (mCi) | 10000 |
| Number input | Shortfall penalty (USD/mCi) | 50000 |
| Slider | w_quota weight | 1.0 |
| Number input | Quarter start month | 1 |
| Text input | Curium unavailable weeks | (comma-separated) |
| Text input | BWXT unavailable weeks | (comma-separated) |

The Results section gains:
- A "Quota Status" expander showing the quarterly status table.
- The summary metrics row adds "Quota Penalty Cost (USD)".

## Onboarding Recommendation Integration

The optimizer-enhancements design replaces the existing single-customer `evaluate_candidate()` with `evaluate_multi_customer()` as the primary entry point (see `.kiro/specs/optimizer-enhancements/design.md`, Feature 4). That function internally calls `solve_with_suppliers` from this module, so that:
- Each candidate's marginal cost includes any marginal quota shortfall.
- If a new EU-restricted customer would create infeasibility (Curium unavailable in the candidate week), that candidate is excluded with explanation.

The call chain is:
```
evaluate_multi_customer()
  → for each combination:
      → solve_with_suppliers(demand_with_new_sites, ..., eu_demand_with_new_sites, supplier_params)
          → solve_plan_integrated(... eu_demand=eu_demand)  [DP + row_cap]
          → allocate_suppliers_weekly(...)                  [post-solve allocation]
          → check_quarterly_quota(...)                     [quota penalty]
```

Single-customer onboarding is the N=1 special case of `evaluate_multi_customer`.

## Testing Strategy

### Unit Tests (`tests/test_supplier_allocation.py`)

1. **Activity formula** — verify all 6 reference weeks exactly.
2. **Allocation logic** — test each scenario:
   - 0 production → empty allocation.
   - y ≤ 15, both available → single Curium.
   - y = 20, both available → split Curium(15) + BWXT(5).
   - y = 45, both available → Curium(15) + BWXT(15) + Curium(15).
   - Curium unavailable, no EU demand → all BWXT.
   - Curium unavailable, EU demand > 0 → InfeasibleAllocationError.
   - BWXT unavailable → all Curium.
3. **Quarter boundaries** — with and without reference date; verify week-to-quarter mapping.
4. **Quota check** — shortfall calculation, penalty computation, zero shortfall case.
5. **Integration** — `solve_with_suppliers` on the real dataset; verify summary includes quota penalty, plan_df has new columns, quota_status is non-empty.

### Property Tests (`tests/test_supplier_properties.py`)

1. **Allocation sums to y**: For any valid y, curium_good + bwxt_good == y.
2. **Run sequence length == batches_needed(y)**: Always.
3. **Curium first**: If sequence is non-empty and len > 1, sequence[0] == "Curium".
4. **Three-run pattern**: If len(sequence) == 3, sequence == ["Curium", "BWXT", "Curium"].
5. **EU constraint by construction**: curium_good >= eu_demand[t] for all t where Curium is available.
6. **Activity formula monotonic**: More generators → more activity.
7. **Activity zero when generators zero**: Always.
8. **Quota shortfall non-negative**: Always >= 0.
9. **Penalty proportional to shortfall**: penalty == shortfall * rate.
10. **No penalty when quota met**: If ordered >= quota, penalty == 0.

## Correctness Properties

| # | Property | Validates |
|---|---|---|
| 1 | For all weeks, curium_good + bwxt_good == y_plan[t] | Req 3.1 |
| 2 | For all split weeks, run_sequence[0] == "Curium" | Req 4.2 |
| 3 | For all 3-run weeks, sequence == ["Curium","BWXT","Curium"] | Req 4.6 |
| 4 | For all weeks where Curium unavailable, curium_good == 0 | Req 3.3 |
| 5 | For all weeks, curium_good >= eu_demand[t] (when Curium available) | Req 5.3 |
| 6 | Activity formula reproduces 6 reference values | Req 2.7 |
| 7 | Quarterly shortfall == max(0, quota - ordered) | Req 6.4 |
| 8 | No penalty when ordered >= quota | Req 6.6 |
| 9 | Total quota penalty appears in summary composite cost | Req 6.8 |
| 10 | All existing plan_df columns preserved | Req 8.5 |

## File Structure (mapped to layers)

Per `.kiro/specs/ARCHITECTURE.md`, the supplier logic is placed by layer:

```
domain/supplier_allocation.py     # allocate_suppliers_weekly, compute_activity, run-sequencing
domain/quota.py                   # compute_quarter_boundaries, check_quarterly_quota
domain/params.py                  # SupplierParams dataclass (added alongside IntegratedParams)
domain/errors.py                  # InfeasibleAllocationError (added)
services/optimizer_service.py     # calls solve_with_suppliers; wires domain + exporter
io/workbook_exporter.py           # adds Quota_Status sheet
ui/tab_settings.py                # supplier parameter widgets
tests/domain/test_supplier_allocation.py   # pure unit tests
tests/domain/test_supplier_properties.py   # property tests
```

The pure DP solver in `domain/solver.py` receives only the minimal `eu_demand` enhancement (shared with the optimizer-enhancements feature) — its core logic is otherwise unchanged, preserving existing test coverage. During the incremental migration, `integrated_cost_optimizer.py` remains as a re-export shim so existing imports keep working.

`solve_with_suppliers` itself is a thin orchestration helper. It may live in `domain/supplier_allocation.py` (pure, since it only composes other pure functions) and is invoked by the optimizer service.
