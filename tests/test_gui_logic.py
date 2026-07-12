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
from gui.widgets.time_axis import LinearTimeAxis, QuadraticCompressionAxis

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
    """Create a DayColumnWidget with a QuadraticCompressionAxis mapper and default viewport."""
    if for_date is None:
        for_date = date(2026, 1, 1)
    mapper = QuadraticCompressionAxis(HOUR_HEIGHT)
    col = DayColumnWidget(for_date, mapper)
    col.set_viewport(800, 0.5)  # mid-scroll = focus at 12h
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
# _y_to_time (uses VariableTimeAxis at scroll_ratio=0.5, focus=12h)
# ----------------------------------------------------------------------

def test_y_to_time_exact_hour(qapp):
    col = _make_col()
    # QuadraticCompressionAxis at r=0.5, vh=800: hour 12 → y_norm = ?
    # Find y_norm for hour 12, then convert to pixel, then back.
    y_norm = col._mapper.hour_to_y(12.0, 800, 0.5)
    y_px = int(y_norm * 800)
    t = col._y_to_time(y_px)
    assert t.hour == 12
    assert t.minute == 0


def test_y_to_time_clamped_top(qapp):
    col = _make_col()
    t = col._y_to_time(-100)
    assert t.hour == 0
    assert t.minute == 0


def test_y_to_time_clamped_bottom(qapp):
    col = _make_col()
    t = col._y_to_time(99999)
    assert t.hour == 23
    assert t.minute == 59


def test_y_to_time_roundtrip(qapp):
    """Verify hour→y→hour round-trip for representative hours."""
    col = _make_col()
    for hour in [1.0, 6.0, 12.0, 18.0]:
        y = int(col._hour_to_content_y(hour))
        t = col._y_to_time(y)
        expected = int(hour * 60)
        got = t.hour * 60 + t.minute
        assert abs(expected - got) <= 2, f"hour={hour}: expected ~{expected}min, got {got}min from y={y}"


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


# ----------------------------------------------------------------------
# QuadraticCompressionAxis tests
# ----------------------------------------------------------------------

def test_quad_monotonic():
    """hour_to_y must be strictly increasing."""
    v = QuadraticCompressionAxis(60, undistorted_hours=4.0)
    prev = -1.0
    for h in range(0, 241):
        y = v.hour_to_y(h / 10.0, 800, 0.5)
        assert y > prev, f"non-monotonic at h={h/10.0}: {prev:.6f} → {y:.6f}"
        prev = y


def test_quad_range():
    """hour_to_y maps 0→0 and 24→1."""
    v = QuadraticCompressionAxis(60)
    for ratio in [0.0, 0.3, 0.5, 0.7, 1.0]:
        assert v.hour_to_y(0.0, 800, ratio) == pytest.approx(0.0, abs=1e-12)
        assert v.hour_to_y(24.0, 800, ratio) == pytest.approx(1.0, abs=1e-12)


def test_quad_c1_continuity():
    """Mapping must be C¹ at the region boundaries."""
    v = QuadraticCompressionAxis(60)
    vh = 800
    for r in [0.1, 0.3, 0.5, 0.7]:
        d = 0.2
        # At r (between q1 and linear): derivative from left = derivative from right
        h_right_a = v.y_to_hour(r, vh, r)
        h_right_b = v.y_to_hour(r + 0.001, vh, r)
        d_right = (h_right_b - h_right_a) / 0.001
        h_left_a = v.y_to_hour(r - 0.001, vh, r)
        h_left_b = v.y_to_hour(r, vh, r)
        d_left = (h_left_b - h_left_a) / 0.001
        assert abs(d_left - d_right) < 0.2, f"C¹ violation at r={r}: {d_left:.4f} vs {d_right:.4f}"

        t2 = r + d
        # At r+δ (between linear and q2)
        h_right_a = v.y_to_hour(t2, vh, r)
        h_right_b = v.y_to_hour(t2 + 0.001, vh, r)
        d_right = (h_right_b - h_right_a) / 0.001
        h_left_a = v.y_to_hour(t2 - 0.001, vh, r)
        h_left_b = v.y_to_hour(t2, vh, r)
        d_left = (h_left_b - h_left_a) / 0.001
        assert abs(d_left - d_right) < 0.2, f"C¹ violation at r+δ={t2}: {d_left:.4f} vs {d_right:.4f}"


def test_quad_roundtrip():
    """Forward + inverse must be accurate within 1e-4 hours."""
    v = QuadraticCompressionAxis(60)
    for h in [0.0, 0.1, 2.5, 6.0, 11.9, 12.0, 12.1, 18.0, 22.7, 24.0]:
        y = v.hour_to_y(h, 800, 0.5)
        h2 = v.y_to_hour(y, 800, 0.5)
        assert abs(h - h2) < 1e-4, f"roundtrip failed: {h} → y={y} → {h2}"


def test_quad_slope_in_linear_region():
    """In the linear region, derivative should be vh/hour_height."""
    v = QuadraticCompressionAxis(60)
    vh = 800
    r = 0.5
    d = 0.2
    for t in [0.52, 0.55, 0.6, 0.68]:
        h1 = v.y_to_hour(t - 0.01, vh, r)
        h2 = v.y_to_hour(t + 0.01, vh, r)
        slope = (h2 - h1) / 0.02
        expected = vh / 60  # ≈ 13.33
        assert abs(slope - expected) < 0.5, f"slope at t={t}: {slope:.2f}, expected {expected:.2f}"


def test_quad_scroll_moves_window():
    """Scroll changes where hours map to."""
    v = QuadraticCompressionAxis(60)
    vh = 800
    y_at_6_r0 = v.hour_to_y(6.0, vh, 0.0)
    y_at_6_r1 = v.hour_to_y(6.0, vh, 1.0)
    # At r=1 the linear window has scrolled to the bottom, so hour 6 is
    # compressed into the early region. Values should differ.
    assert abs(y_at_6_r0 - y_at_6_r1) > 0.1


def test_quad_limit_at_r_plus_delta():
    """At boundary r+δ, both quadratics must give the same hour."""
    v = QuadraticCompressionAxis(60)
    vh = 800
    for r in [0.0, 0.3, 0.5, 0.7]:
        t_boundary = min(r + 0.2, 1.0)
        h = v.y_to_hour(t_boundary, vh, r)
        t_back = v.hour_to_y(h, vh, r)
        assert abs(t_back - t_boundary) < 1e-4
