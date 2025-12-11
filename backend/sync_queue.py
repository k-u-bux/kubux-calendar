"""
Sync Queue for offline-first event management.

Stores pending changes locally and attempts to sync them to the server.
Changes persist across application restarts.
"""

import json
import uuid
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional, Callable
from enum import Enum


class SyncOperation(Enum):
    """Types of sync operations."""
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    DELETE_INSTANCE = "delete_instance"  # For deleting recurring event instances


class SyncStatus(Enum):
    """Status of a pending change."""
    PENDING = "pending"
    SYNCING = "syncing"
    FAILED = "failed"
    SYNCED = "synced"


@dataclass
class PendingChange:
    """A pending change to be synced to the server."""
    id: str
    operation: SyncOperation
    calendar_id: str
    event_uid: str  # UID of the event being modified
    event_data: dict  # Serialized EventData
    created_at: datetime
    retry_count: int = 0
    last_error: str = ""
    status: SyncStatus = SyncStatus.PENDING
    
    # For DELETE_INSTANCE operation
    instance_start: Optional[str] = None  # ISO format datetime string
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "operation": self.operation.value,
            "calendar_id": self.calendar_id,
            "event_uid": self.event_uid,
            "event_data": self.event_data,
            "created_at": self.created_at.isoformat(),
            "retry_count": self.retry_count,
            "last_error": self.last_error,
            "status": self.status.value,
            "instance_start": self.instance_start,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'PendingChange':
        """Create from dictionary (JSON deserialization)."""
        return cls(
            id=data["id"],
            operation=SyncOperation(data["operation"]),
            calendar_id=data["calendar_id"],
            event_uid=data["event_uid"],
            event_data=data["event_data"],
            created_at=datetime.fromisoformat(data["created_at"]),
            retry_count=data.get("retry_count", 0),
            last_error=data.get("last_error", ""),
            status=SyncStatus(data.get("status", "pending")),
            instance_start=data.get("instance_start"),
        )


