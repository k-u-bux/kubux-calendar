from typing import Any, Callable, Optional, TypeVar, Generic

# T represents the Totally Ordered type used for coordinates (Time)
T = TypeVar('T')

class IntervalHandle(Generic[T]):
    """Opaque handle with public accessors for start, end, and data."""
    __slots__ = ['start', 'end', 'data', 'left', 'right', 'parent', 'max_end', 'height']

    def __init__(self, start: T, end: T, data: Any):
        self.start: T = start
        self.end: T = end
        self.data: Any = data
        self.left: Optional['IntervalHandle[T]'] = None
        self.right: Optional['IntervalHandle[T]'] = None
        self.parent: Optional['IntervalHandle[T]'] = None
        self.max_end: T = end
        self.height: int = 1

class IntervalTree(Generic[T]):
    def __init__(self):
        self.root: Optional[IntervalHandle[T]] = None

    # --- Internal Utilities ---

    def _get_height(self, node: Optional[IntervalHandle[T]]) -> int:
        return node.height if node else 0

    def _update(self, node: Optional[IntervalHandle[T]]):
        if not node: return
        node.height = 1 + max(self._get_height(node.left), self._get_height(node.right))
        
        m = node.end
        if node.left: m = max(m, node.left.max_end)
        if node.right: m = max(m, node.right.max_end)
        node.max_end = m

    def _rotate_left(self, x: IntervalHandle[T]):
        y = x.right
        x.right = y.left
        if y.left: y.left.parent = x
        y.parent = x.parent
        if not x.parent: self.root = y
        elif x == x.parent.left: x.parent.left = y
        else: x.parent.right = y
        y.left = x
        x.parent = y
        self._update(x)
        self._update(y)

    def _rotate_right(self, y: IntervalHandle[T]):
        x = y.left
        y.left = x.right
        if x.right: x.right.parent = y
        x.parent = y.parent
        if not y.parent: self.root = x
        elif y == y.parent.left: y.parent.left = x
        else: y.parent.right = x
        x.right = y
        y.parent = x
        self._update(y)
        self._update(x)

    def _rebalance(self, node: Optional[IntervalHandle[T]]):
        while node:
            self._update(node)
            balance = self._get_height(node.left) - self._get_height(node.right)
            if balance > 1:
                if self._get_height(node.left.left) < self._get_height(node.left.right):
                    self._rotate_left(node.left)
                self._rotate_right(node)
            elif balance < -1:
                if self._get_height(node.right.right) < self._get_height(node.right.left):
                    self._rotate_right(node.right)
                self._rotate_left(node)
            node = node.parent


    # --- Public API ---

    def insert(self, start: T, end: T, data: Any) -> IntervalHandle[T]:
        new_node = IntervalHandle(start, end, data)
        if not self.root:
            self.root = new_node
            return new_node

        curr = self.root
        parent = None
        while curr:
            parent = curr
            if start < curr.start: curr = curr.left
            else: curr = curr.right

        new_node.parent = parent
        if start < parent.start: parent.left = new_node
        else: parent.right = new_node

        self._rebalance(new_node)
        return new_node

    def delete(self, handle: IntervalHandle[T]):
        if not handle: return
        if not handle.left or not handle.right:
            z = handle
        else:
            z = handle.right
            while z.left: z = z.left
        
        child = z.left or z.right
        if child: child.parent = z.parent
        
        if not z.parent: self.root = child
        elif z == z.parent.left: z.parent.left = child
        else: z.parent.right = child

        rebalance_point = z.parent
        if z != handle:
            handle.start, handle.end, handle.data = z.start, z.end, z.data
            self._rebalance(rebalance_point)
            self._rebalance(handle)
        else:
            self._rebalance(rebalance_point)


    # --- Search Methods ---

    def find_intersecting(self, start: T, end: T, callback: Callable[[IntervalHandle[T]], None]):
        """Finds intervals that have any overlap with [start, end]."""
        def _search(node):
            if not node or start > node.max_end: return
            if node.left and node.left.max_end >= start: _search(node.left)
            if node.start <= end and node.end >= start: callback(node)
            if node.start <= end: _search(node.right)
        _search(self.root)

    def find_containing(self, start: T, end: T, callback: Callable[[IntervalHandle[T]], None]):
        """Finds intervals that fully enclose the range [start, end]."""
        def _search(node):
            if not node or start > node.max_end: return
            if node.left and node.left.max_end >= start: _search(node.left)
            if node.start <= start and node.end >= end: callback(node)
            if node.start <= start: _search(node.right)
        _search(self.root)

    def find_contained(self, start: T, end: T, callback: Callable[[IntervalHandle[T]], None]):
        """Finds intervals that are strictly inside the range [start, end]."""
        def _search(node):
            if not node or start > node.max_end: return
            if node.left and node.left.max_end >= start: _search(node.left)
            if node.start >= start and node.end <= end: callback(node)
            if node.start <= end: _search(node.right)
        _search(self.root)

    def find_overlapping(self, time: T, callback: Callable[[IntervalHandle[T]], None]):
        """Finds intervals that cover a specific point in time."""
        def _search(node):
            if not node or time > node.max_end: return
            if node.left and node.left.max_end >= time: _search(node.left)
            if node.start <= time and node.end >= time: callback(node)
            if node.start <= time: _search(node.right)
        _search(self.root)


    # --- Debug Tool ---

    def verify_integrity(self):
        """Crashes if AVL height or max_end properties are violated."""
        def _walk(node):
            if not node: return 0, float('-inf')
            
            left_h, left_max = _walk(node.left)
            right_h, right_max = _walk(node.right)
            
            # Check AVL Balance
            if abs(left_h - right_h) > 1:
                raise RuntimeError(f"AVL Violation at {node.start}")
            
            # Check Augmentation
            expected_max = max(node.end, left_max, right_max)
            if node.max_end != expected_max:
                raise RuntimeError(f"MaxEnd Violation at {node.start}")
                
            return 1 + max(left_h, right_h), expected_max
            
        _walk(self.root)
