"""FollowState: shared flag for 'follow present' mode.

When enabled (by clicking [Today]), every update of the current-time
indicator in day/week views also re-centers the view on the current hour.
Any other navigation action or user scroll disables it.
"""

from PySide6.QtCore import QObject, Signal


class FollowState(QObject):
    """Mutable flag shared between CalendarWidget, views, and day columns.

    Emits *changed* whenever the flag is toggled so that MainWindow can
    persist the new value immediately.
    """

    changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._follow_present = False

    @property
    def follow_present(self) -> bool:
        return self._follow_present

    @follow_present.setter
    def follow_present(self, value: bool):
        if self._follow_present != value:
            self._follow_present = value
            self.changed.emit(value)
