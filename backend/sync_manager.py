"""
Sync manager for Kubux Calendar v2.

Orchestrates all network I/O through :mod:`task_dispatch` and keeps
:class:`EventFS`, :class:`EventIndex`, and the GUI in sync.

Every network call runs in a background thread via ``dispatch_task``.
Results are delivered to the main thread through the dispatcher's
Qt-signal mechanism; the manager then updates EventFS / EventIndex
and fires the user-supplied *on_change* callback.
"""

from datetime import datetime, timedelta
from typing import Optional, Callable

import pytz
from icalendar import Calendar as ICalCalendar

from .event import ImmutableEvent, CalendarSource
from .event_fs import EventFS, SourceMeta, PendingOp
from .event_index import EventIndex
from .network_ops import (
    DAVSession, CalendarInfo,
    caldav_connect, caldav_list_calendars, caldav_fetch_events,
    caldav_save_event, caldav_delete_event, caldav_add_exdate,
    ics_fetch, ics_parse_events,
)
from .task_dispatch import dispatch_task
from .log import debug_log, Level


class SyncManager:
    """
    Manages background synchronisation between server sources and
    the local filesystem cache / in-memory index.

    Not a QObject — it fires plain Python callbacks.  The EventStore
    facade wires those callbacks to Qt signals.
    """

    def __init__(
        self,
        fs: EventFS,
        index: EventIndex,
        sources: dict[str, CalendarSource],
        config,                          # backend.config.Config
        on_change: Optional[Callable[[], None]] = None,
        on_sync_status: Optional[Callable[[int, Optional[datetime]], None]] = None,
    ):
        self._fs = fs
        self._index = index
        self._sources = sources          # shared reference with EventStore
        self._config = config
        self._on_change = on_change
        self._on_sync_status = on_sync_status

        # CalDAV runtime state (populated by connect)
        self._sessions: dict[str, DAVSession] = {}          # account_name → session
        self._calendars: dict[str, CalendarInfo] = {}       # source_id → CalendarInfo

        # Timing
        self._last_sync_time: Optional[datetime] = None
        self._source_last_attempt: dict[str, datetime] = {}
        self._source_last_success: dict[str, datetime] = {}

        # ICS subscription URLs (source_id → url)
        self._ics_urls: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def last_sync_time(self) -> Optional[datetime]:
        return self._last_sync_time

    def source_last_success(self, source_id: str) -> Optional[datetime]:
        return self._source_last_success.get(source_id)

    def source_last_attempt(self, source_id: str) -> Optional[datetime]:
        return self._source_last_attempt.get(source_id)

    def is_source_outdated(self, source_id: str) -> bool:
        threshold = self.source_outdate_threshold(source_id)
        last = self._source_last_success.get(source_id)
        if last is None:
            return True
        return (datetime.now() - last).total_seconds() > threshold

    def pending_count(self) -> int:
        return len(self._fs.load_pending())

    def get_calendar_info(self, source_id: str) -> Optional[CalendarInfo]:
        return self._calendars.get(source_id)

    # ------------------------------------------------------------------
    # Config helpers
    # ------------------------------------------------------------------

    def source_refresh_interval(self, source_id: str) -> int:
        """Per-source or global refresh interval in seconds."""
        # Check per-source config from account/subscription
        for acc in self._config.nextcloud_accounts:
            if source_id.startswith(f"caldav:{acc.name}:"):
                if acc.refresh_interval is not None:
                    return acc.refresh_interval
        for sub in self._config.ics_subscriptions:
            sid = f"ics:{sub.name}"
            # ICS source_ids are built from the url hash in EventStore
            # so check by iterating
            if source_id in self._ics_urls:
                if sub.refresh_interval is not None:
                    return sub.refresh_interval
        return self._config.refresh_interval

    def source_outdate_threshold(self, source_id: str) -> int:
        for acc in self._config.nextcloud_accounts:
            if source_id.startswith(f"caldav:{acc.name}:"):
                if acc.outdate_threshold is not None:
                    return acc.outdate_threshold
        for sub in self._config.ics_subscriptions:
            if source_id in self._ics_urls:
                if sub.outdate_threshold is not None:
                    return sub.outdate_threshold
        return self._config.outdate_threshold

    # ------------------------------------------------------------------
    # Notification helpers
    # ------------------------------------------------------------------

    def _notify_change(self):
        if self._on_change:
            self._on_change()

    def _notify_sync_status(self):
        if self._on_sync_status:
            self._on_sync_status(self.pending_count(), self._last_sync_time)

    # ------------------------------------------------------------------
    # Source registration
    # ------------------------------------------------------------------

    def register_ics(self, source_id: str, url: str) -> None:
        self._ics_urls[source_id] = url

    # ------------------------------------------------------------------
    # Connect & discover (background)
    # ------------------------------------------------------------------

    def connect_all_in_background(self) -> None:
        """Connect to every configured CalDAV account + fetch ICS, in background."""
        dispatch_task(self._on_connect_all_done, self._do_connect_all)

    def _do_connect_all(self) -> dict:
        """Runs in worker thread."""
        now = datetime.now()
        result: dict = {"caldav": {}, "ics": {}}
        debug_log(Level.DEBUG, f"sync: _do_connect_all running, accounts={len(self._config.nextcloud_accounts)}, ics_urls={len(self._ics_urls)}")

        # --- CalDAV -------------------------------------------------------
        for account in self._config.nextcloud_accounts:
            debug_log(Level.DEBUG, f"sync: connecting CalDAV account '{account.name}' at {account.url}")
            try:
                pw = account.get_password(self._config.password_program)
                debug_log(Level.DEBUG, f"sync: got password for {account.name}")
                session = caldav_connect(account.url, account.username, pw,
                                         account.name)
                self._sessions[account.name] = session
                debug_log(Level.DEBUG, f"sync: connected to {account.name}")

                calendars = caldav_list_calendars(session)
                debug_log(Level.DEBUG, f"sync: found {len(calendars)} calendars for {account.name}")
                for cal_info in calendars:
                    source_id = f"caldav:{account.name}:{cal_info.id}"
                    debug_log(Level.DEBUG, f"sync:   calendar '{cal_info.name}' id={cal_info.id} writable={cal_info.writable}")
                    self._calendars[source_id] = cal_info

                    # Create or update CalendarSource
                    if source_id not in self._sources:
                        src = CalendarSource(
                            id=source_id, name=cal_info.name,
                            color=cal_info.color,
                            account_name=account.name,
                            read_only=not cal_info.writable,
                            source_type="caldav",
                        )
                        self._sources[source_id] = src
                    else:
                        src = self._sources[source_id]
                        src.read_only = not cal_info.writable
                        src.is_outdated = False
                        src.color = cal_info.color

                    # Persist metadata
                    self._fs.save_source_meta(SourceMeta(
                        source_id=source_id, name=cal_info.name,
                        color=cal_info.color,
                        read_only=not cal_info.writable,
                        source_type="caldav", account_name=account.name,
                        last_success=now,
                    ))
                    self._source_last_success[source_id] = now
                    self._source_last_attempt[source_id] = now

                    # Fetch events
                    window_start = now - timedelta(days=120)
                    window_end = now + timedelta(days=240)
                    debug_log(Level.DEBUG, f"sync: fetching events for {source_id}...")
                    raw_events = caldav_fetch_events(session, cal_info,
                                                     window_start, window_end)
                    debug_log(Level.DEBUG, f"sync: got {len(raw_events)} raw events from {source_id}")
                    events = []
                    config_tz = pytz.timezone(self._config.timezone)
                    for ical_text, href in raw_events:
                        try:
                            ev = ImmutableEvent.from_ical(
                                ical_text, source_id, config_tz=config_tz, caldav_href=href)
                            events.append(ev)
                        except Exception as e:
                            debug_log(Level.DEBUG, f"sync:   parse error for {href}: {e}")
                            continue
                    self._fs.replace_source(source_id, events)
                    result["caldav"][source_id] = len(events)
                    debug_log(Level.DEBUG, f"sync: stored {len(events)} events for {source_id}")

            except Exception as e:
                debug_log(Level.ERROR, f"sync: CalDAV error for {account.name}: {e}")

        # --- ICS ----------------------------------------------------------
        for source_id, url in self._ics_urls.items():
            debug_log(Level.DEBUG, f"sync: fetching ICS {source_id}")
            self._source_last_attempt[source_id] = now
            raw = ics_fetch(url)
            if raw is None:
                debug_log(Level.DEBUG, f"sync:   ICS fetch returned None for {source_id}")
                continue
            texts = ics_parse_events(raw)
            events = []
            config_tz = pytz.timezone(self._config.timezone)
            for t in texts:
                try:
                    events.append(ImmutableEvent.from_ical(t, source_id, config_tz=config_tz))
                except Exception:
                    continue
            self._fs.replace_source(source_id, events)
            self._source_last_success[source_id] = now
            debug_log(Level.DEBUG, f"sync: stored {len(events)} events for ICS {source_id}")

            # Persist metadata
            src = self._sources.get(source_id)
            if src:
                self._fs.save_source_meta(SourceMeta(
                    source_id=source_id, name=src.name,
                    color=src.color, read_only=True,
                    source_type="ics", last_success=now,
                ))
            result["ics"][source_id] = len(events)

        self._last_sync_time = now
        return result

    def _on_connect_all_done(self, result):
        """Main-thread callback after connect_all finishes."""
        caldav_counts = result.get("caldav", {})
        ics_counts = result.get("ics", {})
        debug_log(Level.DEBUG, f"sync: connect_all done — CalDAV calendars: {list(caldav_counts.keys())}")
        for sid, count in caldav_counts.items():
            debug_log(Level.DEBUG, f"sync:   {sid}: {count} events")
        for sid, count in ics_counts.items():
            debug_log(Level.DEBUG, f"sync:   {sid}: {count} events")
        self._rebuild_index()
        self._notify_change()
        self._notify_sync_status()

    # ------------------------------------------------------------------
    # Refresh (background)
    # ------------------------------------------------------------------

    def refresh_in_background(self, source_id: Optional[str] = None) -> None:
        dispatch_task(self._on_refresh_done,
                      self._do_refresh, source_id)

    def _do_refresh(self, source_id: Optional[str] = None) -> list[str]:
        """Runs in worker thread.  Returns list of successfully synced source IDs."""
        now = datetime.now()
        synced: list[str] = []

        # Try to connect any missing CalDAV sessions
        self._try_connect_missing()

        ids = [source_id] if source_id else list(self._sources.keys())
        debug_log(Level.DEBUG, f"sync: _do_refresh sources={ids}")

        # Group CalDAV sources by account so we connect once per account
        caldav_by_account: dict[str, list[str]] = {}
        ics_ids: list[str] = []
        for sid in ids:
            src = self._sources.get(sid)
            if src is None:
                continue
            if src.source_type == "caldav":
                caldav_by_account.setdefault(src.account_name, []).append(sid)
            elif src.source_type == "ics":
                ics_ids.append(sid)

        # Refresh CalDAV — connect once per account, then fetch per calendar
        for account_name, source_ids in caldav_by_account.items():
            debug_log(Level.DEBUG, f"sync: connecting CalDAV account '{account_name}' for {len(source_ids)} calendars")
            session = self._sessions.get(account_name)
            if session is None:
                # Find matching account config and connect
                for acc in self._config.nextcloud_accounts:
                    if acc.name == account_name:
                        try:
                            pw = acc.get_password(self._config.password_program)
                            session = caldav_connect(acc.url, acc.username, pw, acc.name)
                            self._sessions[account_name] = session
                            debug_log(Level.DEBUG, f"sync: connected {account_name}")
                        except Exception as e:
                            debug_log(Level.ERROR, f"sync: connect failed for {account_name}: {e}")
                        break
                if session is None:
                    debug_log(Level.DEBUG, f"sync: no account config for {account_name}")
                    continue

            # List calendars once per account
            try:
                calendars = caldav_list_calendars(session)
            except Exception as e:
                debug_log(Level.ERROR, f"sync: list calendars failed for {account_name}: {e}")
                continue

            # Build lookup: source_id → CalendarInfo
            cal_info_map: dict[str, CalendarInfo] = {}
            for cal_info in calendars:
                cid = f"caldav:{account_name}:{cal_info.id}"
                cal_info_map[cid] = cal_info
                self._calendars[cid] = cal_info

            # Fetch each source
            for sid in source_ids:
                self._source_last_attempt[sid] = now
                cal_info = cal_info_map.get(sid)
                if cal_info is None:
                    debug_log(Level.DEBUG, f"sync:   {sid} not found in server calendar list")
                    continue
                try:
                    ok = self._fetch_caldav_source(sid, cal_info, now)
                    if ok:
                        synced.append(sid)
                        debug_log(Level.DEBUG, f"sync:   {sid} OK")
                    else:
                        debug_log(Level.DEBUG, f"sync:   {sid} FAILED")
                except Exception as e:
                    debug_log(Level.ERROR, f"sync:   {sid} exception: {e}")

        # Refresh ICS — each source is independent
        for sid in ics_ids:
            self._source_last_attempt[sid] = now
            try:
                ok = self._refresh_ics(sid, now)
                if ok:
                    synced.append(sid)
                    debug_log(Level.DEBUG, f"sync:   {sid} OK")
                else:
                    debug_log(Level.DEBUG, f"sync:   {sid} FAILED")
            except Exception as e:
                debug_log(Level.ERROR, f"sync:   {sid} exception: {e}")

        if synced:
            self._last_sync_time = now
        return synced

    def _fetch_caldav_source(self, source_id: str, cal_info: CalendarInfo, now: datetime) -> bool:
        """Fetch events for a single CalDAV source (assumes session + cal_info are ready)."""
        src = self._sources.get(source_id)
        if src is None:
            return False
        session = self._sessions.get(src.account_name)
        if session is None:
            return False

        window_start = now - timedelta(days=120)
        window_end = now + timedelta(days=240)
        debug_log(Level.DEBUG, f"sync: fetching {source_id}...")
        raw_events = caldav_fetch_events(session, cal_info, window_start, window_end)
        debug_log(Level.DEBUG, f"sync: got {len(raw_events)} raw events")
        events = []
        config_tz = pytz.timezone(self._config.timezone)
        for ical_text, href in raw_events:
            try:
                ev = ImmutableEvent.from_ical(
                    ical_text, source_id, config_tz=config_tz, caldav_href=href)
                events.append(ev)
            except Exception as e:
                debug_log(Level.DEBUG, f"sync:   parse error: {e}")
                continue
        self._fs.replace_source(source_id, events)
        self._source_last_success[source_id] = now
        debug_log(Level.DEBUG, f"sync: stored {len(events)} events for {source_id}")

        # Update in-memory source color from server
        src.color = cal_info.color

        # Update metadata
        self._fs.save_source_meta(SourceMeta(
            source_id=source_id, name=cal_info.name,
            color=cal_info.color,
            read_only=not cal_info.writable,
            source_type="caldav", account_name=src.account_name,
            last_success=now,
        ))
        return True

    def _refresh_ics(self, source_id: str, now: datetime) -> bool:
        url = self._ics_urls.get(source_id)
        if url is None:
            return False
        raw = ics_fetch(url)
        if raw is None:
            return False
        texts = ics_parse_events(raw)
        events = []
        config_tz = pytz.timezone(self._config.timezone)
        for t in texts:
            try:
                events.append(ImmutableEvent.from_ical(t, source_id, config_tz=config_tz))
            except Exception:
                continue
        self._fs.replace_source(source_id, events)
        self._source_last_success[source_id] = now

        src = self._sources.get(source_id)
        if src:
            self._fs.save_source_meta(SourceMeta(
                source_id=source_id, name=src.name, color=src.color,
                read_only=True, source_type="ics", last_success=now,
            ))
        return True

    def _on_refresh_done(self, synced_ids: list[str]):
        """Main-thread callback after refresh finishes."""
        self._rebuild_index()
        for sid in synced_ids:
            src = self._sources.get(sid)
            if src:
                src.last_sync_time = self._source_last_success.get(sid)
                src.is_outdated = False
        if synced_ids:
            self._last_sync_time = datetime.now()
        self._notify_change()
        self._notify_sync_status()

    # ------------------------------------------------------------------
    # Refresh due sources
    # ------------------------------------------------------------------

    def get_sources_needing_refresh(self) -> list[str]:
        now = datetime.now()
        due: list[str] = []
        for sid in self._sources:
            interval = self.source_refresh_interval(sid)
            if interval <= 0:
                continue
            last = self._source_last_attempt.get(sid)
            if last is None or (now - last).total_seconds() >= interval:
                due.append(sid)
        return due

    def refresh_due_in_background(self) -> None:
        due = self.get_sources_needing_refresh()
        if not due:
            return
        # Single task refreshes all due sources — avoids flooding the
        # thread pool with N individual tasks that would starve
        # higher-priority work like connect_all_in_background.
        dispatch_task(self._on_refresh_done, self._do_refresh, None)

    # ------------------------------------------------------------------
    # Pending sync (background)
    # ------------------------------------------------------------------

    def sync_pending_in_background(self) -> None:
        ops = self._fs.load_pending()
        if not ops:
            return
        dispatch_task(self._on_sync_pending_done, self._do_sync_pending)

    def _do_sync_pending(self) -> dict:
        """Runs in worker thread."""
        ops = self._fs.load_pending()
        result = {"success": 0, "failed": 0, "done_uids": []}

        debug_log(Level.DEBUG, f"sync: {len(ops)} pending ops")
        debug_log(Level.DEBUG, f"sync: _calendars keys: {list(self._calendars.keys())}")
        debug_log(Level.DEBUG, f"sync: _sessions keys: {list(self._sessions.keys())}")

        for op in ops:
            debug_log(Level.DEBUG, f"sync: processing op={op.operation} uid={op.uid} source_id={op.source_id}")
            ok = self._sync_one(op)
            if ok:
                result["success"] += 1
                result["done_uids"].append(op.uid)
            else:
                result["failed"] += 1

        if result["success"] > 0:
            self._last_sync_time = datetime.now()
        return result

    def _sync_one(self, op: PendingOp) -> bool:
        """Execute a single pending operation.  Returns success."""
        source_id = op.source_id
        cal_info = self._calendars.get(source_id)
        src = self._sources.get(source_id)
        debug_log(Level.DEBUG, f"sync: _sync_one op={op.operation} uid={op.uid} source_id={source_id}")
        if cal_info is None:
            debug_log(Level.DEBUG, f"sync: cal_info for {source_id} NOT FOUND in _calendars (keys={list(self._calendars.keys())})")
        if src is None:
            debug_log(Level.DEBUG, f"sync: src for {source_id} NOT FOUND in _sources")
        if cal_info is None or src is None:
            return False
        session = self._sessions.get(src.account_name)
        if session is None:
            debug_log(Level.DEBUG, f"sync: session for {src.account_name} NOT FOUND in _sessions (keys={list(self._sessions.keys())})")
            return False
        debug_log(Level.DEBUG, f"sync: session OK for {src.account_name}")

        if op.operation == "create":
            ev = self._fs.load_event(source_id, op.uid)
            if ev is None:
                return False
            return caldav_save_event(session, cal_info, ev.ical_data)

        elif op.operation == "update":
            ev = self._fs.load_event(source_id, op.uid)
            if ev is None:
                return False
            return caldav_save_event(session, cal_info, ev.ical_data)

        elif op.operation == "delete":
            return caldav_delete_event(session, cal_info, op.uid)

        elif op.operation == "delete_instance":
            if op.instance_start is None:
                return False
            return caldav_add_exdate(session, cal_info, op.uid,
                                     op.instance_start)

        return False

    def _on_sync_pending_done(self, result: dict):
        """Main-thread callback."""
        ops_before = {o.uid: o for o in self._fs.load_pending()}

        for op in ops_before.values():
            uid = op.uid
            if uid in result.get("done_uids", []):
                debug_log(Level.DEBUG, f"sync: success for uid={uid} op={op.operation} source_id={op.source_id}")
                self._fs.remove_pending(uid)
                # For deletes, also erase the cached .ics file from disk
                if op.operation == "delete":
                    debug_log(Level.DEBUG, f"sync: deleting .ics for {uid} from disk")
                    self._fs.delete_event(op.source_id, uid)

        self._rebuild_index()
        self._notify_change()
        self._notify_sync_status()

    # ------------------------------------------------------------------
    # Index rebuild
    # ------------------------------------------------------------------

    def _rebuild_index(self) -> None:
        """Reload all events from filesystem into the in-memory index."""
        self._index.clear()
        # Build uid→op lookup once instead of re-parsing pending.json per event
        state_map = {
            "create": "pending_create",
            "update": "pending_update",
            "delete": "pending_delete",
            "delete_instance": "pending_delete_instance",
        }
        pending_by_uid: dict[str, str] = {}
        for op in self._fs.load_pending():
            pending_by_uid[op.uid] = state_map.get(op.operation, "clean")
        for source_id in self._sources:
            for ev in self._fs.list_events(source_id):
                pending_op = pending_by_uid.get(ev.uid)
                if pending_op and pending_op != ev.sync_state:
                    ev = ev.with_updates(sync_state=pending_op)
                self._index.add(ev)

    # ------------------------------------------------------------------
    # Helper — reconnect missing CalDAV clients
    # ------------------------------------------------------------------

    def _try_connect_missing(self) -> None:
        for acc in self._config.nextcloud_accounts:
            if acc.name in self._sessions:
                continue
            try:
                pw = acc.get_password(self._config.password_program)
                session = caldav_connect(acc.url, acc.username, pw, acc.name)
                self._sessions[acc.name] = session
                for cal_info in caldav_list_calendars(session):
                    cid = f"caldav:{acc.name}:{cal_info.id}"
                    self._calendars[cid] = cal_info
            except Exception:
                pass
