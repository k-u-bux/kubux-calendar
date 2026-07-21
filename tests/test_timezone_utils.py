"""Tests for library/timezone_utils.py."""

from datetime import datetime, date
import pytz
from library.timezone_utils import (
    ensure_tz, to_utc, to_local_datetime, utc_to_local_naive, local_naive_to_utc,
    set_timezone, get_local_timezone, _system_timezone_name,
)

UTC = pytz.UTC
BERLIN = pytz.timezone("Europe/Berlin")


def test_ensure_tz_already_aware():
    dt = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
    result = ensure_tz(dt)
    assert result is dt


def test_ensure_tz_naive_with_string():
    dt = datetime(2026, 1, 1, 10, 0, 0)
    result = ensure_tz(dt, "Europe/Berlin")
    assert result.tzinfo.zone == "Europe/Berlin"
    assert result.hour == 10


def test_ensure_tz_naive_with_tz():
    dt = datetime(2026, 1, 1, 10, 0, 0)
    result = ensure_tz(dt, BERLIN)
    assert result.tzinfo.zone == "Europe/Berlin"


def test_ensure_tz_naive_no_tz_uses_default():
    dt = datetime(2026, 1, 1, 10, 0, 0)
    result = ensure_tz(dt)
    assert result.tzinfo is not None


def test_ensure_tz_date_input():
    d = date(2026, 1, 1)
    result = ensure_tz(d, tz_or_id=UTC)
    assert isinstance(result, datetime)
    assert result.hour == 0
    assert result.tzinfo is not None


def test_to_utc_aware():
    dt = BERLIN.localize(datetime(2026, 1, 1, 10, 0, 0))
    result = to_utc(dt)
    assert result.tzinfo == UTC
    # Berlin is UTC+1 in winter
    assert result.hour == 9


def test_to_utc_naive():
    dt = datetime(2026, 1, 1, 10, 0, 0)
    result = to_utc(dt)
    assert result.tzinfo == UTC


def test_to_local_datetime():
    dt = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
    result = to_local_datetime(dt)
    assert result.tzinfo is not None


def test_to_local_datetime_naive():
    dt = datetime(2026, 1, 1, 10, 0, 0)
    result = to_local_datetime(dt)
    assert result == dt  # unchanged


def test_utc_to_local_naive():
    dt = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
    result = utc_to_local_naive(dt)
    assert result.tzinfo is None


def test_local_naive_to_utc():
    dt = datetime(2026, 1, 1, 10, 0, 0)
    result = local_naive_to_utc(dt)
    assert result.tzinfo == UTC


def test_set_timezone():
    old = get_local_timezone()
    set_timezone("Europe/Berlin")
    tz = get_local_timezone()
    assert tz.zone == "Europe/Berlin"
    # Restore
    set_timezone(old.zone)


def test_system_timezone_name():
    name = _system_timezone_name()
    assert isinstance(name, str)
    assert len(name) > 0