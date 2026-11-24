"""Tests for settings.py (environment variables)."""

import unittest

import support  # noqa: F401  (sets up the import path)

import settings

GOOD = {
    "BACKUP_HOST": "192.168.1.20",
    "IPMI_HOST": "192.168.1.21",
    "IPMI_USER": "backupctl",
    "IPMI_PASSWORD": "secret",
    "TRUENAS_URL": "wss://127.0.0.1/api/current",
    "TRUENAS_API_KEY": "1-abc",
    "REPLICATION_TASKS": "Primary to backup, Apps to backup",
}


class LoadTest(unittest.TestCase):

    def test_good_settings_and_defaults(self):
        s, problems = settings.load(GOOD)
        self.assertEqual(problems, [])
        self.assertEqual(s.schedule, "0 1 * * mon")
        self.assertEqual(s.replication_tasks, ["Primary to backup", "Apps to backup"])
        self.assertFalse(s.shutdown_on_failure)
        self.assertFalse(s.truenas_verify_tls)
        self.assertTrue(s.has_ipmi)
        self.assertEqual(s.display_name, "192.168.1.20")

    def test_everything_missing_lists_every_problem(self):
        _, problems = settings.load({})
        text = " ".join(problems)
        for name in ["BACKUP_HOST", "WOL_MAC", "TRUENAS_URL", "TRUENAS_API_KEY", "REPLICATION_TASKS"]:
            self.assertIn(name, text)

    def test_wol_alone_is_enough_to_power_on(self):
        env = {k: v for k, v in GOOD.items() if not k.startswith("IPMI")}
        env["WOL_MAC"] = "aa:bb:cc:dd:ee:ff"
        _, problems = settings.load(env)
        self.assertEqual(problems, [])

    def test_bad_values(self):
        env = dict(GOOD, BACKUP_SCHEDULE="every monday", BOOT_TIMEOUT="soon",
                   SHUTDOWN_ON_FAILURE="maybe", WOL_MAC="nope", TRUENAS_URL="https://x")
        _, problems = settings.load(env)
        text = " ".join(problems)
        for name in ["BACKUP_SCHEDULE", "BOOT_TIMEOUT", "SHUTDOWN_ON_FAILURE", "WOL_MAC", "TRUENAS_URL"]:
            self.assertIn(name, text)

    def test_ipmi_needs_password(self):
        _, problems = settings.load(dict(GOOD, IPMI_PASSWORD=""))
        self.assertTrue(any("IPMI_PASSWORD" in p for p in problems))

    def test_booleans(self):
        s, problems = settings.load(dict(GOOD, SHUTDOWN_ON_FAILURE="Yes", RUN_ON_START="1", HA_VERIFY_TLS="false"))
        self.assertEqual(problems, [])
        self.assertTrue(s.shutdown_on_failure)
        self.assertTrue(s.run_on_start)
        self.assertFalse(s.ha_verify_tls)


if __name__ == "__main__":
    unittest.main()
