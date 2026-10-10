"""Tests for library/windows_tz.py and the places that consume it.

Outlook/Exchange stamp iCalendar documents with Windows zone keys
("W. Europe Standard Time"); they must be mapped to their standard name
wherever a tzid is exposed, resolved with pytz, or uploaded to a server.
"""

from datetime import datetime

import pytest
import pytz

import icalendar

from backend.event import ImmutableEvent, _parse_vevent
from library.timezone_utils import ensure_tz
from library.windows_tz import (
    WINDOWS_TO_IANA,
    standard_timezone_name,
    standardize_ics_timezones,
    timezone_for,
)

WINDOWS_KEY = "W. Europe Standard Time"
BERLIN = "Europe/Berlin"

# An Outlook/Exchange invitation: Windows key in both the VTIMEZONE block and
# the DTSTART/DTEND parameters.
OUTLOOK_ICS = (
    "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"
    "PRODID:-//Microsoft Corporation//Outlook 16.0 MIMEDIR//EN\r\n"
    "METHOD:REQUEST\r\n"
    "BEGIN:VTIMEZONE\r\nTZID:W. Europe Standard Time\r\n"
    "BEGIN:STANDARD\r\nDTSTART:16011028T030000\r\n"
    "RRULE:FREQ=YEARLY;BYDAY=-1SU;BYMONTH=10\r\n"
    "TZOFFSETFROM:+0200\r\nTZOFFSETTO:+0100\r\nEND:STANDARD\r\n"
    "BEGIN:DAYLIGHT\r\nDTSTART:16010325T020000\r\n"
    "RRULE:FREQ=YEARLY;BYDAY=-1SU;BYMONTH=3\r\n"
    "TZOFFSETFROM:+0100\r\nTZOFFSETTO:+0200\r\nEND:DAYLIGHT\r\n"
    "END:VTIMEZONE\r\n"
    "BEGIN:VEVENT\r\nUID:win-1@example.com\r\nSUMMARY:Outlook Meeting\r\n"
    "DTSTART;TZID=W. Europe Standard Time:20270630T123000\r\n"
    "DTEND;TZID=W. Europe Standard Time:20270630T140000\r\n"
    "END:VEVENT\r\nEND:VCALENDAR\r\n"
)

STANDARD_ICS = (
    "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//t//\r\n"
    "BEGIN:VEVENT\r\nUID:s-1\r\nSUMMARY:S\r\n"
    "DTSTART;TZID=Europe/Berlin:20270630T123000\r\n"
    "DTEND;TZID=Europe/Berlin:20270630T140000\r\n"
    "END:VEVENT\r\nEND:VCALENDAR\r\n"
)


# =====================================================================
# the table
# =====================================================================

def test_table_shape():
    assert len(WINDOWS_TO_IANA) == 139
    assert WINDOWS_TO_IANA[WINDOWS_KEY] == BERLIN
    assert WINDOWS_TO_IANA["Central Europe Standard Time"] == "Europe/Budapest"
    # every value is a tz database name, and the keys are sorted like CLDR's
    assert all("/" in name for name in WINDOWS_TO_IANA.values())
    assert list(WINDOWS_TO_IANA) == sorted(WINDOWS_TO_IANA)


# =====================================================================
# standard_timezone_name
# =====================================================================

class TestStandardTimezoneName:
    def test_windows_key(self):
        assert standard_timezone_name(WINDOWS_KEY) == BERLIN

    def test_quoted_windows_key(self):
        assert standard_timezone_name('"W. Europe Standard Time"') == BERLIN

    def test_standard_name_passes_through(self):
        assert standard_timezone_name(BERLIN) == BERLIN
        assert standard_timezone_name("America/Argentina/Buenos_Aires") == \
            "America/Argentina/Buenos_Aires"

    def test_empty_and_none(self):
        assert standard_timezone_name(None) == ""
        assert standard_timezone_name("") == ""
        assert standard_timezone_name("   ") == ""

    def test_unknown_key_is_returned_unchanged(self):
        # An id the table does not know (a proprietary one) must not be
        # invented into something else.
        assert standard_timezone_name("Fred Standard Time") == \
            "Fred Standard Time"


# =====================================================================
# timezone_for
# =====================================================================

class TestTimezoneFor:
    def test_windows_key_resolves_to_the_standard_zone(self):
        tz = timezone_for(WINDOWS_KEY)
        assert tz is not None
        assert tz.zone == BERLIN

    def test_offsets_follow_the_standard_zone(self):
        tz = timezone_for(WINDOWS_KEY)
        winter = tz.localize(datetime(2027, 1, 15, 12, 0)).utcoffset()
        summer = tz.localize(datetime(2027, 7, 15, 12, 0)).utcoffset()
        assert winter.total_seconds() == 3600
        assert summer.total_seconds() == 7200

    def test_unknown_and_empty_are_none(self):
        assert timezone_for("Fred Standard Time") is None
        assert timezone_for(None) is None
        assert timezone_for("") is None


# =====================================================================
# standardize_ics_timezones
# =====================================================================

