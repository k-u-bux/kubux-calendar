"""
TimeAxisMapper: maps between hour [0, 24] and viewport-normalized Y [0, 1].

Abstracting Y-position math away from hard-coded hour_height arithmetic.
The scrollbar range is always [0, 1000]; the mapper translates
the normalised ratio [0.0, 1.0] into whatever offset semantics it needs.
"""

from abc import ABC, abstractmethod
import math


class TimeAxisMapper(ABC):
    """Abstract base for time⇔viewport coordinate mapping.

    scroll_ratio is a float in [0.0, 1.0] derived from the scrollbar.
    The mapper itself holds only configuration (e.g. hour_height).
    """

    @abstractmethod
    def hour_to_y(self, hour: float, viewport_height: int, scroll_ratio: float) -> float:
        """Convert hour [0.0, 24.0] → viewport-normalized Y.

        Returns a value in [0, 1] when the hour is visible.
        Values < 0 or > 1 mean the hour is off-screen.
        """
        ...

    @abstractmethod
    def y_to_hour(self, y_norm: float, viewport_height: int, scroll_ratio: float) -> float:
        """Convert viewport-normalized Y [0.0, 1.0] → hour [0.0, 24.0]."""
        ...

    @abstractmethod
    def scrollbar_height(self, viewport_height: int) -> int:
        """Page-step for a QScrollBar with fixed range [0, 1000].

        Controls the handle size — larger value = smaller handle
        (proportional to the visible fraction of 24 h).
        """
        ...


class LinearTimeAxis(TimeAxisMapper):
    """Linear mapping: y = (hour * hour_height − offset) / viewport_height.

    scroll_ratio 0 → offset 0 (top of 24 h)
    scroll_ratio 1 → offset = 24·hour_height − viewport_height (bottom visible)
    """

    def __init__(self, hour_height: int):
        self._hh = hour_height

    def _offset(self, viewport_height: int, scroll_ratio: float) -> float:
        total = 24.0 * self._hh
        return scroll_ratio * max(0.0, total - viewport_height)

    def hour_to_y(self, hour: float, viewport_height: int, scroll_ratio: float) -> float:
        vh = max(1, viewport_height)
        return (hour * self._hh - self._offset(vh, scroll_ratio)) / vh

    def y_to_hour(self, y_norm: float, viewport_height: int, scroll_ratio: float) -> float:
        vh = max(1, viewport_height)
        return (y_norm * vh + self._offset(vh, scroll_ratio)) / self._hh

    def scrollbar_height(self, viewport_height: int) -> int:
        total = 24 * self._hh
        if total <= 0:
            return 1000
        return max(1, int(1000 * viewport_height / total))


