"""
Event Widget for displaying individual calendar events.

Shows event blocks in the calendar view with color coding and event info.
Supports drag-and-drop for moving events and resize handles for changing duration.
"""

from datetime import datetime, timedelta
from enum import Enum

from PySide6.QtWidgets import (
    QWidget, QLabel, QVBoxLayout, QHBoxLayout,
    QFrame, QSizePolicy
)
from PySide6.QtCore import Qt, Signal, QSize, QPointF, QPoint
from PySide6.QtGui import QColor, QPalette, QFont, QMouseEvent, QPainter, QPolygonF, QBrush, QPen, QFontMetrics

from backend.caldav_client import EventData
from backend.config import LayoutConfig, ColorsConfig


class DragMode(Enum):
    """Type of drag operation."""
    NONE = "none"
    MOVE = "move"           # Moving the entire event
    RESIZE_TOP = "resize_top"      # Resizing by dragging top edge
    RESIZE_BOTTOM = "resize_bottom"  # Resizing by dragging bottom edge

# Module-level configs (set by MainWindow at startup via calendar_widget)
_layout_config: LayoutConfig = LayoutConfig()
_colors_config: ColorsConfig = ColorsConfig()


def set_event_layout_config(config: LayoutConfig):
    """Set the layout configuration for event widgets."""
    global _layout_config
    _layout_config = config


def set_event_colors_config(config: ColorsConfig):
    """Set the colors configuration for event widgets."""
    global _colors_config
    _colors_config = config


def get_text_font() -> QFont:
    """Get the configured text font for events."""
    return QFont(_layout_config.text_font, _layout_config.text_font_size)


# Import timezone utilities from shared module
from backend.timezone_utils import to_local_datetime


def get_contrasting_text_color(bg_color: str) -> str:
    """Calculate whether black or white text contrasts better with the background."""
    # Parse hex color
    color = bg_color.lstrip('#')
    if len(color) == 3:
        color = ''.join([c*2 for c in color])
    
    try:
        r = int(color[0:2], 16)
        g = int(color[2:4], 16)
        b = int(color[4:6], 16)
    except (ValueError, IndexError):
        return "#000000"
    
    # Calculate luminance
    luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255
    
    return "#000000" if luminance > 0.5 else "#ffffff"


def lighten_color(hex_color: str, factor: float = 0.3) -> str:
    """Lighten a hex color by the given factor."""
    color = hex_color.lstrip('#')
    if len(color) == 3:
        color = ''.join([c*2 for c in color])
    
    try:
        r = int(color[0:2], 16)
        g = int(color[2:4], 16)
        b = int(color[4:6], 16)
    except (ValueError, IndexError):
        return hex_color
    
    r = int(min(255, r + (255 - r) * factor))
    g = int(min(255, g + (255 - g) * factor))
    b = int(min(255, b + (255 - b) * factor))
    
    return f"#{r:02x}{g:02x}{b:02x}"


