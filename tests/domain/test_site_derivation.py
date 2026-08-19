"""Tests for deriving sites from Master Planner columns (domain.site_derivation).

Header shapes here mirror the real Master Planner's conventions — account number
prefixes, cadence in parentheses, country hints buried in the name — without
using any real customer data.
"""

from __future__ import annotations

import pytest

from domain.site_derivation import (
    SOURCE_GAPS,
    SOURCE_HEADER,
    SOURCE_ONE_TIME,
    PlannerColumn,
    deduplicate_codes,
    derive_interval_from_marks,
    derive_site,
    derive_sites,
    infer_country,
    is_placeholder_name,
    parse_header_interval,
    split_account_and_name,
)


def _code(header: str) -> str:
    """Stand-in for the parser's stable-id generator."""
    return f"GEN-{abs(hash(header)) % 10_000:04d}"


# ---------------------------------------------------------------------------
# Interval declared in the header
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("header,expected", [
    ("00438  Alpha Cardiology, Anchorage, AK (7)", 7),
    ("00436  Beta Diagnostic, Lexington, KY (6)", 6),
    ("Gamma Cardiology, Jackson, TN (7 weeks)", 7),
    ("00465  Delta Clinic, El Centro (CA)  (8)", 8),          # (CA) ignored
    ("00458  Epsilon Cardiology (7) Chicago, IL", 7),          # not at the end
    ("1401  Zeta Hospital, Copenhagen (DK) (7)", 7),           # (DK) ignored
    ("00463  Eta Cardiology-Dr. Example (6)", 6),
    ("00553/0117MAK  Theta Vascular, Maitland, FL (7)", 7),
])
def test_parses_interval_from_header(header, expected):
    assert parse_header_interval(header) == expected


@pytest.mark.parametrize("header", [
    "00449  Iota Specialty Care, Fresno, CA (MIX)",   # variable cadence
    "00640  Kappa Health, Laurel, MD",                # nothing declared
    "1416  Lambda-CH SWZ",
    "00457  Mu Inc., (Kaiser) Atlanta, GA",           # non-numeric only
    "Nu Clinic (0)",                                  # below the sane range
    "Xi Clinic (99)",                                 # above the sane range
])
def test_returns_none_when_no_usable_interval(header):
    assert parse_header_interval(header) is None


def test_last_numeric_group_wins():
    # Cadence is conventionally written at the end of the header.
    assert parse_header_interval("00611  Site # 2 (3) Gurnee IL (7)") == 7


# ---------------------------------------------------------------------------
# Interval inferred from the schedule marks
# ---------------------------------------------------------------------------

def test_interval_from_evenly_spaced_marks():
    assert derive_interval_from_marks((3, 10, 17, 24, 31, 38)) == 7


def test_most_common_gap_wins_over_holiday_shifts():
    # A one-week slip for a holiday must not drag the cadence to 7.5.
    assert derive_interval_from_marks((1, 9, 17, 25, 34, 42)) == 8


def test_interval_from_marks_needs_two_marks():
    assert derive_interval_from_marks(()) is None
    assert derive_interval_from_marks((7,)) is None


def test_interval_from_marks_ignores_order_and_duplicates():
    assert derive_interval_from_marks((24, 3, 17, 10, 3)) == 7


def test_interval_from_marks_is_deterministic_on_ties():
    # Two gaps of 6 and two of 8: the tie must resolve the same way every time.
    marks = (1, 7, 13, 21, 29)
    assert derive_interval_from_marks(marks) == derive_interval_from_marks(marks)
    assert derive_interval_from_marks(marks) == 6


# ---------------------------------------------------------------------------
# Account number and display name
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("header,account,name", [
    ("00449    Alpha Specialty Care, Fresno, CA (MIX)",
     "00449", "Alpha Specialty Care, Fresno, CA (MIX)"),
    ("00631/0105MAK    Beta Two, Chandler, AZ (7)",
     "00631", "Beta Two, Chandler, AZ (7)"),
    ("306 & 416  Gamma Heart Inst., Ottawa, ON, CAN (7)",
     "306", "Gamma Heart Inst., Ottawa, ON, CAN (7)"),
    ("657 Delta Vascular-# 3 Maitland, FL (7)",
     "657", "Delta Vascular-# 3 Maitland, FL (7)"),
])
def test_splits_leading_account_number(header, account, name):
    assert split_account_and_name(header) == (account, name)


