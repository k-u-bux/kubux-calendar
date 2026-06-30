"""Tests for backend/event_store.py."""

import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
import pytz

from backend.event_store import EventStore
from backend.event import ImmutableEvent, CalendarSource, EventView
from backend.event_fs import EventFS, PendingOp
from backend.event_index import EventIndex

UTC = pytz.UTC

BASIC_ICAL = (
    "BEGIN:VCALENDAR\r\n"
    "VERSION:2.0\r\n"
    "PRODID:-//Test//\r\n"
    "BEGIN:VEVENT\r\n"
    "DTSTART:20260101T100000Z\r\n"
    "DTEND:20260101T110000Z\r\n"
    "SUMMARY:Test\r\n"
    "UID:uid-1\r\n"
    "END:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)


def _make_config_path(tmp_path: Path):
    """Create a minimal config with a state_file in tmp_path."""
    cfg = MagicMock()
    cfg.timezone = "UTC"
    cfg.state_file = tmp_path / "state.json"
    cfg.log_level = "warn"
    cfg.refresh_interval = 300
    cfg.outdate_threshold = 7200
    cfg.ics_subscriptions = []
    cfg.nextcloud_accounts = []
    return cfg


@pytest.fixture(autouse=True)
def _clean_event_fs(tmp_path):
    """Each test gets a clean EventFS via monkeypatching _default_base."""
    import backend.event_fs as _efs
    original = _efs._default_base
    _efs._default_base = lambda: tmp_path / "v2"
    yield
    _efs._default_base = original


# ----------------------------------------------------------------------
# create_event
# ----------------------------------------------------------------------

def test_create_event(tmp_path):
    cfg = _make_config_path(tmp_path)
    store = EventStore(cfg)

    # Inject a writable CalDAV source
    src = CalendarSource(id="caldav:acc:cal1", name="Cal1", source_type="caldav")
    store._sources["caldav:acc:cal1"] = src

    view = store.create_event(
        calendar_id="caldav:acc:cal1",
        summary="New Event",
        start=datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC),
        end=datetime(2026, 1, 1, 11, 0, 0, tzinfo=UTC),
        description="desc",
        location="loc",
    )
    assert view is not None
    assert view.summary == "New Event"
    assert view.sync_status == "pending"
    assert view.pending_operation == "create"

    # Check it was saved to FS
    events = store._fs.list_events("caldav:acc:cal1")
    assert len(events) == 1
    assert events[0].summary == "New Event"

    # Check it's in the index
    assert len(store._index) == 1
    results = store._index.query_range(
        datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
        datetime(2026, 1, 2, 0, 0, 0, tzinfo=UTC),
    )
    assert len(results) == 1
    assert results[0].source_id == "caldav:acc:cal1"


def test_create_event_read_only_rejected(tmp_path):
    cfg = _make_config_path(tmp_path)
    store = EventStore(cfg)

    src = CalendarSource(id="caldav:acc:cal1", name="Cal1", source_type="caldav", read_only=True)
    store._sources["caldav:acc:cal1"] = src

    view = store.create_event(
        calendar_id="caldav:acc:cal1",
        summary="New",
        start=datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC),
        end=datetime(2026, 1, 1, 11, 0, 0, tzinfo=UTC),
    )
    assert view is None


def test_create_event_ics_rejected(tmp_path):
    cfg = _make_config_path(tmp_path)
    store = EventStore(cfg)

    src = CalendarSource(id="ics:Holidays", name="Holidays", source_type="ics")
    store._sources["ics:Holidays"] = src

    view = store.create_event(
        calendar_id="ics:Holidays",
        summary="New",
        start=datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC),
        end=datetime(2026, 1, 1, 11, 0, 0, tzinfo=UTC),
    )
    assert view is None


# ----------------------------------------------------------------------
# update_event
# ----------------------------------------------------------------------

def test_update_event(tmp_path):
    cfg = _make_config_path(tmp_path)
    store = EventStore(cfg)

    src = CalendarSource(id="caldav:acc:cal1", name="Cal1", source_type="caldav")
    store._sources["caldav:acc:cal1"] = src

    # Create first
    view = store.create_event(
        calendar_id="caldav:acc:cal1",
        summary="Old",
        start=datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC),
        end=datetime(2026, 1, 1, 11, 0, 0, tzinfo=UTC),
    )
    assert view is not None

    # Mutate
    view.summary = "Updated"
    ok = store.update_event(view)
    assert ok is True

    # Check FS — the updated event is there
    events = store._fs.list_events("caldav:acc:cal1")
    summaries = {e.summary for e in events}
    assert "Updated" in summaries

    # sync_state is in-memory only (not in ical_data on disk).
    # Check the index for the pending_update state.
    indexed = store._index.get(view.uid)
    assert indexed is not None
    assert indexed.sync_state == "pending_update"


