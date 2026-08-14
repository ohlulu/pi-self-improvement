import unittest

from pi_self_improvement import __version__
from pi_self_improvement.model import (
    DEFAULT_EXCERPT_LIMIT,
    KIND_BASH_EXECUTION,
    ORIGIN_SUBAGENT,
    AssistantTurn,
    Evidence,
    ParseCounts,
    SessionSummary,
    ToolCall,
    UserMessage,
)


class TestEvidence(unittest.TestCase):
    def test_reference_is_path_colon_line(self):
        evidence = Evidence(source="failure", path="sessions/a.jsonl", line=12, excerpt="boom")
        self.assertEqual(evidence.reference, "sessions/a.jsonl:12")

    def test_to_dict_carries_every_required_field(self):
        evidence = Evidence(source="failure", path="a.jsonl", line=1, excerpt="boom")
        payload = evidence.to_dict()
        for key in ("source", "path", "line", "excerpt"):
            self.assertIn(key, payload)


class TestToolCall(unittest.TestCase):
    def test_evidence_line_prefers_the_result(self):
        call = ToolCall(tool_name="bash", line=4, result_line=7)
        self.assertEqual(call.evidence_line, 7)

    def test_evidence_line_falls_back_to_the_call_when_dangling(self):
        call = ToolCall(tool_name="bash", line=4)
        self.assertFalse(call.matched)
        self.assertEqual(call.evidence_line, 4)

    def test_bash_execution_kind(self):
        call = ToolCall(tool_name="bash", line=1, kind=KIND_BASH_EXECUTION, exit_code=127)
        self.assertTrue(call.is_bash_execution)


class TestParseCounts(unittest.TestCase):
    def test_merge_sums_counters_and_skipped_records(self):
        left = ParseCounts(files=1, root_sessions=1, tool_calls=3)
        left.skip("compaction")
        right = ParseCounts(files=1, subagent_sessions=1, tool_calls=2)
        right.skip("compaction", 2)
        right.skip("label")

        left.merge(right)

        self.assertEqual(left.files, 2)
        self.assertEqual(left.root_sessions, 1)
        self.assertEqual(left.subagent_sessions, 1)
        self.assertEqual(left.tool_calls, 5)
        self.assertEqual(left.skipped_records, {"compaction": 3, "label": 1})

    def test_to_dict_is_json_ready(self):
        import json

        json.dumps(ParseCounts().to_dict())


class TestSessionSummary(unittest.TestCase):
    def test_has_signal_is_false_for_an_empty_session(self):
        self.assertFalse(SessionSummary(path="a.jsonl").has_signal())

    def test_has_signal_is_true_with_a_tool_call(self):
        summary = SessionSummary(path="a.jsonl", tool_calls=[ToolCall(tool_name="bash", line=2)])
        self.assertTrue(summary.has_signal())

    def test_has_signal_is_true_with_a_user_message(self):
        summary = SessionSummary(path="a.jsonl", user_messages=[UserMessage(text="hi", line=2)])
        self.assertTrue(summary.has_signal())

    def test_has_signal_ignores_assistant_only_sessions(self):
        summary = SessionSummary(path="a.jsonl", assistant_turns=[AssistantTurn(text="hi", line=2)])
        self.assertFalse(summary.has_signal())

    def test_origin_flag(self):
        self.assertTrue(SessionSummary(path="a.jsonl", origin=ORIGIN_SUBAGENT).is_subagent)


class TestPackage(unittest.TestCase):
    def test_version_is_exposed(self):
        self.assertTrue(__version__)

    def test_excerpt_limit_default(self):
        self.assertEqual(DEFAULT_EXCERPT_LIMIT, 360)


if __name__ == "__main__":
    unittest.main()
