# Kubux Calendar

A lightweight PySide6 desktop calendar application for Nextcloud (CalDAV) and ICS subscriptions.

![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)

## Features

- **CalDAV Integration**: Full read/write support for Nextcloud calendars
- **ICS Subscriptions**: Read-only support for external ICS calendar feeds
- **Multiple Views**: Day, Week, and Month views
- **Event Management**: Create, edit, and delete events with recurrence support
- **Calendar Visibility**: Toggle individual calendars on/off
- **Custom Colors**: Assign custom colors to each calendar
- **All-Day Events**: Full support for all-day and multi-day events
- **Recurring Events**: Display and manage recurring events
- **Password Integration**: Secure password retrieval via external programs (e.g., `pass`)
- **Keyboard Navigation**: Configurable keyboard shortcuts
- **Event Caching**: Pre-fetches ±2 months of events for fast navigation
- **Live Config Reload**: Automatically reloads when the config file changes (no restart needed)
- **Persistent UI State**: Remembers window size, sidebar width, view, and scroll position

## Screenshot

The application features a clean, modern interface with a sidebar for calendar selection and a main area for event display.

## Installation

### NixOS / Nix

This project uses Nix flakes for reproducible builds:

```bash
# Build the package
nix build

# Run directly
nix run

# Enter development shell
nix develop
```

### Manual Installation

Ensure you have Python 3.12+ with the following dependencies:

- PySide6
- caldav
- ics
- icalendar
- pytz
- python-dateutil
- requests

```bash
python kubux_calendar.py
```

## Configuration

The configuration file is located at:
- `~/.config/kubux-calendar/kubux-calendar.toml` (TOML format)

### Example Configuration (TOML)

```toml
[General]
password_program = "/usr/bin/pass"

[Layout]
hour_height = 60
text_font = "Sans"
text_font_size = 10

[Bindings]
next = "l"
prev = "h"

[Nextcloud.Primary]
url = "https://nextcloud.example.com"
username = "your_username"
password_key = "nextcloud/password"
color = "#4285f4"

[Subscription.Holidays]
url = "https://example.com/holidays.ics"
name = "Public Holidays"
color = "#ff6b6b"
```

### Configuration Options

#### General Section

| Option | Description |
|--------|-------------|
| `password_program` | Path to password manager (e.g., `/usr/bin/pass`) |

#### Layout Section

| Option | Default | Description |
|--------|---------|-------------|
| `hour_height` | 60 | Height of one hour in pixels (day/week view) |
| `text_font` | Sans | Font family for event text |
| `text_font_size` | 10 | Font size for event text |

#### Bindings Section

| Option | Default | Description |
|--------|---------|-------------|
| `next` | None | Key to navigate forward (day/week/month) |
| `prev` | None | Key to navigate backward |

#### Nextcloud Accounts

Each Nextcloud account is defined as `[Nextcloud.AccountName]`:

| Option | Description |
|--------|-------------|
| `url` | Nextcloud server URL |
| `username` | Your Nextcloud username |
| `password_key` | Key passed to `password_program` to retrieve password |
| `color` | Default hex color for calendars (optional) |

#### ICS Subscriptions

Each subscription is defined as `[Subscription.Name]`:

| Option | Description |
|--------|-------------|
| `url` | URL to the ICS file |
| `name` | Display name for the calendar |
| `color` | Hex color for events (e.g., `#4285f4`) |

## Usage

### Navigation

- **Previous/Next**: Use toolbar buttons or configured keyboard shortcuts
- **Today**: Jump to current date
- **View Switching**: Day / Week / Month buttons in toolbar

### Events

- **View Event**: Single-click on an event
- **Edit Event**: Double-click on an event
- **Create Event**: Double-click on empty time slot

### Visual Indicators

Events display small triangle indicators in the corners:
- **Bottom-left triangle**: Recurring event
- **Bottom-right triangle**: Read-only event (from ICS subscription)

### Sidebar

- Toggle calendar visibility with checkboxes
- Right-click calendar name to change color
- Calendars from Nextcloud are editable; ICS subscriptions are read-only

## Architecture

The application follows a modular architecture:

```
kubux-calendar/
├── kubux_calendar.py    # Main entry point
├── backend/
│   ├── caldav_client.py # CalDAV/Nextcloud communication
│   ├── ics_subscription.py # ICS feed handling
│   ├── event_store.py   # Unified event cache & storage
│   └── config.py        # Configuration management
└── gui/
    ├── main_window.py   # Main application window
    ├── event_dialog.py  # Event create/edit dialog
    └── widgets/
        ├── calendar_widget.py # Day/Week/Month views
        └── event_widget.py    # Event display widget
```

## State Storage

Application state is stored in `~/.local/state/kubux-calendar/state.json`:
- Window geometry and position
- Sidebar width (splitter position)
- Current view type and date
- Scroll position
- Calendar visibility and colors
- Last used calendar for new events

## License

This project is licensed under the Apache License 2.0 - see the [LICENSE](LICENSE) file for details.

## Acknowledgments

This application was vibe coded using Claude Opus 4.
