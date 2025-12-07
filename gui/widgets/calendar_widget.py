"""
Calendar Widget with Day, Week, and Month views.
"""

from datetime import datetime, timedelta, date, time as dt_time
from typing import Optional
from enum import Enum

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QScrollArea, QFrame, QSizePolicy, QStackedWidget
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QFontMetrics, QMouseEvent

from backend.caldav_client import EventData
from backend.config import LayoutConfig
from .event_widget import EventWidget, set_event_layout_config

# Module-level layout config (set by MainWindow at startup)
_layout_config: LayoutConfig = LayoutConfig()

# Module-level hour height (updated when layout config is set)
HOUR_HEIGHT = 60  # Default value


def set_layout_config(config: LayoutConfig):
    """Set the layout configuration for this module and event widget."""
    global _layout_config, HOUR_HEIGHT
    _layout_config = config
    HOUR_HEIGHT = config.hour_height
    # Also set for event widgets
    set_event_layout_config(config)


def get_hour_height() -> int:
    """Get the configured hour height in pixels."""
    return HOUR_HEIGHT


def get_text_font() -> tuple[str, int]:
    """Get the configured text font name and size."""
    return (_layout_config.text_font, _layout_config.text_font_size)


def get_interface_font() -> tuple[str, int]:
    """Get the configured interface font name and size."""
    return (_layout_config.interface_font, _layout_config.interface_font_size)


# Get local timezone offset dynamically
import time as _time


def _get_local_tz_offset() -> timedelta:
    """Get the current local timezone offset from UTC."""
    # Check if DST is currently active
    is_dst = _time.localtime().tm_isdst
    if is_dst:
        offset_seconds = -_time.altzone
    else:
        offset_seconds = -_time.timezone
    return timedelta(seconds=offset_seconds)


def to_local_hour(dt: datetime) -> float:
    """Convert datetime to local timezone and return hour as float (e.g., 14.5 for 14:30)."""
    if dt.tzinfo is not None:
        # Convert from UTC to local
        local_dt = dt + _get_local_tz_offset()
    else:
        local_dt = dt
    return local_dt.hour + local_dt.minute / 60.0


def to_local_datetime(dt: datetime) -> datetime:
    """Convert datetime to local timezone."""
    if dt.tzinfo is not None:
        return dt + _get_local_tz_offset()
    return dt


