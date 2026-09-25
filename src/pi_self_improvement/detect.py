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

from . import cues
from .model import KIND_BASH_EXECUTION, Evidence, SessionSummary, ToolCall
from .redact import Redactor

FAILURE = "failure"
HANG = "hang"
SILENT_EMPTY = "silent_empty"
RETRY = "retry"
SKILL = "skill"
CORRECTION = "correction"

#: DEC-006: a `read` whose path ends here loaded a skill. This is the spec
#: default because it is pi core behaviour; the richer `context:skill_loaded`
#: entry comes from a personal extension and is opt-in via config.
SKILL_FILENAME = "SKILL.md"

#: Tool names that run a shell command, so the executable rather than the tool is
#: what evidence is about.
_SHELL_TOOLS = frozenset({"bash", "sh", "shell"})

#: Skipped when looking for the executable: they wrap the command that matters.
_WRAPPERS = frozenset(
    {"sudo", "env", "command", "nohup", "nice", "time", "timeout", "gtimeout", "caffeinate", "xargs"}
)

#: Shell built-ins that are rarely the point of a command line.
_SHELL_NOISE = frozenset(
    {"cd", "export", "set", "source", ".", "unset", "pushd", "popd", "read"}
)

#: Control-flow words that prefix a command: the program is a later token in the
#: same segment. Without this, `for f in *; do demo-cli run; done` attributes the
#: failure to `do`, so a CLI that only ever runs inside a loop is never seen.
_CONTROL_PREFIXES = frozenset({"do", "then", "else", "elif", "if", "while", "until", "!"})

#: Control-flow words that never precede a program: `for i in 1 2 3` names a
#: variable, and `done` or `fi` closes a block. The segment holds no executable.
#: `break` and `continue` end a loop's last unit, which would otherwise take the
#: attribution from the program the loop runs.
_CONTROL_TERMINALS = frozenset(
    {"for", "done", "fi", "esac", "case", "select", "in", "break", "continue"}
)

#: Source-code keywords. A transcript sometimes records a code fragment where a
#: command belongs, and `const x = 1` is not a program called `const`.
_CODE_KEYWORDS = frozenset(
    {
        "const",
        "let",
        "var",
        "function",
        "class",
        "def",
        "import",
        "return",
        "async",
        "await",
        "new",
    }
)

#: Data producers at the head of a pipeline. `printf x | demo-cli list` is about
#: demo-cli, so attributing the failure to printf discards a tracked CLI.
_PIPE_PRODUCERS = frozenset({"echo", "printf", "cat", "yes", "seq"})

#: Skipped along with the wrapper itself: these options take a value, and the
#: value is not the program. Without this, `env -u GH_TOKEN demo-cli` attributes
#: the command to GH_TOKEN.
_WRAPPER_VALUE_OPTIONS = frozenset({"-u", "--unset", "-C", "--chdir", "-S", "--split-string"})

_ENV_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
#: Fast path for the common command with no quoting to respect. The capture
#: keeps each separator, because which one joins two segments decides whose
#: exit status the shell reports.
_SEGMENT_SPLIT = re.compile(r"\s*(&&|\|\||;|\||\n)\s*")

#: Separators after which the next command runs unconditionally. The status of
#: `a; b` is always b's, so these bound the units a failure is attributed to.
_SEQUENCE_SEPARATORS = frozenset({";", "\n"})

_BLOCK_OPENERS = frozenset({"for", "while", "until", "if", "case", "select"})
_BLOCK_CLOSERS = frozenset({"done", "fi", "esac"})

#: A newline separates commands like `;` does, so a multi-line script is a
#: sequence rather than one command named after its first line. Splitting on it
#: is only safe once quoting is respected: `echo "first\nnode broken"` would
#: otherwise be read as running `node`.
_SEPARATORS = ("&&", "||", ";", "|", "\n")

#: A heredoc body is data, not commands. Without stripping it, a commit message
#: containing "...the boring part; the migration also..." is split on the
#: semicolon and attributed to a program named `the`, and the `EOF` terminator
#: becomes a program of its own.
_HEREDOC_START = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")

