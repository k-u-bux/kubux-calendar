"""Tests for gui/widgets/all_day_events.py — all-day event row."""

from datetime import datetime
from unittest.mock import MagicMock

import pytz

from gui.widgets.all_day_events import AllDayEventsRow

UTC = pytz.UTC


def _mkevent(uid="u", summary="E"):
    ev = MagicMock()
    ev.start = datetime(2026, 1, 1, tzinfo=UTC)
    ev.end = datetime(2026, 1, 1, 1, 0, tzinfo=UTC)
    ev.all_day = False
    ev.read_only = False
    ev.is_recurring = False
    ev.calendar_color = "#ff0000"
    ev.calendar_name = "Cal"
    ev.summary = summary
    ev.uid = uid
    return ev


def test_all_day_row_max_empty(qapp):
    row = AllDayEventsRow(num_days=3)
    assert row.get_max_events() == 0


def test_all_day_row_get_max_events(qapp):
    row = AllDayEventsRow(num_days=3)
    row.set_events_for_day(0, [_mkevent("a")])
    row.set_events_for_day(1, [_mkevent("b"), _mkevent("c")])
    assert row.get_max_events() == 2


def test_all_day_row_update_height_empty(qapp):
    row = AllDayEventsRow(num_days=2)
    row.update_height()
    assert row.height() == 0


def test_all_day_row_update_height_with_events(qapp):
    row = AllDayEventsRow(num_days=2)
    row.set_events_for_day(0, [_mkevent("a")])
    row.update_height()
    assert row.height() > 0


def test_all_day_row_clear_all(qapp):
    row = AllDayEventsRow(num_days=2)
    row.set_events_for_day(0, [_mkevent("a")])
    row.clear_all()
    assert row.get_max_events() == 0


def test_all_day_row_set_events_out_of_range(qapp):
    """day_index out of range is ignored."""
    row = AllDayEventsRow(num_days=2)
    row.set_events_for_day(5, [_mkevent("a")])
    assert row.get_max_events() == 0
