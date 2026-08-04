"""Tests for the generated optimizer input file (io_adapters.input_file_writer)."""

import io

import pandas as pd
import pytest

from domain.demand import clean_sites
from domain.errors import ValidationError
from domain.onboarding import NewCustomer
from domain.params import IntegratedParams
from io_adapters.input_file_writer import (
    EXISTING_FLAG,
    NEW_FLAG,
    InputFileWriter,
    build_combined_frame,
    existing_site_ids,
    generate_input_file,
    validate_new_rows,
)
from io_adapters.sites_reader import ExcelSitesReader
from services.ports import InputFileWriterPort

EXISTING_CSV = (
    "Site_ID,Active,Next_Demand_Week,Interval_Weeks,Country\n"
    "S1,Y,3,7,usa\n"
    "S2,N,5,8,denmark\n"
)


def _existing_xlsx() -> bytes:
    df = pd.DataFrame({
        "Site_ID": ["S1", "S2"],
        "Active": ["Y", "N"],
        "Next_Demand_Week": [3, 5],
        "Interval_Weeks": [7, 8],
        "Country": ["usa", "denmark"],
    })
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        df.to_excel(w, sheet_name="Sites", index=False)
    return buf.getvalue()


CUSTOMERS = [
    NewCustomer("SN-1001", site_name="Acme Cardiology", earliest_week=2,
                latest_week=6, interval_weeks=7, country="usa"),
    NewCustomer("SN-1002", site_name="Nordic Heart", earliest_week=3,
                latest_week=8, interval_weeks=8, country="denmark",
                eu_restricted=True),
]
WEEKS = {"SN-1001": 4, "SN-1002": 6}


def test_writer_satisfies_port():
    assert isinstance(InputFileWriter(), InputFileWriterPort)


def test_generates_valid_workbook_from_csv():
    data = generate_input_file(EXISTING_CSV.encode(), "sites.csv", "Sites", CUSTOMERS, WEEKS)
    assert isinstance(data, bytes) and len(data) > 0
    df = pd.read_excel(io.BytesIO(data), sheet_name="Sites")
    assert len(df) == 4  # 2 existing + 2 new


def test_generates_from_xlsx_source():
    data = generate_input_file(_existing_xlsx(), "sites.xlsx", "Sites", CUSTOMERS, WEEKS)
    df = pd.read_excel(io.BytesIO(data), sheet_name="Sites")
    assert len(df) == 4


def test_existing_rows_preserved_including_inactive():
    data = generate_input_file(EXISTING_CSV.encode(), "sites.csv", "Sites", CUSTOMERS, WEEKS)
    df = pd.read_excel(io.BytesIO(data), sheet_name="Sites")
    s1 = df[df["Site_ID"] == "S1"].iloc[0]
    s2 = df[df["Site_ID"] == "S2"].iloc[0]
    assert s1["Active"] == "Y" and int(s1["Next_Demand_Week"]) == 3
    assert s2["Active"] == "N"          # inactive row kept
    assert int(s2["Interval_Weeks"]) == 8


def test_new_rows_use_planner_site_id_and_selected_week():
    data = generate_input_file(EXISTING_CSV.encode(), "sites.csv", "Sites", CUSTOMERS, WEEKS)
    df = pd.read_excel(io.BytesIO(data), sheet_name="Sites")
    n1 = df[df["Site_ID"] == "SN-1001"].iloc[0]
    assert int(n1["Next_Demand_Week"]) == 4      # selected week, not window start
    assert int(n1["Interval_Weeks"]) == 7
    assert n1["Active"] == "Y"


def test_new_rows_flagged_and_existing_not():
    data = generate_input_file(EXISTING_CSV.encode(), "sites.csv", "Sites", CUSTOMERS, WEEKS)
    df = pd.read_excel(io.BytesIO(data), sheet_name="Sites")
    assert set(df[df["Site_ID"].isin(["S1", "S2"])]["Is_New"]) == {EXISTING_FLAG}
    assert set(df[df["Site_ID"].str.startswith("SN-")]["Is_New"]) == {NEW_FLAG}


def test_country_and_eu_flag_carried_through():
    data = generate_input_file(EXISTING_CSV.encode(), "sites.csv", "Sites", CUSTOMERS, WEEKS)
    df = pd.read_excel(io.BytesIO(data), sheet_name="Sites")
    n2 = df[df["Site_ID"] == "SN-1002"].iloc[0]
    assert n2["Country"] == "denmark"
    assert n2["EU_Restricted"] == "Y"
    n1 = df[df["Site_ID"] == "SN-1001"].iloc[0]
    assert n1["EU_Restricted"] == "N"


