"""Port protocols the service layer depends on.

Services depend on these abstract interfaces, not on concrete adapters. The
adapters in ``io_adapters`` implement them, and the presentation layer wires
concrete implementations into services at startup. This keeps services testable
with in-memory fakes and decouples business orchestration from file formats.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

import pandas as pd

if TYPE_CHECKING:  # avoid importing heavy/optional types at runtime
    from domain.params import IntegratedParams


@runtime_checkable
class SitesReaderPort(Protocol):
    """Reads and normalizes the sites input file into a raw DataFrame."""

    def read(
        self,
        source: bytes | str,
        sheet: str | None = "Sites",
        *,
        is_csv: bool = False,
    ) -> pd.DataFrame:
        """Return a column-normalized DataFrame of raw site rows.

        Parameters
        ----------
        source : bytes | str
            A filesystem path (str) or the raw bytes of an uploaded file.
        sheet : str | None
            Worksheet name to read for Excel input (ignored for CSV).
        is_csv : bool
            When ``source`` is bytes, indicates the payload is CSV rather than
            Excel. For a str path the format is inferred from the extension.
        """
        ...


@runtime_checkable
class ResultExporterPort(Protocol):
    """Writes optimization results to an Excel workbook and returns the bytes."""

    def export(
        self,
        plan_df: pd.DataFrame,
        sites_df: pd.DataFrame,
        issues_df: pd.DataFrame,
        params: "IntegratedParams",
        summary: dict,
        **extras: Any,
    ) -> bytes:
        """Return the result workbook as bytes.

        Implementations may accept optional ``extras`` sections (supplier params,
        quota status, delivery assignments, week dates, comparison tables,
        generated-identifier mapping) and include them as additional sheets.
        """
        ...


@runtime_checkable
class MasterPlannerReaderPort(Protocol):
    """Parses the wide Master Planner workbook into structured data.

    Implemented by ``io_adapters.master_planner_parser.MasterPlannerParser``.
    Returns a ``MasterPlannerData``.
    """

    def parse(
        self,
        source: bytes,
        sheet: str = "Schedule",
        horizon_weeks: int = 52,
        year: int | None = None,
    ) -> Any:
        ...


@runtime_checkable
class MasterPlannerConverterPort(Protocol):
    """Derives optimizer-ready site rows from the wide Master Planner.

    Implemented by
    ``io_adapters.master_planner_converter.MasterPlannerConverter``. ``convert``
    returns a ``DerivedSiteSet``; ``write`` renders it as workbook bytes.
    """

    def convert(
        self,
        source: bytes,
        sheet: str = "Schedule",
        horizon_weeks: int = 52,
        year: int | None = None,
    ) -> Any:
        ...

    def write(self, result: Any) -> bytes:
        ...


@runtime_checkable
class InputFileWriterPort(Protocol):
    """Generates an optimizer-ready input file including new customers.

    Implemented in Phase 10 (``io_adapters.input_file_writer``). Declared here
    so the onboarding service can depend on the interface.
    """

    def write(
        self,
        existing_file_bytes: bytes,
        existing_filename: str,
        sheet: str,
        new_customers: list[Any],
        selected_weeks: dict[str, int],
    ) -> bytes:
        ...
