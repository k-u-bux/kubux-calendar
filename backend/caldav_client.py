"""
CalDAV client for Nextcloud calendar operations.

Provides CRUD operations for calendar events via CalDAV protocol.
"""

import caldav
from caldav.elements import dav, cdav
from datetime import datetime, timedelta
from typing import Optional
from dataclasses import dataclass, field
import pytz
from icalendar import Calendar as ICalendar, Event as ICalEvent
from icalendar import vRecur
import uuid


@dataclass
class CalendarInfo:
    """Information about a CalDAV calendar."""
    id: str
    name: str
    color: str
    url: str
    account_name: str
    writable: bool = True
    
    # Internal reference to the caldav.Calendar object
    _caldav_calendar: Optional[caldav.Calendar] = field(default=None, repr=False)


@dataclass
class RecurrenceRule:
    """Representation of an event recurrence rule."""
    frequency: str  # DAILY, WEEKLY, MONTHLY, YEARLY
    interval: int = 1
    count: Optional[int] = None  # Number of occurrences
    until: Optional[datetime] = None  # End date
    by_day: Optional[list[str]] = None  # e.g., ['MO', 'WE', 'FR']
    by_month_day: Optional[list[int]] = None  # e.g., [1, 15] for 1st and 15th
    by_month: Optional[list[int]] = None  # e.g., [1, 6] for Jan and June
    
    def to_rrule_dict(self) -> dict:
        """Convert to icalendar RRULE format."""
        rrule = {'FREQ': [self.frequency]}
        if self.interval != 1:
            rrule['INTERVAL'] = [self.interval]
        if self.count is not None:
            rrule['COUNT'] = [self.count]
        if self.until is not None:
            rrule['UNTIL'] = [self.until]
        if self.by_day:
            rrule['BYDAY'] = self.by_day
        if self.by_month_day:
            rrule['BYMONTHDAY'] = self.by_month_day
        if self.by_month:
            rrule['BYMONTH'] = self.by_month
        return rrule
    
    @classmethod
    def from_rrule(cls, rrule: vRecur) -> 'RecurrenceRule':
        """Create from an icalendar RRULE."""
        freq = rrule.get('FREQ', ['DAILY'])[0]
        interval = rrule.get('INTERVAL', [1])[0]
        count = rrule.get('COUNT', [None])[0]
        until = rrule.get('UNTIL', [None])[0]
        by_day = rrule.get('BYDAY')
        by_month_day = rrule.get('BYMONTHDAY')
        by_month = rrule.get('BYMONTH')
        
        return cls(
            frequency=freq,
            interval=interval,
            count=count,
            until=until,
            by_day=by_day,
            by_month_day=by_month_day,
            by_month=by_month
        )


@dataclass
class EventData:
    """Data structure for calendar events."""
    uid: str
    summary: str
    start: datetime
    end: datetime
    description: str = ""
    location: str = ""
    all_day: bool = False
    recurrence: Optional[RecurrenceRule] = None
    recurrence_id: Optional[datetime] = None  # For specific instance of recurring event
    
    # Source tracking
    calendar_id: str = ""
    calendar_name: str = ""
    calendar_color: str = "#4285f4"
    source_type: str = "caldav"  # "caldav" or "ics"
    read_only: bool = False
    
    # Internal reference for updates
    _caldav_event: Optional[caldav.Event] = field(default=None, repr=False)
    _raw_ical: Optional[str] = field(default=None, repr=False)
    
    @property
    def is_recurring(self) -> bool:
        """Check if this event has recurrence rules."""
        return self.recurrence is not None
    
    @property
    def duration(self) -> timedelta:
        """Get the duration of the event."""
        return self.end - self.start


