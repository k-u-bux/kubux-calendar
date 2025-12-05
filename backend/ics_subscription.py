"""
ICS Subscription handler for read-only calendar feeds.

Fetches and parses ICS files from URLs.
"""

import requests
from datetime import datetime, timedelta, date
from typing import Optional
from dataclasses import dataclass, field
import pytz
from ics import Calendar as ICSCalendar
from ics.grammar.parse import ContentLine

from .caldav_client import EventData, RecurrenceRule


@dataclass
class SubscriptionInfo:
    """Information about an ICS subscription."""
    id: str
    name: str
    url: str
    color: str = "#34a853"
    last_fetch: Optional[datetime] = None
    error: Optional[str] = None


class ICSSubscription:
    """Handler for ICS calendar subscriptions."""
    
    def __init__(self, name: str, url: str, color: str = "#34a853"):
        """
        Initialize an ICS subscription.
        
        Args:
            name: Display name for the subscription
            url: URL to fetch the ICS file from
            color: Color to display events (hex format)
        """
        self.name = name
        self.url = url
        self.color = color
        self.id = self._generate_id(url)
        
        self._calendar: Optional[ICSCalendar] = None
        self._last_fetch: Optional[datetime] = None
        self._error: Optional[str] = None
        self._raw_data: Optional[str] = None
    
    @staticmethod
    def _generate_id(url: str) -> str:
        """Generate a unique ID from the URL."""
        import hashlib
        return hashlib.md5(url.encode()).hexdigest()[:12]
    
    def fetch(self, timeout: int = 30) -> bool:
        """
        Fetch the ICS file from the URL.
        
        Args:
            timeout: Request timeout in seconds
        
        Returns:
            True if successful, False otherwise.
        """
        try:
            response = requests.get(
                self.url,
                timeout=timeout,
                headers={
                    'User-Agent': 'Kubux-Calendar/1.0',
                    'Accept': 'text/calendar'
                }
            )
            response.raise_for_status()
            
            self._raw_data = response.text
            self._calendar = ICSCalendar(self._raw_data)
            self._last_fetch = datetime.now(pytz.UTC)
            self._error = None
            
            return True
        
        except requests.RequestException as e:
            self._error = f"Network error: {e}"
            return False
        except Exception as e:
            self._error = f"Parse error: {e}"
            return False
    
    def get_events(
        self,
        start: datetime,
        end: datetime,
        force_fetch: bool = False,
        cache_seconds: int = 300
    ) -> list[EventData]:
        """
        Get events from the ICS feed within a time range.
        
        Args:
            start: Start of time range
            end: End of time range
            force_fetch: If True, always fetch from URL
            cache_seconds: How long to use cached data (default 5 minutes)
        
        Returns:
            List of EventData objects (all read-only).
        """
        # Check if we need to fetch
        should_fetch = (
            force_fetch or
            self._calendar is None or
            self._last_fetch is None or
            (datetime.now(pytz.UTC) - self._last_fetch).total_seconds() > cache_seconds
        )
        
        if should_fetch:
            self.fetch()
        
        if self._calendar is None:
            return []
        
        # Ensure timezone awareness
        if start.tzinfo is None:
            start = pytz.UTC.localize(start)
        if end.tzinfo is None:
            end = pytz.UTC.localize(end)
        
        events = []
        
        for ics_event in self._calendar.events:
            try:
                event_data = self._parse_ics_event(ics_event, start, end)
                if event_data:
                    # For recurring events, we might get multiple instances
                    if isinstance(event_data, list):
                        events.extend(event_data)
                    else:
                        events.append(event_data)
            except Exception as e:
                print(f"Error parsing ICS event: {e}")
                continue
        
        return events
    
    def _parse_ics_event(
        self,
        ics_event,
        query_start: datetime,
        query_end: datetime
    ) -> Optional[EventData | list[EventData]]:
        """Parse an ics.Event into our EventData format."""
        try:
            # Get basic properties
            uid = ics_event.uid or ""
            summary = ics_event.name or "Untitled"
            description = ics_event.description or ""
            location = ics_event.location or ""
            
            # Get start and end times
            event_start = ics_event.begin
            event_end = ics_event.end
            
            if event_start is None:
                return None
            
            # Convert Arrow objects to datetime
            start_dt = event_start.datetime
            if event_end:
                end_dt = event_end.datetime
            else:
                end_dt = start_dt + timedelta(hours=1)
            
            # Check if all-day event
            all_day = ics_event.all_day if hasattr(ics_event, 'all_day') else False
            
            # Ensure timezone awareness
            if start_dt.tzinfo is None:
                start_dt = pytz.UTC.localize(start_dt)
            if end_dt.tzinfo is None:
                end_dt = pytz.UTC.localize(end_dt)
            
            # Check for recurrence
            # The ics library handles RRULE expansion differently
            # We'll need to handle recurring events
            
            # Check if event falls within our query range
            # For non-recurring events, simple bounds check
            if not self._has_recurrence(ics_event):
                if end_dt < query_start or start_dt > query_end:
                    return None
                
                return EventData(
                    uid=uid,
                    summary=summary,
                    start=start_dt,
                    end=end_dt,
                    description=description,
                    location=location,
                    all_day=all_day,
                    calendar_id=self.id,
                    calendar_name=self.name,
                    calendar_color=self.color,
                    source_type="ics",
                    read_only=True,
                    _raw_ical=str(ics_event)
                )
            else:
                # Handle recurring events
                return self._expand_recurring_event(
                    ics_event, uid, summary, description, location,
                    all_day, start_dt, end_dt,
                    query_start, query_end
                )
        
        except Exception as e:
            print(f"Error parsing ICS event data: {e}")
            return None
    
    def _has_recurrence(self, ics_event) -> bool:
        """Check if an event has recurrence rules."""
        try:
            # Access the underlying icalendar component
            for line in ics_event.extra:
                if isinstance(line, ContentLine) and line.name == 'RRULE':
                    return True
            return False
        except:
            return False
    
    def _expand_recurring_event(
        self,
        ics_event,
        uid: str,
        summary: str,
        description: str,
        location: str,
        all_day: bool,
        original_start: datetime,
        original_end: datetime,
        query_start: datetime,
        query_end: datetime
    ) -> list[EventData]:
        """
        Expand a recurring event into instances within the query range.
        
        This is a simplified implementation. For complex RRULE patterns,
        consider using the dateutil.rrule module for more accurate expansion.
        """
        events = []
        duration = original_end - original_start
        
        # Try to parse RRULE
        rrule_str = None
        for line in ics_event.extra:
            if isinstance(line, ContentLine) and line.name == 'RRULE':
                rrule_str = line.value
                break
        
        if not rrule_str:
            return events
        
        # Parse simple recurrence patterns
        try:
            from dateutil.rrule import rrulestr
            
            # Build the rrule
            rule = rrulestr(f"RRULE:{rrule_str}", dtstart=original_start)
            
            # Get instances within range
            # Limit to 1000 instances to prevent infinite loops
            instances = rule.between(query_start, query_end, inc=True)[:1000]
            
            for instance_start in instances:
                if instance_start.tzinfo is None:
                    instance_start = pytz.UTC.localize(instance_start)
                
                instance_end = instance_start + duration
                
                event = EventData(
                    uid=f"{uid}_{instance_start.isoformat()}",
                    summary=summary,
                    start=instance_start,
                    end=instance_end,
                    description=description,
                    location=location,
                    all_day=all_day,
                    recurrence=self._parse_rrule_string(rrule_str),
                    recurrence_id=instance_start,
                    calendar_id=self.id,
                    calendar_name=self.name,
                    calendar_color=self.color,
                    source_type="ics",
                    read_only=True
                )
                events.append(event)
        
        except ImportError:
            # dateutil not available, fall back to simple pattern
            # Just return the original event
            event = EventData(
                uid=uid,
                summary=summary,
                start=original_start,
                end=original_end,
                description=description,
                location=location,
                all_day=all_day,
                calendar_id=self.id,
                calendar_name=self.name,
                calendar_color=self.color,
                source_type="ics",
                read_only=True
            )
            if original_end >= query_start and original_start <= query_end:
                events.append(event)
        
        except Exception as e:
            print(f"Error expanding recurring event: {e}")
        
        return events
    
    def _parse_rrule_string(self, rrule_str: str) -> Optional[RecurrenceRule]:
        """Parse an RRULE string into a RecurrenceRule object."""
        try:
            parts = dict(p.split('=') for p in rrule_str.split(';') if '=' in p)
            
            freq = parts.get('FREQ', 'DAILY')
            interval = int(parts.get('INTERVAL', '1'))
            count = int(parts['COUNT']) if 'COUNT' in parts else None
            
            until = None
            if 'UNTIL' in parts:
                until_str = parts['UNTIL']
                try:
                    until = datetime.strptime(until_str, '%Y%m%dT%H%M%SZ')
                    until = pytz.UTC.localize(until)
                except:
                    try:
                        until = datetime.strptime(until_str, '%Y%m%d')
                        until = pytz.UTC.localize(until)
                    except:
                        pass
            
            by_day = parts.get('BYDAY', '').split(',') if 'BYDAY' in parts else None
            by_month_day = [int(d) for d in parts.get('BYMONTHDAY', '').split(',') if d] if 'BYMONTHDAY' in parts else None
            by_month = [int(m) for m in parts.get('BYMONTH', '').split(',') if m] if 'BYMONTH' in parts else None
            
            return RecurrenceRule(
                frequency=freq,
                interval=interval,
                count=count,
                until=until,
                by_day=by_day if by_day and by_day[0] else None,
                by_month_day=by_month_day if by_month_day else None,
                by_month=by_month if by_month else None
            )
        except Exception as e:
            print(f"Error parsing RRULE: {e}")
            return None
    
    def get_info(self) -> SubscriptionInfo:
        """Get information about this subscription."""
        return SubscriptionInfo(
            id=self.id,
            name=self.name,
            url=self.url,
            color=self.color,
            last_fetch=self._last_fetch,
            error=self._error
        )


