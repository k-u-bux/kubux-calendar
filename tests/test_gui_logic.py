"""Tests for pure logic extracted from gui/ modules."""

from datetime import datetime, date, timedelta, time as dt_time
import pytest
import pytz
from unittest.mock import MagicMock

from gui.widgets.event_widget import (
    get_contrasting_text_color,
    lighten_color,
)
from gui.widgets.event_portion import EventPortion
from gui.widgets.config_state import HOUR_HEIGHT
from gui.widgets.day_column import DayColumnWidget
from gui.widgets.time_axis import LinearTimeAxis, QuadraticCompressionAxis, MixedTimeAxis, VariableTimeAxis

UTC = pytz.UTC


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

def _make_event_mock(start: datetime, end: datetime, all_day=False, read_only=False, uid="test-uid"):
    ev = MagicMock()
    ev.start = start
    ev.end = end
    ev.all_day = all_day
    ev.read_only = read_only
    ev.uid = uid
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
        uid=uid,
    )
    return EventPortion(ev, date(2026, 1, 1), start_h, end_h)


def _make_col(for_date: date = None) -> DayColumnWidget:
    """Create a DayColumnWidget with a MixedTimeAxis mapper and default viewport."""
    if for_date is None:
        for_date = date(2026, 1, 1)
    mapper = MixedTimeAxis(HOUR_HEIGHT)
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
    for hour in [10.0, 12.0, 13.0]:
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
    for scroll in [0.0, 0.25, 0.5, 0.75, 1.0]:
        k, m, a1, b1, a2, b2, c2, d, r_y = v._coeffs(vh, scroll)
        if r_y <= 0:
            continue  # no q1 region
        # At r_y (between q1 and linear): derivative from left = derivative from right
        h_right_a = v.y_to_hour(r_y, vh, scroll)
        h_right_b = v.y_to_hour(r_y + 0.001, vh, scroll)
        d_right = (h_right_b - h_right_a) / 0.001
        h_left_a = v.y_to_hour(r_y - 0.001, vh, scroll)
        h_left_b = v.y_to_hour(r_y, vh, scroll)
        d_left = (h_left_b - h_left_a) / 0.001
        assert abs(d_left - d_right) < 0.2, f"C¹ violation at r_y={r_y:.4f} (scroll={scroll}): {d_left:.4f} vs {d_right:.4f}"

        t2 = r_y + d
        if t2 >= 1.0:
            continue  # zero-width q2 region, skip
        # At r_y+δ (between linear and q2)
        h_right_a = v.y_to_hour(t2, vh, scroll)
        h_right_b = v.y_to_hour(t2 + 0.001, vh, scroll)
        d_right = (h_right_b - h_right_a) / 0.001
        h_left_a = v.y_to_hour(t2 - 0.001, vh, scroll)
        h_left_b = v.y_to_hour(t2, vh, scroll)
        d_left = (h_left_b - h_left_a) / 0.001
        assert abs(d_left - d_right) < 0.2, f"C¹ violation at r_y+δ={t2:.4f} (scroll={scroll}): {d_left:.4f} vs {d_right:.4f}"


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
    # scroll_ratio=0.5 → window centered on hour 12, r_y ≈ 0.35, d ≈ 0.3
    # Linear region covers [r_y, r_y+d] ≈ [0.35, 0.65]
    k, m, a1, b1, a2, b2, c2, d, r_y = v._coeffs(vh, 0.5)
    # Test t values strictly inside the linear region
    ts = [r_y + 0.05, r_y + 0.15, (r_y + r_y + d) / 2]
    for t in ts:
        h1 = v.y_to_hour(t - 0.01, vh, 0.5)
        h2 = v.y_to_hour(t + 0.01, vh, 0.5)
        slope = (h2 - h1) / 0.02
        expected = vh / 60  # ≈ 13.33
        assert abs(slope - expected) < 0.5, f"slope at t={t:.4f}: {slope:.2f}, expected {expected:.2f}"


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
    # Use internal r_y from _coeffs to get the actual Y boundaries
    k, m, a1, b1, a2, b2, c2, d, r_y = v._coeffs(vh, 0.5)
    # At r_y (between q1 and linear)
    h = v.y_to_hour(r_y, vh, 0.5)
    t_back = v.hour_to_y(h, vh, 0.5)
    assert abs(t_back - r_y) < 1e-4
    # At r_y+d (between linear and q2)
    h = v.y_to_hour(r_y + d, vh, 0.5)
    t_back = v.hour_to_y(h, vh, 0.5)
    assert abs(t_back - (r_y + d)) < 1e-4


