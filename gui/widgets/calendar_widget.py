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
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QFont, QFontMetrics, QMouseEvent

from backend.caldav_client import EventData
from backend.config import LayoutConfig, LocalizationConfig, ColorsConfig, LabelsConfig
from .event_widget import (
    EventWidget, DraggableEventWidget, DragMode,
    set_event_layout_config, set_event_colors_config
)

# Module-level configs (set by MainWindow at startup)
_layout_config: LayoutConfig = LayoutConfig()
_localization_config: LocalizationConfig = LocalizationConfig()
_colors_config: ColorsConfig = ColorsConfig()
_labels_config: LabelsConfig = LabelsConfig()

# Module-level hour height (updated when layout config is set)
HOUR_HEIGHT = 60  # Default value


def set_layout_config(config: LayoutConfig):
    """Set the layout configuration for this module and event widget."""
    global _layout_config, HOUR_HEIGHT
    _layout_config = config
    HOUR_HEIGHT = config.hour_height
    # Also set for event widgets
    set_event_layout_config(config)


def set_localization_config(config: LocalizationConfig):
    """Set the localization configuration for this module."""
    global _localization_config
    _localization_config = config


def get_localization_config() -> LocalizationConfig:
    """Get the current localization configuration."""
    return _localization_config


def set_colors_config(config: ColorsConfig):
    """Set the colors configuration for this module and event widget."""
    global _colors_config
    _colors_config = config
    set_event_colors_config(config)


def get_colors_config() -> ColorsConfig:
    """Get the current colors configuration."""
    return _colors_config


def set_labels_config(config: LabelsConfig):
    """Set the labels configuration for this module."""
    global _labels_config
    _labels_config = config


def get_labels_config() -> LabelsConfig:
    """Get the current labels configuration."""
    return _labels_config


def get_hour_height() -> int:
    """Get the configured hour height in pixels."""
    return HOUR_HEIGHT


def get_text_font() -> tuple[str, int]:
    """Get the configured text font name and size."""
    return (_layout_config.text_font, _layout_config.text_font_size)


def get_interface_font() -> tuple[str, int]:
    """Get the configured interface font name and size."""
    return (_layout_config.interface_font, _layout_config.interface_font_size)


# Import timezone utilities from shared module
from backend.timezone_utils import to_local_datetime, to_local_hour


from dataclasses import dataclass


@dataclass
class EventPortion:
    """
    A day-specific portion of a multi-day event.
    
    Represents the visible part of an event on a specific day.
    For example, an event "Sat 17:00 - Sun 04:00" creates two portions:
    - Saturday: visible 17:00-24:00
    - Sunday: visible 00:00-04:00
    """
    event: EventData
    display_date: date
    visible_start_hour: float  # 0-24, visible on this day
    visible_end_hour: float    # 0-24, visible on this day
    
    @staticmethod
    def create_for_day(event: EventData, day: date) -> Optional['EventPortion']:
        """
        Create a portion if event is visible on the given day.
        
        Returns None if the event doesn't appear on this day.
        """
        local_start = to_local_datetime(event.start)
        local_end = to_local_datetime(event.end)
        
        # Check if event appears on this day
        if local_end.date() < day or local_start.date() > day:
            return None  # Event doesn't span this day
        
        # Calculate visible hours on this specific day
        if local_start.date() == day:
            start_hour = local_start.hour + local_start.minute / 60.0
        else:
            start_hour = 0.0  # Event started before this day
        
        if local_end.date() == day:
            end_hour = local_end.hour + local_end.minute / 60.0
        else:
            end_hour = 24.0  # Event continues after this day
        
        return EventPortion(event, day, start_hour, end_hour)
    
    def calculate_new_event_times(self, new_visible_start_hour: float, new_visible_end_hour: float) -> tuple[datetime, datetime]:
        """
        Convert portion times to event times after drag-and-drop.
        
        When a portion is moved, we need to translate that to the underlying event's new times.
        """
        # Calculate the delta between old and new portion start
        old_portion_start = datetime.combine(
            self.display_date,
            dt_time(hour=int(self.visible_start_hour),
                   minute=int((self.visible_start_hour % 1) * 60))
        )
        new_portion_start = datetime.combine(
            self.display_date,
            dt_time(hour=int(new_visible_start_hour),
                   minute=int((new_visible_start_hour % 1) * 60))
        )
        
        delta = new_portion_start - old_portion_start
        
        # Apply the same delta to the actual event times
        old_event_start = to_local_datetime(self.event.start)
        old_event_end = to_local_datetime(self.event.end)
        
        new_event_start = old_event_start + delta
        new_event_end = old_event_end + delta
        
        return (new_event_start, new_event_end)


