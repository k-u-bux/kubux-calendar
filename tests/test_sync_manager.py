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

def test_is_source_outdated_no_attempt():
    """No sync attempted this session — data is unverified, not stale."""
    cfg = _make_config(outdate_threshold=7200)
    sources = {"src1": CalendarSource(id="src1", name="Cal1")}
    sm = SyncManager(fs=MagicMock(), index=MagicMock(), sources=sources, config=cfg)
    assert sm.is_source_outdated("src1") is False


def test_is_source_outdated_attempted_but_no_success():
    """Sync was attempted but never succeeded — data is stale."""
    cfg = _make_config(outdate_threshold=7200)
    sources = {"src1": CalendarSource(id="src1", name="Cal1")}
    sm = SyncManager(fs=MagicMock(), index=MagicMock(), sources=sources, config=cfg)
    sm._source_last_attempt["src1"] = datetime.now()
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
    sm._source_last_attempt["src1"] = datetime.now() - timedelta(seconds=10)
    sm._source_last_success["src1"] = datetime.now() - timedelta(seconds=10)
    assert sm.is_source_outdated("src1") is True


# ----------------------------------------------------------------------
# properties
# ----------------------------------------------------------------------

def test_properties():
    cfg = _make_config()
    sm = SyncManager(fs=MagicMock(), index=MagicMock(), sources={}, config=cfg)
    assert sm.last_sync_time is None
    assert sm.is_range_covered(datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC)) is False
    assert sm.source_last_success("src1") is None
    assert sm.source_last_attempt("src1") is None
    assert sm.pending_count() == 0
    assert sm.get_calendar_info("src1") is None


def test_properties_with_values():
    cfg = _make_config()
    sm = SyncManager(fs=MagicMock(), index=MagicMock(), sources={}, config=cfg)
    now = datetime.now(pytz.UTC)
    sm._last_sync_time = now
    sm._sync_windows.append((now - timedelta(days=120), now + timedelta(days=240), now))
    sm._source_last_success["src1"] = now
    sm._source_last_attempt["src1"] = now
    sm._calendars["src1"] = MagicMock()
    sm._fs.load_pending = MagicMock(return_value=[MagicMock()])

    assert sm.last_sync_time == now
    assert sm.is_range_covered(now - timedelta(days=30), now + timedelta(days=30)) is True
    assert sm.is_range_covered(now - timedelta(days=200), now - timedelta(days=150)) is False
    assert sm.source_last_success("src1") == now
    assert sm.source_last_attempt("src1") == now
    assert sm.pending_count() == 1
    assert sm.get_calendar_info("src1") is not None


# ----------------------------------------------------------------------
# register_ics
# ----------------------------------------------------------------------

def test_register_ics():
    cfg = _make_config()
    sm = SyncManager(fs=MagicMock(), index=MagicMock(), sources={}, config=cfg)
    sm.register_ics("ics:Holidays", "https://example.com/holidays.ics")
    assert sm._ics_urls["ics:Holidays"] == "https://example.com/holidays.ics"


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
    sm1._sync_windows.append((datetime(2025, 1, 1, tzinfo=UTC), datetime(2026, 6, 1, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC)))
    sm1._source_last_success["src1"] = datetime(2026, 1, 1, tzinfo=UTC)
    sm1._source_last_attempt["src1"] = datetime(2026, 1, 1, tzinfo=UTC)

    sm2 = SyncManager(fs=MagicMock(), index=MagicMock(), sources={}, config=cfg)
    sm2.copy_state_from(sm1)

    assert sm2._last_sync_time == sm1._last_sync_time
    assert sm2._sync_windows == sm1._sync_windows
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


# ----------------------------------------------------------------------
# _on_refresh_done
# ----------------------------------------------------------------------

