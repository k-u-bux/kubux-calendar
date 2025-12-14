"""
Three-tier event model for Kubux Calendar.

CalEvent: Master event from CalDAV/ICS - what gets synced
EventInstance: A specific occurrence of a CalEvent
InstanceSlice: A display portion of an EventInstance on a single day
"""

from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from typing import Optional
from icalendar import Event as ICalEvent, Calendar as ICalCalendar
import pytz


@dataclass
class CalendarSource:
    """
    Metadata about an event's source calendar.
    """
    id: str
    name: str
    color: str = "#4285f4"
    account_name: str = ""  # For CalDAV accounts
    read_only: bool = False
    source_type: str = "caldav"  # "caldav" or "ics"
    visible: bool = True
    
    def __hash__(self):
        return hash(self.id)
    
    def __eq__(self, other):
        if isinstance(other, CalendarSource):
            return self.id == other.id
        return False


@dataclass
class CalEvent:
    """
    Master event from CalDAV or ICS subscription.
    
    This is what gets stored, synced, and referenced by EventInstances.
    Wraps an icalendar.Event with source metadata.
    """
    event: ICalEvent  # The raw iCalendar event
    source: CalendarSource
    
    # Sync metadata
    pending_operation: Optional[str] = None  # "create", "update", "delete"
    
    # Reference for CalDAV sync (URL for PUT/DELETE)
    caldav_href: Optional[str] = None
    
    # ==================== Core Properties ====================
    
    @property
    def uid(self) -> str:
        uid = self.event.get('UID')
        return str(uid) if uid else ''
    
    @property
    def summary(self) -> str:
        summary = self.event.get('SUMMARY')
        return str(summary) if summary else 'Untitled'
    
    @summary.setter
    def summary(self, value: str):
        if 'SUMMARY' in self.event:
            del self.event['SUMMARY']
        self.event.add('summary', value)
        if not self.source.read_only:
            self.pending_operation = "update"
    
    @property
    def description(self) -> str:
        desc = self.event.get('DESCRIPTION')
        return str(desc) if desc else ''
    
    @description.setter
    def description(self, value: str):
        if 'DESCRIPTION' in self.event:
            del self.event['DESCRIPTION']
        if value:
            self.event.add('description', value)
        if not self.source.read_only:
            self.pending_operation = "update"
    
    @property
    def location(self) -> str:
        loc = self.event.get('LOCATION')
        return str(loc) if loc else ''
    
    @location.setter
    def location(self, value: str):
        if 'LOCATION' in self.event:
            del self.event['LOCATION']
        if value:
            self.event.add('location', value)
        if not self.source.read_only:
            self.pending_operation = "update"
    
    @property
    def dtstart(self) -> datetime:
        """Master event start time (always timezone-aware)."""
        dt = self.event.get('DTSTART')
        if dt is None:
            return datetime.now(pytz.UTC)
        
        val = dt.dt
        if isinstance(val, date) and not isinstance(val, datetime):
            val = datetime.combine(val, datetime.min.time())
        
        if val.tzinfo is None:
            val = pytz.UTC.localize(val)
        
        return val
    
    @dtstart.setter
    def dtstart(self, value: datetime):
        if 'DTSTART' in self.event:
            del self.event['DTSTART']
        if self.all_day:
            self.event.add('dtstart', value.date())
        else:
            self.event.add('dtstart', value)
        if not self.source.read_only:
            self.pending_operation = "update"
    
    @property
    def dtend(self) -> datetime:
        """Master event end time (always timezone-aware)."""
        dt = self.event.get('DTEND')
        if dt is None:
            return self.dtstart + timedelta(hours=1)
        
        val = dt.dt
        if isinstance(val, date) and not isinstance(val, datetime):
            val = datetime.combine(val, datetime.min.time())
        
        if val.tzinfo is None:
            val = pytz.UTC.localize(val)
        
        return val
    
    @dtend.setter
    def dtend(self, value: datetime):
        if 'DTEND' in self.event:
            del self.event['DTEND']
        if self.all_day:
            self.event.add('dtend', value.date())
        else:
            self.event.add('dtend', value)
        if not self.source.read_only:
            self.pending_operation = "update"
    
    @property
    def all_day(self) -> bool:
        dt = self.event.get('DTSTART')
        if dt is None:
            return False
        return isinstance(dt.dt, date) and not isinstance(dt.dt, datetime)
    
    @all_day.setter
    def all_day(self, value: bool):
        current_start = self.dtstart
        current_end = self.dtend
        
        if 'DTSTART' in self.event:
            del self.event['DTSTART']
        if 'DTEND' in self.event:
            del self.event['DTEND']
        
        if value:
            self.event.add('dtstart', current_start.date())
            self.event.add('dtend', current_end.date())
        else:
            self.event.add('dtstart', current_start)
            self.event.add('dtend', current_end)
        
        if not self.source.read_only:
            self.pending_operation = "update"
    
    @property
    def duration(self) -> timedelta:
        return self.dtend - self.dtstart
    
    @property
    def is_recurring(self) -> bool:
        return self.event.get('RRULE') is not None
    
    @property
    def rrule(self) -> Optional[str]:
        rrule = self.event.get('RRULE')
        return rrule.to_ical().decode('utf-8') if rrule else None
    
    @property
    def recurrence(self):
        """
        Parse RRULE and return as RecurrenceRule dataclass for GUI.
        Returns None if no recurrence.
        """
        rrule = self.event.get('RRULE')
        if rrule is None:
            return None
        
        from dataclasses import dataclass
        from typing import Optional as Opt, List
        
        @dataclass
        class RecurrenceRule:
            frequency: str
            interval: int = 1
            count: Opt[int] = None
            until: Opt[datetime] = None
            by_day: Opt[List[str]] = None
        
        # Parse RRULE components
        freq = rrule.get('FREQ', [None])[0]
        if freq is None:
            return None
        
        interval = rrule.get('INTERVAL', [1])[0]
        count = rrule.get('COUNT', [None])[0]
        until = rrule.get('UNTIL', [None])[0]
        byday = rrule.get('BYDAY', None)
        
        # Convert byday to list of strings
        by_day_list = None
        if byday:
            by_day_list = [str(d) for d in byday] if isinstance(byday, list) else [str(byday)]
        
        return RecurrenceRule(
            frequency=str(freq),
            interval=int(interval) if interval else 1,
            count=int(count) if count else None,
            until=until,
            by_day=by_day_list
        )
    
    @recurrence.setter
    def recurrence(self, rule):
        """
        Set recurrence from a RecurrenceRule dataclass or None.
        Updates the RRULE on the underlying icalendar.Event.
        """
        # Remove existing RRULE if any
        if 'RRULE' in self.event:
            del self.event['RRULE']
        
        if rule is None:
            # No recurrence - already removed
            pass
        else:
            # Build RRULE dict
            rrule_dict = {'freq': rule.frequency}
            
            if rule.interval and rule.interval > 1:
                rrule_dict['interval'] = rule.interval
            
            if rule.count:
                rrule_dict['count'] = rule.count
            
            if rule.until:
                rrule_dict['until'] = rule.until
            
            if rule.by_day:
                rrule_dict['byday'] = rule.by_day
            
            self.event.add('rrule', rrule_dict)
        
        if not self.source.read_only:
            self.pending_operation = "update"
    
    # ==================== Source Properties ====================
    
    @property
    def calendar_id(self) -> str:
        return self.source.id
    
    @property
    def calendar_name(self) -> str:
        return self.source.name
    
    @property
    def calendar_color(self) -> str:
        return self.source.color
    
    @property
    def read_only(self) -> bool:
        return self.source.read_only
    
    @property
    def sync_status(self) -> str:
        if self.pending_operation:
            return "pending"
        return ""
    
    # ==================== Aliases ====================
    
    @property
    def start(self) -> datetime:
        return self.dtstart
    
    @start.setter
    def start(self, value: datetime):
        self.dtstart = value
    
    @property
    def end(self) -> datetime:
        return self.dtend
    
    @end.setter
    def end(self, value: datetime):
        self.dtend = value
    
    @property
    def source_type(self) -> str:
        return self.source.source_type
    
    def __hash__(self):
        return hash(self.uid)
    
    def __eq__(self, other):
        if isinstance(other, CalEvent):
            return self.uid == other.uid
        return False
    
    def __repr__(self):
        return f"CalEvent(uid={self.uid!r}, summary={self.summary!r})"


