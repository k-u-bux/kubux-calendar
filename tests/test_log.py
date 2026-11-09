"""Tests for library/log.py."""

import io
import os
import pty
import sys

import pytest

import library.log as logmod
from library.log import debug_log, set_level, Level


@pytest.fixture(autouse=True)
def _terminal_attached(monkeypatch):
    """Run every test below as if launched from a terminal.

    Without a controlling terminal the log has no sink at all (see
    test_log_dropped_without_terminal), so pin the probe's cached answer.
    """
    monkeypatch.setattr(logmod, "_attended", True)


def test_set_level_info():
    set_level(Level.INFO)


def test_debug_log_suppressed():
    set_level(Level.WARN)
    captured = io.StringIO()
    old = sys.stderr
    sys.stderr = captured
    try:
        debug_log(Level.DEBUG, "should not appear")
    finally:
        sys.stderr = old
    assert "should not appear" not in captured.getvalue()


def test_log_dropped_without_terminal(monkeypatch):
    """A session-launched process (no controlling terminal) writes nothing.

    Its stderr is captured by journald; the log must not be redirected there
    behind the user's back.
    """
    monkeypatch.setattr(logmod, "_attended", False)
    set_level(Level.DEBUG)
    captured = io.StringIO()
    old = sys.stderr
    sys.stderr = captured
    try:
        debug_log(Level.ERROR, "must not reach the journal")
    finally:
        sys.stderr = old
    assert captured.getvalue() == ""


def test_log_dropped_when_stderr_missing(monkeypatch):
    """No stderr stream (windowed interpreter) must not raise."""
    monkeypatch.setattr(logmod, "_attended", True)
    set_level(Level.DEBUG)
    old = sys.stderr
    sys.stderr = None
    try:
        debug_log(Level.ERROR, "no sink")  # must not raise
    finally:
        sys.stderr = old


def test_started_from_terminal_without_ctty(monkeypatch):
    """No controlling terminal -> False (the desktop/journald case)."""
    def no_ctty(*args, **kwargs):
        raise OSError(6, "No such device or address")

    monkeypatch.setattr(logmod.os, "open", no_ctty)
    assert logmod._started_from_terminal() is False


def test_started_from_terminal_with_ctty(monkeypatch):
    """A controlling terminal -> True, even if stderr itself is redirected."""
    master, slave = pty.openpty()
    try:
        monkeypatch.setattr(logmod.os, "open", lambda *a, **k: os.dup(slave))
        assert logmod._started_from_terminal() is True
    finally:
        os.close(master)
        os.close(slave)


def test_debug_log_emitted():
    set_level(Level.DEBUG)
    captured = io.StringIO()
    old = sys.stderr
    sys.stderr = captured
    try:
        debug_log(Level.DEBUG, "should appear")
    finally:
        sys.stderr = old
    assert "should appear" in captured.getvalue()


def test_debug_log_warn_level():
    set_level(Level.WARN)
    captured = io.StringIO()
    old = sys.stderr
    sys.stderr = captured
    try:
        debug_log(Level.WARN, "warning")
        debug_log(Level.ERROR, "error")
    finally:
        sys.stderr = old
    assert "warning" in captured.getvalue()
    assert "error" in captured.getvalue()


def test_debug_log_silent_suppresses_all():
    set_level(Level.SILENT)
    captured = io.StringIO()
    old = sys.stderr
    sys.stderr = captured
    try:
        debug_log(Level.ERROR, "should not appear")
    finally:
        sys.stderr = old
    assert "should not appear" not in captured.getvalue()