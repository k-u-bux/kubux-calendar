"""Tests for backend/event.py — ImmutableEvent creation, parsing, and mutation."""

from datetime import datetime, timedelta
import pytz
from backend.event import (
    ImmutableEvent, EventView, CalendarSource, RecurrenceRule,
    _parse_vevent, _rebuild_ical,
)

UTC = pytz.UTC

# ----------------------------------------------------------------------
# _parse_vevent — pure iCalendar parsing
# ----------------------------------------------------------------------

BASIC_ICAL = (
    "BEGIN:VCALENDAR\r\n"
    "VERSION:2.0\r\n"
    "PRODID:-//Test//\r\n"
    "BEGIN:VEVENT\r\n"
    "DTSTART:20260101T100000Z\r\n"
    "DTEND:20260101T110000Z\r\n"
    "SUMMARY:Test Event\r\n"
    "UID:test-uid-001\r\n"
    "END:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)


def test_parse_vevent_basic():
    result = _parse_vevent(BASIC_ICAL, config_tz=UTC)
    assert result["summary"] == "Test Event"
    assert result["description"] == ""
    assert result["location"] == ""
    assert result["all_day"] is False
    assert result["is_recurring"] is False
    assert result["start"] == datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
    assert result["end"] == datetime(2026, 1, 1, 11, 0, 0, tzinfo=UTC)
    assert result["tzid"] == "UTC"


def test_parse_vevent_missing_fields():
    ical = (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "PRODID:-//Test//\r\n"
        "BEGIN:VEVENT\r\n"
        "DTSTART:20260101T120000Z\r\n"
        "UID:minimal\r\n"
        "END:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    )
    result = _parse_vevent(ical)
    assert result["summary"] == "Untitled"
    assert result["description"] == ""
    assert result["location"] == ""


def test_parse_vevent_all_day():
    ical = (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "PRODID:-//Test//\r\n"
        "BEGIN:VEVENT\r\n"
        "DTSTART;VALUE=DATE:20260101\r\n"
        "DTEND;VALUE=DATE:20260102\r\n"
        "SUMMARY:All Day\r\n"
        "UID:allday\r\n"
        "END:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    )
    result = _parse_vevent(ical)
    assert result["all_day"] is True
    assert isinstance(result["start"], datetime)
    assert result["start"].year == 2026


def test_parse_vevent_recurring():
    ical = (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "PRODID:-//Test//\r\n"
        "BEGIN:VEVENT\r\n"
        "DTSTART:20260101T100000Z\r\n"
        "DTEND:20260101T110000Z\r\n"
        "SUMMARY:Recurring\r\n"
        "UID:recur\r\n"
        "RRULE:FREQ=WEEKLY;INTERVAL=2;COUNT=5\r\n"
        "END:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    )
    result = _parse_vevent(ical)
    assert result["is_recurring"] is True
    assert result["recurrence"] is not None
    assert result["recurrence"].frequency == "WEEKLY"
    assert result["recurrence"].interval == 2
    assert result["recurrence"].count == 5


def test_parse_vevent_garbage_input():
    result = _parse_vevent("not valid icalendar at all")
    assert result["summary"] == "Untitled"
    assert isinstance(result["start"], datetime)


# ----------------------------------------------------------------------
# ImmutableEvent.from_ical
# ----------------------------------------------------------------------


def test_from_ical():
    ev = ImmutableEvent.from_ical(BASIC_ICAL, "src1", config_tz=UTC)
    assert ev.uid == "test-uid-001"
    assert ev.source_id == "src1"
    assert ev.summary == "Test Event"
    assert ev.start == datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
    assert ev.end == datetime(2026, 1, 1, 11, 0, 0, tzinfo=UTC)
    assert ev.all_day is False
    assert ev.is_recurring is False
    assert ev.sync_state == "clean"


def test_from_ical_generates_uid_when_missing():
    ical = (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "PRODID:-//Test//\r\n"
        "BEGIN:VEVENT\r\n"
        "DTSTART:20260101T100000Z\r\n"
        "SUMMARY:No UID\r\n"
        "END:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    )
    ev = ImmutableEvent.from_ical(ical, "src1")
    assert ev.uid != ""
    assert len(ev.uid) > 0


# ----------------------------------------------------------------------
# ImmutableEvent.create_new
# ----------------------------------------------------------------------


def test_create_new_basic():
    start = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
    end = datetime(2026, 1, 1, 11, 0, 0, tzinfo=UTC)
    ev = ImmutableEvent.create_new("src1", "New", start, end, description="desc", location="loc")
    assert ev.summary == "New"
    assert ev.description == "desc"
    assert ev.location == "loc"
    assert ev.start == start
    assert ev.end == end
    assert ev.sync_state == "pending_create"
    assert len(ev.uid) > 0


def test_create_new_all_day():
    start = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
    end = datetime(2026, 1, 2, 0, 0, 0, tzinfo=UTC)
    ev = ImmutableEvent.create_new("src1", "All Day", start, end, all_day=True)
    assert ev.all_day is True


def test_create_new_with_recurrence():
    rrule = RecurrenceRule(frequency="WEEKLY", interval=2, count=10)
    start = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
    end = datetime(2026, 1, 1, 11, 0, 0, tzinfo=UTC)
    ev = ImmutableEvent.create_new("src1", "Recur", start, end, recurrence=rrule)
    assert ev.is_recurring is True
    assert ev.recurrence.frequency == "WEEKLY"
    assert ev.recurrence.count == 10


# ----------------------------------------------------------------------
# ImmutableEvent.with_updates
# ----------------------------------------------------------------------


def test_with_updates_summary():
    ev = ImmutableEvent.from_ical(BASIC_ICAL, "src1")
    ev2 = ev.with_updates(summary="Updated")
    assert ev2.summary == "Updated"
    assert ev2.start == ev.start
    assert ev.summary == "Test Event"  # original unchanged


def test_with_updates_sync_state():
    ev = ImmutableEvent.from_ical(BASIC_ICAL, "src1")
    ev2 = ev.with_updates(sync_state="pending_update")
    assert ev2.sync_state == "pending_update"


def test_with_updates_noop():
    ev = ImmutableEvent.from_ical(BASIC_ICAL, "src1")
    ev2 = ev.with_updates()
    assert ev2.uid == ev.uid
    assert ev2.ical_data == ev.ical_data


# ----------------------------------------------------------------------
# ImmutableEvent.as_instance
# ----------------------------------------------------------------------


def test_as_instance():
    ev = ImmutableEvent.from_ical(BASIC_ICAL, "src1")
    inst_start = datetime(2026, 2, 1, 10, 0, 0, tzinfo=UTC)
    inst = ev.as_instance(inst_start)
    assert inst.uid == ev.uid
    assert inst.start == inst_start
    assert inst.end == inst_start + ev.duration
    assert inst.master_start == ev.start  # master unchanged


# ----------------------------------------------------------------------
# ImmutableEvent identity
# ----------------------------------------------------------------------


def test_immutable_event_hash_eq():
    ev1 = ImmutableEvent.from_ical(BASIC_ICAL, "src1")
    ev2 = ImmutableEvent.from_ical(BASIC_ICAL, "src2")  # same UID, different source
    assert ev1 == ev2
    assert hash(ev1) == hash(ev2)


# ----------------------------------------------------------------------
# EventView
# ----------------------------------------------------------------------


def test_event_view_basic():
    ev = ImmutableEvent.from_ical(BASIC_ICAL, "src1")
    src = CalendarSource(id="src1", name="Test Calendar", color="#ff0000")
    view = EventView(ev, src)
    assert view.summary == "Test Event"
    assert view.calendar_color == "#ff0000"
    assert view.calendar_name == "Test Calendar"
    assert view.read_only is False
    assert view.sync_status == ""


def test_event_view_setters():
    ev = ImmutableEvent.from_ical(BASIC_ICAL, "src1")
    src = CalendarSource(id="src1", name="Cal")
    view = EventView(ev, src)
    view.summary = "Modified"
    view.description = "New desc"
    view.location = "New loc"
    assert view.summary == "Modified"
    assert view.description == "New desc"
    assert view.location == "New loc"


def test_event_view_flush_updates():
    ev = ImmutableEvent.from_ical(BASIC_ICAL, "src1")
    src = CalendarSource(id="src1", name="Cal")
    view = EventView(ev, src)
    view.summary = "After Flush"
    new_ev = view.flush_updates()
    assert new_ev.summary == "After Flush"
    assert new_ev.sync_state == "pending_update"
    # view is reset
    assert view.summary == "After Flush"


def test_event_view_flush_no_changes():
    ev = ImmutableEvent.from_ical(BASIC_ICAL, "src1")
    src = CalendarSource(id="src1", name="Cal")
    view = EventView(ev, src)
    new_ev = view.flush_updates()
    assert new_ev is ev  # same object, no changes


# ----------------------------------------------------------------------
# _rebuild_ical
# ----------------------------------------------------------------------


def test_rebuild_ical_update_summary():
    new = _rebuild_ical(BASIC_ICAL, summary="Rebuilt")
    ev = ImmutableEvent.from_ical(new, "src1")
    assert ev.summary == "Rebuilt"
    assert ev.start == datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)


def test_rebuild_ical_add_recurrence():
    rrule = RecurrenceRule(frequency="DAILY", interval=1)
    new = _rebuild_ical(BASIC_ICAL, recurrence=rrule)
    ev = ImmutableEvent.from_ical(new, "src1")
    assert ev.is_recurring is True
    assert ev.recurrence.frequency == "DAILY"


def test_rebuild_ical_remove_recurrence():
    ical_with_rrule = (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "PRODID:-//Test//\r\n"
        "BEGIN:VEVENT\r\n"
        "DTSTART:20260101T100000Z\r\n"
        "DTEND:20260101T110000Z\r\n"
        "SUMMARY:Recurring\r\n"
        "UID:recur\r\n"
        "RRULE:FREQ=WEEKLY\r\n"
        "END:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    )
    new = _rebuild_ical(ical_with_rrule, recurrence=None)
    ev = ImmutableEvent.from_ical(new, "src1")
    assert ev.is_recurring is False