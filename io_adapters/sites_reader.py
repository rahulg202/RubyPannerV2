"""Adapter: read and normalize the sites input file (Excel or CSV).

This is the canonical home for sites-file reading. The backward-compatibility
``read_sites`` in ``integrated_cost_optimizer`` delegates here.

Identifier columns are read as text so leading zeros survive. The Master Planner
uses account codes such as ``00449``; pandas would otherwise infer ``int64`` and
turn that into ``449``, silently breaking every per-customer comparison.
"""

from __future__ import annotations

import io
from typing import Any

import pandas as pd

from domain.demand import REQUIRED_COLS, _norm_cols

# Columns that must never be type-inferred (leading zeros are significant).
ID_COLUMNS = {"site_id", "siteid", "site id"}


def _normalize(name: Any) -> str:
    return str(name).strip().lower()


def _id_dtype_map(columns) -> dict:
    """Map any identifier-like column to ``str`` for a re-read."""
    return {col: str for col in columns if _normalize(col) in ID_COLUMNS}


class ExcelSitesReader:
    """Reads the sites input file from a path or raw bytes.

    Implements :class:`services.ports.SitesReaderPort`.
    """

    def read(
        self,
        source: bytes | str,
        sheet: str | None = "Sites",
        *,
        is_csv: bool = False,
    ) -> pd.DataFrame:
        """Return a column-normalized DataFrame, validating required columns.

        Parameters
        ----------
        source : bytes | str
            A filesystem path (str) or the raw bytes of an uploaded file.
        sheet : str | None
            Worksheet name for Excel input (ignored for CSV).
        is_csv : bool
            When ``source`` is bytes, marks the payload as CSV.

        Raises
        ------
        ValueError
            If any required column is missing after normalization.
        """
        as_bytes = isinstance(source, (bytes, bytearray))
        csv_input = is_csv if as_bytes else str(source).lower().endswith(".csv")

        def _load(dtype: dict | None) -> pd.DataFrame:
            handle = io.BytesIO(source) if as_bytes else source
            if csv_input:
                return pd.read_csv(handle, dtype=dtype)
            return pd.read_excel(handle, sheet_name=sheet, dtype=dtype)

        # First pass discovers the real column names; second pass preserves
        # identifier text (leading zeros) by forcing those columns to str.
        df = _load(None)
        dtype_map = _id_dtype_map(df.columns)
        if dtype_map:
            df = _load(dtype_map)

        df = _norm_cols(df)

        missing = [c for c in REQUIRED_COLS if c not in df.columns]
        if missing:
            raise ValueError(
                f"Missing required columns: {missing}. Found: {list(df.columns)}"
            )
        return df


def read_sites(path: str, sites_sheet: str = "Sites") -> pd.DataFrame:
    """Read site data from an Excel (.xlsx) or CSV file path.

    Canonical implementation (previously in ``integrated_cost_optimizer``).
    Kept as a module-level function for backward compatibility.
    """
    return ExcelSitesReader().read(path, sites_sheet)
