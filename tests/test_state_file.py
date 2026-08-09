"""Tests for gui/state_file.py section-preserving persistence."""
import json
from unittest.mock import MagicMock
from backend.event_store import EventStore
from gui.state_file import save_state_sections


def _config(tmp_path):
    cfg = MagicMock(timezone="UTC")
    cfg.state_file = tmp_path / "state.json"
    cfg.ics_subscriptions = []
    cfg.nextcloud_accounts = []
    return cfg


def test_store_save_preserves_ui(tmp_path):
    save_state_sections(tmp_path / "state.json", {"ui": {"view_type": "week"}})
    store = EventStore(_config(tmp_path))
    store._visibility = {"cal:1": True}
    store._user_colors = {"cal:1": "#ff0000"}
    store._save_state()
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["ui"] == {"view_type": "week"}
    assert state["visibility"] == {"cal:1": True}


def test_gui_save_preserves_store_sections(tmp_path):
    store = EventStore(_config(tmp_path))
    store._visibility = {"cal:1": True}
    store._user_colors = {"cal:1": "#ff0000"}
    store._save_state()
    save_state_sections(tmp_path / "state.json", {"ui": {"view_type": "list"}})
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["ui"] == {"view_type": "list"}
    assert state["visibility"] == {"cal:1": True}


def test_store_reload_round_trip(tmp_path):
    store = EventStore(_config(tmp_path))
    store._visibility = {"cal:1": True}
    store._user_colors = {"cal:1": "#ff0000"}
    store._save_state()
    reloaded = EventStore(_config(tmp_path))
    reloaded._load_state()
    assert reloaded._visibility == {"cal:1": True}
    assert reloaded._user_colors == {"cal:1": "#ff0000"}