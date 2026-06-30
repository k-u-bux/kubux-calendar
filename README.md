# Kubux Calendar

A simple desktop calendar application for Nextcloud (CalDAV) and ICS subscriptions.

![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)

## Features

- **Explicit Sync Status**: **Always know if your local view matches the server state**
  - Visual indicators show pending changes at a glance (triangle in event corners)
  - Status bar displays last sync time and count of pending operations
  - Never wonder if your changes have been saved to the server
  - Unlike many calendar apps that hide sync state, this app is completely transparent
- **Offline-First**: Work instantly, sync in background
  - **Persistent storage**: Events survive app restarts - no need to re-fetch from server
  - **Works offline**: When server is unavailable, cached events are displayed (with outdated indicator)
  - Create, edit, and delete events immediately (no waiting for server)
  - Changes are queued and synced automatically with exponential backoff
  - Pending changes persist across app restarts
- **CalDAV Integration**: Full read/write support for Nextcloud calendars
- **ICS Subscriptions**: Read-only support for external ICS calendar feeds
- **Multiple Views**: Day, Week, Month, and List views
- **Event Management**: Create, edit, and delete events with recurrence support
- **Calendar Visibility**: Toggle individual calendars on/off
- **Custom Colors**: Assign custom colors to each calendar
- **All-Day Events**: Full support for all-day and multi-day events
- **Recurring Events**: Display and manage recurring events
- **Current Time Indicator**: Red line showing current time in day/week views
- **Auto-Refresh**: Configurable automatic refresh from server
- **Localization**: Customize day and month names for any language
- **Password Integration**: Secure password retrieval via external programs (e.g., `pass`)
- **Keyboard Navigation**: Configurable keyboard shortcuts
- **Event Caching**: Pre-fetches ±4 months past and ±8 months future for fast navigation
- **Live Config Reload**: Automatically reloads when the config file changes (no restart needed). Events and sync state are preserved — no re-fetch from server unless sources are added or removed.
- **Persistent UI State**: Remembers window size, sidebar width, view, and scroll position
- **No Dependencies**: Stand alone account management independent of desktop environment

## Sync Status Transparency

**A key differentiator:** Unlike many calendar applications that leave you wondering whether your local view accurately reflects the server state, Kubux Calendar is **completely transparent** about synchronization.

### You Always Know:
- **What's synced**: Events without indicators are confirmed on the server
- **What's pending**: Events with a black triangle (top-right) are queued for sync
- **When it synced**: Status bar shows "Last sync at HH:MM" 
- **What's queuing**: Status bar shows "N changes pending synchronization"

### Why This Matters:
Most calendar apps hide their sync state, leaving you to guess:
- "Did my edit save?"
- "Is this event really deleted?"
- "Am I looking at stale data?"

Kubux Calendar eliminates this uncertainty. The application is designed on the principle that **you should never have to wonder** if your local view matches reality.

### How It Works:
1. **Immediate feedback**: Create/delete operations complete instantly (offline-first)
2. **Visual queuing**: Pending changes are marked with a black triangle indicator
3. **Background sync**: Queued changes sync to server automatically with exponential backoff
4. **Status reporting**: Status bar shows pending count ("N changes pending synchronization") and last sync time
5. **Persistent queue**: Pending changes survive app restarts

**Operation types:**
- **Create**: Queued for background sync, shows in status bar
- **Edit (full dialog)**: Update is sent immediately to server
- **Edit (drag-drop)**: Update is sent immediately to server
- **Delete**: Queued for background sync, shows in status bar

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
- icalendar
- pytz
- recurring-ical-events
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
new_event = "+"

[Localization]
day_names = "Mo Di Mi Do Fr Sa So"
month_names = "Januar Februar März April Mai Juni Juli August September Oktober November Dezember"
first_day_of_week = 0  # 0=Monday, 6=Sunday

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

| Option | Default | Description |
|--------|---------|-------------|
| `password_program` | `/usr/bin/pass` | Path to password manager |
| `refresh_interval` | 300 | Auto-refresh from server (seconds, 0 to disable) |
| `outdate_threshold` | 7200 | Seconds since last successful sync before marking events as "unconfirmed" (default 2 hours) |
| `state_file` | `~/.local/state/kubux-calendar/state.json` | Path to state file |
| `log_level` | warn | Log threshold: debug, info, warn, error, silent |
| `timezone` | (system) | IANA timezone name (e.g. `Europe/Berlin`) |

