"""
Unified event repository using recurring_ical_events for instance expansion.

This module provides a single source of truth for all calendar events,
whether from CalDAV servers or ICS subscriptions. It stores raw iCalendar
data and uses the recurring_ical_events library for proper recurrence handling.
"""

from datetime import datetime, date, timedelta
from typing import Optional, Iterator, Any
from dataclasses import dataclass
import uuid
from icalendar import Calendar as ICalCalendar, Event as ICalEvent
from icalendar.prop import vRecur
import pytz

# recurring_ical_events handles RRULE, RDATE, EXDATE properly
try:
    from recurring_ical_events import of as recurring_events_of
    HAS_RECURRING_ICAL_EVENTS = True
except ImportError:
    HAS_RECURRING_ICAL_EVENTS = False
    print("Warning: recurring_ical_events not installed. Recurrence expansion will be limited.")

from .event_wrapper import CalEvent, CalendarSource, parse_icalendar


class CalendarData:
    """
    Holds raw iCalendar data for a single calendar.
    """
    def __init__(self, source: CalendarSource, ical_text: Optional[str] = None):
        self.source = source
        self._ical_text: Optional[str] = ical_text
        self._calendar: Optional[ICalCalendar] = None
        
        if ical_text:
            self._parse_calendar()
    
    def _parse_calendar(self):
        """Parse the raw iCalendar text."""
        if self._ical_text:
            try:
                self._calendar = parse_icalendar(self._ical_text)
            except Exception as e:
                print(f"Error parsing calendar {self.source.name}: {e}")
                self._calendar = ICalCalendar()
    
    def update_ical(self, ical_text: str):
        """Update with new iCalendar data."""
        self._ical_text = ical_text
        self._parse_calendar()
    
    def add_event(self, event: ICalEvent):
        """Add or update an event in this calendar."""
        if self._calendar is None:
            self._calendar = ICalCalendar()
        
        # Remove existing event with same UID if present
        uid = str(event.get('UID', ''))
        if uid:
            self.remove_event(uid)
        
        self._calendar.add_component(event)
    
    def remove_event(self, uid: str) -> bool:
        """Remove an event by UID."""
        if self._calendar is None:
            return False
        
        for component in list(self._calendar.subcomponents):
            if component.name == 'VEVENT':
                event_uid = str(component.get('UID', ''))
                if event_uid == uid:
                    self._calendar.subcomponents.remove(component)
                    return True
        return False
    
    def get_event(self, uid: str) -> Optional[ICalEvent]:
        """Get an event by UID."""
        if self._calendar is None:
            return None
        
        for component in self._calendar.subcomponents:
            if component.name == 'VEVENT':
                event_uid = str(component.get('UID', ''))
                if event_uid == uid:
                    return component
        return None
    
    def get_all_events(self) -> list[ICalEvent]:
        """Get all events in this calendar (unexpanded)."""
        if self._calendar is None:
            return []
        
        return [
            component for component in self._calendar.subcomponents
            if component.name == 'VEVENT'
        ]
    
    def get_expanded_events(self, start: datetime, end: datetime) -> list[CalEvent]:
        """
        Get all event instances within a time range, with recurrences expanded.
        
        Uses recurring_ical_events library for proper RRULE handling.
        """
        if self._calendar is None:
            return []
        
        events = []
        
        if HAS_RECURRING_ICAL_EVENTS:
            try:
                # recurring_ical_events handles all the complexity
                expanded = recurring_events_of(self._calendar).between(start, end)
                
                for ical_event in expanded:
                    # Get the instance start time for recurring events
                    dtstart = ical_event.get('DTSTART')
                    instance_start = None
                    
                    if dtstart:
                        dt_val = dtstart.dt
                        if isinstance(dt_val, date) and not isinstance(dt_val, datetime):
                            dt_val = datetime.combine(dt_val, datetime.min.time())
                        instance_start = dt_val
                    
                    # Wrap in CalEvent
                    cal_event = CalEvent(
                        event=ical_event,
                        source=self.source,
                        recurrence_id=instance_start if ical_event.get('RRULE') else None,
                        _raw_ical=self._ical_text
                    )
                    events.append(cal_event)
                    
            except Exception as e:
                print(f"Error expanding events for {self.source.name}: {e}")
                # Fallback to simple expansion
                events = self._simple_expand(start, end)
        else:
            # No recurring_ical_events library - use simple expansion
            events = self._simple_expand(start, end)
        
        return events
    
    def _simple_expand(self, start: datetime, end: datetime) -> list[CalEvent]:
        """
        Simple event expansion fallback (non-recurring only).
        Used when recurring_ical_events is not available.
        """
        events = []
        
        for ical_event in self.get_all_events():
            try:
                dtstart = ical_event.get('DTSTART')
                dtend = ical_event.get('DTEND')
                
                if dtstart is None:
                    continue
                
                start_dt = dtstart.dt
                if isinstance(start_dt, date) and not isinstance(start_dt, datetime):
                    start_dt = datetime.combine(start_dt, datetime.min.time())
                
                if dtend:
                    end_dt = dtend.dt
                    if isinstance(end_dt, date) and not isinstance(end_dt, datetime):
                        end_dt = datetime.combine(end_dt, datetime.min.time())
                else:
                    end_dt = start_dt + timedelta(hours=1)
                
                # Ensure timezone awareness for comparison
                if start_dt.tzinfo is None:
                    start_dt = pytz.UTC.localize(start_dt)
                if end_dt.tzinfo is None:
                    end_dt = pytz.UTC.localize(end_dt)
                
                cmp_start = start if start.tzinfo else pytz.UTC.localize(start)
                cmp_end = end if end.tzinfo else pytz.UTC.localize(end)
                
                # Check if event falls within range
                if end_dt >= cmp_start and start_dt <= cmp_end:
                    cal_event = CalEvent(
                        event=ical_event,
                        source=self.source,
                        _raw_ical=self._ical_text
                    )
                    events.append(cal_event)
                    
            except Exception as e:
                print(f"Error processing event: {e}")
                continue
        
        return events
    
    @property
    def ical_text(self) -> Optional[str]:
        """Get the raw iCalendar text."""
        return self._ical_text
    
    @property
    def calendar(self) -> Optional[ICalCalendar]:
        """Get the parsed Calendar object."""
        return self._calendar


