"""
TimeAxisMapper: maps between hour [0, 24] and viewport-normalized Y [0, 1].

Abstracting Y-position math away from hard-coded hour_height arithmetic.
The scrollbar range is always [0, 1000]; the mapper translates
the normalised ratio [0.0, 1.0] into whatever offset semantics it needs.
"""

from abc import ABC, abstractmethod


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

    # ------------------------------------------------------------------
    # Focus position
    # ------------------------------------------------------------------

    def _focus_hour(self, scroll_ratio: float) -> float:
        """Map scroll_ratio [0, 1] → focus hour."""
        rng = 24.0 - 2.0 * self._margin
        return self._margin + scroll_ratio * rng

    # ------------------------------------------------------------------
    # Magnification envelope
    # ------------------------------------------------------------------

    @staticmethod
    def _smoothstep(t: float) -> float:
        """Hermite smoothstep: 1 at t=0, 0 at t=1, C²."""
        t = max(0.0, min(1.0, t))
        return 2.0 * t * t * t - 3.0 * t * t + 1.0

    def _magnification(self, hour: float, focus: float) -> float:
        """Derivative of the warped mapping at *hour*."""
        w = self._lens_width
        if w <= 0.0 or self._stretch <= 1.0:
            return 1.0
        d = abs(hour - focus)
        if d >= w:
            return 1.0
        return 1.0 + (self._stretch - 1.0) * self._smoothstep(d / w)

    # ------------------------------------------------------------------
    # Antiderivative helper  (signed)
    # ------------------------------------------------------------------

    @staticmethod
    def _smoothstep_integral(u: float, w: float) -> float:
        """∫₀ᵘ smoothstep(|x|/w) dx   (closed form, signed)."""
        if w <= 0.0:
            return float(u)
        t = min(abs(u) / w, 1.0)
        # ∫₀ᵗ (2x³ - 3x² + 1) dx  =  t⁴/2 - t³ + t
        val = w * t * (0.5 * t * t * t - t * t + 1.0)
        return val if u >= 0.0 else -val

    # ------------------------------------------------------------------
    # Warped distance  ∫ magnification
    # ------------------------------------------------------------------

    def _warped(self, a: float, b: float, focus: float) -> float:
        """∫ₐᵇ magnification(x) dx."""
        if b <= a:
            return 0.0
        w = self._lens_width
        if w <= 0.0 or self._stretch <= 1.0:
            return b - a

        # Linear (flat) part
        flat = b - a

        # Lens-affected interval  [focus−w, focus+w]  intersected with [a, b]
        la = max(a, focus - w)
        lb = min(b, focus + w)
        if lb <= la:
            return flat

        # Signed smoothstep integrals (centred on focus)
        si_a = self._smoothstep_integral(la - focus, w)
        si_b = self._smoothstep_integral(lb - focus, w)
        extra = (self._stretch - 1.0) * (si_b - si_a)
        return flat + extra

    def _total_warped(self, focus: float) -> float:
        """∫₀²⁴ magnification(x) dx  — always positive."""
        return 24.0 + (self._stretch - 1.0) * self._lens_correction(focus)

    def _lens_correction(self, focus: float) -> float:
        """Extra warped area contributed by the lens (non-flat part)."""
        w = self._lens_width
        if w <= 0.0 or self._stretch <= 1.0:
            return 0.0
        a = max(0.0, focus - w)
        b = min(24.0, focus + w)
        if b <= a:
            return 0.0
        return self._smoothstep_integral(b - focus, w) - self._smoothstep_integral(a - focus, w)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

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
        h = y_norm * 24.0  # initial linear guess
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
        """Page-step proportional to visible hours / focus range."""
        vh = max(1, viewport_height)
        focus_range = 24.0 - 2.0 * self._margin
        if focus_range <= 0.0:
            return 1000
        avg_mag = 1.0 + (self._stretch - 1.0) * min(self._lens_width / 12.0, 1.0)
        visible_hours = vh / max(1.0, self._hh * avg_mag)
        return max(1, min(1000, int(1000 * visible_hours / focus_range)))