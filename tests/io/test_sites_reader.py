"""Tests for the ExcelSitesReader adapter (io_adapters.sites_reader)."""

import io

import pandas as pd
import pytest

from io_adapters.sites_reader import ExcelSitesReader, read_sites
from services.ports import SitesReaderPort


VALID_CSV = (
    "Site_ID,Active,Next_Demand_Week,Interval_Weeks,Country\n"
    "S1,Y,3,7,usa\n"
    "S2,Y,5,8,denmark\n"
)


def _write_csv(tmp_path, text=VALID_CSV):
    p = tmp_path / "sites.csv"
    p.write_text(text)
    return str(p)


def _write_xlsx(tmp_path):
    df = pd.DataFrame(
        {
            "Site_ID": ["S1", "S2"],
            "Active": ["Y", "Y"],
            "Next_Demand_Week": [3, 5],
            "Interval_Weeks": [7, 8],
            "Country": ["usa", "uk"],
        }
    )
    p = tmp_path / "sites.xlsx"
    with pd.ExcelWriter(p, engine="openpyxl") as w:
        df.to_excel(w, sheet_name="Sites", index=False)
    return str(p)


def test_reader_satisfies_port():
    assert isinstance(ExcelSitesReader(), SitesReaderPort)


def test_read_csv_path_normalizes_columns(tmp_path):
    path = _write_csv(tmp_path)
    df = ExcelSitesReader().read(path)
    assert list(df.columns) == ["site_id", "active", "next_demand_week", "interval_weeks", "country"]
    assert len(df) == 2


def test_read_xlsx_path(tmp_path):
    path = _write_xlsx(tmp_path)
    df = ExcelSitesReader().read(path, sheet="Sites")
    assert "site_id" in df.columns
    assert len(df) == 2


def test_read_csv_bytes(tmp_path):
    df = ExcelSitesReader().read(VALID_CSV.encode("utf-8"), is_csv=True)
    assert len(df) == 2
    assert "interval_weeks" in df.columns


def test_read_xlsx_bytes(tmp_path):
    path = _write_xlsx(tmp_path)
    raw = open(path, "rb").read()
    df = ExcelSitesReader().read(raw, sheet="Sites")
    assert len(df) == 2


def test_missing_required_column_raises(tmp_path):
    bad = "Site_ID,Active,Next_Demand_Week\nS1,Y,3\n"  # no interval_weeks
    with pytest.raises(ValueError, match="Missing required columns"):
        ExcelSitesReader().read(bad.encode("utf-8"), is_csv=True)


def test_module_read_sites_matches_adapter(tmp_path):
    path = _write_csv(tmp_path)
    df_fn = read_sites(path)
    df_cls = ExcelSitesReader().read(path)
    pd.testing.assert_frame_equal(df_fn, df_cls)


def test_extra_columns_passed_through(tmp_path):
    text = (
        "Site_ID,Active,Next_Demand_Week,Interval_Weeks,Country,Site_Name,EU_Restricted\n"
        "S1,Y,3,7,usa,Acme Cardiology,N\n"
    )
    df = ExcelSitesReader().read(text.encode("utf-8"), is_csv=True)
    assert "site_name" in df.columns
    assert "eu_restricted" in df.columns


# ---------------------------------------------------------------------------
# Leading-zero preservation (regression)
# ---------------------------------------------------------------------------

LEADING_ZERO_CSV = (
    "Site_ID,Active,Next_Demand_Week,Interval_Weeks,Country\n"
    "00449,Y,2,7,usa\n"
    "00438,Y,4,8,usa\n"
    "1401,Y,3,7,denmark\n"
)


def test_leading_zeros_preserved_from_csv():
    """Account codes like 00449 must not become 449.

    The Master Planner identifies customers by zero-padded account codes. If
    pandas infers int64, every per-customer comparison silently fails to match.
    """
    df = ExcelSitesReader().read(LEADING_ZERO_CSV.encode(), is_csv=True)
    assert df["site_id"].tolist() == ["00449", "00438", "1401"]


def test_leading_zeros_preserved_from_xlsx(tmp_path):
    frame = pd.DataFrame({
        "Site_ID": ["00449", "00438"],
        "Active": ["Y", "Y"],
        "Next_Demand_Week": [2, 4],
        "Interval_Weeks": [7, 8],
        "Country": ["usa", "usa"],
    })
    path = tmp_path / "sites.xlsx"
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        frame.to_excel(writer, sheet_name="Sites", index=False)
    df = ExcelSitesReader().read(str(path), sheet="Sites")
    assert df["site_id"].tolist() == ["00449", "00438"]


def test_leading_zeros_survive_clean_sites():
    from domain.demand import clean_sites
    from domain.params import IntegratedParams
    df = ExcelSitesReader().read(LEADING_ZERO_CSV.encode(), is_csv=True)
    active, _issues = clean_sites(df, IntegratedParams())
    assert set(active["site_id"]) == {"00449", "00438", "1401"}


def test_numeric_columns_still_numeric():
    """Forcing Site_ID to text must not stringify the numeric fields."""
    from domain.demand import clean_sites
    from domain.params import IntegratedParams
    df = ExcelSitesReader().read(LEADING_ZERO_CSV.encode(), is_csv=True)
    active, _issues = clean_sites(df, IntegratedParams())
    assert active["next_demand_week"].dtype.kind in "iu"
    assert active["interval_weeks"].dtype.kind in "iu"
