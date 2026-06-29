"""
Color utilities for Kubux Calendar.

Generates visually distinct colors for calendars that don't have
an explicit color configured.
"""

import random
from PySide6.QtGui import QColor


def color_to_hue(color_str: str) -> float:
    """Extract hue in degrees (0-360) from any Qt-compatible color string."""
    qc = QColor(color_str)
    if not qc.isValid():
        return 0.0
    h = qc.hue()
    return float(h) if h >= 0 else 0.0


def _angular_distance(h1: float, h2: float) -> float:
    """Shortest angular distance between two hues in degrees."""
    d = abs(h1 - h2)
    return min(d, 360.0 - d)


def get_unused_color(used_colors: list[str], n_candidates: int = 5) -> str:
    """Return a hex color visually distinct from all *used_colors*.

    Generates every hue (0-359) at fixed saturation 70% / lightness 50%,
    scores each by minimum angular distance to any used hue,
    keeps the top *n_candidates*, and picks one randomly.

    If *used_colors* is empty, returns a random hue.
    """
    if not used_colors:
        h = random.randint(0, 359)
        qc = QColor.fromHsl(h, 179, 128)  # 179 = 70%, 128 = 50%
        return qc.name()

    used_hues = [color_to_hue(c) for c in used_colors]

    scored: list[tuple[float, int]] = []
    for h in range(360):
        dist = min(_angular_distance(h, uh) for uh in used_hues)
        scored.append((dist, h))

    scored.sort(reverse=True, key=lambda x: x[0])
    top = scored[:n_candidates]
    _, chosen = random.choice(top)

    qc = QColor.fromHsl(chosen, 179, 128)
    return qc.name()