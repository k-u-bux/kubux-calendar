"""
Immutable event types for Kubux Calendar v2.

Core types:
- ImmutableEvent: Frozen dataclass wrapping raw iCalendar data
- CalendarSource: Calendar metadata (mutable for UI state)
- EventView: Read-write display adapter for GUI
- RecurrenceRule: Simple recurrence spec for UI
"""

from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from typing import Optional
import pytz
from icalendar import Calendar as ICalCalendar, Event as ICalEvent
import uuid as _uuid
from library.log import debug_log, Level
from library.timezone_utils import ensure_tz, get_local_timezone


# Shared sync window constants (used by EventStore and SyncManager).
# These define how far back/forward events are fetched from the server.
SYNC_WINDOW_PAST_DAYS = 120
SYNC_WINDOW_FUTURE_DAYS = 240


# ==================== Recurrence Rule ====================

@dataclass
class RecurrenceRule:
    """Simple recurrence rule for UI exchange."""
    frequency: str  # DAILY, WEEKLY, MONTHLY, YEARLY
    interval: int = 1
    count: Optional[int] = None
    until: Optional[datetime] = None
    by_day: Optional[list[str]] = None


# ==================== Calendar Source ====================

@dataclass
class CalendarSource:
    """
    Calendar source metadata.  Mutable — UI toggles visibility/color.
    """
    id: str
    name: str
    color: str = ""
    account_name: str = ""
    read_only: bool = False
    source_type: str = "caldav"   # "caldav" or "ics"
    visible: bool = True
    last_sync_time: Optional[datetime] = None
    is_outdated: bool = False
    is_orphaned: bool = False
    outdate_threshold: int = 28800

    def __hash__(self):
        return hash(self.id)

    def __eq__(self, other):
        if isinstance(other, CalendarSource):
            return self.id == other.id
        return False


# ==================== Internal Parsing ====================

def _extract_vevent(ical_data: str) -> Optional[ICalEvent]:
    """Extract first VEVENT component from iCalendar text."""
    try:
        cal = ICalCalendar.from_ical(ical_data)
        for component in cal.walk():
            if component.name == "VEVENT":
                return component
    except Exception as e:
        debug_log(Level.DEBUG, f"_find_vevent: ical parse failed — {e}")
    return None




def _parse_vevent(ical_data: str, config_tz: Optional[pytz.BaseTzInfo] = None) -> dict:
    """
    Parse a VEVENT into a flat dict of display-ready values.

    *config_tz* is used to interpret floating (timezone-naive) times.
    Non-floating times are returned as-is with their original timezone.
    Floating times are localized to *config_tz* (or UTC if not given).
    The original *ical_data* is never modified — this conversion is only
    for in-memory display / comparison, not for sync.
    """
    fallback = config_tz or pytz.UTC

    vevent = _extract_vevent(ical_data)
    if vevent is None:
        now = datetime.now(pytz.UTC)
        return dict(
            summary="Untitled", description="", location="",
            start=now, end=now + timedelta(hours=1),
            all_day=False, is_recurring=False, recurrence=None,
            duration=timedelta(hours=1),
        )

    summary = str(vevent.get("SUMMARY") or "Untitled")
    description = str(vevent.get("DESCRIPTION") or "")
    location = str(vevent.get("LOCATION") or "")

    # --- times -----------------------------------------------------------
    dtstart_prop = vevent.get("DTSTART")
    all_day = (
        dtstart_prop is not None
        and isinstance(dtstart_prop.dt, date)
        and not isinstance(dtstart_prop.dt, datetime)
    )

    # Extract TZID from DTSTART params (None if floating, "UTC" if Z suffix)
    tzid = None
    if dtstart_prop is not None and hasattr(dtstart_prop, 'params'):
        params = dtstart_prop.params
        if "TZID" in params:
            tzid = str(params["TZID"])
        else:
            dt = dtstart_prop.dt
            if isinstance(dt, datetime) and dt.tzinfo is not None:
                offset = dt.tzinfo.utcoffset(dt)
                if offset is not None and offset.total_seconds() == 0:
                    tzid = "UTC"

    start = ensure_tz(dtstart_prop.dt, default=fallback) if dtstart_prop else datetime.now(pytz.UTC)

    dtend_prop = vevent.get("DTEND")
    end = ensure_tz(dtend_prop.dt, default=fallback) if dtend_prop else start + timedelta(hours=1)

    duration = end - start

    # --- recurrence -------------------------------------------------------
    rrule = vevent.get("RRULE")
    is_recurring = rrule is not None
    recurrence = None
    if rrule:
        freq = rrule.get("FREQ", [None])[0]
        if freq:
            byday = rrule.get("BYDAY", None)
            by_day_list = None
            if byday:
                by_day_list = (
                    [str(d) for d in byday] if isinstance(byday, list) else [str(byday)]
                )
            recurrence = RecurrenceRule(
                frequency=str(freq),
                interval=int(rrule.get("INTERVAL", [1])[0] or 1),
                count=int(c) if (c := rrule.get("COUNT", [None])[0]) else None,
                until=rrule.get("UNTIL", [None])[0],
                by_day=by_day_list,
            )

    return dict(
        summary=summary, description=description, location=location,
        start=start, end=end, all_day=all_day, tzid=tzid,
        is_recurring=is_recurring, recurrence=recurrence,
        duration=duration,
    )