def test_on_refresh_done_applies_state():
    cfg = _make_config()
    sources = {"src1": CalendarSource(id="src1", name="Cal1", color="#000000")}
    sm = SyncManager(fs=MagicMock(), index=MagicMock(), sources=sources, config=cfg)

    result = {
        "synced": ["src1"],
        "sessions": {},
        "calendars": {},
        "sources": {
            "src1": {
                "color": "#ff0000",
                "read_only": False,
                "is_outdated": False,
            }
        },
        "source_last_success": {"src1": datetime(2026, 1, 1, tzinfo=UTC)},
        "source_last_attempt": {"src1": datetime(2026, 1, 1, tzinfo=UTC)},
        "last_sync_time": datetime(2026, 1, 1, tzinfo=UTC),
        "sync_start": datetime(2025, 12, 1, tzinfo=UTC),
        "sync_end": datetime(2026, 3, 1, tzinfo=UTC),
    }
    sm._on_refresh_done(result)

    assert sm._sources["src1"].color == "#ff0000"
    assert sm._last_sync_time == result["last_sync_time"]
    assert sm._source_last_success["src1"] == result["source_last_success"]["src1"]
    # Sync window should be recorded
    assert len(sm._sync_windows) == 1
    assert sm._sync_windows[0][0] == result["sync_start"]
    assert sm._sync_windows[0][1] == result["sync_end"]


def test_on_refresh_done_no_sync_time():
    """When no sync_time in result, last_sync_time is not updated."""
    cfg = _make_config()
    sm = SyncManager(fs=MagicMock(), index=MagicMock(), sources={}, config=cfg)
    sm._on_refresh_done({
        "synced": [],
        "sessions": {}, "calendars": {}, "sources": {},
        "source_last_success": {}, "source_last_attempt": {},
        "last_sync_time": None, "sync_start": None, "sync_end": None,
    })
    assert sm._last_sync_time is None


# ----------------------------------------------------------------------
# _notify_change / _notify_sync_status
# ----------------------------------------------------------------------

def test_notify_change_callback():
    cfg = _make_config()
    fired = []
    sm = SyncManager(fs=MagicMock(), index=MagicMock(), sources={}, config=cfg,
                     on_change=lambda: fired.append(True))
    sm._notify_change()
    assert fired == [True]


def test_notify_sync_status_callback():
    cfg = _make_config()
    fires = []
    sm = SyncManager(fs=MagicMock(), index=MagicMock(), sources={}, config=cfg,
                     on_sync_status=lambda pc, lst: fires.append((pc, lst)))
    sm._notify_sync_status()
    assert len(fires) == 1


# ----------------------------------------------------------------------
# refresh_due_in_background
# ----------------------------------------------------------------------

def test_refresh_due_in_background_dispatches():
    cfg = _make_config(refresh_interval=300)
    sources = {"src1": CalendarSource(id="src1", name="Cal1")}
    sm = SyncManager(fs=MagicMock(), index=MagicMock(), sources=sources, config=cfg)
    with patch('backend.sync_manager.dispatch_task') as mock:
        sm.refresh_due_in_background()
        mock.assert_called_once()


def test_refresh_due_in_background_no_due():
    """No due sources → no dispatch."""
    cfg = _make_config(refresh_interval=300)
    sources = {"src1": CalendarSource(id="src1", name="Cal1")}
    sm = SyncManager(fs=MagicMock(), index=MagicMock(), sources=sources, config=cfg)
    sm._source_last_attempt["src1"] = datetime.now()  # just synced
    with patch('backend.sync_manager.dispatch_task') as mock:
        sm.refresh_due_in_background()
        mock.assert_not_called()


# ----------------------------------------------------------------------
# sync_pending_in_background
# ----------------------------------------------------------------------

def test_sync_pending_in_background_dispatches(tmp_path):
    cfg = _make_config()
    fs = EventFS(base=tmp_path)
    fs.add_pending(PendingOp(uid="u1", source_id="src1", operation="create"))
    sm = SyncManager(fs=fs, index=MagicMock(), sources={}, config=cfg)
    with patch('backend.sync_manager.dispatch_task') as mock:
        sm.sync_pending_in_background()
        mock.assert_called_once()


def test_sync_pending_in_background_no_ops():
    """No pending ops → no dispatch."""
    cfg = _make_config()
    fs = MagicMock()
    fs.load_pending.return_value = []
    sm = SyncManager(fs=fs, index=MagicMock(), sources={}, config=cfg)
    with patch('backend.sync_manager.dispatch_task') as mock:
        sm.sync_pending_in_background()
        mock.assert_not_called()


# ----------------------------------------------------------------------
# _sync_one
# ----------------------------------------------------------------------

def test_sync_one_missing_cal_info():
    cfg = _make_config()
    sm = SyncManager(fs=MagicMock(), index=MagicMock(), sources={}, config=cfg)
    op = PendingOp(uid="u1", source_id="src1", operation="create")
    ok = sm._sync_one(op, {}, {}, {})
    assert ok is False


