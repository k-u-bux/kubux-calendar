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
from .sync_queue import SyncQueue, SyncOperation, PendingChange


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
        
        queue_file = config.state_file.parent / "sync_queue.json"
        self._sync_queue = SyncQueue(queue_file)
        self._sync_queue.set_on_change_callback(self._on_sync_queue_changed)
        
        self._last_sync_time: Optional[datetime] = None
        self._source_last_sync: dict[str, datetime] = {}  # Per-source last sync times
        self._source_refresh_intervals: dict[str, int] = {}  # Per-source refresh intervals
        self._on_change_callback: Optional[Callable[[], None]] = None
        self._on_sync_status_callback: Optional[Callable[[int, Optional[datetime]], None]] = None
    
    def set_on_change_callback(self, callback: Callable[[], None]) -> None:
        self._on_change_callback = callback
    
    def _notify_change(self) -> None:
        if self._on_change_callback:
            self._on_change_callback()
    
    def initialize(self) -> bool:
        """Initialize all calendar sources from configuration."""
        success = False
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
                    
                    for cal in client.get_calendars():
                        source_id = f"caldav:{account.name}:{cal.id}"
                        default_color = cal.color if cal.color != "#4285f4" else account.color
                        
                        source = CalendarSource(
                            id=source_id,
                            name=cal.name,
                            color=self._colors.get(source_id, default_color),
                            account_name=account.name,
                            read_only=not cal.writable,
                            source_type="caldav"
                        )
                        
                        self._calendar_sources[source_id] = source
                        self._caldav_calendars[source_id] = cal
                        self._repository.add_source(source)
                        
                        if source_id in self._visibility:
                            source.visible = self._visibility[source_id]
                        
                        # Store per-source refresh interval (if configured)
                        if account.refresh_interval is not None:
                            self._source_refresh_intervals[source_id] = account.refresh_interval
                    
                    success = True
            except Exception as e:
                print(f"Error initializing CalDAV account {account.name}: {e}")
        
        # Initialize ICS subscriptions
        for sub_config in self.config.ics_subscriptions:
            try:
                sub = self._ics_manager.add_subscription(
                    name=sub_config.name,
                    url=sub_config.url,
                    color=sub_config.color
                )
                
                source_id = f"ics:{sub.id}"
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
                
                # Store per-source refresh interval (if configured)
                if sub_config.refresh_interval is not None:
                    self._source_refresh_intervals[source_id] = sub_config.refresh_interval
                
                success = True
            except Exception as e:
                print(f"Error initializing ICS subscription {sub_config.name}: {e}")
        
        if success:
            self._last_sync_time = datetime.now()
        
        self.invalidate_cache()
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
                if events:
                    self._repository.store_events(source_id, events)
                # Only set initial sync time (if not already set)
                # Per-source refreshes are handled by refresh()
                if source_id not in self._source_last_sync:
                    self._source_last_sync[source_id] = now
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
            if source_id not in self._source_last_sync:
                self._source_last_sync[source_id] = now
                source.last_sync_time = now
        
        self._cache_start = start
        self._cache_end = end
    
    def invalidate_cache(self) -> None:
        self._cache_start = None
        self._cache_end = None
        self._repository.clear()
    
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
        
        # Apply color overrides to source
        for inst in instances:
            source_id = inst.source.id
            if source_id in self._colors:
                inst.event.source.color = self._colors[source_id]
        
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
        # Mark in repository's pending tracker too
        self._repository.mark_pending(cal_event.uid, "create")
        
        # Queue for background sync
        from icalendar import Calendar as ICalendar
        ical = ICalendar()
        ical.add('prodid', '-//Kubux Calendar//kubux.net//')
        ical.add('version', '2.0')
        ical.add_component(cal_event.event)
        raw_ical = ical.to_ical().decode('utf-8')
        
        self._sync_queue.add_create(
            calendar_id=calendar_id,
            event_uid=cal_event.uid,
            event_data={'raw_ical': raw_ical}
        )
        
        # Notify UI to refresh (shows pending indicator)
        self._notify_change()
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
            self.invalidate_cache()
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
        
        # Delete from old calendar
        if not self.delete_event(event):
            # Failed to delete from old - could result in duplicate
            # But we don't want to fail the move, just log it
            import sys
            print(f"Warning: Event moved but failed to delete from old calendar", file=sys.stderr)
        
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
        self._repository.mark_pending(event.uid, "delete")
        event.pending_operation = "delete"
        
        # Queue for background sync (event will be removed when sync completes)
        self._sync_queue.add_delete(
            calendar_id=source.id,
            event_uid=event.uid,
            event_data={}
        )
        
        # Notify UI to refresh - event shows pending indicator
        self._notify_change()
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
        
        # Mark as pending - instance stays visible with pending indicator
        instance_uid = f"{event.uid}_{instance_start.isoformat()}"
        self._repository.mark_pending(instance_uid, "delete")
        event.pending_operation = "delete"
        
        # Queue for background sync
        self._sync_queue.add_delete_instance(
            calendar_id=source.id,
            event_uid=event.uid,
            event_data={},
            instance_start=instance_start
        )
        
        # Notify UI to refresh - instance shows pending indicator
        self._notify_change()
        return True
    
    def get_writable_calendars(self) -> list[CalendarSource]:
        return [s for s in self._calendar_sources.values() if not s.read_only]
    
    def refresh(self, calendar_id: Optional[str] = None) -> None:
        """Refresh data from sources."""
        now = datetime.now()
        refreshed_sources = []
        
        if calendar_id:
            source = self._calendar_sources.get(calendar_id)
            if source and source.source_type == "ics":
                sub = self._ics_subscriptions.get(calendar_id)
                if sub:
                    sub.fetch()
                    refreshed_sources.append(calendar_id)
            elif source and source.source_type == "caldav":
                client = self._caldav_clients.get(source.account_name)
                if client:
                    client.reconnect()
                    for cal in client.get_calendars():
                        cid = f"caldav:{source.account_name}:{cal.id}"
                        if cid == calendar_id:
                            self._caldav_calendars[cid] = cal
                            refreshed_sources.append(cid)
        else:
            for name, client in self._caldav_clients.items():
                if client.reconnect():
                    for cal in client.get_calendars():
                        cid = f"caldav:{name}:{cal.id}"
                        if cid in self._caldav_calendars:
                            self._caldav_calendars[cid] = cal
                            refreshed_sources.append(cid)
            self._ics_manager.fetch_all()
            refreshed_sources.extend(self._ics_subscriptions.keys())
        
        # Update per-source last sync times
        for source_id in refreshed_sources:
            self._source_last_sync[source_id] = now
            source = self._calendar_sources.get(source_id)
            if source:
                source.last_sync_time = now
        
        self.invalidate_cache()
        self._notify_change()
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
    
    # ==================== Sync Queue Methods ====================
    
    def set_on_sync_status_callback(self, callback: Callable[[int, Optional[datetime]], None]) -> None:
        self._on_sync_status_callback = callback
    
    def _on_sync_queue_changed(self) -> None:
        self._notify_sync_status()
    
    def _notify_sync_status(self) -> None:
        if self._on_sync_status_callback:
            self._on_sync_status_callback(self._sync_queue.get_pending_count(), self._last_sync_time)
    
    def get_pending_sync_count(self) -> int:
        return self._sync_queue.get_pending_count()
    
    def get_last_sync_time(self) -> Optional[datetime]:
        return self._last_sync_time
    
    def get_source_last_sync(self, source_id: str) -> Optional[datetime]:
        """Get last sync time for a specific source."""
        return self._source_last_sync.get(source_id)
    
    def get_source_refresh_interval(self, source_id: str) -> int:
        """Get effective refresh interval for a source (per-source or global default)."""
        return self._source_refresh_intervals.get(source_id, self.config.refresh_interval)
    
    def get_sources_needing_refresh(self) -> list[str]:
        """Get list of source IDs that need refresh based on their intervals."""
        now = datetime.now()
        sources_needing_refresh = []
        
        for source_id in self._calendar_sources:
            interval = self.get_source_refresh_interval(source_id)
            if interval <= 0:
                continue  # Refresh disabled for this source
            
            last_sync = self._source_last_sync.get(source_id)
            if last_sync is None:
                sources_needing_refresh.append(source_id)
            elif (now - last_sync).total_seconds() >= interval:
                sources_needing_refresh.append(source_id)
        
        return sources_needing_refresh
    
    def get_cached_event_count(self) -> int:
        return self._repository.get_event_count()
    
    def has_pending_sync(self, event_uid: str) -> bool:
        base_uid = event_uid.split('_')[0] if '_' in event_uid else event_uid
        return self._sync_queue.has_pending_for_event(base_uid)
    
    def sync_pending_changes(self) -> tuple[int, int]:
        """Sync pending changes. Returns (success_count, fail_count)."""
        pending = self._sync_queue.get_pending_changes()
        if not pending:
            return (0, 0)
        
        success_count = 0
        fail_count = 0
        
        for change in pending:
            try:
                self._sync_queue.mark_syncing(change.id)
                result = self._process_sync_change(change)
                
                if result:
                    self._sync_queue.mark_synced(change.id)
                    # Also clear from repository's pending tracker
                    self._repository.clear_pending(change.event_uid)
                    success_count += 1
                else:
                    self._sync_queue.mark_failed(change.id, "Sync failed")
                    fail_count += 1
            except Exception as e:
                self._sync_queue.mark_failed(change.id, str(e))
                fail_count += 1
        
        if success_count > 0:
            self._last_sync_time = datetime.now()
            self.invalidate_cache()
            self._notify_change()
        
        self._notify_sync_status()
        return (success_count, fail_count)
    
    def _process_sync_change(self, change: PendingChange) -> bool:
        """Process a single sync change."""
        source = self._calendar_sources.get(change.calendar_id)
        if not source:
            return False
        
        cal_info = self._caldav_calendars.get(change.calendar_id)
        if not cal_info:
            return False
        
        client = self._caldav_clients.get(source.account_name)
        if not client:
            return False
        
        if change.operation == SyncOperation.CREATE:
            return client.save_raw_event(cal_info, change.event_data.get('raw_ical', ''))
        elif change.operation == SyncOperation.DELETE:
            return client.delete_event(cal_info, change.event_uid)
        elif change.operation == SyncOperation.DELETE_INSTANCE:
            # Add EXDATE to the recurring event
            instance_start = datetime.fromisoformat(change.instance_start) if change.instance_start else None
            if instance_start:
                return client.add_exdate(cal_info, change.event_uid, instance_start)
            return False
        
        return False
