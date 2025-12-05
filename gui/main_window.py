"""
Main Window for Kubux Calendar.

The primary application window with calendar view, sidebar, and navigation.
"""

import json
from datetime import datetime, date
from typing import Optional
from pathlib import Path
import pytz

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QToolBar, QPushButton, QLabel, QComboBox,
    QDockWidget, QScrollArea, QCheckBox, QFrame,
    QSplitter, QStatusBar, QMessageBox, QApplication,
    QColorDialog, QSizePolicy
)
from PySide6.QtCore import Qt, QTimer, QSettings, Signal
from PySide6.QtGui import QAction, QIcon, QCloseEvent, QFont

from backend.config import Config
from backend.event_store import EventStore, CalendarSource, Event
from backend.caldav_client import EventData

from .widgets.calendar_widget import CalendarWidget, ViewType
from .event_dialog import EventDialog


class ClickableColorBox(QFrame):
    """A clickable color box that opens a color picker."""
    
    color_changed = Signal(str)
    
    def __init__(self, color: str, parent=None):
        super().__init__(parent)
        self._color = color
        self.setFixedSize(16, 16)
        self.setCursor(Qt.PointingHandCursor)
        self._update_style()
    
    def _update_style(self):
        self.setStyleSheet(f"background-color: {self._color}; border-radius: 3px; border: 1px solid #999;")
    
    def set_color(self, color: str):
        self._color = color
        self._update_style()
    
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            # Open color picker
            from PySide6.QtGui import QColor
            initial_color = QColor(self._color)
            color = QColorDialog.getColor(initial_color, self, "Choose Calendar Color")
            if color.isValid():
                new_color = color.name()
                self._color = new_color
                self._update_style()
                self.color_changed.emit(new_color)
        super().mousePressEvent(event)


class CalendarSidebarItem(QFrame):
    """A single calendar item in the sidebar with visibility toggle."""
    
    def __init__(
        self,
        calendar: CalendarSource,
        on_toggle: callable,
        on_color_change: callable,
        parent=None
    ):
        super().__init__(parent)
        self.calendar = calendar
        self.on_toggle = on_toggle
        self.on_color_change = on_color_change
        self._setup_ui()
    
    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(8)
        
        # Color indicator (clickable)
        self._color_box = ClickableColorBox(self.calendar.color)
        self._color_box.color_changed.connect(self._on_color_picked)
        layout.addWidget(self._color_box)
        
        # Checkbox
        self._checkbox = QCheckBox(self.calendar.name)
        self._checkbox.setChecked(self.calendar.visible)
        self._checkbox.toggled.connect(self._on_checkbox_changed)
        layout.addWidget(self._checkbox, 1)
        
        # Source type indicator
        if self.calendar.source_type == "ics":
            type_label = QLabel("📡")
            type_label.setToolTip("ICS Subscription (read-only)")
            layout.addWidget(type_label)
    
    def _on_checkbox_changed(self, checked: bool):
        # toggled signal passes the new checked state directly
        self.on_toggle(self.calendar.id, checked)
    
    def _on_color_picked(self, color: str):
        self.on_color_change(self.calendar.id, color)
    
    def set_visible(self, visible: bool):
        self._checkbox.setChecked(visible)
    
    def set_color(self, color: str):
        self._color_box.set_color(color)


class CalendarSidebar(QWidget):
    """Sidebar showing all calendar sources with visibility toggles."""
    
    def __init__(self, event_store: EventStore, parent=None):
        super().__init__(parent)
        self.event_store = event_store
        self._items: dict[str, CalendarSidebarItem] = {}
        self._setup_ui()
    
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)
        
        # Header
        header = QLabel("Calendars")
        font = header.font()
        font.setBold(True)
        header.setFont(font)
        layout.addWidget(header)
        
        # Separator
        sep = QFrame()
        sep.setFrameStyle(QFrame.HLine | QFrame.Sunken)
        layout.addWidget(sep)
        
        # Calendar list
        self._list_layout = QVBoxLayout()
        self._list_layout.setSpacing(2)
        layout.addLayout(self._list_layout)
        
        layout.addStretch()
    
    def refresh(self):
        """Refresh the calendar list."""
        # Clear existing items
        for item in self._items.values():
            item.deleteLater()
        self._items.clear()
        
        # Add calendars
        for calendar in self.event_store.get_calendars():
            item = CalendarSidebarItem(
                calendar,
                self._on_calendar_toggle,
                self._on_calendar_color_change
            )
            self._list_layout.addWidget(item)
            self._items[calendar.id] = item
    
    def _on_calendar_toggle(self, calendar_id: str, visible: bool):
        self.event_store.set_calendar_visibility(calendar_id, visible)
    
    def _on_calendar_color_change(self, calendar_id: str, color: str):
        self.event_store.set_calendar_color(calendar_id, color)


