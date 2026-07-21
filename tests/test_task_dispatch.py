"""Tests for library/task_dispatch.py."""

import time
import pytest
from PySide6.QtCore import QEventLoop, QTimer

from library.task_dispatch import (
    dispatch_task, wait_for_tasks, cancel_task,
    tasks_are_pending, count_pending_tasks,
    thunk, tie_calls, tie_n_calls, thunk_and_tie,
    shutdown_tasks, is_pending, tie_thunks,
)


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_dispatcher():
    """Reset the singleton dispatcher before each test."""
    shutdown_tasks(wait=True)
    yield
    shutdown_tasks(wait=True)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _wait_for_callback(timeout_ms: int = 2000):
    """Run event loop until callback fires or timeout."""
    loop = QEventLoop()
    QTimer.singleShot(timeout_ms, loop.quit)
    loop.exec()


# ----------------------------------------------------------------------
# dispatch_task
# ----------------------------------------------------------------------

def test_dispatch_task_success(qapp):
    result = []
    loop = QEventLoop()

    def callback(value):
        result.append(value)
        loop.quit()

    dispatch_task(callback, lambda x: x * 2, 21)
    loop.exec()

    assert result == [42]


def test_dispatch_task_multiple(qapp):
    results = []
    loop = QEventLoop()
    expected = 3

    def callback(value):
        results.append(value)
        if len(results) == expected:
            loop.quit()

    dispatch_task(callback, lambda x: x + 1, 1)
    dispatch_task(callback, lambda x: x + 2, 2)
    dispatch_task(callback, lambda x: x + 3, 3)

    loop.exec()
    assert sorted(results) == [2, 4, 6]


def test_dispatch_task_failure_no_callback(qapp):
    """Task that raises — callback must NOT be called."""
    called = []

    def callback(value):
        called.append(value)

    def failing():
        raise ValueError("boom")

    dispatch_task(callback, failing)
    _wait_for_callback(500)

    assert called == []


# ----------------------------------------------------------------------
# is_pending
# ----------------------------------------------------------------------

def test_is_pending(qapp):
    loop = QEventLoop()
    ticket = dispatch_task(lambda v: loop.quit(), lambda: 42)
    assert is_pending(ticket) is True
    loop.exec()
    # Give a moment for cleanup
    _wait_for_callback(100)
    # May or may not still be pending depending on cleanup timing
    # So just check it was pending at least once


# ----------------------------------------------------------------------
# wait_for_tasks
# ----------------------------------------------------------------------

def test_wait_for_tasks_completes(qapp):
    results = []

    def callback(value):
        results.append(value)

    dispatch_task(callback, lambda: 42)
    ok = wait_for_tasks(timeout_ms=2000)

    assert ok is True
    assert results == [42]


def test_wait_for_tasks_timeout(qapp):
    """Task that sleeps longer than timeout — wait_for_tasks returns False."""
    def slow():
        time.sleep(2)
        return 42

    dispatch_task(lambda _: None, slow)
    ok = wait_for_tasks(timeout_ms=200)

    assert ok is False


def test_wait_for_tasks_no_pending(qapp):
    """No pending tasks returns True immediately."""
    ok = wait_for_tasks(timeout_ms=1000)
    assert ok is True


# ----------------------------------------------------------------------
# cancel_task
# ----------------------------------------------------------------------

def test_cancel_task(qapp):
    called = []

    def callback(value):
        called.append(value)

    def slow():
        time.sleep(2)
        return 42

    ticket = dispatch_task(callback, slow)
    cancel_task(ticket)
    _wait_for_callback(500)

    assert called == []


# ----------------------------------------------------------------------
# tasks_are_pending / count_pending_tasks
# ----------------------------------------------------------------------

def test_tasks_are_pending(qapp):
    assert tasks_are_pending() is False
    assert count_pending_tasks() == 0

    loop = QEventLoop()

    def callback(value):
        loop.quit()

    dispatch_task(callback, lambda: 42)
    assert tasks_are_pending() is True
    assert count_pending_tasks() == 1

    loop.exec()
    assert tasks_are_pending() is False
    assert count_pending_tasks() == 0


# ----------------------------------------------------------------------
# thunk
# ----------------------------------------------------------------------

def test_thunk():
    f = thunk(lambda a, b: a + b, 3, 4)
    assert f() == 7


def test_thunk_no_args():
    f = thunk(lambda: 99)
    assert f() == 99


# ----------------------------------------------------------------------
# tie_calls
# ----------------------------------------------------------------------

def test_tie_calls():
    f = tie_calls(lambda x: x * 2, lambda y: y + 10)
    result = f(((5,), (3,)))
    assert result == (10, 13)


# ----------------------------------------------------------------------
# tie_n_calls
# ----------------------------------------------------------------------

def test_tie_n_calls():
    f = tie_n_calls(
        lambda x: x * 2,
        lambda y: y + 10,
        lambda z: z * z,
    )
    result = f([(5,), (3,), (4,)])
    assert result == [10, 13, 16]


def test_tie_n_calls_default_args():
    f = tie_n_calls(lambda: 1, lambda: 2)
    result = f()
    assert result == [1, 2]


def test_tie_n_calls_mismatched_args():
    f = tie_n_calls(lambda x: x)
    try:
        f([(1,), (2,)])  # wrong number of arg sets
        assert False, "should have raised"
    except ValueError:
        pass


# ----------------------------------------------------------------------
# thunk_and_tie
# ----------------------------------------------------------------------

def test_thunk_and_tie_mixed():
    t1 = thunk(lambda x: x + 1, 5)  # zero-arg callable
    t2 = (lambda y: y * 2, (3,))     # (func, args) tuple
    f = thunk_and_tie(t1, t2)
    result = f()
    assert result == [6, 6]


def test_thunk_and_tie_with_kwargs():
    """thunk_and_tie with (func, args, kwargs) tuples."""
    f = thunk_and_tie(
        (lambda a, b=1: a + b, (5,), {"b": 2}),
    )
    result = f()
    assert result == [7]


# ----------------------------------------------------------------------
# tie_thunks
# ----------------------------------------------------------------------

def test_tie_thunks():
    t1 = thunk(lambda: 1)
    t2 = thunk(lambda: 2)
    f = tie_thunks(t1, t2)
    result = f()
    assert result == [1, 2]