"""
Configuration parser for Kubux Calendar.

Handles TOML file parsing and secure password retrieval via external programs.
"""

import tomllib
import subprocess
import os
import sys
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class NextcloudAccount:
    """Configuration for a Nextcloud CalDAV account."""
    name: str
    url: str
    username: str
    password_key: str
    color: str = "#4285f4"
    refresh_interval: Optional[int] = None
    outdate_threshold: Optional[int] = None

    _password: Optional[str] = field(default=None, repr=False)

    def get_password(self, password_program: str) -> str:
        """Retrieve password using the configured password program."""
        if self._password is None:
            try:
                result = subprocess.run(
                    [password_program, self.password_key],
                    capture_output=True, text=True, timeout=30,
                )
                if result.returncode == 0:
                    self._password = result.stdout.strip()
                else:
                    raise RuntimeError(
                        f"Password program failed for key '{self.password_key}': {result.stderr}"
                    )
            except subprocess.TimeoutExpired:
                raise RuntimeError(f"Password program timed out for key '{self.password_key}'")
            except FileNotFoundError:
                raise RuntimeError(f"Password program not found: {password_program}")
        return self._password


@dataclass
class ICSSubscription:
    """Configuration for a read-only ICS subscription."""
    name: str
    url: str
    color: str = "#34a853"
    refresh_interval: Optional[int] = None
    outdate_threshold: Optional[int] = None


@dataclass
class LayoutConfig:
    """Configuration for UI layout and fonts."""
    interface_font: str = "Sans"
    interface_font_size: int = 12
    text_font: str = "Sans"
    text_font_size: int = 12
    hour_height: int = 60
    drag_snap_minutes: int = 5


@dataclass
class BindingsConfig:
    """Configuration for keyboard bindings."""
    next: str = "Right"
    prev: str = "Left"
    new_event: str = ""


@dataclass
class ColorsConfig:
    """Configuration for UI colors."""
    day_column_background: str = "#ffffff"
    hour_line: str = "#e8e8e8"
    cell_border: str = "#e0e0e0"
    allday_cell_background: str = "#fafafa"
    current_time_line: str = "#d32f2f"
    header_background: str = "#f5f5f5"
    today_highlight_background: str = "#e3f2fd"
    today_highlight_text: str = "#1976d2"
    month_cell_current: str = "#ffffff"
    month_cell_other: str = "#f5f5f5"
    month_text_current: str = "#000000"
    month_text_other: str = "#999999"
    color_box_border: str = "#999999"
    secondary_text: str = "rgba(0, 0, 0, 0.6)"
    tertiary_text: str = "rgba(0, 0, 0, 0.7)"
    button_save_background: str = "#007bff"
    button_save_text: str = "#ffffff"
    button_delete_background: str = "#dc3545"
    button_delete_text: str = "#ffffff"
    readonly_notice_background: str = "#fff3cd"
    readonly_notice_text: str = "#856404"


@dataclass
class LabelsConfig:
    """Configuration for UI labels."""
    window_title: str = "Kubux Calendar"
    sidebar_header: str = "Calendars"
    view_day: str = "Day"
    view_week: str = "Week"
    view_month: str = "Month"
    view_list: str = "List"
    button_prev: str = "◀"
    button_next: str = "▶"
    button_today: str = "Today"
    button_new_event: str = "New Event"
    button_reload: str = "Reload"
    button_edit_config: str = "Edit Config"
    button_quit: str = "Quit"
    dialog_new_event: str = "New Event"
    dialog_edit_event: str = "Edit: {}"
    field_title: str = "Title:"
    field_calendar: str = "Calendar:"
    field_start: str = "Start:"
    field_end: str = "End:"
    field_location: str = "Location:"
    field_description: str = "Description:"
    checkbox_allday: str = "All-day event"
    button_save: str = "Save"
    button_cancel: str = "Cancel"
    button_delete: str = "Delete"
    recurrence_title: str = "Recurrence"
    recurrence_repeat: str = "Repeat:"
    recurrence_every: str = "Every:"
    recurrence_on_days: str = "On days:"
    recurrence_ends: str = "Ends:"
    recurrence_occurrences: str = "Occurrences:"
    recurrence_until: str = "Until:"
    freq_daily: str = "Daily"
    freq_weekly: str = "Weekly"
    freq_monthly: str = "Monthly"
    freq_yearly: str = "Yearly"
    end_never: str = "Never"
    end_after_count: str = "After N occurrences"
    end_until_date: str = "Until date"
    allday_label: str = "All day"
    no_events: str = "No events"
    location_icon: str = "📍"
    subscription_icon: str = "📡"
    readonly_notice: str = "🔒 This event is read-only (from a subscription)"
    last_sync_label: str = "Last sync:"


