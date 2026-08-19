"""Ruby Fill Optimizer — application entry point.

Wires concrete I/O adapters into the services once, then renders the tabs. All
business logic lives in ``domain``; orchestration in ``services``; file formats in
``io_adapters``. This module deliberately contains no business logic.

Run with:  streamlit run app.py
"""

from __future__ import annotations

import streamlit as st

from domain.errors import ValidationError
from io_adapters.input_file_writer import InputFileWriter
from io_adapters.master_planner_converter import MasterPlannerConverter
from io_adapters.master_planner_parser import MasterPlannerParser
from io_adapters.sites_reader import ExcelSitesReader
from io_adapters.workbook_exporter import WorkbookExporter
from services.comparison_service import ComparisonService
from services.conversion_service import ConversionService
from services.onboarding_service import OnboardingService
from services.optimizer_service import OptimizerService
from services.settings_service import DEFAULTS, build_settings
from ui import state as S
from ui import (
    tab_comparison,
    tab_converter,
    tab_onboarding,
    tab_optimizer,
    tab_settings,
)

st.set_page_config(page_title="Ruby Fill Optimizer", layout="wide")


def build_services() -> tuple[
    OptimizerService, OnboardingService, ComparisonService, ConversionService
]:
    """Inject concrete adapters into the services (the only wiring point)."""
    sites_reader = ExcelSitesReader()
    master_planner = MasterPlannerParser()
    return (
        OptimizerService(
            sites_reader=sites_reader,
            exporter=WorkbookExporter(),
            master_planner_reader=master_planner,
        ),
        OnboardingService(
            sites_reader=sites_reader,
            input_file_writer=InputFileWriter(),
        ),
        ComparisonService(master_planner_reader=master_planner),
        ConversionService(converter=MasterPlannerConverter()),
    )


def current_settings():
    """Assemble validated settings, or return the ValidationError for display."""
    try:
        return build_settings(S.raw_settings(st.session_state, DEFAULTS))
    except ValidationError as exc:
        return exc


def main() -> None:
    S.init_state(st.session_state)
    (
        optimizer_service,
        onboarding_service,
        comparison_service,
        conversion_service,
    ) = build_services()

    st.title("Ruby Fill Optimizer")
    st.caption(
        "Plan 52 weeks of generator production, onboard new customers, and see "
        "what the optimizer saves against your manual plan. "
        "[User guide](https://github.com/rahulg202/RubyPannerV2/blob/main/USER_GUIDE.md)"
    )

    tab_set, tab_conv, tab_opt, tab_ob, tab_cmp = st.tabs(
        ["Settings", "Import Manual Plan", "Cost Optimizer", "Onboarding",
         "Comparison"]
    )

    with tab_set:
        tab_settings.render()

    # Settings are read once after the Settings tab has registered its widgets, so
    # every workflow tab sees the same validated configuration.
    settings = current_settings()

    with tab_conv:
        tab_converter.render(conversion_service, settings)
    with tab_opt:
        tab_optimizer.render(optimizer_service, settings)
    with tab_ob:
        tab_onboarding.render(onboarding_service, settings)
    with tab_cmp:
        tab_comparison.render(comparison_service, settings)


main()
