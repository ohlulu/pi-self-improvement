"""Host-side deterministic writer for the closing half (REQ-022, ADR-0006).

The unattended `pi -p` pass has read-only tools and produces structured triage
on stdout. This module is the only thing that turns that text into files, and it
is ordinary code precisely so its reachable paths can be read off the source
rather than inferred from a model's behaviour.

Nothing here trusts its input. The triage arrives from a language model, so
every field is validated, every string is re-masked, and every path is resolved
against the output root before it is opened.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

from .redact import Redactor
from .safeio import OutputRootEscape, read_json, resolve_within, write_json, write_text
from .state import SCHEMA_VERSION

QUEUE_DIR = "queue"
QUEUE_FILE = f"{QUEUE_DIR}/FIX-QUEUE.md"
DECISIONS_DIR = "decisions"

ACT = "act"
INVESTIGATE = "investigate"
DROP = "drop"
VERDICTS = (ACT, INVESTIGATE, DROP)
#: `drop` is a decision worth recording but not a queue entry.
QUEUED = (ACT, INVESTIGATE)

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")
#: A run of dots is never meaningful in a target and reads as traversal in a
#: filename, so it collapses to one. `resolve_within` already makes escape
#: impossible; this keeps the names legible for anything that greps them.
_DOT_RUN = re.compile(r"\.{2,}")

#: The writer re-masks in default mode always. Triage text quotes packet
#: excerpts, and a packet produced with --full contains raw secrets.
_REDACTOR = Redactor()


class TriageError(ValueError):
    """The headless pass returned something that is not usable triage."""


@dataclass
class TriageEntry:
    key: str
    verdict: str
    reason: str = ""
    suggested_fix: str = ""
    proposal_id: str = ""

    @property
    def logical_id(self) -> str:
        return logical_id(self.key)


@dataclass
class Triage:
    entries: list[TriageEntry] = field(default_factory=list)
    notes: str = ""

    @property
    def queued(self) -> list[TriageEntry]:
        return [entry for entry in self.entries if entry.verdict in QUEUED]


@dataclass
class WriteResult:
    queue_path: Path
    decision_paths: list[Path] = field(default_factory=list)
    queued: int = 0
    dropped: int = 0

    @property
    def paths(self) -> list[Path]:
        return [self.queue_path, *self.decision_paths]


def logical_id(key: str) -> str:
    """A filename-safe id for a `route:target`, identical on every machine.

    Derived from the key rather than the proposal id: a proposal id covers its
    evidence references, which are per-machine session paths, so two machines
    meeting the same friction would file two different decisions for one logical
    incident (AC-051). The trailing digest keeps keys distinct that would
    otherwise slugify together.
    """
    slug = _DOT_RUN.sub(".", _UNSAFE.sub("-", key)).strip("-.").lower() or "unknown"
    digest = sha256(key.encode("utf-8")).hexdigest()[:8]
    return f"{slug[:80]}-{digest}"


def parse_triage(source) -> Triage:
    """Read triage JSON from a path, a file's text, or a dict.

    Tolerant of a code fence around the JSON because models add them under
    instruction not to; intolerant of anything else, because a writer that
    guesses at malformed input is a writer that writes the wrong file.
    """
    if isinstance(source, dict):
        payload = source
    else:
        text = _read_source(source)
        payload = _load_json(text)

    if not isinstance(payload, dict):
        raise TriageError("triage must be a JSON object")
    raw_entries = payload.get("entries")
    if not isinstance(raw_entries, list):
        raise TriageError("triage is missing an `entries` list")

    entries = []
    for index, row in enumerate(raw_entries):
        if not isinstance(row, dict):
            raise TriageError(f"entry {index} is not an object")
        key = row.get("key") or row.get("target")
        verdict = row.get("verdict")
        if not key or not isinstance(key, str):
            raise TriageError(f"entry {index} has no key")
        if verdict not in VERDICTS:
            raise TriageError(
                f"entry {index} has verdict {verdict!r}; expected one of {', '.join(VERDICTS)}"
            )
        entries.append(
            TriageEntry(
                key=_REDACTOR.text(key),
                verdict=verdict,
                reason=_REDACTOR.text(str(row.get("reason") or "")),
                suggested_fix=_REDACTOR.text(str(row.get("suggested_fix") or "")),
                # Model-controlled and written straight into a decision file.
                proposal_id=_REDACTOR.text(str(row.get("id") or "")),
            )
        )
    return Triage(entries=entries, notes=_REDACTOR.text(str(payload.get("notes") or "")))


def _read_source(source) -> str:
    path = Path(source)
    try:
        if path.is_file():
            return path.read_text(encoding="utf-8")
    except OSError as error:
        raise TriageError(f"cannot read triage: {error}") from error
    if isinstance(source, str):
        return source
    raise TriageError(f"no triage input at {source}")


def _load_json(text: str) -> dict:
    candidate = text.strip()
    fenced = _FENCE.search(candidate)
    if fenced:
        candidate = fenced.group(1).strip()
    if not candidate:
        raise TriageError("triage input is empty")
    try:
        return json.loads(candidate)
    except json.JSONDecodeError as error:
        raise TriageError(f"triage is not valid JSON: {error}") from error


def write_triage(
    output_root,
    triage: Triage,
    *,
    machine: str | None = None,
    at: str | None = None,
    resolved_keys=(),
) -> WriteResult:
    """Record decisions, then re-render the queue from them (AC-052).

    The queue is a view, not a store. Writing only this run's verdicts to it
    deleted whatever the human had not got to yet: an empty packet, or one
    covering different targets, silently emptied the list. Deriving it from the
    decision files means a run can only ever add information.
    """
    root = Path(output_root)
    moment = at or datetime.now(timezone.utc).isoformat()

    decision_paths = []
    for entry in triage.entries:
        path = resolve_within(root, f"{DECISIONS_DIR}/{entry.logical_id}.json")
        _append_decision(path, entry, machine, moment)
        decision_paths.append(path)

    open_entries = _open_entries(root, set(resolved_keys))
    queue_path = resolve_within(root, QUEUE_FILE)
    write_text(queue_path, render_queue(open_entries, triage.notes, moment))

    return WriteResult(
        queue_path=queue_path,
        decision_paths=decision_paths,
        queued=len(open_entries),
        dropped=sum(1 for entry in triage.entries if entry.verdict == DROP),
    )


def _open_entries(root: Path, resolved_keys: set) -> list[TriageEntry]:
    """Every incident whose latest verdict is still actionable.

    A resolved target leaves the queue here rather than waiting to be dropped by
    a later triage. It will never appear in one: once resolved, the miner stops
    staging it, so nothing would ever come along to clear the entry.
    """
    directory = root / DECISIONS_DIR
    if not directory.is_dir():
        return []

    entries = []
    for path in sorted(directory.glob("*.json")):
        payload = _existing_decision(path)
        if not payload or not payload["entries"]:
            continue
        key = payload.get("key", "")
        if key in resolved_keys:
            continue
        latest = payload["entries"][-1]
        if latest.get("verdict") not in QUEUED:
            continue
        entries.append(
            TriageEntry(
                key=key,
                verdict=latest.get("verdict", INVESTIGATE),
                reason=latest.get("reason", ""),
                suggested_fix=latest.get("suggested_fix", ""),
                proposal_id=latest.get("proposal_id", ""),
            )
        )
    return entries


def _append_decision(path: Path, entry: TriageEntry, machine: str | None, moment: str) -> None:
    """One file per logical incident; machine lives at entry level (DEC-010).

    Appending rather than replacing is what lets two machines contribute to the
    same incident without either overwriting the other's verdict.
    """
    payload = _existing_decision(path) or {
        "schema_version": SCHEMA_VERSION,
        "id": entry.logical_id,
        "key": entry.key,
        "entries": [],
    }
    payload["entries"].append(
        {
            "machine": machine,
            "verdict": entry.verdict,
            "reason": entry.reason,
            "suggested_fix": entry.suggested_fix,
            "proposal_id": entry.proposal_id,
            "at": moment,
        }
    )
    write_json(path, payload)


def _existing_decision(path: Path):
    payload = read_json(path)
    if isinstance(payload, dict) and isinstance(payload.get("entries"), list):
        return payload
    return None


def render_queue(queued, notes: str, moment: str) -> str:
    queued = list(queued)
    lines = [
        "# Fix queue",
        "",
        f"_Updated {moment}. {len(queued)} entr{'y' if len(queued) == 1 else 'ies'} to work._",
        "",
        "Every entry here is a suggestion backed by evidence. Nothing is applied "
        "automatically; load the `learn-loop` skill to work through them.",
        "",
    ]
    if notes:
        lines += [f"> {notes}", ""]
    if not queued:
        lines += ["Nothing to act on.", ""]
        return "\n".join(lines)

    for verdict, title in ((ACT, "Act"), (INVESTIGATE, "Investigate")):
        group = [entry for entry in queued if entry.verdict == verdict]
        if not group:
            continue
        lines += [f"## {title}", ""]
        for entry in group:
            lines.append(f"- **`{entry.key}`** — {entry.reason}")
            if entry.suggested_fix:
                lines.append(f"  - Suggested: {entry.suggested_fix}")
            lines.append(f"  - Decision file: `{DECISIONS_DIR}/{entry.logical_id}.json`")
        lines.append("")
    return "\n".join(lines)






__all__ = [
    "DROP",
    "ACT",
    "INVESTIGATE",
    "OutputRootEscape",
    "Triage",
    "TriageEntry",
    "TriageError",
    "WriteResult",
    "logical_id",
    "parse_triage",
    "render_queue",
    "write_triage",
]
