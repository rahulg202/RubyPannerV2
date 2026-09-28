"""Domain: typed exception hierarchy.

Services translate these into user-facing messages; the domain raises them so
failure modes are explicit rather than surfacing as bare ``ValueError`` or
``RuntimeError``.
"""

from __future__ import annotations


class RubyFillError(Exception):
    """Base class for all Ruby Fill Optimizer domain errors."""


class ValidationError(RubyFillError):
    """Raised when a parameter or input value fails validation.

    Attributes
    ----------
    errors : list[str]
        One or more human-readable validation messages.
    """

    def __init__(self, errors: str | list[str]) -> None:
        self.errors: list[str] = [errors] if isinstance(errors, str) else list(errors)
        super().__init__("; ".join(self.errors))


class InfeasiblePlanError(RubyFillError):
    """Raised when the solver cannot produce a feasible plan.

    Wraps the conditions the existing DP solver currently signals via
    ``RuntimeError`` (no feasible states, or terminal inventory not reachable).
    """


class InfeasibleAllocationError(RubyFillError):
    """Raised when a weekly production plan cannot be allocated to suppliers.

    Typically because a supplier is unavailable in a week that still requires
    its material (e.g. Curium unavailable in a week with EU-restricted demand).
    """

    def __init__(self, message: str | list[str], week: int | None = None) -> None:
        self.week = week
        messages = [message] if isinstance(message, str) else list(message)
        if week is not None:
            messages = [f"Week {week}: {m}" for m in messages]
        self.messages = messages
        super().__init__("; ".join(messages))


class ShippingCapInfeasibleError(InfeasiblePlanError):
    """Raised when the QC shipping cap cannot be satisfied for a week.

    A week delivering more EU-restricted units than can ship at once needs the
    excess already in stock at the end of the previous week. When that excess
    exceeds everything the remaining horizon could possibly hold, no plan exists.

    This used to surface as the generic "check shutdown weeks and demand — total
    capacity may be insufficient", which pointed the planner at the wrong input
    entirely. Naming the week and the cap is the whole value of the type.
    """

    def __init__(
        self,
        week: int,
        cap: int,
        required: int,
        available: int,
    ) -> None:
        self.week = week
        self.cap = cap
        self.required = required
        self.available = available
        super().__init__(
            f"Week {week} needs {required} EU-restricted unit(s) in stock "
            f"beforehand, because {required + cap} are due and only {cap} can "
            f"ship in a week. At most {available} can be held at that point, so "
            f"no plan can satisfy it. Either raise the QC shipping cap, or spread "
            f"the EU-restricted deliveries currently landing in week {week}."
        )


class StaleResultError(RubyFillError):
    """Raised when a stored result is used against different settings.

    Settings are per-session and a result is a snapshot of the settings that
    produced it. Comparing a plan solved under one set of cost rates against a
    baseline evaluated under another produces a number that describes nothing.
    Hooking every one of the 31 settings widgets to clear results would be
    fragile; stamping results with a fingerprint and refusing the mismatch makes
    a missed invalidation visible instead.
    """

    def __init__(
        self,
        expected: str = "",
        actual: str = "",
        what: str = "result",
    ) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"The {what} was produced under different settings than are now in "
            f"force, so the two cannot be compared. Re-run the Cost Optimizer to "
            f"refresh it, then try again."
        )