def _wrap_as_vcalendar(vevent: ICalEvent) -> str:
    """Wrap a VEVENT in a minimal VCALENDAR."""
    cal = ICalCalendar()
    cal.add("prodid", "-//Kubux Calendar//kubux.net//")
    cal.add("version", "2.0")
    cal.add_component(vevent)
    return cal.to_ical().decode("utf-8")


def _rebuild_ical(ical_data: str, **updates) -> str:
    """Return new iCalendar text with *updates* applied to the VEVENT."""
    vevent = _extract_vevent(ical_data)
    if vevent is None:
        raise ValueError("No VEVENT in iCalendar data")

    target_all_day = updates.get("all_day", None)
    if target_all_day is None:
        prop = vevent.get("DTSTART")
        target_all_day = (
            prop is not None
            and isinstance(prop.dt, date)
            and not isinstance(prop.dt, datetime)
        )

    _simple = {"summary": "SUMMARY", "description": "DESCRIPTION", "location": "LOCATION"}
    for key, ical_key in _simple.items():
        if key in updates:
            if ical_key in vevent:
                del vevent[ical_key]
            if updates[key]:
                vevent.add(ical_key.lower(), updates[key])

    for key, ical_key in (("start", "DTSTART"), ("end", "DTEND")):
        if key in updates:
            if ical_key in vevent:
                del vevent[ical_key]
            val = updates[key]
            if target_all_day and isinstance(val, datetime):
                val = val.date()
            elif isinstance(val, datetime) and val.tzinfo is not None:
                tzname = getattr(val.tzinfo, 'zone', None)
                if tzname and tzname != "UTC":
                    vevent.add(ical_key.lower(), val.replace(tzinfo=None),
                               parameters={"TZID": tzname})
                    continue
            vevent.add(ical_key.lower(), val)

    if "recurrence" in updates:
        if "RRULE" in vevent:
            del vevent["RRULE"]
        rule = updates["recurrence"]
        if rule is not None:
            rd = {"freq": rule.frequency}
            if rule.interval and rule.interval > 1:
                rd["interval"] = rule.interval
            if rule.count:
                rd["count"] = rule.count
            if rule.until:
                rd["until"] = rule.until
            if rule.by_day:
                rd["byday"] = rule.by_day
            vevent.add("rrule", rd)

    if "LAST-MODIFIED" in vevent:
        del vevent["LAST-MODIFIED"]
    vevent.add("last-modified", datetime.now(pytz.UTC))

    return _wrap_as_vcalendar(vevent)


# ==================== ImmutableEvent ====================

