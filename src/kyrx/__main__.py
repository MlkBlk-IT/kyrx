"""Allow ``python -m kyrx``."""

from __future__ import annotations

import sys

from kyrx.cli import main

if __name__ == "__main__":
    sys.exit(main())
