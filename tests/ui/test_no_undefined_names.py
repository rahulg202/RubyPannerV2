"""Static guard: every name used in the source must be defined somewhere.

This exists because 906 tests passed against an app that crashed on load. Three
call sites were changed from ``usd(...)`` to ``usd_md(...)`` without adding the
import, and nothing caught it:

* the Streamlit stub in ``tests/conftest.py`` is a ``MagicMock`` that accepts
  anything, so it cannot fail;
* ``st.button`` returns ``False`` in the stub, so the blocks that render a result
  never execute — and that is where the broken names lived;
* importing a module only executes its top level, not its function bodies, so a
  missing import inside a function is invisible to an import-smoke test.

``HANDOFF.md`` already records this class of failure ("that is why 574 tests once
passed against a broken app"). A guard that needs no test author to remember it is
the only kind that helps.

The check is deliberately conservative: it gathers every name bound *anywhere* in
a module and flags loads of names that appear nowhere. It therefore will not catch
scope or ordering mistakes, only genuinely undefined names — which is the failure
that actually reached production here, and it produces no false positives.
"""

from __future__ import annotations

import ast
import builtins
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
PACKAGES = ("ui", "domain", "services", "io_adapters")

SOURCE_FILES = sorted(
    path
    for package in PACKAGES
    for path in (ROOT / package).glob("*.py")
) + [ROOT / "app.py"]

BUILTIN_NAMES = set(dir(builtins)) | {
    "__name__", "__file__", "__doc__", "__package__", "__spec__", "__loader__",
    "__builtins__", "__annotations__", "__debug__",
}


class _BoundNames(ast.NodeVisitor):
    """Collect every name the module binds, in any scope."""

    def __init__(self) -> None:
        self.bound: set[str] = set()

    # --- bindings -----------------------------------------------------
    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.bound.add(node.id)
        self.generic_visit(node)

    def visit_arg(self, node: ast.arg) -> None:
        self.bound.add(node.arg)
        self.generic_visit(node)

    def visit_FunctionDef(self, node) -> None:
        self.bound.add(node.name)
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.bound.add(node.name)
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.bound.add(alias.asname or alias.name.split(".")[0])
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            self.bound.add(alias.asname or alias.name)
        self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self.bound.add(node.name)
        self.generic_visit(node)

    def visit_Global(self, node: ast.Global) -> None:
        self.bound.update(node.names)
        self.generic_visit(node)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self.bound.update(node.names)
        self.generic_visit(node)

    def visit_MatchAs(self, node) -> None:       # pragma: no cover - py3.10+
        if getattr(node, "name", None):
            self.bound.add(node.name)
        self.generic_visit(node)

    def visit_MatchStar(self, node) -> None:     # pragma: no cover - py3.10+
        if getattr(node, "name", None):
            self.bound.add(node.name)
        self.generic_visit(node)


def _loaded_names(tree: ast.AST) -> list[tuple[str, int]]:
    """Every name read, with its line number."""
    return [
        (node.id, node.lineno)
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    ]


def undefined_names(source: str) -> list[str]:
    """Names read but never bound anywhere in the module."""
    tree = ast.parse(source)
    collector = _BoundNames()
    collector.visit(tree)
    known = collector.bound | BUILTIN_NAMES

    missing: dict[str, int] = {}
    for name, lineno in _loaded_names(tree):
        if name not in known and name not in missing:
            missing[name] = lineno
    return [f"line {lineno}: '{name}'" for name, lineno in sorted(
        missing.items(), key=lambda item: item[1]
    )]


@pytest.mark.parametrize(
    "path", SOURCE_FILES, ids=lambda p: f"{p.parent.name}/{p.name}"
)
def test_module_has_no_undefined_names(path: pathlib.Path):
    offences = undefined_names(path.read_text())
    assert offences == [], (
        f"{path.relative_to(ROOT)} reads names that are never defined or "
        f"imported:\n  " + "\n  ".join(offences)
    )


def test_source_file_list_is_not_empty():
    """Guard the guard: a bad glob must not make this pass vacuously."""
    assert len(SOURCE_FILES) > 30, f"only found {len(SOURCE_FILES)} source files"


# ---------------------------------------------------------------------------
# The detector must fire on the bug it was written for
# ---------------------------------------------------------------------------

MISSING_IMPORT = '''
from ui.formatting import usd

def render(value):
    return f"The {usd_md(value)} surcharge"
'''

CORRECT_IMPORT = '''
from ui.formatting import usd, usd_md

def render(value):
    return f"The {usd_md(value)} surcharge"
'''


def test_detector_catches_the_missing_import():
    offences = undefined_names(MISSING_IMPORT)
    assert len(offences) == 1
    assert "usd_md" in offences[0]


def test_detector_passes_once_the_import_is_added():
    assert undefined_names(CORRECT_IMPORT) == []


def test_detector_understands_comprehensions_and_walrus():
    """No false positives on the constructs this codebase actually uses."""
    source = '''
import math

def f(items, limit):
    squares = [x * x for x in items if x < limit]
    lookup = {k: v for k, v in zip(items, squares)}
    pairs = {(a, b) for a, b in lookup.items()}
    if (total := sum(squares)) > 0:
        return math.sqrt(total), pairs
    with open("f") as handle:
        data = handle.read()
    try:
        return data
    except ValueError as exc:
        return str(exc)
'''
    assert undefined_names(source) == []


def test_detector_understands_class_and_nested_scopes():
    source = '''
from dataclasses import dataclass

@dataclass
class Holder:
    value: int = 0

    def scaled(self, factor):
        def inner(v):
            return v * factor
        return inner(self.value)
'''
    assert undefined_names(source) == []
