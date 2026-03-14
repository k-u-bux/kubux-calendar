"""
Unified Event Store for Kubux Calendar.

Provides a single interface to access events from all sources (CalDAV and ICS).
Uses EventRepository for storage with CalEvent objects.
Returns EventInstance for display.
"""

import json
from datetime import datetime, timedelta
from typing import Optional, Callable
import pytz

from .config import Config
from .caldav_client import CalDAVClient, CalendarInfo
from .ics_subscription import ICSSubscription, ICSSubscriptionManager
from .event_wrapper import CalEvent, CalendarSource, EventInstance
from .event_repository import EventRepository
from .network_worker import get_network_worker, NetworkWorker


def _debug_print(message: str) -> None:
    import sys
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {message}", file=sys.stderr)


# Type alias for backwards compatibility
Event = CalEvent


class EventStore:
    """
    Unified event store combining CalDAV and ICS sources.
    
    Uses CalEvent for storage, EventInstance for display.
    """
    
    # Cache window: how far to fetch events from the center
    CACHE_WINDOW_PAST_MONTHS = 4    # Fetch 4 months into past
    CACHE_WINDOW_FUTURE_MONTHS = 8  # Fetch 8 months into future
    
    # Prefetch margins: trigger re-fetch when approaching cache boundaries
    PREFETCH_MARGIN_PAST_MONTHS = 2    # Re-fetch when within 2 months of past edge
    PREFETCH_MARGIN_FUTURE_MONTHS = 4  # Re-fetch when within 4 months of future edge
    
    def __init__(self, config: Config):
        self.config = config
        
        self._caldav_clients: dict[str, CalDAVClient] = {}
        self._ics_manager = ICSSubscriptionManager()
        self._repository = EventRepository()
        
        self._calendar_sources: dict[str, CalendarSource] = {}
        self._caldav_calendars: dict[str, CalendarInfo] = {}
        self._ics_subscriptions: dict[str, ICSSubscription] = {}
        
        self._visibility: dict[str, bool] = {}
        self._colors: dict[str, str] = {}
        
        self._cache_start: Optional[datetime] = None
        self._cache_end: Optional[datetime] = None
        
        self._state_file = config.state_file
        
        self._last_sync_time: Optional[datetime] = None
        self._source_last_attempt: dict[str, datetime] = {}  # Per-source last sync attempt times
        self._source_last_success: dict[str, datetime] = {}  # Per-source last successful sync times
        self._source_refresh_intervals: dict[str, int] = {}  # Per-source refresh intervals
        self._source_outdate_thresholds: dict[str, int] = {}  # Per-source outdate thresholds
        self._on_change_callback: Optional[Callable[[], None]] = None
        self._on_sync_status_callback: Optional[Callable[[int, Optional[datetime]], None]] = None
    
    def set_on_change_callback(self, callback: Callable[[], None]) -> None:
        self._on_change_callback = callback
    
    def _notify_change(self) -> None:
        if self._on_change_callback:
            self._on_change_callback()
    
    def initialize_sources_only(self) -> bool:
        """Initialize source metadata only (fast), no event loading.
        
        This is Phase 1 of initialization - creates CalendarSource objects
        from stored metadata so the sidebar can be populated immediately.
        Events are NOT loaded here - call load_events_for_source() separately.
        
        Returns True if at least one source was loaded.
        """
        success = False
        self._load_state()
        
        from .event_storage import SourceMetadata
        
        # Phase 1: Load CalDAV sources metadata (no events yet)
        for account in self.config.nextcloud_accounts:
            try:
                stored_sources = self._repository._storage.list_sources()
                for source_id in stored_sources:
                    if source_id.startswith(f"caldav:{account.name}:"):
                        metadata = self._repository.load_source_metadata(source_id)
                        if metadata:
                            source = CalendarSource(
                                id=source_id,
                                name=metadata.name,
                                color=self._colors.get(source_id, metadata.color),
                                account_name=metadata.account_name,
                                read_only=metadata.read_only,
                                source_type=metadata.source_type,
                            )
                            # Restore last_success for outdated indicator
                            if metadata.last_success:
                                self._source_last_success[source_id] = metadata.last_success
                            
                            self._calendar_sources[source_id] = source
                            self._repository.add_source(source)
                            
                            if source_id in self._visibility:
                                source.visible = self._visibility[source_id]
                            
                            # Store per-source intervals from config
                            if account.refresh_interval is not None:
                                self._source_refresh_intervals[source_id] = account.refresh_interval
                            if account.outdate_threshold is not None:
                                self._source_outdate_thresholds[source_id] = account.outdate_threshold
                            
                            success = True
            except Exception as e:
                print(f"Error loading CalDAV account {account.name} from storage: {e}")
        
        # Phase 2: Initialize ICS sources (no event loading yet)
        for sub_config in self.config.ics_subscriptions:
            try:
                sub = self._ics_manager.add_subscription(
                    name=sub_config.name,
                    url=sub_config.url,
                    color=sub_config.color
                )
                
                source_id = f"ics:{sub.id}"
                
                # Check if we have persisted metadata
                metadata = self._repository.load_source_metadata(source_id)
                if metadata and metadata.last_success:
                    self._source_last_success[source_id] = metadata.last_success
                
                source = CalendarSource(
                    id=source_id,
                    name=sub.name,
                    color=self._colors.get(source_id, sub.color),
                    read_only=True,
                    source_type="ics"
                )
                
                self._calendar_sources[source_id] = source
                self._ics_subscriptions[source_id] = sub
                self._repository.add_source(source)
                
                if source_id in self._visibility:
                    source.visible = self._visibility[source_id]
                
                if sub_config.refresh_interval is not None:
                    self._source_refresh_intervals[source_id] = sub_config.refresh_interval
                if sub_config.outdate_threshold is not None:
                    self._source_outdate_thresholds[source_id] = sub_config.outdate_threshold
                
                success = True
            except Exception as e:
                print(f"Error initializing ICS subscription {sub_config.name}: {e}")
        
        return success
    
    def load_events_for_source(self, source_id: str) -> int:
        """Load events for a single source from storage.
        
        This is Phase 2 of initialization - loads events for one source at a time.
        Call this progressively for each source to allow UI updates between loads.
        
        Returns number of events loaded.
        """
        return self._repository.load_from_storage(source_id)
    
    def set_cache_window_from_storage(self) -> None:
        """Set the cache window after loading events from storage.
        
        Call this after all load_events_for_source() calls are complete.
        Sets the cache window based on "now" using the standard window size,
        assuming storage contains events for that range.
        
        This prevents unnecessary network fetches when the data is already in the repository.
        """
        if self._cache_start is not None and self._cache_end is not None:
            # Cache window already set (e.g., by network fetch)
            return
        
        # Only set if we have at least some stored events
        if self._repository.get_event_count() == 0:
            _debug_print("No events in storage - cache window not set")
            return
        
        # Set cache window based on "now" using the standard window
        now = datetime.now()
        self._cache_start = now - timedelta(days=self.CACHE_WINDOW_PAST_MONTHS * 30)
        self._cache_end = now + timedelta(days=self.CACHE_WINDOW_FUTURE_MONTHS * 30)
        _debug_print(f"Cache window set from storage: {self._cache_start.date()} to {self._cache_end.date()}")
    
    def get_sources_by_visibility(self) -> tuple[list[str], list[str]]:
        """Get source IDs sorted by visibility.
        
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
        """Initialize from storage (legacy method - loads everything at once).
        
        For faster startup, use initialize_sources_only() + load_events_for_source() instead.
        """
        success = self.initialize_sources_only()
        
        # Load events for all sources
        for source_id in self._calendar_sources:
            self._repository.load_from_storage(source_id)
            _debug_print(f"Loaded events from storage: {source_id}")
        
        return success
    
    def refresh_all_async(self) -> None:
        """
        Connect to all servers and refresh data.
        
        This is the network-heavy operation - call after UI renders.
        Connects CalDAV clients, fetches calendars, and syncs events.
        """
        from .event_storage import SourceMetadata
        now = datetime.now()
        
        # Connect CalDAV clients
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
                    _debug_print(f"CalDAV {account.name}: connected")
                    self._caldav_clients[account.name] = client
                    
                    for cal in client.get_calendars():
                        source_id = f"caldav:{account.name}:{cal.id}"
                        default_color = cal.color if cal.color != "#4285f4" else account.color
                        
                        # Update or create source
                        if source_id not in self._calendar_sources:
                            source = CalendarSource(
                                id=source_id,
                                name=cal.name,
                                color=self._colors.get(source_id, default_color),
                                account_name=account.name,
                                read_only=not cal.writable,
                                source_type="caldav"
                            )
                            self._calendar_sources[source_id] = source
                            self._repository.add_source(source)
                            
                            if source_id in self._visibility:
                                source.visible = self._visibility[source_id]
                            if account.refresh_interval is not None:
                                self._source_refresh_intervals[source_id] = account.refresh_interval
                            if account.outdate_threshold is not None:
                                self._source_outdate_thresholds[source_id] = account.outdate_threshold
                        else:
                            source = self._calendar_sources[source_id]
                            source.read_only = not cal.writable
                            source.is_outdated = False
                        
                        self._caldav_calendars[source_id] = cal
                        
                        # Persist source metadata with last_success
                        metadata = SourceMetadata(
                            source_id=source_id,
                            name=cal.name,
                            color=default_color,
                            read_only=not cal.writable,
                            source_type="caldav",
                            account_name=account.name,
                            last_success=now,
                        )
                        self._repository.save_source_metadata(metadata)
                        
                        # Update success time
                        self._source_last_success[source_id] = now
                        self._source_last_attempt[source_id] = now
                else:
                    _debug_print(f"CalDAV {account.name}: connection failed (will use cached data)")
            except Exception as e:
                _debug_print(f"CalDAV {account.name}: error: {e}")
        
        # Refresh ICS subscriptions
        for source_id, sub in self._ics_subscriptions.items():
            self._source_last_attempt[source_id] = now
            success = sub.fetch()
            if success:
                source = self._calendar_sources.get(source_id)
                if source:
                    events = sub.get_events(source, force_fetch=False)
                    if events is not None:
                        self._repository.store_events(source_id, events)
                    self._source_last_success[source_id] = now
                    
                    # Persist metadata with last_success
                    metadata = SourceMetadata(
                        source_id=source_id,
                        name=source.name,
                        color=source.color,
                        read_only=True,
                        source_type="ics",
                        last_success=now,
                    )
                    self._repository.save_source_metadata(metadata)
                    _debug_print(f"ICS {source_id}: refreshed")
            else:
                _debug_print(f"ICS {source_id}: fetch failed (will use cached data)")
        
        self._last_sync_time = now
        self._notify_change()
    
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
        
        Returns False if:
        - No cache exists
        - Requested range is outside cache bounds
        - Requested range approaches cache boundaries (prefetch margins)
        """
        if self._cache_start is None or self._cache_end is None:
            return False
        
        # Basic check: requested range must be within cache
        if start < self._cache_start or end > self._cache_end:
            return False
        
        # Prefetch margin check: trigger re-fetch when approaching boundaries
        past_margin = timedelta(days=self.PREFETCH_MARGIN_PAST_MONTHS * 30)
        future_margin = timedelta(days=self.PREFETCH_MARGIN_FUTURE_MONTHS * 30)
        
        # If start is within 2 months of past cache edge, need refresh
        if start < self._cache_start + past_margin:
            return False
        
        # If end is within 4 months of future cache edge, need refresh
        if end > self._cache_end - future_margin:
            return False
        
        return True
    
    def _fetch_into_repository(self, start: datetime, end: datetime) -> None:
        """Fetch CalEvent objects from all sources into repository."""
        _debug_print(f"Fetching events {start.date()} to {end.date()}")
        now = datetime.now()
        
        # Fetch from CalDAV as CalEvent objects
        for source_id, cal_info in self._caldav_calendars.items():
            source = self._calendar_sources.get(source_id)
            if not source:
                continue
            
            client = self._caldav_clients.get(source.account_name)
            if client:
                events = client.get_events(cal_info, source, start, end)
                _debug_print(f"CalDAV {source_id}: got {len(events) if events else 0} events")
                if events:
                    self._repository.store_events(source_id, events)
                # Only set initial sync time (if not already set)
                # Per-source refreshes are handled by refresh()
                if source_id not in self._source_last_success:
                    self._source_last_success[source_id] = now
                    self._source_last_attempt[source_id] = now
                    source.last_sync_time = now
        
        # Fetch from ICS subscriptions as CalEvent objects
        for source_id, sub in self._ics_subscriptions.items():
            source = self._calendar_sources.get(source_id)
            if not source:
                continue
            
            events = sub.get_events(source, force_fetch=True)
            if events:
                self._repository.store_events(source_id, events)
            # Only set initial sync time (if not already set)
            # Per-source refreshes are handled by refresh()
            if source_id not in self._source_last_success:
                self._source_last_success[source_id] = now
                self._source_last_attempt[source_id] = now
                source.last_sync_time = now
        
        # Only mark cache as valid if we actually have sources to fetch from
        # (prevents marking valid before initialize() completes)
        if self._caldav_calendars or self._ics_subscriptions:
            self._cache_start = start
            self._cache_end = end
            _debug_print(f"Cache window set: {start.date()} to {end.date()}")
        else:
            _debug_print(f"No sources configured yet, cache NOT set")
    
    def invalidate_cache(self) -> None:
        self._cache_start = None
        self._cache_end = None
        self._repository.clear()
    
    def _get_visible_sources(self) -> list[str]:
        """Get list of visible source IDs."""
        return [
            s.id for s in self._calendar_sources.values()
            if self._visibility.get(s.id, True)
        ]
    
    def get_events_from_cache(self, start: datetime, end: datetime) -> list[EventInstance]:
        """Get events from local cache only - NO network fetch.
        
        Used during initial load to display events without triggering network access.
        
        Args:
            start: Start of date range
            end: End of date range
            
        Returns:
            List of events from all visible sources within the date range.
        """
        source_ids = self._get_visible_sources()
        return self._repository.get_instances(start, end, source_ids)
    
    def get_events(
        self,
        start: datetime,
        end: datetime,
        calendar_ids: Optional[list[str]] = None,
        visible_only: bool = True
    ) -> list[EventInstance]:
        """
        Get EventInstance objects for display within a time range.
        
        Returns EventInstance (not CalEvent) - use instance.event for the CalEvent.
        """
        import traceback
        _debug_print(f"get_events() called, caldav_calendars={len(self._caldav_calendars)}")
        
        # Expand cache window if needed (asymmetric: -4 months to +8 months)
        if not self._is_cache_valid(start, end):
            # Center on the START of the requested range (the viewing date)
            # This ensures asymmetric window works correctly
            center = start
            window_start = center - timedelta(days=self.CACHE_WINDOW_PAST_MONTHS * 30)
            window_end = center + timedelta(days=self.CACHE_WINDOW_FUTURE_MONTHS * 30)
            self._fetch_into_repository(window_start, window_end)
        
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
        
        # Get EventInstance objects from repository
        instances = self._repository.get_instances(start, end, source_ids)
        
        # Apply color overrides and update outdated status on sources
        for inst in instances:
            source_id = inst.source.id
            if source_id in self._colors:
                inst.event.source.color = self._colors[source_id]
            # Update is_outdated flag based on last successful sync
            inst.event.source.is_outdated = self.is_source_outdated(source_id)
        
        return instances
    
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
    ) -> Optional[CalEvent]:
        """
        Create a new event in a CalDAV calendar (optimistic).
        
        Creates locally with pending status, syncs in background.
        """
        source = self._calendar_sources.get(calendar_id)
        if not source or source.read_only or source.source_type != "caldav":
            return None
        
        cal_info = self._caldav_calendars.get(calendar_id)
        if not cal_info:
            return None
        
        client = self._caldav_clients.get(source.account_name)
        if not client:
            return None
        
        # Create event in repository with pending status
        cal_event = self._repository.create_event(
            source_id=calendar_id,
            summary=summary,
            start=start,
            end=end,
            description=description,
            location=location,
            all_day=all_day,
            recurrence=recurrence
        )
        
        if cal_event is None:
            return None
        
        # Event is already marked pending_operation="create" by repository
        # mark_pending persists immediately to storage
        self._repository.mark_pending(cal_event.uid, "create")
        
        # Notify UI to refresh (shows pending indicator)
        # Background sync timer will pick it up later
        self._notify_change()
        self._notify_sync_status()
        return cal_event
    
    def update_event(self, event: CalEvent) -> bool:
        """Update an existing event."""
        if event.read_only:
            return False
        
        source = event.source
        cal_info = self._caldav_calendars.get(source.id)
        if not cal_info:
            return False
        
        client = self._caldav_clients.get(source.account_name)
        if not client:
            return False
        
        # Mark as pending
        self._repository.mark_pending(event.uid, "update")
        
        if client.update_event(cal_info, event.uid, event.event):
            self._repository.clear_pending(event.uid)
            # No invalidate_cache() - event is already updated in place
            self._notify_change()
            return True

        self._notify_change()
        return False

    def move_event(self, event: CalEvent, new_calendar_id: str) -> Optional[CalEvent]:
        """
        Move an event to a different calendar.
        
        This creates the event in the new calendar and deletes it from the old one.
        Returns the new CalEvent if successful, None otherwise.
        """
        if event.read_only:
            return None
        
        # Get source info
        old_source = event.source
        new_source = self._calendar_sources.get(new_calendar_id)
        
        if not new_source or new_source.read_only or new_source.source_type != "caldav":
            return None
        
        # Same calendar - no move needed
        if old_source.id == new_calendar_id:
            return event
        
        # Get calendar info and clients
        old_cal_info = self._caldav_calendars.get(old_source.id)
        new_cal_info = self._caldav_calendars.get(new_calendar_id)
        if not old_cal_info or not new_cal_info:
            return None
        
        old_client = self._caldav_clients.get(old_source.account_name)
        new_client = self._caldav_clients.get(new_source.account_name)
        if not old_client or not new_client:
            return None
        
        # Create event in new calendar first
        new_event = self.create_event(
            calendar_id=new_calendar_id,
            summary=event.summary,
            start=event.start,
            end=event.end,
            description=event.description,
            location=event.location,
            all_day=event.all_day,
            recurrence=event.recurrence
        )
        
        if not new_event:
            return None
        
        # Delete from old calendar (queues for background sync)
        if not self.delete_event(event):
            # Failed to delete from old - could result in duplicate
            # But we don't want to fail the move, just log it
            import sys
            print(f"Warning: Event moved but failed to delete from old calendar", file=sys.stderr)
        
        # Remove old event from repository immediately (no "dying shadow")
        # User sees calendar change as property change, not as delete+create
        self._repository.remove_event(old_source.id, event.uid)

        return new_event

    def delete_event(self, event: CalEvent) -> bool:
        """
        Delete an event (transparent sync).
        
        Marks as pending delete, event stays visible until server confirms.
        """
        if event.read_only:
            return False
        
        source = event.source
        cal_info = self._caldav_calendars.get(source.id)
        if not cal_info:
            return False
        
        client = self._caldav_clients.get(source.account_name)
        if not client:
            return False
        
        # Mark as pending delete - event stays visible with pending indicator
        # mark_pending persists immediately to storage
        self._repository.mark_pending(event.uid, "delete")
        event.pending_operation = "delete"
        
        # Notify UI to refresh - event shows pending indicator
        # Background sync timer will pick it up later
        self._notify_change()
        self._notify_sync_status()
        return True
    
    def delete_recurring_instance(self, event: CalEvent, instance_start: datetime) -> bool:
        """
        Delete a specific instance of a recurring event (transparent sync).
        
        Marks instance as pending delete, syncs in background.
        """
        if event.read_only or not event.is_recurring:
            return False
        
        source = event.source
        cal_info = self._caldav_calendars.get(source.id)
        if not cal_info:
            return False
        
        client = self._caldav_clients.get(source.account_name)
        if not client:
            return False
        
        # For delete_instance, we need to store the instance_start on the event
        # so sync knows which instance to add EXDATE for
        event.pending_instance_start = instance_start
        
        # Mark as pending - instance stays visible with pending indicator
        # mark_pending persists immediately to storage
        self._repository.mark_pending(event.uid, "delete_instance")
        event.pending_operation = "delete_instance"
        
        # Notify UI to refresh - instance shows pending indicator
        self._notify_change()
        self._notify_sync_status()
        return True
    
    def get_writable_calendars(self) -> list[CalendarSource]:
        """Get calendars that can be written to (not read-only)."""
        return [s for s in self._calendar_sources.values() if not s.read_only]
    
    def _try_connect_missing_caldav_clients(self) -> None:
        """Try to connect CalDAV accounts that don't have clients yet.
        
        Called during refresh() to recover from starting offline.
        """
        from .event_storage import SourceMetadata
        
        for account in self.config.nextcloud_accounts:
            if account.name in self._caldav_clients:
                continue  # Already connected
            
            try:
                password = account.get_password(self.config.password_program)
                client = CalDAVClient(
                    url=account.url,
                    username=account.username,
                    password=password,
                    account_name=account.name
                )
                
                if client.connect():
                    _debug_print(f"CalDAV {account.name}: reconnected!")
                    self._caldav_clients[account.name] = client
                    
                    for cal in client.get_calendars():
                        source_id = f"caldav:{account.name}:{cal.id}"
                        self._caldav_calendars[source_id] = cal
                        
                        # Update source if it exists (was loaded from storage)
                        source = self._calendar_sources.get(source_id)
                        if source:
                            source.read_only = not cal.writable
                            source.is_outdated = False
                        
                        # Persist updated source metadata
                        default_color = cal.color if cal.color != "#4285f4" else account.color
                        metadata = SourceMetadata(
                            source_id=source_id,
                            name=cal.name,
                            color=default_color,
                            read_only=not cal.writable,
                            source_type="caldav",
                            account_name=account.name,
                        )
                        self._repository.save_source_metadata(metadata)
            except Exception as e:
                _debug_print(f"CalDAV {account.name}: reconnect failed: {e}")
    
    def refresh(self, calendar_id: Optional[str] = None) -> None:
        """Refresh data from sources.
        
        Tracks both attempt time (always) and success time (only on success).
        Retry interval is based on attempt time, outdate threshold is based on success time.
        
        Events are ONLY replaced on successful sync. Failed syncs keep existing events
        (they become "outdated" when threshold passes).
        
        If started offline, will try to connect CalDAV clients that weren't connected.
        """
        now = datetime.now()
        successfully_synced = []
        
        # Try to connect any CalDAV accounts that don't have clients yet
        self._try_connect_missing_caldav_clients()
        
        if calendar_id:
            source = self._calendar_sources.get(calendar_id)
            if source:
                # Mark attempt time
                self._source_last_attempt[calendar_id] = now
                
                if source.source_type == "ics":
                    sub = self._ics_subscriptions.get(calendar_id)
                    if sub:
                        success = sub.fetch()  # Returns bool
                        if success:
                            # Fetch succeeded - replace events for this source
                            events = sub.get_events(source, force_fetch=False)  # Already fetched
                            if events is not None:
                                self._repository.store_events(calendar_id, events)
                            successfully_synced.append(calendar_id)
                            
                elif source.source_type == "caldav":
                    client = self._caldav_clients.get(source.account_name)
                    if client and client.reconnect():
                        for cal in client.get_calendars():
                            cid = f"caldav:{source.account_name}:{cal.id}"
                            if cid == calendar_id:
                                self._caldav_calendars[cid] = cal
                                # Fetch events from cache window and replace
                                if self._cache_start and self._cache_end:
                                    events = client.get_events(cal, source, self._cache_start, self._cache_end)
                                    if events is not None:
                                        self._repository.store_events(cid, events)
                                successfully_synced.append(cid)
        else:
            # Refresh all sources - CalDAV
            for name, client in self._caldav_clients.items():
                # Mark attempt for all caldav sources of this account
                for cid in list(self._calendar_sources.keys()):
                    if cid.startswith(f"caldav:{name}:"):
                        self._source_last_attempt[cid] = now
                
                if client.reconnect():
                    for cal in client.get_calendars():
                        cid = f"caldav:{name}:{cal.id}"
                        self._caldav_calendars[cid] = cal
                        # Fetch events from cache window and replace
                        source = self._calendar_sources.get(cid)
                        if source and self._cache_start and self._cache_end:
                            events = client.get_events(cal, source, self._cache_start, self._cache_end)
                            if events is not None:
                                self._repository.store_events(cid, events)
                        successfully_synced.append(cid)
            
            # ICS subscriptions
            for source_id, sub in self._ics_subscriptions.items():
                self._source_last_attempt[source_id] = now
                success = sub.fetch()  # Returns bool
                if success:
                    source = self._calendar_sources.get(source_id)
                    if source:
                        events = sub.get_events(source, force_fetch=False)  # Already fetched
                        if events is not None:
                            self._repository.store_events(source_id, events)
                    successfully_synced.append(source_id)
        
        # Update last success time ONLY for successfully synced sources
        for source_id in successfully_synced:
            self._source_last_success[source_id] = now
            source = self._calendar_sources.get(source_id)
            if source:
                source.last_sync_time = now
        
        # No invalidate_cache() - events persist, only replaced on success
        self._notify_change()
        if successfully_synced:
            self._last_sync_time = now
    
    def refresh_due_sources(self) -> list[str]:
        """
        Refresh all sources that are due for refresh based on their intervals.
        
        Returns list of source IDs that were refreshed.
        """
        sources_to_refresh = self.get_sources_needing_refresh()
        if not sources_to_refresh:
            return []
        
        for source_id in sources_to_refresh:
            self.refresh(source_id)
        
        return sources_to_refresh
    
    def _load_state(self) -> None:
        if self._state_file.exists():
            try:
                with open(self._state_file, 'r') as f:
                    state = json.load(f)
                    self._visibility = state.get('visibility', {})
                    self._colors = state.get('colors', {})
            except Exception as e:
                print(f"Error loading state: {e}")
    
    def _save_state(self) -> None:
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._state_file, 'w') as f:
                json.dump({'visibility': self._visibility, 'colors': self._colors}, f, indent=2)
        except Exception as e:
            print(f"Error saving state: {e}")
    
    def get_state(self) -> dict:
        return {'visibility': self._visibility.copy(), 'colors': self._colors.copy()}
    
    def set_state(self, state: dict) -> None:
        self._visibility = state.get('visibility', {})
        self._colors = state.get('colors', {})
    
    # ==================== Sync Methods (Repository-Based) ====================
    
    def set_on_sync_status_callback(self, callback: Callable[[int, Optional[datetime]], None]) -> None:
        self._on_sync_status_callback = callback
    
    def _notify_sync_status(self) -> None:
        if self._on_sync_status_callback:
            pending_count = self._repository.get_pending_count()
            self._on_sync_status_callback(pending_count, self._last_sync_time)
    
    def get_pending_sync_count(self) -> int:
        """Get count of events with pending operations from repository."""
        return self._repository.get_pending_count()
    
    def get_last_sync_time(self) -> Optional[datetime]:
        return self._last_sync_time
    
    def get_source_last_sync(self, source_id: str) -> Optional[datetime]:
        """Get last sync time for a specific source (alias for last_success)."""
        return self._source_last_success.get(source_id)
    
    def get_source_refresh_interval(self, source_id: str) -> int:
        """Get effective refresh interval for a source (per-source or global default)."""
        return self._source_refresh_intervals.get(source_id, self.config.refresh_interval)
    
    def get_source_outdate_threshold(self, source_id: str) -> int:
        """Get effective outdate threshold for a source (per-source or global default)."""
        return self._source_outdate_thresholds.get(source_id, self.config.outdate_threshold)
    
    def is_source_outdated(self, source_id: str) -> bool:
        """Check if a source's data is outdated (no successful sync within threshold).
        
        Returns True if the source hasn't successfully synced within outdate_threshold seconds.
        """
        threshold = self.get_source_outdate_threshold(source_id)
        last_success = self._source_last_success.get(source_id)
        
        if last_success is None:
            return True  # Never synced = outdated
        
        now = datetime.now()
        seconds_since_success = (now - last_success).total_seconds()
        return seconds_since_success > threshold
    
    def get_source_last_success(self, source_id: str) -> Optional[datetime]:
        """Get last successful sync time for a specific source."""
        return self._source_last_success.get(source_id)
    
    def get_source_last_attempt(self, source_id: str) -> Optional[datetime]:
        """Get last sync attempt time for a specific source."""
        return self._source_last_attempt.get(source_id)
    
    def get_sources_needing_refresh(self) -> list[str]:
        """Get list of source IDs that need refresh based on their intervals.
        
        Uses last_attempt time (not last_success) to avoid hammering failing sources.
        """
        now = datetime.now()
        sources_needing_refresh = []
        
        for source_id in self._calendar_sources:
            interval = self.get_source_refresh_interval(source_id)
            if interval <= 0:
                continue  # Refresh disabled for this source
            
            # Use last_attempt for retry timing (not last_success)
            last_attempt = self._source_last_attempt.get(source_id)
            if last_attempt is None:
                sources_needing_refresh.append(source_id)
            elif (now - last_attempt).total_seconds() >= interval:
                sources_needing_refresh.append(source_id)
        
        return sources_needing_refresh
    
    def get_cached_event_count(self) -> int:
        return self._repository.get_event_count()
    
    def has_pending_sync(self, event_uid: str) -> bool:
        """Check if an event has a pending operation."""
        base_uid = event_uid.split('_')[0] if '_' in event_uid else event_uid
        return self._repository.has_pending(base_uid)
    
    def sync_pending_changes(self) -> tuple[int, int]:
        """
        Sync pending changes from repository. Returns (success_count, fail_count).
        
        Iterates events with pending_operation and attempts to sync each one.
        Single source of truth: the event in the repository IS the authoritative data.
        """
        pending_events = self._repository.get_pending_events()
        if not pending_events:
            return (0, 0)
        
        success_count = 0
        fail_count = 0
        
        for event in pending_events:
            try:
                result = self._sync_event(event)
                
                if result:
                    # For DELETE operations, remove the event from repository
                    if event.pending_operation == "delete":
                        self._repository.remove_event(event.source.id, event.uid)
                    else:
                        # Clear pending status  
                        self._repository.clear_pending(event.uid)
                    success_count += 1
                else:
                    fail_count += 1
            except Exception as e:
                _debug_print(f"Sync failed for {event.uid}: {e}")
                fail_count += 1
        
        if success_count > 0:
            self._last_sync_time = datetime.now()
            self._notify_change()
        
        self._notify_sync_status()
        return (success_count, fail_count)
    
    def _sync_event(self, event: CalEvent) -> bool:
        """Sync a single event based on its pending_operation."""
        source = event.source
        cal_info = self._caldav_calendars.get(source.id)
        if not cal_info:
            return False
        
        client = self._caldav_clients.get(source.account_name)
        if not client:
            return False
        
        op = event.pending_operation
        
        if op == "create":
            # Generate current raw_ical from the event (includes any modifications)
            from icalendar import Calendar as ICalendar
            ical = ICalendar()
            ical.add('prodid', '-//Kubux Calendar//kubux.net//')
            ical.add('version', '2.0')
            ical.add_component(event.event)
            raw_ical = ical.to_ical().decode('utf-8')
            return client.save_raw_event(cal_info, raw_ical)
        
        elif op == "update":
            return client.update_event(cal_info, event.uid, event.event)
        
        elif op == "delete":
            return client.delete_event(cal_info, event.uid)
        
        elif op == "delete_instance":
            # Get instance_start from event (stored when delete_recurring_instance was called)
            instance_start = getattr(event, 'pending_instance_start', None)
            if instance_start:
                return client.add_exdate(cal_info, event.uid, instance_start)
            return False
        
        return False
    
    # ==================== Non-Blocking Network Operations ====================
    
    def _setup_network_worker(self) -> NetworkWorker:
        """Get or set up the network worker with proper signal connections."""
        worker = get_network_worker()
        # Connect signals if not already connected (check via attribute)
        if not getattr(self, '_network_worker_connected', False):
            worker.operation_finished.connect(self._on_network_operation_finished)
            worker.operation_error.connect(self._on_network_operation_error)
            self._network_worker_connected = True
        return worker
    
    def refresh_all_in_background(self) -> None:
        """
        Connect to all servers and refresh data in background thread.
        
        Non-blocking version of refresh_all_async().
        UI remains responsive while network operations happen.
        """
        worker = self._setup_network_worker()
        worker.submit("refresh_all", self._do_refresh_all)
    
    def _do_refresh_all(self) -> dict:
        """Background worker for refresh_all - returns status dict."""
        from .event_storage import SourceMetadata
        now = datetime.now()
        results = {"caldav_connected": [], "caldav_failed": [], "ics_refreshed": [], "ics_failed": []}
        
        # Connect CalDAV clients
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
                    _debug_print(f"CalDAV {account.name}: connected (background)")
                    self._caldav_clients[account.name] = client
                    results["caldav_connected"].append(account.name)
                    
                    for cal in client.get_calendars():
                        source_id = f"caldav:{account.name}:{cal.id}"
                        default_color = cal.color if cal.color != "#4285f4" else account.color
                        
                        # Update or create source
                        if source_id not in self._calendar_sources:
                            source = CalendarSource(
                                id=source_id,
                                name=cal.name,
                                color=self._colors.get(source_id, default_color),
                                account_name=account.name,
                                read_only=not cal.writable,
                                source_type="caldav"
                            )
                            self._calendar_sources[source_id] = source
                            self._repository.add_source(source)
                            
                            if source_id in self._visibility:
                                source.visible = self._visibility[source_id]
                            if account.refresh_interval is not None:
                                self._source_refresh_intervals[source_id] = account.refresh_interval
                            if account.outdate_threshold is not None:
                                self._source_outdate_thresholds[source_id] = account.outdate_threshold
                        else:
                            source = self._calendar_sources[source_id]
                            source.read_only = not cal.writable
                            source.is_outdated = False
                        
                        self._caldav_calendars[source_id] = cal
                        
                        # Persist source metadata with last_success
                        metadata = SourceMetadata(
                            source_id=source_id,
                            name=cal.name,
                            color=default_color,
                            read_only=not cal.writable,
                            source_type="caldav",
                            account_name=account.name,
                            last_success=now,
                        )
                        self._repository.save_source_metadata(metadata)
                        
                        # Update success time
                        self._source_last_success[source_id] = now
                        self._source_last_attempt[source_id] = now
                else:
                    _debug_print(f"CalDAV {account.name}: connection failed (background)")
                    results["caldav_failed"].append(account.name)
            except Exception as e:
                _debug_print(f"CalDAV {account.name}: error (background): {e}")
                results["caldav_failed"].append(account.name)
        
        # Refresh ICS subscriptions
        for source_id, sub in self._ics_subscriptions.items():
            self._source_last_attempt[source_id] = now
            success = sub.fetch()
            if success:
                source = self._calendar_sources.get(source_id)
                if source:
                    events = sub.get_events(source, force_fetch=False)
                    if events is not None:
                        self._repository.store_events(source_id, events)
                    self._source_last_success[source_id] = now
                    
                    # Persist metadata with last_success
                    metadata = SourceMetadata(
                        source_id=source_id,
                        name=source.name,
                        color=source.color,
                        read_only=True,
                        source_type="ics",
                        last_success=now,
                    )
                    self._repository.save_source_metadata(metadata)
                    results["ics_refreshed"].append(source_id)
                    _debug_print(f"ICS {source_id}: refreshed (background)")
            else:
                results["ics_failed"].append(source_id)
                _debug_print(f"ICS {source_id}: fetch failed (background)")
        
        self._last_sync_time = now
        return results
    
    def refresh_in_background(self, calendar_id: Optional[str] = None) -> None:
        """
        Refresh data from sources in background thread.
        
        Non-blocking version of refresh().
        """
        worker = self._setup_network_worker()
        op_id = f"refresh:{calendar_id}" if calendar_id else "refresh:all"
        worker.submit(op_id, self._do_refresh, calendar_id)
    
    def _do_refresh(self, calendar_id: Optional[str] = None) -> dict:
        """Background worker for refresh - returns status dict."""
        now = datetime.now()
        results = {"synced": [], "failed": []}
        
        # Try to connect any CalDAV accounts that don't have clients yet
        self._try_connect_missing_caldav_clients()
        
        if calendar_id:
            source = self._calendar_sources.get(calendar_id)
            if source:
                self._source_last_attempt[calendar_id] = now
                
                if source.source_type == "ics":
                    sub = self._ics_subscriptions.get(calendar_id)
                    if sub:
                        success = sub.fetch()
                        if success:
                            events = sub.get_events(source, force_fetch=False)
                            if events is not None:
                                self._repository.store_events(calendar_id, events)
                            results["synced"].append(calendar_id)
                        else:
                            results["failed"].append(calendar_id)
                            
                elif source.source_type == "caldav":
                    client = self._caldav_clients.get(source.account_name)
                    if client and client.reconnect():
                        for cal in client.get_calendars():
                            cid = f"caldav:{source.account_name}:{cal.id}"
                            if cid == calendar_id:
                                self._caldav_calendars[cid] = cal
                                if self._cache_start and self._cache_end:
                                    events = client.get_events(cal, source, self._cache_start, self._cache_end)
                                    if events is not None:
                                        self._repository.store_events(cid, events)
                                results["synced"].append(cid)
                    else:
                        results["failed"].append(calendar_id)
        else:
            # Refresh all sources
            for name, client in self._caldav_clients.items():
                for cid in list(self._calendar_sources.keys()):
                    if cid.startswith(f"caldav:{name}:"):
                        self._source_last_attempt[cid] = now
                
                if client.reconnect():
                    for cal in client.get_calendars():
                        cid = f"caldav:{name}:{cal.id}"
                        self._caldav_calendars[cid] = cal
                        source = self._calendar_sources.get(cid)
                        if source and self._cache_start and self._cache_end:
                            events = client.get_events(cal, source, self._cache_start, self._cache_end)
                            if events is not None:
                                self._repository.store_events(cid, events)
                        results["synced"].append(cid)
            
            for source_id, sub in self._ics_subscriptions.items():
                self._source_last_attempt[source_id] = now
                success = sub.fetch()
                if success:
                    source = self._calendar_sources.get(source_id)
                    if source:
                        events = sub.get_events(source, force_fetch=False)
                        if events is not None:
                            self._repository.store_events(source_id, events)
                    results["synced"].append(source_id)
                else:
                    results["failed"].append(source_id)
        
        # Update success times for synced sources
        for source_id in results["synced"]:
            self._source_last_success[source_id] = now
            source = self._calendar_sources.get(source_id)
            if source:
                source.last_sync_time = now
        
        if results["synced"]:
            self._last_sync_time = now
        
        return results
    
    def sync_pending_in_background(self) -> None:
        """
        Sync pending changes in background thread.
        
        Non-blocking version of sync_pending_changes().
        """
        pending_events = self._repository.get_pending_events()
        if not pending_events:
            return
        
        worker = self._setup_network_worker()
        worker.submit("sync_pending", self._do_sync_pending)
    
    def _do_sync_pending(self) -> dict:
        """Background worker for sync_pending - returns status dict."""
        pending_events = self._repository.get_pending_events()
        results = {"success": 0, "failed": 0, "deleted_uids": []}
        
        for event in pending_events:
            try:
                result = self._sync_event(event)
                
                if result:
                    if event.pending_operation == "delete":
                        results["deleted_uids"].append((event.source.id, event.uid))
                    else:
                        self._repository.clear_pending(event.uid)
                    results["success"] += 1
                else:
                    results["failed"] += 1
            except Exception as e:
                _debug_print(f"Sync failed for {event.uid} (background): {e}")
                results["failed"] += 1
        
        if results["success"] > 0:
            self._last_sync_time = datetime.now()
        
        return results
    
    def refresh_due_sources_in_background(self) -> None:
        """
        Refresh all due sources in background thread.
        
        Non-blocking version of refresh_due_sources().
        """
        sources_to_refresh = self.get_sources_needing_refresh()
        if not sources_to_refresh:
            return
        
        worker = self._setup_network_worker()
        worker.submit("refresh_due", self._do_refresh_due, sources_to_refresh)
    
    def _do_refresh_due(self, source_ids: list[str]) -> dict:
        """Background worker for refresh_due_sources."""
        results = {"refreshed": []}
        for source_id in source_ids:
            result = self._do_refresh(source_id)
            if result.get("synced"):
                results["refreshed"].extend(result["synced"])
        return results
    
    def _on_network_operation_finished(self, operation_id: str, result: object) -> None:
        """Handle completion of background network operation."""
        _debug_print(f"Network operation finished: {operation_id}")
        
        if operation_id == "refresh_all":
            # Notify UI to update
            self._notify_change()
        
        elif operation_id.startswith("refresh:"):
            self._notify_change()
        
        elif operation_id == "sync_pending":
            # Handle deleted events
            if isinstance(result, dict):
                for source_id, uid in result.get("deleted_uids", []):
                    self._repository.remove_event(source_id, uid)
            self._notify_change()
            self._notify_sync_status()
        
        elif operation_id == "refresh_due":
            self._notify_change()
    
    def _on_network_operation_error(self, operation_id: str, error_msg: str) -> None:
        """Handle error from background network operation."""
        _debug_print(f"Network operation error: {operation_id}: {error_msg}")
        # Still notify in case partial data was updated
        self._notify_change()
