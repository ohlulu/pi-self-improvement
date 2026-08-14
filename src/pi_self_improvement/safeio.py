"""Confined, atomic file writes (AC-001, AC-052).

Every file this package creates goes through here. It is a separate module
because `stage`, `state` and `writer` all need the same guarantee and `stage`
already imports `state` — one shared implementation, no import cycle.

Two properties, both load-bearing:

- **Confined.** A path is resolved against the output root before it is opened,
  and a symlink at the destination is refused rather than followed. Resolving
  the directory alone is not enough: a symlink planted at the *target* makes an
  ordinary `open(..., "w")` truncate a file outside the root.
- **Atomic.** Write to a temporary sibling, flush, fsync, rename. A scheduled
  job that is killed mid-write otherwise leaves half a JSON file, and the
  readers here treat unparseable as absent — which silently discards history.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


class OutputRootEscape(RuntimeError):
    """Raised when a write would land outside the output root."""


def resolve_within(root, relative: str) -> Path:
    """Resolve `relative` under `root`, refusing anything that escapes it."""
    base = Path(root).expanduser().resolve()
    candidate = (base / relative).resolve()
    if candidate != base and base not in candidate.parents:
        raise OutputRootEscape(f"refusing to write outside the output root: {candidate}")
    return candidate


def write_text(path, text: str) -> None:
    """Atomically replace `path`, never following a symlink to get there."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise OutputRootEscape(f"refusing to write through a symlink: {path}")

    tmp = path.with_name(path.name + ".tmp")
    # unlink removes the link itself, so a planted symlink cannot survive to be
    # followed by the open below.
    try:
        tmp.unlink()
    except FileNotFoundError:
        pass
    except OSError as error:  # pragma: no cover - unusual filesystem state
        raise OutputRootEscape(f"cannot clear temporary file {tmp}: {error}") from error

    descriptor = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, path)


def write_json(path, payload) -> None:
    write_text(path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def read_json(path):
    """Return the parsed object, or None when it is missing or unreadable.

    Callers that must distinguish the two — an import the user explicitly asked
    for, say — should check existence themselves and fail fast.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None


_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
