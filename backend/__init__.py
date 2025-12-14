"""
Kubux Calendar Backend Module

This module provides the core functionality for calendar operations:
- Configuration parsing (config.py)
- CalDAV client for Nextcloud (caldav_client.py)
- ICS subscription fetching (ics_subscription.py)
- Unified event store (event_store.py)
- Event wrapper (event_wrapper.py) - CalEvent wraps icalendar.Event
- Event repository (event_repository.py) - unified storage with recurring_ical_events
"""

from .config import Config
from .caldav_client import CalDAVClient
from .ics_subscription import ICSSubscription
from .event_store import EventStore

# New architecture
from .event_wrapper import CalEvent, CalendarSource
from .event_repository import EventRepository, CalendarData

# Backward compatibility: Event is an alias for CalEvent during migration
# Once migration is complete, use CalEvent directly
try:
    from .caldav_client import EventData as Event
except ImportError:
    Event = CalEvent

__all__ = [
    'Config',
    'CalDAVClient',
    'ICSSubscription',
    'EventStore',
    'Event',
    # New architecture
    'CalEvent',
    'CalendarSource',
    'EventRepository',
    'CalendarData',
]
