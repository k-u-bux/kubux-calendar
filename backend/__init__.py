"""
Kubux Calendar Backend Module

This module provides the core functionality for calendar operations:
- Configuration parsing (config.py)
- CalDAV client for Nextcloud (caldav_client.py)
- ICS subscription fetching (ics_subscription.py)
- Event wrapper (event_wrapper.py) - CalEvent wraps icalendar.Event
- Event repository (event_repository.py) - unified storage with recurring_ical_events
- Event store (event_store.py) - high-level API for GUI
"""

from .config import Config
from .caldav_client import CalDAVClient, CalendarInfo
from .ics_subscription import ICSSubscription, ICSSubscriptionManager
from .event_wrapper import CalEvent, CalendarSource
from .event_repository import EventRepository, CalendarData

# EventStore uses the new architecture
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
    'EventRepository',
    'CalendarData',
]
