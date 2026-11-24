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
        for name in ["BACKUP_HOST", "WOL_MAC", "BACKUP_TRUENAS_URL", "TRUENAS_URL", "TRUENAS_API_KEY", "REPLICATION_TASKS"]:
            self.assertIn(name, text)

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

    def test_ipmi_is_optional(self):
        env = {k: v for k, v in GOOD.items() if not k.startswith("IPMI")}
        env.update(WOL_MAC="aa:bb:cc:dd:ee:ff", BACKUP_TRUENAS_URL="wss://192.168.1.20/api/current",
                   BACKUP_TRUENAS_API_KEY="2-key")
        s, problems = settings.load(env)
        self.assertEqual(problems, [])
        self.assertFalse(s.has_ipmi)

    def test_booleans(self):
        s, problems = settings.load(dict(GOOD, SHUTDOWN_ON_FAILURE="Yes", RUN_ON_START="1", HA_VERIFY_TLS="false"))
        self.assertEqual(problems, [])
        self.assertTrue(s.shutdown_on_failure)
        self.assertTrue(s.run_on_start)
        self.assertFalse(s.ha_verify_tls)


if __name__ == "__main__":
    unittest.main()


class PowerMethodsTest(unittest.TestCase):

    def base(self, **extra):
        env = {k: v for k, v in GOOD.items() if not k.startswith("IPMI")}
        env.update(extra)
        return env

    def test_defaults_with_everything(self):
        s, problems = settings.load(dict(GOOD, WOL_MAC="aa:bb:cc:dd:ee:ff",
                                         BACKUP_TRUENAS_URL="wss://192.168.1.20/api/current",
                                         BACKUP_TRUENAS_API_KEY="2-key"))
        self.assertEqual(problems, [])
        self.assertEqual(s.power_on_methods, ["ipmi", "wol"])
        self.assertEqual(s.power_off_methods, ["api", "ipmi"])

    def test_wol_and_backup_api_without_ipmi(self):
        s, problems = settings.load(self.base(WOL_MAC="aa:bb:cc:dd:ee:ff",
                                              BACKUP_TRUENAS_URL="wss://192.168.1.20/api/current",
                                              BACKUP_TRUENAS_API_KEY="2-key"))
        self.assertEqual(problems, [])
        self.assertEqual(s.power_on_methods, ["wol"])
        self.assertEqual(s.power_off_methods, ["api"])

    def test_wol_alone_cannot_shut_down(self):
        _, problems = settings.load(self.base(WOL_MAC="aa:bb:cc:dd:ee:ff"))
        self.assertTrue(any("no way to shut the backup box down" in p for p in problems))

    def test_backup_api_alone_cannot_power_on(self):
        _, problems = settings.load(self.base(BACKUP_TRUENAS_URL="wss://192.168.1.20/api/current",
                                              BACKUP_TRUENAS_API_KEY="2-key"))
        self.assertTrue(any("no way to power the backup box on" in p for p in problems))

    def test_custom_order(self):
        s, problems = settings.load(dict(GOOD, WOL_MAC="aa:bb:cc:dd:ee:ff",
                                         POWER_ON_ORDER="wol, ipmi", POWER_OFF_ORDER="ipmi"))
        self.assertEqual(problems, [])
        self.assertEqual(s.power_on_methods, ["wol", "ipmi"])
        self.assertEqual(s.power_off_methods, ["ipmi"])

    def test_order_can_leave_a_method_out(self):
        _, problems = settings.load(dict(GOOD, POWER_ON_ORDER="wol"))
        self.assertTrue(any("no way to power the backup box on" in p for p in problems))

    def test_bad_order_and_half_configured(self):
        _, problems = settings.load(dict(GOOD, POWER_ON_ORDER="ipmi,magic", IPMI_PASSWORD="",
                                         BACKUP_TRUENAS_URL="wss://x/api/current"))
        text = " ".join(problems)
        self.assertIn("POWER_ON_ORDER: 'magic'", text)
        self.assertIn("IPMI needs all of", text)
        self.assertIn("BACKUP_TRUENAS_API_KEY", text)
