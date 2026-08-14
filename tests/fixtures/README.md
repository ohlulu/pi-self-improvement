# Fixtures

Synthetic pi transcripts. **No real transcript content, paths, skill names or secrets** —
every value here is invented. The layout mirrors `~/.pi/agent/sessions/` so discovery can
be pointed at `tests/fixtures/sessions/` as a fake sessions root.

```
sessions/
  --tmp-pi-fixtures-alpha--/
    ...a001.jsonl                     normal session: skill read, context:skill_loaded, clean bash
    ...a001/9f2c1ab4d7/run-1/         subagent session (nested <session-id>/<hash>/run-N/session.jsonl)
      session.jsonl                     Task: seed prompt + a failure that must not reach the backlog
    ...a002.jsonl                     friction: isError, hang, silent-empty, retries, bashExecution
  --tmp-pi-fixtures-beta--/
    ...b001.jsonl                     corrections (zh/en) + scaffold + false positives
    ...b002.jsonl                     ext-tool failures, aborted/error turns, dangling call, branch
  --tmp-pi-fixtures-gamma--/
    ...d001.jsonl                     non-canonical schema (valid JSONL, not pi's shape)
```

## What each file pins

| File | Covers |
|------|--------|
| `a001` | skill invocation from a `SKILL.md` read (DEC-006), `context:skill_loaded` custom entry (AC-044), a clean session that must produce no friction |
| `a001/.../run-1/session.jsonl` | subagent origin (DEC-015), `Task:` seed scaffold, a failure that AC-021 keeps out of the backlog |
| `a002` | `isError` failure with its command (AC-007), success log containing "error" (AC-008), `Command timed out after 30 seconds` (AC-009), `timeout 120 …` that succeeds (AC-010), retry shape across three flag combos (AC-012), `[]` result nobody acknowledges (AC-013), `rg` no-match (AC-014), `bashExecution` exit 127 (AC-040, AC-043) |
| `b001` | `custom_message` docs-index injection (AC-020), zh correction (AC-016), 「沒錯」 guard (AC-018), long pasted doc with "instead" (AC-017), en strong cue, box-drawing separator (AC-019), `Task:` seed, a user message that merely mentions `[Project docs index]` (AC-045) |
| `b002` | ext-tool family failures (AC-025), builtin `read` failure (AC-026), `aborted` and `error` turns, a dangling `toolCall` (AC-041), sibling `parentId` branch point (AC-050), skill invocation followed by a correction (AC-022) |
| `d001` | a non-canonical schema file: valid JSON on every line, no `session` header, foreign record shape (AC-041) |

Timestamps are fixed and in the past. Tests that exercise the `--since-days` window copy the
tree into a temporary directory and set mtimes explicitly — never rely on checkout mtimes.