class ViewType(Enum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    LIST = "list"


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
    Supports drag-and-drop for moving/resizing events.
    """
    
    slot_clicked = Signal(datetime)
    slot_double_clicked = Signal(datetime)
    event_clicked = Signal(EventData)
    event_double_clicked = Signal(EventData)
    event_time_changed = Signal(EventData, datetime, datetime)  # event, new_start, new_end
    
    def __init__(self, for_date: date, parent=None):
        super().__init__(parent)
        self._date = for_date
        self._portions: list[EventPortion] = []
        self._event_widgets: list[DraggableEventWidget] = []
        self._event_layout: list[tuple[EventPortion, int, int]] = []  # (portion, column, total_columns)
        
        # Drag state
        self._dragging_event: Optional[EventData] = None
        self._drag_mode: DragMode = DragMode.NONE
        self._drag_start_y: int = 0
        self._drag_original_start: Optional[datetime] = None
        self._drag_original_end: Optional[datetime] = None
        self._drag_grab_offset_y: int = 0  # Offset from event top to grab point (pixels)
        
        self._setup_ui()
        self._setup_time_indicator()
        
        # Enable mouse tracking for drag handling
        self.setMouseTracking(True)
    
    def _setup_ui(self):
        colors = get_colors_config()
        # Fixed height for 24 hours
        self.setMinimumHeight(24 * HOUR_HEIGHT)
        self.setMaximumHeight(24 * HOUR_HEIGHT)
        self.setStyleSheet(f"background-color: {colors.day_column_background}; border: 1px solid {colors.cell_border};")
        self.setCursor(Qt.PointingHandCursor)
        
        # Draw hour lines
        for hour in range(1, 24):
            line = QFrame(self)
            line.setFrameStyle(QFrame.HLine | QFrame.Plain)
            line.setStyleSheet(f"background-color: {colors.hour_line};")
            line.setGeometry(0, hour * HOUR_HEIGHT, 2000, 1)
    
    def _setup_time_indicator(self):
        """Set up the current time indicator line."""
        # Create the time indicator line
        self._time_indicator = QFrame(self)
        self._time_indicator.setFrameStyle(QFrame.HLine | QFrame.Plain)
        colors = get_colors_config()
        self._time_indicator.setStyleSheet(f"background-color: {colors.current_time_line};")
        self._time_indicator.setFixedHeight(3)
        self._time_indicator.raise_()  # Ensure it's on top of other elements
        
        # Create timer to update every minute
        self._time_timer = QTimer(self)
        self._time_timer.timeout.connect(self._update_time_indicator)
        self._time_timer.start(60000)  # Update every 60 seconds
        
        # Initial update
        self._update_time_indicator()
    
    def _update_time_indicator(self):
        """Update the position of the current time indicator."""
        if self._date != date.today():
            self._time_indicator.hide()
            return
        
        # Show and position the indicator
        self._time_indicator.show()
        now = datetime.now()
        current_hour = now.hour + now.minute / 60.0
        y_pos = int(current_hour * HOUR_HEIGHT)
        self._time_indicator.setGeometry(0, y_pos, self.width(), 2)
        self._time_indicator.raise_()  # Keep on top
    
    def set_date(self, new_date: date):
        self._date = new_date
        self._update_time_indicator()  # Update visibility based on new date
        self._refresh_events()
    
    def add_portion(self, portion: EventPortion):
        """Add an event portion to this day column."""
        self._portions.append(portion)
    
    def finalize_portions(self):
        """Call after all portions are added to calculate layout and create widgets."""
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
            # Use DraggableEventWidget for editable events, EventWidget for read-only
            if event.read_only:
                widget = EventWidget(event, compact=True, parent=self)
            else:
                widget = DraggableEventWidget(event, compact=True, parent=self)
                widget.drag_started.connect(self._on_drag_started)
                widget.drag_moved.connect(self._on_drag_moved)
                widget.drag_finished.connect(self._on_drag_finished)
            widget.clicked.connect(self.event_clicked.emit)
            widget.double_clicked.connect(self.event_double_clicked.emit)
            self._event_widgets.append(widget)
            widget.show()
        
        self._position_event_widgets()
        
        # Ensure time indicator stays on top of event widgets
        self._time_indicator.raise_()
    
    def _y_to_time(self, y: int) -> dt_time:
        """Convert Y position to time, snapped to configured interval."""
        snap_minutes = _layout_config.drag_snap_minutes
        # Convert Y to hours (float)
        hours = y / HOUR_HEIGHT
        hours = max(0, min(24, hours))
        
        # Convert to minutes
        total_minutes = int(hours * 60)
        
        # Snap to nearest interval
        snapped_minutes = round(total_minutes / snap_minutes) * snap_minutes
        snapped_minutes = max(0, min(24 * 60 - 1, snapped_minutes))
        
        return dt_time(hour=snapped_minutes // 60, minute=snapped_minutes % 60)
    
    def _on_drag_started(self, event: EventData, mode: DragMode, y_offset_in_widget: int = 0):
        """Handle drag start.
        
        Args:
            event: The event being dragged
            mode: The drag mode (MOVE, RESIZE_TOP, RESIZE_BOTTOM)
            y_offset_in_widget: Y offset from the widget's top to the grab point (pixels)
        """
        self._dragging_event = event
        self._drag_mode = mode
        self._drag_original_start = to_local_datetime(event.start)
        self._drag_original_end = to_local_datetime(event.end)
        
        # Store the grab offset (distance from event top to where user clicked)
        # This ensures grab-and-release-without-moving keeps event in place
        self._drag_grab_offset_y = y_offset_in_widget
    
    def _on_drag_moved(self, event: EventData, mode: DragMode, global_pos):
        """Handle drag move - show time tooltip as visual feedback."""
        local_pos = self.mapFromGlobal(global_pos)
        y = local_pos.y()
        
        # Calculate what times would be after drag
        if self._drag_original_start and self._drag_original_end:
            orig_start = self._drag_original_start
            orig_end = self._drag_original_end
            duration = orig_end - orig_start
            
            if mode == DragMode.MOVE:
                # Subtract grab offset so the event follows the original grab point
                adjusted_y = y - self._drag_grab_offset_y
                new_time = self._y_to_time(adjusted_y)
                new_start = datetime.combine(self._date, new_time)
                new_end = new_start + duration
                time_str = f"{new_start.strftime('%H:%M')} - {new_end.strftime('%H:%M')}"
            elif mode == DragMode.RESIZE_TOP:
                new_time = self._y_to_time(y)
                new_start_time = new_time
                time_str = f"{new_start_time.strftime('%H:%M')} - {orig_end.strftime('%H:%M')}"
            elif mode == DragMode.RESIZE_BOTTOM:
                new_time = self._y_to_time(y)
                new_end_time = new_time
                time_str = f"{orig_start.strftime('%H:%M')} - {new_end_time.strftime('%H:%M')}"
            else:
                return
            
            # Show tooltip at cursor position
            from PySide6.QtWidgets import QToolTip
            QToolTip.showText(global_pos, time_str, self)
    
    def _find_target_day_column(self, global_pos) -> tuple[date, int]:
        """Find which DayColumnWidget is under the global position.
        
        Returns:
            Tuple of (target_date, local_y) where the event should be placed.
        """
        from PySide6.QtWidgets import QApplication
        
        # Default to this widget if we can't find another
        target_date = self._date
        local_pos = self.mapFromGlobal(global_pos)
        local_y = local_pos.y()
        
        # Find widget under cursor
        widget_at_pos = QApplication.widgetAt(global_pos)
        if widget_at_pos is None:
            return (target_date, local_y)
        
        # Walk up the widget tree to find a DayColumnWidget
        current = widget_at_pos
        while current is not None:
            if isinstance(current, DayColumnWidget):
                target_date = current._date
                local_y = current.mapFromGlobal(global_pos).y()
                break
            current = current.parentWidget()
        
        return (target_date, local_y)
    
    def _on_drag_finished(self, event: EventData, mode: DragMode, global_pos):
        """Handle drag completion - calculate new times and emit signal."""
        if self._dragging_event is None:
            return
        
        # Find target day column (for cross-day dragging in week view)
        target_date, y = self._find_target_day_column(global_pos)
        
        # Calculate new start and end based on drag mode
        orig_start = self._drag_original_start
        orig_end = self._drag_original_end
        duration = orig_end - orig_start
        
        if mode == DragMode.MOVE:
            # Subtract grab offset so event stays aligned with where user grabbed
            adjusted_y = y - self._drag_grab_offset_y
            new_time = self._y_to_time(adjusted_y)
            # Move entire event - keep duration (use target_date for cross-day drops)
            new_start = datetime.combine(target_date, new_time)
            new_end = new_start + duration
        elif mode == DragMode.RESIZE_TOP:
            # For resize, use mouse Y directly (no offset adjustment)
            new_time = self._y_to_time(y)
            # Change start time, keep end
            new_start = datetime.combine(self._date, new_time)
            new_end = datetime.combine(self._date, orig_end.time())
            # Don't allow start after end
            if new_start >= new_end:
                new_start = new_end - timedelta(minutes=_layout_config.drag_snap_minutes)
        elif mode == DragMode.RESIZE_BOTTOM:
            # For resize, use mouse Y directly (no offset adjustment)
            new_time = self._y_to_time(y)
            # Change end time, keep start
            new_start = datetime.combine(self._date, orig_start.time())
            new_end = datetime.combine(self._date, new_time)
            # Don't allow end before start
            if new_end <= new_start:
                new_end = new_start + timedelta(minutes=_layout_config.drag_snap_minutes)
        else:
            # No change
            self._dragging_event = None
            return
        
        # Emit signal with new times
        self.event_time_changed.emit(event, new_start, new_end)
        
        # Reset drag state
        self._dragging_event = None
        self._drag_mode = DragMode.NONE
    
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
        self._update_time_indicator()  # Update width on resize
    
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
    event_time_changed = Signal(EventData, datetime, datetime)  # For drag-and-drop
    
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
        self._day_column.event_time_changed.connect(self.event_time_changed.emit)
        
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
    event_time_changed = Signal(EventData, datetime, datetime)  # For drag-and-drop
    
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
        
        colors = get_colors_config()
        header = QWidget()
        header.setStyleSheet(f"background: {colors.header_background};")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(time_col_width, 0, scrollbar_width, 0)  # Match time column + scrollbar
        header_layout.setSpacing(1)
        
        self._header_labels = []
        for i in range(7):
            label = QLabel()
            label.setAlignment(Qt.AlignCenter)
            label.setStyleSheet(f"font-weight: bold; padding: 8px; background: {colors.header_background};")
            header_layout.addWidget(label, 1)
            self._header_labels.append(label)
        
        main_layout.addWidget(header)
        
        # All-day events section (with time column spacer)
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
        colors = get_colors_config()
        time_widget = QWidget()
        time_widget.setFixedWidth(time_col_width)
        time_widget.setFixedHeight(24 * HOUR_HEIGHT)
        time_widget.setStyleSheet(f" background: {colors.header_background};")
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
            col.event_time_changed.connect(self.event_time_changed.emit)
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
        localization = get_localization_config()
        colors = get_colors_config()
        font_name, font_size = get_interface_font()
        for i, label in enumerate(self._header_labels):
            d = self._start_date + timedelta(days=i)
            day_name = localization.get_day_name(i)
            label.setText(f"{day_name} {d.day}")
            if d == date.today():
                label.setStyleSheet(f"font-family: '{font_name}'; font-size: {font_size}pt; font-weight: bold; padding: 8px; background: {colors.today_highlight_background}; color: {colors.today_highlight_text};")
            else:
                label.setStyleSheet(f"font-family: '{font_name}'; font-size: {font_size}pt; font-weight: bold; padding: 8px; background: {colors.header_background};")
    
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
            day_name = localization.get_day_name(i)
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
    
    def _on_drag_started(self, event: EventData, mode: DragMode, y_offset: int):
        """Handle drag start - store original times."""
        self._dragging_event = event
        self._drag_original_start = to_local_datetime(event.start)
        self._drag_original_end = to_local_datetime(event.end)
    
    def _find_target_day_cell(self, global_pos) -> Optional[date]:
        """Find which MonthDayCell is under the global position."""
        from PySide6.QtWidgets import QApplication
        
        widget_at_pos = QApplication.widgetAt(global_pos)
        if widget_at_pos is None:
            return None
        
        # Walk up the widget tree to find a MonthDayCell
        current = widget_at_pos
        while current is not None:
            if isinstance(current, MonthDayCell):
                return current._date
            current = current.parentWidget()
        
        return None
    
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


class ListEventWidget(QFrame):
    """Full-width event widget for list view showing all event info."""
    
    clicked = Signal(EventData)
    double_clicked = Signal(EventData)
    
    def __init__(self, event_data: EventData, parent=None):
        super().__init__(parent)
        self.event_data = event_data
        self._setup_ui()
        self._apply_style()
    
    def _setup_ui(self):
        """Set up the widget with all event info."""
        from .event_widget import get_text_font, get_contrasting_text_color, lighten_color, to_local_datetime as to_local_dt
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(12)
        
        text_font = get_text_font()
        
        # Date/time column (two lines, width based on font metrics)
        local_start = to_local_dt(self.event_data.start)
        local_end = to_local_dt(self.event_data.end)
        
        # Build two-line date/time text
        if self.event_data.all_day:
            # All-day: "YYYY/mm/dd" + "(Allday)"
            line1 = local_start.strftime("%Y/%m/%d")
            line2 = "(Allday)"
        else:
            # Timed: "YYYY/mm/dd HH:mm" + "to: mm/dd HH:mm"
            line1 = local_start.strftime("%Y/%m/%d %H:%M")
            line2 = f"to: {local_end.strftime('%m/%d %H:%M')}"
        
        datetime_label = QLabel(f"{line1}\n{line2}")
        datetime_label.setFont(text_font)
        datetime_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        
        # Calculate width dynamically based on longest possible line
        fm = QFontMetrics(text_font)
        # Sample text for width calculation: "YYYY/mm/dd HH:mm" is the widest format
        sample_text = "0000/00/00 00:00"
        datetime_width = fm.horizontalAdvance(sample_text) + 8  # Add small padding
        datetime_label.setFixedWidth(datetime_width)
        
        layout.addWidget(datetime_label, 0, Qt.AlignTop)
        
        # Content column (title, location, description)
        content_layout = QVBoxLayout()
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(2)
        
        # Title row with calendar name
        title_row = QHBoxLayout()
        title_row.setSpacing(12)
        
        # Title (bold)
        title_font = QFont(text_font)
        title_font.setBold(True)
        title_label = QLabel(self.event_data.summary)
        title_label.setFont(title_font)
        title_label.setWordWrap(False)
        title_row.addWidget(title_label, 1)
        
        # Calendar name (right-aligned)
        colors = get_colors_config()
        cal_label = QLabel(self.event_data.calendar_name)
        cal_label.setFont(text_font)
        cal_label.setStyleSheet(f"color: {colors.secondary_text};")
        title_row.addWidget(cal_label)
        
        content_layout.addLayout(title_row)
        
        # Location (if present)
        if self.event_data.location:
            location_label = QLabel(f"📍 {self.event_data.location}")
            location_label.setFont(text_font)
            content_layout.addWidget(location_label)
        
        # Description (if present, truncated)
        if self.event_data.description:
            desc = self.event_data.description.replace('\n', ' ').replace('\r', '')
            if len(desc) > 200:
                desc = desc[:200] + "..."
            desc_label = QLabel(desc)
            desc_label.setFont(text_font)
            desc_label.setWordWrap(True)
            desc_label.setStyleSheet(f"color: {colors.tertiary_text};")
            content_layout.addWidget(desc_label)
        
        layout.addLayout(content_layout, 1)
        
        self.setFrameStyle(QFrame.StyledPanel | QFrame.Plain)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
    
    def _apply_style(self):
        """Apply color styling based on the event's calendar color."""
        from .event_widget import get_contrasting_text_color, lighten_color
        
        bg_color = self.event_data.calendar_color
        text_color = get_contrasting_text_color(bg_color)
        border_color = bg_color
        bg_lighter = lighten_color(bg_color, 0.4)
        
        self.setStyleSheet(f"""
            ListEventWidget {{
                background-color: {bg_lighter};
                border: 2px solid {border_color};
                border-left: 4px solid {border_color};
                border-radius: 4px;
                color: {text_color};
            }}
            ListEventWidget:hover {{
                background-color: {lighten_color(bg_color, 0.2)};
            }}
            QLabel {{
                color: {text_color};
                background: transparent;
                border: none;
            }}
        """)
    
    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.event_data)
        super().mousePressEvent(event)
    
    def mouseDoubleClickEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self.double_clicked.emit(self.event_data)
        super().mouseDoubleClickEvent(event)
    
    def paintEvent(self, event):
        """Draw indicator triangles for recurring and read-only events."""
        super().paintEvent(event)
        
        if not self.event_data.is_recurring and not self.event_data.read_only:
            return
        
        from PySide6.QtGui import QPainter, QBrush, QColor, QPolygonF
        from .event_widget import get_contrasting_text_color, lighten_color
        
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        fm = QFontMetrics(self.font())
        triangle_size = fm.height() // 2
        
        w = self.width()
        h = self.height()
        
        bg_color = lighten_color(self.event_data.calendar_color, 0.4)
        triangle_color = get_contrasting_text_color(bg_color)
        
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(QColor(triangle_color)))
        
        if self.event_data.is_recurring:
            from PySide6.QtCore import QPointF
            recurring_points = QPolygonF([
                QPointF(0, h),
                QPointF(triangle_size, h),
                QPointF(0, h - triangle_size)
            ])
            painter.drawPolygon(recurring_points)
        
        if self.event_data.read_only:
            from PySide6.QtCore import QPointF
            readonly_points = QPolygonF([
                QPointF(w, h),
                QPointF(w - triangle_size, h),
                QPointF(w, h - triangle_size)
            ])
            painter.drawPolygon(readonly_points)
        
        painter.end()


