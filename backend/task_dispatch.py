"""
Task Dispatcher Module
A procedural, ticket-based execution engine for blocking tasks.
"""

import uuid
import sys
from concurrent.futures import ThreadPoolExecutor, Future
from typing import Callable, Any, Dict, Optional
from PySide6.QtCore import QObject, QMetaObject, Qt, QTimer, QEventLoop

# --- Private Implementation ---

class _DispatcherEngine(QObject):
    def __init__(self, max_workers: int = 5):
        super().__init__()
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, 
            thread_name_prefix="TaskWorker"
        )
        self._pending: Dict[str, Future] = {}

    def submit(self, func: Callable, notify: Callable[[Any], None], *args, **kwargs) -> str:
        ticket = str(uuid.uuid4())
        
        def _wrapper(f: Future):
            # If ticket was removed via cancel_task, ignore the thread completion
            if ticket not in self._pending:
                return 

            self._pending.pop(ticket)            
            try:
                result = f.result()
                # Marshall the notify call back to the Main Thread
                QMetaObject.invokeMethod(
                    self,
                    lambda: notify(result),
                    Qt.ConnectionType.QueuedConnection
                )
            except Exception as e:
                # Fail Hard: Background thread crashes exit the process
                print(f"CRITICAL: Task {ticket} crashed: {e}", file=sys.stderr)
                sys.exit(1)

        future = self._executor.submit(func, *args, **kwargs)
        self._pending[ticket] = future
        future.add_done_callback(_wrapper)
        
        return ticket

# Internal singleton (late initialization avoids problems with QT)
_instance: Optional[_DispatcherEngine] = None

def _get_engine() -> _DispatcherEngine:
    global _instance
    if _instance is None:
        _instance = _DispatcherEngine()
    return _instance

# --- Public API ---

def dispatch_task(notify: Callable[[Any], None], func: Callable, *args, **kwargs) -> str:
    """Dispatches a task and returns a ticket ID."""
    return _get_engine().submit(func, notify, *args, **kwargs)


def is_pending(ticket: str) -> bool:
    """Check if a ticket is currently active."""
    return ticket in _get_engine()._pending


def tasks_are_pending() -> bool:
    return _get_engine()._pending


def count_pending_tasks() -> int:
    return len(_get_engine()._pending)


def cancel_task(ticket: str, timeout_ms: int = None, on_timeout: Callable[[], None] = None) -> str:
    """
    Cancels a task. If timeout_ms is provided, schedules cancellation.
    Returns the ticket ID to allow chaining.
    """
    engine = _get_engine()

    def _execute_cancellation():
        if ticket in engine._pending:
            future = engine._pending.pop(ticket)
            future.cancel() # Only stops if task hasn't started
            
            if on_timeout:
                on_timeout(ticket)

    if timeout_ms is not None:
        # Create a single-shot timer on the main thread
        timer = QTimer(engine)
        timer.setSingleShot(True)
        timer.timeout.connect(_execute_cancellation)
        timer.timeout.connect(timer.deleteLater)
        timer.start(timeout_ms)
    else:
        _execute_cancellation()

    return ticket


def shutdown_tasks(wait: bool = False) -> None:
    """Closes the background thread pool."""
    global _instance
    if _instance is not None:
        _instance._executor.shutdown(wait=wait)
        _instance = None


# convenience functions

def wait_for_tasks(timeout_ms: int = None) -> bool:
    """
    Blocks the current procedural flow until all tasks are finished,
    while keeping the Qt UI responsive (processing events).
    
    Returns True if all tasks finished, False if it timed out.
    """
    if not tasks_are_pending():
        return True

    loop = QEventLoop()
    check_timer = QTimer()
    
    def _check():
        if not tasks_are_pending():
            loop.quit()
            
    check_timer.timeout.connect(_check)
    loop.finished.connect(check_timer.deleteLater)
    check_timer.start(50)
    if timeout_ms:
        QTimer.singleShot(timeout_ms, loop.quit)
    loop.exec()
    check_timer.stop()

    return not tasks_are_pending()