@dataclass
class SyncConfig:
    """Configuration for sync queue behavior."""
    initial_interval: int = 10
    max_interval: int = 300
    backoff_multiplier: float = 2.0


@dataclass
class LocalizationConfig:
    """Configuration for localized day and month names."""
    day_names: list[str] = None
    month_names: list[str] = None

    def __post_init__(self):
        if self.day_names is None:
            self.day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        if self.month_names is None:
            self.month_names = [
                "January", "February", "March", "April", "May", "June",
                "July", "August", "September", "October", "November", "December",
            ]

    def get_day_name(self, weekday: int) -> str:
        return self.day_names[weekday] if 0 <= weekday < len(self.day_names) else ""

    def get_month_name(self, month: int) -> str:
        return self.month_names[month - 1] if 1 <= month <= len(self.month_names) else ""


@dataclass
class Config:
    """Main configuration container for Kubux Calendar."""

    password_program: str
    state_file: Path
    timezone: str = "Europe/Amsterdam"
    refresh_interval: int = 300
    outdate_threshold: int = 7200
    layout: LayoutConfig = field(default_factory=LayoutConfig)
    bindings: BindingsConfig = field(default_factory=BindingsConfig)
    localization: LocalizationConfig = field(default_factory=LocalizationConfig)
    colors: ColorsConfig = field(default_factory=ColorsConfig)
    labels: LabelsConfig = field(default_factory=LabelsConfig)
    sync: SyncConfig = field(default_factory=SyncConfig)
    nextcloud_accounts: list[NextcloudAccount] = field(default_factory=list)
    ics_subscriptions: list[ICSSubscription] = field(default_factory=list)

    @classmethod
    def get_default_config_path(cls) -> Path:
        xdg_config = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
        return Path(xdg_config) / "kubux-calendar" / "kubux-calendar.toml"

    @classmethod
    def get_default_state_path(cls) -> Path:
        xdg_state = os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state"))
        return Path(xdg_state) / "kubux-calendar" / "state.json"

    @classmethod
    def load(cls, config_path: Optional[Path] = None) -> "Config":
        """Load configuration from TOML file."""
        if config_path is None:
            config_path = cls.get_default_config_path()

        if not config_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {config_path}")

        with open(config_path, "rb") as f:
            data = tomllib.load(f)

        # General
        general = data.get("General", {})
        password_program = general.get("password_program", "/usr/bin/pass")
        refresh_interval = general.get("refresh_interval", 300)
        outdate_threshold = general.get("outdate_threshold", 7200)
        timezone = general.get("timezone", "Europe/Amsterdam")
        state_file = Path(os.path.expanduser(
            general.get("state_file", str(cls.get_default_state_path()))
        ))

        # Nextcloud accounts
        nextcloud_accounts: list[NextcloudAccount] = []
        for key, value in data.items():
            if key.startswith("Nextcloud.") and isinstance(value, dict):
                account_name = key.split(".", 1)[1]
                nextcloud_accounts.append(NextcloudAccount(
                    name=account_name,
                    url=value.get("url", ""),
                    username=value.get("username", ""),
                    password_key=value.get("password_key", ""),
                    color=value.get("color", "#4285f4"),
                    refresh_interval=value.get("refresh_interval"),
                    outdate_threshold=value.get("outdate_threshold"),
                ))
            elif key == "Nextcloud" and isinstance(value, dict):
                for sub_key, sub_value in value.items():
                    if isinstance(sub_value, dict):
                        nextcloud_accounts.append(NextcloudAccount(
                            name=sub_key,
                            url=sub_value.get("url", ""),
                            username=sub_value.get("username", ""),
                            password_key=sub_value.get("password_key", ""),
                            color=sub_value.get("color", "#4285f4"),
                            refresh_interval=sub_value.get("refresh_interval"),
                            outdate_threshold=sub_value.get("outdate_threshold"),
                        ))

        # ICS subscriptions
        ics_subscriptions: list[ICSSubscription] = []
        for key, value in data.items():
            if key.startswith("Subscription.") and isinstance(value, dict):
                sub_id = key.split(".", 1)[1]
                ics_subscriptions.append(ICSSubscription(
                    name=value.get("name", sub_id),
                    url=value.get("url", ""),
                    color=value.get("color", "#34a853"),
                    refresh_interval=value.get("refresh_interval"),
                    outdate_threshold=value.get("outdate_threshold"),
                ))
            elif key == "Subscription" and isinstance(value, dict):
                for sub_key, sub_value in value.items():
                    if isinstance(sub_value, dict):
                        ics_subscriptions.append(ICSSubscription(
                            name=sub_value.get("name", sub_key),
                            url=sub_value.get("url", ""),
                            color=sub_value.get("color", "#34a853"),
                            refresh_interval=sub_value.get("refresh_interval"),
                            outdate_threshold=sub_value.get("outdate_threshold"),
                        ))

        # Layout
        ld = data.get("Layout", {})
        layout = LayoutConfig(
            interface_font=ld.get("interface_font", "Sans"),
            interface_font_size=ld.get("interface_font_size", 12),
            text_font=ld.get("text_font", "Sans"),
            text_font_size=ld.get("text_font_size", 12),
            hour_height=ld.get("hour_height", 60),
            drag_snap_minutes=ld.get("drag_snap_minutes", 5),
        )

        # Bindings
        bd = data.get("Bindings", {})
        bindings = BindingsConfig(
            next=bd.get("next", "Right"),
            prev=bd.get("prev", "Left"),
            new_event=bd.get("new_event", ""),
        )

        # Localization
        loc_d = data.get("Localization", {})
        day_names_str = loc_d.get("day_names", "")
        month_names_str = loc_d.get("month_names", "")
        localization = LocalizationConfig(
            day_names=day_names_str.split() if day_names_str else None,
            month_names=month_names_str.split() if month_names_str else None,
        )

        # Colors — apply overrides from TOML
        cd = data.get("Colors", {})
        colors_kwargs = {}
        for f in ColorsConfig.__dataclass_fields__:
            if f in cd:
                colors_kwargs[f] = cd[f]
        colors = ColorsConfig(**colors_kwargs)

        # Sync
        sd = data.get("Sync", {})
        sync = SyncConfig(
            initial_interval=sd.get("initial_interval", SyncConfig.initial_interval),
            max_interval=sd.get("max_interval", SyncConfig.max_interval),
            backoff_multiplier=sd.get("backoff_multiplier", SyncConfig.backoff_multiplier),
        )

        # Labels — apply overrides from TOML
        ld2 = data.get("Labels", {})
        labels_kwargs = {}
        for f in LabelsConfig.__dataclass_fields__:
            if f in ld2:
                labels_kwargs[f] = ld2[f]
        labels = LabelsConfig(**labels_kwargs)

        return cls(
            password_program=password_program,
            state_file=state_file,
            timezone=timezone,
            refresh_interval=refresh_interval,
            outdate_threshold=outdate_threshold,
            layout=layout,
            bindings=bindings,
            localization=localization,
            colors=colors,
            labels=labels,
            sync=sync,
            nextcloud_accounts=nextcloud_accounts,
            ics_subscriptions=ics_subscriptions,
        )


# Color palette for auto-assignment
CALENDAR_COLORS = [
    "#4285f4", "#34a853", "#ea4335", "#fbbc05", "#9c27b0",
    "#00bcd4", "#ff5722", "#607d8b", "#e91e63", "#3f51b5",
]


def get_next_color(used_colors: list[str]) -> str:
    """Get the next available color from the palette."""
    for color in CALENDAR_COLORS:
        if color.lower() not in [c.lower() for c in used_colors]:
            return color
    return CALENDAR_COLORS[len(used_colors) % len(CALENDAR_COLORS)]
