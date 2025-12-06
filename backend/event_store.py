"""
Unified Event Store for Kubux Calendar.

Provides a single interface to access events from all sources (CalDAV and ICS).
"""

import json
from datetime import datetime, timedelta
from typing import Optional, Callable
from dataclasses import dataclass, field, asdict
from pathlib import Path
import pytz

from .config import Config, NextcloudAccount, ICSSubscription as ICSSubscriptionConfig
from .caldav_client import CalDAVClient, CalendarInfo, EventData, RecurrenceRule
from .ics_subscription import ICSSubscription, ICSSubscriptionManager


@dataclass
class CalendarSource:
    """Unified calendar source representation."""
    id: str
    name: str
    color: str
    source_type: str  # "caldav" or "ics"
    account_name: str = ""  # For CalDAV calendars
    url: str = ""
    writable: bool = False
    visible: bool = True
    
    # Internal references
    _caldav_calendar: Optional[CalendarInfo] = field(default=None, repr=False)
    _ics_subscription: Optional[ICSSubscription] = field(default=None, repr=False)


# Re-export EventData as Event for cleaner API
Event = EventData


class EventStore:
    """
    Unified event store combining CalDAV and ICS sources.
    
    Provides a single API for the GUI to:
    - List all calendar sources
    - Query events across all sources
    - Create/update/delete events (for writable calendars)
    - Manage calendar visibility
    """
    
    # Cache window: fetch ±2 months around the center of requested date range
    CACHE_WINDOW_MONTHS = 2
    
    def __init__(self, config: Config):
        """
        Initialize the event store.
        
        Args:
            config: Application configuration
        """
        self.config = config
        
        self._caldav_clients: dict[str, CalDAVClient] = {}
        self._ics_manager = ICSSubscriptionManager()
        self._calendars: dict[str, CalendarSource] = {}
        self._visibility: dict[str, bool] = {}
        self._colors: dict[str, str] = {}
        
        # Event cache for performance
        self._cached_events: list[Event] = []
        self._cache_start: Optional[datetime] = None
        self._cache_end: Optional[datetime] = None
        
        # State file path
        self._state_file = config.state_file
        
        # Callback for notifying GUI of changes
        self._on_change_callback: Optional[Callable[[], None]] = None
    
    def set_on_change_callback(self, callback: Callable[[], None]) -> None:
        """Set a callback to be invoked when data changes."""
        self._on_change_callback = callback
    
    def _notify_change(self) -> None:
        """Notify listeners of data changes."""
        if self._on_change_callback:
            self._on_change_callback()
    
    def initialize(self) -> bool:
        """
        Initialize all calendar sources from configuration.
        
        Returns:
            True if at least one source connected successfully.
        """
        success = False
        
        # Load saved visibility state
        self._load_state()
        
        # Initialize CalDAV clients
        for account in self.config.nextcloud_accounts:
            try:
                password = account.get_password(self.config.password_program)
                client = CalDAVClient(
                    url=account.url,
                    username=account.username,
                    password=password,
                    account_name=account.name
                )
                
                if client.connect():
                    self._caldav_clients[account.name] = client
                    
                    # Get calendars from this account
                    calendars = client.get_calendars()
                    for cal in calendars:
                        cal_id = f"caldav:{account.name}:{cal.id}"
                        # Use saved color if available, otherwise use calendar or account color
                        default_color = cal.color if cal.color != "#4285f4" else account.color
                        source = CalendarSource(
                            id=cal_id,
                            name=cal.name,
                            color=self._colors.get(cal_id, default_color),
                            source_type="caldav",
                            account_name=account.name,
                            url=cal.url,
                            writable=cal.writable,
                            visible=self._visibility.get(cal_id, True),
                            _caldav_calendar=cal
                        )
                        self._calendars[source.id] = source
                    
                    success = True
                else:
                    print(f"Failed to connect to Nextcloud account: {account.name}")
            
            except Exception as e:
                print(f"Error initializing Nextcloud account {account.name}: {e}")
        
        # Initialize ICS subscriptions
        for sub_config in self.config.ics_subscriptions:
            try:
                sub = self._ics_manager.add_subscription(
                    name=sub_config.name,
                    url=sub_config.url,
                    color=sub_config.color
                )
                
                sub_id = f"ics:{sub.id}"
                source = CalendarSource(
                    id=sub_id,
                    name=sub.name,
                    color=self._colors.get(sub_id, sub.color),
                    source_type="ics",
                    url=sub.url,
                    writable=False,
                    visible=self._visibility.get(sub_id, True),
                    _ics_subscription=sub
                )
                self._calendars[source.id] = source
                success = True
            
            except Exception as e:
                print(f"Error initializing ICS subscription {sub_config.name}: {e}")
        
        return success
    
    def get_calendars(self, visible_only: bool = False) -> list[CalendarSource]:
        """
        Get all calendar sources.
        
        Args:
            visible_only: If True, only return visible calendars
        
        Returns:
            List of CalendarSource objects.
        """
        calendars = list(self._calendars.values())
        if visible_only:
            calendars = [c for c in calendars if c.visible]
        return calendars
    
    def get_calendar(self, calendar_id: str) -> Optional[CalendarSource]:
        """Get a specific calendar by ID."""
        return self._calendars.get(calendar_id)
    
    def set_calendar_visibility(self, calendar_id: str, visible: bool) -> None:
        """
        Set visibility of a calendar.
        
        Args:
            calendar_id: Calendar ID
            visible: Whether to show this calendar
        """
        if calendar_id in self._calendars:
            self._calendars[calendar_id].visible = visible
            self._visibility[calendar_id] = visible
            self._save_state()
            self._notify_change()
    
    def set_calendar_color(self, calendar_id: str, color: str) -> None:
        """
        Set color of a calendar.
        
        Args:
            calendar_id: Calendar ID
            color: Hex color string (e.g., "#ff0000")
        """
        if calendar_id in self._calendars:
            self._calendars[calendar_id].color = color
            self._colors[calendar_id] = color
            self._save_state()
            self._notify_change()
    
    def _is_cache_valid(self, start: datetime, end: datetime) -> bool:
        """Check if the requested range is within the cache window."""
        if self._cache_start is None or self._cache_end is None:
            return False
        if not self._cached_events:
            return False
        return start >= self._cache_start and end <= self._cache_end
    
    def _calculate_cache_window(self, start: datetime, end: datetime) -> tuple[datetime, datetime]:
        """Calculate the cache window (±2 months around the center of the requested range)."""
        # Find the center of the requested range
        center = start + (end - start) / 2
        
        # Calculate ±2 months window
        # Use a simple 30-day month approximation for speed
        months = self.CACHE_WINDOW_MONTHS
        window_start = center - timedelta(days=months * 30)
        window_end = center + timedelta(days=months * 30)
        
        return window_start, window_end
    
    def _fetch_events_into_cache(self, start: datetime, end: datetime) -> None:
        """Fetch events from all sources and store in cache."""
        import sys
        print(f"DEBUG: Fetching events into cache for range {start.date()} to {end.date()}", file=sys.stderr)
        
        all_events = []
        
        # Fetch from all calendars (not just visible ones, for flexibility)
        for calendar in self._calendars.values():
            if calendar.source_type == "caldav":
                # Get events from CalDAV
                if calendar._caldav_calendar is not None:
                    account_name = calendar.account_name
                    client = self._caldav_clients.get(account_name)
                    if client:
                        events = client.get_events(
                            calendar._caldav_calendar,
                            start, end
                        )
                        # Store calendar info with events
                        for event in events:
                            event.calendar_color = calendar.color
                            event._source_calendar_id = calendar.id
                        all_events.extend(events)
            
            elif calendar.source_type == "ics":
                # Get events from ICS subscription
                if calendar._ics_subscription is not None:
                    events = calendar._ics_subscription.get_events(start, end)
                    # Store calendar info with events
                    for event in events:
                        event.calendar_color = calendar.color
                        event._source_calendar_id = calendar.id
                    all_events.extend(events)
        
        # Update cache
        self._cached_events = all_events
        self._cache_start = start
        self._cache_end = end
        
        print(f"DEBUG: Cached {len(all_events)} events", file=sys.stderr)
    
    def invalidate_cache(self) -> None:
        """Invalidate the event cache. Call this when events are modified."""
        self._cached_events = []
        self._cache_start = None
        self._cache_end = None
    
    def get_events(
        self,
        start: datetime,
        end: datetime,
        calendar_ids: Optional[list[str]] = None,
        visible_only: bool = True
    ) -> list[Event]:
        """
        Get events from specified calendars within a time range.
        
        Uses a cache with a ±2 month window for faster navigation.
        
        Args:
            start: Start of time range
            end: End of time range
            calendar_ids: List of calendar IDs to query (None = all)
            visible_only: If True, only query visible calendars
        
        Returns:
            List of Event objects sorted by start time.
        """
        # Check if we need to refresh the cache
        if not self._is_cache_valid(start, end):
            cache_start, cache_end = self._calculate_cache_window(start, end)
            self._fetch_events_into_cache(cache_start, cache_end)
        
        # Determine which calendars are visible
        if calendar_ids is None:
            visible_calendar_ids = {
                c.id for c in self._calendars.values()
                if not visible_only or c.visible
            }
        else:
            visible_calendar_ids = {
                cid for cid in calendar_ids
                if cid in self._calendars and
                (not visible_only or self._calendars[cid].visible)
            }
        
        # Helper to make datetime comparison work with mixed tz-aware/naive
        def make_comparable(dt: datetime) -> datetime:
            """Strip timezone info for comparison."""
            if dt.tzinfo is not None:
                return dt.replace(tzinfo=None)
            return dt
        
        start_cmp = make_comparable(start)
        end_cmp = make_comparable(end)
        
        # Filter events from cache
        filtered_events = []
        for event in self._cached_events:
            # Check if event is in the requested time range
            event_end = make_comparable(event.end)
            event_start = make_comparable(event.start)
            if event_end < start_cmp or event_start > end_cmp:
                continue
            
            # Check if calendar is visible
            source_cal_id = getattr(event, '_source_calendar_id', None)
            if source_cal_id and source_cal_id not in visible_calendar_ids:
                continue
            
            # Update calendar color in case it changed
            if source_cal_id and source_cal_id in self._calendars:
                event.calendar_color = self._calendars[source_cal_id].color
            
            filtered_events.append(event)
        
        # Sort by start time
        filtered_events.sort(key=lambda e: e.start)
        
        return filtered_events
    
    def create_event(
        self,
        calendar_id: str,
        summary: str,
        start: datetime,
        end: datetime,
        description: str = "",
        location: str = "",
        all_day: bool = False,
        recurrence: Optional[RecurrenceRule] = None
    ) -> Optional[Event]:
        """
        Create a new event.
        
        Args:
            calendar_id: Target calendar ID
            summary: Event title
            start: Start time
            end: End time
            description: Event description
            location: Event location
            all_day: Whether this is an all-day event
            recurrence: Optional recurrence rule
        
        Returns:
            Created Event object, or None on failure.
        """
        calendar = self._calendars.get(calendar_id)
        if calendar is None or not calendar.writable:
            return None
        
        if calendar.source_type != "caldav":
            return None  # Can only create in CalDAV calendars
        
        client = self._caldav_clients.get(calendar.account_name)
        if client is None or calendar._caldav_calendar is None:
            return None
        
        event = Event(
            uid="",  # Will be generated
            summary=summary,
            start=start,
            end=end,
            description=description,
            location=location,
            all_day=all_day,
            recurrence=recurrence
        )
        
        result = client.create_event(calendar._caldav_calendar, event)
        if result:
            self.invalidate_cache()  # Clear cache so new event is fetched
            self._notify_change()
        return result
    
    def update_event(self, event: Event) -> bool:
        """
        Update an existing event.
        
        Args:
            event: Event with updated data
        
        Returns:
            True if successful, False otherwise.
        """
        if event.read_only:
            return False
        
        # Find the client for this event
        calendar = self._calendars.get(f"caldav:{event.calendar_name}:{event.calendar_id}")
        if calendar is None:
            # Try to find by iterating
            for cal in self._calendars.values():
                if cal.source_type == "caldav" and cal._caldav_calendar and \
                   cal._caldav_calendar.id == event.calendar_id:
                    calendar = cal
                    break
        
        if calendar is None or calendar.source_type != "caldav":
            return False
        
        client = self._caldav_clients.get(calendar.account_name)
        if client is None:
            return False
        
        result = client.update_event(event)
        if result:
            self.invalidate_cache()  # Clear cache so updated event is fetched
            self._notify_change()
        return result
    
    def delete_event(self, event: Event) -> bool:
        """
        Delete an event.
        
        Args:
            event: Event to delete
        
        Returns:
            True if successful, False otherwise.
        """
        if event.read_only:
            return False
        
        # Find the client for this event
        for cal in self._calendars.values():
            if cal.source_type == "caldav" and cal._caldav_calendar and \
               cal._caldav_calendar.id == event.calendar_id:
                client = self._caldav_clients.get(cal.account_name)
                if client:
                    result = client.delete_event(event)
                    if result:
                        self.invalidate_cache()  # Clear cache so deleted event is removed
                        self._notify_change()
                    return result
        
        return False
    
    def delete_recurring_instance(self, event: Event, instance_start: datetime) -> bool:
        """
        Delete a specific instance of a recurring event.
        
        Args:
            event: The recurring event
            instance_start: Start time of the instance to delete
        
        Returns:
            True if successful, False otherwise.
        """
        if event.read_only or not event.is_recurring:
            return False
        
        for cal in self._calendars.values():
            if cal.source_type == "caldav" and cal._caldav_calendar and \
               cal._caldav_calendar.id == event.calendar_id:
                client = self._caldav_clients.get(cal.account_name)
                if client:
                    result = client.delete_recurring_instance(event, instance_start)
                    if result:
                        self.invalidate_cache()  # Clear cache so deleted instance is removed
                        self._notify_change()
                    return result
        
        return False
    
    def get_writable_calendars(self) -> list[CalendarSource]:
        """Get all calendars that can be written to."""
        return [c for c in self._calendars.values() if c.writable]
    
    def refresh(self, calendar_id: Optional[str] = None) -> None:
        """
        Refresh calendar data.
        
        Args:
            calendar_id: Specific calendar to refresh, or None for all
        """
        if calendar_id:
            calendar = self._calendars.get(calendar_id)
            if calendar and calendar.source_type == "ics" and calendar._ics_subscription:
                calendar._ics_subscription.fetch()
        else:
            # Refresh all ICS subscriptions
            self._ics_manager.fetch_all()
        
        # Invalidate cache so fresh data is fetched on next get_events call
        self.invalidate_cache()
        self._notify_change()
    
    def _load_state(self) -> None:
        """Load visibility and color state from file."""
        if self._state_file.exists():
            try:
                with open(self._state_file, 'r') as f:
                    state = json.load(f)
                    self._visibility = state.get('visibility', {})
                    self._colors = state.get('colors', {})
            except Exception as e:
                print(f"Error loading state: {e}")
    
    def _save_state(self) -> None:
        """Save visibility and color state to file."""
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            
            state = {
                'visibility': self._visibility,
                'colors': self._colors
            }
            
            with open(self._state_file, 'w') as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            print(f"Error saving state: {e}")
    
    def get_state(self) -> dict:
        """Get current state for persistence."""
        return {
            'visibility': self._visibility.copy(),
            'colors': self._colors.copy()
        }
    
    def set_state(self, state: dict) -> None:
        """Restore state from persistence."""
        self._visibility = state.get('visibility', {})
        self._colors = state.get('colors', {})
        
        # Apply visibility and colors to calendars
        for cal_id, visible in self._visibility.items():
            if cal_id in self._calendars:
                self._calendars[cal_id].visible = visible
        for cal_id, color in self._colors.items():
            if cal_id in self._calendars:
                self._calendars[cal_id].color = color
