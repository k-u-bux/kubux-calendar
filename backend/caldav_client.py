"""
CalDAV client for Nextcloud calendar operations.

Provides CRUD operations for calendar events via CalDAV protocol.
Returns raw VCALENDAR data. EventRepository handles recurrence expansion
using recurring_ical_events.
"""

import caldav
from caldav.elements import dav, cdav
from datetime import datetime, timedelta
from typing import Optional
from dataclasses import dataclass, field
import pytz
from icalendar import Calendar as ICalendar, Event as ICalEvent
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
    
    def reconnect(self) -> bool:
        """
        Force a fresh reconnection to the CalDAV server.
        
        Clears all cached data and establishes a new connection.
        
        Returns:
            True if reconnection successful, False otherwise.
        """
        self._client = None
        self._principal = None
        self._calendars = {}
        return self.connect()
    
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
            try:
                name = cal.name or "Unnamed"
            except:
                name = "Unnamed"
            
            color = "#4285f4"  # Default blue
            try:
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
    
    def get_calendar_by_id(self, calendar_id: str) -> Optional[CalendarInfo]:
        """Get a calendar by its ID."""
        if calendar_id in self._calendars:
            return self._calendars[calendar_id]
        # Try to refresh calendars list
        self.get_calendars()
        return self._calendars.get(calendar_id)
    
    def get_calendar_ical(
        self,
        calendar: CalendarInfo,
        start: datetime,
        end: datetime
    ) -> Optional[str]:
        """
        Fetch raw VCALENDAR data from a calendar (no recurrence expansion).
        
        For use with EventRepository which handles recurrence expansion
        using recurring_ical_events library.
        
        Args:
            calendar: The calendar to query
            start: Start of time range
            end: End of time range
        
        Returns:
            Raw VCALENDAR text containing all events in range, or None on error.
        """
        if calendar._caldav_calendar is None:
            return None
        
        try:
            if start.tzinfo is None:
                start = pytz.UTC.localize(start)
            if end.tzinfo is None:
                end = pytz.UTC.localize(end)
            
            caldav_events = calendar._caldav_calendar.date_search(
                start=start,
                end=end,
                expand=False  # No expansion - let recurring_ical_events handle it
            )
            
            combined_cal = ICalendar()
            combined_cal.add('prodid', '-//Kubux Calendar//kubux.net//')
            combined_cal.add('version', '2.0')
            
            for caldav_event in caldav_events:
                try:
                    ical = ICalendar.from_ical(caldav_event.data)
                    for component in ical.walk():
                        if component.name == 'VEVENT':
                            combined_cal.add_component(component)
                except Exception as e:
                    print(f"Error parsing CalDAV event: {e}")
                    continue
            
            return combined_cal.to_ical().decode('utf-8')
        
        except Exception as e:
            print(f"Error fetching calendar ICAL: {e}")
            return None
    
    def get_raw_event_ical(self, calendar: CalendarInfo, uid: str) -> Optional[str]:
        """
        Get raw VCALENDAR data for a specific event.
        
        Args:
            calendar: The calendar containing the event
            uid: The event UID
        
        Returns:
            Raw VCALENDAR text, or None if not found.
        """
        if calendar._caldav_calendar is None:
            return None
        
        try:
            caldav_event = calendar._caldav_calendar.event_by_uid(uid)
            if caldav_event:
                return caldav_event.data
        except Exception:
            pass
        
        return None
    
    def save_event(self, calendar: CalendarInfo, event: ICalEvent) -> bool:
        """
        Save an icalendar.Event to a calendar.
        
        Args:
            calendar: Target calendar
            event: The icalendar.Event to save
        
        Returns:
            True if successful, False otherwise.
        """
        if calendar._caldav_calendar is None or not calendar.writable:
            return False
        
        try:
            # Ensure UID
            if not event.get('UID'):
                event.add('uid', str(uuid.uuid4()))
            
            # Build VCALENDAR wrapper
            ical = ICalendar()
            ical.add('prodid', '-//Kubux Calendar//kubux.net//')
            ical.add('version', '2.0')
            ical.add_component(event)
            
            calendar._caldav_calendar.save_event(ical.to_ical().decode('utf-8'))
            return True
        except Exception as e:
            print(f"Error saving event: {e}")
            return False
    
    def save_raw_event(self, calendar: CalendarInfo, ical_text: str) -> bool:
        """
        Save raw VCALENDAR data to a calendar.
        
        Args:
            calendar: Target calendar
            ical_text: Raw VCALENDAR text to save
        
        Returns:
            True if successful, False otherwise.
        """
        if calendar._caldav_calendar is None or not calendar.writable:
            return False
        
        try:
            calendar._caldav_calendar.save_event(ical_text)
            return True
        except Exception as e:
            print(f"Error saving raw event: {e}")
            return False
    
    def update_event(self, calendar: CalendarInfo, uid: str, event: ICalEvent) -> bool:
        """
        Update an existing event.
        
        Args:
            calendar: The calendar containing the event
            uid: The event UID
            event: The updated icalendar.Event
        
        Returns:
            True if successful, False otherwise.
        """
        if calendar._caldav_calendar is None or not calendar.writable:
            return False
        
        try:
            caldav_event = calendar._caldav_calendar.event_by_uid(uid)
            if not caldav_event:
                return False
            
            # Build new VCALENDAR
            ical = ICalendar()
            ical.add('prodid', '-//Kubux Calendar//kubux.net//')
            ical.add('version', '2.0')
            ical.add_component(event)
            
            caldav_event.data = ical.to_ical().decode('utf-8')
            caldav_event.save()
            return True
        except Exception as e:
            print(f"Error updating event: {e}")
            return False
    
    def delete_event(self, calendar: CalendarInfo, uid: str) -> bool:
        """
        Delete an event by its UID.
        
        Args:
            calendar: The calendar containing the event
            uid: The event UID
        
        Returns:
            True if successful, False otherwise.
        """
        if calendar._caldav_calendar is None:
            return False
        
        try:
            caldav_event = calendar._caldav_calendar.event_by_uid(uid)
            if caldav_event:
                caldav_event.delete()
                return True
        except Exception as e:
            print(f"Error deleting event: {e}")
        
        return False
    
    def add_exdate(self, calendar: CalendarInfo, uid: str, instance_start: datetime) -> bool:
        """
        Add an EXDATE to exclude a specific instance of a recurring event.
        
        Args:
            calendar: The calendar containing the event
            uid: The event UID
            instance_start: The start time of the instance to exclude
        
        Returns:
            True if successful, False otherwise.
        """
        if calendar._caldav_calendar is None:
            return False
        
        try:
            caldav_event = calendar._caldav_calendar.event_by_uid(uid)
            if not caldav_event:
                return False
            
            ical = ICalendar.from_ical(caldav_event.data)
            
            for component in ical.walk():
                if component.name == "VEVENT":
                    component.add('exdate', instance_start)
                    break
            
            caldav_event.data = ical.to_ical().decode('utf-8')
            caldav_event.save()
            return True
        except Exception as e:
            print(f"Error adding EXDATE: {e}")
            return False