def test_update_event_read_only_rejected(tmp_path):
    cfg = _make_config_path(tmp_path)
    store = EventStore(cfg)

    src = CalendarSource(id="caldav:acc:cal1", name="Cal1", source_type="caldav", read_only=True)
    store._sources["caldav:acc:cal1"] = src

    ev = ImmutableEvent.from_ical(BASIC_ICAL, "caldav:acc:cal1")
    store._fs.save_event(ev)
    store._index.add(ev)

    view = EventView(ev, src)
    view.summary = "Changed"
    ok = store.update_event(view)
    assert ok is False


# ----------------------------------------------------------------------
# delete_event
# ----------------------------------------------------------------------

def test_delete_event(tmp_path):
    cfg = _make_config_path(tmp_path)
    store = EventStore(cfg)

    src = CalendarSource(id="caldav:acc:cal1", name="Cal1", source_type="caldav")
    store._sources["caldav:acc:cal1"] = src

    view = store.create_event(
        calendar_id="caldav:acc:cal1",
        summary="To Delete",
        start=datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC),
        end=datetime(2026, 1, 1, 11, 0, 0, tzinfo=UTC),
    )
    assert view is not None

    ok = store.delete_event(view)
    assert ok is True

    # Pending op added — delete op for this event exists
    ops = store._fs.load_pending()
    delete_ops = [o for o in ops if o.operation == "delete" and o.uid == view.uid]
    assert len(delete_ops) == 1


def test_delete_event_read_only_rejected(tmp_path):
    cfg = _make_config_path(tmp_path)
    store = EventStore(cfg)

    src = CalendarSource(id="caldav:acc:cal1", name="Cal1", source_type="caldav", read_only=True)
    store._sources["caldav:acc:cal1"] = src

    ev = ImmutableEvent.from_ical(BASIC_ICAL, "caldav:acc:cal1")
    view = EventView(ev, src)

    ok = store.delete_event(view)
    assert ok is False


# ----------------------------------------------------------------------
# delete_recurring_instance
# ----------------------------------------------------------------------

def test_delete_recurring_instance(tmp_path):
    cfg = _make_config_path(tmp_path)
    store = EventStore(cfg)

    src = CalendarSource(id="caldav:acc:cal1", name="Cal1", source_type="caldav")
    store._sources["caldav:acc:cal1"] = src

    ical = BASIC_ICAL.replace("END:VEVENT", "RRULE:FREQ=WEEKLY\r\nEND:VEVENT")
    ev = ImmutableEvent.from_ical(ical, "caldav:acc:cal1")
    store._fs.save_event(ev)
    store._index.add(ev)

    view = EventView(ev, src)
    inst_start = datetime(2026, 1, 8, 10, 0, 0, tzinfo=UTC)

    ok = store.delete_recurring_instance(view, inst_start)
    assert ok is True

    ops = store._fs.load_pending()
    instance_ops = [o for o in ops if o.operation == "delete_instance"]
    assert len(instance_ops) >= 1
    assert instance_ops[-1].instance_start == inst_start


# ----------------------------------------------------------------------
# move_event
# ----------------------------------------------------------------------

def test_move_event(tmp_path):
    cfg = _make_config_path(tmp_path)
    store = EventStore(cfg)

    src1 = CalendarSource(id="caldav:acc:cal1", name="Cal1", source_type="caldav")
    src2 = CalendarSource(id="caldav:acc:cal2", name="Cal2", source_type="caldav")
    store._sources["caldav:acc:cal1"] = src1
    store._sources["caldav:acc:cal2"] = src2

    view = store.create_event(
        calendar_id="caldav:acc:cal1",
        summary="Movable",
        start=datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC),
        end=datetime(2026, 1, 1, 11, 0, 0, tzinfo=UTC),
    )
    assert view is not None

    result = store.move_event(view, "caldav:acc:cal2")
    assert result is not None
    assert result.source.id == "caldav:acc:cal2"
    assert result.summary == "Movable"


