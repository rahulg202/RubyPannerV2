# Implementation Tasks — Ruby Fill Optimizer Business Enhancements

> **This feature is built as part of a single unified solution.** The six enhancements do not ship separately — they are one build together with the supplier-constraints feature and the layered refactor, converging on one unified Streamlit UI and one export.
>
> The full, ordered task plan lives in **[`.kiro/specs/tasks.md`](../tasks.md)**.

## Where the enhancement tasks are

In the unified plan, the six enhancements map to these phases (requirement prefix **[E-n.m]**):

| Enhancement | Phase(s) |
|---|---|
| Foundation / layered refactor | Phases 0–2 |
| Full parameter configurability [E-7.*] | Phase 3 (settings service), Phase 13.1 (one Settings tab) |
| Reference week + calibration dates [E-6.*] | Phase 4 (domain), Phase 13 (UI columns) |
| row_cap enforcement [E-7.4] | Phase 5 |
| Highlight changed customer weeks [E-3.*] | Phase 7 (delivery assignment), Phase 12 (export), Phase 13.2 (UI) |
| Manual-plan cost comparison [E-1.*] | Phase 8 (parser + comparison), Phase 11.2 (service), Phase 13.4 (Comparison tab) |
| Master Planner input contract [E-2.*] | Phase 2.2, Phase 8.1 |
| Multiple customers in onboarding [E-4.*] | Phase 9, Phase 13.3 |
| Generate optimizer input file [E-5.*] | Phase 10, Phase 13.3 |
| Backward compatibility [E-8.*] | Phases 1–2 (shims), Phase 14.3 |

See the unified plan for the exact task checkboxes, ordering, and checkpoints.
