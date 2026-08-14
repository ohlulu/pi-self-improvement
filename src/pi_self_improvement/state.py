"""Seen keys, recurrence, resolutions and the pipeline order (REQ-016, REQ-017).

The pipeline order is fixed by DEC-017 and is not a detail:

    resolution filter -> seen-key filter -> grouping/staging -> recurrence

Filtering seen keys first would swallow a regression before the resolution
filter ever saw it, so a target marked `fixed` could never come back. Every
function here preserves that order.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .route import Proposal

SCHEMA_VERSION = 1

FIXED = "fixed"
WONTFIX = "wontfix"
IGNORED = "ignored"
DECISIONS = (FIXED, WONTFIX, IGNORED)

#: `wontfix` and `ignored` are permanent: the user said no, and asking again
#: every run is how a review tool teaches people to ignore it.
_PERMANENT = (WONTFIX, IGNORED)

STATE_FILE = "state.json"
RESOLUTIONS_FILE = "resolutions.json"


def proposal_id(proposal: Proposal) -> str:
    """A stable id for route + target + the exact evidence behind it (REQ-016).

    Evidence references are part of the identity so that the same friction
    observed again is the same proposal, while genuinely new evidence produces a
    new one. That is what lets the seen filter suppress repeats (AC-029) without
    also suppressing a regression carrying only post-watermark evidence.
    """
    parts = [proposal.route, proposal.target]
    parts.extend(sorted(item.reference for item in proposal.evidence))
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:16]


def parse_timestamp(value: str | None) -> datetime | None:
    """ISO-8601 as pi writes it, tolerant of the trailing `Z` on Python 3.10."""
    if not value:
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


@dataclass
class Resolution:
    key: str
    decision: str
    resolved_at: str
    pr: str | None = None
    note: str | None = None
    by: str | None = None

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "decision": self.decision,
            "resolved_at": self.resolved_at,
            "pr": self.pr,
            "note": self.note,
            "by": self.by,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> Resolution:
        return cls(
            key=payload.get("key", ""),
            decision=payload.get("decision", FIXED),
            resolved_at=payload.get("resolved_at", ""),
            pr=payload.get("pr"),
            note=payload.get("note"),
            by=payload.get("by"),
        )


@dataclass
class Staged:
    """A proposal that survived the filters, with what staging needs to know."""

    proposal: Proposal
    id: str
    regression: bool = False
    previous_runs: int = 0

    @property
    def key(self) -> str:
        return self.proposal.key

    @property
    def route(self) -> str:
        return self.proposal.route

    @property
    def target(self) -> str:
        return self.proposal.target

    @property
    def recurring(self) -> bool:
        return self.previous_runs > 0


@dataclass
class PipelineResult:
    staged: list[Staged] = field(default_factory=list)
    suppressed_resolved: int = 0
    suppressed_seen: int = 0

    @property
    def regressions(self) -> list[Staged]:
        return [item for item in self.staged if item.regression]


class Resolutions:
    """The `route:target` registry (REQ-017)."""

    def __init__(self, entries: dict[str, Resolution] | None = None):
        self.entries: dict[str, Resolution] = dict(entries or {})

    @classmethod
    def load(cls, path: Path) -> Resolutions:
        payload = _read_json(path)
        raw = payload.get("resolutions", {}) if isinstance(payload, dict) else {}
        return cls({key: Resolution.from_dict(value) for key, value in raw.items()})

    def to_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "resolutions": {key: value.to_dict() for key, value in self.entries.items()},
        }

    def save(self, path: Path) -> None:
        _write_json(path, self.to_dict())

    def get(self, key: str) -> Resolution | None:
        return self.entries.get(key)

    def resolve(
        self,
        key: str,
        decision: str,
        *,
        resolved_at: str | None = None,
        pr: str | None = None,
        note: str | None = None,
        by: str | None = None,
        state: State | None = None,
    ) -> Resolution:
        """Record a decision and forget the recurrence it accumulated (REQ-017).

        Trimming is not bookkeeping. A target resolved after five noisy runs
        keeps that history otherwise, so its first regression is announced as
        "also flagged in 5 previous run(s)" — the reviewer reads a brand-new
        recurrence as an old one and cannot tell the fix ever worked (AC-049).
        """
        if decision not in DECISIONS:
            raise ValueError(f"unknown decision: {decision!r}")
        entry = Resolution(
            key=key,
            decision=decision,
            resolved_at=resolved_at or datetime.now(timezone.utc).isoformat(),
            pr=pr,
            note=note,
            by=by,
        )
        self.entries[key] = entry
        if state is not None:
            state.trim_recurrence(key, parse_timestamp(entry.resolved_at))
        return entry

    def unresolve(self, key: str) -> bool:
        return self.entries.pop(key, None) is not None

    def import_decisions(self, source, *, state: State | None = None) -> list[str]:
        """Import a `decisions.json` handoff (AC-032).

        Only the three real decisions are imported. `open` and `deferred` mean
        the reviewer has not decided, so importing them would silently suppress
        a proposal that is still under discussion.
        """
        payload = source if isinstance(source, (dict, list)) else _read_json(Path(source))
        rows = payload.get("decisions", []) if isinstance(payload, dict) else payload
        imported = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            key = row.get("key") or row.get("target")
            decision = row.get("decision")
            if not key or decision not in DECISIONS:
                continue
            self.resolve(
                key,
                decision,
                resolved_at=row.get("resolved_at"),
                pr=row.get("pr"),
                note=row.get("note"),
                by=row.get("by"),
                state=state,
            )
            imported.append(key)
        return imported

    def apply(self, proposal: Proposal) -> tuple[Proposal | None, bool]:
        """Return the proposal as it survives resolution, and whether it regressed.

        `fixed` is a watermark, not an erasure: evidence from before the fix is
        dropped and anything after it comes back as a regression. `wontfix` and
        `ignored` suppress permanently (AC-049).
        """
        entry = self.entries.get(proposal.key)
        if entry is None:
            return proposal, False
        if entry.decision in _PERMANENT:
            return None, False

        watermark = parse_timestamp(entry.resolved_at)
        if watermark is None:
            return proposal, False

        fresh = [
            signal
            for signal in proposal.signals
            if _is_after(signal.evidence.timestamp, watermark)
        ]
        if not fresh:
            return None, False
        survivor = Proposal(
            route=proposal.route,
            target=proposal.target,
            signals=fresh,
            summary=proposal.summary,
        )
        return survivor, True


def _is_after(timestamp: str | None, watermark: datetime) -> bool:
    """Evidence with no usable timestamp cannot prove it is new.

    A resolution is an explicit human decision, so undated evidence stays
    suppressed rather than re-opening something the user closed.
    """
    moment = parse_timestamp(timestamp)
    return moment is not None and moment > watermark


class State:
    """Seen proposal ids and per-target recurrence history.

    Recurrence entries carry the run's timestamp, not just its id. Without a
    date the registry cannot drop history from before a resolution, and the
    first regression after a fix reports the recurrence it accumulated before
    the fix — which is exactly what AC-049 forbids.
    """

    def __init__(self, seen: dict | None = None, recurrence: dict | None = None):
        self.seen: dict[str, dict] = dict(seen or {})
        self.recurrence: dict[str, list[dict]] = {
            key: [_recurrence_entry(item) for item in value]
            for key, value in (recurrence or {}).items()
        }

    @classmethod
    def load(cls, path: Path) -> State:
        payload = _read_json(path)
        if not isinstance(payload, dict):
            return cls()
        return cls(seen=payload.get("seen"), recurrence=payload.get("recurrence"))

    def to_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "seen": self.seen,
            "recurrence": self.recurrence,
        }

    def save(self, path: Path) -> None:
        _write_json(path, self.to_dict())

    def has_seen(self, identifier: str) -> bool:
        return identifier in self.seen

    def previous_runs(self, key: str) -> int:
        return len(self.recurrence.get(key, ()))

    def record(self, staged: list[Staged], run_id: str, at: str | None = None) -> None:
        """Commit a staged run. Never called on a dry run (DEC-017)."""
        moment = at or datetime.now(timezone.utc).isoformat()
        for item in staged:
            self.seen.setdefault(item.id, {"key": item.key, "run_id": run_id})
            history = self.recurrence.setdefault(item.key, [])
            if all(entry.get("run_id") != run_id for entry in history):
                history.append({"run_id": run_id, "at": moment})

    def trim_recurrence(self, key: str, watermark: datetime | None) -> int:
        """Forget runs from before a resolution (REQ-017).

        Undated entries go too: an entry that cannot be dated cannot be shown to
        post-date the fix, and keeping it would inflate the first regression's
        count — the precise failure AC-049 names.
        """
        history = self.recurrence.get(key)
        if not history:
            return 0
        if watermark is None:
            kept: list[dict] = []
        else:
            kept = [entry for entry in history if _is_after(entry.get("at"), watermark)]
        removed = len(history) - len(kept)
        if kept:
            self.recurrence[key] = kept
        else:
            self.recurrence.pop(key, None)
        return removed


def _recurrence_entry(item) -> dict:
    if isinstance(item, dict):
        return {"run_id": item.get("run_id", ""), "at": item.get("at")}
    return {"run_id": str(item), "at": None}


def run_pipeline(
    proposals,
    *,
    state: State,
    resolutions: Resolutions | None = None,
    include_seen: bool = False,
    include_resolved: bool = False,
) -> PipelineResult:
    """Apply DEC-017's fixed order. Pure: nothing here writes or mutates state."""
    resolutions = resolutions or Resolutions()
    result = PipelineResult()

    for proposal in proposals:
        # 1. Resolution filter, before anything can swallow a regression.
        if include_resolved:
            survivor, regression = proposal, False
        else:
            survivor, regression = resolutions.apply(proposal)
        if survivor is None:
            result.suppressed_resolved += 1
            continue

        # 2. Seen-key filter.
        identifier = proposal_id(survivor)
        if state.has_seen(identifier) and not include_seen:
            result.suppressed_seen += 1
            continue

        # 3. Staging is the caller's job; 4. recurrence annotation is ours.
        result.staged.append(
            Staged(
                proposal=survivor,
                id=identifier,
                regression=regression,
                previous_runs=state.previous_runs(survivor.key),
            )
        )

    result.staged.sort(
        key=lambda item: (not item.regression, -item.previous_runs, item.route, item.target)
    )
    return result


def self_check(counts) -> list[str]:
    """Warnings a scan must surface loudly (REQ-018).

    The zero-tool-call case is the one that matters: it is what a silent parser
    break looks like from the outside. Everything appears to work, the scan
    reports success, and it quietly finds nothing forever.
    """
    warnings = []
    if counts.files and not counts.tool_calls:
        warnings.append(
            f"parsed {counts.files} transcript(s) but found 0 tool calls — "
            "the parser may not match this pi version"
        )
    if counts.parse_errors:
        warnings.append(f"{counts.parse_errors} transcript line(s) failed to parse")
    if counts.non_canonical_files:
        warnings.append(f"{counts.non_canonical_files} transcript(s) use a non-canonical schema")
    return warnings


def _read_json(path: Path) -> dict:
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    tmp.replace(path)
