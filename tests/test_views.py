"""Tests for gui/widgets/day_view.py and week_view.py — date ranges."""

from datetime import date, datetime, time as dt_time, timedelta

from gui.widgets.config_state import HOUR_HEIGHT
from gui.widgets.time_axis import MixedTimeAxis
from gui.widgets.shared_scrollbar import _ScrollBarState
from gui.widgets.day_view import DayView
from gui.widgets.week_view import WeekView


def _make_day_view():
    return DayView(mapper=MixedTimeAxis(HOUR_HEIGHT), scroll_state=_ScrollBarState())


def _make_week_view():
    return WeekView(mapper=MixedTimeAxis(HOUR_HEIGHT), scroll_state=_ScrollBarState())


def test_day_view_date_range(qapp):
    v = _make_day_view()
    v.set_date(date(2026, 1, 15))
    start, end = v.get_date_range()
    assert start == datetime.combine(date(2026, 1, 15), dt_time.min)
    assert end == datetime.combine(date(2026, 1, 15), dt_time.max)


def test_week_view_date_range(qapp):
    v = _make_week_view()
    v.set_date(date(2026, 1, 15))  # Thursday
    start, end = v.get_date_range()
    # default first_day_of_week=0 (Monday) → week start 2026-01-12
    assert start == datetime.combine(date(2026, 1, 12), dt_time.min)
    assert end == datetime.combine(date(2026, 1, 18), dt_time.max)


def test_week_view_get_week_start(qapp):
    v = _make_week_view()
    # 2026-01-01 is Thursday → Monday start is 2025-12-29
    assert v._get_week_start(date(2026, 1, 1)) == date(2025, 12, 29)


def test_day_view_num_days(qapp):
    v = _make_day_view()
    assert v._get_num_days() == 1
    assert v._get_day_dates() == [v._date]


def test_week_view_num_days(qapp):
    v = _make_week_view()
    assert v._get_num_days() == 7
    dates = v._get_day_dates()
    assert len(dates) == 7
    assert dates[0] == v._start_date
    assert dates[-1] == v._start_date + timedelta(days=6)