#### Layout Section

| Option | Default | Description |
|--------|---------|-------------|
| `hour_height` | 60 | Height of one hour in pixels (day/week view) |
| `interface_font` | Sans | Font family for interface elements |
| `interface_font_size` | 12 | Font size for interface elements |
| `text_font` | Sans | Font family for event text |
| `text_font_size` | 12 | Font size for event text |

#### Bindings Section

| Option | Default | Description |
|--------|---------|-------------|
| `next` | Right | Key to navigate forward (day/week/month) |
| `prev` | Left | Key to navigate backward |
| `new_event` | (none) | Key to create a new event |

#### Localization Section

Customize day and month names for your language. If omitted, English defaults are used.

| Option | Default | Description |
|--------|---------|-------------|
| `day_names` | "Mon Tue Wed Thu Fri Sat Sun" | Space-separated abbreviated day names (Monday=first) |
| `first_day_of_week` | 0 | First day of week: 0=Monday through 6=Sunday |
| `month_names` | "January February ... December" | Space-separated full month names |

Example for German:
```toml
[Localization]
day_names = "Mo Di Mi Do Fr Sa So"
month_names = "Januar Februar März April Mai Juni Juli August September Oktober November Dezember"
first_day_of_week = 0  # 0=Monday, 6=Sunday
```

#### Nextcloud Accounts

Each Nextcloud account is defined as `[Nextcloud.AccountName]`:

| Option | Description |
|--------|-------------|
| `url` | Nextcloud server URL |
| `username` | Your Nextcloud username |
| `password_key` | Key passed to `password_program` to retrieve password |
| `color` | Default hex color for calendars (optional) |

#### Sync Section

Configure offline sync behavior with exponential backoff:

| Option | Default | Description |
|--------|---------|-------------|
| `initial_interval` | 10 | Initial sync retry interval (seconds) |
| `max_interval` | 300 | Maximum sync retry interval (seconds) |
| `backoff_multiplier` | 2.0 | Multiplier applied on each failed sync |

Example: With defaults, retries occur at 10s, 20s, 40s, 80s, 160s, 300s (capped). Resets to 10s on success.

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
- **View Switching**: Day / Week / Month / List in toolbar dropdown

### List View

The List view displays all events in a chronological scrollable list:
- Shows full event details: date, time, title, location, description, and calendar name
- **Previous/Next**: Scroll backward/forward by one page
- **Today**: Scrolls to position the next upcoming event at the top

### Events

- **View Event**: Single-click on an event
- **Edit Event**: Double-click on an event
- **Create Event**: Double-click on empty time slot

### Visual Indicators

**Transparency at a glance:** Events display small indicators to show their status:

- **Top-right triangle (black)**: **Pending sync** - This event has changes queued but not yet confirmed on the server
  - Create/edit/delete operations show this indicator immediately
  - Disappears once the server confirms the change
  - **This is your guarantee**: If you see this triangle, the change is queued and will sync
- **Top-right square (black)**: **Unconfirmed/Outdated** - This event hasn't been verified with the server recently
  - Appears when the source hasn't successfully synced within `outdate_threshold` seconds
  - Common when server is unavailable or you're working offline
  - Events still display from persistent cache, but their server state is unverified
- **Bottom-left triangle**: **Recurring event** - Part of a repeating series
- **Bottom-right triangle**: **Read-only** - Event from ICS subscription (cannot be edited)

**No indicator = Fully synced.** If an event has no top-right square or top-right triangle, you can be certain it accurately reflects the server state.

### Sidebar

- Toggle calendar visibility with checkboxes
- Click the color box next to a calendar name to change its color
- Calendars from Nextcloud are editable; ICS subscriptions are read-only

## Architecture

The application follows a modular architecture:

