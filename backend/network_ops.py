"""
Pure network operations for Kubux Calendar v2.

Stateless functions for CalDAV and ICS HTTP interactions.
Every function blocks, returns data, and has no side effects
beyond the network call itself.  Designed to run inside
:func:`task_dispatch.dispatch_task`.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import pytz
import requests
import lxml.etree as etree

import caldav
from caldav.elements import ical as caldav_ical
from icalendar import Calendar as ICalCalendar, Event as ICalEvent
import uuid as _uuid


# ==================== Data Types ====================

@dataclass
class CalendarInfo:
    """Information about a remote CalDAV calendar."""
    id: str
    name: str
    color: str
    url: str
    account_name: str
    writable: bool = True
    # Internal — not serialised.
    _caldav_cal: Optional[caldav.Calendar] = field(default=None, repr=False)


@dataclass
class DAVSession:
    """
    Lightweight wrapper around a *caldav.DAVClient* connection.

    Passed to ``caldav_*`` functions so they don't need credentials.
    """
    client: caldav.DAVClient
    principal: caldav.Principal
    account_name: str


# ==================== CalDAV — connect ================================

def caldav_connect(url: str, username: str, password: str,
                   account_name: str = "") -> DAVSession:
    """
    Connect to a CalDAV server.

    Raises on failure (caller should catch and handle).
    """
    caldav_url = f"{url.rstrip('/')}/remote.php/dav"
    client = caldav.DAVClient(url=caldav_url, username=username, password=password)
    principal = client.principal()
    return DAVSession(client=client, principal=principal,
                      account_name=account_name or url)


# ==================== CalDAV — calendars ==============================

def _check_writable(cal: caldav.Calendar) -> bool:
    """Check write privileges via PROPFIND."""
    try:
        body = (
            '<?xml version="1.0" encoding="utf-8" ?>'
            '<D:propfind xmlns:D="DAV:">'
            '  <D:prop><D:current-user-privilege-set/></D:prop>'
            '</D:propfind>'
        )
        resp = cal.client.propfind(cal.url, props=body, depth=0)
        if resp is None:
            return True
        raw = getattr(resp, "raw", None) or getattr(resp, "text", None) or str(resp)
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="ignore")
        lower = raw.lower()
        return ("<d:write" in lower or "<write" in lower or
                "<d:bind" in lower or "<bind" in lower)
    except Exception:
        return True  # Assume writable on error


def caldav_list_calendars(session: DAVSession) -> list[CalendarInfo]:
    """Return metadata for every calendar visible to *session*."""
    result: list[CalendarInfo] = []
    for cal in session.principal.calendars():
        try:
            name = cal.name or "Unnamed"
        except Exception:
            name = "Unnamed"

        color = "#4285f4"
        try:
            props = cal.get_properties([caldav_ical.CalendarColor()])
            for v in (props or {}).values():
                if v and isinstance(v, str):
                    color = v.strip()
                    break
        except Exception:
            pass

        url_str = str(cal.url)
        cal_id = (url_str.split("/")[-2] if url_str.endswith("/")
                  else url_str.split("/")[-1])

        result.append(CalendarInfo(
            id=cal_id, name=name, color=color, url=url_str,
            account_name=session.account_name,
            writable=_check_writable(cal),
            _caldav_cal=cal,
        ))
    return result


# ==================== CalDAV — events ==================================

def caldav_fetch_events(session: DAVSession, calendar: CalendarInfo,
                        start: datetime, end: datetime) -> list[tuple[str, Optional[str]]]:
    """
    Fetch events from a CalDAV calendar.

    Returns a list of ``(raw_vcalendar_text, href)`` tuples.
    *href* is the CalDAV resource URL needed for PUT/DELETE.
    """
    cal = calendar._caldav_cal
    if cal is None:
        return []

    if start.tzinfo is None:
        start = pytz.UTC.localize(start)
    if end.tzinfo is None:
        end = pytz.UTC.localize(end)

    results: list[tuple[str, Optional[str]]] = []
    try:
        for ev in cal.date_search(start=start, end=end, expand=False):
            try:
                href = str(ev.url) if ev.url else None
                results.append((ev.data, href))
            except Exception:
                continue
    except Exception:
        pass
    return results


def caldav_save_event(session: DAVSession, calendar: CalendarInfo,
                      ical_text: str) -> bool:
    """Create or update an event on the server.  Returns success."""
    cal = calendar._caldav_cal
    if cal is None or not calendar.writable:
        return False
    try:
        cal.save_event(ical_text)
        return True
    except Exception:
        return False


def caldav_update_event(session: DAVSession, calendar: CalendarInfo,
                        uid: str, ical_event: ICalEvent) -> bool:
    """Update an existing event identified by *uid*.  Returns success."""
    cal = calendar._caldav_cal
    if cal is None or not calendar.writable:
        return False
    try:
        caldav_ev = cal.event_by_uid(uid)
        if not caldav_ev:
            return False
        vcal = ICalCalendar()
        vcal.add("prodid", "-//Kubux Calendar//kubux.net//")
        vcal.add("version", "2.0")
        vcal.add_component(ical_event)
        caldav_ev.data = vcal.to_ical().decode("utf-8")
        caldav_ev.save()
        return True
    except Exception:
        return False


def caldav_delete_event(session: DAVSession, calendar: CalendarInfo,
                        uid: str) -> bool:
    """Delete an event by UID.  Returns success."""
    cal = calendar._caldav_cal
    if cal is None:
        return False
    try:
        ev = cal.event_by_uid(uid)
        if ev:
            ev.delete()
            return True
    except Exception:
        pass
    return False


def caldav_add_exdate(session: DAVSession, calendar: CalendarInfo,
                      uid: str, instance_start: datetime) -> bool:
    """Add an EXDATE to exclude a specific recurring instance."""
    cal = calendar._caldav_cal
    if cal is None:
        return False
    try:
        ev = cal.event_by_uid(uid)
        if not ev:
            return False
        ical = ICalCalendar.from_ical(ev.data)
        for comp in ical.walk():
            if comp.name == "VEVENT":
                comp.add("exdate", instance_start)
                break
        ev.data = ical.to_ical().decode("utf-8")
        ev.save()
        return True
    except Exception:
        return False


# ==================== ICS subscriptions ================================

def ics_fetch(url: str, timeout: int = 30) -> Optional[str]:
    """
    Fetch an ICS feed.

    Returns raw VCALENDAR text, or *None* on failure.
    """
    try:
        resp = requests.get(
            url, timeout=timeout,
            headers={"User-Agent": "Kubux-Calendar/2.0",
                     "Accept": "text/calendar"},
        )
        resp.raise_for_status()
        resp.encoding = "utf-8"
        return resp.text
    except Exception:
        return None


def ics_parse_events(vcalendar_text: str) -> list[str]:
    """
    Split a VCALENDAR into individual per-event VCALENDAR texts.

    Each returned string is a standalone VCALENDAR containing exactly
    one VEVENT (suitable for :meth:`ImmutableEvent.from_ical`).
    """
    try:
        cal = ICalCalendar.from_ical(vcalendar_text)
    except Exception:
        return []

    results: list[str] = []
    for comp in cal.walk():
        if comp.name == "VEVENT":
            wrapper = ICalCalendar()
            wrapper.add("prodid", "-//Kubux Calendar//kubux.net//")
            wrapper.add("version", "2.0")
            wrapper.add_component(comp)
            results.append(wrapper.to_ical().decode("utf-8"))
    return results
