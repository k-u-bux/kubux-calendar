"""Tests for MainWindow._events_signature — change detection for display rebuilds."""

from datetime import datetime
from unittest.mock import MagicMock

import pytz

from gui.main_window import MainWindow

UTC = pytz.UTC


def _mkevent(uid="u", summary="E", start=None):
    ev = MagicMock()
    ev.uid = uid
    ev.start = start or datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
    ev.end = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    ev.summary = summary
    ev.location = ""
    ev.description = ""
    ev.all_day = False
    ev.calendar_name = "Cal"
    ev.calendar_color = "#ff0000"
    ev.read_only = False
    ev.is_recurring = False
    ev.sync_status = ""
    ev.pending_operation = None
    ev.is_outdated = False
    ev.confirmed_at = None
    return ev


def test_signature_equal_for_same_data():
    events = [_mkevent(uid="a"), _mkevent(uid="b")]
    sig1 = MainWindow._events_signature(events)
    # Fresh objects, same content -> same signature
    events2 = [_mkevent(uid="a"), _mkevent(uid="b")]
    sig2 = MainWindow._events_signature(events2)
    assert sig1 == sig2


def test_signature_changes_on_content_change():
    sig1 = MainWindow._events_signature([_mkevent(uid="a")])
    sig2 = MainWindow._events_signature([_mkevent(uid="a", summary="Changed")])
    assert sig1 != sig2


def test_signature_changes_on_added_event():
    sig1 = MainWindow._events_signature([_mkevent(uid="a")])
    sig2 = MainWindow._events_signature([_mkevent(uid="a"), _mkevent(uid="b")])
    assert sig1 != sig2


def test_signature_changes_on_staleness():
    ev1 = _mkevent(uid="a")
    sig1 = MainWindow._events_signature([ev1])
    ev2 = _mkevent(uid="a")
    ev2.is_outdated = True
    sig2 = MainWindow._events_signature([ev2])
    assert sig1 != sig2
