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