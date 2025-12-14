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
    
    CACHE_WINDOW_MONTHS = 2
    
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
        if self._cache_start is None or self._cache_end is None:
            return False
        return start >= self._cache_start and end <= self._cache_end
    
    def _fetch_into_repository(self, start: datetime, end: datetime) -> None:
        """Fetch CalEvent objects from all sources into repository."""
        _debug_print(f"Fetching events {start.date()} to {end.date()}")
        
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
        
        # Fetch from ICS subscriptions as CalEvent objects
        for source_id, sub in self._ics_subscriptions.items():
            source = self._calendar_sources.get(source_id)
            if not source:
                continue
            
            events = sub.get_events(source, force_fetch=True)
            if events:
                self._repository.store_events(source_id, events)
        
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
        # Expand cache window if needed
        if not self._is_cache_valid(start, end):
            center = start + (end - start) / 2
            window_start = center - timedelta(days=self.CACHE_WINDOW_MONTHS * 30)
            window_end = center + timedelta(days=self.CACHE_WINDOW_MONTHS * 30)
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
        """Create a new event in a CalDAV calendar."""
        source = self._calendar_sources.get(calendar_id)
        if not source or source.read_only or source.source_type != "caldav":
            return None
        
        cal_info = self._caldav_calendars.get(calendar_id)
        if not cal_info:
            return None
        
        client = self._caldav_clients.get(source.account_name)
        if not client:
            return None
        
        # Create event in repository
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
        
        # Sync to server
        if client.save_event(cal_info, cal_event.event):
            cal_event.pending_operation = None
            self._notify_change()
            return cal_event
        
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
    
    def delete_event(self, event: CalEvent) -> bool:
        """Delete an event."""
        if event.read_only:
            return False
        
        source = event.source
        cal_info = self._caldav_calendars.get(source.id)
        if not cal_info:
            return False
        
        client = self._caldav_clients.get(source.account_name)
        if not client:
            return False
        
        if client.delete_event(cal_info, event.uid):
            self.invalidate_cache()
            self._notify_change()
            return True
        
        return False
    
    def delete_recurring_instance(self, event: CalEvent, instance_start: datetime) -> bool:
        """Delete a specific instance of a recurring event."""
        if event.read_only or not event.is_recurring:
            return False
        
        source = event.source
        cal_info = self._caldav_calendars.get(source.id)
        if not cal_info:
            return False
        
        client = self._caldav_clients.get(source.account_name)
        if not client:
            return False
        
        if client.add_exdate(cal_info, event.uid, instance_start):
            self.invalidate_cache()
            self._notify_change()
            return True
        
        return False
    
    def get_writable_calendars(self) -> list[CalendarSource]:
        return [s for s in self._calendar_sources.values() if not s.read_only]
    
    def refresh(self, calendar_id: Optional[str] = None) -> None:
        """Refresh data from sources."""
        if calendar_id:
            source = self._calendar_sources.get(calendar_id)
            if source and source.source_type == "ics":
                sub = self._ics_subscriptions.get(calendar_id)
                if sub:
                    sub.fetch()
            elif source and source.source_type == "caldav":
                client = self._caldav_clients.get(source.account_name)
                if client:
                    client.reconnect()
                    for cal in client.get_calendars():
                        cid = f"caldav:{source.account_name}:{cal.id}"
                        if cid == calendar_id:
                            self._caldav_calendars[cid] = cal
        else:
            for name, client in self._caldav_clients.items():
                if client.reconnect():
                    for cal in client.get_calendars():
                        cid = f"caldav:{name}:{cal.id}"
                        if cid in self._caldav_calendars:
                            self._caldav_calendars[cid] = cal
            self._ics_manager.fetch_all()
        
        self.invalidate_cache()
        self._notify_change()
        self._last_sync_time = datetime.now()
    
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
        
        return False
