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
from truenas_api import ApiError

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


class FakeBackupApi:

    def __init__(self, works=True):
        self.works = works
        self.reasons = []

    def shutdown(self, reason):
        self.reasons.append(reason)
        if not self.works:
            raise ApiError("cannot connect")


class PowerOrderTest(unittest.TestCase):

    def make(self, ipmi_ok=True, api=None, **extra):
        env = dict(ENV, WOL_MAC="aa:bb:cc:dd:ee:ff", BACKUP_TRUENAS_URL="wss://b/api/current",
                   BACKUP_TRUENAS_API_KEY="2-key", **extra)
        env = {k: v for k, v in env.items() if v is not None}
        s, problems = settings.load(env)
        self.assertEqual(problems, [])
        self.ipmi_calls = []

        def run(cmd, env, **kwargs):
            self.ipmi_calls.append(cmd[-1])
            return subprocess.CompletedProcess(cmd, 0 if ipmi_ok else 1, stdout="", stderr="")

        p = power.Power(s, run=run, backup_api=api or FakeBackupApi())
        self.wol_sent = []
        p.send_wol = lambda: self.wol_sent.append(1)
        return p

    def test_power_on_prefers_ipmi_then_falls_back_to_wol(self):
        self.assertEqual(self.make().power_on(), "ipmi")
        self.assertEqual(self.wol_sent, [])
        self.assertEqual(self.make(ipmi_ok=False).power_on(), "wol")
        self.assertEqual(self.wol_sent, [1])

    def test_power_on_order_can_put_wol_first(self):
        self.assertEqual(self.make(POWER_ON_ORDER="wol,ipmi").power_on(), "wol")
        self.assertEqual(self.ipmi_calls, [])

    def test_power_on_without_ipmi(self):
        p = self.make(IPMI_HOST=None, IPMI_USER=None, IPMI_PASSWORD=None)
        self.assertEqual(p.power_on(), "wol")
        self.assertFalse(p.confirms_power_state)

    def test_shutdown_prefers_the_backup_api(self):
        api = FakeBackupApi()
        self.assertEqual(self.make(api=api).shut_down(), "api")
        self.assertEqual(len(api.reasons), 1)
        self.assertNotIn("soft", self.ipmi_calls)

    def test_shutdown_falls_back_to_ipmi(self):
        self.assertEqual(self.make(api=FakeBackupApi(works=False)).shut_down(), "ipmi")
        self.assertIn("soft", self.ipmi_calls)

    def test_shutdown_order_ipmi_first(self):
        api = FakeBackupApi()
        self.assertEqual(self.make(api=api, POWER_OFF_ORDER="ipmi,api").shut_down(), "ipmi")
        self.assertEqual(api.reasons, [])

    def test_everything_fails(self):
        self.assertIsNone(self.make(ipmi_ok=False, api=FakeBackupApi(works=False)).shut_down())

    def test_off_without_ipmi_means_not_answering(self):
        p = self.make(IPMI_HOST=None, IPMI_USER=None, IPMI_PASSWORD=None)
        p.host_up = lambda: False
        self.assertTrue(p.is_off())
        p.host_up = lambda: True
        self.assertFalse(p.is_off())


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