def test_move_event_same_calendar_noop(tmp_path):
    cfg = _make_config_path(tmp_path)
    store = EventStore(cfg)

    src = CalendarSource(id="caldav:acc:cal1", name="Cal1", source_type="caldav")
    store._sources["caldav:acc:cal1"] = src

    view = store.create_event(
        calendar_id="caldav:acc:cal1",
        summary="Stay",
        start=datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC),
        end=datetime(2026, 1, 1, 11, 0, 0, tzinfo=UTC),
    )
    result = store.move_event(view, "caldav:acc:cal1")
    assert result is view  # same object returned


# ----------------------------------------------------------------------
# get_calendars
# ----------------------------------------------------------------------

def test_get_calendars_sorting():
    cfg = MagicMock()
    cfg.timezone = "UTC"
    cfg.state_file = Path("/tmp/state.json")
    cfg.ics_subscriptions = []
    cfg.nextcloud_accounts = [MagicMock(name="Personal"), MagicMock(name="Work")]
    cfg.nextcloud_accounts[0].name = "Personal"
    cfg.nextcloud_accounts[1].name = "Work"

    store = EventStore(cfg)
    store._sources["caldav:Personal:a"] = CalendarSource(
        id="caldav:Personal:a", name="Alpha", account_name="Personal"
    )
    store._sources["caldav:Work:b"] = CalendarSource(
        id="caldav:Work:b", name="Beta", account_name="Work"
    )
    store._sources["caldav:Personal:c"] = CalendarSource(
        id="caldav:Personal:c", name="Charlie", account_name="Personal"
    )

    cals = store.get_calendars()
    ids = [c.id for c in cals]
    # Personal first (order: Alpha, Charlie), then Work (Beta)
    assert ids == ["caldav:Personal:a", "caldav:Personal:c", "caldav:Work:b"]


def test_get_calendars_visible_only():
    cfg = MagicMock()
    cfg.timezone = "UTC"
    cfg.state_file = Path("/tmp/state.json")
    cfg.ics_subscriptions = []
    cfg.nextcloud_accounts = []

    store = EventStore(cfg)
    store._sources["cal1"] = CalendarSource(id="cal1", name="Cal1")
    store._sources["cal2"] = CalendarSource(id="cal2", name="Cal2")
    store._visibility["cal2"] = False

    cals = store.get_calendars(visible_only=True)
    assert len(cals) == 1
    assert cals[0].id == "cal1"


def test_get_writable_calendars():
    cfg = MagicMock()
    cfg.timezone = "UTC"
    cfg.state_file = Path("/tmp/state.json")
    cfg.ics_subscriptions = []
    cfg.nextcloud_accounts = []

    store = EventStore(cfg)
    store._sources["cal1"] = CalendarSource(id="cal1", name="Cal1", read_only=True)
    store._sources["cal2"] = CalendarSource(id="cal2", name="Cal2", read_only=False)

    writable = store.get_writable_calendars()
    assert len(writable) == 1
    assert writable[0].id == "cal2"


# ----------------------------------------------------------------------
# visibility and color
# ----------------------------------------------------------------------

def test_set_calendar_visibility(tmp_path):
    cfg = _make_config_path(tmp_path)
    store = EventStore(cfg)
    store._sources["cal1"] = CalendarSource(id="cal1", name="Cal1")

    store.set_calendar_visibility("cal1", False)
    assert store._visibility["cal1"] is False
    assert store._sources["cal1"].visible is False

    # Persisted
    with open(cfg.state_file) as f:
        state = json.load(f)
    assert state["visibility"]["cal1"] is False


def test_set_calendar_color(tmp_path):
    cfg = _make_config_path(tmp_path)
    store = EventStore(cfg)
    store._sources["cal1"] = CalendarSource(id="cal1", name="Cal1", color="#000000")

    store.set_calendar_color("cal1", "#ff0000")
    assert store._sources["cal1"].color == "#ff0000"
    assert store._user_colors["cal1"] == "#ff0000"

    # Persisted
    with open(cfg.state_file) as f:
        state = json.load(f)
    assert state["user-assigned-colors"]["cal1"] == "#ff0000"


# ----------------------------------------------------------------------
# get_sources_by_visibility
# ----------------------------------------------------------------------

def test_get_sources_by_visibility():
    cfg = MagicMock()
    cfg.timezone = "UTC"
    cfg.state_file = Path("/tmp/state.json")
    cfg.ics_subscriptions = []
    cfg.nextcloud_accounts = []

    store = EventStore(cfg)
    store._sources["cal1"] = CalendarSource(id="cal1", name="Cal1")
    store._sources["cal2"] = CalendarSource(id="cal2", name="Cal2")
    store._visibility["cal2"] = False

    visible, invisible = store.get_sources_by_visibility()
    assert visible == ["cal1"]
    assert invisible == ["cal2"]


