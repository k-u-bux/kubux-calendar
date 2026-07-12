"""
DayColumnWidget: a single day column with absolute-positioned events.

Extracted from ``calendar_widget.py``.
"""

from datetime import datetime, date, time as dt_time, timedelta
from typing import Optional

from PySide6.QtWidgets import QWidget, QFrame
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QMouseEvent

from backend import EventView as EventData
from .config_state import (
    get_colors_config,
    get_layout_config,
    get_hour_height,
)
from .event_portion import EventPortion
from .event_widget import EventWidget, DraggableEventWidget, DragMode
from .time_axis import TimeAxisMapper
from library.timezone_utils import to_local_datetime


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

    def __init__(self, for_date: date, time_mapper: TimeAxisMapper, parent=None):
        super().__init__(parent)
        self._date = for_date
        self._mapper = time_mapper
        self._viewport_height = 0
        self._scroll_offset = 0
        self._portions: list[EventPortion] = []
        self._event_widgets: list[DraggableEventWidget] = []
        self._event_layout: list[tuple[EventPortion, int, int]] = []  # (portion, column, total_columns)
        self._widget_to_portion: dict[DraggableEventWidget, EventPortion] = {}  # Map widgets to portions

        # Drag state
        self._dragging_event: Optional[EventData] = None
        self._dragging_portion: Optional[EventPortion] = None  # Track which portion is being dragged
        self._drag_mode: DragMode = DragMode.NONE
        self._drag_start_y: int = 0
        self._drag_original_start: Optional[datetime] = None
        self._drag_original_end: Optional[datetime] = None
        self._drag_grab_offset_y: int = 0  # Offset from event top to grab point (pixels)

        self._setup_ui()
        self._setup_time_indicator()

        # Enable mouse tracking for drag handling
        self.setMouseTracking(True)

    def set_viewport(self, viewport_height: int, scroll_offset: int):
        """Update viewport dimensions (called by parent on resize/scroll)."""
        self._viewport_height = viewport_height
        self._scroll_offset = scroll_offset

    # ------------------------------------------------------------------
    # Content-space helpers (bridge between viewport-normalized mapper
    # and content-pixel coordinates used by QScrollArea)
    # ------------------------------------------------------------------

    def _hour_to_content_y(self, hour: float) -> float:
        """hour → content pixel Y."""
        y_norm = self._mapper.hour_to_y(hour, self._viewport_height, self._scroll_offset)
        return y_norm * self._viewport_height + self._scroll_offset

    def _content_y_to_hour(self, content_y: float) -> float:
        """content pixel Y → hour."""
        y_norm = (content_y - self._scroll_offset) / max(1, self._viewport_height)
        return self._mapper.y_to_hour(y_norm, self._viewport_height, self._scroll_offset)

    def _setup_ui(self):
        colors = get_colors_config()
        hour_height = get_hour_height()
        content_h = 24 * hour_height
        # Fixed height for 24 hours
        self.setMinimumHeight(content_h)
        self.setMaximumHeight(content_h)
        self.setStyleSheet(f"background-color: {colors.day_column_background}; border: 1px solid {colors.cell_border};")
        self.setCursor(Qt.PointingHandCursor)

        # Draw hour lines
        for hour in range(1, 24):
            line = QFrame(self)
            line.setFrameStyle(QFrame.HLine | QFrame.Plain)
            line.setStyleSheet(f"background-color: {colors.hour_line};")
            y = int(self._hour_to_content_y(float(hour)))
            line.setGeometry(0, y, 2000, 1)

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
        y_pos = int(self._hour_to_content_y(current_hour))
        self._time_indicator.setGeometry(0, y_pos, self.width(), 2)
        self._time_indicator.raise_()  # Keep on top

    def set_date(self, new_date: date):
        self._date = new_date
        self._update_time_indicator()  # Update visibility based on new date
        # Note: Event refresh handled by parent view (DayView/WeekView)

    def add_portion(self, portion: EventPortion):
        """Add an event portion to this day column."""
        self._portions.append(portion)

    def finalize_portions(self):
        """Call after all portions are added to calculate layout and create widgets."""
        self._calculate_layout()
        self._create_event_widgets()

    def _portions_overlap(self, p1: EventPortion, p2: EventPortion) -> bool:
        """Check if two event portions overlap in visible hours on this day."""
        s1 = p1.visible_start_hour
        e1 = p1.visible_end_hour
        s2 = p2.visible_start_hour
        e2 = p2.visible_end_hour
        # Ensure minimum duration
        if e1 <= s1:
            e1 = s1 + 0.5
        if e2 <= s2:
            e2 = s2 + 0.5
        return s1 < e2 and s2 < e1

    def _calculate_layout(self):
        """Calculate column positions for overlapping portions."""
        if not self._portions:
            self._event_layout = []
            return

        # Sort portions by start hour, then by duration (longer first)
        sorted_portions = sorted(self._portions, key=lambda p: (p.visible_start_hour, -(p.visible_end_hour - p.visible_start_hour)))

        # Assign columns to portions
        portion_groups: list[list[EventPortion]] = []  # groups of overlapping portions

        # Build overlap groups
        for portion in sorted_portions:
            # Find which existing groups this portion overlaps with
            overlapping_groups = []
            for i, group in enumerate(portion_groups):
                for group_portion in group:
                    if self._portions_overlap(portion, group_portion):
                        overlapping_groups.append(i)
                        break

            if not overlapping_groups:
                # Start a new group
                portion_groups.append([portion])
            elif len(overlapping_groups) == 1:
                # Add to existing group
                portion_groups[overlapping_groups[0]].append(portion)
            else:
                # Merge groups
                merged = []
                for i in sorted(overlapping_groups, reverse=True):
                    merged.extend(portion_groups.pop(i))
                merged.append(portion)
                portion_groups.append(merged)

        # Assign column numbers within each group
        self._event_layout = []
        for group in portion_groups:
            # Sort group by start time
            group.sort(key=lambda p: p.visible_start_hour)

            # Assign columns greedily
            columns: list[float] = []  # end hour of portion in each column
            portion_col_map: dict[str, int] = {}  # event.uid -> column

            for portion in group:
                start = portion.visible_start_hour
                end = portion.visible_end_hour
                if end <= start:
                    end = start + 0.5

                # Find first column where this portion fits
                assigned = False
                for col_idx, col_end in enumerate(columns):
                    if start >= col_end:
                        columns[col_idx] = end
                        portion_col_map[portion.event.uid] = col_idx
                        assigned = True
                        break

                if not assigned:
                    # Need a new column
                    portion_col_map[portion.event.uid] = len(columns)
                    columns.append(end)

            total_cols = len(columns)
            for portion in group:
                col = portion_col_map[portion.event.uid]
                self._event_layout.append((portion, col, total_cols))

    def _create_event_widgets(self):
        """Create and position event widgets based on calculated layout."""
        for widget in self._event_widgets:
            widget.hide()  # Hide immediately to prevent visual duplication
            widget.deleteLater()
        self._event_widgets.clear()
        self._widget_to_portion.clear()

        for portion, col, total_cols in self._event_layout:
            # Use DraggableEventWidget for editable events, EventWidget for read-only
            # Get the actual EventData from the portion
            event = portion.event
            if event.read_only:
                widget = EventWidget(event, compact=True, parent=self)
            else:
                widget = DraggableEventWidget(event, compact=True, parent=self)
                widget.drag_started.connect(self._on_drag_started)
                widget.drag_moved.connect(self._on_drag_moved)
                widget.drag_finished.connect(self._on_drag_finished)
                # Map widget to portion for drag operations
                self._widget_to_portion[widget] = portion
            widget.clicked.connect(self.event_clicked.emit)
            widget.double_clicked.connect(self.event_double_clicked.emit)
            self._event_widgets.append(widget)
            widget.show()

        self._position_event_widgets()

        # Ensure time indicator stays on top of event widgets
        self._time_indicator.raise_()

    def _y_to_time(self, y: int) -> dt_time:
        """Convert content-absolute Y position to time, snapped to configured interval."""
        layout_config = get_layout_config()
        snap_minutes = layout_config.drag_snap_minutes

        hours = self._content_y_to_hour(float(y))
        hours = max(0.0, min(24.0, hours))

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

        # Find which portion is being dragged
        self._dragging_portion = None
        for widget, portion in self._widget_to_portion.items():
            if portion.event == event:
                self._dragging_portion = portion
                break

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
                new_hour = new_time.hour + new_time.minute / 60.0

                # Use portion to calculate event times if available
                if self._dragging_portion:
                    # Calculate where the portion moves to
                    new_portion_start_hour = new_hour
                    new_portion_end_hour = new_portion_start_hour + (self._dragging_portion.visible_end_hour - self._dragging_portion.visible_start_hour)

                    # Use portion's method to calculate new event times
                    new_start, new_end = self._dragging_portion.calculate_new_event_times(
                        new_portion_start_hour,
                        new_portion_end_hour
                    )
                else:
                    # Fallback: treat as single-day event
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
            new_hour = new_time.hour + new_time.minute / 60.0

            # Use portion to calculate event times if available
            if self._dragging_portion:
                # Calculate where the portion moves to
                new_portion_start_hour = new_hour
                new_portion_end_hour = new_portion_start_hour + (self._dragging_portion.visible_end_hour - self._dragging_portion.visible_start_hour)

                # Use portion's method to calculate new event times
                new_start, new_end = self._dragging_portion.calculate_new_event_times(
                    new_portion_start_hour,
                    new_portion_end_hour
                )

                # Handle cross-day dragging - adjust for date change
                if target_date != self._dragging_portion.display_date:
                    date_delta = target_date - self._dragging_portion.display_date
                    new_start = new_start + date_delta
                    new_end = new_end + date_delta
            else:
                # Fallback: treat as single-day event
                new_start = datetime.combine(target_date, new_time)
                new_end = new_start + duration
        elif mode == DragMode.RESIZE_TOP:
            # For resize, use mouse Y directly (no offset adjustment)
            new_time = self._y_to_time(y)
            # Change start time on THIS day, keep original end date/time
            new_start = datetime.combine(self._date, new_time)
            new_end = orig_end  # Preserve original end (including date!)
            # Don't allow start after end (strip timezone for comparison)
            start_cmp = new_start.replace(tzinfo=None) if new_start.tzinfo else new_start
            end_cmp = new_end.replace(tzinfo=None) if new_end.tzinfo else new_end
            if start_cmp >= end_cmp:
                # Set start to minimum interval before end
                end_naive = new_end.replace(tzinfo=None) if new_end.tzinfo else new_end
                new_start = end_naive - timedelta(minutes=get_layout_config().drag_snap_minutes)
        elif mode == DragMode.RESIZE_BOTTOM:
            # For resize, use mouse Y directly (no offset adjustment)
            new_time = self._y_to_time(y)
            # Change end time on THIS day, keep original start date/time
            new_start = orig_start  # Preserve original start (including date!)
            new_end = datetime.combine(self._date, new_time)
            # Don't allow end before start (strip timezone for comparison)
            start_cmp = new_start.replace(tzinfo=None) if new_start.tzinfo else new_start
            end_cmp = new_end.replace(tzinfo=None) if new_end.tzinfo else new_end
            if end_cmp <= start_cmp:
                # Set end to minimum interval after start
                start_naive = new_start.replace(tzinfo=None) if new_start.tzinfo else new_start
                new_end = start_naive + timedelta(minutes=get_layout_config().drag_snap_minutes)
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

        for widget, (portion, col, total_cols) in zip(self._event_widgets, self._event_layout):
            # Use portion's visible hours for this specific day
            start_hour = portion.visible_start_hour
            end_hour = portion.visible_end_hour
            start_hour = max(0.0, min(24.0, start_hour))
            end_hour = max(0.0, min(24.0, end_hour))
            if end_hour <= start_hour:
                end_hour = start_hour + 0.5

            y = int(self._hour_to_content_y(start_hour))
            y_end = int(self._hour_to_content_y(end_hour))
            height = max(y_end - y, 20)

            # Calculate width and x position based on column
            col_width = available_width // total_cols
            x = 2 + col * col_width
            width = col_width - 1  # 1px gap between columns

            widget.setGeometry(x, y + 1, width, height - 2)

    def clear_portions(self):
        """Clear all portions and widgets."""
        for widget in self._event_widgets:
            widget.hide()  # Hide immediately to prevent visual duplication
            widget.deleteLater()
        self._event_widgets.clear()
        self._portions.clear()
        self._event_layout.clear()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._position_event_widgets()
        self._update_time_indicator()  # Update width on resize

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            y = event.position().y()
            dt_hour = self._y_to_time(y)
            dt = datetime.combine(self._date, dt_hour)
            self.slot_clicked.emit(dt)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            y = event.position().y()
            dt_hour = self._y_to_time(y)
            dt = datetime.combine(self._date, dt_hour)
            self.slot_double_clicked.emit(dt)
        super().mouseDoubleClickEvent(event)