class ListView(QWidget):
    """Chronological list view of events with bi-infinite scrolling."""
    
    event_clicked = Signal(EventData)
    event_double_clicked = Signal(EventData)
    visible_range_changed = Signal(datetime, datetime)  # Emitted when visible events change
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._events: list[EventData] = []
        self._event_widgets: list[ListEventWidget] = []
        self._current_date = date.today()
        self._sorted_events: list[EventData] = []  # Keep sorted list for navigation
        self._pending_scroll_datetime: Optional[datetime] = None  # Scroll target applied after events load
        self._last_scroll_datetime: Optional[datetime] = None  # Track last successful scroll target
        self._setup_ui()
    
    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # Scroll area
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        
        # Content widget
        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(8, 8, 8, 8)
        self._content_layout.setSpacing(4)
        self._content_layout.addStretch()  # Keep events at top
        
        self._scroll.setWidget(self._content)
        main_layout.addWidget(self._scroll)
        
        # Connect scroll to detect visible range
        self._scroll.verticalScrollBar().valueChanged.connect(self._on_scroll)
    
    def _on_scroll(self):
        """Handle scroll event to update visible range."""
        visible_range = self.get_visible_date_range()
        if visible_range[0] and visible_range[1]:
            self.visible_range_changed.emit(visible_range[0], visible_range[1])
    
    def set_date(self, d: date):
        """Set the current date (used for navigation context)."""
        self._current_date = d
    
    def set_events(self, events: list[EventData]):
        """Set events and rebuild the list."""
        self._events = events
        self._refresh_display()
    
    def _refresh_display(self):
        """Rebuild the event list display."""
        # Clear existing widgets
        for widget in self._event_widgets:
            widget.deleteLater()
        self._event_widgets.clear()
        
        # Sort events chronologically
        sorted_events = sorted(self._events, key=lambda e: to_local_datetime(e.start))
        
        # Remove the stretch at the end temporarily
        stretch_item = self._content_layout.takeAt(self._content_layout.count() - 1)
        
        # Add event widgets
        for event in sorted_events:
            widget = ListEventWidget(event)
            widget.clicked.connect(self.event_clicked.emit)
            widget.double_clicked.connect(self.event_double_clicked.emit)
            self._content_layout.addWidget(widget)
            self._event_widgets.append(widget)
        
        # Re-add stretch
        self._content_layout.addStretch()
        
        # Apply pending scroll if set (after events are loaded)
        from PySide6.QtCore import QTimer
        if self._pending_scroll_datetime:
            target_dt = self._pending_scroll_datetime
            self._pending_scroll_datetime = None  # Clear before applying
            QTimer.singleShot(50, lambda: self.scroll_to_datetime(target_dt))
        
        # Emit visible range after layout is complete
        def _emit_visible_range():
            visible_range = self.get_visible_date_range()
            if visible_range[0] and visible_range[1]:
                self.visible_range_changed.emit(visible_range[0], visible_range[1])
        QTimer.singleShot(100, _emit_visible_range)
    
    def get_visible_date_range(self) -> tuple[Optional[datetime], Optional[datetime]]:
        """Get the date range of currently visible events."""
        if not self._event_widgets:
            return (None, None)
        
        viewport = self._scroll.viewport()
        scroll_pos = self._scroll.verticalScrollBar().value()
        viewport_height = viewport.height()
        
        first_visible: Optional[datetime] = None
        last_visible: Optional[datetime] = None
        
        for widget in self._event_widgets:
            # Get widget position relative to scroll area
            widget_pos = widget.mapTo(self._content, widget.rect().topLeft())
            widget_top = widget_pos.y()
            widget_bottom = widget_top + widget.height()
            
            # Check if widget is visible
            if widget_bottom > scroll_pos and widget_top < scroll_pos + viewport_height:
                event_start = to_local_datetime(widget.event_data.start)
                if first_visible is None:
                    first_visible = event_start
                last_visible = event_start
        
        return (first_visible, last_visible)
    
    def get_first_visible_datetime(self) -> Optional[datetime]:
        """Get the datetime of the first visible event (top of view)."""
        visible_range = self.get_visible_date_range()
        return visible_range[0]
    
    def scroll_to_datetime(self, target_dt: datetime):
        """Scroll to position the first event at or after target_dt at the top."""
        # Track the scroll target for view switching
        self._last_scroll_datetime = target_dt
        
        if not self._event_widgets:
            return
        
        # Find the first event at or after target_dt
        target_widget = None
        for widget in self._event_widgets:
            event_start = to_local_datetime(widget.event_data.start)
            # Remove timezone info for comparison
            event_start_naive = event_start.replace(tzinfo=None) if event_start.tzinfo else event_start
            target_naive = target_dt.replace(tzinfo=None) if target_dt.tzinfo else target_dt
            if event_start_naive >= target_naive:
                target_widget = widget
                break
        
        # If no event at or after target, use the last event
        if target_widget is None and self._event_widgets:
            target_widget = self._event_widgets[-1]
        
        if target_widget:
            # Scroll so the target widget is at the top
            from PySide6.QtCore import QTimer
            def _scroll_to_widget():
                widget_pos = target_widget.mapTo(self._content, target_widget.rect().topLeft())
                self._scroll.verticalScrollBar().setValue(max(0, widget_pos.y() - 8))
            # Defer to ensure layout is complete
            QTimer.singleShot(50, _scroll_to_widget)
    
    def get_date_range(self) -> tuple[datetime, datetime]:
        """Get the date range for fetching events (±3 months from current date)."""
        start = datetime.combine(self._current_date - timedelta(days=90), dt_time.min)
        end = datetime.combine(self._current_date + timedelta(days=90), dt_time.max)
        return start, end
    
    def get_scroll_position(self) -> int:
        """Get current scroll position."""
        return self._scroll.verticalScrollBar().value()
    
    def set_scroll_position(self, position: int):
        """Set scroll position."""
        self._scroll.verticalScrollBar().setValue(position)
    
    def scroll_page_forward(self):
        """Scroll forward by one page height."""
        scrollbar = self._scroll.verticalScrollBar()
        page_height = self._scroll.viewport().height()
        scrollbar.setValue(scrollbar.value() + page_height)
    
    def scroll_page_backward(self):
        """Scroll backward by one page height."""
        scrollbar = self._scroll.verticalScrollBar()
        page_height = self._scroll.viewport().height()
        scrollbar.setValue(scrollbar.value() - page_height)
    
    def scroll_to_upcoming(self):
        """Scroll to position the next upcoming event at the top of the page."""
        if not self._event_widgets:
            return
        
        # Get current local time for comparison
        now = datetime.now()
        
        # Find the first event that starts after now
        target_widget = None
        for widget in self._event_widgets:
            # Compare in local time (to_local_datetime converts UTC events to local)
            event_start = to_local_datetime(widget.event_data.start)
            # Remove timezone info for comparison if present
            event_start_naive = event_start.replace(tzinfo=None) if event_start.tzinfo else event_start
            if event_start_naive >= now:
                target_widget = widget
                break
        
        # If no future event, scroll to the last event (it's already past)
        if target_widget is None and self._event_widgets:
            target_widget = self._event_widgets[-1]
        
        if target_widget:
            # Use ensureWidgetVisible to scroll the target into view at the top
            self._scroll.ensureWidgetVisible(target_widget, 0, 0)
            # Then adjust to put it at the top
            from PySide6.QtCore import QTimer
            def _scroll_to_top():
                widget_pos = target_widget.mapTo(self._content, target_widget.rect().topLeft())
                self._scroll.verticalScrollBar().setValue(max(0, widget_pos.y() - 8))
            QTimer.singleShot(10, _scroll_to_top)
    
    def refresh_styles(self):
        """Refresh styles after config change (rebuild widgets)."""
        self._refresh_display()