# ----------------------------------------------------------------------
# MixedTimeAxis tests
# ----------------------------------------------------------------------

def test_mixed_linear_regime():
    """When viewport is small enough, MixedTimeAxis delegates to LinearTimeAxis."""
    v = MixedTimeAxis(60, undistorted_hours=4.0)
    # ratio = H*hh/vh = 4*60/200 = 1.2 >= 0.95 → linear
    vh = 200
    for h in [0.0, 6.0, 12.0, 18.0]:
        y = v.hour_to_y(h, vh, 0.5)
        expected = LinearTimeAxis(60).hour_to_y(h, vh, 0.5)
        assert y == pytest.approx(expected, abs=1e-12)


def test_mixed_quadratic_regime():
    """When viewport is large, MixedTimeAxis delegates to QuadraticCompressionAxis."""
    v = MixedTimeAxis(60, undistorted_hours=4.0)
    # ratio = H*hh/vh = 4*60/800 = 0.3 < 0.95 → quadratic
    vh = 800
    for h in [0.0, 6.0, 12.0, 18.0, 24.0]:
        y = v.hour_to_y(h, vh, 0.5)
        expected = QuadraticCompressionAxis(60, 4.0).hour_to_y(h, vh, 0.5)
        assert y == pytest.approx(expected, abs=1e-12)


def test_mixed_monotonic():
    """hour_to_y must be strictly increasing in both regimes."""
    v = MixedTimeAxis(60)
    for vh in [200, 800]:
        prev = -999.0
        for h in range(0, 241):
            y = v.hour_to_y(h / 10.0, vh, 0.5)
            assert y > prev, f"non-monotonic at vh={vh}, h={h/10.0}: {prev:.6f} → {y:.6f}"
            prev = y


def test_mixed_range():
    """hour_to_y maps 0→0 and 24→1 in quadratic regime (the common case)."""
    v = MixedTimeAxis(60)
    for ratio in [0.0, 0.3, 0.5, 0.7, 1.0]:
        assert v.hour_to_y(0.0, 800, ratio) == pytest.approx(0.0, abs=1e-12)
        assert v.hour_to_y(24.0, 800, ratio) == pytest.approx(1.0, abs=1e-12)


def test_mixed_roundtrip():
    """Forward + inverse must be accurate within 1e-4 hours in both regimes."""
    v = MixedTimeAxis(60)
    for vh in [200, 800]:
        for h in [0.0, 0.1, 2.5, 6.0, 11.9, 12.0, 12.1, 18.0, 22.7, 24.0]:
            y = v.hour_to_y(h, vh, 0.5)
            h2 = v.y_to_hour(y, vh, 0.5)
            assert abs(h - h2) < 1e-4, f"roundtrip failed at vh={vh}: {h} → y={y} → {h2}"


def test_mixed_scrollbar_height_linear():
    """In linear regime, scrollbar_height matches LinearTimeAxis."""
    v = MixedTimeAxis(60)
    vh = 200  # linear regime
    assert v.scrollbar_height(vh) == LinearTimeAxis(60).scrollbar_height(vh)


