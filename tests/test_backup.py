"""Tests for backup.py: the whole cycle against fake power, API and Home Assistant."""

import unittest

import support  # noqa: F401  (sets up the import path)

import backup
import settings
from truenas_api import ApiError

ENV = {
    "BACKUP_HOST": "192.168.1.20",
    "BACKUP_NAME": "backup box",
    "IPMI_HOST": "192.168.1.21",
    "IPMI_USER": "backupctl",
    "IPMI_PASSWORD": "secret",
    "TRUENAS_URL": "wss://127.0.0.1/api/current",
    "TRUENAS_API_KEY": "1-abc",
    "REPLICATION_TASKS": "Primary to backup",
    "RETRY_WAIT": "300",
}


class Clock:
    """A fake clock: sleeping just moves time forward."""

    def __init__(self):
        self.now = 1_000.0

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class FakePower:

    def __init__(self, on=False, boots=True, powers_off=True, power_on_works=True):
        self.on = on
        self.boots = boots
        self.powers_off = powers_off
        self.power_on_works = power_on_works
        self.calls = []

    def is_on(self):
        return self.on

    def power_on(self):
        self.calls.append("on")
        if self.power_on_works:
            self.on = self.boots
        return self.power_on_works

    def host_up(self):
        return self.on

    def soft_off(self):
        self.calls.append("soft_off")
        if self.powers_off:
            self.on = False
        return True

    def is_off(self):
        return not self.on


class FakeApi:
    """Job states are handed out in order, one per poll."""

    def __init__(self, tasks=None, job_states=None, final_state="FINISHED", fail_start=False):
        self.tasks = tasks if tasks is not None else {"Primary to backup": 7}
        self.job_states = list(job_states) if job_states is not None else ["RUNNING", "SUCCESS"]
        self.final_state = final_state
        self.fail_start = fail_start
        self.runs = []

    def find_replication(self, name):
        return {"id": self.tasks[name], "name": name} if name in self.tasks else None

    def run_replication(self, task_id):
        if self.fail_start:
            raise ApiError("replication.run: boom")
        self.runs.append(task_id)
        return 100 + len(self.runs)

    def job(self, job_id):
        state = self.job_states.pop(0) if self.job_states else "RUNNING"
        if state == "API_DOWN":
            raise ApiError("cannot connect")
        return {"id": job_id, "state": state, "error": "cannot receive: dataset busy" if state == "FAILED" else None}

    def replication_state(self, task_id):
        return {"state": self.final_state, "datetime": {"$date": 2_000_000}}


class FakeNotifier:

    def __init__(self):
        self.sent = []

    def send(self, status, title, message):
        self.sent.append((status, title, message))
        return True


def make(power=None, api=None, **env):
    s, problems = settings.load(dict(ENV, **env))
    assert problems == [], problems
    clock = Clock()
    cycle = backup.BackupCycle(
        s, power or FakePower(), api or FakeApi(), FakeNotifier(),
        sleep=clock.sleep, clock=clock.time, wall=clock.time,
    )
    return cycle


