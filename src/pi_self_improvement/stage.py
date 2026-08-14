"""Run metadata, proposal JSON and the review packet (REQ-015).

Everything written here lands under the output root and nowhere else. The
confinement check is a real assertion rather than a convention: this is the only
module that creates files, so it is the only place the guarantee can be made.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from .model import ParseCounts
from .redact import Redactor
from .state import SCHEMA_VERSION, Staged

RUNS_DIR = "runs"
PROPOSALS_DIR = "proposals"
PACKETS_DIR = "review-packets"


class OutputRootEscape(RuntimeError):
    """Raised when a write would land outside the output root."""


@dataclass
class StageResult:
    run_id: str
    run_path: Path
    packet_path: Path
    proposal_paths: list[Path] = field(default_factory=list)

    @property
    def paths(self) -> list[Path]:
        return [self.run_path, self.packet_path, *self.proposal_paths]


def new_run_id(moment: float | None = None) -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(moment if moment is not None else time.time()))


def write_run(
    output_root,
    staged: list[Staged],
    *,
    run_id: str | None = None,
    counts: ParseCounts | None = None,
    redactor: Redactor | None = None,
    warnings=(),
    machine: str | None = None,
) -> StageResult:
    """Write the three staging outputs for one scan (AC-027)."""
    root = Path(output_root)
    run_id = run_id or new_run_id()
    local_only = bool(redactor.local_only) if redactor is not None else False
    warnings = list(warnings)
    started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    proposal_paths = []
    for item in staged:
        path = _resolve(root, f"{PROPOSALS_DIR}/{run_id}/{item.id}.json")
        _write_json(path, _proposal_payload(item, run_id, local_only, machine))
        proposal_paths.append(path)

    run_path = _resolve(root, f"{RUNS_DIR}/{run_id}.json")
    _write_json(
        run_path,
        {
            "schema_version": SCHEMA_VERSION,
            "run_id": run_id,
            "started_at": started_at,
            "local_only": local_only,
            "machine": machine,
            "warnings": warnings,
            "counts": counts.to_dict() if counts is not None else {},
            "proposals": [
                {
                    "id": item.id,
                    "key": item.key,
                    "route": item.route,
                    "target": item.target,
                    "evidence_count": len(item.proposal.evidence),
                    "regression": item.regression,
                    "previous_runs": item.previous_runs,
                }
                for item in staged
            ],
        },
    )

    packet_path = _resolve(root, f"{PACKETS_DIR}/{run_id}.md")
    _write_text(packet_path, render_packet(staged, run_id, counts, local_only, warnings, machine))

    return StageResult(
        run_id=run_id, run_path=run_path, packet_path=packet_path, proposal_paths=proposal_paths
    )


def _proposal_payload(item: Staged, run_id: str, local_only: bool, machine: str | None) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "id": item.id,
        "run_id": run_id,
        "key": item.key,
        "route": item.route,
        "target": item.target,
        "summary": item.proposal.summary,
        "regression": item.regression,
        "recurring": item.recurring,
        "previous_runs": item.previous_runs,
        "local_only": local_only,
        "machine": machine,
        "evidence": [evidence.to_dict() for evidence in item.proposal.evidence],
    }


def render_packet(
    staged: list[Staged],
    run_id: str,
    counts: ParseCounts | None,
    local_only: bool,
    warnings=(),
    machine: str | None = None,
) -> str:
    """The human-readable packet. Ordering carries meaning (AC-028).

    Regressions first because something believed fixed came back, then recurring
    targets because repetition is the evidence that a one-off was not one, then
    everything new.
    """
    lines = [f"# Review packet {run_id}", ""]
    if local_only:
        lines += [
            "> **LOCAL ONLY — do not share.** This run was produced with `--full`, so "
            "evidence bypassed the redaction boundary and may contain secrets.",
            "",
        ]
    for warning in warnings:
        lines += [f"> **WARNING** {warning}", ""]

    regressions = [item for item in staged if item.regression]
    recurring = [item for item in staged if item.recurring and not item.regression]
    fresh = [item for item in staged if not item.recurring and not item.regression]

    lines += [
        f"{len(staged)} proposal(s): {len(regressions)} regression, "
        f"{len(recurring)} recurring, {len(fresh)} new.",
        "",
    ]
    for title, group in (
        ("Regressions", regressions),
        ("Recurring", recurring),
        ("New", fresh),
    ):
        if not group:
            continue
        lines += [f"## {title}", ""]
        for item in group:
            lines += _render_proposal(item)
    if counts is not None:
        lines += _render_counts(counts)
    if machine:
        lines += [f"_Scanned on {machine}._", ""]
    return "\n".join(lines).rstrip() + "\n"


def _render_proposal(item: Staged) -> list[str]:
    lines = [f"### `{item.key}`", ""]
    notes = []
    if item.regression:
        notes.append("regressed after being resolved")
    if item.recurring:
        notes.append(f"also flagged in {item.previous_runs} previous run(s)")
    if notes:
        lines += ["_" + "; ".join(notes) + "._", ""]
    lines += [item.proposal.summary, "", f"Evidence ({len(item.proposal.evidence)}):", ""]
    for evidence in item.proposal.evidence[:5]:
        lines.append(f"- `{evidence.reference}` — {evidence.excerpt}")
    remaining = len(item.proposal.evidence) - 5
    if remaining > 0:
        lines.append(f"- …and {remaining} more")
    lines += ["", f"Proposal id: `{item.id}`", ""]
    return lines


def _render_counts(counts: ParseCounts) -> list[str]:
    payload = counts.to_dict()
    lines = ["## Parser self-check", ""]
    for label, value in payload.items():
        if isinstance(value, dict):
            rendered = ", ".join(f"{k}={v}" for k, v in sorted(value.items())) or "none"
            lines.append(f"- {label}: {rendered}")
        else:
            lines.append(f"- {label}: {value}")
    lines.append("")
    return lines


def _resolve(root: Path, relative: str) -> Path:
    """Every write goes through here, so nothing can escape the output root."""
    base = root.expanduser().resolve()
    candidate = (base / relative).resolve()
    if candidate != base and base not in candidate.parents:
        raise OutputRootEscape(f"refusing to write outside the output root: {candidate}")
    return candidate


def _write_json(path: Path, payload: dict) -> None:
    _write_text(path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