def test_mixed_scrollbar_height_quadratic():
    """In quadratic regime, scrollbar_height matches QuadraticCompressionAxis."""
    v = MixedTimeAxis(60)
    vh = 800  # quadratic regime
    assert v.scrollbar_height(vh) == QuadraticCompressionAxis(60).scrollbar_height(vh)


def test_mixed_delegation_boundary():
    """At the boundary ratio ≈ 0.95, both delegates give similar results."""
    v = MixedTimeAxis(60, undistorted_hours=4.0)
    # boundary: H*hh/vh = 0.95 → vh = 4*60/0.95 ≈ 252.6
    vh = 253
    y_mixed = v.hour_to_y(12.0, vh, 0.5)
    y_quad = QuadraticCompressionAxis(60, 4.0).hour_to_y(12.0, vh, 0.5)
    y_lin = LinearTimeAxis(60).hour_to_y(12.0, vh, 0.5)
    # Mixed should pick one; both are close at this boundary
    assert abs(y_mixed - y_quad) < 0.05 or abs(y_mixed - y_lin) < 0.05


# ----------------------------------------------------------------------
# LinearTimeAxis tests
# ----------------------------------------------------------------------

def test_linear_monotonic():
    """hour_to_y must be strictly increasing."""
    v = LinearTimeAxis(60)
    prev = -1.0
    for h in range(0, 241):
        y = v.hour_to_y(h / 10.0, 800, 0.5)
        assert y > prev, f"non-monotonic at h={h/10.0}: {prev:.6f} → {y:.6f}"
        prev = y


def test_linear_range():
    """hour_to_y with scroll_ratio=0 maps 0→0 and 24→(24*60/800)."""
    v = LinearTimeAxis(60)
    # At scroll_ratio=0, offset=0, so hour_to_y(0) = 0, hour_to_y(24) = 24*60/800
    for ratio in [0.0]:
        assert v.hour_to_y(0.0, 800, ratio) == pytest.approx(0.0, abs=1e-12)
        assert v.hour_to_y(24.0, 800, ratio) == pytest.approx(1440.0 / 800.0, abs=1e-12)


def test_linear_roundtrip():
    """Forward + inverse must be accurate within 1e-4 hours."""
    v = LinearTimeAxis(60)
    for h in [0.0, 0.1, 2.5, 6.0, 11.9, 12.0, 12.1, 18.0, 22.7, 24.0]:
        y = v.hour_to_y(h, 800, 0.5)
        h2 = v.y_to_hour(y, 800, 0.5)
        assert abs(h - h2) < 1e-4, f"roundtrip failed: {h} → y={y} → {h2}"


def test_linear_scroll_changes_offset():
    """LinearTimeAxis shifts with scroll_ratio."""
    v = LinearTimeAxis(60)
    # At scroll_ratio=0, hour 6 → 6*60/800 = 0.45
    # At scroll_ratio=1, hour 6 → (6*60 - 640)/800 = -0.35
    y0 = v.hour_to_y(6.0, 800, 0.0)
    y1 = v.hour_to_y(6.0, 800, 1.0)
    assert y0 != pytest.approx(y1, abs=1e-6)


def test_linear_y_to_hour_roundtrip():
    """y_to_hour round-trips with hour_to_y."""
    v = LinearTimeAxis(60)
    for h in [0.0, 6.0, 12.0, 24.0]:
        y = v.hour_to_y(h, 800, 0.5)
        h2 = v.y_to_hour(y, 800, 0.5)
        assert abs(h - h2) < 1e-4, f"roundtrip failed: {h} → y={y} → {h2}"


def test_linear_scrollbar_height():
    """scrollbar_height returns expected value."""
    v = LinearTimeAxis(60)
    h = v.scrollbar_height(800)
    # scrollbar_height = max(1, int(1000 * viewport / total))
    # = max(1, int(1000 * 800 / 1440)) = max(1, 555) = 555
    assert h == int(1000 * 800 / (24 * 60))


# ----------------------------------------------------------------------
# EventPortion property tests
# ----------------------------------------------------------------------

