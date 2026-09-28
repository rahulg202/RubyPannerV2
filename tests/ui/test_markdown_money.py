"""Money in Streamlit markdown must not be eaten by LaTeX.

Streamlit renders markdown with LaTeX support, so a *pair* of dollar signs on one
line is read as a maths expression. This line:

    - $225,000 — 15 normal slots left empty elsewhere, at $15,000 each

rendered as monospace maths with **both dollar signs consumed as delimiters**,
while the line above it — carrying only one amount — looked fine. So the bug only
appears on lines quoting two figures, which is exactly the kind of thing that
survives a casual look at the screen.

``st.metric`` is not markdown-rendered, so plain ``usd`` is correct there. Anything
passed to ``st.markdown`` / ``st.info`` / ``st.caption`` / ``st.warning`` /
``st.success`` / ``st.error`` must use the escaped variants.

This test reads the UI source rather than rendering, because the Streamlit stub in
``tests/conftest.py`` accepts anything and would happily swallow the bug — the
same blind spot that once let 574 tests pass against an app that crashed on load.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from ui.formatting import escape_dollars, usd, usd_md, usd_signed, usd_signed_md

UI_DIR = pathlib.Path(__file__).resolve().parents[2] / "ui"

# Streamlit calls whose text argument is rendered as markdown.
MARKDOWN_CALLS = {
    "markdown", "info", "caption", "warning", "success", "error", "write",
}

# Formatters that emit an unescaped "$".
RAW_MONEY = {"usd", "usd_signed"}


# ---------------------------------------------------------------------------
# The formatters themselves
# ---------------------------------------------------------------------------

def test_escaped_formatters_escape_the_dollar():
    assert usd_md(1500) == r"\$1,500"
    assert usd_signed_md(-2000) == r"-\$2,000"


def test_plain_formatters_are_left_alone_for_metrics():
    """st.metric is not markdown, so the plain versions must stay unescaped."""
    assert usd(1500) == "$1,500"
    assert usd_signed(-2000) == "-$2,000"


def test_escape_dollars_handles_multiple_amounts():
    text = "from $225,000 down to $15,000"
    assert escape_dollars(text) == r"from \$225,000 down to \$15,000"


def test_escaped_output_survives_a_two_amount_line():
    """The exact shape that broke: two amounts, one line."""
    line = f"- {usd_md(225_000)} at {usd_md(15_000)} each"
    assert line.count("$") == line.count(r"\$"), (
        f"unescaped dollar sign would start a LaTeX block: {line!r}"
    )


# ---------------------------------------------------------------------------
# The call sites
# ---------------------------------------------------------------------------

def _markdown_money_offences(path: pathlib.Path) -> list[str]:
    """Find raw money formatters used inside markdown-rendered Streamlit calls."""
    tree = ast.parse(path.read_text())
    offences: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr in MARKDOWN_CALLS):
            continue
        # Only st.* / column.* style calls carrying rendered text.
        for arg in node.args:
            for inner in ast.walk(arg):
                if (
                    isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Name)
                    and inner.func.id in RAW_MONEY
                ):
                    offences.append(
                        f"{path.name}:{inner.lineno} {func.attr}() uses "
                        f"{inner.func.id}() — use {inner.func.id}_md()"
                    )

    return offences


@pytest.mark.parametrize(
    "path", sorted(UI_DIR.glob("*.py")), ids=lambda p: p.name
)
def test_no_raw_money_formatter_inside_markdown(path):
    offences = _markdown_money_offences(path)
    assert offences == [], "\n".join(offences)


def _bare_dollar_offences(path: pathlib.Path) -> list[str]:
    """Find markdown calls whose literal text carries two or more bare '$'."""
    tree = ast.parse(path.read_text())
    offences: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr in MARKDOWN_CALLS):
            continue

        # Concatenate every string literal in the call's arguments; an f-string's
        # literal parts land here too, which is where the original bug lived.
        text = ""
        for arg in node.args:
            for inner in ast.walk(arg):
                if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
                    text += inner.value

        bare = text.count("$") - text.count("\\$")
        if bare >= 2:
            offences.append(
                f"{path.name}:{node.lineno} {func.attr}() has {bare} unescaped '$' "
                f"in its text — Streamlit will read the pair as LaTeX"
            )
    return offences


@pytest.mark.parametrize(
    "path", sorted(UI_DIR.glob("*.py")), ids=lambda p: p.name
)
def test_no_paired_bare_dollars_in_markdown_text(path):
    offences = _bare_dollar_offences(path)
    assert offences == [], "\n".join(offences)


# ---------------------------------------------------------------------------
# Guard the guards: both detectors must fire on the code as it actually was
# ---------------------------------------------------------------------------

BROKEN_SOURCE = '''
import streamlit as st
from ui.formatting import usd

def render(total, overtime_rate, surcharge, capacity_rate, units):
    st.info(
        f"**One full overtime week really costs ${total:,.0f}.**\\n\\n"
        f"- ${overtime_rate:,.0f} — the overtime rate above\\n"
        f"- ${surcharge:,.0f} — {units} normal slots left empty elsewhere "
        f"in the year, at ${capacity_rate:,.0f} each\\n\\n"
    )
    st.caption(f"It is paying {usd(total)} in holding cost.")
    st.metric("Fine here", usd(total))
'''


def test_bare_dollar_detector_fires_on_the_original_bug(tmp_path):
    broken = tmp_path / "broken_tab.py"
    broken.write_text(BROKEN_SOURCE)
    offences = _bare_dollar_offences(broken)
    assert offences, "the detector missed the very bug it was written for"
    assert "info()" in offences[0]


def test_raw_formatter_detector_fires_on_markdown_usage(tmp_path):
    broken = tmp_path / "broken_tab.py"
    broken.write_text(BROKEN_SOURCE)
    offences = _markdown_money_offences(broken)
    assert len(offences) == 1, offences
    assert "caption()" in offences[0]
    assert "usd_md()" in offences[0]


def test_metric_usage_is_not_flagged(tmp_path):
    """st.metric is not markdown-rendered, so plain usd() there is correct."""
    ok = tmp_path / "ok_tab.py"
    ok.write_text(
        "import streamlit as st\n"
        "from ui.formatting import usd\n"
        "def render(v):\n"
        "    st.metric('Total', usd(v))\n"
    )
    assert _markdown_money_offences(ok) == []
    assert _bare_dollar_offences(ok) == []
