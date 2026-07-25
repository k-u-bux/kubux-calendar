"""
Task Dispatcher Module
A procedural, ticket-based execution engine for blocking tasks.
"""

import uuid
from concurrent.futures import ThreadPoolExecutor, Future
from typing import Callable, Any, Dict, Optional
from PySide6.QtCore import QObject, Signal, Qt, QTimer, QEventLoop

from library.log import debug_log, Level

# --- Private Implementation ---

class _DispatcherEngine(QObject):
    # Signal emitted when a task completes with its result
    task_completed = Signal(object, object)  # ticket, result
    # Signal emitted when a task fails — emits the ticket and error message
    task_failed = Signal(object, str)        # ticket, error_message
    
    def __init__(self, max_workers: int = 5):
        super().__init__()
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, 
            thread_name_prefix="TaskWorker"
        )
        self._pending: Dict[str, Future] = {}
        self._callbacks: Dict[str, Callable[[Any], None]] = {}
        
        # Connect signals
        self.task_completed.connect(self._handle_task_completed)
        self.task_failed.connect(self._handle_task_failed)

    def submit(self, func: Callable, notify: Callable[[Any], None], *args, **kwargs) -> str:
        ticket = str(uuid.uuid4())
        self._callbacks[ticket] = notify
        
        # Register ticket BEFORE submitting — otherwise _wrapper's
        # "if ticket not in self._pending: return" check may see
        # an empty dict if the thread pool picks up the task
        # immediately (race condition).
        self._pending[ticket] = None  # placeholder, replaced below
        
        def _wrapper():
            # If ticket was removed via cancel_task, ignore the thread completion
            if ticket not in self._pending:
                return
            
            try:
                result = func(*args, **kwargs)
                # Emit signal to notify main thread
                self.task_completed.emit(ticket, result)
            except ImportError as e:
                debug_log(Level.ERROR, f"BUG: Task {ticket} missing dependency: {e}")
                self.task_failed.emit(ticket, str(e))
            except (ConnectionError, TimeoutError, OSError) as e:
                debug_log(Level.ERROR, f"NETWORK: Task {ticket} transient: {type(e).__name__}: {e}")
                self.task_failed.emit(ticket, str(e))
            except PermissionError as e:
                debug_log(Level.ERROR, f"PERMISSION: Task {ticket} can't access file: {e}")
                self.task_failed.emit(ticket, str(e))
            except (ValueError, KeyError, IndexError) as e:
                debug_log(Level.ERROR, f"DATA: Task {ticket} malformed input: {type(e).__name__}: {e}")
                self.task_failed.emit(ticket, str(e))
            except TypeError as e:
                debug_log(Level.ERROR, f"BUG: Task {ticket} type mismatch: {e}")
                self.task_failed.emit(ticket, str(e))
            except AttributeError as e:
                debug_log(Level.ERROR, f"BUG: Task {ticket} missing attribute: {e}")
                self.task_failed.emit(ticket, str(e))
            except Exception as e:
                debug_log(Level.ERROR, f"UNKNOWN: Task {ticket} {type(e).__name__}: {e} — investigate")
                self.task_failed.emit(ticket, str(e))
            except BaseException as e:
                # SystemExit / KeyboardInterrupt in a worker: nothing may
                # escape, or the ticket stays in _pending forever and
                # wait_for_tasks() hangs.  Emit task_failed so the ticket
                # is cleaned up on the main thread.
                debug_log(Level.ERROR, f"FATAL: Task {ticket} {type(e).__name__}: {e}")
                self.task_failed.emit(ticket, str(e))



        future = self._executor.submit(_wrapper)
        self._pending[ticket] = future
        
        return ticket
    
    def _handle_task_completed(self, ticket: str, result: Any):
        """Slot to handle task completion signal."""
        if ticket in self._callbacks:
            callback = self._callbacks.pop(ticket)
            if ticket in self._pending:
                self._pending.pop(ticket)
            try:
                callback(result)
            except Exception as e:
                debug_log(Level.ERROR, f"Callback for task {ticket} failed: {e}")

    def _handle_task_failed(self, ticket: str, error_message: str):
        """Slot to handle task failure signal — clean up pending ticket."""
        if ticket in self._callbacks:
            self._callbacks.pop(ticket)
        if ticket in self._pending:
            self._pending.pop(ticket)
        # No callback invoked — the task failed, the caller gets nothing
        # (stale cached data will be shown instead)

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
    return bool(_get_engine()._pending)


