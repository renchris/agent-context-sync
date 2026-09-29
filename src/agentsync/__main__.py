"""``python -m agentsync`` (what the launchd agents exec, by absolute interpreter path)."""

from __future__ import annotations

import sys

from agentsync.cli import main

if __name__ == "__main__":
    sys.exit(main())
