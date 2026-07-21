"""Tests for gui/widgets/month_view.py — event distribution across cells."""

from datetime import date, datetime
from unittest.mock import MagicMock

import pytz

from gui.widgets.month_view import MonthView

UTC = pytz.UTC


def _mkevent(start, end, all_day=False, summary="E", uid="u"):
    ev = MagicMock()
    ev.start = start
    ev.end = end
    ev.all_day = all_day
    ev.read_only = False
    ev.is_recurring = False
    ev.calendar_color = "#ff0000"
    ev.calendar_name = "Cal"
    ev.summary = summary
    ev.uid = uid
    return ev


def _cell_for(mv, d):
    for cell in mv._cells:
        if cell.date == d:
            return cell
    return None


def test_month_view_all_day_multi_day(qapp):
    mv = MonthView()
    mv.set_date(date(2026, 1, 15))
    ev = _mkevent(
        datetime(2026, 1, 10, tzinfo=UTC),
        datetime(2026, 1, 13, tzinfo=UTC),
        all_day=True, uid="a",
    )
    mv.set_events([ev])
    # all-day Jan 10-13 → display on 10, 11, 12 (end exclusive -1 day)
    assert len(_cell_for(mv, date(2026, 1, 10))._event_widgets) == 1
    assert len(_cell_for(mv, date(2026, 1, 11))._event_widgets) == 1
    assert len(_cell_for(mv, date(2026, 1, 12))._event_widgets) == 1
    assert len(_cell_for(mv, date(2026, 1, 13))._event_widgets) == 0


def test_month_view_timed_event_on_start_day(qapp):
    mv = MonthView()
    mv.set_date(date(2026, 1, 15))
    ev = _mkevent(
        datetime(2026, 1, 10, 10, 0, tzinfo=UTC),
        datetime(2026, 1, 10, 11, 0, tzinfo=UTC),
        uid="t",
    )
    mv.set_events([ev])
    assert len(_cell_for(mv, date(2026, 1, 10))._event_widgets) == 1
    assert len(_cell_for(mv, date(2026, 1, 11))._event_widgets) == 0


def test_month_view_event_in_other_month_cell(qapp):
    """Events in the grid but outside the current month still appear."""
    mv = MonthView()
    mv.set_date(date(2026, 1, 15))
    ev = _mkevent(
        datetime(2025, 12, 30, 10, 0, tzinfo=UTC),
        datetime(2025, 12, 30, 11, 0, tzinfo=UTC),
        uid="x",
    )
    mv.set_events([ev])
    cell = _cell_for(mv, date(2025, 12, 30))
    assert cell is not None
    assert len(cell._event_widgets) == 1


def test_month_view_no_events(qapp):
    mv = MonthView()
    mv.set_date(date(2026, 1, 15))
    mv.set_events([])
    for cell in mv._cells:
        assert len(cell._event_widgets) == 0


def test_month_view_get_date_range(qapp):
    mv = MonthView()
    mv.set_date(date(2026, 1, 15))
    start, end = mv.get_date_range()
    assert start.date() == mv._cells[0].date
    assert end.date() == mv._cells[-1].date