def count_pending_tasks() -> int:
    return len(_get_engine()._pending)


def cancel_task(ticket: str, timeout_ms: Optional[int] = None, on_timeout: Optional[Callable[[], None]] = None) -> str:
    """
    Cancels a task. If timeout_ms is provided, schedules cancellation.
    Returns the ticket ID to allow chaining.
    """
    engine = _get_engine()

    def _execute_cancellation():
        if ticket in engine._pending:
            future = engine._pending.pop(ticket)
            future.cancel()  # Only stops if task hasn't started
            if ticket in engine._callbacks:
                engine._callbacks.pop(ticket)
            
            if on_timeout:
                on_timeout()

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

def wait_for_tasks(timeout_ms: Optional[int] = None) -> bool:
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
    check_timer.start(50)
    if timeout_ms:
        QTimer.singleShot(timeout_ms, loop.quit)
    loop.exec()
    check_timer.stop()
    check_timer.deleteLater()

    return not tasks_are_pending()


def tie_calls(func_a: Callable, func_b: Callable) -> Callable:
    """
    Returns a single callable that executes func_a and func_b 
    in parallel and returns a tuple of their results.
    """
    def composite_task(args_pair: tuple[tuple, tuple] = ((), ())) -> tuple[Any, Any]:
        # Unpack arguments for both functions
        args_a, args_b = args_pair
        
        # We use a temporary executor to run these in parallel 
        # inside the worker thread to avoid blocking.
        with ThreadPoolExecutor(max_workers=2) as executor:
            future_a = executor.submit(func_a, *args_a)
            future_b = executor.submit(func_b, *args_b)
            return (future_a.result(), future_b.result())
            
    return composite_task


def tie_n_calls(*funcs: Callable) -> Callable:
    """
    Returns a single callable that executes N functions in parallel.
    Accepts a sequence of argument tuples and returns a list of results.
    """
    def composite_task(args_sequence: Optional[list[tuple]] = None) -> list[Any]:
        # If no args provided, default to empty tuples for all functions
        if args_sequence is None:
            args_sequence = [()] * len(funcs)
        
        # Ensure arg parity with functions
        if len(args_sequence) != len(funcs):
            raise ValueError(f"Expected {len(funcs)} arg sets, got {len(args_sequence)}")

        with ThreadPoolExecutor(max_workers=len(funcs)) as executor:
            # Map functions to their respective arguments and submit
            futures = [
                executor.submit(f, *a) 
                for f, a in zip(funcs, args_sequence)
            ]
            # .result() will raise if the underlying call fails,
            # which gets caught by _wrapper's categorized handlers
            return [f.result() for f in futures]
            
    return composite_task


def thunk(func: Callable, *args, **kwargs) -> Callable[[], Any]:
    """
    Wraps a function and its arguments into a zero-argument callable.
    """
    def wrapper() -> Any:
        return func(*args, **kwargs)
    
    return wrapper



def tie_thunks(*thunks: Callable[[], Any]) -> Callable[[], list[Any]]:
    def composite_task() -> list[Any]:
        with ThreadPoolExecutor(max_workers=len(thunks)) as executor:
            # Each thunk() call here is now zero-argument
            futures = [executor.submit(t) for t in thunks]
            return [f.result() for f in futures]
    return composite_task


def thunk_and_tie(*tasks: Callable | tuple) -> Callable[[], list[Any]]:
    """
    Combines N tasks into one zero-argument callable.
    Each task can be:
      - A zero-argument callable (thunk)
      - A tuple: (func, args)
      - A tuple: (func, args, kwargs)
    """
    def normalize(task) -> Callable:
        if isinstance(task, tuple):
            func = task[0]
            args = task[1] if len(task) > 1 else ()
            kwargs = task[2] if len(task) > 2 else {}
            return lambda: func(*args, **kwargs)
        return task # Assumed to be a thunk

    # Pre-normalize tasks into a list of thunks
    thunks = [normalize(t) for t in tasks]

    def composite_task() -> list[Any]:
        with ThreadPoolExecutor(max_workers=len(thunks)) as executor:
            # Dispatch all thunks in parallel
            futures = [executor.submit(t) for t in thunks]
            # .result() will raise if the underlying call fails,
            # which gets caught by _wrapper's categorized handlers
            return [f.result() for f in futures]
            
    return composite_task


