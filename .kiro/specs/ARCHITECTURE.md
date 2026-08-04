# Shared Architecture — Ruby Fill Optimizer

This document defines the target layered architecture for the Ruby Fill Optimizer and the software-engineering practices that both the `supplier-constraints` and `optimizer-enhancements` features must follow. It is the reference both design documents point to for structure, so the two features grow into one coherent codebase rather than two bolt-ons.

## Why a layered architecture

The current code mixes concerns: `app.py` (993 lines) holds UI widgets, file parsing, business orchestration, and Excel writing all together; `integrated_cost_optimizer.py` mixes the pure DP solver with pandas I/O and CLI argument parsing. As we add six enhancements plus supplier constraints, that coupling would make the code untestable and fragile. A layered design isolates change: business rules stop depending on Streamlit, I/O stops depending on business rules, and each layer is testable in isolation.

## The Four Layers

```
┌──────────────────────────────────────────────────────────────┐
│  PRESENTATION LAYER            app.py, ui/                      │
│  Streamlit widgets, tabs, tables, download buttons.            │
│  NO business logic. NO file parsing. Only calls services.      │
└───────────────────────────┬──────────────────────────────────┘
                            │ calls, passing DTOs
┌───────────────────────────▼──────────────────────────────────┐
│  APPLICATION / SERVICE LAYER   services/                       │
│  Use-case orchestration. One service per user workflow.        │
│  OptimizerService, OnboardingService, ComparisonService.       │
│  Coordinates domain + adapters. Stateless. Returns DTOs.       │
└──────────────┬───────────────────────────────┬───────────────┘
              │ pure calls                     │ port interfaces
┌──────────────▼──────────────┐   ┌────────────▼──────────────────┐
│  DOMAIN LAYER   domain/      │   │  ADAPTER / INFRA LAYER  io/    │
│  Pure business logic.        │   │  All file I/O and formatting.  │
│  DP solver, cost model,      │   │  Excel/CSV readers, Master     │
│  supplier allocation,        │   │  Planner parser, workbook      │
│  delivery assignment,        │   │  exporter, input-file writer.  │
│  activity formula, quota,    │   │  Isolates pandas/openpyxl.     │
│  date math. No I/O, no UI,   │   │  Implements ports the services │
│  no pandas where avoidable.  │   │  depend on.                    │
└──────────────────────────────┘   └────────────────────────────────┘
```

### Dependency rule

Dependencies point **inward and downward only**:
- Presentation → Application → Domain.
- Presentation → Application → Adapters (via port interfaces).
- Domain depends on **nothing** above it and no I/O library.
- Adapters depend on Domain types (DTOs) but never on services or UI.

The Domain layer never imports Streamlit, openpyxl, or argparse. It may use `pandas` only for the existing plan DataFrame construction, and even there we prefer plain dataclasses/lists at layer boundaries.

## Layer Contents

### Domain Layer (`domain/`)

Pure, deterministic, side-effect-free business logic. Everything here is unit-testable without files or a UI.

| Module | Responsibility | Source |
|---|---|---|
| `domain/params.py` | `IntegratedParams`, `SupplierParams` dataclasses (frozen, validated) | existing + supplier spec |
| `domain/cost_model.py` | `compute_weekly_cost`, cost decomposition | extracted from `integrated_cost_optimizer.py` |
| `domain/solver.py` | `solve_plan_integrated`, `compute_inventory_bounds` (+ `eu_demand` row_cap enforcement) | extracted |
| `domain/demand.py` | `build_weekly_demand`, `build_weekly_row_demand`, batch utilities | extracted |
| `domain/supplier_allocation.py` | `allocate_suppliers_weekly`, `compute_activity`, run-sequencing | supplier spec |
| `domain/quota.py` | `compute_quarter_boundaries`, `check_quarterly_quota` | supplier spec |
| `domain/delivery_assignment.py` | `assign_deliveries` (unit→customer mapping) | enhancements spec |
| `domain/comparison.py` | `compute_baseline_cost`, `compare_plans` | enhancements spec |
| `domain/dates.py` | `derive_week_dates`, `current_planning_week` | enhancements spec |
| `domain/errors.py` | Typed domain exceptions (`InfeasiblePlanError`, `InfeasibleAllocationError`, `ValidationError`) | new |

### Application / Service Layer (`services/`)

Each service represents one user-facing workflow. Services orchestrate: they call adapters to load data, call domain functions to compute, and return DTOs. They hold no UI code and no parsing logic. They are the **only** layer that both domain and adapters are wired together in.

