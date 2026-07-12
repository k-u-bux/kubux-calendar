"""
TimelineViewBase: shared base for Day and Week views.

Extracts the common infrastructure that both views duplicate:
  - Time labels column (hour markers)
  - All-day events row with scrollbar-aligned spacers
  - Standalone scrollbar (not a QScrollArea — the mapper handles all positioning)
  - get/set scroll position
  - refresh_events template (all-day vs timed separation)

Subclasses provide day columns and optional header via hooks.
"""

from datetime import datetime, date, time as dt_time, timedelta

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QScrollBar, QLabel,
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
    return metrics.horizontalAdvance("00:00") + 15


class TimelineViewBase(QWidget):
    """
    Base class for timeline-based calendar views (Day, Week).

    Provides a standalone scrollbar, time labels, all-day events row,
    and event-refresh logic.  Day columns fill the viewport; the mapper
    translates scrollbar position into visible-hour geometry.
    """

    # Signals
    slot_clicked = Signal(datetime)
    slot_double_clicked = Signal(datetime)
    event_clicked = Signal(EventData)
    event_double_clicked = Signal(EventData)
    event_time_changed = Signal(EventData, datetime, datetime)

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
        raise NotImplementedError

    def _get_day_dates(self) -> list[date]:
        raise NotImplementedError

    def _create_day_columns(self) -> list[DayColumnWidget]:
        raise NotImplementedError

    def _create_header(self, time_col_width: int, scrollbar_width: int) -> QWidget | None:
        return None

    def _on_scrollbar_visibility_changed(self, needs_scrollbar: bool) -> None:
        pass

    def _update_headers(self) -> None:
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

        # Optional header
        header = self._create_header(time_col_width, scrollbar_width)
        if header is not None:
            main_layout.addWidget(header)

        # All-day events section
        all_day_container = QWidget()
        all_day_layout = QHBoxLayout(all_day_container)
        all_day_layout.setContentsMargins(0, 0, 0, 0)
        all_day_layout.setSpacing(0)

        colors = get_colors_config()
        all_day_spacer = QWidget()
        all_day_spacer.setStyleSheet(f"background: {colors.header_background};")
        all_day_spacer.setFixedWidth(time_col_width)
        all_day_layout.addWidget(all_day_spacer)

        num_days = self._get_num_days()
        self._all_day_row = AllDayEventsRow(num_days=num_days)
        self._all_day_row.event_clicked.connect(self.event_clicked.emit)
        self._all_day_row.event_double_clicked.connect(self.event_double_clicked.emit)
        self._all_day_row.hide()
        all_day_layout.addWidget(self._all_day_row, 1)

        self._all_day_scrollbar_spacer = QWidget()
        self._all_day_scrollbar_spacer.setFixedWidth(scrollbar_width)
        self._all_day_scrollbar_spacer.hide()
        all_day_layout.addWidget(self._all_day_scrollbar_spacer)

        main_layout.addWidget(all_day_container)

        # Main area: time labels + day columns + scrollbar
        grid_row = QWidget()
        grid_layout = QHBoxLayout(grid_row)
        grid_layout.setContentsMargins(0, 0, 0, 0)
        grid_layout.setSpacing(0)

        # Time labels (overlaid in a fixed-width strip, repositioned on scroll)
        self._time_labels_widget = self._create_time_labels(time_col_width)
        grid_layout.addWidget(self._time_labels_widget)

        # Day columns (created by subclass)
        self._day_columns = self._create_day_columns()
        for col in self._day_columns:
            col.slot_clicked.connect(self.slot_clicked.emit)
            col.slot_double_clicked.connect(self.slot_double_clicked.emit)
            col.event_clicked.connect(self.event_clicked.emit)
            col.event_double_clicked.connect(self.event_double_clicked.emit)
            col.event_time_changed.connect(self.event_time_changed.emit)
            grid_layout.addWidget(col, 1)

        # Standalone scrollbar
        self._scrollbar = QScrollBar(Qt.Vertical)
        self._scrollbar.valueChanged.connect(self._on_scrollbar_value_changed)
        grid_layout.addWidget(self._scrollbar)

        main_layout.addWidget(grid_row, 1)

        # Initial viewport push + scrollbar range setup
        QTimer.singleShot(0, self._sync_scrollbar_range)

    def _on_scrollbar_value_changed(self, value: int):
        """Scrollbar moved — push new scroll offset to day columns."""
        self._push_viewport_to_columns()

    def _sync_scrollbar_range(self):
        """Update scrollbar range from mapper and push viewport."""
        vh = self._grid_content_height()
        if vh <= 0:
            return
        max_val = self._mapper.scrollbar_maximum(vh)
        needs_scrollbar = max_val > 0
        self._scrollbar.setRange(0, max_val)
        self._scrollbar.setVisible(needs_scrollbar)
        self._all_day_scrollbar_spacer.setVisible(needs_scrollbar)
        self._on_scrollbar_visibility_changed(needs_scrollbar)
        self._push_viewport_to_columns()

    def _grid_content_height(self) -> int:
        """Height of the grid row (the area shared by time labels + day columns)."""
        # The grid row fills available space; use the time labels widget height
        # as reference (it's a fixed-width strip that stretches).
        return self._time_labels_widget.height()

    def _push_viewport_to_columns(self):
        """Push current viewport height and scroll offset to all day columns."""
        vh = self._grid_content_height()
        if vh <= 0:
            return
        so = self._scrollbar.value()
        for col in self._day_columns:
            col.set_viewport(vh, so)
        self._position_time_labels()

    # ------------------------------------------------------------------
    # Time labels (absolute-positioned inside a fixed-width container)
    # ------------------------------------------------------------------

    def _create_time_labels(self, time_col_width: int) -> QWidget:
        """Create the time labels container."""
        colors = get_colors_config()
        hour_height = get_hour_height()

        container = QWidget()
        container.setFixedWidth(time_col_width)
        container.setStyleSheet(f"background: {colors.header_background};")

        self._time_label_widgets: list[QLabel] = []
        for hour in range(24):
            lbl = QLabel(f"{hour:02d}:00", container)
            lbl.setFixedHeight(hour_height)
            lbl.setAlignment(Qt.AlignCenter)
            lbl.hide()
            self._time_label_widgets.append(lbl)

        return container

    def _position_time_labels(self):
        """Reposition time labels according to current viewport + scroll."""
        vh = self._grid_content_height()
        if vh <= 0:
            return
        so = self._scrollbar.value()

        for hour, lbl in enumerate(self._time_label_widgets):
            y = self._mapper.hour_to_y(float(hour), vh, so) * vh
            # Labels are positioned centered on the hour line:
            # hour line at y, label height = hour_height
            hour_height = get_hour_height()
            label_y = y - hour_height / 2
            if -hour_height < label_y < vh:
                lbl.setGeometry(0, int(label_y), lbl.parent().width(), hour_height)
                lbl.show()
            else:
                lbl.hide()

    # ------------------------------------------------------------------
    # Scroll position (shared)
    # ------------------------------------------------------------------

    def get_scroll_position(self) -> int:
        return self._scrollbar.value()

    def set_scroll_position(self, position: int):
        self._scrollbar.setValue(position)
        self._push_viewport_to_columns()

    # ------------------------------------------------------------------
    # Event handling (shared template)
    # ------------------------------------------------------------------

    def set_events(self, events: list[EventData]):
        self._events = events
        self.refresh_events()

    def refresh_events(self):
        for col in self._day_columns:
            col.clear_portions()
        self._all_day_row.clear_all()

        num_days = self._get_num_days()
        day_dates = self._get_day_dates()

        all_day_by_day: list[list[EventData]] = [[] for _ in range(num_days)]

        for event in self._events:
            local_start = to_local_datetime(event.start)
            local_end = to_local_datetime(event.end)

            if is_all_day_event(event):
                start_date = local_start.date()
                end_date = local_end.date()
                if end_date > start_date:
                    end_date = end_date - timedelta(days=1)

                for day_idx in range(num_days):
                    if start_date <= day_dates[day_idx] <= end_date:
                        all_day_by_day[day_idx].append(event)
            else:
                for day_idx in range(num_days):
                    portion = EventPortion.create_for_day(event, day_dates[day_idx])
                    if portion:
                        self._day_columns[day_idx].add_portion(portion)

        for day_idx, events in enumerate(all_day_by_day):
            self._all_day_row.set_events_for_day(day_idx, events)

        for col in self._day_columns:
            col.finalize_portions()

        self._all_day_row.update_height()

    def refresh_styles(self):
        self._update_headers()

    # ------------------------------------------------------------------
    # Resize — re-sync scrollbar range + push viewport
    # ------------------------------------------------------------------

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_scrollbar_range()