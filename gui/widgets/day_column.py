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
)
from .event_portion import EventPortion
from .event_widget import EventWidget, DraggableEventWidget, DragMode
from library.timezone_utils import to_local_datetime


class DayColumnWidget(QWidget):
    """
    A single day column with absolute positioning for events.

    The widget fills the viewport.  All positioning is viewport-relative
    via the TimeAxisMapper — no content overflow, no QScrollArea inside.
    """

    slot_clicked = Signal(datetime)
    slot_double_clicked = Signal(datetime)
    event_clicked = Signal(EventData)
    event_double_clicked = Signal(EventData)
    event_time_changed = Signal(EventData, datetime, datetime)

    def __init__(self, for_date: date, time_mapper, parent=None):
        super().__init__(parent)
        self._date = for_date
        self._mapper = time_mapper
        self._viewport_height = 0
        self._scroll_ratio = 0.0
        self._portions: list[EventPortion] = []
        self._event_widgets: list[DraggableEventWidget] = []
        self._event_layout: list[tuple[EventPortion, int, int]] = []
        self._widget_to_portion: dict[DraggableEventWidget, EventPortion] = {}

        self._dragging_event: Optional[EventData] = None
        self._dragging_portion: Optional[EventPortion] = None
        self._drag_mode: DragMode = DragMode.NONE
        self._drag_start_y: int = 0
        self._drag_original_start: Optional[datetime] = None
        self._drag_original_end: Optional[datetime] = None
        self._drag_grab_offset_y: int = 0

        self._setup_ui()
        self._setup_time_indicator()
        self.setMouseTracking(True)

    def set_viewport(self, viewport_height: int, scroll_ratio: float):
        """Update viewport dimensions (called by parent on resize/scroll).

        Resizes the widget and repositions all children to reflect
        the new scroll state.
        """
        self._viewport_height = viewport_height
        self._scroll_ratio = scroll_ratio
        self.setMinimumHeight(viewport_height)
        self.setMaximumHeight(viewport_height)
        self._position_hour_lines()
        self._position_event_widgets()
        self._update_time_indicator()

    # ------------------------------------------------------------------
    # Coordinate helpers (widget ≡ viewport, no content overflow)
    # ------------------------------------------------------------------

    def _hour_to_content_y(self, hour: float) -> float:
        return self._mapper.hour_to_y(hour, self._viewport_height, self._scroll_ratio) * self._viewport_height

    def _content_y_to_hour(self, content_y: float) -> float:
        vh = max(1, self._viewport_height)
        y_norm = content_y / vh
        return self._mapper.y_to_hour(y_norm, self._viewport_height, self._scroll_ratio)

    def _setup_ui(self):
        colors = get_colors_config()
        self.setStyleSheet(
            f"background-color: {colors.day_column_background}; "
            f"border: 1px solid {colors.cell_border};"
        )
        self.setCursor(Qt.PointingHandCursor)

        self._hour_lines: list[QFrame] = []
        for _ in range(24):
            line = QFrame(self)
            line.setFrameStyle(QFrame.HLine | QFrame.Plain)
            line.setStyleSheet(f"background-color: {colors.hour_line};")
            line.hide()
            self._hour_lines.append(line)

    def _position_hour_lines(self):
        for hour in range(24):
            line = self._hour_lines[hour]
            y = self._hour_to_content_y(float(hour))
            if 0 <= y <= self._viewport_height:
                line.setGeometry(0, int(y), self.width(), 1)
                line.show()
            else:
                line.hide()

    def _setup_time_indicator(self):
        self._time_indicator = QFrame(self)
        self._time_indicator.setFrameStyle(QFrame.HLine | QFrame.Plain)
        colors = get_colors_config()
        self._time_indicator.setStyleSheet(f"background-color: {colors.current_time_line};")
        self._time_indicator.setFixedHeight(3)
        self._time_indicator.raise_()

        self._time_timer = QTimer(self)
        self._time_timer.timeout.connect(self._update_time_indicator)
        self._time_timer.start(60000)
        self._update_time_indicator()

    def _update_time_indicator(self):
        if self._date != date.today():
            self._time_indicator.hide()
            return

        self._time_indicator.show()
        now = datetime.now()
        current_hour = now.hour + now.minute / 60.0
        y_pos = int(self._hour_to_content_y(current_hour))
        self._time_indicator.setGeometry(0, y_pos, self.width(), 2)
        self._time_indicator.raise_()

    def set_date(self, new_date: date):
        self._date = new_date
        self._update_time_indicator()

    def add_portion(self, portion: EventPortion):
        self._portions.append(portion)

    def finalize_portions(self):
        self._calculate_layout()
        self._create_event_widgets()

    def _portions_overlap(self, p1: EventPortion, p2: EventPortion) -> bool:
        s1, e1 = p1.visible_start_hour, p1.visible_end_hour
        s2, e2 = p2.visible_start_hour, p2.visible_end_hour
        if e1 <= s1:
            e1 = s1 + 0.5
        if e2 <= s2:
            e2 = s2 + 0.5
        return s1 < e2 and s2 < e1

    def _calculate_layout(self):
        if not self._portions:
            self._event_layout = []
            return

        sorted_portions = sorted(
            self._portions,
            key=lambda p: (p.visible_start_hour, -(p.visible_end_hour - p.visible_start_hour)),
        )

        portion_groups: list[list[EventPortion]] = []
        for portion in sorted_portions:
            overlapping_groups = []
            for i, group in enumerate(portion_groups):
                for group_portion in group:
                    if self._portions_overlap(portion, group_portion):
                        overlapping_groups.append(i)
                        break

            if not overlapping_groups:
                portion_groups.append([portion])
            elif len(overlapping_groups) == 1:
                portion_groups[overlapping_groups[0]].append(portion)
            else:
                merged = []
                for i in sorted(overlapping_groups, reverse=True):
                    merged.extend(portion_groups.pop(i))
                merged.append(portion)
                portion_groups.append(merged)

        self._event_layout = []
        for group in portion_groups:
            group.sort(key=lambda p: p.visible_start_hour)
            columns: list[float] = []
            portion_col_map: dict[str, int] = {}

            for portion in group:
                start = portion.visible_start_hour
                end = portion.visible_end_hour
                if end <= start:
                    end = start + 0.5

                assigned = False
                for col_idx, col_end in enumerate(columns):
                    if start >= col_end:
                        columns[col_idx] = end
                        portion_col_map[portion.event.uid] = col_idx
                        assigned = True
                        break

                if not assigned:
                    portion_col_map[portion.event.uid] = len(columns)
                    columns.append(end)

            total_cols = len(columns)
            for portion in group:
                col = portion_col_map[portion.event.uid]
                self._event_layout.append((portion, col, total_cols))

    def _create_event_widgets(self):
        for widget in self._event_widgets:
            widget.hide()
            widget.deleteLater()
        self._event_widgets.clear()
        self._widget_to_portion.clear()

        for portion, col, total_cols in self._event_layout:
            event = portion.event
            if event.read_only:
                widget = EventWidget(event, compact=True, parent=self)
            else:
                widget = DraggableEventWidget(event, compact=True, parent=self)
                widget.drag_started.connect(self._on_drag_started)
                widget.drag_moved.connect(self._on_drag_moved)
                widget.drag_finished.connect(self._on_drag_finished)
                self._widget_to_portion[widget] = portion
            widget.clicked.connect(self.event_clicked.emit)
            widget.double_clicked.connect(self.event_double_clicked.emit)
            self._event_widgets.append(widget)
            widget.show()

        self._position_event_widgets()
        self._time_indicator.raise_()

    def _y_to_time(self, y: int) -> dt_time:
        layout_config = get_layout_config()
        snap_minutes = layout_config.drag_snap_minutes

        hours = self._content_y_to_hour(float(y))
        hours = max(0.0, min(24.0, hours))

        total_minutes = int(hours * 60)
        snapped_minutes = round(total_minutes / snap_minutes) * snap_minutes
        snapped_minutes = max(0, min(24 * 60 - 1, snapped_minutes))

        return dt_time(hour=snapped_minutes // 60, minute=snapped_minutes % 60)

    def _on_drag_started(self, event: EventData, mode: DragMode, y_offset_in_widget: int = 0):
        self._dragging_event = event
        self._drag_mode = mode
        self._drag_original_start = to_local_datetime(event.start)
        self._drag_original_end = to_local_datetime(event.end)

        self._dragging_portion = None
        for widget, portion in self._widget_to_portion.items():
            if portion.event == event:
                self._dragging_portion = portion
                break

        self._drag_grab_offset_y = y_offset_in_widget

    def _on_drag_moved(self, event: EventData, mode: DragMode, global_pos):
        local_pos = self.mapFromGlobal(global_pos)
        y = local_pos.y()

        if self._drag_original_start and self._drag_original_end:
            orig_start = self._drag_original_start
            orig_end = self._drag_original_end
            duration = orig_end - orig_start

            if mode == DragMode.MOVE:
                adjusted_y = y - self._drag_grab_offset_y
                new_time = self._y_to_time(adjusted_y)
                new_hour = new_time.hour + new_time.minute / 60.0

                if self._dragging_portion:
                    new_portion_start_hour = new_hour
                    new_portion_end_hour = new_portion_start_hour + (
                        self._dragging_portion.visible_end_hour
                        - self._dragging_portion.visible_start_hour
                    )
                    new_start, new_end = self._dragging_portion.calculate_new_event_times(
                        new_portion_start_hour, new_portion_end_hour
                    )
                else:
                    new_start = datetime.combine(self._date, new_time)
                    new_end = new_start + duration

                time_str = f"{new_start.strftime('%H:%M')} - {new_end.strftime('%H:%M')}"
            elif mode == DragMode.RESIZE_TOP:
                new_time = self._y_to_time(y)
                time_str = f"{new_time.strftime('%H:%M')} - {orig_end.strftime('%H:%M')}"
            elif mode == DragMode.RESIZE_BOTTOM:
                new_time = self._y_to_time(y)
                time_str = f"{orig_start.strftime('%H:%M')} - {new_time.strftime('%H:%M')}"
            else:
                return

            from PySide6.QtWidgets import QToolTip
            QToolTip.showText(global_pos, time_str, self)

    def _find_target_day_column(self, global_pos) -> tuple[date, int]:
        from PySide6.QtWidgets import QApplication

        target_date = self._date
        local_pos = self.mapFromGlobal(global_pos)
        local_y = local_pos.y()

        widget_at_pos = QApplication.widgetAt(global_pos)
        if widget_at_pos is None:
            return (target_date, local_y)

        current = widget_at_pos
        while current is not None:
            if isinstance(current, DayColumnWidget):
                target_date = current._date
                local_y = current.mapFromGlobal(global_pos).y()
                break
            current = current.parentWidget()

        return (target_date, local_y)

    def _on_drag_finished(self, event: EventData, mode: DragMode, global_pos):
        if self._dragging_event is None:
            return

        target_date, y = self._find_target_day_column(global_pos)

        orig_start = self._drag_original_start
        orig_end = self._drag_original_end
        duration = orig_end - orig_start

        if mode == DragMode.MOVE:
            adjusted_y = y - self._drag_grab_offset_y
            new_time = self._y_to_time(adjusted_y)
            new_hour = new_time.hour + new_time.minute / 60.0

            if self._dragging_portion:
                new_portion_start_hour = new_hour
                new_portion_end_hour = new_portion_start_hour + (
                    self._dragging_portion.visible_end_hour
                    - self._dragging_portion.visible_start_hour
                )
                new_start, new_end = self._dragging_portion.calculate_new_event_times(
                    new_portion_start_hour, new_portion_end_hour
                )
                if target_date != self._dragging_portion.display_date:
                    date_delta = target_date - self._dragging_portion.display_date
                    new_start = new_start + date_delta
                    new_end = new_end + date_delta
            else:
                new_start = datetime.combine(target_date, new_time)
                new_end = new_start + duration
        elif mode == DragMode.RESIZE_TOP:
            new_time = self._y_to_time(y)
            new_start = datetime.combine(self._date, new_time)
            new_end = orig_end
            start_cmp = new_start.replace(tzinfo=None) if new_start.tzinfo else new_start
            end_cmp = new_end.replace(tzinfo=None) if new_end.tzinfo else new_end
            if start_cmp >= end_cmp:
                end_naive = new_end.replace(tzinfo=None) if new_end.tzinfo else new_end
                new_start = end_naive - timedelta(minutes=get_layout_config().drag_snap_minutes)
        elif mode == DragMode.RESIZE_BOTTOM:
            new_time = self._y_to_time(y)
            new_start = orig_start
            new_end = datetime.combine(self._date, new_time)
            start_cmp = new_start.replace(tzinfo=None) if new_start.tzinfo else new_start
            end_cmp = new_end.replace(tzinfo=None) if new_end.tzinfo else new_end
            if end_cmp <= start_cmp:
                start_naive = new_start.replace(tzinfo=None) if new_start.tzinfo else new_start
                new_end = start_naive + timedelta(minutes=get_layout_config().drag_snap_minutes)
        else:
            self._dragging_event = None
            return

        self.event_time_changed.emit(event, new_start, new_end)
        self._dragging_event = None
        self._drag_mode = DragMode.NONE

    def _position_event_widgets(self):
        available_width = self.width() - 4

        for widget, (portion, col, total_cols) in zip(self._event_widgets, self._event_layout):
            start_hour = max(0.0, min(24.0, portion.visible_start_hour))
            end_hour = max(0.0, min(24.0, portion.visible_end_hour))
            if end_hour <= start_hour:
                end_hour = start_hour + 0.5

            y = int(self._hour_to_content_y(start_hour))
            y_end = int(self._hour_to_content_y(end_hour))
            height = max(y_end - y, 20)

            col_width = available_width // total_cols
            x = 2 + col * col_width
            width = col_width - 1

            widget.setGeometry(x, y + 1, width, height - 2)

        # Repaint background to clear artifacts from old widget positions
        self.update()

    def clear_portions(self):
        for widget in self._event_widgets:
            widget.hide()
            widget.deleteLater()
        self._event_widgets.clear()
        self._portions.clear()
        self._event_layout.clear()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._position_event_widgets()
        self._position_hour_lines()
        self._update_time_indicator()

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