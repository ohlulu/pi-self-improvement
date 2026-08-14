"""Route signals to proposals (REQ-013, REQ-014).

Four routes, one target each, per the REQ-013 table. A signal that fits none of
them is discarded rather than staged: a review packet is only worth reading if
everything in it is worth acting on.

`route:target` is simultaneously the proposal id, the recurrence key and the
resolutions key (ADR-0005), so target spelling is a data format. It is computed
deterministically here and never depends on the run's redaction mode — a target
that changed under `--full` would fragment state across runs.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .detect import CORRECTION, FAILURE, HANG, RETRY, SILENT_EMPTY, SKILL, Signal
from .redact import Redactor

ROUTE_TOOL = "tool"
ROUTE_SKILL = "skill_improvement"
ROUTE_MEMORY = "memory_context"
ROUTE_BACKLOG = "backlog"
ROUTES = (ROUTE_TOOL, ROUTE_SKILL, ROUTE_MEMORY, ROUTE_BACKLOG)

UNKNOWN_TARGET = "<unknown>"
EXT_PREFIX = "ext:"

#: AC-023: one noisy session must not fill the packet on its own.
MAX_CORRECTIONS_PER_SESSION = 3

#: AC-024: the backlog is for friction that recurs, so one session is not enough.
#: A one-off failure of an untracked program is noise (REQ-013's discard rule).
MIN_BACKLOG_SESSIONS = 2

#: Pi's own tools. DEC-007 keeps them out of extension-family grouping, otherwise
#: every `read` failure would invent a target called `ext:read` (AC-026).
#: Deliberately limited to pi core: `ralph_done` and `subagent` come from
#: extensions, so grouping them as `ext:ralph` and `ext:subagent` is correct.
DEFAULT_BUILTIN_TOOLS = frozenset(
    {"read", "bash", "edit", "write", "grep", "find", "ls", "ask", "todo"}
)

#: Shell noise that is never worth a backlog entry.
DEFAULT_BACKLOG_IGNORE = frozenset(
    {"cd", "echo", "true", "false", "test", "export", "source", "set", "unset", "printf"}
)

_WORKTREE_GITDIR = re.compile(r"gitdir:\s*(.+)")

#: Targets are identity, so they are masked in default mode always \u2014 never with
#: the run's redactor, whose `--full` mode would change the spelling.
_TARGET_REDACTOR = Redactor()


@dataclass(frozen=True)
class RouteConfig:
    ext_family_map: dict = field(default_factory=dict)
    builtin_tools: frozenset = DEFAULT_BUILTIN_TOOLS
    extra_backlog_ignore: tuple[str, ...] = ()
    max_corrections_per_session: int = MAX_CORRECTIONS_PER_SESSION
    min_backlog_sessions: int = MIN_BACKLOG_SESSIONS
    home: str | None = None


DEFAULT_ROUTE_CONFIG = RouteConfig()


@dataclass
class Proposal:
    route: str
    target: str
    signals: list[Signal] = field(default_factory=list)
    summary: str = ""

    @property
    def key(self) -> str:
        return f"{self.route}:{self.target}"

    @property
    def evidence(self) -> list:
        return [signal.evidence for signal in self.signals]

    @property
    def sessions(self) -> set:
        return {signal.evidence.session_id for signal in self.signals}


def ext_family(tool_name: str, config: RouteConfig | None = None) -> str | None:
    """The extension family a tool belongs to, or None for a builtin (DEC-007).

    Pi names extension tools flatly (`jira_search_issues`, `cymbal_show`), so the
    token before the first underscore is the family. Grouping matters: without
    it one extension's failures scatter across a dozen targets and none ever
    reaches the recurrence threshold.
    """
    config = config or DEFAULT_ROUTE_CONFIG
    if not tool_name or tool_name in config.builtin_tools:
        return None
    mapped = config.ext_family_map.get(tool_name)
    if mapped:
        return mapped
    head = tool_name.split("_", 1)[0]
    if not head or head == tool_name and "_" not in tool_name:
        # A single-word non-builtin tool is not an extension family.
        return None
    return config.ext_family_map.get(head, head)


def repository_root(path: str | None, *, home: str | None = None) -> str:
    """Normalize a working directory to its repository root (ADR-0005).

    realpath to resolve symlinks, then up to the git toplevel, then home
    shortened for readability and portability. Case is preserved so the result
    can be copied out of a review packet and used.
    """
    if not path:
        return UNKNOWN_TARGET
    candidate = Path(path)
    try:
        candidate = candidate.resolve()
    except OSError:
        pass
    root = _git_root(candidate) or candidate
    return _shorten_home(str(root), home)


def _git_root(start: Path) -> Path | None:
    for candidate in (start, *start.parents):
        marker = candidate / ".git"
        try:
            if marker.is_dir():
                return candidate
            if marker.is_file():
                return _worktree_root(marker) or candidate
        except OSError:
            return None
    return None


def _worktree_root(marker: Path) -> Path | None:
    """A worktree's `.git` file points back at the main repository.

    Without this a repo and its worktree are two targets for one codebase, each
    accumulating evidence separately and neither reaching the recurrence
    threshold (AC-046).
    """
    try:
        match = _WORKTREE_GITDIR.search(marker.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None
    if not match:
        return None
    parts = Path(match.group(1).strip()).parts
    if "worktrees" not in parts:
        return None
    git_dir = Path(*parts[: len(parts) - 1 - parts[::-1].index("worktrees")])
    return git_dir.parent if git_dir.name == ".git" else None


def _shorten_home(path: str, home: str | None) -> str:
    base = str(home) if home is not None else str(Path.home())
    if base and path.startswith(base):
        return "~" + path[len(base) :]
    return path


def build_proposals(
    signals, *, config: RouteConfig | None = None, redactor: Redactor | None = None
) -> list[Proposal]:
    """Group signals into proposals, discarding what does not belong anywhere."""
    config = config or DEFAULT_ROUTE_CONFIG
    redactor = redactor or _TARGET_REDACTOR
    ordered = sorted(signals, key=lambda s: (s.evidence.session_id or "", s.evidence.line))

    skills = _skills_by_session(ordered)
    grouped: dict[tuple[str, str], list[Signal]] = defaultdict(list)
    corrections_seen: dict[str, int] = defaultdict(int)

    for signal in ordered:
        if signal.kind == SKILL:
            # Context for AC-022, never a proposal of its own.
            continue
        placement = (
            _route_correction(signal, skills, corrections_seen, config)
            if signal.kind == CORRECTION
            else _route_tool_signal(signal, config)
        )
        if placement is not None:
            route_name, target = placement
            grouped[(route_name, _safe_target(target))].append(signal)

    proposals = [
        Proposal(route=route, target=target, signals=members)
        for (route, target), members in grouped.items()
    ]
    proposals = [
        proposal
        for proposal in proposals
        if proposal.route != ROUTE_BACKLOG
        or len(proposal.sessions) >= config.min_backlog_sessions
    ]
    for proposal in proposals:
        proposal.summary = summarize(proposal, redactor)
    proposals.sort(key=lambda p: (ROUTES.index(p.route), p.target))
    return proposals


def _safe_target(target: str) -> str:
    """Mask a target before it becomes an identity.

    Always with the module redactor in default mode, never the run's: a target
    whose spelling changed under `--full` would fragment recurrence history and
    orphan every recorded resolution.
    """
    return _TARGET_REDACTOR.text(target) or UNKNOWN_TARGET


def _skills_by_session(signals) -> dict[str, list[tuple[int, str]]]:
    skills: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for signal in signals:
        if signal.kind == SKILL:
            skills[signal.evidence.session_id or ""].append((signal.evidence.line, signal.subject))
    for entries in skills.values():
        entries.sort()
    return skills


def _route_correction(
    signal: Signal,
    skills: dict[str, list[tuple[int, str]]],
    corrections_seen: dict[str, int],
    config: RouteConfig,
) -> tuple[str, str] | None:
    session = signal.evidence.session_id or ""
    if corrections_seen[session] >= config.max_corrections_per_session:
        return None
    corrections_seen[session] += 1

    skill = _skill_before(skills.get(session, ()), signal.evidence.line)
    if skill:
        # AC-022: the skill was loaded and the user still had to correct it.
        return (ROUTE_SKILL, skill)
    return (ROUTE_MEMORY, repository_root(signal.detail.get("cwd"), home=config.home))


def _skill_before(entries, line: int) -> str | None:
    name = None
    for skill_line, skill_name in entries:
        if skill_line < line:
            name = skill_name
        else:
            break
    return name


def _route_tool_signal(signal: Signal, config: RouteConfig) -> tuple[str, str] | None:
    if signal.kind not in (FAILURE, HANG, SILENT_EMPTY, RETRY):
        return None

    tool = signal.detail.get("tool", "")
    family = ext_family(tool, config)
    if family:
        return (ROUTE_TOOL, EXT_PREFIX + family)
    if tool in config.builtin_tools and signal.subject == tool:
        # A builtin failing on a bad path is the agent's mistake, not friction in
        # the user's setup. REQ-013 discards what it cannot classify.
        return None
    if signal.detail.get("tracked"):
        return (ROUTE_TOOL, signal.subject)
    if not signal.detail.get("backlog_eligible", True):
        return None
    ignore = DEFAULT_BACKLOG_IGNORE | set(config.extra_backlog_ignore)
    if signal.subject in ignore:
        return None
    return (ROUTE_BACKLOG, signal.subject)


def summarize(proposal: Proposal, redactor: Redactor) -> str:
    """One line a reviewer can act on, with the counts that justify it."""
    kinds = defaultdict(int)
    for signal in proposal.signals:
        kinds[signal.kind] += 1
    sessions = len(proposal.sessions)
    target = proposal.target

    if proposal.route == ROUTE_SKILL:
        return (
            f"`{target}` was loaded, then the user corrected the result "
            f"{kinds[CORRECTION]} time(s) across {sessions} session(s)"
        )
    if proposal.route == ROUTE_MEMORY:
        return (
            f"{kinds[CORRECTION]} correction(s) in `{target}` across {sessions} session(s) "
            "with no skill loaded beforehand"
        )

    parts = []
    for kind, label in ((FAILURE, "failed"), (HANG, "hung"), (SILENT_EMPTY, "returned empty")):
        if kinds[kind]:
            parts.append(f"{label} {kinds[kind]} time(s)")
    detail = ", ".join(parts) or "produced friction"
    line = f"`{target}` {detail} across {sessions} session(s)"
    retry = next((s for s in proposal.signals if s.kind == RETRY), None)
    if retry is not None:
        line += (
            f"; `{retry.detail.get('subcommand')}` was retried {retry.detail.get('attempts')} time(s) "
            f"with {retry.detail.get('flag_combinations')} different flag sets"
        )
    return redactor.text(line)
