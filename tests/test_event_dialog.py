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