class ICSSubscriptionManager:
    """Manager for multiple ICS subscriptions."""
    
    def __init__(self):
        self._subscriptions: dict[str, ICSSubscription] = {}
    
    def add_subscription(self, name: str, url: str, color: str = "#34a853") -> ICSSubscription:
        """Add a new subscription."""
        sub = ICSSubscription(name=name, url=url, color=color)
        self._subscriptions[sub.id] = sub
        return sub
    
    def remove_subscription(self, subscription_id: str) -> bool:
        """Remove a subscription."""
        if subscription_id in self._subscriptions:
            del self._subscriptions[subscription_id]
            return True
        return False
    
    def get_subscription(self, subscription_id: str) -> Optional[ICSSubscription]:
        """Get a subscription by ID."""
        return self._subscriptions.get(subscription_id)
    
    def get_all_subscriptions(self) -> list[ICSSubscription]:
        """Get all subscriptions."""
        return list(self._subscriptions.values())
    
    def fetch_all(self) -> dict[str, bool]:
        """
        Fetch all subscriptions.
        
        Returns:
            Dict mapping subscription ID to success status.
        """
        results = {}
        for sub_id, sub in self._subscriptions.items():
            results[sub_id] = sub.fetch()
        return results
    
    def get_all_events(
        self,
        start: datetime,
        end: datetime,
        force_fetch: bool = False
    ) -> list[EventData]:
        """
        Get events from all subscriptions within a time range.
        
        Args:
            start: Start of time range
            end: End of time range
            force_fetch: If True, fetch fresh data from all URLs
        
        Returns:
            List of EventData objects from all subscriptions.
        """
        all_events = []
        for sub in self._subscriptions.values():
            events = sub.get_events(start, end, force_fetch=force_fetch)
            all_events.extend(events)
        return all_events
