"""Tests for gui/widgets/shared_scrollbar.py — shared scrollbar state.

``_ScrollBarState`` is a plain class (no Qt needed); ``SharedScrollBar``
mirrors a state object across instances and needs a ``QApplication``.
"""

from gui.widgets.shared_scrollbar import _ScrollBarState, SharedScrollBar


# ----------------------------------------------------------------------
# _ScrollBarState — plain class, no QApplication needed
# ----------------------------------------------------------------------

def test_state_defaults():
    s = _ScrollBarState()
    assert s.value == 0
    assert s.minimum == 0
    assert s.maximum == 1000
    assert s.page_step == 1000
    assert s.single_step == 1


def test_set_value_clamps_to_range():
    s = _ScrollBarState()
    s.set_value(-50)
    assert s.value == 0
    s.set_value(5000)
    assert s.value == 1000
    s.set_value(500)
    assert s.value == 500


def test_set_value_no_notify_on_same_value():
    s = _ScrollBarState()
    notified = []
    s.add_listener(lambda attr, val: notified.append((attr, val)))
    s.set_value(0)  # same as default → no notify
    assert notified == []


def test_set_value_notifies_listeners():
    s = _ScrollBarState()
    events = []
    s.add_listener(lambda attr, val: events.append((attr, val)))
    s.set_value(100)
    assert events == [("value", 100)]


def test_set_range_notifies():
    s = _ScrollBarState()
    events = []
    s.add_listener(lambda attr, val: events.append((attr, val)))
    s.set_range(10, 20)
    assert s.minimum == 10
    assert s.maximum == 20
    # set_range notifies "range" twice (lo, hi)
    assert events == [("range", 10), ("range", 20)]


def test_set_page_step_clamps_min_one():
    s = _ScrollBarState()
    s.set_page_step(0)
    assert s.page_step == 1
    s.set_page_step(50)
    assert s.page_step == 50


def test_multiple_listeners_all_notified():
    s = _ScrollBarState()
    a, b = [], []
    s.add_listener(lambda attr, val: a.append(val))
    s.add_listener(lambda attr, val: b.append(val))
    s.set_value(42)
    assert a == [42]
    assert b == [42]


# ----------------------------------------------------------------------
# SharedScrollBar — mirrors state across instances (needs qapp)
# ----------------------------------------------------------------------

def test_shared_scrollbar_mirrors_value(qapp):
    state = _ScrollBarState()
    bar1 = SharedScrollBar(state)
    bar2 = SharedScrollBar(state)
    bar1.setValue(300)
    assert bar2.value() == 300
    assert bar1.value() == 300


def test_shared_scrollbar_page_step(qapp):
    state = _ScrollBarState()
    bar = SharedScrollBar(state)
    bar.setPageStep(200)
    assert bar.pageStep() == 200


def test_shared_scrollbar_set_range(qapp):
    state = _ScrollBarState()
    bar = SharedScrollBar(state)
    bar.setRange(5, 50)
    assert bar.minimum() == 5
    assert bar.maximum() == 50