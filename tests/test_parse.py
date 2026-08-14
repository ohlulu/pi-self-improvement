import tempfile
import time
import unittest
from pathlib import Path

from pi_self_improvement import parse
from pi_self_improvement.model import KIND_BASH_EXECUTION, KIND_TOOL, ORIGIN_ROOT, ORIGIN_SUBAGENT

from tests import support

NOW = 1_800_000_000.0


class DiscoveryTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.sessions = self.tmp / "sessions"
        self.sessions.mkdir()

    def make_root(self, name: str, days_ago: float, slug: str = "--tmp-alpha--") -> Path:
        path = support.make_root_transcript(
            self.sessions, slug, name, [support.session_record(name), support.user_record("hello")]
        )
        return support.age(path, days_ago, now=NOW)

    def discover(self, **kwargs):
        return parse.discover_transcripts([self.sessions], now=NOW, **kwargs)


class TestDiscoveryWindow(DiscoveryTestCase):
    def test_since_days_excludes_out_of_window_history(self):
        """AC-002: a transcript last touched 3 days ago is out of a 1-day window."""
        self.make_root("fresh", days_ago=0.2)
        self.make_root("stale", days_ago=3)

        found = {t.path.stem for t in self.discover(since_days=1)}

        self.assertEqual(found, {"fresh"})

    def test_window_does_not_depend_on_state_presence(self):
        """AC-038: a fresh output root changes nothing — the window is the window."""
        self.make_root("fresh", days_ago=0.2)
        self.make_root("stale", days_ago=30)

        self.assertEqual({t.path.stem for t in self.discover(since_days=7)}, {"fresh"})

    def test_all_includes_out_of_window_history(self):
        """AC-038: `--all` is the only way in for history outside the window."""
        self.make_root("fresh", days_ago=0.2)
        self.make_root("stale", days_ago=30)

        found = {t.path.stem for t in self.discover(include_all=True)}

        self.assertEqual(found, {"fresh", "stale"})

    def test_multiple_roots_are_searched(self):
        extra = self.tmp / "extra"
        extra.mkdir()
        support.age(
            support.make_root_transcript(extra, "--tmp-beta--", "other", [support.session_record("other")]),
            0.1,
            now=NOW,
        )
        self.make_root("fresh", days_ago=0.1)

        found = {t.path.stem for t in parse.discover_transcripts([self.sessions, extra], now=NOW)}

        self.assertEqual(found, {"fresh", "other"})

    def test_missing_root_is_ignored_not_fatal(self):
        self.assertEqual(parse.discover_transcripts([self.tmp / "nope"], now=NOW), [])


class TestDiscoveryLimit(DiscoveryTestCase):
    def test_max_sessions_keeps_the_latest_root_sessions(self):
        """AC-003."""
        for index in range(7):
            self.make_root(f"s{index}", days_ago=index * 0.1)

        found = {t.path.stem for t in self.discover(max_sessions=3)}

        self.assertEqual(found, {"s0", "s1", "s2"})

    def test_subagent_sessions_ride_along_without_consuming_quota(self):
        """AC-039: 10 subagents under one root must not evict the other 4 roots."""
        roots = [self.make_root(f"s{index}", days_ago=index * 0.1) for index in range(5)]
        for run in range(1, 11):
            nested = support.make_subagent_transcript(
                roots[0], "9f2c1ab4d7", run, [support.session_record(f"sub{run}")]
            )
            support.age(nested, 0.05, now=NOW)

        found = self.discover(max_sessions=5)
        root_names = {t.path.stem for t in found if t.origin == ORIGIN_ROOT}
        subagents = [t for t in found if t.origin == ORIGIN_SUBAGENT]

        self.assertEqual(root_names, {"s0", "s1", "s2", "s3", "s4"})
        self.assertEqual(len(subagents), 10)

    def test_a_dropped_root_takes_its_subagents_with_it(self):
        roots = [self.make_root(f"s{index}", days_ago=index) for index in range(3)]
        support.age(
            support.make_subagent_transcript(roots[2], "abc123", 1, [support.session_record("sub")]),
            2,
            now=NOW,
        )

        found = self.discover(max_sessions=1)

        self.assertEqual([t.path.stem for t in found], ["s0"])


