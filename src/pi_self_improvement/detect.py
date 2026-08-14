"""Friction detectors: turn a parsed session into signals.

A `Signal` is one detected occurrence — what kind of friction, what it is about,
the evidence backing it, and whatever extra a router needs. Detection does not
decide whether a signal is worth staging; that is routing's job (REQ-013), and
keeping the two apart is what lets a subagent failure be detected, counted, and
still kept out of the backlog (REQ-012).

Every string that leaves this module has passed through the redaction boundary.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .model import KIND_BASH_EXECUTION, Evidence, SessionSummary, ToolCall
from .redact import Redactor

FAILURE = "failure"
HANG = "hang"

#: Stall shapes, matched against tool *output* only. Matching the command would
#: flag `timeout 120 foo` as a hang every time it succeeded (AC-010).
_HANG_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\btimed out\b",
        r"\btimeout (?:after|exceeded|reached)\b",
        r"\bexecution timed? ?out\b",
        r"\boperation timed out\b",
        r"\b(?:context )?deadline exceeded\b",
        r"\betimedout\b",
        r"\bkilled (?:after|due to|by (?:the )?(?:timeout|watchdog))\b",
        r"\bsig(?:kill|term)\b",
        r"\bno output (?:for|after) \d+",
        r"\btook too long\b",
    )
)

#: Text fallback for failure, used only when `isError` is absent (DEC-005). The
#: real corpus carries the flag on every result, so this exists to keep the miner
#: from going blind if pi's format drifts, not for day-to-day precision.
_FAILURE_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bcommand not found\b",
        r"\bno such file or directory\b",
        r"\bpermission denied\b",
        r"\bsegmentation fault\b",
        r"^traceback \(most recent call last\)",
        r"^fatal: ",
        r"^error: ",
        r"\bexit(?:ed with)? (?:code|status) [1-9]",
        r"\bis not recognized as an internal or external command\b",
    )
)


@dataclass
class DetectConfig:
    """Detector knobs. `config.py` (T022) builds one of these; defaults stay generic."""


DEFAULT_CONFIG = DetectConfig()


@dataclass(frozen=True)
class Signal:
    """One detected occurrence of friction."""

    kind: str
    subject: str
    evidence: Evidence
    detail: dict = field(default_factory=dict)


def detect_session(
    summary: SessionSummary,
    *,
    redactor: Redactor,
    config: DetectConfig | None = None,
) -> list[Signal]:
    """Detect every friction signal in one session, in transcript order."""
    config = config or DEFAULT_CONFIG
    signals: list[Signal] = []
    for call in summary.tool_calls:
        signals.extend(_detect_call(summary, call, redactor))
    return signals


def _detect_call(summary: SessionSummary, call: ToolCall, redactor: Redactor) -> list[Signal]:
    if not call.matched:
        # A call with no result never completed as far as the transcript knows.
        return []

    stall = _hang_pattern(call)
    if stall is not None:
        # A stall is one piece of friction, not a hang plus a failure.
        return [_signal(HANG, summary, call, redactor, focus=stall)]
    if _is_failure(call):
        return [_signal(FAILURE, summary, call, redactor)]
    return []


def _is_failure(call: ToolCall) -> bool:
    if call.is_error is True:
        return True
    if call.is_error is False:
        # The flag is authoritative. A build log full of the word "error" that
        # exited clean is not friction (AC-008).
        return False
    return any(pattern.search(call.result_text) for pattern in _FAILURE_PATTERNS)


def _hang_pattern(call: ToolCall) -> re.Pattern | None:
    """The stall pattern this result matched, if it stalled (REQ-006).

    A stall is stall-shaped output *without clean completion*. The completion
    flag is not optional decoration here: without it, reading a file that
    documents timeouts counts as a hang — on the real corpus that was most of the
    hits, including a changelog, an error-handling doc, and every `read` of a
    skill mentioning SIGTERM.
    """
    if call.is_error is False:
        return None
    for pattern in _HANG_PATTERNS:
        if pattern.search(call.result_text):
            return pattern
    return None


def _signal(
    kind: str,
    summary: SessionSummary,
    call: ToolCall,
    redactor: Redactor,
    focus: re.Pattern | None = None,
) -> Signal:
    detail = {"tool": call.tool_name}
    if call.command:
        detail["command"] = redactor.command(call.command)
    if call.exit_code is not None:
        detail["exit_code"] = call.exit_code
    if call.kind == KIND_BASH_EXECUTION:
        detail["bash_execution"] = True
    return Signal(
        kind=kind,
        subject=call.tool_name,
        evidence=_evidence(kind, summary, call, redactor, focus),
        detail=detail,
    )


def _evidence(
    kind: str,
    summary: SessionSummary,
    call: ToolCall,
    redactor: Redactor,
    focus: re.Pattern | None = None,
) -> Evidence:
    return Evidence(
        source=kind,
        path=redactor.path(summary.path),
        line=call.evidence_line,
        excerpt=redactor.excerpt_focused(call.result_text, focus),
        timestamp=call.result_timestamp or call.timestamp,
        session_id=summary.session_id,
        origin=summary.origin,
    )
