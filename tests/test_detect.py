import tempfile
import unittest
from pathlib import Path

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


def turn(text: str, line: int = 99):
    from pi_self_improvement.model import AssistantTurn

    return AssistantTurn(text=text, line=line)


class DetectTestCase(unittest.TestCase):
    config = None

    def setUp(self):
        self.redactor = Redactor()

    def detect(self, summary: SessionSummary, config=None):
        return detect.detect_session(summary, redactor=self.redactor, config=config or self.config)

    def only(self, summary: SessionSummary, kind: str, config=None):
        return [signal for signal in self.detect(summary, config) if signal.kind == kind]


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


class TestStatusAsAnswer(DetectTestCase):
    """A non-zero status from a search program is its answer, not a failure.

    Found by routing the real corpus: `grep` produced 388 backlog entries across
    229 sessions, essentially all "exit 1, no output". AC-014 already excludes an
    empty search result from silent-empty; the same reasoning applies when the
    shell reports the emptiness as a non-zero exit.
    """

    def test_grep_finding_nothing_is_not_a_failure(self):
        summary = session(
            call(
                command="grep -r needle src",
                result="(no output)\nCommand exited with code 1",
                is_error=True,
                exit_code=1,
            )
        )

        self.assertEqual(self.only(summary, detect.FAILURE), [])

    def test_the_same_shape_from_a_normal_program_is_still_a_failure(self):
        summary = session(
            call(
                command="make build",
                result="(no output)\nCommand exited with code 1",
                is_error=True,
                exit_code=1,
            )
        )

        self.assertEqual(len(self.only(summary, detect.FAILURE)), 1)

    def test_grep_with_real_error_output_is_still_a_failure(self):
        summary = session(
            call(
                command="grep -r needle /nope",
                result="grep: /nope: No such file or directory\nCommand exited with code 1",
                is_error=True,
                exit_code=1,
            )
        )

        self.assertEqual(len(self.only(summary, detect.FAILURE)), 1)

    def test_a_worse_exit_code_from_a_search_tool_is_a_failure(self):
        summary = session(
            call(command="grep -r needle src", result="(no output)", is_error=True, exit_code=2)
        )

        self.assertEqual(len(self.only(summary, detect.FAILURE)), 1)

    def test_the_exit_code_is_read_from_the_result_text(self):
        """Pi only sometimes repeats the status in `details`."""
        from pi_self_improvement import parse as parse_module

        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.jsonl"
            support.write_jsonl(
                path,
                [
                    support.session_record("x"),
                    support.tool_call_record("c1", "bash", {"command": "grep needle src"}),
                    support.tool_result_record(
                        "c1", "bash", "(no output)\nCommand exited with code 1", is_error=True
                    ),
                ],
            )
            summary = parse_module.parse_transcript(path)

        self.assertEqual(summary.tool_calls[0].exit_code, 1)


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


class TestSubagentExclusion(DetectTestCase):
    """REQ-012: a subagent session is not the user, and its failures are not the
    user's backlog."""

    @classmethod
    def setUpClass(cls):
        cls.path = (
            support.FIXTURE_SESSIONS
            / "--tmp-pi-fixtures-alpha--"
            / "2026-01-05T09-00-00-000Z_00000000-0000-7000-8000-00000000a001"
            / "9f2c1ab4d7"
            / "run-1"
            / "session.jsonl"
        )

    def test_the_fixture_is_recognised_as_a_subagent_session(self):
        self.assertTrue(parse.parse_transcript(self.path).is_subagent)

    def test_a_subagent_failure_is_detected_but_not_backlog_eligible(self):
        """AC-021: still counted, just not the user's problem to file."""
        summary = parse.parse_transcript(self.path)

        failures = self.only(summary, detect.FAILURE)

        self.assertEqual(len(failures), 1)
        self.assertFalse(failures[0].detail["backlog_eligible"])
        self.assertEqual(failures[0].evidence.origin, "subagent")

    def test_config_can_opt_the_failures_in(self):
        summary = parse.parse_transcript(self.path)
        config = detect.DetectConfig(include_subagent_failures=True)

        self.assertTrue(self.only(summary, detect.FAILURE, config)[0].detail["backlog_eligible"])

    def test_a_root_session_failure_is_always_eligible(self):
        summary = session(call(command="demo-cli sync", result="boom", is_error=True))

        self.assertTrue(self.only(summary, detect.FAILURE)[0].detail["backlog_eligible"])

    def test_an_injected_prompt_is_never_a_correction(self):
        """The subagent seed contains a strong English cue verbatim.

        What sits in the `user` role of a subagent session was written by the
        orchestrating agent, so counting it would have the loop mine its own
        instructions.
        """
        summary = parse.parse_transcript(self.path)
        seed = summary.user_messages[0].text

        self.assertIn("That's wrong", seed)
        self.assertEqual(self.only(summary, detect.CORRECTION), [])


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


