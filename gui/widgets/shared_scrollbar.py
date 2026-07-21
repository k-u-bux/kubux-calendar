"""
SharedScrollBar: a thin wrapper around QScrollBar that shares state
across multiple instances.

Multiple SharedScrollBar widgets can be created (e.g. one per view in a
QStackedWidget).  They all mirror a single _ScrollBarState — when one
changes the value all others follow.
"""

from PySide6.QtWidgets import QWidget, QScrollBar, QVBoxLayout
from PySide6.QtCore import Qt, Signal


class _ScrollBarState:
    """Shared state object for one or more SharedScrollBar widgets.

    Attributes
    ----------
    value : int
        Current scrollbar position in [min, max].
    minimum : int
    maximum : int
    page_step : int
    single_step : int
    """

    def __init__(self):
        self.value = 0
        self.minimum = 0
        self.maximum = 1000
        self.page_step = 1000
        self.single_step = 1
        self._listeners: list[callable] = []

    def add_listener(self, cb: callable):
        self._listeners.append(cb)

    def _notify(self, attr: str, val: int):
        for cb in self._listeners:
            cb(attr, val)

    def set_value(self, v: int):
        v = max(self.minimum, min(self.maximum, v))
        if v != self.value:
            self.value = v
            self._notify("value", v)

    def set_range(self, lo: int, hi: int):
        self.minimum = lo
        self.maximum = hi
        self._notify("range", lo)
        self._notify("range", hi)

    def set_page_step(self, s: int):
        self.page_step = max(1, s)
        self._notify("page_step", self.page_step)


class SharedScrollBar(QWidget):
    """A QScrollBar that mirrors a shared _ScrollBarState.

    Multiple SharedScrollBar instances can point to the same state;
    changes from any instance propagate to all.
    """

    # Expose the underlying QScrollBar's valueChanged signal
    valueChanged = Signal(int)
    # User-only interactions (drag, trough click, wheel) — never fires
    # for programmatic setValue.
    actionTriggered = Signal(int)

    def __init__(self, state: _ScrollBarState, parent=None):
        super().__init__(parent)
        self._state = state

        self._bar = QScrollBar(Qt.Vertical, self)
        self._bar.setRange(state.minimum, state.maximum)
        self._bar.setPageStep(state.page_step)
        self._bar.setSingleStep(state.single_step)
        self._bar.setValue(state.value)

        # Layout so the QScrollBar fills this widget
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._bar)

        # Propagate local changes → state → all instances
        self._bar.valueChanged.connect(self._on_local_change)
        self._bar.actionTriggered.connect(self.actionTriggered.emit)

        # Propagate state changes → local
        self._state.add_listener(self._on_state_change)

    def _on_local_change(self, v: int):
        self._state.set_value(v)
        self.valueChanged.emit(v)

    def _on_state_change(self, attr: str, val: int):
        self._bar.blockSignals(True)
        if attr == "value":
            self._bar.setValue(val)
            self.valueChanged.emit(val)
        elif attr == "range":
            self._bar.setRange(self._state.minimum, self._state.maximum)
        elif attr == "page_step":
            self._bar.setPageStep(val)
        self._bar.blockSignals(False)

    # ------------------------------------------------------------------
    # Convenience passthroughs so callers can treat this like QScrollBar
    # ------------------------------------------------------------------

    def value(self) -> int:
        return self._state.value

    def setValue(self, v: int):
        self._state.set_value(v)

    def setPageStep(self, s: int):
        self._state.set_page_step(s)

    def pageStep(self) -> int:
        return self._state.page_step

    def minimum(self) -> int:
        return self._state.minimum

    def maximum(self) -> int:
        return self._state.maximum

    def singleStep(self) -> int:
        return self._state.single_step

    def setSingleStep(self, s: int):
        self._state.single_step = s
        self._bar.setSingleStep(s)

    def setRange(self, lo: int, hi: int):
        self._state.set_range(lo, hi)

    def setFocus(self):
        self._bar.setFocus()

    def hasFocus(self) -> bool:
        return self._bar.hasFocus()

    def wheelEvent(self, event):
        self._bar.wheelEvent(event)