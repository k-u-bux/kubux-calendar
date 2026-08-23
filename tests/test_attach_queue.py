"""Tests for the kubux-calendar-attach XDG handler (parsing + queuing) and
the pending-changes file locking added to EventFS."""

import os
import threading
from datetime import datetime
from io import StringIO
from pathlib import Path
from unittest import mock

import pytest
import pytz

from backend.event_fs import EventFS, PendingOp
from backend.network_ops import CalendarInfo

from cli import calendar_attach as ca

SAMPLE_ICS = (
    "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Attach//\r\n"
    "BEGIN:VEVENT\r\nUID:att-1@example.com\r\nSUMMARY:Team Meeting\r\n"
    "DTSTART:20260810T090000Z\r\nDTEND:20260810T100000Z\r\n"
    "LOCATION:Room 1\r\nDESCRIPTION:Weekly standup\r\n"
    "END:VEVENT\r\nEND:VCALENDAR\r\n"
)
NORMALIZED = SAMPLE_ICS.replace("\r\n", "\n")


# =====================================================================
# read_input
# =====================================================================

class TestReadInput:
    def test_from_path(self, tmp_path):
        p = tmp_path / "a.ics"
        p.write_bytes(SAMPLE_ICS.encode())
        assert ca.read_input(str(p)) == NORMALIZED

    def test_from_stdin(self):
        assert ca.read_input(None, StringIO(SAMPLE_ICS)) == NORMALIZED


# =====================================================================
# parse_ics
# =====================================================================

class TestParseIcs:
    def test_roundtrip(self):
        canonical, ev = ca.parse_ics(SAMPLE_ICS, "src", config_tz=None)
        assert ev.uid == "att-1@example.com"
        assert ev.summary == "Team Meeting"
        assert ev.location == "Room 1"
        assert ev.description == "Weekly standup"
        # Original content preserved in the canonical text.
        assert "SUMMARY:Team Meeting" in canonical
        assert "UID:att-1@example.com" in canonical

    def test_injects_uid_when_missing(self):
        no_uid = NORMALIZED.replace("UID:att-1@example.com\n", "")
        canonical, ev = ca.parse_ics(no_uid, "src")
        assert ev.uid
        assert "UID:" in canonical

    def test_no_vevent_raises(self):
        with pytest.raises(ValueError, match="no VEVENT"):
            ca.parse_ics("BEGIN:VCALENDAR\nEND:VCALENDAR\n", "src")


# =====================================================================
# build_pending_op
# =====================================================================

class TestBuildPendingOp:
    def test_no_edits_keeps_original(self):
        op = ca.build_pending_op("caldav:Primary:beruflich", NORMALIZED, "att-1@example.com")
        assert op.operation == "create"
        assert op.uid == "att-1@example.com"
        assert op.source_id == "caldav:Primary:beruflich"
        assert op.ical_data == NORMALIZED

    def test_with_edits_rebuilds_and_preserves_uid(self):
        import pytz
        start = pytz.UTC.localize(__import__("datetime").datetime(2026, 9, 1, 9, 0))
        end = pytz.UTC.localize(__import__("datetime").datetime(2026, 9, 1, 10, 0))
        edits = {"summary": "Changed", "start": start, "end": end,
                 "all_day": False, "location": "Zoom", "description": "New desc"}
        op = ca.build_pending_op("caldav:Primary:beruflich", NORMALIZED,
                                 "att-1@example.com", edits)
        assert "SUMMARY:Changed" in op.ical_data
        assert "UID:att-1@example.com" in op.ical_data
        assert "LOCATION:Zoom" in op.ical_data.replace("\r\n", "\n")


# =====================================================================
# enqueue_pending + EventFS locking
# =====================================================================

def make_fs(tmp_path: Path) -> EventFS:
    return EventFS(base=tmp_path / "v2")


