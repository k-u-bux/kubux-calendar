"""Hour-line / time-label alignment in Day and Week views.

Regression tests for the "misaligned until first scroll" bug: after
navigation to a day without all-day events, hour lines and hour marks
must be aligned immediately (no pending singleShot left unprocessed).
"""

from datetime import date, datetime, timedelta, timezone

from PySide6.QtWidgets import QApplication

from gui.widgets.calendar_widget import CalendarWidget, ViewType
from backend.event import CalendarSource, EventView, ImmutableEvent


def _all_day_event(uid: str, day: date) -> EventView:
    """Build a display EventView for a one-day all-day event."""
    src = CalendarSource(id="test-src", name="Test")
    ev = ImmutableEvent.create_new(
        source_id=src.id,
        summary="All day",
        start=datetime(day.year, day.month, day.day, 0, 0, tzinfo=timezone.utc),
        end=datetime(day.year, day.month, day.day, 0, 0, tzinfo=timezone.utc) + timedelta(days=1),
        all_day=True,
    )
    return EventView(ev, src)


def _visible_hour_ys(view):
    """(hour, y) for currently visible hour lines in the view's first column."""
    col = view._day_columns[0]
    out = {}
    for hour in range(24):
        line = col._hour_lines[hour]
        if line.isVisible():
            out[hour] = line.y()
    return out


def _visible_label_ys(view):
    """(hour, y) for currently visible time labels in the view."""
    out = {}
    for hour, lbl in view._time_label_widgets:
        if lbl.isVisible():
            # Label is vertically centred on the hour line: its centre
            # should coincide with the hour line's y.
            out[hour] = lbl.y() + lbl.height() // 2
    return out


def _assert_geometry_consistent(view, msg):
    """The day column must fill the grid row: stale fixed heights are the
    misalignment bug (hour lines laid out for one height, labels for another)."""
    QApplication.processEvents()
    grid_h = view._grid_row.height()
    for i, col in enumerate(view._day_columns):
        assert col.height() == grid_h, (
            f"{msg}: column {i} height {col.height()} != grid {grid_h}"
        )


def _process_and_check(cw, view, msg):
    """Let Qt run deferred singleshot timers, then assert alignment."""
    # refresh_events schedules _sync_scrollbar_appearance via
    # singleshot(0); processing events fires it.
    QApplication.processEvents()
    QApplication.processEvents()

    lines = _visible_hour_ys(view)
    labels = _visible_label_ys(view)
    assert lines, f"{msg}: no hour lines visible"
    assert labels, f"{msg}: no time labels visible"

    shared = sorted(set(lines) & set(labels))
    assert shared, f"{msg}: no hours with both a line and a label visible"

    for hour in shared:
        assert lines[hour] == labels[hour], (
            f"{msg}: hour {hour}: line y={lines[hour]} "
            f"!= label centre y={labels[hour]}"
        )


def _resize(cw, w, h):
    cw.resize(w, h)
    QApplication.processEvents()
    QApplication.processEvents()


def test_week_no_all_day_events_aligned(qapp):
    """Navigate to a week with no all-day events: lines and marks aligned."""
    cw = CalendarWidget()
    cw.show()
    cw.set_view(ViewType.WEEK)
    _resize(cw, 1000, 700)

    target = date(2099, 7, 6)  # a Monday, empty store
    assert target.weekday() == 0
    cw.set_date(target)
    cw.set_events([])  # navigation flow: set_date then set_events
    _process_and_check(cw, cw._week_view, "week without all-day events")


def test_day_no_all_day_events_aligned(qapp):
    cw = CalendarWidget()
    cw.show()
    cw.set_view(ViewType.DAY)
    _resize(cw, 1000, 700)

    cw.set_date(date(2099, 7, 5))
    cw.set_events([])
    _process_and_check(cw, cw._day_view, "day without all-day events")


def test_week_with_all_day_events_aligned(qapp):
    """Sanity: the same flow WITH all-day events stays aligned."""
    cw = CalendarWidget()
    cw.show()
    cw.set_view(ViewType.WEEK)
    _resize(cw, 1000, 700)

    monday = date(2099, 7, 6)
    ev = _all_day_event("aligned-1", monday)
    cw.set_date(monday)
    cw.set_events([ev])
    _process_and_check(cw, cw._week_view, "week with all-day events")


def test_navigation_with_pending_layout_stays_aligned(qapp):
    """Navigate while the previous refresh's singleShot(0) is still pending.

    This is the real next/prev flow: the button handler runs synchronously
    inside the event loop, so the deferred _sync_scrollbar_appearance of
    the outgoing page is still queued when the new page's refresh_events
    updates the all-day row height.  Pre-fix, the column kept the stale
    fixed height and hour lines/labels ended up laid out for different
    viewport heights.
    """
    cw = CalendarWidget()
    cw.show()
    cw.set_view(ViewType.WEEK)
    _resize(cw, 1000, 700)

    monday = date(2099, 7, 6)
    ev = _all_day_event("aligned-2", monday)

    # All-day week, fully laid out.
    cw.set_date(monday)
    cw.set_events([ev])
    QApplication.processEvents()
    QApplication.processEvents()

    # Two rapid navigations before the event loop runs (fast next-next
    # clicks): all-day week → empty week → all-day week, each with its
    # deferred singleShot(0) still queued when the next one fires.
    cw.go_next()
    cw.set_events([])
    cw.go_previous()
    cw.set_events([ev])
    _assert_geometry_consistent(cw._week_view, "rapid double navigation")

    # Now a third navigation to the empty week — the state the user sees.
    cw.go_next()
    cw.set_events([])
    _assert_geometry_consistent(cw._week_view, "empty week after rapid nav")
    _process_and_check(cw, cw._week_view, "empty next week")

    # and back to the all-day week
    cw.go_previous()
    cw.set_events([ev])
    _assert_geometry_consistent(cw._week_view, "back to all-day week, tight loop")
    _process_and_check(cw, cw._week_view, "back to all-day week")