#: A program name, as opposed to a comment marker, a test bracket or a quoted
#: string. Without this, `# Check the thing` attributes evidence to `#`.
_PLAUSIBLE_EXECUTABLE = re.compile(r"^[A-Za-z0-9_.+-]+$")

#: A subcommand, as opposed to a path, a redirect or a search pattern. `grep
#: "enum" src` has an argument here, not a subcommand.
_PLAUSIBLE_SUBCOMMAND = re.compile(r"^[A-Za-z][A-Za-z0-9._-]*$")

#: Verbs that mean "this call went to fetch something".
DEFAULT_FETCH_VERBS = (
    "list", "ls", "get", "show", "search", "query", "fetch", "find", "describe",
    "dump", "export", "view", "read", "inspect", "count", "status", "log", "logs",
)

#: Tools and executables where an empty answer is the answer, not friction.
DEFAULT_SILENT_EMPTY_IGNORE = (
    "grep", "rg", "ag", "ack", "find", "fd", "ls", "locate", "which", "read", "glob",
)

_EMPTY_PAYLOADS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"^\[\s*\]$",
        r"^\{\s*\}$",
        r"^null$",
        r"^none$",
        r"^\(empty\)$",
        r"^0 rows?\b",
        r"^no results?\b",
        r"^no matches?\b",
        r"^no items?\b",
        r"^nothing found\b",
    )
)

#: An agent noticing the emptiness in a later turn. Bilingual, because half the
#: corpus is Traditional Chinese and an English-only check would call every
#: acknowledged empty result silent.
_ACKNOWLEDGEMENTS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bempty\b",
        r"\bno results?\b",
        r"\bno matches?\b",
        r"\bno items?\b",
        r"\bnothing\b",
        r"\bnone (?:found|returned|matched)\b",
        r"\bdid ?n[o']t find\b",
        r"\bzero\b",
        r"沒有",
        r"找不到",
        r"空的",
        r"無結果",
        r"無資料",
    )
)

#: AC-012: three different flag combinations on one subcommand.
_RETRY_COMBINATIONS = 3

#: Programs whose exit status is an answer rather than an error: `grep` exits 1
#: for "no match", `test` for "false", `diff` for "they differ". AC-014 already
#: says an empty search result is not friction; the same is true when the shell
#: reports it as a non-zero status.
_STATUS_AS_ANSWER = frozenset({"grep", "rg", "ag", "ack", "find", "fd", "test", "diff", "cmp", "["})

#: Pi's placeholder for a command that printed nothing, plus the status line it
#: appends. Neither is output the user wrote or a program produced.
_NO_OUTPUT = re.compile(r"\(no output\)|Command exited with code \d+", re.IGNORECASE)

#: DEC-009 scaffold markers. Deliberately only two.
#:
#: Pi expresses injected context as its own record type, so the strongest filter
#: is the data model, not a regex — `custom_message` never becomes a user message
#: in the first place (AC-020). What is left are the two shapes measured *inside*
#: user message bodies: an orchestrator steering block's box-drawing rule, and a
#: subagent task seed. Marker lists that also contain `[Project docs index]` or
#: `Cymbal suggests:` misfire the moment someone talks *about* those injections
#: (AC-045), and they buy nothing, because those arrive as separate records.
_SCAFFOLD_SEPARATOR = re.compile("\u2500{10,}")
_SCAFFOLD_TASK_SEED = re.compile(r"^\s*Task:")

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


@dataclass(frozen=True)
class DetectConfig:
    """Detector knobs. `config.py` (T022) builds one of these; defaults stay generic."""

    tracked_clis: tuple[str, ...] = ()
    tracked_cli_suffix: tuple[str, ...] = ("-cli",)
    detect_silent_empty: bool = True
    silent_empty_fetch_verbs: tuple[str, ...] = DEFAULT_FETCH_VERBS
    silent_empty_ignore: tuple[str, ...] = DEFAULT_SILENT_EMPTY_IGNORE
    #: Empty by default (DEC-006/AC-044): these entries come from personal
    #: extensions, and a public default keyed on one would silently find nothing
    #: on anyone else's machine.
    skill_loaded_custom_types: tuple[str, ...] = ()
    cue_packs: tuple[cues.CuePack, ...] = cues.BUILTIN_PACKS
    extra_scaffold_markers: tuple[str, ...] = ()
    #: REQ-012: a subagent's failures stay out of the backlog unless asked for.
    include_subagent_failures: bool = False


