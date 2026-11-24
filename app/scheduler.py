#!/usr/bin/env python3
"""
TrueNAS backup: the container's entry point.

Waits for the next time in BACKUP_SCHEDULE (cron syntax, container TZ) and
runs one backup cycle (backup.py), forever.

  * Checks the settings at start. If any are missing or wrong, the log lists
    them all and the container stays up but unhealthy, instead of
    restarting in a loop. Fix the settings and recreate the container.
  * RUN_ON_START=true also runs one cycle right after the container starts.
  * A cycle runs in its own thread, so the status file the healthcheck reads
    stays fresh during a replication that takes hours.
  * A run that is still going when the next one is due is skipped (the
    backup.py lock), never started twice.
  * Stops cleanly on Docker's stop signal. A cycle in progress is cut off;
    the replication itself keeps going on TrueNAS.
"""

from __future__ import annotations

import json
import logging
import signal
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import backup
import cronspec
import settings as settings_module
from healthcheck import HEALTH_FILE


log = logging.getLogger("scheduler")

APP_DIR = Path(__file__).resolve().parent
VERSION_FILE = APP_DIR / "VERSION"

# How often the status file is rewritten (and the clock checked).
TICK_SECONDS = 15


def version() -> str:
    try:
        return VERSION_FILE.read_text().strip()
    except OSError:
        return "dev"


class Scheduler:

    def __init__(self, s: settings_module.Settings, problems: list[str]):
        self.s = s
        self.problems = problems
        self.stop = threading.Event()
        self.worker: threading.Thread | None = None
        self.last: dict | None = None
        self.next_run: datetime | None = None
        self.schedule = None if problems else cronspec.parse(s.schedule)

    # ---- status for healthcheck.py -------------------------------------------

    def write_status(self) -> None:

        status = {
            "updated": time.time(),
            "version": version(),
            "problems": self.problems,
            "schedule": self.s.schedule,
            "next_run": self.next_run.isoformat(timespec="minutes") if self.next_run else None,
            "running": self.running,
            "last": self.last,
        }

        try:
            HEALTH_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp = HEALTH_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(status, indent=2))
            tmp.replace(HEALTH_FILE)
        except OSError as exc:
            log.warning("Could not write %s: %s", HEALTH_FILE, exc)

    @property
    def running(self) -> bool:
        return bool(self.worker and self.worker.is_alive())

    # ---- running a cycle -----------------------------------------------------

    def start_cycle(self, reason: str) -> None:

        if self.running:
            log.warning("The previous backup run is still going, so the %s run is skipped.", reason)
            return

        def work() -> None:
            started = datetime.now()
            try:
                outcome = backup.run_locked(self.s)
            except Exception as exc:  # noqa: BLE001  (never let a bug kill the scheduler)
                log.exception("The backup run crashed.")
                backup.Notifier(self.s).send(
                    "failure", "TrueNAS backup failed",
                    f"The backup run crashed: {exc}. See the container log.",
                )
                outcome = backup.Outcome("failure", "crashed", str(exc))
            if outcome is not None:
                self.last = {
                    "started": started.isoformat(timespec="seconds"),
                    "finished": datetime.now().isoformat(timespec="seconds"),
                    "status": outcome.status,
                    "message": outcome.message,
                }

        log.info("Starting the %s backup run.", reason)
        self.worker = threading.Thread(target=work, name="backup", daemon=True)
        self.worker.start()

    # ---- main loop -----------------------------------------------------------

    def loop(self) -> None:

        log.info("TrueNAS backup %s starting.", version())

        if self.problems:
            for problem in self.problems:
                log.error("Setting problem: %s", problem)
            log.error("Not scheduling any backups until the settings are fixed (then recreate the container).")
            while not self.stop.is_set():
                self.write_status()
                self.stop.wait(TICK_SECONDS)
            return

        log.info(
            "Backing up %s with task(s): %s.",
            self.s.display_name, ", ".join(self.s.replication_tasks),
        )

        if self.s.run_on_start:
            self.start_cycle("start-up")

        self.next_run = self.schedule.next_after(datetime.now())
        log.info("Schedule '%s': next run %s.", self.s.schedule, self.next_run.strftime("%a %Y-%m-%d %H:%M"))

        while not self.stop.is_set():

            self.write_status()
            now = datetime.now()

            if now >= self.next_run:
                self.start_cycle("scheduled")
                self.next_run = self.schedule.next_after(now)
                log.info("Next run %s.", self.next_run.strftime("%a %Y-%m-%d %H:%M"))

            self.stop.wait(TICK_SECONDS)

        log.info("Stopping.")


def main() -> int:

    backup.setup_logging()
    s, problems = settings_module.load()
    scheduler = Scheduler(s, problems)

    def on_signal(signum, _frame):
        log.info("Got %s.", signal.Signals(signum).name)
        scheduler.stop.set()

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    scheduler.loop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