class VariableTimeAxis(TimeAxisMapper):
    """Frog-eye lens: hours near the focus centre get more space.

    scroll_ratio 0 → focus = margin (e.g. hour 2 — near the top)
    scroll_ratio 1 → focus = 24 − margin (e.g. hour 22 — near the bottom)

    A smoothstep magnification envelope stretches the central hours;
    distant hours are compressed to compensate so the full 24 h always
    maps into the [0, 1] viewport.

    Parameters
    ----------
    hour_height : int
        Base pixels per hour at the uncompressed edges.
    stretch : float
        Peak magnification at the focus (≥ 1.0).
    lens_width : float
        Half-width of the lens in hours (≥ 0).
    margin : float
        Minimum focus distance from midnight / 24 h.
    """

    def __init__(
        self,
        hour_height: int,
        stretch: float = 2.5,
        lens_width: float = 6.0,
        margin: float = 2.0,
    ):
        self._hh = hour_height
        self._stretch = max(1.0, stretch)
        self._lens_width = max(0.0, lens_width)
        self._margin = margin

    def _focus_hour(self, scroll_ratio: float) -> float:
        rng = 24.0 - 2.0 * self._margin
        return self._margin + scroll_ratio * rng

    @staticmethod
    def _smoothstep(t: float) -> float:
        t = max(0.0, min(1.0, t))
        return 2.0 * t * t * t - 3.0 * t * t + 1.0

    def _magnification(self, hour: float, focus: float) -> float:
        w = self._lens_width
        if w <= 0.0 or self._stretch <= 1.0:
            return 1.0
        d = abs(hour - focus)
        if d >= w:
            return 1.0
        return 1.0 + (self._stretch - 1.0) * self._smoothstep(d / w)

    @staticmethod
    def _smoothstep_integral(u: float, w: float) -> float:
        if w <= 0.0:
            return float(u)
        t = min(abs(u) / w, 1.0)
        val = w * t * (0.5 * t * t * t - t * t + 1.0)
        return val if u >= 0.0 else -val

    def _warped(self, a: float, b: float, focus: float) -> float:
        if b <= a:
            return 0.0
        w = self._lens_width
        if w <= 0.0 or self._stretch <= 1.0:
            return b - a
        flat = b - a
        la = max(a, focus - w)
        lb = min(b, focus + w)
        if lb <= la:
            return flat
        si_a = self._smoothstep_integral(la - focus, w)
        si_b = self._smoothstep_integral(lb - focus, w)
        extra = (self._stretch - 1.0) * (si_b - si_a)
        return flat + extra

    def _total_warped(self, focus: float) -> float:
        return 24.0 + (self._stretch - 1.0) * self._lens_correction(focus)

    def _lens_correction(self, focus: float) -> float:
        w = self._lens_width
        if w <= 0.0 or self._stretch <= 1.0:
            return 0.0
        a = max(0.0, focus - w)
        b = min(24.0, focus + w)
        if b <= a:
            return 0.0
        return self._smoothstep_integral(b - focus, w) - self._smoothstep_integral(a - focus, w)

    def hour_to_y(self, hour: float, viewport_height: int, scroll_ratio: float) -> float:
        focus = self._focus_hour(scroll_ratio)
        total = self._total_warped(focus)
        if total <= 0.0:
            return hour / 24.0
        return self._warped(0.0, max(0.0, hour), focus) / total

    def y_to_hour(self, y_norm: float, viewport_height: int, scroll_ratio: float) -> float:
        y_norm = max(0.0, min(1.0, y_norm))
        focus = self._focus_hour(scroll_ratio)
        total = self._total_warped(focus)
        if total <= 0.0:
            return y_norm * 24.0
        target = y_norm * total
        h = y_norm * 24.0
        for _ in range(6):
            cur = self._warped(0.0, h, focus)
            d = self._magnification(h, focus)
            if d <= 0.0:
                break
            delta = (cur - target) / d
            h -= delta
            h = max(0.0, min(24.0, h))
            if abs(delta) < 1e-6:
                break
        return h

    def scrollbar_height(self, viewport_height: int) -> int:
        vh = max(1, viewport_height)
        focus_range = 24.0 - 2.0 * self._margin
        if focus_range <= 0.0:
            return 1000
        avg_mag = 1.0 + (self._stretch - 1.0) * min(self._lens_width / 12.0, 1.0)
        visible_hours = vh / max(1.0, self._hh * avg_mag)
        return max(1, min(1000, int(1000 * visible_hours / focus_range)))