@dataclass
class EventInstance:
    """
    A specific occurrence of a CalEvent.
    
    For non-recurring events: one instance with the same times as the master.
    For recurring events: one instance per occurrence in the queried date range.
    """
    event: CalEvent  # The master event
    start: datetime  # This instance's start time
    end: datetime    # This instance's end time
    
    @property
    def uid(self) -> str:
        return self.event.uid
    
    @property
    def summary(self) -> str:
        return self.event.summary
    
    @property
    def description(self) -> str:
        return self.event.description
    
    @property
    def location(self) -> str:
        return self.event.location
    
    @property
    def all_day(self) -> bool:
        return self.event.all_day
    
    @property
    def is_recurring(self) -> bool:
        return self.event.is_recurring
    
    @property
    def calendar_color(self) -> str:
        return self.event.calendar_color
    
    @property
    def read_only(self) -> bool:
        return self.event.read_only
    
    @property
    def sync_status(self) -> str:
        return self.event.sync_status
    
    @property
    def source(self) -> CalendarSource:
        return self.event.source
    
    @property
    def duration(self) -> timedelta:
        return self.end - self.start
    
    @property
    def calendar_name(self) -> str:
        return self.event.calendar_name
    
    @property
    def recurrence(self):
        """Delegate to CalEvent's recurrence property."""
        return self.event.recurrence
    
    def __hash__(self):
        return hash((self.event.uid, self.start))
    
    def __eq__(self, other):
        if isinstance(other, EventInstance):
            return self.event.uid == other.event.uid and self.start == other.start
        return False
    
    def __repr__(self):
        return f"EventInstance({self.summary!r}, {self.start})"


