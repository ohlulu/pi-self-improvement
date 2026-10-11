"""Normalized event model for a parsed pi transcript.

A transcript is reduced to the few things the detectors need: tool calls with
their paired results, user messages, assistant turns, and a counts block for the
parser self-check (REQ-018). Full transcripts are never held or written — every
signal points back with a `path:line` reference plus a short excerpt (REQ-003).
"""

from __future__ import annotations

from dataclasses import dataclass, field

DEFAULT_EXCERPT_LIMIT = 360

ORIGIN_ROOT = "root"
ORIGIN_SUBAGENT = "subagent"

KIND_TOOL = "tool"
KIND_BASH_EXECUTION = "bash_execution"


@dataclass(frozen=True)
class Evidence:
    """One observed occurrence backing a proposal, pinned to where it happened.

    `path` and `excerpt` are display strings and MUST already have passed through
    the redaction boundary before an Evidence is written anywhere.
    """

    source: str
    path: str
    line: int
    excerpt: str
    timestamp: str | None = None
    session_id: str | None = None
    origin: str = ORIGIN_ROOT

    @property
    def reference(self) -> str:
        return f"{self.path}:{self.line}"

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "path": self.path,
            "line": self.line,
            "excerpt": self.excerpt,
            "timestamp": self.timestamp,
            "session_id": self.session_id,
            "origin": self.origin,
        }


@dataclass
class ToolCall:
    """An agent tool call, or a user-run `bashExecution`, plus its result.

    A `bashExecution` record carries its own command, exit code and output, so it
    is modeled as a ToolCall with `kind == KIND_BASH_EXECUTION` rather than a
    parallel type: every detector that reasons about "a command that ran" wants
    both, and only correction detection has to exclude it (AC-040).
    """

    tool_name: str
    line: int
    call_id: str | None = None
    arguments: dict = field(default_factory=dict)
    timestamp: str | None = None
    kind: str = KIND_TOOL

    # Result side. `matched` stays False for a dangling call (AC-041).
    matched: bool = False
    result_text: str = ""
    is_error: bool | None = None
    result_line: int | None = None
    result_timestamp: str | None = None

    # `bashExecution` payload; `command` also carries the reconstructed command
    # line for a bash tool call so detectors have one field to read.
    command: str | None = None
    exit_code: int | None = None
    cancelled: bool = False

    @property
    def is_bash_execution(self) -> bool:
        return self.kind == KIND_BASH_EXECUTION

    @property
    def evidence_line(self) -> int:
        """Where to point evidence: the result if there is one, else the call."""
        return self.result_line if self.result_line is not None else self.line


@dataclass
class UserMessage:
    """Text a user typed. Scaffold records never become a UserMessage (REQ-011)."""

    text: str
    line: int
    timestamp: str | None = None


@dataclass
class AssistantTurn:
    text: str
    line: int
    timestamp: str | None = None
    stop_reason: str | None = None


@dataclass
class ParseCounts:
    """Parser self-check counters (REQ-018). Aggregated across a whole scan."""

    files: int = 0
    root_sessions: int = 0
    subagent_sessions: int = 0
    parse_errors: int = 0
    non_canonical_files: int = 0
    branch_points: int = 0
    aborted_turns: int = 0
    error_turns: int = 0
    dangling_tool_calls: int = 0
    tool_calls: int = 0
    tool_results: int = 0
    tool_results_with_is_error: int = 0
    skipped_records: dict[str, int] = field(default_factory=dict)

    def skip(self, record_type: str, n: int = 1) -> None:
        self.skipped_records[record_type] = self.skipped_records.get(record_type, 0) + n

    def merge(self, other: ParseCounts) -> None:
        self.files += other.files
        self.root_sessions += other.root_sessions
        self.subagent_sessions += other.subagent_sessions
        self.parse_errors += other.parse_errors
        self.non_canonical_files += other.non_canonical_files
        self.branch_points += other.branch_points
        self.aborted_turns += other.aborted_turns
        self.error_turns += other.error_turns
        self.dangling_tool_calls += other.dangling_tool_calls
        self.tool_calls += other.tool_calls
        self.tool_results += other.tool_results
        self.tool_results_with_is_error += other.tool_results_with_is_error
        for key, value in other.skipped_records.items():
            self.skip(key, value)

    def to_dict(self) -> dict:
        return {
            "files": self.files,
            "root_sessions": self.root_sessions,
            "subagent_sessions": self.subagent_sessions,
            "parse_errors": self.parse_errors,
            "non_canonical_files": self.non_canonical_files,
            "branch_points": self.branch_points,
            "aborted_turns": self.aborted_turns,
            "error_turns": self.error_turns,
            "dangling_tool_calls": self.dangling_tool_calls,
            "tool_calls": self.tool_calls,
            "tool_results": self.tool_results,
            "tool_results_with_is_error": self.tool_results_with_is_error,
            "skipped_records": dict(sorted(self.skipped_records.items())),
        }


@dataclass
class SessionSummary:
    """One transcript reduced to its minable parts."""

    path: str
    session_id: str = ""
    origin: str = ORIGIN_ROOT
    cwd: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    user_messages: list[UserMessage] = field(default_factory=list)
    assistant_turns: list[AssistantTurn] = field(default_factory=list)
    counts: ParseCounts = field(default_factory=ParseCounts)

    @property
    def is_subagent(self) -> bool:
        return self.origin == ORIGIN_SUBAGENT

    def has_signal(self) -> bool:
        """True when this session holds anything a detector could act on.

        A session with neither a tool call nor a user message carries no friction
        signal; skipping it early keeps empty and scaffold-only transcripts out of
        the pipeline without them counting as a parse problem.
        """
        return bool(self.tool_calls or self.user_messages)
