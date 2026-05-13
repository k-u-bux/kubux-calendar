"""
IntervalTree-based event index for Kubux Calendar v2.

Wraps :class:`IntervalTree` to provide O(log n + k) range queries
over :class:`ImmutableEvent` objects.  All times are stored in UTC
for consistent cross-timezone comparison.
"""

from datetime import datetime
from typing import Optional

import pytz

from .interval_tree import IntervalTree, IntervalHandle
from .event import ImmutableEvent


class EventIndex:
    """
    In-memory spatial index for events keyed on UTC time intervals.

    Each event is indexed by its master start/end in UTC.  Recurring
    instance expansion is **not** handled here — the caller must
    expand recurrences before inserting individual instances, or query
    with a range that encompasses the master event and filter later.

    For this calendar the typical usage is:

    1. Insert master events (non-recurring use master times).
    2. Query a display range to get candidate UIDs.
    3. Expand recurring events via ``recurring_ical_events`` for the
       display range.
    """

    def __init__(self):
        self._tree: IntervalTree[datetime] = IntervalTree()
        self._handles: dict[str, IntervalHandle[datetime]] = {}   # uid → handle

    # === Mutation ===========================================================

    def add(self, event: ImmutableEvent) -> None:
        """Index *event* by its UTC time range.  Replaces if UID exists."""
        if event.uid in self._handles:
            self.remove(event.uid)
        handle = self._tree.insert(event.start_utc, event.end_utc, event)
        self._handles[event.uid] = handle

    def remove(self, uid: str) -> None:
        """Remove event from index.  No-op if not present."""
        handle = self._handles.pop(uid, None)
        if handle is not None:
            self._tree.delete(handle)

    def clear(self) -> None:
        """Drop all entries."""
        self._tree = IntervalTree()
        self._handles.clear()

    # === Queries ============================================================

    def query_range(self, start: datetime, end: datetime) -> list[ImmutableEvent]:
        """
        Return events whose UTC interval overlaps ``[start, end]``.

        Both *start* and *end* should be timezone-aware (UTC preferred).
        """
        start_utc = start.astimezone(pytz.UTC) if start.tzinfo else pytz.UTC.localize(start)
        end_utc = end.astimezone(pytz.UTC) if end.tzinfo else pytz.UTC.localize(end)
        results: list[ImmutableEvent] = []
        self._tree.find_intersecting(start_utc, end_utc, lambda h: results.append(h.data))
        return results

    def query_point(self, time: datetime) -> list[ImmutableEvent]:
        """Return events that cover a specific point in time."""
        t = time.astimezone(pytz.UTC) if time.tzinfo else pytz.UTC.localize(time)
        results: list[ImmutableEvent] = []
        self._tree.find_overlapping(t, lambda h: results.append(h.data))
        return results

    # === Introspection ======================================================

    def __len__(self) -> int:
        return len(self._handles)

    def __contains__(self, uid: str) -> bool:
        return uid in self._handles

    def get(self, uid: str) -> Optional[ImmutableEvent]:
        """Return the indexed event for *uid*, or *None*."""
        handle = self._handles.get(uid)
        return handle.data if handle else None
