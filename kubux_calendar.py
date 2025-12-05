#!/usr/bin/env python3
"""
Kubux Calendar - A PySide6 desktop calendar for CalDAV (Nextcloud) and ICS subscriptions.

This is the main entry point for the application.
"""

import sys
import argparse
from pathlib import Path

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt

from backend.config import Config
from gui.main_window import MainWindow


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Kubux Calendar - A desktop calendar for Nextcloud and ICS subscriptions"
    )
    parser.add_argument(
        "-c", "--config",
        type=Path,
        help="Path to configuration file (default: auto-detect)"
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug output"
    )
    return parser.parse_args()


def main():
    """Main entry point."""
    args = parse_args()
    
    # Enable high DPI scaling
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    
    # Create application
    app = QApplication(sys.argv)
    app.setApplicationName("Kubux Calendar")
    app.setApplicationVersion("0.1")
    app.setOrganizationName("kubux")
    app.setOrganizationDomain("kubux.net")
    
    # Set application style
    app.setStyle("Fusion")
    
    # Load configuration
    try:
        config = Config.load(args.config)
    except FileNotFoundError as e:
        print(f"Error: {e}")
        print("\nPlease create a configuration file at one of these locations:")
        print(f"  - {Config.get_default_config_path()}")
        print("  - ./kubux-calendar.ini")
        print("\nExample configuration:")
        print("""
[General]
password_program = /usr/bin/pass

[Nextcloud.Primary]
url = https://nextcloud.example.com
username = your_username
password_key = nextcloud/password

[Subscription.Example]
url = https://example.com/calendar.ics
name = Example Calendar
color = #4285f4
""")
        sys.exit(1)
    except Exception as e:
        print(f"Error loading configuration: {e}")
        sys.exit(1)
    
    if args.debug:
        print(f"Loaded configuration from: {args.config or Config.get_default_config_path()}")
        print(f"  Nextcloud accounts: {len(config.nextcloud_accounts)}")
        print(f"  ICS subscriptions: {len(config.ics_subscriptions)}")
    
    # Create and show main window
    window = MainWindow(config)
    window.show()
    
    # Run event loop
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
