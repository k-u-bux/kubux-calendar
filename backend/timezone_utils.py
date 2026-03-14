"""
Timezone utilities for Kubux Calendar.

All event times are stored with their original timezone (or UTC).
These functions convert to/from the configured local timezone for display.
"""

from datetime import datetime, timedelta
import time as _time
import pytz


_local_timezone_name: str = "Europe/Amsterdam"


def set_timezone(timezone_name: str):
    """Set the local timezone (called at startup from Config)."""
    global _local_timezone_name
    _local_timezone_name = timezone_name


def get_local_timezone():
    """Get the local timezone as a pytz timezone object."""
    try:
        return pytz.timezone(_local_timezone_name)
    except pytz.UnknownTimeZoneError:
        try:
            return pytz.timezone(_time.tzname[0])
        except Exception:
            is_dst = _time.localtime().tm_isdst
            offset_seconds = -_time.altzone if is_dst else -_time.timezone
            return pytz.FixedOffset(offset_seconds // 60)


def to_local_datetime(dt: datetime) -> datetime:
    """Convert a timezone-aware datetime to the local timezone."""
    if dt.tzinfo is not None:
        return dt.astimezone(get_local_timezone())
    return dt


def to_utc_datetime(dt: datetime) -> datetime:
    """Convert a datetime to UTC."""
    if dt.tzinfo is None:
        return get_local_timezone().localize(dt).astimezone(pytz.UTC)
    return dt.astimezone(pytz.UTC)


def utc_to_local_naive(dt: datetime) -> datetime:
    """Convert UTC datetime to naive local datetime (for QDateTimeEdit)."""
    if dt.tzinfo is not None:
        return to_local_datetime(dt).replace(tzinfo=None)
    return dt


def local_naive_to_utc(dt: datetime) -> datetime:
    """Convert naive local datetime to UTC (from QDateTimeEdit)."""
    if dt.tzinfo is None:
        return get_local_timezone().localize(dt).astimezone(pytz.UTC)
    return dt.astimezone(pytz.UTC)


def to_local_hour(dt: datetime) -> float:
    """Convert datetime to local hour as float (e.g. 14.5 = 14:30)."""
    local_dt = to_local_datetime(dt)
    return local_dt.hour + local_dt.minute / 60.0
