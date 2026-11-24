"""Tests for cronspec.py, the BACKUP_SCHEDULE parser."""

import unittest
from datetime import datetime

import support  # noqa: F401  (sets up the import path)

import cronspec


class NextRunTest(unittest.TestCase):

    def test_default_is_monday_at_one(self):
        schedule = cronspec.parse("0 1 * * mon")
        # 2026-11-22 is a Sunday.
        self.assertEqual(schedule.next_after(datetime(2026, 11, 22, 23, 59)), datetime(2026, 11, 23, 1, 0))
        # Exactly at the run time, the next one is a week later.
        self.assertEqual(schedule.next_after(datetime(2026, 11, 23, 1, 0)), datetime(2026, 11, 30, 1, 0))

    def test_numbers_names_and_sunday_as_seven(self):
        self.assertEqual(cronspec.parse("0 1 * * 1").weekdays, frozenset({1}))
        self.assertEqual(cronspec.parse("0 1 * * Monday").weekdays, frozenset({1}))
        self.assertEqual(cronspec.parse("0 1 * * 7").weekdays, frozenset({0}))
        self.assertEqual(cronspec.parse("0 1 * * mon-fri").weekdays, frozenset({1, 2, 3, 4, 5}))
        self.assertEqual(cronspec.parse("0 1 * * mon,thu").weekdays, frozenset({1, 4}))

    def test_steps_and_ranges(self):
        self.assertEqual(cronspec.parse("*/15 * * * *").minutes, frozenset({0, 15, 30, 45}))
        self.assertEqual(cronspec.parse("0 0-6/3 * * *").hours, frozenset({0, 3, 6}))
        self.assertEqual(cronspec.parse("0 0 * jan,jul *").months, frozenset({1, 7}))

    def test_day_of_month_or_weekday(self):
        # As in cron: the 1st of the month OR any Monday.
        schedule = cronspec.parse("0 2 1 * mon")
        self.assertTrue(schedule.matches(datetime(2026, 12, 1, 2, 0)))    # a Tuesday, the 1st
        self.assertTrue(schedule.matches(datetime(2026, 12, 7, 2, 0)))    # a Monday
        self.assertFalse(schedule.matches(datetime(2026, 12, 8, 2, 0)))   # neither

    def test_monthly_first_monday_style_dom_only(self):
        schedule = cronspec.parse("30 3 15 * *")
        self.assertEqual(schedule.next_after(datetime(2026, 11, 15, 3, 30)), datetime(2026, 12, 15, 3, 30))

    def test_bad_schedules(self):
        for text in ["", "0 1 * *", "60 1 * * *", "0 24 * * *", "0 1 * * funday",
                     "0 1 5-2 * *", "*/0 * * * *", "0 0 31 2 *"]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                cronspec.parse(text)


if __name__ == "__main__":
    unittest.main()
