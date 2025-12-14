"""
Lightweight wrapper around icalendar.Event with source metadata.

This module provides a clean separation between the raw iCalendar data
and the application-level metadata (source calendar, sync status, etc).
The wrapper delegates to the underlying icalendar.Event rather than
duplicating its functionality.
"""

from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from typing import Optional, Union
from icalendar import Event as ICalEvent, Calendar as ICalCalendar
import pytz


@dataclass
class CalendarSource:
    """
    Metadata about an event's source calendar.
    
    This is separate from the event itself and contains information
    about where the event came from and how to display it.
    """
    id: str
    name: str
    color: str = "#4285f4"  # Default Google blue
    account_name: str = ""  # For CalDAV accounts
    read_only: bool = False  # True for ICS subscriptions
    source_type: str = "caldav"  # "caldav" or "ics"
    visible: bool = True  # Whether to display in calendar view
    
    def __hash__(self):
        return hash(self.id)
    
    def __eq__(self, other):
        if isinstance(other, CalendarSource):
            return self.id == other.id
        return False


@dataclass
class CalEvent:
    """
    Lightweight wrapper around icalendar.Event with source metadata.
    
    Does NOT duplicate Event facilities - delegates to self.event.
    This provides a clean interface for the GUI while keeping the
    raw iCalendar data intact for proper recurrence handling.
    """
    event: ICalEvent  # The actual iCalendar event
    source: CalendarSource  # Where this event comes from
    
    # For recurring event instances - the specific occurrence datetime
    recurrence_id: Optional[datetime] = None
    
    # Sync metadata (only for CalDAV, not ICS subscriptions)
    last_sync: Optional[datetime] = None
    sync_retries: int = 0
    pending_operation: Optional[str] = None  # "create", "update", "delete"
    
    # Raw VCALENDAR text (for roundtrip preservation)
    _raw_ical: Optional[str] = None
    
    # ==================== Convenience Properties ====================
    # These delegate to self.event rather than duplicating data
    
    @property
    def uid(self) -> str:
        """Get the event's unique identifier."""
        uid = self.event.get('UID')
        return str(uid) if uid else ''
    
    @property
    def summary(self) -> str:
        """Get the event's title/summary."""
        summary = self.event.get('SUMMARY')
        return str(summary) if summary else 'Untitled'
    
    @property
    def description(self) -> str:
        """Get the event's description."""
        desc = self.event.get('DESCRIPTION')
        return str(desc) if desc else ''
    
    @property
    def location(self) -> str:
        """Get the event's location."""
        loc = self.event.get('LOCATION')
        return str(loc) if loc else ''
    
    @property
    def start(self) -> datetime:
        """Alias for dtstart for GUI compatibility."""
        return self.dtstart
    
    @property
    def end(self) -> datetime:
        """Alias for dtend for GUI compatibility."""
        return self.dtend
    
    @property
    def dtstart(self) -> datetime:
        """
        Get the event's start time as a datetime.
        For recurring instances, returns recurrence_id if set.
        Always returns timezone-aware datetime.
        """
        if self.recurrence_id:
            val = self.recurrence_id
        else:
            dt = self.event.get('DTSTART')
            if dt is None:
                return datetime.now(pytz.UTC)
            
            val = dt.dt
            if isinstance(val, date) and not isinstance(val, datetime):
                # All-day event - convert to datetime at midnight UTC
                val = datetime.combine(val, datetime.min.time())
        
        # Ensure timezone-aware
        if val.tzinfo is None:
            val = pytz.UTC.localize(val)
        
        return val
    
    @property
    def dtend(self) -> datetime:
        """Get the event's end time as a datetime. Always timezone-aware."""
        dt = self.event.get('DTEND')
        if dt is None:
            # No end time - use start + 1 hour
            return self.dtstart + timedelta(hours=1)
        
        val = dt.dt
        if isinstance(val, date) and not isinstance(val, datetime):
            # All-day event - convert to datetime
            val = datetime.combine(val, datetime.min.time())
        
        # For recurring instances, adjust end by the same delta from start
        if self.recurrence_id:
            original_start = self.event.get('DTSTART')
            if original_start:
                original_start_dt = original_start.dt
                if isinstance(original_start_dt, date) and not isinstance(original_start_dt, datetime):
                    original_start_dt = datetime.combine(original_start_dt, datetime.min.time())
                if original_start_dt.tzinfo is None:
                    original_start_dt = pytz.UTC.localize(original_start_dt)
                rec_id = self.recurrence_id
                if rec_id.tzinfo is None:
                    rec_id = pytz.UTC.localize(rec_id)
                delta = rec_id - original_start_dt
                val = val + delta
        
        # Ensure timezone-aware
        if val.tzinfo is None:
            val = pytz.UTC.localize(val)
        
        return val
    
    @property
    def all_day(self) -> bool:
        """Check if this is an all-day event."""
        dt = self.event.get('DTSTART')
        if dt is None:
            return False
        # All-day events have date values, not datetime
        return isinstance(dt.dt, date) and not isinstance(dt.dt, datetime)
    
    @property
    def duration(self) -> timedelta:
        """Get the event's duration."""
        return self.dtend - self.dtstart
    
    @property
    def is_recurring(self) -> bool:
        """Check if this event has recurrence rules."""
        return self.event.get('RRULE') is not None
    
    @property
    def rrule(self) -> Optional[str]:
        """Get the RRULE as a string, if present."""
        rrule = self.event.get('RRULE')
        return rrule.to_ical().decode('utf-8') if rrule else None
    
    # ==================== Source-based Properties ====================
    
    @property
    def calendar_id(self) -> str:
        """Get the source calendar's ID."""
        return self.source.id
    
    @property
    def calendar_name(self) -> str:
        """Get the source calendar's name."""
        return self.source.name
    
    @property
    def calendar_color(self) -> str:
        """Get the source calendar's display color."""
        return self.source.color
    
    @property
    def read_only(self) -> bool:
        """Check if this event is read-only (from ICS subscription)."""
        return self.source.read_only
    
    @property
    def source_type(self) -> str:
        """Get the source type ('caldav' or 'ics')."""
        return self.source.source_type
    
    @property
    def sync_status(self) -> str:
        """Get sync status. Returns 'pending' if there's a pending operation."""
        if self.pending_operation:
            return "pending"
        return ""
    
    @property
    def recurrence(self):
        """Get recurrence rule. Returns None for now (legacy compatibility)."""
        return None
    
    # ==================== Instance Creation ====================
    
    def create_instance(self, instance_start: datetime) -> 'CalEvent':
        """
        Create a recurring instance of this event at the given start time.
        
        Args:
            instance_start: The start datetime for this instance
            
        Returns:
            A new CalEvent representing this specific occurrence
        """
        return CalEvent(
            event=self.event,
            source=self.source,
            recurrence_id=instance_start,
            last_sync=self.last_sync,
            sync_retries=self.sync_retries,
            pending_operation=self.pending_operation,
            _raw_ical=self._raw_ical
        )
    
    def __hash__(self):
        """Hash based on UID and recurrence_id for uniqueness."""
        return hash((self.uid, self.recurrence_id))
    
    def __eq__(self, other):
        if isinstance(other, CalEvent):
            return self.uid == other.uid and self.recurrence_id == other.recurrence_id
        return False
    
    def __repr__(self):
        return f"CalEvent(uid={self.uid!r}, summary={self.summary!r}, dtstart={self.dtstart})"


def parse_icalendar(ical_text: str) -> ICalCalendar:
    """
    Parse iCalendar text into an icalendar.Calendar object.
    
    Args:
        ical_text: Raw iCalendar text (VCALENDAR)
        
    Returns:
        Parsed Calendar object
    """
    return ICalCalendar.from_ical(ical_text)


def create_cal_event(
    event: ICalEvent,
    source: CalendarSource,
    raw_ical: Optional[str] = None
) -> CalEvent:
    """
    Create a CalEvent wrapper from an icalendar.Event.
    
    Args:
        event: The icalendar.Event object
        source: The CalendarSource metadata
        raw_ical: Optional raw VCALENDAR text for roundtrip
        
    Returns:
        A CalEvent wrapper
    """
    return CalEvent(
        event=event,
        source=source,
        _raw_ical=raw_ical
    )