@dataclass(frozen=True)
class ImmutableEvent:
    """
    Frozen event wrapping raw iCalendar data.

    Content properties are eagerly parsed from *ical_data*.
    Updates produce new instances via :meth:`with_updates`.
    """
    uid: str
    source_id: str
    ical_data: str
    sync_state: str = "clean"
    caldav_href: Optional[str] = None

    # Timestamp of last server confirmation (file mtime when written).
    # None = never confirmed (e.g. newly created, pending sync).
    confirmed_at: Optional[datetime] = None

    # Override times for recurring-event instances (ephemeral, not persisted).
    _instance_start: Optional[datetime] = field(
        default=None, repr=False, compare=False, hash=False
    )
    _instance_end: Optional[datetime] = field(
        default=None, repr=False, compare=False, hash=False
    )

    # Eagerly-parsed cache — set in __post_init__ via object.__setattr__.
    _cache: dict = field(
        default_factory=dict, init=False, repr=False, compare=False, hash=False
    )

    # Fallback timezone for floating times.
    _config_tz: Optional[pytz.BaseTzInfo] = field(
        default=None, repr=False, compare=False, hash=False
    )

    def __post_init__(self):
        object.__setattr__(
            self, "_cache", _parse_vevent(self.ical_data, self._config_tz)
        )


    def is_outdated(self, threshold: int) -> bool:
        """Return True if this event's cache is older than *threshold* seconds."""
        if self.confirmed_at is None:
            return False
        elapsed = (datetime.now() - self.confirmed_at).total_seconds()
        return elapsed > threshold

    # --- content properties ------------------------------------------------

    @property
    def summary(self) -> str:
        return self._cache["summary"]

    @property
    def description(self) -> str:
        return self._cache["description"]

    @property
    def location(self) -> str:
        return self._cache["location"]

    @property
    def all_day(self) -> bool:
        return self._cache["all_day"]

    @property
    def is_recurring(self) -> bool:
        return self._cache["is_recurring"]

    @property
    def recurrence(self) -> Optional[RecurrenceRule]:
        return self._cache["recurrence"]

    @property
    def duration(self) -> timedelta:
        return self._cache["duration"]

    @property
    def tzid(self) -> Optional[str]:
        """Original TZID from DTSTART, or None if floating, or 'UTC'."""
        return self._cache.get("tzid")

    # --- time properties ---------------------------------------------------

    @property
    def start(self) -> datetime:
        """Instance start (or master start for non-recurring)."""
        return self._instance_start if self._instance_start is not None else self.master_start

    @property
    def end(self) -> datetime:
        """Instance end (or master end for non-recurring)."""
        return self._instance_end if self._instance_end is not None else self.master_end

    @property
    def master_start(self) -> datetime:
        return self._cache["start"]

    @property
    def master_end(self) -> datetime:
        return self._cache["end"]

    @property
    def start_utc(self) -> datetime:
        return self.start.astimezone(pytz.UTC)

    @property
    def end_utc(self) -> datetime:
        return self.end.astimezone(pytz.UTC)

    # --- factories ---------------------------------------------------------

    @classmethod
    def from_ical(
        cls,
        ical_text: str,
        source_id: str,
        config_tz: Optional[pytz.BaseTzInfo] = None,
        caldav_href: Optional[str] = None,
        sync_state: str = "clean",
        confirmed_at: Optional[datetime] = None,
    ) -> "ImmutableEvent":
        """
        Parse raw iCalendar text into an ImmutableEvent.

        Timezone rules:
        - UTC events → keep as UTC
        - TZID events → keep original TZID
        - Floating events → interpreted in *config_tz* for display/comparison

        The original *ical_text* is stored verbatim — floating times are
        NEVER rewritten. They are only localized when accessed via
        ``start``/``end`` properties.
        """
        vevent = _extract_vevent(ical_text)
        uid = str(vevent.get("UID") or "") if vevent else ""
        if not uid:
            uid = str(_uuid.uuid4())

        ev = cls(
            uid=uid,
            source_id=source_id,
            ical_data=ical_text,
            sync_state=sync_state,
            caldav_href=caldav_href,
            confirmed_at=confirmed_at,
            _config_tz=config_tz,
        )
        return ev

    @classmethod
    def create_new(
        cls,
        source_id: str,
        summary: str,
        start: datetime,
        end: datetime,
        description: str = "",
        location: str = "",
        all_day: bool = False,
        recurrence: Optional[RecurrenceRule] = None,
        config_tz: Optional[pytz.BaseTzInfo] = None,
    ) -> "ImmutableEvent":
        """Create a brand-new event (pending_create)."""
        uid = str(_uuid.uuid4())
        vevent = ICalEvent()
        vevent.add("uid", uid)
        vevent.add("summary", summary)
        vevent.add("dtstamp", datetime.now(pytz.UTC))

        if description:
            vevent.add("description", description)
        if location:
            vevent.add("location", location)

        if all_day:
            vevent.add("dtstart", start.date() if isinstance(start, datetime) else start)
            vevent.add("dtend", end.date() if isinstance(end, datetime) else end)
        else:
            vevent.add("dtstart", start)
            vevent.add("dtend", end)

        if recurrence:
            rd = {"freq": recurrence.frequency}
            if recurrence.interval and recurrence.interval > 1:
                rd["interval"] = recurrence.interval
            if recurrence.count:
                rd["count"] = recurrence.count
            if recurrence.until:
                rd["until"] = recurrence.until
            if recurrence.by_day:
                rd["byday"] = recurrence.by_day
            vevent.add("rrule", rd)

        ev = cls(
            uid=uid,
            source_id=source_id,
            ical_data=_wrap_as_vcalendar(vevent),
            sync_state="pending_create",
            _config_tz=config_tz,
        )
        return ev

    # --- copy-on-write -----------------------------------------------------

    def with_updates(self, **kwargs) -> "ImmutableEvent":
        """
        Return a new event with fields replaced.

        Content keys (summary, start, end, description, location, all_day,
        recurrence) rebuild the iCalendar data.  Metadata keys (sync_state,
        caldav_href, source_id) update the outer shell only.
        """
        meta_keys = {"sync_state", "caldav_href", "source_id",
                      "_instance_start", "_instance_end"}
        content = {k: v for k, v in kwargs.items() if k not in meta_keys}

        new_ical = _rebuild_ical(self.ical_data, **content) if content else self.ical_data

        new = ImmutableEvent(
            uid=self.uid,
            source_id=kwargs.get("source_id", self.source_id),
            ical_data=new_ical,
            sync_state=kwargs.get("sync_state", self.sync_state),
            caldav_href=kwargs.get("caldav_href", self.caldav_href),
            confirmed_at=kwargs.get("confirmed_at", self.confirmed_at),
            _instance_start=kwargs.get("_instance_start", self._instance_start),
            _instance_end=kwargs.get("_instance_end", self._instance_end),
            _config_tz=self._config_tz,
        )
        return new

    def as_instance(self, instance_start: datetime) -> "ImmutableEvent":
        """Ephemeral view for a specific recurring-event occurrence."""
        new = ImmutableEvent(
            uid=self.uid,
            source_id=self.source_id,
            ical_data=self.ical_data,
            sync_state=self.sync_state,
            caldav_href=self.caldav_href,
            _instance_start=ensure_tz(instance_start),
            _instance_end=ensure_tz(instance_start) + self.duration,
            _config_tz=self._config_tz,
        )
        return new

    # --- identity ----------------------------------------------------------

    def __hash__(self):
        return hash(self.uid)

    def __eq__(self, other):
        if isinstance(other, ImmutableEvent):
            return self.uid == other.uid
        return False

    def __repr__(self):
        return f"ImmutableEvent(uid={self.uid!r}, summary={self.summary!r})"