class CalDAVClient:
    """Client for interacting with a Nextcloud CalDAV server."""
    
    def __init__(self, url: str, username: str, password: str, account_name: str = ""):
        """
        Initialize the CalDAV client.
        
        Args:
            url: Base URL of the Nextcloud instance (e.g., https://nextcloud.example.com)
            username: Nextcloud username
            password: Nextcloud password/app token
            account_name: Human-readable name for this account
        """
        self.base_url = url.rstrip('/')
        self.username = username
        self.password = password
        self.account_name = account_name or url
        
        # CalDAV URL is typically at /remote.php/dav
        self.caldav_url = f"{self.base_url}/remote.php/dav"
        
        self._client: Optional[caldav.DAVClient] = None
        self._principal: Optional[caldav.Principal] = None
        self._calendars: dict[str, CalendarInfo] = {}
    
    def connect(self) -> bool:
        """
        Establish connection to the CalDAV server.
        
        Returns:
            True if connection successful, False otherwise.
        """
        try:
            self._client = caldav.DAVClient(
                url=self.caldav_url,
                username=self.username,
                password=self.password
            )
            self._principal = self._client.principal()
            return True
        except Exception as e:
            print(f"Failed to connect to CalDAV server: {e}")
            return False
    
    def get_calendars(self) -> list[CalendarInfo]:
        """
        Get list of all calendars for this account.
        
        Returns:
            List of CalendarInfo objects.
        """
        if self._principal is None:
            if not self.connect():
                return []
        
        calendars = []
        for cal in self._principal.calendars():
            # Try to get calendar properties
            try:
                name = cal.name or "Unnamed"
            except:
                name = "Unnamed"
            
            # Try to get color from calendar properties
            color = "#4285f4"  # Default blue
            try:
                # Nextcloud stores color in a custom property
                props = cal.get_properties([caldav.elements.ical.CalendarColor()])
                if props:
                    for prop in props.values():
                        if prop and isinstance(prop, str):
                            color = prop.strip()
                            break
            except:
                pass
            
            cal_id = str(cal.url).split('/')[-2] if str(cal.url).endswith('/') else str(cal.url).split('/')[-1]
            
            cal_info = CalendarInfo(
                id=cal_id,
                name=name,
                color=color,
                url=str(cal.url),
                account_name=self.account_name,
                writable=True,
                _caldav_calendar=cal
            )
            calendars.append(cal_info)
            self._calendars[cal_id] = cal_info
        
        return calendars
    
    def get_events(
        self,
        calendar: CalendarInfo,
        start: datetime,
        end: datetime,
        expand_recurring: bool = True
    ) -> list[EventData]:
        """
        Get events from a calendar within a time range.
        
        Args:
            calendar: The calendar to query
            start: Start of time range
            end: End of time range
            expand_recurring: If True, expand recurring events into individual instances
        
        Returns:
            List of EventData objects.
        """
        if calendar._caldav_calendar is None:
            return []
        
        events = []
        
        try:
            # Ensure timezone awareness
            if start.tzinfo is None:
                start = pytz.UTC.localize(start)
            if end.tzinfo is None:
                end = pytz.UTC.localize(end)
            
            # Fetch events from CalDAV
            caldav_events = calendar._caldav_calendar.date_search(
                start=start,
                end=end,
                expand=expand_recurring
            )
            
            for caldav_event in caldav_events:
                try:
                    event_data = self._parse_caldav_event(caldav_event, calendar)
                    if event_data:
                        events.append(event_data)
                except Exception as e:
                    print(f"Error parsing event: {e}")
                    continue
        
        except Exception as e:
            print(f"Error fetching events: {e}")
        
        return events
    
    def _parse_caldav_event(
        self,
        caldav_event: caldav.Event,
        calendar: CalendarInfo
    ) -> Optional[EventData]:
        """Parse a caldav.Event into our EventData format."""
        try:
            ical = ICalendar.from_ical(caldav_event.data)
            
            for component in ical.walk():
                if component.name == "VEVENT":
                    # Get basic properties
                    uid = str(component.get('UID', ''))
                    summary = str(component.get('SUMMARY', 'Untitled'))
                    description = str(component.get('DESCRIPTION', ''))
                    location = str(component.get('LOCATION', ''))
                    
                    # Get start and end times
                    dtstart = component.get('DTSTART')
                    dtend = component.get('DTEND')
                    
                    if dtstart is None:
                        continue
                    
                    start = dtstart.dt
                    all_day = not isinstance(start, datetime)
                    
                    if all_day:
                        # Convert date to datetime for consistency
                        start = datetime.combine(start, datetime.min.time())
                        start = pytz.UTC.localize(start)
                        if dtend:
                            end = datetime.combine(dtend.dt, datetime.min.time())
                            end = pytz.UTC.localize(end)
                        else:
                            end = start + timedelta(days=1)
                    else:
                        if start.tzinfo is None:
                            start = pytz.UTC.localize(start)
                        if dtend:
                            end = dtend.dt
                            if end.tzinfo is None:
                                end = pytz.UTC.localize(end)
                        else:
                            # Default to 1 hour duration
                            end = start + timedelta(hours=1)
                    
                    # Parse recurrence rule if present
                    recurrence = None
                    rrule = component.get('RRULE')
                    if rrule:
                        recurrence = RecurrenceRule.from_rrule(rrule)
                    
                    # Check for recurrence-id (specific instance)
                    recurrence_id = None
                    recur_id_prop = component.get('RECURRENCE-ID')
                    if recur_id_prop:
                        recurrence_id = recur_id_prop.dt
                        if isinstance(recurrence_id, datetime) and recurrence_id.tzinfo is None:
                            recurrence_id = pytz.UTC.localize(recurrence_id)
                    
                    return EventData(
                        uid=uid,
                        summary=summary,
                        start=start,
                        end=end,
                        description=description,
                        location=location,
                        all_day=all_day,
                        recurrence=recurrence,
                        recurrence_id=recurrence_id,
                        calendar_id=calendar.id,
                        calendar_name=calendar.name,
                        calendar_color=calendar.color,
                        source_type="caldav",
                        read_only=not calendar.writable,
                        _caldav_event=caldav_event,
                        _raw_ical=caldav_event.data
                    )
            
            return None
        
        except Exception as e:
            print(f"Error parsing iCal data: {e}")
            return None
    
    def create_event(self, calendar: CalendarInfo, event: EventData) -> Optional[EventData]:
        """
        Create a new event in the specified calendar.
        
        Args:
            calendar: Target calendar
            event: Event data to create
        
        Returns:
            The created EventData with server-assigned properties, or None on failure.
        """
        if calendar._caldav_calendar is None or not calendar.writable:
            return None
        
        try:
            # Generate UID if not present
            if not event.uid:
                event.uid = str(uuid.uuid4())
            
            # Build iCalendar event
            ical = ICalendar()
            ical.add('prodid', '-//Kubux Calendar//kubux.net//')
            ical.add('version', '2.0')
            
            vevent = ICalEvent()
            vevent.add('uid', event.uid)
            vevent.add('summary', event.summary)
            vevent.add('description', event.description)
            vevent.add('location', event.location)
            vevent.add('dtstamp', datetime.now(pytz.UTC))
            
            if event.all_day:
                vevent.add('dtstart', event.start.date())
                vevent.add('dtend', event.end.date())
            else:
                vevent.add('dtstart', event.start)
                vevent.add('dtend', event.end)
            
            if event.recurrence:
                vevent.add('rrule', event.recurrence.to_rrule_dict())
            
            ical.add_component(vevent)
            
            # Create event on server
            caldav_event = calendar._caldav_calendar.save_event(ical.to_ical().decode('utf-8'))
            
            # Update event with server response
            event._caldav_event = caldav_event
            event._raw_ical = caldav_event.data
            event.calendar_id = calendar.id
            event.calendar_name = calendar.name
            event.calendar_color = calendar.color
            
            return event
        
        except Exception as e:
            print(f"Error creating event: {e}")
            return None
    
    def update_event(self, event: EventData) -> bool:
        """
        Update an existing event.
        
        Args:
            event: Event with updated data
        
        Returns:
            True if successful, False otherwise.
        """
        if event._caldav_event is None or event.read_only:
            return False
        
        try:
            # Parse existing iCal data
            ical = ICalendar.from_ical(event._caldav_event.data)
            
            # Find and update the VEVENT component
            for component in ical.walk():
                if component.name == "VEVENT":
                    # Update properties
                    component['SUMMARY'] = event.summary
                    component['DESCRIPTION'] = event.description
                    component['LOCATION'] = event.location
                    
                    # Update times
                    del component['DTSTART']
                    del component['DTEND']
                    
                    if event.all_day:
                        component.add('dtstart', event.start.date())
                        component.add('dtend', event.end.date())
                    else:
                        component.add('dtstart', event.start)
                        component.add('dtend', event.end)
                    
                    # Update recurrence
                    if 'RRULE' in component:
                        del component['RRULE']
                    if event.recurrence:
                        component.add('rrule', event.recurrence.to_rrule_dict())
                    
                    # Update last modified
                    if 'LAST-MODIFIED' in component:
                        del component['LAST-MODIFIED']
                    component.add('last-modified', datetime.now(pytz.UTC))
                    
                    break
            
            # Save to server
            event._caldav_event.data = ical.to_ical().decode('utf-8')
            event._caldav_event.save()
            event._raw_ical = event._caldav_event.data
            
            return True
        
        except Exception as e:
            print(f"Error updating event: {e}")
            return False
    
    def delete_event(self, event: EventData) -> bool:
        """
        Delete an event.
        
        Args:
            event: Event to delete
        
        Returns:
            True if successful, False otherwise.
        """
        if event._caldav_event is None or event.read_only:
            return False
        
        try:
            event._caldav_event.delete()
            return True
        except Exception as e:
            print(f"Error deleting event: {e}")
            return False
    
    def delete_recurring_instance(self, event: EventData, instance_start: datetime) -> bool:
        """
        Delete a specific instance of a recurring event by adding an EXDATE.
        
        Args:
            event: The recurring event
            instance_start: The start time of the instance to exclude
        
        Returns:
            True if successful, False otherwise.
        """
        if event._caldav_event is None or event.read_only or not event.is_recurring:
            return False
        
        try:
            ical = ICalendar.from_ical(event._caldav_event.data)
            
            for component in ical.walk():
                if component.name == "VEVENT":
                    # Add EXDATE to exclude this instance
                    component.add('exdate', instance_start)
                    break
            
            event._caldav_event.data = ical.to_ical().decode('utf-8')
            event._caldav_event.save()
            event._raw_ical = event._caldav_event.data
            
            return True
        
        except Exception as e:
            print(f"Error excluding recurring instance: {e}")
            return False
