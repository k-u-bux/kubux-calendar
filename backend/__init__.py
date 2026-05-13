"""
Kubux Calendar Backend Module — v2.

Core types:
- ImmutableEvent: Frozen dataclass wrapping raw iCalendar data
- CalendarSource: Calendar metadata (mutable for UI state)
- EventView: Read-write display adapter for GUI
- RecurrenceRule: Simple recurrence spec for UI
- EventStore: Unified facade over EventFS, EventIndex, SyncManager
"""

from .config import Config
from .event import ImmutableEvent, CalendarSource, EventView, RecurrenceRule
from .event_fs import EventFS
from .event_index import EventIndex
from .sync_manager import SyncManager
from .event_store import EventStore

__all__ = [
    'Config',
    'EventStore',
    'ImmutableEvent',
    'CalendarSource',
    'EventView',
    'RecurrenceRule',
    'EventFS',
    'EventIndex',
    'SyncManager',
]