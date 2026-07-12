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