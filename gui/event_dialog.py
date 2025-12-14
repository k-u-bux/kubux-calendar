"""
Event Dialog for creating and editing calendar events.

This is an independent window (not a modal dialog) for editing event details.
"""

from datetime import datetime, timedelta, date, time as dt_time
from typing import Optional
import pytz

import json

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLineEdit, QTextEdit, QDateTimeEdit, QCheckBox,
    QComboBox, QPushButton, QLabel, QGroupBox,
    QSpinBox, QMessageBox, QFrame, QSizePolicy, QScrollArea
)
from PySide6.QtCore import Qt, Signal, QDateTime
from PySide6.QtGui import QFont, QCloseEvent, QFontMetrics

from backend.event_wrapper import CalEvent, CalendarSource, EventInstance
from backend.event_store import EventStore

# EventData can be either EventInstance (received from main_window) or CalEvent (from create_event)
EventData = EventInstance
from dataclasses import dataclass
from typing import Optional

# Simple RecurrenceRule for UI purposes
# The actual RRULE is stored in icalendar.Event
@dataclass
class RecurrenceRule:
    """Simple representation for UI configuration."""
    frequency: str  # DAILY, WEEKLY, MONTHLY, YEARLY
    interval: int = 1
    count: Optional[int] = None
    until: Optional[datetime] = None
    by_day: Optional[list[str]] = None
from backend.timezone_utils import utc_to_local_naive as utc_to_local, local_naive_to_utc as local_to_utc


