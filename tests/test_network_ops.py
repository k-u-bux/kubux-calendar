"""Tests for backend/network_ops.py."""

from unittest.mock import patch, MagicMock
from backend.network_ops import ics_parse_events, ics_fetch


# ----------------------------------------------------------------------
# ics_parse_events — pure logic
# ----------------------------------------------------------------------

SINGLE_EVENT_ICAL = (
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


def test_ics_parse_events_single():
    results = ics_parse_events(SINGLE_EVENT_ICAL)
    assert len(results) == 1
    assert "UID:uid-1" in results[0]
    assert "BEGIN:VCALENDAR" in results[0]
    assert "END:VCALENDAR" in results[0]


MULTI_EVENT_ICAL = (
    "BEGIN:VCALENDAR\r\n"
    "VERSION:2.0\r\n"
    "PRODID:-//Test//\r\n"
    "BEGIN:VEVENT\r\n"
    "DTSTART:20260101T100000Z\r\n"
    "SUMMARY:A\r\n"
    "UID:uid-a\r\n"
    "END:VEVENT\r\n"
    "BEGIN:VEVENT\r\n"
    "DTSTART:20260101T120000Z\r\n"
    "SUMMARY:B\r\n"
    "UID:uid-b\r\n"
    "END:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)


def test_ics_parse_events_multiple():
    results = ics_parse_events(MULTI_EVENT_ICAL)
    assert len(results) == 2
    summaries = []
    for r in results:
        assert "BEGIN:VCALENDAR" in r
        assert "END:VCALENDAR" in r
        if "uid-a" in r:
            summaries.append("A")
        if "uid-b" in r:
            summaries.append("B")
    assert sorted(summaries) == ["A", "B"]


def test_ics_parse_events_no_vevents():
    ical = (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "PRODID:-//Test//\r\n"
        "END:VCALENDAR\r\n"
    )
    results = ics_parse_events(ical)
    assert results == []


def test_ics_parse_events_garbage():
    results = ics_parse_events("not valid ical")
    assert results == []


def test_ics_parse_events_skips_non_vevent():
    """VTODO and VJOURNAL should be skipped, only VEVENT returned."""
    ical = (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "PRODID:-//Test//\r\n"
        "BEGIN:VTODO\r\n"
        "SUMMARY:Todo\r\n"
        "UID:todo-1\r\n"
        "END:VTODO\r\n"
        "BEGIN:VEVENT\r\n"
        "DTSTART:20260101T100000Z\r\n"
        "SUMMARY:Event\r\n"
        "UID:ev-1\r\n"
        "END:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    )
    results = ics_parse_events(ical)
    assert len(results) == 1
    assert "uid:ev-1" in results[0].lower()


# ----------------------------------------------------------------------
# ics_fetch — mock requests
# ----------------------------------------------------------------------

def test_ics_fetch_success():
    with patch("backend.network_ops.requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.text = SINGLE_EVENT_ICAL
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = ics_fetch("https://example.com/cal.ics")
        assert result == SINGLE_EVENT_ICAL
        mock_get.assert_called_once_with(
            "https://example.com/cal.ics", timeout=30,
            headers={"User-Agent": "Kubux-Calendar/2.0",
                     "Accept": "text/calendar"},
        )


def test_ics_fetch_http_error():
    with patch("backend.network_ops.requests.get") as mock_get:
        import requests
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = requests.HTTPError("404")
        mock_get.return_value = mock_resp

        result = ics_fetch("https://example.com/cal.ics")
        assert result is None


def test_ics_fetch_connection_error():
    with patch("backend.network_ops.requests.get") as mock_get:
        import requests
        mock_get.side_effect = requests.ConnectionError("timeout")

        result = ics_fetch("https://example.com/cal.ics")
        assert result is None


def test_ics_fetch_timeout():
    with patch("backend.network_ops.requests.get") as mock_get:
        import requests
        mock_get.side_effect = requests.Timeout("timed out")

        result = ics_fetch("https://example.com/cal.ics")
        assert result is None


# ----------------------------------------------------------------------
# caldav_connect — mock caldav
# ----------------------------------------------------------------------

def test_caldav_connect():
    with patch("backend.network_ops.caldav.DAVClient") as mock_client:
        mock_instance = MagicMock()
        mock_principal = MagicMock()
        mock_instance.principal.return_value = mock_principal
        mock_client.return_value = mock_instance

        from backend.network_ops import caldav_connect
        session = caldav_connect("https://nc.example.com", "user", "pass", "Personal")
        assert session.account_name == "Personal"
        assert session.principal is mock_principal


# ----------------------------------------------------------------------
# _check_writable
# ----------------------------------------------------------------------

def test_check_writable():
    with patch("backend.network_ops.caldav.DAVClient") as mock_client:
        from backend.network_ops import caldav_connect, caldav_list_calendars
        from backend.network_ops import _check_writable

        mock_cal = MagicMock()
        mock_cal.client = MagicMock()
        mock_cal.client.propfind.return_value = MagicMock()

        # Test writable
        mock_cal.client.propfind.return_value.raw = "<d:write></d:write>"
        assert _check_writable(mock_cal) is True

        # Test not writable
        mock_cal.client.propfind.return_value.raw = "<d:read></d:read>"
        assert _check_writable(mock_cal) is False


# ----------------------------------------------------------------------
# caldav_list_calendars
# ----------------------------------------------------------------------

def test_caldav_list_calendars():
    with patch("backend.network_ops.caldav.DAVClient") as mock_client:
        from backend.network_ops import DAVSession, caldav_list_calendars

        mock_principal = MagicMock()
        mock_cal = MagicMock()
        mock_cal.name = "My Calendar"
        mock_cal.url = "https://nc.example.com/remote.php/dav/calendars/user/abc123/"
        mock_cal.get_properties.return_value = {"color": "#ff0000"}
        mock_cal.client = MagicMock()
        mock_cal.client.propfind.return_value = MagicMock()
        mock_cal.client.propfind.return_value.raw = "<d:write></d:write>"
        mock_principal.calendars.return_value = [mock_cal]

        session = DAVSession(client=MagicMock(), principal=mock_principal, account_name="Personal")
        calendars = caldav_list_calendars(session)
        assert len(calendars) == 1
        assert calendars[0].name == "My Calendar"
        assert calendars[0].color == "#ff0000"
        assert calendars[0].id == "abc123"
        assert calendars[0].writable is True
        assert calendars[0].account_name == "Personal"


# ----------------------------------------------------------------------
# caldav_save_event
# ----------------------------------------------------------------------

def test_caldav_save_event():
    from backend.network_ops import caldav_save_event, CalendarInfo

    cal_info = CalendarInfo(
        id="cal1", name="Cal1", color="", url="",
        account_name="Personal", writable=True,
    )
    cal_info._caldav_cal = MagicMock()
    result = caldav_save_event(cal_info, SINGLE_EVENT_ICAL)
    assert result is True
    cal_info._caldav_cal.save_event.assert_called_once_with(SINGLE_EVENT_ICAL)


def test_caldav_save_event_not_writable():
    from backend.network_ops import caldav_save_event, CalendarInfo

    cal_info = CalendarInfo(
        id="cal1", name="Cal1", color="", url="",
        account_name="Personal", writable=False,
    )
    result = caldav_save_event(cal_info, SINGLE_EVENT_ICAL)
    assert result is False


def test_caldav_save_event_no_cal():
    from backend.network_ops import caldav_save_event, CalendarInfo

    cal_info = CalendarInfo(
        id="cal1", name="Cal1", color="", url="",
        account_name="Personal", writable=True,
    )
    cal_info._caldav_cal = None
    result = caldav_save_event(cal_info, SINGLE_EVENT_ICAL)
    assert result is False

# ----------------------------------------------------------------------
# caldav_fetch_events
# ----------------------------------------------------------------------

def test_caldav_fetch_events_success():
    from backend.network_ops import caldav_fetch_events, CalendarInfo
    cal_info = CalendarInfo(id="cal1", name="Cal1", color="", url="",
                            account_name="Personal", writable=True)
    mock_cal = MagicMock()
    mock_ev = MagicMock()
    mock_ev.data = SINGLE_EVENT_ICAL
    mock_ev.url = "https://nc.example.com/ev1.ics"
    mock_cal.search.return_value = [mock_ev]
    cal_info._caldav_cal = mock_cal
    result = caldav_fetch_events(cal_info,
                                 __import__("datetime").datetime(2026, 1, 1, tzinfo=__import__("pytz").UTC),
                                 __import__("datetime").datetime(2026, 1, 2, tzinfo=__import__("pytz").UTC))
    assert result is not None
    assert len(result) == 1
    assert result[0][0] == SINGLE_EVENT_ICAL
    assert result[0][1] == "https://nc.example.com/ev1.ics"


def test_caldav_fetch_events_no_cal():
    from backend.network_ops import caldav_fetch_events, CalendarInfo
    cal_info = CalendarInfo(id="cal1", name="Cal1", color="", url="",
                            account_name="Personal", writable=True)
    cal_info._caldav_cal = None
    result = caldav_fetch_events(cal_info,
                                 __import__("datetime").datetime(2026, 1, 1, tzinfo=__import__("pytz").UTC),
                                 __import__("datetime").datetime(2026, 1, 2, tzinfo=__import__("pytz").UTC))
    assert result is None


def test_caldav_fetch_events_exception_returns_none():
    from backend.network_ops import caldav_fetch_events, CalendarInfo
    cal_info = CalendarInfo(id="cal1", name="Cal1", color="", url="",
                            account_name="Personal", writable=True)
    mock_cal = MagicMock()
    mock_cal.search.side_effect = Exception("boom")
    cal_info._caldav_cal = mock_cal
    result = caldav_fetch_events(cal_info,
                                 __import__("datetime").datetime(2026, 1, 1, tzinfo=__import__("pytz").UTC),
                                 __import__("datetime").datetime(2026, 1, 2, tzinfo=__import__("pytz").UTC))
    assert result is None


# ----------------------------------------------------------------------
# caldav_delete_event
# ----------------------------------------------------------------------

def test_caldav_delete_event_success():
    from backend.network_ops import caldav_delete_event, CalendarInfo
    cal_info = CalendarInfo(id="cal1", name="Cal1", color="", url="",
                            account_name="Personal", writable=True)
    mock_cal = MagicMock()
    mock_ev = MagicMock()
    mock_cal.event_by_uid.return_value = mock_ev
    cal_info._caldav_cal = mock_cal
    assert caldav_delete_event(cal_info, "uid-1") is True
    mock_ev.delete.assert_called_once()


def test_caldav_delete_event_not_found():
    """Event already gone server-side — the delete's goal is achieved."""
    from backend.network_ops import caldav_delete_event, CalendarInfo
    cal_info = CalendarInfo(id="cal1", name="Cal1", color="", url="",
                            account_name="Personal", writable=True)
    mock_cal = MagicMock()
    mock_cal.event_by_uid.return_value = None
    cal_info._caldav_cal = mock_cal
    assert caldav_delete_event(cal_info, "uid-1") is True


def test_caldav_delete_event_not_found_exception():
    """NotFoundError from the caldav lib also counts as success."""
    from caldav.lib.error import NotFoundError
    from backend.network_ops import caldav_delete_event, CalendarInfo
    cal_info = CalendarInfo(id="cal1", name="Cal1", color="", url="",
                            account_name="Personal", writable=True)
    mock_cal = MagicMock()
    mock_cal.event_by_uid.side_effect = NotFoundError()
    cal_info._caldav_cal = mock_cal
    assert caldav_delete_event(cal_info, "uid-1") is True


def test_caldav_delete_event_other_exception_fails():
    from backend.network_ops import caldav_delete_event, CalendarInfo
    cal_info = CalendarInfo(id="cal1", name="Cal1", color="", url="",
                            account_name="Personal", writable=True)
    mock_cal = MagicMock()
    mock_cal.event_by_uid.side_effect = ConnectionError("boom")
    cal_info._caldav_cal = mock_cal
    assert caldav_delete_event(cal_info, "uid-1") is False



def test_caldav_delete_event_no_cal():
    from backend.network_ops import caldav_delete_event, CalendarInfo
    cal_info = CalendarInfo(id="cal1", name="Cal1", color="", url="",
                            account_name="Personal", writable=True)
    cal_info._caldav_cal = None
    assert caldav_delete_event(cal_info, "uid-1") is False


# ----------------------------------------------------------------------
# caldav_add_exdate
# ----------------------------------------------------------------------

def test_caldav_add_exdate_no_master_returns_false():
    """If no master VEVENT (only overrides), add_exdate returns False."""
    from backend.network_ops import caldav_add_exdate, CalendarInfo
    cal_info = CalendarInfo(id="cal1", name="Cal1", color="", url="",
                            account_name="Personal", writable=True)
    mock_cal = MagicMock()
    mock_ev = MagicMock()
    override_ical = (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//T//\r\n"
        "BEGIN:VEVENT\r\nUID:u1\r\nDTSTART:20260101T100000Z\r\n"
        "RECURRENCE-ID:20260101T100000Z\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    )
    mock_ev.data = override_ical
    mock_cal.event_by_uid.return_value = mock_ev
    cal_info._caldav_cal = mock_cal
    import pytz
    from datetime import datetime
    result = caldav_add_exdate(cal_info, "u1", datetime(2026, 1, 2, 10, 0, tzinfo=pytz.UTC))
    assert result is False


def test_caldav_add_exdate_no_cal_returns_false():
    from backend.network_ops import caldav_add_exdate, CalendarInfo
    cal_info = CalendarInfo(id="cal1", name="Cal1", color="", url="",
                            account_name="Personal", writable=True)
    cal_info._caldav_cal = None
    import pytz
    from datetime import datetime
    assert caldav_add_exdate(cal_info, "u1", datetime(2026, 1, 2, 10, 0, tzinfo=pytz.UTC)) is False


# ----------------------------------------------------------------------
# Regression: ics_fetch must respect the declared charset and only
# default to UTF-8 (RFC 5545) when none is declared
# ----------------------------------------------------------------------

def test_ics_fetch_respects_declared_charset():
    with patch("backend.network_ops.requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.headers = {"Content-Type": "text/calendar; charset=iso-8859-1"}
        mock_resp.encoding = "iso-8859-1"
        mock_resp.text = SINGLE_EVENT_ICAL
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = ics_fetch("https://example.com/cal.ics")
        assert result == SINGLE_EVENT_ICAL
        # Declared charset must not be overridden
        assert mock_resp.encoding == "iso-8859-1"


def test_ics_fetch_defaults_to_utf8_without_charset():
    with patch("backend.network_ops.requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.headers = {"Content-Type": "text/calendar"}
        mock_resp.encoding = None
        mock_resp.text = SINGLE_EVENT_ICAL
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = ics_fetch("https://example.com/cal.ics")
        assert result == SINGLE_EVENT_ICAL
        assert mock_resp.encoding == "utf-8"