class TestOrigin(unittest.TestCase):
    def test_nested_run_path_is_a_subagent_session(self):
        """DEC-015: origin is decided by the nested path shape."""
        path = Path("/x/sessions/--slug--/2026-01-05T09-00-00-000Z_abc/9f2c/run-1/session.jsonl")
        self.assertTrue(parse.is_subagent_path(path))

    def test_a_plain_transcript_is_a_root_session(self):
        path = Path("/x/sessions/--slug--/2026-01-05T09-00-00-000Z_abc.jsonl")
        self.assertFalse(parse.is_subagent_path(path))

    def test_a_file_merely_named_session_is_not_a_subagent(self):
        self.assertFalse(parse.is_subagent_path(Path("/x/sessions/--slug--/session.jsonl")))


class FixtureParseTestCase(unittest.TestCase):
    """Parses the synthetic fixtures; see tests/fixtures/README.md for the map."""

    @classmethod
    def setUpClass(cls):
        cls.root = support.FIXTURE_SESSIONS
        cls.alpha = cls.root / "--tmp-pi-fixtures-alpha--"
        cls.beta = cls.root / "--tmp-pi-fixtures-beta--"
        cls.clean = cls.alpha / "2026-01-05T09-00-00-000Z_00000000-0000-7000-8000-00000000a001.jsonl"
        cls.friction = cls.alpha / "2026-01-06T10-00-00-000Z_00000000-0000-7000-8000-00000000a002.jsonl"
        cls.corrections = cls.beta / "2026-01-07T11-00-00-000Z_00000000-0000-7000-8000-00000000b001.jsonl"
        cls.edges = cls.beta / "2026-01-08T12-00-00-000Z_00000000-0000-7000-8000-00000000b002.jsonl"
        cls.subagent = cls.clean.with_suffix("") / "9f2c1ab4d7" / "run-1" / "session.jsonl"
        cls.noncanonical = (
            cls.root
            / "--tmp-pi-fixtures-gamma--"
            / "2026-01-09T13-00-00-000Z_00000000-0000-7000-8000-00000000d001.jsonl"
        )


class TestSessionFields(FixtureParseTestCase):
    def test_cwd_and_session_id_come_from_the_session_record(self):
        summary = parse.parse_transcript(self.clean)
        self.assertEqual(summary.cwd, "/tmp/pi-fixtures/alpha")
        self.assertEqual(summary.session_id, "00000000-0000-7000-8000-00000000a001")

    def test_timestamps_span_the_transcript(self):
        summary = parse.parse_transcript(self.clean)
        self.assertEqual(summary.started_at, "2026-01-05T09:00:00.000Z")
        self.assertEqual(summary.ended_at, "2026-01-05T09:00:07.000Z")

    def test_origin_is_inferred_from_the_path(self):
        self.assertEqual(parse.parse_transcript(self.clean).origin, ORIGIN_ROOT)
        self.assertEqual(parse.parse_transcript(self.subagent).origin, ORIGIN_SUBAGENT)

    def test_a_parsed_session_reports_signal(self):
        self.assertTrue(parse.parse_transcript(self.clean).has_signal())


