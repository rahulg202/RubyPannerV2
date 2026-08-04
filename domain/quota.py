"""Domain: quarterly supplier quota accounting (pure).

Groups the planning horizon into quarters (calendar-based when a reference week
is known, otherwise 13-week blocks), accumulates each supplier's ordered Sr-82
activity per quarter, and computes the shortfall penalty against the minimum
quota. See .kiro/specs/supplier-constraints/design.md, Requirement 6.

Partial quarters
----------------
A 52-week horizon only lines up with whole calendar quarters when it starts on a
quarter boundary. Otherwise the first and last quarters are *partial*: part of
the real commercial quarter falls outside the plan.

Such a quarter cannot be judged for quota compliance:

* the leading partial quarter is missing weeks in the **past**, whose orders
  already happened and are not visible to the planner;
* the trailing partial quarter is missing weeks in the **future**, beyond the
  horizon, in which ordering will continue.

Charging a full quarterly quota against a fragment therefore invents a shortfall.
Partial quarters are excluded from the penalty (they contribute nothing to the
objective) but are still reported, with a pro-rated target as a run-rate
reference, so nothing is hidden from the planner. Interior quarters are always
fully covered and are penalised normally.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import List, Sequence

from domain.dates import derive_week_dates
from domain.params import SupplierParams
from domain.supplier_allocation import BWXT, CURIUM, WeeklySupplierAllocation

WEEKS_PER_QUARTER_FALLBACK = 13

STATUS_OK = "OK"
STATUS_SHORTFALL = "SHORTFALL"
STATUS_PARTIAL = "Partial — not penalised"


@dataclass(frozen=True)
class QuarterSpan:
    """One quarter of the planning horizon and how much of it the plan covers."""

    quarter: int                    # 1-based index across the horizon
    weeks: tuple[int, ...]          # planning weeks falling in this quarter
    expected_weeks: int             # weeks the full calendar quarter contains
    is_partial: bool                # True when the plan covers only part of it

    @property
    def coverage(self) -> float:
        """Fraction of the real quarter the plan covers (0.0–1.0)."""
        if self.expected_weeks <= 0:
            return 1.0
        return min(1.0, len(self.weeks) / self.expected_weeks)


@dataclass
class QuarterlyQuotaStatus:
    """Quota status for one supplier in one quarter."""

    supplier: str
    quarter: int
    weeks: tuple[int, ...]
    quota_mci: float                # the full quarterly minimum
    ordered_mci: float              # activity ordered within the horizon
    remaining_mci: float            # quota - ordered (informational)
    shortfall_mci: float            # penalised shortfall; 0 for partial quarters
    penalty_usd: float              # shortfall x rate; 0 for partial quarters
    is_partial: bool = False
    weeks_covered: int = 0
    expected_weeks: int = WEEKS_PER_QUARTER_FALLBACK
    prorated_quota_mci: float = 0.0      # quota scaled to the weeks covered
    prorated_shortfall_mci: float = 0.0  # run-rate gap, informational only
    status: str = STATUS_OK

    @property
    def coverage(self) -> float:
        if self.expected_weeks <= 0:
            return 1.0
        return min(1.0, self.weeks_covered / self.expected_weeks)


def _quarter_key(mfg: date, reference: date, quarter_start_month: int) -> int:
    """Monotonic quarter index for a date, relative to the reference year."""
    months_since = (
        (mfg.year - reference.year) * 12 + (mfg.month - quarter_start_month)
    )
    return months_since // 3


def compute_quarter_boundaries(
    horizon_weeks: int,
    quarter_start_month: int,
    reference_week_date: date | None,
) -> List[QuarterSpan]:
    """Group the horizon into quarters, flagging any that are only partly covered.

    With a reference week, weeks are grouped by calendar quarter relative to
    ``quarter_start_month``. Without one, consecutive 13-week blocks are used.

    Only the first and last quarters can be partial — interior quarters are
    necessarily complete, because planning weeks run continuously.
    """
    if horizon_weeks < 1:
        return []

    if reference_week_date is None:
        spans: List[QuarterSpan] = []
        qnum = 0
        for start in range(1, horizon_weeks + 1, WEEKS_PER_QUARTER_FALLBACK):
            qnum += 1
            weeks = tuple(range(
                start, min(start + WEEKS_PER_QUARTER_FALLBACK, horizon_weeks + 1)
            ))
            spans.append(QuarterSpan(
                quarter=qnum,
                weeks=weeks,
                expected_weeks=WEEKS_PER_QUARTER_FALLBACK,
                # Week 1 is treated as a block boundary, so only a short tail
                # block is partial.
                is_partial=len(weeks) < WEEKS_PER_QUARTER_FALLBACK,
            ))
        return spans

    # Calendar-based grouping.
    week_dates = derive_week_dates(reference_week_date, 0, horizon_weeks)
    buckets: dict[int, List[int]] = {}
    for week, mfg, _cal in week_dates:
        key = _quarter_key(mfg, reference_week_date, quarter_start_month)
        buckets.setdefault(key, []).append(week)

    # Count the weeks each quarter would contain if the grid were extended past
    # both ends of the horizon. That gives the true size of the calendar quarter,
    # which is what "partial" must be measured against.
    from datetime import timedelta

    full_counts: dict[int, int] = {}
    for offset in range(-WEEKS_PER_QUARTER_FALLBACK - 2,
                        horizon_weeks + WEEKS_PER_QUARTER_FALLBACK + 3):
        mfg = reference_week_date + timedelta(days=7 * offset)
        key = _quarter_key(mfg, reference_week_date, quarter_start_month)
        if key in buckets:
            full_counts[key] = full_counts.get(key, 0) + 1

    spans = []
    for index, key in enumerate(sorted(buckets), start=1):
        weeks = tuple(buckets[key])
        expected = full_counts.get(key, len(weeks))
        spans.append(QuarterSpan(
            quarter=index,
            weeks=weeks,
            expected_weeks=expected,
            is_partial=len(weeks) < expected,
        ))
    return spans


def check_quarterly_quota(
    allocations: Sequence[WeeklySupplierAllocation],
    supplier_params: SupplierParams,
    quarter_boundaries: Sequence[QuarterSpan],
) -> List[QuarterlyQuotaStatus]:
    """Compute per-supplier per-quarter quota status and shortfall penalty.

    Partial quarters are reported but never penalised — see the module docstring
    for why. Their ``prorated_shortfall_mci`` is a run-rate indicator only.
    """
    by_week = {a.week: a for a in allocations}
    rate = supplier_params.quota_shortfall_penalty_rate

    suppliers = (
        (CURIUM, supplier_params.curium_quarterly_quota_mci,
         lambda a: a.curium_activity_mci),
        (BWXT, supplier_params.bwxt_quarterly_quota_mci,
         lambda a: a.bwxt_activity_mci),
    )

    statuses: List[QuarterlyQuotaStatus] = []
    for span in quarter_boundaries:
        for name, quota, getter in suppliers:
            ordered = sum(getter(by_week[w]) for w in span.weeks if w in by_week)
            prorated_quota = quota * span.coverage
            prorated_gap = max(0.0, prorated_quota - ordered)

            if span.is_partial:
                # Not judgeable: part of this commercial quarter lies outside the
                # plan, so no penalty is charged.
                shortfall = 0.0
                penalty = 0.0
                status = STATUS_PARTIAL
            else:
                shortfall = max(0.0, quota - ordered)
                penalty = shortfall * rate
                status = STATUS_SHORTFALL if shortfall > 0 else STATUS_OK

            statuses.append(QuarterlyQuotaStatus(
                supplier=name,
                quarter=span.quarter,
                weeks=span.weeks,
                quota_mci=quota,
                ordered_mci=ordered,
                remaining_mci=quota - ordered,
                shortfall_mci=shortfall,
                penalty_usd=penalty,
                is_partial=span.is_partial,
                weeks_covered=len(span.weeks),
                expected_weeks=span.expected_weeks,
                prorated_quota_mci=prorated_quota,
                prorated_shortfall_mci=prorated_gap,
                status=status,
            ))
    return statuses


def partial_quarter_note(statuses: Sequence[QuarterlyQuotaStatus]) -> str | None:
    """Return a short note naming the excluded partial quarters, else None."""
    partial = sorted({
        (s.quarter, s.weeks_covered, s.expected_weeks)
        for s in statuses if s.is_partial
    })
    if not partial:
        return None
    detail = ", ".join(
        f"Q{q} {covered}/{expected} wks" for q, covered, expected in partial
    )
    return (
        f"{detail} fall partly outside the 52-week plan, so quota compliance "
        "can't be judged for them — no penalty is charged. Target is pro-rated "
        "as a run-rate guide only."
    )
