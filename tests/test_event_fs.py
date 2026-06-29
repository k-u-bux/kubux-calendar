"""Tests for backend/event_fs.py."""

from datetime import datetime
import pytz
from backend.event_fs import EventFS, SourceMeta, PendingOp, _safe_filename
from backend.event import ImmutableEvent

UTC = pytz.UTC

BASIC_ICAL = (
    "BEGIN:VCALENDAR\r\n"
    "VERSION:2.0\r\n"
    "PRODID:-//Test//\r\n"
    "BEGIN:VEVENT\r\n"
    "DTSTART:20260101T100000Z\r\n"
    "DTEND:20260101T110000Z\r\n"
    "SUMMARY:Test\r\n"
    "UID:test-uid-001\r\n"
    "END:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)


def test_safe_filename():
    encoded = _safe_filename("caldav:account:calendar")
    assert isinstance(encoded, str)
    assert len(encoded) > 0
    # round-trip
    assert bytes.fromhex(encoded).decode("utf-8") == "caldav:account:calendar"


def test_save_and_load_event(tmp_path):
    fs = EventFS(base=tmp_path)
    ev = ImmutableEvent.from_ical(BASIC_ICAL, "src1", config_tz=UTC)
    fs.save_event(ev)
    loaded = fs.load_event("src1", "test-uid-001")
    assert loaded is not None
    assert loaded.uid == "test-uid-001"
    assert loaded.summary == "Test"


def test_load_event_missing(tmp_path):
    fs = EventFS(base=tmp_path)
    assert fs.load_event("src1", "nonexistent") is None


def test_delete_event(tmp_path):
    fs = EventFS(base=tmp_path)
    ev = ImmutableEvent.from_ical(BASIC_ICAL, "src1")
    fs.save_event(ev)
    fs.delete_event("src1", "test-uid-001")
    assert fs.load_event("src1", "test-uid-001") is None


def test_list_events(tmp_path):
    fs = EventFS(base=tmp_path)
    ev1 = ImmutableEvent.from_ical(BASIC_ICAL, "src1")
    ev2 = ImmutableEvent.from_ical(
        BASIC_ICAL.replace("UID:test-uid-001", "UID:test-uid-002"), "src1"
    )
    fs.save_event(ev1)
    fs.save_event(ev2)
    events = fs.list_events("src1")
    assert len(events) == 2
    uids = {e.uid for e in events}
    assert uids == {"test-uid-001", "test-uid-002"}


def test_list_events_empty_source(tmp_path):
    fs = EventFS(base=tmp_path)
    assert fs.list_events("nonexistent") == []


def test_source_meta_save_and_load(tmp_path):
    fs = EventFS(base=tmp_path)
    meta = SourceMeta(
        source_id="src1", name="Test", color="#ff0000",
        read_only=False, source_type="caldav", account_name="acc1",
    )
    fs.save_source_meta(meta)
    loaded = fs.load_source_meta("src1")
    assert loaded is not None
    assert loaded.name == "Test"
    assert loaded.color == "#ff0000"


def test_source_meta_round_trip(tmp_path):
    fs = EventFS(base=tmp_path)
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    meta = SourceMeta(
        source_id="src1", name="Cal", last_attempt=now, last_success=now,
    )
    fs.save_source_meta(meta)
    loaded = fs.load_source_meta("src1")
    assert loaded is not None
    assert loaded.last_attempt == now
    assert loaded.last_success == now


def test_list_source_ids(tmp_path):
    fs = EventFS(base=tmp_path)
    fs.save_source_meta(SourceMeta(source_id="src1"))
    fs.save_source_meta(SourceMeta(source_id="src2"))
    ids = fs.list_source_ids()
    assert "src1" in ids
    assert "src2" in ids


def test_pending_ops(tmp_path):
    fs = EventFS(base=tmp_path)
    assert fs.load_pending() == []

    fs.add_pending(PendingOp(uid="u1", source_id="src1", operation="create"))
    fs.add_pending(PendingOp(uid="u2", source_id="src1", operation="update"))

    ops = fs.load_pending()
    assert len(ops) == 2

    fs.remove_pending("u1")
    ops = fs.load_pending()
    assert len(ops) == 1
    assert ops[0].uid == "u2"

    fs.clear_pending()
    assert fs.load_pending() == []


def test_pending_op_replace_on_duplicate_uid(tmp_path):
    fs = EventFS(base=tmp_path)
    fs.add_pending(PendingOp(uid="u1", source_id="src1", operation="create"))
    fs.add_pending(PendingOp(uid="u1", source_id="src1", operation="update"))
    ops = fs.load_pending()
    assert len(ops) == 1
    assert ops[0].operation == "update"


def test_replace_source(tmp_path):
    fs = EventFS(base=tmp_path)
    ev1 = ImmutableEvent.from_ical(BASIC_ICAL, "src1")
    ev2 = ImmutableEvent.from_ical(
        BASIC_ICAL.replace("UID:test-uid-001", "UID:test-uid-002"), "src1"
    )
    fs.save_event(ev1)
    fs.replace_source("src1", [ev2])
    events = fs.list_events("src1")
    assert len(events) == 1
    assert events[0].uid == "test-uid-002"


def test_replace_source_preserves_pending(tmp_path):
    fs = EventFS(base=tmp_path)
    ev_pending = ImmutableEvent.from_ical(BASIC_ICAL, "src1")
    ev_pending = ev_pending.with_updates(sync_state="pending_create")
    fs.save_event(ev_pending)
    fs.add_pending(PendingOp(uid="test-uid-001", source_id="src1", operation="create"))

    ev_new = ImmutableEvent.from_ical(
        BASIC_ICAL.replace("UID:test-uid-001", "UID:test-uid-002"), "src1"
    )
    fs.replace_source("src1", [ev_new])
    events = fs.list_events("src1")
    uids = {e.uid for e in events}
    assert "test-uid-001" in uids  # pending event preserved
    assert "test-uid-002" in uids


def test_purge_source(tmp_path):
    fs = EventFS(base=tmp_path)
    ev = ImmutableEvent.from_ical(BASIC_ICAL, "src1")
    fs.save_event(ev)
    fs.save_source_meta(SourceMeta(source_id="src1"))
    fs.purge_source("src1")
    assert fs.load_event("src1", "test-uid-001") is None
    assert fs.load_source_meta("src1") is None


def test_purge_all(tmp_path):
    fs = EventFS(base=tmp_path)
    ev = ImmutableEvent.from_ical(BASIC_ICAL, "src1")
    fs.save_event(ev)
    fs.save_source_meta(SourceMeta(source_id="src1"))
    fs.purge_all()
    assert fs.load_event("src1", "test-uid-001") is None
    assert fs.load_source_meta("src1") is None