"""Tests for gui/widgets/calendar_widget.py — view switching and navigation."""

from datetime import date

from gui.widgets.calendar_widget import CalendarWidget, ViewType


def _make_cal():
    return CalendarWidget()


# ----------------------------------------------------------------------
# view switching
# ----------------------------------------------------------------------

def test_default_view_is_week(qapp):
    cw = _make_cal()
    assert cw.get_current_view() == ViewType.WEEK


def test_set_view_day(qapp):
    cw = _make_cal()
    cw.set_view(ViewType.DAY)
    assert cw.get_current_view() == ViewType.DAY


def test_set_view_month(qapp):
    cw = _make_cal()
    cw.set_view(ViewType.MONTH)
    assert cw.get_current_view() == ViewType.MONTH


def test_set_view_list(qapp):
    cw = _make_cal()
    cw.set_view(ViewType.LIST)
    assert cw.get_current_view() == ViewType.LIST


def test_view_changed_signal(qapp):
    cw = _make_cal()
    seen = []
    cw.view_changed.connect(lambda v: seen.append(v))
    cw.set_view(ViewType.MONTH)
    assert seen == [ViewType.MONTH]


# ----------------------------------------------------------------------
# day navigation
# ----------------------------------------------------------------------

def test_go_previous_day(qapp):
    cw = _make_cal()
    cw.set_view(ViewType.DAY)
    cw.set_date(date(2026, 1, 10))
    cw.go_previous()
    assert cw.get_current_date() == date(2026, 1, 9)


def test_go_next_day(qapp):
    cw = _make_cal()
    cw.set_view(ViewType.DAY)
    cw.set_date(date(2026, 1, 10))
    cw.go_next()
    assert cw.get_current_date() == date(2026, 1, 11)


# ----------------------------------------------------------------------
# week navigation
# ----------------------------------------------------------------------

def test_go_previous_week(qapp):
    cw = _make_cal()
    cw.set_view(ViewType.WEEK)
    cw.set_date(date(2026, 1, 15))
    cw.go_previous()
    assert cw.get_current_date() == date(2026, 1, 8)


def test_go_next_week(qapp):
    cw = _make_cal()
    cw.set_view(ViewType.WEEK)
    cw.set_date(date(2026, 1, 15))
    cw.go_next()
    assert cw.get_current_date() == date(2026, 1, 22)


# ----------------------------------------------------------------------
# month navigation (with year wrap)
# ----------------------------------------------------------------------

def test_go_previous_month(qapp):
    cw = _make_cal()
    cw.set_view(ViewType.MONTH)
    cw.set_date(date(2026, 3, 15))
    cw.go_previous()
    assert cw.get_current_date() == date(2026, 2, 15)


def test_go_next_month(qapp):
    cw = _make_cal()
    cw.set_view(ViewType.MONTH)
    cw.set_date(date(2026, 11, 15))
    cw.go_next()
    assert cw.get_current_date() == date(2026, 12, 15)


def test_go_previous_month_wrap_to_dec(qapp):
    cw = _make_cal()
    cw.set_view(ViewType.MONTH)
    cw.set_date(date(2026, 1, 15))
    cw.go_previous()
    assert cw.get_current_date() == date(2025, 12, 15)


def test_go_next_month_wrap_to_jan(qapp):
    cw = _make_cal()
    cw.set_view(ViewType.MONTH)
    cw.set_date(date(2026, 12, 15))
    cw.go_next()
    assert cw.get_current_date() == date(2027, 1, 15)


# ----------------------------------------------------------------------
# go_today
# ----------------------------------------------------------------------

def test_go_today_day(qapp):
    cw = _make_cal()
    cw.set_view(ViewType.DAY)
    cw.set_date(date(2020, 1, 1))
    cw.go_today()
    assert cw.get_current_date() == date.today()


def test_go_today_month(qapp):
    cw = _make_cal()
    cw.set_view(ViewType.MONTH)
    cw.set_date(date(2020, 1, 1))
    cw.go_today()
    assert cw.get_current_date() == date.today()


# ----------------------------------------------------------------------
# get_reference_datetime
# ----------------------------------------------------------------------

def test_get_reference_datetime_day(qapp):
    cw = _make_cal()
    cw.set_view(ViewType.DAY)
    cw.set_date(date(2026, 1, 10))
    ref = cw.get_reference_datetime()
    assert ref.date() == date(2026, 1, 10)


def test_get_reference_datetime_month(qapp):
    cw = _make_cal()
    cw.set_view(ViewType.MONTH)
    cw.set_date(date(2026, 3, 15))
    ref = cw.get_reference_datetime()
    assert ref.date() == date(2026, 3, 1)