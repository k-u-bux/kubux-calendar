"""
WeekView: 7-day timeline view with day-name header.

Extracted from ``calendar_widget.py``.
"""

from datetime import datetime, date, time as dt_time, timedelta

from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel
from PySide6.QtCore import Qt

from backend import EventView as EventData
from .config_state import (
    get_colors_config,
    get_localization_config,
    get_interface_font,
)
from .timeline_view import TimelineViewBase
from .day_column import DayColumnWidget


class WeekView(TimelineViewBase):
    """Week view showing 7 days side by side with all-day events section."""

    def __init__(self, parent=None, mapper=None, scroll_state=None, follow_state=None):
        self._start_date = self._get_week_start(date.today())
        super().__init__(parent, mapper=mapper, scroll_state=scroll_state, follow_state=follow_state)

    # ------------------------------------------------------------------
    # Hooks
    # ------------------------------------------------------------------

    def _get_num_days(self) -> int:
        return 7

    def _get_day_dates(self) -> list[date]:
        return [self._start_date + timedelta(days=i) for i in range(7)]

    def _create_day_columns(self) -> list[DayColumnWidget]:
        return [DayColumnWidget(self._start_date + timedelta(days=i), self._mapper, self._follow_state) for i in range(7)]

    def _create_header(self, time_col_width: int, scrollbar_width: int) -> QWidget:
        """Create the day-name header with scrollbar-aligned spacer."""
        colors = get_colors_config()
        header = QWidget()
        header.setStyleSheet(f"background: {colors.header_background};")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(time_col_width, 0, 0, 0)
        header_layout.setSpacing(1)

        self._header_labels: list[QLabel] = []
        for _ in range(7):
            label = QLabel()
            label.setAlignment(Qt.AlignCenter)
            label.setStyleSheet(f"font-weight: bold; padding: 8px; background: {colors.header_background};")
            header_layout.addWidget(label, 1)
            self._header_labels.append(label)

        # Scrollbar-aligned spacer
        self._header_scrollbar_spacer = QWidget()
        self._header_scrollbar_spacer.setFixedWidth(scrollbar_width)
        header_layout.addWidget(self._header_scrollbar_spacer)

        self._update_headers()
        return header

    def _on_scrollbar_visibility_changed(self, needs_scrollbar: bool) -> None:
        """Show/hide header scrollbar spacer to align with main scroll."""
        self._header_scrollbar_spacer.setVisible(needs_scrollbar)

    def _update_headers(self) -> None:
        """Update day-name labels with current week's dates."""
        localization = get_localization_config()
        colors = get_colors_config()
        font_name, font_size = get_interface_font()
        for i, label in enumerate(self._header_labels):
            d = self._start_date + timedelta(days=i)
            day_name = localization.get_day_name_for_column(i)
            label.setText(f"{day_name} {d.day}")
            if d == date.today():
                label.setStyleSheet(
                    f"font-family: '{font_name}'; font-size: {font_size}pt; "
                    f"font-weight: bold; padding: 8px; "
                    f"background: {colors.today_highlight_background}; "
                    f"color: {colors.today_highlight_text};"
                )
            else:
                label.setStyleSheet(
                    f"font-family: '{font_name}'; font-size: {font_size}pt; "
                    f"font-weight: bold; padding: 8px; "
                    f"background: {colors.header_background};"
                )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_date(self, d: date):
        self._start_date = self._get_week_start(d)
        for i, col in enumerate(self._day_columns):
            col.set_date(self._start_date + timedelta(days=i))
        self._update_headers()
        # refresh_events() is called via set_events() which follows set_date()
        # in the navigation flow.  Avoid double-render.

    def get_date_range(self) -> tuple[datetime, datetime]:
        start = datetime.combine(self._start_date, dt_time.min)
        end = datetime.combine(self._start_date + timedelta(days=6), dt_time.max)
        return start, end

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_week_start(self, d: date) -> date:
        localization = get_localization_config()
        return localization.get_week_start(d)