class TestSilentEmpty(DetectTestCase):
    """REQ-008: a data-intending call that came back empty and nobody noticed."""

    def test_an_unacknowledged_empty_json_list_is_silent_empty(self):
        """AC-013."""
        summary = session(
            call(command="demo-cli list --json", result="[]", is_error=False),
            assistant_turns=[turn("Sync finished and the build is green, so the workspace is ready.")],
        )

        signals = self.only(summary, detect.SILENT_EMPTY)

        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].detail["command"], "demo-cli list --json")

    def test_an_acknowledged_empty_result_is_not_friction(self):
        summary = session(
            call(command="demo-cli list --json", result="[]", is_error=False),
            assistant_turns=[turn("The list came back empty, so there is nothing to sync.")],
        )

        self.assertEqual(self.only(summary, detect.SILENT_EMPTY), [])

    def test_acknowledgement_in_traditional_chinese_counts(self):
        summary = session(
            call(command="demo-cli list --json", result="[]", is_error=False),
            assistant_turns=[turn("查詢結果是空的，沒有需要同步的項目。")],
        )

        self.assertEqual(self.only(summary, detect.SILENT_EMPTY), [])

    def test_only_later_turns_can_acknowledge(self):
        """A turn before the call cannot have noticed its result."""
        summary = session(
            call(command="demo-cli list --json", result="[]", is_error=False, line=40),
            assistant_turns=[turn("Nothing found so far.", line=10)],
        )

        self.assertEqual(len(self.only(summary, detect.SILENT_EMPTY)), 1)

    def test_search_tools_are_ignored(self):
        """AC-014: an empty search result is an answer, not friction."""
        for command in ("rg --no-heading needle src", "grep -r needle src", "find . -name nope"):
            with self.subTest(command=command):
                summary = session(call(command=command, result="", is_error=False))
                self.assertEqual(self.only(summary, detect.SILENT_EMPTY), [])

    def test_the_builtin_search_tools_are_ignored(self):
        for tool in ("grep", "find"):
            with self.subTest(tool=tool):
                summary = session(call(tool, arguments={"pattern": "needle"}, result="[]", is_error=False))
                self.assertEqual(self.only(summary, detect.SILENT_EMPTY), [])

    def test_a_call_with_no_data_intent_is_ignored(self):
        summary = session(call(command="demo-cli sync --all", result="", is_error=False))

        self.assertEqual(self.only(summary, detect.SILENT_EMPTY), [])

    def test_every_empty_payload_shape_counts(self):
        for payload in ("[]", "{}", "null", "(empty)", "0 rows", "No results found", "   "):
            with self.subTest(payload=payload):
                summary = session(call(command="demo-cli list --json", result=payload, is_error=False))
                self.assertEqual(len(self.only(summary, detect.SILENT_EMPTY)), 1)

    def test_a_non_empty_payload_is_not_flagged(self):
        summary = session(call(command="demo-cli list --json", result='[{"id": 1}]', is_error=False))

        self.assertEqual(self.only(summary, detect.SILENT_EMPTY), [])

    def test_a_failing_empty_call_is_a_failure_not_a_silent_empty(self):
        summary = session(call(command="demo-cli list --json", result="", is_error=True))

        self.assertEqual(kinds(self.detect(summary)), [detect.FAILURE])

    def test_detection_can_be_switched_off(self):
        summary = session(call(command="demo-cli list --json", result="[]", is_error=False))
        config = detect.DetectConfig(detect_silent_empty=False)

        self.assertEqual(self.only(summary, detect.SILENT_EMPTY, config), [])

    def test_an_extension_tool_fetch_counts(self):
        summary = session(call("demoext_search_items", arguments={"query": "beta"}, result="[]", is_error=False))

        self.assertEqual(len(self.only(summary, detect.SILENT_EMPTY)), 1)


