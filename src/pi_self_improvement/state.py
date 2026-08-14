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
    """Seen proposal ids and per-target recurrence history."""

    def __init__(self, seen: dict | None = None, recurrence: dict | None = None):
        self.seen: dict[str, dict] = dict(seen or {})
        self.recurrence: dict[str, list[str]] = {
            key: list(value) for key, value in (recurrence or {}).items()
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

    def record(self, staged: list[Staged], run_id: str) -> None:
        """Commit a staged run. Never called on a dry run (DEC-017)."""
        for item in staged:
            self.seen.setdefault(item.id, {"key": item.key, "run_id": run_id})
            history = self.recurrence.setdefault(item.key, [])
            if run_id not in history:
                history.append(run_id)


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
