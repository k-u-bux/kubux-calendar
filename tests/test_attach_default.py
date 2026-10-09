"""Tests for the ``[General] default_calendar`` pre-selection.

Covers both the Qt-free resolution helper used by the import window and the
window itself (headless via the session ``qapp`` fixture).
"""

from types import SimpleNamespace

import pytz

import cli.calendar_attach as ca

ICS = (
    "BEGIN:VCALENDAR\r\n"
    "VERSION:2.0\r\n"
    "PRODID:-//test//EN\r\n"
    "BEGIN:VEVENT\r\n"
    "UID:att-1@example.com\r\n"
    "SUMMARY:Team Meeting\r\n"
    "LOCATION:Room 4\r\n"
    "DTSTART:20260901T090000Z\r\n"
    "DTEND:20260901T100000Z\r\n"
    "END:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)

TZ = "Europe/Berlin"


def _src(source_id: str, name: str) -> SimpleNamespace:
    """Stand-in for CalendarSource (what the combo needs)."""
    return SimpleNamespace(id=source_id, name=name, account_name="Nextcloud.Primary")


CALS = [
    _src("caldav:Nextcloud.Primary:urlaub", "Urlaub"),
    _src("caldav:Nextcloud.Primary:beruflich", "beruflich"),
]


def _config(**kwargs):
    base = dict(timezone=TZ)
    base.update(kwargs)
    return SimpleNamespace(**base)


class TestDefaultCalendarId:
    """The config value, read per launch (a one-shot process)."""

    def test_unset_returns_none(self):
        assert ca.default_calendar_id(_config(default_calendar=""), CALS) is None

    def test_missing_attribute_returns_none(self):
        """A config object without the field (older code paths) is tolerated."""
        assert ca.default_calendar_id(_config(), CALS) is None

    def test_bare_name(self):
        cfg = _config(default_calendar="beruflich")
        assert ca.default_calendar_id(cfg, CALS) == CALS[1].id

    def test_account_slash_calendar(self):
        cfg = _config(default_calendar="Nextcloud.Primary/Urlaub")
        assert ca.default_calendar_id(cfg, CALS) == CALS[0].id

    def test_full_source_id(self):
        cfg = _config(default_calendar="caldav:Nextcloud.Primary:urgent")
        cals = CALS + [_src("caldav:Nextcloud.Primary:urgent", "urgent")]
        assert ca.default_calendar_id(cfg, cals) == cals[2].id

    def test_unresolvable_returns_none(self):
        cfg = _config(default_calendar="does-not-exist")
        assert ca.default_calendar_id(cfg, CALS) is None


class TestDialogPreSelection:
    def _dialog(self, config):
        config_tz = pytz.timezone(config.timezone)
        canonical, ev = ca.parse_ics(ICS, "src", config_tz=config_tz)
        return ca.AttachDialog(config, canonical, ev, CALS)

    def test_config_default_is_selected(self, qapp):
        dlg = self._dialog(_config(default_calendar="beruflich"))
        assert dlg._calendar_combo.currentData() == "caldav:Nextcloud.Primary:beruflich"

    def test_account_slash_form_is_selected(self, qapp):
        dlg = self._dialog(_config(default_calendar="Nextcloud.Primary/Urlaub"))
        assert dlg._calendar_combo.currentData() == "caldav:Nextcloud.Primary:urlaub"

    def test_every_writable_calendar_is_offered(self, qapp):
        dlg = self._dialog(_config(default_calendar="beruflich"))
        offered = [dlg._calendar_combo.itemData(i)
                   for i in range(dlg._calendar_combo.count())]
        assert offered == [c.id for c in CALS]

    def test_without_default_first_entry_stays(self, qapp):
        dlg = self._dialog(_config())
        assert dlg._calendar_combo.currentData() == CALS[0].id

    def test_unresolvable_default_keeps_first_entry(self, qapp):
        dlg = self._dialog(_config(default_calendar="does-not-exist"))
        assert dlg._calendar_combo.currentData() == CALS[0].id