DEFAULT_CONFIG = DetectConfig()


def _analyze(command: str | None) -> tuple[str | None, str | None, tuple[str, ...]]:
    """Split a command line into (executable, subcommand, distinct flags).

    Whitespace tokenisation on purpose: `shlex` raises on the unbalanced quoting
    that real transcripts are full of, and this only needs the shape.
    """
    if not command or not command.strip():
        return None, None, ()

    # The last unit that runs a real program is the one whose status the shell
    # reports: `trash x && make test; ls out` failing is about `ls`, and naming
    # the first program filed that failure against `trash`.
    units = [
        [parsed for parsed in map(_analyze_segment, unit) if parsed is not None]
        for unit in _sequential_units(_split_segments(_normalize(command)))
    ]
    for unit in reversed(units):
        for parsed in unit:
            if parsed[0] not in _SHELL_NOISE and parsed[0] not in _PIPE_PRODUCERS:
                return parsed
    first = next((parsed for unit in units for parsed in unit), None)
    return first if first is not None else (None, None, ())


def _normalize(command: str) -> str:
    """Heredoc bodies dropped and backslash continuations joined into one line."""
    return _strip_heredocs(command).replace("\\\n", " ").strip()


def _sequential_units(segments: list[tuple[str, str]]) -> list[list[str]]:
    """Group segments into units separated by `;` or a newline.

    A `for`, `while`, `if` or `case` block is one unit however many `;` it
    holds: in `ls d && for f in *; do cat $f; done | head`, the `head` does not
    run after the `ls` so much as after the whole block.
    """
    units: list[list[str]] = [[]]
    depth = 0
    for separator, segment in segments:
        if separator in _SEQUENCE_SEPARATORS and depth == 0:
            units.append([])
        units[-1].append(segment)
        keyword = _block_keyword(segment)
        if keyword in _BLOCK_OPENERS:
            depth += 1
        elif keyword in _BLOCK_CLOSERS:
            depth = max(depth - 1, 0)
    return units


def _block_keyword(segment: str) -> str | None:
    for token in segment.split():
        if token not in ("do", "then", "else", "!"):
            return token
    return None


def _status_owner(command: str | None) -> str | None:
    """The program whose exit status the shell reports, when that is knowable.

    That is the last element of the last pipeline. A `&&` makes it unknowable:
    in `make build && rg x`, a status of 1 may be make's. Only a chain of shell
    noise such as `cd dir && rg x` is exempt, since noise is not what failed.
    """
    if not command or not command.strip():
        return None
    segments = _split_segments(_normalize(command))
    last = _analyze_segment(segments[-1][1])
    if last is None:
        return None
    start = len(segments) - 1
    while start > 0 and segments[start][0] == "|":
        start -= 1
    if start < len(segments) - 1 and "pipefail" in command:
        # Any element of the pipeline may own the status.
        return None
    index = start
    while index > 0 and segments[index][0] not in _SEQUENCE_SEPARATORS:
        preceding = _analyze_segment(segments[index - 1][1])
        if preceding is None or preceding[0] not in _SHELL_NOISE:
            return None
        index -= 1
    return last[0]