class ViewType(Enum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"


def _get_single_line_event_height() -> int:
    """Calculate height for a single-line event based on font metrics."""
    sample_label = QLabel("Sample")
    fm = QFontMetrics(sample_label.font())
    return fm.height() + 8  # font height + padding


def is_all_day_event(event: 'EventData') -> bool:
    """Check if an event is an all-day event."""
    return event.all_day


def _get_time_column_width() -> int:
    """Calculate time column width based on actual font metrics."""
    sample_label = QLabel("00:00")
    metrics = QFontMetrics(sample_label.font())
    # Measure the text plus padding for right margin
    return metrics.horizontalAdvance("00:00") + 15


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
        self.setStyleSheet("background-color: #fafafa; border-bottom: 1px solid #e0e0e0;")
    
    def add_event(self, event: EventData):
        self._events.append(event)
        widget = EventWidget(event, compact=True, show_time=False, parent=self)
        event_height = _get_single_line_event_height()
        widget.setFixedHeight(event_height - 4)
        widget.clicked.connect(self.event_clicked.emit)
        widget.double_clicked.connect(self.event_double_clicked.emit)
        self._layout.addWidget(widget)
        self._event_widgets.append(widget)
    
    def clear_events(self):
        for widget in self._event_widgets:
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


class DayColumnWidget(QWidget):
    """
    A single day column with absolute positioning for events.
    Events span according to their duration. Overlapping events are placed side by side.
    """
    
    slot_clicked = Signal(datetime)
    slot_double_clicked = Signal(datetime)
    event_clicked = Signal(EventData)
    event_double_clicked = Signal(EventData)
    
    def __init__(self, for_date: date, parent=None):
        super().__init__(parent)
        self._date = for_date
        self._events: list[EventData] = []
        self._event_widgets: list[EventWidget] = []
        self._event_layout: list[tuple[EventData, int, int]] = []  # (event, column, total_columns)
        self._setup_ui()
    
    def _setup_ui(self):
        # Fixed height for 24 hours
        self.setMinimumHeight(24 * HOUR_HEIGHT)
        self.setMaximumHeight(24 * HOUR_HEIGHT)
        self.setStyleSheet("background-color: white; border: 1px solid #e0e0e0;")
        self.setCursor(Qt.PointingHandCursor)
        
        # Draw hour lines
        for hour in range(1, 24):
            line = QFrame(self)
            line.setFrameStyle(QFrame.HLine | QFrame.Plain)
            line.setStyleSheet("background-color: #e8e8e8;")
            line.setGeometry(0, hour * HOUR_HEIGHT, 2000, 1)
    
    def set_date(self, new_date: date):
        self._date = new_date
        self._refresh_events()
    
    def add_event(self, event: EventData):
        self._events.append(event)
    
    def finalize_events(self):
        """Call after all events are added to calculate layout and create widgets."""
        self._calculate_layout()
        self._create_event_widgets()
    
    def _events_overlap(self, e1: EventData, e2: EventData) -> bool:
        """Check if two events overlap in time."""
        s1 = to_local_hour(e1.start)
        e1_end = to_local_hour(e1.end)
        s2 = to_local_hour(e2.start)
        e2_end = to_local_hour(e2.end)
        # Ensure minimum duration
        if e1_end <= s1:
            e1_end = s1 + 0.5
        if e2_end <= s2:
            e2_end = s2 + 0.5
        return s1 < e2_end and s2 < e1_end
    
    def _calculate_layout(self):
        """Calculate column positions for overlapping events."""
        if not self._events:
            self._event_layout = []
            return
        
        # Sort events by start time, then by duration (longer first)
        sorted_events = sorted(self._events, key=lambda e: (to_local_hour(e.start), -(to_local_hour(e.end) - to_local_hour(e.start))))
        
        # Assign columns to events
        # Each event gets (column_index, total_columns_in_group)
        event_columns: dict[str, int] = {}  # event.uid -> column
        event_groups: list[list[EventData]] = []  # groups of overlapping events
        
        # Build overlap groups
        for event in sorted_events:
            # Find which existing groups this event overlaps with
            overlapping_groups = []
            for i, group in enumerate(event_groups):
                for group_event in group:
                    if self._events_overlap(event, group_event):
                        overlapping_groups.append(i)
                        break
            
            if not overlapping_groups:
                # Start a new group
                event_groups.append([event])
            elif len(overlapping_groups) == 1:
                # Add to existing group
                event_groups[overlapping_groups[0]].append(event)
            else:
                # Merge groups
                merged = []
                for i in sorted(overlapping_groups, reverse=True):
                    merged.extend(event_groups.pop(i))
                merged.append(event)
                event_groups.append(merged)
        
        # Assign column numbers within each group
        self._event_layout = []
        for group in event_groups:
            # Sort group by start time
            group.sort(key=lambda e: to_local_hour(e.start))
            
            # Assign columns greedily
            columns: list[float] = []  # end time of event in each column
            event_col_map: dict[str, int] = {}
            
            for event in group:
                start = to_local_hour(event.start)
                end = to_local_hour(event.end)
                if end <= start:
                    end = start + 0.5
                
                # Find first column where this event fits
                assigned = False
                for col_idx, col_end in enumerate(columns):
                    if start >= col_end:
                        columns[col_idx] = end
                        event_col_map[event.uid] = col_idx
                        assigned = True
                        break
                
                if not assigned:
                    # Need a new column
                    event_col_map[event.uid] = len(columns)
                    columns.append(end)
            
            total_cols = len(columns)
            for event in group:
                col = event_col_map[event.uid]
                self._event_layout.append((event, col, total_cols))
    
    def _create_event_widgets(self):
        """Create and position event widgets based on calculated layout."""
        for widget in self._event_widgets:
            widget.deleteLater()
        self._event_widgets.clear()
        
        for event, col, total_cols in self._event_layout:
            widget = EventWidget(event, compact=True, parent=self)
            widget.clicked.connect(self.event_clicked.emit)
            widget.double_clicked.connect(self.event_double_clicked.emit)
            self._event_widgets.append(widget)
            widget.show()
        
        self._position_event_widgets()
    
    def _position_event_widgets(self):
        """Position all event widgets based on their layout."""
        available_width = self.width() - 4  # Leave 2px margin on each side
        
        for widget, (event, col, total_cols) in zip(self._event_widgets, self._event_layout):
            start_hour = to_local_hour(event.start)
            end_hour = to_local_hour(event.end)
            start_hour = max(0, min(24, start_hour))
            end_hour = max(0, min(24, end_hour))
            if end_hour <= start_hour:
                end_hour = start_hour + 0.5
            
            y = int(start_hour * HOUR_HEIGHT)
            height = max(int((end_hour - start_hour) * HOUR_HEIGHT), 20)
            
            # Calculate width and x position based on column
            col_width = available_width // total_cols
            x = 2 + col * col_width
            width = col_width - 1  # 1px gap between columns
            
            widget.setGeometry(x, y + 1, width, height - 2)
    
    def clear_events(self):
        for widget in self._event_widgets:
            widget.deleteLater()
        self._event_widgets.clear()
        self._events.clear()
        self._event_layout.clear()
    
    def _refresh_events(self):
        events = self._events.copy()
        self.clear_events()
        self._events = events
        self._calculate_layout()
        self._create_event_widgets()
    
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._position_event_widgets()
    
    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            hour = int(event.position().y() / HOUR_HEIGHT)
            hour = max(0, min(23, hour))
            dt = datetime.combine(self._date, dt_time(hour=hour))
            self.slot_clicked.emit(dt)
        super().mousePressEvent(event)
    
    def mouseDoubleClickEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            hour = int(event.position().y() / HOUR_HEIGHT)
            hour = max(0, min(23, hour))
            dt = datetime.combine(self._date, dt_time(hour=hour))
            self.slot_double_clicked.emit(dt)
        super().mouseDoubleClickEvent(event)


class DayView(QWidget):
    """Single day view with hourly time slots and all-day events section."""
    
    slot_clicked = Signal(datetime)
    slot_double_clicked = Signal(datetime)
    event_clicked = Signal(EventData)
    event_double_clicked = Signal(EventData)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._date = date.today()
        self._events: list[EventData] = []
        self._setup_ui()
    
    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # Calculate time column width dynamically
        time_col_width = _get_time_column_width()
        
        # All-day events section (with time column spacer)
        all_day_container = QWidget()
        all_day_layout = QHBoxLayout(all_day_container)
        all_day_layout.setContentsMargins(0, 0, 0, 0)
        all_day_layout.setSpacing(0)
        
        # Spacer to align with time column
        all_day_spacer = QWidget()
        all_day_spacer.setFixedWidth(time_col_width)
        all_day_layout.addWidget(all_day_spacer)
        
        # All-day events row (single day)
        self._all_day_row = AllDayEventsRow(num_days=1)
        self._all_day_row.event_clicked.connect(self.event_clicked.emit)
        self._all_day_row.event_double_clicked.connect(self.event_double_clicked.emit)
        self._all_day_row.hide()  # Hidden initially
        all_day_layout.addWidget(self._all_day_row, 1)
        
        main_layout.addWidget(all_day_container)
        
        # Time grid section
        grid_container = QWidget()
        grid_layout = QHBoxLayout(grid_container)
        grid_layout.setContentsMargins(0, 0, 0, 0)
        grid_layout.setSpacing(0)
        
        # Time labels column (fixed, outside scroll)
        self._time_labels = QWidget()
        self._time_labels.setFixedWidth(time_col_width)
        self._time_labels.setFixedHeight(24 * HOUR_HEIGHT)
        time_layout = QVBoxLayout(self._time_labels)
        time_layout.setContentsMargins(0, 0, 0, 0)
        time_layout.setSpacing(0)
        
        # Spacer to align labels with hour lines
        top_spacer = QWidget()
        top_spacer.setFixedHeight(0.5 * HOUR_HEIGHT)
        time_layout.addWidget(top_spacer)
        
        # Labels 01:00 - 23:00 with AlignCenter
        for hour in range(1, 24):
            lbl = QLabel(f"{hour:02d}:00")
            lbl.setFixedHeight(HOUR_HEIGHT)
            lbl.setAlignment(Qt.AlignCenter)
            time_layout.addWidget(lbl)
        
        bot_spacer = QWidget()
        bot_spacer.setFixedHeight(0.5 * HOUR_HEIGHT)
        time_layout.addWidget(bot_spacer)
        
        # Time label scroll area (synced with main scroll)
        time_scroll = QScrollArea()
        time_scroll.setWidgetResizable(True)
        time_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        time_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        time_scroll.setFixedWidth(time_col_width)
        time_scroll.setWidget(self._time_labels)
        
        # Main scroll area for day column
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        
        self._day_column = DayColumnWidget(self._date)
        self._day_column.slot_clicked.connect(self.slot_clicked.emit)
        self._day_column.slot_double_clicked.connect(self.slot_double_clicked.emit)
        self._day_column.event_clicked.connect(self.event_clicked.emit)
        self._day_column.event_double_clicked.connect(self.event_double_clicked.emit)
        
        scroll.setWidget(self._day_column)
        self._scroll = scroll
        
        # Sync scrollbars
        scroll.verticalScrollBar().valueChanged.connect(time_scroll.verticalScrollBar().setValue)
        
        grid_layout.addWidget(time_scroll)
        grid_layout.addWidget(scroll, 1)
        
        main_layout.addWidget(grid_container, 1)
    
    def get_scroll_position(self) -> int:
        """Get current vertical scroll position."""
        return self._scroll.verticalScrollBar().value()
    
    def set_scroll_position(self, position: int):
        """Set vertical scroll position."""
        self._scroll.verticalScrollBar().setValue(position)
    
    def set_date(self, d: date):
        self._date = d
        self._day_column.set_date(d)
        self.refresh_events()
    
    def set_events(self, events: list[EventData]):
        self._events = events
        self.refresh_events()
    
    def refresh_events(self):
        self._day_column.clear_events()
        self._all_day_row.clear_all()
        
        all_day_events = []
        timed_events = []
        
        for event in self._events:
            local_start = to_local_datetime(event.start)
            local_end = to_local_datetime(event.end)
            
            if is_all_day_event(event):
                # Check if this all-day event spans this date
                start_date = local_start.date()
                end_date = local_end.date()
                # All-day events typically have end at midnight of next day, so subtract 1 day for display
                if end_date > start_date:
                    end_date = end_date - timedelta(days=1)
                if start_date <= self._date <= end_date:
                    all_day_events.append(event)
            else:
                # Timed event - only show if it starts on this day
                if local_start.date() == self._date:
                    timed_events.append(event)
        
        # Add all-day events
        self._all_day_row.set_events_for_day(0, all_day_events)
        self._all_day_row.update_height()
        
        # Add timed events
        for event in timed_events:
            self._day_column.add_event(event)
        self._day_column.finalize_events()
    
    def get_date_range(self) -> tuple[datetime, datetime]:
        start = datetime.combine(self._date, dt_time.min)
        end = datetime.combine(self._date, dt_time.max)
        return start, end


class WeekView(QWidget):
    """Week view showing 7 days side by side with all-day events section."""
    
    slot_clicked = Signal(datetime)
    slot_double_clicked = Signal(datetime)
    event_clicked = Signal(EventData)
    event_double_clicked = Signal(EventData)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._start_date = self._get_week_start(date.today())
        self._events: list[EventData] = []
        self._day_columns: list[DayColumnWidget] = []
        self._setup_ui()
    
    def _get_week_start(self, d: date) -> date:
        return d - timedelta(days=d.weekday())
    
    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # Calculate time column width dynamically
        time_col_width = _get_time_column_width()
        
        # Header with day names
        # Account for scrollbar width on the right (typically ~16px on most systems)
        from PySide6.QtWidgets import QApplication, QStyle
        scrollbar_width = QApplication.style().pixelMetric(QStyle.PM_ScrollBarExtent)
        
        header = QWidget()
        header.setStyleSheet("background: #f5f5f5;")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(time_col_width, 0, scrollbar_width, 0)  # Match time column + scrollbar
        header_layout.setSpacing(1)
        
        self._header_labels = []
        for i in range(7):
            label = QLabel()
            label.setAlignment(Qt.AlignCenter)
            label.setStyleSheet("font-weight: bold; padding: 8px; background: #f5f5f5;")
            header_layout.addWidget(label, 1)
            self._header_labels.append(label)
        
        main_layout.addWidget(header)
        
        # All-day events section (with time column spacer)
        all_day_container = QWidget()
        all_day_layout = QHBoxLayout(all_day_container)
        all_day_layout.setContentsMargins(0, 0, 0, 0)
        all_day_layout.setSpacing(0)
        
        # Spacer to align with time column
        all_day_spacer = QWidget()
        all_day_spacer.setStyleSheet("background: #f5f5f5;")
        all_day_spacer.setFixedWidth(time_col_width)
        all_day_layout.addWidget(all_day_spacer)
        
        # All-day events row (7 days)
        self._all_day_row = AllDayEventsRow(num_days=7)
        self._all_day_row.event_clicked.connect(self.event_clicked.emit)
        self._all_day_row.event_double_clicked.connect(self.event_double_clicked.emit)
        self._all_day_row.hide()  # Hidden initially
        all_day_layout.addWidget(self._all_day_row, 1)
        
        main_layout.addWidget(all_day_container)
        
        # Scroll area for time grid
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        
        # Content: time labels + day columns
        content = QWidget()
        content_layout = QHBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        
        # Time labels
        time_widget = QWidget()
        time_widget.setFixedWidth(time_col_width)
        time_widget.setFixedHeight(24 * HOUR_HEIGHT)
        time_widget.setStyleSheet(" background: #f5f5f5;")
        time_layout = QVBoxLayout(time_widget)
        time_layout.setContentsMargins(0, 0, 0, 0)
        time_layout.setSpacing(0)
        
        # Spacer to align labels with hour lines
        top_spacer = QWidget()
        top_spacer.setFixedHeight(0.5* HOUR_HEIGHT)
        time_layout.addWidget(top_spacer)
        
        # Labels 01:00 - 23:00 with AlignTop
        for hour in range(1, 24):
            lbl = QLabel(f"{hour:02d}:00")
            lbl.setFixedHeight(HOUR_HEIGHT)
            lbl.setAlignment(Qt.AlignCenter)
            # lbl.setStyleSheet("padding-right: 5px; color: #666;")
            time_layout.addWidget(lbl)
        
        bot_spacer = QWidget()
        bot_spacer.setFixedHeight(0.5* HOUR_HEIGHT)
        time_layout.addWidget(bot_spacer)

        content_layout.addWidget(time_widget)
        
        # Day columns
        for i in range(7):
            d = self._start_date + timedelta(days=i)
            col = DayColumnWidget(d)
            col.slot_clicked.connect(self.slot_clicked.emit)
            col.slot_double_clicked.connect(self.slot_double_clicked.emit)
            col.event_clicked.connect(self.event_clicked.emit)
            col.event_double_clicked.connect(self.event_double_clicked.emit)
            content_layout.addWidget(col, 1)
            self._day_columns.append(col)
        
        scroll.setWidget(content)
        self._scroll = scroll
        main_layout.addWidget(scroll, 1)
        self._update_headers()
    
    def get_scroll_position(self) -> int:
        """Get current vertical scroll position."""
        return self._scroll.verticalScrollBar().value()
    
    def set_scroll_position(self, position: int):
        """Set vertical scroll position."""
        self._scroll.verticalScrollBar().setValue(position)
    
    def _update_headers(self):
        day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        font_name, font_size = get_interface_font()
        for i, label in enumerate(self._header_labels):
            d = self._start_date + timedelta(days=i)
            label.setText(f"{day_names[i]} {d.day}")
            if d == date.today():
                label.setStyleSheet(f"font-family: '{font_name}'; font-size: {font_size}pt; font-weight: bold; padding: 8px; background: #e3f2fd; color: #1976d2;")
            else:
                label.setStyleSheet(f"font-family: '{font_name}'; font-size: {font_size}pt; font-weight: bold; padding: 8px; background: #f5f5f5;")
    
    def set_date(self, d: date):
        self._start_date = self._get_week_start(d)
        for i, col in enumerate(self._day_columns):
            col.set_date(self._start_date + timedelta(days=i))
        self._update_headers()
        self.refresh_events()
    
    def set_events(self, events: list[EventData]):
        self._events = events
        self.refresh_events()
    
    def refresh_events(self):
        for col in self._day_columns:
            col.clear_events()
        self._all_day_row.clear_all()
        
        # Group events by day and type (all-day vs timed)
        all_day_by_day: list[list[EventData]] = [[] for _ in range(7)]
        
        for event in self._events:
            local_start = to_local_datetime(event.start)
            local_end = to_local_datetime(event.end)
            
            if is_all_day_event(event):
                # Multi-day all-day events should appear on each day they span
                start_date = local_start.date()
                end_date = local_end.date()
                # All-day events typically have end at midnight of next day, so subtract 1 day for display
                if end_date > start_date:
                    end_date = end_date - timedelta(days=1)
                
                # Add to each day in the week that this event spans
                for day_idx in range(7):
                    day_date = self._start_date + timedelta(days=day_idx)
                    if start_date <= day_date <= end_date:
                        all_day_by_day[day_idx].append(event)
            else:
                # Timed event - only show on start day
                event_date = local_start.date()
                day_offset = (event_date - self._start_date).days
                if 0 <= day_offset < 7:
                    self._day_columns[day_offset].add_event(event)
        
        # Add all-day events to their respective day cells
        for day_idx, events in enumerate(all_day_by_day):
            self._all_day_row.set_events_for_day(day_idx, events)
        
        # Finalize event layouts for all day columns
        for col in self._day_columns:
            col.finalize_events()
        
        # Update the all-day row height (same height for all days, based on max)
        self._all_day_row.update_height()
    
    def get_date_range(self) -> tuple[datetime, datetime]:
        start = datetime.combine(self._start_date, dt_time.min)
        end = datetime.combine(self._start_date + timedelta(days=6), dt_time.max)
        return start, end
    
    def refresh_styles(self):
        """Refresh header styles after config change."""
        self._update_headers()


class MonthDayCell(QFrame):
    """Single day cell in month view."""
    
    clicked = Signal(date)
    double_clicked = Signal(date)
    event_clicked = Signal(EventData)
    event_double_clicked = Signal(EventData)
    
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
        self.setMinimumSize(100, 80)
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
        bg = "#ffffff" if self.is_current_month else "#f5f5f5"
        text = "#000000" if self.is_current_month else "#999999"
        
        if self._date == date.today():
            self._day_label.setStyleSheet("color: #1976d2; font-weight: bold; background: #e3f2fd; border-radius: 10px; padding: 2px 6px;")
        else:
            self._day_label.setStyleSheet(f"color: {text};")
        
        self.setStyleSheet(f"background-color: {bg}; border: 1px solid #e0e0e0;")
    
    def set_date(self, d: date, is_current_month: bool = True):
        self._date = d
        self.is_current_month = is_current_month
        self._day_label.setText(str(d.day))
        self._update_style()
        self.clear_events()
    
    def add_event(self, event: EventData):
        # Month view: title only, no location
        widget = EventWidget(event, compact=True, show_time=False, show_location=False)
        event_height = _get_single_line_event_height()
        widget.setMaximumHeight(event_height)
        widget.clicked.connect(self.event_clicked.emit)
        widget.double_clicked.connect(self.event_double_clicked.emit)
        self._events_layout.addWidget(widget)
        self._event_widgets.append(widget)
    
    def clear_events(self):
        for widget in self._event_widgets:
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
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._year = date.today().year
        self._month = date.today().month
        self._events: list[EventData] = []
        self._cells: list[MonthDayCell] = []
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
        for name in ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]:
            label = QLabel(name)
            label.setAlignment(Qt.AlignCenter)
            label.setStyleSheet(f"font-family: '{font_name}'; font-size: {font_size}pt; font-weight: bold; padding: 8px; background: #f5f5f5;")
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
                self._grid_layout.addWidget(cell, row, col)
                self._cells.append(cell)
        
        layout.addWidget(grid_widget, 1)
        self._update_grid()
    
    def _update_grid(self):
        first_day = date(self._year, self._month, 1)
        start_offset = first_day.weekday()
        grid_start = first_day - timedelta(days=start_offset)
        
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
        
        for event in self._events:
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
    
    def refresh_styles(self):
        """Refresh header styles after config change."""
        font_name, font_size = get_interface_font()
        for label in self._header_labels:
            label.setStyleSheet(f"font-family: '{font_name}'; font-size: {font_size}pt; font-weight: bold; padding: 8px; background: #f5f5f5;")


