"""Pytest configuration: install a Streamlit stub before any test module is
imported so that `import app` and `import ui.*` never touch the real Streamlit
runtime.
"""
import sys
import types
from unittest.mock import MagicMock

# Widgets that simply return a MagicMock are fine.
_PASSTHROUGH_ATTRS = [
    "set_page_config", "title", "caption", "subheader", "header", "info",
    "success", "error", "warning", "write", "markdown", "divider",
    "file_uploader", "selectbox", "multiselect", "text_input", "date_input",
    "spinner", "stop", "metric", "dataframe", "download_button", "expander",
    "data_editor", "line_chart", "bar_chart", "table", "json", "code",
    "radio", "form", "form_submit_button", "toggle", "container", "empty",
    "sidebar", "rerun", "cache_data", "cache_resource",
]


def _install_streamlit_stub() -> None:
    """Replace the real streamlit module with a lightweight mock."""
    _st = types.ModuleType("streamlit")

    for _attr in _PASSTHROUGH_ATTRS:
        setattr(_st, _attr, MagicMock(return_value=MagicMock()))

    def _context_mock():
        obj = MagicMock()
        obj.__enter__ = MagicMock(return_value=obj)
        obj.__exit__ = MagicMock(return_value=False)
        return obj

    def _tabs(labels, **kwargs):
        return [_context_mock() for _ in labels]

    def _columns(spec, **kwargs):
        count = spec if isinstance(spec, int) else len(spec)
        return [_context_mock() for _ in range(count)]

    def _expander(*args, **kwargs):
        return _context_mock()

    def _spinner(*args, **kwargs):
        return _context_mock()

    _st.tabs = _tabs
    _st.columns = _columns
    _st.expander = _expander
    _st.spinner = _spinner

    # Buttons return False so run blocks do not execute at import time.
    _st.button = MagicMock(return_value=False)
    _st.checkbox = MagicMock(return_value=False)
    _st.number_input = MagicMock(return_value=1.0)
    _st.slider = MagicMock(return_value=1.0)

    # Progress objects expose .progress()
    def _progress(*args, **kwargs):
        bar = MagicMock()
        bar.progress = MagicMock()
        return bar

    _st.progress = _progress

    # column_config namespace used by data_editor / dataframe
    _column_config = types.SimpleNamespace(
        TextColumn=MagicMock(return_value=MagicMock()),
        NumberColumn=MagicMock(return_value=MagicMock()),
        CheckboxColumn=MagicMock(return_value=MagicMock()),
        DateColumn=MagicMock(return_value=MagicMock()),
        SelectboxColumn=MagicMock(return_value=MagicMock()),
    )
    _st.column_config = _column_config

    _st.session_state = {}

    # Replace any already-registered streamlit modules.
    for key in list(sys.modules.keys()):
        if key == "streamlit" or key.startswith("streamlit."):
            sys.modules[key] = _st
    sys.modules["streamlit"] = _st


_install_streamlit_stub()


# ---------------------------------------------------------------------------
# Synthetic sites file
# ---------------------------------------------------------------------------
# The suite does not ship a sites file built from real customer records. Tests
# that need a realistic multi-site input use this generated one instead.

import pytest

from tests.fixtures import write_sites_csv


@pytest.fixture(scope="session")
def sites_csv(tmp_path_factory) -> str:
    """Path to a generated synthetic sites CSV, shared across the session."""
    directory = tmp_path_factory.mktemp("sites")
    return write_sites_csv(directory / "sites_synthetic.csv")
