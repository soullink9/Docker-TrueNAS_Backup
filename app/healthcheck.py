#!/usr/bin/env python3
"""
Docker healthcheck for the TrueNAS backup container.

scheduler.py rewrites a small status file every 15 seconds. The container is
healthy when that file is fresh (the scheduler is alive) and the settings had
no problems. A failed backup does NOT make the container unhealthy: failures
are reported to Home Assistant, and the next scheduled run tries again.

Prints the problems and exits 1 when unhealthy, so `docker inspect` shows why.
Run with --status to print the whole status (next run, last result).
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

# Where scheduler.py writes its status. /tmp is inside the container only.
HEALTH_FILE = Path("/tmp/truenas-backup/health.json")

# The scheduler writes every 15 s; allow plenty of slack before calling it dead.
MAX_STATUS_AGE = 90


def problems(status: dict, now: float) -> list[str]:
    """Everything wrong with a status written by the scheduler ([] = healthy)."""

    found: list[str] = []

    age = now - float(status.get("updated", 0))

    if age > MAX_STATUS_AGE:
        found.append(f"scheduler status is {int(age)} s old")

    for problem in status.get("problems") or []:
        found.append(f"setting: {problem}")

    return found


def main(argv: list[str]) -> int:

    try:
        status = json.loads(HEALTH_FILE.read_text())
    except (OSError, ValueError) as exc:
        print(f"unhealthy: cannot read {HEALTH_FILE}: {exc}")
        return 1

    if "--status" in argv:
        print(json.dumps(status, indent=2))

    found = problems(status, time.time())

    if found:
        print("unhealthy: " + "; ".join(found))
        return 1

    print(f"healthy; next run {status.get('next_run') or 'not scheduled'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
