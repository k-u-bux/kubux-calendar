"""Tests for library/log.py."""

import io
import sys
from library.log import debug_log, set_level, Level


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