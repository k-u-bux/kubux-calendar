"""
Configuration parser for Kubux Calendar.

Handles INI file parsing and secure password retrieval via external programs.
"""

import configparser
import subprocess
import os
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
class Config:
    """Main configuration container for Kubux Calendar."""
    
    password_program: str
    state_file: Path
    nextcloud_accounts: list[NextcloudAccount] = field(default_factory=list)
    ics_subscriptions: list[ICSSubscription] = field(default_factory=list)
    
    @classmethod
    def get_default_config_path(cls) -> Path:
        """Get the default configuration file path."""
        xdg_config = os.environ.get('XDG_CONFIG_HOME', os.path.expanduser('~/.config'))
        return Path(xdg_config) / 'kubux-calendar' / 'kubux-calendar.ini'
    
    @classmethod
    def get_default_state_path(cls) -> Path:
        """Get the default state file path."""
        xdg_state = os.environ.get('XDG_STATE_HOME', os.path.expanduser('~/.local/state'))
        return Path(xdg_state) / 'kubux-calendar' / 'state.json'
    
    @classmethod
    def load(cls, config_path: Optional[Path] = None) -> 'Config':
        """Load configuration from INI file."""
        if config_path is None:
            config_path = cls.get_default_config_path()
        
        if not config_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {config_path}")
        
        parser = configparser.ConfigParser()
        parser.read(config_path)
        
        # Parse General section
        general = parser['General'] if 'General' in parser else {}
        password_program = general.get('password_program', '/usr/bin/pass').strip('"\'')
        
        state_file_str = general.get('state_file', str(cls.get_default_state_path()))
        state_file = Path(os.path.expanduser(state_file_str.strip('"\'')))
        
        # Parse Nextcloud accounts
        nextcloud_accounts = []
        for section in parser.sections():
            if section.startswith('Nextcloud.'):
                account_name = section.split('.', 1)[1]
                nc_config = parser[section]
                
                account = NextcloudAccount(
                    name=account_name,
                    url=nc_config.get('url', '').strip('"\''),
                    username=nc_config.get('username', '').strip('"\''),
                    password_key=nc_config.get('password_key', '').strip('"\''),
                    color=nc_config.get('color', '#4285f4').strip('"\'')
                )
                nextcloud_accounts.append(account)
        
        # Parse ICS subscriptions
        ics_subscriptions = []
        for section in parser.sections():
            if section.startswith('Subscription.'):
                sub_id = section.split('.', 1)[1]
                sub_config = parser[section]
                
                subscription = ICSSubscription(
                    name=sub_config.get('name', sub_id).strip('"\''),
                    url=sub_config.get('url', '').strip('"\''),
                    color=sub_config.get('color', '#34a853').strip('"\'')
                )
                ics_subscriptions.append(subscription)
        
        return cls(
            password_program=password_program,
            state_file=state_file,
            nextcloud_accounts=nextcloud_accounts,
            ics_subscriptions=ics_subscriptions
        )
    
    def save(self, config_path: Optional[Path] = None) -> None:
        """Save configuration to INI file."""
        if config_path is None:
            config_path = self.get_default_config_path()
        
        # Ensure directory exists
        config_path.parent.mkdir(parents=True, exist_ok=True)
        
        parser = configparser.ConfigParser()
        
        # General section
        parser['General'] = {
            'password_program': self.password_program,
            'state_file': str(self.state_file)
        }
        
        # Nextcloud accounts
        for account in self.nextcloud_accounts:
            section = f'Nextcloud.{account.name}'
            parser[section] = {
                'url': account.url,
                'username': account.username,
                'password_key': account.password_key,
                'color': account.color
            }
        
        # ICS subscriptions
        for i, sub in enumerate(self.ics_subscriptions):
            # Create a valid section name from the subscription name
            section_name = sub.name.replace(' ', '_').replace('.', '_')
            section = f'Subscription.{section_name}'
            parser[section] = {
                'url': sub.url,
                'name': sub.name,
                'color': sub.color
            }
        
        with open(config_path, 'w') as f:
            parser.write(f)


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
