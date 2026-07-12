"""Tests for pure logic extracted from gui/ modules."""

from datetime import datetime, date, timedelta, time as dt_time
import pytest
import pytz
from unittest.mock import MagicMock

from PySide6.QtWidgets import QApplication

from gui.widgets.event_widget import (
    get_contrasting_text_color,
    lighten_color,
)
from gui.widgets.event_portion import EventPortion
from gui.widgets.config_state import HOUR_HEIGHT
from gui.widgets.day_column import DayColumnWidget
from gui.widgets.time_axis import LinearTimeAxis

UTC = pytz.UTC


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


# ----------------------------------------------------------------------
# get_contrasting_text_color
# ----------------------------------------------------------------------

def test_contrasting_text_color_black_on_white():
    assert get_contrasting_text_color("#ffffff") == "#000000"


def test_contrasting_text_color_white_on_black():
    assert get_contrasting_text_color("#000000") == "#ffffff"


def test_contrasting_text_color_dark_bg():
    assert get_contrasting_text_color("#1a1a2e") == "#ffffff"


def test_contrasting_text_color_light_bg():
    assert get_contrasting_text_color("#f0f0f0") == "#000000"


def test_contrasting_text_color_short_hex():
    assert get_contrasting_text_color("#fff") == "#000000"


def test_contrasting_text_color_invalid():
    assert get_contrasting_text_color("not-a-color") == "#000000"


# ----------------------------------------------------------------------
# lighten_color
# ----------------------------------------------------------------------

def test_lighten_color_positive():
    result = lighten_color("#000000", 0.5)
    assert result.startswith("#")
    r = int(result[1:3], 16)
    g = int(result[3:5], 16)
    b = int(result[5:7], 16)
    assert r > 0 and g > 0 and b > 0


def test_lighten_color_negative_darkens_non_white():
    result = lighten_color("#808080", -0.5)
    r = int(result[1:3], 16)
    assert r < 0x80


def test_lighten_color_short_hex():
    result = lighten_color("#abc", 0.3)
    assert len(result) == 7


def test_lighten_color_clips():
    result = lighten_color("#ffffff", 1.0)
    assert result == "#ffffff"


# ----------------------------------------------------------------------
# EventPortion.create_for_day
# ----------------------------------------------------------------------

CET = pytz.timezone("Europe/Amsterdam")

def _make_event_mock(start: datetime, end: datetime, all_day=False, read_only=False):
    ev = MagicMock()
    ev.start = start
    ev.end = end
    ev.all_day = all_day
    ev.read_only = read_only
    ev.uid = "test-uid"
    return ev


def test_portion_single_day_event():
    start = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
    end = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    ev = _make_event_mock(start, end)

    portion = EventPortion.create_for_day(ev, date(2026, 1, 1))
    assert portion is not None
    assert portion.visible_start_hour == 11.0   # 10 UTC → 11 CET
    assert portion.visible_end_hour == 13.0     # 12 UTC → 13 CET


def test_portion_multi_day_event_first_day():
    start = datetime(2026, 1, 1, 22, 0, 0, tzinfo=UTC)
    end = datetime(2026, 1, 2, 4, 0, 0, tzinfo=UTC)
    ev = _make_event_mock(start, end)

    portion = EventPortion.create_for_day(ev, date(2026, 1, 1))
    assert portion is not None
    assert portion.visible_start_hour == 23.0   # 22 UTC → 23 CET
    assert portion.visible_end_hour == 24.0


def test_portion_multi_day_event_last_day():
    start = datetime(2026, 1, 1, 22, 0, 0, tzinfo=UTC)
    end = datetime(2026, 1, 2, 4, 0, 0, tzinfo=UTC)
    ev = _make_event_mock(start, end)

    portion = EventPortion.create_for_day(ev, date(2026, 1, 2))
    assert portion is not None
    assert portion.visible_start_hour == 0.0
    assert portion.visible_end_hour == 5.0     # 4 UTC → 5 CET


def test_portion_multi_day_event_middle_day():
    start = datetime(2026, 1, 1, 22, 0, 0, tzinfo=UTC)
    end = datetime(2026, 1, 3, 6, 0, 0, tzinfo=UTC)
    ev = _make_event_mock(start, end)

    portion = EventPortion.create_for_day(ev, date(2026, 1, 2))
    assert portion is not None
    assert portion.visible_start_hour == 0.0
    assert portion.visible_end_hour == 24.0


def test_portion_not_on_day():
    start = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
    end = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    ev = _make_event_mock(start, end)

    portion = EventPortion.create_for_day(ev, date(2026, 1, 2))
    assert portion is None