def _split_segments(command: str) -> list[tuple[str, str]]:
    """Split a command line on shell separators that are not inside quotes.

    Each segment comes with the separator before it, `""` for the first.
    """
    if "'" not in command and '"' not in command and "$(" not in command:
        parts = _SEGMENT_SPLIT.split(command)
        return list(zip([""] + parts[1::2], parts[0::2]))

    segments: list[tuple[str, str]] = []
    separator_before = ""
    current: list[str] = []
    quote = ""
    depth = 0
    index = 0
    while index < len(command):
        char = command[index]
        if quote:
            if char == quote:
                quote = ""
            current.append(char)
            index += 1
            continue
        if char in "'\"":
            quote = char
            current.append(char)
            index += 1
            continue
        if command.startswith("$(", index):
            # A command substitution is one value: the `|` in
            # `X=$(find . | head -1)` does not start a new command.
            depth += 1
            current.append(command[index : index + 2])
            index += 2
            continue
        if depth and char == ")":
            depth -= 1
            current.append(char)
            index += 1
            continue
        if depth:
            current.append(char)
            index += 1
            continue
        for separator in _SEPARATORS:
            if command.startswith(separator, index):
                segments.append((separator_before, "".join(current).strip()))
                separator_before = separator
                current = []
                index += len(separator)
                break
        else:
            current.append(char)
            index += 1
    segments.append((separator_before, "".join(current).strip()))
    return segments


def _strip_heredocs(command: str) -> str:
    """Drop every heredoc body and terminator, keeping the command line itself."""
    if "<<" not in command:
        return command

    lines = command.split("\n")
    kept: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        kept.append(line)
        index += 1
        match = _HEREDOC_START.search(line)
        if match is None:
            continue
        terminator = match.group(2)
        while index < len(lines) and lines[index].strip() != terminator:
            index += 1
        index += 1  # the terminator line is shell syntax, not a command
    return "\n".join(kept)


def _opening_quote(token: str) -> str:
    """The quote an assignment opens and does not close in this token, if any."""
    value = token.split("=", 1)[1]
    if not value or value[0] not in "'\"":
        return ""
    quote = value[0]
    return "" if value[1:].endswith(quote) else quote


def _opens_substitution(token: str) -> bool:
    """Whether an assignment opens a `$(...)` value it does not close.

    `APP_PATH=$(find .derivedData -name x)` is one value. Reading on used to
    take the substitution's first argument as the program.
    """
    value = token.split("=", 1)[1]
    return value.startswith("$(") and value.count("(") > value.count(")")


def _analyze_segment(segment: str) -> tuple[str, str | None, tuple[str, ...]] | None:
    executable: str | None = None
    subcommand: str | None = None
    settled = False
    flags: set[str] = set()

    skip_next = False
    open_quote = ""
    open_substitution = False
    for token in segment.split():
        if executable is None:
            if open_quote:
                # Inside a quoted assignment value: `GG="git --git-dir x/.git"`
                # is one value, and its last path used to be read as a program.
                if token.endswith(open_quote):
                    open_quote = ""
                continue
            if open_substitution:
                if token.count(")") > token.count("("):
                    open_substitution = False
                continue
            if skip_next:
                skip_next = False
                continue
            if token in _WRAPPER_VALUE_OPTIONS:
                skip_next = True
                continue
            if _ENV_ASSIGNMENT.match(token):
                open_quote = _opening_quote(token)
                open_substitution = not open_quote and _opens_substitution(token)
                continue
            if token.isdigit() or token.startswith("-"):
                continue
            if ">" in token or "<" in token:
                # A redirection is shell syntax: `gtimeout --version 2>/dev/null`
                # used to be attributed to a program named `null`.
                continue
            bare = token.rsplit("/", 1)[-1]
            if bare in _WRAPPERS or bare in _CONTROL_PREFIXES:
                continue
            if bare in _CONTROL_TERMINALS or bare in _CODE_KEYWORDS:
                # Shell syntax or a code fragment, not a program. Give up on the
                # segment rather than promoting the next token.
                return None
            if not _PLAUSIBLE_EXECUTABLE.match(bare):
                # Not a program: a comment marker, `[`, a quoted fragment. Give up
                # on this segment rather than promoting the next token.
                return None
            executable = bare
            continue
        if token.startswith("-"):
            flags.add(token)
        elif not settled:
            # The subcommand is the first non-flag token or there is none. Scanning
            # further finds arguments: `grep "enum Foo" src` would yield `src`.
            settled = True
            if _PLAUSIBLE_SUBCOMMAND.match(token):
                subcommand = token

    if executable is None:
        return None
    return executable, subcommand, tuple(sorted(flags))