class RecurrenceWidget(QGroupBox):
    """Widget for configuring event recurrence."""
    
    # Day name abbreviations in RRULE format
    DAY_CODES = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
    DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    
    def __init__(self, labels_config=None, parent=None):
        from backend.config import LabelsConfig
        self.labels = labels_config or LabelsConfig()
        super().__init__(self.labels.recurrence_title, parent)
        self.setCheckable(True)
        self.setChecked(False)
        self._setup_ui()
    
    def _setup_ui(self):
        layout = QFormLayout(self)
        
        # Frequency
        self._freq_combo = QComboBox()
        self._freq_combo.addItems([
            self.labels.freq_daily,
            self.labels.freq_weekly,
            self.labels.freq_monthly,
            self.labels.freq_yearly
        ])
        layout.addRow(self.labels.recurrence_repeat, self._freq_combo)
        
        # Interval
        interval_layout = QHBoxLayout()
        self._interval_spin = QSpinBox()
        self._interval_spin.setRange(1, 99)
        self._interval_spin.setValue(1)
        interval_layout.addWidget(self._interval_spin)
        self._interval_label = QLabel("day(s)")
        interval_layout.addWidget(self._interval_label)
        interval_layout.addStretch()
        layout.addRow(self.labels.recurrence_every, interval_layout)
        
        # Week day checkboxes (shown only for weekly recurrence)
        # Use a container for the entire row so we can hide label + checkboxes together
        self._weekday_row = QWidget()
        weekday_row_layout = QHBoxLayout(self._weekday_row)
        weekday_row_layout.setContentsMargins(0, 0, 0, 0)
        weekday_row_layout.setSpacing(8)
        
        weekday_label = QLabel(self.labels.recurrence_on_days)
        weekday_row_layout.addWidget(weekday_label)
        
        self._weekday_checks: list[QCheckBox] = []
        for i, label in enumerate(self.DAY_LABELS):
            cb = QCheckBox(label)
            cb.setToolTip(f"Repeat on {label}")
            weekday_row_layout.addWidget(cb)
            self._weekday_checks.append(cb)
        weekday_row_layout.addStretch()
        
        layout.addRow(self._weekday_row)
        self._weekday_row.hide()  # Hidden until "Weekly" is selected
        
        self._freq_combo.currentIndexChanged.connect(self._update_interval_label)
        self._freq_combo.currentIndexChanged.connect(self._update_weekday_visibility)
        
        # End condition
        self._end_combo = QComboBox()
        self._end_combo.addItems([
            self.labels.end_never,
            self.labels.end_after_count,
            self.labels.end_until_date
        ])
        self._end_combo.currentIndexChanged.connect(self._update_end_widget)
        layout.addRow(self.labels.recurrence_ends, self._end_combo)
        
        self._count_spin = QSpinBox()
        self._count_spin.setRange(1, 999)
        self._count_spin.setValue(10)
        self._count_spin.hide()
        layout.addRow(self.labels.recurrence_occurrences, self._count_spin)
        
        self._until_edit = QDateTimeEdit()
        self._until_edit.setCalendarPopup(True)
        self._until_edit.setDateTime(QDateTime.currentDateTime().addMonths(1))
        self._until_edit.hide()
        layout.addRow(self.labels.recurrence_until, self._until_edit)
    
    def _update_interval_label(self):
        freq = self._freq_combo.currentText().lower()
        labels = {"daily": "day(s)", "weekly": "week(s)", "monthly": "month(s)", "yearly": "year(s)"}
        self._interval_label.setText(labels.get(freq, "day(s)"))
    
    def _update_weekday_visibility(self):
        """Show/hide weekday row based on frequency selection."""
        is_weekly = self._freq_combo.currentText() == "Weekly"
        self._weekday_row.setVisible(is_weekly)
    
    def _update_end_widget(self):
        index = self._end_combo.currentIndex()
        self._count_spin.setVisible(index == 1)
        self._until_edit.setVisible(index == 2)
    
    def get_recurrence(self) -> Optional[RecurrenceRule]:
        if not self.isChecked():
            return None
        freq_map = {"Daily": "DAILY", "Weekly": "WEEKLY", "Monthly": "MONTHLY", "Yearly": "YEARLY"}
        freq = freq_map[self._freq_combo.currentText()]
        interval = self._interval_spin.value()
        count = None
        until = None
        by_day = None
        
        # Get selected weekdays for weekly recurrence
        if freq == "WEEKLY":
            selected_days = []
            for i, cb in enumerate(self._weekday_checks):
                if cb.isChecked():
                    selected_days.append(self.DAY_CODES[i])
            if selected_days:
                by_day = selected_days
        
        end_index = self._end_combo.currentIndex()
        if end_index == 1:
            count = self._count_spin.value()
        elif end_index == 2:
            until_dt = self._until_edit.dateTime().toPython()
            until = pytz.UTC.localize(until_dt) if until_dt.tzinfo is None else until_dt
        
        return RecurrenceRule(frequency=freq, interval=interval, count=count, until=until, by_day=by_day)
    
    def set_recurrence(self, rule: Optional[RecurrenceRule]):
        if rule is None:
            self.setChecked(False)
            return
        self.setChecked(True)
        freq_map = {"DAILY": 0, "WEEKLY": 1, "MONTHLY": 2, "YEARLY": 3}
        self._freq_combo.setCurrentIndex(freq_map.get(rule.frequency, 0))
        self._interval_spin.setValue(rule.interval)
        
        # Set weekday checkboxes
        for cb in self._weekday_checks:
            cb.setChecked(False)
        if rule.by_day:
            for day_code in rule.by_day:
                # Handle both string and potential vWeekday objects
                day_str = str(day_code).upper()
                # Strip any prefix numbers (like "1MO" for first Monday)
                if len(day_str) > 2:
                    day_str = day_str[-2:]
                if day_str in self.DAY_CODES:
                    idx = self.DAY_CODES.index(day_str)
                    self._weekday_checks[idx].setChecked(True)
        
        if rule.count:
            self._end_combo.setCurrentIndex(1)
            self._count_spin.setValue(rule.count)
        elif rule.until:
            self._end_combo.setCurrentIndex(2)
            self._until_edit.setDateTime(QDateTime(rule.until))
        else:
            self._end_combo.setCurrentIndex(0)


