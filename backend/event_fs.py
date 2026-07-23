"""
Filesystem-based event cache for Kubux Calendar v2.

Directory layout::

    {base}/
    ├── cache/{source_id}/{uuid}.ics   — one iCalendar file per event
    ├── sources/{source_id}.json       — per-source metadata
    └── pending.json                   — pending sync operations

All writes are atomic (tempfile + rename).  The entire cache is
disposable — the server is the source of truth.
"""

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from .event import ImmutableEvent
from library.log import debug_log, Level
import pytz


# ==================== Helpers ====================

def _safe_filename(name: str) -> str:
    """Encode *name* as a hex string safe for filesystem use."""
    return name.encode("utf-8").hex()


def _atomic_write(path: Path, data: str | bytes, *, binary: bool = False) -> None:
    """Write *data* to *path* atomically via tempfile + rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "wb" if binary else "w"
    encoding = None if binary else "utf-8"
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, mode, encoding=encoding) as f:   # type: ignore[call-overload]
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _default_base() -> Path:
    xdg = os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state"))
    return Path(xdg) / "kubux-calendar" / "v2"


# ==================== Source Metadata ====================

class SourceMeta:
    """Serialisable metadata for a calendar source."""
    __slots__ = (
        "source_id", "name", "color", "read_only", "source_type",
        "account_name", "last_attempt", "last_success",
    )

    def __init__(
        self,
        source_id: str,
        name: str = "",
        color: str = "",
        read_only: bool = False,
        source_type: str = "caldav",
        account_name: str = "",
        last_attempt: Optional[datetime] = None,
        last_success: Optional[datetime] = None,
    ):
        self.source_id = source_id
        self.name = name
        self.color = color
        self.read_only = read_only
        self.source_type = source_type
        self.account_name = account_name
        self.last_attempt = last_attempt
        self.last_success = last_success

    def to_dict(self) -> dict:
        return {
            "source_id": self.source_id,
            "name": self.name,
            "color": self.color,
            "read_only": self.read_only,
            "source_type": self.source_type,
            "account_name": self.account_name,
            "last_attempt": self.last_attempt.isoformat() if self.last_attempt else None,
            "last_success": self.last_success.isoformat() if self.last_success else None,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SourceMeta":
        return cls(
            source_id=d["source_id"],
            name=d.get("name", ""),
            color=d.get("color", ""),
            read_only=d.get("read_only", False),
            source_type=d.get("source_type", "caldav"),
            account_name=d.get("account_name", ""),
            last_attempt=datetime.fromisoformat(d["last_attempt"]) if d.get("last_attempt") else None,
            last_success=datetime.fromisoformat(d["last_success"]) if d.get("last_success") else None,
        )


# ==================== Pending Operations ====================

class PendingOp:
    """One pending sync operation."""
    __slots__ = ("uid", "source_id", "operation", "instance_start")

    def __init__(self, uid: str, source_id: str, operation: str,
                 instance_start: Optional[datetime] = None):
        self.uid = uid
        self.source_id = source_id
        self.operation = operation          # create, update, delete, delete_instance
        self.instance_start = instance_start  # only for delete_instance

    def to_dict(self) -> dict:
        d: dict = {"uid": self.uid, "source_id": self.source_id, "operation": self.operation}
        if self.instance_start:
            d["instance_start"] = self.instance_start.isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "PendingOp":
        inst = None
        if d.get("instance_start"):
            inst = datetime.fromisoformat(d["instance_start"])
        return cls(uid=d["uid"], source_id=d["source_id"],
                   operation=d["operation"], instance_start=inst)


# ==================== EventFS ====================

class EventFS:
    """
    Filesystem cache for :class:`ImmutableEvent` objects.

    Every public method is synchronous and safe to call from any thread
    (writes are atomic at the OS level).
    """

    def __init__(self, base: Optional[Path] = None, config_tz: Optional[pytz.BaseTzInfo] = None):
        self.base = Path(base) if base else _default_base()
        self._cache_dir = self.base / "cache"
        self._sources_dir = self.base / "sources"
        self._pending_file = self.base / "pending.json"
        self._config_tz = config_tz

    # --- internal paths ----------------------------------------------------

    def _source_dir(self, source_id: str) -> Path:
        return self._cache_dir / _safe_filename(source_id)

    def _event_path(self, source_id: str, uid: str) -> Path:
        return self._source_dir(source_id) / f"{_safe_filename(uid)}.ics"

    def _source_meta_path(self, source_id: str) -> Path:
        return self._sources_dir / f"{_safe_filename(source_id)}.json"

    # === Event CRUD ========================================================

    def save_event(self, event: ImmutableEvent) -> None:
        """Persist a single event (atomic).  Returns confirmed_at (mtime)."""
        path = self._event_path(event.source_id, event.uid)
        _atomic_write(path, event.ical_data)
        try:
            return datetime.fromtimestamp(path.stat().st_mtime)
        except OSError:
            return None

    def load_event(self, source_id: str, uid: str) -> Optional[ImmutableEvent]:
        """Load a single event, or *None* if not cached."""
        path = self._event_path(source_id, uid)
        if not path.exists():
            return None
        try:
            ical_data = path.read_text(encoding="utf-8")
            confirmed = datetime.fromtimestamp(path.stat().st_mtime)
            return ImmutableEvent.from_ical(
                ical_data, source_id, config_tz=self._config_tz,
                confirmed_at=confirmed,
            )
        except Exception as e:
            debug_log(Level.WARN, f"event_fs: load_event failed — {e}")
            return None

    def delete_event(self, source_id: str, uid: str) -> None:
        """Remove a cached event file."""
        path = self._event_path(source_id, uid)
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    def list_events(self, source_id: str) -> list[ImmutableEvent]:
        """Load all cached events for a source."""
        src_dir = self._source_dir(source_id)
        if not src_dir.is_dir():
            return []
        events = []
        for p in src_dir.glob("*.ics"):
            try:
                ical_data = p.read_text(encoding="utf-8")
                confirmed = datetime.fromtimestamp(p.stat().st_mtime)
                ev = ImmutableEvent.from_ical(
                    ical_data, source_id, config_tz=self._config_tz,
                    confirmed_at=confirmed,
                )
                events.append(ev)
            except Exception as e:
                debug_log(Level.DEBUG, f"event_fs: list_events — skipping unparseable event: {e}")
                continue
        return events

    def replace_source(self, source_id: str, events: list[ImmutableEvent]) -> None:
        """
        Replace *all* cached events for a source atomically.

        Old files are deleted, new files are written.  Events with pending
        sync state are **not** overwritten if they already exist.
        """
        src_dir = self._source_dir(source_id)

        # Read existing pending UIDs so we don't clobber them
        pending_uids: set[str] = set()
        for op in self.load_pending():
            if op.source_id == source_id:
                pending_uids.add(op.uid)

        # Delete old files that aren't in new set (and aren't pending)
        if src_dir.is_dir():
            for p in src_dir.glob("*.ics"):
                uid_stem = p.stem  # sanitised UID
                # Check if any new event would map to this file
                keep = False
                for ev in events:
                    if _safe_filename(ev.uid) == uid_stem:
                        keep = True
                        break
                if not keep and uid_stem not in {_safe_filename(u) for u in pending_uids}:
                    try:
                        p.unlink()
                    except OSError:
                        pass

        # Write new events (skip pending ones)
        for ev in events:
            if ev.uid not in pending_uids:
                self.save_event(ev)

    def purge_source(self, source_id: str) -> None:
        """Delete all cached events and metadata for a source."""
        import shutil
        src_dir = self._source_dir(source_id)
        if src_dir.is_dir():
            shutil.rmtree(src_dir, ignore_errors=True)
        meta_path = self._source_meta_path(source_id)
        try:
            meta_path.unlink(missing_ok=True)
        except OSError:
            pass

    def purge_all(self) -> None:
        """Delete entire cache.  Safe — server is source of truth."""
        import shutil
        if self.base.is_dir():
            shutil.rmtree(self.base, ignore_errors=True)

    # === Source Metadata ===================================================

    def save_source_meta(self, meta: SourceMeta) -> None:
        path = self._source_meta_path(meta.source_id)
        _atomic_write(path, json.dumps(meta.to_dict(), indent=2))

    def load_source_meta(self, source_id: str) -> Optional[SourceMeta]:
        path = self._source_meta_path(source_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return SourceMeta.from_dict(data)
        except Exception as e:
            debug_log(Level.WARN, f"event_fs: load_source_meta failed — {e}")
            return None

    def list_source_ids(self) -> list[str]:
        """Return source IDs that have cached data or metadata."""
        ids: set[str] = set()
        if self._cache_dir.is_dir():
            for d in self._cache_dir.iterdir():
                if d.is_dir():
                    ids.add(d.name)
        if self._sources_dir.is_dir():
            for f in self._sources_dir.glob("*.json"):
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    if "source_id" in data:
                        ids.add(data["source_id"])
                except Exception as e:
                    debug_log(Level.WARN, f"event_fs: list_source_ids — skipping corrupt entry: {e}")
        return list(ids)

    # === Pending Operations ================================================

    def save_pending(self, ops: list[PendingOp]) -> None:
        data = [op.to_dict() for op in ops]
        _atomic_write(self._pending_file, json.dumps(data, indent=2))

    def load_pending(self) -> list[PendingOp]:
        if not self._pending_file.exists():
            return []
        try:
            data = json.loads(self._pending_file.read_text(encoding="utf-8"))
            return [PendingOp.from_dict(d) for d in data]
        except Exception as e:
            debug_log(Level.WARN, f"event_fs: load_pending failed — {e}")
            return []

    def add_pending(self, op: PendingOp) -> None:
        """Append or replace a pending operation for an event."""
        ops = [o for o in self.load_pending() if o.uid != op.uid]
        ops.append(op)
        self.save_pending(ops)

    def remove_pending(self, uid: str) -> None:
        """Remove the pending operation for *uid*."""
        ops = [o for o in self.load_pending() if o.uid != uid]
        self.save_pending(ops)

    def clear_pending(self) -> None:
        self.save_pending([])