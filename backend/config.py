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
    color: str = "#4285f4"  # Default Google Blue
    
    _password: Optional[str] = field(default=None, repr=False)
    
    def get_password(self, password_program: str) -> str:
        """Retrieve password using the configured password program."""
        if self._password is None:
            try:
                result = subprocess.run(
                    [password_program, self.password_key],
                    capture_output=True,
                    text=True,
                    timeout=30
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
    color: str = "#34a853"  # Default Google Green


@dataclass
class LayoutConfig:
    """Configuration for UI layout and fonts."""
    interface_font: str = "Sans"
    interface_font_size: int = 12
    text_font: str = "Sans"
    text_font_size: int = 12
    hour_height: int = 60  # Height of an hour slot in day/week view in pixels


@dataclass
class BindingsConfig:
    """Configuration for keyboard bindings."""
    next: str = "Right"  # Key to go to next period
    prev: str = "Left"   # Key to go to previous period
    new_event: str = ""  # Key to create a new event


@dataclass
class ColorsConfig:
    """Configuration for UI colors."""
    # Calendar/Grid Colors
    day_column_background: str = "#ffffff"
    hour_line: str = "#e8e8e8"
    cell_border: str = "#e0e0e0"
    allday_cell_background: str = "#fafafa"
    current_time_line: str = "#d32f2f"      # Red line indicating current time
    
    # Header/Navigation Colors
    header_background: str = "#f5f5f5"
    today_highlight_background: str = "#e3f2fd"
    today_highlight_text: str = "#1976d2"
    
    # Month View Colors
    month_cell_current: str = "#ffffff"
    month_cell_other: str = "#f5f5f5"
    month_text_current: str = "#000000"
    month_text_other: str = "#999999"
    
    # UI Element Colors
    color_box_border: str = "#999999"
    secondary_text: str = "rgba(0, 0, 0, 0.6)"
    tertiary_text: str = "rgba(0, 0, 0, 0.7)"
    
    # Button Colors (Event Dialog)
    button_save_background: str = "#007bff"
    button_save_text: str = "#ffffff"
    button_delete_background: str = "#dc3545"
    button_delete_text: str = "#ffffff"
    
    # Notice/Alert Colors
    readonly_notice_background: str = "#fff3cd"
    readonly_notice_text: str = "#856404"


@dataclass
class LabelsConfig:
    """Configuration for UI labels."""
    # Main Window Labels
    window_title: str = "Kubux Calendar"
    sidebar_header: str = "Calendars"
    
    # View Switcher Labels
    view_day: str = "Day"
    view_week: str = "Week"
    view_month: str = "Month"
    view_list: str = "List"
    
    # Toolbar Button Labels
    button_prev: str = "◀"
    button_next: str = "▶"
    button_today: str = "Today"
    button_new_event: str = "New Event"
    button_reload: str = "Reload"
    button_edit_config: str = "Edit Config"
    button_quit: str = "Quit"
    
    # Event Dialog Labels
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
    
    # Recurrence Labels
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
    
    # Miscellaneous Labels
    allday_label: str = "All day"
    no_events: str = "No events"
    location_icon: str = "📍"
    subscription_icon: str = "📡"
    readonly_notice: str = "🔒 This event is read-only (from a subscription)"


@dataclass
class SyncConfig:
    """Configuration for sync queue behavior."""
    initial_interval: int = 10      # Initial sync retry interval in seconds
    max_interval: int = 300         # Maximum sync retry interval in seconds (5 min)
    backoff_multiplier: float = 2.0 # Multiplier for exponential backoff


@dataclass
class LocalizationConfig:
    """Configuration for localized day and month names."""
    # Default to English abbreviated day names
    day_names: list[str] = None  # Mon Tue Wed Thu Fri Sat Sun
    # Default to English full month names
    month_names: list[str] = None  # January February ... December
    
    def __post_init__(self):
        if self.day_names is None:
            self.day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        if self.month_names is None:
            self.month_names = [
                "January", "February", "March", "April", "May", "June",
                "July", "August", "September", "October", "November", "December"
            ]
    
    def get_day_name(self, weekday: int) -> str:
        """Get localized day name for weekday (0=Monday, 6=Sunday)."""
        return self.day_names[weekday] if 0 <= weekday < len(self.day_names) else ""
    
    def get_month_name(self, month: int) -> str:
        """Get localized month name (1=January, 12=December)."""
        return self.month_names[month - 1] if 1 <= month <= len(self.month_names) else ""


@dataclass
class Config:
    """Main configuration container for Kubux Calendar."""
    
    password_program: str
    state_file: Path
    refresh_interval: int = 300  # Auto-refresh interval in seconds (0 to disable)
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
        """Get the default configuration file path."""
        xdg_config = os.environ.get('XDG_CONFIG_HOME', os.path.expanduser('~/.config'))
        return Path(xdg_config) / 'kubux-calendar' / 'kubux-calendar.toml'
    
    @classmethod
    def get_default_state_path(cls) -> Path:
        """Get the default state file path."""
        xdg_state = os.environ.get('XDG_STATE_HOME', os.path.expanduser('~/.local/state'))
        return Path(xdg_state) / 'kubux-calendar' / 'state.json'
    
    @classmethod
    def load(cls, config_path: Optional[Path] = None) -> 'Config':
        """Load configuration from TOML file."""
        if config_path is None:
            config_path = cls.get_default_config_path()
        
        if not config_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {config_path}")
        
        with open(config_path, 'rb') as f:
            data = tomllib.load(f)
        
        # Parse General section
        general = data.get('General', {})
        password_program = general.get('password_program', '/usr/bin/pass')
        refresh_interval = general.get('refresh_interval', 300)  # Default 5 minutes
        
        state_file_str = general.get('state_file', str(cls.get_default_state_path()))
        state_file = Path(os.path.expanduser(state_file_str))
        
        # Parse Nextcloud accounts
        # Supports both [Nextcloud.AccountName] and [Nextcloud] with nested accounts
        nextcloud_accounts = []
        print(f"DEBUG: TOML data keys: {list(data.keys())}", file=sys.stderr)
        for key, value in data.items():
            print(f"DEBUG: Checking key '{key}' (type={type(value).__name__})", file=sys.stderr)
            
            # Format 1: [Nextcloud.AccountName]
            if key.startswith('Nextcloud.') and isinstance(value, dict):
                account_name = key.split('.', 1)[1]
                print(f"DEBUG: Found Nextcloud account (dot format): {account_name}", file=sys.stderr)
                print(f"DEBUG:   url={value.get('url', '')}", file=sys.stderr)
                print(f"DEBUG:   username={value.get('username', '')}", file=sys.stderr)
                print(f"DEBUG:   password_key={value.get('password_key', '')}", file=sys.stderr)
                account = NextcloudAccount(
                    name=account_name,
                    url=value.get('url', ''),
                    username=value.get('username', ''),
                    password_key=value.get('password_key', ''),
                    color=value.get('color', '#4285f4')
                )
                nextcloud_accounts.append(account)
            
            # Format 2: [Nextcloud] with nested [Nextcloud.AccountName] sub-tables
            elif key == 'Nextcloud' and isinstance(value, dict):
                print(f"DEBUG: Found Nextcloud root section, checking sub-accounts...", file=sys.stderr)
                print(f"DEBUG:   Sub-keys: {list(value.keys())}", file=sys.stderr)
                for sub_key, sub_value in value.items():
                    print(f"DEBUG:   Sub-key '{sub_key}' (type={type(sub_value).__name__})", file=sys.stderr)
                    if isinstance(sub_value, dict):
                        account_name = sub_key
                        print(f"DEBUG: Found Nextcloud account (nested format): {account_name}", file=sys.stderr)
                        print(f"DEBUG:     url={sub_value.get('url', '')}", file=sys.stderr)
                        print(f"DEBUG:     username={sub_value.get('username', '')}", file=sys.stderr)
                        print(f"DEBUG:     password_key={sub_value.get('password_key', '')}", file=sys.stderr)
                        account = NextcloudAccount(
                            name=account_name,
                            url=sub_value.get('url', ''),
                            username=sub_value.get('username', ''),
                            password_key=sub_value.get('password_key', ''),
                            color=sub_value.get('color', '#4285f4')
                        )
                        nextcloud_accounts.append(account)
        
        print(f"DEBUG: Total Nextcloud accounts found: {len(nextcloud_accounts)}", file=sys.stderr)
        
        # Parse ICS subscriptions
        # Supports both [Subscription.Name] and [Subscription] with nested sub-tables
        ics_subscriptions = []
        for key, value in data.items():
            # Format 1: [Subscription.Name]
            if key.startswith('Subscription.') and isinstance(value, dict):
                sub_id = key.split('.', 1)[1]
                print(f"DEBUG: Found ICS subscription (dot format): {sub_id}", file=sys.stderr)
                subscription = ICSSubscription(
                    name=value.get('name', sub_id),
                    url=value.get('url', ''),
                    color=value.get('color', '#34a853')
                )
                ics_subscriptions.append(subscription)
            
            # Format 2: [Subscription] with nested [Subscription.Name] sub-tables
            elif key == 'Subscription' and isinstance(value, dict):
                print(f"DEBUG: Found Subscription root section, checking sub-subscriptions...", file=sys.stderr)
                print(f"DEBUG:   Sub-keys: {list(value.keys())}", file=sys.stderr)
                for sub_key, sub_value in value.items():
                    if isinstance(sub_value, dict):
                        sub_id = sub_key
                        print(f"DEBUG: Found ICS subscription (nested format): {sub_id}", file=sys.stderr)
                        print(f"DEBUG:     url={sub_value.get('url', '')}", file=sys.stderr)
                        print(f"DEBUG:     name={sub_value.get('name', sub_id)}", file=sys.stderr)
                        subscription = ICSSubscription(
                            name=sub_value.get('name', sub_id),
                            url=sub_value.get('url', ''),
                            color=sub_value.get('color', '#34a853')
                        )
                        ics_subscriptions.append(subscription)
        
        print(f"DEBUG: Total ICS subscriptions found: {len(ics_subscriptions)}", file=sys.stderr)
        
        # Parse Layout section
        layout_data = data.get('Layout', {})
        layout = LayoutConfig(
            interface_font=layout_data.get('interface_font', 'Sans'),
            interface_font_size=layout_data.get('interface_font_size', 12),
            text_font=layout_data.get('text_font', 'Sans'),
            text_font_size=layout_data.get('text_font_size', 12),
            hour_height=layout_data.get('hour_height', 60)
        )
        
        # Parse Bindings section
        bindings_data = data.get('Bindings', {})
        bindings = BindingsConfig(
            next=bindings_data.get('next', 'Right'),
            prev=bindings_data.get('prev', 'Left'),
            new_event=bindings_data.get('new_event', '')
        )
        
        # Parse Localization section
        localization_data = data.get('Localization', {})
        day_names_str = localization_data.get('day_names', '')
        month_names_str = localization_data.get('month_names', '')
        
        # Parse space-separated day names (if provided)
        day_names = day_names_str.split() if day_names_str else None
        # Parse space-separated month names (if provided)
        month_names = month_names_str.split() if month_names_str else None
        
        localization = LocalizationConfig(
            day_names=day_names,
            month_names=month_names
        )
        
        # Parse Colors section
        colors_data = data.get('Colors', {})
        colors = ColorsConfig(
            day_column_background=colors_data.get('day_column_background', ColorsConfig.day_column_background),
            hour_line=colors_data.get('hour_line', ColorsConfig.hour_line),
            cell_border=colors_data.get('cell_border', ColorsConfig.cell_border),
            allday_cell_background=colors_data.get('allday_cell_background', ColorsConfig.allday_cell_background),
            current_time_line=colors_data.get('current_time_line', ColorsConfig.current_time_line),
            header_background=colors_data.get('header_background', ColorsConfig.header_background),
            today_highlight_background=colors_data.get('today_highlight_background', ColorsConfig.today_highlight_background),
            today_highlight_text=colors_data.get('today_highlight_text', ColorsConfig.today_highlight_text),
            month_cell_current=colors_data.get('month_cell_current', ColorsConfig.month_cell_current),
            month_cell_other=colors_data.get('month_cell_other', ColorsConfig.month_cell_other),
            month_text_current=colors_data.get('month_text_current', ColorsConfig.month_text_current),
            month_text_other=colors_data.get('month_text_other', ColorsConfig.month_text_other),
            color_box_border=colors_data.get('color_box_border', ColorsConfig.color_box_border),
            secondary_text=colors_data.get('secondary_text', ColorsConfig.secondary_text),
            tertiary_text=colors_data.get('tertiary_text', ColorsConfig.tertiary_text),
            button_save_background=colors_data.get('button_save_background', ColorsConfig.button_save_background),
            button_save_text=colors_data.get('button_save_text', ColorsConfig.button_save_text),
            button_delete_background=colors_data.get('button_delete_background', ColorsConfig.button_delete_background),
            button_delete_text=colors_data.get('button_delete_text', ColorsConfig.button_delete_text),
            readonly_notice_background=colors_data.get('readonly_notice_background', ColorsConfig.readonly_notice_background),
            readonly_notice_text=colors_data.get('readonly_notice_text', ColorsConfig.readonly_notice_text),
        )
        
        # Parse Sync section
        sync_data = data.get('Sync', {})
        sync = SyncConfig(
            initial_interval=sync_data.get('initial_interval', SyncConfig.initial_interval),
            max_interval=sync_data.get('max_interval', SyncConfig.max_interval),
            backoff_multiplier=sync_data.get('backoff_multiplier', SyncConfig.backoff_multiplier),
        )
        
        # Parse Labels section
        labels_data = data.get('Labels', {})
        labels = LabelsConfig(
            window_title=labels_data.get('window_title', LabelsConfig.window_title),
            sidebar_header=labels_data.get('sidebar_header', LabelsConfig.sidebar_header),
            view_day=labels_data.get('view_day', LabelsConfig.view_day),
            view_week=labels_data.get('view_week', LabelsConfig.view_week),
            view_month=labels_data.get('view_month', LabelsConfig.view_month),
            view_list=labels_data.get('view_list', LabelsConfig.view_list),
            button_prev=labels_data.get('button_prev', LabelsConfig.button_prev),
            button_next=labels_data.get('button_next', LabelsConfig.button_next),
            button_today=labels_data.get('button_today', LabelsConfig.button_today),
            button_new_event=labels_data.get('button_new_event', LabelsConfig.button_new_event),
            button_reload=labels_data.get('button_reload', LabelsConfig.button_reload),
            button_edit_config=labels_data.get('button_edit_config', LabelsConfig.button_edit_config),
            button_quit=labels_data.get('button_quit', LabelsConfig.button_quit),
            dialog_new_event=labels_data.get('dialog_new_event', LabelsConfig.dialog_new_event),
            dialog_edit_event=labels_data.get('dialog_edit_event', LabelsConfig.dialog_edit_event),
            field_title=labels_data.get('field_title', LabelsConfig.field_title),
            field_calendar=labels_data.get('field_calendar', LabelsConfig.field_calendar),
            field_start=labels_data.get('field_start', LabelsConfig.field_start),
            field_end=labels_data.get('field_end', LabelsConfig.field_end),
            field_location=labels_data.get('field_location', LabelsConfig.field_location),
            field_description=labels_data.get('field_description', LabelsConfig.field_description),
            checkbox_allday=labels_data.get('checkbox_allday', LabelsConfig.checkbox_allday),
            button_save=labels_data.get('button_save', LabelsConfig.button_save),
            button_cancel=labels_data.get('button_cancel', LabelsConfig.button_cancel),
            button_delete=labels_data.get('button_delete', LabelsConfig.button_delete),
            recurrence_title=labels_data.get('recurrence_title', LabelsConfig.recurrence_title),
            recurrence_repeat=labels_data.get('recurrence_repeat', LabelsConfig.recurrence_repeat),
            recurrence_every=labels_data.get('recurrence_every', LabelsConfig.recurrence_every),
            recurrence_on_days=labels_data.get('recurrence_on_days', LabelsConfig.recurrence_on_days),
            recurrence_ends=labels_data.get('recurrence_ends', LabelsConfig.recurrence_ends),
            recurrence_occurrences=labels_data.get('recurrence_occurrences', LabelsConfig.recurrence_occurrences),
            recurrence_until=labels_data.get('recurrence_until', LabelsConfig.recurrence_until),
            freq_daily=labels_data.get('freq_daily', LabelsConfig.freq_daily),
            freq_weekly=labels_data.get('freq_weekly', LabelsConfig.freq_weekly),
            freq_monthly=labels_data.get('freq_monthly', LabelsConfig.freq_monthly),
            freq_yearly=labels_data.get('freq_yearly', LabelsConfig.freq_yearly),
            end_never=labels_data.get('end_never', LabelsConfig.end_never),
            end_after_count=labels_data.get('end_after_count', LabelsConfig.end_after_count),
            end_until_date=labels_data.get('end_until_date', LabelsConfig.end_until_date),
            allday_label=labels_data.get('allday_label', LabelsConfig.allday_label),
            no_events=labels_data.get('no_events', LabelsConfig.no_events),
            location_icon=labels_data.get('location_icon', LabelsConfig.location_icon),
            subscription_icon=labels_data.get('subscription_icon', LabelsConfig.subscription_icon),
            readonly_notice=labels_data.get('readonly_notice', LabelsConfig.readonly_notice),
        )
        
        return cls(
            password_program=password_program,
            state_file=state_file,
            refresh_interval=refresh_interval,
            layout=layout,
            bindings=bindings,
            localization=localization,
            colors=colors,
            labels=labels,
            sync=sync,
            nextcloud_accounts=nextcloud_accounts,
            ics_subscriptions=ics_subscriptions
        )


# Colors palette for auto-assignment to calendars
CALENDAR_COLORS = [
    '#4285f4',  # Blue
    '#34a853',  # Green
    '#ea4335',  # Red
    '#fbbc05',  # Yellow
    '#9c27b0',  # Purple
    '#00bcd4',  # Cyan
    '#ff5722',  # Deep Orange
    '#607d8b',  # Blue Grey
    '#e91e63',  # Pink
    '#3f51b5',  # Indigo
]


def get_next_color(used_colors: list[str]) -> str:
    """Get the next available color from the palette."""
    for color in CALENDAR_COLORS:
        if color.lower() not in [c.lower() for c in used_colors]:
            return color
    # If all colors are used, cycle back
    return CALENDAR_COLORS[len(used_colors) % len(CALENDAR_COLORS)]