class CalendarWidget(QWidget):
    """Main calendar widget with switchable views."""
    
    slot_clicked = Signal(datetime)
    slot_double_clicked = Signal(datetime)
    event_clicked = Signal(EventData)
    event_double_clicked = Signal(EventData)
    view_changed = Signal(ViewType)
    date_changed = Signal(date)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_view = ViewType.WEEK
        self._current_date = date.today()
        self._setup_ui()
    
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        self._stack = QStackedWidget()
        
        self._day_view = DayView()
        self._week_view = WeekView()
        self._month_view = MonthView()
        
        for view in [self._day_view, self._week_view]:
            view.slot_clicked.connect(self.slot_clicked.emit)
            view.slot_double_clicked.connect(self.slot_double_clicked.emit)
            view.event_clicked.connect(self.event_clicked.emit)
            view.event_double_clicked.connect(self.event_double_clicked.emit)
        
        self._month_view.day_clicked.connect(lambda d: self.slot_clicked.emit(datetime.combine(d, dt_time(hour=9))))
        self._month_view.day_double_clicked.connect(lambda d: self.slot_double_clicked.emit(datetime.combine(d, dt_time(hour=9))))
        self._month_view.event_clicked.connect(self.event_clicked.emit)
        self._month_view.event_double_clicked.connect(self.event_double_clicked.emit)
        
        self._stack.addWidget(self._day_view)
        self._stack.addWidget(self._week_view)
        self._stack.addWidget(self._month_view)
        
        layout.addWidget(self._stack)
        self.set_view(self._current_view)
    
    def set_view(self, view_type: ViewType):
        self._current_view = view_type
        if view_type == ViewType.DAY:
            self._stack.setCurrentWidget(self._day_view)
            self._day_view.set_date(self._current_date)
        elif view_type == ViewType.WEEK:
            self._stack.setCurrentWidget(self._week_view)
            self._week_view.set_date(self._current_date)
        else:
            self._stack.setCurrentWidget(self._month_view)
            self._month_view.set_date(self._current_date)
        self.view_changed.emit(view_type)
    
    def set_date(self, d: date):
        self._current_date = d
        self._day_view.set_date(d)
        self._week_view.set_date(d)
        self._month_view.set_date(d)
        self.date_changed.emit(d)
    
    def set_events(self, events: list[EventData]):
        self._day_view.set_events(events)
        self._week_view.set_events(events)
        self._month_view.set_events(events)
    
    def get_current_view(self) -> ViewType:
        return self._current_view
    
    def get_current_date(self) -> date:
        return self._current_date
    
    def get_date_range(self) -> tuple[datetime, datetime]:
        if self._current_view == ViewType.DAY:
            return self._day_view.get_date_range()
        elif self._current_view == ViewType.WEEK:
            return self._week_view.get_date_range()
        else:
            return self._month_view.get_date_range()
    
    def go_today(self):
        self.set_date(date.today())
    
    def go_previous(self):
        if self._current_view == ViewType.DAY:
            self.set_date(self._current_date - timedelta(days=1))
        elif self._current_view == ViewType.WEEK:
            self.set_date(self._current_date - timedelta(weeks=1))
        else:
            if self._current_date.month == 1:
                self.set_date(self._current_date.replace(year=self._current_date.year - 1, month=12))
            else:
                self.set_date(self._current_date.replace(month=self._current_date.month - 1))
    
    def go_next(self):
        if self._current_view == ViewType.DAY:
            self.set_date(self._current_date + timedelta(days=1))
        elif self._current_view == ViewType.WEEK:
            self.set_date(self._current_date + timedelta(weeks=1))
        else:
            if self._current_date.month == 12:
                self.set_date(self._current_date.replace(year=self._current_date.year + 1, month=1))
            else:
                self.set_date(self._current_date.replace(month=self._current_date.month + 1))
    
    def get_scroll_position(self) -> int:
        """Get scroll position for day/week views."""
        if self._current_view == ViewType.DAY:
            return self._day_view.get_scroll_position()
        elif self._current_view == ViewType.WEEK:
            return self._week_view.get_scroll_position()
        return 0
    
    def set_scroll_position(self, position: int):
        """Set scroll position for day/week views."""
        self._day_view.set_scroll_position(position)
        self._week_view.set_scroll_position(position)
    
    def refresh_styles(self):
        """Refresh styles after config change."""
        self._week_view.refresh_styles()
        self._month_view.refresh_styles()