class SyncQueue:
    """
    Manages a queue of pending changes to sync with the CalDAV server.
    
    Changes are persisted to disk so they survive application restarts.
    """
    
    MAX_RETRIES = 5
    
    def __init__(self, queue_file: Path):
        """
        Initialize the sync queue.
        
        Args:
            queue_file: Path to the JSON file for persisting the queue
        """
        self._queue_file = queue_file
        self._pending: dict[str, PendingChange] = {}  # id -> PendingChange
        self._on_change_callback: Optional[Callable[[], None]] = None
        
        # Load existing queue from disk
        self._load()
    
    def set_on_change_callback(self, callback: Callable[[], None]) -> None:
        """Set callback to be invoked when queue changes."""
        self._on_change_callback = callback
    
    def _notify_change(self) -> None:
        """Notify listeners of queue changes."""
        if self._on_change_callback:
            self._on_change_callback()
    
    def _load(self) -> None:
        """Load queue from disk."""
        if self._queue_file.exists():
            try:
                with open(self._queue_file, 'r') as f:
                    data = json.load(f)
                    for item in data.get("pending", []):
                        change = PendingChange.from_dict(item)
                        # Reset SYNCING status to PENDING on load (sync was interrupted)
                        if change.status == SyncStatus.SYNCING:
                            change.status = SyncStatus.PENDING
                        self._pending[change.id] = change
            except Exception as e:
                print(f"Error loading sync queue: {e}", file=__import__('sys').stderr)
    
    def _save(self) -> None:
        """Save queue to disk."""
        try:
            self._queue_file.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "pending": [change.to_dict() for change in self._pending.values()]
            }
            with open(self._queue_file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"Error saving sync queue: {e}", file=__import__('sys').stderr)
    
    def add_create(self, calendar_id: str, event_uid: str, event_data: dict) -> str:
        """
        Add a create operation to the queue.
        
        Args:
            calendar_id: Target calendar ID
            event_uid: UID of the new event
            event_data: Serialized event data
        
        Returns:
            ID of the pending change
        """
        change_id = str(uuid.uuid4())
        change = PendingChange(
            id=change_id,
            operation=SyncOperation.CREATE,
            calendar_id=calendar_id,
            event_uid=event_uid,
            event_data=event_data,
            created_at=datetime.now(),
        )
        self._pending[change_id] = change
        self._save()
        self._notify_change()
        return change_id
    
    def add_update(self, calendar_id: str, event_uid: str, event_data: dict) -> str:
        """
        Add an update operation to the queue.
        
        If there's already a pending CREATE for this event, just update the event data.
        If there's already a pending UPDATE, replace it.
        
        Args:
            calendar_id: Calendar ID
            event_uid: UID of the event to update
            event_data: Updated event data
        
        Returns:
            ID of the pending change
        """
        # Check for existing pending change for this event
        for change in self._pending.values():
            if change.event_uid == event_uid:
                if change.operation == SyncOperation.CREATE:
                    # Just update the event data in the pending CREATE
                    change.event_data = event_data
                    self._save()
                    self._notify_change()
                    return change.id
                elif change.operation == SyncOperation.UPDATE:
                    # Replace the pending UPDATE
                    change.event_data = event_data
                    change.retry_count = 0
                    change.status = SyncStatus.PENDING
                    self._save()
                    self._notify_change()
                    return change.id
        
        # No existing change, add new UPDATE
        change_id = str(uuid.uuid4())
        change = PendingChange(
            id=change_id,
            operation=SyncOperation.UPDATE,
            calendar_id=calendar_id,
            event_uid=event_uid,
            event_data=event_data,
            created_at=datetime.now(),
        )
        self._pending[change_id] = change
        self._save()
        self._notify_change()
        return change_id
    
    def add_delete(self, calendar_id: str, event_uid: str, event_data: dict) -> str:
        """
        Add a delete operation to the queue.
        
        If there's a pending CREATE for this event, just remove it from queue.
        If there's a pending UPDATE, replace it with DELETE.
        
        Args:
            calendar_id: Calendar ID
            event_uid: UID of the event to delete
            event_data: Event data (needed for the delete operation)
        
        Returns:
            ID of the pending change (empty string if no change needed)
        """
        # Check for existing pending change for this event
        for change_id, change in list(self._pending.items()):
            if change.event_uid == event_uid:
                if change.operation == SyncOperation.CREATE:
                    # Event was created locally but never synced - just remove from queue
                    del self._pending[change_id]
                    self._save()
                    self._notify_change()
                    return ""  # No pending change needed
                elif change.operation == SyncOperation.UPDATE:
                    # Convert UPDATE to DELETE
                    change.operation = SyncOperation.DELETE
                    change.event_data = event_data
                    change.retry_count = 0
                    change.status = SyncStatus.PENDING
                    self._save()
                    self._notify_change()
                    return change.id
        
        # No existing change, add new DELETE
        change_id = str(uuid.uuid4())
        change = PendingChange(
            id=change_id,
            operation=SyncOperation.DELETE,
            calendar_id=calendar_id,
            event_uid=event_uid,
            event_data=event_data,
            created_at=datetime.now(),
        )
        self._pending[change_id] = change
        self._save()
        self._notify_change()
        return change_id
    
    def add_delete_instance(
        self, calendar_id: str, event_uid: str, event_data: dict, instance_start: datetime
    ) -> str:
        """
        Add a delete-instance operation to the queue (for recurring events).
        
        Args:
            calendar_id: Calendar ID
            event_uid: UID of the recurring event
            event_data: Event data
            instance_start: Start time of the instance to delete
        
        Returns:
            ID of the pending change
        """
        change_id = str(uuid.uuid4())
        change = PendingChange(
            id=change_id,
            operation=SyncOperation.DELETE_INSTANCE,
            calendar_id=calendar_id,
            event_uid=event_uid,
            event_data=event_data,
            created_at=datetime.now(),
            instance_start=instance_start.isoformat(),
        )
        self._pending[change_id] = change
        self._save()
        self._notify_change()
        return change_id
    
    def mark_syncing(self, change_id: str) -> None:
        """Mark a change as currently syncing."""
        if change_id in self._pending:
            self._pending[change_id].status = SyncStatus.SYNCING
            self._save()
    
    def mark_synced(self, change_id: str) -> None:
        """Mark a change as successfully synced and remove from queue."""
        if change_id in self._pending:
            del self._pending[change_id]
            self._save()
            self._notify_change()
    
    def mark_failed(self, change_id: str, error: str) -> None:
        """Mark a change as failed."""
        if change_id in self._pending:
            change = self._pending[change_id]
            change.status = SyncStatus.FAILED
            change.last_error = error
            change.retry_count += 1
            self._save()
            self._notify_change()
    
    def reset_to_pending(self, change_id: str) -> None:
        """Reset a change back to pending status (for retry)."""
        if change_id in self._pending:
            self._pending[change_id].status = SyncStatus.PENDING
            self._save()
    
    def get_pending_changes(self) -> list[PendingChange]:
        """Get all changes ready for sync (PENDING or FAILED, not currently syncing)."""
        return [
            c for c in self._pending.values() 
            if c.status in (SyncStatus.PENDING, SyncStatus.FAILED) and c.retry_count < self.MAX_RETRIES
        ]
    
    def get_all_changes(self) -> list[PendingChange]:
        """Get all changes in the queue."""
        return list(self._pending.values())
    
    def get_pending_count(self) -> int:
        """Get number of pending changes."""
        return len(self._pending)
    
    def has_pending_for_event(self, event_uid: str) -> bool:
        """Check if there are pending changes for a specific event."""
        return any(c.event_uid == event_uid for c in self._pending.values())
    
    def get_pending_for_event(self, event_uid: str) -> Optional[PendingChange]:
        """Get pending change for a specific event, if any."""
        for change in self._pending.values():
            if change.event_uid == event_uid:
                return change
        return None
    
    def clear(self) -> None:
        """Clear all pending changes (use with caution!)."""
        self._pending.clear()
        self._save()
        self._notify_change()