def test_portion_sub_hour_minutes():
    start = datetime(2026, 1, 1, 10, 30, 0, tzinfo=UTC)
    end = datetime(2026, 1, 1, 11, 45, 0, tzinfo=UTC)
    ev = _make_event_mock(start, end)

    portion = EventPortion.create_for_day(ev, date(2026, 1, 1))
    assert portion is not None
    assert portion.visible_start_hour == 11.5    # 10:30 UTC → 11:30 CET
    assert portion.visible_end_hour == 12.75     # 11:45 UTC → 12:45 CET


# ----------------------------------------------------------------------
# portions_overlap
# ----------------------------------------------------------------------

def _make_portion(start_h: float, end_h: float, uid: str = "uid") -> EventPortion:
    ev = _make_event_mock(
        datetime(2026, 1, 1, tzinfo=UTC),
        datetime(2026, 1, 1, tzinfo=UTC),
    )
    return EventPortion(ev, date(2026, 1, 1), start_h, end_h)


def _make_col(for_date: date = None) -> DayColumnWidget:
    """Create a DayColumnWidget with a LinearTimeAxis mapper and default viewport."""
    if for_date is None:
        for_date = date(2026, 1, 1)
    mapper = LinearTimeAxis(HOUR_HEIGHT)
    col = DayColumnWidget(for_date, mapper)
    # Set a default viewport: 800px tall, ratio 0 (top)
    col.set_viewport(800, 0.0)
    return col


def test_portions_overlap_true(qapp):
    p1 = _make_portion(10.0, 12.0)
    p2 = _make_portion(11.0, 13.0)
    col = _make_col()
    assert col._portions_overlap(p1, p2) is True


def test_portions_overlap_false_adjacent(qapp):
    p1 = _make_portion(10.0, 12.0)
    p2 = _make_portion(12.0, 14.0)
    col = _make_col()
    assert col._portions_overlap(p1, p2) is False


def test_portions_overlap_false_separate(qapp):
    p1 = _make_portion(10.0, 12.0)
    p2 = _make_portion(14.0, 16.0)
    col = _make_col()
    assert col._portions_overlap(p1, p2) is False


def test_portions_overlap_contained(qapp):
    p1 = _make_portion(10.0, 16.0)
    p2 = _make_portion(12.0, 14.0)
    col = _make_col()
    assert col._portions_overlap(p1, p2) is True


def test_portions_overlap_zero_duration_enforced_min(qapp):
    p1 = _make_portion(10.0, 10.0)
    p2 = _make_portion(10.1, 12.0)
    col = _make_col()
    assert col._portions_overlap(p1, p2) is True


# ----------------------------------------------------------------------
# _y_to_time
# ----------------------------------------------------------------------

def test_y_to_time_exact_hour(qapp):
    col = _make_col()
    t = col._y_to_time(10 * HOUR_HEIGHT)
    assert t.hour == 10
    assert t.minute == 0


def test_y_to_time_half_hour(qapp):
    col = _make_col()
    t = col._y_to_time(int(10.5 * HOUR_HEIGHT))
    assert t.hour == 10
    assert t.minute == 30


def test_y_to_time_clamped_top(qapp):
    col = _make_col()
    t = col._y_to_time(-100)
    assert t.hour == 0
    assert t.minute == 0


def test_y_to_time_clamped_bottom(qapp):
    col = _make_col()
    t = col._y_to_time(25 * HOUR_HEIGHT)
    assert t.hour == 23
    assert t.minute == 59


# ----------------------------------------------------------------------
# calculate_new_event_times
# ----------------------------------------------------------------------

def test_calculate_new_event_times_move():
    start = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
    end = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    ev = _make_event_mock(start, end)

    portion = EventPortion(ev, date(2026, 1, 1), 10.0, 12.0)
    new_start, new_end = portion.calculate_new_event_times(14.0, 16.0)

    assert new_start.hour == 15
    assert new_end.hour == 17
    assert (new_end - new_start) == timedelta(hours=2)


def test_calculate_new_event_times_move_multi_day_first():
    start = datetime(2026, 1, 1, 22, 0, 0, tzinfo=UTC)
    end = datetime(2026, 1, 2, 4, 0, 0, tzinfo=UTC)
    ev = _make_event_mock(start, end)

    portion = EventPortion(ev, date(2026, 1, 1), 22.0, 24.0)
    new_start, new_end = portion.calculate_new_event_times(20.0, 22.0)

    assert new_start.hour == 21
    assert new_end.hour == 3  # next day
    assert new_end.day == 2