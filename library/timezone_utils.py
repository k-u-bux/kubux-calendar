"""
Timezone utilities for Kubux Calendar.

Provides unified timezone conversion functions for the entire application.
All event times are stored in UTC and converted to local time for display.
"""

from datetime import datetime, timedelta, date
import time as _time
import pytz
from typing import Optional, Union
from library.log import debug_log, Level


def _system_timezone_name() -> str:
    """Detect system timezone via pytz, or 'UTC' as last resort."""
    result = "UTC"
    try:
        result = str(pytz.timezone(_time.tzname[0]))
    except Exception:
        debug_log( Level.WARN, "timezone info not found, using UTC")
    debug_log( Level.INFO, f"local timezone: {result}")
    return result


# Default timezone — detected from system, overridable by config
_local_timezone_name: str = _system_timezone_name()


def set_timezone(timezone_name: str):
    """Set the local timezone for the application."""
    global _local_timezone_name
    _local_timezone_name = timezone_name


def get_local_timezone():
    """
    Get the local timezone as a pytz timezone object.

    First tries the configured/detected timezone name.
    Falls back to fresh system detection if that fails.
    
    Returns:
        pytz timezone object for the local timezone.
    """
    try:
        return pytz.timezone(_local_timezone_name)
    except pytz.UnknownTimeZoneError:
        return pytz.timezone(_system_timezone_name())


# ==================== Core helpers ====================


def ensure_tz(
    dt: datetime,
    tz_or_id: Optional[Union[str, pytz.BaseTzInfo]] = None,
    default: Optional[pytz.BaseTzInfo] = None,
) -> datetime:
    """
    Return *dt* as a timezone-aware datetime.

    - If *dt* is a :class:`date` (not a :class:`datetime`), combine with midnight.
    - If *dt* is already aware, return as-is.
    - If *tz_or_id* is a string, interpret as an IANA timezone name.
    - If *tz_or_id* is a ``pytz.BaseTzInfo``, use directly.
    - If *tz_or_id* is *None*, use *default* (which itself defaults to UTC).
    """
    if isinstance(dt, date) and not isinstance(dt, datetime):
        dt = datetime.combine(dt, datetime.min.time())
    if dt.tzinfo is not None:
        return dt
    if isinstance(tz_or_id, str):
        try:
            return pytz.timezone(tz_or_id).localize(dt)
        except Exception:
            pass
    tz = tz_or_id if isinstance(tz_or_id, pytz.BaseTzInfo) else (default or pytz.UTC)
    return tz.localize(dt)


def to_utc(dt: datetime) -> datetime:
    """
    Normalize *dt* to a UTC-aware datetime.

    - Naive datetimes are assumed to be in local time.
    - Aware datetimes are converted to UTC.
    """
    if dt.tzinfo is None:
        # Assume it's in local timezone
        local_tz = get_local_timezone()
        local_dt = local_tz.localize(dt)
        return local_dt.astimezone(pytz.UTC)
    return dt.astimezone(pytz.UTC)


# ==================== Local-time conversions ====================


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
