"""Tests for backend/event_index.py."""

from datetime import datetime, timedelta
import pytz
from backend.event_index import EventIndex
from backend.event import ImmutableEvent

UTC = pytz.UTC


def _make_event(uid: str, source_id: str, start_h: int, end_h: int, recurring: bool = False) -> ImmutableEvent:
    """Create a minimal ImmutableEvent for testing."""
    start = datetime(2026, 1, 1, start_h, 0, 0, tzinfo=UTC)
    end = datetime(2026, 1, 1, end_h, 0, 0, tzinfo=UTC)
    ical = (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "PRODID:-//Test//\r\n"
        "BEGIN:VEVENT\r\n"
        f"DTSTART:{start.strftime('%Y%m%dT%H%M%SZ')}\r\n"
        f"DTEND:{end.strftime('%Y%m%dT%H%M%SZ')}\r\n"
        f"SUMMARY:E{uid}\r\n"
        f"UID:{uid}\r\n"
        + (f"RRULE:FREQ=WEEKLY\r\n" if recurring else "") +
        "END:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    )
    return ImmutableEvent.from_ical(ical, source_id, config_tz=UTC)


def test_add_and_query_range():
    idx = EventIndex()
    ev1 = _make_event("u1", "src1", 10, 12)
    ev2 = _make_event("u2", "src1", 14, 16)
    idx.add(ev1)
    idx.add(ev2)

    start = datetime(2026, 1, 1, 9, 0, 0, tzinfo=UTC)
    end = datetime(2026, 1, 1, 18, 0, 0, tzinfo=UTC)
    results = idx.query_range(start, end)
    assert len(results) == 2
    uids = {e.uid for e in results}
    assert uids == {"u1", "u2"}


def test_query_range_partial():
    idx = EventIndex()
    idx.add(_make_event("u1", "src1", 10, 12))
    idx.add(_make_event("u2", "src1", 14, 16))

    start = datetime(2026, 1, 1, 11, 0, 0, tzinfo=UTC)
    end = datetime(2026, 1, 1, 13, 0, 0, tzinfo=UTC)
    results = idx.query_range(start, end)
    assert len(results) == 1
    assert results[0].uid == "u1"


def test_query_range_empty():
    idx = EventIndex()
    start = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
    end = datetime(2026, 1, 1, 23, 0, 0, tzinfo=UTC)
    assert idx.query_range(start, end) == []


def test_recurring_always_included():
    idx = EventIndex()
    # recurring event with master interval far in the past
    ev = _make_event("u-recur", "src1", 0, 1, recurring=True)
    idx.add(ev)

    # query a range far from the master interval
    start = datetime(2026, 6, 1, 0, 0, 0, tzinfo=UTC)
    end = datetime(2026, 6, 1, 23, 0, 0, tzinfo=UTC)
    results = idx.query_range(start, end)
    assert len(results) == 1
    assert results[0].uid == "u-recur"


def test_remove():
    idx = EventIndex()
    ev = _make_event("u1", "src1", 10, 12)
    idx.add(ev)
    assert len(idx) == 1
    idx.remove("u1")
    assert len(idx) == 0
    assert "u1" not in idx


def test_remove_nonexistent():
    idx = EventIndex()
    idx.remove("nonexistent")  # no-op


def test_clear():
    idx = EventIndex()
    idx.add(_make_event("u1", "src1", 10, 12))
    idx.add(_make_event("u2", "src1", 14, 16))
    idx.clear()
    assert len(idx) == 0


def test_add_replaces_duplicate_uid():
    idx = EventIndex()
    ev1 = _make_event("u1", "src1", 10, 12)
    ev2 = _make_event("u1", "src1", 14, 16)  # same UID, different time
    idx.add(ev1)
    idx.add(ev2)
    assert len(idx) == 1
    start = datetime(2026, 1, 1, 14, 0, 0, tzinfo=UTC)
    end = datetime(2026, 1, 1, 15, 0, 0, tzinfo=UTC)
    results = idx.query_range(start, end)
    assert len(results) == 1


def test_copy():
    idx = EventIndex()
    ev = _make_event("u1", "src1", 10, 12)
    idx.add(ev)
    idx2 = idx.copy()
    assert len(idx2) == 1
    # modify original, copy unaffected
    idx.remove("u1")
    assert len(idx) == 0
    assert len(idx2) == 1


def test_query_point():
    idx = EventIndex()
    idx.add(_make_event("u1", "src1", 10, 12))
    idx.add(_make_event("u2", "src1", 14, 16))

    results = idx.query_point(datetime(2026, 1, 1, 11, 0, 0, tzinfo=UTC))
    assert len(results) == 1
    assert results[0].uid == "u1"

    results = idx.query_point(datetime(2026, 1, 1, 13, 0, 0, tzinfo=UTC))
    assert len(results) == 0


def test_get():
    idx = EventIndex()
    ev = _make_event("u1", "src1", 10, 12)
    idx.add(ev)
    assert idx.get("u1") is not None
    assert idx.get("u1").uid == "u1"
    assert idx.get("nonexistent") is None


def test_contains():
    idx = EventIndex()
    ev = _make_event("u1", "src1", 10, 12)
    idx.add(ev)
    assert "u1" in idx
    assert "u2" not in idx