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

def test_get_reference_datetime_list_uses_anchor(qapp):
    """List view reference datetime comes from the top visible event (anchor)."""
    from datetime import datetime
    cw = _make_cal()
    cw.set_view(ViewType.LIST)
    anchor = datetime(2026, 2, 10, 9, 0)
    cw._list_view._anchor_datetime = anchor
    assert cw.get_reference_datetime() == anchor


def test_get_reference_datetime_list_anchor_beats_last_scroll(qapp):
    """The live anchor takes priority over the tracked scroll target."""
    from datetime import datetime
    cw = _make_cal()
    cw.set_view(ViewType.LIST)
    cw._list_view._anchor_datetime = datetime(2026, 2, 10, 9, 0)
    cw._list_view._last_scroll_datetime = datetime(2026, 5, 1, 9, 0)
    assert cw.get_reference_datetime() == datetime(2026, 2, 10, 9, 0)


def test_list_scroll_updates_current_date_silently(qapp):
    """Scrolling the list view updates _current_date without date_changed."""
    from datetime import datetime
    cw = _make_cal()
    cw.set_view(ViewType.LIST)
    cw.set_date(date(2026, 1, 1))

    seen = []
    cw.date_changed.connect(lambda d: seen.append(d))

    cw._list_view.visible_range_changed.emit(datetime(2026, 3, 10, 9, 0), datetime(2026, 3, 12, 9, 0))
    assert cw.get_current_date() == date(2026, 3, 10)
    assert seen == []  # silent - no signal


def test_list_visible_range_ignored_in_other_views(qapp):
    """The tracking handler must not fire when list view is inactive."""
    from datetime import datetime
    cw = _make_cal()
    cw.set_view(ViewType.WEEK)
    cw.set_date(date(2026, 1, 5))

    cw._list_view.visible_range_changed.emit(datetime(2026, 3, 10, 9, 0), datetime(2026, 3, 12, 9, 0))
    assert cw.get_current_date() == date(2026, 1, 5)