class MainWindow(QMainWindow):
    """
    Main application window.
    
    Contains:
    - Toolbar with navigation and view switching
    - Sidebar with calendar visibility toggles
    - Main calendar view (day/week/month)
    """
    
    def __init__(self, config: Config, parent=None):
        super().__init__(parent)
        self.config = config
        
        # Initialize event store
        self.event_store = EventStore(config)
        self.event_store.set_on_change_callback(self._on_data_changed)
        
        # Track open event dialogs
        self._event_dialogs: list[EventDialog] = []
        
        # Settings for persistence
        self._settings = QSettings("kubux", "kubux-calendar")
        
        # Auto-refresh timer
        self._auto_refresh_timer = QTimer(self)
        self._auto_refresh_timer.timeout.connect(self._on_auto_refresh)
        
        self._setup_window()
        self._setup_ui()
        self._setup_toolbar()
        self._setup_statusbar()
        
        # Load state and initialize
        self._load_state()
        self._initialize_data()
        
        # Start auto-refresh timer if interval > 0
        if config.refresh_interval > 0:
            self._auto_refresh_timer.start(config.refresh_interval * 1000)  # Convert to milliseconds
            print(f"DEBUG: Auto-refresh enabled every {config.refresh_interval} seconds", file=__import__('sys').stderr)
    
    def _setup_window(self):
        """Configure main window properties."""
        self.setWindowTitle("Kubux Calendar")
        self.setMinimumSize(800, 600)
        
        # Restore window geometry
        geometry = self._settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)
        else:
            self.resize(1200, 800)
    
    def _setup_ui(self):
        """Set up the main UI layout."""
        # Central widget with splitter
        splitter = QSplitter(Qt.Horizontal)
        
        # Sidebar
        sidebar_scroll = QScrollArea()
        sidebar_scroll.setWidgetResizable(True)
        sidebar_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        sidebar_scroll.setMinimumWidth(180)
        sidebar_scroll.setMaximumWidth(300)
        
        self._sidebar = CalendarSidebar(self.event_store)
        sidebar_scroll.setWidget(self._sidebar)
        splitter.addWidget(sidebar_scroll)
        
        # Main calendar view
        main_widget = QWidget()
        main_layout = QVBoxLayout(main_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        
        self._calendar_widget = CalendarWidget()
        self._calendar_widget.slot_double_clicked.connect(self._on_slot_double_clicked)
        self._calendar_widget.event_clicked.connect(self._on_event_clicked)
        self._calendar_widget.event_double_clicked.connect(self._on_event_double_clicked)
        self._calendar_widget.view_changed.connect(self._on_view_changed)
        self._calendar_widget.date_changed.connect(self._on_date_changed)
        
        main_layout.addWidget(self._calendar_widget)
        splitter.addWidget(main_widget)
        
        # Set splitter sizes
        splitter.setSizes([200, 1000])
        
        self.setCentralWidget(splitter)
    
    def _setup_toolbar(self):
        """Set up the navigation toolbar."""
        toolbar = QToolBar("Navigation")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        
        # === LEFT BLOCK: Navigation ===
        self._prev_btn = QPushButton("◀")
        self._prev_btn.setToolTip("Previous")
        self._prev_btn.clicked.connect(self._calendar_widget.go_previous)
        toolbar.addWidget(self._prev_btn)
        
        self._today_btn = QPushButton("Today")
        self._today_btn.clicked.connect(self._calendar_widget.go_today)
        toolbar.addWidget(self._today_btn)
        
        self._next_btn = QPushButton("▶")
        self._next_btn.setToolTip("Next")
        self._next_btn.clicked.connect(self._calendar_widget.go_next)
        toolbar.addWidget(self._next_btn)
        
        toolbar.addSeparator()
        
        # Date label
        self._date_label = QLabel()
        font = self._date_label.font()
        font.setBold(True)
        self._date_label.setFont(font)
        self._date_label.setMinimumWidth(200)
        toolbar.addWidget(self._date_label)
        
        toolbar.addSeparator()
        
        # View switcher
        self._view_combo = QComboBox()
        self._view_combo.addItem("Day", ViewType.DAY)
        self._view_combo.addItem("Week", ViewType.WEEK)
        self._view_combo.addItem("Month", ViewType.MONTH)
        self._view_combo.setCurrentIndex(1)  # Default to week view
        self._view_combo.currentIndexChanged.connect(self._on_view_combo_changed)
        toolbar.addWidget(self._view_combo)
        
        # === LEFT SPACER: Push New Event to center ===
        left_spacer = QWidget()
        left_spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        toolbar.addWidget(left_spacer)
        
        # === CENTER: New Event button ===
        self._new_event_btn = QPushButton("+ New Event")
        self._new_event_btn.setStyleSheet("background: #007bff; color: white; padding: 6px 12px;")
        self._new_event_btn.clicked.connect(self._on_new_event)
        toolbar.addWidget(self._new_event_btn)
        
        # === RIGHT SPACER: Push actions to right ===
        right_spacer = QWidget()
        right_spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        toolbar.addWidget(right_spacer)
        
        # === RIGHT BLOCK: Actions ===
        self._reload_btn = QPushButton("Reload")
        self._reload_btn.setToolTip("Reload events from all calendars")
        self._reload_btn.clicked.connect(self._refresh_events)
        toolbar.addWidget(self._reload_btn)
        
        self._edit_config_btn = QPushButton("Edit Config")
        self._edit_config_btn.setToolTip("Open configuration file")
        self._edit_config_btn.clicked.connect(self._on_edit_config)
        toolbar.addWidget(self._edit_config_btn)
        
        self._quit_btn = QPushButton("Quit")
        self._quit_btn.setToolTip("Exit application")
        self._quit_btn.clicked.connect(self.close)
        toolbar.addWidget(self._quit_btn)
        
        self._update_date_label()
    
    def _setup_statusbar(self):
        """Set up the status bar."""
        self._statusbar = QStatusBar()
        self.setStatusBar(self._statusbar)
        self._statusbar.showMessage("Ready")
    
    def _load_state(self):
        """Load persisted application state."""
        # View type
        view_str = self._settings.value("view_type", "week")
        view_map = {"day": ViewType.DAY, "week": ViewType.WEEK, "month": ViewType.MONTH}
        view_type = view_map.get(view_str, ViewType.WEEK)
        
        # Current date
        date_str = self._settings.value("current_date")
        if date_str:
            try:
                current_date = date.fromisoformat(date_str)
            except:
                current_date = date.today()
        else:
            current_date = date.today()
        
        # Apply state
        self._calendar_widget.set_date(current_date)
        self._calendar_widget.set_view(view_type)
        
        # Update view combo
        view_index = {ViewType.DAY: 0, ViewType.WEEK: 1, ViewType.MONTH: 2}
        self._view_combo.setCurrentIndex(view_index.get(view_type, 1))
        
        # Restore scroll position (defer to after layout)
        scroll_pos = self._settings.value("scroll_position", 0, type=int)
        QTimer.singleShot(100, lambda: self._calendar_widget.set_scroll_position(scroll_pos))
    
    def _save_state(self):
        """Save application state."""
        # Window geometry
        self._settings.setValue("geometry", self.saveGeometry())
        
        # View type
        view_type = self._calendar_widget.get_current_view()
        self._settings.setValue("view_type", view_type.value)
        
        # Current date
        current_date = self._calendar_widget.get_current_date()
        self._settings.setValue("current_date", current_date.isoformat())
        
        # Scroll position
        scroll_pos = self._calendar_widget.get_scroll_position()
        self._settings.setValue("scroll_position", scroll_pos)
    
    def _initialize_data(self):
        """Initialize calendar data."""
        self._statusbar.showMessage("Connecting to calendar servers...")
        QApplication.processEvents()
        
        if self.event_store.initialize():
            self._sidebar.refresh()
            self._refresh_events()
            self._statusbar.showMessage("Connected", 3000)
        else:
            self._statusbar.showMessage("Failed to connect to some calendars")
            QMessageBox.warning(
                self,
                "Connection Warning",
                "Could not connect to all calendar sources. Some calendars may be unavailable."
            )
    
    def _refresh_events(self):
        """Refresh events for the current view."""
        self._statusbar.showMessage("Loading events...")
        QApplication.processEvents()
        
        start, end = self._calendar_widget.get_date_range()
        events = self.event_store.get_events(start, end)
        self._calendar_widget.set_events(events)
        
        self._statusbar.showMessage(f"Loaded {len(events)} events", 3000)
    
    def _update_date_label(self):
        """Update the date label in the toolbar."""
        current_date = self._calendar_widget.get_current_date()
        view_type = self._calendar_widget.get_current_view()
        
        if view_type == ViewType.DAY:
            text = current_date.strftime("%A, %B %d, %Y")
        elif view_type == ViewType.WEEK:
            week_start = current_date - timedelta(days=current_date.weekday())
            week_end = week_start + timedelta(days=6)
            if week_start.month == week_end.month:
                text = f"{week_start.strftime('%B %d')} - {week_end.strftime('%d, %Y')}"
            else:
                text = f"{week_start.strftime('%b %d')} - {week_end.strftime('%b %d, %Y')}"
        else:
            text = current_date.strftime("%B %Y")
        
        self._date_label.setText(text)
    
    def _on_view_combo_changed(self, index: int):
        """Handle view combo box change."""
        view_type = self._view_combo.currentData()
        if view_type:
            self._calendar_widget.set_view(view_type)
    
    def _on_view_changed(self, view_type: ViewType):
        """Handle view change from calendar widget."""
        self._update_date_label()
        self._refresh_events()
    
    def _on_date_changed(self, d: date):
        """Handle date change."""
        self._update_date_label()
        self._refresh_events()
    
    def _on_data_changed(self):
        """Handle data change from event store."""
        self._refresh_events()
    
    def _on_auto_refresh(self):
        """Handle auto-refresh timer tick."""
        self._refresh_events()
    
    def _on_slot_double_clicked(self, dt: datetime):
        """Handle double-click on empty time slot to create event."""
        self._open_event_dialog(initial_datetime=dt)
    
    def _on_event_clicked(self, event: EventData):
        """Handle single click on event - open for editing."""
        self._open_event_dialog(event=event)
    
    def _on_event_double_clicked(self, event: EventData):
        """Handle double-click on event to edit."""
        self._open_event_dialog(event=event)
    
    def _on_new_event(self):
        """Handle new event button click."""
        # Default to current time
        now = datetime.now()
        # Round to next hour
        if now.minute > 0:
            now = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        self._open_event_dialog(initial_datetime=now)
    
    def _open_event_dialog(
        self,
        event: Optional[EventData] = None,
        initial_datetime: Optional[datetime] = None
    ):
        """Open an event dialog window."""
        dialog = EventDialog(
            event_store=self.event_store,
            event_data=event,
            initial_datetime=initial_datetime
        )
        
        dialog.event_saved.connect(self._on_event_saved)
        dialog.event_deleted.connect(self._on_event_deleted)
        dialog.closed.connect(lambda: self._event_dialogs.remove(dialog) if dialog in self._event_dialogs else None)
        
        self._event_dialogs.append(dialog)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
    
    def _on_event_saved(self, event: EventData):
        """Handle event saved."""
        self._refresh_events()
        self._statusbar.showMessage(f"Event '{event.summary}' saved", 3000)
    
    def _on_event_deleted(self, event: EventData):
        """Handle event deleted."""
        self._refresh_events()
        self._statusbar.showMessage(f"Event '{event.summary}' deleted", 3000)
    
    def _on_edit_config(self):
        """Open the configuration file with the system default application."""
        import subprocess
        config_path = Config.get_default_config_path()
        
        if not config_path.exists():
            QMessageBox.warning(
                self,
                "Config Not Found",
                f"Configuration file not found at:\n{config_path}\n\nPlease create the file first."
            )
            return
        
        try:
            subprocess.Popen(["xdg-open", str(config_path)])
            self._statusbar.showMessage(f"Opened config: {config_path}", 3000)
        except Exception as e:
            QMessageBox.critical(
                self,
                "Error",
                f"Could not open config file:\n{e}"
            )
    
    def closeEvent(self, event: QCloseEvent):
        """Handle window close."""
        # Close all event dialogs
        for dialog in self._event_dialogs[:]:
            dialog.close()
        
        # Save state
        self._save_state()
        
        super().closeEvent(event)


# Import timedelta for date calculations
from datetime import timedelta
