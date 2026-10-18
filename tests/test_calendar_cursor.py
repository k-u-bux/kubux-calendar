"""Tests for gui/widgets/calendar_cursor.py — the non-lossy date triple."""

from datetime import date

import pytest

from gui.widgets.calendar_cursor import CalendarCursor


# ----------------------------------------------------------------------
# construction and projection
# ----------------------------------------------------------------------

def test_from_date_round_trips():
    c = CalendarCursor.from_date(date(2026, 1, 31))
    assert (c.year, c.month, c.day) == (2026, 1, 31)
    assert c.resolve() == date(2026, 1, 31)


def test_resolve_clamps_day_to_month_length():
    assert CalendarCursor(2026, 2, 31).resolve() == date(2026, 2, 28)


def test_resolve_clamps_to_leap_day():
    assert CalendarCursor(2028, 2, 31).resolve() == date(2028, 2, 29)


def test_resolve_is_identity_when_day_is_valid():
    assert CalendarCursor(2026, 4, 30).resolve() == date(2026, 4, 30)


def test_invalid_day_is_an_allowed_marker():
    """The whole point: the triple may be invalid for its month."""
    c = CalendarCursor(2026, 2, 31)
    assert c.day == 31              # intent preserved
    assert c.resolve().day == 28    # projection clamps


def test_frozen_is_immutable():
    c = CalendarCursor(2026, 2, 31)
    with pytest.raises(Exception):
        c.day = 1


# ----------------------------------------------------------------------
# invariant
# ----------------------------------------------------------------------

def test_month_out_of_range_rejected():
    with pytest.raises(ValueError):
        CalendarCursor(2026, 13, 1)
    with pytest.raises(ValueError):
        CalendarCursor(2026, 0, 1)


def test_day_out_of_range_rejected():
    with pytest.raises(ValueError):
        CalendarCursor(2026, 1, 32)
    with pytest.raises(ValueError):
        CalendarCursor(2026, 1, 0)


# ----------------------------------------------------------------------
# shift_months — lossless, day intent survives
# ----------------------------------------------------------------------

def test_shift_months_preserves_day_intent():
    """Jan 31 -> next month keeps day 31 as intent; resolve clamps for display."""
    jan31 = CalendarCursor(2026, 1, 31)
    feb = jan31.shift_months(1)
    assert (feb.year, feb.month, feb.day) == (2026, 2, 31)
    assert feb.resolve() == date(2026, 2, 28)
    mar = feb.shift_months(1)
    assert (mar.year, mar.month, mar.day) == (2026, 3, 31)
    assert mar.resolve() == date(2026, 3, 31)


def test_shift_months_round_trip_is_identity():
    """Jan 31 -> NEXT -> PREV returns to Jan 31 exactly, intent included."""
    c = CalendarCursor(2026, 1, 31)
    assert c.shift_months(1).shift_months(-1) == c
    assert c.shift_months(-1).shift_months(1) == c


def test_shift_months_carries_across_year():
    assert CalendarCursor(2026, 12, 15).shift_months(1) == CalendarCursor(2027, 1, 15)
    assert CalendarCursor(2026, 1, 15).shift_months(-1) == CalendarCursor(2025, 12, 15)


def test_shift_months_by_twelve_is_one_year():
    assert CalendarCursor(2026, 5, 31).shift_months(12) == CalendarCursor(2027, 5, 31)


def test_shift_months_never_produces_invalid_month():
    """Month stays 1..12 for every offset in a wide range."""
    for months in range(-30, 31):
        c = CalendarCursor(2026, 1, 31).shift_months(months)
        assert 1 <= c.month <= 12
        assert c.day == 31          # untouched, even when invalid for c.month


# ----------------------------------------------------------------------
# shift_days — resolves first, result always valid
# ----------------------------------------------------------------------

def test_shift_days_steps_from_valid_date():
    assert CalendarCursor(2026, 1, 31).shift_days(1) == CalendarCursor(2026, 2, 1)


def test_shift_days_resolves_invalid_triple_first():
    """No "next day" after Feb 31 — clamp to Feb 28 first, then step to Mar 1."""
    assert CalendarCursor(2026, 2, 31).shift_days(1).resolve() == date(2026, 3, 1)


def test_shift_days_week_step():
    assert CalendarCursor(2026, 1, 31).shift_days(7).resolve() == date(2026, 2, 7)


def test_shift_days_negative_crosses_year():
    assert CalendarCursor(2026, 1, 1).shift_days(-1).resolve() == date(2025, 12, 31)


def test_shift_days_always_yields_valid_date():
    """Every day step from an invalid marker still resolves without raising."""
    c = CalendarCursor(2026, 2, 31)
    for days in range(-40, 41):
        c.shift_days(days).resolve()      # must not raise
