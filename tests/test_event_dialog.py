"""Tests for gui/event_dialog.py — timezone handling and validation."""

from datetime import datetime
from unittest.mock import MagicMock, patch

from PySide6.QtCore import QDateTime

from backend.config import Config
from backend.event import CalendarSource
from gui.event_dialog import EventDialog


def _make_config(tmp_path):
    return Config(password_program="/usr/bin/true", state_file=tmp_path / "state.json")


def _make_store(tmp_path, writable=None):
    cfg = _make_config(tmp_path)
    store = MagicMock()
    store.config = cfg
    store.get_writable_calendars = lambda: writable or []
    return store


def test_get_tzid_from_combo_floating(qapp, tmp_path):
    store = _make_store(tmp_path)
    with patch("gui.event_dialog.QMessageBox"):
        dlg = EventDialog(store, event_data=None, initial_datetime=datetime(2026, 1, 1, 10, 0))
    dlg._tz_combo.setCurrentText("Floating")
    assert dlg._get_tzid_from_combo() is None


def test_get_tzid_from_combo_utc(qapp, tmp_path):
    store = _make_store(tmp_path)
    with patch("gui.event_dialog.QMessageBox"):
        dlg = EventDialog(store, event_data=None, initial_datetime=datetime(2026, 1, 1, 10, 0))
    dlg._tz_combo.setCurrentText("UTC")
    assert dlg._get_tzid_from_combo() == "UTC"


def test_on_save_empty_title_rejected(qapp, tmp_path):
    store = _make_store(tmp_path)
    with patch("gui.event_dialog.QMessageBox") as mb:
        dlg = EventDialog(store, event_data=None, initial_datetime=datetime(2026, 1, 1, 10, 0))
        dlg._title_edit.setText("")
        created = []
        store.create_event = lambda **kw: created.append(kw)
        dlg._on_save()
    assert created == []
    mb.warning.assert_called()


def test_on_save_end_before_start_rejected(qapp, tmp_path):
    store = _make_store(tmp_path)
    with patch("gui.event_dialog.QMessageBox"):
        dlg = EventDialog(store, event_data=None, initial_datetime=datetime(2026, 1, 1, 10, 0))
        dlg._title_edit.setText("Test")
        dlg._start_edit.setDateTime(QDateTime(datetime(2026, 1, 1, 12, 0)))
        dlg._end_edit.setDateTime(QDateTime(datetime(2026, 1, 1, 10, 0)))
        created = []
        store.create_event = lambda **kw: created.append(kw)
        dlg._on_save()
    assert created == []


def test_on_save_creates_event(qapp, tmp_path):
    src = CalendarSource(id="cal1", name="Cal1", source_type="caldav")
    store = _make_store(tmp_path, writable=[src])
    created = []

    def fake_create(**kw):
        created.append(kw)
        v = MagicMock()
        v.summary = kw["summary"]
        return v

    store.create_event = fake_create
    with patch("gui.event_dialog.QMessageBox"):
        dlg = EventDialog(store, event_data=None, initial_datetime=datetime(2026, 1, 1, 10, 0))
        dlg._title_edit.setText("Meeting")
        dlg._start_edit.setDateTime(QDateTime(datetime(2026, 1, 1, 10, 0)))
        dlg._end_edit.setDateTime(QDateTime(datetime(2026, 1, 1, 11, 0)))
        dlg._on_save()
    assert len(created) == 1
    assert created[0]["summary"] == "Meeting"


def test_on_tz_changed_converts_displayed_time(qapp, tmp_path):
    store = _make_store(tmp_path)
    with patch("gui.event_dialog.QMessageBox"):
        dlg = EventDialog(store, event_data=None, initial_datetime=datetime(2026, 1, 1, 10, 0))
    dlg._ignore_tz_change = True
    dlg._display_tzid = "UTC"
    dlg._start_edit.setDateTime(QDateTime(datetime(2026, 1, 1, 10, 0)))
    dlg._end_edit.setDateTime(QDateTime(datetime(2026, 1, 1, 11, 0)))
    dlg._tz_combo.setCurrentText("Europe/Berlin")
    dlg._ignore_tz_change = False
    dlg._on_tz_changed(0)
    start = dlg._start_edit.dateTime().toPython()
    end = dlg._end_edit.dateTime().toPython()
    assert start.hour == 11
    assert end.hour == 12


# ----------------------------------------------------------------------
# Regression: edit-dialog title must apply the dialog_edit_event
# format template ("Edit: {}") instead of showing a literal "{}"
# ----------------------------------------------------------------------

def _make_event_view(summary="Dentist"):
    ev = MagicMock()
    ev.summary = summary
    ev.location = ""
    ev.description = ""
    ev.all_day = False
    ev.read_only = False
    ev.recurrence = None
    ev.start = datetime(2026, 1, 1, 10, 0)
    ev.end = datetime(2026, 1, 1, 11, 0)
    src = CalendarSource(id="cal1", name="Cal1", source_type="caldav")
    ev.source = src
    ev.immutable_event = ev
    ev.tzid = None
    return ev