class EventWidget(QFrame):
    """
    Widget representing a single event in the calendar view.
    
    Displays the event title, time, and optional indicators for
    recurrence, all-day events, and read-only status.
    """
    
    # Signal emitted when the event is clicked
    clicked = Signal(EventData)
    
    # Signal emitted when the event is double-clicked (for editing)
    double_clicked = Signal(EventData)
    
    def __init__(
        self,
        event_data: EventData,
        compact: bool = False,
        show_time: bool = True,
        show_location: bool = True,
        parent: QWidget = None
    ):
        """
        Initialize the event widget.
        
        Args:
            event_data: The event data to display
            compact: If True, use a compact single-line layout
            show_time: If True, show the event time
            show_location: If True, show the location (if present)
            parent: Parent widget
        """
        super().__init__(parent)
        self.event_data = event_data
        self.compact = compact
        self.show_time = show_time
        self.show_location = show_location
        
        self._setup_ui()
        self._apply_style()
    
    def _setup_ui(self) -> None:
        """Set up the widget UI."""
        # Apply text font to this widget and its children
        self.setFont(get_text_font())
        
        if self.compact:
            self._setup_compact_ui()
        else:
            self._setup_full_ui()
        
        self.setFrameStyle(QFrame.StyledPanel | QFrame.Plain)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        
        # Set tooltip with full event info
        self._setup_tooltip()
    
    def _sanitize_text(self, text: str) -> str:
        """Convert line breaks to spaces for single-line display."""
        if not text:
            return text
        # Replace various line break types with a single space
        return ' '.join(text.split())
    
    def _setup_tooltip(self) -> None:
        """Set up the tooltip with full event information."""
        lines = []
        
        # Title
        lines.append(f"<b>{self.event_data.summary}</b>")
        
        # Time
        if self.event_data.all_day:
            lines.append("All day")
        else:
            local_start = to_local_datetime(self.event_data.start)
            local_end = to_local_datetime(self.event_data.end)
            lines.append(f"{local_start.strftime('%H:%M')} - {local_end.strftime('%H:%M')}")
        
        # Location
        if self.event_data.location:
            lines.append(f"📍 {self.event_data.location}")
        
        # Calendar
        lines.append(f"<i>{self.event_data.calendar_name}</i>")
        
        # Description (truncate if too long)
        if self.event_data.description:
            desc = self.event_data.description
            if len(desc) > 200:
                desc = desc[:200] + "..."
            lines.append(f"<br>{desc}")
        
        self.setToolTip("<br>".join(lines))
    
    def _setup_compact_ui(self) -> None:
        """Set up a compact layout with title and optionally location, top-aligned."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(0)
        layout.setAlignment(Qt.AlignTop)
        
        # Get the text font for events
        text_font = get_text_font()
        
        # Title - convert line breaks to spaces
        # Indicators (recurring, read-only) are rendered as corner triangles in paintEvent
        title_text = self._sanitize_text(self.event_data.summary)
        
        title_label = QLabel(title_text)
        title_label.setWordWrap(False)  # Single line, no wrapping
        title_label.setTextFormat(Qt.PlainText)
        # Apply text font with bold
        title_font = QFont(text_font)
        title_font.setBold(True)
        title_label.setFont(title_font)
        layout.addWidget(title_label)
        
        # Location (if present and show_location is True)
        if self.show_location and self.event_data.location:
            location_text = self._sanitize_text(self.event_data.location)
            location_label = QLabel(location_text)
            location_label.setWordWrap(False)  # Single line, no wrapping
            location_label.setTextFormat(Qt.PlainText)
            location_label.setFont(text_font)
            layout.addWidget(location_label)
    
    def _setup_full_ui(self) -> None:
        """Set up a full multi-line layout."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(2)
        
        # Get the text font for events
        text_font = get_text_font()
        
        # Header row with time and indicators
        header_layout = QHBoxLayout()
        header_layout.setSpacing(4)
        
        # Time
        if self.show_time and not self.event_data.all_day:
            local_start = to_local_datetime(self.event_data.start)
            local_end = to_local_datetime(self.event_data.end)
            time_text = f"{local_start.strftime('%H:%M')} - {local_end.strftime('%H:%M')}"
            time_label = QLabel(time_text)
            time_label.setFont(text_font)
            header_layout.addWidget(time_label)
        elif self.event_data.all_day:
            all_day_label = QLabel("All day")
            all_day_label.setFont(text_font)
            header_layout.addWidget(all_day_label)
        
        header_layout.addStretch()
        
        # Indicators (recurring, read-only) are rendered as corner triangles in paintEvent
        
        layout.addLayout(header_layout)
        
        # Title
        title_label = QLabel(self.event_data.summary)
        title_font = QFont(text_font)
        title_font.setBold(True)
        title_label.setFont(title_font)
        title_label.setWordWrap(True)
        layout.addWidget(title_label)
        
        # Location (if present)
        if self.event_data.location:
            location_label = QLabel(f"📍 {self.event_data.location}")
            location_label.setFont(text_font)
            location_label.setWordWrap(True)
            layout.addWidget(location_label)
        
        # Calendar name
        cal_label = QLabel(self.event_data.calendar_name)
        cal_label.setFont(text_font)
        cal_label.setStyleSheet("color: rgba(0, 0, 0, 0.6);")
        layout.addWidget(cal_label)
    
    def _apply_style(self) -> None:
        """Apply color styling based on the event's calendar color."""
        bg_color = self.event_data.calendar_color
        text_color = get_contrasting_text_color(bg_color)
        border_color = bg_color
        
        # Lighten background slightly for better readability
        bg_lighter = lighten_color(bg_color, 0.4)
        
        self.setStyleSheet(f"""
            EventWidget {{
                background-color: {bg_lighter};
                border: 2px solid {border_color};
                border-left: 4px solid {border_color};
                border-radius: 4px;
                color: {text_color};
            }}
            EventWidget:hover {{
                background-color: {lighten_color(bg_color, 0.2)};
            }}
            QLabel {{
                color: {text_color};
                background: transparent;
                border: none;
                padding: 0px;
                margin: 0px;
            }}
        """)
    
    def mousePressEvent(self, mouse_event: QMouseEvent) -> None:
        """Handle mouse press event."""
        if mouse_event.button() == Qt.LeftButton:
            self.clicked.emit(self.event_data)
        super().mousePressEvent(mouse_event)
    
    def mouseDoubleClickEvent(self, mouse_event: QMouseEvent) -> None:
        """Handle double-click event."""
        if mouse_event.button() == Qt.LeftButton:
            self.double_clicked.emit(self.event_data)
        super().mouseDoubleClickEvent(mouse_event)
    
    def sizeHint(self) -> QSize:
        """Return the preferred size for this widget based on font metrics."""
        fm = QFontMetrics(self.font())
        line_height = fm.height()
        
        if self.compact:
            # Compact: 1-2 lines (title + optional location) + padding
            num_lines = 2 if (self.show_location and self.event_data.location) else 1
            height = num_lines * line_height + 8  # 4px padding top/bottom
            return QSize(150, height)
        else:
            # Full: time + title + location + calendar = up to 4 lines + padding
            num_lines = 2  # time header + title (minimum)
            if self.event_data.location:
                num_lines += 1
            num_lines += 1  # calendar name
            height = num_lines * line_height + 12  # 6px padding top/bottom
            return QSize(150, height)
    
    def minimumSizeHint(self) -> QSize:
        """Return the minimum size for this widget based on font metrics."""
        fm = QFontMetrics(self.font())
        line_height = fm.height()
        
        if self.compact:
            # Minimum: 1 line + minimal padding
            return QSize(50, line_height + 4)
        else:
            # Minimum: 2 lines (time + title) + minimal padding
            return QSize(80, 2 * line_height + 8)
    
    def paintEvent(self, event) -> None:
        """Override paintEvent to draw indicator triangles for recurring, read-only, and sync status."""
        super().paintEvent(event)
        
        # Determine if we need to draw any triangles
        has_pending_sync = self.event_data.sync_status != "synced"
        if not self.event_data.is_recurring and not self.event_data.read_only and not has_pending_sync:
            return
        
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        # Calculate triangle size based on font height (half a line)
        fm = QFontMetrics(self.font())
        triangle_size = fm.height() // 2
        
        w = self.width()
        h = self.height()
        
        # Determine triangle color based on background luminance
        bg_color = lighten_color(self.event_data.calendar_color, 0.4)
        triangle_color = get_contrasting_text_color(bg_color)
        
        painter.setPen(Qt.NoPen)
        
        # Draw sync pending indicator triangle in upper-right corner (black)
        if has_pending_sync:
            painter.setBrush(QBrush(QColor("#000000")))
            sync_points = QPolygonF([
                QPointF(w, 0),                              # Top-right corner
                QPointF(w - triangle_size, 0),              # Left along top
                QPointF(w, triangle_size)                   # Down along right edge
            ])
            painter.drawPolygon(sync_points)
        
        painter.setBrush(QBrush(QColor(triangle_color)))
        
        # Draw recurring indicator triangle in bottom-left corner
        if self.event_data.is_recurring:
            recurring_points = QPolygonF([
                QPointF(0, h),                              # Bottom-left corner
                QPointF(triangle_size, h),                  # Right along bottom
                QPointF(0, h - triangle_size)               # Up along left edge
            ])
            painter.drawPolygon(recurring_points)
        
        # Draw read-only indicator triangle in bottom-right corner
        if self.event_data.read_only:
            readonly_points = QPolygonF([
                QPointF(w, h),                              # Bottom-right corner
                QPointF(w - triangle_size, h),              # Left along bottom
                QPointF(w, h - triangle_size)               # Up along right edge
            ])
            painter.drawPolygon(readonly_points)
        
        painter.end()


