"""kubux-caldav-send — push an ``.ics`` document to a Nextcloud calendar.

A small command line tool that uploads a VCALENDAR document (one or more
events) to a calendar on a Nextcloud server.  The server account and
credentials come from the standard kubux-calendar configuration
(``~/.config/kubux-calendar/kubux-calendar.toml`` by default); the target
calendar is a calendar on the server, selected by its id or display name.

Examples::

    cat event.ics | kubux-caldav-send --account Nextcloud.Primary --calendar beruflich
    kubux-caldav-send --account Nextcloud.Primary --calendar beruflich --file event.ics

Exit status is ``0`` on success and non-zero on any failure.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional, TextIO

from backend.config import Config, NextcloudAccount

# Imported lazily inside functions that need it so that ``--help`` stays fast
# and the module is importable in a headless environment.
from backend.network_ops import (
    CalendarInfo,
    caldav_connect,
    caldav_list_calendars,
    caldav_save_event,
)


class UsageError(Exception):
    """Raised for expected, user-facing errors (bad args, missing account…)."""


# ============================================================ input reading

def read_input(file: Optional[str], stdin: Optional[TextIO] = None) -> str:
    """Read and decode the VCALENDAR document.

    Reads *file* if given, otherwise *stdin* (or ``sys.stdin.buffer`` when
    *stdin* is not provided).  A leading UTF-8 BOM is stripped.  CRLF/CR are
    normalised to ``\\n`` for consistent handling by the caldav library.
    """
    if file is not None:
        with open(file, "rb") as fh:
            raw = fh.read()
    elif stdin is not None:
        raw = stdin.read()
    else:
        raw = sys.stdin.buffer.read()

    if isinstance(raw, bytes):
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as e:
            raise UsageError(f"input is not valid UTF-8: {e}") from e
    else:
        text = raw
    return text.replace("\r\n", "\n").replace("\r", "\n")


# ============================================================ config/account

def resolve_account(config: Config, account: str) -> NextcloudAccount:
    """Find *account* in *config*'s Nextcloud accounts.

    *account* may be given with or without the ``Nextcloud.`` prefix and is
    matched case-insensitively.  If *account* is empty and there is exactly
    one configured account, that account is returned.
    """
    accounts = config.nextcloud_accounts
    query = (account or "").lower()
    if not query:
        if len(accounts) == 1:
            return accounts[0]
        raise UsageError(
            f"no --account given and {len(accounts)} Nextcloud accounts configured; "
            f"available: {', '.join(a.name for a in accounts)!r}"
        )

    for acc in accounts:
        names = {acc.name.lower()}
        if acc.name.lower().startswith("nextcloud."):
            names.add(acc.name.lower().split(".", 1)[1])
        elif f"nextcloud.{acc.name}".lower() == query:
            names.add(query)  # plain name matches with prefix
        if query in names or query == acc.name.lower():
            return acc
    raise UsageError(
        f"unknown Nextcloud account {account!r}; "
        f"available: {', '.join(a.name for a in accounts)!r}"
    )


# ============================================================ calendar lookup

def select_calendar(calendars: list[CalendarInfo], query: str) -> CalendarInfo:
    """Return the calendar in *calendars* matching *query*.

    Matching is case-insensitive against either the calendar's server id
    (the last URL segment, e.g. ``beruflich``) or its display name.  Raises
    :class:`UsageError` if no unique match is found.
    """
    q = query.casefold()
    matches = [c for c in calendars
               if c.id.casefold() == q or c.name.casefold() == q]
    if not matches:
        available = sorted({c.id or c.name for c in calendars})
        raise UsageError(
            f"no calendar named {query!r}. Available: {', '.join(available) or '(none)'}"
        )
    if len(matches) > 1:
        # Prefer a writable calendar if there's any ambiguity.
        writable = [c for c in matches if c.writable]
        if writable:
            return writable[0]
    return matches[0]


# ============================================================ orchestration

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kubux-caldav-send",
        description=(
            "Upload an .ics document to a Nextcloud (CalDAV) calendar. "
            "Reads the event from stdin unless --file is given."
        ),
        epilog=(
            "The account is a [Nextcloud.<Name>] section in the kubux-calendar "
            "config; --calendar is a calendar on the server (by id or display "
            "name)."
        ),
    )
    parser.add_argument(
        "--account",
        default="",
        help=(
            "Nextcloud account to use, given as 'Nextcloud.Primary' or "
            "'Primary'.  Defaults to the only configured account if there is "
            "exactly one."
        ),
    )
    parser.add_argument(
        "--calendar",
        required=True,
        help="Calendar on the server to upload to (by id or display name).",
    )
    parser.add_argument(
        "-f", "--file",
        help="Read the event from FILE instead of stdin.",
    )
    parser.add_argument(
        "--config",
        help="Path to the kubux-calendar config TOML (default: auto-detect).",
    )
    return parser


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def run(config: Config, account_name: str, calendar_query: str,
        ical_text: str) -> str:
    """Send *ical_text* to *calendar_query* on *account_name*.

    Returns a human-readable success message.
    """
    account = resolve_account(config, account_name)
    password = account.get_password(config.password_program)
    session = caldav_connect(account.url, account.username, password,
                             account_name=account.name)
    calendars = caldav_list_calendars(session)
    cal = select_calendar(calendars, calendar_query)

    if not cal.writable:
        raise UsageError(
            f"calendar {calendar_query!r} is not writable (read-only)."
        )
    if not caldav_save_event(cal, ical_text):
        raise UsageError(
            f"failed to save event to {calendar_query!r} "
            f"on {account.name} — check the server and credentials."
        )
    return (f"Sent event to calendar {calendar_query!r} "
            f"({cal.id}) on {account.name}")


def main(argv: Optional[list[str]] = None,
         stdin: Optional[TextIO] = None) -> int:
    try:
        args = parse_args(argv)
    except SystemExit as e:
        # argparse calls sys.exit() on --help / bad invocation.  Propagate the
        # exit code (2 for usage errors, 0 for --help) rather than turning it
        # into an unhandled traceback.
        return int(e.code) if isinstance(e.code, int) else 0
    try:
        ical_text = read_input(args.file, stdin)
        if not ical_text.strip():
            raise UsageError("empty input; nothing to send")
        config = Config.load(Path(args.config) if args.config else None)
        msg = run(config, args.account, args.calendar, ical_text)
    except (UsageError, FileNotFoundError) as e:
        print(f"kubux-caldav-send: error: {e}", file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001 — surface any failure cleanly
        print(f"kubux-caldav-send: unexpected error: {e}", file=sys.stderr)
        return 1
    print(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
