import unittest

from pi_self_improvement import detect, parse
from pi_self_improvement.model import KIND_BASH_EXECUTION, SessionSummary, ToolCall
from pi_self_improvement.redact import Redactor

from tests import support


def session(*calls: ToolCall, path: str = "sessions/--tmp-alpha--/s1.jsonl", **kwargs) -> SessionSummary:
    return SessionSummary(path=path, session_id="s1", tool_calls=list(calls), **kwargs)


def call(
    tool_name: str = "bash",
    *,
    command: str | None = None,
    result: str = "",
    is_error=None,
    line: int = 3,
    kind: str = "tool",
    exit_code: int | None = None,
    matched: bool = True,
    arguments: dict | None = None,
) -> ToolCall:
    return ToolCall(
        tool_name=tool_name,
        line=line,
        call_id="tc-1",
        arguments=arguments if arguments is not None else ({"command": command} if command else {}),
        kind=kind,
        matched=matched,
        result_text=result,
        is_error=is_error,
        result_line=line + 1 if matched else None,
        command=command,
        exit_code=exit_code,
    )


def kinds(signals) -> list[str]:
    return [signal.kind for signal in signals]


class DetectTestCase(unittest.TestCase):
    def setUp(self):
        self.redactor = Redactor()

    def detect(self, summary: SessionSummary):
        return detect.detect_session(summary, redactor=self.redactor)

    def only(self, summary: SessionSummary, kind: str):
        return [signal for signal in self.detect(summary) if signal.kind == kind]


class TestFailureDetection(DetectTestCase):
    def test_is_error_true_produces_failure_evidence_with_the_command(self):
        """AC-007."""
        summary = session(
            call(command="demo-cli sync --force", result="demo-cli: error: unknown flag", is_error=True)
        )

        failures = self.only(summary, detect.FAILURE)

        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].detail["command"], "demo-cli sync --force")
        self.assertIn("unknown flag", failures[0].evidence.excerpt)

    def test_is_error_false_never_produces_failure_however_the_text_reads(self):
        """AC-008: the flag wins over the words. A build log is not a failure."""
        summary = session(
            call(
                command="demo-build --release",
                result="note: 2 error-handling warnings suppressed\nerror recovery pass finished\nBuild succeeded",
                is_error=False,
            )
        )

        self.assertEqual(self.only(summary, detect.FAILURE), [])

    def test_heuristics_run_only_when_the_flag_is_absent(self):
        """DEC-005: the text fallback exists for format drift, not for daily use."""
        summary = session(call(command="demo-cli build", result="demo-cli: command not found", is_error=None))

        self.assertEqual(len(self.only(summary, detect.FAILURE)), 1)

    def test_absent_flag_with_clean_output_is_not_a_failure(self):
        summary = session(call(command="demo-cli build", result="built 4 targets", is_error=None))

        self.assertEqual(self.only(summary, detect.FAILURE), [])

    def test_a_dangling_call_produces_nothing(self):
        """AC-041: no result means no evidence."""
        summary = session(call(command="demo-cli sync", matched=False, is_error=None))

        self.assertEqual(self.detect(summary), [])

    def test_evidence_carries_every_required_field(self):
        """AC-004."""
        summary = session(call(command="demo-cli sync", result="boom", is_error=True, line=7))

        evidence = self.only(summary, detect.FAILURE)[0].evidence

        self.assertEqual(evidence.source, detect.FAILURE)
        self.assertEqual(evidence.path, "sessions/--tmp-alpha--/s1.jsonl")
        self.assertEqual(evidence.line, 8)
        self.assertEqual(evidence.excerpt, "boom")
        self.assertEqual(evidence.session_id, "s1")

    def test_secrets_in_output_never_reach_the_evidence(self):
        """REQ-004: detection is downstream of the redaction boundary."""
        secret = "ghp_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
        summary = session(call(command=f"demo-cli auth {secret}", result=f"rejected token {secret}", is_error=True))

        signal = self.only(summary, detect.FAILURE)[0]

        self.assertNotIn(secret, signal.evidence.excerpt)
        self.assertNotIn(secret, signal.detail["command"])

    def test_excerpts_respect_the_limit(self):
        summary = session(call(command="demo-cli sync", result="x" * 5000, is_error=True))

        self.assertLessEqual(len(self.only(summary, detect.FAILURE)[0].evidence.excerpt), 360)


class TestBashExecutionFailures(DetectTestCase):
    def test_a_non_zero_exit_is_a_failure(self):
        """AC-040/AC-043: the exit code is this record type's error flag."""
        summary = session(
            call(
                command="demo-cli doctor",
                result="demo-cli: command not found",
                kind=KIND_BASH_EXECUTION,
                is_error=True,
                exit_code=127,
            )
        )

        failures = self.only(summary, detect.FAILURE)

        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].detail["exit_code"], 127)

    def test_a_zero_exit_is_not_a_failure(self):
        summary = session(
            call(
                command="demo-cli doctor",
                result="all good",
                kind=KIND_BASH_EXECUTION,
                is_error=False,
                exit_code=0,
            )
        )

        self.assertEqual(self.only(summary, detect.FAILURE), [])


