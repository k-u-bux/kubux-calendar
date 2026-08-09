"""Shared widget-tree helpers for the GUI widgets."""

from typing import Optional, Type, TypeVar

from PySide6.QtWidgets import QApplication, QWidget

W = TypeVar("W", bound=QWidget)


def find_ancestor_widget(global_pos, widget_type: Type[W]) -> Optional[W]:
    """Return the widget of *widget_type* under *global_pos*, or None.

    Walks up the parent chain from the widget at the given global
    position until it finds an instance of *widget_type*.
    """
    current = QApplication.widgetAt(global_pos)
    while current is not None:
        if isinstance(current, widget_type):
            return current
        current = current.parentWidget()
    return None