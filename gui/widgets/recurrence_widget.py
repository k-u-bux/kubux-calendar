"""
RecurrenceWidget: widget for configuring event recurrence (RRULE).

Extracted from ``event_dialog.py``.
"""

from typing import Optional
import pytz

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QComboBox, QPushButton, QLabel, QGroupBox,
    QSpinBox, QCheckBox, QDateTimeEdit,
)
from PySide6.QtCore import QDateTime

from backend import RecurrenceRule


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