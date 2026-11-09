"""
Minimal log-level filtered logging for Kubux Calendar.

The only sink is stderr, and only while the process has a **controlling
terminal**.  A terminal launch is meant to be chatty.  A launch by the
desktop session, a window manager, cron or the mail client has its stderr
captured into the journal, and calendar content (event summaries, UIDs,
server hrefs) does not belong there — so those launches log nothing at all
rather than being redirected somewhere unseen.  An explicit redirection from
a shell (``2>file``, ``|& tee``) keeps the controlling terminal and is
honoured: that choice is the user's, not ours.

Gating :func:`debug_log` alone is not enough: journald receives every byte
written to file descriptor 2, including output from the Qt C++ libraries,
from subprocesses (which inherit the descriptor) and from Python's own
warnings and tracebacks, none of which pass through this module.  So
:func:`silence_unattended_stderr` redirects the descriptor itself to
``/dev/null``; the entry points whose output is captured (the GUI and the
``.ics`` handler) call it before importing anything else.  Command-line tools
keep their stderr — there it is the tool's own output channel.

Usage::

    from library.log import debug_log, Level

    debug_log(Level.DEBUG, "connecting to server ...")
    debug_log(Level.WARN, "retrying ...")
    debug_log(Level.ERROR, "connection failed")
"""

import os
import sys
from datetime import datetime
from enum import IntEnum


def silence_unattended_stderr() -> None:
    """Point fd 2 at ``/dev/null`` when there is no controlling terminal.

    Probed via ``/dev/tty`` rather than ``sys.stderr.isatty()``: the latter is
    also false for a shell redirection, which is a deliberate user action,
    while a session-launched process has no controlling terminal at all and its
    stderr lands in journald.  Child processes inherit the redirected
    descriptor, so their output is silenced by the same call.
    """
    try:
        os.close(os.open("/dev/tty", os.O_WRONLY))
    except OSError:
        pass
    else:
        return                    # started from a terminal: keep stderr
    try:
        fd = os.open(os.devnull, os.O_WRONLY)
    except OSError:
        return
    os.dup2(fd, 2)
    os.close(fd)


class Level(IntEnum):
    """Log severity levels (higher = more severe)."""
    DEBUG = 0
    INFO = 1
    WARN = 2
    ERROR = 3
    SILENT = 5


_current = Level.WARN
"""Global threshold — messages below this level are suppressed."""

_attended: "bool | None" = None
"""Cached answer of :func:`_started_from_terminal`."""


def set_level(level: Level | int) -> None:
    """Set the global log threshold."""
    global _current
    _current = Level(level)


def _started_from_terminal() -> bool:
    """True if this process has a controlling terminal.

    Probed via ``/dev/tty`` rather than ``sys.stderr.isatty()``: the latter
    is also false for a shell redirection, which is a deliberate user action,
    while a session-launched process has no controlling terminal at all and
    its stderr lands in journald.
    """
    try:
        fd = os.open("/dev/tty", os.O_WRONLY)
    except OSError:
        return False
    try:
        return os.isatty(fd)
    finally:
        os.close(fd)


def debug_log(level: Level, message: str) -> None:
    """Print *message* to stderr if *level* >= the configured threshold.

    Dropped silently when the process was not started from a terminal: the
    stderr of a session-launched app is captured (journald), and this log
    must not be redirected there behind the user's back.
    """
    global _attended
    if level < _current:
        return
    if _attended is None:
        _attended = _started_from_terminal()
    if not _attended or sys.stderr is None:
        return
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {message}", file=sys.stderr)