class TestSkillInvocation(DetectTestCase):
    """REQ-009 / DEC-006: the read-path heuristic is the specification default."""

    def test_a_skill_md_read_names_the_skill_after_its_directory(self):
        """AC-015, using the path the requirement names literally."""
        summary = session(
            call("read", arguments={"path": "~/.pi/agent/skills/commit/SKILL.md"}, result="---", is_error=False)
        )

        signals = self.only(summary, detect.SKILL)

        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].subject, "commit")

    def test_an_absolute_path_works_the_same(self):
        summary = session(
            call("read", arguments={"path": "/tmp/pi-fixtures/skills/demo-skill/SKILL.md"}, is_error=False)
        )

        self.assertEqual(self.only(summary, detect.SKILL)[0].subject, "demo-skill")

    def test_a_failed_read_loaded_nothing(self):
        summary = session(
            call(
                "read",
                arguments={"path": "/tmp/skills/gone/SKILL.md"},
                result="No such file or directory",
                is_error=True,
            )
        )

        self.assertEqual(self.only(summary, detect.SKILL), [])

    def test_an_unrelated_read_is_not_a_skill(self):
        summary = session(call("read", arguments={"path": "/tmp/notes/README.md"}, is_error=False))

        self.assertEqual(self.only(summary, detect.SKILL), [])

    def test_a_file_merely_containing_the_word_skill_is_not_one(self):
        summary = session(call("read", arguments={"path": "/tmp/docs/SKILL.md.bak"}, is_error=False))

        self.assertEqual(self.only(summary, detect.SKILL), [])

    def test_custom_entries_are_ignored_by_default(self):
        """AC-044: opt-in, because the entry comes from a personal extension."""
        summary = parse.parse_transcript(
            support.FIXTURE_SESSIONS
            / "--tmp-pi-fixtures-alpha--"
            / "2026-01-05T09-00-00-000Z_00000000-0000-7000-8000-00000000a001.jsonl"
        )

        names = [signal.subject for signal in self.only(summary, detect.SKILL)]

        self.assertEqual(names, ["demo-skill"], "the custom entry must not double-count")

    def test_custom_entries_count_once_configured(self):
        """AC-044, second half."""
        summary = parse.parse_transcript(
            support.FIXTURE_SESSIONS
            / "--tmp-pi-fixtures-alpha--"
            / "2026-01-05T09-00-00-000Z_00000000-0000-7000-8000-00000000a001.jsonl"
        )
        config = detect.DetectConfig(skill_loaded_custom_types=("context:skill_loaded",))

        names = [signal.subject for signal in self.only(summary, detect.SKILL, config)]

        self.assertEqual(names, ["demo-skill", "demo-skill"])

    def test_the_evidence_points_at_the_read(self):
        summary = session(
            call("read", arguments={"path": "/tmp/skills/demo-skill/SKILL.md"}, line=12, is_error=False)
        )

        evidence = self.only(summary, detect.SKILL)[0].evidence

        self.assertEqual(evidence.line, 12)
        self.assertEqual(evidence.source, detect.SKILL)


class TestExecutableAttribution(DetectTestCase):
    """REQ-007: which CLI a piece of tool-route evidence is about."""

    def test_the_executable_becomes_the_subject(self):
        """AC-011."""
        summary = session(call(command="demo-cli sync --force", result="boom", is_error=True))
        config = detect.DetectConfig(tracked_clis=("demo-cli",))

        signal = self.only(summary, detect.FAILURE, config)[0]

        self.assertEqual(signal.subject, "demo-cli")
        self.assertTrue(signal.detail["tracked"])

    def test_a_bash_execution_is_attributed_the_same_way(self):
        """AC-043."""
        summary = session(
            call(
                command="demo-cli doctor",
                result="demo-cli: command not found",
                kind=KIND_BASH_EXECUTION,
                is_error=True,
                exit_code=127,
            )
        )
        config = detect.DetectConfig(tracked_clis=("demo-cli",))

        signal = self.only(summary, detect.FAILURE, config)[0]

        self.assertEqual(signal.subject, "demo-cli")
        self.assertTrue(signal.detail["tracked"])
        self.assertEqual(signal.detail["exit_code"], 127)

    def test_a_cancelled_execution_is_tool_route_evidence(self):
        summary = session(
            call(
                command="demo-cli wait",
                result="",
                kind=KIND_BASH_EXECUTION,
                is_error=True,
            )
        )
        summary.tool_calls[0].cancelled = True

        signal = self.only(summary, detect.FAILURE)[0]

        self.assertTrue(signal.detail["cancelled"])

    def test_the_suffix_pattern_tracks_without_an_explicit_entry(self):
        summary = session(call(command="other-cli push", result="boom", is_error=True))
        config = detect.DetectConfig(tracked_clis=(), tracked_cli_suffix=("-cli",))

        self.assertTrue(self.only(summary, detect.FAILURE, config)[0].detail["tracked"])

    def test_an_untracked_executable_is_still_attributed_but_not_tracked(self):
        summary = session(call(command="someprog build", result="boom", is_error=True))
        config = detect.DetectConfig(tracked_clis=("demo-cli",), tracked_cli_suffix=())

        signal = self.only(summary, detect.FAILURE, config)[0]

        self.assertEqual(signal.subject, "someprog")
        self.assertFalse(signal.detail["tracked"])

    def test_a_non_bash_tool_keeps_its_tool_name(self):
        summary = session(call("demoext_get_item", arguments={"id": "1"}, result="boom", is_error=True))

        self.assertEqual(self.only(summary, detect.FAILURE)[0].subject, "demoext_get_item")