class TestRoleMapping(FixtureParseTestCase):
    def test_user_messages_are_collected_with_line_numbers(self):
        summary = parse.parse_transcript(self.clean)
        self.assertEqual(len(summary.user_messages), 1)
        message = summary.user_messages[0]
        self.assertEqual(message.text, "Set up the demo-cli config for this repo.")
        self.assertEqual(message.line, 3)

    def test_assistant_text_turns_are_collected(self):
        summary = parse.parse_transcript(self.clean)
        texts = [turn.text for turn in summary.assistant_turns if turn.text]
        self.assertTrue(any("alpha profile" in text for text in texts))

    def test_thinking_blocks_never_become_assistant_text(self):
        summary = parse.parse_transcript(self.clean)
        self.assertFalse(any("Load the project skill" in turn.text for turn in summary.assistant_turns))

    def test_tool_calls_carry_name_arguments_and_line(self):
        summary = parse.parse_transcript(self.clean)
        read_call = next(call for call in summary.tool_calls if call.tool_name == "read")
        self.assertEqual(read_call.arguments["path"], "/tmp/pi-fixtures/skills/demo-skill/SKILL.md")
        self.assertEqual(read_call.kind, KIND_TOOL)
        self.assertEqual(read_call.line, 4)

    def test_bash_execution_becomes_a_tool_call_with_exit_code(self):
        """AC-040."""
        summary = parse.parse_transcript(self.friction)
        execution = next(call for call in summary.tool_calls if call.kind == KIND_BASH_EXECUTION)
        self.assertEqual(execution.command, "demo-cli doctor")
        self.assertEqual(execution.exit_code, 127)
        self.assertFalse(execution.cancelled)
        self.assertEqual(execution.result_text, "demo-cli: command not found")

    def test_bash_execution_is_not_a_user_message(self):
        """AC-040: it must not reach correction detection."""
        summary = parse.parse_transcript(self.friction)
        self.assertNotIn(
            "demo-cli doctor", " ".join(message.text for message in summary.user_messages)
        )

    def test_custom_message_records_never_become_user_messages(self):
        """AC-020: structural exclusion, no marker matching needed."""
        summary = parse.parse_transcript(self.corrections)
        joined = " ".join(message.text for message in summary.user_messages)
        self.assertNotIn("Listing project docs", joined)
        self.assertEqual(summary.counts.skipped_records.get("custom_message"), 1)

    def test_custom_entries_are_kept_for_opt_in_detectors(self):
        """AC-044: available, but not wired into detection by default."""
        summary = parse.parse_transcript(self.clean)
        entries = [entry for entry in summary.custom_entries if entry.custom_type == "context:skill_loaded"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].data["name"], "demo-skill")
        self.assertEqual(summary.counts.skipped_records.get("custom"), 1)


class TestPairing(FixtureParseTestCase):
    def test_results_pair_to_calls_by_tool_call_id(self):
        summary = parse.parse_transcript(self.friction)
        first = summary.tool_calls[0]
        self.assertTrue(first.matched)
        self.assertIs(first.is_error, True)
        self.assertIn("unknown flag --force", first.result_text)
        self.assertEqual(first.command, "demo-cli sync --force")

    def test_result_line_points_at_the_result_record(self):
        summary = parse.parse_transcript(self.friction)
        first = summary.tool_calls[0]
        self.assertEqual(first.line, 3)
        self.assertEqual(first.result_line, 4)
        self.assertEqual(first.evidence_line, 4)

    def test_a_dangling_call_is_counted_and_stays_unmatched(self):
        """AC-041."""
        summary = parse.parse_transcript(self.edges)
        dangling = [call for call in summary.tool_calls if not call.matched]
        self.assertEqual(len(dangling), 1)
        self.assertEqual(dangling[0].call_id, "btc-05")
        self.assertEqual(summary.counts.dangling_tool_calls, 1)

    def test_pairing_is_scoped_to_one_transcript(self):
        """Call ids repeat across transcripts; a result must never pair across files."""
        summary = parse.parse_transcript(self.clean)
        self.assertTrue(all(call.matched for call in summary.tool_calls))
        self.assertEqual(summary.counts.dangling_tool_calls, 0)

    def test_is_error_coverage_is_counted(self):
        summary = parse.parse_transcript(self.friction)
        self.assertEqual(summary.counts.tool_results, summary.counts.tool_results_with_is_error)


class TestLineOrderTraversal(FixtureParseTestCase):
    def test_both_sides_of_a_branch_are_parsed_in_file_order(self):
        """DEC-014: line order, no parentId walk — the sibling turn is kept, not pruned."""
        summary = parse.parse_transcript(self.edges)
        texts = [turn.text for turn in summary.assistant_turns if turn.text]
        self.assertIn("Re-running with the dry-run pass first.", texts)
        self.assertIn("Alternative take: starting from the dry-run output instead.", texts)
        self.assertLess(
            texts.index("Re-running with the dry-run pass first."),
            texts.index("Alternative take: starting from the dry-run output instead."),
        )

    def test_branch_points_are_counted(self):
        """AC-050: the cost of line-order parsing is measured, not hidden."""
        self.assertEqual(parse.parse_transcript(self.edges).counts.branch_points, 1)
        self.assertEqual(parse.parse_transcript(self.clean).counts.branch_points, 0)