def test_sync_one_missing_session():
    cfg = _make_config()
    sm = SyncManager(fs=MagicMock(), index=MagicMock(), sources={}, config=cfg)
    cal_info = MagicMock()
    src = CalendarSource(id="src1", name="Cal1", account_name="acc1")
    op = PendingOp(uid="u1", source_id="src1", operation="create")
    ok = sm._sync_one(op, {"src1": cal_info}, {}, {"src1": src})
    assert ok is False


def test_sync_one_create_missing_event():
    """If the event is not in FS, create returns False."""
    cfg = _make_config()
    fs = MagicMock()
    fs.load_event.return_value = None
    sm = SyncManager(fs=fs, index=MagicMock(), sources={}, config=cfg)
    cal_info = MagicMock()
    session = MagicMock()
    src = CalendarSource(id="src1", name="Cal1", account_name="acc1")
    op = PendingOp(uid="u1", source_id="src1", operation="create")
    ok = sm._sync_one(op, {"src1": cal_info}, {"acc1": session}, {"src1": src})
    assert ok is False


def test_sync_one_delete_instance_no_start():
    cfg = _make_config()
    sm = SyncManager(fs=MagicMock(), index=MagicMock(), sources={}, config=cfg)
    op = PendingOp(uid="u1", source_id="src1", operation="delete_instance", instance_start=None)
    ok = sm._sync_one(op, {}, {}, {})
    assert ok is False


def test_sync_one_unknown_operation():
    cfg = _make_config()
    sm = SyncManager(fs=MagicMock(), index=MagicMock(), sources={}, config=cfg)
    op = PendingOp(uid="u1", source_id="src1", operation="unknown")
    cal_info = MagicMock()
    session = MagicMock()
    src = CalendarSource(id="src1", name="Cal1", account_name="acc1")
    ok = sm._sync_one(op, {"src1": cal_info}, {"acc1": session}, {"src1": src})
    assert ok is False


# ----------------------------------------------------------------------
# _try_connect_missing
# ----------------------------------------------------------------------

def test_try_connect_missing_no_accounts():
    cfg = _make_config()
    sm = SyncManager(fs=MagicMock(), index=MagicMock(), sources={}, config=cfg)
    result = sm._try_connect_missing()
    assert result == {"sessions": {}, "calendars": {}}

# ----------------------------------------------------------------------
# _do_connect_all (worker)
# ----------------------------------------------------------------------

def test_do_connect_all_caldav(tmp_path):
    cfg = _make_config()
    fs = EventFS(base=tmp_path)
    idx = EventIndex()
    sources = {}
    acc = MagicMock()
    acc.name = "Personal"
    acc.url = "https://nc.example.com"
    acc.username = "user"
    acc.get_password = MagicMock(return_value="pw")
    cfg.nextcloud_accounts = [acc]
    sm = SyncManager(fs=fs, index=idx, sources=sources, config=cfg)

    with patch("backend.sync_manager.caldav_connect") as mock_connect, \
         patch("backend.sync_manager.caldav_list_calendars") as mock_list, \
         patch("backend.sync_manager.caldav_fetch_events") as mock_fetch:
        mock_connect.return_value = MagicMock()
        mock_cal = MagicMock()
        mock_cal.id = "cal1"
        mock_cal.name = "Cal1"
        mock_cal.color = "#ff0000"
        mock_cal.writable = True
        mock_list.return_value = [mock_cal]
        mock_fetch.return_value = [(BASIC_ICAL, "/cal/ev1.ics")]
        result = sm._do_connect_all()

    sid = "caldav:Personal:cal1"
    assert sid in result["calendars"]
    # _do_connect_all no longer fetches events — it only discovers calendars.
    # Event fetching is done by the subsequent refresh in the queue.
    assert sid in result["sources"]
    assert result["sources"][sid]["name"] == "Cal1"


def test_do_connect_all_no_accounts(tmp_path):
    cfg = _make_config()
    fs = EventFS(base=tmp_path)
    idx = EventIndex()
    sm = SyncManager(fs=fs, index=idx, sources={}, config=cfg)
    result = sm._do_connect_all()
    assert result["sessions"] == {}
    assert result["calendars"] == {}
    assert result["sync_start"] is not None
    assert result["sync_end"] is not None


