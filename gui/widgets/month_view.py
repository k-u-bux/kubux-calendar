"""
MonthView: calendar grid view.

Extracted from ``calendar_widget.py``.
"""

from datetime import datetime, date, time as dt_time, timedelta
from typing import Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QFrame,
    QSizePolicy,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFontMetrics, QMouseEvent

from backend import EventView as EventData
from .config_state import (
    get_colors_config,
    get_localization_config,
    get_interface_font,
)
from .event_portion import is_all_day_event
from .event_widget import EventWidget, DraggableEventWidget, DragMode
from .all_day_events import _get_single_line_event_height
from library.timezone_utils import to_local_datetime


class MonthDayCell(QFrame):
    """Single day cell in month view."""

    clicked = Signal(date)
    double_clicked = Signal(date)
    event_clicked = Signal(EventData)
    event_double_clicked = Signal(EventData)
    event_drag_started = Signal(EventData, DragMode, int)
    event_drag_moved = Signal(EventData, DragMode, object)  # QPoint
    event_drag_finished = Signal(EventData, DragMode, object)  # QPoint

    def __init__(self, d: date, is_current_month: bool = True, parent=None):
        super().__init__(parent)
        self._date = d
        self.is_current_month = is_current_month
        self._event_widgets: list[EventWidget] = []
        self._setup_ui()

    @property
    def date(self):
        return self._date

    def _setup_ui(self):
        self.setFrameStyle(QFrame.Box | QFrame.Plain)
        # Calculate minimum size based on font metrics
        fm = QFontMetrics(self.font())
        line_height = fm.height()
        # Minimum: day number + space for 2 event lines + padding
        min_height = line_height + 2 * _get_single_line_event_height() + 12
        # Minimum width: enough for day number "00" + padding
        min_width = fm.horizontalAdvance("00") + 16
        self.setMinimumSize(max(min_width, 60), max(min_height, 60))
        self.setCursor(Qt.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)

        self._day_label = QLabel(str(self._date.day))
        self._day_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        layout.addWidget(self._day_label)

        self._events_layout = QVBoxLayout()
        self._events_layout.setSpacing(1)
        layout.addLayout(self._events_layout)
        layout.addStretch()

        self._update_style()

    def _update_style(self):
        colors = get_colors_config()
        bg = colors.month_cell_current if self.is_current_month else colors.month_cell_other
        text = colors.month_text_current if self.is_current_month else colors.month_text_other

        if self._date == date.today():
            self._day_label.setStyleSheet(f"color: {colors.today_highlight_text}; font-weight: bold; background: {colors.today_highlight_background}; border-radius: 10px; padding: 2px 6px;")
        else:
            self._day_label.setStyleSheet(f"color: {text};")

        self.setStyleSheet(f"background-color: {bg}; border: 1px solid {colors.cell_border};")

    def set_date(self, d: date, is_current_month: bool = True):
        self._date = d
        self.is_current_month = is_current_month
        self._day_label.setText(str(d.day))
        self._update_style()
        self.clear_events()

    def add_event(self, event: EventData):
        # Month view: title only, no location
        # Use DraggableEventWidget for editable events
        if event.read_only:
            widget = EventWidget(event, compact=True, show_time=False, show_location=False)
        else:
            widget = DraggableEventWidget(event, compact=True, show_time=False, show_location=False)
            widget.drag_started.connect(self.event_drag_started.emit)
            widget.drag_moved.connect(lambda e, m, p: self.event_drag_moved.emit(e, m, p))
            widget.drag_finished.connect(lambda e, m, p: self.event_drag_finished.emit(e, m, p))

        event_height = _get_single_line_event_height()
        widget.setMaximumHeight(event_height)
        widget.clicked.connect(self.event_clicked.emit)
        widget.double_clicked.connect(self.event_double_clicked.emit)
        self._events_layout.addWidget(widget)
        self._event_widgets.append(widget)

    def clear_events(self):
        for widget in self._event_widgets:
            widget.hide()  # Hide immediately to prevent visual duplication
            widget.deleteLater()
        self._event_widgets.clear()

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self._date)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self.double_clicked.emit(self._date)
        super().mouseDoubleClickEvent(event)