class TestHangDetection(DetectTestCase):
    def test_timeout_text_in_the_result_is_a_hang(self):
        """AC-009."""
        summary = session(
            call(command="demo-cli wait-ready", result="Command timed out after 30 seconds", is_error=True)
        )

        hangs = self.only(summary, detect.HANG)

        self.assertEqual(len(hangs), 1)
        self.assertIn("timed out", hangs[0].evidence.excerpt)

    def test_the_command_alone_never_triggers_a_hang(self):
        """AC-010: `timeout 120 foo` that succeeds is not a stall."""
        summary = session(call(command="timeout 120 demo-cli health", result="health: ok", is_error=False))

        self.assertEqual(self.only(summary, detect.HANG), [])

    def test_a_command_mentioning_timeout_that_does_stall_is_still_a_hang(self):
        summary = session(
            call(command="timeout 120 demo-cli health", result="Killed after deadline exceeded", is_error=True)
        )

        self.assertEqual(len(self.only(summary, detect.HANG)), 1)

    def test_a_hang_is_not_also_counted_as_a_failure(self):
        """One occurrence is one piece of friction, not two."""
        summary = session(
            call(command="demo-cli wait-ready", result="Command timed out after 30 seconds", is_error=True)
        )

        self.assertEqual(kinds(self.detect(summary)), [detect.HANG])

    def test_a_successful_result_that_merely_mentions_a_timeout_is_not_a_hang(self):
        """REQ-006's "without clean completion" clause.

        Reading a document that discusses timeouts is not a stall. On the real
        corpus this was most of the hang hits before the clause was honoured.
        """
        summary = session(
            call(
                "read",
                arguments={"path": "/tmp/pi-fixtures/docs/errors.md"},
                result="# Error handling\n\nRetry when the request timed out or SIGTERM arrives.",
                is_error=False,
            )
        )

        self.assertEqual(self.only(summary, detect.HANG), [])

    def test_the_excerpt_shows_the_stall_notice_not_the_head_of_the_output(self):
        """A harness appends the termination notice last, so the head of a long
        output is the least useful part of it to show a reviewer."""
        noise = "drwxr-xr-x  build/artifacts/module\n" * 200
        summary = session(
            call(
                command="demo-cli build --all",
                result=noise + "Command timed out after 300 seconds",
                is_error=True,
            )
        )

        evidence = self.only(summary, detect.HANG)[0].evidence

        self.assertIn("timed out after 300 seconds", evidence.excerpt)
        self.assertLessEqual(len(evidence.excerpt), 360)

    def test_the_word_error_alone_is_not_a_hang(self):
        summary = session(call(command="demo-cli sync", result="demo-cli: error: bad flag", is_error=True))

        self.assertEqual(self.only(summary, detect.HANG), [])


class TestAgainstFixtures(DetectTestCase):
    """The synthetic corpus, end to end through parse + detect."""

    @classmethod
    def setUpClass(cls):
        alpha = support.FIXTURE_SESSIONS / "--tmp-pi-fixtures-alpha--"
        cls.friction_path = alpha / "2026-01-06T10-00-00-000Z_00000000-0000-7000-8000-00000000a002.jsonl"
        cls.clean_path = alpha / "2026-01-05T09-00-00-000Z_00000000-0000-7000-8000-00000000a001.jsonl"

    def test_the_friction_transcript_yields_the_expected_failures(self):
        summary = parse.parse_transcript(self.friction_path)

        failures = self.only(summary, detect.FAILURE)
        commands = sorted(signal.detail["command"] for signal in failures)

        self.assertEqual(
            commands, ["demo-cli doctor", "demo-cli sync --all", "demo-cli sync --force"]
        )

    def test_the_friction_transcript_yields_one_hang(self):
        summary = parse.parse_transcript(self.friction_path)

        hangs = self.only(summary, detect.HANG)

        self.assertEqual(len(hangs), 1)
        self.assertEqual(hangs[0].detail["command"], "demo-cli wait-ready")

    def test_the_successful_build_log_is_not_a_failure(self):
        """AC-008 on the fixture that contains the word error three times."""
        summary = parse.parse_transcript(self.friction_path)

        commands = {signal.detail.get("command") for signal in self.detect(summary)}

        self.assertNotIn("demo-build --release", commands)

    def test_a_clean_transcript_yields_no_failure_or_hang(self):
        summary = parse.parse_transcript(self.clean_path)

        signals = [s for s in self.detect(summary) if s.kind in (detect.FAILURE, detect.HANG)]

        self.assertEqual(signals, [])


if __name__ == "__main__":
    unittest.main()
