"""
CalendarWidget: main calendar widget with switchable views.

This is the coordinator that switches between Day, Week, Month, and List views.
All view implementations live in their own modules; this file just wires them
together and provides the public API used by MainWindow.
"""

from datetime import datetime, date, timedelta, time as dt_time
from typing import Optional
from enum import Enum

from PySide6.QtWidgets import QWidget, QVBoxLayout, QStackedWidget
from PySide6.QtCore import Signal

from backend import EventView as EventData
from .config_state import (
    set_layout_config,
    set_localization_config,
    set_colors_config,
    set_labels_config,
    get_localization_config,
    get_hour_height,
)
from .day_view import DayView
from .week_view import WeekView
from .month_view import MonthView
from .list_view import ListView


class ViewType(Enum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    LIST = "list"


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
        self._cached_events: list[EventData] = []
        # Stale flags - views need refresh when switched to
        self._day_view_stale = True
        self._week_view_stale = True
        self._month_view_stale = True
        self._list_view_stale = True
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
            self._day_view.setFocus()
        elif view_type == ViewType.WEEK:
            self._stack.setCurrentWidget(self._week_view)
            self._week_view.set_date(self._current_date)
            self._week_view.setFocus()
        elif view_type == ViewType.MONTH:
            self._stack.setCurrentWidget(self._month_view)
            self._month_view.set_date(self._current_date)
            self._month_view.setFocus()
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
            # Start of week (respects first_day_of_week config)
            localization = get_localization_config()
            week_start = localization.get_week_start(self._current_date)
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
        """Set events - only updates the active view, others are marked stale."""
        self._cached_events = events

        # Only update the currently active view, mark others as stale
        if self._current_view == ViewType.DAY:
            self._day_view.set_events(events)
            self._day_view_stale = False  # Just got fresh events
            self._week_view_stale = True
            self._month_view_stale = True
            self._list_view_stale = True
        elif self._current_view == ViewType.WEEK:
            self._week_view.set_events(events)
            self._week_view_stale = False  # Just got fresh events
            self._day_view_stale = True
            self._month_view_stale = True
            self._list_view_stale = True
        elif self._current_view == ViewType.MONTH:
            self._month_view.set_events(events)
            self._month_view_stale = False  # Just got fresh events
            self._day_view_stale = True
            self._week_view_stale = True
            self._list_view_stale = True
        else:  # LIST
            self._list_view.set_events(events)
            self._list_view_stale = False  # Just got fresh events
            self._day_view_stale = True
            self._week_view_stale = True
            self._month_view_stale = True

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