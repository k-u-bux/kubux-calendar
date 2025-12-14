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
from PySide6.QtCore import Qt, QTimer, Signal, QFileSystemWatcher
from PySide6.QtGui import QAction, QIcon, QCloseEvent, QFont, QKeySequence, QShortcut

from backend.config import Config
from backend.event_store import EventStore, Event
from backend.event_wrapper import CalEvent, CalendarSource

# Alias for backwards compatibility in this file
EventData = CalEvent

from .widgets.calendar_widget import CalendarWidget, ViewType, set_layout_config, set_localization_config, get_localization_config, set_colors_config, set_labels_config
from .event_dialog import EventDialog


class ClickableColorBox(QFrame):
    """A clickable color box that opens a color picker."""
    
    color_changed = Signal(str)
    
    def __init__(self, color: str, border_color: str = "#999999", parent=None):
        super().__init__(parent)
        self._color = color
        self._border_color = border_color
        # Size based on font metrics - approximately 1 line height
        from PySide6.QtGui import QFontMetrics
        fm = QFontMetrics(self.font())
        size = max(fm.height(), 16)  # Minimum 16px for usability
        self.setFixedSize(size, size)
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
        subscription_icon: str = "📡",
        parent=None
    ):
        super().__init__(parent)
        self.calendar = calendar
        self.on_toggle = on_toggle
        self.on_color_change = on_color_change
        self.subscription_icon = subscription_icon
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
            type_label = QLabel(self.subscription_icon)
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
        
        # Header
        self._header = QLabel(self.event_store.config.labels.sidebar_header)
        if self._interface_font:
            header_font = QFont(self._interface_font)
            header_font.setBold(True)
            self._header.setFont(header_font)
        else:
            font = self._header.font()
            font.setBold(True)
            self._header.setFont(font)
        layout.addWidget(self._header)
        
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
                self._on_calendar_color_change,
                subscription_icon=self.event_store.config.labels.subscription_icon
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
        
        # Set layout config, localization, colors, and labels for calendar widget BEFORE creating UI
        set_layout_config(config.layout)
        set_localization_config(config.localization)
        set_colors_config(config.colors)
        set_labels_config(config.labels)
        
        # Apply text_font as application default (for tooltips, event content, etc.)
        text_font = QFont(config.layout.text_font, config.layout.text_font_size)
        QApplication.instance().setFont(text_font)
        
        # Store interface font for explicit use on UI elements
        self._interface_font = QFont(config.layout.interface_font, config.layout.interface_font_size)
        
        # Initialize event store
        self.event_store = EventStore(config)
        self.event_store.set_on_change_callback(self._on_data_changed)
        
        # Track open event dialogs
        self._event_dialogs: list[EventDialog] = []
        
        # State file for persistence (using JSON, not QSettings)
        self._state_file = config.state_file
        self._ui_state: dict = {}
        
        # Auto-refresh timer
        self._auto_refresh_timer = QTimer(self)
        self._auto_refresh_timer.timeout.connect(self._on_auto_refresh)
        
        # Sync timer for pending changes (uses exponential backoff)
        self._sync_timer = QTimer(self)
        self._sync_timer.timeout.connect(self._on_sync_timer)
        self._current_sync_interval = config.sync.initial_interval  # Start with initial interval
        self._sync_timer.start(self._current_sync_interval * 1000)  # Convert to milliseconds
        
        # Config file watcher
        self._config_watcher = QFileSystemWatcher(self)
        config_path = Config.get_default_config_path()
        if config_path.exists():
            self._config_watcher.addPath(str(config_path))
        self._config_watcher.fileChanged.connect(self._on_config_file_changed)
        
        self._setup_window()
        self._setup_ui()
        self._setup_toolbar()
        self._setup_shortcuts()
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
        self.setWindowTitle(self.config.labels.window_title)
        self.setMinimumSize(800, 600)
        
        # Load UI state from JSON file
        self._load_ui_state()
        
        # Restore window geometry
        geometry = self._ui_state.get("geometry")
        if geometry:
            import base64
            self.restoreGeometry(base64.b64decode(geometry))
        else:
            self.resize(1200, 800)
    
    def _setup_shortcuts(self):
        """Set up keyboard shortcuts from config bindings."""
        # Previous period
        prev_shortcut = QShortcut(QKeySequence(self.config.bindings.prev), self)
        prev_shortcut.activated.connect(self._calendar_widget.go_previous)
        
        # Next period
        next_shortcut = QShortcut(QKeySequence(self.config.bindings.next), self)
        next_shortcut.activated.connect(self._calendar_widget.go_next)
        
        # New event
        if self.config.bindings.new_event:
            new_event_shortcut = QShortcut(QKeySequence(self.config.bindings.new_event), self)
            new_event_shortcut.activated.connect(self._on_new_event)
    
    def _setup_ui(self):
        """Set up the main UI layout."""
        # Central widget with splitter
        self._splitter = QSplitter(Qt.Horizontal)
        
        # Sidebar
        sidebar_scroll = QScrollArea()
        sidebar_scroll.setWidgetResizable(True)
        sidebar_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        sidebar_scroll.setMinimumWidth(50)  # Allow sidebar to be made smaller
        sidebar_scroll.setMaximumWidth(400)  # Allow sidebar to be made wider
        
        self._sidebar = CalendarSidebar(self.event_store, interface_font=self._interface_font)
        self._sidebar.setFont(self._interface_font)
        sidebar_scroll.setWidget(self._sidebar)
        self._splitter.addWidget(sidebar_scroll)
        
        # Main calendar view
        main_widget = QWidget()
        main_layout = QVBoxLayout(main_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        
        self._calendar_widget = CalendarWidget()
        self._calendar_widget.slot_double_clicked.connect(self._on_slot_double_clicked)
        self._calendar_widget.event_clicked.connect(self._on_event_clicked)
        self._calendar_widget.event_double_clicked.connect(self._on_event_double_clicked)
        self._calendar_widget.event_time_changed.connect(self._on_event_time_changed)
        self._calendar_widget.view_changed.connect(self._on_view_changed)
        self._calendar_widget.date_changed.connect(self._on_date_changed)
        self._calendar_widget.visible_range_changed.connect(self._on_list_visible_range_changed)
        
        main_layout.addWidget(self._calendar_widget)
        self._splitter.addWidget(main_widget)
        
        # Set default splitter sizes (will be overridden by saved state if available)
        self._splitter.setSizes([200, 1000])
        
        self.setCentralWidget(self._splitter)
    
    def _setup_toolbar(self):
        """Set up the navigation toolbar."""
        toolbar = QToolBar("Navigation")
        toolbar.setMovable(False)
        toolbar.setContentsMargins( 0, 0, 4, 0 )
        self.addToolBar(toolbar)
        
        # Access the toolbar's internal layout and set margins (left, top, right, bottom)
        if toolbar.layout():
            toolbar.layout().setContentsMargins( 8, 12, 8, 8 )
        
        # === LEFT BLOCK ===
        # Date label (info first)
        self._date_label = QLabel()
        date_font = QFont(self._interface_font)
        date_font.setBold(True)
        self._date_label.setFont(date_font)
        self._date_label.setMinimumWidth(200)
        toolbar.addWidget(self._date_label)
        
        toolbar.addSeparator()
        
        # View switcher
        self._view_combo = QComboBox()
        self._view_combo.setFont(self._interface_font)
        self._view_combo.addItem(self.config.labels.view_day, ViewType.DAY)
        self._view_combo.addItem(self.config.labels.view_week, ViewType.WEEK)
        self._view_combo.addItem(self.config.labels.view_month, ViewType.MONTH)
        self._view_combo.insertSeparator(3)  # Add separator after Month
        self._view_combo.addItem(self.config.labels.view_list, ViewType.LIST)
        self._view_combo.setCurrentIndex(1)  # Default to week view
        self._view_combo.currentIndexChanged.connect(self._on_view_combo_changed)
        toolbar.addWidget(self._view_combo)
        
        toolbar.addSeparator()
        
        # Navigation buttons
        self._prev_btn = QPushButton(self.config.labels.button_prev)
        self._prev_btn.setFont(self._interface_font)
        self._prev_btn.setToolTip("Previous")
        self._prev_btn.clicked.connect(self._calendar_widget.go_previous)
        toolbar.addWidget(self._prev_btn)
        
        self._today_btn = QPushButton(self.config.labels.button_today)
        self._today_btn.setFont(self._interface_font)
        self._today_btn.clicked.connect(self._calendar_widget.go_today)
        toolbar.addWidget(self._today_btn)
        
        self._next_btn = QPushButton(self.config.labels.button_next)
        self._next_btn.setFont(self._interface_font)
        self._next_btn.setToolTip("Next")
        self._next_btn.clicked.connect(self._calendar_widget.go_next)
        toolbar.addWidget(self._next_btn)
        
        # === LEFT SPACER: Push New Event to center ===
        left_spacer = QWidget()
        left_spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        toolbar.addWidget(left_spacer)
        
        # === CENTER: New Event button ===
        self._new_event_btn = QPushButton(self.config.labels.button_new_event)
        self._new_event_btn.setFont(self._interface_font)
        self._new_event_btn.clicked.connect(self._on_new_event)
        toolbar.addWidget(self._new_event_btn)
        
        # === RIGHT SPACER: Push actions to right ===
        right_spacer = QWidget()
        right_spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        toolbar.addWidget(right_spacer)
        
        # === RIGHT BLOCK: Actions ===
        self._reload_btn = QPushButton(self.config.labels.button_reload)
        self._reload_btn.setFont(self._interface_font)
        self._reload_btn.setToolTip("Reload events from all calendars")
        self._reload_btn.clicked.connect(self._on_reload_clicked)
        toolbar.addWidget(self._reload_btn)
        
        self._edit_config_btn = QPushButton(self.config.labels.button_edit_config)
        self._edit_config_btn.setFont(self._interface_font)
        self._edit_config_btn.setToolTip("Open configuration file")
        self._edit_config_btn.clicked.connect(self._on_edit_config)
        toolbar.addWidget(self._edit_config_btn)
        
        self._quit_btn = QPushButton(self.config.labels.button_quit)
        self._quit_btn.setFont(self._interface_font)
        self._quit_btn.setToolTip("Exit application")
        self._quit_btn.clicked.connect(self.close)
        toolbar.addWidget(self._quit_btn)
        
        self._update_date_label()
    
    def _setup_statusbar(self):
        """Set up the status bar."""
        self._statusbar = QStatusBar()
        self._statusbar.setFont(self._interface_font)
        self.setStatusBar(self._statusbar)
        self._statusbar.showMessage("Ready")
    
    def _load_ui_state(self):
        """Load UI state from the JSON state file."""
        if self._state_file.exists():
            try:
                with open(self._state_file, 'r') as f:
                    state = json.load(f)
                    self._ui_state = state.get('ui', {})
            except Exception as e:
                print(f"Error loading UI state: {e}", file=__import__('sys').stderr)
                self._ui_state = {}
        else:
            self._ui_state = {}
    
    def _save_ui_state(self):
        """Save UI state to the JSON state file."""
        try:
            # Load existing state to preserve calendar visibility/colors
            existing_state = {}
            if self._state_file.exists():
                with open(self._state_file, 'r') as f:
                    existing_state = json.load(f)
            
            # Update UI state
            existing_state['ui'] = self._ui_state
            
            # Ensure directory exists
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            
            with open(self._state_file, 'w') as f:
                json.dump(existing_state, f, indent=2)
        except Exception as e:
            print(f"Error saving UI state: {e}", file=__import__('sys').stderr)
    
    def _load_state(self):
        """Load persisted application state from JSON."""
        import sys
        
        # View type
        view_str = self._ui_state.get("view_type", "week")
        view_map = {"day": ViewType.DAY, "week": ViewType.WEEK, "month": ViewType.MONTH, "list": ViewType.LIST}
        view_type = view_map.get(view_str, ViewType.WEEK)
        
        # Current date
        date_str = self._ui_state.get("current_date")
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
        
        # Update view combo (index 4 for List due to separator at index 3)
        view_index = {ViewType.DAY: 0, ViewType.WEEK: 1, ViewType.MONTH: 2, ViewType.LIST: 4}
        self._view_combo.setCurrentIndex(view_index.get(view_type, 1))
        
        # Restore scroll position (defer to after layout and data load)
        scroll_pos = self._ui_state.get("scroll_position", 0)
        list_top_datetime_str = self._ui_state.get("list_top_datetime")
        
        print(f"DEBUG _load_state: view_type={view_type}, scroll_pos={scroll_pos}, list_dt={list_top_datetime_str}", file=sys.stderr)
        
        # Store for deferred scroll restoration
        self._pending_restore_view_type = view_type
        self._pending_restore_scroll_pos = scroll_pos
        self._pending_restore_list_dt_str = list_top_datetime_str
        
        # Restore splitter sizes (sidebar width)
        splitter_sizes = self._ui_state.get("splitter_sizes")
        if splitter_sizes and isinstance(splitter_sizes, list) and len(splitter_sizes) == 2:
            self._splitter.setSizes(splitter_sizes)
    
    def _save_state(self):
        """Save application state to JSON."""
        import base64
        import sys
        
        # Window geometry (encode as base64 string for JSON)
        self._ui_state["geometry"] = base64.b64encode(self.saveGeometry().data()).decode('utf-8')
        
        # View type
        view_type = self._calendar_widget.get_current_view()
        self._ui_state["view_type"] = view_type.value
        
        # Current date
        current_date = self._calendar_widget.get_current_date()
        self._ui_state["current_date"] = current_date.isoformat()
        
        # Scroll position - save separately for list view and day/week views
        if view_type == ViewType.LIST:
            # For list view, save the datetime of the first visible event
            first_visible_dt = self._calendar_widget.get_list_first_visible_datetime()
            if first_visible_dt:
                self._ui_state["list_top_datetime"] = first_visible_dt.isoformat()
                print(f"DEBUG _save_state: list_top_datetime={first_visible_dt.isoformat()}", file=sys.stderr)
        else:
            scroll_pos = self._calendar_widget.get_scroll_position()
            self._ui_state["scroll_position"] = scroll_pos
            print(f"DEBUG _save_state: scroll_position={scroll_pos}", file=sys.stderr)
        
        # Splitter sizes (sidebar width)
        self._ui_state["splitter_sizes"] = self._splitter.sizes()
        
        # Save to file
        self._save_ui_state()
    
    def _initialize_data(self):
        """Initialize calendar data."""
        self._statusbar.showMessage("Connecting to calendar servers...")
        QApplication.processEvents()
        
        if self.event_store.initialize():
            self._sidebar.refresh()
            self._refresh_events()
            
            # Show sync status in status bar (replaces "Connected" message)
            self._update_sync_status()
            
            # Restore scroll position after data is loaded (unless caller handles it)
            if not getattr(self, '_skip_auto_scroll_restore', False):
                QTimer.singleShot(300, self._restore_scroll_position)
        else:
            self._statusbar.showMessage("Failed to connect to some calendars")
            QMessageBox.warning(
                self,
                "Connection Warning",
                "Could not connect to all calendar sources. Some calendars may be unavailable."
            )
    
    def _restore_scroll_position(self):
        """Restore scroll position after data load (deferred from _load_state)."""
        import sys
        view_type = getattr(self, '_pending_restore_view_type', None)
        scroll_pos = getattr(self, '_pending_restore_scroll_pos', 0)
        list_dt_str = getattr(self, '_pending_restore_list_dt_str', None)
        
        print(f"DEBUG _restore_scroll_position: view_type={view_type}, scroll_pos={scroll_pos}, list_dt={list_dt_str}", file=sys.stderr)
        
        if view_type == ViewType.LIST:
            if list_dt_str:
                # Scroll to saved datetime
                try:
                    list_top_dt = datetime.fromisoformat(list_dt_str)
                    self._calendar_widget.scroll_list_to_datetime(list_top_dt)
                except:
                    # Fallback: scroll to upcoming if datetime invalid
                    self._calendar_widget.go_today()
            else:
                # No saved datetime - scroll to upcoming events
                self._calendar_widget.go_today()
        else:
            print(f"DEBUG _restore_scroll_position: calling set_scroll_position({scroll_pos})", file=sys.stderr)
            self._calendar_widget.set_scroll_position(scroll_pos)
    
    def _refresh_events(self):
        """Refresh events for the current view."""
        self._statusbar.showMessage("Loading events...")
        QApplication.processEvents()
        
        start, end = self._calendar_widget.get_date_range()
        events = self.event_store.get_events(start, end)
        self._calendar_widget.set_events(events)
        
        self._statusbar.showMessage(f"Loaded {len(events)} events", 3000)
    
    def _update_date_label(self):
        """Update the date label in the toolbar using yyyy/mm/dd format."""
        current_date = self._calendar_widget.get_current_date()
        view_type = self._calendar_widget.get_current_view()
        
        if view_type == ViewType.DAY:
            # Single date: yyyy/mm/dd
            text = current_date.strftime("%Y/%m/%d")
        elif view_type == ViewType.WEEK:
            week_start = current_date - timedelta(days=current_date.weekday())
            week_end = week_start + timedelta(days=6)
            if week_start.year == week_end.year and week_start.month == week_end.month:
                # Same month: yyyy/mm/dd-dd
                text = f"{week_start.strftime('%Y/%m/%d')}-{week_end.day:02d}"
            else:
                # Different months: yyyy/mm/dd - yyyy/mm/dd
                text = f"{week_start.strftime('%Y/%m/%d')} - {week_end.strftime('%Y/%m/%d')}"
        elif view_type == ViewType.LIST:
            # For list view, show visible range (will be updated dynamically)
            visible_range = self._calendar_widget.get_list_visible_range()
            if visible_range[0] and visible_range[1]:
                start_date = visible_range[0].date()
                end_date = visible_range[1].date()
                if start_date == end_date:
                    text = start_date.strftime("%Y/%m/%d")
                elif start_date.year == end_date.year and start_date.month == end_date.month:
                    text = f"{start_date.strftime('%Y/%m/%d')}-{end_date.day:02d}"
                else:
                    text = f"{start_date.strftime('%Y/%m/%d')} - {end_date.strftime('%Y/%m/%d')}"
            else:
                text = "No events"
        else:  # MONTH
            # Month view: yyyy/mm (first day of month)
            first_of_month = current_date.replace(day=1)
            last_day = (first_of_month.replace(month=first_of_month.month % 12 + 1, day=1) - timedelta(days=1)).day if first_of_month.month < 12 else 31
            text = f"{current_date.strftime('%Y/%m')}/01-{last_day:02d}"
        
        self._date_label.setText(text)
    
    def _on_list_visible_range_changed(self, start: datetime, end: datetime):
        """Handle visible range change in list view - update date label."""
        if self._calendar_widget.get_current_view() == ViewType.LIST:
            start_str = start.strftime("%Y/%m/%d")
            end_str = end.strftime("%Y/%m/%d")
            self._date_label.setText(f"{start_str} - {end_str}")
    
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
        """Handle auto-refresh timer tick - fetch fresh data from server."""
        import sys
        print("DEBUG: Auto-refresh triggered - calling event_store.refresh()", file=sys.stderr)
        self.event_store.refresh()
    
    def _on_sync_timer(self):
        """Handle sync timer tick - attempt to sync pending changes with exponential backoff."""
        import sys
        
        pending_count = self.event_store.get_pending_sync_count()
        if pending_count > 0:
            print(f"DEBUG: Sync timer - {pending_count} pending changes (interval: {self._current_sync_interval}s)", file=sys.stderr)
            success, failed = self.event_store.sync_pending_changes()
            
            if success > 0:
                print(f"DEBUG: Synced {success} changes, {failed} failed", file=sys.stderr)
                # Reset to initial interval on success
                self._current_sync_interval = self.config.sync.initial_interval
                print(f"DEBUG: Reset sync interval to {self._current_sync_interval}s", file=sys.stderr)
            elif failed > 0:
                # Increase interval on failure (exponential backoff)
                new_interval = int(self._current_sync_interval * self.config.sync.backoff_multiplier)
                self._current_sync_interval = min(new_interval, self.config.sync.max_interval)
                print(f"DEBUG: Backoff - next sync interval: {self._current_sync_interval}s", file=sys.stderr)
            
            # Update timer with new interval
            self._sync_timer.setInterval(self._current_sync_interval * 1000)
        
        # Update status bar
        self._update_sync_status()
    
    def _update_sync_status(self):
        """Update status bar with sync status."""
        pending_count = self.event_store.get_pending_sync_count()
        last_sync = self.event_store.get_last_sync_time()
        
        if pending_count > 0:
            self._statusbar.showMessage(f"{pending_count} changes pending synchronization")
        elif last_sync:
            # Show count of cached events (not just visible ones)
            cached_count = self.event_store.get_cached_event_count()
            time_str = last_sync.strftime("%H:%M")
            self._statusbar.showMessage(f"Last sync at {time_str}, {cached_count} events cached")
    
    def _on_reload_clicked(self):
        """Handle reload button click - force refresh from server."""
        import sys
        print("DEBUG: Reload clicked - calling event_store.refresh()", file=sys.stderr)
        self._statusbar.showMessage("Reloading from server...")
        QApplication.processEvents()
        
        # This will reconnect to CalDAV servers and re-fetch ICS subscriptions,
        # then invalidate cache and call _on_data_changed() -> _refresh_events()
        self.event_store.refresh()
    
    def _on_config_file_changed(self, path: str):
        """Handle config file change - reload configuration."""
        import sys
        print(f"DEBUG: Config file changed: {path}", file=sys.stderr)
        
        # Some editors (like vim) delete and recreate the file, which removes it from the watcher
        # Re-add the path if it exists
        config_path = Config.get_default_config_path()
        if config_path.exists() and str(config_path) not in self._config_watcher.files():
            self._config_watcher.addPath(str(config_path))
        
        # Delay reload slightly to ensure file is fully written
        QTimer.singleShot(500, self._load_pending_config)
    
    def _load_pending_config(self):
        """Load new config and apply or defer depending on open dialogs."""
        import sys
        try:
            new_config = Config.load()
            
            # Capture current scroll position NOW before any processing
            current_scroll_pos = self._calendar_widget.get_scroll_position()
            current_view = self._calendar_widget.get_current_view()
            current_date = self._calendar_widget.get_current_date()
            current_list_dt = None
            if current_view == ViewType.LIST:
                current_list_dt = self._calendar_widget.get_list_first_visible_datetime()
            
            print(f"DEBUG _load_pending_config: captured scroll_pos={current_scroll_pos}, view={current_view}", file=sys.stderr)
            
            if self._event_dialogs:
                # Dialogs are open - defer config application
                self._pending_config = new_config
                self._pending_scroll_capture = {
                    'scroll_pos': current_scroll_pos,
                    'view': current_view,
                    'date': current_date,
                    'list_dt': current_list_dt
                }
                self._statusbar.showMessage("Config changed. Will apply when edit dialogs close.", 5000)
                print("DEBUG: Config change deferred until dialogs close", file=sys.stderr)
            else:
                # No dialogs open - apply immediately with captured scroll position
                self._apply_config(new_config, captured_scroll={
                    'scroll_pos': current_scroll_pos,
                    'view': current_view,
                    'date': current_date,
                    'list_dt': current_list_dt
                })
                
        except Exception as e:
            error_msg = f"Failed to load config: {e}"
            print(f"ERROR: {error_msg}", file=sys.stderr)
            self._statusbar.showMessage(error_msg, 5000)
    
    def _on_event_dialog_closed(self, dialog: EventDialog):
        """Handle event dialog close - check if we should apply pending config."""
        if dialog in self._event_dialogs:
            self._event_dialogs.remove(dialog)
        
        # If all dialogs closed and we have pending config, apply it
        if not self._event_dialogs and hasattr(self, '_pending_config') and self._pending_config:
            pending = self._pending_config
            pending_scroll = getattr(self, '_pending_scroll_capture', None)
            self._pending_config = None
            self._pending_scroll_capture = None
            self._apply_config(pending, captured_scroll=pending_scroll)
    
    def _apply_config(self, new_config: Config, captured_scroll: dict = None):
        """Apply new configuration by rebuilding the UI in place."""
        import sys
        print("DEBUG: Applying new config...", file=sys.stderr)
        
        try:
            # Save current UI state before rebuild (primarily for geometry)
            self._save_state()
            
            # Update config reference
            self.config = new_config
            self._state_file = new_config.state_file
            
            # Update global config modules
            set_layout_config(new_config.layout)
            set_localization_config(new_config.localization)
            set_colors_config(new_config.colors)
            set_labels_config(new_config.labels)
            
            # Update fonts
            text_font = QFont(new_config.layout.text_font, new_config.layout.text_font_size)
            QApplication.instance().setFont(text_font)
            self._interface_font = QFont(new_config.layout.interface_font, new_config.layout.interface_font_size)
            
            # Stop auto-refresh timer during rebuild
            self._auto_refresh_timer.stop()
            
            # Clear existing UI (keep window shell)
            self._clear_ui()
            
            # Reinitialize event store
            self.event_store = EventStore(new_config)
            self.event_store.set_on_change_callback(self._on_data_changed)
            
            # Rebuild UI with new config
            self._setup_ui()
            self._setup_toolbar()
            self._setup_shortcuts()
            self._setup_statusbar()
            
            # Restore state - use captured scroll data if provided
            self._load_ui_state()
            self._load_state()
            
            # Override pending scroll restoration with captured data if available
            if captured_scroll:
                self._pending_restore_view_type = captured_scroll['view']
                self._pending_restore_scroll_pos = captured_scroll['scroll_pos']
                self._pending_restore_list_dt_str = captured_scroll['list_dt'].isoformat() if captured_scroll['list_dt'] else None
                # Also apply the current date
                self._calendar_widget.set_date(captured_scroll['date'])
                print(f"DEBUG _apply_config: using captured scroll_pos={captured_scroll['scroll_pos']}", file=sys.stderr)
            
            # Initialize data (skip automatic scroll restoration - we'll do it explicitly)
            self._skip_auto_scroll_restore = True
            self._initialize_data()
            self._skip_auto_scroll_restore = False
            
            # Restart auto-refresh timer if configured
            if new_config.refresh_interval > 0:
                self._auto_refresh_timer.start(new_config.refresh_interval * 1000)
                print(f"DEBUG: Auto-refresh enabled every {new_config.refresh_interval} seconds", file=sys.stderr)
            
            # Re-add config path to watcher (may have been removed during file editing)
            config_path = Config.get_default_config_path()
            if config_path.exists() and str(config_path) not in self._config_watcher.files():
                self._config_watcher.addPath(str(config_path))
                print(f"DEBUG: Re-added config path to watcher: {config_path}", file=sys.stderr)
            
            # Restore scroll position after UI has settled (longer delay for config reload)
            QTimer.singleShot(500, self._restore_scroll_position)
            
            self._statusbar.showMessage("Configuration applied successfully", 3000)
            print("DEBUG: Config applied successfully", file=sys.stderr)
            
        except Exception as e:
            error_msg = f"Failed to apply config: {e}"
            print(f"ERROR: {error_msg}", file=sys.stderr)
            self._statusbar.showMessage(error_msg, 5000)
            QMessageBox.warning(
                self,
                "Config Apply Error",
                f"Failed to apply configuration:\n{e}"
            )
    
    def _clear_ui(self):
        """Clear existing UI components to prepare for rebuild."""
        # Remove central widget
        old_central = self.centralWidget()
        if old_central:
            old_central.deleteLater()
        self.setCentralWidget(None)
        
        # Remove toolbar
        for toolbar in self.findChildren(QToolBar):
            self.removeToolBar(toolbar)
            toolbar.deleteLater()
        
        # Remove statusbar (will be recreated)
        old_statusbar = self.statusBar()
        if old_statusbar:
            self.setStatusBar(None)
    
    def _on_slot_double_clicked(self, dt: datetime):
        """Handle double-click on empty time slot to create event."""
        self._open_event_dialog(initial_datetime=dt)
    
    def _on_event_clicked(self, event: EventData):
        """Handle single click on event - open for editing."""
        self._open_event_dialog(event=event)
    
    def _on_event_double_clicked(self, event: EventData):
        """Handle double-click on event to edit."""
        self._open_event_dialog(event=event)
    
    def _on_event_time_changed(self, event: EventData, new_start: datetime, new_end: datetime):
        """Handle event time change from drag-and-drop."""
        import sys
        
        # Skip if times haven't actually changed
        from backend.timezone_utils import to_local_datetime
        old_start = to_local_datetime(event.start)
        old_end = to_local_datetime(event.end)
        
        # Compare times (ignoring timezone for comparison)
        if (new_start.replace(tzinfo=None) == old_start.replace(tzinfo=None) and
            new_end.replace(tzinfo=None) == old_end.replace(tzinfo=None)):
            return
        
        print(f"DEBUG: Event time changed: {event.summary}", file=sys.stderr)
        print(f"DEBUG: Old: {old_start} - {old_end}", file=sys.stderr)
        print(f"DEBUG: New: {new_start} - {new_end}", file=sys.stderr)
        
        # Handle recurring events - ask user what to do
        if event.is_recurring:
            result = QMessageBox.question(
                self,
                "Modify Recurring Event",
                f"'{event.summary}' is a recurring event.\n\n"
                "Moving recurring events affects all instances in the series.\n"
                "To modify just this instance, edit the event and change dates there.\n\n"
                "Move all instances?",
                QMessageBox.Yes | QMessageBox.Cancel,
                QMessageBox.Cancel
            )
            if result != QMessageBox.Yes:
                # User cancelled - refresh to restore display
                self._refresh_events()
                return
        
        # Convert local times to UTC for storage (server expects UTC)
        from backend.timezone_utils import local_naive_to_utc
        
        new_start_utc = local_naive_to_utc(new_start)
        new_end_utc = local_naive_to_utc(new_end)
        
        # Update the event's times directly (preserves _caldav_event reference)
        event.start = new_start_utc
        event.end = new_end_utc
        
        # Save through event store
        success = self.event_store.update_event(event)
        if success:
            self._refresh_events()
        else:
            # Refresh to restore original display
            self._refresh_events()
    
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
        dialog.closed.connect(lambda d=dialog: self._on_event_dialog_closed(d))
        
        self._event_dialogs.append(dialog)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
    
    def _on_event_saved(self, event: EventData):
        """Handle event saved."""
        self._refresh_events()
    
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
