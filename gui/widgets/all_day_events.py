"""
All-day event display widgets.

Extracted from ``calendar_widget.py``.
"""

from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFontMetrics

from backend import EventView as EventData
from .config_state import get_colors_config
from .event_widget import EventWidget


def _get_single_line_event_height() -> int:
    """Calculate height for a single-line event based on font metrics."""
    sample_label = QLabel("Sample")
    fm = QFontMetrics(sample_label.font())
    return fm.height() + 8  # font height + padding


class AllDayEventCell(QWidget):
    """A cell for displaying all-day events for a single day."""

    event_clicked = Signal(EventData)
    event_double_clicked = Signal(EventData)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._events: list[EventData] = []
        self._event_widgets: list[EventWidget] = []
        self._setup_ui()

    def _setup_ui(self):
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(2, 2, 2, 2)
        self._layout.setSpacing(2)
        colors = get_colors_config()
        self.setStyleSheet(f"background-color: {colors.allday_cell_background}; border-bottom: 1px solid {colors.cell_border};")

    def add_event(self, event: EventData):
        self._events.append(event)
        widget = EventWidget(event, compact=True, show_time=False, show_location=False, parent=self)
        event_height = _get_single_line_event_height()
        widget.setFixedHeight(event_height - 4)
        widget.clicked.connect(self.event_clicked.emit)
        widget.double_clicked.connect(self.event_double_clicked.emit)
        self._layout.addWidget(widget)
        self._layout.setAlignment(Qt.AlignTop)
        self._event_widgets.append(widget)

    def clear_events(self):
        for widget in self._event_widgets:
            widget.hide()  # Hide immediately to prevent visual duplication
            widget.deleteLater()
        self._event_widgets.clear()
        self._events.clear()

    def event_count(self) -> int:
        return len(self._events)


class AllDayEventsRow(QWidget):
    """Row displaying all-day events across multiple days."""

    event_clicked = Signal(EventData)
    event_double_clicked = Signal(EventData)

    def __init__(self, num_days: int = 1, parent=None):
        super().__init__(parent)
        self._num_days = num_days
        self._cells: list[AllDayEventCell] = []
        self._setup_ui()

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(1)

        for _ in range(self._num_days):
            cell = AllDayEventCell()
            cell.event_clicked.connect(self.event_clicked.emit)
            cell.event_double_clicked.connect(self.event_double_clicked.emit)
            layout.addWidget(cell, 1)
            self._cells.append(cell)

    def set_events_for_day(self, day_index: int, events: list[EventData]):
        """Set all-day events for a specific day column."""
        if 0 <= day_index < len(self._cells):
            self._cells[day_index].clear_events()
            for event in events:
                self._cells[day_index].add_event(event)

    def clear_all(self):
        for cell in self._cells:
            cell.clear_events()

    def get_max_events(self) -> int:
        """Get the maximum number of all-day events across all days."""
        return max((cell.event_count() for cell in self._cells), default=0)

    def update_height(self):
        """Update height based on maximum events across all days."""
        max_events = self.get_max_events()
        if max_events == 0:
            self.setFixedHeight(0)
            self.hide()
        else:
            event_height = _get_single_line_event_height()
            height = max_events * event_height + 4
            self.setFixedHeight(height)
            self.show()