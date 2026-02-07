"""
Event Wrapper for GUI compatibility.

Provides a decorated event interface that wraps ImmutableEvent with calendar metadata
to meet GUI expectations while maintaining v2 backend architecture.
"""

from typing import Any
from datetime import datetime
from .event import ImmutableEvent
from .timezone_utils import to_local_datetime


class DecoratedEvent:
    """
    GUI-facing event with calendar metadata.
    
    Wraps an ImmutableEvent and CalendarSource to provide all properties
    that GUI components expect while maintaining v2 backend architecture.
    """
    
    def __init__(self, event: ImmutableEvent, calendar_source):
        """
        Initialize decorated event.
        
        Args:
            event: ImmutableEvent from v2 backend
            calendar_source: CalendarSource with metadata (name, color, etc.)
        """
        # Import here to avoid circular dependency
        from .event_store import CalendarSource
        if not isinstance(calendar_source, CalendarSource):
            raise TypeError(f"calendar_source must be CalendarSource, got {type(calendar_source)}")
        
        # Type check for event
        if not isinstance(event, ImmutableEvent):
            raise TypeError(f"event must be ImmutableEvent, got {type(event)}")
        
        self._event = event
        self._calendar_source = calendar_source
        
        # Map sync_state to GUI's sync_status
        self._sync_status_map = {
            "clean": "",
            "pending_create": "pending",
            "pending_update": "pending", 
            "pending_delete": "pending",
            "error": "failed"
        }
    
    # === ImmutableEvent Properties (delegated) ===
    
    @property
    def uuid(self) -> str:
        return self._event.uuid
    
    @property 
    def uid(self) -> str:
        """Alias for uuid for GUI compatibility."""
        return self._event.uuid
    
    @property
    def source_id(self) -> str:
        return self._event.source_id
    
    @property
    def summary(self) -> str:
        return self._event.summary
    
    @property 
    def description(self) -> str:
        return self._event.description
    
    @property
    def location(self) -> str:
        return self._event.location
    
    @property
    def all_day(self) -> bool:
        return self._event.is_all_day
    
    @property
    def is_recurring(self) -> bool:
        return self._event.is_recurring
    
    # === Time Properties with GUI compatibility ===
    
    @property
    def start(self) -> datetime:
        """
        Start datetime (GUI expects this name, not start_tz).
        
        Returns timezone-aware datetime in local timezone for display.
        """
        return self.start_tz
    
    @property
    def end(self) -> datetime:
        """
        End datetime (GUI expects this name, not end_tz).
        
        Returns timezone-aware datetime in local timezone for display.
        """
        return self.end_tz
    
    @property
    def start_tz(self) -> datetime:
        """Start datetime with proper timezone (from ImmutableEvent)."""
        return self._event.start_tz
    
    @property
    def end_tz(self) -> datetime:
        """End datetime with proper timezone (from ImmutableEvent)."""
        return self._event.end_tz
    
    @property
    def start_utc(self) -> datetime:
        """Start datetime in UTC (for IntervalTree indexing)."""
        return self._event.start_utc
    
    @property
    def end_utc(self) -> datetime:
        """End datetime in UTC (for IntervalTree indexing)."""
        return self._event.end_utc
    
    # === Calendar Metadata Properties ===
    
    @property
    def calendar_name(self) -> str:
        """Display name of calendar (from CalendarSource)."""
        return self._calendar_source.name
    
    @property
    def calendar_color(self) -> str:
        """Color for event display (from CalendarSource)."""
        return self._calendar_source.color
    
    @property
    def source(self):
        """CalendarSource object with metadata."""
        return self._calendar_source
    
    @property
    def read_only(self) -> bool:
        """Whether calendar is read-only (ICS subscriptions)."""
        return self._calendar_source.read_only
    
    @property
    def sync_status(self) -> str:
        """
        Sync status for GUI display.
        
        Maps v2 sync_state enum to GUI's expected string values:
        - "" (empty string) = not tracked/synced
        - "pending" = waiting for sync
        - "syncing" = in progress (not in v2)
        - "synced" = done (not in v2) 
        - "failed" = error
        """
        sync_state_str = self._event.sync_state.value
        return self._sync_status_map.get(sync_state_str, "")
    
    @property 
    def sync_state(self):
        """Direct access to v2 sync_state enum."""
        return self._event.sync_state
    
    # === Utility Properties ===
    
    @property
    def duration(self):
        """Event duration."""
        return self._event.duration
    
    @property
    def is_outdated(self) -> bool:
        """Whether source data is outdated."""
        return getattr(self._calendar_source, 'is_outdated', False)
    
    # === Delegated Methods ===
    
    def with_summary(self, new_summary: str):
        """Return new decorated event with updated summary."""
        new_event = self._event.with_summary(new_summary)
        return DecoratedEvent(new_event, self._calendar_source)
    
    def with_description(self, new_description: str):
        """Return new decorated event with updated description."""
        new_event = self._event.with_description(new_description)
        return DecoratedEvent(new_event, self._calendar_source)
    
    def with_location(self, new_location: str):
        """Return new decorated event with updated location."""
        new_event = self._event.with_location(new_location)
        return DecoratedEvent(new_event, self._calendar_source)
    
    def with_times(self, new_start: datetime, new_end: datetime):
        """Return new decorated event with updated start/end times."""
        new_event = self._event.with_times(new_start, new_end)
        return DecoratedEvent(new_event, self._calendar_source)
    
    def with_pending_create(self):
        """Return new decorated event marked as pending create."""
        new_event = self._event.with_pending_create()
        return DecoratedEvent(new_event, self._calendar_source)
    
    def with_pending_update(self):
        """Return new decorated event marked as pending update."""
        new_event = self._event.with_pending_update()
        return DecoratedEvent(new_event, self._calendar_source)
    
    def with_pending_delete(self):
        """Return new decorated event marked as pending delete."""
        new_event = self._event.with_pending_delete()
        return DecoratedEvent(new_event, self._calendar_source)
    
    def with_clean(self):
        """Return new decorated event marked as clean (synced)."""
        new_event = self._event.with_clean()
        return DecoratedEvent(new_event, self._calendar_source)
    
    # === Utility Methods ===
    
    def __getattr__(self, name: str) -> Any:
        """
        Forward any other attribute access to the underlying ImmutableEvent.
        
        This ensures backward compatibility for any additional properties
        that might be accessed by GUI components.
        """
        try:
            return getattr(self._event, name)
        except AttributeError as e:
            # Provide more informative error
            raise AttributeError(
                f"'{self.__class__.__name__}' object has no attribute '{name}', "
                f"and underlying ImmutableEvent also lacks attribute '{name}'. "
                f"DecoratedEvent properties: {[p for p in dir(self) if not p.startswith('_')]}"
            ) from e
    
    def __repr__(self) -> str:
        return f"DecoratedEvent({self.summary!r}, calendar={self.calendar_name!r}, start={self.start})"
    
    def __str__(self) -> str:
        return f"{self.summary} ({self.calendar_name}, {self.start.strftime('%Y-%m-%d %H:%M')})"
