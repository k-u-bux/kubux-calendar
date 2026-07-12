"""
EventPortion: a day-specific slice of a (possibly multi-day) event.

Extracted from ``calendar_widget.py`` to keep modules focused.
"""

from dataclasses import dataclass
from datetime import datetime, date, time as dt_time
from typing import Optional

from backend import EventView as EventData
from library.timezone_utils import to_local_datetime


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

    def calculate_new_event_times(
        self, new_visible_start_hour: float, new_visible_end_hour: float
    ) -> tuple[datetime, datetime]:
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


def is_all_day_event(event: 'EventData') -> bool:
    """Check if an event is an all-day event."""
    return event.all_day