class CalendarWidget(QWidget):
    """Main calendar widget with switchable views."""
    
    slot_clicked = Signal(datetime)
    slot_double_clicked = Signal(datetime)
    event_clicked = Signal(EventData)
    event_double_clicked = Signal(EventData)
    event_time_changed = Signal(EventData, datetime, datetime)  # For drag-and-drop
    view_changed = Signal(ViewType)
    date_changed = Signal(date)
    visible_range_changed = Signal(datetime, datetime)  # For list view date label updates
    
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
        self._list_view = ListView()
        
        for view in [self._day_view, self._week_view]:
            view.slot_clicked.connect(self.slot_clicked.emit)
            view.slot_double_clicked.connect(self.slot_double_clicked.emit)
            view.event_clicked.connect(self.event_clicked.emit)
            view.event_double_clicked.connect(self.event_double_clicked.emit)
            view.event_time_changed.connect(self.event_time_changed.emit)
        
        self._month_view.day_clicked.connect(lambda d: self.slot_clicked.emit(datetime.combine(d, dt_time(hour=9))))
        self._month_view.day_double_clicked.connect(lambda d: self.slot_double_clicked.emit(datetime.combine(d, dt_time(hour=9))))
        self._month_view.event_clicked.connect(self.event_clicked.emit)
        self._month_view.event_double_clicked.connect(self.event_double_clicked.emit)
        self._month_view.event_time_changed.connect(self.event_time_changed.emit)
        
        # List view only emits event clicks (no slot clicks - new events via toolbar)
        self._list_view.event_clicked.connect(self.event_clicked.emit)
        self._list_view.event_double_clicked.connect(self.event_double_clicked.emit)
        self._list_view.visible_range_changed.connect(self.visible_range_changed.emit)
        
        self._stack.addWidget(self._day_view)
        self._stack.addWidget(self._week_view)
        self._stack.addWidget(self._month_view)
        self._stack.addWidget(self._list_view)
        
        layout.addWidget(self._stack)
        self.set_view(self._current_view)
    
    def set_view(self, view_type: ViewType):
        # Capture reference datetime from the old view before switching
        old_view = self._current_view
        ref_datetime = self.get_reference_datetime()
        
        self._current_view = view_type
        
        # Use reference datetime's date for the new view
        if ref_datetime:
            self._current_date = ref_datetime.date()
        
        if view_type == ViewType.DAY:
            self._stack.setCurrentWidget(self._day_view)
            self._day_view.set_date(self._current_date)
        elif view_type == ViewType.WEEK:
            self._stack.setCurrentWidget(self._week_view)
            self._week_view.set_date(self._current_date)
        elif view_type == ViewType.MONTH:
            self._stack.setCurrentWidget(self._month_view)
            self._month_view.set_date(self._current_date)
        else:  # LIST
            self._stack.setCurrentWidget(self._list_view)
            self._list_view.set_date(self._current_date)
            # For list view switching to it: set pending scroll target
            # It will be applied after events are loaded in _refresh_display()
            if old_view != ViewType.LIST and ref_datetime:
                self._list_view._pending_scroll_datetime = ref_datetime
        
        self.view_changed.emit(view_type)
    
    def get_reference_datetime(self) -> datetime:
        """Get reference datetime for current view (for view-agnostic state persistence).
        
        Returns:
            - Day view: start of current day
            - Week view: start of current week (Monday)
            - Month view: start of current month
            - List view: first visible event datetime, or now if no events visible
        """
        if self._current_view == ViewType.DAY:
            return datetime.combine(self._current_date, dt_time.min)
        elif self._current_view == ViewType.WEEK:
            # Start of week (Monday)
            week_start = self._current_date - timedelta(days=self._current_date.weekday())
            return datetime.combine(week_start, dt_time.min)
        elif self._current_view == ViewType.MONTH:
            # Start of month
            return datetime.combine(self._current_date.replace(day=1), dt_time.min)
        else:  # LIST
            # Use tracked scroll datetime if available (more reliable than checking visible widgets)
            if self._list_view._last_scroll_datetime:
                return self._list_view._last_scroll_datetime
            # Fallback to first visible event datetime
            first_visible = self._list_view.get_first_visible_datetime()
            if first_visible:
                return first_visible
            # Final fallback: current datetime
            return datetime.now()
    
    def set_date(self, d: date):
        self._current_date = d
        self._day_view.set_date(d)
        self._week_view.set_date(d)
        self._month_view.set_date(d)
        self._list_view.set_date(d)
        self.date_changed.emit(d)
    
    def set_events(self, events: list[EventData]):
        self._day_view.set_events(events)
        self._week_view.set_events(events)
        self._month_view.set_events(events)
        self._list_view.set_events(events)
    
    def get_current_view(self) -> ViewType:
        return self._current_view
    
    def get_current_date(self) -> date:
        return self._current_date
    
    def get_date_range(self) -> tuple[datetime, datetime]:
        if self._current_view == ViewType.DAY:
            return self._day_view.get_date_range()
        elif self._current_view == ViewType.WEEK:
            return self._week_view.get_date_range()
        elif self._current_view == ViewType.MONTH:
            return self._month_view.get_date_range()
        else:  # LIST
            return self._list_view.get_date_range()
    
    def get_list_visible_range(self) -> tuple[Optional[datetime], Optional[datetime]]:
        """Get the visible date range for list view."""
        return self._list_view.get_visible_date_range()
    
    def get_list_first_visible_datetime(self) -> Optional[datetime]:
        """Get the datetime of the first visible event in list view."""
        return self._list_view.get_first_visible_datetime()
    
    def scroll_list_to_datetime(self, target_dt: datetime):
        """Scroll list view to position events at or after target_dt at top."""
        self._list_view.scroll_to_datetime(target_dt)
    
    def go_today(self):
        if self._current_view == ViewType.LIST:
            # For list view: scroll to next upcoming event
            self._list_view.scroll_to_upcoming()
        else:
            self.set_date(date.today())
    
    def go_previous(self):
        if self._current_view == ViewType.DAY:
            self.set_date(self._current_date - timedelta(days=1))
        elif self._current_view == ViewType.WEEK:
            self.set_date(self._current_date - timedelta(weeks=1))
        elif self._current_view == ViewType.MONTH:
            if self._current_date.month == 1:
                self.set_date(self._current_date.replace(year=self._current_date.year - 1, month=12))
            else:
                self.set_date(self._current_date.replace(month=self._current_date.month - 1))
        else:  # LIST - scroll up by one page
            self._list_view.scroll_page_backward()
    
    def go_next(self):
        if self._current_view == ViewType.DAY:
            self.set_date(self._current_date + timedelta(days=1))
        elif self._current_view == ViewType.WEEK:
            self.set_date(self._current_date + timedelta(weeks=1))
        elif self._current_view == ViewType.MONTH:
            if self._current_date.month == 12:
                self.set_date(self._current_date.replace(year=self._current_date.year + 1, month=1))
            else:
                self.set_date(self._current_date.replace(month=self._current_date.month + 1))
        else:  # LIST - scroll down by one page
            self._list_view.scroll_page_forward()
    
    def get_scroll_position(self) -> int:
        """Get scroll position for day/week/list views."""
        if self._current_view == ViewType.DAY:
            return self._day_view.get_scroll_position()
        elif self._current_view == ViewType.WEEK:
            return self._week_view.get_scroll_position()
        elif self._current_view == ViewType.LIST:
            return self._list_view.get_scroll_position()
        return 0
    
    def set_scroll_position(self, position: int):
        """Set scroll position for day/week/list views."""
        self._day_view.set_scroll_position(position)
        self._week_view.set_scroll_position(position)
        self._list_view.set_scroll_position(position)
    
    def refresh_styles(self):
        """Refresh styles after config change."""
        self._week_view.refresh_styles()
        self._month_view.refresh_styles()
        self._list_view.refresh_styles()