class EventRepository:
    """
    Unified repository for all calendar events.
    
    Stores CalendarData from all sources (CalDAV and ICS) and provides
    a single interface for querying events with proper recurrence expansion.
    """
    
    def __init__(self):
        self._calendars: dict[str, CalendarData] = {}
        self._sources: dict[str, CalendarSource] = {}
        # Track pending operations by event UID - persists across cache invalidations
        self._pending_operations: dict[str, str] = {}  # uid -> operation ("create", "update", "delete")
    
    def add_source(self, source: CalendarSource):
        """Register a calendar source."""
        self._sources[source.id] = source
        if source.id not in self._calendars:
            self._calendars[source.id] = CalendarData(source)
    
    def remove_source(self, source_id: str):
        """Remove a calendar source and its data."""
        self._sources.pop(source_id, None)
        self._calendars.pop(source_id, None)
    
    def get_source(self, source_id: str) -> Optional[CalendarSource]:
        """Get a calendar source by ID."""
        return self._sources.get(source_id)
    
    def get_all_sources(self) -> list[CalendarSource]:
        """Get all calendar sources."""
        return list(self._sources.values())
    
    def update_calendar_data(self, source_id: str, ical_text: str):
        """
        Update calendar data from raw iCalendar text.
        
        Args:
            source_id: The calendar source ID
            ical_text: Raw VCALENDAR text
        """
        if source_id not in self._sources:
            raise ValueError(f"Unknown source: {source_id}")
        
        if source_id in self._calendars:
            self._calendars[source_id].update_ical(ical_text)
        else:
            self._calendars[source_id] = CalendarData(
                self._sources[source_id],
                ical_text
            )
    
    def add_event(self, source_id: str, event: ICalEvent):
        """Add an event to a calendar."""
        if source_id not in self._calendars:
            if source_id in self._sources:
                self._calendars[source_id] = CalendarData(self._sources[source_id])
            else:
                raise ValueError(f"Unknown source: {source_id}")
        
        self._calendars[source_id].add_event(event)
    
    def remove_event(self, source_id: str, uid: str) -> bool:
        """Remove an event from a calendar."""
        if source_id not in self._calendars:
            return False
        return self._calendars[source_id].remove_event(uid)
    
    def get_event(self, source_id: str, uid: str) -> Optional[CalEvent]:
        """Get a specific event by source and UID."""
        if source_id not in self._calendars:
            return None
        
        ical_event = self._calendars[source_id].get_event(uid)
        if ical_event is None:
            return None
        
        return CalEvent(
            event=ical_event,
            source=self._sources[source_id],
            _raw_ical=self._calendars[source_id].ical_text
        )
    
    def get_events(
        self,
        start: datetime,
        end: datetime,
        source_ids: Optional[list[str]] = None
    ) -> list[CalEvent]:
        """
        Get all events within a time range, with recurrences expanded.
        
        Args:
            start: Start of time range
            end: End of time range
            source_ids: Optional list of source IDs to filter by
            
        Returns:
            List of CalEvent objects (including recurring instances)
        """
        all_events = []
        
        calendars_to_query = (
            [self._calendars[sid] for sid in source_ids if sid in self._calendars]
            if source_ids
            else self._calendars.values()
        )
        
        for cal_data in calendars_to_query:
            events = cal_data.get_expanded_events(start, end)
            all_events.extend(events)
        
        # Apply pending operations from tracking
        for event in all_events:
            if event.uid in self._pending_operations:
                event.pending_operation = self._pending_operations[event.uid]
        
        # Sort by start time (handle mixed tz-aware/naive)
        def sort_key(e):
            dt = e.dtstart
            if dt.tzinfo is not None:
                return dt.replace(tzinfo=None)  # Strip tz for comparison
            return dt
        
        all_events.sort(key=sort_key)
        
        return all_events
    
    # ==================== Pending Operations Tracking ====================
    
    def mark_pending(self, uid: str, operation: str):
        """Mark an event as having a pending operation."""
        self._pending_operations[uid] = operation
    
    def clear_pending(self, uid: str):
        """Clear pending status for an event (after successful sync)."""
        self._pending_operations.pop(uid, None)
    
    def has_pending(self, uid: str) -> bool:
        """Check if an event has a pending operation."""
        return uid in self._pending_operations
    
    def get_pending_operation(self, uid: str) -> Optional[str]:
        """Get the pending operation for an event."""
        return self._pending_operations.get(uid)
    
    def get_calendar_data(self, source_id: str) -> Optional[CalendarData]:
        """Get the raw calendar data for a source."""
        return self._calendars.get(source_id)
    
    def clear(self):
        """Clear all calendar data (but keep sources)."""
        for cal_data in self._calendars.values():
            cal_data._calendar = None
            cal_data._ical_text = None
    
    def clear_source(self, source_id: str):
        """Clear data for a specific source."""
        if source_id in self._calendars:
            self._calendars[source_id]._calendar = None
            self._calendars[source_id]._ical_text = None
    
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
        Create a new event in the repository.
        
        Args:
            source_id: Calendar source ID
            summary: Event title
            start: Start datetime
            end: End datetime
            description: Event description
            location: Event location
            all_day: True for all-day events
            recurrence: Recurrence rule (RecurrenceRule dataclass or None)
        
        Returns:
            CalEvent wrapper for the created event, or None if failed
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
        
        # Handle dates
        if all_day:
            event.add('dtstart', start.date())
            event.add('dtend', end.date())
        else:
            event.add('dtstart', start)
            event.add('dtend', end)
        
        # Handle recurrence
        if recurrence:
            rrule = self._build_rrule(recurrence)
            if rrule:
                event.add('rrule', rrule)
        
        # Add to calendar data
        self.add_event(source_id, event)
        
        # Return wrapped event
        return CalEvent(
            event=event,
            source=source,
            pending_operation="create"
        )
    
    def update_event(
        self,
        cal_event: CalEvent,
        summary: Optional[str] = None,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        description: Optional[str] = None,
        location: Optional[str] = None,
        all_day: Optional[bool] = None,
        recurrence: Optional[Any] = None,
    ) -> bool:
        """
        Update an existing event in the repository.
        
        Args:
            cal_event: The CalEvent to update
            Other args: Fields to update (None = no change)
        
        Returns:
            True if successful
        """
        source_id = cal_event.source.id
        if source_id not in self._calendars:
            return False
        
        ical_event = self._calendars[source_id].get_event(cal_event.uid)
        if ical_event is None:
            return False
        
        # Update fields
        if summary is not None:
            if 'SUMMARY' in ical_event:
                del ical_event['SUMMARY']
            ical_event.add('summary', summary)
        
        if description is not None:
            if 'DESCRIPTION' in ical_event:
                del ical_event['DESCRIPTION']
            if description:
                ical_event.add('description', description)
        
        if location is not None:
            if 'LOCATION' in ical_event:
                del ical_event['LOCATION']
            if location:
                ical_event.add('location', location)
        
        # Handle date changes
        if start is not None or end is not None or all_day is not None:
            new_start = start if start is not None else cal_event.dtstart
            new_end = end if end is not None else cal_event.dtend
            new_all_day = all_day if all_day is not None else cal_event.all_day
            
            if 'DTSTART' in ical_event:
                del ical_event['DTSTART']
            if 'DTEND' in ical_event:
                del ical_event['DTEND']
            
            if new_all_day:
                ical_event.add('dtstart', new_start.date())
                ical_event.add('dtend', new_end.date())
            else:
                ical_event.add('dtstart', new_start)
                ical_event.add('dtend', new_end)
        
        # Handle recurrence
        if recurrence is not None:
            if 'RRULE' in ical_event:
                del ical_event['RRULE']
            rrule = self._build_rrule(recurrence)
            if rrule:
                ical_event.add('rrule', rrule)
        
        # Update modified time
        if 'LAST-MODIFIED' in ical_event:
            del ical_event['LAST-MODIFIED']
        ical_event.add('last-modified', datetime.now(pytz.UTC))
        
        # Mark as pending update
        cal_event.pending_operation = "update"
        
        return True
    
    def delete_event(self, cal_event: CalEvent) -> bool:
        """
        Delete an event from the repository.
        
        Args:
            cal_event: The CalEvent to delete
        
        Returns:
            True if successful
        """
        source_id = cal_event.source.id
        return self.remove_event(source_id, cal_event.uid)
    
    def _build_rrule(self, recurrence: Any) -> Optional[dict]:
        """
        Build an RRULE dict from a recurrence specification.
        
        Args:
            recurrence: RecurrenceRule dataclass or dict-like object
        
        Returns:
            Dict suitable for icalendar RRULE, or None
        """
        if recurrence is None:
            return None
        
        # Handle RecurrenceRule dataclass (from old code)
        freq = getattr(recurrence, 'frequency', None) or recurrence.get('frequency') if hasattr(recurrence, 'get') else None
        if not freq:
            return None
        
        rrule = {'freq': freq.upper() if isinstance(freq, str) else freq}
        
        # Interval
        interval = getattr(recurrence, 'interval', None) or (recurrence.get('interval') if hasattr(recurrence, 'get') else None)
        if interval and interval > 1:
            rrule['interval'] = interval
        
        # Count
        count = getattr(recurrence, 'count', None) or (recurrence.get('count') if hasattr(recurrence, 'get') else None)
        if count:
            rrule['count'] = count
        
        # Until
        until = getattr(recurrence, 'until', None) or (recurrence.get('until') if hasattr(recurrence, 'get') else None)
        if until:
            rrule['until'] = until
        
        # Weekdays (BYDAY)
        byday = getattr(recurrence, 'by_day', None) or (recurrence.get('by_day') if hasattr(recurrence, 'get') else None)
        if byday:
            rrule['byday'] = byday
        
        return rrule