def executable_of(command: str | None) -> str | None:
    """The program a command line actually runs, wrappers and env prefixes aside."""
    return _analyze(command)[0]


def is_tracked(name: str | None, config: DetectConfig) -> bool:
    """REQ-007: the config list decides, the suffix pattern is the fallback."""
    if not name:
        return False
    if name in config.tracked_clis:
        return True
    return any(suffix and name.endswith(suffix) for suffix in config.tracked_cli_suffix)


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
    troubled: set[int] = set()

    for call in summary.tool_calls:
        signal = _detect_call(summary, call, redactor, config)
        if signal is None:
            continue
        signals.append(signal)
        if signal.kind in (FAILURE, HANG):
            troubled.add(id(call))

    signals.extend(_detect_retries(summary, redactor, config, troubled))
    signals.extend(_detect_skills(summary, redactor, config))
    signals.extend(_detect_corrections(summary, redactor, config))
    return signals


def _detect_skills(
    summary: SessionSummary, redactor: Redactor, config: DetectConfig
) -> list[Signal]:
    """REQ-009: a skill's instructions were loaded into this session."""
    signals: list[Signal] = []
    for call in summary.tool_calls:
        if call.tool_name != "read" or call.is_error is True:
            # A read that failed loaded nothing.
            continue
        if not call.matched:
            # A dangling call never returned, so nothing was loaded and AC-041
            # says it produces no evidence. It is already in the counts.
            continue
        path = call.arguments.get("path")
        if not isinstance(path, str) or not path.endswith(SKILL_FILENAME):
            continue
        name = _skill_name_from(path)
        if name:
            signals.append(_context_signal(SKILL, name, summary, call.line, path, redactor))

    wanted = set(config.skill_loaded_custom_types)
    for entry in summary.custom_entries:
        if entry.custom_type not in wanted:
            continue
        name = entry.data.get("name") or _skill_name_from(str(entry.data.get("path") or ""))
        if name:
            signals.append(
                _context_signal(
                    SKILL, str(name), summary, entry.line, str(entry.data.get("path") or name), redactor
                )
            )
    return signals


def _skill_name_from(path: str) -> str | None:
    """`.../skills/commit/SKILL.md` names the skill `commit`."""
    parts = [part for part in path.replace("\\", "/").split("/") if part]
    if len(parts) < 2:
        return None
    return parts[-2]


def is_scaffold(text: str, config: DetectConfig | None = None) -> bool:
    """REQ-011: text that the harness or an extension injected, not the user.

    The loop has to eat its own dog food here. An injection its own harness makes
    will otherwise be mined as user friction, and the tool ends up filing
    proposals against itself.
    """
    config = config or DEFAULT_CONFIG
    if not text:
        return False
    if _SCAFFOLD_SEPARATOR.search(text):
        return True
    if _SCAFFOLD_TASK_SEED.match(text):
        return True
    return any(marker and marker in text for marker in config.extra_scaffold_markers)


def _detect_corrections(
    summary: SessionSummary, redactor: Redactor, config: DetectConfig
) -> list[Signal]:
    """REQ-010: the user telling the agent it got something wrong.

    Only a message that follows an assistant turn can be a correction — there is
    nothing to correct before the agent has answered (AC-016).

    A subagent session has no user messages at all: what sits in the `user` role
    is a prompt the orchestrating agent wrote (REQ-012). Reading those as
    corrections would have the loop mine its own instructions.
    """
    if not summary.assistant_turns or not config.cue_packs or summary.is_subagent:
        return []
    first_answer = min(turn.line for turn in summary.assistant_turns)

    signals: list[Signal] = []
    for message in summary.user_messages:
        if message.line <= first_answer or is_scaffold(message.text, config):
            continue
        hit = cues.find_cue(message.text, config.cue_packs)
        if hit is None:
            continue
        signals.append(
            Signal(
                kind=CORRECTION,
                subject=summary.cwd or "<unknown>",
                evidence=Evidence(
                    source=CORRECTION,
                    path=redactor.path(summary.path),
                    line=message.line,
                    excerpt=redactor.excerpt(message.text),
                    timestamp=redactor.text(message.timestamp or "") or None,
                    session_id=redactor.text(summary.session_id or "") or None,
                    origin=summary.origin,
                ),
                detail={
                    "pack": hit.pack,
                    "cue": hit.cue,
                    "strength": hit.strength,
                    "topic": hit.topic,
                    "cwd": summary.cwd,
                },
            )
        )
    return signals


