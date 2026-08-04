"""Adapter: generate an optimizer-ready input file including new customers.

After the planner selects a start week for each onboarded customer, this writes a
workbook that can be uploaded straight back into the Cost Optimizer. Existing
rows are preserved verbatim (including inactive rows and any pass-through
columns); new customers are appended using the planner-entered ``Site_ID`` (the
elution system serial number) — never auto-generated.

See .kiro/specs/optimizer-enhancements/design.md, Feature 5.
"""

from __future__ import annotations

import io
from typing import Any, Sequence

import pandas as pd

from domain.errors import ValidationError

# Canonical column order for generated files.
CANONICAL_COLUMNS = [
    "Site_ID",
    "Site_Name",
    "Active",
    "Next_Demand_Week",
    "Interval_Weeks",
    "Country",
    "EU_Restricted",
    "Is_New",
]

NEW_FLAG = "Y"
EXISTING_FLAG = "N"


def _find_column(columns: Sequence[str], target: str) -> str | None:
    """Locate a column case/space-insensitively, returning its actual name."""
    want = target.strip().lower().replace(" ", "_")
    for col in columns:
        if str(col).strip().lower().replace(" ", "_") == want:
            return col
    return None


class InputFileWriter:
    """Builds a combined sites workbook. Implements ``InputFileWriterPort``."""

    def write(
        self,
        existing_file_bytes: bytes,
        existing_filename: str,
        sheet: str,
        new_customers: Sequence[Any],
        selected_weeks: dict[str, int],
    ) -> bytes:
        """Return workbook bytes combining existing sites with new customers.

        Parameters
        ----------
        existing_file_bytes : bytes
            The uploaded sites file (xlsx or csv).
        existing_filename : str
            Used only to detect CSV vs Excel.
        sheet : str
            Worksheet name to read (and write) for Excel input.
        new_customers : Sequence[NewCustomer]
            Customers to append. Each needs ``site_id``, ``interval_weeks``,
            ``country``, optionally ``site_name`` and ``eu_restricted``.
        selected_weeks : dict[str, int]
            Chosen start week per ``site_id``. Every customer must have one.

        Raises
        ------
        ValidationError
            If a start week is missing, or a Site_ID collides with an existing
            site or another new customer.
        """
        existing_df = self._read_existing(existing_file_bytes, existing_filename, sheet)
        errors = validate_new_rows(existing_df, new_customers, selected_weeks)
        if errors:
            raise ValidationError(errors)

        combined = build_combined_frame(existing_df, new_customers, selected_weeks)
        return self._to_bytes(combined, sheet)

    # ------------------------------------------------------------------

    @staticmethod
    def _read_existing(data: bytes, filename: str, sheet: str) -> pd.DataFrame:
        """Read the uploaded sites file, preserving identifier text.

        ``Site_ID`` is forced to ``str`` so account codes with leading zeros
        (e.g. ``00449``) survive the round-trip instead of becoming ``449``.
        """
        is_csv = str(filename).lower().endswith(".csv")

        def _load(dtype: dict | None) -> pd.DataFrame:
            buffer = io.BytesIO(data)
            if is_csv:
                return pd.read_csv(buffer, dtype=dtype)
            return pd.read_excel(buffer, sheet_name=sheet, dtype=dtype)

        df = _load(None)
        id_col = _find_column(df.columns, "Site_ID")
        if id_col is not None:
            df = _load({id_col: str})
        return df

    @staticmethod
    def _to_bytes(df: pd.DataFrame, sheet: str) -> bytes:
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            df.to_excel(writer, sheet_name=sheet or "Sites", index=False)
        return buffer.getvalue()


# ---------------------------------------------------------------------------
# Validation and frame assembly (pure helpers, unit-testable)
# ---------------------------------------------------------------------------

def existing_site_ids(existing_df: pd.DataFrame) -> set[str]:
    """Return the set of Site_IDs already present, as trimmed strings."""
    col = _find_column(existing_df.columns, "Site_ID")
    if col is None:
        return set()
    return {
        str(v).strip()
        for v in existing_df[col].tolist()
        if v is not None and str(v).strip() and str(v).strip().lower() != "nan"
    }


def validate_new_rows(
    existing_df: pd.DataFrame,
    new_customers: Sequence[Any],
    selected_weeks: dict[str, int],
) -> list[str]:
    """Check that every customer has a start week and a unique Site_ID."""
    errors: list[str] = []
    existing_ids = existing_site_ids(existing_df)
    seen: set[str] = set()

    for cust in new_customers:
        site_id = str(getattr(cust, "site_id", "")).strip()
        if not site_id:
            errors.append(
                "Every new customer needs a Site_ID (elution system serial number)."
            )
            continue
        if site_id in existing_ids:
            errors.append(
                f"Site_ID '{site_id}' already exists in the uploaded sites file."
            )
        if site_id in seen:
            errors.append(f"Site_ID '{site_id}' is duplicated among the new customers.")
        seen.add(site_id)

        if site_id not in selected_weeks:
            errors.append(f"No start week selected for new customer '{site_id}'.")

    return errors


def build_combined_frame(
    existing_df: pd.DataFrame,
    new_customers: Sequence[Any],
    selected_weeks: dict[str, int],
) -> pd.DataFrame:
    """Append new-customer rows to the existing frame, preserving all columns.

    Existing rows are untouched. An ``Is_New`` flag distinguishes appended rows;
    existing rows are marked ``N``.
    """
    df = existing_df.copy()

    # Resolve the actual column names present in the uploaded file.
    resolved = {
        target: _find_column(df.columns, target)
        for target in CANONICAL_COLUMNS
    }

    # Ensure the columns we must populate exist.
    for target in ("Site_ID", "Active", "Next_Demand_Week", "Interval_Weeks",
                   "Country", "Site_Name", "EU_Restricted", "Is_New"):
        if resolved[target] is None:
            df[target] = "" if target in ("Site_Name", "EU_Restricted", "Country") else None
            resolved[target] = target

    # Mark existing rows as not new.
    df[resolved["Is_New"]] = EXISTING_FLAG

    rows = []
    for cust in new_customers:
        site_id = str(getattr(cust, "site_id", "")).strip()
        row: dict[str, Any] = {c: None for c in df.columns}
        row[resolved["Site_ID"]] = site_id
        row[resolved["Site_Name"]] = getattr(cust, "site_name", "") or ""
        row[resolved["Active"]] = "Y"
        row[resolved["Next_Demand_Week"]] = int(selected_weeks[site_id])
        row[resolved["Interval_Weeks"]] = int(getattr(cust, "interval_weeks", 0))
        row[resolved["Country"]] = getattr(cust, "country", "") or ""
        eu = getattr(cust, "eu_restricted", False)
        row[resolved["EU_Restricted"]] = "Y" if eu else "N"
        row[resolved["Is_New"]] = NEW_FLAG
        rows.append(row)

    if not rows:
        return df

    return pd.concat([df, pd.DataFrame(rows)], ignore_index=True)


def generate_input_file(
    existing_file_bytes: bytes,
    existing_filename: str,
    sheet: str,
    new_customers: Sequence[Any],
    selected_weeks: dict[str, int],
) -> bytes:
    """Module-level convenience wrapper around :class:`InputFileWriter`."""
    return InputFileWriter().write(
        existing_file_bytes, existing_filename, sheet, new_customers, selected_weeks
    )
