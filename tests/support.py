"""Shared helpers for building throwaway session trees in tests."""

import json
import os
import shutil
import time
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FIXTURE_SESSIONS = FIXTURES / "sessions"

DAY = 86400.0


def copy_fixture_sessions(dest: Path) -> Path:
    """Copy the fixture session tree into `dest/sessions` and return that path."""
    target = dest / "sessions"
    shutil.copytree(FIXTURE_SESSIONS, target)
    return target


def write_jsonl(path: Path, records: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path


def age(path: Path, days: float, now: float | None = None) -> Path:
    """Set a transcript's mtime to `days` ago. Window tests must never trust checkout mtimes."""
    stamp = (now if now is not None else time.time()) - days * DAY
    os.utime(path, (stamp, stamp))
    return path


def session_record(session_id: str, cwd: str = "/tmp/pi-fixtures/alpha", **extra) -> dict:
    record = {
        "type": "session",
        "id": session_id,
        "cwd": cwd,
        "timestamp": "2026-01-05T09:00:00.000Z",
        "version": 3,
    }
    record.update(extra)
    return record


def user_record(text: str, record_id: str = "u1", parent_id: str | None = None) -> dict:
    return {
        "type": "message",
        "id": record_id,
        "parentId": parent_id,
        "timestamp": "2026-01-05T09:00:01.000Z",
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
    }


def tool_call_record(
    call_id: str,
    name: str,
    arguments: dict,
    record_id: str = "a1",
    parent_id: str | None = None,
    stop_reason: str = "toolUse",
) -> dict:
    return {
        "type": "message",
        "id": record_id,
        "parentId": parent_id,
        "timestamp": "2026-01-05T09:00:02.000Z",
        "message": {
            "role": "assistant",
            "stopReason": stop_reason,
            "content": [{"type": "toolCall", "id": call_id, "name": name, "arguments": arguments}],
        },
    }


def tool_result_record(
    call_id: str,
    tool_name: str,
    text: str,
    is_error: bool = False,
    record_id: str = "t1",
    parent_id: str | None = None,
    details: dict | None = None,
) -> dict:
    message = {
        "role": "toolResult",
        "toolCallId": call_id,
        "toolName": tool_name,
        "isError": is_error,
        "content": [{"type": "text", "text": text}],
        "details": details or {},
    }
    return {
        "type": "message",
        "id": record_id,
        "parentId": parent_id,
        "timestamp": "2026-01-05T09:00:03.000Z",
        "message": message,
    }


def make_root_transcript(sessions_root: Path, slug: str, name: str, records: list[dict]) -> Path:
    return write_jsonl(sessions_root / slug / f"{name}.jsonl", records)


def make_subagent_transcript(root_transcript: Path, hash_dir: str, run: int, records: list[dict]) -> Path:
    nested = root_transcript.with_suffix("") / hash_dir / f"run-{run}" / "session.jsonl"
    return write_jsonl(nested, records)
