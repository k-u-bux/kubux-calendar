"""Tests for the kubux-caldav-send CLI tool."""

from io import StringIO
from pathlib import Path
from unittest import mock

import pytest

from backend.config import Config, NextcloudAccount
from backend.network_ops import CalendarInfo

from cli import caldav_send as cs


# =====================================================================
# Config fixture helpers
# =====================================================================

def make_config(accounts=(("Primary",),)) -> mock.Mock:
    """Build a config whose nextcloud_accounts mirror *accounts*."""
    cfg = mock.Mock()
    cfg.password_program = "/usr/bin/pass"
    cfg.nextcloud_accounts = [
        NextcloudAccount(name=n, url=f"https://nc.example.com/{n}",
                         username="user", password_key=f"k{accounts[0][0]}", _password="s3cret")
        for n, *_ in accounts
    ]
    return cfg


def make_account(name="Primary"):
    return NextcloudAccount(
        name=name, url=f"https://nc.example.com/{name}",
        username="user", password_key="key", _password="s3cret",
    )


def make_calendar(id_, name=None, writable=True):
    return CalendarInfo(id=id_, name=name or id_, color="", url=f"https://x/{id_}/",
                        account_name="Primary", writable=writable)


SAMPLE_ICAL = (
    "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Test//\r\n"
    "BEGIN:VEVENT\r\nUID:u@example.com\r\nSUMMARY:Test\r\n"
    "DTSTART:20260101T100000Z\r\nDTEND:20260101T110000Z\r\n"
    "END:VEVENT\r\nEND:VCALENDAR\r\n"
)
NORMALIZED_ICAL = SAMPLE_ICAL.replace("\r\n", "\n")


# =====================================================================
# read_input
# =====================================================================

class TestReadInput:
    def test_reads_from_path(self, tmp_path: Path):
        p = tmp_path / "e.ics"
        p.write_bytes(SAMPLE_ICAL.encode())
        assert cs.read_input(str(p)) == NORMALIZED_ICAL

    def test_strips_bom(self, tmp_path: Path):
        p = tmp_path / "e.ics"
        p.write_bytes(b"\xef\xbb\xbf" + SAMPLE_ICAL.encode())
        assert cs.read_input(str(p)) == NORMALIZED_ICAL

    def test_reads_from_stdin_stream(self):
        stream = StringIO(SAMPLE_ICAL)
        assert cs.read_input(None, stream) == NORMALIZED_ICAL

    def test_invalid_utf8_raises(self, tmp_path: Path):
        p = tmp_path / "bad.ics"
        p.write_bytes(b"\xff\xfe\x00BAD")
        with pytest.raises(cs.UsageError):
            cs.read_input(str(p))

    def test_normalizes_crlf_and_cr(self):
        stream = StringIO("A\r\nB\rC")
        assert cs.read_input(None, stream) == "A\nB\nC"


# =====================================================================
# resolve_account
# =====================================================================

class TestResolveAccount:
    def test_exact_name(self):
        cfg = make_config([("Primary",), ("Secondary",)])
        assert cs.resolve_account(cfg, "Primary").name == "Primary"

    def test_with_nextcloud_prefix(self):
        cfg = make_config([("Primary",), ("Secondary",)])
        assert cs.resolve_account(cfg, "Nextcloud.Primary").name == "Primary"

    def test_case_insensitive(self):
        cfg = make_config([("Primary",)])
        assert cs.resolve_account(cfg, "primary").name == "Primary"

    def test_empty_with_single_account(self):
        cfg = make_config([("Primary",)])
        assert cs.resolve_account(cfg, "").name == "Primary"

    def test_empty_with_multiple_raises(self):
        cfg = make_config([("A",), ("B",)])
        with pytest.raises(cs.UsageError):
            cs.resolve_account(cfg, "")

    def test_unknown_account_raises(self):
        from configparser import Error
        cfg = make_config([("Primary",)])
        with pytest.raises(cs.UsageError, match="unknown Nextcloud account"):
            cs.resolve_account(cfg, "Nope")


