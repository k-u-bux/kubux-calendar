"""
Unified Event Store for Kubux Calendar v2.

Provides the same public API as the v1 EventStore, but delegates to
v2 immutable components: EventFS, EventIndex, SyncManager, and
returns EventView objects instead of CalEvent/EventInstance.

The server is the source of truth. Local cache is disposable.
"""

import json
import os
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
from library.task_dispatch import dispatch_task
from library.timezone_utils import ensure_tz
from library.log import debug_log, Level
from library.color_utils import get_unused_color


class EventStore:
    """
    v2 EventStore — facade over EventFS, EventIndex, SyncManager.

    Implements the same public API as the v1 EventStore so the GUI
    layer requires zero changes.
    """

    def __init__(self, config: Config):
        self.config = config

        # v2 components
        self._config_tz = pytz.timezone(config.timezone)
        self._fs = EventFS(config_tz=self._config_tz)
        self._index = EventIndex()
        self._sources: dict[str, CalendarSource] = {}
        self._sync_manager: Optional[SyncManager] = None

        # CalDAV runtime state (used by sync operations)
        self._accounts: list[dict] = []
        self._caldav_calendars: dict[str, CalendarInfo] = {}
        self._sessions: dict[str, DAVSession] = {}
        self._ics_urls: dict[str, str] = {}

        # State persistence (visibility, colors)
        self._state_file = config.state_file
        self._visibility: dict[str, bool] = {}
        self._user_colors: dict[str, str] = {}  # user-picked, non-negotiable
        self._auto_colors: dict[str, str] = {}  # auto-assigned, can be re-evaluated on collision

        # Callbacks
        self._on_change_callback: Optional[Callable[[], None]] = None
        self._on_sync_status_callback: Optional[Callable[[int, Optional[datetime]], None]] = None


    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------

    def set_on_change_callback(self, callback: Callable[[], None]) -> None:
        self._on_change_callback = callback

    def set_on_sync_status_callback(
        self, callback: Callable[[int, Optional[datetime]], None]
    ) -> None:
        self._on_sync_status_callback = callback

    def _cleanup_orphaned_state(self) -> None:
        """Remove visibility/color state for sources that no longer exist."""
        current_ids = set(self._sources.keys())
        changed = False
        for sid in list(self._visibility.keys()):
            if sid not in current_ids:
                del self._visibility[sid]
                changed = True
        for sid in list(self._user_colors.keys()):
            if sid not in current_ids:
                del self._user_colors[sid]
                changed = True
        for sid in list(self._auto_colors.keys()):
            if sid not in current_ids:
                del self._auto_colors[sid]
                changed = True
        if changed:
            self._save_state()

    def _notify_change(self) -> None:
        self._cleanup_orphaned_state()
        self._apply_source_state()
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
                color=self._user_colors.get(meta.source_id, self._auto_colors.get(meta.source_id, meta.color)),
                account_name=meta.account_name,
                read_only=meta.read_only,
                source_type=meta.source_type,
            )
            if meta.source_id in self._visibility:
                src.visible = self._visibility[meta.source_id]
            self._sources[meta.source_id] = src
            success = True

        # Register ICS subscriptions from config — iterate the config directly
        # so every subscription gets its real URL, regardless of cache state.
        # Always use config color, overriding any cached state.
        config_ics_ids: set[str] = set()
        for sub in self.config.ics_subscriptions:
            sid = f"ics:{sub.name}"
            config_ics_ids.add(sid)
            self._ics_urls[sid] = sub.url
            if sid not in self._sources:
                self._sources[sid] = CalendarSource(
                    id=sid, name=sub.name, color=sub.color,
                    read_only=True, source_type="ics",
                )
            else:
                self._sources[sid].color = sub.color

        # Remove ICS sources from cache that are no longer in the config.
        # Otherwise a subscription removed between runs would persist.
        for sid in list(self._sources.keys()):
            if sid.startswith("ics:") and sid not in config_ics_ids:
                self._sources.pop(sid, None)
                self._visibility.pop(sid, None)
                self._user_colors.pop(sid, None)
                self._auto_colors.pop(sid, None)

        self._apply_source_state()
        self._ensure_source_colors()
        return success

    def _ensure_source_colors(self) -> None:
        """Assign distinct colors to sources that have no explicit color.

        Persists auto-assigned colors to state so they survive restarts.
        If an auto-assigned color collides with another source's final color,
        a new one is picked.
        """
        any_changed = False
        for sid, src in self._sources.items():
            if sid in self._user_colors:
                continue  # user-picked, non-negotiable
            if src.color:
                continue  # explicitly set in config TOML or server-reported

            # Already have a persisted auto-assigned color?
            existing = self._auto_colors.get(sid)

            # Build set of "taken" colors: user-assigned + config-defined + other auto-assigned
            taken = set()
            for s in self._sources.values():
                if s.id == sid:
                    continue
                if s.id in self._user_colors:
                    taken.add(self._user_colors[s.id])
                elif s.id in self._auto_colors:
                    taken.add(self._auto_colors[s.id])
                elif s.color:
                    taken.add(s.color)

            if existing and existing not in taken:
                # Existing auto-color is still distinct — keep it
                src.color = existing
            else:
                # Pick a fresh one
                src.color = get_unused_color(list(taken))
                self._auto_colors[sid] = src.color
                any_changed = True

        if any_changed:
            self._save_state()

    def _apply_source_state(self) -> None:
        """Apply color overrides, outdated status, and per-source threshold.

        Called after source initialization and after sync cycles.
        Uses SyncManager's per-session timing when available; falls
        back to disk metadata only for sources the SyncManager doesn't
        know about yet (pre-initialization).
        """
        for sid, src in self._sources.items():
            if sid in self._user_colors:
                src.color = self._user_colors[sid]
            elif sid in self._auto_colors:
                src.color = self._auto_colors[sid]
            # Set per-source outdate threshold from config
            src.outdate_threshold = self._source_outdate_threshold(sid)
            if self._sync_manager is not None:
                src.is_outdated = self._sync_manager.is_source_outdated(sid)
            else:
                # No SyncManager yet — use disk metadata.
                # If no last_success on disk, data is unverified, not stale.
                meta = self._fs.load_source_meta(sid)
                if meta and meta.last_success:
                    elapsed = (datetime.now() - meta.last_success).total_seconds()
                    src.is_outdated = elapsed > src.outdate_threshold
                else:
                    src.is_outdated = False

    def _source_outdate_threshold(self, source_id: str) -> int:
        """Delegates to SyncManager's per-source or global threshold."""
        if self._sync_manager is not None:
            return self._sync_manager.source_outdate_threshold(source_id)
        return self.config.outdate_threshold

    def load_events_for_source(self, source_id: str) -> int:
        """
        Load cached events for *source_id* from filesystem into index.

        Phase 2 — can be called progressively to keep UI responsive.
        """
        events = self._fs.list_events(source_id)
        for ev in events:
            self._index.add(ev)
        return len(events)

    def get_sources_by_visibility(self) -> tuple[list[str], list[str]]:
        visible: list[str] = []
        invisible: list[str] = []
        for sid in self._sources:
            if self._visibility.get(sid, True):
                visible.append(sid)
            else:
                invisible.append(sid)
        return visible, invisible

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

        # Sort: CalDAV accounts in config order, calendars alphabetical within
        # each account, then ICS subscriptions in config order.
        account_order = {acc.name: i for i, acc in enumerate(self.config.nextcloud_accounts)}
        sub_order = {sub.name: i for i, sub in enumerate(self.config.ics_subscriptions)}

        def sort_key(src: CalendarSource) -> tuple:
            if src.source_type == "caldav":
                # (group 0, account index, calendar name lowercase)
                idx = account_order.get(src.account_name, 999)
                return (0, idx, src.name.lower())
            else:
                # ICS — group 1, subscription index, name lowercase
                idx = sub_order.get(src.name, 999)
                return (1, idx, src.name.lower())

        sources.sort(key=sort_key)

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

    def get_used_colors(self) -> list[str]:
        """Return all colors currently assigned to calendar sources (deduplicated)."""
        return list({src.color for src in self._sources.values()})

    def set_calendar_color(self, calendar_id: str, color: str) -> None:
        if calendar_id in self._sources:
            self._sources[calendar_id].color = color
            self._user_colors[calendar_id] = color
            self._save_state()
            self._notify_change()

    # ------------------------------------------------------------------
    # Get events
    # ------------------------------------------------------------------

    def _is_cache_valid(self, start: datetime, end: datetime) -> bool:
        """Check if the SyncManager has a non-stale sync window covering [start, end]."""
        sm = self._sync_manager
        if sm is None:
            return False
        return sm.is_range_covered(start, end)

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
                            _instance_start=ensure_tz(dtstart, tzid),
                            _instance_end=ensure_tz(dtend, tzid),
                        )
                        results.append(instance)
                except Exception as e:
                    debug_log(Level.WARN, f"store: _expand_instances — falling back to master: {e}")
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

        Never blocks.  If cache window is invalid, triggers a background
        refresh and returns whatever cached data is available immediately.
        The UI will update when the background refresh completes (via
        the on_change callback).

        The requested time window is forwarded to the background fetch so
        the sync layer fetches exactly the range the user is viewing
        (widened by SYNC_WINDOW_*_DAYS).
        """
        if not self._is_cache_valid(start, end):
            self._trigger_background_fetch(start, end)

        return self._build_event_views(start, end, calendar_ids, visible_only)

    def _build_event_views(
        self, start: datetime, end: datetime,
        calendar_ids: Optional[list[str]] = None,
        visible_only: bool = True,
    ) -> list[EventView]:
        """Query index, load from FS, expand recurrences, wrap in EventView."""
        # Ensure start/end are tz-aware (GUI passes naive datetimes)
        if start.tzinfo is None:
            start = start.replace(tzinfo=pytz.UTC)
        if end.tzinfo is None:
            end = end.replace(tzinfo=pytz.UTC)

        # Get candidate master events from index
        candidates = self._index.query_range(start, end)
        debug_log(Level.DEBUG, f"store: query_range returned {len(candidates)} candidates")

        # Filter by calendar
        if calendar_ids is not None:
            candidates = [e for e in candidates if e.source_id in calendar_ids]
            debug_log(Level.DEBUG, f"store: after calendar_ids filter: {len(candidates)}")
        elif visible_only:
            before = len(candidates)
            candidates = [
                e for e in candidates
                if self._visibility.get(e.source_id, True)
            ]
            debug_log(Level.DEBUG, f"store: after visible_only filter: {len(candidates)} (was {before})")

        # Expand recurrences
        instances = self._expand_instances(candidates, start, end)
        debug_log(Level.DEBUG, f"store: after _expand_instances: {len(instances)}")

        # Filter to time range (recurrence expansion may include out-of-range)
        before = len(instances)
        filtered = [
            ev for ev in instances
            if ev.start < end and ev.end > start
        ]
        debug_log(Level.DEBUG, f"store: after time-range filter: {len(filtered)} (was {before})")

        # Build EventView wrappers
        # NOTE: color override is applied here (harmless dict lookup).
        # Outdated status is NOT set here — that would trigger lazy
        # SyncManager creation during a read operation (see _apply_source_state
        # which runs at lifecycle boundaries instead).
        views: list[EventView] = []
        for ev in filtered:
            src = self._sources.get(ev.source_id)
            if src is None:
                debug_log(Level.DEBUG, f"store: skipping event {ev.uid} — source {ev.source_id} not found")
                continue
            if src.id in self._user_colors:
                src.color = self._user_colors[src.id]
            elif src.id in self._auto_colors:
                src.color = self._auto_colors[src.id]
            views.append(EventView(ev, src))

        return views

    def _trigger_background_fetch(
        self,
        sync_start: Optional[datetime] = None,
        sync_end: Optional[datetime] = None,
    ) -> None:
        """
        Kick off a background refresh of all sources without blocking.
        The on_change callback will fire when data arrives.

        *sync_start* / *sync_end* define the time window to fetch from
        the server.  When called from :meth:`get_events`, these are the
        widened viewer window.
        """
        self.refresh_all_in_background(sync_start, sync_end)

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
            config_tz=self._config_tz,
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
        if event.read_only:
            debug_log(Level.DEBUG, f"store: delete uid={event.uid} FAILED — read_only")
            return False

        debug_log(Level.DEBUG, f"store: delete uid={event.uid} source_id={event.source.id}")
        self._fs.add_pending(PendingOp(
            uid=event.uid, source_id=event.source.id, operation="delete"
        ))
        event._set_pending_sync_state("delete")

        updated_ev = event.immutable_event
        self._index.remove(updated_ev.uid)
        self._index.add(updated_ev)

        debug_log(Level.DEBUG, f"store: delete pending ops: {len(self._fs.load_pending())}")

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
                    self._user_colors = state.get('user-assigned-colors', {})
                    self._auto_colors = state.get('auto-assigned-colors', {})
            except Exception as e:
                debug_log(Level.WARN, f"store: _load_state failed — {e}")

    def _save_state(self) -> None:
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            # Import here to avoid circular import at module level.
            from gui.main_window import main_window
            ui_state = main_window._ui_state if main_window is not None else {}
            with open(self._state_file, 'w') as f:
                json.dump(
                    {
                        'ui': ui_state,
                        'visibility': self._visibility,
                        'user-assigned-colors': self._user_colors,
                        'auto-assigned-colors': self._auto_colors,
                    },
                    f, indent=2,
                )
        except Exception as e:
            debug_log(Level.WARN, f"store: _save_state failed — {e}")

    # ------------------------------------------------------------------
    # Sync operations (background)
    # ------------------------------------------------------------------

    def refresh_all_in_background(
        self,
        sync_start: Optional[datetime] = None,
        sync_end: Optional[datetime] = None,
    ) -> None:
        """Connect servers and refresh all data in background.

        *sync_start* / *sync_end* define the time window to fetch.
        If omitted, falls back to ``now ± SYNC_WINDOW_*_DAYS``.
        """
        self._ensure_sync_manager()
        # Register ICS URLs
        for sid, url in self._ics_urls.items():
            self._sync_manager.register_ics(sid, url)
        self._sync_manager.connect_all_in_background(sync_start, sync_end)

    def refresh_in_background(
        self,
        calendar_id: Optional[str] = None,
        sync_start: Optional[datetime] = None,
        sync_end: Optional[datetime] = None,
    ) -> None:
        """Refresh one or all sources in background.

        *sync_start* / *sync_end* define the time window to fetch.
        If omitted, falls back to ``now ± SYNC_WINDOW_*_DAYS``.
        """
        sm = self._ensure_sync_manager()
        for sid, url in self._ics_urls.items():
            sm.register_ics(sid, url)
        sm.refresh_in_background(calendar_id, sync_start, sync_end)

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

    def is_source_outdated(self, source_id: str) -> bool:
        sm = self._ensure_sync_manager()
        return sm.is_source_outdated(source_id)

    # ------------------------------------------------------------------
    # Config reload support
    # ------------------------------------------------------------------

    def clone(self, new_config: Config) -> 'EventStore':
        """Return a new EventStore with *new_config* but the same runtime state.

        Copies the in-memory event index, sources, visibility/color state,
        sync timing, and live CalDAV sessions from *self*.  The original
        EventStore is not touched — callers perform an atomic pointer swap
        (``self.event_store = self.event_store.clone(new_config)``).

        Callbacks are **not** copied — the caller must re-register them.
        """
        import copy as _copy

        new = EventStore.__new__(EventStore)
        new.config = new_config
        new._config_tz = pytz.timezone(new_config.timezone)
        new._state_file = new_config.state_file

        # Fresh filesystem layer (same disk paths — reads the same cache)
        new._fs = EventFS(config_tz=new._config_tz)

        # Copy in-memory data
        new._index = self._index.copy()
        new._sources = _copy.copy(self._sources)  # CalendarSource objects are mutable — shallow copy
        new._visibility = dict(self._visibility)
        new._user_colors = dict(self._user_colors)
        new._auto_colors = dict(self._auto_colors)

        # CalDAV runtime state — share live sessions
        new._accounts = list(self._accounts)
        new._caldav_calendars = dict(self._caldav_calendars)
        new._sessions = dict(self._sessions)

        # ICS URLs from new config
        new._ics_urls = {f"ics:{sub.name}": sub.url for sub in new_config.ics_subscriptions}

        # Reconcile ICS subscription sources against new config:
        #   - Remove subscriptions that were deleted from config
        #   - Add subscriptions that were added to config
        # This ensures the sidebar and event grid reflect changes immediately.
        new_ics_ids = {f"ics:{sub.name}" for sub in new_config.ics_subscriptions}
        old_ics_ids = {
            sid for sid in new._sources
            if sid.startswith("ics:")
        }
        # Remove old ICS sources no longer in config
        for sid in old_ics_ids - new_ics_ids:
            new._sources.pop(sid, None)
            new._visibility.pop(sid, None)
            new._user_colors.pop(sid, None)
            new._auto_colors.pop(sid, None)
            # Also remove from index so they don't render
            new._index.remove_all_for_source(sid)
        # Add new ICS sources from config
        for sid in new_ics_ids - old_ics_ids:
            # Find the matching subscription in config
            sub_name = sid[len("ics:"):]
            for sub in new_config.ics_subscriptions:
                if sub.name == sub_name:
                    new._sources[sid] = CalendarSource(
                        id=sid,
                        name=sub.name,
                        color=sub.color,
                        read_only=True,
                        source_type="ics",
                    )
                    break
        new._ensure_source_colors()

        # Callbacks — caller must re-register
        new._on_change_callback = None
        new._on_sync_status_callback = None

        # SyncManager — create fresh, transfer timing from old
        new._sync_manager = SyncManager(
            fs=new._fs,
            index=new._index,
            sources=new._sources,
            config=new_config,
        )
        if self._sync_manager is not None:
            new._sync_manager.copy_state_from(self._sync_manager)

        return new

    def get_cached_event_count(self) -> int:
        return len(self._index)

    def get_stale_event_count(self) -> int:
        """Count cached events whose mtime (confirmed_at) exceeds the outdate threshold."""
        stale = 0
        for sid, src in self._sources.items():
            for ev in self._fs.list_events(sid):
                if ev.is_outdated(src.outdate_threshold):
                    stale += 1
        return stale

    def mark_pending(self, uid: str, operation: str, source_id: str) -> None:
        """Record a pending sync operation for an event."""
        self._fs.add_pending(PendingOp(
            uid=uid, source_id=source_id,
            operation=operation,
        ))
        self._notify_change()
        self._notify_sync_status()