| Service | Workflow | Composes |
|---|---|---|
| `services/optimizer_service.py` | Run the full optimization | reader port → clean → solve → allocate → assign → export DTO |
| `services/onboarding_service.py` | Multi-customer onboarding recommendation | reader → `evaluate_multi_customer` → input-file generator |
| `services/comparison_service.py` | Manual vs optimized comparison | Master Planner parser → baseline cost → compare |
| `services/settings_service.py` | Assemble validated params from raw UI values | builds `IntegratedParams` + `SupplierParams` |

Services depend on **ports** (abstract interfaces) for I/O, not concrete adapters — see Dependency Inversion below.

### Adapter / Infrastructure Layer (`io_adapters/`)

> **Naming note:** this package is `io_adapters/`, not `io/`. A top-level package named `io` would shadow Python's standard-library `io` module (used throughout the codebase via `io.BytesIO`), breaking imports. All references to "the `io/` layer" in these documents mean `io_adapters/`.

All reading, writing, and format-specific code. This is the only layer allowed to import `openpyxl` and to touch the filesystem or byte streams. It implements the port interfaces the services declare.

| Module | Responsibility |
|---|---|
| `io/sites_reader.py` | Read + normalize the sites input file (xlsx/csv), implement `SitesReaderPort` |
| `io/master_planner_parser.py` | Parse the wide Master Planner workbook, assign stable IDs |
| `io/workbook_exporter.py` | Write the multi-sheet result workbook, apply highlight formatting |
| `io/input_file_writer.py` | Generate the optimizer-ready input file including new customers |

### Presentation Layer (`app.py`, optionally `ui/`)

Streamlit only. Reads widget values, calls a service, renders the returned DTO. If `app.py` grows too large it splits into `ui/tab_optimizer.py`, `ui/tab_onboarding.py`, `ui/tab_comparison.py`, `ui/tab_settings.py`, each a render function taking a service and session state.

## Dependency Inversion (Ports & Adapters)

Services must not import concrete I/O modules directly; they depend on small abstract interfaces (ports) defined near the service layer. Adapters implement them. This keeps services testable with in-memory fakes and lets file formats change without touching business logic.

```python
# services/ports.py
from typing import Protocol
import pandas as pd

class SitesReaderPort(Protocol):
    def read(self, source: bytes, sheet: str | None) -> pd.DataFrame: ...

class MasterPlannerReaderPort(Protocol):
    def parse(self, source: bytes, sheet: str) -> "MasterPlannerData": ...

class ResultExporterPort(Protocol):
    def export(self, result: "OptimizationResult") -> bytes: ...
```

```python
# services/optimizer_service.py
class OptimizerService:
    def __init__(
        self,
        sites_reader: SitesReaderPort,
        exporter: ResultExporterPort,
    ) -> None:
        self._sites_reader = sites_reader
        self._exporter = exporter

    def run(self, request: OptimizeRequest) -> OptimizationResult:
        raw = self._sites_reader.read(request.file_bytes, request.sheet)
        active, issues = clean_sites(raw, request.params)      # domain
        demand = build_weekly_demand(active, request.params)   # domain
        eu_demand = build_weekly_row_demand(active, request.params)
        plan_df, summary, quota = solve_with_suppliers(...)    # domain
        assignments = assign_deliveries(...)                   # domain
        return OptimizationResult(plan_df, summary, quota, assignments, issues)
```

The presentation layer wires concrete adapters into services once, at startup:

```python
# app.py
service = OptimizerService(
    sites_reader=ExcelSitesReader(),
    exporter=WorkbookExporter(),
)
```

## Data Transfer Objects (DTOs)

Layers communicate through explicit dataclasses, never through loosely-typed dicts passed across boundaries. Requests into services and results out of them are dataclasses:

```python
@dataclass(frozen=True)
class OptimizeRequest:
    file_bytes: bytes
    sheet: str
    params: IntegratedParams
    supplier_params: SupplierParams
    shutdown_weeks: tuple[int, ...]
    partial_shutdown_weeks: tuple[int, ...]
    reference_week_date: date | None

@dataclass
class OptimizationResult:
    plan_df: pd.DataFrame
    summary: dict
    quota_status: list[QuarterlyQuotaStatus]
    assignments: list[DeliveryRecord]
    issues_df: pd.DataFrame
    week_dates: list[tuple[int, date, date]]
```

The existing summary `dict` is retained for backward compatibility but new fields flow through typed DTOs.

## Software-Engineering Practices

