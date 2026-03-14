"""
Event Store facade for Kubux Calendar v2.

Provides the public API that the GUI layer calls.  Internally
composes :class:`EventFS`, :class:`EventIndex`, and
:class:`SyncManager` to deliver events to the display.
"""

import json
import hashlib
from datetime import datetime, date, timedelta
from typing import Optional, Callable

import pytz
from icalendar import Calendar as ICalCalendar
from recurring_ical_events import of as recurring_events_of

from .config import Config
from .event import (
    ImmutableEvent, CalendarSource, EventView, RecurrenceRule,
)
from .event_fs import EventFS, SourceMeta, PendingOp
from .event_index import EventIndex
from .sync_manager import SyncManager
from .task_dispatch import dispatch_task, shutdown_tasks


class EventStore:
    """
    Unified event store — single point of contact for the GUI.

    All public methods match the interface expected by
    ``gui/main_window.py`` and ``gui/event_dialog.py``.
    """

    def __init__(self, config: Config):
        self.config = config

        self._fs = EventFS()
        self._index = EventIndex()
        self._sources: dict[str, CalendarSource] = {}
        self._visibility: dict[str, bool] = {}
        self._colors: dict[str, str] = {}
        self._state_file = config.state_file

        self._on_change_callback: Optional[Callable[[], None]] = None
        self._on_sync_status_callback: Optional[
            Callable[[int, Optional[datetime]], None]
        ] = None

        self._sync = SyncManager(
            fs=self._fs,
            index=self._index,
            sources=self._sources,
            config=config,
            on_change=self._notify_change,
            on_sync_status=self._notify_sync_status,
        )

    # === Callbacks =========================================================

    def set_on_change_callback(self, cb: Callable[[], None]) -> None:
        self._on_change_callback = cb
        self._sync._on_change = cb

    def set_on_sync_status_callback(
        self, cb: Callable[[int, Optional[datetime]], None]
    ) -> None:
        self._on_sync_status_callback = cb
        self._sync._on_sync_status = cb

    def _notify_change(self) -> None:
        if self._on_change_callback:
            self._on_change_callback()

    def _notify_sync_status(self) -> None:
        if self._on_sync_status_callback:
            self._on_sync_status_callback(
                self._sync.pending_count(), self._sync.last_sync_time
            )

    # === Initialisation (progressive) ======================================

    def initialize_sources_only(self) -> bool:
        """
        Phase 1 — fast: load source metadata from disk so the sidebar
        can be populated immediately.  No events, no network.
        """
        self._load_state()
        success = False

        # CalDAV sources from persisted metadata
        for account in self.config.nextcloud_accounts:
            for sid in self._fs.list_source_ids():
                if not sid.startswith(f"caldav_{account.name}_"):
                    # Also try the canonical form
                    if not sid == f"caldav:{account.name}" and \
                       not sid.startswith(f"caldav:{account.name}:"):
                        continue
                meta = self._fs.load_source_meta(sid)
                if meta is None:
                    continue
                src = CalendarSource(
                    id=meta.source_id, name=meta.name,
                    color=self._colors.get(meta.source_id, meta.color),
                    account_name=meta.account_name,
                    read_only=meta.read_only,
                    source_type=meta.source_type,
                )
                if meta.last_success:
                    self._sync._source_last_success[meta.source_id] = meta.last_success
                if meta.source_id in self._visibility:
                    src.visible = self._visibility[meta.source_id]
                self._sources[meta.source_id] = src
                success = True

        # ICS sources
        for sub_cfg in self.config.ics_subscriptions:
            sub_id = hashlib.md5(sub_cfg.url.encode()).hexdigest()[:12]
            source_id = f"ics:{sub_id}"
            meta = self._fs.load_source_meta(source_id)

            src = CalendarSource(
                id=source_id,
                name=sub_cfg.name,
                color=self._colors.get(source_id, sub_cfg.color),
                read_only=True,
                source_type="ics",
            )
            if meta and meta.last_success:
                self._sync._source_last_success[source_id] = meta.last_success
            if source_id in self._visibility:
                src.visible = self._visibility[source_id]

            self._sources[source_id] = src
            self._sync.register_ics(source_id, sub_cfg.url)
            success = True

        return success

    def load_events_for_source(self, source_id: str) -> int:
        """Phase 2 — load cached events for one source into the index."""
        events = self._fs.list_events(source_id)
        for ev in events:
            self._index.add(ev)
        return len(events)

    def set_cache_window_from_storage(self) -> None:
        """Phase 2 finalisation — no-op in v2 (index is always valid)."""
        pass

    def get_sources_by_visibility(self) -> tuple[list[str], list[str]]:
        visible = [s for s in self._sources if self._visibility.get(s, True)]
        hidden = [s for s in self._sources if not self._visibility.get(s, True)]
        return visible, hidden

    # === Source queries =====================================================

    def get_calendars(self, visible_only: bool = False) -> list[CalendarSource]:
        srcs = list(self._sources.values())
        if visible_only:
            srcs = [s for s in srcs if self._visibility.get(s.id, True)]
        return srcs

    def get_calendar(self, calendar_id: str) -> Optional[CalendarSource]:
        return self._sources.get(calendar_id)

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

    def get_writable_calendars(self) -> list[CalendarSource]:
        return [s for s in self._sources.values()
                if not s.read_only and s.source_type == "caldav"]

    # === Event queries =====================================================

    def get_events(
        self,
        start: datetime,
        end: datetime,
        calendar_ids: Optional[list[str]] = None,
        visible_only: bool = True,
    ) -> list[EventView]:
        """
        Return display-ready events for the given range.

        Combines IntervalTree lookup with recurrence expansion
        via *recurring_ical_events*.
        """
        return self._query_events(start, end, calendar_ids, visible_only)

    def get_events_from_cache(
        self, start: datetime, end: datetime
    ) -> list[EventView]:
        """Same as get_events but explicitly cache-only (no network)."""
        return self._query_events(start, end)

    def _query_events(
        self,
        start: datetime,
        end: datetime,
        calendar_ids: Optional[list[str]] = None,
        visible_only: bool = True,
    ) -> list[EventView]:
        """
        Internal: query the index, expand recurrences, build EventViews.
        """
        # Resolve source filter
        if calendar_ids is None:
            allowed = {
                s.id
                for s in self._sources.values()
                if not visible_only or self._visibility.get(s.id, True)
            }
        else:
            allowed = {
                cid
                for cid in calendar_ids
                if cid in self._sources
                and (not visible_only or self._visibility.get(cid, True))
            }

        candidates = self._index.query_range(start, end)
        views: list[EventView] = []

        for ev in candidates:
            if ev.source_id not in allowed:
                continue
            src = self._sources.get(ev.source_id)
            if src is None:
                continue

            # Apply color overrides
            if ev.source_id in self._colors:
                src.color = self._colors[ev.source_id]
            src.is_outdated = self._sync.is_source_outdated(ev.source_id)

            if ev.is_recurring:
                instances = self._expand_recurring(ev, start, end)
                for inst in instances:
                    views.append(EventView(inst, src))
            else:
                views.append(EventView(ev, src))

        # Sort by start time
        views.sort(key=lambda v: v.start)
        return views

    def _expand_recurring(
        self, ev: ImmutableEvent, start: datetime, end: datetime
    ) -> list[ImmutableEvent]:
        """Expand a recurring event into instance-views for the range."""
        try:
            cal = ICalCalendar.from_ical(ev.ical_data)
            expanded = recurring_events_of(cal).between(start, end)
            instances: list[ImmutableEvent] = []
            for comp in expanded:
                dtstart = comp.get("DTSTART")
                if dtstart:
                    dt_val = dtstart.dt
                    if isinstance(dt_val, date) and not isinstance(dt_val, datetime):
                        dt_val = datetime.combine(dt_val, datetime.min.time())
                    if dt_val.tzinfo is None:
                        dt_val = pytz.UTC.localize(dt_val)
                    instances.append(ev.as_instance(dt_val))
            return instances
        except Exception:
            # Fallback: return master event
            return [ev]

    # === Event CRUD ========================================================

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
        """
        Create a new event (optimistic).  Immediately visible in cache;
        synced to server in background.
        """
        src = self._sources.get(calendar_id)
        if src is None or src.read_only or src.source_type != "caldav":
            return None

        # Convert RecurrenceRule if needed
        rr = None
        if recurrence is not None:
            if isinstance(recurrence, RecurrenceRule):
                rr = recurrence
            else:
                # Accept anything with .frequency attribute
                rr = RecurrenceRule(
                    frequency=getattr(recurrence, "frequency", "DAILY"),
                    interval=getattr(recurrence, "interval", 1),
                    count=getattr(recurrence, "count", None),
                    until=getattr(recurrence, "until", None),
                    by_day=getattr(recurrence, "by_day", None),
                )

        ev = ImmutableEvent.create_new(
            source_id=calendar_id,
            summary=summary,
            start=start,
            end=end,
            description=description,
            location=location,
            all_day=all_day,
            recurrence=rr,
        )

        # Persist + index
        self._fs.save_event(ev)
        self._fs.add_pending(PendingOp(uid=ev.uid, source_id=calendar_id,
                                        operation="create"))
        self._index.add(ev)

        self._notify_change()
        self._notify_sync_status()
        return EventView(ev, src)

    def update_event(
        self,
        uid: str,
        source_id: str,
        **kwargs,
    ) -> bool:
        """
        Update an event by UID.  Accepts content kwargs
        (summary, start, end, description, location, all_day, recurrence).

        Returns True on success.
        """
        ev = self._index.get(uid)
        if ev is None:
            return False

        src = self._sources.get(source_id)
        if src is None or src.read_only:
            return False

        # Don't downgrade pending_create to pending_update
        new_sync = "pending_update"
        if ev.sync_state == "pending_create":
            new_sync = "pending_create"

        new_ev = ev.with_updates(sync_state=new_sync, **kwargs)

        self._fs.save_event(new_ev)

        # Only add pending if not already pending_create
        if new_sync == "pending_update":
            self._fs.add_pending(PendingOp(uid=uid, source_id=source_id,
                                            operation="update"))
        self._index.add(new_ev)

        self._notify_change()
        self._notify_sync_status()
        return True

    def mark_pending(self, uid: str, operation: str) -> None:
        """
        Explicitly mark an event as pending.

        Used by the GUI for drag-and-drop updates where the event is
        modified through EventView properties and then synced.
        """
        ev = self._index.get(uid)
        if ev is None:
            return
        state_map = {
            "create": "pending_create",
            "update": "pending_update",
            "delete": "pending_delete",
        }
        new_state = state_map.get(operation, "clean")

        # Don't downgrade create → update
        if ev.sync_state == "pending_create" and operation == "update":
            return

        new_ev = ev.with_updates(sync_state=new_state)
        self._fs.save_event(new_ev)
        self._fs.add_pending(PendingOp(uid=uid, source_id=ev.source_id,
                                        operation=operation))
        self._index.add(new_ev)
        self._notify_change()
        self._notify_sync_status()

    def delete_event(self, event_view: EventView) -> bool:
        """
        Delete an event (pending delete, synced in background).

        Accepts an :class:`EventView` (from the GUI).
        """
        ev = event_view.immutable_event
        src = self._sources.get(ev.source_id)
        if src is None or src.read_only:
            return False

        new_ev = ev.with_updates(sync_state="pending_delete")
        self._fs.save_event(new_ev)
        self._fs.add_pending(PendingOp(uid=ev.uid, source_id=ev.source_id,
                                        operation="delete"))
        self._index.add(new_ev)

        self._notify_change()
        self._notify_sync_status()
        return True

    def delete_recurring_instance(
        self, event_view: EventView, instance_start: datetime
    ) -> bool:
        """Delete a specific instance of a recurring event."""
        ev = event_view.immutable_event
        src = self._sources.get(ev.source_id)
        if src is None or src.read_only or not ev.is_recurring:
            return False

        new_ev = ev.with_updates(sync_state="pending_delete_instance")
        self._fs.save_event(new_ev)
        self._fs.add_pending(PendingOp(
            uid=ev.uid, source_id=ev.source_id,
            operation="delete_instance", instance_start=instance_start,
        ))
        self._index.add(new_ev)

        self._notify_change()
        self._notify_sync_status()
        return True

    def move_event(
        self, event_view: EventView, new_calendar_id: str
    ) -> Optional[EventView]:
        """Move an event to a different calendar (create + delete)."""
        ev = event_view.immutable_event
        old_src = self._sources.get(ev.source_id)
        new_src = self._sources.get(new_calendar_id)
        if (old_src is None or old_src.read_only or
                new_src is None or new_src.read_only or
                new_src.source_type != "caldav"):
            return None
        if old_src.id == new_calendar_id:
            return event_view

        # Create in new calendar
        new_view = self.create_event(
            calendar_id=new_calendar_id,
            summary=ev.summary,
            start=ev.start,
            end=ev.end,
            description=ev.description,
            location=ev.location,
            all_day=ev.all_day,
            recurrence=ev.recurrence,
        )
        if new_view is None:
            return None

        # Delete from old calendar
        self.delete_event(event_view)

        # Remove old event from index immediately
        self._index.remove(ev.uid)
        self._fs.delete_event(ev.source_id, ev.uid)

        return new_view

    # === Sync façade =======================================================

    def refresh_all_in_background(self) -> None:
        self._sync.connect_all_in_background()

    def refresh_in_background(self, calendar_id: Optional[str] = None) -> None:
        self._sync.refresh_in_background(calendar_id)

    def refresh_due_sources_in_background(self) -> None:
        self._sync.refresh_due_in_background()

    def sync_pending_in_background(self) -> None:
        self._sync.sync_pending_in_background()

    # === Status queries ====================================================

    def get_pending_sync_count(self) -> int:
        return self._sync.pending_count()

    def get_last_sync_time(self) -> Optional[datetime]:
        return self._sync.last_sync_time

    def get_source_last_sync(self, source_id: str) -> Optional[datetime]:
        return self._sync.source_last_success(source_id)

    def get_source_refresh_interval(self, source_id: str) -> int:
        return self._sync._source_refresh_interval(source_id)

    def is_source_outdated(self, source_id: str) -> bool:
        return self._sync.is_source_outdated(source_id)

    def get_cached_event_count(self) -> int:
        return len(self._index)

    def has_pending_sync(self, event_uid: str) -> bool:
        base_uid = event_uid.split("_")[0] if "_" in event_uid else event_uid
        for op in self._fs.load_pending():
            if op.uid == base_uid:
                return True
        return False

    # === State persistence ==================================================

    def _load_state(self) -> None:
        if self._state_file.exists():
            try:
                with open(self._state_file, "r") as f:
                    state = json.load(f)
                self._visibility = state.get("visibility", {})
                self._colors = state.get("colors", {})
            except Exception:
                pass

    def _save_state(self) -> None:
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            # Preserve existing sections
            existing: dict = {}
            if self._state_file.exists():
                with open(self._state_file, "r") as f:
                    existing = json.load(f)
            existing["visibility"] = self._visibility
            existing["colors"] = self._colors
            with open(self._state_file, "w") as f:
                json.dump(existing, f, indent=2)
        except Exception:
            pass

    def get_state(self) -> dict:
        return {"visibility": self._visibility.copy(),
                "colors": self._colors.copy()}

    def set_state(self, state: dict) -> None:
        self._visibility = state.get("visibility", {})
        self._colors = state.get("colors", {})
