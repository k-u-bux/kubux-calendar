"""Tests for follow-present mode.

Follow mode is enabled by go_today() and disabled by any other navigation
action or user scroll.  While enabled, every update of the current-time
indicator in day columns also re-centers the view on the current hour.
"""

from datetime import date, timedelta

from PySide6.QtCore import Qt, QEvent, QPoint, QPointF
from PySide6.QtGui import QKeyEvent, QWheelEvent

from gui.widgets.calendar_widget import CalendarWidget, ViewType
from gui.widgets.day_column import DayColumnWidget
from gui.widgets.time_axis import MixedTimeAxis
from gui.widgets.follow_state import FollowState
from gui.widgets.config_state import get_hour_height, get_layout_config


def _make_cal():
    return CalendarWidget()


# ----------------------------------------------------------------------
# flag toggling via CalendarWidget navigation
# ----------------------------------------------------------------------

def test_follow_off_by_default(qapp):
    cw = _make_cal()
    assert cw._follow_state.follow_present is False


def test_go_today_enables_follow(qapp):
    cw = _make_cal()
    cw.set_view(ViewType.WEEK)
    cw.go_today()
    assert cw._follow_state.follow_present is True


def test_go_today_enables_follow_day_view(qapp):
    cw = _make_cal()
    cw.set_view(ViewType.DAY)
    cw.go_previous()  # move away from today first
    cw.go_today()
    assert cw._follow_state.follow_present is True


def test_go_previous_disables_follow(qapp):
    cw = _make_cal()
    cw.go_today()
    cw.go_previous()
    assert cw._follow_state.follow_present is False


def test_go_next_disables_follow(qapp):
    cw = _make_cal()
    cw.go_today()
    cw.go_next()
    assert cw._follow_state.follow_present is False


def test_set_date_disables_follow(qapp):
    cw = _make_cal()
    cw.go_today()
    cw.set_date(date.today() + timedelta(days=3))
    assert cw._follow_state.follow_present is False


def test_set_view_disables_follow(qapp):
    cw = _make_cal()
    cw.go_today()
    cw.set_view(ViewType.DAY)
    assert cw._follow_state.follow_present is False


# ----------------------------------------------------------------------
# user scroll disables follow
# ----------------------------------------------------------------------

def test_scrollbar_action_disables_follow(qapp):
    cw = _make_cal()
    cw.set_view(ViewType.WEEK)
    cw.go_today()
    # Simulate a user interaction on the actual QScrollBar (drag/trough).
    cw._week_view._scrollbar._bar.actionTriggered.emit(1)
    assert cw._follow_state.follow_present is False


def test_scrollbar_action_in_day_view_disables_follow(qapp):
    cw = _make_cal()
    cw.set_view(ViewType.DAY)
    cw.go_today()
    cw._day_view._scrollbar._bar.actionTriggered.emit(1)
    assert cw._follow_state.follow_present is False


def test_wheel_disables_follow(qapp):
    cw = _make_cal()
    cw.set_view(ViewType.WEEK)
    cw.go_today()
    event = QWheelEvent(
        QPointF(10, 10), QPointF(10, 10),
        QPoint(0, 0), QPoint(0, -120),
        Qt.NoButton, Qt.NoModifier, Qt.ScrollPhase.NoScrollPhase, False,
    )
    cw._week_view.wheelEvent(event)
    assert cw._follow_state.follow_present is False


def test_navigation_key_disables_follow(qapp):
    cw = _make_cal()
    cw.set_view(ViewType.WEEK)
    cw.go_today()
    event = QKeyEvent(QEvent.KeyPress, Qt.Key_Down, Qt.NoModifier)
    cw._week_view.keyPressEvent(event)
    assert cw._follow_state.follow_present is False


# ----------------------------------------------------------------------
# go-today invocation on hour-bar update (DayColumnWidget level)
# ----------------------------------------------------------------------

def _make_column(col_date, following):
    """Column wired like in production: callback assigned before follow can
    be enabled (follow only turns on via go_today, post-construction)."""
    mapper = MixedTimeAxis(get_hour_height(), get_layout_config().undistorted_hours)
    state = FollowState()
    col = DayColumnWidget(col_date, mapper, state)
    calls = []
    col._go_today_cb = lambda: calls.append(1)
    col.set_viewport(600, 0.0)
    state.follow_present = following
    return col, calls


def test_indicator_update_invokes_go_today_when_following(qapp):
    col, calls = _make_column(date.today(), following=True)
    col._update_time_indicator()
    assert len(calls) == 1


def test_indicator_update_no_go_today_when_not_following(qapp):
    col, calls = _make_column(date.today(), following=False)
    col._update_time_indicator()
    assert calls == []


def test_indicator_update_invokes_go_today_on_stale_column(qapp):
    """Midnight rollover: a column whose date is no longer today must still
    trigger go_today so the view advances to the new day/week."""
    col, calls = _make_column(date.today() - timedelta(days=1), following=True)
    col._update_time_indicator()
    assert len(calls) == 1


# ----------------------------------------------------------------------
# go_today rollover behavior (CalendarWidget level)
# ----------------------------------------------------------------------

def test_go_today_already_on_today_does_not_emit_date_changed(qapp):
    """Hour-bar ticks invoke go_today every minute — it must be cheap
    (no date_changed → no event refresh / state save) when date is unchanged."""
    cw = _make_cal()
    cw.set_view(ViewType.WEEK)
    cw.set_date(date.today())  # week view defaults to week start (Monday)
    seen = []
    cw.date_changed.connect(lambda d: seen.append(d))
    cw.go_today()
    assert seen == []


def test_go_today_rollover_advances_date(qapp):
    """Follow-mode tick on a stale day view advances to today (one emission)."""
    cw = _make_cal()
    cw.set_view(ViewType.DAY)
    cw.set_date(date.today() - timedelta(days=1))
    seen = []
    cw.date_changed.connect(lambda d: seen.append(d))
    cw.go_today()
    assert cw.get_current_date() == date.today()
    assert seen == [date.today()]
    # follow mode stays on after the rollover
    assert cw._follow_state.follow_present is True


def test_go_today_rollover_week_view(qapp):
    """Follow-mode tick on a week that no longer contains today advances
    to the current week."""
    cw = _make_cal()
    cw.set_view(ViewType.WEEK)
    cw.set_date(date.today() - timedelta(weeks=2))
    cw.go_today()
    assert cw.get_current_date() == date.today()
    assert cw._follow_state.follow_present is True
