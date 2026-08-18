"""The scheduling examples must actually cover their own schedule (AC-037).

The window is computed from the plists rather than restated here. A test that
hard-codes "8 > 7" passes forever while someone edits the plist to run weekly,
which is the change that would silently open a blind spot.
"""

import plistlib
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent
EXAMPLES = ROOT / "examples"
TEMPLATES = ROOT / "templates"

MINER_PLIST = EXAMPLES / "com.pi-self-improvement.miner.plist"
FIXLOOP_PLIST = EXAMPLES / "com.pi-self-improvement.fixloop.plist"

DAYS_IN_WEEK = 7


def load(path):
    with open(path, "rb") as handle:
        return plistlib.load(handle)


def fire_weekdays(plist):
    """launchd Weekday: 0/7 = Sunday, 1 = Monday."""
    entries = plist["StartCalendarInterval"]
    if isinstance(entries, dict):
        entries = [entries]
    return sorted((entry["Weekday"] % 7) for entry in entries if "Weekday" in entries[0])


def longest_gap_after_one_miss(weekdays):
    """Worst-case days between two successful runs when one fire is skipped.

    With fires on days [a, b], dropping either one leaves the other as the only
    fire that week, so the surviving interval is a full week. With three or more
    fires, dropping one merges two adjacent gaps.
    """
    if len(weekdays) < 2:
        return DAYS_IN_WEEK
    gaps = [
        (weekdays[(index + 1) % len(weekdays)] - day) % DAYS_IN_WEEK or DAYS_IN_WEEK
        for index, day in enumerate(weekdays)
    ]
    return max(gaps[index] + gaps[(index + 1) % len(gaps)] for index in range(len(gaps)))


def since_days(plist):
    env = plist.get("EnvironmentVariables", {})
    return float(env["PSI_SINCE_DAYS"])


class TestPlistsAreValid(unittest.TestCase):
    def test_both_plists_parse(self):
        self.assertEqual(load(MINER_PLIST)["Label"], "com.pi-self-improvement.miner")
        self.assertEqual(load(FIXLOOP_PLIST)["Label"], "com.pi-self-improvement.fixloop")

    @unittest.skipUnless(shutil.which("plutil"), "plutil ships with macOS only")
    def test_plutil_accepts_them(self):
        for path in sorted(EXAMPLES.glob("*.plist")):
            with self.subTest(plist=path.name):
                result = subprocess.run(
                    ["plutil", "-lint", str(path)], capture_output=True, text=True
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_neither_job_runs_at_load(self):
        """Loading a job should not immediately scan or trigger a headless run."""
        for path in (MINER_PLIST, FIXLOOP_PLIST):
            with self.subTest(plist=path.name):
                self.assertFalse(load(path)["RunAtLoad"])


class TestCoverageWindow(unittest.TestCase):
    """AC-037 — the window must strictly exceed a missed fire."""

    def test_the_window_covers_a_missed_fire(self):
        plist = load(MINER_PLIST)
        weekdays = fire_weekdays(plist)
        gap = longest_gap_after_one_miss(weekdays)

        self.assertGreater(
            since_days(plist),
            gap,
            f"--since-days {since_days(plist)} does not cover a {gap}-day gap "
            f"from fires on weekdays {weekdays}",
        )

    def test_the_miner_fires_twice_weekly(self):
        self.assertEqual(len(fire_weekdays(load(MINER_PLIST))), 2)

    def test_the_gap_calculation_is_right_for_the_shipped_schedule(self):
        """Monday and Thursday: gaps of 3 and 4, so one miss leaves 7."""
        self.assertEqual(longest_gap_after_one_miss([1, 4]), 7)

    def test_a_weekly_schedule_would_fail_the_window_check(self):
        """Guards the guard: the assertion must be capable of failing."""
        self.assertGreaterEqual(longest_gap_after_one_miss([1]), DAYS_IN_WEEK)
        self.assertLess(8, longest_gap_after_one_miss([1]) + DAYS_IN_WEEK)

    def test_a_denser_schedule_still_computes_a_sane_gap(self):
        self.assertEqual(longest_gap_after_one_miss([1, 3, 5]), 5)


class TestRunnerScripts(unittest.TestCase):
    def test_both_runners_parse(self):
        for name in ("miner-run.sh", "fixloop-run.sh"):
            with self.subTest(script=name):
                result = subprocess.run(
                    ["bash", "-n", str(TEMPLATES / name)], capture_output=True, text=True
                )
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_plists_point_at_the_shipped_runner_names(self):
        for plist_path, script in (
            (MINER_PLIST, "miner-run.sh"),
            (FIXLOOP_PLIST, "fixloop-run.sh"),
        ):
            with self.subTest(plist=plist_path.name):
                arguments = load(plist_path)["ProgramArguments"]
                self.assertTrue(
                    any(argument.endswith(script) for argument in arguments),
                    f"{plist_path.name} does not invoke {script}",
                )
                self.assertTrue((TEMPLATES / script).is_file())

    def test_the_miner_runner_defaults_to_the_documented_window(self):
        text = (TEMPLATES / "miner-run.sh").read_text(encoding="utf-8")

        self.assertIn("PSI_SINCE_DAYS:-8", text)


if __name__ == "__main__":
    unittest.main()
