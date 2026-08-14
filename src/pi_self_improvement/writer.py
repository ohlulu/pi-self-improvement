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
from .stage import OutputRootEscape, resolve_within
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
                proposal_id=str(row.get("id") or ""),
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
    output_root, triage: Triage, *, machine: str | None = None, at: str | None = None
) -> WriteResult:
    """Write the queue and one decision file per logical incident (AC-052)."""
    root = Path(output_root)
    moment = at or datetime.now(timezone.utc).isoformat()

    decision_paths = []
    for entry in triage.entries:
        path = resolve_within(root, f"{DECISIONS_DIR}/{entry.logical_id}.json")
        _append_decision(path, entry, machine, moment)
        decision_paths.append(path)

    queue_path = resolve_within(root, QUEUE_FILE)
    _write_text(queue_path, render_queue(triage, moment))

    return WriteResult(
        queue_path=queue_path,
        decision_paths=decision_paths,
        queued=len(triage.queued),
        dropped=sum(1 for entry in triage.entries if entry.verdict == DROP),
    )


def _append_decision(path: Path, entry: TriageEntry, machine: str | None, moment: str) -> None:
    """One file per logical incident; machine lives at entry level (DEC-010).

    Appending rather than replacing is what lets two machines contribute to the
    same incident without either overwriting the other's verdict.
    """
    payload = _read_json(path) or {
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
    _write_text(path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def render_queue(triage: Triage, moment: str) -> str:
    queued = triage.queued
    lines = [
        "# Fix queue",
        "",
        f"_Updated {moment}. {len(queued)} entr{'y' if len(queued) == 1 else 'ies'} to work._",
        "",
        "Every entry here is a suggestion backed by evidence. Nothing is applied "
        "automatically; load the `learn-loop` skill to work through them.",
        "",
    ]
    if triage.notes:
        lines += [f"> {triage.notes}", ""]
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


def _read_json(path: Path):
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) and isinstance(payload.get("entries"), list) else None


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


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
