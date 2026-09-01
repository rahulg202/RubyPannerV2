# Archived material

Nothing in here describes the current application. It is kept for history only.

| File | What it was | Superseded by |
|---|---|---|
| `guide_excel_model.md` | Guide to the original Excel workbook (`Production_Planner_PenaltyOnly_Max16.xlsx`), where the plan was built with formulas and a pasted-in Python result | [USER_GUIDE.md](../USER_GUIDE.md) |
| `production_planner_cli_guide.md` | Guide to the standalone command-line optimizer, before the Streamlit application existed | [USER_GUIDE.md](../USER_GUIDE.md) and [TOOL_LOGIC.md](../TOOL_LOGIC.md) |
| `production_planner_penalty_max16.py` | The original single-file optimizer | `domain/solver.py` |
| `run_onboarding_recommendation.py` | Early onboarding script | `domain/onboarding.py` + the Onboarding tab |
| `analyze_row_*.py`, `discrepancy_analysis.py`, `final_discrepancy_report.py`, `find_missing_row.py` | One-off analyses used while reconciling the Master Planner against the model | — |

Both archived guides describe business rules that have since changed. The two most
misleading: they predate the Curium/BWXT/BWXT run sequencing, and they describe
overtime as unpenalised. Read [TOOL_LOGIC.md](../TOOL_LOGIC.md) for the rules that
actually apply.