```
kubux-calendar/
├── kubux_calendar.py      # Main entry point
├── backend/
│   ├── config.py          # Configuration management
│   ├── event.py           # Core event types (ImmutableEvent, EventView, CalendarSource)
│   ├── event_fs.py        # Filesystem-based event cache
│   ├── event_index.py     # In-memory interval-tree index
│   ├── event_store.py     # Unified facade over FS + index + sync
│   ├── interval_tree.py   # AVL-based interval tree
│   ├── network_ops.py     # CalDAV and ICS network operations
│   └── sync_manager.py    # Background sync orchestration
├── gui/
│   ├── main_window.py     # Main application window
│   ├── event_dialog.py    # Event create/edit dialog
│   └── widgets/
│       ├── calendar_widget.py # Day/Week/Month/List views
│       └── event_widget.py    # Event display widget
├── library/
│   ├── color_utils.py     # Color generation utilities
│   ├── log.py             # Level-filtered logging
│   ├── task_dispatch.py   # Thread-pool task dispatcher
│   └── timezone_utils.py  # Timezone conversion utilities
└── tests/
    ├── test_interval_tree.py   # AVL tree insert/delete/query/integrity
    ├── test_event_parsing.py   # iCalendar parsing, ImmutableEvent, EventView
    ├── test_color_utils.py     # Color generation
    ├── test_timezone_utils.py  # Timezone conversion
    ├── test_log.py             # Log level filtering
    ├── test_event_fs.py        # Filesystem cache (save/load/pending)
    ├── test_event_index.py     # In-memory index queries
    ├── test_config.py          # TOML config parsing
    ├── test_task_dispatch.py   # Background task execution
    ├── test_network_ops.py     # ICS parsing, HTTP fetch (mocked)
    ├── test_sync_manager.py    # Sync orchestration logic
    ├── test_event_store.py     # CRUD, visibility, state persistence
    └── test_gui_logic.py       # Color math, event portions, overlap detection
```

## Testing

Run the test suite with pytest (requires the Nix development shell):

```bash
nix develop --command python -m pytest tests/ -v
```

197 tests across 13 test files:

| File | Tests | What it covers |
|---|---|---|
| `test_interval_tree.py` | 16 | AVL tree insert/delete, all 4 query types, integrity verification |
| `test_event_parsing.py` | 22 | iCalendar parsing, ImmutableEvent, EventView, _rebuild_ical |
| `test_color_utils.py` | 6 | Color-to-hue conversion, unused color selection |
| `test_timezone_utils.py` | 11 | Timezone-aware conversion, naive→aware, UTC→local |
| `test_log.py` | 5 | Log level filtering, stderr output |
| `test_event_fs.py` | 15 | Filesystem cache CRUD, source metadata, pending ops |
| `test_event_index.py` | 12 | In-memory index add/remove/query, recurring inclusion |
| `test_config.py` | 17 | TOML parsing, all sections, both config formats |
| `test_task_dispatch.py` | 12 | Background task execution, wait/cancel, thunk/tie |
| `test_network_ops.py` | 9 | ICS event parsing, HTTP fetch with mocks |
| `test_sync_manager.py` | 21 | Refresh intervals, outdated detection, index rebuild, callbacks |
| `test_event_store.py` | 24 | CRUD operations, visibility/color, state persistence, clone |
| `test_gui_logic.py` | 27 | Color math, event portion splitting, overlap detection, pixel→time |

All tests write to temporary directories — no data is written to the real cache at `~/.local/state/kubux-calendar/`.

## Data Storage

### UI State
Application state is stored in `~/.local/state/kubux-calendar/state.json`:
- Window geometry and position
- Sidebar width (splitter position)
- Current view type and date
- Scroll position
- Calendar visibility and colors
- Last used calendar for new events

### Event Cache (Offline-First)
Events are persisted to `~/.local/state/kubux-calendar/v2/`:
- `cache/{source_id}/*.ics` - One iCalendar file per event (survive app restarts)
- `sources/{source_id}.json` - Per-source sync metadata
- `pending.json` - Queued sync operations

This enables offline operation - when the server is unavailable, events are loaded from the local cache and displayed with an "unconfirmed" indicator.

### Sync Queue
Pending changes are stored as `pending.json` inside the event cache directory:
- Create/update/delete operations queued for server
- Persists across app restarts
- Syncs automatically when server becomes available

## License

This project is licensed under the Apache License 2.0 - see the [LICENSE](LICENSE) file for details.

## Acknowledgments

This application is vibe coded using various models.
