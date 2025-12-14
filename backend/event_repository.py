"""
Event repository storing CalEvent objects.

Provides recurrence expansion to EventInstance objects using
recurring_ical_events library.
"""

from datetime import datetime, date, timedelta
from typing import Optional, Any
import uuid
import pytz
from icalendar import Calendar as ICalCalendar, Event as ICalEvent
from recurring_ical_events import of as recurring_events_of

from .event_wrapper import (
    CalEvent, CalendarSource, EventInstance,
    create_instance, parse_icalendar
)


class EventRepository:
    """
    Repository for CalEvent objects.
    
    Stores master events by source ID, provides recurrence expansion
    to EventInstance objects.
    """
    
    def __init__(self):
        # CalEvent objects stored by source_id -> uid -> CalEvent
        self._events: dict[str, dict[str, CalEvent]] = {}
        self._sources: dict[str, CalendarSource] = {}
        # Track pending operations by event UID
        self._pending_operations: dict[str, str] = {}
    
    # ==================== Source Management ====================
    
    def add_source(self, source: CalendarSource):
        """Register a calendar source."""
        self._sources[source.id] = source
        if source.id not in self._events:
            self._events[source.id] = {}
    
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
    
    def store_events(self, source_id: str, events: list[CalEvent]):
        """
        Store CalEvent objects for a source (replaces existing).
        
        Args:
            source_id: Calendar source ID
            events: List of CalEvent objects from CalDAV/ICS
        """
        if source_id not in self._sources:
            raise ValueError(f"Unknown source: {source_id}")
        
        self._events[source_id] = {e.uid: e for e in events}
    
    def add_event(self, event: CalEvent):
        """Add or update a single CalEvent."""
        source_id = event.source.id
        if source_id not in self._events:
            self._events[source_id] = {}
        self._events[source_id][event.uid] = event
    
    def remove_event(self, source_id: str, uid: str) -> bool:
        """Remove an event from a calendar."""
        if source_id not in self._events:
            return False
        if uid not in self._events[source_id]:
            return False
        del self._events[source_id][uid]
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
        
        # Apply pending operations
        for inst in instances:
            if inst.event.uid in self._pending_operations:
                inst.event.pending_operation = self._pending_operations[inst.event.uid]
        
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
        """Mark an event as having a pending operation."""
        self._pending_operations[uid] = operation
    
    def clear_pending(self, uid: str):
        """Clear pending status after successful sync."""
        self._pending_operations.pop(uid, None)
    
    def has_pending(self, uid: str) -> bool:
        """Check if an event has a pending operation."""
        return uid in self._pending_operations
    
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