class EventDialog(QWidget):
    """Independent window for creating/editing calendar events."""
    
    event_saved = Signal(EventData)
    event_deleted = Signal(EventData)
    closed = Signal()
    
    def __init__(self, event_store: EventStore, event_data: Optional[EventData] = None,
                 initial_datetime: Optional[datetime] = None, parent=None):
        super().__init__(parent)
        self.event_store = event_store
        self.event_data = event_data
        self.is_new = event_data is None
        self.initial_datetime = initial_datetime or datetime.now()
        
        self._setup_window()
        self._setup_ui()
        self._populate_data()
    
    def _setup_window(self):
        if self.is_new:
            self.setWindowTitle(self.event_store.config.labels.dialog_new_event)
        else:
            self.setWindowTitle(f"{self.event_store.config.labels.dialog_edit_event}: {self.event_data.summary}")
        self.setWindowFlags(Qt.Window)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setMinimumSize(400, 500)
        
        # State file for persistence (from event_store's config)
        self._state_file = self.event_store.config.state_file
        self._dialog_state = self._load_dialog_state()
        
        # Restore window geometry from JSON state
        geometry = self._dialog_state.get("event_dialog_geometry")
        if geometry:
            import base64
            self.restoreGeometry(base64.b64decode(geometry))
        else:
            self.resize(500, 600)
    
    def _load_dialog_state(self) -> dict:
        """Load dialog state from the JSON state file."""
        if self._state_file.exists():
            try:
                with open(self._state_file, 'r') as f:
                    state = json.load(f)
                    return state.get('event_dialog', {})
            except Exception:
                pass
        return {}
    
    def _save_dialog_state(self):
        """Save dialog state to the JSON state file."""
        try:
            # Load existing state to preserve other data
            existing_state = {}
            if self._state_file.exists():
                with open(self._state_file, 'r') as f:
                    existing_state = json.load(f)
            
            # Update dialog state
            existing_state['event_dialog'] = self._dialog_state
            
            # Ensure directory exists
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            
            with open(self._state_file, 'w') as f:
                json.dump(existing_state, f, indent=2)
        except Exception as e:
            print(f"Error saving dialog state: {e}", file=__import__('sys').stderr)
    
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(8, 8, 8, 8)
        
        if self.event_data and self.event_data.read_only:
            notice = QLabel(self.event_store.config.labels.readonly_notice)
            notice.setStyleSheet(
                f"background: {self.event_store.config.colors.readonly_notice_background}; "
                f"padding: 8px; border-radius: 4px; "
                f"color: {self.event_store.config.colors.readonly_notice_text};"
            )
            layout.addWidget(notice)
        
        # Create scrollable content area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFrameStyle(QFrame.NoFrame)
        
        # Content widget inside scroll area
        scroll_content = QWidget()
        content_layout = QVBoxLayout(scroll_content)
        content_layout.setSpacing(8)
        content_layout.setContentsMargins(0, 0, 0, 0)
        
        form = QFormLayout()
        form.setSpacing(8)
        
        self._title_edit = QLineEdit()
        self._title_edit.setPlaceholderText("Event title")
        form.addRow(self.event_store.config.labels.field_title, self._title_edit)
        
        self._calendar_combo = QComboBox()
        self._calendars = self.event_store.get_writable_calendars()
        for cal in self._calendars:
            self._calendar_combo.addItem(f"{cal.name} ({cal.account_name})", cal.id)
        
        if self.is_new:
            form.addRow(self.event_store.config.labels.field_calendar, self._calendar_combo)
            # Set last used calendar as default
            last_calendar_id = self._dialog_state.get("last_calendar_id")
            if last_calendar_id:
                for i in range(self._calendar_combo.count()):
                    if self._calendar_combo.itemData(i) == last_calendar_id:
                        self._calendar_combo.setCurrentIndex(i)
                        break
        else:
            cal_label = QLabel(f"{self.event_data.calendar_name}")
            form.addRow(self.event_store.config.labels.field_calendar, cal_label)
        
        self._all_day_check = QCheckBox(self.event_store.config.labels.checkbox_allday)
        self._all_day_check.stateChanged.connect(self._on_all_day_changed)
        form.addRow("", self._all_day_check)
        
        self._start_edit = QDateTimeEdit()
        self._start_edit.setCalendarPopup(True)
        self._start_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        self._start_edit.dateTimeChanged.connect(self._on_start_changed)
        form.addRow(self.event_store.config.labels.field_start, self._start_edit)
        
        self._end_edit = QDateTimeEdit()
        self._end_edit.setCalendarPopup(True)
        self._end_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        form.addRow(self.event_store.config.labels.field_end, self._end_edit)
        
        self._location_edit = QLineEdit()
        self._location_edit.setPlaceholderText("Location (optional)")
        form.addRow(self.event_store.config.labels.field_location, self._location_edit)
        
        self._description_edit = QTextEdit()
        self._description_edit.setPlaceholderText("Description (optional)")
        # Set a reasonable minimum height for description (5 lines)
        fm = QFontMetrics(self._description_edit.font())
        min_height = fm.height() * 5 + 10
        self._description_edit.setMinimumHeight(min_height)
        form.addRow(self.event_store.config.labels.field_description, self._description_edit)
        
        content_layout.addLayout(form)
        
        self._recurrence_widget = RecurrenceWidget(labels_config=self.event_store.config.labels)
        content_layout.addWidget(self._recurrence_widget)
        
        scroll.setWidget(scroll_content)
        layout.addWidget(scroll, 1)  # stretch factor 1 - scroll area gets extra space
        
        button_layout = QHBoxLayout()
        
        if not self.is_new and not (self.event_data and self.event_data.read_only):
            self._delete_btn = QPushButton(self.event_store.config.labels.button_delete)
            self._delete_btn.setStyleSheet(
                f"background: {self.event_store.config.colors.button_delete_background}; "
                f"color: {self.event_store.config.colors.button_delete_text};"
            )
            self._delete_btn.clicked.connect(self._on_delete)
            button_layout.addWidget(self._delete_btn)
        
        button_layout.addStretch()
        
        self._cancel_btn = QPushButton(self.event_store.config.labels.button_cancel)
        self._cancel_btn.clicked.connect(self.close)
        button_layout.addWidget(self._cancel_btn)
        
        if not (self.event_data and self.event_data.read_only):
            self._save_btn = QPushButton(self.event_store.config.labels.button_save)
            self._save_btn.setStyleSheet(
                f"background: {self.event_store.config.colors.button_save_background}; "
                f"color: {self.event_store.config.colors.button_save_text};"
            )
            self._save_btn.clicked.connect(self._on_save)
            self._save_btn.setDefault(True)
            button_layout.addWidget(self._save_btn)
        
        layout.addLayout(button_layout)
        
        if self.event_data and self.event_data.read_only:
            self._title_edit.setReadOnly(True)
            self._location_edit.setReadOnly(True)
            self._description_edit.setReadOnly(True)
            self._start_edit.setEnabled(False)
            self._end_edit.setEnabled(False)
            self._all_day_check.setEnabled(False)
            self._recurrence_widget.setEnabled(False)
    
    def _populate_data(self):
        if self.event_data:
            self._title_edit.setText(self.event_data.summary)
            self._location_edit.setText(self.event_data.location)
            self._description_edit.setText(self.event_data.description)
            self._all_day_check.setChecked(self.event_data.all_day)
            # Convert UTC times to local for display
            start_local = utc_to_local(self.event_data.start)
            end_local = utc_to_local(self.event_data.end)
            self._start_edit.setDateTime(QDateTime(start_local))
            self._end_edit.setDateTime(QDateTime(end_local))
            self._recurrence_widget.set_recurrence(self.event_data.recurrence)
        else:
            start = self.initial_datetime
            if start.tzinfo:
                start = start.replace(tzinfo=None)
            minutes = (start.minute // 30) * 30
            start = start.replace(minute=minutes, second=0, microsecond=0)
            end = start + timedelta(hours=1)
            self._start_edit.setDateTime(QDateTime(start))
            self._end_edit.setDateTime(QDateTime(end))
    
    def _on_all_day_changed(self, state: int):
        is_all_day = state == Qt.Checked
        fmt = "yyyy-MM-dd" if is_all_day else "yyyy-MM-dd HH:mm"
        self._start_edit.setDisplayFormat(fmt)
        self._end_edit.setDisplayFormat(fmt)
    
    def _on_start_changed(self, dt: QDateTime):
        if self._end_edit.dateTime() <= dt:
            if self._all_day_check.isChecked():
                self._end_edit.setDateTime(dt.addDays(1))
            else:
                self._end_edit.setDateTime(dt.addSecs(3600))
    
    def _on_save(self):
        title = self._title_edit.text().strip()
        if not title:
            QMessageBox.warning(self, "Validation Error", "Please enter an event title.")
            self._title_edit.setFocus()
            return
        
        # Get times from UI (these are in local timezone)
        start_local = self._start_edit.dateTime().toPython()
        end_local = self._end_edit.dateTime().toPython()
        
        if end_local <= start_local:
            QMessageBox.warning(self, "Validation Error", "End time must be after start time.")
            return
        
        # Convert local times to UTC for storage
        start_dt = local_to_utc(start_local)
        end_dt = local_to_utc(end_local)
        recurrence = self._recurrence_widget.get_recurrence()
        
        if self.is_new:
            calendar_id = self._calendar_combo.currentData()
            if not calendar_id:
                QMessageBox.warning(self, "Error", "Please select a calendar.")
                return
            
            new_event = self.event_store.create_event(
                calendar_id=calendar_id, summary=title, start=start_dt, end=end_dt,
                description=self._description_edit.toPlainText(),
                location=self._location_edit.text().strip(),
                all_day=self._all_day_check.isChecked(), recurrence=recurrence
            )
            if new_event:
                # Save last used calendar
                self._dialog_state["last_calendar_id"] = calendar_id
                self.event_saved.emit(new_event)
                self.close()
            else:
                QMessageBox.critical(self, "Error", "Failed to create event.")
        else:
            # Get the underlying CalEvent for modification
            # (EventInstance.event is the CalEvent)
            cal_event = self.event_data.event if hasattr(self.event_data, 'event') else self.event_data
            
            cal_event.summary = title
            cal_event.start = start_dt
            cal_event.end = end_dt
            cal_event.description = self._description_edit.toPlainText()
            cal_event.location = self._location_edit.text().strip()
            cal_event.all_day = self._all_day_check.isChecked()
            # Note: recurrence update via repository would need special handling
            
            if self.event_store.update_event(cal_event):
                self.event_saved.emit(self.event_data)
                self.close()
            else:
                QMessageBox.critical(self, "Error", "Failed to update event.")
    
    def _on_delete(self):
        if self.event_data is None:
            return
        
        # Get the underlying CalEvent for deletion
        cal_event = self.event_data.event if hasattr(self.event_data, 'event') else self.event_data
        
        if self.event_data.is_recurring:
            result = QMessageBox.question(self, "Delete Recurring Event",
                "This is a recurring event. Do you want to delete all occurrences?",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel, QMessageBox.Cancel)
            if result == QMessageBox.Cancel:
                return
            elif result == QMessageBox.No:
                # Delete single instance - use the instance's start time
                instance_start = self.event_data.start
                if self.event_store.delete_recurring_instance(cal_event, instance_start):
                    self.event_deleted.emit(self.event_data)
                    self.close()
                else:
                    QMessageBox.critical(self, "Error", "Failed to delete event instance.")
                return
        else:
            result = QMessageBox.question(self, "Delete Event",
                f"Are you sure you want to delete '{self.event_data.summary}'?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if result != QMessageBox.Yes:
                return
        
        if self.event_store.delete_event(cal_event):
            self.event_deleted.emit(self.event_data)
            self.close()
        else:
            QMessageBox.critical(self, "Error", "Failed to delete event.")
    
    def closeEvent(self, close_event: QCloseEvent):
        # Save window geometry (encode as base64 for JSON)
        import base64
        self._dialog_state["event_dialog_geometry"] = base64.b64encode(self.saveGeometry().data()).decode('utf-8')
        self._save_dialog_state()
        self.closed.emit()
        super().closeEvent(close_event)
