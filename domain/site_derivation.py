"""Derive optimizer-ready site records from Master Planner columns.

The planning team maintains one wide Master Planner sheet: a row per production
week, a column per customer, and the integer ``1`` in a cell to mark a scheduled
generator. The optimizer needs the opposite shape — one row per site with a
delivery cadence. This module performs that inversion.

Everything here is pure. The caller (``io_adapters.master_planner_converter``)
reads the workbook and hands over plain :class:`PlannerColumn` values; this
module decides each site's code, cadence, first demand week and country.

Why this exists
---------------
The manual plan and the optimizer input sheet previously had no shared key, so
the Comparison tool could not line the two up. Deriving the input sheet *from*
the manual plan fixes that by construction: every site carries the same
``site_code`` in both places.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

import pandas as pd

from domain.demand import ROW_COUNTRIES

# Countries the optimizer treats as ROW / EU-restricted (see domain.demand).
# Values are emitted lowercase to match ``ROW_COUNTRIES`` there.
COUNTRY_USA = "usa"

# Ordered country rules. First match wins, so put the specific tokens first.
# Word boundaries matter: "Swedish Medical Center, Bronx, NY" is a US site and
# must not be read as Sweden.
_COUNTRY_PATTERNS: tuple[tuple[str, str], ...] = (
    ("denmark", r"\bdenmark\b|\bdk\b"),
    # "ziekenhuis" is Dutch for hospital and appears (variously misspelt) in the
    # Netherlands headers that do not name the country.
    ("netherlands", r"\bnetherlands\b|\bholland\b|\bnl\b|zie[kh]en|zeihne"),
    ("uk", r"\bunited kingdom\b|\bengland\b|\bscotland\b|\buk\b"),
    ("sweden", r"\bsweden\b"),
    ("switzerland", r"\bswitzerland\b|\bswz\b|\bsuisse\b|-ch\b"),
    ("israel", r"\bisrael\b"),
    ("canada", r"\bcanada\b|\bcan\b|\bontario\b|\bquebec\b|\bqc\b|\btoronto\b"
                r"|\bmontreal\b|\bedmonton\b|\bottawa\b|\bsudbury\b"),
)

# An interval declared in the header, e.g. "(7)" or "(7 weeks)".
_HEADER_INTERVAL = re.compile(r"\(\s*(\d{1,2})\s*(?:weeks?|wks?)?\s*\)", re.IGNORECASE)

# Leading account number, e.g. "00449 ..." or "00631/0105MAK ...".
_LEADING_ACCOUNT = re.compile(r"^\s*(\d+)")

# Placeholder headers the planning team uses for not-yet-named columns.
_PLACEHOLDER = re.compile(r"^[\s?\-_.]*$")

MAX_INTERVAL_WEEKS = 52

# Interval provenance labels, surfaced to the planner in the mapping sheet.
SOURCE_HEADER = "header"
SOURCE_GAPS = "schedule gaps"
SOURCE_ONE_TIME = "one-time delivery"


@dataclass(frozen=True)
class PlannerColumn:
    """One customer column lifted out of the Master Planner sheet.

    Attributes
    ----------
    header : str
        The column header text exactly as it appears in the sheet.
    marks : tuple[int, ...]
        Week numbers carrying a scheduled generator, ascending and de-duplicated.
    eu_restricted : bool
        True when the column is shaded with the EU-restricted fill.
    column_letter : str
        Spreadsheet column reference, so the planner can find the column again.
    """

    header: str
    marks: tuple[int, ...] = ()
    eu_restricted: bool = False
    column_letter: str = ""


@dataclass
class DerivedSite:
    """One optimizer-ready site row derived from a Master Planner column."""

    site_code: str
    site_name: str
    active: bool
    next_demand_week: int
    interval_weeks: int
    country: str
    eu_restricted: bool
    column_header: str
    column_letter: str = ""
    code_source: str = ""          # "account number" | "generated"
    interval_source: str = ""      # SOURCE_* above
    deliveries: int = 0
    notes: list[str] = field(default_factory=list)


def parse_header_interval(header: str) -> int | None:
    """Return the delivery interval declared in the header, if any.

    The team writes the cadence in parentheses, e.g.
    ``"00438  Alaska Heart & Vascular Institute, Anchorage, AK (7)"``. Non-numeric
    parentheses such as ``(DK)``, ``(MIX)`` or ``(Kaiser)`` are ignored, and a
    header may contain several groups — the last numeric one wins, since the
    cadence is conventionally written at the end.

    Returns ``None`` when no plausible interval is present.
    """
    matches = _HEADER_INTERVAL.findall(str(header))
    for raw in reversed(matches):
        value = int(raw)
        if 1 <= value <= MAX_INTERVAL_WEEKS:
            return value
    return None


def derive_interval_from_marks(marks: tuple[int, ...] | list[int]) -> int | None:
    """Infer the cadence from the spacing of scheduled generators.

    Used when the header does not declare an interval (or declares something
    unusable, e.g. ``(MIX)``). The most common gap between consecutive marks is
    the cadence; occasional shifts for holidays or shutdowns are outvoted rather
    than averaged in.

    Returns ``None`` when there are fewer than two marks.
    """
    ordered = sorted({int(w) for w in marks})
    if len(ordered) < 2:
        return None
    gaps = [b - a for a, b in zip(ordered, ordered[1:]) if 1 <= b - a <= MAX_INTERVAL_WEEKS]
    if not gaps:
        return None
    # Counter.most_common breaks ties by insertion order; sort so the result is
    # deterministic regardless of the order gaps happen to appear in.
    best = max(Counter(gaps).items(), key=lambda kv: (kv[1], -kv[0]))
    return best[0]


def split_account_and_name(header: str) -> tuple[str | None, str]:
    """Split a header into its leading account number and the display name.

    ``"00631/0105MAK    CII Chandler 2, Chandler, AZ (7)"`` yields
    ``("00631", "CII Chandler 2, Chandler, AZ (7)")``. Leading zeros are kept:
    they are part of the account code.
    """
    text = str(header).strip()
    match = _LEADING_ACCOUNT.match(text)
    if not match:
        return None, text
    account = match.group(1)
    # Drop the matched number plus any secondary code glued to it
    # (e.g. "/0105MAK", "& 416", a bare "/") before the readable name begins.
    # A secondary code must start with a digit, so "00620/  MIS Colorado Springs"
    # keeps "MIS" instead of mistaking it for part of the code.
    remainder = text[match.end():]
    remainder = re.sub(r"^\s*(?:[/&]\s*(?:\d\w*)?\s*)*", "", remainder).strip()
    return account, remainder or text


def infer_country(header: str) -> str:
    """Infer the site's country from the header text.

    Defaults to ``"usa"``: the Master Planner is overwhelmingly US sites, and
    non-US ones name their country or an unambiguous city. Matching uses word
    boundaries so ``"Swedish Medical Center, Bronx, NY"`` stays a US site.
    """
    text = str(header).lower()
    for country, pattern in _COUNTRY_PATTERNS:
        if re.search(pattern, text):
            return country
    return COUNTRY_USA


def is_placeholder_name(name: str) -> bool:
    """True when a header carries no readable customer name (e.g. ``"????"``)."""
    return bool(_PLACEHOLDER.match(str(name)))


def derive_site(
    column: PlannerColumn,
    generated_code: str,
    horizon_weeks: int = 52,
) -> DerivedSite:
    """Turn one Master Planner column into a site row.

    Parameters
    ----------
    column : PlannerColumn
        The extracted column.
    generated_code : str
        Deterministic fallback code, used when the header has no account number.
        Supplied by the caller so it stays identical to the one the Master
        Planner parser assigns (keeping the Comparison tool's matching intact).
    horizon_weeks : int
        Planning horizon; marks outside ``1..horizon_weeks`` are ignored.

    Notes
    -----
    Cadence precedence is header, then observed gaps, then one-time. The header
    is trusted first because it is the team's stated intent: a column with a
    single mark and ``(7)`` in its header is a weekly-cadence site that started
    late, not a one-off delivery.
    """
    header = str(column.header).strip()
    account, name = split_account_and_name(header)
    notes: list[str] = []

    marks = tuple(w for w in sorted(set(column.marks)) if 1 <= w <= horizon_weeks)

    if account:
        site_code, code_source = account, "account number"
    else:
        site_code, code_source = generated_code, "generated"
        notes.append(
            "No account number in the Master Planner header; a stable code was "
            "generated. Add it to the header to keep the two files linked."
        )

    header_interval = parse_header_interval(header)
    if header_interval is not None:
        interval, interval_source = header_interval, SOURCE_HEADER
        observed = derive_interval_from_marks(marks)
        if observed is not None and observed != header_interval:
            notes.append(
                f"Header says every {header_interval} weeks, but the scheduled "
                f"weeks are {observed} weeks apart. The header value was used."
            )
    else:
        observed = derive_interval_from_marks(marks)
        if observed is not None:
            interval, interval_source = observed, SOURCE_GAPS
            notes.append(
                f"No interval in the header; every {observed} weeks was read "
                f"from the spacing of the scheduled weeks."
            )
        else:
            interval, interval_source = 0, SOURCE_ONE_TIME
            if marks:
                notes.append(
                    "Only one scheduled week and no interval in the header; "
                    "treated as a one-time delivery."
                )

    active = bool(marks)
    if not active:
        notes.append(
            "No scheduled generators in the selected year; marked inactive. "
            "Set Active to Y and fill in a start week to include this site."
        )

    if is_placeholder_name(name):
        notes.append("Header has no readable customer name.")

    country = infer_country(header)
    # The shading is the authoritative EU-restricted signal. If the header text
    # does not also name a restricted country the optimizer would ship this site
    # from either supplier, so the mismatch has to be surfaced.
    if column.eu_restricted and country not in ROW_COUNTRIES:
        notes.append(
            f"Shaded as EU-restricted in the Master Planner but the header reads "
            f"as '{country}'. Set the correct country so the supply constraint "
            f"is applied."
        )

    return DerivedSite(
        site_code=site_code,
        site_name=name,
        active=active,
        next_demand_week=marks[0] if marks else 1,
        interval_weeks=interval,
        country=country,
        eu_restricted=bool(column.eu_restricted),
        column_header=header,
        column_letter=column.column_letter,
        code_source=code_source,
        interval_source=interval_source,
        deliveries=len(marks),
        notes=notes,
    )


def deduplicate_codes(sites: list[DerivedSite]) -> list[str]:
    """Suffix repeated site codes so every row has a unique key.

    The Master Planner occasionally carries the same account number on two
    columns (a retired column kept alongside its replacement). The optimizer
    drops duplicate Site_IDs, so the first occurrence keeps the bare code and
    later ones become ``00460-2``, ``00460-3``. Returns the warnings raised.
    """
    seen: dict[str, int] = {}
    warnings: list[str] = []
    for site in sites:
        base = site.site_code
        count = seen.get(base, 0) + 1
        seen[base] = count
        if count > 1:
            site.site_code = f"{base}-{count}"
            note = (
                f"Master Planner has more than one column with code '{base}'. "
                f"This one was renamed '{site.site_code}' to keep codes unique — "
                f"please correct the header."
            )
            site.notes.append(note)
            warnings.append(f"{site.site_code}: {note}")
    return warnings


def derive_sites(
    columns: list[PlannerColumn],
    code_generator,
    horizon_weeks: int = 52,
) -> tuple[list[DerivedSite], list[str]]:
    """Derive every site from the extracted columns.

    Parameters
    ----------
    columns : list[PlannerColumn]
        Customer columns from the Master Planner, in sheet order.
    code_generator : Callable[[str], str]
        Produces the fallback code for a header with no account number.
    horizon_weeks : int
        Planning horizon.

    Returns
    -------
    (sites, warnings)
        Sites in sheet order, plus workbook-level warnings worth showing the
        planner. Per-site detail stays on each :class:`DerivedSite`.
    """
    sites = [
        derive_site(col, code_generator(col.header), horizon_weeks)
        for col in columns
    ]
    warnings = deduplicate_codes(sites)
    return sites, warnings


# ---------------------------------------------------------------------------
# Tabular views
# ---------------------------------------------------------------------------
# These shape derived sites into the three tables the planner works with. They
# live here, not in the adapter, so the service layer can build them without
# reaching across into ``io_adapters``.

SITES_COLUMNS = [
    "Site_ID", "Site_Name", "Active", "Next_Demand_Week",
    "Interval_Weeks", "Country", "EU_Restricted",
]

MAPPING_COLUMNS = [
    "Site_ID", "Site_Name", "Master_Planner_Column", "Master_Planner_Header",
    "Code_Source", "Interval_Weeks", "Interval_Source",
    "Scheduled_Deliveries", "First_Scheduled_Week",
]

NOTES_COLUMNS = ["Site_ID", "Site_Name", "Master_Planner_Column", "Note"]


def sites_frame(sites: list[DerivedSite]) -> pd.DataFrame:
    """The optimizer input sheet: one row per site, canonical column order."""
    rows = [
        {
            "Site_ID": s.site_code,
            "Site_Name": s.site_name,
            "Active": "Y" if s.active else "N",
            "Next_Demand_Week": int(s.next_demand_week),
            "Interval_Weeks": int(s.interval_weeks),
            "Country": s.country,
            "EU_Restricted": "Y" if s.eu_restricted else "N",
        }
        for s in sites
    ]
    return pd.DataFrame(rows, columns=SITES_COLUMNS)


def mapping_frame(sites: list[DerivedSite]) -> pd.DataFrame:
    """Each site code beside the Master Planner column it was derived from.

    This is the table that keeps the two files linked: paste the codes into the
    Master Planner headers and the Comparison tab can match them from then on.
    """
    rows = [
        {
            "Site_ID": s.site_code,
            "Site_Name": s.site_name,
            "Master_Planner_Column": s.column_letter,
            "Master_Planner_Header": s.column_header,
            "Code_Source": s.code_source,
            "Interval_Weeks": int(s.interval_weeks),
            "Interval_Source": s.interval_source,
            "Scheduled_Deliveries": int(s.deliveries),
            "First_Scheduled_Week": int(s.next_demand_week) if s.active else None,
        }
        for s in sites
    ]
    return pd.DataFrame(rows, columns=MAPPING_COLUMNS)


def notes_frame(sites: list[DerivedSite]) -> pd.DataFrame:
    """One row per thing the planner should check."""
    rows = [
        {
            "Site_ID": s.site_code,
            "Site_Name": s.site_name,
            "Master_Planner_Column": s.column_letter,
            "Note": note,
        }
        for s in sites
        for note in s.notes
    ]
    return pd.DataFrame(rows, columns=NOTES_COLUMNS)