@dataclass
class InstanceSlice:
    """
    A display portion of an EventInstance on a single day.
    
    For single-day events: one slice covering the full event.
    For multi-day events: one slice per day the event spans.
    Maps directly to GUI rectangles.
    """
    instance: EventInstance  # The parent instance
    display_date: date       # Which day this slice is for
    visible_start_hour: float  # Start hour on this day (0.0-24.0)
    visible_end_hour: float    # End hour on this day (0.0-24.0)
    
    @property
    def uid(self) -> str:
        return self.instance.uid
    
    @property
    def summary(self) -> str:
        return self.instance.summary
    
    @property
    def description(self) -> str:
        return self.instance.description
    
    @property
    def location(self) -> str:
        return self.instance.location
    
    @property
    def all_day(self) -> bool:
        return self.instance.all_day
    
    @property
    def is_recurring(self) -> bool:
        return self.instance.is_recurring
    
    @property
    def calendar_color(self) -> str:
        return self.instance.calendar_color
    
    @property
    def read_only(self) -> bool:
        return self.instance.read_only
    
    @property
    def sync_status(self) -> str:
        return self.instance.sync_status
    
    @property
    def event(self) -> CalEvent:
        """Access the underlying CalEvent for modifications."""
        return self.instance.event
    
    @property
    def source(self) -> CalendarSource:
        return self.instance.source
    
    # GUI convenience
    @property
    def start(self) -> datetime:
        return self.instance.start
    
    @property
    def end(self) -> datetime:
        return self.instance.end
    
    def __repr__(self):
        return f"InstanceSlice({self.summary!r}, {self.display_date}, {self.visible_start_hour:.1f}-{self.visible_end_hour:.1f})"


# ==================== Factory Functions ====================

def create_instance(event: CalEvent, instance_start: datetime = None) -> EventInstance:
    """
    Create an EventInstance from a CalEvent.
    
    For non-recurring events, uses the event's own times.
    For recurring events, instance_start specifies this occurrence.
    """
    if instance_start is None:
        # Non-recurring: use master times
        return EventInstance(
            event=event,
            start=event.dtstart,
            end=event.dtend
        )
    else:
        # Recurring: calculate end from duration
        duration = event.duration
        return EventInstance(
            event=event,
            start=instance_start,
            end=instance_start + duration
        )


def create_slices(instance: EventInstance) -> list[InstanceSlice]:
    """
    Create InstanceSlice objects for displaying an EventInstance.
    
    For single-day events: returns one slice.
    For multi-day events: returns one slice per day.
    """
    slices = []
    
    start = instance.start
    end = instance.end
    
    # Strip timezone for date comparison
    start_naive = start.replace(tzinfo=None) if start.tzinfo else start
    end_naive = end.replace(tzinfo=None) if end.tzinfo else end
    
    start_date = start_naive.date()
    end_date = end_naive.date()
    
    if instance.all_day:
        # All-day events: one slice per day
        current_date = start_date
        while current_date < end_date:  # end_date is exclusive for all-day
            slices.append(InstanceSlice(
                instance=instance,
                display_date=current_date,
                visible_start_hour=0.0,
                visible_end_hour=24.0
            ))
            current_date += timedelta(days=1)
    elif start_date == end_date:
        # Single-day event
        slices.append(InstanceSlice(
            instance=instance,
            display_date=start_date,
            visible_start_hour=start_naive.hour + start_naive.minute / 60.0,
            visible_end_hour=end_naive.hour + end_naive.minute / 60.0
        ))
    else:
        # Multi-day event
        current_date = start_date
        while current_date <= end_date:
            if current_date == start_date:
                # First day: from start time to midnight
                vis_start = start_naive.hour + start_naive.minute / 60.0
                vis_end = 24.0
            elif current_date == end_date:
                # Last day: from midnight to end time
                vis_start = 0.0
                vis_end = end_naive.hour + end_naive.minute / 60.0
            else:
                # Middle day: full day
                vis_start = 0.0
                vis_end = 24.0
            
            slices.append(InstanceSlice(
                instance=instance,
                display_date=current_date,
                visible_start_hour=vis_start,
                visible_end_hour=vis_end
            ))
            current_date += timedelta(days=1)
    
    return slices


# ==================== Parse Utilities ====================

def parse_icalendar(ical_text: str) -> ICalCalendar:
    """Parse iCalendar text into an icalendar.Calendar object."""
    return ICalCalendar.from_ical(ical_text)
