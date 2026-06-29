"""Tests for backend/config.py."""

import tempfile
from pathlib import Path
from backend.config import Config


def _write_config(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "config.toml"
    p.write_text(content)
    return p


def test_load_minimal(tmp_path):
    cfg = _write_config(tmp_path, """
[General]
password_program = "/usr/bin/pass"
""")
    config = Config.load(cfg)
    assert config.password_program == "/usr/bin/pass"
    assert config.log_level == "warn"
    assert config.refresh_interval == 300
    assert config.outdate_threshold == 7200


def test_load_log_level(tmp_path):
    cfg = _write_config(tmp_path, """
[General]
password_program = "/usr/bin/pass"
log_level = "debug"
""")
    config = Config.load(cfg)
    assert config.log_level == "debug"


def test_load_refresh_interval(tmp_path):
    cfg = _write_config(tmp_path, """
[General]
password_program = "/usr/bin/pass"
refresh_interval = 60
""")
    config = Config.load(cfg)
    assert config.refresh_interval == 60


def test_load_nextcloud_accounts_format1(tmp_path):
    cfg = _write_config(tmp_path, """
[General]
password_program = "/usr/bin/pass"

[Nextcloud.Personal]
url = "https://nc.example.com"
username = "user"
password_key = "nc/password"
""")
    config = Config.load(cfg)
    assert len(config.nextcloud_accounts) == 1
    acc = config.nextcloud_accounts[0]
    assert acc.name == "Personal"
    assert acc.url == "https://nc.example.com"
    assert acc.username == "user"
    assert acc.password_key == "nc/password"


def test_load_nextcloud_accounts_format2(tmp_path):
    cfg = _write_config(tmp_path, """
[General]
password_program = "/usr/bin/pass"

[Nextcloud]
[Nextcloud.Personal]
url = "https://nc.example.com"
username = "user"
password_key = "nc/password"
""")
    config = Config.load(cfg)
    assert len(config.nextcloud_accounts) == 1
    acc = config.nextcloud_accounts[0]
    assert acc.name == "Personal"


def test_load_ics_subscriptions_format1(tmp_path):
    cfg = _write_config(tmp_path, """
[General]
password_program = "/usr/bin/pass"

[Subscription.Holidays]
url = "https://example.com/holidays.ics"
name = "Holidays"
color = "#ff0000"
""")
    config = Config.load(cfg)
    assert len(config.ics_subscriptions) == 1
    sub = config.ics_subscriptions[0]
    assert sub.name == "Holidays"
    assert sub.url == "https://example.com/holidays.ics"
    assert sub.color == "#ff0000"


def test_load_ics_subscriptions_format2(tmp_path):
    cfg = _write_config(tmp_path, """
[General]
password_program = "/usr/bin/pass"

[Subscription]
[Subscription.Holidays]
url = "https://example.com/holidays.ics"
name = "Holidays"
""")
    config = Config.load(cfg)
    assert len(config.ics_subscriptions) == 1


def test_load_layout(tmp_path):
    cfg = _write_config(tmp_path, """
[General]
password_program = "/usr/bin/pass"

[Layout]
hour_height = 80
drag_snap_minutes = 15
""")
    config = Config.load(cfg)
    assert config.layout.hour_height == 80
    assert config.layout.drag_snap_minutes == 15


def test_load_layout_defaults(tmp_path):
    cfg = _write_config(tmp_path, """
[General]
password_program = "/usr/bin/pass"
""")
    config = Config.load(cfg)
    assert config.layout.hour_height == 60
    assert config.layout.interface_font == "Sans"


def test_load_bindings(tmp_path):
    cfg = _write_config(tmp_path, """
[General]
password_program = "/usr/bin/pass"

[Bindings]
next = "Right"
prev = "Left"
""")
    config = Config.load(cfg)
    assert config.bindings.next == "Right"
    assert config.bindings.prev == "Left"


def test_load_localization(tmp_path):
    cfg = _write_config(tmp_path, """
[General]
password_program = "/usr/bin/pass"

[Localization]
day_names = "Mo Di Mi Do Fr Sa So"
month_names = "Januar Februar März"
first_day_of_week = 0
""")
    config = Config.load(cfg)
    assert config.localization.day_names == ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
    assert config.localization.month_names == ["Januar", "Februar", "März"]


def test_load_colors(tmp_path):
    cfg = _write_config(tmp_path, """
[General]
password_program = "/usr/bin/pass"

[Colors]
current_time_line = "#ff0000"
day_column_background = "#eeeeee"
""")
    config = Config.load(cfg)
    assert config.colors.current_time_line == "#ff0000"
    assert config.colors.day_column_background == "#eeeeee"


def test_load_sync(tmp_path):
    cfg = _write_config(tmp_path, """
[General]
password_program = "/usr/bin/pass"

[Sync]
initial_interval = 20
max_interval = 600
backoff_multiplier = 3.0
""")
    config = Config.load(cfg)
    assert config.sync.initial_interval == 20
    assert config.sync.max_interval == 600
    assert config.sync.backoff_multiplier == 3.0


def test_load_labels(tmp_path):
    cfg = _write_config(tmp_path, """
[General]
password_program = "/usr/bin/pass"

[Labels]
button_today = "Heute"
button_new_event = "Neu"
""")
    config = Config.load(cfg)
    assert config.labels.button_today == "Heute"
    assert config.labels.button_new_event == "Neu"


def test_load_missing_file():
    try:
        Config.load(Path("/nonexistent/config.toml"))
        assert False, "should have raised"
    except FileNotFoundError:
        pass


def test_load_timezone(tmp_path):
    cfg = _write_config(tmp_path, """
[General]
password_program = "/usr/bin/pass"
timezone = "Europe/Berlin"
""")
    config = Config.load(cfg)
    assert config.timezone == "Europe/Berlin"


def test_load_per_source_refresh_outdate(tmp_path):
    cfg = _write_config(tmp_path, """
[General]
password_program = "/usr/bin/pass"

[Nextcloud.Personal]
url = "https://nc.example.com"
username = "user"
password_key = "nc/password"
refresh_interval = 120
outdate_threshold = 3600

[Subscription.Holidays]
url = "https://example.com/holidays.ics"
name = "Holidays"
refresh_interval = 600
outdate_threshold = 14400
""")
    config = Config.load(cfg)
    assert config.nextcloud_accounts[0].refresh_interval == 120
    assert config.nextcloud_accounts[0].outdate_threshold == 3600
    assert config.ics_subscriptions[0].refresh_interval == 600
    assert config.ics_subscriptions[0].outdate_threshold == 14400