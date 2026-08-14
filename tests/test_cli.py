import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from pi_self_improvement import cli

FIXTURES = Path(__file__).parent / "fixtures" / "sessions"


class CliTestCase(unittest.TestCase):
    """Every run happens over a throwaway HOME, so a stray write is visible."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.sessions = self.home / ".pi" / "agent" / "sessions"
        self.sessions.parent.mkdir(parents=True)
        shutil.copytree(FIXTURES, self.sessions)
        self.output_root = self.home / ".pi-self-improvement"
        self.addCleanup(self._tmp.cleanup)

    def run_cli(self, *argv, expect=cli.EXIT_OK):
        out, err = io.StringIO(), io.StringIO()
        code = cli.main(
            ["--home", str(self.home), "--all", *argv], stdout=out, stderr=err
        )
        self.assertEqual(code, expect, msg=f"stderr: {err.getvalue()}")
        return out.getvalue(), err.getvalue()

    def snapshot(self):
        return {
            path: path.stat().st_mtime_ns
            for path in sorted(self.home.rglob("*"))
            if path.is_file()
        }

    def proposals(self, run_id=None):
        base = self.output_root / "proposals"
        return sorted(base.rglob("*.json"))


class TestScan(CliTestCase):
    def test_a_scan_writes_the_three_outputs(self):
        stdout, _ = self.run_cli()

        self.assertTrue(list((self.output_root / "runs").glob("*.json")))
        self.assertTrue(list((self.output_root / "review-packets").glob("*.md")))
        self.assertIn("packet:", stdout)

    def test_a_scan_finds_friction_in_the_fixtures(self):
        self.run_cli()

        self.assertTrue(self.proposals(), "fixtures should produce at least one proposal")

    def test_the_summary_line_reports_counts(self):
        stdout, _ = self.run_cli()

        self.assertIn("staged", stdout)
        self.assertIn("transcript(s)", stdout)


class TestOutputRootConfinement(CliTestCase):
    """AC-001 — asserted over a temporary HOME snapshot."""

    def test_a_scan_writes_only_under_the_output_root(self):
        before = self.snapshot()

        self.run_cli()

        after = self.snapshot()
        touched = {
            path
            for path in set(after) | set(before)
            if before.get(path) != after.get(path)
        }
        outside = {path for path in touched if self.output_root not in path.parents}

        self.assertEqual(outside, set(), f"wrote outside the output root: {outside}")

    def test_the_session_transcripts_are_not_modified(self):
        before = self.snapshot()

        self.run_cli()

        after = self.snapshot()
        for path, stamp in before.items():
            if self.sessions in path.parents:
                self.assertEqual(after.get(path), stamp, f"{path} was modified")


class TestManualApproval(CliTestCase):
    """AC-001's second half."""

    def test_every_proposal_is_marked_manual_approval_required(self):
        self.run_cli()

        files = self.proposals()
        self.assertTrue(files)
        for path in files:
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertTrue(payload["manual_approval_required"], path)

    def test_the_run_metadata_carries_the_same_mark(self):
        self.run_cli()

        run = json.loads(next((self.output_root / "runs").glob("*.json")).read_text("utf-8"))

        self.assertTrue(run["manual_approval_required"])

    def test_the_packet_says_so_in_words(self):
        self.run_cli()

        packet = next((self.output_root / "review-packets").glob("*.md")).read_text("utf-8")

        self.assertIn("requires manual approval", packet)


class TestEvidenceFields(CliTestCase):
    """AC-004."""

    def test_every_evidence_item_is_complete_and_bounded(self):
        self.run_cli()

        seen = 0
        for path in self.proposals():
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertTrue(payload["evidence"], f"{path} has no evidence")
            for item in payload["evidence"]:
                for field in ("source", "path", "line", "excerpt"):
                    self.assertIn(field, item)
                    self.assertIsNotNone(item[field], f"{field} is null in {path}")
                self.assertLessEqual(len(item["excerpt"]), 360)
                self.assertIsInstance(item["line"], int)
                seen += 1
        self.assertGreater(seen, 0)

    def test_evidence_points_at_a_transcript_line(self):
        self.run_cli()

        payload = json.loads(self.proposals()[0].read_text(encoding="utf-8"))
        item = payload["evidence"][0]

        self.assertTrue(item["path"].endswith(".jsonl"))
        self.assertGreater(item["line"], 0)