# ==================== EventView ====================

class EventView:
    """
    Read-write display adapter returned by ``EventStore.get_events()``.

    Provides every property that the GUI widgets access, drawing from
    an underlying :class:`ImmutableEvent` and :class:`CalendarSource`.

    Supports mutation: ``ev.summary = "new title"`` stores the change
    in ``_dirty``.  Call :meth:`flush_updates` to produce a new
    :class:`ImmutableEvent` with all dirty fields applied.
    """
    __slots__ = ("_event", "_source", "_dirty")

    def __init__(self, event: ImmutableEvent, source: CalendarSource):
        self._event = event
        self._source = source
        self._dirty = {}

    # --- content properties (read from event, fall back to dirty) ----------

    @property
    def uid(self) -> str:
        return self._event.uid

    @property
    def summary(self) -> str:
        return self._dirty.get("summary", self._event.summary)

    @summary.setter
    def summary(self, value: str):
        self._dirty["summary"] = value

    @property
    def start(self) -> datetime:
        return self._dirty.get("start", self._event.start)

    @start.setter
    def start(self, value: datetime):
        self._dirty["start"] = value

    @property
    def end(self) -> datetime:
        return self._dirty.get("end", self._event.end)

    @end.setter
    def end(self, value: datetime):
        self._dirty["end"] = value

    @property
    def description(self) -> str:
        return self._dirty.get("description", self._event.description)

    @description.setter
    def description(self, value: str):
        self._dirty["description"] = value

    @property
    def location(self) -> str:
        return self._dirty.get("location", self._event.location)

    @location.setter
    def location(self, value: str):
        self._dirty["location"] = value

    @property
    def all_day(self) -> bool:
        return self._dirty.get("all_day", self._event.all_day)

    @all_day.setter
    def all_day(self, value: bool):
        self._dirty["all_day"] = value

    @property
    def recurrence(self) -> Optional[RecurrenceRule]:
        return self._dirty.get("recurrence", self._event.recurrence)

    @recurrence.setter
    def recurrence(self, value: Optional[RecurrenceRule]):
        self._dirty["recurrence"] = value

    @property
    def duration(self) -> timedelta:
        return self._event.duration

    @property
    def is_recurring(self) -> bool:
        return self._event.is_recurring

    # --- source metadata ---------------------------------------------------

    @property
    def calendar_color(self) -> str:
        return self._source.color

    @property
    def calendar_name(self) -> str:
        return self._source.name

    @property
    def read_only(self) -> bool:
        return self._source.read_only

    @property
    def source(self) -> CalendarSource:
        return self._source

    @property
    def source_type(self) -> str:
        return self._source.source_type

    # --- sync status -------------------------------------------------------

    @property
    def sync_status(self) -> str:
        return "pending" if self._event.sync_state != "clean" else ""

    @property
    def is_outdated(self) -> bool:
        """Per-event staleness based on file mtime vs config threshold."""
        return self._event.is_outdated(self._source.outdate_threshold)

    @property
    def pending_operation(self):
        _map = {
            "pending_create": "create",
            "pending_update": "update",
            "pending_delete": "delete",
            "pending_delete_instance": "delete_instance",
        }
        return self._dirty.get("pending_operation", _map.get(self._event.sync_state))

    @pending_operation.setter
    def pending_operation(self, value):
        self._dirty["pending_operation"] = value

    # --- GUI compatibility -------------------------------------------------

    @property
    def event(self) -> "EventView":
        """The old GUI does ``event_data.event.pending_operation``."""
        return self

    @property
    def immutable_event(self) -> ImmutableEvent:
        """Access the underlying ImmutableEvent for store operations."""
        return self._event

    # --- mutation support --------------------------------------------------

    def flush_updates(self) -> ImmutableEvent:
        """
        Produce a new :class:`ImmutableEvent` with all dirty fields applied.

        Clears the dirty state and returns the fresh immutable.
        """
        if not self._dirty:
            return self._event
        # Separate metadata from content
        content_keys = {"summary", "start", "end", "description",
                        "location", "all_day", "recurrence"}
        content = {k: v for k, v in self._dirty.items() if k in content_keys}
        meta = {k: v for k, v in self._dirty.items() if k not in content_keys}

        # Rebuild ical if content changed
        if content:
            new_ical = _rebuild_ical(self._event.ical_data, **content)
        else:
            new_ical = self._event.ical_data

        sync_state = meta.pop("sync_state", "pending_update")

        result = ImmutableEvent(
            uid=self._event.uid,
            source_id=self._event.source_id,
            ical_data=new_ical,
            sync_state=sync_state,
            caldav_href=self._event.caldav_href,
            _config_tz=self._event._config_tz,
        )
        # Reset dirty
        self._dirty = {}
        self._event = result
        return result

    def _set_pending_sync_state(self, op: str):
        """Mark this event view with a pending sync state."""
        state_map = {
            "create": "pending_create",
            "update": "pending_update",
            "delete": "pending_delete",
            "delete_instance": "pending_delete_instance",
        }
        new_state = state_map.get(op, "clean")
        orig = self._event
        new = ImmutableEvent(
            uid=orig.uid, source_id=orig.source_id, ical_data=orig.ical_data,
            sync_state=new_state, caldav_href=orig.caldav_href,
            _config_tz=orig._config_tz,
        )
        self._event = new

    # --- dunder ------------------------------------------------------------

    def __eq__(self, other):
        if isinstance(other, EventView):
            return self._event.uid == other._event.uid and self.start == other.start
        return False

    def __repr__(self):
        return f"EventView({self.summary!r}, {self.start})"
