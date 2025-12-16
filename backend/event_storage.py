"""
Persistent Event Storage for Kubux Calendar.

Abstract base class and implementations for storing events on disk.
This enables offline-first operation where events survive app restarts.
"""

import json
import os
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Optional
import sys


def _debug_print(msg: str) -> None:
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] STORAGE: {msg}", file=sys.stderr)


class StoredEvent:
    """
    Event as stored on disk with sync metadata.
    
    Separate from CalEvent which is the in-memory runtime representation.
    """
    def __init__(
        self,
        uid: str,
        source_id: str,
        raw_ical: str,
        etag: Optional[str] = None,
        last_modified: Optional[datetime] = None,
        local_modified: Optional[datetime] = None,
        pending_operation: Optional[str] = None,
        caldav_href: Optional[str] = None,
    ):
        self.uid = uid
        self.source_id = source_id
        self.raw_ical = raw_ical
        self.etag = etag
        self.last_modified = last_modified  # Server's timestamp
        self.local_modified = local_modified  # Our local modification time
        self.pending_operation = pending_operation  # None, "create", "update", "delete"
        self.caldav_href = caldav_href  # URL for CalDAV operations
    
    def to_dict(self) -> dict:
        return {
            "uid": self.uid,
            "source_id": self.source_id,
            "raw_ical": self.raw_ical,
            "etag": self.etag,
            "last_modified": self.last_modified.isoformat() if self.last_modified else None,
            "local_modified": self.local_modified.isoformat() if self.local_modified else None,
            "pending_operation": self.pending_operation,
            "caldav_href": self.caldav_href,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'StoredEvent':
        last_mod = None
        if data.get("last_modified"):
            last_mod = datetime.fromisoformat(data["last_modified"])
        
        local_mod = None
        if data.get("local_modified"):
            local_mod = datetime.fromisoformat(data["local_modified"])
        
        return cls(
            uid=data["uid"],
            source_id=data["source_id"],
            raw_ical=data["raw_ical"],
            etag=data.get("etag"),
            last_modified=last_mod,
            local_modified=local_mod,
            pending_operation=data.get("pending_operation"),
            caldav_href=data.get("caldav_href"),
        )


class SourceMetadata:
    """
    Metadata about a calendar source for sync tracking.
    """
    def __init__(
        self,
        source_id: str,
        last_attempt: Optional[datetime] = None,
        last_success: Optional[datetime] = None,
        ctag: Optional[str] = None,
        sync_token: Optional[str] = None,
    ):
        self.source_id = source_id
        self.last_attempt = last_attempt
        self.last_success = last_success
        self.ctag = ctag  # CalDAV collection ctag
        self.sync_token = sync_token  # CalDAV sync-token
    
    def to_dict(self) -> dict:
        return {
            "source_id": self.source_id,
            "last_attempt": self.last_attempt.isoformat() if self.last_attempt else None,
            "last_success": self.last_success.isoformat() if self.last_success else None,
            "ctag": self.ctag,
            "sync_token": self.sync_token,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'SourceMetadata':
        last_attempt = None
        if data.get("last_attempt"):
            last_attempt = datetime.fromisoformat(data["last_attempt"])
        
        last_success = None
        if data.get("last_success"):
            last_success = datetime.fromisoformat(data["last_success"])
        
        return cls(
            source_id=data["source_id"],
            last_attempt=last_attempt,
            last_success=last_success,
            ctag=data.get("ctag"),
            sync_token=data.get("sync_token"),
        )


class EventStorageBackend(ABC):
    """
    Abstract base class for event storage backends.
    
    Implementations must handle persistence (JSON, SQLite, etc).
    """
    
    @abstractmethod
    def load_events(self, source_id: str) -> list[StoredEvent]:
        """Load all events for a source."""
        pass
    
    @abstractmethod
    def save_event(self, event: StoredEvent) -> None:
        """Save or update a single event."""
        pass
    
    @abstractmethod
    def delete_event(self, source_id: str, uid: str) -> None:
        """Delete an event."""
        pass
    
    @abstractmethod
    def get_event(self, source_id: str, uid: str) -> Optional[StoredEvent]:
        """Get a single event by UID."""
        pass
    
    @abstractmethod
    def get_all_uids(self, source_id: str) -> set[str]:
        """Get all UIDs for a source (for sync comparison)."""
        pass
    
    @abstractmethod
    def load_source_metadata(self, source_id: str) -> Optional[SourceMetadata]:
        """Load metadata for a source."""
        pass
    
    @abstractmethod
    def save_source_metadata(self, metadata: SourceMetadata) -> None:
        """Save metadata for a source."""
        pass
    
    @abstractmethod
    def list_sources(self) -> list[str]:
        """List all source IDs with stored data."""
        pass


class JsonEventStorage(EventStorageBackend):
    """
    JSON file-based event storage.
    
    Structure:
    - {storage_dir}/events/{source_id}.json - events for each source
    - {storage_dir}/sources/{source_id}.json - metadata for each source
    """
    
    def __init__(self, storage_dir: Path):
        self.storage_dir = Path(storage_dir)
        self.events_dir = self.storage_dir / "events"
        self.sources_dir = self.storage_dir / "sources"
        
        # Create directories
        self.events_dir.mkdir(parents=True, exist_ok=True)
        self.sources_dir.mkdir(parents=True, exist_ok=True)
        
        _debug_print(f"Initialized JSON storage at {self.storage_dir}")
    
    def _source_id_to_filename(self, source_id: str) -> str:
        """Convert source_id to safe filename."""
        # Replace characters that are problematic in filenames
        return source_id.replace(":", "_").replace("/", "_") + ".json"
    
    def _events_file(self, source_id: str) -> Path:
        return self.events_dir / self._source_id_to_filename(source_id)
    
    def _source_file(self, source_id: str) -> Path:
        return self.sources_dir / self._source_id_to_filename(source_id)
    
    def load_events(self, source_id: str) -> list[StoredEvent]:
        """Load all events for a source."""
        file_path = self._events_file(source_id)
        if not file_path.exists():
            return []
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            events = []
            for event_data in data.get("events", []):
                try:
                    events.append(StoredEvent.from_dict(event_data))
                except Exception as e:
                    _debug_print(f"Error loading event: {e}")
            
            _debug_print(f"Loaded {len(events)} events from {source_id}")
            return events
        except Exception as e:
            _debug_print(f"Error loading events from {source_id}: {e}")
            return []
    
    def _save_events_list(self, source_id: str, events: list[StoredEvent]) -> None:
        """Save full event list for a source."""
        file_path = self._events_file(source_id)
        data = {
            "source_id": source_id,
            "updated": datetime.now().isoformat(),
            "events": [e.to_dict() for e in events]
        }
        
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            _debug_print(f"Saved {len(events)} events for {source_id}")
        except Exception as e:
            _debug_print(f"Error saving events for {source_id}: {e}")
    
    def save_event(self, event: StoredEvent) -> None:
        """Save or update a single event."""
        events = self.load_events(event.source_id)
        
        # Find and update or append
        found = False
        for i, e in enumerate(events):
            if e.uid == event.uid:
                events[i] = event
                found = True
                break
        
        if not found:
            events.append(event)
        
        self._save_events_list(event.source_id, events)
    
    def delete_event(self, source_id: str, uid: str) -> None:
        """Delete an event."""
        events = self.load_events(source_id)
        events = [e for e in events if e.uid != uid]
        self._save_events_list(source_id, events)
    
    def get_event(self, source_id: str, uid: str) -> Optional[StoredEvent]:
        """Get a single event by UID."""
        events = self.load_events(source_id)
        for e in events:
            if e.uid == uid:
                return e
        return None
    
    def get_all_uids(self, source_id: str) -> set[str]:
        """Get all UIDs for a source."""
        events = self.load_events(source_id)
        return {e.uid for e in events}
    
    def load_source_metadata(self, source_id: str) -> Optional[SourceMetadata]:
        """Load metadata for a source."""
        file_path = self._source_file(source_id)
        if not file_path.exists():
            return None
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return SourceMetadata.from_dict(data)
        except Exception as e:
            _debug_print(f"Error loading source metadata for {source_id}: {e}")
            return None
    
    def save_source_metadata(self, metadata: SourceMetadata) -> None:
        """Save metadata for a source."""
        file_path = self._source_file(metadata.source_id)
        
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(metadata.to_dict(), f, indent=2)
        except Exception as e:
            _debug_print(f"Error saving source metadata for {metadata.source_id}: {e}")
    
    def list_sources(self) -> list[str]:
        """List all source IDs with stored data."""
        sources = set()
        
        # From events directory
        for f in self.events_dir.glob("*.json"):
            try:
                with open(f, 'r', encoding='utf-8') as file:
                    data = json.load(file)
                    if "source_id" in data:
                        sources.add(data["source_id"])
            except:
                pass
        
        # From sources directory
        for f in self.sources_dir.glob("*.json"):
            try:
                with open(f, 'r', encoding='utf-8') as file:
                    data = json.load(file)
                    if "source_id" in data:
                        sources.add(data["source_id"])
            except:
                pass
        
        return list(sources)
    
    def bulk_save_events(self, source_id: str, events: list[StoredEvent]) -> None:
        """
        Efficient bulk save - replaces all events for a source.
        
        Used during initial load or full sync.
        """
        self._save_events_list(source_id, events)


def get_default_storage_dir() -> Path:
    """Get the default storage directory respecting XDG."""
    xdg_data = os.environ.get('XDG_DATA_HOME', os.path.expanduser('~/.local/share'))
    return Path(xdg_data) / 'kubux-calendar' / 'storage'


def create_storage_backend(storage_dir: Optional[Path] = None) -> EventStorageBackend:
    """Factory function to create a storage backend."""
    if storage_dir is None:
        storage_dir = get_default_storage_dir()
    
    return JsonEventStorage(storage_dir)
