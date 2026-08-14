"""Discover pi transcripts and reduce each one to a `SessionSummary`.

Two responsibilities, deliberately kept apart: `discover_transcripts()` decides
*which* transcripts a scan looks at (window, limit, origin), `parse_transcript()`
decides *what* a transcript contains.

Transcripts are read in file line order and never walked through `parentId`
(DEC-014 / ADR-0004): session format v1 has no `parentId`, and a miner reading the
file directly never triggers pi's on-load version migration, so a tree parser would
silently return nothing on someone else's older transcripts. The cost — sibling
branches being read as one stream — is measured by `ParseCounts.branch_points`
rather than hidden.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

from .model import (
    KIND_BASH_EXECUTION,
    KIND_TOOL,
    ORIGIN_ROOT,
    ORIGIN_SUBAGENT,
    AssistantTurn,
    CustomEntry,
    ParseCounts,
    SessionSummary,
    ToolCall,
    UserMessage,
)

DEFAULT_SESSIONS_ROOT = "~/.pi/agent/sessions"

#: Schema versions this parser understands. Anything else is counted as
#: non-canonical and mined for nothing (AC-041).
KNOWN_SCHEMA_VERSIONS = frozenset({1, 2, 3})

_RUN_DIR = re.compile(r"run-\d+\Z")

#: Pi appends the exit status to a bash result's text and only sometimes repeats
#: it in `details`. Reading it here means detectors get the status for every bash
#: call rather than the ~8% that carry the structured field.
_EXIT_IN_TEXT = re.compile(r"Command exited with code (\d+)\s*\Z")
_DAY = 86400.0
_ABORTED_STOP_REASONS = frozenset({"aborted", "error"})


def default_sessions_root(home: str | Path | None = None) -> Path:
    if home is None:
        return Path(DEFAULT_SESSIONS_ROOT).expanduser()
    return Path(home) / ".pi" / "agent" / "sessions"


def is_subagent_path(path: str | Path) -> bool:
    """A subagent transcript is `<session-id>/<hash>/run-N/session.jsonl` (DEC-015).

    The shape sits inside pi's own session directory, so it survives pi-subagents
    moving its artifacts around.
    """
    parts = Path(path).parts
    if len(parts) < 4 or parts[-1] != "session.jsonl":
        return False
    return _RUN_DIR.fullmatch(parts[-2]) is not None


def root_transcript_for(path: str | Path) -> Path:
    """The root transcript a subagent transcript belongs to."""
    session_dir = Path(path).parents[2]
    return session_dir.parent / (session_dir.name + ".jsonl")


@dataclass(frozen=True)
class DiscoveredTranscript:
    path: Path
    origin: str
    mtime: float
    root_path: Path | None = None


def discover_transcripts(
    roots,
    *,
    since_days: float | None = None,
    include_all: bool = False,
    max_sessions: int | None = None,
    now: float | None = None,
) -> list[DiscoveredTranscript]:
    """Find transcripts under `roots`, newest root session first.

    The window and the limit both apply to root sessions only; a kept root brings
    its subagent sessions with it and they consume no quota (AC-003, AC-039). State
    is not consulted — a fresh output root scans exactly the same window as an old
    one, and history outside it only enters via `include_all` (DEC-013 / AC-038).
    """
    now = time.time() if now is None else now
    paths = _collect_paths(roots)

    subagents_by_root: dict[Path, list[Path]] = {}
    root_paths: list[Path] = []
    orphans: list[Path] = []
    for path in paths:
        if not is_subagent_path(path):
            root_paths.append(path)
            continue
        owner = root_transcript_for(path)
        if owner.exists():
            subagents_by_root.setdefault(owner, []).append(path)
        else:
            orphans.append(path)

    cutoff = None if include_all or since_days is None else now - since_days * _DAY

    kept_roots = [path for path in root_paths if cutoff is None or _mtime(path) >= cutoff]
    kept_roots.sort(key=lambda path: (-_mtime(path), str(path)))
    if max_sessions is not None:
        kept_roots = kept_roots[:max_sessions]

    discovered: list[DiscoveredTranscript] = []
    for path in kept_roots:
        discovered.append(DiscoveredTranscript(path=path, origin=ORIGIN_ROOT, mtime=_mtime(path)))
        for nested in sorted(subagents_by_root.get(path, [])):
            discovered.append(
                DiscoveredTranscript(
                    path=nested, origin=ORIGIN_SUBAGENT, mtime=_mtime(nested), root_path=path
                )
            )

    for path in sorted(orphans):
        if cutoff is None or _mtime(path) >= cutoff:
            discovered.append(
                DiscoveredTranscript(path=path, origin=ORIGIN_SUBAGENT, mtime=_mtime(path))
            )

    return discovered


def _collect_paths(roots) -> list[Path]:
    seen: set[Path] = set()
    found: list[Path] = []
    for root in roots:
        base = Path(root).expanduser()
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.jsonl")):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            found.append(path)
    return found


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def parse_transcripts(discovered) -> tuple[list[SessionSummary], ParseCounts]:
    """Parse every discovered transcript and aggregate the self-check counts."""
    summaries: list[SessionSummary] = []
    total = ParseCounts()
    for item in discovered:
        if isinstance(item, DiscoveredTranscript):
            summary = parse_transcript(item.path, origin=item.origin)
        else:
            summary = parse_transcript(item)
        summaries.append(summary)
        total.merge(summary.counts)
    return summaries, total


def parse_transcript(path: str | Path, *, origin: str | None = None) -> SessionSummary:
    path = Path(path)
    if origin is None:
        origin = ORIGIN_SUBAGENT if is_subagent_path(path) else ORIGIN_ROOT

    summary = SessionSummary(path=str(path), origin=origin)
    counts = summary.counts
    counts.files = 1
    if origin == ORIGIN_SUBAGENT:
        counts.subagent_sessions = 1
    else:
        counts.root_sessions = 1

    records = _read_records(path, counts)
    header = next((record for _, record in records if record.get("type") == "session"), None)
    if header is None or header.get("version") not in KNOWN_SCHEMA_VERSIONS:
        counts.non_canonical_files = 1
        return summary

    summary.session_id = str(header.get("id") or path.stem)
    summary.cwd = header.get("cwd")
    summary.started_at = header.get("timestamp")

    calls_by_id: dict[str, ToolCall] = {}
    children_per_parent: dict[str, int] = {}

    for line, record in records:
        timestamp = record.get("timestamp")
        if timestamp:
            summary.ended_at = timestamp
        record_type = record.get("type")
        parent_id = record.get("parentId")
        if parent_id and record_type == "message":
            # Only messages fork a conversation. Annotations (`label`, `custom`,
            # `model_change`) hang off the current node and share its parent, so
            # counting every record type inflates the branch count several-fold.
            children_per_parent[parent_id] = children_per_parent.get(parent_id, 0) + 1

        if record_type == "session":
            continue
        if record_type == "custom":
            counts.skip("custom")
            summary.custom_entries.append(
                CustomEntry(
                    custom_type=str(record.get("customType") or ""),
                    data=record.get("data") or {},
                    line=line,
                    timestamp=timestamp,
                )
            )
            continue
        if record_type != "message":
            # Injected context (`custom_message`), compactions, labels and the rest
            # never reach a detector, but REQ-003 forbids dropping them silently.
            counts.skip(str(record_type))
            continue

        message = record.get("message")
        if not isinstance(message, dict):
            counts.skip("message(malformed)")
            continue
        _absorb_message(summary, message, line, timestamp, calls_by_id)

    counts.branch_points = sum(1 for count in children_per_parent.values() if count > 1)
    counts.dangling_tool_calls = sum(
        1 for call in summary.tool_calls if call.kind == KIND_TOOL and not call.matched
    )
    counts.tool_calls = len(summary.tool_calls)
    return summary


def _read_records(path: Path, counts: ParseCounts) -> list[tuple[int, dict]]:
    records: list[tuple[int, dict]] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        counts.parse_errors += 1
        return records
    # Split on \n only. `str.splitlines()` also breaks on U+2028, U+2029, \x0b,
    # \x0c and \x85, all of which JSON allows unescaped inside a string — and all
    # of which occur in real transcripts. Splitting on them tears records in half
    # and reports the halves as parse errors.
    for line, raw in enumerate(text.split("\n"), start=1):
        raw = raw.rstrip("\r")
        if not raw.strip():
            continue
        try:
            record = json.loads(raw)
        except ValueError:
            counts.parse_errors += 1
            continue
        if not isinstance(record, dict):
            counts.parse_errors += 1
            continue
        records.append((line, record))
    return records


def _absorb_message(
    summary: SessionSummary,
    message: dict,
    line: int,
    timestamp: str | None,
    calls_by_id: dict[str, ToolCall],
) -> None:
    counts = summary.counts
    role = message.get("role")
    stamp = message.get("timestamp") or timestamp

    if role == "user":
        text = _text_of(message.get("content"))
        if text:
            summary.user_messages.append(UserMessage(text=text, line=line, timestamp=stamp))
        return

    if role == "assistant":
        stop_reason = message.get("stopReason")
        if stop_reason in _ABORTED_STOP_REASONS:
            # A turn that never completed carries no reliable signal (AC-041).
            if stop_reason == "aborted":
                counts.aborted_turns += 1
            else:
                counts.error_turns += 1
            return
        text = _text_of(message.get("content"), kinds=("text",))
        if text:
            summary.assistant_turns.append(
                AssistantTurn(text=text, line=line, timestamp=stamp, stop_reason=stop_reason)
            )
        for block in message.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "toolCall":
                call = _tool_call_from(block, line, stamp)
                summary.tool_calls.append(call)
                if call.call_id:
                    calls_by_id[call.call_id] = call
        return

    if role == "toolResult":
        counts.tool_results += 1
        if "isError" in message:
            counts.tool_results_with_is_error += 1
        call = calls_by_id.get(str(message.get("toolCallId")))
        if call is None:
            counts.skip("toolResult(unmatched)")
            return
        call.matched = True
        call.result_text = _text_of(message.get("content"), kinds=("text",))
        call.is_error = message.get("isError")
        call.result_line = line
        call.result_timestamp = stamp
        details = message.get("details")
        if isinstance(details, dict):
            if call.command is None and isinstance(details.get("command"), str):
                call.command = details["command"]
            if call.exit_code is None and isinstance(details.get("exitCode"), int):
                call.exit_code = details["exitCode"]
        if call.exit_code is None:
            found = _EXIT_IN_TEXT.search(call.result_text.rstrip())
            if found:
                call.exit_code = int(found.group(1))
        return

    if role == "bashExecution":
        exit_code = message.get("exitCode")
        cancelled = bool(message.get("cancelled"))
        summary.tool_calls.append(
            ToolCall(
                tool_name="bash",
                line=line,
                timestamp=stamp,
                kind=KIND_BASH_EXECUTION,
                matched=True,
                result_text=message.get("output") or "",
                # No `isError` on this record type; the exit code is the structural
                # error flag, so deriving it here is normalization, not a heuristic.
                is_error=bool(cancelled or (isinstance(exit_code, int) and exit_code != 0)),
                result_line=line,
                result_timestamp=stamp,
                command=message.get("command"),
                exit_code=exit_code if isinstance(exit_code, int) else None,
                cancelled=cancelled,
            )
        )
        return

    counts.skip(f"message({role})")


def _tool_call_from(block: dict, line: int, timestamp: str | None) -> ToolCall:
    arguments = block.get("arguments")
    if not isinstance(arguments, dict):
        arguments = {}
    command = arguments.get("command")
    return ToolCall(
        tool_name=str(block.get("name") or ""),
        line=line,
        call_id=str(block["id"]) if block.get("id") else None,
        arguments=arguments,
        timestamp=timestamp,
        command=command if isinstance(command, str) else None,
    )


def _text_of(content, kinds: tuple[str, ...] = ("text",)) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = [
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type") in kinds
    ]
    return "\n".join(part for part in parts if part)
