"""
TimelineViewBase: shared base for Day and Week views.

Extracts the common infrastructure that both views duplicate:
  - Time labels column (hour markers)
  - All-day events row with scrollbar-aligned spacers
  - Scroll area with scrollbar-visibility sync
  - get/set scroll position
  - refresh_events template (all-day vs timed separation)

Subclasses provide day columns and optional header via hooks.
"""

from datetime import datetime, date, time as dt_time, timedelta

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QScrollArea, QLabel,
    QApplication, QStyle,
)
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QFontMetrics

from backend import EventView as EventData
from .config_state import (
    get_colors_config,
    get_hour_height,
)
from .event_portion import EventPortion, is_all_day_event
from .all_day_events import AllDayEventsRow
from .day_column import DayColumnWidget
from .time_axis import TimeAxisMapper, LinearTimeAxis
from library.timezone_utils import to_local_datetime


def _get_time_column_width() -> int:
    """Calculate time column width based on actual font metrics."""
    sample_label = QLabel("00:00")
    metrics = QFontMetrics(sample_label.font())
    # Measure the text plus padding for right margin
    return metrics.horizontalAdvance("00:00") + 15


class TimelineViewBase(QWidget):
    """
    Base class for timeline-based calendar views (Day, Week).

    Provides the shared scroll area, time labels, all-day events row,
    and event-refresh logic.  Subclasses implement hooks to supply
    day columns, optional headers, and date arithmetic.
    """

    # Signals (re-emitted from child widgets)
    slot_clicked = Signal(datetime)
    slot_double_clicked = Signal(datetime)
    event_clicked = Signal(EventData)
    event_double_clicked = Signal(EventData)
    event_time_changed = Signal(EventData, datetime, datetime)  # For drag-and-drop

    def __init__(self, parent=None):
        super().__init__(parent)
        self._events: list[EventData] = []
        self._day_columns: list[DayColumnWidget] = []
        self._mapper = LinearTimeAxis(get_hour_height())
        self._setup_ui()

    # ------------------------------------------------------------------
    # Hooks for subclasses
    # ------------------------------------------------------------------

    def _get_num_days(self) -> int:
        """Return the number of day columns in this view."""
        raise NotImplementedError

    def _get_day_dates(self) -> list[date]:
        """Return the list of dates for each day column."""
        raise NotImplementedError

    def _create_day_columns(self) -> list[DayColumnWidget]:
        """Create and return the list of day column widgets."""
        raise NotImplementedError

    def _create_header(self, time_col_width: int, scrollbar_width: int) -> QWidget | None:
        """Create an optional header widget above the all-day row.

        Returns None if the view has no header (default).
        """
        return None

    def _on_scrollbar_visibility_changed(self, needs_scrollbar: bool) -> None:
        """Hook called when the main scrollbar visibility changes.

        Subclasses with a header can use this to show/hide header spacers.
        """
        pass

    def _update_headers(self) -> None:
        """Update header labels (e.g. day names).  Default: do nothing."""
        pass

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        time_col_width = _get_time_column_width()
        scrollbar_width = QApplication.style().pixelMetric(QStyle.PM_ScrollBarExtent)

        # Optional header (subclasses may override)
        header = self._create_header(time_col_width, scrollbar_width)
        if header is not None:
            main_layout.addWidget(header)

        # All-day events section
        all_day_container = QWidget()
        all_day_layout = QHBoxLayout(all_day_container)
        all_day_layout.setContentsMargins(0, 0, 0, 0)
        all_day_layout.setSpacing(0)

        # Spacer to align with time column
        colors = get_colors_config()
        all_day_spacer = QWidget()
        all_day_spacer.setStyleSheet(f"background: {colors.header_background};")
        all_day_spacer.setFixedWidth(time_col_width)
        all_day_layout.addWidget(all_day_spacer)

        # All-day events row
        num_days = self._get_num_days()
        self._all_day_row = AllDayEventsRow(num_days=num_days)
        self._all_day_row.event_clicked.connect(self.event_clicked.emit)
        self._all_day_row.event_double_clicked.connect(self.event_double_clicked.emit)
        self._all_day_row.hide()  # Hidden initially
        all_day_layout.addWidget(self._all_day_row, 1)

        # Dynamic scrollbar-width spacer
        self._all_day_scrollbar_spacer = QWidget()
        self._all_day_scrollbar_spacer.setFixedWidth(scrollbar_width)
        self._all_day_scrollbar_spacer.hide()
        all_day_layout.addWidget(self._all_day_scrollbar_spacer)

        main_layout.addWidget(all_day_container)

        # Scroll area for time grid
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        # Content: time labels + day columns (inline, scroll together)
        content = QWidget()
        content_layout = QHBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        # Time labels
        time_widget = self._create_time_labels(time_col_width)
        content_layout.addWidget(time_widget)

        # Day columns (created by subclass)
        self._day_columns = self._create_day_columns()
        for col in self._day_columns:
            col.slot_clicked.connect(self.slot_clicked.emit)
            col.slot_double_clicked.connect(self.slot_double_clicked.emit)
            col.event_clicked.connect(self.event_clicked.emit)
            col.event_double_clicked.connect(self.event_double_clicked.emit)
            col.event_time_changed.connect(self.event_time_changed.emit)
            content_layout.addWidget(col, 1)

        scroll.setWidget(content)
        self._scroll = scroll
        main_layout.addWidget(scroll, 1)

        # Sync all-day/header spacers with scrollbar visibility.
        # Also push viewport dimensions to day columns on scroll/resize.
        def _on_scrollbar_range_changed(min_val, max_val):
            needs_scrollbar = max_val > 0
            self._all_day_scrollbar_spacer.setVisible(needs_scrollbar)
            self._on_scrollbar_visibility_changed(needs_scrollbar)
            self._push_viewport_to_columns()

        scroll.verticalScrollBar().rangeChanged.connect(_on_scrollbar_range_changed)
        scroll.verticalScrollBar().valueChanged.connect(lambda _: self._push_viewport_to_columns())
        QTimer.singleShot(0, lambda: _on_scrollbar_range_changed(
            scroll.verticalScrollBar().minimum(),
            scroll.verticalScrollBar().maximum()))

    def _push_viewport_to_columns(self):
        """Push current viewport height and scroll offset to all day columns."""
        viewport = self._scroll.viewport()
        if not viewport:
            return
        vh = viewport.height()
        so = self._scroll.verticalScrollBar().value()
        for col in self._day_columns:
            col.set_viewport(vh, so)

    def _create_time_labels(self, time_col_width: int) -> QWidget:
        """Create the time labels column (shared by Day and Week views)."""
        colors = get_colors_config()
        hour_height = get_hour_height()
        content_h = 24 * hour_height

        time_widget = QWidget()
        time_widget.setFixedWidth(time_col_width)
        time_widget.setFixedHeight(content_h)
        time_widget.setStyleSheet(f" background: {colors.header_background};")
        time_layout = QVBoxLayout(time_widget)
        time_layout.setContentsMargins(0, 0, 0, 0)
        time_layout.setSpacing(0)

        # Spacer to align labels with hour lines
        top_spacer = QWidget()
        top_spacer.setFixedHeight(int(0.5 * hour_height))
        time_layout.addWidget(top_spacer)

        # Labels 01:00 - 23:00
        for hour in range(1, 24):
            lbl = QLabel(f"{hour:02d}:00")
            lbl.setFixedHeight(hour_height)
            lbl.setAlignment(Qt.AlignCenter)
            time_layout.addWidget(lbl)

        bot_spacer = QWidget()
        bot_spacer.setFixedHeight(int(0.5 * hour_height))
        time_layout.addWidget(bot_spacer)

        return time_widget

    # ------------------------------------------------------------------
    # Scroll position (shared)
    # ------------------------------------------------------------------

    def get_scroll_position(self) -> int:
        """Get current vertical scroll position."""
        return self._scroll.verticalScrollBar().value()

    def set_scroll_position(self, position: int):
        """Set vertical scroll position."""
        self._scroll.verticalScrollBar().setValue(position)
        self._push_viewport_to_columns()

    # ------------------------------------------------------------------
    # Event handling (shared template)
    # ------------------------------------------------------------------

    def set_events(self, events: list[EventData]):
        self._events = events
        self.refresh_events()

    def refresh_events(self):
        """Refresh events — shared logic for all-day vs timed separation."""
        for col in self._day_columns:
            col.clear_portions()
        self._all_day_row.clear_all()

        num_days = self._get_num_days()
        day_dates = self._get_day_dates()

        # Group all-day events by day
        all_day_by_day: list[list[EventData]] = [[] for _ in range(num_days)]

        for event in self._events:
            local_start = to_local_datetime(event.start)
            local_end = to_local_datetime(event.end)

            if is_all_day_event(event):
                # Multi-day all-day events appear on each day they span
                start_date = local_start.date()
                end_date = local_end.date()
                # All-day events typically have end at midnight of next day
                if end_date > start_date:
                    end_date = end_date - timedelta(days=1)

                for day_idx in range(num_days):
                    if start_date <= day_dates[day_idx] <= end_date:
                        all_day_by_day[day_idx].append(event)
            else:
                # Timed event — create portions for each day it spans
                for day_idx in range(num_days):
                    portion = EventPortion.create_for_day(event, day_dates[day_idx])
                    if portion:
                        self._day_columns[day_idx].add_portion(portion)

        # Add all-day events to their respective day cells
        for day_idx, events in enumerate(all_day_by_day):
            self._all_day_row.set_events_for_day(day_idx, events)

        # Finalize event layouts for all day columns
        for col in self._day_columns:
            col.finalize_portions()

        # Update the all-day row height
        self._all_day_row.update_height()

    def refresh_styles(self):
        """Refresh header styles after config change.  Default: update headers."""
        self._update_headers()