class DraggableEventWidget(EventWidget):
    """
    EventWidget with drag-and-drop and resize support.
    
    In Day/Week views: drag to move event to different time, resize edges to change duration.
    In Month view: drag to change date (keeps time).
    """
    
    # Signals for drag operations
    drag_started = Signal(EventData, DragMode)  # Event data, drag mode
    drag_moved = Signal(EventData, DragMode, QPoint)  # Event data, mode, global position
    drag_finished = Signal(EventData, DragMode, QPoint)  # Event data, mode, final global position
    
    # Resize zone height in pixels
    RESIZE_ZONE_HEIGHT = 8
    
    # Drag threshold in pixels (to distinguish from click)
    DRAG_THRESHOLD = 5
    
    def __init__(
        self,
        event_data: EventData,
        compact: bool = False,
        show_time: bool = True,
        show_location: bool = True,
        parent: QWidget = None
    ):
        super().__init__(event_data, compact, show_time, show_location, parent)
        
        # Drag state
        self._drag_mode = DragMode.NONE
        self._press_pos: QPoint = None
        self._press_global_pos: QPoint = None
        self._is_dragging = False
        
        # Enable mouse tracking for cursor changes
        self.setMouseTracking(True)
    
    def _get_drag_mode_at_pos(self, pos: QPoint) -> DragMode:
        """Determine what drag mode should be used based on mouse position."""
        # Read-only events cannot be dragged
        if self.event_data.read_only:
            return DragMode.NONE
        
        h = self.height()
        y = pos.y()
        
        # Check resize zones (top and bottom edges)
        if y <= self.RESIZE_ZONE_HEIGHT:
            return DragMode.RESIZE_TOP
        elif y >= h - self.RESIZE_ZONE_HEIGHT:
            return DragMode.RESIZE_BOTTOM
        else:
            return DragMode.MOVE
    
    def _update_cursor(self, mode: DragMode) -> None:
        """Update cursor based on drag mode."""
        if mode == DragMode.RESIZE_TOP or mode == DragMode.RESIZE_BOTTOM:
            self.setCursor(Qt.SizeVerCursor)
        elif mode == DragMode.MOVE:
            self.setCursor(Qt.OpenHandCursor)
        else:
            self.setCursor(Qt.PointingHandCursor)
    
    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """Handle mouse move for cursor changes and dragging."""
        if self._press_pos is not None and event.buttons() & Qt.LeftButton:
            # Check if we've exceeded the drag threshold
            if not self._is_dragging:
                distance = (event.pos() - self._press_pos).manhattanLength()
                if distance >= self.DRAG_THRESHOLD:
                    self._is_dragging = True
                    self.setCursor(Qt.ClosedHandCursor)
                    self.drag_started.emit(self.event_data, self._drag_mode)
            
            if self._is_dragging:
                # Emit drag move with global position
                self.drag_moved.emit(self.event_data, self._drag_mode, event.globalPosition().toPoint())
        else:
            # Not dragging - just update cursor based on position
            mode = self._get_drag_mode_at_pos(event.pos())
            self._update_cursor(mode)
        
        # Don't call super() during drag to prevent event propagation issues
        if not self._is_dragging:
            super().mouseMoveEvent(event)
    
    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Handle mouse press - start potential drag."""
        if event.button() == Qt.LeftButton and not self.event_data.read_only:
            self._press_pos = event.pos()
            self._press_global_pos = event.globalPosition().toPoint()
            self._drag_mode = self._get_drag_mode_at_pos(event.pos())
            self._is_dragging = False
            # Don't emit clicked yet - wait to see if this is a drag
        else:
            super().mousePressEvent(event)
    
    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """Handle mouse release - complete drag or emit click."""
        if event.button() == Qt.LeftButton:
            if self._is_dragging:
                # Complete drag operation
                self.drag_finished.emit(
                    self.event_data,
                    self._drag_mode,
                    event.globalPosition().toPoint()
                )
                self.setCursor(Qt.OpenHandCursor)
            else:
                # Was a click, not a drag
                self.clicked.emit(self.event_data)
            
            # Reset drag state
            self._press_pos = None
            self._press_global_pos = None
            self._drag_mode = DragMode.NONE
            self._is_dragging = False
        else:
            super().mouseReleaseEvent(event)
    
    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        """Handle double-click - reset drag state and emit signal."""
        self._press_pos = None
        self._is_dragging = False
        self._drag_mode = DragMode.NONE
        super().mouseDoubleClickEvent(event)
    
    def leaveEvent(self, event) -> None:
        """Reset cursor when mouse leaves widget."""
        if not self._is_dragging:
            self.setCursor(Qt.PointingHandCursor)
        super().leaveEvent(event)


class AllDayEventWidget(EventWidget):
    """
    Specialized widget for all-day events.
    
    Displays in a horizontal bar format suitable for the all-day section.
    """
    
    def __init__(self, event_data: EventData, parent: QWidget = None):
        super().__init__(event_data, compact=True, show_time=False, show_location=False, parent=parent)
        # Set height based on font metrics (1 line + padding)
        from PySide6.QtGui import QFontMetrics
        fm = QFontMetrics(self.font())
        line_height = fm.height()
        self.setMaximumHeight(line_height + 8)  # 8px padding
    
    def _apply_style(self) -> None:
        """Apply styling for all-day events."""
        bg_color = self.event_data.calendar_color
        text_color = get_contrasting_text_color(bg_color)
        
        self.setStyleSheet(f"""
            AllDayEventWidget {{
                background-color: {bg_color};
                border-radius: 3px;
                color: {text_color};
                padding: 2px 6px;
            }}
            AllDayEventWidget:hover {{
                background-color: {lighten_color(bg_color, -0.1)};
            }}
            QLabel {{
                color: {text_color};
                background: transparent;
            }}
        """)
