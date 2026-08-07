"""Tests for backend/event_fs.py."""

from datetime import datetime
import json
import pytz
from backend.event_fs import EventFS, SourceMeta, PendingOp, _safe_filename, _default_base
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


def test_load_event_corrupt(tmp_path):
    """Corrupt .ics file (binary junk) should return None."""
    fs = EventFS(base=tmp_path)
    path = fs._event_path("src1", "test-uid-001")
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write raw bytes that are not valid UTF-8 to trigger read error
    path.write_bytes(b"\xff\xfe\x00\xff")
    loaded = fs.load_event("src1", "test-uid-001")
    assert loaded is None


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


def test_list_events_skips_corrupt(tmp_path):
    """list_events should skip corrupt .ics files (binary junk) without crashing."""
    fs = EventFS(base=tmp_path)
    # Save one valid
    ev = ImmutableEvent.from_ical(BASIC_ICAL, "src1")
    fs.save_event(ev)
    # Write a file with invalid UTF-8 bytes
    path = fs._event_path("src1", "corrupt-uid")
    path.write_bytes(b"\xff\xfe\x00\xff")
    events = fs.list_events("src1")
    assert len(events) == 1
    assert events[0].uid == "test-uid-001"


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


def test_source_meta_all_fields(tmp_path):
    """Round-trip all SourceMeta fields including null datetimes."""
    fs = EventFS(base=tmp_path)
    meta = SourceMeta(
        source_id="src-full",
        name="Full",
        color="#00ff00",
        read_only=True,
        source_type="ics",
        account_name="",
    )
    assert meta.last_attempt is None
    assert meta.last_success is None
    fs.save_source_meta(meta)
    loaded = fs.load_source_meta("src-full")
    assert loaded is not None
    assert loaded.name == "Full"
    assert loaded.color == "#00ff00"
    assert loaded.read_only is True
    assert loaded.source_type == "ics"
    assert loaded.last_attempt is None
    assert loaded.last_success is None


def test_load_source_meta_corrupt(tmp_path):
    """Corrupt JSON should return None."""
    fs = EventFS(base=tmp_path)
    path = fs._source_meta_path("src1")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json")
    assert fs.load_source_meta("src1") is None


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


def test_pending_op_with_instance_start(tmp_path):
    """PendingOp with instance_start round-trips correctly."""
    fs = EventFS(base=tmp_path)
    inst_start = datetime(2026, 1, 8, 10, 0, 0, tzinfo=UTC)
    fs.add_pending(PendingOp(
        uid="u1", source_id="src1", operation="delete_instance",
        instance_start=inst_start,
    ))
    ops = fs.load_pending()
    assert len(ops) == 1
    assert ops[0].operation == "delete_instance"
    assert ops[0].instance_start == inst_start


def test_load_pending_corrupt(tmp_path):
    """Corrupt pending.json should return empty list."""
    fs = EventFS(base=tmp_path)
    fs._pending_file.parent.mkdir(parents=True, exist_ok=True)
    fs._pending_file.write_text("not json")
    assert fs.load_pending() == []


def test_replace_source(tmp_path):
    """replace_source deletes events inside the sync window that the server didn't return."""
    fs = EventFS(base=tmp_path)
    ev1 = ImmutableEvent.from_ical(BASIC_ICAL, "src1")
    ev2 = ImmutableEvent.from_ical(
        BASIC_ICAL.replace("UID:test-uid-001", "UID:test-uid-002"), "src1"
    )
    fs.save_event(ev1)
    # ev1 (2026-01-01) is inside the sync window and not in the server response → deleted
    fs.replace_source("src1", [ev2],
                      datetime(2025, 1, 1, tzinfo=UTC),
                      datetime(2027, 1, 1, tzinfo=UTC))
    events = fs.list_events("src1")
    assert len(events) == 1
    assert events[0].uid == "test-uid-002"


def test_replace_source_overwrites_pending_cache(tmp_path):
    """The cache is a pure server mirror.

    A pending event's *cache* version is overwritten by server data; the
    pending edit itself lives in pending_events/ and is overlaid by the
    store at display time.  If the server no longer returns the event
    (deleted remotely), its cached .ics is removed.
    """
    fs = EventFS(base=tmp_path)
    ev_pending = ImmutableEvent.from_ical(BASIC_ICAL, "src1")
    ev_pending = ev_pending.with_updates(sync_state="pending_create")
    fs.save_event(ev_pending)
    fs.save_pending_event(ev_pending)
    fs.add_pending(PendingOp(uid="test-uid-001", source_id="src1", operation="create"))

    ev_new = ImmutableEvent.from_ical(
        BASIC_ICAL.replace("UID:test-uid-001", "UID:test-uid-002"), "src1"
    )
    fs.replace_source("src1", [ev_new],
                      datetime(2025, 1, 1, tzinfo=UTC),
                      datetime(2027, 1, 1, tzinfo=UTC))
    # Cache reflects the server — the event no longer exists there.
    events = fs.list_events("src1")
    uids = {e.uid for e in events}
    assert "test-uid-001" not in uids  # deleted from cache (server mirror)
    assert "test-uid-002" in uids

    # The pending edit is preserved in pending_events/ for display.
    pending = fs.load_pending_event("src1", "test-uid-001")
    assert pending is not None
    assert pending.uid == "test-uid-001"


def test_replace_source_with_pending_but_not_on_disk(tmp_path):
    """Pending op that hasn't been saved to disk shouldn't cause issues."""
    fs = EventFS(base=tmp_path)
    fs.add_pending(PendingOp(uid="pending-only", source_id="src1", operation="create"))
    ev_new = ImmutableEvent.from_ical(BASIC_ICAL, "src1")
    fs.replace_source("src1", [ev_new],
                      datetime(2025, 1, 1, tzinfo=UTC),
                      datetime(2027, 1, 1, tzinfo=UTC))
    events = fs.list_events("src1")
    assert len(events) == 1  # pending-only wasn't on disk, so only new event


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


def test_config_tz_round_trip(tmp_path):
    """config_tz is stored but __post_init__ uses UTC for parsing.
    This test verifies round-trip doesn't crash and returns an event."""
    berlin = pytz.timezone("Europe/Berlin")
    fs = EventFS(base=tmp_path, config_tz=berlin)
    ical = (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "PRODID:-//Test//\r\n"
        "BEGIN:VEVENT\r\n"
        "DTSTART:20260101T100000Z\r\n"
        "DTEND:20260101T110000Z\r\n"
        "SUMMARY:Test\r\n"
        "UID:float-1\r\n"
        "END:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    )
    ev = ImmutableEvent.from_ical(ical, "src1", config_tz=berlin)
    fs.save_event(ev)
    loaded = fs.load_event("src1", "float-1")
    assert loaded is not None
    assert loaded.summary == "Test"
