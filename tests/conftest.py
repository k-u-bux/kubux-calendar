"""Shared pytest fixtures and configuration for Kubux Calendar tests.

Sets up a headless Qt environment and a single session-scoped
``QApplication`` so GUI tests can run without a display (CI-friendly).
"""

import os

# Force the offscreen Qt platform plugin before any QApplication is
# constructed.  This lets the GUI/widget tests run headless.
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import pytest
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="session")
def qapp():
    """One QApplication shared by all GUI tests.

    Constructed once per session; never quit so other modules can keep
    using it.
    """
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app