def test_site_name_preserved():
    data = generate_input_file(EXISTING_CSV.encode(), "sites.csv", "Sites", CUSTOMERS, WEEKS)
    df = pd.read_excel(io.BytesIO(data), sheet_name="Sites")
    assert df[df["Site_ID"] == "SN-1001"].iloc[0]["Site_Name"] == "Acme Cardiology"


def test_pass_through_columns_preserved():
    csv = (
        "Site_ID,Active,Next_Demand_Week,Interval_Weeks,Country,Notes\n"
        "S1,Y,3,7,usa,keep-me\n"
    )
    data = generate_input_file(csv.encode(), "sites.csv", "Sites", CUSTOMERS, WEEKS)
    df = pd.read_excel(io.BytesIO(data), sheet_name="Sites")
    assert "Notes" in df.columns
    assert df[df["Site_ID"] == "S1"].iloc[0]["Notes"] == "keep-me"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_collision_with_existing_site_rejected():
    clash = [NewCustomer("S1", interval_weeks=7, country="usa")]
    with pytest.raises(ValidationError, match="already exists"):
        generate_input_file(EXISTING_CSV.encode(), "sites.csv", "Sites", clash, {"S1": 4})


def test_duplicate_among_new_customers_rejected():
    dup = [
        NewCustomer("SN-9", interval_weeks=7, country="usa"),
        NewCustomer("SN-9", interval_weeks=8, country="usa"),
    ]
    with pytest.raises(ValidationError, match="duplicated"):
        generate_input_file(EXISTING_CSV.encode(), "sites.csv", "Sites", dup, {"SN-9": 4})


def test_missing_selected_week_rejected():
    cust = [NewCustomer("SN-7", interval_weeks=7, country="usa")]
    with pytest.raises(ValidationError, match="No start week"):
        generate_input_file(EXISTING_CSV.encode(), "sites.csv", "Sites", cust, {})


def test_blank_site_id_rejected():
    cust = [NewCustomer("", interval_weeks=7, country="usa")]
    with pytest.raises(ValidationError, match="needs a Site_ID"):
        generate_input_file(EXISTING_CSV.encode(), "sites.csv", "Sites", cust, {})


def test_existing_site_ids_helper():
    df = pd.read_csv(io.BytesIO(EXISTING_CSV.encode()))
    assert existing_site_ids(df) == {"S1", "S2"}


def test_validate_new_rows_returns_empty_when_ok():
    df = pd.read_csv(io.BytesIO(EXISTING_CSV.encode()))
    assert validate_new_rows(df, CUSTOMERS, WEEKS) == []


def test_no_new_customers_returns_existing_only():
    df = pd.read_csv(io.BytesIO(EXISTING_CSV.encode()))
    out = build_combined_frame(df, [], {})
    assert len(out) == 2


# ---------------------------------------------------------------------------
# Round-trip: the generated file must load cleanly in the optimizer
# ---------------------------------------------------------------------------

def test_round_trip_through_sites_reader_no_new_issues():
    data = generate_input_file(EXISTING_CSV.encode(), "sites.csv", "Sites", CUSTOMERS, WEEKS)
    raw = ExcelSitesReader().read(data, sheet="Sites")
    params = IntegratedParams()
    active, issues = clean_sites(raw, params)
    # New customers are active and present
    assert {"SN-1001", "SN-1002"} <= set(active["site_id"])
    # No data-quality issues attributable to the generated rows
    bad = issues[issues["site_id"].isin(["SN-1001", "SN-1002"])]
    assert bad.empty


def test_round_trip_demand_reflects_new_customers():
    from domain.demand import build_weekly_demand
    data = generate_input_file(EXISTING_CSV.encode(), "sites.csv", "Sites", CUSTOMERS, WEEKS)
    raw = ExcelSitesReader().read(data, sheet="Sites")
    params = IntegratedParams()
    active, _ = clean_sites(raw, params)
    demand = build_weekly_demand(active, params)
    # SN-1001 starts week 4 with interval 7 -> demand in weeks 4, 11, 18, ...
    assert demand[4] >= 1
    assert demand[11] >= 1