1. **Single Responsibility** — one module, one reason to change. The DP solver does not parse Excel; the exporter does not compute costs.
2. **Pure domain functions** — deterministic, no I/O, no globals. Enables the property-based tests both specs rely on.
3. **Immutability at boundaries** — `frozen=True` dataclasses for parameters and requests; avoid mutating shared lists across layers.
4. **Dependency inversion** — services depend on port protocols, adapters implement them; concrete wiring happens only in the presentation layer.
5. **Explicit typed errors** — a small `domain/errors.py` hierarchy; services translate domain errors into user-facing messages; no bare `except Exception` swallowing.
6. **No business logic in the UI** — `app.py` only reads widgets, calls a service, renders a DTO.
7. **Backward compatibility** — the existing `integrated_cost_optimizer.py` public functions remain importable (re-exported from their new `domain/` homes) so current tests keep passing during the refactor.
8. **Configuration over hardcoding** — every cost, rate, weight, and constraint is a parameter (Requirement 7), assembled by `settings_service` and validated once.
9. **Type hints everywhere** — full annotations; the project already targets Python 3.11.
10. **Testability by layer** — domain: pure unit + property tests; services: tests with fake adapters; adapters: tests with small fixture files; presentation: thin, minimal logic to test.

## Migration Strategy (non-breaking)

The refactor is incremental and keeps every existing test green:

1. **Phase 0 — package skeleton.** Create `domain/`, `services/`, `io/` packages. Leave existing files in place.
2. **Phase 1 — extract domain.** Move pure functions from `integrated_cost_optimizer.py` into `domain/` modules. Keep `integrated_cost_optimizer.py` as a thin re-export shim (`from domain.solver import solve_plan_integrated`) so existing imports and tests are unaffected.
3. **Phase 2 — introduce adapters.** Move file reading/writing out of `app.py` and the optimizer into `io/`. Define ports.
4. **Phase 3 — introduce services.** Extract orchestration from `app.py` into services. `app.py` shrinks to widgets + service calls.
5. **Phase 4 — build new features inside the layers.** Supplier allocation, comparison, delivery assignment, multi-customer onboarding, and dates are all added as new domain/service/adapter modules, never back into the monoliths.

Each phase ends with the full test suite passing. The one-off analysis scripts and the legacy `production_planner_penalty_max16.py` are moved to a `legacy/` folder and excluded from the package.

## Target Package Layout

```
domain/
    __init__.py
    params.py            # IntegratedParams, SupplierParams
    cost_model.py
    solver.py            # solve_plan_integrated, compute_inventory_bounds
    demand.py
    supplier_allocation.py
    quota.py
    delivery_assignment.py
    comparison.py
    dates.py
    errors.py
services/
    __init__.py
    ports.py             # Protocol interfaces
    settings_service.py
    optimizer_service.py
    onboarding_service.py
    comparison_service.py
    dtos.py              # OptimizeRequest, OptimizationResult, etc.
io/
    __init__.py
    sites_reader.py
    master_planner_parser.py
    workbook_exporter.py
    input_file_writer.py
ui/                      # optional split of app.py
    tab_optimizer.py
    tab_onboarding.py
    tab_comparison.py
    tab_settings.py
app.py                   # entry point: wires adapters into services, renders tabs
integrated_cost_optimizer.py   # thin re-export shim (backward compat) + CLI main()
onboarding_recommendation.py   # thin re-export shim (backward compat)
legacy/                  # archived one-off scripts + old planner
tests/
    domain/              # pure unit + property tests
    services/            # tests with fake adapters
    io/                  # tests with fixture files
```

## How Each Feature Maps to the Layers

| Feature | Domain | Service | Adapter | Presentation |
|---|---|---|---|---|
| Supplier constraints | `supplier_allocation.py`, `quota.py` | `optimizer_service` wires it in | exporter adds Quota_Status sheet | Settings widgets, Quota Status table |
| Cost comparison | `comparison.py` | `comparison_service` | `master_planner_parser` | Comparison tab |
| Changed weeks | `delivery_assignment.py` | `optimizer_service` | exporter adds Changed_Weeks sheet + formatting | Changed-week table + filters |
| Multi-customer onboarding | `evaluate_multi_customer` (in onboarding domain) | `onboarding_service` | `input_file_writer` | Onboarding tab, multi-row input |
| Reference dates | `dates.py` | all services attach dates | exporter adds date columns | date columns in tables |
| row_cap enforcement | `solver.py` bounds change | `optimizer_service` passes `eu_demand` | — | Settings widget |
| Full configurability | `params.py` validation | `settings_service` | Model_Params sheet | Settings tab |

Both design documents describe *what* each component does; this document defines *where it lives* and *how the layers depend on each other*.
