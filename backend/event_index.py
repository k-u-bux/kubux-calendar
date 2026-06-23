"""
IntervalTree-based event index for Kubux Calendar v2.

Wraps :class:`IntervalTree` to provide O(log n + k) range queries
over :class:`ImmutableEvent` objects.  All times are stored in UTC
for consistent cross-timezone comparison.
"""

from datetime import datetime
from typing import Optional

from .interval_tree import IntervalTree, IntervalHandle
from .event import ImmutableEvent
from .timezone_utils import to_utc


class EventIndex:
    """
    In-memory spatial index for events keyed on UTC time intervals.

    Each event is indexed by its master start/end in UTC.  Recurring
    events with master intervals far outside the query range would be
    missed by the interval tree, so they are stored separately and
    always included in query results.
    """

    def __init__(self):
        self._tree: IntervalTree[datetime] = IntervalTree()
        self._handles: dict[str, IntervalHandle[datetime]] = {}   # uid → handle
        self._recurring: dict[str, ImmutableEvent] = {}           # uid → event (always included in queries)

    # === Mutation ===========================================================

    def add(self, event: ImmutableEvent) -> None:
        """Index *event* by its UTC time range.  Replaces if UID exists."""
        if event.uid in self._handles:
            self.remove(event.uid)
        handle = self._tree.insert(event.start_utc, event.end_utc, event)
        self._handles[event.uid] = handle
        if event.is_recurring:
            self._recurring[event.uid] = event

    def remove(self, uid: str) -> None:
        """Remove event from index.  No-op if not present."""
        handle = self._handles.pop(uid, None)
        if handle is not None:
            self._tree.delete(handle)
        self._recurring.pop(uid, None)

    def clear(self) -> None:
        """Drop all entries."""
        self._tree = IntervalTree()
        self._handles.clear()
        self._recurring.clear()

    # === Queries ============================================================

    def query_range(self, start: datetime, end: datetime) -> list[ImmutableEvent]:
        """
        Return events whose UTC interval overlaps ``[start, end]``,
        plus all recurring events (master events are always included
        so the caller can expand instances for the query range).

        Both *start* and *end* should be timezone-aware (UTC preferred).
        """
        start_utc = to_utc(start)
        end_utc = to_utc(end)
        results: list[ImmutableEvent] = []
        self._tree.find_intersecting(start_utc, end_utc, lambda h: results.append(h.data))
        # Include all recurring events — their master interval may be
        # outside the query range but instances could fall inside.
        seen_uids = set(e.uid for e in results)
        for uid, ev in self._recurring.items():
            if uid not in seen_uids:
                results.append(ev)
                seen_uids.add(uid)
        return results

    def query_point(self, time: datetime) -> list[ImmutableEvent]:
        """Return events that cover a specific point in time."""
        t = to_utc(time)
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
        if handle:
            return handle.data
        return self._recurring.get(uid)
