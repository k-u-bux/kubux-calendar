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


@dataclass
class Config:
    """Main configuration container for Kubux Calendar."""
    
    password_program: str
    state_file: Path
    refresh_interval: int = 300  # Auto-refresh interval in seconds (0 to disable)
    layout: LayoutConfig = field(default_factory=LayoutConfig)
    bindings: BindingsConfig = field(default_factory=BindingsConfig)
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
            prev=bindings_data.get('prev', 'Left')
        )
        
        return cls(
            password_program=password_program,
            state_file=state_file,
            refresh_interval=refresh_interval,
            layout=layout,
            bindings=bindings,
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
