"""
Unified Event Store for Kubux Calendar v2.

Provides the same public API as the v1 EventStore, but delegates to
v2 immutable components: EventFS, EventIndex, SyncManager, and
returns EventView objects instead of CalEvent/EventInstance.

The server is the source of truth. Local cache is disposable.
"""

import json
import os
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional, Callable
import pytz
from icalendar import Calendar as ICalCalendar
import recurring_ical_events

from .config import Config
from .event import ImmutableEvent, CalendarSource, EventView, RecurrenceRule
from .event_fs import EventFS, SourceMeta, PendingOp
from .event_index import EventIndex
from .sync_manager import SyncManager
from .network_ops import (
    caldav_connect, caldav_list_calendars, DAVSession, CalendarInfo,
)
from .task_dispatch import dispatch_task


def _debug_print(message: str) -> None:
    import sys
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {message}", file=sys.stderr)


def _load_recurring_ical_events(ical_data: str, start: datetime, end: datetime) -> list:
    """Expand recurring events in *ical_data* into the [start, end] range."""
    try:
        cal = ICalCalendar.from_ical(ical_data)
        expanded = recurring_ical_events.of(cal).between(start, end)
        return expanded
    except Exception:
        return []


class EventStore:
    """
    v2 EventStore — facade over EventFS, EventIndex, SyncManager.

    Implements the same public API as the v1 EventStore so the GUI
    layer requires zero changes.
    """

    CACHE_WINDOW_PAST_MONTHS = 4
    CACHE_WINDOW_FUTURE_MONTHS = 8
    PREFETCH_MARGIN_PAST_MONTHS = 2
    PREFETCH_MARGIN_FUTURE_MONTHS = 4

    def __init__(self, config: Config):
        self.config = config

        # v2 components
        self._fs = EventFS()
        self._index = EventIndex()
        self._sources: dict[str, CalendarSource] = {}
        self._sync_manager: Optional[SyncManager] = None

        # CalDAV runtime state (used by sync operations)
        self._accounts: list[dict] = []
        self._caldav_calendars: dict[str, CalendarInfo] = {}
        self._sessions: dict[str, DAVSession] = {}
        self._ics_urls: dict[str, str] = {}

        # State persistence (visibility, colors — same as v1)
        self._state_file = config.state_file
        self._visibility: dict[str, bool] = {}
        self._colors: dict[str, str] = {}

        # Cache window tracking
        self._cache_start: Optional[datetime] = None
        self._cache_end: Optional[datetime] = None

        # Callbacks
        self._on_change_callback: Optional[Callable[[], None]] = None
        self._on_sync_status_callback: Optional[Callable[[int, Optional[datetime]], None]] = None

        # Purge old v1 cache on first v2 launch
        self._maybe_purge_v1_cache()

    # ------------------------------------------------------------------
    # Cache cleanup
    # ------------------------------------------------------------------

    def _maybe_purge_v1_cache(self) -> None:
        """Delete the old v1 cache directory if its criteria are met."""
        xdg_data = os.environ.get(
            "XDG_DATA_HOME", os.path.expanduser("~/.local/share")
        )
        v1_storage = Path(xdg_data) / "kubux-calendar"
        if v1_storage.is_dir():
            try:
                shutil.rmtree(v1_storage)
                _debug_print(f"Purged old v1 cache: {v1_storage}")
            except Exception as e:
                _debug_print(f"Could not purge v1 cache: {e}")

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------

    def set_on_change_callback(self, callback: Callable[[], None]) -> None:
        self._on_change_callback = callback

    def set_on_sync_status_callback(
        self, callback: Callable[[int, Optional[datetime]], None]
    ) -> None:
        self._on_sync_status_callback = callback

    def _notify_change(self) -> None:
        if self._on_change_callback:
            self._on_change_callback()

    def _notify_sync_status(self, pending_count: int = 0, last_sync_time=None) -> None:
        if self._on_sync_status_callback:
            if pending_count == 0 and last_sync_time is None:
                pending_count = self.get_pending_sync_count()
                last_sync_time = self.get_last_sync_time()
            self._on_sync_status_callback(pending_count, last_sync_time)

    # ------------------------------------------------------------------
    # Initialization (Phase 1 + 2)
    # ------------------------------------------------------------------

    def initialize_sources_only(self) -> bool:
        """
        Load source metadata from filesystem cache (fast, no network).

        Phase 1 — populates the sidebar without events.
        """
        self._load_state()

        # Walk source IDs found in EventFS
        source_ids = self._fs.list_source_ids()
        success = False
        for sid in source_ids:
            meta = self._fs.load_source_meta(sid)
            if meta is None:
                continue
            src = CalendarSource(
                id=meta.source_id,
                name=meta.name,
                color=self._colors.get(meta.source_id, meta.color),
                account_name=meta.account_name,
                read_only=meta.read_only,
                source_type=meta.source_type,
            )
            if meta.source_id in self._visibility:
                src.visible = self._visibility[meta.source_id]
            self._sources[meta.source_id] = src
            success = True

        # Also register ICS URLs from source metadata for refresh
        for sid in source_ids:
            meta = self._fs.load_source_meta(sid)
            if meta and meta.source_type == "ics":
                self._ics_urls[sid] = sid  # URL stored in source name? No — need to get from config

        # Build mapping from config for ICS URLs
        for sub in self.config.ics_subscriptions:
            for sid in source_ids:
                meta = self._fs.load_source_meta(sid)
                if meta and meta.name == sub.name and meta.source_type == "ics":
                    self._ics_urls[sid] = sub.url

        return success

    def load_events_for_source(self, source_id: str) -> int:
        """
        Load cached events for *source_id* from filesystem into index.

        Phase 2 — can be called progressively to keep UI responsive.
        """
        events = self._fs.list_events(source_id)
        for ev in events:
            self._index.add(ev)
        return len(events)

    def set_cache_window_from_storage(self) -> None:
        """Set cache window based on now (avoids immediate network fetch)."""
        if self._cache_start is not None and self._cache_end is not None:
            return
        if len(self._index) == 0:
            return
        now = datetime.now()
        self._cache_start = now - timedelta(days=self.CACHE_WINDOW_PAST_MONTHS * 30)
        self._cache_end = now + timedelta(days=self.CACHE_WINDOW_FUTURE_MONTHS * 30)

    def get_sources_by_visibility(self) -> tuple[list[str], list[str]]:
        visible: list[str] = []
        invisible: list[str] = []
        for sid in self._sources:
            if self._visibility.get(sid, True):
                visible.append(sid)
            else:
                invisible.append(sid)
        return visible, invisible

    def initialize(self) -> bool:
        """Legacy — load everything at once."""
        success = self.initialize_sources_only()
        for sid in list(self._sources.keys()):
            self.load_events_for_source(sid)
        return success

    # ------------------------------------------------------------------
    # SyncManager lazy init
    # ------------------------------------------------------------------

    def _ensure_sync_manager(self) -> SyncManager:
        if self._sync_manager is None:
            self._sync_manager = SyncManager(
                fs=self._fs,
                index=self._index,
                sources=self._sources,
                config=self.config,
                on_change=self._notify_change,
                on_sync_status=self._notify_sync_status,
            )
        return self._sync_manager

    # ------------------------------------------------------------------
    # Calendar source access
    # ------------------------------------------------------------------

    def get_calendars(self, visible_only: bool = False) -> list[CalendarSource]:
        sources = list(self._sources.values())
        if visible_only:
            sources = [s for s in sources if self._visibility.get(s.id, True)]
        return sources

    def get_calendar(self, calendar_id: str) -> Optional[CalendarSource]:
        return self._sources.get(calendar_id)

    def get_writable_calendars(self) -> list[CalendarSource]:
        return [s for s in self._sources.values() if not s.read_only]

    def set_calendar_visibility(self, calendar_id: str, visible: bool) -> None:
        if calendar_id in self._sources:
            self._visibility[calendar_id] = visible
            self._sources[calendar_id].visible = visible
            self._save_state()
            self._notify_change()

    def set_calendar_color(self, calendar_id: str, color: str) -> None:
        if calendar_id in self._sources:
            self._sources[calendar_id].color = color
            self._colors[calendar_id] = color
            self._save_state()
            self._notify_change()

    # ------------------------------------------------------------------
    # Get events
    # ------------------------------------------------------------------

    def _is_cache_valid(self, start: datetime, end: datetime) -> bool:
        if self._cache_start is None or self._cache_end is None:
            return False
        if start < self._cache_start or end > self._cache_end:
            return False
        past_margin = timedelta(days=self.PREFETCH_MARGIN_PAST_MONTHS * 30)
        future_margin = timedelta(days=self.PREFETCH_MARGIN_FUTURE_MONTHS * 30)
        if start < self._cache_start + past_margin:
            return False
        if end > self._cache_end - future_margin:
            return False
        return True

    def _expand_instances(
        self, events: list[ImmutableEvent],
        start: datetime, end: datetime
    ) -> list[ImmutableEvent]:
        """Expand recurring events into instances in [start, end]."""
        results: list[ImmutableEvent] = []
        for ev in events:
            if ev.is_recurring:
                try:
                    ical = ICalCalendar.from_ical(ev.ical_data)
                    expanded = recurring_ical_events.of(ical).between(start, end)
                    for comp in expanded:
                        dtstart_prop = comp.get("DTSTART")
                        dtstart = dtstart_prop.dt
                        dtend_prop = comp.get("DTEND")
                        dtend = dtend_prop.dt if dtend_prop else dtstart

                        # Use the expanded component's TZID if present,
                        # otherwise fall back to the master event's TZID.
                        tzid = dtstart_prop.params.get("TZID") if hasattr(dtstart_prop, 'params') else None
                        instance = ev.with_updates(
                            _instance_start=_ensure_tz(dtstart, tzid),
                            _instance_end=_ensure_tz(dtend, tzid),
                        )
                        results.append(instance)
                except Exception:
                    # Fall back to master event if expansion fails
                    results.append(ev)
            else:
                results.append(ev)
        return results

    def get_events_from_cache(
        self, start: datetime, end: datetime,
    ) -> list[EventView]:
        """
        Return events from local cache only — no network.
        Used for initial render.
        """
        return self._build_event_views(start, end)

    def get_events(
        self,
        start: datetime,
        end: datetime,
        calendar_ids: Optional[list[str]] = None,
        visible_only: bool = True,
    ) -> list[EventView]:
        """
        Return EventView objects for the given time range.

        Triggers network fetch if cache window is invalid.
        """
        if not self._is_cache_valid(start, end):
            center = start
            window_start = center - timedelta(days=self.CACHE_WINDOW_PAST_MONTHS * 30)
            window_end = center + timedelta(days=self.CACHE_WINDOW_FUTURE_MONTHS * 30)
            self._fetch_into_cache(window_start, window_end)

        return self._build_event_views(start, end, calendar_ids, visible_only)

    def _build_event_views(
        self, start: datetime, end: datetime,
        calendar_ids: Optional[list[str]] = None,
        visible_only: bool = True,
    ) -> list[EventView]:
        """Query index, load from FS, expand recurrences, wrap in EventView."""
        import sys
        # Ensure start/end are tz-aware (GUI passes naive datetimes)
        if start.tzinfo is None:
            start = start.replace(tzinfo=pytz.UTC)
        if end.tzinfo is None:
            end = end.replace(tzinfo=pytz.UTC)

        # Get candidate master events from index
        candidates = self._index.query_range(start, end)
        print(f"DEBUG _build_event_views: index.query_range returned {len(candidates)} candidates", file=sys.stderr)

        # Filter by calendar
        if calendar_ids is not None:
            candidates = [e for e in candidates if e.source_id in calendar_ids]
            print(f"DEBUG _build_event_views: after calendar_ids filter: {len(candidates)}", file=sys.stderr)
        elif visible_only:
            before = len(candidates)
            candidates = [
                e for e in candidates
                if self._visibility.get(e.source_id, True)
            ]
            print(f"DEBUG _build_event_views: after visible_only filter: {len(candidates)} (was {before})", file=sys.stderr)

        # Expand recurrences
        instances = self._expand_instances(candidates, start, end)
        print(f"DEBUG _build_event_views: after _expand_instances: {len(instances)}", file=sys.stderr)

        # Filter to time range (recurrence expansion may include out-of-range)
        before = len(instances)
        filtered = [
            ev for ev in instances
            if ev.start < end and ev.end > start
        ]
        print(f"DEBUG _build_event_views: after time-range filter: {len(filtered)} (was {before})", file=sys.stderr)

        # Apply color overrides and outdated status
        views: list[EventView] = []
        for ev in filtered:
            src = self._sources.get(ev.source_id)
            if src is None:
                print(f"DEBUG _build_event_views: skipping event {ev.uid} — source {ev.source_id} not found", file=sys.stderr)
                continue
            if src.id in self._colors:
                src.color = self._colors[src.id]
            src.is_outdated = self.is_source_outdated(src.id)
            views.append(EventView(ev, src))

        return views

    def _fetch_into_cache(self, start: datetime, end: datetime) -> None:
        """
        Fetch events from CalDAV servers and ICS subscriptions.
        This is a blocking call — used by get_events when cache is invalid.
        """
        _debug_print(f"Fetching events {start.date()} to {end.date()}")
        now = datetime.now()

        # Build account list from config
        for account in self.config.nextcloud_accounts:
            if account.name in self._sessions:
                continue
            try:
                pw = account.get_password(self.config.password_program)
                session = caldav_connect(
                    account.url, account.username, pw, account.name
                )
                self._sessions[account.name] = session
                for cal_info in caldav_list_calendars(session):
                    cid = f"caldav:{account.name}:{cal_info.id}"
                    self._caldav_calendars[cid] = cal_info
                    if cid not in self._sources:
                        src = CalendarSource(
                            id=cid, name=cal_info.name,
                            color=cal_info.color if cal_info.color != "#4285f4" else account.color,
                            account_name=account.name,
                            read_only=not cal_info.writable,
                            source_type="caldav",
                        )
                        self._sources[cid] = src

                    # Fetch events
                    from .network_ops import caldav_fetch_events
                    raw = caldav_fetch_events(session, cal_info, start, end)
                    events = []
                    config_tz = pytz.timezone(self.config.timezone)
                    for ical_text, href in raw:
                        try:
                            ev = ImmutableEvent.from_ical(
                                ical_text, cid, config_tz=config_tz, caldav_href=href,
                            )
                            events.append(ev)
                        except Exception:
                            continue
                    self._fs.replace_source(cid, events)

                    # Persist source metadata
                    self._fs.save_source_meta(SourceMeta(
                        source_id=cid, name=cal_info.name,
                        color=cal_info.color,
                        read_only=not cal_info.writable,
                        source_type="caldav",
                        account_name=account.name,
                        last_success=now,
                    ))
            except Exception as e:
                _debug_print(f"CalDAV {account.name}: {e}")

        # ICS subscriptions
        for sid, url in self._ics_urls.items():
            from .network_ops import ics_fetch, ics_parse_events
            raw = ics_fetch(url)
            if raw is None:
                continue
            texts = ics_parse_events(raw)
            events = []
            config_tz = pytz.timezone(self.config.timezone)
            for t in texts:
                try:
                    ev = ImmutableEvent.from_ical(t, sid, config_tz=config_tz)
                    events.append(ev)
                except Exception:
                    continue
            self._fs.replace_source(sid, events)

            src = self._sources.get(sid)
            if src:
                self._fs.save_source_meta(SourceMeta(
                    source_id=sid, name=src.name, color=src.color,
                    read_only=True, source_type="ics", last_success=now,
                ))

        self._rebuild_index()
        self._cache_start = start
        self._cache_end = end

    def _rebuild_index(self) -> None:
        """Rebuild in-memory index from filesystem cache."""
        self._index.clear()
        for sid in self._sources:
            for ev in self._fs.list_events(sid):
                self._index.add(ev)

    # ------------------------------------------------------------------
    # Event CRUD
    # ------------------------------------------------------------------

    def create_event(
        self,
        calendar_id: str,
        summary: str,
        start: datetime,
        end: datetime,
        description: str = "",
        location: str = "",
        all_day: bool = False,
        recurrence=None,
    ) -> Optional[EventView]:
        """Create a new event (pending_create)."""
        src = self._sources.get(calendar_id)
        if not src or src.read_only or src.source_type != "caldav":
            return None

        # Convert RecurrenceRule if needed
        rrule = None
        if recurrence:
            if hasattr(recurrence, 'frequency'):
                rrule = RecurrenceRule(
                    frequency=recurrence.frequency,
                    interval=getattr(recurrence, 'interval', 1),
                    count=getattr(recurrence, 'count', None),
                    until=getattr(recurrence, 'until', None),
                    by_day=getattr(recurrence, 'by_day', None),
                )
            elif isinstance(recurrence, RecurrenceRule):
                rrule = recurrence

        ev = ImmutableEvent.create_new(
            source_id=calendar_id,
            summary=summary,
            start=start,
            end=end,
            description=description,
            location=location,
            all_day=all_day,
            recurrence=rrule,
        )

        self._fs.save_event(ev)
        self._fs.add_pending(PendingOp(uid=ev.uid, source_id=calendar_id, operation="create"))
        self._index.add(ev)
        self._notify_change()
        self._notify_sync_status()
        return EventView(ev, src)

    def update_event(self, event: EventView) -> bool:
        """
        Update an event.  *event* should be the EventView returned by
        get_events(), with any mutations already applied via setters.
        """
        if event.read_only:
            return False

        # Flush dirty fields into a new ImmutableEvent
        new_ev = event.flush_updates()

        # Sync state is now pending_update (set by flush_updates)
        self._fs.save_event(new_ev)
        self._fs.add_pending(PendingOp(uid=new_ev.uid, source_id=new_ev.source_id, operation="update"))
        self._index.remove(new_ev.uid)
        self._index.add(new_ev)
        self._notify_change()
        self._notify_sync_status()
        return True

    def move_event(self, event: EventView, new_calendar_id: str) -> Optional[EventView]:
        """
        Move an event to a different calendar.
        Creates in new calendar, deletes from old.
        """
        if event.read_only:
            return None

        old_source = event.source
        new_source = self._sources.get(new_calendar_id)
        if not new_source or new_source.read_only or new_source.source_type != "caldav":
            return None

        # Same calendar — no-op
        if old_source.id == new_calendar_id:
            return event

        # Create in new calendar
        new_ev = self.create_event(
            calendar_id=new_calendar_id,
            summary=event.summary,
            start=event.start,
            end=event.end,
            description=event.description,
            location=event.location,
            all_day=event.all_day,
            recurrence=event.recurrence,
        )
        if new_ev is None:
            return None

        # Delete from old calendar
        self.delete_event(event)

        return new_ev

    def delete_event(self, event: EventView) -> bool:
        """Delete an event (pending_delete)."""
        import sys
        if event.read_only:
            print(f"DEBUG delete_event: uid={event.uid} FAILED — read_only", file=sys.stderr)
            return False

        print(f"DEBUG delete_event: uid={event.uid} source_id={event.source.id} — adding PendingOp delete", file=sys.stderr)
        self._fs.add_pending(PendingOp(
            uid=event.uid, source_id=event.source.id, operation="delete"
        ))
        event._set_pending_sync_state("delete")

        # Update the in-memory index so the display picks up the pending state
        updated_ev = event.immutable_event
        self._index.remove(updated_ev.uid)
        self._index.add(updated_ev)

        # Debug: log current pending ops count
        pending = self._fs.load_pending()
        print(f"DEBUG delete_event: after add_pending — {len(pending)} pending ops total", file=sys.stderr)
        for op in pending:
            if op.uid == event.uid:
                print(f"DEBUG delete_event:   found PendingOp for {event.uid}: op={op.operation} source_id={op.source_id}", file=sys.stderr)

        self._notify_change()
        self._notify_sync_status()
        return True

    def delete_recurring_instance(self, event: EventView, instance_start: datetime) -> bool:
        """Delete a specific recurring instance (adds EXDATE)."""
        if event.read_only or not event.is_recurring:
            return False

        self._fs.add_pending(PendingOp(
            uid=event.uid, source_id=event.source.id,
            operation="delete_instance", instance_start=instance_start,
        ))
        event._set_pending_sync_state("delete_instance")
        self._notify_change()
        self._notify_sync_status()
        return True

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------

    def _load_state(self) -> None:
        if self._state_file.exists():
            try:
                with open(self._state_file, 'r') as f:
                    state = json.load(f)
                    self._visibility = state.get('visibility', {})
                    self._colors = state.get('colors', {})
            except Exception:
                pass

    def _save_state(self) -> None:
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._state_file, 'w') as f:
                json.dump(
                    {'visibility': self._visibility, 'colors': self._colors},
                    f, indent=2,
                )
        except Exception:
            pass

    def get_state(self) -> dict:
        return {'visibility': self._visibility.copy(), 'colors': self._colors.copy()}

    def set_state(self, state: dict) -> None:
        self._visibility = state.get('visibility', {})
        self._colors = state.get('colors', {})

    # ------------------------------------------------------------------
    # Sync operations (background)
    # ------------------------------------------------------------------

    def refresh_all_in_background(self) -> None:
        """Connect servers and refresh all data in background."""
        self._ensure_sync_manager()
        # Register ICS URLs
        for sid, url in self._ics_urls.items():
            self._sync_manager.register_ics(sid, url)
        self._sync_manager.connect_all_in_background()

    def refresh_in_background(self, calendar_id: Optional[str] = None) -> None:
        """Refresh one or all sources in background."""
        sm = self._ensure_sync_manager()
        for sid, url in self._ics_urls.items():
            sm.register_ics(sid, url)
        sm.refresh_in_background(calendar_id)

    def refresh_due_sources_in_background(self) -> None:
        """Refresh sources that are due in background."""
        sm = self._ensure_sync_manager()
        sm.refresh_due_in_background()

    def sync_pending_in_background(self) -> None:
        """Sync pending changes in background."""
        sm = self._ensure_sync_manager()
        sm.sync_pending_in_background()

    # ------------------------------------------------------------------
    # Sync operations (blocking — legacy)
    # ------------------------------------------------------------------

    def refresh(self, calendar_id: Optional[str] = None) -> None:
        """Blocking refresh (used by old code paths)."""
        sm = self._ensure_sync_manager()
        synced = sm._do_refresh(calendar_id)
        for sid in synced:
            src = self._sources.get(sid)
            if src:
                src.last_sync_time = sm.source_last_success(sid)
                src.is_outdated = False
        self._rebuild_index()
        self._notify_change()

    def refresh_due_sources(self) -> list[str]:
        """Refresh due sources (blocking)."""
        sm = self._ensure_sync_manager()
        due = sm.get_sources_needing_refresh()
        for sid in due:
            self.refresh(sid)
        return due

    def sync_pending_changes(self) -> tuple[int, int]:
        """Sync pending changes (blocking). Returns (success, fail)."""
        sm = self._ensure_sync_manager()
        result = sm._do_sync_pending()
        for uid in result.get("done_uids", []):
            self._fs.remove_pending(uid)
        self._rebuild_index()
        self._notify_change()
        self._notify_sync_status()
        return (result.get("success", 0), result.get("failed", 0))

    # ------------------------------------------------------------------
    # Sync status
    # ------------------------------------------------------------------

    def get_sources_needing_refresh(self) -> list[str]:
        sm = self._ensure_sync_manager()
        return sm.get_sources_needing_refresh()

    def get_pending_sync_count(self) -> int:
        return len(self._fs.load_pending())

    def get_last_sync_time(self) -> Optional[datetime]:
        sm = self._ensure_sync_manager()
        return sm.last_sync_time

    def get_source_last_sync(self, source_id: str) -> Optional[datetime]:
        sm = self._ensure_sync_manager()
        return sm.source_last_success(source_id)

    def get_source_refresh_interval(self, source_id: str) -> int:
        sm = self._ensure_sync_manager()
        return sm.source_refresh_interval(source_id)

    def is_source_outdated(self, source_id: str) -> bool:
        sm = self._ensure_sync_manager()
        return sm.is_source_outdated(source_id)

    def get_cached_event_count(self) -> int:
        return len(self._index)

    def has_pending_sync(self, event_uid: str) -> bool:
        uid = event_uid.split('_')[0] if '_' in event_uid else event_uid
        for op in self._fs.load_pending():
            if op.uid == uid:
                return True
        return False

    # ------------------------------------------------------------------
    # CalDAV client access (for compatibility — avoid touching directly)
    # ------------------------------------------------------------------

    @property
    def _repository(self):
        """Compatibility shim for main_window.py accessing
        ``self.event_store._repository.mark_pending(...)``.
        We provide a minimal interface.  The new code path uses
        :meth:`update_event` instead, but the drag-drop handler
        in main_window.py still uses the old pattern.
        """
        return _RepositoryCompat(self)


class _RepositoryCompat:
    """
    Minimal compatibility shim so that the drag-drop handler in
    ``main_window.py`` can still call ``_repository.mark_pending()``.
    """

    def __init__(self, store: EventStore):
        self._store = store

    def mark_pending(self, uid: str, operation: str) -> None:
        self._store._fs.add_pending(PendingOp(
            uid=uid, source_id="",
            operation=operation,
        ))
        self._store._notify_change()
        self._store._notify_sync_status()

    def get_pending_events(self) -> list:
        return []

    def get_pending_count(self) -> int:
        return self._store.get_pending_sync_count()


def _ensure_tz(dt, tzid: Optional[str] = None):
    """Ensure *dt* is timezone-aware (default UTC, or *tzid* if given)."""
    if isinstance(dt, date) and not isinstance(dt, datetime):
        dt = datetime.combine(dt, datetime.min.time())
    if dt.tzinfo is None:
        if tzid:
            try:
                dt = pytz.timezone(tzid).localize(dt)
            except Exception:
                dt = dt.replace(tzinfo=pytz.UTC)
        else:
            dt = dt.replace(tzinfo=pytz.UTC)
    return dt