class TestDryRunPurity(CliTestCase):
    """AC-048 / DEC-017."""

    def test_a_dry_run_writes_nothing_at_all(self):
        before = self.snapshot()

        stdout, _ = self.run_cli("--dry-run")

        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.output_root.exists(), "dry run created the output root")
        self.assertIn("would stage", stdout)

    def test_a_dry_run_prints_the_packet_it_would_write(self):
        stdout, _ = self.run_cli("--dry-run")

        self.assertIn("# Review packet", stdout)

    def test_a_dry_run_first_does_not_change_the_real_run(self):
        """The README tells people to preview a backfill this way. If the preview
        recorded seen keys, the real backfill would suppress itself entirely."""
        self.run_cli("--dry-run")
        self.run_cli()
        with_preview = sorted(path.name for path in self.proposals())

        shutil.rmtree(self.output_root)
        self.run_cli()
        without_preview = sorted(path.name for path in self.proposals())

        self.assertEqual(with_preview, without_preview)
        self.assertTrue(with_preview)


class TestDeduplication(CliTestCase):
    def test_a_second_scan_stages_nothing_new(self):
        self.run_cli()
        first = len(self.proposals())

        stdout, _ = self.run_cli()

        self.assertGreater(first, 0)
        self.assertIn("staged 0 proposal(s)", stdout)

    def test_include_seen_stages_them_again(self):
        self.run_cli()

        stdout, _ = self.run_cli("--include-seen")

        self.assertNotIn("staged 0 proposal(s)", stdout)


class TestResolutionSubflows(CliTestCase):
    def target(self):
        payload = json.loads(self.proposals()[0].read_text(encoding="utf-8"))
        return payload["key"]

    def test_resolve_then_list_shows_the_decision(self):
        self.run_cli()
        key = self.target()

        self.run_cli("--resolve", key, "--decision", "wontfix", "--note", "by design")
        stdout, _ = self.run_cli("--list-resolutions")

        self.assertIn(key, stdout)
        self.assertIn("wontfix", stdout)
        self.assertIn("by design", stdout)

    def test_a_resolved_target_is_suppressed_on_the_next_scan(self):
        self.run_cli()
        key = self.target()
        self.run_cli("--resolve", key, "--decision", "wontfix")

        stdout, _ = self.run_cli("--include-seen")

        self.assertIn("suppressed 1 resolved", stdout)

    def test_unresolve_brings_it_back(self):
        self.run_cli()
        key = self.target()
        self.run_cli("--resolve", key, "--decision", "wontfix")

        self.run_cli("--unresolve", key)
        stdout, _ = self.run_cli("--list-resolutions")

        self.assertIn("no resolutions recorded", stdout)

    def test_unresolving_something_unresolved_is_an_error(self):
        self.run_cli("--unresolve", "tool:nothing", expect=cli.EXIT_ERROR)

    def test_resolve_without_a_decision_is_an_error(self):
        self.run_cli("--resolve", "tool:x", expect=cli.EXIT_ERROR)

    def test_resolve_from_imports_a_decisions_file(self):
        self.run_cli()
        key = self.target()
        path = self.home / "decisions.json"
        path.write_text(
            json.dumps({"decisions": [{"key": key, "decision": "fixed"}]}), encoding="utf-8"
        )

        self.run_cli("--resolve-from", str(path))
        stdout, _ = self.run_cli("--list-resolutions")

        self.assertIn(key, stdout)
        self.assertIn("fixed", stdout)

    def test_listing_an_empty_registry_says_so(self):
        stdout, _ = self.run_cli("--list-resolutions")

        self.assertIn("no resolutions recorded", stdout)