def test_edit_dialog_title_uses_format_template(qapp, tmp_path):
    store = _make_store(tmp_path)
    with patch("gui.event_dialog.QMessageBox"):
        dlg = EventDialog(store, event_data=_make_event_view())
    assert dlg.windowTitle() == "Edit: Dentist"


def test_edit_dialog_title_label_without_placeholder(qapp, tmp_path):
    store = _make_store(tmp_path)
    store.config.labels.dialog_edit_event = "Edit:"
    with patch("gui.event_dialog.QMessageBox"):
        dlg = EventDialog(store, event_data=_make_event_view())
    assert dlg.windowTitle() == "Edit: Dentist"


# ----------------------------------------------------------------------
# New-event calendar default: MainWindow passes the config's
# [General] default_calendar (read at startup, in memory); the dialog
# never persists it and stops needing it once the session used one
# ----------------------------------------------------------------------

URLAUB = "caldav:Nextcloud.Primary:urlaub"
BERUFLICH = "caldav:Nextcloud.Primary:beruflich"


def _two_calendar_store(tmp_path):
    urlaub = CalendarSource(id=URLAUB, name="Urlaub", source_type="caldav")
    beruf = CalendarSource(id=BERUFLICH, name="beruflich", source_type="caldav")
    return _make_store(tmp_path, writable=[urlaub, beruf])


def _dialog_state_file(tmp_path):
    return tmp_path / "dialog_state.json"


def _remember(state_file, calendar_id):
    import json
    state_file.write_text(json.dumps({"last_calendar_id": calendar_id}))


def _new_dialog(store, startup_default=None, event_data=None):
    with patch("gui.event_dialog.QMessageBox"):
        return EventDialog(store, event_data=event_data,
                           initial_datetime=datetime(2026, 1, 1, 10, 0),
                           startup_default_calendar_id=startup_default)


class TestStartupDefaultCalendar:
    """The config value MainWindow resolved at startup (memory only)."""

    def test_startup_default_is_selected(self, qapp, tmp_path):
        dlg = _new_dialog(_two_calendar_store(tmp_path), BERUFLICH)
        assert dlg._calendar_combo.currentData() == BERUFLICH

    def test_startup_default_beats_remembered_calendar(self, qapp, tmp_path):
        """Previous runs remembered urlaub; the config decides at startup."""
        _remember(_dialog_state_file(tmp_path), URLAUB)
        dlg = _new_dialog(_two_calendar_store(tmp_path), BERUFLICH)
        assert dlg._calendar_combo.currentData() == BERUFLICH

    def test_remembered_calendar_used_without_startup_default(self, qapp, tmp_path):
        _remember(_dialog_state_file(tmp_path), BERUFLICH)
        dlg = _new_dialog(_two_calendar_store(tmp_path), None)
        assert dlg._calendar_combo.currentData() == BERUFLICH

    def test_first_entry_when_nothing_is_known(self, qapp, tmp_path):
        dlg = _new_dialog(_two_calendar_store(tmp_path), None)
        assert dlg._calendar_combo.currentData() == URLAUB

    def test_startup_default_is_not_persisted(self, qapp, tmp_path):
        """dialog_state.json belongs to the session, not to the config."""
        dlg = _new_dialog(_two_calendar_store(tmp_path), BERUFLICH)
        assert dlg._dialog_state.get("last_calendar_id") is None
        dlg.close()
        text = _dialog_state_file(tmp_path).read_text()
        assert "last_calendar_id" not in text

    def test_edit_dialog_ignores_startup_default(self, qapp, tmp_path):
        ev = _make_event_view()
        ev.source = CalendarSource(id=URLAUB, name="Urlaub", source_type="caldav")
        dlg = _new_dialog(_two_calendar_store(tmp_path), BERUFLICH, event_data=ev)
        assert dlg._calendar_combo.currentData() == URLAUB


class TestSessionCalendarUse:
    """The dialog reports when the session itself picked a calendar."""

    def test_not_used_before_anything_happens(self, qapp, tmp_path):
        assert _new_dialog(_two_calendar_store(tmp_path), BERUFLICH) \
            .calendar_used_this_session is False

    def test_new_event_marks_it_used_and_persists(self, qapp, tmp_path):
        store = _two_calendar_store(tmp_path)
        dlg = _new_dialog(store, BERUFLICH)
        dlg._title_edit.setText("Meeting")
        dlg._on_save()
        assert dlg.calendar_used_this_session is True
        dlg.close()
        assert BERUFLICH in _dialog_state_file(tmp_path).read_text()

    def test_edit_same_calendar_does_not_mark_it_used(self, qapp, tmp_path):
        ev = _make_event_view()
        ev.source = CalendarSource(id=URLAUB, name="Urlaub", source_type="caldav")
        store = _two_calendar_store(tmp_path)
        store.update_event = lambda cal_event: True
        dlg = _new_dialog(store, None, event_data=ev)
        dlg._on_save()
        assert dlg.calendar_used_this_session is False