def test_keeps_leading_zeros_in_account_number():
    assert split_account_and_name("00449  Alpha, CA (7)")[0] == "00449"


def test_secondary_code_must_start_with_a_digit():
    # "00620/  MIS Colorado" — MIS is part of the name, not a second code.
    account, name = split_account_and_name("00620/  MIS Colorado Springs, CO (6)")
    assert account == "00620"
    assert name.startswith("MIS Colorado Springs")


def test_unnumbered_header_has_no_account():
    account, name = split_account_and_name("Alpha Cardiology, Jackson, TN (7 weeks)")
    assert account is None
    assert name == "Alpha Cardiology, Jackson, TN (7 weeks)"


@pytest.mark.parametrize("name,expected", [
    ("????????", True),
    ("   ", True),
    ("---", True),
    ("Alpha Cardiology", False),
])
def test_detects_placeholder_names(name, expected):
    assert is_placeholder_name(name) is expected


# ---------------------------------------------------------------------------
# Country inference
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("header,expected", [
    ("1401  Alpha Hospital, Univ of Copenhagen (DK) (7)", "denmark"),
    ("1414  Beta Hospital Denmark, Denmark (7)", "denmark"),
    ("01420  Gamma Bosch, Netherlands (7)", "netherlands"),
    ("1411  Delta Ziekenhuis (8)", "netherlands"),
    ("1405  Epsilon UK, London UK (7)", "uk"),
    ("1408  Zeta Clinique, Switzerland (7)", "switzerland"),
    ("1416  Eta-CH SWZ", "switzerland"),
    ("641  Theta Science Center Sudbury, Ont. CAN (7)", "canada"),
    ("502  Iota Toronto (UHN) (7)", "canada"),
    ("ISORAD, INC. (Site Example Hospital) Israel", "israel"),
    ("00411  Kappa General Hosp., Pitts. PA (7)", "usa"),
])
def test_infers_country_from_header(header, expected):
    assert infer_country(header) == expected


def test_swedish_named_us_site_is_not_sweden():
    # A real trap: "Swedish Medical Center, Bronx, NY" is a US site.
    assert infer_country("00617  Swedish Medical Center, Bronx, NY (7)") == "usa"


def test_country_defaults_to_usa():
    assert infer_country("00605  Watson Clinic (7)") == "usa"


# ---------------------------------------------------------------------------
# Whole-site derivation
# ---------------------------------------------------------------------------

def test_derives_site_from_numbered_column():
    site = derive_site(
        PlannerColumn("00438  Alpha Cardiology, Anchorage, AK (7)",
                      marks=(3, 10, 17, 24), column_letter="P"),
        _code("x"),
    )
    assert site.site_code == "00438"
    assert site.site_name == "Alpha Cardiology, Anchorage, AK (7)"
    assert site.active is True
    assert site.next_demand_week == 3
    assert site.interval_weeks == 7
    assert site.interval_source == SOURCE_HEADER
    assert site.code_source == "account number"
    assert site.country == "usa"
    assert site.deliveries == 4
    assert site.column_letter == "P"
    assert site.notes == []


def test_unnumbered_column_uses_the_generated_code_and_says_so():
    site = derive_site(
        PlannerColumn("Alpha Cardiology, Jackson, TN (7 weeks)", marks=(5, 12)),
        "RF-abcd1234",
    )
    assert site.site_code == "RF-abcd1234"
    assert site.code_source == "generated"
    assert any("No account number" in n for n in site.notes)


def test_header_interval_beats_the_observed_gaps():
    # The header is the team's stated cadence; a single mark means the site
    # started late, not that it is a one-off.
    site = derive_site(
        PlannerColumn("00500  Alpha Clinic, TX (7)", marks=(7,)), _code("y")
    )
    assert site.interval_weeks == 7
    assert site.interval_source == SOURCE_HEADER
    assert site.next_demand_week == 7


