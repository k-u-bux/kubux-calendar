"""TimelineViewBase: shared base for Day and Week views.

Extracts the common infrastructure that both views duplicate:
  - Time labels column (hour markers)
  - All-day events row with scrollbar-aligned spacers
  - Standalone scrollbar (range [0, 1000]; mapper controls handle size)
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
from PySide6.QtGui import QFontMetrics, QWheelEvent, QKeyEvent

from backend import EventView as EventData
from .config_state import (
    get_colors_config,
    get_hour_height,
    get_layout_config,
)
from .event_portion import EventPortion, is_all_day_event
from .all_day_events import AllDayEventsRow
from .day_column import DayColumnWidget
from .time_axis import LinearTimeAxis, QuadraticCompressionAxis, MixedTimeAxis
from .shared_scrollbar import SharedScrollBar, _ScrollBarState
from library.timezone_utils import to_local_datetime


def _get_time_column_width() -> int:
    """Calculate time column width based on actual font metrics."""
    sample_label = QLabel("00:00")
    metrics = QFontMetrics(sample_label.font())
    return metrics.horizontalAdvance("00:00") + 15


_WHEEL_MULTIPLIER = 4


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

    def __init__(self, parent=None, mapper=None, scroll_state: _ScrollBarState = None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.StrongFocus)
        self._events: list[EventData] = []
        self._day_columns: list[DayColumnWidget] = []
        self._needs_scrollbar_sync = False

        if mapper is not None:
            self._mapper = mapper
        else:
            self._mapper = MixedTimeAxis(get_hour_height(), get_layout_config().undistorted_hours)

        self._scroll_state = scroll_state
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
        self._grid_row = grid_row
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

        # Standalone scrollbar — range always [0, 1000]
        if self._scroll_state is not None:
            self._scrollbar = SharedScrollBar(self._scroll_state, self)
        else:
            self._scrollbar = QScrollBar(Qt.Vertical)
            self._scrollbar.setRange(0, 1000)
            self._scrollbar.setSingleStep(1)
        self._scrollbar.valueChanged.connect(self._on_scrollbar_value_changed)
        grid_layout.addWidget(self._scrollbar)

        main_layout.addWidget(grid_row, 1)

        # Initial viewport push + scrollbar range setup
        QTimer.singleShot(0, self._sync_scrollbar_appearance)

    def _on_scrollbar_value_changed(self, value: int):
        """Scrollbar moved — push new scroll ratio to day columns."""
        self._push_viewport_to_columns()

    def _sync_scrollbar_appearance(self):
        """Update scrollbar handle size and push viewport."""
        vh = self._grid_content_height()
        if vh <= 0:
            return
        raw = self._mapper.scrollbar_height(vh)  # r = 1000 * H/24
        # Qt handle fraction = pageStep / (1000 + pageStep)
        # We want fraction = H/24 = raw/1000
        # pageStep = 1000 * raw / (1000 - raw)
        if raw >= 1000:
            page_step = 1000
        else:
            page_step = int(1000 * raw / (1000 - raw))
        page_step = min(page_step, 1000)
        self._scrollbar.setPageStep(page_step)
        needs_scrollbar = raw < 1000
        self._scrollbar.setVisible(needs_scrollbar)
        self._all_day_scrollbar_spacer.setVisible(needs_scrollbar)
        self._on_scrollbar_visibility_changed(needs_scrollbar)
        self._push_viewport_to_columns()

    def _grid_content_height(self) -> int:
        """Height of the grid row (the area shared by time labels + day columns)."""
        return self._grid_row.height()

    def _push_viewport_to_columns(self):
        """Push current viewport height and scroll ratio to all day columns."""
        vh = self._grid_content_height()
        if vh <= 0:
            return
        ratio = self._scrollbar.value() / 1000.0
        for col in self._day_columns:
            col.set_viewport(vh, ratio)
        self._position_time_labels()

    # ------------------------------------------------------------------
    # Time labels (absolute-positioned inside a fixed-width container)
    # ------------------------------------------------------------------

    def _create_time_labels(self, time_col_width: int) -> QWidget:
        colors = get_colors_config()

        container = QWidget()
        container.setFixedWidth(time_col_width)
        container.setStyleSheet(f"background: {colors.header_background};")

        self._time_label_widgets: list[tuple[int, QLabel]] = []  # (hour, label)
        # Interleaved order: 1, 23, 2, 22, 3, 21, … so overlapping labels
        # show the hours closest to the visible center on top.
        for i in range(12):
            hour = i + 1
            lbl = QLabel(f"{hour:02d}:00", container)
            lbl.setAlignment(Qt.AlignCenter)
            lbl.hide()
            self._time_label_widgets.append((hour, lbl))
            if hour < 12:
                hour_opp = 24 - hour
                lbl = QLabel(f"{hour_opp:02d}:00", container)
                lbl.setAlignment(Qt.AlignCenter)
                lbl.hide()
                self._time_label_widgets.append((hour_opp, lbl))

        return container

    def _position_time_labels(self):
        vh = self._grid_content_height()
        if vh <= 0:
            return
        ratio = self._scrollbar.value() / 1000.0

        # Use actual QLabel text height, not hour_height from config
        label_h = max(lbl.sizeHint().height() for _, lbl in self._time_label_widgets)
        label_w = self._time_labels_widget.width()

        # Build hour → label lookup for O(1) access
        labels_by_hour: dict[int, QLabel] = {h: lbl for h, lbl in self._time_label_widgets}

        # Collect pixel Y positions for all 24 hours
        positions: list[tuple[int, int, float]] = []  # (hour, pixel_y, norm_y)
        for hour, _ in self._time_label_widgets:
            y_norm = self._mapper.hour_to_y(float(hour), vh, ratio)
            y_px = int(y_norm * vh)
            positions.append((hour, y_px, y_norm))

        for hour, y_px, y_norm in positions:
            lbl = labels_by_hour[hour]

            # Check if this label is within the viewport
            if not (0 < y_px < vh):
                lbl.hide()
                continue

            label_y = y_px - label_h // 2
            lbl.setGeometry(0, label_y, label_w, label_h)
            lbl.show()

    # ------------------------------------------------------------------
    # Scroll position (shared)
    # ------------------------------------------------------------------

    def get_scroll_position(self) -> int:
        return self._scrollbar.value()

    def set_scroll_position(self, position: int):
        self._scrollbar.setValue(position)
        self._push_viewport_to_columns()

    def scroll_to_center_hour(self, hour: float):
        """Scroll so that *hour* is centred in the undistorted/lens window."""
        vh = self._grid_content_height()
        if vh <= 0:
            return
        ratio = self._mapper.scroll_ratio_for_hour(hour, vh)
        self.set_scroll_position(int(ratio * 1000))

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
        QTimer.singleShot(0, self._sync_scrollbar_appearance)

    def refresh_styles(self):
        self._update_headers()

    # ------------------------------------------------------------------
    # Resize — re-sync scrollbar appearance + push viewport
    # ------------------------------------------------------------------

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_scrollbar_appearance()

    # ------------------------------------------------------------------
    # Input events handled directly (standalone scrollbar has no focus)
    # ------------------------------------------------------------------

    def wheelEvent(self, event: QWheelEvent):
        scaled_delta = event.angleDelta() * _WHEEL_MULTIPLIER
        scaled_event = QWheelEvent(
            event.position(), event.globalPosition(),
            event.pixelDelta(), scaled_delta,
            event.buttons(), event.modifiers(),
            event.phase(), event.inverted(), event.source(),
        )
        self._scrollbar.wheelEvent(scaled_event)

    def keyPressEvent(self, event: QKeyEvent):
        """Navigation keys manipulate the scrollbar value directly.

        Arrow keys = 30 min (1/48 of 24h range).
        Page keys = ~1/8 viewport.
        Home/End = bounds.
        """
        sb = self._scrollbar
        page = sb.pageStep() or 50
        small = 1000 // 48  # 30 minutes
        page_small = max(page // 8, 1)

        key = event.key()
        if key == Qt.Key_Up:
            sb.setValue(sb.value() - small)
        elif key == Qt.Key_Down:
            sb.setValue(sb.value() + small)
        elif key == Qt.Key_PageUp:
            sb.setValue(sb.value() - page_small)
        elif key == Qt.Key_PageDown:
            sb.setValue(sb.value() + page_small)
        elif key == Qt.Key_Home:
            sb.setValue(0)
        elif key == Qt.Key_End:
            sb.setValue(1000)
        else:
            super().keyPressEvent(event)
