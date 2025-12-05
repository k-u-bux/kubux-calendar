"""
Event Dialog for creating and editing calendar events.

This is an independent window (not a modal dialog) for editing event details.
"""

from datetime import datetime, timedelta, date, time
from typing import Optional
import pytz

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLineEdit, QTextEdit, QDateTimeEdit, QCheckBox,
    QComboBox, QPushButton, QLabel, QGroupBox,
    QSpinBox, QMessageBox, QFrame
)
from PySide6.QtCore import Qt, Signal, QDateTime, QSettings
from PySide6.QtGui import QFont, QCloseEvent

from backend.caldav_client import EventData, RecurrenceRule
from backend.event_store import EventStore, CalendarSource


class RecurrenceWidget(QGroupBox):
    """Widget for configuring event recurrence."""
    
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
        
        self._freq_combo.currentIndexChanged.connect(self._update_interval_label)
        
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
        end_index = self._end_combo.currentIndex()
        if end_index == 1:
            count = self._count_spin.value()
        elif end_index == 2:
            until_dt = self._until_edit.dateTime().toPython()
            until = pytz.UTC.localize(until_dt) if until_dt.tzinfo is None else until_dt
        return RecurrenceRule(frequency=freq, interval=interval, count=count, until=until)
    
    def set_recurrence(self, rule: Optional[RecurrenceRule]):
        if rule is None:
            self.setChecked(False)
            return
        self.setChecked(True)
        freq_map = {"DAILY": 0, "WEEKLY": 1, "MONTHLY": 2, "YEARLY": 3}
        self._freq_combo.setCurrentIndex(freq_map.get(rule.frequency, 0))
        self._interval_spin.setValue(rule.interval)
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
        
        # Restore window geometry from settings
        self._settings = QSettings("kubux", "kubux-calendar")
        geometry = self._settings.value("event_dialog_geometry")
        if geometry:
            self.restoreGeometry(geometry)
        else:
            self.resize(500, 600)
    
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
        form.addRow("Description:", self._description_edit)
        
        layout.addLayout(form)
        
        self._recurrence_widget = RecurrenceWidget()
        layout.addWidget(self._recurrence_widget)
        
        layout.addStretch()
        
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
            start_dt = self.event_data.start
            end_dt = self.event_data.end
            self._start_edit.setDateTime(QDateTime(start_dt.replace(tzinfo=None)))
            self._end_edit.setDateTime(QDateTime(end_dt.replace(tzinfo=None)))
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
        
        start_dt = self._start_edit.dateTime().toPython()
        end_dt = self._end_edit.dateTime().toPython()
        
        if end_dt <= start_dt:
            QMessageBox.warning(self, "Validation Error", "End time must be after start time.")
            return
        
        start_dt = pytz.UTC.localize(start_dt) if start_dt.tzinfo is None else start_dt
        end_dt = pytz.UTC.localize(end_dt) if end_dt.tzinfo is None else end_dt
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
        # Save window geometry
        self._settings.setValue("event_dialog_geometry", self.saveGeometry())
        self.closed.emit()
        super().closeEvent(close_event)