def test_disagreement_between_header_and_schedule_is_flagged():
    site = derive_site(
        PlannerColumn("00501  Beta Clinic, TX (7)", marks=(1, 10, 19, 28)),
        _code("z"),
    )
    assert site.interval_weeks == 7  # header still wins
    assert any("9 weeks apart" in n for n in site.notes)


def test_falls_back_to_schedule_gaps_when_header_has_no_interval():
    site = derive_site(
        PlannerColumn("00449  Alpha Specialty Care, CA (MIX)",
                      marks=(10, 19, 27, 36, 45)),
        _code("a"),
    )
    assert site.interval_weeks == 9
    assert site.interval_source == SOURCE_GAPS
    assert any("read from the spacing" in n for n in site.notes)


def test_single_mark_and_no_header_interval_is_a_one_time_delivery():
    site = derive_site(PlannerColumn("Alpha Clinic, TX", marks=(14,)), _code("b"))
    assert site.interval_weeks == 0
    assert site.interval_source == SOURCE_ONE_TIME
    assert site.next_demand_week == 14
    assert site.active is True


def test_column_with_no_marks_is_inactive():
    site = derive_site(PlannerColumn("00443  Alpha Heart Center, GA (7)"), _code("c"))
    assert site.active is False
    assert site.deliveries == 0
    assert site.next_demand_week == 1  # placeholder, not used while inactive
    assert any("No scheduled generators" in n for n in site.notes)


def test_marks_outside_the_horizon_are_ignored():
    site = derive_site(
        PlannerColumn("00502  Alpha Clinic, TX (7)", marks=(0, 3, 10, 99)),
        _code("d"), horizon_weeks=52,
    )
    assert site.deliveries == 2
    assert site.next_demand_week == 3


def test_eu_shading_is_carried_through():
    site = derive_site(
        PlannerColumn("1405  Alpha UK, London UK (7)", marks=(3, 10),
                      eu_restricted=True),
        _code("e"),
    )
    assert site.eu_restricted is True
    assert site.country == "uk"
    assert site.notes == []


def test_eu_shading_without_a_restricted_country_is_flagged():
    # The shading is authoritative; if the text does not agree, the planner has
    # to resolve it or the supply constraint silently will not apply.
    site = derive_site(
        PlannerColumn("1419  Alpha Clinic (10)", marks=(8, 18), eu_restricted=True),
        _code("f"),
    )
    assert site.eu_restricted is True
    assert any("Shaded as EU-restricted" in n for n in site.notes)


def test_placeholder_name_is_flagged():
    site = derive_site(PlannerColumn("00460     ??????????", marks=(2,)), _code("g"))
    assert any("no readable customer name" in n for n in site.notes)


# ---------------------------------------------------------------------------
# Uniqueness across the workbook
# ---------------------------------------------------------------------------

def test_duplicate_codes_are_suffixed_and_reported():
    sites = [
        derive_site(PlannerColumn("00460  Alpha Med., FL (7)", marks=(3, 10)), "g1"),
        derive_site(PlannerColumn("00460  ???????", marks=()), "g2"),
    ]
    warnings = deduplicate_codes(sites)

    assert sites[0].site_code == "00460"      # first occurrence keeps the code
    assert sites[1].site_code == "00460-2"
    assert len(warnings) == 1
    assert "00460" in warnings[0]
    assert any("more than one column" in n for n in sites[1].notes)


def test_derive_sites_returns_unique_codes():
    columns = [
        PlannerColumn("00449  Alpha, CA (7)", marks=(1, 8)),
        PlannerColumn("00449  Alpha duplicate, CA (7)", marks=(2, 9)),
        PlannerColumn("Beta, TX (7)", marks=(3, 10)),
    ]
    sites, warnings = derive_sites(columns, _code)

    codes = [s.site_code for s in sites]
    assert len(set(codes)) == len(codes)
    assert warnings


def test_derive_sites_preserves_sheet_order():
    columns = [
        PlannerColumn("00003  Gamma, TX (7)", marks=(1,)),
        PlannerColumn("00001  Alpha, TX (7)", marks=(1,)),
        PlannerColumn("00002  Beta, TX (7)", marks=(1,)),
    ]
    sites, _ = derive_sites(columns, _code)
    assert [s.site_code for s in sites] == ["00003", "00001", "00002"]
