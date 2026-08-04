# Implementation Tasks — Raw Material Supplier Constraints

> **This feature is built as part of a single unified solution.** The supplier-constraints work does not ship on its own — it is one build together with the optimizer-enhancements feature and the layered refactor, converging on one unified Streamlit UI and one export.
>
> The full, ordered task plan lives in **[`.kiro/specs/tasks.md`](../tasks.md)**.

## Where the supplier-constraints tasks are

In the unified plan, supplier-constraints work is concentrated in these phases (requirement prefix **[S-n.m]**):

| Phase | Supplier-constraints content |
|---|---|
| Phase 3 | `SupplierParams` dataclass + validation; unified settings assembly [S-1.*], [S-6.10] |
| Phase 5 | `eu_demand` / row_cap enforcement in the solver (shared with enhancements) |
| Phase 6 | `domain/supplier_allocation.py`, `domain/quota.py`, `solve_with_suppliers`, tests [S-2.*]–[S-8.*] |
| Phase 9 | Onboarding with supplier constraints active [S-9.*] |
| Phase 12 | Quota_Status sheet + supplier columns in the unified export [S-7.5], [S-8.*] |
| Phase 13 | Supplier params in the one Settings tab; Quota Status display in the optimizer tab [S-7.1]–[S-7.4] |

See the unified plan for the exact task checkboxes, ordering, and checkpoints.
