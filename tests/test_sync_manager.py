"""Tests for backend/sync_manager.py."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch, call
import pytz

from backend.sync_manager import SyncManager
from backend.event import ImmutableEvent, CalendarSource
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


def _make_config(timezone="UTC", refresh_interval=300, outdate_threshold=7200):
    """Minimal mock config with required attributes."""
    cfg = MagicMock()
    cfg.timezone = timezone
    cfg.refresh_interval = refresh_interval
    cfg.outdate_threshold = outdate_threshold
    cfg.nextcloud_accounts = []
    cfg.ics_subscriptions = []
    return cfg


# ----------------------------------------------------------------------
# source_refresh_interval
# ----------------------------------------------------------------------

def test_source_refresh_interval_global_default():
    cfg = _make_config(refresh_interval=300)
    sm = SyncManager(fs=MagicMock(), index=MagicMock(), sources={}, config=cfg)
    assert sm.source_refresh_interval("any-source") == 300


def test_source_refresh_interval_per_caldav():
    acc = MagicMock()
    acc.name = "Personal"
    acc.refresh_interval = 120
    acc.outdate_threshold = None
    cfg = _make_config(refresh_interval=300)
    cfg.nextcloud_accounts = [acc]

    sm = SyncManager(fs=MagicMock(), index=MagicMock(), sources={}, config=cfg)
    assert sm.source_refresh_interval("caldav:Personal:cal1") == 120


def test_source_refresh_interval_per_ics():
    sub = MagicMock()
    sub.name = "Holidays"
    sub.refresh_interval = 600
    sub.outdate_threshold = None
    cfg = _make_config(refresh_interval=300)
    cfg.ics_subscriptions = [sub]

    sm = SyncManager(fs=MagicMock(), index=MagicMock(), sources={}, config=cfg)
    assert sm.source_refresh_interval("ics:Holidays") == 600


def test_source_refresh_interval_fallback_when_none():
    acc = MagicMock()
    acc.name = "Personal"
    acc.refresh_interval = None  # no per-source override
    acc.outdate_threshold = None
    cfg = _make_config(refresh_interval=300)
    cfg.nextcloud_accounts = [acc]

    sm = SyncManager(fs=MagicMock(), index=MagicMock(), sources={}, config=cfg)
    assert sm.source_refresh_interval("caldav:Personal:cal1") == 300


# ----------------------------------------------------------------------
# source_outdate_threshold
# ----------------------------------------------------------------------

def test_source_outdate_threshold_global_default():
    cfg = _make_config(outdate_threshold=7200)
    sm = SyncManager(fs=MagicMock(), index=MagicMock(), sources={}, config=cfg)
    assert sm.source_outdate_threshold("any") == 7200


def test_source_outdate_threshold_per_caldav():
    acc = MagicMock()
    acc.name = "Personal"
    acc.outdate_threshold = 3600
    acc.refresh_interval = None
    cfg = _make_config(outdate_threshold=7200)
    cfg.nextcloud_accounts = [acc]

    sm = SyncManager(fs=MagicMock(), index=MagicMock(), sources={}, config=cfg)
    assert sm.source_outdate_threshold("caldav:Personal:cal1") == 3600


# ----------------------------------------------------------------------
# get_sources_needing_refresh
# ----------------------------------------------------------------------

def test_get_sources_needing_refresh_all_never_synced():
    cfg = _make_config(refresh_interval=300)
    sources = {
        "src1": CalendarSource(id="src1", name="Cal1"),
        "src2": CalendarSource(id="src2", name="Cal2"),
    }
    sm = SyncManager(fs=MagicMock(), index=MagicMock(), sources=sources, config=cfg)
    due = sm.get_sources_needing_refresh()
    assert set(due) == {"src1", "src2"}


def test_get_sources_needing_refresh_recently_synced():
    cfg = _make_config(refresh_interval=300)
    sources = {"src1": CalendarSource(id="src1", name="Cal1")}
    sm = SyncManager(fs=MagicMock(), index=MagicMock(), sources=sources, config=cfg)
    sm._source_last_attempt["src1"] = datetime.now()
    due = sm.get_sources_needing_refresh()
    assert due == []


def test_get_sources_needing_refresh_stale():
    cfg = _make_config(refresh_interval=300)
    sources = {"src1": CalendarSource(id="src1", name="Cal1")}
    sm = SyncManager(fs=MagicMock(), index=MagicMock(), sources=sources, config=cfg)
    sm._source_last_attempt["src1"] = datetime.now() - timedelta(seconds=600)
    due = sm.get_sources_needing_refresh()
    assert due == ["src1"]


def test_get_sources_needing_refresh_zero_interval_skipped():
    cfg = _make_config(refresh_interval=0)
    sources = {"src1": CalendarSource(id="src1", name="Cal1")}
    sm = SyncManager(fs=MagicMock(), index=MagicMock(), sources=sources, config=cfg)
    due = sm.get_sources_needing_refresh()
    assert due == []


# ----------------------------------------------------------------------
# is_source_outdated
# ----------------------------------------------------------------------

def test_is_source_outdated_no_last_success():
    cfg = _make_config(outdate_threshold=7200)
    sources = {"src1": CalendarSource(id="src1", name="Cal1")}
    sm = SyncManager(fs=MagicMock(), index=MagicMock(), sources=sources, config=cfg)
    assert sm.is_source_outdated("src1") is True


def test_is_source_outdated_recent():
    cfg = _make_config(outdate_threshold=7200)
    sources = {"src1": CalendarSource(id="src1", name="Cal1")}
    sm = SyncManager(fs=MagicMock(), index=MagicMock(), sources=sources, config=cfg)
    sm._source_last_success["src1"] = datetime.now()
    assert sm.is_source_outdated("src1") is False


def test_is_source_outdated_stale():
    cfg = _make_config(outdate_threshold=1)
    sources = {"src1": CalendarSource(id="src1", name="Cal1")}
    sm = SyncManager(fs=MagicMock(), index=MagicMock(), sources=sources, config=cfg)
    sm._source_last_success["src1"] = datetime.now() - timedelta(seconds=10)
    assert sm.is_source_outdated("src1") is True


# ----------------------------------------------------------------------
# _rebuild_index
# ----------------------------------------------------------------------

def test_rebuild_index(tmp_path):
    cfg = _make_config()
    fs = EventFS(base=tmp_path)
    idx = EventIndex()
    sources = {"src1": CalendarSource(id="src1", name="Cal1")}

    ev = ImmutableEvent.from_ical(BASIC_ICAL, "src1", config_tz=UTC)
    fs.save_event(ev)

    sm = SyncManager(fs=fs, index=idx, sources=sources, config=cfg)
    sm._rebuild_index()

    assert len(idx) == 1
    assert "uid-1" in idx


def test_rebuild_index_with_pending_ops(tmp_path):
    cfg = _make_config()
    fs = EventFS(base=tmp_path)
    idx = EventIndex()
    sources = {"src1": CalendarSource(id="src1", name="Cal1")}

    ev = ImmutableEvent.from_ical(BASIC_ICAL, "src1", config_tz=UTC)
    fs.save_event(ev)
    fs.add_pending(PendingOp(uid="uid-1", source_id="src1", operation="update"))

    sm = SyncManager(fs=fs, index=idx, sources=sources, config=cfg)
    sm._rebuild_index()

    assert len(idx) == 1
    indexed = idx.get("uid-1")
    assert indexed is not None
    assert indexed.sync_state == "pending_update"


def test_rebuild_index_clears_before_rebuild(tmp_path):
    cfg = _make_config()
    fs = EventFS(base=tmp_path)
    idx = EventIndex()
    sources = {"src1": CalendarSource(id="src1", name="Cal1")}

    # Add a stale event directly to index
    old_ev = ImmutableEvent.from_ical(
        BASIC_ICAL.replace("UID:uid-1", "UID:stale"), "src1", config_tz=UTC
    )
    idx.add(old_ev)

    # Save a different event to disk
    ev = ImmutableEvent.from_ical(BASIC_ICAL, "src1", config_tz=UTC)
    fs.save_event(ev)

    sm = SyncManager(fs=fs, index=idx, sources=sources, config=cfg)
    sm._rebuild_index()

    assert "uid-1" in idx
    assert "stale" not in idx


# ----------------------------------------------------------------------
# _on_connect_all_done
# ----------------------------------------------------------------------

def test_on_connect_all_done_adds_sources():
    cfg = _make_config()
    idx = EventIndex()
    sources: dict = {}
    sm = SyncManager(fs=MagicMock(), index=idx, sources=sources, config=cfg)

    result = {
        "sources": {
            "caldav:Personal:cal1": {
                "id": "caldav:Personal:cal1",
                "name": "My Calendar",
                "color": "#ff0000",
                "account_name": "Personal",
                "read_only": False,
                "source_type": "caldav",
                "is_outdated": False,
            }
        },
        "sessions": {},
        "calendars": {},
        "source_last_success": {"caldav:Personal:cal1": datetime(2026, 1, 1, tzinfo=UTC)},
        "source_last_attempt": {"caldav:Personal:cal1": datetime(2026, 1, 1, tzinfo=UTC)},
        "last_sync_time": datetime(2026, 1, 1, tzinfo=UTC),
        "sync_start": datetime(2025, 1, 1, tzinfo=UTC),
        "sync_end": datetime(2026, 6, 1, tzinfo=UTC),
    }
    sm._on_connect_all_done(result)

    assert "caldav:Personal:cal1" in sm._sources
    assert sm._sources["caldav:Personal:cal1"].name == "My Calendar"
    assert sm._valid_sync_window_start is not None
    assert sm._valid_sync_window_end is not None


def test_on_connect_all_done_updates_existing_source():
    cfg = _make_config()
    idx = EventIndex()
    src = CalendarSource(id="caldav:Personal:cal1", name="Old", color="#000000",
                         read_only=False, is_outdated=True)
    sources = {"caldav:Personal:cal1": src}
    sm = SyncManager(fs=MagicMock(), index=idx, sources=sources, config=cfg)

    result = {
        "sources": {
            "caldav:Personal:cal1": {
                "id": "caldav:Personal:cal1",
                "name": "My Calendar",
                "color": "#ff0000",
                "account_name": "Personal",
                "read_only": False,
                "source_type": "caldav",
                "is_outdated": False,
            }
        },
        "sessions": {},
        "calendars": {},
        "source_last_success": {},
        "source_last_attempt": {},
        "last_sync_time": None,
        "sync_start": None,
        "sync_end": None,
    }
    sm._on_connect_all_done(result)

    # name is NOT overwritten (only read_only, is_outdated, color are updated)
    assert sources["caldav:Personal:cal1"].name == "Old"
    assert sources["caldav:Personal:cal1"].color == "#ff0000"
    assert sources["caldav:Personal:cal1"].is_outdated is False


# ----------------------------------------------------------------------
# _on_sync_pending_done
# ----------------------------------------------------------------------

def test_on_sync_pending_done_removes_successful(tmp_path):
    cfg = _make_config()
    fs = EventFS(base=tmp_path)
    idx = EventIndex()
    sources = {"src1": CalendarSource(id="src1", name="Cal1")}

    ev = ImmutableEvent.from_ical(BASIC_ICAL, "src1", config_tz=UTC)
    fs.save_event(ev)
    fs.add_pending(PendingOp(uid="uid-1", source_id="src1", operation="delete"))
    idx.add(ev)

    sm = SyncManager(fs=fs, index=idx, sources=sources, config=cfg)

    result = {"success": 1, "failed": 0, "done_uids": ["uid-1"], "last_sync_time": datetime.now()}
    sm._on_sync_pending_done(result)

    assert len(fs.load_pending()) == 0
    assert fs.load_event("src1", "uid-1") is None  # deleted from disk


# ----------------------------------------------------------------------
# copy_state_from
# ----------------------------------------------------------------------

def test_copy_state_from():
    cfg = _make_config()
    sm1 = SyncManager(fs=MagicMock(), index=MagicMock(), sources={}, config=cfg)
    sm1._last_sync_time = datetime(2026, 1, 1, tzinfo=UTC)
    sm1._valid_sync_window_start = datetime(2025, 1, 1, tzinfo=UTC)
    sm1._valid_sync_window_end = datetime(2026, 6, 1, tzinfo=UTC)
    sm1._source_last_success["src1"] = datetime(2026, 1, 1, tzinfo=UTC)
    sm1._source_last_attempt["src1"] = datetime(2026, 1, 1, tzinfo=UTC)

    sm2 = SyncManager(fs=MagicMock(), index=MagicMock(), sources={}, config=cfg)
    sm2.copy_state_from(sm1)

    assert sm2._last_sync_time == sm1._last_sync_time
    assert sm2._valid_sync_window_start == sm1._valid_sync_window_start
    assert sm2._valid_sync_window_end == sm1._valid_sync_window_end
    assert sm2._source_last_success == sm1._source_last_success
    assert sm2._source_last_attempt == sm1._source_last_attempt


# ----------------------------------------------------------------------
# _on_connect_all_done auto-color assignment
# ----------------------------------------------------------------------

def test_on_connect_all_done_assigns_color_when_empty():
    """When server reports no color, a random one is assigned."""
    cfg = _make_config()
    idx = EventIndex()
    sources: dict = {}
    sm = SyncManager(fs=MagicMock(), index=idx, sources=sources, config=cfg)

    result = {
        "sources": {
            "caldav:Personal:cal1": {
                "id": "caldav:Personal:cal1",
                "name": "No Color Cal",
                "color": "",
                "account_name": "Personal",
                "read_only": False,
                "source_type": "caldav",
                "is_outdated": False,
            }
        },
        "sessions": {},
        "calendars": {},
        "source_last_success": {},
        "source_last_attempt": {},
        "last_sync_time": None,
        "sync_start": None,
        "sync_end": None,
    }
    sm._on_connect_all_done(result)

    src = sm._sources["caldav:Personal:cal1"]
    assert src.color.startswith("#")
    assert len(src.color) == 7