# ----------------------------------------------------------------------
# _do_sync_pending (worker)
# ----------------------------------------------------------------------

def test_do_sync_pending_success(tmp_path):
    cfg = _make_config()
    fs = EventFS(base=tmp_path)
    idx = EventIndex()
    sources = {"src1": CalendarSource(id="src1", name="Cal1", account_name="acc1")}
    sm = SyncManager(fs=fs, index=idx, sources=sources, config=cfg)
    fs.add_pending(PendingOp(uid="u1", source_id="src1", operation="create"))
    with patch.object(sm, "_sync_one", return_value=True):
        result = sm._do_sync_pending({}, {}, sources)
    assert result["success"] == 1
    assert result["done_uids"] == ["u1"]


def test_do_sync_pending_failure_counts(tmp_path):
    cfg = _make_config()
    fs = EventFS(base=tmp_path)
    idx = EventIndex()
    sources = {"src1": CalendarSource(id="src1", name="Cal1", account_name="acc1")}
    sm = SyncManager(fs=fs, index=idx, sources=sources, config=cfg)
    fs.add_pending(PendingOp(uid="u1", source_id="src1", operation="delete"))
    with patch.object(sm, "_sync_one", return_value=False):
        result = sm._do_sync_pending({}, {}, sources)
    assert result["success"] == 0
    assert result["failed"] == 1
    assert result["done_uids"] == []


# ----------------------------------------------------------------------
# _refresh_ics (worker)
# ----------------------------------------------------------------------