class TestEnqueue:
    def test_writes_pending_file(self, tmp_path):
        base = tmp_path / "v2"
        op = PendingOp(uid="u1", source_id="caldav:A:b", operation="create",
                       ical_data=NORMALIZED)
        ca.enqueue_pending(op, config_tz=None, base=base)
        loaded = make_fs(tmp_path).load_pending()
        assert len(loaded) == 1
        assert loaded[0].uid == "u1"
        assert loaded[0].source_id == "caldav:A:b"

    def test_concurrent_add_pending_loses_nothing(self, tmp_path):
        """Two threads adding distinct events must both survive (locking)."""
        base = tmp_path / "v2"
        N = 60

        def worker(prefix):
            fs = EventFS(base=base)
            for i in range(N):
                fs.add_pending(PendingOp(
                    uid=f"{prefix}-{i}", source_id="caldav:A:b",
                    operation="create", ical_data=NORMALIZED,
                ))

        t1 = threading.Thread(target=worker, args=("t1",))
        t2 = threading.Thread(target=worker, args=("t2",))
        t1.start(); t2.start(); t1.join(); t2.join()

        loaded = make_fs(tmp_path).load_pending()
        uids = {op.uid for op in loaded}
        assert len(uids) == 2 * N  # all 120 survived — no lost update

    def test_concurrent_across_processes(self, tmp_path):
        """Same guarantee across real OS processes (subprocess => separate
        Python interpreters, so it genuinely exercises the flock)."""
        import subprocess
        import sys
        import json

        base = tmp_path / "v2"
        N = 40
        # Each worker process adds N distinct-uid pending ops.
        code = (
            "import sys, json, os\n"
            "import sys\n"
            "from backend.event_fs import EventFS, PendingOp\n"
            "base=sys.argv[1]; prefix=sys.argv[2]; n=int(sys.argv[3])\n"
            "fs=EventFS(base=base)\n"
            "for i in range(n):\n"
            "    fs.add_pending(PendingOp(uid=f'{prefix}-{i}', source_id='caldav:A:b', operation='create', ical_data='BEGIN:VCALENDAR\\nEND:VCALENDAR\\n'))\n"
        )
        env = dict(os.environ)
        env["PYTHONPATH"] = os.getcwd()
        procs = [
            subprocess.Popen([sys.executable, "-c", code, str(base), p, str(N)],
                             env=env)
            for p in ("p1", "p2")
        ]
        for p in procs:
            assert p.wait(timeout=60) == 0

        loaded = make_fs(tmp_path).load_pending()
        assert len({op.uid for op in loaded}) == 2 * N


# =====================================================================
# queue_from_args (non-interactive) — mocks, no real network
# =====================================================================

class TestQueueFromArgs:
    def test_source_id_format(self, tmp_path):
        cal = CalendarInfo(id="beruflich", name="beruflich", color="",
                           url="https://x/beruflich/", account_name="Primary",
                           writable=True)
        account = mock.Mock()
        account.name = "Primary"
        account.url = "https://nc.example.com"
        account.username = "user"
        account.get_password.return_value = "pw"

        cfg = mock.Mock()
        cfg.timezone = "UTC"
        cfg.password_program = "/usr/bin/pass"

        base = tmp_path / "v2"
        with mock.patch.object(ca, "resolve_account", return_value=account), \
             mock.patch.object(ca, "caldav_connect", return_value=mock.Mock()), \
             mock.patch.object(ca, "caldav_list_calendars", return_value=[cal]):
            msg = ca.queue_from_args(SAMPLE_ICS, "Nextcloud.Primary/beruflich",
                                     cfg, base=base)

        assert "beruflich" in msg
        op = make_fs(tmp_path).load_pending()[0]
        # The GUI keys CalDAV sources as "caldav:<account>:<calendar_id>".
        assert op.source_id == "caldav:Primary:beruflich"

    def test_bad_spec_raises(self):
        cfg = mock.Mock()
        cfg.timezone = "UTC"
        with pytest.raises(ValueError, match="<account>/<calendar>"):
            ca.queue_from_args(SAMPLE_ICS, "no-slash", cfg)


# =====================================================================
# AttachDialog timezone handling (Qt, offscreen)
# =====================================================================

