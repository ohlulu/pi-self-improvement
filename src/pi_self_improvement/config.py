"""Load, validate and apply the JSON config (REQ-019).

Defaults stay generic. Everything that depends on one person's extensions or
workflow lives here and nowhere else, because a default keyed on a personal
extension silently finds nothing on anyone else's machine (DEC-006).

Validation is strict on purpose. A misspelled key that is quietly ignored looks
exactly like a key that had no effect, and the user has no way to tell which.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import cues
from .detect import DetectConfig
from .redact import Redactor
from .route import DEFAULT_BUILTIN_TOOLS, RouteConfig

DEFAULT_OUTPUT_ROOT = "~/.pi-self-improvement"
CONFIG_FILE = "config.json"

#: Keys accepted in config.json, grouped by the shape each one must have.
_LIST_KEYS = (
    "extra_session_roots",
    "tracked_clis",
    "tracked_cli_suffix",
    "extra_scaffold_markers",
    "extra_redaction_patterns",
    "extra_backlog_ignore",
    "skill_loaded_custom_types",
    "silent_empty_fetch_verbs",
    "silent_empty_ignore",
)
_BOOL_KEYS = ("include_subagent_failures", "detect_silent_empty")
_MAP_KEYS = ("ext_family_map", "cue_packs")
KNOWN_KEYS = frozenset(_LIST_KEYS + _BOOL_KEYS + _MAP_KEYS)


class ConfigError(ValueError):
    """A config file that cannot be applied as written."""


@dataclass(frozen=True)
class Config:
    raw: dict = field(default_factory=dict)
    detect: DetectConfig = field(default_factory=DetectConfig)
    route: RouteConfig = field(default_factory=RouteConfig)
    extra_redaction_patterns: tuple[str, ...] = ()
    #: REQ-002: scanned in addition to the default pi sessions root, never
    #: instead of it.
    extra_session_roots: tuple[str, ...] = ()

    def session_roots(self, home=None) -> list:
        from .parse import default_sessions_root

        roots = [default_sessions_root(home)]
        roots.extend(Path(root).expanduser() for root in self.extra_session_roots)
        return roots

    def redactor(self, *, full: bool = False, home=None) -> Redactor:
        return Redactor(full=full, extra_patterns=self.extra_redaction_patterns, home=home)

    @classmethod
    def load(cls, path=None, *, output_root=None) -> Config:
        """Read a config file. A missing file is not an error — it is the default."""
        target = Path(path) if path else Path(output_root or DEFAULT_OUTPUT_ROOT).expanduser() / CONFIG_FILE
        if not target.is_file():
            if path:
                raise ConfigError(f"config file not found: {target}")
            return cls()
        try:
            with open(target, encoding="utf-8") as handle:
                payload = json.load(handle)
        except json.JSONDecodeError as error:
            raise ConfigError(f"{target} is not valid JSON: {error}") from error
        except OSError as error:
            raise ConfigError(f"cannot read {target}: {error}") from error
        return cls.from_dict(payload)

    @classmethod
    def from_dict(cls, payload) -> Config:
        if not isinstance(payload, dict):
            raise ConfigError("config must be a JSON object")
        _validate(payload)

        defaults = DetectConfig()
        detect = DetectConfig(
            tracked_clis=_strings(payload, "tracked_clis", defaults.tracked_clis),
            tracked_cli_suffix=_strings(payload, "tracked_cli_suffix", defaults.tracked_cli_suffix),
            detect_silent_empty=bool(
                payload.get("detect_silent_empty", defaults.detect_silent_empty)
            ),
            silent_empty_fetch_verbs=_strings(
                payload, "silent_empty_fetch_verbs", defaults.silent_empty_fetch_verbs
            ),
            silent_empty_ignore=_strings(
                payload, "silent_empty_ignore", defaults.silent_empty_ignore
            ),
            skill_loaded_custom_types=_strings(
                payload, "skill_loaded_custom_types", defaults.skill_loaded_custom_types
            ),
            cue_packs=cues.build_packs(payload.get("cue_packs")),
            extra_scaffold_markers=_strings(
                payload, "extra_scaffold_markers", defaults.extra_scaffold_markers
            ),
            include_subagent_failures=bool(
                payload.get("include_subagent_failures", defaults.include_subagent_failures)
            ),
        )
        route = RouteConfig(
            ext_family_map=dict(payload.get("ext_family_map", {})),
            builtin_tools=DEFAULT_BUILTIN_TOOLS,
            extra_backlog_ignore=_strings(payload, "extra_backlog_ignore", ()),
        )
        return cls(
            raw=dict(payload),
            detect=detect,
            route=route,
            extra_redaction_patterns=_strings(payload, "extra_redaction_patterns", ()),
            extra_session_roots=_strings(payload, "extra_session_roots", ()),
        )


def _validate(payload: dict) -> None:
    unknown = sorted(set(payload) - KNOWN_KEYS)
    if unknown:
        hints = ", ".join(_suggest(key) for key in unknown)
        raise ConfigError(f"unknown config key(s): {hints}")

    for key in _LIST_KEYS:
        if key not in payload:
            continue
        value = payload[key]
        # A bare string iterates character by character, so "demo-cli" would
        # become ('d','e','m','o',...) and match nothing. Reject it loudly.
        if isinstance(value, str) or not isinstance(value, (list, tuple)):
            raise ConfigError(f"{key} must be a list of strings, got {type(value).__name__}")
        for item in value:
            if not isinstance(item, str):
                raise ConfigError(f"{key} must contain only strings, found {type(item).__name__}")

    for key in _BOOL_KEYS:
        if key in payload and not isinstance(payload[key], bool):
            raise ConfigError(f"{key} must be true or false")

    for key in _MAP_KEYS:
        if key in payload and not isinstance(payload[key], dict):
            raise ConfigError(f"{key} must be an object")

    for name, target in payload.get("ext_family_map", {}).items():
        if not isinstance(name, str) or not isinstance(target, str):
            raise ConfigError("ext_family_map must map tool names to family names")

    for pattern in payload.get("extra_redaction_patterns", ()):
        try:
            re.compile(pattern)
        except re.error as error:
            raise ConfigError(f"extra_redaction_patterns: {pattern!r} is not a regex: {error}")

    known_packs = {pack.name for pack in cues.BUILTIN_PACKS}
    for name, settings in payload.get("cue_packs", {}).items():
        if name not in known_packs:
            raise ConfigError(
                f"unknown cue pack {name!r}; available: {', '.join(sorted(known_packs))}"
            )
        if not isinstance(settings, dict):
            raise ConfigError(f"cue_packs.{name} must be an object")


def _suggest(key: str) -> str:
    import difflib

    close = difflib.get_close_matches(key, KNOWN_KEYS, n=1, cutoff=0.7)
    return f"{key!r} (did you mean {close[0]!r}?)" if close else repr(key)


def _strings(payload: dict, key: str, default) -> tuple[str, ...]:
    return tuple(payload[key]) if key in payload else tuple(default)
