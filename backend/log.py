"""
Minimal log-level filtered logging for Kubux Calendar.

Usage::

    from backend.log import debug_log, Level

    debug_log(Level.DEBUG, "connecting to server ...")
    debug_log(Level.WARN, "retrying ...")
    debug_log(Level.ERROR, "connection failed")
"""

from enum import IntEnum


class Level(IntEnum):
    """Log severity levels (higher = more severe)."""
    DEBUG = 0
    INFO = 1
    WARN = 2
    ERROR = 3
    SILENT = 5


_current = Level.WARN
"""Global threshold — messages below this level are suppressed."""


def set_level(level: Level | int) -> None:
    """Set the global log threshold."""
    global _current
    _current = Level(level)


def debug_log(level: Level, message: str) -> None:
    """Print *message* to stderr if *level* >= the configured threshold."""
    if level >= _current:
        import sys
        from datetime import datetime
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{timestamp}] {message}", file=sys.stderr)