_CAL = None


def _make_dialog(qapp, ics, timezone="Europe/Berlin"):
    import pytz
    from backend.event import CalendarSource
    global _CAL
    if _CAL is None:
        _CAL = CalendarSource(id="cal-1", name="beruflich", account_name="P",
                              source_type="caldav", read_only=False)
    config_tz = pytz.timezone(timezone)
    base, ev = ca.parse_ics(ics, config_tz=config_tz)
    cfg = type("Cfg", (), {"timezone": timezone, "password_program": "echo"})()
    d = ca.AttachDialog(cfg, base, ev, [_CAL])
    qapp.processEvents()
    return d


COMMON_ICS = (
    "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//t//\n"
    "BEGIN:VEVENT\nUID:u\nSUMMARY:S\nDTSTART:__START__\nDTEND:__END__\n"
    "END:VEVENT\nEND:VCALENDAR\n"
)


@pytest.mark.skipif(not ca._QT_OK, reason="PySide6 not available")
class TestAttachDialogTz:
    def test_floating_defaults_to_local_and_preserves_wall(self, qapp):
        ics = COMMON_ICS.replace("__START__", "20260810T090000") \
                        .replace("__END__", "20260810T100000")
        d = _make_dialog(qapp, ics)
        try:
            assert d._display_tzid == "Europe/Berlin"
            assert d._tz_combo.currentText() == "Europe/Berlin"
            # Time taken as-is: 09:00 local wall clock.
            assert d._start_edit.dateTime().toPython().strftime("%H:%M") == "09:00"
            edits = d._collect_edits()
            assert edits["start"].tzinfo.zone == "Europe/Berlin"
            # 09:00 (CEST, +2) == 07:00 UTC.
            assert edits["start"].astimezone(pytz.UTC).replace(tzinfo=None) == \
                datetime(2026, 8, 10, 7, 0)
        finally:
            d.close()

    def test_changing_tz_adjusts_wall_clock_same_instant(self, qapp):
        ics = COMMON_ICS.replace("__START__", "20260810T090000") \
                        .replace("__END__", "20260810T100000")
        d = _make_dialog(qapp, ics)
        try:
            d._tz_combo.setCurrentText("UTC")
            qapp.processEvents()
            assert d._display_tzid == "UTC"
            # Same instant -> wall clock moves to 07:00 (Berlin +2 CEST).
            assert d._start_edit.dateTime().toPython().strftime("%H:%M") == "07:00"
            edits = d._collect_edits()
            assert edits["start"].tzinfo == pytz.UTC
            assert edits["start"].replace(tzinfo=None) == datetime(2026, 8, 10, 7, 0)
        finally:
            d.close()

    def test_tzid_event_is_respected(self, qapp):
        ics = ("BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//t//\n"
               "BEGIN:VEVENT\nUID:t1\nSUMMARY:Tz\n"
               "DTSTART;TZID=Europe/Berlin:20260810T090000\n"
               "DTEND;TZID=Europe/Berlin:20260810T100000\n"
               "END:VEVENT\nEND:VCALENDAR\n")
        d = _make_dialog(qapp, ics)
        try:
            assert d._display_tzid == "Europe/Berlin"
            edits = d._collect_edits()
            assert edits["start"].tzinfo.zone == "Europe/Berlin"
            # 09:00 BST/Berlin == 07:00 UTC.
            assert edits["start"].astimezone(pytz.UTC).replace(tzinfo=None) == \
                datetime(2026, 8, 10, 7, 0)
        finally:
            d.close()

    def test_utc_event_shows_and_saves_utc(self, qapp):
        ics = COMMON_ICS.replace("__START__", "20260810T090000Z") \
                        .replace("__END__", "20260810T100000Z")
        d = _make_dialog(qapp, ics)
        try:
            assert d._display_tzid == "UTC"
            assert d._start_edit.dateTime().toPython().strftime("%H:%M") == "09:00"
            assert d._collect_edits()["start"].tzinfo == pytz.UTC
        finally:
            d.close()