def _context_signal(
    kind: str, subject: str, summary: SessionSummary, line: int, excerpt: str, redactor: Redactor
) -> Signal:
    return Signal(
        kind=kind,
        subject=subject,
        evidence=Evidence(
            source=kind,
            path=redactor.path(summary.path),
            line=line,
            excerpt=redactor.path(excerpt),
            session_id=redactor.text(summary.session_id or "") or None,
            origin=summary.origin,
        ),
        detail={"name": subject},
    )


def _detect_call(
    summary: SessionSummary, call: ToolCall, redactor: Redactor, config: DetectConfig
) -> Signal | None:
    if not call.matched:
        # A call with no result never completed as far as the transcript knows.
        return None

    stall = _hang_pattern(call)
    if stall is not None:
        # A stall is one piece of friction, not a hang plus a failure.
        return _signal(HANG, summary, call, redactor, config, focus=stall)
    if _is_failure(call):
        return _signal(FAILURE, summary, call, redactor, config)
    if _is_silent_empty(summary, call, config):
        return _signal(SILENT_EMPTY, summary, call, redactor, config)
    return None


def _is_silent_empty(summary: SessionSummary, call: ToolCall, config: DetectConfig) -> bool:
    """REQ-008: a call that went looking for data, found none, and nobody said so."""
    if not config.detect_silent_empty:
        return False
    if call.is_error is not False:
        # Errors are already reported as failures; only a clean call can be silent.
        return False
    if not _is_empty_payload(call.result_text):
        return False

    executable, subcommand, _ = _analyze(call.command)
    is_shell = call.kind == KIND_BASH_EXECUTION or call.tool_name in _SHELL_TOOLS
    ignore = {name.lower() for name in config.silent_empty_ignore}
    if (executable or "").lower() in ignore or call.tool_name.lower() in ignore:
        return False
    if not _has_data_intent(call, executable, subcommand, is_shell, config):
        return False
    return not _acknowledged_after(summary, call)


def _is_empty_payload(text: str) -> bool:
    stripped = (text or "").strip()
    if not stripped:
        return True
    return any(pattern.match(stripped) for pattern in _EMPTY_PAYLOADS)


def _has_data_intent(
    call: ToolCall,
    executable: str | None,
    subcommand: str | None,
    is_shell: bool,
    config: DetectConfig,
) -> bool:
    verbs = {verb.lower() for verb in config.silent_empty_fetch_verbs}
    if is_shell:
        command = call.command or ""
        if "--json" in command or "-o json" in command or "--format json" in command:
            return True
        return (subcommand or "").lower() in verbs or (executable or "").lower() in verbs
    # An extension tool's flat name carries its verb: `demoext_search_items`.
    return bool(verbs & set(re.split(r"[_\-]", call.tool_name.lower())))


def _acknowledged_after(summary: SessionSummary, call: ToolCall) -> bool:
    """Did any later agent turn notice the emptiness?"""
    boundary = call.evidence_line
    for assistant_turn in summary.assistant_turns:
        if assistant_turn.line <= boundary:
            continue
        if any(pattern.search(assistant_turn.text) for pattern in _ACKNOWLEDGEMENTS):
            return True
    return False


