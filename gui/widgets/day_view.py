"""
DayView: single-day timeline view.

Extracted from ``calendar_widget.py``.
"""

from datetime import datetime, date, time as dt_time

from PySide6.QtWidgets import QWidget

from backend import EventView as EventData
from .timeline_view import TimelineViewBase
from .day_column import DayColumnWidget


class DayView(TimelineViewBase):
    """Single day view with hourly time slots and all-day events section."""

    def __init__(self, parent=None):
        self._date = date.today()
        super().__init__(parent)

    # ------------------------------------------------------------------
    # Hooks
    # ------------------------------------------------------------------

    def _get_num_days(self) -> int:
        return 1

    def _get_day_dates(self) -> list[date]:
        return [self._date]

    def _create_day_columns(self) -> list[DayColumnWidget]:
        return [DayColumnWidget(self._date)]

    # No header for day view (uses default None from base)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_date(self, d: date):
        self._date = d
        self._day_columns[0].set_date(d)
        self.refresh_events()

    def get_date_range(self) -> tuple[datetime, datetime]:
        start = datetime.combine(self._date, dt_time.min)
        end = datetime.combine(self._date, dt_time.max)
        return start, end