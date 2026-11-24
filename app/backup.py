#!/usr/bin/env python3
"""
One backup cycle: power the backup box on, replicate, power it off, report.

The scheduler (scheduler.py) runs this on BACKUP_SCHEDULE. It can also be run
by hand inside the container:

    docker exec truenas-backup backup                 full cycle now
    docker exec truenas-backup backup --no-shutdown   leave the backup box on
    docker exec truenas-backup backup --test-notify   only send a test alert

Steps:
  1. If the backup box is off, power it on with the methods in
     POWER_ON_ORDER that are configured (IPMI, Wake-on-LAN). If it is already on, it is left on at the end, unless
     SHUTDOWN_IF_ALREADY_ON is true.
  2. Wait for it to answer on CHECK_PORT (SSH), then SETTLE_SECONDS more.
  3. Run each replication task in REPLICATION_TASKS through the primary's
     TrueNAS API, wait for its job, and check the task's state is FINISHED.
     A failed task is retried REPLICATION_RETRIES times.
  4. Shut it down with the methods in POWER_OFF_ORDER that are configured
     (its own TrueNAS API, IPMI soft-off) and wait until it is off: IPMI's
     power status when available, else until it stops answering.
  5. Send the result to Home Assistant.

Only one cycle runs at a time; a second one started meanwhile exits at once.
"""

from __future__ import annotations

import argparse
import fcntl
import logging
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import settings as settings_module
from notify import Notifier
from power import Power
from truenas_api import ApiError, TrueNAS


log = logging.getLogger("backup")

LOCK_FILE = Path("/tmp/truenas-backup/run.lock")

# How often a running replication job is checked.
POLL_SECONDS = 30

# How often the backup box is checked while booting or shutting down.
BOOT_POLL_SECONDS = 10
SHUTDOWN_POLL_SECONDS = 15

# How many API failures in a row are tolerated while a job runs.
MAX_POLL_ERRORS = 10

# Job states that end a replication job.
FINISHED_JOB_STATES = {"SUCCESS"}
FAILED_JOB_STATES = {"FAILED", "ABORTED"}


@dataclass
class Outcome:
    """The result of one cycle; it is what Home Assistant receives."""

    status: str          # success | failure | warning
    title: str
    message: str

    @property
    def ok(self) -> bool:
        return self.status == "success"


class TaskFailed(Exception):
    """One replication task failed.

    retry=False marks failures a retry cannot fix (unknown task name, or a
    replication that is still running). still_running=True means TrueNAS may
    still be replicating, so the backup box must not be powered off.
    """

    def __init__(self, reason: str, retry: bool = True, still_running: bool = False):
        super().__init__(reason)
        self.reason = reason
        self.retry = retry
        self.still_running = still_running


class Aborted(Exception):
    """Stops the cycle early; carries the reason."""


