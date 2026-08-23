"""Entry point so the tool can be run as ``python -m cli`` (or via the
``kubux-caldav-send`` wrapper)."""

import sys

from cli.caldav_send import main

if __name__ == "__main__":
    sys.exit(main())
