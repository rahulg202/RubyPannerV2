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
SITES_FROM_PLANNER = "sites_from_planner"  # True when derived, not uploaded
# Identifies the file currently in each uploader. st.file_uploader returns its
# file on every rerun, so without this the app would re-read it and clear every
# result on each interaction.
SITES_FP = "sites_fingerprint"
MP_FP = "mp_fingerprint"
MP_BYTES = "mp_bytes"
MP_NAME = "mp_name"
MP_SHEETS = "mp_sheet_names"
MP_SHEET = "mp_sheet"

# --- Results ---
OPT_RESULT = "opt_result"
CMP_RESULT = "cmp_result"
OB_RESULT = "ob_result"
OB_CUSTOMERS = "ob_customers"      # seed handed to the editor; kept stable
OB_VIEW = "ob_customers_view"      # last resolved editor output; never fed back
OB_EDITOR_MODE = "ob_editor_mode"  # "weeks" or "dates", to detect a switch
OB_SELECTION = "ob_selection"
OB_GENERATED = "ob_generated_file"
CONV_RESULT = "conv_result"
CONV_ANNOTATED = "conv_annotated_planner"
SIDEBAR_FLASH = "sidebar_flash"    # message shown once after a rerun

ALL_KEYS = [
    SITES_BYTES, SITES_NAME, SITES_SHEETS, SITES_SHEET, SITES_FROM_PLANNER,
    SITES_FP, MP_BYTES, MP_NAME, MP_SHEETS, MP_SHEET, MP_FP,
    OPT_RESULT, CMP_RESULT, OB_RESULT, OB_CUSTOMERS, OB_VIEW, OB_EDITOR_MODE,
    OB_SELECTION, OB_GENERATED, CONV_RESULT, CONV_ANNOTATED,
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
    for key in (OPT_RESULT, CMP_RESULT, OB_RESULT, OB_SELECTION, OB_GENERATED,
                CONV_RESULT, CONV_ANNOTATED):
        session[key] = None