class TestSelfCheckCounts(FixtureParseTestCase):
    def test_aborted_and_error_turns_are_counted_and_yield_nothing(self):
        """AC-041."""
        summary = parse.parse_transcript(self.edges)
        self.assertEqual(summary.counts.aborted_turns, 1)
        self.assertEqual(summary.counts.error_turns, 1)
        reasons = {turn.stop_reason for turn in summary.assistant_turns}
        self.assertNotIn("aborted", reasons)
        self.assertNotIn("error", reasons)

    def test_skipped_record_types_are_counted_not_dropped(self):
        summary = parse.parse_transcript(self.clean)
        self.assertEqual(summary.counts.skipped_records.get("model_change"), 1)
        self.assertEqual(summary.counts.skipped_records.get("session_info"), 1)

    def test_non_canonical_file_is_counted_and_produces_no_evidence(self):
        """AC-041."""
        summary = parse.parse_transcript(self.noncanonical)
        self.assertEqual(summary.counts.non_canonical_files, 1)
        self.assertFalse(summary.has_signal())
        self.assertEqual(summary.tool_calls, [])
        self.assertEqual(summary.user_messages, [])

    def test_a_canonical_file_is_not_flagged(self):
        self.assertEqual(parse.parse_transcript(self.clean).counts.non_canonical_files, 0)

    def test_unicode_line_separators_inside_a_record_do_not_break_it(self):
        """Regression: `str.splitlines()` breaks on U+2028/U+2029/\\x85, which JSON
        allows unescaped inside a string. Real transcripts contain them, and
        splitting there tears one record into fragments that look like parse errors."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sep.jsonl"
            support.write_jsonl(
                path,
                [
                    support.session_record("sep"),
                    support.user_record("first\u2028second\x85third\u2029fourth"),
                ],
            )
            summary = parse.parse_transcript(path)

        self.assertEqual(summary.counts.parse_errors, 0)
        self.assertEqual(len(summary.user_messages), 1)
        self.assertIn("fourth", summary.user_messages[0].text)

    def test_carriage_returns_are_tolerated(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "crlf.jsonl"
            path.write_text(
                '{"type":"session","id":"x","cwd":"/tmp/a","version":3}\r\n', encoding="utf-8"
            )
            self.assertEqual(parse.parse_transcript(path).counts.parse_errors, 0)

    def test_malformed_lines_count_as_parse_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken.jsonl"
            path.write_text('{"type":"session","id":"x","cwd":"/tmp/a","version":3}\nnot json\n', encoding="utf-8")
            self.assertEqual(parse.parse_transcript(path).counts.parse_errors, 1)

    def test_fixtures_parse_without_errors(self):
        """DEC-012 structural invariant, held on the synthetic corpus."""
        summaries, counts = parse.parse_transcripts(parse.discover_transcripts([support.FIXTURE_SESSIONS], include_all=True))
        self.assertEqual(counts.parse_errors, 0)
        self.assertEqual(counts.files, len(summaries))
        self.assertEqual(counts.root_sessions + counts.subagent_sessions, counts.files)
        self.assertEqual(counts.subagent_sessions, 1)


class TestParseTranscripts(unittest.TestCase):
    def test_counts_are_aggregated_across_transcripts(self):
        discovered = parse.discover_transcripts([support.FIXTURE_SESSIONS], include_all=True, now=time.time())
        summaries, counts = parse.parse_transcripts(discovered)
        self.assertEqual(len(summaries), 6)
        self.assertEqual(counts.non_canonical_files, 1)
        self.assertGreater(counts.tool_calls, 0)

    def test_origin_from_discovery_is_carried_into_the_summary(self):
        discovered = parse.discover_transcripts([support.FIXTURE_SESSIONS], include_all=True)
        summaries, _ = parse.parse_transcripts(discovered)
        origins = {summary.origin for summary in summaries}
        self.assertEqual(origins, {ORIGIN_ROOT, ORIGIN_SUBAGENT})


if __name__ == "__main__":
    unittest.main()