class BackupCycle:
    """Runs the five steps of one backup (see the module docstring).

    power, api and notifier are a power.Power, a truenas_api.TrueNAS (the
    primary) and a notify.Notifier; sleep and clock are injectable so the
    tests can run a 12-hour replication in milliseconds.
    """

    def __init__(self, s: settings_module.Settings, power, api, notifier,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic):
        self.s = s
        self.power = power
        self.api = api
        self.notifier = notifier
        self.sleep = sleep
        self.clock = clock
        self.started = 0.0
        self.was_on = False
        self.still_running = False

    # ---- helpers ------------------------------------------------------------

    def elapsed(self) -> str:
        seconds = int(self.clock() - self.started)
        return f"{seconds // 3600}h {(seconds % 3600) // 60:02d}m"

    def wait_until(self, check: Callable[[], bool], timeout: float, poll: float) -> bool:

        deadline = self.clock() + timeout

        while True:
            if check():
                return True
            if self.clock() >= deadline:
                return False
            self.sleep(poll)

    # ---- steps --------------------------------------------------------------

    def start_backup_box(self) -> None:

        if self.power.is_on():
            self.was_on = True
            log.info(
                "%s is already on, so it will be %s at the end.",
                self.s.display_name,
                "powered off" if self.s.shutdown_if_already_on else "left on",
            )
        elif not self.power.power_on():
            raise Aborted(
                f"Could not power on {self.s.display_name} "
                f"({', '.join(self.s.power_on_methods) or 'no method configured'} failed)"
            )

        log.info("Waiting up to %ds for %s:%d.", self.s.boot_timeout, self.s.backup_host, self.s.check_port)

        if not self.wait_until(self.power.host_up, self.s.boot_timeout, BOOT_POLL_SECONDS):
            raise Aborted(
                f"{self.s.display_name} did not come up on port {self.s.check_port} "
                f"within {self.s.boot_timeout // 60} minutes"
            )

        log.info("%s is reachable.", self.s.backup_host)

        if not self.was_on and self.s.settle_seconds:
            log.info("Letting TrueNAS finish starting for %ds.", self.s.settle_seconds)
            self.sleep(self.s.settle_seconds)

    def run_task(self, name: str) -> None:
        """Run one replication task to the end. Raises TaskFailed."""

        try:
            task = self.api.find_replication(name)
        except ApiError as exc:
            raise TaskFailed(f"'{name}': {exc}") from None

        if not task:
            raise TaskFailed(f"no replication task named '{name}' exists on the primary", retry=False)

        task_id = task["id"]

        log.info("Starting replication task '%s' (id %s).", name, task_id)

        try:
            job_id = self.api.run_replication(task_id)
        except ApiError as exc:
            raise TaskFailed(f"'{name}' could not be started: {exc}") from None

        deadline = self.clock() + self.s.replication_timeout
        errors = 0

        while True:

            self.sleep(POLL_SECONDS)

            try:
                job = self.api.job(job_id) or {}
                errors = 0
            except ApiError as exc:
                errors += 1
                log.warning("Checking job %s failed (%d in a row): %s", job_id, errors, exc)
                if errors >= MAX_POLL_ERRORS:
                    raise TaskFailed(
                        f"'{name}': lost contact with the TrueNAS API ({exc})",
                        retry=False, still_running=True,
                    ) from None
                job = {"state": "RUNNING"}

            state = job.get("state", "UNKNOWN")

            if state in FINISHED_JOB_STATES:
                break

            if state in FAILED_JOB_STATES:
                error = (job.get("error") or "").strip().splitlines()
                raise TaskFailed(f"'{name}' failed ({state}{': ' + error[-1] if error else ''})")

            if self.clock() >= deadline:
                raise TaskFailed(
                    f"'{name}' was still running after {self.s.replication_timeout // 3600}h",
                    retry=False, still_running=True,
                )

        # The job says it worked; the task's own state should agree.
        try:
            task_state = self.api.replication_state(task_id)
        except ApiError as exc:
            log.warning("Could not read the state of '%s' after its job: %s", name, exc)
            task_state = {}

        state = task_state.get("state")

        if state and state != "FINISHED":
            raise TaskFailed(f"'{name}' ended in state {state}: {task_state.get('error') or 'no error text'}")

        log.info("Replication '%s' finished.", name)

    def replicate(self) -> list[str]:
        """Runs every task; returns the failures (empty = all good)."""

        failures: list[str] = []
        self.still_running = False

        for name in self.s.replication_tasks:

            attempt = 0

            while True:
                try:
                    self.run_task(name)
                    break
                except TaskFailed as exc:
                    if exc.still_running:
                        self.still_running = True
                    if not exc.retry or attempt >= self.s.replication_retries:
                        failures.append(exc.reason)
                        break
                    attempt += 1
                    log.warning(
                        "%s. Retrying in %ds (retry %d of %d).",
                        exc.reason, self.s.retry_wait, attempt, self.s.replication_retries,
                    )
                    self.sleep(self.s.retry_wait)

        return failures

    def shut_down(self) -> bool:

        log.info("Shutting down %s (%s).", self.s.display_name, ", ".join(self.s.power_off_methods))

        if not self.power.shut_down():
            log.warning("Every shutdown method failed.")
            return False

        if self.wait_until(self.power.is_off, self.s.shutdown_timeout, SHUTDOWN_POLL_SECONDS):
            if self.power.confirms_power_state:
                log.info("%s is powered off.", self.s.display_name)
            else:
                log.info("%s stopped answering (no IPMI to confirm the power state).", self.s.display_name)
            return True

        return False

    # ---- the cycle ----------------------------------------------------------

    def failed(self, reason: str, no_shutdown: bool, box_up: bool) -> Outcome:

        log.error("FAILED: %s", reason)

        if not (self.s.shutdown_on_failure and box_up and not self.was_on and not no_shutdown):
            note = "The backup box was left as it is."
        elif self.still_running:
            note = "The backup box was left on because a replication is still running."
        elif self.shut_down():
            note = "The backup box was powered off."
        else:
            note = "Powering the backup box off also failed."

        return Outcome(
            "failure",
            "TrueNAS backup failed",
            f"{reason}. {note} Stopped after {self.elapsed()}.",
        )

    def run(self, no_shutdown: bool = False) -> Outcome:

        self.started = self.clock()
        self.was_on = False
        self.still_running = False

        log.info("=== Backup run started ===")

        try:
            self.start_backup_box()
        except Aborted as exc:
            outcome = self.failed(str(exc), no_shutdown, box_up=self.power.host_up())
            return self.finish(outcome)

        failures = self.replicate()

        if failures:
            outcome = self.failed("Replication failed: " + "; ".join(failures), no_shutdown, box_up=True)
            return self.finish(outcome)

        log.info("All replication tasks finished.")

        if no_shutdown:
            outcome = Outcome("success", "TrueNAS backup succeeded",
                              f"Replication succeeded in {self.elapsed()}. The backup box was left on (--no-shutdown).")
        elif self.was_on and not self.s.shutdown_if_already_on:
            outcome = Outcome("success", "TrueNAS backup succeeded",
                              f"Replication succeeded in {self.elapsed()}. The backup box was already on, so it was left on.")
        elif self.shut_down():
            outcome = Outcome("success", "TrueNAS backup succeeded",
                              f"Replication succeeded and the backup box shut down after {self.elapsed()}.")
        else:
            outcome = Outcome("warning", "TrueNAS backup: box still on",
                              f"Replication succeeded, but {self.s.display_name} did not shut down "
                              f"within {self.s.shutdown_timeout // 60} minutes. Check it.")

        return self.finish(outcome)

    def finish(self, outcome: Outcome) -> Outcome:

        if outcome.ok:
            log.info(outcome.message)
        else:
            log.warning(outcome.message)

        self.notifier.send(outcome.status, outcome.title, outcome.message)
        log.info("=== Backup run finished: %s ===", outcome.status)
        return outcome