# =====================================================================
# select_calendar
# =====================================================================

class TestSelectCalendar:
    def setup_method(self):
        self.cals = [
            make_calendar("beruflich"),
            make_calendar("private", name="Mein Kalender"),
        ]

    def test_match_by_id(self):
        assert cs.select_calendar(self.cals, "beruflich").id == "beruflich"

    def test_match_by_display_name(self):
        assert cs.select_calendar(self.cals, "Mein Kalender").name == "Mein Kalender"

    def test_case_insensitive(self):
        assert cs.select_calendar(self.cals, "BERUFLICH").id == "beruflich"

    def test_missing_raises_with_available(self):
        with pytest.raises(cs.UsageError, match="no calendar named 'x'"):
            cs.select_calendar(self.cals, "x")

    def test_prefers_writable_on_id_collision(self):
        # Two calendars with the same id but different display names.
        ro = make_calendar("dup", name="Read Only", writable=False)
        rw = make_calendar("dup", name="Editable", writable=True)
        picked = cs.select_calendar([ro, rw], "dup")
        # Both match "dup" by id; the writable one is preferred.
        assert picked.writable is True


# =====================================================================
# run — end to end with mocks, no real network
# =====================================================================

class TestRun:
    def test_success(self):
        cal_info = make_calendar("beruflich", writable=True)
        cfg = make_config([("Primary",)])

        with mock.patch.object(cs, "caldav_connect") as connect, \
             mock.patch.object(cs, "caldav_list_calendars", return_value=[cal_info]) as lst, \
             mock.patch.object(cs, "caldav_save_event", return_value=True) as save:
            msg = cs.run(cfg, "Primary", "beruflich", SAMPLE_ICAL)

        assert "beruflich" in msg
        connect.assert_called_once()
        lst.assert_called_once()
        save.assert_called_once_with(cal_info, SAMPLE_ICAL)

    def test_rejects_readonly_calendar(self):
        cal_info = make_calendar("beruflich", writable=False)
        cfg = make_config([("Primary",)])

        with mock.patch.object(cs, "caldav_connect"), \
             mock.patch.object(cs, "caldav_list_calendars", return_value=[cal_info]):
            with pytest.raises(cs.UsageError, match="not writable"):
                cs.run(cfg, "Primary", "beruflich", SAMPLE_ICAL)

    def test_save_failure_raises(self):
        cal_info = make_calendar("beruflich", writable=True)
        cfg = make_config([("Primary",)])

        with mock.patch.object(cs, "caldav_connect"), \
             mock.patch.object(cs, "caldav_list_calendars", return_value=[cal_info]):
            with pytest.raises(cs.UsageError, match="failed to save"):
                cs.run(cfg, "Primary", "beruflich", SAMPLE_ICAL)


# =====================================================================
# main — top level with a real (temp) config, network fully mocked
# =====================================================================

def write_config(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "kubux-calendar.toml"
    p.write_text(body)
    return p


class TestMain:
    def test_missing_calendar_arg_fails(self):
        assert cs.main(["--account", "Primary"], stdin=StringIO(SAMPLE_ICAL)) != 0

    def test_full_send_via_stdin(self, tmp_path):
        cfg_path = write_config(tmp_path, f"""
[General]
password_program = "echo"
[Nextcloud.Primary]
url = "https://nc.example.com"
username = "user"
password_key = "k/calendar"
""")
        cal_info = make_calendar("beruflich", writable=True)
        with mock.patch.object(cs, "caldav_connect") as connect, \
             mock.patch.object(cs, "caldav_list_calendars", return_value=[cal_info]), \
             mock.patch.object(cs, "caldav_save_event", return_value=True) as save:
            rc = cs.main(["--config", str(cfg_path),
                          "--account", "Nextcloud.Primary",
                          "--calendar", "beruflich"],
                         stdin=StringIO(SAMPLE_ICAL))
        assert rc == 0
        assert save.called