def test_portion_properties():
    """Test EventPortion basic properties."""
    start = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
    end = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    ev = _make_event_mock(start, end)
    portion = EventPortion(ev, date(2026, 1, 1), 10.0, 12.0)
    assert portion.event.uid == "test-uid"
    assert portion.display_date == date(2026, 1, 1)
    assert portion.visible_start_hour == 10.0
    assert portion.visible_end_hour == 12.0


# ----------------------------------------------------------------------
# VariableTimeAxis tests
# ----------------------------------------------------------------------

def test_variable_monotonic():
    """hour_to_y must be strictly increasing."""
    v = VariableTimeAxis(60)
    prev = -1.0
    for h in range(0, 241):
        y = v.hour_to_y(h / 10.0, 800, 0.5)
        assert y > prev, f"non-monotonic at h={h/10.0}: {prev:.6f} → {y:.6f}"
        prev = y


def test_variable_range():
    """hour_to_y maps 0→0 and 24→1."""
    v = VariableTimeAxis(60)
    for ratio in [0.0, 0.3, 0.5, 0.7, 1.0]:
        assert v.hour_to_y(0.0, 800, ratio) == pytest.approx(0.0, abs=1e-9)
        assert v.hour_to_y(24.0, 800, ratio) == pytest.approx(1.0, abs=1e-9)


def test_variable_roundtrip():
    """Forward + inverse must be accurate within 1e-3 hours."""
    v = VariableTimeAxis(60)
    for h in [0.0, 0.5, 2.5, 6.0, 11.9, 12.0, 12.1, 18.0, 22.7, 24.0]:
        y = v.hour_to_y(h, 800, 0.5)
        h2 = v.y_to_hour(y, 800, 0.5)
        assert abs(h - h2) < 1e-3, f"roundtrip failed: {h} → y={y} → {h2}"


def test_variable_scroll_moves_focus():
    """Scroll changes where hours map to."""
    v = VariableTimeAxis(60)
    y_at_6_r0 = v.hour_to_y(6.0, 800, 0.0)
    y_at_6_r1 = v.hour_to_y(6.0, 800, 1.0)
    assert abs(y_at_6_r0 - y_at_6_r1) > 0.01


def test_variable_scroll_ratio_for_hour_clamps():
    v = VariableTimeAxis(60)
    assert v.scroll_ratio_for_hour(0.0, 800) == 0.0
    assert v.scroll_ratio_for_hour(24.0, 800) == 1.0
    assert 0.0 < v.scroll_ratio_for_hour(12.0, 800) < 1.0


def test_variable_stretch_magnifies_focus():
    """A 1-hour span at the focus covers more Y than one at the edge."""
    v = VariableTimeAxis(60, stretch=3.0, lens_width=6.0, margin=2.0)
    # focus at scroll=0.5 is hour 12
    y_focus_span = v.hour_to_y(12.5, 800, 0.5) - v.hour_to_y(11.5, 800, 0.5)
    y_edge_span = v.hour_to_y(1.5, 800, 0.5) - v.hour_to_y(0.5, 800, 0.5)
    assert y_focus_span > y_edge_span


# ----------------------------------------------------------------------
# DayColumnWidget._calculate_layout (column packing)
# ----------------------------------------------------------------------

def test_calculate_layout_disjoint_one_column(qapp):
    col = _make_col()
    col.add_portion(_make_portion(8.0, 9.0, uid="a"))
    col.add_portion(_make_portion(10.0, 11.0, uid="b"))
    col.add_portion(_make_portion(13.0, 14.0, uid="c"))
    col._calculate_layout()  # layout only, skip widget creation
    total_cols = {entry[2] for entry in col._event_layout}
    assert total_cols == {1}