class CycleTest(unittest.TestCase):

    def test_success_powers_on_replicates_and_powers_off(self):
        cycle = make()
        outcome = cycle.run()
        self.assertEqual(outcome.status, "success")
        self.assertEqual(cycle.power.calls, ["on", "soft_off"])
        self.assertEqual(cycle.api.runs, [7])
        self.assertEqual(cycle.notifier.sent[0][0], "success")
        self.assertIn("powered off", outcome.message)

    def test_already_on_is_left_on(self):
        cycle = make(power=FakePower(on=True))
        outcome = cycle.run()
        self.assertEqual(outcome.status, "success")
        self.assertEqual(cycle.power.calls, [])
        self.assertIn("already on", outcome.message)

    def test_already_on_powered_off_when_asked(self):
        cycle = make(power=FakePower(on=True), SHUTDOWN_IF_ALREADY_ON="true")
        cycle.run()
        self.assertEqual(cycle.power.calls, ["soft_off"])

    def test_no_shutdown(self):
        cycle = make()
        outcome = cycle.run(no_shutdown=True)
        self.assertEqual(outcome.status, "success")
        self.assertEqual(cycle.power.calls, ["on"])

    def test_failure_retries_once_then_leaves_box_on(self):
        api = FakeApi(job_states=["FAILED", "FAILED"])
        cycle = make(api=api)
        outcome = cycle.run()
        self.assertEqual(outcome.status, "failure")
        self.assertEqual(len(api.runs), 2)
        self.assertEqual(cycle.power.calls, ["on"])
        self.assertIn("dataset busy", outcome.message)
        self.assertIn("left as it is", outcome.message)

    def test_retry_that_works_is_a_success(self):
        api = FakeApi(job_states=["FAILED", "RUNNING", "SUCCESS"])
        cycle = make(api=api)
        self.assertEqual(cycle.run().status, "success")
        self.assertEqual(len(api.runs), 2)

    def test_failure_can_power_off(self):
        cycle = make(api=FakeApi(job_states=["FAILED", "FAILED"]), SHUTDOWN_ON_FAILURE="yes")
        outcome = cycle.run()
        self.assertEqual(outcome.status, "failure")
        self.assertIn("soft_off", cycle.power.calls)
        self.assertIn("was powered off", outcome.message)

    def test_missing_task_is_not_retried(self):
        api = FakeApi(tasks={})
        cycle = make(api=api)
        outcome = cycle.run()
        self.assertEqual(outcome.status, "failure")
        self.assertEqual(api.runs, [])
        self.assertIn("no replication task named 'Primary to backup'", outcome.message)

    def test_timeout_never_powers_off_a_running_replication(self):
        api = FakeApi(job_states=[])          # RUNNING forever
        cycle = make(api=api, REPLICATION_TIMEOUT="3600", SHUTDOWN_ON_FAILURE="yes")
        outcome = cycle.run()
        self.assertEqual(outcome.status, "failure")
        self.assertNotIn("soft_off", cycle.power.calls)
        self.assertIn("still running after 1h", outcome.message)
        self.assertEqual(len(api.runs), 1)

    def test_short_api_outage_while_polling_is_tolerated(self):
        api = FakeApi(job_states=["API_DOWN", "API_DOWN", "RUNNING", "SUCCESS"])
        self.assertEqual(make(api=api).run().status, "success")

    def test_task_state_must_be_finished(self):
        api = FakeApi(final_state="ERROR")
        outcome = make(api=api, REPLICATION_RETRIES="0").run()
        self.assertEqual(outcome.status, "failure")
        self.assertIn("state ERROR", outcome.message)

    def test_start_error_is_retried(self):
        api = FakeApi(fail_start=True)
        outcome = make(api=api).run()
        self.assertEqual(outcome.status, "failure")
        self.assertIn("could not be started", outcome.message)

    def test_power_on_failure(self):
        cycle = make(power=FakePower(power_on_works=False))
        outcome = cycle.run()
        self.assertEqual(outcome.status, "failure")
        self.assertIn("Could not power on backup box", outcome.message)
        self.assertEqual(cycle.api.runs, [])

    def test_box_that_never_boots(self):
        cycle = make(power=FakePower(boots=False), BOOT_TIMEOUT="60")
        outcome = cycle.run()
        self.assertEqual(outcome.status, "failure")
        self.assertIn("did not come up on port 22 within 1 minutes", outcome.message)

    def test_box_that_does_not_power_off_is_a_warning(self):
        cycle = make(power=FakePower(powers_off=False), SHUTDOWN_TIMEOUT="60")
        outcome = cycle.run()
        self.assertEqual(outcome.status, "warning")
        self.assertEqual(cycle.notifier.sent[0][0], "warning")

    def test_several_tasks_run_in_order(self):
        api = FakeApi(tasks={"A": 1, "B": 2}, job_states=["SUCCESS", "SUCCESS"])
        make(api=api, REPLICATION_TASKS="A,B").run()
        self.assertEqual(api.runs, [1, 2])


class DateTest(unittest.TestCase):

    def test_date_forms(self):
        self.assertEqual(backup.date_ms({"$date": 1234}), 1234)
        self.assertEqual(backup.date_ms(5678), 5678)
        self.assertEqual(backup.date_ms("1970-01-01T00:00:01Z"), 1000)
        self.assertEqual(backup.date_ms(None), 0)
        self.assertEqual(backup.date_ms("garbage"), 0)


if __name__ == "__main__":
    unittest.main()
