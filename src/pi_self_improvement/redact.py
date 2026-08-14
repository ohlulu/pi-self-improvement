"""The single redaction boundary (REQ-004).

Every transcript-derived string — excerpt, command, arguments, summary, cwd,
displayed path — passes through a `Redactor` before it reaches disk. Nothing
outside this module is allowed to decide what is safe to write.

Two failure modes matter, not one. A secret surviving is the obvious one. A
review packet so heavily masked that nobody reads it is the other, so the pattern
set is deliberately shape-driven: things that look like credentials are masked,
things that look like git SHAs, session ids and file paths are left alone. The
corpus test in `tests/test_redaction_corpus.py` pins both directions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .model import DEFAULT_EXCERPT_LIMIT

MASK = "[REDACTED]"
ELLIPSIS = "…"

_THREE_CLASSES = (
    re.compile(r"[a-z]"),
    re.compile(r"[A-Z]"),
    re.compile(r"[0-9]"),
)
_LOWERCASE_RUN = re.compile(r"[a-z]+")

#: Longest run of consecutive lowercase letters a token may contain and still be
#: treated as random. Measured on the real corpus: base64 credentials top out
#: around 3 (`wJalrXUtnFEMI/K7MDENG/…`), while the shapes that must survive are
#: word-structured and run far longer — `Persistence` 10, `improvement` 11.
#: Entropy was tried first and does not separate them (3.9–4.4 against 4.6–5.0).
_MAX_WORDLIKE_RUN = 5

#: Values that follow a credential-ish key but are obviously not credentials:
#: type annotations, CI permissions and shell variable references.
_NON_SECRET_VALUES = frozenset(
    {
        "any", "array", "boolean", "false", "integer", "none", "null", "number",
        "object", "optional", "read", "required", "string", "true", "void", "write",
    }
)


def _looks_random(token: str) -> bool:
    """True when a long token looks generated rather than written.

    Two guards. All three character classes must appear, which drops a lowercase
    hex git SHA and a lowercase UUID session id. And no long lowercase run, which
    drops file paths and concatenated identifiers — the false positives that
    matter, because a masked path makes evidence useless.
    """
    if not all(pattern.search(token) for pattern in _THREE_CLASSES):
        return False
    return all(len(run) <= _MAX_WORDLIKE_RUN for run in _LOWERCASE_RUN.findall(token))


def _mask_if_random(match: re.Match) -> str:
    token = match.group(0)
    return MASK if _looks_random(token) else token


def _mask_if_random_non_path(match: re.Match) -> str:
    """As `_mask_if_random`, but never fires on something slash-anchored.

    A base64 key may contain `/` but never starts or ends with one; a path
    substring of exactly the key length usually does.
    """
    token = match.group(0)
    if token.startswith("/") or token.endswith("/"):
        return token
    return _mask_if_random(match)


def _is_obviously_not_secret(value: str) -> bool:
    """`token: string`, `id-token: write`, `apiToken: ${{ … }}` are not secrets."""
    return value.lower() in _NON_SECRET_VALUES or value.startswith("$")


#: Ordered specific → generic. Each entry is (compiled pattern, replacement).
#: A replacement may be a string (whole match) or a callable.
_PATTERNS: list[tuple[re.Pattern, object]] = [
    # Private key blocks, first: they span lines and swallow everything between.
    (
        re.compile(
            r"-----BEGIN[A-Z ]*PRIVATE KEY-----.*?-----END[A-Z ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
        MASK,
    ),
    # JWT: three base64url segments.
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), MASK),
    # Authorization headers, including the `-H "Authorization: ..."` curl shape.
    (
        re.compile(r"(?i)((?:proxy-)?authorization\s*:\s*)([^\s\"']+)(\s+[^\s\"']+)?"),
        lambda m: m.group(1) + MASK,
    ),
    # Credentials embedded in a URL.
    (
        re.compile(r"\b([a-zA-Z][a-zA-Z0-9+.-]*://)([^/\s:@]+):([^/\s:@]+)@"),
        lambda m: m.group(1) + MASK + "@",
    ),
    # Provider key shapes.
    (re.compile(r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA)[A-Z0-9]{16}\b"), MASK),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), MASK),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), MASK),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"), MASK),
    (re.compile(r"\b(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9]{10,}\b"), MASK),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"), MASK),
    # An AWS secret access key is exactly 40 base64 characters. `/` has to stay in
    # the character class because real keys contain it, which means path
    # substrings of exactly 40 characters reach this rule too; the word-run guard
    # is what keeps `DingKit/Persistence/Migration1BaseSchema` readable.
    (re.compile(r"\b[A-Za-z0-9+/]{40}\b"), _mask_if_random_non_path),
    # `TOKEN=…`, `password: …`, `api_key = '…'`. The prefix is intentionally loose
    # so `DEPLOY_TOKEN` and `client_secret` both match.
    (
        re.compile(
            r"(?i)([A-Za-z0-9_.-]*(?:password|passwd|secret|token|api[_-]?key|apikey|credentials?)s?)"
            r"(\s*[:=]\s*)[\"']?([^\s\"',;]+)"
        ),
        lambda m: m.group(1) + m.group(2) + (m.group(3) if _is_obviously_not_secret(m.group(3)) else MASK),
    ),
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), MASK),
    # Phone numbers: international with a leading +, and dash-grouped local forms.
    (re.compile(r"\+\d[\d\s().-]{7,}\d"), MASK),
    (re.compile(r"\b\d{4}-\d{3}-\d{3}\b"), MASK),
    # Generic long opaque token. `/` and `-` are excluded from the run so that
    # paths and dash-segmented ids break into short pieces instead of matching
    # whole, and `=` is allowed only as trailing base64 padding — mid-token it
    # glues `HEAD=<sha>` into one uppercase-bearing run that looks random.
    (re.compile(r"\b[A-Za-z0-9_+]{32,}={0,2}"), _mask_if_random),
]


class Redactor:
    """Masks secret shapes and shortens excerpts.

    `full=True` turns the whole boundary off: text is returned verbatim and
    `local_only` becomes True so every writer can mark its output as unsafe to
    share (AC-006).
    """

    def __init__(
        self,
        *,
        full: bool = False,
        excerpt_limit: int = DEFAULT_EXCERPT_LIMIT,
        extra_patterns: Iterable[str] = (),
        home: str | Path | None = None,
    ):
        self._full = bool(full)
        self.excerpt_limit = int(excerpt_limit)
        self._home = str(home) if home is not None else str(Path.home())
        self._extra = [self._compile(pattern) for pattern in extra_patterns]

    @staticmethod
    def _compile(pattern: str) -> re.Pattern:
        try:
            return re.compile(pattern)
        except re.error as error:
            raise ValueError(f"invalid redaction pattern {pattern!r}: {error}") from error

    @property
    def local_only(self) -> bool:
        """True when output keeps original text and must not leave the machine."""
        return self._full

    def text(self, value) -> str:
        """Mask a free-text string. No shortening — used for summaries and prose."""
        if value is None:
            return ""
        value = value if isinstance(value, str) else str(value)
        if self._full:
            return value
        for pattern, replacement in _PATTERNS:
            value = pattern.sub(replacement, value)
        for pattern in self._extra:
            value = pattern.sub(MASK, value)
        return value

    def excerpt(self, value) -> str:
        """Mask, collapse whitespace, then shorten to the limit (AC-004).

        Masking happens before shortening on purpose: shortening first could cut a
        secret in half and leave the front of it in the output.
        """
        masked = self.text(value)
        if self._full:
            return masked
        collapsed = " ".join(masked.split())
        if len(collapsed) <= self.excerpt_limit:
            return collapsed
        return collapsed[: max(self.excerpt_limit - 1, 0)] + ELLIPSIS

    def command(self, value) -> str:
        """A command line is display data, so it is shortened like an excerpt."""
        return self.excerpt(value)

    def arguments(self, value):
        """Recursively mask a tool call's arguments."""
        if self._full:
            return value
        if isinstance(value, dict):
            return {self.text(key): self.arguments(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.arguments(item) for item in value]
        if isinstance(value, str):
            return self.excerpt(value)
        return value

    def path(self, value) -> str:
        """Mask a path and shorten the home prefix for readability."""
        if value is None:
            return ""
        text = value if isinstance(value, str) else str(value)
        if not self._full and self._home and text.startswith(self._home):
            text = "~" + text[len(self._home) :]
        return self.text(text)


@dataclass(frozen=True)
class CanaryHit:
    path: Path
    canary: str


def scan_for_canaries(root: str | Path, canaries: Iterable[str]) -> list[CanaryHit]:
    """Search every file under `root` for any of `canaries` (AC-042).

    The last line of defence: it does not care which module wrote the file or
    whether that module remembered to redact. A missing root is not an error —
    a scan that found nowhere to look found nothing.
    """
    base = Path(root)
    needles = [needle for needle in canaries if needle]
    if not base.is_dir() or not needles:
        return []

    hits: list[CanaryHit] = []
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        try:
            content = path.read_bytes().decode("utf-8", errors="ignore")
        except OSError:
            continue
        for needle in needles:
            if needle in content:
                hits.append(CanaryHit(path=path, canary=needle))
    return hits
