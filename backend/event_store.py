"""
Unified Event Store for Kubux Calendar.

Provides a single interface to access events from all sources (CalDAV and ICS).
"""

import json
import uuid
from datetime import datetime, timedelta
from typing import Optional, Callable
from dataclasses import dataclass, field, asdict
from pathlib import Path
import pytz

from .config import Config, NextcloudAccount, ICSSubscription as ICSSubscriptionConfig
from .caldav_client import CalDAVClient, CalendarInfo, EventData, RecurrenceRule
from .ics_subscription import ICSSubscription, ICSSubscriptionManager
from .sync_queue import SyncQueue, SyncOperation, SyncStatus, PendingChange


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
        
        # Sync queue for offline-first operations
        queue_file = config.state_file.parent / "sync_queue.json"
        self._sync_queue = SyncQueue(queue_file)
        self._sync_queue.set_on_change_callback(self._on_sync_queue_changed)
        
        # Track last successful sync time
        self._last_sync_time: Optional[datetime] = None
        
        # Local events (created locally, not yet on server)
        self._local_events: dict[str, Event] = {}  # event_uid -> Event
        
        # Callback for notifying GUI of changes
        self._on_change_callback: Optional[Callable[[], None]] = None
        
        # Callback for sync status updates
        self._on_sync_status_callback: Optional[Callable[[int, Optional[datetime]], None]] = None
    
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
        
        # Add local (pending sync) events that haven't been synced yet
        for event in self._local_events.values():
            event_start = event.start.replace(tzinfo=None) if event.start.tzinfo else event.start
            event_end = event.end.replace(tzinfo=None) if event.end.tzinfo else event.end
            start_naive = start.replace(tzinfo=None) if start.tzinfo else start
            end_naive = end.replace(tzinfo=None) if end.tzinfo else end
            
            # Check if local event is in range
            if event_end >= start_naive and event_start <= end_naive:
                all_events.append(event)
        
        # Update cache
        self._cached_events = all_events
        self._cache_start = start
        self._cache_end = end
        
        print(f"DEBUG: Cached {len(all_events)} events (including {len(self._local_events)} local)", file=sys.stderr)
    
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
        Create a new event (offline-first).
        
        Creates a local event immediately and queues sync to server.
        The event will have sync_status="pending" until synced.
        
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
            Created Event object (local), or None on failure.
        """
        calendar = self._calendars.get(calendar_id)
        if calendar is None or not calendar.writable:
            return None
        
        if calendar.source_type != "caldav":
            return None  # Can only create in CalDAV calendars
        
        # Generate UID for new event
        event_uid = str(uuid.uuid4())
        
        # Create local event immediately
        event = Event(
            uid=event_uid,
            summary=summary,
            start=start,
            end=end,
            description=description,
            location=location,
            all_day=all_day,
            recurrence=recurrence,
            calendar_id=calendar._caldav_calendar.id if calendar._caldav_calendar else "",
            calendar_name=calendar.name,
            calendar_color=calendar.color,
            source_type="caldav",
            read_only=False,
            sync_status="pending",  # Mark as pending sync
        )
        event._source_calendar_id = calendar_id
        
        # Store locally
        self._local_events[event_uid] = event
        
        # Add to sync queue
        event_data = self._event_to_dict(event)
        self._sync_queue.add_create(calendar_id, event_uid, event_data)
        
        # Invalidate cache so event shows up
        self.invalidate_cache()
        self._notify_change()
        
        return event
    
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
        Refresh calendar data from all sources (CalDAV and ICS).
        
        Forces a fresh reconnection to CalDAV servers to ensure we get the latest data.
        
        Args:
            calendar_id: Specific calendar to refresh, or None for all
        """
        import sys
        print(f"DEBUG: Refresh called, calendar_id={calendar_id}", file=sys.stderr)
        
        if calendar_id:
            calendar = self._calendars.get(calendar_id)
            if calendar:
                if calendar.source_type == "ics" and calendar._ics_subscription:
                    calendar._ics_subscription.fetch()
                elif calendar.source_type == "caldav":
                    # Force reconnect to get fresh data from server
                    client = self._caldav_clients.get(calendar.account_name)
                    if client:
                        print(f"DEBUG: Reconnecting CalDAV client for {calendar.account_name}", file=sys.stderr)
                        if client.reconnect():
                            # Re-fetch all calendars from this account to get fresh objects
                            calendars = client.get_calendars()
                            for cal in calendars:
                                cal_id = f"caldav:{calendar.account_name}:{cal.id}"
                                if cal_id == calendar_id and cal_id in self._calendars:
                                    # Update the internal caldav calendar reference
                                    self._calendars[cal_id]._caldav_calendar = cal
                                    print(f"DEBUG: Refreshed CalDAV calendar: {cal_id}", file=sys.stderr)
                        else:
                            print(f"DEBUG: Failed to reconnect CalDAV client for {calendar.account_name}", file=sys.stderr)
        else:
            # Refresh ALL sources
            
            # 1. Refresh all CalDAV calendars by forcing reconnection
            for account_name, client in self._caldav_clients.items():
                try:
                    print(f"DEBUG: Reconnecting CalDAV client for {account_name}", file=sys.stderr)
                    if client.reconnect():
                        calendars = client.get_calendars()
                        for cal in calendars:
                            cal_id = f"caldav:{account_name}:{cal.id}"
                            if cal_id in self._calendars:
                                # Update the internal caldav calendar reference with fresh object
                                self._calendars[cal_id]._caldav_calendar = cal
                                print(f"DEBUG: Refreshed CalDAV calendar: {cal_id}", file=sys.stderr)
                    else:
                        print(f"DEBUG: Failed to reconnect CalDAV client for {account_name}", file=sys.stderr)
                except Exception as e:
                    print(f"DEBUG: Error refreshing CalDAV account {account_name}: {e}", file=sys.stderr)
            
            # 2. Refresh all ICS subscriptions
            self._ics_manager.fetch_all()
        
        # Invalidate cache so fresh data is fetched on next get_events call
        self.invalidate_cache()
        self._notify_change()
        print(f"DEBUG: Refresh complete, cache invalidated", file=sys.stderr)
    
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
    
    # ==================== Sync Queue Methods ====================
    
    def set_on_sync_status_callback(self, callback: Callable[[int, Optional[datetime]], None]) -> None:
        """
        Set callback for sync status updates.
        
        Args:
            callback: Function(pending_count, last_sync_time)
        """
        self._on_sync_status_callback = callback
    
    def _on_sync_queue_changed(self) -> None:
        """Handle sync queue changes - notify listeners."""
        self._notify_sync_status()
    
    def _notify_sync_status(self) -> None:
        """Notify listeners of sync status change."""
        if self._on_sync_status_callback:
            self._on_sync_status_callback(
                self._sync_queue.get_pending_count(),
                self._last_sync_time
            )
    
    def get_pending_sync_count(self) -> int:
        """Get number of pending changes waiting to sync."""
        return self._sync_queue.get_pending_count()
    
    def get_last_sync_time(self) -> Optional[datetime]:
        """Get time of last successful sync."""
        return self._last_sync_time
    
    def has_pending_sync(self, event_uid: str) -> bool:
        """Check if an event has pending sync changes."""
        # Handle expanded recurring events (UID_timestamp format)
        base_uid = event_uid.split('_')[0] if '_' in event_uid else event_uid
        return self._sync_queue.has_pending_for_event(base_uid)
    
    def sync_pending_changes(self) -> tuple[int, int]:
        """
        Attempt to sync all pending changes to the server.
        
        Returns:
            Tuple of (successful_count, failed_count)
        """
        import sys
        pending = self._sync_queue.get_pending_changes()
        if not pending:
            return (0, 0)
        
        print(f"DEBUG: Syncing {len(pending)} pending changes", file=sys.stderr)
        
        success_count = 0
        fail_count = 0
        
        for change in pending:
            try:
                self._sync_queue.mark_syncing(change.id)
                
                if change.operation == SyncOperation.CREATE:
                    result = self._sync_create(change)
                elif change.operation == SyncOperation.UPDATE:
                    result = self._sync_update(change)
                elif change.operation == SyncOperation.DELETE:
                    result = self._sync_delete(change)
                elif change.operation == SyncOperation.DELETE_INSTANCE:
                    result = self._sync_delete_instance(change)
                else:
                    result = False
                
                if result:
                    self._sync_queue.mark_synced(change.id)
                    # Remove from local events if it was there
                    if change.event_uid in self._local_events:
                        del self._local_events[change.event_uid]
                    success_count += 1
                else:
                    self._sync_queue.mark_failed(change.id, "Sync operation failed")
                    fail_count += 1
                    
            except Exception as e:
                print(f"DEBUG: Sync error for {change.id}: {e}", file=sys.stderr)
                self._sync_queue.mark_failed(change.id, str(e))
                fail_count += 1
        
        # Update last sync time if any succeeded
        if success_count > 0:
            self._last_sync_time = datetime.now()
            self.invalidate_cache()
            self._notify_change()
        
        self._notify_sync_status()
        return (success_count, fail_count)
    
    def _sync_create(self, change: PendingChange) -> bool:
        """Sync a CREATE operation to the server."""
        calendar = self._calendars.get(change.calendar_id)
        if calendar is None or calendar._caldav_calendar is None:
            return False
        
        client = self._caldav_clients.get(calendar.account_name)
        if client is None:
            return False
        
        # Reconstruct EventData from stored data
        event = self._event_from_dict(change.event_data)
        if event is None:
            return False
        
        result = client.create_event(calendar._caldav_calendar, event)
        return result is not None
    
    def _sync_update(self, change: PendingChange) -> bool:
        """Sync an UPDATE operation to the server."""
        # For updates, we need the original caldav_event reference
        # This is tricky because we may not have it if the app restarted
        # For now, try to find the event on the server and update it
        calendar = self._calendars.get(change.calendar_id)
        if calendar is None or calendar._caldav_calendar is None:
            return False
        
        client = self._caldav_clients.get(calendar.account_name)
        if client is None:
            return False
        
        event = self._event_from_dict(change.event_data)
        if event is None:
            return False
        
        # If we have the caldav_event reference, use it
        if event._caldav_event is not None:
            return client.update_event(event)
        
        # Otherwise, we need to refetch and update
        # This is a limitation of the current implementation
        return False
    
    def _sync_delete(self, change: PendingChange) -> bool:
        """Sync a DELETE operation to the server."""
        event = self._event_from_dict(change.event_data)
        if event is None or event._caldav_event is None:
            return False
        
        for cal in self._calendars.values():
            if cal.source_type == "caldav" and cal._caldav_calendar:
                client = self._caldav_clients.get(cal.account_name)
                if client:
                    return client.delete_event(event)
        
        return False
    
    def _sync_delete_instance(self, change: PendingChange) -> bool:
        """Sync a DELETE_INSTANCE operation to the server."""
        event = self._event_from_dict(change.event_data)
        if event is None or event._caldav_event is None or change.instance_start is None:
            return False
        
        instance_start = datetime.fromisoformat(change.instance_start)
        
        for cal in self._calendars.values():
            if cal.source_type == "caldav" and cal._caldav_calendar:
                client = self._caldav_clients.get(cal.account_name)
                if client:
                    return client.delete_recurring_instance(event, instance_start)
        
        return False
    
    def _event_to_dict(self, event: Event) -> dict:
        """Serialize an Event to a dictionary for storage."""
        return {
            "uid": event.uid,
            "summary": event.summary,
            "start": event.start.isoformat(),
            "end": event.end.isoformat(),
            "description": event.description,
            "location": event.location,
            "all_day": event.all_day,
            "calendar_id": event.calendar_id,
            "calendar_name": event.calendar_name,
            "calendar_color": event.calendar_color,
            "source_type": event.source_type,
            "read_only": event.read_only,
            "recurrence": self._recurrence_to_dict(event.recurrence) if event.recurrence else None,
        }
    
    def _event_from_dict(self, data: dict) -> Optional[Event]:
        """Deserialize an Event from a dictionary."""
        try:
            recurrence = None
            if data.get("recurrence"):
                recurrence = self._recurrence_from_dict(data["recurrence"])
            
            return Event(
                uid=data["uid"],
                summary=data["summary"],
                start=datetime.fromisoformat(data["start"]),
                end=datetime.fromisoformat(data["end"]),
                description=data.get("description", ""),
                location=data.get("location", ""),
                all_day=data.get("all_day", False),
                calendar_id=data.get("calendar_id", ""),
                calendar_name=data.get("calendar_name", ""),
                calendar_color=data.get("calendar_color", "#4285f4"),
                source_type=data.get("source_type", "caldav"),
                read_only=data.get("read_only", False),
                recurrence=recurrence,
            )
        except Exception as e:
            print(f"Error deserializing event: {e}", file=__import__('sys').stderr)
            return None
    
    def _recurrence_to_dict(self, rule: RecurrenceRule) -> dict:
        """Serialize a RecurrenceRule to a dictionary."""
        return {
            "frequency": rule.frequency,
            "interval": rule.interval,
            "count": rule.count,
            "until": rule.until.isoformat() if rule.until else None,
            "by_day": rule.by_day,
            "by_month_day": rule.by_month_day,
            "by_month": rule.by_month,
        }
    
    def _recurrence_from_dict(self, data: dict) -> RecurrenceRule:
        """Deserialize a RecurrenceRule from a dictionary."""
        return RecurrenceRule(
            frequency=data["frequency"],
            interval=data.get("interval", 1),
            count=data.get("count"),
            until=datetime.fromisoformat(data["until"]) if data.get("until") else None,
            by_day=data.get("by_day"),
            by_month_day=data.get("by_month_day"),
            by_month=data.get("by_month"),
        )
