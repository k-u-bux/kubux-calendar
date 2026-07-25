"""Tests for gui/widgets/recurrence_widget.py — recurrence rule round-trip."""

from datetime import datetime

from PySide6.QtCore import QDateTime

from backend.event import RecurrenceRule
from gui.widgets.recurrence_widget import RecurrenceWidget


def _make_widget():
    return RecurrenceWidget()


# ----------------------------------------------------------------------
# get_recurrence
# ----------------------------------------------------------------------

def test_get_recurrence_unchecked_returns_none(qapp):
    w = _make_widget()
    w.setChecked(False)
    assert w.get_recurrence() is None


def test_recurrence_daily_round_trip(qapp):
    w = _make_widget()
    w.setChecked(True)
    w._freq_combo.setCurrentText("Daily")
    w._interval_spin.setValue(3)
    w._end_combo.setCurrentIndex(0)  # never
    rule = w.get_recurrence()
    assert rule.frequency == "DAILY"
    assert rule.interval == 3
    assert rule.count is None
    assert rule.until is None


def test_recurrence_weekly_with_byday(qapp):
    w = _make_widget()
    w.setChecked(True)
    w._freq_combo.setCurrentText("Weekly")
    w._weekday_checks[0].setChecked(True)  # MO
    w._weekday_checks[2].setChecked(True)  # WE
    rule = w.get_recurrence()
    assert rule.frequency == "WEEKLY"
    assert rule.by_day == ["MO", "WE"]


def test_recurrence_monthly_with_count(qapp):
    w = _make_widget()
    w.setChecked(True)
    w._freq_combo.setCurrentText("Monthly")
    w._end_combo.setCurrentIndex(1)  # after N
    w._count_spin.setValue(10)
    rule = w.get_recurrence()
    assert rule.frequency == "MONTHLY"
    assert rule.count == 10


def test_recurrence_yearly_with_until(qapp):
    w = _make_widget()
    w.setChecked(True)
    w._freq_combo.setCurrentText("Yearly")
    w._end_combo.setCurrentIndex(2)  # until date
    w._until_edit.setDateTime(QDateTime(datetime(2027, 1, 1, 0, 0, 0)))
    rule = w.get_recurrence()
    assert rule.frequency == "YEARLY"
    assert rule.until is not None


# ----------------------------------------------------------------------
# set_recurrence
# ----------------------------------------------------------------------

def test_set_recurrence_none_unchecks(qapp):
    w = _make_widget()
    w.setChecked(True)
    w.set_recurrence(None)
    assert w.isChecked() is False


def test_set_recurrence_round_trip(qapp):
    w = _make_widget()
    rule = RecurrenceRule(frequency="WEEKLY", interval=2, by_day=["MO", "WE", "FR"])
    w.set_recurrence(rule)
    out = w.get_recurrence()
    assert out is not None
    assert out.frequency == "WEEKLY"
    assert out.interval == 2
    assert out.by_day == ["MO", "WE", "FR"]


def test_set_recurrence_strips_byday_prefix(qapp):
    """'1MO' (first Monday) should strip to 'MO'."""
    w = _make_widget()
    rule = RecurrenceRule(frequency="WEEKLY", by_day=["1MO", "FR"])
    w.set_recurrence(rule)
    assert w._weekday_checks[0].isChecked()  # MO
    assert w._weekday_checks[4].isChecked()  # FR
    out = w.get_recurrence()
    assert out.by_day == ["MO", "FR"]


def test_set_recurrence_with_count(qapp):
    w = _make_widget()
    rule = RecurrenceRule(frequency="DAILY", count=5)
    w.set_recurrence(rule)
    assert w._end_combo.currentIndex() == 1
    assert w._count_spin.value() == 5
    out = w.get_recurrence()
    assert out.count == 5


def test_set_recurrence_with_until(qapp):
    w = _make_widget()
    until = datetime(2027, 6, 1, 12, 0, 0)
    rule = RecurrenceRule(frequency="DAILY", until=until)
    w.set_recurrence(rule)
    assert w._end_combo.currentIndex() == 2
    out = w.get_recurrence()
    assert out.until is not None

# ----------------------------------------------------------------------
# Regression: localized (non-English) labels must not break frequency
# handling — mapping is index-based, never text-based
# ----------------------------------------------------------------------

def test_recurrence_widget_localized_labels(qapp):
    from backend.config import LabelsConfig
    labels = LabelsConfig()
    labels.freq_daily = "Täglich"
    labels.freq_weekly = "Wöchentlich"
    labels.freq_monthly = "Monatlich"
    labels.freq_yearly = "Jährlich"
    w = RecurrenceWidget(labels_config=labels)
    w.setChecked(True)

    # Select "Wöchentlich" (index 1) — a text-based freq lookup would
    # raise KeyError here.
    w._freq_combo.setCurrentIndex(1)
    rule = w.get_recurrence()
    assert rule is not None
    assert rule.frequency == "WEEKLY"

    # Interval label follows the index, not the (localized) text
    w._freq_combo.setCurrentIndex(2)
    assert w._interval_label.text() == "month(s)"


def test_recurrence_widget_weekday_visibility_index_based(qapp):
    w = RecurrenceWidget()
    w._freq_combo.setCurrentIndex(0)  # DAILY
    w._update_weekday_visibility()
    assert w._weekday_row.isHidden()
    w._freq_combo.setCurrentIndex(1)  # WEEKLY
    w._update_weekday_visibility()
    assert not w._weekday_row.isHidden()
    w._freq_combo.setCurrentIndex(2)  # MONTHLY
    w._update_weekday_visibility()
    assert w._weekday_row.isHidden()