# ---- wiring -------------------------------------------------------------------

def build(s: settings_module.Settings) -> BackupCycle:
    return BackupCycle(
        s,
        power=Power(s),
        api=TrueNAS(s.truenas_url, s.truenas_api_key, verify_tls=s.truenas_verify_tls),
        notifier=Notifier(s),
    )


class RunLock:
    """An exclusive lock, so a manual run and a scheduled one never overlap."""

    def __init__(self, path: Path = LOCK_FILE):
        self.path = path
        self._fh = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "w")
        try:
            fcntl.flock(self._fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            self._fh.close()
            self._fh = None
            return False

    def release(self) -> None:
        if self._fh:
            fcntl.flock(self._fh, fcntl.LOCK_UN)
            self._fh.close()
            self._fh = None


def run_locked(s: settings_module.Settings, no_shutdown: bool = False) -> Outcome | None:
    """Runs one cycle unless another is running (then returns None)."""

    lock = RunLock()

    if not lock.acquire():
        log.warning("Another backup run is already in progress, so this one is skipped.")
        return None

    try:
        return build(s).run(no_shutdown=no_shutdown)
    finally:
        lock.release()


def setup_logging() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )


def main(argv: list[str] | None = None) -> int:

    parser = argparse.ArgumentParser(description="Run one TrueNAS backup cycle now.")
    parser.add_argument("--no-shutdown", action="store_true", help="leave the backup box on afterwards")
    parser.add_argument("--test-notify", action="store_true", help="only send a test message to Home Assistant")
    args = parser.parse_args(argv)

    setup_logging()
    s, problems = settings_module.load()

    if args.test_notify:
        sent = Notifier(s).send(
            "test", "TrueNAS backup test",
            "Test message from the backup container. If you see this on your phone, alerts work.",
        )
        return 0 if sent else 1

    if problems:
        for problem in problems:
            log.error("Setting problem: %s", problem)
        return 2

    outcome = run_locked(s, no_shutdown=args.no_shutdown)

    if outcome is None:
        return 1

    return 0 if outcome.ok else 1


if __name__ == "__main__":
    sys.exit(main())
