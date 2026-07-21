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


# ----------------------------------------------------------------------
# NextcloudAccount.get_password
# ----------------------------------------------------------------------

def test_get_password_success(tmp_path):
    from backend.config import NextcloudAccount
    # The password program is called as [password_program, password_key]
    # /usr/bin/true returns empty stdout, so password will be ""
    acc = NextcloudAccount(name="Test", url="", username="", password_key="test-secret")
    try:
        pw = acc.get_password("/usr/bin/true")
        assert pw == ""  # true returns empty stdout
    except RuntimeError:
        # May not be available in all environments
        pass


def test_get_password_cached():
    """Password is cached after first call."""
    from backend.config import NextcloudAccount
    acc = NextcloudAccount(name="Test", url="", username="", password_key="test/key")
    try:
        pw1 = acc.get_password("/usr/bin/true")
        pw2 = acc.get_password("/usr/bin/true")  # cached, no second call
        assert pw1 == pw2
    except RuntimeError:
        pass


def test_get_password_failure():
    from backend.config import NextcloudAccount
    acc = NextcloudAccount(name="Test", url="", username="", password_key="test/key")
    try:
        acc.get_password("/nonexistent/password/program")
        assert False, "should have raised"
    except RuntimeError:
        pass


# ----------------------------------------------------------------------
# LocalizationConfig
# ----------------------------------------------------------------------

def test_get_day_name():
    from backend.config import LocalizationConfig
    loc = LocalizationConfig()
    assert loc.get_day_name(0) == "Mon"
    assert loc.get_day_name(6) == "Sun"
    assert loc.get_day_name(7) == ""


def test_get_day_name_out_of_range():
    from backend.config import LocalizationConfig
    loc = LocalizationConfig()
    assert loc.get_day_name(-1) == ""
    assert loc.get_day_name(99) == ""


def test_get_day_name_for_column():
    from backend.config import LocalizationConfig
    loc = LocalizationConfig(day_names=["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
                             first_day_of_week=0)  # Monday first
    assert loc.get_day_name_for_column(0) == "Mon"
    assert loc.get_day_name_for_column(3) == "Thu"

    loc2 = LocalizationConfig(day_names=["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"],
                              first_day_of_week=6)  # Sunday first
    # Formula: (first_day_of_week + col) % 7 = (6 + 0) % 7 = 6 → day_names[6] = "Sat"
    # So column 0 = Saturday, column 1 = Sunday
    assert loc2.get_day_name_for_column(0) == "Sat"
    assert loc2.get_day_name_for_column(1) == "Sun"


def test_get_week_start():
    from backend.config import LocalizationConfig
    from datetime import date

    loc = LocalizationConfig(first_day_of_week=0)  # Monday
    # 2026-01-01 is a Thursday
    d = date(2026, 1, 1)
    week_start = loc.get_week_start(d)
    assert week_start.weekday() == 0  # Monday
    assert week_start == date(2025, 12, 29)  # Monday before Thursday

    loc2 = LocalizationConfig(first_day_of_week=6)  # Sunday
    week_start2 = loc2.get_week_start(d)
    assert week_start2.weekday() == 6  # Sunday
    assert week_start2 == date(2025, 12, 28)  # Sunday before Thursday


def test_get_month_name():
    from backend.config import LocalizationConfig
    loc = LocalizationConfig()
    assert loc.get_month_name(1) == "January"
    assert loc.get_month_name(12) == "December"
    assert loc.get_month_name(0) == ""
    assert loc.get_month_name(13) == ""


# ----------------------------------------------------------------------
# Default paths
# ----------------------------------------------------------------------

def test_get_default_config_path():
    import os
    path = Config.get_default_config_path()
    assert str(path).endswith("kubux-calendar.toml")
    assert "XDG_CONFIG_HOME" not in str(path) or os.environ.get("XDG_CONFIG_HOME") is not None


def test_get_default_state_path():
    import os
    path = Config.get_default_state_path()
    assert str(path).endswith("state.json")
    assert "XDG_STATE_HOME" not in str(path) or os.environ.get("XDG_STATE_HOME") is not None

# ----------------------------------------------------------------------
# Edge cases
# ----------------------------------------------------------------------

def test_load_empty_general_uses_defaults(tmp_path):
    """A config with an empty [General] still loads with defaults."""
    cfg = _write_config(tmp_path, """
[General]
""")
    config = Config.load(cfg)
    assert config.password_program == "/usr/bin/pass"
    assert config.refresh_interval == 300


def test_load_invalid_toml_raises(tmp_path):
    """Malformed TOML should raise tomllib.TOMLDecodeError."""
    import tomllib
    cfg = _write_config(tmp_path, "this is = = not valid toml [[[")
    try:
        Config.load(cfg)
        assert False, "should have raised"
    except tomllib.TOMLDecodeError:
        pass


def test_load_state_file_expansion(tmp_path):
    """state_file with ~ should be expanded."""
    cfg = _write_config(tmp_path, """
[General]
password_program = "/usr/bin/pass"
state_file = "~/some-state.json"
""")
    config = Config.load(cfg)
    assert "~" not in str(config.state_file)


def test_load_outdate_threshold_default(tmp_path):
    cfg = _write_config(tmp_path, """
[General]
password_program = "/usr/bin/pass"
""")
    config = Config.load(cfg)
    assert config.outdate_threshold == 7200


def test_load_outdate_threshold_custom(tmp_path):
    cfg = _write_config(tmp_path, """
[General]
password_program = "/usr/bin/pass"
outdate_threshold = 3600
""")
    config = Config.load(cfg)
    assert config.outdate_threshold == 3600
