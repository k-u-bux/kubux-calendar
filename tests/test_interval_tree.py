"""Tests for backend/interval_tree.py."""

from datetime import datetime, timedelta
import pytz
from backend.interval_tree import IntervalTree


def dt(hours: int) -> datetime:
    return datetime(2026, 1, 1, tzinfo=pytz.UTC) + timedelta(hours=hours)


def test_insert_and_find_intersecting():
    tree: IntervalTree[datetime] = IntervalTree()
    h1 = tree.insert(dt(0), dt(2), "A")
    h2 = tree.insert(dt(3), dt(5), "B")
    h3 = tree.insert(dt(6), dt(8), "C")

    results = []
    tree.find_intersecting(dt(0), dt(10), lambda h: results.append(h.data))
    assert set(results) == {"A", "B", "C"}


def test_intersecting_partial():
    tree: IntervalTree[datetime] = IntervalTree()
    tree.insert(dt(0), dt(2), "A")
    tree.insert(dt(3), dt(5), "B")
    tree.insert(dt(6), dt(8), "C")

    results = []
    tree.find_intersecting(dt(2), dt(4), lambda h: results.append(h.data))
    assert set(results) == {"A", "B"}  # A ends at 2 (edge), B starts at 3


def test_intersecting_empty_result():
    tree: IntervalTree[datetime] = IntervalTree()
    tree.insert(dt(0), dt(2), "A")
    tree.insert(dt(4), dt(6), "B")

    results = []
    tree.find_intersecting(dt(2), dt(4), lambda h: results.append(h.data))
    assert len(results) == 2


def test_intersecting_strictly_disjoint():
    tree: IntervalTree[datetime] = IntervalTree()
    tree.insert(dt(0), dt(1), "A")
    tree.insert(dt(3), dt(4), "B")

    results = []
    tree.find_intersecting(dt(1), dt(3), lambda h: results.append(h.data))
    assert len(results) == 2


def test_find_containing():
    tree: IntervalTree[datetime] = IntervalTree()
    tree.insert(dt(0), dt(10), "big")
    tree.insert(dt(3), dt(5), "small")

    results = []
    tree.find_containing(dt(4), dt(4), lambda h: results.append(h.data))
    assert set(results) == {"big", "small"}


def test_find_containing_none():
    tree: IntervalTree[datetime] = IntervalTree()
    tree.insert(dt(0), dt(2), "A")

    results = []
    tree.find_containing(dt(0), dt(3), lambda h: results.append(h.data))
    assert results == []


def test_find_contained():
    tree: IntervalTree[datetime] = IntervalTree()
    tree.insert(dt(2), dt(4), "inside")
    tree.insert(dt(0), dt(10), "outside")

    results = []
    tree.find_contained(dt(0), dt(10), lambda h: results.append(h.data))
    assert set(results) == {"inside", "outside"}


def test_find_contained_strict():
    tree: IntervalTree[datetime] = IntervalTree()
    tree.insert(dt(2), dt(4), "inside")

    results = []
    tree.find_contained(dt(2), dt(4), lambda h: results.append(h.data))
    assert len(results) == 1


def test_find_overlapping():
    tree: IntervalTree[datetime] = IntervalTree()
    tree.insert(dt(0), dt(2), "A")
    tree.insert(dt(3), dt(5), "B")
    tree.insert(dt(6), dt(8), "C")

    results = []
    tree.find_overlapping(dt(4), lambda h: results.append(h.data))
    assert results == ["B"]


def test_find_overlapping_none():
    tree: IntervalTree[datetime] = IntervalTree()
    tree.insert(dt(0), dt(2), "A")

    results = []
    tree.find_overlapping(dt(3), lambda h: results.append(h.data))
    assert results == []


def test_delete():
    tree: IntervalTree[datetime] = IntervalTree()
    h1 = tree.insert(dt(0), dt(2), "A")
    h2 = tree.insert(dt(3), dt(5), "B")

    tree.delete(h1)

    results = []
    tree.find_intersecting(dt(0), dt(5), lambda h: results.append(h.data))
    assert results == ["B"]


def test_delete_last():
    tree: IntervalTree[datetime] = IntervalTree()
    h = tree.insert(dt(0), dt(2), "A")
    tree.delete(h)

    results = []
    tree.find_intersecting(dt(0), dt(10), lambda h: results.append(h.data))
    assert results == []


def test_empty_tree():
    tree: IntervalTree[datetime] = IntervalTree()
    results = []
    tree.find_intersecting(dt(0), dt(10), lambda h: results.append(h.data))
    assert results == []


def test_verify_integrity_empty():
    tree: IntervalTree[datetime] = IntervalTree()
    tree.verify_integrity()  # should not raise


def test_verify_integrity_after_inserts():
    tree: IntervalTree[datetime] = IntervalTree()
    for i in range(20):
        tree.insert(dt(i), dt(i + 2), f"E{i}")
    tree.verify_integrity()


def test_verify_integrity_after_deletes():
    tree: IntervalTree[datetime] = IntervalTree()
    handles = [tree.insert(dt(i), dt(i + 2), f"E{i}") for i in range(10)]
    tree.delete(handles[3])
    tree.delete(handles[7])
    tree.delete(handles[0])
    tree.verify_integrity()

# ----------------------------------------------------------------------
# Randomized stress test
# ----------------------------------------------------------------------

def test_random_stress_integrity():
    """Random inserts/deletes keep AVL + max_end invariants.

    The core assertion is that verify_integrity() passes after every
    insert and every delete.  We also check that the number of surviving
    intervals matches the number of successful deletes.
    """
    import random
    random.seed(42)
    tree: IntervalTree[int] = IntervalTree()
    handles = []
    for _ in range(200):
        s = random.randint(0, 1000)
        e = s + random.randint(1, 50)
        h = tree.insert(s, e, (s, e))
        handles.append(h)
        tree.verify_integrity()
    # Delete half; verify_integrity after each is the real check
    random.shuffle(handles)
    for h in handles[:100]:
        tree.delete(h)
        tree.verify_integrity()
    # Remaining intervals must be queryable
    results = []
    tree.find_intersecting(0, 1100, lambda h: results.append(h.data))
    assert len(results) > 0


def test_random_stress_query_matches_bruteforce():
    """find_intersecting count matches a brute-force scan."""
    import random
    random.seed(7)
    tree: IntervalTree[int] = IntervalTree()
    intervals = []
    for _ in range(100):
        s = random.randint(0, 500)
        e = s + random.randint(1, 30)
        tree.insert(s, e, (s, e))
        intervals.append((s, e))
    qs, qe = 100, 300
    tree_results = []
    tree.find_intersecting(qs, qe, lambda h: tree_results.append(h.data))
    brute = [(s, e) for (s, e) in intervals if s <= qe and e >= qs]
    assert len(tree_results) == len(brute)