class TestParserWarning(CliTestCase):
    """AC-033's stderr third."""

    def test_transcripts_without_tool_calls_warn_on_stderr(self):
        for path in self.sessions.rglob("*.jsonl"):
            path.unlink()
        empty = self.sessions / "--tmp-empty--" / "2026-01-01T00-00-00-000Z_x.jsonl"
        empty.parent.mkdir(parents=True, exist_ok=True)
        empty.write_text(
            json.dumps({"type": "session", "id": "x", "cwd": "/tmp/x", "timestamp": "2026-01-01T00:00:00Z", "version": 3})
            + "\n",
            encoding="utf-8",
        )

        _, stderr = self.run_cli()

        self.assertIn("0 tool calls", stderr)

    def test_a_scan_with_tool_calls_does_not_warn_about_tool_calls(self):
        _, stderr = self.run_cli()

        self.assertNotIn("0 tool calls", stderr)

    def test_the_non_canonical_fixture_is_reported_on_stderr(self):
        """The fixture set deliberately contains one non-canonical transcript, so
        a truly silent stderr here would mean the counts never reached it."""
        _, stderr = self.run_cli()

        self.assertIn("non-canonical", stderr)


class TestConfigErrors(CliTestCase):
    def test_a_bad_config_exits_with_an_error(self):
        path = self.home / "config.json"
        path.write_text(json.dumps({"nonsense_key": []}), encoding="utf-8")

        out, err = io.StringIO(), io.StringIO()
        code = cli.main(
            ["--home", str(self.home), "--config", str(path)], stdout=out, stderr=err
        )

        self.assertEqual(code, cli.EXIT_ERROR)
        self.assertIn("nonsense_key", err.getvalue())

    def test_a_config_override_reaches_the_scan(self):
        path = self.home / "config.json"
        path.write_text(
            json.dumps({"extra_backlog_ignore": ["everything"]}), encoding="utf-8"
        )

        self.run_cli("--config", str(path))

        self.assertTrue(list((self.output_root / "runs").glob("*.json")))


if __name__ == "__main__":
    unittest.main()


class TestDryRunAppliesToEveryFlow(CliTestCase):
    """REQ-016 scopes dry-run to the program, not just the scan."""

    def test_a_dry_run_resolve_writes_nothing(self):
        before = self.snapshot()

        self.run_cli("--dry-run", "--resolve", "tool:x", "--decision", "fixed",
                     expect=cli.EXIT_ERROR)

        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.output_root.exists())

    def test_a_dry_run_write_queue_writes_nothing(self):
        path = self.home / "triage.json"
        path.write_text(json.dumps({"entries": []}), encoding="utf-8")
        before = self.snapshot()

        self.run_cli("--dry-run", "--write-queue", str(path), expect=cli.EXIT_ERROR)

        self.assertEqual(self.snapshot(), before)

    def test_a_dry_run_listing_is_allowed_because_it_writes_nothing(self):
        self.run_cli("--dry-run", "--list-resolutions")

    def test_the_refusal_names_the_conflicting_flag(self):
        _, stderr = self.run_cli("--dry-run", "--resolve", "tool:x", "--decision", "fixed",
                                 expect=cli.EXIT_ERROR)

        self.assertIn("--dry-run", stderr)


class TestExtraSessionRoots(CliTestCase):
    """REQ-002: config roots are scanned in addition to the default."""

    def test_a_configured_root_is_scanned(self):
        extra = self.home / "elsewhere"
        shutil.copytree(FIXTURES, extra)
        for path in self.sessions.rglob("*.jsonl"):
            path.unlink()
        config = self.home / "config.json"
        config.write_text(json.dumps({"extra_session_roots": [str(extra)]}), encoding="utf-8")

        stdout, _ = self.run_cli("--config", str(config))

        self.assertNotIn("from 0 transcript(s)", stdout)
        self.assertTrue(self.proposals())

    def test_the_default_root_is_still_scanned(self):
        extra = self.home / "empty-elsewhere"
        extra.mkdir()
        config = self.home / "config.json"
        config.write_text(json.dumps({"extra_session_roots": [str(extra)]}), encoding="utf-8")

        stdout, _ = self.run_cli("--config", str(config))

        self.assertNotIn("from 0 transcript(s)", stdout)

    def test_an_invalid_resolved_at_is_refused(self):
        _, stderr = self.run_cli("--resolve", "tool:x", "--decision", "fixed",
                                 "--resolved-at", "not-a-time", expect=cli.EXIT_ERROR)

        self.assertIn("ISO-8601", stderr)

    def test_a_missing_decisions_file_is_refused(self):
        _, stderr = self.run_cli("--resolve-from", str(self.home / "nope.json"),
                                 expect=cli.EXIT_ERROR)

        self.assertIn("not found", stderr)
