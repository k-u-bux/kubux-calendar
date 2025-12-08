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
    QSpinBox, QMessageBox, QFrame, QSizePolicy
)
from PySide6.QtCore import Qt, Signal, QDateTime
from PySide6.QtGui import QFont, QCloseEvent, QFontMetrics

from backend.caldav_client import EventData, RecurrenceRule
from backend.event_store import EventStore, CalendarSource
from backend.timezone_utils import utc_to_local_naive as utc_to_local, local_naive_to_utc as local_to_utc


class RecurrenceWidget(QGroupBox):
    """Widget for configuring event recurrence."""
    
    # Day name abbreviations in RRULE format
    DAY_CODES = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
    DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    
    def __init__(self, parent=None):
        super().__init__("Recurrence", parent)
        self.setCheckable(True)
        self.setChecked(False)
        self._setup_ui()
    
    def _setup_ui(self):
        layout = QFormLayout(self)
        
        # Frequency
        self._freq_combo = QComboBox()
        self._freq_combo.addItems(["Daily", "Weekly", "Monthly", "Yearly"])
        layout.addRow("Repeat:", self._freq_combo)
        
        # Interval
        interval_layout = QHBoxLayout()
        self._interval_spin = QSpinBox()
        self._interval_spin.setRange(1, 99)
        self._interval_spin.setValue(1)
        interval_layout.addWidget(self._interval_spin)
        self._interval_label = QLabel("day(s)")
        interval_layout.addWidget(self._interval_label)
        interval_layout.addStretch()
        layout.addRow("Every:", interval_layout)
        
        # Week day checkboxes (shown only for weekly recurrence)
        # Use a container for the entire row so we can hide label + checkboxes together
        self._weekday_row = QWidget()
        weekday_row_layout = QHBoxLayout(self._weekday_row)
        weekday_row_layout.setContentsMargins(0, 0, 0, 0)
        weekday_row_layout.setSpacing(8)
        
        weekday_label = QLabel("On days:")
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
        self._end_combo.addItems(["Never", "After N occurrences", "Until date"])
        self._end_combo.currentIndexChanged.connect(self._update_end_widget)
        layout.addRow("Ends:", self._end_combo)
        
        self._count_spin = QSpinBox()
        self._count_spin.setRange(1, 999)
        self._count_spin.setValue(10)
        self._count_spin.hide()
        layout.addRow("Occurrences:", self._count_spin)
        
        self._until_edit = QDateTimeEdit()
        self._until_edit.setCalendarPopup(True)
        self._until_edit.setDateTime(QDateTime.currentDateTime().addMonths(1))
        self._until_edit.hide()
        layout.addRow("Until:", self._until_edit)
    
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
            self.setWindowTitle("New Event")
        else:
            self.setWindowTitle(f"Edit: {self.event_data.summary}")
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
        layout.setSpacing(12)
        
        if self.event_data and self.event_data.read_only:
            notice = QLabel("🔒 This event is read-only (from a subscription)")
            notice.setStyleSheet("background: #fff3cd; padding: 8px; border-radius: 4px; color: #856404;")
            layout.addWidget(notice)
        
        form = QFormLayout()
        form.setSpacing(8)
        
        self._title_edit = QLineEdit()
        self._title_edit.setPlaceholderText("Event title")
        form.addRow("Title:", self._title_edit)
        
        self._calendar_combo = QComboBox()
        self._calendars = self.event_store.get_writable_calendars()
        for cal in self._calendars:
            self._calendar_combo.addItem(f"{cal.name} ({cal.account_name})", cal.id)
        
        if self.is_new:
            form.addRow("Calendar:", self._calendar_combo)
            # Set last used calendar as default
            last_calendar_id = self._dialog_state.get("last_calendar_id")
            if last_calendar_id:
                for i in range(self._calendar_combo.count()):
                    if self._calendar_combo.itemData(i) == last_calendar_id:
                        self._calendar_combo.setCurrentIndex(i)
                        break
        else:
            cal_label = QLabel(f"{self.event_data.calendar_name}")
            form.addRow("Calendar:", cal_label)
        
        self._all_day_check = QCheckBox("All-day event")
        self._all_day_check.stateChanged.connect(self._on_all_day_changed)
        form.addRow("", self._all_day_check)
        
        self._start_edit = QDateTimeEdit()
        self._start_edit.setCalendarPopup(True)
        self._start_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        self._start_edit.dateTimeChanged.connect(self._on_start_changed)
        form.addRow("Start:", self._start_edit)
        
        self._end_edit = QDateTimeEdit()
        self._end_edit.setCalendarPopup(True)
        self._end_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        form.addRow("End:", self._end_edit)
        
        self._location_edit = QLineEdit()
        self._location_edit.setPlaceholderText("Location (optional)")
        form.addRow("Location:", self._location_edit)
        
        self._description_edit = QTextEdit()
        self._description_edit.setPlaceholderText("Description (optional)")
        # Make description field expand vertically and have a reasonable minimum height
        self._description_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        fm = QFontMetrics(self._description_edit.font())
        min_height = fm.height() * 5 + 10  # Minimum 5 lines + padding
        self._description_edit.setMinimumHeight(min_height)
        form.addRow("Description:", self._description_edit)
        
        layout.addLayout(form, 1)  # stretch factor 1 - form (with description) gets extra space
        
        self._recurrence_widget = RecurrenceWidget()
        layout.addWidget(self._recurrence_widget)
        
        # No addStretch() here - description field should grow, not empty space
        
        button_layout = QHBoxLayout()
        
        if not self.is_new and not (self.event_data and self.event_data.read_only):
            self._delete_btn = QPushButton("Delete")
            self._delete_btn.setStyleSheet("background: #dc3545; color: white;")
            self._delete_btn.clicked.connect(self._on_delete)
            button_layout.addWidget(self._delete_btn)
        
        button_layout.addStretch()
        
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.clicked.connect(self.close)
        button_layout.addWidget(self._cancel_btn)
        
        if not (self.event_data and self.event_data.read_only):
            self._save_btn = QPushButton("Save")
            self._save_btn.setStyleSheet("background: #007bff; color: white;")
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
            self.event_data.summary = title
            self.event_data.start = start_dt
            self.event_data.end = end_dt
            self.event_data.description = self._description_edit.toPlainText()
            self.event_data.location = self._location_edit.text().strip()
            self.event_data.all_day = self._all_day_check.isChecked()
            self.event_data.recurrence = recurrence
            
            if self.event_store.update_event(self.event_data):
                self.event_saved.emit(self.event_data)
                self.close()
            else:
                QMessageBox.critical(self, "Error", "Failed to update event.")
    
    def _on_delete(self):
        if self.event_data is None:
            return
        
        if self.event_data.is_recurring:
            result = QMessageBox.question(self, "Delete Recurring Event",
                "This is a recurring event. Do you want to delete all occurrences?",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel, QMessageBox.Cancel)
            if result == QMessageBox.Cancel:
                return
            elif result == QMessageBox.No:
                if self.event_data.recurrence_id:
                    if self.event_store.delete_recurring_instance(self.event_data, self.event_data.recurrence_id):
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
        
        if self.event_store.delete_event(self.event_data):
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
