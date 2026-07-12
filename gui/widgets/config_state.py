"""
Shared module-level config singletons for GUI widgets.

All widget modules import config getters/setters from here instead of
reaching into each other.  This breaks the previous circular dependency
between ``calendar_widget`` and ``event_widget``.
"""

from PySide6.QtGui import QFont

from backend.config import (
    LayoutConfig,
    LocalizationConfig,
    ColorsConfig,
    LabelsConfig,
)

# Module-level config singletons (set by MainWindow at startup)
_layout_config: LayoutConfig = LayoutConfig()
_localization_config: LocalizationConfig = LocalizationConfig()
_colors_config: ColorsConfig = ColorsConfig()
_labels_config: LabelsConfig = LabelsConfig()

# Module-level hour height (updated when layout config is set)
HOUR_HEIGHT: int = 60  # Default value


# ---------------------------------------------------------------------------
# Layout config
# ---------------------------------------------------------------------------

def set_layout_config(config: LayoutConfig) -> None:
    """Set the layout configuration for all widget modules."""
    global _layout_config, HOUR_HEIGHT
    _layout_config = config
    HOUR_HEIGHT = config.hour_height


def get_layout_config() -> LayoutConfig:
    """Get the current layout configuration."""
    return _layout_config


def get_hour_height() -> int:
    """Get the configured hour height in pixels."""
    return HOUR_HEIGHT


def get_text_font() -> QFont:
    """Get the configured text font as a QFont object."""
    return QFont(_layout_config.text_font, _layout_config.text_font_size)


def get_interface_font() -> tuple[str, int]:
    """Get the configured interface font name and size."""
    return (_layout_config.interface_font, _layout_config.interface_font_size)


# ---------------------------------------------------------------------------
# Localization config
# ---------------------------------------------------------------------------

def set_localization_config(config: LocalizationConfig) -> None:
    """Set the localization configuration."""
    global _localization_config
    _localization_config = config


def get_localization_config() -> LocalizationConfig:
    """Get the current localization configuration."""
    return _localization_config


# ---------------------------------------------------------------------------
# Colors config
# ---------------------------------------------------------------------------

def set_colors_config(config: ColorsConfig) -> None:
    """Set the colors configuration."""
    global _colors_config
    _colors_config = config


def get_colors_config() -> ColorsConfig:
    """Get the current colors configuration."""
    return _colors_config


# ---------------------------------------------------------------------------
# Labels config
# ---------------------------------------------------------------------------

def set_labels_config(config: LabelsConfig) -> None:
    """Set the labels configuration."""
    global _labels_config
    _labels_config = config


def get_labels_config() -> LabelsConfig:
    """Get the current labels configuration."""
    return _labels_config