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
