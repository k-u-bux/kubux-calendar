"""Tests for gui/widgets/list_view.py — date range and sorting."""

from datetime import date, datetime, time as dt_time
from unittest.mock import MagicMock

import pytz

from gui.widgets.list_view import ListView

UTC = pytz.UTC


def _mkevent(start, end, uid="u", summary="E"):
    ev = MagicMock()
    ev.start = start
    ev.end = end
    ev.all_day = False
    ev.read_only = False
    ev.is_recurring = False
    ev.calendar_color = "#ff0000"
    ev.calendar_name = "Cal"
    ev.summary = summary
    ev.location = ""
    ev.description = ""
    ev.uid = uid
    return ev


def test_list_view_get_date_range(qapp):
    lv = ListView()
    lv.set_date(date(2026, 1, 15))
    start, end = lv.get_date_range()
    # ±90 days from current date
    assert start == datetime.combine(date(2025, 10, 17), dt_time.min)
    assert end == datetime.combine(date(2026, 4, 15), dt_time.max)


def test_list_view_sorted_events(qapp):
    lv = ListView()
    ev3 = _mkevent(datetime(2026, 3, 1, tzinfo=UTC), datetime(2026, 3, 1, 1, 0, tzinfo=UTC), uid="c")
    ev1 = _mkevent(datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 1, 1, 0, tzinfo=UTC), uid="a")
    ev2 = _mkevent(datetime(2026, 2, 1, tzinfo=UTC), datetime(2026, 2, 1, 1, 0, tzinfo=UTC), uid="b")
    lv.set_events([ev3, ev1, ev2])
    assert [e.uid for e in lv._sorted_events] == ["a", "b", "c"]


def test_list_view_empty(qapp):
    lv = ListView()
    lv.set_events([])
    assert lv._sorted_events == []
    assert lv.get_visible_date_range() == (None, None)


def test_list_view_set_date(qapp):
    lv = ListView()
    lv.set_date(date(2026, 5, 20))
    assert lv._current_date == date(2026, 5, 20)