"""
ListView: chronological list view of events with bi-infinite scrolling.

Extracted from ``calendar_widget.py``.
"""

from datetime import datetime, date, timedelta, time as dt_time
from typing import Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QScrollArea, QFrame, QSizePolicy,
)
from PySide6.QtCore import Qt, Signal, QTimer, QPointF
from PySide6.QtGui import QFont, QFontMetrics, QMouseEvent, QPainter, QBrush, QColor, QPolygonF

from backend import EventView as EventData
from .config_state import get_colors_config
from .event_widget import (
    get_text_font,
    get_contrasting_text_color,
    lighten_color,
)
from library.timezone_utils import to_local_datetime


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
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(12)

        text_font = get_text_font()

        # Date/time column (two lines, width based on font metrics)
        local_start = to_local_datetime(self.event_data.start)
        local_end = to_local_datetime(self.event_data.end)

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
            recurring_points = QPolygonF([
                QPointF(0, h),
                QPointF(triangle_size, h),
                QPointF(0, h - triangle_size)
            ])
            painter.drawPolygon(recurring_points)

        if self.event_data.read_only:
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
        self._anchor_datetime: Optional[datetime] = None  # First visible event - live position anchor
        self._rebuilding = False  # True while a rebuild is in progress (anchor updates suspended)
        self._batch_generation = 0  # Incremented on each _refresh_display to invalidate old batch timers
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
        self._content.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        main_layout.addWidget(self._scroll)

        # Connect scroll to detect visible range
        self._scroll.verticalScrollBar().valueChanged.connect(self._on_scroll)

    def _on_scroll(self):
        """Handle scroll event to update visible range and position anchor."""
        # Ignore scroll events caused by a rebuild in progress - they would
        # clobber the anchor we are about to restore to, and the transient
        # visible range is meaningless to listeners.
        if self._rebuilding:
            return
        visible_range = self.get_visible_date_range()
        if visible_range[0] and visible_range[1]:
            self._anchor_datetime = visible_range[0]
            self.visible_range_changed.emit(visible_range[0], visible_range[1])

    def get_anchor_datetime(self) -> Optional[datetime]:
        """Get the live position anchor (start of first visible event)."""
        return self._anchor_datetime

    def set_date(self, d: date):
        """Set the current date (used for navigation context)."""
        self._current_date = d

    def set_events(self, events: list[EventData]):
        """Set events and rebuild the list."""
        self._events = events
        self._refresh_display()

    def _refresh_display(self):
        """Rebuild the event list display progressively to avoid UI blocking."""
        # Increment generation to invalidate any pending batch timers from previous refresh
        self._batch_generation += 1
        current_generation = self._batch_generation
        self._rebuilding = True

        # Clear existing widgets
        for widget in self._event_widgets:
            widget.hide()  # Hide immediately to prevent visual duplication
            widget.deleteLater()
        self._event_widgets.clear()

        # Sort events chronologically
        self._sorted_events = sorted(self._events, key=lambda e: to_local_datetime(e.start))

        # Remove the stretch at the end temporarily (if exists)
        if self._content_layout.count() > 0:
            stretch_item = self._content_layout.takeAt(self._content_layout.count() - 1)

        # Re-add stretch immediately (widgets will be inserted before it)
        self._content_layout.addStretch()

        # Start progressive widget creation with current generation
        self._pending_events_index = 0
        self._create_widgets_batch(generation=current_generation)

    def _create_widgets_batch(self, generation: int = None, batch_size: int = 20):
        """Create widgets in batches to avoid blocking UI.

        Args:
            generation: The batch generation number. If it doesn't match
                       self._batch_generation, this batch is stale and exits.
            batch_size: Number of widgets to create per batch.
        """
        # Check if this batch was superseded by a newer refresh
        if generation is not None and generation != self._batch_generation:
            return  # Stale batch - a newer _refresh_display() call superseded this

        if self._pending_events_index >= len(self._sorted_events):
            # All done - finalize
            self._finalize_display()
            return

        # Create a batch of widgets
        end_index = min(self._pending_events_index + batch_size, len(self._sorted_events))

        for i in range(self._pending_events_index, end_index):
            event = self._sorted_events[i]
            widget = ListEventWidget(event)
            widget.clicked.connect(self.event_clicked.emit)
            widget.double_clicked.connect(self.event_double_clicked.emit)
            # Insert before the stretch (which is at the end)
            insert_pos = self._content_layout.count() - 1
            self._content_layout.insertWidget(insert_pos, widget)
            self._event_widgets.append(widget)

        self._pending_events_index = end_index

        # Schedule next batch with same generation (yields to event loop between batches)
        current_gen = self._batch_generation
        QTimer.singleShot(0, lambda: self._create_widgets_batch(generation=current_gen))

    def _finalize_display(self):
        """Called after all widgets are created."""
        generation = self._batch_generation

        # Apply pending scroll if set (after events are loaded)
        if self._pending_scroll_datetime:
            target_dt = self._pending_scroll_datetime
            self._pending_scroll_datetime = None  # Clear before applying
            QTimer.singleShot(50, lambda: self.scroll_to_datetime(target_dt))
            # Lift the rebuild guard after the deferred scroll (50+50ms) landed
            QTimer.singleShot(150, lambda: self._clear_rebuilding(generation))
        elif self._anchor_datetime:
            # No explicit target: restore the pre-rebuild position so that
            # syncs / auto-refreshes / style refreshes don't move the view.
            anchor_dt = self._anchor_datetime
            QTimer.singleShot(50, lambda: self.scroll_to_datetime(anchor_dt))
            QTimer.singleShot(150, lambda: self._clear_rebuilding(generation))
        else:
            self._rebuilding = False

        # Emit visible range after layout is complete
        def _emit_visible_range():
            visible_range = self.get_visible_date_range()
            if visible_range[0] and visible_range[1]:
                self.visible_range_changed.emit(visible_range[0], visible_range[1])
        QTimer.singleShot(100, _emit_visible_range)

    def _clear_rebuilding(self, generation: int):
        """Lift the rebuild guard unless a newer rebuild started meanwhile."""
        if generation == self._batch_generation:
            self._rebuilding = False

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

        # Defer to ensure layout is complete; re-find the target widget by
        # event datetime at execution time (guards against widget deletion
        # and against being called mid-rebuild with a partial widget list).
        def _scroll_to_widget():
            target_naive = target_dt.replace(tzinfo=None) if target_dt.tzinfo else target_dt
            chosen = None
            for widget in self._event_widgets:
                try:
                    event_start = to_local_datetime(widget.event_data.start)
                    event_start_naive = event_start.replace(tzinfo=None) if event_start.tzinfo else event_start
                    if event_start_naive >= target_naive:
                        chosen = widget
                        break
                except RuntimeError:
                    # Widget was deleted, skip it
                    continue
            # Fallback: no event at or after target -> scroll to last event
            if chosen is None and self._event_widgets:
                chosen = self._event_widgets[-1]
            if chosen is not None:
                try:
                    widget_pos = chosen.mapTo(self._content, chosen.rect().topLeft())
                    # Offset by the layout spacing (4px): a larger margin would
                    # leave a sliver of the previous event visible, making it
                    # the "first visible" event and corrupting the anchor.
                    self._scroll.verticalScrollBar().setValue(max(0, widget_pos.y() - 4))
                except RuntimeError:
                    pass
        QTimer.singleShot(50, _scroll_to_widget)

    @staticmethod
    def _add_months(d: date, months: int) -> date:
        """Shift a date by whole months, clamping the day to month length."""
        import calendar
        m = d.month - 1 + months
        y = d.year + m // 12
        m = m % 12 + 1
        day = min(d.day, calendar.monthrange(y, m)[1])
        return date(y, m, day)

    def get_date_range(self) -> tuple[datetime, datetime]:
        """Get the date range for fetching events.

        Asymmetric around the shown entries: -4 months from the first shown
        entry, +8 months from the last shown entry.  Falls back to ±90 days
        around the current date when nothing is shown yet (startup).
        """
        first_visible, last_visible = self.get_visible_date_range()
        first = first_visible or self._anchor_datetime
        last = last_visible or first

        if first is None:
            start = datetime.combine(self._current_date - timedelta(days=90), dt_time.min)
            end = datetime.combine(self._current_date + timedelta(days=90), dt_time.max)
            return start, end

        start = datetime.combine(self._add_months(first.date(), -4), dt_time.min)
        end = datetime.combine(self._add_months(last.date(), 8), dt_time.max)
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
            def _scroll_to_top():
                widget_pos = target_widget.mapTo(self._content, target_widget.rect().topLeft())
                self._scroll.verticalScrollBar().setValue(max(0, widget_pos.y() - 8))
            QTimer.singleShot(10, _scroll_to_top)

    def refresh_styles(self):
        """Refresh styles after config change (rebuild widgets)."""
        self._refresh_display()