def _detect_retries(
    summary: SessionSummary, redactor: Redactor, config: DetectConfig, troubled: set[int]
) -> list[Signal]:
    """REQ-007 retry-before-success: one subcommand tried several ways, one failing.

    Flag *variation* is the signal, not repetition: running the identical command
    three times is one habit, while three different flag sets is someone hunting
    for the incantation that works.
    """
    groups: dict[tuple[str, str], list[ToolCall]] = {}
    combos: dict[tuple[str, str], set[tuple[str, ...]]] = {}
    for call in summary.tool_calls:
        if not call.command:
            continue
        executable, subcommand, flags = _analyze(call.command)
        if not executable or not subcommand:
            continue
        if not is_tracked(executable, config):
            # REQ-007 scopes tool-route attribution to tracked CLIs. Without that
            # scope, `git diff` with three flag sets reads as a retry when it is
            # just someone looking around.
            continue
        key = (executable, subcommand)
        groups.setdefault(key, []).append(call)
        combos.setdefault(key, set()).add(flags)

    signals: list[Signal] = []
    for (executable, subcommand), calls in groups.items():
        if len(combos[(executable, subcommand)]) < _RETRY_COMBINATIONS:
            continue
        failing = [call for call in calls if id(call) in troubled]
        if not failing:
            continue
        anchor = failing[-1]
        signals.append(
            Signal(
                kind=RETRY,
                subject=executable,
                evidence=_evidence(RETRY, summary, anchor, redactor),
                detail={
                    "executable": executable,
                    "subcommand": subcommand,
                    "attempts": len(calls),
                    "flag_combinations": len(combos[(executable, subcommand)]),
                    "tracked": is_tracked(executable, config),
                    "command": redactor.command(anchor.command),
                },
            )
        )
    return signals


def _is_answer_not_failure(call: ToolCall) -> bool:
    """True when a non-zero status is the program's answer, not a problem.

    Measured on the real corpus: `grep` alone accounted for 388 backlog entries
    across 229 sessions, essentially all of them "exit 1, no output" — a search
    that found nothing. Filing that as friction buries the real entries.
    """
    if call.exit_code != 1:
        return False
    owner = _status_owner(call.command)
    if owner is not None:
        # Earlier commands in the line may have printed plenty, so the output
        # cannot be required to be empty. The owner's own diagnostic can be
        # required to be absent: `grep: x: No such file` is a real failure.
        return owner in _STATUS_AS_ANSWER and not re.search(
            rf"(?m)^{re.escape(owner)}: ", call.result_text or ""
        )
    executable = executable_of(call.command) or call.tool_name
    if executable not in _STATUS_AS_ANSWER:
        return False
    return not _NO_OUTPUT.sub("", call.result_text).strip()


def _is_failure(call: ToolCall) -> bool:
    if _is_answer_not_failure(call):
        return False
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


def _subject_of(call: ToolCall, config: DetectConfig) -> str:
    """What the signal is about: the executable for a shell call, else the tool.

    REQ-013 keys both the tool and backlog routes on the executable name, so a
    `bash` subject would collapse every shell failure onto one meaningless target.
    """
    if call.kind == KIND_BASH_EXECUTION or call.tool_name in _SHELL_TOOLS:
        executable = executable_of(call.command)
        if executable:
            return executable
    return call.tool_name


def _signal(
    kind: str,
    summary: SessionSummary,
    call: ToolCall,
    redactor: Redactor,
    config: DetectConfig,
    focus: re.Pattern | None = None,
) -> Signal:
    subject = _subject_of(call, config)
    detail = {
        "tool": call.tool_name,
        "tracked": is_tracked(subject, config),
        # REQ-012: routing reads this to keep a subagent's failures out of the
        # backlog. The signal is still detected and still counted.
        "backlog_eligible": not summary.is_subagent or config.include_subagent_failures,
    }
    if call.command:
        detail["command"] = redactor.command(call.command)
    if call.exit_code is not None:
        detail["exit_code"] = call.exit_code
    if call.kind == KIND_BASH_EXECUTION:
        detail["bash_execution"] = True
    if call.cancelled:
        detail["cancelled"] = True
    return Signal(
        kind=kind,
        subject=subject,
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
        timestamp=redactor.text(call.result_timestamp or call.timestamp or "") or None,
        session_id=redactor.text(summary.session_id or "") or None,
        origin=summary.origin,
    )
