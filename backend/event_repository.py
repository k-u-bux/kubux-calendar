"""
Event repository storing CalEvent objects.

Provides recurrence expansion to EventInstance objects using
recurring_ical_events library.

Supports persistent storage via pluggable storage backends.
"""

from datetime import datetime, date, timedelta
from typing import Optional, Any
from pathlib import Path
import uuid
import pytz
import sys
from icalendar import Calendar as ICalCalendar, Event as ICalEvent
from recurring_ical_events import of as recurring_events_of

from .event_wrapper import (
    CalEvent, CalendarSource, EventInstance,
    create_instance, parse_icalendar
)
from .event_storage import (
    EventStorageBackend, StoredEvent, SourceMetadata,
    create_storage_backend, get_default_storage_dir
)


def _debug_print(msg: str) -> None:
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] REPO: {msg}", file=sys.stderr)


class EventRepository:
    """
    Repository for CalEvent objects.
    
    Stores master events by source ID, provides recurrence expansion
    to EventInstance objects.
    
    Integrates with persistent storage backend for offline-first operation.
    """
    
    def __init__(self, storage_dir: Optional[Path] = None):
        # CalEvent objects stored by source_id -> uid -> CalEvent
        self._events: dict[str, dict[str, CalEvent]] = {}
        self._sources: dict[str, CalendarSource] = {}
        # Track pending operations by event UID
        self._pending_operations: dict[str, str] = {}
        
        # Initialize persistent storage
        self._storage = create_storage_backend(storage_dir)
    
    # ==================== Persistence ====================
    
    def _cal_event_to_stored(self, event: CalEvent) -> StoredEvent:
        """Convert CalEvent to StoredEvent for persistence."""
        # Build the raw iCalendar data
        vcal = ICalCalendar()
        vcal.add('prodid', '-//Kubux Calendar//kubux.net//')
        vcal.add('version', '2.0')
        vcal.add_component(event.event)
        raw_ical = vcal.to_ical().decode('utf-8')
        
        # Get LAST-MODIFIED if present
        last_mod = None
        lm = event.event.get('LAST-MODIFIED')
        if lm:
            last_mod = lm.dt if hasattr(lm, 'dt') else None
        
        return StoredEvent(
            uid=event.uid,
            source_id=event.source.id,
            raw_ical=raw_ical,
            etag=getattr(event, 'etag', None),
            last_modified=last_mod,
            local_modified=getattr(event, 'local_modified', None),
            pending_operation=event.pending_operation,
            caldav_href=event.caldav_href,
        )
    
    def _stored_to_cal_event(self, stored: StoredEvent, source: CalendarSource) -> Optional[CalEvent]:
        """Convert StoredEvent to CalEvent for runtime use."""
        try:
            vcal = parse_icalendar(stored.raw_ical)
            for component in vcal.walk():
                if component.name == 'VEVENT':
                    cal_event = CalEvent(
                        event=component,
                        source=source,
                        pending_operation=stored.pending_operation,
                        caldav_href=stored.caldav_href,
                    )
                    # Store extra metadata
                    cal_event.etag = stored.etag
                    cal_event.local_modified = stored.local_modified
                    return cal_event
        except Exception as e:
            _debug_print(f"Error parsing stored event {stored.uid}: {e}")
        return None
    
    def load_from_storage(self, source_id: str) -> int:
        """
        Load events for a source from persistent storage.
        
        Returns number of events loaded.
        """
        source = self._sources.get(source_id)
        if not source:
            _debug_print(f"Cannot load from storage: unknown source {source_id}")
            return 0
        
        stored_events = self._storage.load_events(source_id)
        loaded = 0
        
        for stored in stored_events:
            cal_event = self._stored_to_cal_event(stored, source)
            if cal_event:
                if source_id not in self._events:
                    self._events[source_id] = {}
                self._events[source_id][cal_event.uid] = cal_event
                
                # Restore pending operation tracking
                if stored.pending_operation:
                    self._pending_operations[cal_event.uid] = stored.pending_operation
                
                loaded += 1
        
        _debug_print(f"Loaded {loaded} events from storage for {source_id}")
        return loaded
    
    def save_to_storage(self, source_id: str) -> int:
        """
        Save all events for a source to persistent storage.
        
        Returns number of events saved.
        """
        events = self._events.get(source_id, {})
        stored_events = []
        
        for cal_event in events.values():
            stored_events.append(self._cal_event_to_stored(cal_event))
        
        self._storage.bulk_save_events(source_id, stored_events)
        return len(stored_events)
    
    def save_event_to_storage(self, event: CalEvent) -> None:
        """Save a single event to persistent storage."""
        stored = self._cal_event_to_stored(event)
        self._storage.save_event(stored)
    
    def delete_event_from_storage(self, source_id: str, uid: str) -> None:
        """Delete an event from persistent storage."""
        self._storage.delete_event(source_id, uid)
    
    def load_source_metadata(self, source_id: str) -> Optional[SourceMetadata]:
        """Load source metadata from storage."""
        return self._storage.load_source_metadata(source_id)
    
    def save_source_metadata(self, metadata: SourceMetadata) -> None:
        """Save source metadata to storage."""
        self._storage.save_source_metadata(metadata)
    
    def get_stored_uids(self, source_id: str) -> set[str]:
        """Get all UIDs stored for a source (for sync comparison)."""
        return self._storage.get_all_uids(source_id)
    
    # ==================== Source Management ====================
    
    def add_source(self, source: CalendarSource, load_from_storage: bool = True):
        """
        Register a calendar source.
        
        If load_from_storage is True, also loads any persisted events for this source.
        """
        self._sources[source.id] = source
        if source.id not in self._events:
            self._events[source.id] = {}
        
        if load_from_storage:
            self.load_from_storage(source.id)
    
    def remove_source(self, source_id: str):
        """Remove a calendar source and all its events."""
        self._sources.pop(source_id, None)
        self._events.pop(source_id, None)
    
    def get_source(self, source_id: str) -> Optional[CalendarSource]:
        """Get a calendar source by ID."""
        return self._sources.get(source_id)
    
    def get_all_sources(self) -> list[CalendarSource]:
        """Get all calendar sources."""
        return list(self._sources.values())
    
    # ==================== Event Storage ====================
    
    def store_events(self, source_id: str, events: list[CalEvent], persist: bool = True):
        """
        Store CalEvent objects for a source (replaces existing, but preserves pending local events).
        
        Args:
            source_id: Calendar source ID
            events: List of CalEvent objects from CalDAV/ICS
            persist: If True, also save to persistent storage
        """
        if source_id not in self._sources:
            raise ValueError(f"Unknown source: {source_id}")
        
        _debug_print(f"store_events({source_id}): {len(events)} events, persist={persist}")
        
        # Preserve events with pending operations (they haven't been synced to server yet)
        preserved_events = {}
        existing = self._events.get(source_id, {})
        for uid, event in existing.items():
            if event.pending_operation in ("create", "update", "delete", "delete_instance"):
                preserved_events[uid] = event
                _debug_print(f"store_events({source_id}): preserving pending event {uid} ({event.pending_operation})")
        
        # Replace with server events
        self._events[source_id] = {e.uid: e for e in events}
        
        # Restore preserved pending events
        for uid, event in preserved_events.items():
            self._events[source_id][uid] = event
        
        if persist:
            saved = self.save_to_storage(source_id)
            _debug_print(f"store_events({source_id}): saved {saved} events to storage")
    
    def merge_events(self, source_id: str, server_events: list[CalEvent]) -> dict:
        """
        Merge server events into local repository (incremental sync).
        
        Compares server events with local:
        - New server events → add
        - Changed server events → update (unless local has newer pending change)
        - Missing from server → delete from local
        
        Returns dict with counts: {'added': N, 'updated': N, 'deleted': N, 'conflicts': N}
        """
        if source_id not in self._sources:
            raise ValueError(f"Unknown source: {source_id}")
        
        source = self._sources[source_id]
        if source_id not in self._events:
            self._events[source_id] = {}
        
        local_events = self._events[source_id]
        server_uids = {e.uid for e in server_events}
        local_uids = set(local_events.keys())
        
        added = 0
        updated = 0
        deleted = 0
        conflicts = 0
        
        # Process server events
        for server_event in server_events:
            uid = server_event.uid
            local_event = local_events.get(uid)
            
            if local_event is None:
                # New event from server
                local_events[uid] = server_event
                added += 1
            else:
                # Event exists locally
                if local_event.pending_operation:
                    # Local has pending changes - check conflict
                    local_mod = getattr(local_event, 'local_modified', None)
                    server_mod = self._get_last_modified(server_event)
                    
                    if local_mod and server_mod and server_mod > local_mod:
                        # Server is newer - server wins, discard local changes
                        self._pending_operations.pop(uid, None)
                        local_events[uid] = server_event
                        updated += 1
                        conflicts += 1
                        _debug_print(f"Conflict resolved (server wins): {uid}")
                    else:
                        # Local is newer - keep local pending change
                        _debug_print(f"Keeping local pending change: {uid}")
                else:
                    # No local pending changes - just update
                    local_events[uid] = server_event
                    updated += 1
        
        # Find events deleted on server (not in server response but in local)
        deleted_uids = local_uids - server_uids
        for uid in deleted_uids:
            local_event = local_events.get(uid)
            
            # Don't delete if we have local pending create (event not yet on server)
            if local_event and local_event.pending_operation == "create":
                continue
            
            # Don't delete if we have local pending update (might be offline)
            if local_event and local_event.pending_operation == "update":
                # This is tricky - server deleted but we have local changes
                # For now, remove local and note as conflict
                conflicts += 1
                _debug_print(f"Conflict: server deleted event with local changes: {uid}")
            
            del local_events[uid]
            self._pending_operations.pop(uid, None)
            deleted += 1
        
        # Persist changes
        self.save_to_storage(source_id)
        
        _debug_print(f"Merge {source_id}: +{added} ~{updated} -{deleted} conflicts={conflicts}")
        return {'added': added, 'updated': updated, 'deleted': deleted, 'conflicts': conflicts}
    
    def _get_last_modified(self, event: CalEvent) -> Optional[datetime]:
        """Get LAST-MODIFIED timestamp from event."""
        lm = event.event.get('LAST-MODIFIED')
        if lm and hasattr(lm, 'dt'):
            return lm.dt
        return None
    
    def add_event(self, event: CalEvent, persist: bool = True):
        """Add or update a single CalEvent."""
        source_id = event.source.id
        if source_id not in self._events:
            self._events[source_id] = {}
        self._events[source_id][event.uid] = event
        
        if persist:
            self.save_event_to_storage(event)
    
    def remove_event(self, source_id: str, uid: str, persist: bool = True) -> bool:
        """Remove an event from a calendar."""
        if source_id not in self._events:
            return False
        if uid not in self._events[source_id]:
            return False
        del self._events[source_id][uid]
        
        if persist:
            self.delete_event_from_storage(source_id, uid)
        
        return True
    
    def get_event(self, source_id: str, uid: str) -> Optional[CalEvent]:
        """Get a specific CalEvent by source and UID."""
        if source_id not in self._events:
            return None
        return self._events[source_id].get(uid)
    
    def get_all_events(self, source_id: str) -> list[CalEvent]:
        """Get all CalEvent objects for a source."""
        return list(self._events.get(source_id, {}).values())
    
    def clear_source(self, source_id: str):
        """Clear all events for a source."""
        if source_id in self._events:
            self._events[source_id] = {}
    
    def clear(self):
        """Clear all events (but keep sources)."""
        for source_id in self._events:
            self._events[source_id] = {}
    
    # ==================== Recurrence Expansion ====================
    
    def get_instances(
        self,
        start: datetime,
        end: datetime,
        source_ids: Optional[list[str]] = None
    ) -> list[EventInstance]:
        """
        Get EventInstance objects for a time range.
        
        Expands recurring events using recurring_ical_events library.
        
        Args:
            start: Start of time range
            end: End of time range
            source_ids: Optional list of source IDs to filter by
            
        Returns:
            List of EventInstance objects sorted by start time
        """
        instances = []
        
        # Determine which sources to query
        if source_ids:
            sources = [sid for sid in source_ids if sid in self._events]
        else:
            sources = list(self._events.keys())
        
        for source_id in sources:
            source_instances = self._expand_source(source_id, start, end)
            instances.extend(source_instances)
        
        # Apply or clear pending operations
        for inst in instances:
            if inst.event.uid in self._pending_operations:
                inst.event.pending_operation = self._pending_operations[inst.event.uid]
            else:
                # Clear pending_operation if no longer in pending dict
                inst.event.pending_operation = None
        
        # Sort by start time
        instances.sort(key=lambda i: i.start.replace(tzinfo=None) if i.start.tzinfo else i.start)
        
        return instances
    
    def _expand_source(
        self,
        source_id: str,
        start: datetime,
        end: datetime
    ) -> list[EventInstance]:
        """Expand events for a single source."""
        instances = []
        events = self._events.get(source_id, {})
        
        for uid, cal_event in events.items():
            if cal_event.is_recurring:
                # Build a VCALENDAR for recurring_ical_events
                event_instances = self._expand_recurring(cal_event, start, end)
                instances.extend(event_instances)
            else:
                # Non-recurring: check if it falls in range
                event_start = cal_event.dtstart
                event_end = cal_event.dtend
                
                # Normalize for comparison
                cmp_start = start if start.tzinfo else pytz.UTC.localize(start)
                cmp_end = end if end.tzinfo else pytz.UTC.localize(end)
                ev_start = event_start if event_start.tzinfo else pytz.UTC.localize(event_start)
                ev_end = event_end if event_end.tzinfo else pytz.UTC.localize(event_end)
                
                if ev_end >= cmp_start and ev_start <= cmp_end:
                    instances.append(create_instance(cal_event))
        
        return instances
    
    def _expand_recurring(
        self,
        cal_event: CalEvent,
        start: datetime,
        end: datetime
    ) -> list[EventInstance]:
        """Expand a recurring event using recurring_ical_events."""
        instances = []
        
        try:
            # Build a minimal VCALENDAR containing just this event
            vcal = ICalCalendar()
            vcal.add('prodid', '-//Kubux Calendar//kubux.net//')
            vcal.add('version', '2.0')
            vcal.add_component(cal_event.event)
            
            # Use recurring_ical_events to expand
            expanded = recurring_events_of(vcal).between(start, end)
            
            for ical_event in expanded:
                # Get the instance start time
                dtstart = ical_event.get('DTSTART')
                if dtstart:
                    dt_val = dtstart.dt
                    if isinstance(dt_val, date) and not isinstance(dt_val, datetime):
                        dt_val = datetime.combine(dt_val, datetime.min.time())
                    if dt_val.tzinfo is None:
                        dt_val = pytz.UTC.localize(dt_val)
                    
                    instance = create_instance(cal_event, dt_val)
                    instances.append(instance)
        
        except Exception as e:
            print(f"Error expanding recurring event {cal_event.uid}: {e}")
            # Fallback: return single instance
            instances.append(create_instance(cal_event))
        
        return instances
    
    # ==================== Pending Operations ====================
    
    def mark_pending(self, uid: str, operation: str):
        """Mark an event as having a pending operation (persists immediately).
        
        Note: "create" is not overwritten by "update" - the event must be
        created on the server first. Modifications are included in the CREATE.
        """
        current_op = self._pending_operations.get(uid)
        
        # Don't downgrade "create" to "update" - event still needs to be created first
        # The modifications are already in the CalEvent and will be included in CREATE
        if current_op == "create" and operation == "update":
            _debug_print(f"mark_pending({uid}): keeping 'create' (not downgrading to 'update')")
            return
        
        self._pending_operations[uid] = operation
        
        # Also update the CalEvent and persist to storage
        event = self._find_event_by_uid(uid)
        if event:
            event.pending_operation = operation
            self.save_event_to_storage(event)
    
    def clear_pending(self, uid: str):
        """Clear pending status after successful sync (persists immediately)."""
        self._pending_operations.pop(uid, None)
        
        # Also update the CalEvent and persist to storage
        event = self._find_event_by_uid(uid)
        if event:
            event.pending_operation = None
            self.save_event_to_storage(event)
    
    def has_pending(self, uid: str) -> bool:
        """Check if an event has a pending operation."""
        return uid in self._pending_operations
    
    def get_pending_events(self) -> list[CalEvent]:
        """Get all events with pending operations."""
        events = []
        for uid in self._pending_operations:
            event = self._find_event_by_uid(uid)
            if event:
                events.append(event)
        return events
    
    def get_pending_count(self) -> int:
        """Get count of pending operations."""
        return len(self._pending_operations)
    
    def _find_event_by_uid(self, uid: str) -> Optional[CalEvent]:
        """Find an event by UID across all sources."""
        for source_events in self._events.values():
            if uid in source_events:
                return source_events[uid]
        return None
    
    # ==================== CRUD Operations ====================
    
    def create_event(
        self,
        source_id: str,
        summary: str,
        start: datetime,
        end: datetime,
        description: str = "",
        location: str = "",
        all_day: bool = False,
        recurrence: Optional[Any] = None,
    ) -> Optional[CalEvent]:
        """
        Create a new event.
        
        Returns:
            CalEvent for the created event, or None if failed
        """
        source = self._sources.get(source_id)
        if not source or source.read_only:
            return None
        
        # Create iCalendar event
        event = ICalEvent()
        event.add('uid', str(uuid.uuid4()))
        event.add('summary', summary)
        event.add('dtstamp', datetime.now(pytz.UTC))
        
        if description:
            event.add('description', description)
        if location:
            event.add('location', location)
        
        if all_day:
            event.add('dtstart', start.date())
            event.add('dtend', end.date())
        else:
            event.add('dtstart', start)
            event.add('dtend', end)
        
        if recurrence:
            rrule = self._build_rrule(recurrence)
            if rrule:
                event.add('rrule', rrule)
        
        cal_event = CalEvent(
            event=event,
            source=source,
            pending_operation="create"
        )
        
        self.add_event(cal_event)
        return cal_event
    
    def _build_rrule(self, recurrence: Any) -> Optional[dict]:
        """Build RRULE dict from recurrence specification."""
        if recurrence is None:
            return None
        
        freq = getattr(recurrence, 'frequency', None)
        if not freq and hasattr(recurrence, 'get'):
            freq = recurrence.get('frequency')
        if not freq:
            return None
        
        rrule = {'freq': freq.upper() if isinstance(freq, str) else freq}
        
        interval = getattr(recurrence, 'interval', None)
        if not interval and hasattr(recurrence, 'get'):
            interval = recurrence.get('interval')
        if interval and interval > 1:
            rrule['interval'] = interval
        
        count = getattr(recurrence, 'count', None)
        if not count and hasattr(recurrence, 'get'):
            count = recurrence.get('count')
        if count:
            rrule['count'] = count
        
        until = getattr(recurrence, 'until', None)
        if not until and hasattr(recurrence, 'get'):
            until = recurrence.get('until')
        if until:
            rrule['until'] = until
        
        byday = getattr(recurrence, 'by_day', None)
        if not byday and hasattr(recurrence, 'get'):
            byday = recurrence.get('by_day')
        if byday:
            rrule['byday'] = byday
        
        return rrule
    
    # ==================== Statistics ====================
    
    def get_event_count(self) -> int:
        """Get total number of stored events."""
        return sum(len(events) for events in self._events.values())
