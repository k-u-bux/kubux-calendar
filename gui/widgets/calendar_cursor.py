"""A calendar position that may be invalid as a date.

The GUI's "where am I" state cannot be a :class:`datetime.date`.  Month
navigation has to keep the day-of-month *intent* even when it is not valid in
the month being viewed: stepping from Jan 31 into February must land on
"Feb 31" as an intent — rendered as Feb 28 — so that stepping back out
(Feb -> Mar) lands on Mar 31 and not on Mar 28.  A ``date`` cannot hold that
intent, so every month step would have to discard it permanently.

:class:`CalendarCursor` keeps the triple as an internal marker:

* ``year``  — any integer
* ``month`` — 1..12
* ``day``   — 1..31  (may be *invalid for* ``month``/``year``)

Nothing in this module interprets the triple as a calendar date.  Validity is
a *projection*, applied only where the triple is **used as a date** —
:meth:`CalendarCursor.resolve`, which clamps ``day`` to the month's length.
Callers hold the cursor unclamped and resolve at the edge (rendering,
persistence, comparisons).

Arithmetic:

* :meth:`CalendarCursor.shift_months` moves ``(year, month)`` with carry and
  leaves ``day`` untouched — the lossless month step.
* :meth:`CalendarCursor.shift_days` steps whole days.  Day arithmetic is only
  defined on a real date (there is no "next day" after Feb 31), so it resolves
  first and always writes back a valid triple.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta

__all__ = ["CalendarCursor"]


@dataclass(frozen=True)
class CalendarCursor:
    """A ``(year, month, day)`` position, possibly invalid as a date.

    Immutable: navigation returns a new cursor instead of mutating this one,
    so a cursor handed to a view can never be changed underneath it.
    """

    year: int
    month: int
    day: int

    def __post_init__(self):
        # The invariant.  Note what is *not* checked: whether ``day`` exists
        # in ``month``/``year`` — that is exactly the freedom this type needs.
        if not 1 <= self.month <= 12:
            raise ValueError(f"month out of range: {self.month}")
        if not 1 <= self.day <= 31:
            raise ValueError(f"day out of range: {self.day}")

    # -- construction ----------------------------------------------------

    @classmethod
    def from_date(cls, d: date) -> "CalendarCursor":
        """The cursor for a real calendar date."""
        return cls(d.year, d.month, d.day)

    # -- projection: the only place the triple is used as a date ----------

    def resolve(self) -> date:
        """Clamp to a real calendar date.

        ``day`` is truncated to the month's length — "Feb 31" resolves to
        Feb 28 (29 in a leap year) without destroying the stored intent.
        """
        return date(self.year, self.month,
                    min(self.day, calendar.monthrange(self.year, self.month)[1]))

    # -- navigation -------------------------------------------------------

    def shift_months(self, months: int) -> "CalendarCursor":
        """Move whole months with carry; ``day`` is preserved verbatim.

        The lossless step: Jan 31 -> (+1) -> "Feb 31" -> (+1) -> Mar 31.
        Nothing is clamped here — that is the point.
        """
        m = self.month - 1 + months
        return CalendarCursor(self.year + m // 12, m % 12 + 1, self.day)

    def shift_days(self, days: int) -> "CalendarCursor":
        """Move whole days, resolving first.

        A day step is only meaningful on a real date, so the triple is clamped
        before the step and the result is valid again.
        """
        return CalendarCursor.from_date(self.resolve() + timedelta(days=days))
