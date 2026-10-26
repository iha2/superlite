#!/usr/bin/env python3
"""Old name of checkpoint.py, kept so `sl commit_task`, old briefs and imports keep working."""
import sys  # O(1)
from pathlib import Path  # O(1)

sys.path.insert(0, str(Path(__file__).resolve().parent))  # O(1)
from checkpoint import *  # noqa: E402,F401,F403 | O(1)
from checkpoint import main  # noqa: E402 | O(1)

if __name__ == "__main__":  # O(1)
    sys.exit(main())  # O(1)
