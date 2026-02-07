"""
Event Store for Kubux Calendar.

Main event management interface with proper timezone handling.
"""

import json
from datetime import datetime, timedelta, date
from typing import Optional, Callable, List, Dict, Tuple, Any
from pathlib import Path
import pytz
import sys

from .config import Config
from .event import ImmutableEvent, SyncState
from .event_fs import EventFS
from .event_index import EventIndex
from .sync_manager import SyncManager, SyncStatus
from .timezone_utils import get_local_timezone

# Import network_ops functions
from .network_ops import (
    caldav_fetch_events,
    caldav_push_event,
    caldav_delete_event,
    ics_fetch,
)


# === CalendarSource Definition ===

from dataclasses import dataclass

@dataclass
class CalendarSource:
    """Calendar source definition."""
    id: str
    name: str
    color: str = "#4285f4"
    account_name: str = ""
    read_only: bool = False
    source_type: str = "caldav"
    visible: bool = True
    last_sync_time: Optional[datetime] = None
    is_outdated: bool = False


class EventStore:
    """
    Event Store for calendar event management.
    
    Provides unified interface for event storage, retrieval, and synchronization.
    """
    
    # Cache window: how far to fetch events from the center
    CACHE_WINDOW_PAST_MONTHS = 4    # Fetch 4 months into past
    CACHE_WINDOW_FUTURE_MONTHS = 8  # Fetch 8 months into future
    
    # Prefetch margins: trigger re-fetch when approaching cache boundaries
    PREFETCH_MARGIN_PAST_MONTHS = 2    # Re-fetch when within 2 months of past edge
    PREFETCH_MARGIN_FUTURE_MONTHS = 4  # Re-fetch when within 4 months of future edge
    
    def __init__(self, config: Config):
        self.config = config
        self._state_file = config.state_file
        
        # Initialize components
        self._event_fs = EventFS()
        self._event_index = EventIndex()
        self._sync_manager = SyncManager(self._event_fs)
        
        # State tracking
        self._calendar_sources: Dict[str, CalendarSource] = {}
        self._uuid_to_source_id: Dict[str, str] = {}  # Map UUID -> source_id
        self._visibility: Dict[str, bool] = {}
        self._colors: Dict[str, str] = {}
        
        self._last_sync_time: Optional[datetime] = None
        self._source_last_attempt: Dict[str, datetime] = {}
        self._source_last_success: Dict[str, datetime] = {}
        self._source_refresh_intervals: Dict[str, int] = {}
        self._source_outdate_thresholds: Dict[str, int] = {}
        
        self._cache_start: Optional[datetime] = None
        self._cache_end: Optional[datetime] = None
        
        # Calendar URL cache - initialized in initialize_sources_only
        self._calendar_urls: Dict[str, str] = {}
        
        # Callbacks
        self._on_change_callback: Optional[Callable[[], None]] = None
        self._on_sync_status_callback: Optional[Callable[[int, Optional[datetime]], None]] = None
        
        # Load saved state
        self._load_state()
        
        # Try to load cached calendars
        self._load_calendar_cache()
        
        # We'll schedule async calendar discovery later if needed
        self._calendar_discovery_scheduled = False
    
    def _to_utc(self, dt: datetime) -> datetime:
        """Convert datetime to UTC, assuming naive datetimes are in local timezone."""
        if dt.tzinfo is None:
            # Assume local timezone
            local_tz = get_local_timezone()
            dt = local_tz.localize(dt)
        return dt.astimezone(pytz.UTC)
    
    # === Public API ===
    
    def set_on_change_callback(self, callback: Callable[[], None]) -> None:
        """Set callback for when data changes."""
        self._on_change_callback = callback
    
    def set_on_sync_status_callback(self, callback: Callable[[int, Optional[datetime]], None]) -> None:
        """Set callback for sync status changes."""
        self._on_sync_status_callback = callback
    
    def initialize_sources_only(self) -> bool:
        """
        Initialize source metadata only (fast), no event loading.
        
        Returns True if at least one source was loaded.
        """
        success = False
        
        # Load CalDAV sources from config
        for account in self.config.nextcloud_accounts:
            try:
                # First, fetch actual calendars from the server
                print(f"DEBUG EventStore: Fetching calendars for account {account.name}", file=sys.stderr)
                password = account.get_password(self.config.password_program)
                auth = (account.username, password)
                
                try:
                    # Fetch calendars from server - use the same URL construction as caldav_fetch_events
                    from .network_ops import caldav_fetch_calendars
                    
                    # Construct proper CalDAV URL
                    caldav_url = account.url
                    if "/remote.php/dav" not in account.url and not account.url.endswith("/calendars/"):
                        caldav_url = account.url.rstrip("/") + "/remote.php/dav"
                    
                    print(f"DEBUG EventStore: Fetching calendars from CalDAV URL: {caldav_url}", file=sys.stderr)
                    calendars = caldav_fetch_calendars(caldav_url, auth)
                    
                    if not calendars:
                        print(f"DEBUG EventStore: No calendars found for account {account.name}, using default", file=sys.stderr)
                        # Create a default calendar source
                        source_id = f"caldav:{account.name}:default"
                        source = CalendarSource(
                            id=source_id,
                            name=account.name,
                            color=account.color,
                            account_name=account.name,
                            read_only=False,
                            source_type="caldav",
                            visible=self._visibility.get(source_id, True),
                        )
                        self._calendar_sources[source_id] = source
                        success = True
                    else:
                        # Create a source for each calendar
                        for cal_info in calendars:
                            source_id = f"caldav:{account.name}:{cal_info['id']}"
                            source = CalendarSource(
                                id=source_id,
                                name=cal_info['name'],
                                color=cal_info['color'],
                                account_name=account.name,
                                read_only=not cal_info['writable'],
                                source_type="caldav",
                                visible=self._visibility.get(source_id, True),
                            )
                            self._calendar_sources[source_id] = source
                            
                            # Store calendar URL for later fetching
                            if not hasattr(self, '_calendar_urls'):
                                self._calendar_urls = {}
                            self._calendar_urls[source_id] = cal_info['url']
                            
                            print(f"DEBUG EventStore: Created source {source_id} for calendar '{cal_info['name']}' at {cal_info['url']}", file=sys.stderr)
                            success = True
                    
                except Exception as e:
                    print(f"DEBUG EventStore: Failed to fetch calendars for {account.name}: {e}", file=sys.stderr)
                    # Fallback to default
                    source_id = f"caldav:{account.name}:default"
                    source = CalendarSource(
                        id=source_id,
                        name=account.name,
                        color=account.color,
                        account_name=account.name,
                        read_only=False,
                        source_type="caldav",
                        visible=self._visibility.get(source_id, True),
                    )
                    self._calendar_sources[source_id] = source
                    success = True
                
                # Load metadata for all sources of this account
                for source_id in [sid for sid in self._calendar_sources.keys() if sid.startswith(f"caldav:{account.name}:")]:
                    metadata = self._event_fs.load_source_metadata(source_id)
                    if metadata and "last_success" in metadata:
                        try:
                            last_success = datetime.fromisoformat(metadata["last_success"])
                            self._source_last_success[source_id] = last_success
                            self._calendar_sources[source_id].last_sync_time = last_success
                        except (ValueError, TypeError):
                            pass
                    
                    # Set refresh intervals
                    if account.refresh_interval is not None:
                        self._source_refresh_intervals[source_id] = account.refresh_interval
                    if account.outdate_threshold is not None:
                        self._source_outdate_thresholds[source_id] = account.outdate_threshold
                
            except Exception as e:
                print(f"Error loading CalDAV account {account.name}: {e}", file=sys.stderr)
        
        # Load ICS subscriptions
        for sub_config in self.config.ics_subscriptions:
            try:
                source_id = f"ics:{sub_config.name}"
                source = CalendarSource(
                    id=source_id,
                    name=sub_config.name,
                    color=sub_config.color,
                    read_only=True,
                    source_type="ics",
                    visible=self._visibility.get(source_id, True),
                )
                self._calendar_sources[source_id] = source
                
                # Load metadata
                metadata = self._event_fs.load_source_metadata(source_id)
                if metadata and "last_success" in metadata:
                    try:
                        last_success = datetime.fromisoformat(metadata["last_success"])
                        self._source_last_success[source_id] = last_success
                        source.last_sync_time = last_success
                    except (ValueError, TypeError):
                        pass
                
                # Set refresh intervals
                if sub_config.refresh_interval is not None:
                    self._source_refresh_intervals[source_id] = sub_config.refresh_interval
                if sub_config.outdate_threshold is not None:
                    self._source_outdate_thresholds[source_id] = sub_config.outdate_threshold
                
                success = True
                
            except Exception as e:
                print(f"Error loading ICS subscription {sub_config.name}: {e}", file=sys.stderr)
        
        # Apply visibility fix for CalDAV sources after they're created
        # This fixes the issue where CalDAV events don't show in UI
        for source_id, source in self._calendar_sources.items():
            if source.source_type == "caldav":
                if source_id not in self._visibility:
                    self._visibility[source_id] = True
                    print(f"DEBUG EventStore: Setting {source_id} visible by default (not in dict)", file=sys.stderr)
                elif not self._visibility[source_id]:
                    # Override existing False value
                    self._visibility[source_id] = True
                    print(f"DEBUG EventStore: Forcing {source_id} visible (was False)", file=sys.stderr)
        
        return success
    
    def load_events_for_source(self, source_id: str) -> int:
        """
        Load events for a single source from storage.
        
        Returns number of events loaded.
        """
        events = self._event_fs.list_all(source_id)
        
        # Add to index and track UUID -> source_id mapping
        for event in events:
            self._event_index.add_or_update(event)
            self._uuid_to_source_id[event.uuid] = source_id
        
        return len(events)
    
    def load_all_cached_events(self) -> int:
        """
        Bulk load ALL cached events from storage for immediate display.
        
        Returns total number of events loaded.
        """
        total_events = 0
        for source_id in self._calendar_sources:
            events = self._event_fs.list_all(source_id)
            for event in events:
                self._event_index.add_or_update(event)
                self._uuid_to_source_id[event.uuid] = source_id
            total_events += len(events)
            print(f"DEBUG EventStore: Loaded {len(events)} cached events from {source_id}", file=sys.stderr)
        print(f"DEBUG EventStore: Total cached events loaded: {total_events}", file=sys.stderr)
        return total_events
    
    def get_sources_by_visibility(self) -> tuple[list[str], list[str]]:
        """
        Get source IDs sorted by visibility.
        
        Returns (visible_source_ids, invisible_source_ids) for progressive loading.
        """
        visible = []
        invisible = []
        for source_id in self._calendar_sources:
            if self._visibility.get(source_id, True):
                visible.append(source_id)
            else:
                invisible.append(source_id)
        return visible, invisible
    
    def initialize(self) -> bool:
        """
        Initialize from storage (legacy method - loads everything at once).
        
        For faster startup, use initialize_sources_only() + load_events_for_source() instead.
        """
        success = self.initialize_sources_only()
        
        # Load events for all sources
        for source_id in self._calendar_sources:
            self.load_events_for_source(source_id)
        
        return success
    
    def get_calendars(self, visible_only: bool = False) -> list[CalendarSource]:
        sources = list(self._calendar_sources.values())
        if visible_only:
            sources = [s for s in sources if self._visibility.get(s.id, True)]
        return sources
    
    def get_calendar(self, calendar_id: str) -> Optional[CalendarSource]:
        return self._calendar_sources.get(calendar_id)
    
    def set_calendar_visibility(self, calendar_id: str, visible: bool) -> None:
        if calendar_id in self._calendar_sources:
            self._visibility[calendar_id] = visible
            self._save_state()
            self._notify_change()
    
    def set_calendar_color(self, calendar_id: str, color: str) -> None:
        if calendar_id in self._calendar_sources:
            self._calendar_sources[calendar_id].color = color
            self._colors[calendar_id] = color
            self._save_state()
            self._notify_change()
    
    def _is_cache_valid(self, start: datetime, end: datetime) -> bool:
        """
        Check if cache is valid for the requested range.
        
        With load_all_cached_events(), we load ALL cached events upfront.
        Always return True to avoid blocking UI on network fetches.
        Background refresh will update stale events.
        """
        # Always return True to show cached events immediately
        # Background refresh will update data if needed
        return True
    
    def _fetch_into_cache(self, start: datetime, end: datetime) -> None:
        """Fetch events from all sources into cache.
        
        Note: This is a synchronous blocking method - it WILL cause UI hangs.
        It's kept for backward compatibility but should be replaced with async
        _schedule_source_refresh calls.
        """
        print(f"WARNING: _fetch_into_cache called (blocking) {start.date()} to {end.date()}", file=sys.stderr)
        print(f"WARNING: This will cause UI hangs. Use _schedule_source_refresh instead.", file=sys.stderr)
        
        now = datetime.now(pytz.UTC)
        
        # Initialize sources if not already done - but skip if still initializing
        if not self._calendar_sources:
            print(f"DEBUG EventStore: No calendar sources found, cannot fetch events", file=sys.stderr)
            return
        
        # Fetch from CalDAV sources (per calendar, not per account)
        total_calendar_events = 0
        if hasattr(self, '_calendar_urls') and self._calendar_urls:
            print(f"DEBUG EventStore: Fetching events from {len(self._calendar_urls)} calendars (blocking)", file=sys.stderr)
            
            # Group by account to get passwords
            for account in self.config.nextcloud_accounts:
                try:
                    # Get password from password program (BLOCKING)
                    password = account.get_password(self.config.password_program)
                    auth = (account.username, password)
                    
                    # Get all source IDs for this account
                    account_source_ids = [sid for sid in self._calendar_urls.keys() if sid.startswith(f"caldav:{account.name}:")]
                    
                    if not account_source_ids:
                        print(f"DEBUG EventStore: No calendar sources found for account {account.name}", file=sys.stderr)
                        continue
                    
                    print(f"DEBUG EventStore: Fetching events for account {account.name} ({len(account_source_ids)} calendars)", file=sys.stderr)
                    
                    account_event_count = 0
                    # Fetch each calendar separately (BLOCKING)
                    for source_id in account_source_ids:
                        try:
                            calendar_url = self._calendar_urls[source_id]
                            source = self._calendar_sources.get(source_id)
                            source_name = source.name if source else source_id
                            
                            print(f"DEBUG EventStore: Fetching events from calendar '{source_name}' at {calendar_url} (BLOCKING)", file=sys.stderr)
                            
                            # Fetch events from this specific calendar URL (BLOCKING)
                            events_ical = caldav_fetch_events(
                                url=calendar_url,
                                auth=auth,
                                start=start,
                                end=end
                            )
                            
                            print(f"DEBUG EventStore: Fetched {len(events_ical)} events from calendar '{source_name}'", file=sys.stderr)
                            
                            # Convert iCalendar strings to ImmutableEvents
                            config_tz = get_local_timezone()
                            
                            calendar_event_count = 0
                            for ical_text in events_ical:
                                try:
                                    event = ImmutableEvent.from_ical(ical_text, source_id, config_tz)
                                    # Save to cache and index
                                    self._event_fs.save(event)
                                    self._event_index.add_or_update(event)
                                    self._uuid_to_source_id[event.uuid] = source_id
                                    calendar_event_count += 1
                                    account_event_count += 1
                                    total_calendar_events += 1
                                except Exception as e:
                                    print(f"Error converting iCalendar event for {source_id}: {e}", file=sys.stderr)
                            
                            print(f"DEBUG EventStore: Loaded {calendar_event_count} events from {source_id}", file=sys.stderr)
                            
                            # Update sync time for this source
                            self._source_last_success[source_id] = now
                            self._source_last_attempt[source_id] = now
                            
                            # Update source if it exists
                            if source_id in self._calendar_sources:
                                self._calendar_sources[source_id].last_sync_time = now
                                self._calendar_sources[source_id].is_outdated = False
                            
                        except Exception as e:
                            print(f"Error fetching events for calendar {source_id}: {e}", file=sys.stderr)
                            # Continue with next calendar instead of failing completely
                    
                    print(f"DEBUG EventStore: Account {account.name}: loaded {account_event_count} events total", file=sys.stderr)
                    
                except Exception as e:
                    print(f"Error processing CalDAV account {account.name}: {e}", file=sys.stderr)
        else:
            # Fallback to old method if no calendar URLs stored
            print(f"DEBUG EventStore: WARNING: No calendar URLs stored, using fallback method (BLOCKING)", file=sys.stderr)
            
            for account in self.config.nextcloud_accounts:
                try:
                    # Get password from password program (BLOCKING)
                    password = account.get_password(self.config.password_program)
                    auth = (account.username, password)
                    
                    print(f"DEBUG EventStore: Fetching CalDAV events for account {account.name} (fallback, BLOCKING)", file=sys.stderr)
                    
                    try:
                        events_ical = caldav_fetch_events(
                            url=account.url,
                            auth=auth,
                            start=start,
                            end=end
                        )
                        
                        print(f"DEBUG EventStore: Fetched {len(events_ical)} events for account {account.name}", file=sys.stderr)
                        
                        # Convert iCalendar strings to ImmutableEvents
                        config_tz = get_local_timezone()
                        
                        # Get all source IDs for this account
                        account_source_ids = [sid for sid in self._calendar_sources.keys() if sid.startswith(f"caldav:{account.name}:")]
                        
                        # If we have multiple calendars, we can't map events properly in fallback
                        # Use first calendar or default
                        if account_source_ids:
                            source_id = account_source_ids[0]
                            source_name = self._calendar_sources[source_id].name
                            print(f"DEBUG EventStore: WARNING: Using fallback source {source_id} ('{source_name}') for all events from {account.name}", file=sys.stderr)
                        else:
                            source_id = f"caldav:{account.name}:default"
                            print(f"DEBUG EventStore: WARNING: No calendar sources found for {account.name}, using default: {source_id}", file=sys.stderr)
                        
                        event_count = 0
                        for ical_text in events_ical:
                            try:
                                event = ImmutableEvent.from_ical(ical_text, source_id, config_tz)
                                # Save to cache and index
                                self._event_fs.save(event)
                                self._event_index.add_or_update(event)
                                self._uuid_to_source_id[event.uuid] = source_id
                                event_count += 1
                                total_calendar_events += 1
                            except Exception as e:
                                print(f"Error converting iCalendar event: {e}", file=sys.stderr)
                        
                        print(f"DEBUG EventStore: Loaded {event_count} events from {source_id}", file=sys.stderr)
                        
                        # Update sync time
                        self._source_last_success[source_id] = now
                        self._source_last_attempt[source_id] = now
                        
                        # Update source if it exists
                        if source_id in self._calendar_sources:
                            self._calendar_sources[source_id].last_sync_time = now
                            self._calendar_sources[source_id].is_outdated = False
                        
                    except Exception as e:
                        print(f"Error fetching CalDAV events for {account.name}: {e}", file=sys.stderr)
                except Exception as e:
                    print(f"Error processing CalDAV account {account.name}: {e}", file=sys.stderr)
        
        print(f"DEBUG EventStore: Total CalDAV events loaded: {total_calendar_events}", file=sys.stderr)
        
        # Fetch from ICS subscriptions (BLOCKING)
        for sub_config in self.config.ics_subscriptions:
            try:
                source_id = f"ics:{sub_config.name}"
                
                try:
                    events_ical = ics_fetch(sub_config.url)
                    
                    # Convert iCalendar strings to ImmutableEvents
                    config_tz = get_local_timezone()
                    
                    for ical_text in events_ical:
                        try:
                            event = ImmutableEvent.from_ical(ical_text, source_id, config_tz)
                            # Save to cache and index
                            self._event_fs.save(event)
                            self._event_index.add_or_update(event)
                            self._uuid_to_source_id[event.uuid] = source_id
                        except Exception as e:
                            print(f"Error converting iCalendar event: {e}", file=sys.stderr)
                    
                    # Update sync time
                    self._source_last_success[source_id] = now
                    self._source_last_attempt[source_id] = now
                    
                    # Update source if it exists
                    if source_id in self._calendar_sources:
                        self._calendar_sources[source_id].last_sync_time = now
                        self._calendar_sources[source_id].is_outdated = False
                    
                except Exception as e:
                    print(f"Error fetching ICS events for {sub_config.name}: {e}", file=sys.stderr)
            except Exception as e:
                print(f"Error processing ICS subscription {sub_config.name}: {e}", file=sys.stderr)
        
        # Set cache window
        self._cache_start = start
        self._cache_end = end
        
        print(f"Cache window set: {start.date()} to {end.date()}", file=sys.stderr)
        print(f"Fetched events from {len(self.config.nextcloud_accounts)} CalDAV accounts and {len(self.config.ics_subscriptions)} ICS subscriptions", file=sys.stderr)
        print(f"WARNING: _fetch_into_cache completed (UI was blocked during this call)", file=sys.stderr)
    
    def get_events(
        self,
        start: datetime,
        end: datetime,
        calendar_ids: Optional[list[str]] = None,
        visible_only: bool = True
    ) -> list:
        """
        Get events within a time range.
        
        Returns DecoratedEvent objects with calendar metadata.
        """
        # Convert to UTC for consistent comparison
        start_utc = self._to_utc(start)
        end_utc = self._to_utc(end)
        
        # Expand cache window if needed
        if not self._is_cache_valid(start_utc, end_utc):
            center = start_utc
            window_start = center - timedelta(days=self.CACHE_WINDOW_PAST_MONTHS * 30)
            window_end = center + timedelta(days=self.CACHE_WINDOW_FUTURE_MONTHS * 30)
            self._fetch_into_cache(window_start, window_end)
        
        # Determine visible sources
        if calendar_ids is None:
            source_ids = [
                s.id for s in self._calendar_sources.values()
                if not visible_only or self._visibility.get(s.id, True)
            ]
        else:
            source_ids = [
                cid for cid in calendar_ids
                if cid in self._calendar_sources and
                (not visible_only or self._visibility.get(cid, True))
            ]
        
        # Query index for event UUIDs in range (UTC)
        uuids = self._event_index.query_range(start_utc, end_utc)
        
        # Load events and decorate with calendar metadata
        events = []
        for uuid in uuids:
            source_id = self._uuid_to_source_id.get(uuid)
            if source_id and source_id in source_ids:
                # Load event from filesystem
                event = self._event_fs.load(source_id, uuid)
                if event:
                    source = self._calendar_sources.get(source_id)
                    if source:
                        # Update source outdated status
                        source.is_outdated = self.is_source_outdated(source_id)
                        # Create decorated event for GUI compatibility
                        from .event_wrapper import DecoratedEvent
                        decorated_event = DecoratedEvent(event, source)
                        events.append(decorated_event)
        
        return events
    
    def create_event(
        self,
        calendar_id: str,
        summary: str,
        start: datetime,
        end: datetime,
        description: str = "",
        location: str = "",
        all_day: bool = False,
        recurrence = None,
    ) -> Optional[ImmutableEvent]:
        """
        Create a new event in a calendar (optimistic).
        
        Creates locally with pending status, syncs in background.
        """
        source = self._calendar_sources.get(calendar_id)
        if not source or source.read_only:
            return None
        
        # Create ImmutableEvent
        config_tz = get_local_timezone()
        event = ImmutableEvent.create_new(
            source_id=calendar_id,
            summary=summary,
            start=start,
            end=end,
            description=description,
            location=location,
            all_day=all_day,
            recurrence=recurrence,
            config_tz=config_tz,
        )
        
        # Mark as pending create
        pending_event = event.with_pending_create()
        
        # Save to cache, index, and track mapping
        self._event_fs.save(pending_event)
        self._event_index.add_or_update(pending_event)
        self._uuid_to_source_id[pending_event.uuid] = calendar_id
        
        # Notify UI
        self._notify_change()
        self._notify_sync_status()
        
        return pending_event
    
    def update_event(self, event: ImmutableEvent) -> bool:
        """Update an existing event."""
        source_id = event.source_id
        source = self._calendar_sources.get(source_id)
        
        if not source or source.read_only:
            return False
        
        # Create updated event with pending status
        updated_event = event.with_pending_update()
        
        # Update in cache and index (mapping stays the same)
        self._event_fs.save(updated_event)
        self._event_index.update(event.uuid, updated_event)
        
        # Notify UI
        self._notify_change()
        self._notify_sync_status()
        
        return True
    
    def move_event(self, event: ImmutableEvent, new_calendar_id: str) -> Optional[ImmutableEvent]:
        """
        Move an event to a different calendar.
        
        This creates the event in the new calendar and deletes it from the old one.
        Returns the new event if successful, None otherwise.
        """
        source_id = event.source_id
        source = self._calendar_sources.get(source_id)
        new_source = self._calendar_sources.get(new_calendar_id)
        
        if not source or source.read_only or not new_source or new_source.read_only:
            return None
        
        # Same calendar - no move needed
        if source_id == new_calendar_id:
            return event
        
        # Create event in new calendar
        # TODO: This needs to properly create new iCalendar with new UID
        # For now, just return None
        return None
    
    def delete_event(self, event: ImmutableEvent) -> bool:
        """
        Delete an event (transparent sync).
        
        Marks as pending delete, event stays visible until server confirms.
        """
        source_id = event.source_id
        source = self._calendar_sources.get(source_id)
        
        if not source or source.read_only:
            return False
        
        # Mark as pending delete
        deleted_event = event.with_pending_delete()
        
        # Update in cache and index (keep mapping for get_events filtering)
        self._event_fs.save(deleted_event)
        self._event_index.update(event.uuid, deleted_event)
        
        # Notify UI
        self._notify_change()
        self._notify_sync_status()
        
        return True
    
    def delete_recurring_instance(self, event: ImmutableEvent, instance_start: datetime) -> bool:
        """
        Delete a specific instance of a recurring event (transparent sync).
        
        Marks instance as pending delete, syncs in background.
        """
        source_id = event.source_id
        source = self._calendar_sources.get(source_id)
        
        if not source or source.read_only or not event.is_recurring:
            return False
        
        # TODO: Implement recurring instance deletion
        # For now, just delete the entire event
        return self.delete_event(event)
    
    def get_writable_calendars(self) -> list[CalendarSource]:
        """Get calendars that can be written to (not read-only)."""
        return [s for s in self._calendar_sources.values() if not s.read_only]
    
    def get_source_last_sync(self, source_id: str) -> Optional[datetime]:
        """Get last sync time for a specific source."""
        return self._source_last_success.get(source_id)
    
    def get_source_refresh_interval(self, source_id: str) -> int:
        """Get effective refresh interval for a source (per-source or global default)."""
        return self._source_refresh_intervals.get(source_id, self.config.refresh_interval)
    
    def get_source_outdate_threshold(self, source_id: str) -> int:
        """Get effective outdate threshold for a source (per-source or global default)."""
        return self._source_outdate_thresholds.get(source_id, self.config.outdate_threshold)
    
    def is_source_outdated(self, source_id: str) -> bool:
        """Check if a source's data is outdated (no successful sync within threshold)."""
        threshold = self.get_source_outdate_threshold(source_id)
        last_success = self._source_last_success.get(source_id)
        
        if last_success is None:
            return True  # Never synced = outdated
        
        now = datetime.now(pytz.UTC)
        seconds_since_success = (now - last_success).total_seconds()
        return seconds_since_success > threshold
    
    def get_source_last_attempt(self, source_id: str) -> Optional[datetime]:
        """Get last sync attempt time for a specific source."""
        return self._source_last_attempt.get(source_id)
    
    def get_sources_needing_refresh(self) -> list[str]:
        """Get list of source IDs that need refresh based on their intervals."""
        now = datetime.now(pytz.UTC)
        sources_needing_refresh = []
        
        for source_id in self._calendar_sources:
            interval = self.get_source_refresh_interval(source_id)
            if interval <= 0:
                continue  # Refresh disabled for this source
            
            last_attempt = self._source_last_attempt.get(source_id)
            if last_attempt is None:
                sources_needing_refresh.append(source_id)
            elif (now - last_attempt).total_seconds() >= interval:
                sources_needing_refresh.append(source_id)
        
        return sources_needing_refresh
    
    def get_cached_event_count(self) -> int:
        return self._event_fs.get_event_count()
    
    def get_pending_sync_count(self) -> int:
        """Get count of events with pending operations."""
        # TODO: Implement pending count from sync queue
        return 0
    
    def get_last_sync_time(self) -> Optional[datetime]:
        return self._last_sync_time
    
    def refresh_all_in_background(self) -> None:
        """Connect to all servers and refresh data in background thread."""
        print("DEBUG EventStore: refresh_all_in_background called", file=sys.stderr)
        
        # Calculate date range for refresh (same as cache window)
        now = datetime.now(pytz.UTC)
        window_start = now - timedelta(days=self.CACHE_WINDOW_PAST_MONTHS * 30)
        window_end = now + timedelta(days=self.CACHE_WINDOW_FUTURE_MONTHS * 30)
        
        # Schedule fetch for all sources
        for source_id, source in self._calendar_sources.items():
            self._schedule_source_refresh(source_id, window_start, window_end)
    
    def refresh_in_background(self, calendar_id: Optional[str] = None) -> None:
        """Refresh data from sources in background thread."""
        print(f"DEBUG EventStore: refresh_in_background called: {calendar_id}", file=sys.stderr)
        
        # Calculate date range for refresh
        now = datetime.now(pytz.UTC)
        window_start = now - timedelta(days=self.CACHE_WINDOW_PAST_MONTHS * 30)
        window_end = now + timedelta(days=self.CACHE_WINDOW_FUTURE_MONTHS * 30)
        
        if calendar_id:
            # Refresh specific calendar
            if calendar_id in self._calendar_sources:
                self._schedule_source_refresh(calendar_id, window_start, window_end)
        else:
            # Refresh all calendars
            for source_id in self._calendar_sources:
                self._schedule_source_refresh(source_id, window_start, window_end)
    
    def sync_pending_in_background(self) -> None:
        """Sync pending changes in background thread."""
        # TODO: Implement pending sync using SyncManager for push/delete operations
        print("DEBUG EventStore: sync_pending_in_background called (not yet implemented)", file=sys.stderr)
    
    def refresh_due_sources_in_background(self) -> None:
        """Refresh all due sources in background thread."""
        print("DEBUG EventStore: refresh_due_sources_in_background called", file=sys.stderr)
        
        # Get sources that need refresh
        sources_needing_refresh = self.get_sources_needing_refresh()
        if not sources_needing_refresh:
            return
        
        # Calculate date range for refresh
        now = datetime.now(pytz.UTC)
        window_start = now - timedelta(days=self.CACHE_WINDOW_PAST_MONTHS * 30)
        window_end = now + timedelta(days=self.CACHE_WINDOW_FUTURE_MONTHS * 30)
        
        for source_id in sources_needing_refresh:
            self._schedule_source_refresh(source_id, window_start, window_end)
    
    def _schedule_source_refresh(self, source_id: str, start: datetime, end: datetime) -> None:
        """Schedule a background refresh for a specific source."""
        source = self._calendar_sources.get(source_id)
        if not source:
            return
        
        print(f"DEBUG EventStore: Scheduling refresh for {source_id} ({start.date()} to {end.date()})", file=sys.stderr)
        
        # Get URL for this source
        url = None
        auth = None
        
        if source.source_type == "caldav":
            # Get calendar URL from cache
            url = self._calendar_urls.get(source_id)
            if not url and source.account_name:
                # No cached URL - try to construct from account
                # This happens on first run before calendar discovery completes
                for account in self.config.nextcloud_accounts:
                    if account.name == source.account_name:
                        # Use account URL as fallback (will fetch all calendars)
                        url = account.url
                        print(f"DEBUG EventStore: Using account URL as fallback for {source_id}: {url}", file=sys.stderr)
                        break
            
            if not url:
                print(f"DEBUG EventStore: WARNING: No URL for {source_id}, skipping refresh", file=sys.stderr)
                return
            
            # ALWAYS get auth for CalDAV sources, regardless of URL source
            account_name = source.account_name
            if account_name:
                print(f"DEBUG EventStore: Getting authentication for account {account_name}", file=sys.stderr)
                for account in self.config.nextcloud_accounts:
                    if account.name == account_name:
                        try:
                            print(f"DEBUG EventStore: Getting password for {account_name} using {self.config.password_program}", file=sys.stderr)
                            password = account.get_password(self.config.password_program)
                            auth = (account.username, password)
                            print(f"DEBUG EventStore: Got authentication for {account_name}", file=sys.stderr)
                            break
                        except Exception as e:
                            print(f"DEBUG EventStore: Error getting password for {account_name}: {e}", file=sys.stderr)
                            return
                if not auth:
                    print(f"DEBUG EventStore: Could not find account {account_name} for source {source_id}", file=sys.stderr)
                    return
            else:
                print(f"DEBUG EventStore: No account name for source {source_id}, cannot get auth", file=sys.stderr)
                return
        
        elif source.source_type == "ics":
            # Find ICS subscription
            for sub_config in self.config.ics_subscriptions:
                if sub_config.name == source.name:
                    url = sub_config.url
                    print(f"DEBUG EventStore: Found ICS URL for {source_id}: {url}", file=sys.stderr)
                    break
        
        if not url:
            print(f"DEBUG EventStore: No URL found for source {source_id}", file=sys.stderr)
            return
        
        # Update last attempt time
        self._source_last_attempt[source_id] = datetime.now(pytz.UTC)
        print(f"DEBUG EventStore: Updated last attempt time for {source_id}", file=sys.stderr)
        
        # Schedule fetch via SyncManager
        def on_complete(result):
            print(f"DEBUG EventStore: Refresh completed for {source_id}: {result.status}", file=sys.stderr)
            
            if result.status == SyncStatus.SUCCESS:
                print(f"DEBUG EventStore: Refresh SUCCESS for {source_id}", file=sys.stderr)
                # Update last success time
                self._source_last_success[source_id] = datetime.now(pytz.UTC)
                if source_id in self._calendar_sources:
                    self._calendar_sources[source_id].last_sync_time = self._source_last_success[source_id]
                    self._calendar_sources[source_id].is_outdated = False
                
                # Update index with new events if result has data
                if result.data and isinstance(result.data, list):
                    print(f"DEBUG EventStore: Updating index with {len(result.data)} events for {source_id}", file=sys.stderr)
                    for event in result.data:
                        # Update index with new event
                        self._event_index.add_or_update(event)
                        self._uuid_to_source_id[event.uuid] = source_id
                    print(f"DEBUG EventStore: Index updated for {source_id}", file=sys.stderr)
                else:
                    print(f"DEBUG EventStore: No new events in result for {source_id}", file=sys.stderr)
                
                # Notify UI of data change
                print(f"DEBUG EventStore: Notifying UI of data change for {source_id}", file=sys.stderr)
                self._notify_change()
                self._notify_sync_status()
            else:
                print(f"DEBUG EventStore: Refresh FAILED for {source_id}: {result.error}", file=sys.stderr)
        
        # Schedule fetch
        if source.source_type == "caldav" and auth:
            print(f"DEBUG EventStore: Scheduling CalDAV fetch for {source_id} with auth", file=sys.stderr)
            self._sync_manager.schedule_fetch(
                source_id=source_id,
                url=url,
                start=start,
                end=end,
                on_complete=on_complete,
                auth=auth
            )
        elif source.source_type == "ics":
            print(f"DEBUG EventStore: Scheduling ICS fetch for {source_id}", file=sys.stderr)
            self._sync_manager.schedule_fetch(
                source_id=source_id,
                url=url,
                start=start,
                end=end,
                on_complete=on_complete,
                auth=None  # No auth for ICS
            )
        else:
            print(f"DEBUG EventStore: Could not schedule fetch for {source_id}: source_type={source.source_type}, auth={'yes' if auth else 'no'}", file=sys.stderr)
    
    # === Internal Methods ===
    
    def _notify_change(self) -> None:
        if self._on_change_callback:
            self._on_change_callback()
    
    def _notify_sync_status(self) -> None:
        if self._on_sync_status_callback:
            pending_count = self.get_pending_sync_count()
            self._on_sync_status_callback(pending_count, self._last_sync_time)
    
    def _load_state(self) -> None:
        if self._state_file.exists():
            try:
                with open(self._state_file, 'r') as f:
                    state = json.load(f)
                    self._visibility = state.get('visibility', {})
                    self._colors = state.get('colors', {})
            except Exception as e:
                print(f"Error loading state: {e}", file=sys.stderr)
        
        # Note: Visibility fix for CalDAV sources moved to initialize_sources_only()
        # because _calendar_sources is populated there, not in constructor
    
    def _save_state(self) -> None:
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._state_file, 'w') as f:
                json.dump({'visibility': self._visibility, 'colors': self._colors}, f, indent=2)
        except Exception as e:
            print(f"Error saving state: {e}", file=sys.stderr)
    
    def _load_calendar_cache(self) -> None:
        """Load cached calendar data from filesystem."""
        try:
            cache_file = Path(self.config.cache_dir) / "calendar_cache.json"
            if cache_file.exists():
                with open(cache_file, 'r') as f:
                    cache_data = json.load(f)
                
                # Load calendar URLs
                self._calendar_urls = cache_data.get('calendar_urls', {})
                
                # Load cached calendars by account
                self._cached_calendars = cache_data.get('cached_calendars', {})
                
                print(f"DEBUG EventStore: Loaded {len(self._calendar_urls)} calendar URLs from cache", file=sys.stderr)
        except Exception as e:
            print(f"Error loading calendar cache: {e}", file=sys.stderr)
            self._calendar_urls = {}
            self._cached_calendars = {}
    
    def _save_calendar_cache(self) -> None:
        """Save calendar cache to filesystem."""
        try:
            cache_dir = Path(self.config.cache_dir)
            cache_dir.mkdir(parents=True, exist_ok=True)
            cache_file = cache_dir / "calendar_cache.json"
            
            cache_data = {
                'calendar_urls': self._calendar_urls,
                'cached_calendars': self._cached_calendars,
                'saved_at': datetime.now(pytz.UTC).isoformat()
            }
            
            with open(cache_file, 'w') as f:
                json.dump(cache_data, f, indent=2)
            
            print(f"DEBUG EventStore: Saved {len(self._calendar_urls)} calendar URLs to cache", file=sys.stderr)
        except Exception as e:
            print(f"Error saving calendar cache: {e}", file=sys.stderr)
    
    def _schedule_calendar_discovery(self) -> None:
        """Schedule async calendar discovery for all accounts."""
        if self._calendar_discovery_scheduled:
            return
        
        self._calendar_discovery_scheduled = True
        
        for account in self.config.nextcloud_accounts:
            self._schedule_account_calendar_discovery(account)
    
    def _schedule_account_calendar_discovery(self, account) -> None:
        """Schedule async calendar discovery for a specific account."""
        # Get password (might block briefly but that's okay in background)
        try:
            password = account.get_password(self.config.password_program)
            auth = (account.username, password)
            
            # Construct CalDAV URL
            caldav_url = account.url
            if "/remote.php/dav" not in account.url and not account.url.endswith("/calendars/"):
                caldav_url = account.url.rstrip("/") + "/remote.php/dav"
            
            # Schedule async discovery via SyncManager
            def on_complete(result):
                if result.status == SyncStatus.SUCCESS and result.data:
                    calendars = result.data
                    print(f"DEBUG EventStore: Calendar discovery completed for {account.name}: {len(calendars)} calendars", file=sys.stderr)
                    
                    # Update cached calendars
                    self._update_calendar_cache(account.name, calendars)
                    
                    # Update calendar URLs
                    for cal_info in calendars:
                        source_id = f"caldav:{account.name}:{cal_info['id']}"
                        self._calendar_urls[source_id] = cal_info['url']
                    
                    # Save cache
                    self._save_calendar_cache()
                    
                    # Update UI if needed
                    self._notify_change()
            
            # We need a custom fetch operation for calendars since SyncManager doesn't have one
            # For now, we'll use task_dispatch directly
            from .task_dispatch import dispatch_task
            
            def fetch_calendars():
                from .network_ops import caldav_fetch_calendars
                try:
                    calendars = caldav_fetch_calendars(caldav_url, auth)
                    return SyncResult.success(calendars)
                except Exception as e:
                    return SyncResult.failed(str(e))
            
            dispatch_task(on_complete, fetch_calendars)
            
        except Exception as e:
            print(f"DEBUG EventStore: Failed to schedule calendar discovery for {account.name}: {e}", file=sys.stderr)
    
    def _update_calendar_cache(self, account_name: str, calendars: List[dict]) -> None:
        """Update cached calendars for an account."""
        if not hasattr(self, '_cached_calendars'):
            self._cached_calendars = {}
        
        self._cached_calendars[account_name] = calendars
        
        # Also update calendar sources if they exist
        for cal_info in calendars:
            source_id = f"caldav:{account_name}:{cal_info['id']}"
            if source_id in self._calendar_sources:
                # Update existing source with proper name and color
                source = self._calendar_sources[source_id]
                source.name = cal_info['name']
                source.color = cal_info['color']
                source.read_only = not cal_info.get('writable', True)
    
    def _initialize_with_cached_calendars(self) -> bool:
        """Initialize calendar sources using cached data only (no network calls)."""
        success = False
        
        # Load CalDAV sources from cached data
        for account in self.config.nextcloud_accounts:
            try:
                account_calendars = self._cached_calendars.get(account.name, [])
                
                if account_calendars:
                    # Use cached calendars
                    for cal_info in account_calendars:
                        source_id = f"caldav:{account.name}:{cal_info['id']}"
                        source = CalendarSource(
                            id=source_id,
                            name=cal_info['name'],
                            color=cal_info['color'],
                            account_name=account.name,
                            read_only=not cal_info.get('writable', True),
                            source_type="caldav",
                            visible=self._visibility.get(source_id, True),
                        )
                        self._calendar_sources[source_id] = source
                        
                        # Store calendar URL for later fetching
                        self._calendar_urls[source_id] = cal_info['url']
                        
                        print(f"DEBUG EventStore: Using cached source {source_id} for calendar '{cal_info['name']}'", file=sys.stderr)
                        success = True
                else:
                    # No cached calendars - create a placeholder source
                    source_id = f"caldav:{account.name}:default"
                    source = CalendarSource(
                        id=source_id,
                        name=account.name,
                        color=account.color,
                        account_name=account.name,
                        read_only=False,
                        source_type="caldav",
                        visible=self._visibility.get(source_id, True),
                    )
                    self._calendar_sources[source_id] = source
                    print(f"DEBUG EventStore: Created placeholder source {source_id} (no cached calendars)", file=sys.stderr)
                    success = True
                
                # Load metadata for all sources of this account
                for source_id in [sid for sid in self._calendar_sources.keys() if sid.startswith(f"caldav:{account.name}:")]:
                    metadata = self._event_fs.load_source_metadata(source_id)
                    if metadata and "last_success" in metadata:
                        try:
                            last_success = datetime.fromisoformat(metadata["last_success"])
                            self._source_last_success[source_id] = last_success
                            self._calendar_sources[source_id].last_sync_time = last_success
                        except (ValueError, TypeError):
                            pass
                    
                    # Set refresh intervals
                    if account.refresh_interval is not None:
                        self._source_refresh_intervals[source_id] = account.refresh_interval
                    if account.outdate_threshold is not None:
                        self._source_outdate_thresholds[source_id] = account.outdate_threshold
                
            except Exception as e:
                print(f"Error loading CalDAV account {account.name} from cache: {e}", file=sys.stderr)
        
        # Load ICS subscriptions (no network needed)
        for sub_config in self.config.ics_subscriptions:
            try:
                source_id = f"ics:{sub_config.name}"
                source = CalendarSource(
                    id=source_id,
                    name=sub_config.name,
                    color=sub_config.color,
                    read_only=True,
                    source_type="ics",
                    visible=self._visibility.get(source_id, True),
                )
                self._calendar_sources[source_id] = source
                
                # Load metadata
                metadata = self._event_fs.load_source_metadata(source_id)
                if metadata and "last_success" in metadata:
                    try:
                        last_success = datetime.fromisoformat(metadata["last_success"])
                        self._source_last_success[source_id] = last_success
                        source.last_sync_time = last_success
                    except (ValueError, TypeError):
                        pass
                
                # Set refresh intervals
                if sub_config.refresh_interval is not None:
                    self._source_refresh_intervals[source_id] = sub_config.refresh_interval
                if sub_config.outdate_threshold is not None:
                    self._source_outdate_thresholds[source_id] = sub_config.outdate_threshold
                
                success = True
                
            except Exception as e:
                print(f"Error loading ICS subscription {sub_config.name}: {e}", file=sys.stderr)
        
        # Apply visibility fix for CalDAV sources
        for source_id, source in self._calendar_sources.items():
            if source.source_type == "caldav":
                if source_id not in self._visibility:
                    self._visibility[source_id] = True
                    print(f"DEBUG EventStore: Setting {source_id} visible by default (not in dict)", file=sys.stderr)
                elif not self._visibility[source_id]:
                    # Override existing False value
                    self._visibility[source_id] = True
                    print(f"DEBUG EventStore: Forcing {source_id} visible (was False)", file=sys.stderr)
        
        return success
    
    def get_state(self) -> dict:
        return {'visibility': self._visibility.copy(), 'colors': self._colors.copy()}
    
    def set_state(self, state: dict) -> None:
        self._visibility = state.get('visibility', {})
        self._colors = state.get('colors', {})
    
    def __repr__(self) -> str:
        return f"EventStore(sources={len(self._calendar_sources)}, events={self.get_cached_event_count()})"
