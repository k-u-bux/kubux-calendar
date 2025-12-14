"""
Kubux Calendar Backend Module

Three-tier event model:
- CalEvent: Master event (what gets synced)
- EventInstance: A specific occurrence (expanded from CalEvent)
- InstanceSlice: Display portion on single day (maps to GUI rectangle)

Modules:
- config.py: Configuration parsing
- caldav_client.py: CalDAV client for Nextcloud, returns CalEvent
- ics_subscription.py: ICS subscription fetching, returns CalEvent
- event_wrapper.py: CalEvent, EventInstance, InstanceSlice definitions
- event_repository.py: Stores CalEvent, expands to EventInstance
- event_store.py: High-level API for GUI
"""

from .config import Config
from .caldav_client import CalDAVClient, CalendarInfo
from .ics_subscription import ICSSubscription, ICSSubscriptionManager
from .event_wrapper import (
    CalEvent,
    CalendarSource,
    EventInstance,
    InstanceSlice,
    create_instance,
    create_slices,
)
from .event_repository import EventRepository
from .event_store import EventStore

__all__ = [
    'Config',
    'CalDAVClient',
    'CalendarInfo',
    'ICSSubscription',
    'ICSSubscriptionManager',
    'EventStore',
    'CalEvent',
    'CalendarSource',
    'EventInstance',
    'InstanceSlice',
    'create_instance',
    'create_slices',
    'EventRepository',
]
