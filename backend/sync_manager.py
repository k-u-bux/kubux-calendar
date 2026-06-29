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

from library.color_utils import get_unused_color
from .event import ImmutableEvent, CalendarSource, SYNC_WINDOW_PAST_DAYS, SYNC_WINDOW_FUTURE_DAYS
from .event_fs import EventFS, SourceMeta, PendingOp
from .event_index import EventIndex
from .network_ops import (
    DAVSession, CalendarInfo,
    caldav_connect, caldav_list_calendars, caldav_fetch_events,
    caldav_save_event, caldav_delete_event, caldav_add_exdate,
    ics_fetch, ics_parse_events,
)
from library.task_dispatch import dispatch_task
from library.log import debug_log, Level


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

        # Cached timezone object (avoids repeated pytz.timezone() calls)
        self._config_tz = pytz.timezone(config.timezone)

        # CalDAV runtime state (populated by connect)
        self._sessions: dict[str, DAVSession] = {}          # account_name → session
        self._calendars: dict[str, CalendarInfo] = {}       # source_id → CalendarInfo

        # Timing
        self._last_sync_time: Optional[datetime] = None
        self._source_last_attempt: dict[str, datetime] = {}
        self._source_last_success: dict[str, datetime] = {}

        # Sync window — tracks what time range the network has actually fetched.
        # None until the first connect/refresh completes.
        self._valid_sync_window_start: Optional[datetime] = None
        self._valid_sync_window_end: Optional[datetime] = None

        # ICS subscription URLs (source_id → url)
        self._ics_urls: dict[str, str] = {}

    # ------------------------------------------------------------------
    # State transfer (for EventStore.clone)
    # ------------------------------------------------------------------

    def copy_state_from(self, other: 'SyncManager') -> None:
        """Transfer runtime sync state from *other* to this fresh SyncManager.

        Used by :meth:`EventStore.clone` to preserve sync timing and live
        CalDAV sessions so the cloned store does not trigger a full re-fetch.
        """
        self._last_sync_time = other._last_sync_time
        self._valid_sync_window_start = other._valid_sync_window_start
        self._valid_sync_window_end = other._valid_sync_window_end
        self._source_last_attempt = dict(other._source_last_attempt)
        self._source_last_success = dict(other._source_last_success)
        self._sessions = dict(other._sessions)       # share live connections
        self._calendars = dict(other._calendars)      # share calendar metadata
        self._ics_urls = dict(other._ics_urls)

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def last_sync_time(self) -> Optional[datetime]:
        return self._last_sync_time

    @property
    def valid_sync_window_start(self) -> Optional[datetime]:
        return self._valid_sync_window_start

    @property
    def valid_sync_window_end(self) -> Optional[datetime]:
        return self._valid_sync_window_end

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
            if sid == source_id:
                if sub.refresh_interval is not None:
                    return sub.refresh_interval
        return self._config.refresh_interval

    def source_outdate_threshold(self, source_id: str) -> int:
        for acc in self._config.nextcloud_accounts:
            if source_id.startswith(f"caldav:{acc.name}:"):
                if acc.outdate_threshold is not None:
                    return acc.outdate_threshold
        for sub in self._config.ics_subscriptions:
            sid = f"ics:{sub.name}"
            if sid == source_id:
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

    def connect_all_in_background(
        self,
        sync_start: Optional[datetime] = None,
        sync_end: Optional[datetime] = None,
    ) -> None:
        """Connect to every configured CalDAV account + fetch ICS, in background.

        *sync_start* / *sync_end* define the time window to fetch.
        If omitted, falls back to ``now ± SYNC_WINDOW_*_DAYS``.
        """
        dispatch_task(self._on_connect_all_done, self._do_connect_all, sync_start, sync_end)

    def _do_connect_all(
        self,
        sync_start: Optional[datetime] = None,
        sync_end: Optional[datetime] = None,
    ) -> dict:
        """Runs in worker thread.  Returns results dict — no shared state mutation."""
        now = datetime.now()
        if sync_start is None:
            sync_start = now - timedelta(days=SYNC_WINDOW_PAST_DAYS)
        if sync_end is None:
            sync_end = now + timedelta(days=SYNC_WINDOW_FUTURE_DAYS)

        result: dict = {
            "caldav": {},
            "ics": {},
            "sessions": {},
            "calendars": {},
            "sources": {},          # source_id → CalendarSource data dict
            "source_last_success": {},
            "source_last_attempt": {},
            "last_sync_time": now,
            "sync_start": sync_start,
            "sync_end": sync_end,
        }
        debug_log(Level.DEBUG, f"sync: _do_connect_all running, accounts={len(self._config.nextcloud_accounts)}, ics_urls={len(self._ics_urls)}")

        # --- CalDAV -------------------------------------------------------
        for account in self._config.nextcloud_accounts:
            debug_log(Level.DEBUG, f"sync: connecting CalDAV account '{account.name}' at {account.url}")
            try:
                pw = account.get_password(self._config.password_program)
                debug_log(Level.DEBUG, f"sync: got password for {account.name}")
                session = caldav_connect(account.url, account.username, pw,
                                         account.name)
                result["sessions"][account.name] = session
                debug_log(Level.DEBUG, f"sync: connected to {account.name}")

                calendars = caldav_list_calendars(session)
                debug_log(Level.DEBUG, f"sync: found {len(calendars)} calendars for {account.name}")
                for cal_info in calendars:
                    source_id = f"caldav:{account.name}:{cal_info.id}"
                    debug_log(Level.DEBUG, f"sync:   calendar '{cal_info.name}' id={cal_info.id} writable={cal_info.writable}")
                    result["calendars"][source_id] = cal_info

                    # Snapshot of source fields to apply on main thread
                    result["sources"][source_id] = {
                        "id": source_id,
                        "name": cal_info.name,
                        "color": cal_info.color,
                        "account_name": account.name,
                        "read_only": not cal_info.writable,
                        "source_type": "caldav",
                        "is_outdated": False,
                    }

                    # Persist metadata (filesystem write is safe from worker)
                    self._fs.save_source_meta(SourceMeta(
                        source_id=source_id, name=cal_info.name,
                        color=cal_info.color,
                        read_only=not cal_info.writable,
                        source_type="caldav", account_name=account.name,
                        last_success=now,
                    ))
                    result["source_last_success"][source_id] = now
                    result["source_last_attempt"][source_id] = now

                    # Fetch events
                    debug_log(Level.DEBUG, f"sync: fetching events for {source_id} in window {sync_start}..{sync_end}...")
                    raw_events = caldav_fetch_events(session, cal_info,
                                                     sync_start, sync_end)
                    debug_log(Level.DEBUG, f"sync: got {len(raw_events)} raw events from {source_id}")
                    events = []
                    for ical_text, href in raw_events:
                        try:
                            ev = ImmutableEvent.from_ical(
                                ical_text, source_id, config_tz=self._config_tz, caldav_href=href)
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
            result["source_last_attempt"][source_id] = now
            raw = ics_fetch(url)
            if raw is None:
                debug_log(Level.DEBUG, f"sync:   ICS fetch returned None for {source_id}")
                continue
            texts = ics_parse_events(raw)
            events = []
            for t in texts:
                try:
                    events.append(ImmutableEvent.from_ical(t, source_id, config_tz=self._config_tz))
                except Exception as e:
                    debug_log(Level.DEBUG, f"sync: ics_event parse — skipping: {e}")
                    continue
            self._fs.replace_source(source_id, events)
            result["source_last_success"][source_id] = now
            debug_log(Level.DEBUG, f"sync: stored {len(events)} events for ICS {source_id}")

            # Snapshot source info for main-thread metadata write
            src = self._sources.get(source_id)
            if src:
                self._fs.save_source_meta(SourceMeta(
                    source_id=source_id, name=src.name,
                    color=src.color, read_only=True,
                    source_type="ics", last_success=now,
                ))
                result["sources"][source_id] = {
                    "id": source_id,
                    "name": src.name,
                    "color": src.color,
                    "read_only": True,
                    "source_type": "ics",
                    "is_outdated": False,
                }
            result["ics"][source_id] = len(events)

        # NOTE: self._calendars, self._sessions, self._sources, self._source_last_*,
        #       self._last_sync_time are NOT written here — they are applied in
        #       _on_connect_all_done on the main thread.
        return result

    def _on_connect_all_done(self, result):
        """Main-thread callback — applies worker results to shared state."""
        caldav_counts = result.get("caldav", {})
        ics_counts = result.get("ics", {})
        debug_log(Level.DEBUG, f"sync: connect_all done — CalDAV calendars: {list(caldav_counts.keys())}")
        for sid, count in caldav_counts.items():
            debug_log(Level.DEBUG, f"sync:   {sid}: {count} events")
        for sid, count in ics_counts.items():
            debug_log(Level.DEBUG, f"sync:   {sid}: {count} events")

        # Apply sessions
        self._sessions.update(result.get("sessions", {}))
        # Apply calendars
        self._calendars.update(result.get("calendars", {}))
        # Apply sources: create new, update existing
        for sid, src_data in result.get("sources", {}).items():
            existing = self._sources.get(sid)
            if existing:
                existing.read_only = src_data["read_only"]
                existing.is_outdated = src_data["is_outdated"]
                existing.color = src_data["color"]
            else:
                color = src_data["color"]
                if not color:
                    used = [s.color for s in self._sources.values()]
                    color = get_unused_color(used)
                self._sources[sid] = CalendarSource(
                    id=src_data["id"],
                    name=src_data["name"],
                    color=color,
                    account_name=src_data["account_name"],
                    read_only=src_data["read_only"],
                    source_type=src_data["source_type"],
                )
        # Apply timing
        self._source_last_success.update(result.get("source_last_success", {}))
        self._source_last_attempt.update(result.get("source_last_attempt", {}))
        self._last_sync_time = result.get("last_sync_time")
        # Apply sync window
        self._valid_sync_window_start = result.get("sync_start")
        self._valid_sync_window_end = result.get("sync_end")

        self._rebuild_index()
        self._notify_change()
        self._notify_sync_status()

    # ------------------------------------------------------------------
    # Refresh (background)
    # ------------------------------------------------------------------

    def refresh_in_background(
        self,
        source_id: Optional[str] = None,
        sync_start: Optional[datetime] = None,
        sync_end: Optional[datetime] = None,
    ) -> None:
        dispatch_task(self._on_refresh_done,
                      self._do_refresh, source_id, sync_start, sync_end)

    def _do_refresh(
        self,
        source_id: Optional[str] = None,
        sync_start: Optional[datetime] = None,
        sync_end: Optional[datetime] = None,
    ) -> dict:
        """Runs in worker thread.  Returns result dict — no shared state mutation."""
        now = datetime.now()
        if sync_start is None:
            sync_start = now - timedelta(days=SYNC_WINDOW_PAST_DAYS)
        if sync_end is None:
            sync_end = now + timedelta(days=SYNC_WINDOW_FUTURE_DAYS)

        result: dict = {
            "synced": [],
            "sessions": {},
            "calendars": {},
            "sources": {},          # source_id → {color, read_only, is_outdated}
            "source_last_success": {},
            "source_last_attempt": {},
            "last_sync_time": None,
            "sync_start": sync_start,
            "sync_end": sync_end,
        }

        # Try to connect any missing CalDAV sessions
        connect_result = self._try_connect_missing()
        result["sessions"].update(connect_result["sessions"])
        result["calendars"].update(connect_result["calendars"])

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
            session = result["sessions"].get(account_name)
            if session is None:
                # Find matching account config and connect
                for acc in self._config.nextcloud_accounts:
                    if acc.name == account_name:
                        try:
                            pw = acc.get_password(self._config.password_program)
                            session = caldav_connect(acc.url, acc.username, pw, acc.name)
                            result["sessions"][account_name] = session
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
                result["calendars"][cid] = cal_info

            # Fetch each source
            for sid in source_ids:
                result["source_last_attempt"][sid] = now
                cal_info = cal_info_map.get(sid)
                if cal_info is None:
                    debug_log(Level.DEBUG, f"sync:   {sid} not found in server calendar list")
                    continue
                try:
                    fetch_result = self._fetch_caldav_source(sid, cal_info, now, sync_start, sync_end)
                    if fetch_result["ok"]:
                        result["synced"].append(sid)
                        result["source_last_success"][sid] = fetch_result.get("last_success", now)
                        if "source_data" in fetch_result:
                            result["sources"][sid] = fetch_result["source_data"]
                        debug_log(Level.DEBUG, f"sync:   {sid} OK")
                    else:
                        debug_log(Level.DEBUG, f"sync:   {sid} FAILED")
                except Exception as e:
                    debug_log(Level.ERROR, f"sync:   {sid} exception: {e}")

        # Refresh ICS — each source is independent
        for sid in ics_ids:
            result["source_last_attempt"][sid] = now
            try:
                fetch_result = self._refresh_ics(sid, now, sync_start, sync_end)
                if fetch_result["ok"]:
                    result["synced"].append(sid)
                    result["source_last_success"][sid] = fetch_result.get("last_success", now)
                    if "source_data" in fetch_result:
                        result["sources"][sid] = fetch_result["source_data"]
                    debug_log(Level.DEBUG, f"sync:   {sid} OK")
                else:
                    debug_log(Level.DEBUG, f"sync:   {sid} FAILED")
            except Exception as e:
                debug_log(Level.ERROR, f"sync:   {sid} exception: {e}")

        if result["synced"]:
            result["last_sync_time"] = now
        return result

    def _fetch_caldav_source(
        self,
        source_id: str,
        cal_info: CalendarInfo,
        now: datetime,
        sync_start: Optional[datetime] = None,
        sync_end: Optional[datetime] = None,
    ) -> dict:
        """Fetch events for a single CalDAV source. Returns result dict — no shared state mutation."""
        result: dict = {"ok": False}
        src = self._sources.get(source_id)
        if src is None:
            return result
        session = self._sessions.get(src.account_name)
        if session is None:
            return result

        if sync_start is None:
            sync_start = now - timedelta(days=SYNC_WINDOW_PAST_DAYS)
        if sync_end is None:
            sync_end = now + timedelta(days=SYNC_WINDOW_FUTURE_DAYS)

        debug_log(Level.DEBUG, f"sync: fetching {source_id} in window {sync_start}..{sync_end}...")
        raw_events = caldav_fetch_events(session, cal_info, sync_start, sync_end)
        debug_log(Level.DEBUG, f"sync: got {len(raw_events)} raw events")
        events = []
        for ical_text, href in raw_events:
            try:
                ev = ImmutableEvent.from_ical(
                    ical_text, source_id, config_tz=self._config_tz, caldav_href=href)
                events.append(ev)
            except Exception as e:
                debug_log(Level.DEBUG, f"sync:   parse error: {e}")
                continue
        self._fs.replace_source(source_id, events)
        debug_log(Level.DEBUG, f"sync: stored {len(events)} events for {source_id}")

        # Source data to apply on main thread
        result["source_data"] = {
            "id": source_id,
            "name": cal_info.name,
            "color": cal_info.color,
            "account_name": src.account_name,
            "read_only": not cal_info.writable,
            "source_type": "caldav",
            "is_outdated": False,
        }
        result["last_success"] = now

        # Update metadata (filesystem write is safe from worker)
        self._fs.save_source_meta(SourceMeta(
            source_id=source_id, name=cal_info.name,
            color=cal_info.color,
            read_only=not cal_info.writable,
            source_type="caldav", account_name=src.account_name,
            last_success=now,
        ))
        result["ok"] = True
        return result

    def _refresh_ics(
        self,
        source_id: str,
        now: datetime,
        sync_start: Optional[datetime] = None,
        sync_end: Optional[datetime] = None,
    ) -> dict:
        """Fetch events for a single ICS source. Returns result dict — no shared state mutation."""
        result: dict = {"ok": False}
        url = self._ics_urls.get(source_id)
        if url is None:
            return result
        raw = ics_fetch(url)
        if raw is None:
            return result
        texts = ics_parse_events(raw)
        events = []
        for t in texts:
            try:
                events.append(ImmutableEvent.from_ical(t, source_id, config_tz=self._config_tz))
            except Exception as e:
                debug_log(Level.DEBUG, f"sync: ics_event parse — skipping: {e}")
                continue
        self._fs.replace_source(source_id, events)
        result["last_success"] = now

        src = self._sources.get(source_id)
        if src:
            self._fs.save_source_meta(SourceMeta(
                source_id=source_id, name=src.name, color=src.color,
                read_only=True, source_type="ics", last_success=now,
            ))
            result["source_data"] = {
                "id": source_id,
                "name": src.name,
                "color": src.color,
                "read_only": True,
                "source_type": "ics",
                "is_outdated": False,
            }
        result["ok"] = True
        return result

    def _on_refresh_done(self, result: dict):
        """Main-thread callback — applies worker results to shared state."""
        # Apply sessions
        self._sessions.update(result.get("sessions", {}))
        # Apply calendars
        self._calendars.update(result.get("calendars", {}))
        # Apply timing
        self._source_last_success.update(result.get("source_last_success", {}))
        self._source_last_attempt.update(result.get("source_last_attempt", {}))
        if result.get("last_sync_time"):
            self._last_sync_time = result["last_sync_time"]

        # Apply sync window
        sync_start = result.get("sync_start")
        sync_end = result.get("sync_end")
        if sync_start is not None:
            self._valid_sync_window_start = sync_start
        if sync_end is not None:
            self._valid_sync_window_end = sync_end

        # Apply source mutations (color, outdated) from fetch results
        for sid, src_data in result.get("sources", {}).items():
            src = self._sources.get(sid)
            if src:
                color = src_data.get("color", src.color)
                if not color:
                    color = src.color
                src.color = color
                src.read_only = src_data.get("read_only", src.read_only)
                src.is_outdated = src_data.get("is_outdated", src.is_outdated)
                src.last_sync_time = result.get("source_last_success", {}).get(sid)

        self._rebuild_index()
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
        # Snapshot shared dicts on the main thread before dispatching.
        # The worker thread must not read self._calendars / self._sessions /
        # self._sources directly — they are mutated by main-thread callbacks.
        calendars_snapshot = dict(self._calendars)
        sessions_snapshot = dict(self._sessions)
        sources_snapshot = dict(self._sources)
        dispatch_task(self._on_sync_pending_done, self._do_sync_pending,
                      calendars_snapshot, sessions_snapshot, sources_snapshot)

    def _do_sync_pending(
        self,
        calendars: dict[str, CalendarInfo],
        sessions: dict[str, DAVSession],
        sources: dict[str, CalendarSource],
    ) -> dict:
        """Runs in worker thread. Returns result dict — no shared state mutation."""
        ops = self._fs.load_pending()
        result = {"success": 0, "failed": 0, "done_uids": [], "last_sync_time": None}

        debug_log(Level.DEBUG, f"sync: {len(ops)} pending ops")
        debug_log(Level.DEBUG, f"sync: calendars keys: {list(calendars.keys())}")
        debug_log(Level.DEBUG, f"sync: sessions keys: {list(sessions.keys())}")

        for op in ops:
            debug_log(Level.DEBUG, f"sync: processing op={op.operation} uid={op.uid} source_id={op.source_id}")
            ok = self._sync_one(op, calendars, sessions, sources)
            if ok:
                result["success"] += 1
                result["done_uids"].append(op.uid)
            else:
                result["failed"] += 1

        if result["success"] > 0:
            result["last_sync_time"] = datetime.now()
        return result

    def _sync_one(
        self,
        op: PendingOp,
        calendars: dict[str, CalendarInfo],
        sessions: dict[str, DAVSession],
        sources: dict[str, CalendarSource],
    ) -> bool:
        """Execute a single pending operation.  Returns success."""
        source_id = op.source_id
        cal_info = calendars.get(source_id)
        src = sources.get(source_id)
        debug_log(Level.DEBUG, f"sync: _sync_one op={op.operation} uid={op.uid} source_id={source_id}")
        if cal_info is None:
            debug_log(Level.DEBUG, f"sync: cal_info for {source_id} NOT FOUND in calendars (keys={list(calendars.keys())})")
        if src is None:
            debug_log(Level.DEBUG, f"sync: src for {source_id} NOT FOUND in sources")
        if cal_info is None or src is None:
            return False
        session = sessions.get(src.account_name)
        if session is None:
            debug_log(Level.DEBUG, f"sync: session for {src.account_name} NOT FOUND in sessions (keys={list(sessions.keys())})")
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
        """Main-thread callback — applies worker results to shared state."""
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

        if result.get("last_sync_time"):
            self._last_sync_time = result["last_sync_time"]

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

    def _try_connect_missing(self) -> dict:
        """Runs in worker thread. Returns result dict — no shared state mutation."""
        result: dict = {"sessions": {}, "calendars": {}}
        for acc in self._config.nextcloud_accounts:
            if acc.name in self._sessions:
                continue
            try:
                pw = acc.get_password(self._config.password_program)
                session = caldav_connect(acc.url, acc.username, pw, acc.name)
                result["sessions"][acc.name] = session
                for cal_info in caldav_list_calendars(session):
                    cid = f"caldav:{acc.name}:{cal_info.id}"
                    result["calendars"][cid] = cal_info
            except Exception as e:
                debug_log(Level.WARN, f"sync: _try_connect_missing failed for {acc.name}: {e}")
        return result