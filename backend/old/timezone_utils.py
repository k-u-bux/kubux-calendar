"""
Timezone utilities for Kubux Calendar.

Provides unified timezone conversion functions for the entire application.
All event times are stored in UTC and converted to local time for display.
"""

from datetime import datetime, timedelta
import time as _time
import pytz


# Default timezone - can be overridden by config
_local_timezone_name: str = "Europe/Amsterdam"


def set_timezone(timezone_name: str):
    """Set the local timezone for the application."""
    global _local_timezone_name
    _local_timezone_name = timezone_name


def get_local_timezone():
    """
    Get the local timezone as a pytz timezone object.
    
    Returns:
        pytz timezone object for the configured local timezone.
    """
    try:
        return pytz.timezone(_local_timezone_name)
    except pytz.UnknownTimeZoneError:
        # Fallback: try system timezone name
        try:
            return pytz.timezone(_time.tzname[0])
        except:
            # Last resort: calculate offset and use fixed offset timezone
            is_dst = _time.localtime().tm_isdst
            if is_dst:
                offset_seconds = -_time.altzone
            else:
                offset_seconds = -_time.timezone
            return pytz.FixedOffset(offset_seconds // 60)


def to_local_datetime(dt: datetime) -> datetime:
    """
    Convert a UTC datetime to local timezone.
    
    Args:
        dt: A datetime object, typically in UTC with tzinfo set.
    
    Returns:
        A timezone-aware datetime in the local timezone.
        If input has no tzinfo, returns it unchanged.
    """
    if dt.tzinfo is not None:
        local_tz = get_local_timezone()
        return dt.astimezone(local_tz)
    return dt


def to_utc_datetime(dt: datetime) -> datetime:
    """
    Convert a local datetime to UTC.
    
    Args:
        dt: A datetime object in local timezone.
    
    Returns:
        A timezone-aware datetime in UTC.
    """
    if dt.tzinfo is None:
        # Assume it's in local timezone
        local_tz = get_local_timezone()
        local_dt = local_tz.localize(dt)
        return local_dt.astimezone(pytz.UTC)
    else:
        return dt.astimezone(pytz.UTC)


def utc_to_local_naive(dt: datetime) -> datetime:
    """
    Convert a UTC datetime to a naive local datetime.
    
    Used for UI components (like QDateTimeEdit) that expect naive datetimes.
    
    Args:
        dt: A datetime object in UTC (with tzinfo).
    
    Returns:
        A naive datetime (tzinfo=None) representing local time.
    """
    if dt.tzinfo is not None:
        local_dt = to_local_datetime(dt)
        return local_dt.replace(tzinfo=None)
    return dt


def local_naive_to_utc(dt: datetime) -> datetime:
    """
    Convert a naive local datetime to UTC.
    
    Used for UI components (like QDateTimeEdit) that provide naive datetimes.
    
    Args:
        dt: A naive datetime representing local time.
    
    Returns:
        A timezone-aware datetime in UTC.
    """
    if dt.tzinfo is None:
        local_tz = get_local_timezone()
        local_dt = local_tz.localize(dt)
        return local_dt.astimezone(pytz.UTC)
    return dt.astimezone(pytz.UTC)


def to_local_hour(dt: datetime) -> float:
    """
    Convert datetime to local timezone and return hour as float.
    
    Args:
        dt: A datetime object.
    
    Returns:
        Hour as float (e.g., 14.5 for 14:30).
    """
    local_dt = to_local_datetime(dt)
    return local_dt.hour + local_dt.minute / 60.0
