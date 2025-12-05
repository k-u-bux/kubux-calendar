"""
Event Widget for displaying individual calendar events.

Shows event blocks in the calendar view with color coding and event info.
"""

from datetime import datetime, timedelta

from PySide6.QtWidgets import (
    QWidget, QLabel, QVBoxLayout, QHBoxLayout,
    QFrame, QSizePolicy
)
from PySide6.QtCore import Qt, Signal, QSize, QPointF
from PySide6.QtGui import QColor, QPalette, QFont, QMouseEvent, QPainter, QPolygonF, QBrush, QPen, QFontMetrics

from backend.caldav_client import EventData
from backend.config import LayoutConfig

# Module-level layout config (set by MainWindow at startup via calendar_widget)
_layout_config: LayoutConfig = LayoutConfig()


def set_event_layout_config(config: LayoutConfig):
    """Set the layout configuration for event widgets."""
    global _layout_config
    _layout_config = config


def get_text_font() -> QFont:
    """Get the configured text font for events."""
    return QFont(_layout_config.text_font, _layout_config.text_font_size)

# Get local timezone offset dynamically
import time as _time


def _get_local_tz_offset() -> timedelta:
    """Get the current local timezone offset from UTC."""
    is_dst = _time.localtime().tm_isdst
    if is_dst:
        offset_seconds = -_time.altzone
    else:
        offset_seconds = -_time.timezone
    return timedelta(seconds=offset_seconds)


def to_local_datetime(dt: datetime) -> datetime:
    """Convert datetime to local timezone."""
    if dt.tzinfo is not None:
        return dt + _get_local_tz_offset()
    return dt


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
        
        # Title (with optional indicators) - convert line breaks to spaces
        title_text = self._sanitize_text(self.event_data.summary)
        if self.event_data.is_recurring:
            title_text = "🔄 " + title_text
        # Read-only indicator is rendered as corner triangle in paintEvent
        
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
        
        # Indicators
        if self.event_data.is_recurring:
            recur_label = QLabel("🔄")
            recur_label.setToolTip("Recurring event")
            header_layout.addWidget(recur_label)
        
        # Read-only indicator is rendered as corner triangle in paintEvent
        
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
        """Return the preferred size for this widget."""
        if self.compact:
            return QSize(150, 24)
        else:
            return QSize(150, 80)
    
    def minimumSizeHint(self) -> QSize:
        """Return the minimum size for this widget."""
        if self.compact:
            return QSize(50, 20)
        else:
            return QSize(80, 40)
    
    def paintEvent(self, event) -> None:
        """Override paintEvent to draw read-only indicator triangle."""
        super().paintEvent(event)
        
        if self.event_data.read_only:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)
            
            # Calculate triangle size based on font height (half a line)
            fm = QFontMetrics(self.font())
            triangle_size = fm.height() // 2
            
            # Triangle position: bottom-right corner
            w = self.width()
            h = self.height()
            
            # Triangle points: bottom-right corner, going up and left
            points = QPolygonF([
                QPointF(w, h),                              # Bottom-right corner
                QPointF(w - triangle_size, h),              # Left along bottom
                QPointF(w, h - triangle_size)               # Up along right edge
            ])
            
            # Determine triangle color based on background luminance
            bg_color = lighten_color(self.event_data.calendar_color, 0.4)
            triangle_color = get_contrasting_text_color(bg_color)
            
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(QColor(triangle_color)))
            painter.drawPolygon(points)
            painter.end()


class AllDayEventWidget(EventWidget):
    """
    Specialized widget for all-day events.
    
    Displays in a horizontal bar format suitable for the all-day section.
    """
    
    def __init__(self, event_data: EventData, parent: QWidget = None):
        super().__init__(event_data, compact=True, show_time=False, parent=parent)
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
