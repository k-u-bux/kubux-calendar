"""
Kubux Calendar Backend Module

This module provides the core functionality for calendar operations:
- Configuration parsing (config.py)
- CalDAV client for Nextcloud (caldav_client.py)
- ICS subscription fetching (ics_subscription.py)
- Unified event store (event_store.py)
"""

from .config import Config
from .caldav_client import CalDAVClient
from .ics_subscription import ICSSubscription
from .event_store import EventStore, Event

__all__ = ['Config', 'CalDAVClient', 'ICSSubscription', 'EventStore', 'Event']
