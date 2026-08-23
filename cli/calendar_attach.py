"""kubux-calendar-attach — open the event editor for an ``.ics`` and queue it.

XDG default handler for ``text/calendar`` (``.ics``) attachments.  It parses
the attached iCalendar document, opens a small event-editor window prefilled
from it, and on confirmation inserts a ``create`` operation into the
kubux-calendar **pending-changes queue** (``pending.json``).  A running
kubux-calendar instance watches that file, so the newly queued event appears
in it and is synced to the server automatically.

Concurrency: the GUI's EventStore and this tool both access ``pending.json``
through :class:`backend.event_fs.EventFS`, whose read-modify-write operations
are guarded by an advisory file lock.  Whatever writes first wins a clean
state — no lost updates, regardless of which process is running.

Examples::

    kubux-calendar-attach event.ics          # open editor, pick calendar
    kubux-calendar-attach --file event.ics   # same
    cat event.ics | kubux-calendar-attach    # read from stdin
    kubux-calendar-attach event.ics --calendar Nextcloud.Primary/beruflich

When ``--calendar`` is given the event is queued non-interactively
(no editor window).  ``--calendar`` takes ``<account>/<calendar>`` where
*account* is a ``[Nextcloud.<Name>]`` config section and *calendar* is a
calendar id/name on that account.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import pytz

from backend.config import Config
from backend.event import ImmutableEvent, _rebuild_ical
from backend.event_fs import EventFS, PendingOp
from backend.network_ops import caldav_connect, caldav_list_calendars

try:
    from PySide6.QtWidgets import (
        QApplication, QWidget, QVBoxLayout, QFormLayout, QLineEdit,
        QDateTimeEdit, QCheckBox, QComboBox, QTextEdit, QPushButton,
        QHBoxLayout, QLabel, QMessageBox,
    )
    from PySide6.QtCore import Qt, QDateTime
    _QT_OK = True
except Exception:  # pragma: no cover - headless import guard
    _QT_OK = False


# ============================================================ input parsing

def read_input(file: Optional[str], stdin=None) -> str:
    """Read and decode the VCALENDAR document (shared with caldav_send)."""
    if file is not None:
        raw = Path(file).read_bytes()
        text = raw.decode("utf-8-sig")
    elif stdin is not None:
        text = stdin.read()
    else:
        text = sys.stdin.read()
    return text.replace("\r\n", "\n").replace("\r", "\n")


def parse_ics(text: str, source_id: str = "", config_tz=None) -> tuple[str, ImmutableEvent]:
    """Parse ``.ics`` text into a canonical VCALENDAR string + ImmutableEvent.

    Guarantees the returned ical carries a stable ``UID`` (injecting a fresh
    one if the attachment had none) so the queued ``create`` op has a stable
    key.  The event's content (summary, dates, attendees, recurrence, …) is
    preserved verbatim.
    """
    import icalendar
    cal = icalendar.Calendar.from_ical(text)
    vevent = None
    for comp in cal.walk():
        if comp.name == "VEVENT":
            vevent = comp
            break
    if vevent is None:
        raise ValueError("no VEVENT found in calendar data")

    if not vevent.get("UID"):
        import uuid
        vevent.add("uid", str(uuid.uuid4()))

    canonical = cal.to_ical().decode("utf-8")
    ev = ImmutableEvent.from_ical(canonical, source_id, config_tz=config_tz)
    return canonical, ev


# ============================================================ queuing

def build_pending_op(source_id: str, base_ical: str, uid: str,
                     edits: Optional[dict] = None) -> PendingOp:
    """Build a pending ``create`` op for *base_ical* into *source_id*.

    *edits* applies field updates (summary/description/location/start/end/
    all_day) to a supplied base via the same iCalendar rebuild used by the
    GUI, preserving everything else (UID, attendees, RRULE, …).  When *edits*
    is empty/None the original text is queued untouched.
    """
    if edits:
        ical_data = _rebuild_ical(base_ical, **edits)
    else:
        ical_data = base_ical
    return PendingOp(uid=uid, source_id=source_id, operation="create",
                     ical_data=ical_data)


def enqueue_pending(op: PendingOp, config_tz=None, base: Optional[Path] = None) -> None:
    """Write *op* into the pending-changes queue (process-safe).

    Uses the real state dir by default; *base* overrides the EventFS base
    (for tests).
    """
    EventFS(base=base, config_tz=config_tz).add_pending(op)


def queue_from_args(text: str, calendar_spec: str, config: Config,
                    base: Optional[Path] = None) -> str:
    """Non-interactive queue of *text* into *calendar_spec*.

    *calendar_spec* is ``<account>/<calendar>``.  Returns a success message.
    """
    config_tz = pytz.timezone(config.timezone)
    base_ical, ev = parse_ics(text, config_tz=config_tz)
    account, _, cal_query = calendar_spec.partition("/")
    if not cal_query:
        raise ValueError("--calendar must be '<account>/<calendar>'")

    account = resolve_account(config, account)
    password = account.get_password(config.password_program)
    session = caldav_connect(account.url, account.username, password,
                             account_name=account.name)
    calendars = caldav_list_calendars(session)
    cal = select_calendar(calendars, cal_query)

    # The GUI keys CalDAV sources as "caldav:<account>:<calendar_id>".
    source_id = f"caldav:{account.name}:{cal.id}"
    op = build_pending_op(source_id, base_ical, ev.uid)
    enqueue_pending(op, config_tz, base)
    return (f"Queued event '{ev.summary or ev.uid}' for calendar "
            f"{cal.id!r} on {account.name} (pending sync)")


# Account/calendar resolution is shared with caldav_send — import lazily
# to avoid pulling in its argparse at import time is unnecessary; reuse.
def resolve_account(config: Config, account: str):
    from cli.caldav_send import resolve_account as _ra
    return _ra(config, account)


def select_calendar(calendars, query: str):
    from cli.caldav_send import select_calendar as _sc
    return _sc(calendars, query)


# ============================================================ calendar picker

def _writable_calendar_sources(config: Config) -> list:
    """Return writable calendar sources via the offline EventStore.

    Uses locally cached source metadata (no network) — the same view the
    GUI's event editor offers.  If no sources are known yet, returns [].
    """
    from backend.event_store import EventStore
    store = EventStore(config)
    store.initialize_sources_only()
    return store.get_writable_calendars()


# ============================================================ editor dialog

if _QT_OK:
    class AttachDialog(QWidget):
        """Minimal event editor (lookalike of the GUI's EventDialog).

        Prefilled from the parsed attachment; lets the user pick a writable
        calendar and edit the common fields, then queues a pending create.
        """

        def __init__(self, config: Config, base_ical: str, event: ImmutableEvent,
                     calendars: list, parent=None):
            super().__init__(parent)
            self._config = config
            self._base_ical = base_ical
            self._event = event
            self._calendars = calendars
            self._config_tz = pytz.timezone(config.timezone)
            self._queued = False
            self._tzid = event.tzid

            self.setWindowTitle("Import event into Kubux Calendar")
            self.setAttribute(Qt.WA_DeleteOnClose)
            self.setMinimumSize(420, 480)
            self._build_ui()
            self._populate()

        def _build_ui(self):
            layout = QVBoxLayout(self)
            form = QFormLayout()

            self._title_edit = QLineEdit()
            form.addRow("Title:", self._title_edit)

            self._calendar_combo = QComboBox()
            for cal in self._calendars:
                self._calendar_combo.addItem(f"{cal.name} ({cal.account_name})", cal.id)
            form.addRow("Calendar:", self._calendar_combo)

            self._all_day_check = QCheckBox("All-day event")
            form.addRow("", self._all_day_check)

            self._start_edit = QDateTimeEdit()
            self._start_edit.setCalendarPopup(True)
            self._start_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
            form.addRow("Start:", self._start_edit)

            self._end_edit = QDateTimeEdit()
            self._end_edit.setCalendarPopup(True)
            self._end_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
            form.addRow("End:", self._end_edit)

            tz_label = QLabel(self._tzid or "Floating (local time)")
            form.addRow("Timezone:", tz_label)

            self._location_edit = QLineEdit()
            form.addRow("Location:", self._location_edit)

            self._description_edit = QTextEdit()
            self._description_edit.setMinimumHeight(120)
            form.addRow("Description:", self._description_edit)

            layout.addLayout(form)
            layout.addStretch(1)

            buttons = QHBoxLayout()
            buttons.addStretch(1)
            cancel = QPushButton("Cancel")
            cancel.clicked.connect(self.close)
            buttons.addWidget(cancel)
            save = QPushButton("Queue for sync")
            save.setDefault(True)
            save.clicked.connect(self._on_queue)
            buttons.addWidget(save)
            layout.addLayout(buttons)

        def _populate(self):
            ev = self._event
            self._title_edit.setText(ev.summary)
            self._location_edit.setText(ev.location)
            self._description_edit.setText(ev.description)
            self._all_day_check.setChecked(ev.all_day)
            if ev.all_day:
                self._start_edit.setDisplayFormat("yyyy-MM-dd")
                self._end_edit.setDisplayFormat("yyyy-MM-dd")

            start, end = ev.start, ev.end
            if start.tzinfo is not None:
                try:
                    start = start.astimezone(self._config_tz).replace(tzinfo=None)
                    end = end.astimezone(self._config_tz).replace(tzinfo=None)
                except Exception:
                    pass
            self._start_edit.setDateTime(QDateTime(start))
            self._end_edit.setDateTime(QDateTime(end))

        def _collect_edits(self) -> dict:
            def as_aware(dt: datetime) -> datetime:
                if self._tzid and self._tzid != "UTC":
                    return pytz.timezone(self._tzid).localize(dt)
                if self._tzid == "UTC":
                    return dt.replace(tzinfo=pytz.UTC)
                return dt  # floating — keep naive

            return {
                "summary": self._title_edit.text().strip(),
                "location": self._location_edit.text().strip(),
                "description": self._description_edit.toPlainText(),
                "all_day": self._all_day_check.isChecked(),
                "start": as_aware(self._start_edit.dateTime().toPython()),
                "end": as_aware(self._end_edit.dateTime().toPython()),
            }

        def _on_queue(self):
            if not self._title_edit.text().strip():
                QMessageBox.warning(self, "Import", "Please enter an event title.")
                return
            cal_id = self._calendar_combo.currentData()
            if not cal_id:
                QMessageBox.warning(self, "Import", "Please select a calendar.")
                return
            try:
                op = build_pending_op(cal_id, self._base_ical, self._event.uid,
                                      self._collect_edits())
                enqueue_pending(op, self._config_tz)
            except Exception as e:  # noqa: BLE001
                QMessageBox.critical(self, "Import", f"Failed to queue event:\n{e}")
                return
            self._queued = True
            self.close()

        def queued(self) -> bool:
            return self._queued


# ============================================================ CLI

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kubux-calendar-attach",
        description=(
            "Import an .ics attachment into the kubux-calendar pending "
            "queue.  Opens an event editor unless --calendar is given."
        ),
    )
    parser.add_argument(
        "file", nargs="?", default=None,
        help="Path to the .ics file (default: read from stdin).",
    )
    parser.add_argument(
        "--file", dest="file_opt", metavar="FILE",
        help="Path to the .ics file (same as the positional FILE).",
    )
    parser.add_argument(
        "--calendar", dest="calendar_spec", default="",
        help=(
            "Queue non-interactively to '<account>/<calendar>' "
            "(e.g. 'Nextcloud.Primary/beruflich').  Without this, an editor "
            "window opens to choose the calendar."
        ),
    )
    parser.add_argument(
        "--config", default=None,
        help="Path to the kubux-calendar config TOML (default: auto-detect).",
    )
    return parser


def parse_args(argv=None) -> argparse.Namespace:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.file_opt and args.file:
        parser.error("file given both positionally and via --file")
    args.input_path = args.file_opt or args.file
    return args


def main(argv=None, stdin=None) -> int:
    args = parse_args(argv)
    try:
        text = read_input(args.input_path, stdin)
        if not text.strip():
            raise ValueError("empty input; nothing to import")
        config = Config.load(Path(args.config) if args.config else None)

        if args.calendar_spec:
            print(queue_from_args(text, args.calendar_spec, config))
            return 0

        return _interactive(config, text)
    except Exception as e:  # noqa: BLE001
        print(f"kubux-calendar-attach: error: {e}", file=sys.stderr)
        return 1


def _interactive(config: Config, text: str) -> int:
    if not _QT_OK:
        print("kubux-calendar-attach: PySide6 not available", file=sys.stderr)
        return 1

    config_tz = pytz.timezone(config.timezone)
    base_ical, ev = parse_ics(text, config_tz=config_tz)

    calendars = _writable_calendar_sources(config)
    if not calendars:
        print(
            "kubux-calendar-attach: no writable calendars configured.\n"
            "  Run kubux-calendar once so it can cache your Nextcloud "
            "calendars, then retry.",
            file=sys.stderr,
        )
        return 1

    app = QApplication.instance() or QApplication([sys.argv[0]])
    dialog = AttachDialog(config, base_ical, ev, calendars)
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    app.exec()

    if dialog.queued():
        print(f"Queued event '{ev.summary or ev.uid}' for sync.")
        return 0
    print("Cancelled — nothing queued.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