def test_calculate_layout_three_mutual_overlap(qapp):
    col = _make_col()
    col.add_portion(_make_portion(10.0, 12.0, uid="a"))
    col.add_portion(_make_portion(10.5, 12.5, uid="b"))
    col.add_portion(_make_portion(11.0, 13.0, uid="c"))
    col._calculate_layout()
    total_cols = {entry[2] for entry in col._event_layout}
    assert total_cols == {3}


def test_calculate_layout_chain_two_columns(qapp):
    """A∩B and B∩C but A and C adjacent → one group, 2 columns."""
    col = _make_col()
    col.add_portion(_make_portion(8.0, 10.0, uid="a"))
    col.add_portion(_make_portion(9.0, 11.0, uid="b"))
    col.add_portion(_make_portion(10.0, 12.0, uid="c"))
    col._calculate_layout()
    total_cols = {entry[2] for entry in col._event_layout}
    assert total_cols == {2}


def test_calculate_layout_empty(qapp):
    col = _make_col()
    col._calculate_layout()
    assert col._event_layout == []


# ----------------------------------------------------------------------
# EventPortion.calculate_new_event_times — more cases
# ----------------------------------------------------------------------

def test_calculate_new_event_times_negative_delta():
    """Dragging earlier shifts the event back."""
    start = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    end = datetime(2026, 1, 1, 13, 0, 0, tzinfo=UTC)
    ev = _make_event_mock(start, end)
    portion = EventPortion(ev, date(2026, 1, 1), 12.0, 13.0)
    new_start, new_end = portion.calculate_new_event_times(10.0, 11.0)
    assert new_start.hour == 11
    assert new_end.hour == 12


def test_calculate_new_event_times_preserves_duration():
    start = datetime(2026, 1, 1, 9, 0, 0, tzinfo=UTC)
    end = datetime(2026, 1, 1, 9, 45, 0, tzinfo=UTC)
    ev = _make_event_mock(start, end)
    portion = EventPortion(ev, date(2026, 1, 1), 9.0, 9.75)
    new_start, new_end = portion.calculate_new_event_times(14.0, 14.75)
    assert (new_end - new_start) == timedelta(minutes=45)


# ----------------------------------------------------------------------
# lighten_color edge cases
# ----------------------------------------------------------------------

def test_lighten_color_negative_on_white_unchanged():
    """The blend formula only moves toward white, so white is fixed."""
    assert lighten_color("#ffffff", -0.5) == "#ffffff"


def test_lighten_color_full_darken_clips():
    result = lighten_color("#202020", -1.0)
    assert result == "#000000"


def test_lighten_color_invalid_returns_input():
    assert lighten_color("not-a-color", 0.3) == "not-a-color"


# ----------------------------------------------------------------------
# EventWidget._sanitize_text
# ----------------------------------------------------------------------

def _widget_event(summary="Test", all_day=False, location="", read_only=False,
                  is_recurring=False, calendar_color="#ff0000", calendar_name="Cal"):
    ev = MagicMock()
    ev.summary = summary
    ev.all_day = all_day
    ev.location = location
    ev.read_only = read_only
    ev.is_recurring = is_recurring
    ev.calendar_color = calendar_color
    ev.calendar_name = calendar_name
    ev.sync_status = ""
    ev.pending_operation = None
    ev.uid = "test-uid"
    ev.start = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
    ev.end = datetime(2026, 1, 1, 11, 0, 0, tzinfo=UTC)
    src = MagicMock()
    src.last_sync_time = None
    src.is_outdated = False
    src.color = calendar_color
    src.name = calendar_name
    src.read_only = read_only
    ev.source = src
    ev.event = ev  # EventView.event returns self
    return ev


def test_sanitize_text_collapses_whitespace(qapp):
    from gui.widgets.event_widget import EventWidget
    w = EventWidget(_widget_event(summary="T"), compact=True)
    assert w._sanitize_text("Line\nBreak\tTab") == "Line Break Tab"
    assert w._sanitize_text("  multiple   spaces  ") == "multiple spaces"
    assert w._sanitize_text("") == ""