class QuadraticCompressionAxis(TimeAxisMapper):
    """Piecewise quadratic compression with an undistorted linear window.

    The viewport-normalized Y axis [0, 1] is split into three regions:

        [0, r]         — quadratic (compressed)
        [r, r + δ]     — linear  (undistorted)
        [r + δ, 1]     — quadratic (compressed)

    The scrollbar maps linearly to the *start hour* of the undistorted
    window: scroll_ratio 0 → window at hours [0, H], scroll_ratio 1 →
    window at hours [24−H, 24].  The scrollbar handle size is
    proportional to H/24.

    Parameters
    ----------
    hour_height : int
        Pixels per hour in the undistorted window.
    undistorted_hours : float
        Width of the undistorted window in hours (default 4.0).
    """

    def __init__(self, hour_height: int, undistorted_hours: float = 4.0):
        self._hh = hour_height
        self._undistorted_hours = max(0.5, undistorted_hours)

    def _delta(self, vh: int) -> float:
        """δ = Y-span of the linear window for the given viewport height."""
        raw = self._undistorted_hours * self._hh / max(1, vh)
        return min(raw, 1.0)

    def _scroll_to_r(self, scroll_ratio: float, vh: int) -> float:
        """Convert scroll_ratio (hour-linear) → r (Y-position of window start).

        scroll_ratio 0 → start_hour = 0 → r = 0
        scroll_ratio 1 → start_hour = 24 − H → r = 1 − δ
        """
        k = vh / self._hh
        d = self._delta(vh)
        if (1.0 - d) <= 0:
            return 0.0
        # start_hour = k·r + m = r·(k + (24−k)/(1−d))
        factor = k + (24.0 - k) / (1.0 - d)
        max_start = 24.0 - self._undistorted_hours
        start_hour = scroll_ratio * max_start
        r = start_hour / factor if factor > 0 else 0.0
        return max(0.0, min(1.0 - d, r))

    def _coeffs(self, vh: int, scroll_ratio: float) -> tuple:
        """
        Compute all coefficients.

        Returns (k, m, a1, b1, a2, b2, c2, d, r) where:
          d = δ (Y-span of linear region)
          r = Y-position of linear window start (from scroll_ratio)
          k = slope in linear region (hours per unit Y) = vh / hh
          m = intercept of linear region
          q1(t) = a1·t² + b1·t          on [0, r]
          l(t)  = k·t + m                on [r, r+d]
          q2(t) = a2·t² + b2·t + c2     on [r+d, 1]
        """
        vh = max(1, vh)
        k = vh / self._hh
        d = self._delta(vh)
        r = self._scroll_to_r(scroll_ratio, vh)

        m = r * (24.0 - k) / (1.0 - d) if (1.0 - d) > 0 else 0.0

        if r > 0:
            a1 = -m / (r * r)
            b1 = k + 2.0 * m / r
        else:
            a1 = 0.0
            b1 = k

        s = 1.0 - r - d  # width of q2 region
        if s > 0:
            a2 = (24.0 - k - m) / (s * s)
            b2 = k - 2.0 * a2 * (r + d)
            c2 = m + a2 * (r + d) * (r + d)
        else:
            a2 = 0.0
            b2 = k
            c2 = m

        return (k, m, a1, b1, a2, b2, c2, d, r)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def hour_to_y(self, hour: float, viewport_height: int, scroll_ratio: float) -> float:
        """Inverse of y_to_hour — solve h(t) = hour for t ∈ [0, 1]."""
        vh = max(1, viewport_height)
        k, m, a1, b1, a2, b2, c2, d, r = self._coeffs(vh, scroll_ratio)

        hour = max(0.0, min(24.0, hour))

        h_lin_start = k * r + m
        h_lin_end = k * (r + d) + m

        if hour <= h_lin_start:
            if abs(a1) < 1e-12:
                t = hour / b1 if abs(b1) > 1e-12 else 0.0
            else:
                disc = b1 * b1 + 4.0 * a1 * hour
                if disc < 0:
                    t = 0.0
                else:
                    t = (-b1 + math.sqrt(disc)) / (2.0 * a1)
            return max(0.0, min(r, t))

        elif hour >= h_lin_end:
            if abs(a2) < 1e-12:
                t = (hour - c2) / b2 if abs(b2) > 1e-12 else 1.0
            else:
                disc = b2 * b2 - 4.0 * a2 * (c2 - hour)
                if disc < 0:
                    t = 1.0
                else:
                    t = (-b2 + math.sqrt(disc)) / (2.0 * a2)
            return max(r + d, min(1.0, t))

        else:
            t = (hour - m) / k if abs(k) > 1e-12 else r
            return max(r, min(r + d, t))

    def y_to_hour(self, y_norm: float, viewport_height: int, scroll_ratio: float) -> float:
        """Forward mapping: normalized Y → hour."""
        vh = max(1, viewport_height)
        k, m, a1, b1, a2, b2, c2, d, r = self._coeffs(vh, scroll_ratio)

        t = max(0.0, min(1.0, y_norm))

        if t <= r:
            return a1 * t * t + b1 * t
        elif t <= r + d:
            return k * t + m
        else:
            return a2 * t * t + b2 * t + c2

    def scrollbar_height(self, viewport_height: int) -> int:
        ratio = self._undistorted_hours * self._hh / viewport_height
        return max(1, min(1000, int(1000 * ratio)))


class MixedTimeAxis(TimeAxisMapper):

    def __init__(self, hour_height: int, undistorted_hours: float = 4.0):
        self._linear = LinearTimeAxis( hour_height )
        self._quadratic = QuadraticCompressionAxis( hour_height, undistorted_hours )

    def _is_quadratic ( self, viewport_height ):
        if viewport_height <= 0:
            return False
        ratio = self._quadratic._undistorted_hours * self._quadratic._hh / viewport_height
        return ratio < 0.95

    def hour_to_y(self, hour: float, viewport_height: int, scroll_ratio: float) -> float:
        if self._is_quadratic( viewport_height ):
            return self._quadratic.hour_to_y( hour, viewport_height, scroll_ratio )
        else:
            return self._linear.hour_to_y( hour, viewport_height, scroll_ratio )

    def y_to_hour(self, y_norm: float, viewport_height: int, scroll_ratio: float) -> float:
        if self._is_quadratic( viewport_height ):
            return self._quadratic.y_to_hour( y_norm, viewport_height, scroll_ratio )
        else:
            return self._linear.y_to_hour( y_norm, viewport_height, scroll_ratio )

    def scrollbar_height(self, viewport_height: int) -> int:
        if self._is_quadratic( viewport_height ):
            return self._quadratic.scrollbar_height( viewport_height )
        else:
            return self._linear.scrollbar_height( viewport_height )
