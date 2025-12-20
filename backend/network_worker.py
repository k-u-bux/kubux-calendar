"""
Network Worker - runs blocking network operations in background threads.

Uses ThreadPoolExecutor to prevent UI freezes during network operations.
Results are delivered via Qt signals emitted on the main thread.
"""

from concurrent.futures import ThreadPoolExecutor, Future
from typing import Callable, Any, Optional
import traceback
import sys

from PySide6.QtCore import QObject, Signal, QMetaObject, Qt, Q_ARG


class NetworkWorker(QObject):
    """
    Runs network operations in background threads.
    
    Signals are emitted on the main thread when operations complete.
    """
    
    # Signal emitted when an operation completes successfully
    # Args: (operation_id: str, result: object)
    operation_finished = Signal(str, object)
    
    # Signal emitted when an operation fails
    # Args: (operation_id: str, error_message: str)
    operation_error = Signal(str, str)
    
    def __init__(self, max_workers: int = 3, parent=None):
        super().__init__(parent)
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="network")
        self._pending: dict[str, Future] = {}
    
    def submit(self, operation_id: str, func: Callable, *args, **kwargs) -> None:
        """
        Submit a blocking operation to run in a background thread.
        
        Args:
            operation_id: Unique identifier for this operation (for callback matching)
            func: The blocking function to run
            *args, **kwargs: Arguments to pass to func
        
        The operation_finished or operation_error signal will be emitted when complete.
        """
        future = self._executor.submit(func, *args, **kwargs)
        self._pending[operation_id] = future
        future.add_done_callback(lambda f: self._on_done(operation_id, f))
    
    def _on_done(self, operation_id: str, future: Future) -> None:
        """Handle completion of a background operation."""
        # Remove from pending
        self._pending.pop(operation_id, None)
        
        try:
            result = future.result()
            # Emit signal on main thread (Qt handles cross-thread signal delivery)
            self.operation_finished.emit(operation_id, result)
        except Exception as e:
            error_msg = f"{type(e).__name__}: {e}"
            print(f"DEBUG NetworkWorker: Operation '{operation_id}' failed: {error_msg}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            self.operation_error.emit(operation_id, error_msg)
    
    def is_pending(self, operation_id: str) -> bool:
        """Check if an operation is still pending."""
        return operation_id in self._pending
    
    def cancel(self, operation_id: str) -> bool:
        """
        Attempt to cancel a pending operation.
        
        Returns True if cancelled, False if already running or completed.
        """
        future = self._pending.get(operation_id)
        if future:
            return future.cancel()
        return False
    
    def shutdown(self, wait: bool = True) -> None:
        """Shutdown the executor, optionally waiting for pending operations."""
        self._executor.shutdown(wait=wait)


# Global worker instance (created lazily)
_global_worker: Optional[NetworkWorker] = None


def get_network_worker() -> NetworkWorker:
    """Get the global NetworkWorker instance."""
    global _global_worker
    if _global_worker is None:
        _global_worker = NetworkWorker()
    return _global_worker


def shutdown_network_worker() -> None:
    """Shutdown the global NetworkWorker."""
    global _global_worker
    if _global_worker is not None:
        _global_worker.shutdown(wait=False)
        _global_worker = None