class MonthView(QWidget):
    """Month view showing a calendar grid."""

    day_clicked = Signal(date)
    day_double_clicked = Signal(date)
    event_clicked = Signal(EventData)
    event_double_clicked = Signal(EventData)
    event_time_changed = Signal(EventData, datetime, datetime)  # For drag-and-drop

    def __init__(self, parent=None):
        super().__init__(parent)
        self._year = date.today().year
        self._month = date.today().month
        self._events: list[EventData] = []
        self._cells: list[MonthDayCell] = []
        self._dragging_event: Optional[EventData] = None
        self._drag_original_start: Optional[datetime] = None
        self._drag_original_end: Optional[datetime] = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Day name headers
        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(1)

        self._header_labels = []
        font_name, font_size = get_interface_font()
        localization = get_localization_config()
        colors = get_colors_config()
        for i in range(7):
            day_name = localization.get_day_name_for_column(i)
            label = QLabel(day_name)
            label.setAlignment(Qt.AlignCenter)
            label.setStyleSheet(f"font-family: '{font_name}'; font-size: {font_size}pt; font-weight: bold; padding: 8px; background: {colors.header_background};")
            header_layout.addWidget(label, 1)
            self._header_labels.append(label)

        layout.addWidget(header)

        # Grid of day cells
        grid_widget = QWidget()
        self._grid_layout = QGridLayout(grid_widget)
        self._grid_layout.setContentsMargins(0, 0, 0, 0)
        self._grid_layout.setSpacing(1)

        # Set equal column stretch for all 7 days
        for col in range(7):
            self._grid_layout.setColumnStretch(col, 1)

        for row in range(6):
            for col in range(7):
                cell = MonthDayCell(date.today())
                cell.clicked.connect(self.day_clicked.emit)
                cell.double_clicked.connect(self.day_double_clicked.emit)
                cell.event_clicked.connect(self.event_clicked.emit)
                cell.event_double_clicked.connect(self.event_double_clicked.emit)
                cell.event_drag_started.connect(self._on_drag_started)
                cell.event_drag_finished.connect(self._on_drag_finished)
                self._grid_layout.addWidget(cell, row, col)
                self._cells.append(cell)

        layout.addWidget(grid_widget, 1)
        self._update_grid()

    def _update_grid(self):
        first_day = date(self._year, self._month, 1)
        localization = get_localization_config()
        grid_start = localization.get_week_start(first_day)

        for i, cell in enumerate(self._cells):
            cell_date = grid_start + timedelta(days=i)
            is_current = cell_date.month == self._month
            cell.set_date(cell_date, is_current)

    def set_month(self, year: int, month: int):
        self._year = year
        self._month = month
        self._update_grid()
        self.refresh_events()

    def set_date(self, d: date):
        self.set_month(d.year, d.month)

    def set_events(self, events: list[EventData]):
        self._events = events
        self.refresh_events()

    def refresh_events(self):
        for cell in self._cells:
            cell.clear_events()

        # All-day events first, then timed events chronologically by start.
        ordered = sorted(
            self._events,
            key=lambda e: (0 if is_all_day_event(e) else 1, to_local_datetime(e.start)),
        )
        for event in ordered:
            local_start = to_local_datetime(event.start)
            local_end = to_local_datetime(event.end)

            if is_all_day_event(event):
                # Multi-day all-day events should appear on each day they span
                start_date = local_start.date()
                end_date = local_end.date()
                # All-day events typically have end at midnight of next day, so subtract 1 day for display
                if end_date > start_date:
                    end_date = end_date - timedelta(days=1)

                # Add to each cell that falls within the event's date range
                for cell in self._cells:
                    if start_date <= cell.date <= end_date:
                        cell.add_event(event)
            else:
                # Timed event - only show on start day
                event_date = local_start.date()
                for cell in self._cells:
                    if cell.date == event_date:
                        cell.add_event(event)
                        break

    def get_date_range(self) -> tuple[datetime, datetime]:
        start = datetime.combine(self._cells[0].date, dt_time.min)
        end = datetime.combine(self._cells[-1].date, dt_time.max)
        return start, end

    def _on_drag_started(self, event: EventData, mode: DragMode, y_offset: int):
        """Handle drag start - store original times."""
        self._dragging_event = event
        self._drag_original_start = to_local_datetime(event.start)
        self._drag_original_end = to_local_datetime(event.end)

    def _find_target_day_cell(self, global_pos) -> Optional[date]:
        """Find which MonthDayCell is under the global position."""
        from .widget_utils import find_ancestor_widget

        cell = find_ancestor_widget(global_pos, MonthDayCell)
        return cell._date if cell is not None else None

    def _on_drag_finished(self, event: EventData, mode: DragMode, global_pos):
        """Handle drag completion - change only the date, keep original time."""
        if self._dragging_event is None or self._drag_original_start is None:
            return

        # Find target day cell
        target_date = self._find_target_day_cell(global_pos)
        if target_date is None:
            # Dropped outside any cell - cancel
            self._dragging_event = None
            return

        # Calculate new times: change date, keep original time and duration
        orig_start = self._drag_original_start
        orig_end = self._drag_original_end
        duration = orig_end - orig_start

        # Combine target date with original time
        new_start = datetime.combine(target_date, orig_start.time())
        new_end = new_start + duration

        # Emit signal with new times
        self.event_time_changed.emit(event, new_start, new_end)

        # Reset drag state
        self._dragging_event = None
        self._drag_original_start = None
        self._drag_original_end = None

    def refresh_styles(self):
        """Refresh header styles after config change."""
        font_name, font_size = get_interface_font()
        colors = get_colors_config()
        for label in self._header_labels:
            label.setStyleSheet(f"font-family: '{font_name}'; font-size: {font_size}pt; font-weight: bold; padding: 8px; background: {colors.header_background};")