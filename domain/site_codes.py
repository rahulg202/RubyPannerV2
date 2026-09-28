"""Domain: site-code assignment and duplicate suffixing (pure).

**One implementation, used by both Master Planner readers.** That is the whole
purpose of this module, and it is worth stating plainly because the project has
already paid for the lesson twice:

* ``domain/week_anchor.py`` exists because anchoring planning week 1 in only one
  of the two readers offset every per-customer week and produced 1,186 fabricated
  changed weeks against 60 real ones.
* Duplicate site codes were the same defect in a different rule.
  ``site_derivation`` renamed a repeated account number to ``00460-2`` while
  ``master_planner_parser`` merged both columns into a single ``00460`` entry with
  their marks summed. The generated input file therefore contained a site the
  parsed manual plan had no record of, so it was reported as a **new customer**,
  and the surviving ``00460`` was compared against an interleaved schedule
  belonging to two different hospitals.

If a third reader of the Master Planner is ever added, it calls this module.

Code precedence
---------------
1. A generated code already written into the header (``RF-4e40dfd2 ...``) is read
   verbatim. This is what makes annotating the workbook idempotent: without it,
   writing a code derived from the header text would change the text the code was
   derived from.
2. A leading account number (``00631/0105MAK ...`` -> ``00631``). Leading zeros
   are part of the code.
3. A deterministic ``RF-<sha1_8>`` of the normalised header.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

GENERATED_PREFIX = "RF-"

#: Leading account number, e.g. ``"00449 ..."`` or ``"00631/0105MAK ..."``.
LEADING_ACCOUNT = re.compile(r"^\s*(\d+)")

#: A generated code already present in the header, written by the annotator.
LEADING_GENERATED = re.compile(r"^\s*(RF-[0-9a-fA-F]{6,})")

SOURCE_ACCOUNT = "account number"
SOURCE_GENERATED = "generated"


def normalize_header(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace — the stable-ID key."""
    no_punct = re.sub(r"[^\w\s]", "", str(text).strip().lower())
    return re.sub(r"\s+", " ", no_punct).strip()


def generated_code(header: str, digest_len: int = 8) -> str:
    """Deterministic ``RF-<hash>`` identifier derived from the column header.

    The same header always yields the same identifier, so re-parsing a workbook —
    or the planning team reusing a shared mapping — is stable across runs.
    """
    digest = hashlib.sha1(normalize_header(header).encode("utf-8")).hexdigest()
    return f"{GENERATED_PREFIX}{digest[:digest_len]}"


def leading_generated_code(header: str) -> str | None:
    """Return a generated code already written into the header, if present."""
    match = LEADING_GENERATED.match(str(header).strip())
    return match.group(1) if match else None


def leading_account_code(header: str) -> str | None:
    """Return the leading account number, if present. Keeps leading zeros."""
    match = LEADING_ACCOUNT.match(str(header).strip())
    return match.group(1) if match else None


@dataclass(frozen=True)
class CodeAssignment:
    """A column's base code and where it came from."""

    code: str
    source: str

    @property
    def was_generated(self) -> bool:
        return self.source == SOURCE_GENERATED


def assign_code(header: str, digest_len: int = 8) -> CodeAssignment:
    """Resolve one column header to its base code, before de-duplication."""
    explicit = leading_generated_code(header)
    if explicit:
        # Already carries a code: read it literally. Re-deriving would defeat the
        # annotation that put it there.
        return CodeAssignment(explicit, SOURCE_GENERATED)

    account = leading_account_code(header)
    if account:
        return CodeAssignment(account, SOURCE_ACCOUNT)

    return CodeAssignment(generated_code(header, digest_len), SOURCE_GENERATED)


class CodeDeduplicator:
    """Suffixes repeated codes ``-2``, ``-3`` ... in call order.

    The Master Planner occasionally carries one account number on two columns — a
    retired column kept beside its replacement. Since the optimizer drops
    duplicate ``Site_ID`` values, the first occurrence keeps the bare code and
    later ones become ``00460-2``, ``00460-3``.

    Both readers walk header cells left to right, so feeding each column through
    one instance per workbook gives both the same answer. Order is the contract:
    construct a fresh instance per read, and do not reorder columns before
    assigning.
    """

    def __init__(self) -> None:
        self._seen: dict[str, int] = {}

    def assign(self, base: str) -> tuple[str, bool]:
        """Return ``(code, was_renamed)`` for one column's base code."""
        count = self._seen.get(base, 0) + 1
        self._seen[base] = count
        if count == 1:
            return base, False
        return f"{base}-{count}", True

    @staticmethod
    def rename_note(base: str, code: str) -> str:
        """The message both readers report for a renamed duplicate.

        Shared so the two never drift into describing the same event differently.
        """
        return (
            f"Master Planner has more than one column with code '{base}'. This one "
            f"was renamed '{code}' to keep codes unique — please correct the "
            f"header."
        )
