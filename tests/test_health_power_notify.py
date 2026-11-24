"""Tests for healthcheck.py, power.py and notify.py."""

import json
import subprocess
import unittest
import urllib.error

import support  # noqa: F401  (sets up the import path)

import healthcheck
import power
import settings
from notify import Notifier

ENV = {
    "BACKUP_HOST": "192.168.1.20",
    "IPMI_HOST": "192.168.1.21",
    "IPMI_USER": "backupctl",
    "IPMI_PASSWORD": "secret",
    "TRUENAS_URL": "wss://127.0.0.1/api/current",
    "TRUENAS_API_KEY": "1-abc",
    "REPLICATION_TASKS": "Primary to backup",
    "HA_WEBHOOK_URL": "http://ha:8123/api/webhook/x",
}


class HealthTest(unittest.TestCase):

    def test_fresh_status_is_healthy(self):
        self.assertEqual(healthcheck.problems({"updated": 100, "problems": []}, 110), [])

    def test_stale_or_misconfigured(self):
        found = healthcheck.problems({"updated": 0, "problems": ["BACKUP_HOST is not set"]}, 1000)
        self.assertEqual(len(found), 2)


class PowerTest(unittest.TestCase):

    def setUp(self):
        self.s, _ = settings.load(ENV)
        self.calls = []

    def fake_run(self, output, code=0):
        def run(cmd, env, **kwargs):
            self.calls.append((cmd, env))
            return subprocess.CompletedProcess(cmd, code, stdout=output, stderr="")
        return run

    def test_password_goes_through_the_environment(self):
        p = power.Power(self.s, run=self.fake_run("Chassis Power is on\n"))
        self.assertEqual(p.ipmi_state(), "on")
        cmd, env = self.calls[0]
        self.assertNotIn("secret", cmd)
        self.assertIn("-E", cmd)
        self.assertEqual(env["IPMI_PASSWORD"], "secret")
        self.assertEqual(cmd[-3:], ["chassis", "power", "status"])

    def test_states(self):
        self.assertEqual(power.Power(self.s, run=self.fake_run("Chassis Power is off")).ipmi_state(), "off")
        self.assertEqual(power.Power(self.s, run=self.fake_run("Error", code=1)).ipmi_state(), "unknown")

    def test_magic_packet(self):
        packet = power.magic_packet("aa:bb:cc:dd:ee:ff")
        self.assertEqual(len(packet), 102)
        self.assertEqual(packet[:6], b"\xff" * 6)
        self.assertEqual(packet[6:12], bytes.fromhex("aabbccddeeff"))
        with self.assertRaises(ValueError):
            power.magic_packet("aa:bb")


class NotifyTest(unittest.TestCase):

    def test_posts_json(self):
        s, _ = settings.load(ENV)
        seen = []

        class Response:
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def read(self):
                return b""

        def opener(request, timeout, context):
            seen.append(request)
            return Response()

        self.assertTrue(Notifier(s, opener=opener).send("failure", "T", "M"))
        body = json.loads(seen[0].data)
        self.assertEqual(body["status"], "failure")
        self.assertEqual(body["message"], "M")
        self.assertEqual(seen[0].get_method(), "POST")

    def test_unreachable_tries_three_times(self):
        s, _ = settings.load(ENV)
        tries = []

        def opener(request, timeout, context):
            tries.append(1)
            raise urllib.error.URLError("down")

        self.assertFalse(Notifier(s, opener=opener, sleep=lambda _: None).send("failure", "T", "M"))
        self.assertEqual(len(tries), 3)

    def test_no_url_sends_nothing(self):
        s, _ = settings.load(dict(ENV, HA_WEBHOOK_URL=""))
        self.assertFalse(Notifier(s, opener=None).send("failure", "T", "M"))


if __name__ == "__main__":
    unittest.main()
