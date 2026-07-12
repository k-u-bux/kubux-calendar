"""
TimeAxisMapper: maps between hour [0, 24] and viewport-normalized Y [0, 1].

Abstracting Y-position math away from hard-coded hour_height arithmetic.
"""

from abc import ABC, abstractmethod


class TimeAxisMapper(ABC):
    """Abstract base for time⇔viewport coordinate mapping.

    viewport_height and scroll_offset are passed as method parameters
    because they change dynamically (resize, scroll).  The mapper itself
    holds only configuration (e.g. hour_height).
    """

    @abstractmethod
    def hour_to_y(self, hour: float, viewport_height: int, scroll_offset: int) -> float:
        """Convert hour [0.0, 24.0] → viewport-normalized Y.

        Returns a value in [0, 1] when the hour is visible.
        Values < 0 or > 1 mean the hour is off-screen.
        """
        ...

    @abstractmethod
    def y_to_hour(self, y_norm: float, viewport_height: int, scroll_offset: int) -> float:
        """Convert viewport-normalized Y [0.0, 1.0] → hour [0.0, 24.0]."""
        ...

    @abstractmethod
    def scrollbar_maximum(self, viewport_height: int) -> int:
        """Return the scrollbar maximum value for the given viewport height."""
        ...


class LinearTimeAxis(TimeAxisMapper):
    """Linear mapping: y = (hour * hour_height - scroll_offset) / viewport_height."""

    def __init__(self, hour_height: int):
        self._hh = hour_height

    def hour_to_y(self, hour: float, viewport_height: int, scroll_offset: int) -> float:
        vh = max(1, viewport_height)
        return (hour * self._hh - scroll_offset) / vh

    def y_to_hour(self, y_norm: float, viewport_height: int, scroll_offset: int) -> float:
        vh = max(1, viewport_height)
        return (y_norm * vh + scroll_offset) / self._hh

    def scrollbar_maximum(self, viewport_height: int) -> int:
        return max(0, 24 * self._hh - viewport_height)