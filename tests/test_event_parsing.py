"""Tests for backend/event.py — ImmutableEvent creation, parsing, and mutation."""

from datetime import datetime, timedelta
import pytz
from backend.event import (
    ImmutableEvent, EventView, CalendarSource, RecurrenceRule,
    _parse_vevent, _rebuild_ical, _extract_vevent,
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


def test_parse_vevent_with_byday():
    """RecurrenceRule with BYDAY."""
    ical = (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "PRODID:-//Test//\r\n"
        "BEGIN:VEVENT\r\n"
        "DTSTART:20260101T100000Z\r\n"
        "DTEND:20260101T110000Z\r\n"
        "SUMMARY:Weekly MWF\r\n"
        "UID:recur-byday\r\n"
        "RRULE:FREQ=WEEKLY;BYDAY=MO,WE,FR\r\n"
        "END:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    )
    result = _parse_vevent(ical)
    assert result["is_recurring"] is True
    assert result["recurrence"] is not None
    assert result["recurrence"].frequency == "WEEKLY"
    assert result["recurrence"].by_day == ["MO", "WE", "FR"]


def test_parse_vevent_with_until():
    """RecurrenceRule with UNTIL."""
    ical = (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "PRODID:-//Test//\r\n"
        "BEGIN:VEVENT\r\n"
        "DTSTART:20260101T100000Z\r\n"
        "DTEND:20260101T110000Z\r\n"
        "SUMMARY:Until Date\r\n"
        "UID:recur-until\r\n"
        "RRULE:FREQ=DAILY;UNTIL=20260201T100000Z\r\n"
        "END:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    )
    result = _parse_vevent(ical)
    assert result["is_recurring"] is True
    assert result["recurrence"].until is not None


def test_parse_vevent_float_time():
    """Floating (timezone-naive) times use config_tz."""
    ical = (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "PRODID:-//Test//\r\n"
        "BEGIN:VEVENT\r\n"
        "DTSTART:20260101T100000\r\n"
        "DTEND:20260101T110000\r\n"
        "SUMMARY:Floating\r\n"
        "UID:float-1\r\n"
        "END:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    )
    result = _parse_vevent(ical, config_tz=pytz.timezone("Europe/Berlin"))
    assert result["start"].tzinfo is not None
    assert result["tzid"] is None  # floating — no TZID


# ----------------------------------------------------------------------
# _extract_vevent
# ----------------------------------------------------------------------

def test_extract_vevent_success():
    vevent = _extract_vevent(BASIC_ICAL)
    assert vevent is not None
    assert vevent.name == "VEVENT"


def test_extract_vevent_garbage():
    assert _extract_vevent("garbage") is None


def test_extract_vevent_empty():
    ical = (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "END:VCALENDAR\r\n"
    )
    assert _extract_vevent(ical) is None


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


def test_from_ical_with_caldav_href():
    ev = ImmutableEvent.from_ical(BASIC_ICAL, "src1", caldav_href="/cal/test.ics")
    assert ev.caldav_href == "/cal/test.ics"


def test_from_ical_with_sync_state():
    ev = ImmutableEvent.from_ical(BASIC_ICAL, "src1", sync_state="pending_create")
    assert ev.sync_state == "pending_create"


# ----------------------------------------------------------------------
# ImmutableEvent properties
# ----------------------------------------------------------------------

def test_immutable_event_properties():
    ev = ImmutableEvent.from_ical(BASIC_ICAL, "src1", config_tz=UTC)
    assert ev.master_start == ev.start
    assert ev.master_end == ev.end
    assert ev.duration == timedelta(hours=1)
    assert ev.tzid == "UTC"
    assert ev.start_utc.tzinfo == UTC
    assert ev.end_utc.tzinfo == UTC


def test_immutable_event_location_empty():
    ev = ImmutableEvent.from_ical(BASIC_ICAL, "src1")
    assert ev.location == ""


def test_immutable_event_description_empty():
    ev = ImmutableEvent.from_ical(BASIC_ICAL, "src1")
    assert ev.description == ""


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


def test_create_new_recurrence_by_day():
    rrule = RecurrenceRule(frequency="WEEKLY", by_day=["MO", "WE"])
    start = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
    end = datetime(2026, 1, 1, 11, 0, 0, tzinfo=UTC)
    ev = ImmutableEvent.create_new("src1", "ByDay", start, end, recurrence=rrule)
    assert ev.is_recurring is True
    assert ev.recurrence.by_day == ["MO", "WE"]


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


def test_with_updates_start_end():
    ev = ImmutableEvent.from_ical(BASIC_ICAL, "src1")
    new_start = datetime(2026, 2, 1, 10, 0, 0, tzinfo=UTC)
    new_end = datetime(2026, 2, 1, 12, 0, 0, tzinfo=UTC)
    ev2 = ev.with_updates(start=new_start, end=new_end)
    assert ev2.start == new_start
    assert ev2.end == new_end
    assert ev2.duration == timedelta(hours=2)


def test_with_updates_location():
    ev = ImmutableEvent.from_ical(BASIC_ICAL, "src1")
    ev2 = ev.with_updates(location="New Location")
    assert ev2.location == "New Location"


def test_with_updates_description():
    ev = ImmutableEvent.from_ical(BASIC_ICAL, "src1")
    ev2 = ev.with_updates(description="New Description")
    assert ev2.description == "New Description"


def test_with_updates_source_id():
    ev = ImmutableEvent.from_ical(BASIC_ICAL, "src1")
    ev2 = ev.with_updates(source_id="src2")
    assert ev2.source_id == "src2"


def test_with_updates_caldav_href():
    ev = ImmutableEvent.from_ical(BASIC_ICAL, "src1")
    ev2 = ev.with_updates(caldav_href="/new/href.ics")
    assert ev2.caldav_href == "/new/href.ics"


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


def test_immutable_event_neq():
    ical2 = BASIC_ICAL.replace("UID:test-uid-001", "UID:test-uid-002")
    ev1 = ImmutableEvent.from_ical(BASIC_ICAL, "src1")
    ev2 = ImmutableEvent.from_ical(ical2, "src1")
    assert ev1 != ev2


# ----------------------------------------------------------------------
# CalendarSource
# ----------------------------------------------------------------------

def test_calendar_source_hash():
    src1 = CalendarSource(id="cal1", name="Cal1")
    src2 = CalendarSource(id="cal1", name="Cal2")  # same id
    assert hash(src1) == hash(src2)
    assert src1 == src2


def test_calendar_source_neq():
    src1 = CalendarSource(id="cal1", name="Cal1")
    src2 = CalendarSource(id="cal2", name="Cal2")
    assert src1 != src2


def test_calendar_source_not_equal_to_non_source():
    src = CalendarSource(id="cal1", name="Cal1")
    assert src.__eq__("not-a-source") is False


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


def test_event_view_start_end_setters():
    ev = ImmutableEvent.from_ical(BASIC_ICAL, "src1")
    src = CalendarSource(id="src1", name="Cal")
    view = EventView(ev, src)
    new_start = datetime(2026, 2, 1, 10, 0, 0, tzinfo=UTC)
    new_end = datetime(2026, 2, 1, 12, 0, 0, tzinfo=UTC)
    view.start = new_start
    view.end = new_end
    assert view.start == new_start
    assert view.end == new_end


def test_event_view_all_day_setter():
    """all_day=True flush requires start/end date values to rebuild ical correctly."""
    ev = ImmutableEvent.from_ical(BASIC_ICAL, "src1")
    src = CalendarSource(id="src1", name="Cal")
    view = EventView(ev, src)
    view.all_day = True
    view.start = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
    view.end = datetime(2026, 1, 2, 0, 0, 0, tzinfo=UTC)
    assert view.all_day is True
    new_ev = view.flush_updates()
    assert new_ev.all_day is True


def test_event_view_recurrence_setter():
    ev = ImmutableEvent.from_ical(BASIC_ICAL, "src1")
    src = CalendarSource(id="src1", name="Cal")
    view = EventView(ev, src)
    rrule = RecurrenceRule(frequency="DAILY")
    view.recurrence = rrule
    assert view.recurrence is rrule
    # flush should produce a recurring event
    new_ev = view.flush_updates()
    assert new_ev.is_recurring is True


def test_event_view_source_type():
    ev = ImmutableEvent.from_ical(BASIC_ICAL, "src1")
    src = CalendarSource(id="src1", name="Cal", source_type="ics")
    view = EventView(ev, src)
    assert view.source_type == "ics"


def test_event_view_sync_status_pending():
    ev = ImmutableEvent.from_ical(BASIC_ICAL, "src1", sync_state="pending_create")
    src = CalendarSource(id="src1", name="Cal")
    view = EventView(ev, src)
    assert view.sync_status == "pending"


def test_event_view_pending_operation():
    ev = ImmutableEvent.from_ical(BASIC_ICAL, "src1", sync_state="pending_create")
    src = CalendarSource(id="src1", name="Cal")
    view = EventView(ev, src)
    assert view.pending_operation == "create"


def test_event_view_pending_operation_setter():
    ev = ImmutableEvent.from_ical(BASIC_ICAL, "src1")
    src = CalendarSource(id="src1", name="Cal")
    view = EventView(ev, src)
    view.pending_operation = "update"
    assert view.pending_operation == "update"


def test_event_view_event_compat():
    """event property returns self for backward compatibility."""
    ev = ImmutableEvent.from_ical(BASIC_ICAL, "src1")
    src = CalendarSource(id="src1", name="Cal")
    view = EventView(ev, src)
    assert view.event is view


def test_event_view_immutable_event():
    ev = ImmutableEvent.from_ical(BASIC_ICAL, "src1")
    src = CalendarSource(id="src1", name="Cal")
    view = EventView(ev, src)
    assert view.immutable_event is ev


def test_event_view_eq():
    ev = ImmutableEvent.from_ical(BASIC_ICAL, "src1")
    src = CalendarSource(id="src1", name="Cal")
    view1 = EventView(ev, src)
    view2 = EventView(ev, src)
    assert view1 == view2


def test_event_view_neq():
    ev1 = ImmutableEvent.from_ical(BASIC_ICAL, "src1")
    ev2 = ImmutableEvent.from_ical(BASIC_ICAL.replace("UID:test-uid-001", "UID:test-uid-999"), "src1")
    src = CalendarSource(id="src1", name="Cal")
    view1 = EventView(ev1, src)
    view2 = EventView(ev2, src)
    assert view1 != view2


def test_event_view_duration():
    ev = ImmutableEvent.from_ical(BASIC_ICAL, "src1")
    src = CalendarSource(id="src1", name="Cal")
    view = EventView(ev, src)
    assert view.duration == timedelta(hours=1)


def test_event_view_is_recurring():
    ev = ImmutableEvent.from_ical(BASIC_ICAL, "src1")
    src = CalendarSource(id="src1", name="Cal")
    view = EventView(ev, src)
    assert view.is_recurring is False


def test_event_view_read_only_source():
    ev = ImmutableEvent.from_ical(BASIC_ICAL, "src1")
    src = CalendarSource(id="src1", name="Cal", read_only=True)
    view = EventView(ev, src)
    assert view.read_only is True


def test_event_view_flush_without_content_changes():
    """Flush with only metadata (pending_operation) should preserve ical."""
    ev = ImmutableEvent.from_ical(BASIC_ICAL, "src1")
    src = CalendarSource(id="src1", name="Cal")
    view = EventView(ev, src)
    original_ical = view._event.ical_data
    view.pending_operation = "delete"
    new_ev = view.flush_updates()
    assert new_ev.sync_state == "pending_update"
    assert new_ev.ical_data == original_ical  # no content changed


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


def test_rebuild_ical_update_location():
    new = _rebuild_ical(BASIC_ICAL, location="Conference Room")
    ev = ImmutableEvent.from_ical(new, "src1")
    assert ev.location == "Conference Room"


def test_rebuild_ical_update_description():
    new = _rebuild_ical(BASIC_ICAL, description="Detailed description")
    ev = ImmutableEvent.from_ical(new, "src1")
    assert ev.description == "Detailed description"


def test_rebuild_ical_raises_on_no_vevent():
    ical = (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "END:VCALENDAR\r\n"
    )
    try:
        _rebuild_ical(ical, summary="Test")
        assert False, "should have raised"
    except ValueError:
        pass


def test_rebuild_ical_preserves_tzid():
    """Caller passes TZ-aware values — _rebuild_ical writes TZID parameter."""
    ical = (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "PRODID:-//Test//\r\n"
        "BEGIN:VEVENT\r\n"
        "DTSTART;TZID=America/New_York:20260101T100000\r\n"
        "DTEND;TZID=America/New_York:20260101T110000\r\n"
        "SUMMARY:TZID Event\r\n"
        "UID:tzid-test\r\n"
        "END:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    )
    tz = pytz.timezone("America/New_York")
    new_start = tz.localize(datetime(2026, 1, 1, 12, 0, 0))
    new_end = tz.localize(datetime(2026, 1, 1, 13, 0, 0))
    new_ical = _rebuild_ical(ical, start=new_start, end=new_end)
    # Verify raw ical output
    assert "DTSTART;TZID=America/New_York:20260101T120000" in new_ical
    assert "DTEND;TZID=America/New_York:20260101T130000" in new_ical


def test_rebuild_ical_preserves_floating():
    """Caller passes naive values — _rebuild_ical writes floating (no TZID, no Z)."""
    ical = (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "PRODID:-//Test//\r\n"
        "BEGIN:VEVENT\r\n"
        "DTSTART:20260101T100000\r\n"
        "DTEND:20260101T110000\r\n"
        "SUMMARY:Floating Event\r\n"
        "UID:float-test\r\n"
        "END:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    )
    new_start = datetime(2026, 1, 1, 12, 0, 0)
    new_end = datetime(2026, 1, 1, 13, 0, 0)
    new_ical = _rebuild_ical(ical, start=new_start, end=new_end)
    # Verify raw ical output: no Z suffix, no TZID parameter
    assert "DTSTART:20260101T120000\r\n" in new_ical
    assert "DTEND:20260101T130000\r\n" in new_ical
    assert "TZID=" not in new_ical

# ----------------------------------------------------------------------
# Regression: floating times via ImmutableEvent must use config_tz
# ----------------------------------------------------------------------

FLOATING_ICAL = (
    "BEGIN:VCALENDAR\r\n"
    "VERSION:2.0\r\n"
    "PRODID:-//Test//\r\n"
    "BEGIN:VEVENT\r\n"
    "DTSTART:20260101T100000\r\n"
    "DTEND:20260101T110000\r\n"
    "SUMMARY:Floating\r\n"
    "UID:float-1\r\n"
    "END:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)


def test_from_ical_floating_time_uses_config_tz():
    """Floating (naive) times must be localized to config_tz, not UTC.

    Regression: __post_init__ used to call _parse_vevent without
    _config_tz, silently interpreting floating events as UTC.
    """
    berlin = pytz.timezone("Europe/Berlin")
    ev = ImmutableEvent.from_ical(FLOATING_ICAL, "src1", config_tz=berlin)
    assert ev.master_start.tzinfo is not None
    assert ev.master_start.hour == 10  # wall-clock preserved
    assert ev.master_start.utcoffset() == timedelta(hours=1)  # January → CET
    assert ev.master_start.astimezone(UTC).hour == 9


def test_from_ical_floating_time_defaults_to_utc_without_config_tz():
    ev = ImmutableEvent.from_ical(FLOATING_ICAL, "src1")
    assert ev.master_start.utcoffset() == timedelta(0)
