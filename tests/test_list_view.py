"""Tests for gui/widgets/list_view.py — date range, sorting, scroll anchoring."""

from datetime import date, datetime, timedelta, time as dt_time
from unittest.mock import MagicMock

import pytz
from PySide6.QtTest import QTest

from gui.widgets.list_view import ListView
from library.timezone_utils import to_local_datetime

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
    """Fallback (nothing shown): ±90 days around current date."""
    lv = ListView()
    lv.set_date(date(2026, 1, 15))
    start, end = lv.get_date_range()
    assert start == datetime.combine(date(2025, 10, 17), dt_time.min)
    assert end == datetime.combine(date(2026, 4, 15), dt_time.max)


def test_list_view_get_date_range_anchored(qapp):
    """With an anchor: -4 months from first shown, +8 months from last shown."""
    lv = ListView()
    lv._anchor_datetime = datetime(2026, 6, 15, 9, 0)
    start, end = lv.get_date_range()
    assert start == datetime.combine(date(2026, 2, 15), dt_time.min)
    assert end == datetime.combine(date(2027, 2, 15), dt_time.max)


def test_add_months_clamps_day(qapp):
    assert ListView._add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert ListView._add_months(date(2026, 3, 31), -1) == date(2026, 2, 28)
    assert ListView._add_months(date(2026, 12, 15), 2) == date(2027, 2, 15)
    assert ListView._add_months(date(2026, 1, 15), -4) == date(2025, 9, 15)


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


# ----------------------------------------------------------------------
# scroll anchoring
# ----------------------------------------------------------------------

def _make_many_events(n=50):
    base = datetime(2026, 1, 1, 8, 0, tzinfo=UTC)
    return [
        _mkevent(base + timedelta(hours=3 * i), base + timedelta(hours=3 * i + 1), uid=f"e{i}")
        for i in range(n)
    ]


def _show_and_settle(lv, events, wait=400):
    lv.resize(400, 600)
    lv.set_events(events)
    lv.show()
    QTest.qWait(wait)


def test_anchor_updated_on_scroll(qapp):
    lv = ListView()
    events = _make_many_events()
    _show_and_settle(lv, events)

    lv.scroll_to_datetime(to_local_datetime(events[20].start))
    QTest.qWait(200)

    anchor = lv.get_anchor_datetime()
    assert anchor is not None
    assert anchor == lv.get_first_visible_datetime()
    lv.close()


def test_rebuild_preserves_position(qapp):
    """set_events (sync/auto-refresh) must not move the visible position."""
    lv = ListView()
    events = _make_many_events()
    _show_and_settle(lv, events)

    lv.scroll_to_datetime(to_local_datetime(events[20].start))
    QTest.qWait(200)
    first_before = lv.get_first_visible_datetime()
    assert first_before is not None

    # Rebuild with an extra event (simulates sync completion)
    extra = _mkevent(datetime(2026, 2, 1, 12, 0, tzinfo=UTC), datetime(2026, 2, 1, 13, 0, tzinfo=UTC), uid="extra")
    lv.set_events(events + [extra])
    QTest.qWait(500)

    assert lv.get_first_visible_datetime() == first_before
    lv.close()


def test_scroll_to_datetime_fallback_last_event(qapp):
    """Target beyond the last event scrolls to the last event (not nowhere)."""
    lv = ListView()
    events = _make_many_events()
    _show_and_settle(lv, events)

    lv.scroll_to_datetime(datetime(2030, 1, 1))
    QTest.qWait(200)

    # Scrollbar clamps at the bottom, so the last event must be visible
    # (at the bottom of the viewport, not necessarily at the top).
    first_visible, last_visible = lv.get_visible_date_range()
    assert first_visible is not None
    assert last_visible == to_local_datetime(events[-1].start)
    lv.close()