class TestExecutableExtraction(unittest.TestCase):
    def test_shapes(self):
        cases = {
            "demo-cli sync --force": "demo-cli",
            "/usr/local/bin/demo-cli sync": "demo-cli",
            "timeout 120 demo-cli health": "demo-cli",
            "sudo demo-cli restart": "demo-cli",
            "DEMO_ENV=1 demo-cli sync": "demo-cli",
            "env DEMO_ENV=1 demo-cli sync": "demo-cli",
            "cd /tmp/workspace && demo-cli sync": "demo-cli",
            "demo-cli": "demo-cli",
            "": None,
            "   ": None,
        }
        for command, expected in cases.items():
            with self.subTest(command=command):
                self.assertEqual(detect.executable_of(command), expected)

    def test_shell_noise_alone_yields_the_noise_itself(self):
        self.assertEqual(detect.executable_of("cd /tmp"), "cd")

    def test_things_that_are_not_programs_are_rejected(self):
        """Found on the real corpus: `#` and `[` were being attributed evidence."""
        for command in ('# Check the thing', '[ "$WORKSPACE" != "" ]', '"quoted thing"'):
            with self.subTest(command=command):
                self.assertIsNone(detect.executable_of(command))

    def test_an_argument_is_not_a_subcommand(self):
        """`grep` and `echo` have arguments here, not subcommands."""
        for command in ('grep "enum Foo" src', 'echo "=== done ==="', "cat > /tmp/out"):
            with self.subTest(command=command):
                self.assertIsNone(detect._analyze(command)[1])

    def test_a_path_is_not_a_subcommand(self):
        self.assertIsNone(detect._analyze("git /tmp/workspace/repo status")[1])