ICS_ICAL = (
    "BEGIN:VCALENDAR\r\n"
    "VERSION:2.0\r\n"
    "PRODID:-//Test//\r\n"
    "BEGIN:VEVENT\r\n"
    "DTSTART:20260101T100000Z\r\n"
    "DTEND:20260101T110000Z\r\n"
    "SUMMARY:Holiday\r\n"
    "UID:ics-1\r\n"
    "END:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)


def test_refresh_ics_ok(tmp_path):
    cfg = _make_config()
    fs = EventFS(base=tmp_path)
    idx = EventIndex()
    sources = {"ics:Holidays": CalendarSource(id="ics:Holidays", name="Holidays", source_type="ics")}
    sm = SyncManager(fs=fs, index=idx, sources=sources, config=cfg)
    sm.register_ics("ics:Holidays", "https://example.com/h.ics")
    with patch("backend.sync_manager.ics_fetch", return_value=ICS_ICAL), \
         patch("backend.sync_manager.ics_parse_events", return_value=[ICS_ICAL]):
        result = sm._refresh_ics("ics:Holidays", datetime.now(UTC), None, None)
    assert result["ok"] is True
    events = fs.list_events("ics:Holidays")
    assert len(events) == 1


def test_refresh_ics_fetch_fails(tmp_path):
    cfg = _make_config()
    fs = EventFS(base=tmp_path)
    idx = EventIndex()
    sources = {"ics:Holidays": CalendarSource(id="ics:Holidays", name="Holidays", source_type="ics")}
    sm = SyncManager(fs=fs, index=idx, sources=sources, config=cfg)
    sm.register_ics("ics:Holidays", "https://example.com/h.ics")
    with patch("backend.sync_manager.ics_fetch", return_value=None):
        result = sm._refresh_ics("ics:Holidays", datetime.now(UTC), None, None)
    assert result["ok"] is False


# ----------------------------------------------------------------------
# Regression: failed refresh must not record a sync window (would make
# is_range_covered claim the stale cache is fresh and suppress refetch)
# ----------------------------------------------------------------------

def test_on_refresh_done_no_window_on_failed_refresh():
    cfg = _make_config()
    sm = SyncManager(fs=MagicMock(), index=MagicMock(), sources={}, config=cfg)
    sm._on_refresh_done({
        "synced": [],
        "sessions": {}, "calendars": {}, "sources": {},
        "source_last_success": {}, "source_last_attempt": {},
        "last_sync_time": None,
        "sync_start": datetime(2025, 12, 1, tzinfo=UTC),
        "sync_end": datetime(2026, 3, 1, tzinfo=UTC),
    })
    assert sm._sync_windows == []


def test_on_refresh_done_records_window_when_synced():
    cfg = _make_config()
    sm = SyncManager(fs=MagicMock(), index=MagicMock(), sources={}, config=cfg)
    sm._on_refresh_done({
        "synced": ["src1"],
        "sessions": {}, "calendars": {}, "sources": {},
        "source_last_success": {}, "source_last_attempt": {},
        "last_sync_time": None,
        "sync_start": datetime(2025, 12, 1, tzinfo=UTC),
        "sync_end": datetime(2026, 3, 1, tzinfo=UTC),
    })
    assert len(sm._sync_windows) == 1


# ----------------------------------------------------------------------
# Regression: last_sync_time of a source must not be wiped when that
# source was not part of the refresh round's successes
# ----------------------------------------------------------------------

def test_on_refresh_done_preserves_last_sync_time_for_unsynced_source():
    cfg = _make_config()
    prev = datetime(2026, 1, 1, tzinfo=UTC)
    src = CalendarSource(id="src1", name="Cal1")
    src.last_sync_time = prev
    sources = {"src1": src}
    sm = SyncManager(fs=MagicMock(), index=MagicMock(), sources=sources, config=cfg)
    sm._on_refresh_done({
        "synced": ["src1"],
        "sessions": {}, "calendars": {},
        "sources": {"src1": {"color": "#ff0000", "read_only": False, "is_outdated": False}},
        "source_last_success": {},  # src1 not in this round's successes
        "source_last_attempt": {},
        "last_sync_time": None,
        "sync_start": None, "sync_end": None,
    })
    assert src.last_sync_time == prev


# ----------------------------------------------------------------------
# _enqueue_refresh merge — pending slot must not drop viewport windows
# ----------------------------------------------------------------------

def test_enqueue_refresh_merges_windows_union():
    """A second enqueue must not discard the first request's window."""
    cfg = _make_config()
    sm = SyncManager(fs=MagicMock(), index=MagicMock(), sources={}, config=cfg)
    sm._sync_running = True  # force queueing, no dispatch

    past_start = datetime(2026, 1, 1)
    past_end = datetime(2026, 2, 1)
    sm._enqueue_refresh(None, past_start, past_end)

    now_start = datetime(2026, 6, 1)
    now_end = datetime(2026, 12, 1)
    sm._enqueue_refresh(None, now_start, now_end)

    assert sm._pending_refresh is not None
    source_id, w_start, w_end = sm._pending_refresh
    assert source_id is None
    assert w_start == past_start   # union keeps the far-past start
    assert w_end == now_end        # and the latest end


def test_enqueue_refresh_none_window_resolves_to_default():
    """A None window becomes now +/- SYNC_WINDOW_* before merging."""
    from backend.event import SYNC_WINDOW_PAST_DAYS, SYNC_WINDOW_FUTURE_DAYS
    cfg = _make_config()
    sm = SyncManager(fs=MagicMock(), index=MagicMock(), sources={}, config=cfg)
    sm._sync_running = True

    past_start = datetime(2026, 1, 1)
    past_end = datetime(2026, 2, 1)
    sm._enqueue_refresh(None, past_start, past_end)
    sm._enqueue_refresh(None)  # due-timer style request, no window

    source_id, w_start, w_end = sm._pending_refresh
    assert w_start == past_start  # past window survived the None-window enqueue
    assert w_end >= datetime.now() + timedelta(days=SYNC_WINDOW_FUTURE_DAYS - 1)


def test_enqueue_refresh_all_sources_wins_over_specific_source():
    cfg = _make_config()
    sm = SyncManager(fs=MagicMock(), index=MagicMock(), sources={}, config=cfg)
    sm._sync_running = True

    sm._enqueue_refresh("src1", datetime(2026, 1, 1), datetime(2026, 2, 1))
    sm._enqueue_refresh(None, datetime(2026, 6, 1), datetime(2026, 7, 1))

    source_id, w_start, w_end = sm._pending_refresh
    assert source_id is None
    assert w_start == datetime(2026, 1, 1)
    assert w_end == datetime(2026, 7, 1)


def test_refresh_due_forwards_window():
    """refresh_due_in_background passes the given window to the queue."""
    cfg = _make_config(refresh_interval=300)
    sources = {"src1": CalendarSource(id="src1", name="Cal1")}
    sm = SyncManager(fs=MagicMock(), index=MagicMock(), sources=sources, config=cfg)
    sm._sync_running = True

    start = datetime(2026, 1, 1)
    end = datetime(2026, 2, 1)
    sm.refresh_due_in_background(start, end)

    assert sm._pending_refresh == (None, start, end)
