"""
Calendar sidebar widgets.

Extracted from ``main_window.py``: ClickableColorBox, ToggleLabel,
CalendarSidebarItem, CalendarSidebar.
"""

from typing import Callable

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFrame, QLabel, QColorDialog,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics

from backend import EventStore, CalendarSource


class ClickableColorBox(QFrame):
    """A clickable color box that opens a color picker."""

    color_changed = Signal(str)

    def __init__(self, color: str, border_color: str = "#999999", parent=None):
        super().__init__(parent)
        self._color = color
        self._border_color = border_color
        # Size based on font metrics - approximately 1 line height
        fm = QFontMetrics(self.font())
        size = 0.8 * max(fm.height(), 16)  # Minimum size for usability
        self.setFixedSize(int(size), int(size))
        self.setCursor(Qt.PointingHandCursor)
        self._update_style()

    def _update_style(self):
        self.setStyleSheet(f"background-color: {self._color}; border-radius: 3px; border: 1px solid {self._border_color};")

    def set_color(self, color: str):
        self._color = color
        self._update_style()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            # Open color picker
            initial_color = QColor(self._color)
            color = QColorDialog.getColor(initial_color, self, "Choose Calendar Color")
            if color.isValid():
                new_color = color.name()
                self._color = new_color
                self._update_style()
                self.color_changed.emit(new_color)
        super().mousePressEvent(event)


class ToggleLabel(QLabel):
    """Replacement for QCheckBox — a clickable label that toggles state."""

    toggled = Signal(bool)

    def __init__(self, text, checked=True):
        super().__init__(text)
        self._checked = checked
        self._update_style()

    def setChecked(self, checked):
        self._checked = checked
        self._update_style()

    def mousePressEvent(self, event):
        self.setChecked(not self._checked)
        self.toggled.emit(self._checked)

    def _update_style(self):
        color = "black" if self._checked else "rgba(0,0,0,0.4)"
        self.setStyleSheet(f"color: {color};")


class CalendarSidebarItem(QFrame):
    """A single calendar item in the sidebar with visibility toggle."""

    def __init__(
        self,
        calendar: CalendarSource,
        on_toggle: Callable[[str, bool], None],
        on_color_change: Callable[[str, str], None],
        subscription_icon: str = "📡",
        orphaned_icon: str = "⚠",
        orphaned_tooltip: str = "Calendar deleted on server",
        parent=None
    ):
        super().__init__(parent)
        self.calendar = calendar
        self.on_toggle = on_toggle
        self.on_color_change = on_color_change
        self.subscription_icon = subscription_icon
        self.orphaned_icon = orphaned_icon
        self.orphaned_tooltip = orphaned_tooltip
        self._setup_ui()

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(8)

        # Color indicator (clickable)
        self._color_box = ClickableColorBox(self.calendar.color)
        self._color_box.color_changed.connect(self._on_color_picked)
        layout.addWidget(self._color_box)

        # Visibility indicator (clickable)
        self._visibility_toggle = ToggleLabel(self.calendar.name)
        self._visibility_toggle.setChecked(self.calendar.visible)
        self._visibility_toggle.toggled.connect(self._on_visibility_toggle_changed)
        layout.addWidget(self._visibility_toggle, 1)

        # Source type indicator
        if self.calendar.source_type == "ics":
            type_label = QLabel(self.subscription_icon)
            type_label.setToolTip("ICS Subscription (read-only)")
            layout.addWidget(type_label)

        # Orphan indicator (calendar deleted on server)
        if self.calendar.is_orphaned:
            orphan_label = QLabel(self.orphaned_icon)
            orphan_label.setToolTip(self.orphaned_tooltip)
            layout.addWidget(orphan_label)

    def _on_visibility_toggle_changed(self, checked: bool):
        self.on_toggle(self.calendar.id, checked)

    def _on_color_picked(self, color: str):
        self.on_color_change(self.calendar.id, color)

    def set_visible(self, visible: bool):
        self._visibility_toggle.setChecked(visible)

    def set_color(self, color: str):
        self._color_box.set_color(color)


class CalendarSidebar(QWidget):
    """Sidebar showing all calendar sources with visibility toggles."""

    def __init__(self, event_store: EventStore, interface_font: QFont = None, parent=None):
        super().__init__(parent)
        self.event_store = event_store
        self._interface_font = interface_font
        self._items: dict[str, CalendarSidebarItem] = {}
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        # Calendar list
        self._list_layout = QVBoxLayout()
        self._list_layout.setSpacing(2)
        layout.addLayout(self._list_layout)

        layout.addStretch()

    def refresh(self):
        """Refresh the calendar list (incremental update to avoid flicker)."""
        current_calendars = {c.id: c for c in self.event_store.get_calendars()}
        current_ids = set(current_calendars.keys())
        existing_ids = set(self._items.keys())

        # Remove items for calendars that no longer exist
        for calendar_id in existing_ids - current_ids:
            item = self._items.pop(calendar_id)
            self._list_layout.removeWidget(item)
            item.deleteLater()

        # Update existing items and add new ones
        for calendar_id, calendar in current_calendars.items():
            if calendar_id in self._items:
                # Update existing item (visibility, color may have changed)
                item = self._items[calendar_id]
                item.calendar = calendar
                item.set_visible(calendar.visible)
                item.set_color(calendar.color)
            else:
                # Add new item
                item = CalendarSidebarItem(
                    calendar,
                    self._on_calendar_toggle,
                    self._on_calendar_color_change,
                    subscription_icon=self.event_store.config.labels.subscription_icon,
                    orphaned_icon=self.event_store.config.labels.orphaned_icon,
                    orphaned_tooltip=self.event_store.config.labels.orphaned_tooltip,
                )
                self._list_layout.addWidget(item)
                self._items[calendar_id] = item

    def _on_calendar_toggle(self, calendar_id: str, visible: bool):
        self.event_store.set_calendar_visibility(calendar_id, visible)

    def _on_calendar_color_change(self, calendar_id: str, color: str):
        self.event_store.set_calendar_color(calendar_id, color)

    def update_tooltips(self):
        """Update tooltips for all calendar items with last sync times."""
        last_sync_label = self.event_store.config.labels.last_sync_label

        for calendar_id, item in self._items.items():
            last_sync = self.event_store.get_source_last_sync(calendar_id)
            if last_sync:
                time_str = last_sync.strftime("%H:%M")
                item.setToolTip(f"{last_sync_label} {time_str}")
            else:
                item.setToolTip("")