class TestRetryShapes(DetectTestCase):
    """AC-012: the same subcommand tried three ways, one of which failed."""

    def three_attempts(self, last_is_error=True):
        return session(
            call(command="demo-cli sync --force", result="boom", is_error=True, line=3),
            call(command="demo-cli sync --all", result="boom", is_error=True, line=5),
            call(command="demo-cli sync --retry 3 --verbose", result="ok", is_error=False, line=7),
        )

    def test_three_flag_combinations_with_a_failure_are_a_retry(self):
        signals = self.only(self.three_attempts(), detect.RETRY)

        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].subject, "demo-cli")
        self.assertEqual(signals[0].detail["subcommand"], "sync")
        self.assertEqual(signals[0].detail["attempts"], 3)

    def test_two_attempts_are_not_a_retry(self):
        summary = session(
            call(command="demo-cli sync --force", result="boom", is_error=True, line=3),
            call(command="demo-cli sync --all", result="ok", is_error=False, line=5),
        )

        self.assertEqual(self.only(summary, detect.RETRY), [])

    def test_three_clean_attempts_are_not_a_retry(self):
        summary = session(
            call(command="demo-cli sync --force", result="ok", is_error=False, line=3),
            call(command="demo-cli sync --all", result="ok", is_error=False, line=5),
            call(command="demo-cli sync --verbose", result="ok", is_error=False, line=7),
        )

        self.assertEqual(self.only(summary, detect.RETRY), [])

    def test_the_same_flags_repeated_are_not_three_combinations(self):
        summary = session(
            call(command="demo-cli sync --force", result="boom", is_error=True, line=3),
            call(command="demo-cli sync --force", result="boom", is_error=True, line=5),
            call(command="demo-cli sync --force", result="boom", is_error=True, line=7),
        )

        self.assertEqual(self.only(summary, detect.RETRY), [])

    def test_only_tracked_clis_produce_retry_signals(self):
        """REQ-007 scopes this to tracked CLIs.

        Without the scope, ordinary exploration reads as retry: on the real corpus
        it reported `git diff` and `echo` as retry-before-success.
        """
        summary = session(
            call(command="someprog sync --force", result="boom", is_error=True, line=3),
            call(command="someprog sync --all", result="boom", is_error=True, line=5),
            call(command="someprog sync --verbose", result="ok", is_error=False, line=7),
        )
        config = detect.DetectConfig(tracked_clis=(), tracked_cli_suffix=())

        self.assertEqual(self.only(summary, detect.RETRY, config), [])

        tracked = detect.DetectConfig(tracked_clis=("someprog",), tracked_cli_suffix=())
        self.assertEqual(len(self.only(summary, detect.RETRY, tracked)), 1)

    def test_different_subcommands_do_not_group(self):
        summary = session(
            call(command="demo-cli sync --force", result="boom", is_error=True, line=3),
            call(command="demo-cli push --all", result="boom", is_error=True, line=5),
            call(command="demo-cli pull --verbose", result="boom", is_error=True, line=7),
        )

        self.assertEqual(self.only(summary, detect.RETRY), [])

    def test_the_retry_evidence_points_at_a_real_line(self):
        signal = self.only(self.three_attempts(), detect.RETRY)[0]

        self.assertIn(signal.evidence.line, {4, 6, 8})
        self.assertEqual(signal.evidence.source, detect.RETRY)

    def test_the_fixture_session_shows_its_retry(self):
        alpha = support.FIXTURE_SESSIONS / "--tmp-pi-fixtures-alpha--"
        summary = parse.parse_transcript(
            alpha / "2026-01-06T10-00-00-000Z_00000000-0000-7000-8000-00000000a002.jsonl"
        )

        retries = self.only(summary, detect.RETRY)

        self.assertEqual(len(retries), 1)
        self.assertEqual(retries[0].detail["subcommand"], "sync")
        self.assertEqual(retries[0].detail["attempts"], 3)


if __name__ == "__main__":
    unittest.main()


class TestAttributionGuards(unittest.TestCase):
    """Review findings: a pipeline head and a wrapper's option value were being
    mistaken for the program, which discards tracked-CLI failures."""

    def test_a_pipeline_head_producer_is_not_the_program(self):
        self.assertEqual(detect.executable_of("printf x | demo-cli list"), "demo-cli")

    def test_a_wrapper_option_value_is_not_the_program(self):
        self.assertEqual(detect.executable_of("env -u GH_TOKEN demo-cli list"), "demo-cli")

    def test_sudo_option_values_are_skipped_too(self):
        self.assertEqual(detect.executable_of("sudo -u root demo-cli run"), "demo-cli")

    def test_a_downstream_filter_is_still_attributed(self):
        """`cat f | grep x` is about grep; there is no later segment to prefer."""
        self.assertEqual(detect.executable_of("cat f | grep needle"), "grep")

    def test_a_producer_alone_is_still_itself(self):
        self.assertEqual(detect.executable_of("echo hello"), "echo")

    def test_an_ordinary_command_is_unaffected(self):
        self.assertEqual(detect.executable_of("demo-cli list --json"), "demo-cli")


class TestDanglingCallsProduceNoEvidence(DetectTestCase):
    """AC-041: a toolCall with no result is counted, never turned into evidence."""

    def test_a_dangling_skill_read_is_not_a_skill_invocation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.jsonl"
            support.write_jsonl(
                path,
                [
                    support.session_record("x"),
                    support.tool_call_record("c1", "read", {"path": "/tmp/skills/demo/SKILL.md"}),
                ],
            )
            summary = parse.parse_transcript(path)

        signals = detect.detect_session(summary, redactor=Redactor())

        self.assertEqual(summary.counts.dangling_tool_calls, 1)
        self.assertEqual([s for s in signals if s.kind == detect.SKILL], [])

    def test_a_completed_skill_read_still_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.jsonl"
            support.write_jsonl(
                path,
                [
                    support.session_record("x"),
                    support.tool_call_record("c1", "read", {"path": "/tmp/skills/demo/SKILL.md"}),
                    support.tool_result_record("c1", "read", "# Demo skill"),
                ],
            )
            summary = parse.parse_transcript(path)

        signals = detect.detect_session(summary, redactor=Redactor())

        self.assertEqual([s.subject for s in signals if s.kind == detect.SKILL], ["demo"])