# ----------------------------------------------------------------------
# state persistence
# ----------------------------------------------------------------------

def test_state_round_trip(tmp_path):
    cfg = _make_config_path(tmp_path)
    store = EventStore(cfg)
    store._visibility["cal1"] = False
    store._user_colors["cal1"] = "#ff0000"
    store._auto_colors["cal2"] = "#00ff00"
    store._save_state()

    # New store loads it
    store2 = EventStore(cfg)
    store2._load_state()
    assert store2._visibility["cal1"] is False
    assert store2._user_colors["cal1"] == "#ff0000"
    assert store2._auto_colors["cal2"] == "#00ff00"


# ----------------------------------------------------------------------
# clone
# ----------------------------------------------------------------------

def test_clone(tmp_path):
    cfg = _make_config_path(tmp_path)
    store = EventStore(cfg)
    store._sources["cal1"] = CalendarSource(id="cal1", name="Cal1")
    store._visibility["cal1"] = False

    ev = ImmutableEvent.from_ical(BASIC_ICAL, "cal1")
    store._index.add(ev)

    clone = store.clone(cfg)
    assert "cal1" in clone._sources
    assert clone._visibility["cal1"] is False
    assert len(clone._index) == 1
    assert clone._on_change_callback is None  # callbacks not copied


# ----------------------------------------------------------------------
# _ensure_source_colors
# ----------------------------------------------------------------------

def test_ensure_source_colors_assigns_when_empty(tmp_path):
    cfg = _make_config_path(tmp_path)
    store = EventStore(cfg)
    src = CalendarSource(id="cal1", name="Cal1", color="")
    store._sources["cal1"] = src

    store._ensure_source_colors()
    assert src.color.startswith("#")
    assert len(src.color) == 7


def test_ensure_source_colors_preserves_config_color():
    cfg = MagicMock()
    cfg.timezone = "UTC"
    cfg.state_file = Path("/tmp/state.json")
    cfg.ics_subscriptions = []
    cfg.nextcloud_accounts = []

    store = EventStore(cfg)
    src = CalendarSource(id="cal1", name="Cal1", color="#abcdef")
    store._sources["cal1"] = src

    store._ensure_source_colors()
    assert src.color == "#abcdef"


def test_ensure_source_colors_preserves_user_color():
    cfg = MagicMock()
    cfg.timezone = "UTC"
    cfg.state_file = Path("/tmp/state.json")
    cfg.ics_subscriptions = []
    cfg.nextcloud_accounts = []

    store = EventStore(cfg)
    src = CalendarSource(id="cal1", name="Cal1", color="#000000")
    store._sources["cal1"] = src
    store._user_colors["cal1"] = "#ff0000"

    store._ensure_source_colors()
    assert src.color == "#000000"  # user color applied elsewhere, _ensure_source_colors skips user-colored


# ----------------------------------------------------------------------
# _is_cache_valid
# ----------------------------------------------------------------------

def test_is_cache_valid_no_sync_manager():
    cfg = _make_config_path(Path("/tmp"))
    store = EventStore(cfg)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = datetime(2026, 2, 1, tzinfo=UTC)
    assert store._is_cache_valid(start, end) is False


def test_is_cache_valid_within_window():
    cfg = _make_config_path(Path("/tmp"))
    store = EventStore(cfg)
    store._sync_manager = MagicMock()
    store._sync_manager.valid_sync_window_start = datetime(2025, 12, 1, tzinfo=UTC)
    store._sync_manager.valid_sync_window_end = datetime(2026, 3, 1, tzinfo=UTC)

    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = datetime(2026, 2, 1, tzinfo=UTC)
    assert store._is_cache_valid(start, end) is True


def test_is_cache_valid_outside_window():
    cfg = _make_config_path(Path("/tmp"))
    store = EventStore(cfg)
    store._sync_manager = MagicMock()
    store._sync_manager.valid_sync_window_start = datetime(2025, 12, 1, tzinfo=UTC)
    store._sync_manager.valid_sync_window_end = datetime(2026, 1, 15, tzinfo=UTC)

    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = datetime(2026, 2, 1, tzinfo=UTC)
    assert store._is_cache_valid(start, end) is False