"""Tests for library/calendar_ref.py — resolving a calendar reference.

The reference is the ``[General] default_calendar`` value: it must resolve
offline against the cached calendar sources, and an ambiguous name must be
reported as "no match" instead of picking one silently.
"""

from types import SimpleNamespace

from library.calendar_ref import match_calendar_index, resolve_calendar_id


def _src(source_id: str, name: str = "", account_name: str = "") -> SimpleNamespace:
    """Stand-in for CalendarSource (only what the resolver touches)."""
    return SimpleNamespace(id=source_id, name=name, account_name=account_name)


CALENDARS = [
    _src("caldav:Nextcloud.Primary:urlaub", "Urlaub", "Nextcloud.Primary"),
    _src("caldav:Nextcloud.Primary:beruflich", "beruflich", "Nextcloud.Primary"),
    _src("caldav:Nextcloud.Mail:contacts", "contacts", "Nextcloud.Mail"),
]


class TestFullSourceId:
    def test_exact_id(self):
        assert match_calendar_index("caldav:Nextcloud.Primary:beruflich", CALENDARS) == 1

    def test_unknown_id(self):
        assert match_calendar_index("caldav:Nextcloud.Primary:nope", CALENDARS) is None

    def test_ics_id(self):
        cals = [_src("ics:Holidays", "Public Holidays")]
        assert match_calendar_index("ics:Holidays", cals) == 0

    def test_id_is_case_sensitive(self):
        assert match_calendar_index("caldav:nextcloud.primary:beruflich", CALENDARS) is None


class TestAccountSlashCalendar:
    def test_calendar_id(self):
        assert match_calendar_index("Nextcloud.Primary/beruflich", CALENDARS) == 1

    def test_account_prefix_optional(self):
        assert match_calendar_index("Primary/beruflich", CALENDARS) == 1

    def test_display_name(self):
        assert match_calendar_index("Nextcloud.Primary/Urlaub", CALENDARS) == 0

    def test_case_insensitive(self):
        assert match_calendar_index("nextcloud.primary/BERUFLICH", CALENDARS) == 1

    def test_wrong_account(self):
        assert match_calendar_index("Nextcloud.Mail/beruflich", CALENDARS) is None

    def test_unknown_calendar(self):
        assert match_calendar_index("Nextcloud.Primary/nonexistent", CALENDARS) is None

    def test_empty_calendar_part(self):
        assert match_calendar_index("Nextcloud.Primary/", CALENDARS) is None


class TestBareName:
    def test_display_name(self):
        assert match_calendar_index("Urlaub", CALENDARS) == 0

    def test_calendar_id(self):
        assert match_calendar_index("contacts", CALENDARS) == 2

    def test_case_insensitive(self):
        assert match_calendar_index("URLAUB", CALENDARS) == 0

    def test_exact_case_wins_over_case_insensitive(self):
        cals = [_src("caldav:A:x", "Privat"), _src("caldav:B:y", "privat")]
        assert match_calendar_index("Privat", cals) == 0
        assert match_calendar_index("privat", cals) == 1

    def test_ambiguous_is_no_match(self):
        cals = [_src("caldav:A:x", "Privat"), _src("caldav:B:y", "privat")]
        assert match_calendar_index("PRIVAT", cals) is None

    def test_unknown(self):
        assert match_calendar_index("Dienstlich", CALENDARS) is None


class TestResolveCalendarId:
    """resolve_calendar_id — matching plus the id, for config consumers."""

    def test_returns_the_id(self):
        assert resolve_calendar_id("Nextcloud.Primary/beruflich", CALENDARS) \
            == "caldav:Nextcloud.Primary:beruflich"
        assert resolve_calendar_id("Urlaub", CALENDARS) \
            == "caldav:Nextcloud.Primary:urlaub"
        assert resolve_calendar_id("caldav:Nextcloud.Mail:contacts", CALENDARS) \
            == "caldav:Nextcloud.Mail:contacts"

    def test_unset_returns_none(self):
        assert resolve_calendar_id("", CALENDARS) is None
        assert resolve_calendar_id("   ", CALENDARS) is None
        assert resolve_calendar_id(None, CALENDARS) is None

    def test_unknown_returns_none(self):
        assert resolve_calendar_id("Diensltich", CALENDARS) is None

    def test_ambiguous_returns_none(self):
        cals = [_src("caldav:A:x", "Privat"), _src("caldav:B:y", "privat")]
        assert resolve_calendar_id("PRIVAT", cals) is None

    def test_no_calendars_returns_none(self):
        assert resolve_calendar_id("Urlaub", []) is None


class TestDegenerateInput:
    def test_empty_ref(self):
        assert match_calendar_index("", CALENDARS) is None
        assert match_calendar_index("   ", CALENDARS) is None
        assert match_calendar_index(None, CALENDARS) is None

    def test_no_calendars(self):
        assert match_calendar_index("Urlaub", []) is None

    def test_source_without_id(self):
        assert match_calendar_index("Urlaub", [SimpleNamespace(name="Urlaub")]) == 0
