"""Entry point for ``kubux-calendar-attach`` (XDG .ics handler).

Wraps ``cli.calendar_attach.main`` so the Nix wrapper can invoke it via
``python cli/attach_main.py``; also runnable as ``python -m cli.attach_main``.
"""

import sys

from cli.calendar_attach import main

if __name__ == "__main__":
    sys.exit(main())
