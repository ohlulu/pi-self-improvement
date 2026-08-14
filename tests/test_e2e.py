"""The full lifecycle over synthetic fixtures (REQ-001, REQ-015, REQ-016).

Every other test file checks one module. This one drives the CLI through the
sequence a real user lives: scan, scan again, decide, and then meet the same
friction after it was supposedly fixed. The steps depend on each other, which is
the point — the bugs this catches are the ones that only appear when state
written by one run is read by the next.
"""

import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from pi_self_improvement import cli

from . import support

FIXTURES = Path(__file__).parent / "fixtures" / "sessions"

WATERMARK = "2026-03-01T00:00:00Z"
AFTER_WATERMARK = "2026-06-01T12:00:00Z"


class EndToEndTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.sessions = self.home / ".pi" / "agent" / "sessions"
        self.sessions.parent.mkdir(parents=True)
        shutil.copytree(FIXTURES, self.sessions)
        self.output_root = self.home / ".pi-self-improvement"
        self.addCleanup(self._tmp.cleanup)

    def scan(self, *argv, expect=cli.EXIT_OK):
        out, err = io.StringIO(), io.StringIO()
        code = cli.main(["--home", str(self.home), "--all", *argv], stdout=out, stderr=err)
        self.assertEqual(code, expect, msg=f"stderr: {err.getvalue()}")
        return out.getvalue()

    def keys_on_disk(self):
        """Cumulative across every run. For "did this run stage X", use
        `keys_in_latest_run` — proposals from earlier runs stay on disk."""
        return sorted(
            json.loads(path.read_text(encoding="utf-8"))["key"]
            for path in (self.output_root / "proposals").rglob("*.json")
        )

    def latest_packet(self):
        packets = sorted((self.output_root / "review-packets").glob("*.md"))
        return packets[-1].read_text(encoding="utf-8")

    def latest_run(self):
        runs = sorted((self.output_root / "runs").glob("*.json"))
        return json.loads(runs[-1].read_text(encoding="utf-8"))

    def keys_in_latest_run(self):
        """What *this* run staged, as opposed to what is on disk from earlier runs."""
        return sorted(entry["key"] for entry in self.latest_run()["proposals"])

    def add_late_failure(self, name: str) -> None:
        """A new transcript carrying the same friction, dated after the watermark."""
        records = [
            support.session_record(name, cwd="/tmp/pi-fixtures/beta"),
            support.user_record("try the search again"),
            support.tool_call_record("late-1", "demoext_search_items", {"query": "widgets"}),
            {
                **support.tool_result_record(
                    "late-1", "demoext_search_items", "error: upstream timed out", is_error=True
                ),
                "timestamp": AFTER_WATERMARK,
            },
        ]
        support.make_root_transcript(
            self.sessions, "--tmp-pi-fixtures-beta--", f"2026-06-01T12-00-00-000Z_{name}", records
        )


class TestFullLifecycle(EndToEndTestCase):
    def test_scan_dedup_resolve_regress(self):
        """One test, four acts, because the acts are what interact.

        Splitting this into four independent tests would rebuild the state each
        time and lose the only thing worth checking: that run N+1 reads what run
        N wrote.
        """
        # 1. A first scan stages proposals and writes all three outputs.
        self.scan()
        first_keys = self.keys_on_disk()

        self.assertTrue(first_keys, "fixtures should produce proposals")
        self.assertTrue((self.output_root / "state.json").is_file())
        self.assertTrue(list((self.output_root / "runs").glob("*.json")))

        target = "tool:ext:demoext"
        self.assertIn(target, first_keys)

        # 2. The same evidence again stages nothing.
        stdout = self.scan()
        self.assertIn("staged 0 proposal(s)", stdout)
        self.assertEqual(self.keys_on_disk(), first_keys)

        # 3. Resolving it suppresses the target even when seen keys are ignored.
        self.scan("--resolve", target, "--decision", "fixed", "--resolved-at", WATERMARK)
        self.scan("--include-seen")

        restaged = self.keys_in_latest_run()
        self.assertNotIn(target, restaged)
        self.assertTrue(restaged, "--include-seen should restage the other targets")

        # 4. New evidence dated after the watermark brings it back as a regression.
        self.add_late_failure("late-regression")
        stdout = self.scan()

        self.assertEqual(self.keys_in_latest_run(), [target])
        self.assertTrue(self.latest_run()["proposals"][0]["regression"])
        packet = self.latest_packet()
        self.assertIn("## Regressions", packet)
        self.assertIn(target, packet)
        self.assertIn("regressed after being resolved", packet)


class TestOutputsAreComplete(EndToEndTestCase):
    def test_a_scan_produces_a_readable_packet(self):
        self.scan()

        packet = self.latest_packet()

        self.assertIn("# Review packet", packet)
        self.assertIn("Parser self-check", packet)
        self.assertIn("requires manual approval", packet)

    def test_run_metadata_matches_the_proposals_on_disk(self):
        self.scan()

        run = json.loads(next((self.output_root / "runs").glob("*.json")).read_text("utf-8"))
        files = list((self.output_root / "proposals").rglob("*.json"))

        self.assertEqual(len(run["proposals"]), len(files))
        self.assertEqual(
            sorted(entry["id"] for entry in run["proposals"]),
            sorted(path.stem for path in files),
        )

    def test_state_records_every_staged_proposal(self):
        self.scan()

        store = json.loads((self.output_root / "state.json").read_text(encoding="utf-8"))
        files = list((self.output_root / "proposals").rglob("*.json"))

        self.assertEqual(len(store["seen"]), len(files))
        for path in files:
            self.assertIn(path.stem, store["seen"])

    def test_recurrence_history_is_dated(self):
        """The pre-watermark trim depends on it, so a bare run id is a bug."""
        self.scan()

        store = json.loads((self.output_root / "state.json").read_text(encoding="utf-8"))
        entries = [entry for history in store["recurrence"].values() for entry in history]

        self.assertTrue(entries)
        for entry in entries:
            self.assertIn("run_id", entry)
            self.assertIsNotNone(entry["at"])


class TestRecurrenceAcrossRuns(EndToEndTestCase):
    def test_a_target_seen_again_with_new_evidence_is_marked_recurring(self):
        self.scan()

        self.add_late_failure("second-sighting")
        self.scan()

        packet = self.latest_packet()

        self.assertIn("## Recurring", packet)
        self.assertIn("also flagged in 1 previous run(s)", packet)


class TestPermanentSuppression(EndToEndTestCase):
    def test_wontfix_survives_new_evidence(self):
        self.scan()
        target = "tool:ext:demoext"

        self.scan("--resolve", target, "--decision", "wontfix")
        self.add_late_failure("still-broken")
        self.scan()

        self.assertNotIn(target, self.keys_in_latest_run())

    def test_include_resolved_shows_it_anyway(self):
        self.scan()
        target = "tool:ext:demoext"
        self.scan("--resolve", target, "--decision", "wontfix")

        stdout = self.scan("--include-seen", "--include-resolved")

        self.assertNotIn("suppressed 1 resolved", stdout)


if __name__ == "__main__":
    unittest.main()
