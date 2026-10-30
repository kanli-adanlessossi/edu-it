"""Seed the relational catalog rows the real stack needs for the measurement (task 21.5).

Idempotent. Run once after ``edu-it-migrate upgrade`` and before starting api/worker::

    python tools/perf/bootstrap_stack.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stack  # noqa: E402


def main() -> int:
    print(json.dumps({"inserted": stack.seed_database()}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