class TestStandardizeIcsTimezones:
    def test_rewrites_property_and_parameter(self):
        out = standardize_ics_timezones(OUTLOOK_ICS)
        assert "TZID:Europe/Berlin" in out
        assert "DTSTART;TZID=Europe/Berlin:20270630T123000" in out
        assert "DTEND;TZID=Europe/Berlin:20270630T140000" in out
        assert WINDOWS_KEY not in out

    def test_is_idempotent(self):
        once = standardize_ics_timezones(OUTLOOK_ICS)
        assert standardize_ics_timezones(once) == once

    def test_still_parses_to_the_same_event(self):
        before = icalendar.Calendar.from_ical(OUTLOOK_ICS).walk("VEVENT")[0]
        after = icalendar.Calendar.from_ical(
            standardize_ics_timezones(OUTLOOK_ICS)).walk("VEVENT")[0]
        assert before["DTSTART"].dt == after["DTSTART"].dt
        assert before["DTEND"].dt == after["DTEND"].dt
        assert str(after["UID"]) == str(before["UID"])
        assert str(after["SUMMARY"]) == str(before["SUMMARY"])

    def test_standard_document_round_trips_byte_identically(self):
        assert standardize_ics_timezones(STANDARD_ICS) == STANDARD_ICS
        # ... and line endings are not "fixed" for its own sake.
        lf = STANDARD_ICS.replace("\r\n", "\n")
        assert standardize_ics_timezones(lf) == lf

    def test_unknown_key_is_left_alone(self):
        doc = STANDARD_ICS.replace("Europe/Berlin", "Customized Time Zone")
        assert standardize_ics_timezones(doc) == doc

    def test_folded_tzid_parameter_is_unfolded(self):
        folded = (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//t//\r\n"
            "BEGIN:VEVENT\r\nUID:f-1\r\nSUMMARY:F\r\n"
            "DTSTART;TZID=\"W. Europe Stan\r\n dard Time\":20270630T123000\r\n"
            "DTEND;TZID=\"W. Europe Stan\r\n dard Time\":20270630T140000\r\n"
            "END:VEVENT\r\nEND:VCALENDAR\r\n"
        )
        out = standardize_ics_timezones(folded)
        assert "DTSTART;TZID=\"Europe/Berlin\":20270630T123000" in out
        assert "DTEND;TZID=\"Europe/Berlin\":20270630T140000" in out
        assert WINDOWS_KEY not in out.replace("\r\n ", "")

    def test_folded_tzid_property_is_unfolded(self):
        folded = (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//t//\r\n"
            "BEGIN:VTIMEZONE\r\nTZID:W. Europe Stan\r\n dard Time\r\n"
            "END:VTIMEZONE\r\n"
            "BEGIN:VEVENT\r\nUID:p-1\r\nSUMMARY:P\r\n"
            "DTSTART;TZID=W. Europe Standard Time:20270630T123000\r\n"
            "END:VEVENT\r\nEND:VCALENDAR\r\n"
        )
        out = standardize_ics_timezones(folded)
        assert "TZID:Europe/Berlin" in out
        assert WINDOWS_KEY not in out

    def test_rewritten_document_uses_crlf_and_ends_with_one(self):
        out = standardize_ics_timezones(OUTLOOK_ICS.replace("\r\n", "\n"))
        assert out.endswith("\r\n")
        assert "\n" not in out.replace("\r\n", "")


# =====================================================================
# ensure_tz — the pytz-based paths (GUI save, attach editor)
# =====================================================================

class TestEnsureTzAcceptsWindowsKeys:
    def test_summer_offset_is_not_utc(self):
        dt = ensure_tz(datetime(2027, 7, 15, 12, 0), WINDOWS_KEY)
        assert dt.tzinfo.zone == BERLIN
        assert dt.utcoffset().total_seconds() == 7200

    def test_winter_offset(self):
        dt = ensure_tz(datetime(2027, 1, 15, 12, 0), WINDOWS_KEY)
        assert dt.utcoffset().total_seconds() == 3600

    def test_unknown_id_uses_the_default(self):
        dt = ensure_tz(datetime(2027, 7, 15, 12, 0), "Fred Standard Time",
                       default=pytz.UTC)
        assert dt.tzinfo is pytz.UTC


# =====================================================================
# backend.event — the exposed tzid
# =====================================================================

class TestEventTzidIsStandardized:
    def test_parse_vevent_reports_the_standard_name(self):
        parsed = _parse_vevent(OUTLOOK_ICS)
        assert parsed["tzid"] == BERLIN
        assert parsed["start"].utcoffset().total_seconds() == 7200
        assert parsed["end"].utcoffset().total_seconds() == 7200

    def test_immutable_event_keeps_the_raw_text(self):
        ev = ImmutableEvent.from_ical(OUTLOOK_ICS, "caldav:P:cal",
                                      config_tz=pytz.timezone(BERLIN))
        assert ev.tzid == BERLIN
        # The stored document is a copy of what the server sent — only the
        # exposed tzid is mapped.
        assert WINDOWS_KEY in ev.ical_data
        assert ev.start.utcoffset().total_seconds() == 7200
