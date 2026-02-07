"""
Kubux Calendar Backend v2

Functional, immutable backend implementation replacing v1 object-oriented architecture.
"""

__version__ = "2.0.0"

# Re-export shared components
from .interval_tree import IntervalTree, IntervalHandle
from .task_dispatch import (
    dispatch_task,
    is_pending,
    tasks_are_pending,
    count_pending_tasks,
    cancel_task,
    shutdown_tasks,
    wait_for_tasks,
)
from .timezone_utils import (
    get_local_timezone,
    to_local_datetime,
    to_utc_datetime,
    utc_to_local_naive,
    local_naive_to_utc,
    to_local_hour,
    set_timezone,
)

# v2 components
from .event import ImmutableEvent, SyncState
from .event_fs import EventFS
from .event_index import EventIndex
from .sync_manager import SyncManager, SyncStatus, SyncResult
