"""Presentation: session-state keys and helpers.

Centralises the ``st.session_state`` keys so tabs share data without magic
strings scattered through the UI. Settings are per-session (Requirement E-7.13):
the planner adjusts values before each run; nothing is persisted to disk.
"""

from __future__ import annotations

from typing import Any

# --- Settings (raw widget values; validated by services.settings_service) ---
SETTINGS_PREFIX = "cfg_"

# --- Uploaded files ---
SITES_BYTES = "sites_bytes"
SITES_NAME = "sites_name"
SITES_SHEETS = "sites_sheet_names"
SITES_SHEET = "sites_sheet"
MP_BYTES = "mp_bytes"
MP_NAME = "mp_name"
MP_SHEETS = "mp_sheet_names"
MP_SHEET = "mp_sheet"

# --- Results ---
OPT_RESULT = "opt_result"
CMP_RESULT = "cmp_result"
OB_RESULT = "ob_result"
OB_CUSTOMERS = "ob_customers"
OB_SELECTION = "ob_selection"
OB_GENERATED = "ob_generated_file"

ALL_KEYS = [
    SITES_BYTES, SITES_NAME, SITES_SHEETS, SITES_SHEET,
    MP_BYTES, MP_NAME, MP_SHEETS, MP_SHEET,
    OPT_RESULT, CMP_RESULT, OB_RESULT, OB_CUSTOMERS, OB_SELECTION, OB_GENERATED,
]


def init_state(session: Any) -> None:
    """Ensure every shared key exists so tabs can read them unconditionally."""
    for key in ALL_KEYS:
        if key not in session:
            session[key] = None


def cfg_key(name: str) -> str:
    """Namespaced session key for a settings field."""
    return f"{SETTINGS_PREFIX}{name}"


def raw_settings(session: Any, defaults: dict) -> dict:
    """Collect raw settings values from session state, falling back to defaults."""
    return {
        name: session.get(cfg_key(name), default)
        for name, default in defaults.items()
    }


def clear_results(session: Any) -> None:
    """Drop derived results — called when inputs or settings change."""
    for key in (OPT_RESULT, CMP_RESULT, OB_RESULT, OB_SELECTION, OB_GENERATED):
        session[key] = None
