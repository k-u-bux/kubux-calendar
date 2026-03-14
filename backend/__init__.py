"""
Kubux Calendar Backend v2.

Architecture:
- ImmutableEvent: Frozen event wrapping raw iCalendar data
- EventFS: Filesystem cache (one .ics file per event)
- EventIndex: IntervalTree for O(log n + k) range queries
- SyncManager: Background sync via TaskDispatcher
- EventStore: GUI-facing facade

Modules:
- config.py: TOML configuration parsing
- timezone_utils.py: Local timezone conversions
- event.py: ImmutableEvent, CalendarSource, EventView, RecurrenceRule
- event_fs.py: Filesystem cache with atomic writes
- event_index.py: IntervalTree wrapper
- network_ops.py: Pure CalDAV/ICS HTTP functions
- sync_manager.py: Background sync orchestration
- event_store.py: Unified API for GUI
- interval_tree.py: AVL-balanced augmented interval tree
- task_dispatch.py: Ticket-based async task dispatcher
"""

from .config import Config
from .event import (
    ImmutableEvent,
    CalendarSource,
    EventView,
    RecurrenceRule,
)
from .event_store import EventStore
from .task_dispatch import shutdown_tasks

__all__ = [
    "Config",
    "ImmutableEvent",
    "CalendarSource",
    "EventView",
    "RecurrenceRule",
    "EventStore",
    "shutdown_tasks",
]
