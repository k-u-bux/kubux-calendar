"""FollowState: shared flag for 'follow present' mode.

When enabled (by clicking [Today]), every update of the current-time
indicator in day/week views also re-centers the view on the current hour.
Any other navigation action or user scroll disables it.
"""


class FollowState:
    """Mutable flag shared between CalendarWidget, views, and day columns."""

    def __init__(self):
        self.follow_present = False