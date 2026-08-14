---
summary: Ordered implementation checklist with verify commands for the pi-native port
read_when:
  - Executing or resuming the implementation
  - Checking progress or picking the next task
---

# Tasks: Pi-Native Self-Improvement Loop

## Phase 1: Foundation

- [x] T001 [REQ-001] Scaffold package: `pyproject.toml` (entry point `pi-self-improvement`, Python >= 3.10), `src/pi_self_improvement/__init__.py`, GitHub Actions CI running unittest on 3.10/3.12. Verify: `python3 -m unittest discover -s tests` → runs (0 tests OK)
- [x] T002 [REQ-003] Create `src/pi_self_improvement/model.py` with `Evidence`, `ToolCall`, `SessionSummary` dataclasses and `has_signal()`. Verify: `python3 -c "from pi_self_improvement.model import SessionSummary"` → no error
- [x] T003 [REQ-002,REQ-003] Build synthetic pi transcript fixtures in `tests/fixtures/` covering: normal session, isError result, timeout output, empty-result call, SKILL.md read, `context:skill_loaded` custom entry, zh/en corrections, box-draw separator and `Task:` scaffold, `custom_message` injection, `bashExecution` with non-zero exit, `aborted`/`error` stopReason, dangling toolCall, branched session (sibling `parentId`), nested subagent transcript path, and a non-canonical schema file. No real transcript content. Verify: `python3 -m json.tool` on each fixture line → valid

## Phase 2: Parse (REQ-002, REQ-005 slice)

- [x] T004 [REQ-002,REQ-003,REQ-005] Write failing tests for `parse.py`: session discovery windowing (`--since-days` excludes out-of-window history regardless of state presence, `--all` includes it; AC-002, AC-038), `--max-sessions` counts root sessions only while subagent sessions ride along (AC-003, AC-039), four-role mapping including `bashExecution` (AC-040), line-order traversal with no `parentId` walk, toolCall/toolResult pairing by `toolCallId` with dangling-call counting (AC-041), cwd/timestamps, session `origin`. Verify: `python3 -m unittest tests.test_parse` → RED
- [x] T005 [REQ-002,REQ-003,REQ-005] Implement `parse.py`. Verify: `python3 -m unittest tests.test_parse` → GREEN

## Phase 3: Redaction (REQ-004 slice)

- [x] T006 [REQ-004] Write redaction corpus test: secret shapes (emails, phones, auth headers, cloud keys, JWT, private keys, long tokens) never survive into any transcript-derived string — excerpt, command, arguments, summary, or displayed path (AC-005); `--full` marks `local_only: true` in run metadata, proposal JSON and packet (AC-006); a canary scan over the whole temporary output root finds nothing (AC-042). Verify: `python3 -m unittest tests.test_redaction_corpus` → RED
- [x] T007 [REQ-004] Implement `redact.py` as the single redaction boundary (pattern set + excerpt shortening + `--full` bypass + output-root canary scan). Verify: `python3 -m unittest tests.test_redaction_corpus` → GREEN

## Phase 4: Friction detectors (REQ-005–REQ-008 slice)

- [x] T008 [REQ-005,REQ-006] Write failing tests: isError precedence over text, heuristic fallback only when flag absent, hang from output not command. Covers AC-007–AC-010. Verify: `python3 -m unittest tests.test_detect` → RED
- [x] T009 [REQ-005,REQ-006] Implement failure + hang detection in `detect.py`. Verify: `python3 -m unittest tests.test_detect` → GREEN
- [x] T010 [REQ-008] Implement silent-empty detection (data-intent classifier, empty payload match, swallowed check, ignore lists) with tests covering AC-013, AC-014. Verify: `python3 -m unittest tests.test_detect` → GREEN
- [x] T011 [REQ-007] Implement tracked-CLI attribution (config list + suffix), `bashExecution` exit-code and `cancelled` attribution, and retry-shape detection, with tests covering AC-011, AC-012, AC-043. Verify: `python3 -m unittest tests.test_detect` → GREEN

## Phase 5: Skill and corrections (REQ-009–REQ-012 slice)

- [x] T012 [REQ-009] Implement skill-invocation detection from `read` paths ending in `SKILL.md` as the specification default, plus optional `skill_loaded_custom_types` entries, with tests covering AC-015, AC-044. Verify: `python3 -m unittest tests.test_detect` → GREEN
- [x] T013 [REQ-010] Implement `cues.py` (pack model, `en` + `zh-Hant` per DEC-008 table, gates, negative guards) plus corrections corpus test including known false positives (pasted doc "instead", affirmative 「沒錯」). Covers AC-016–AC-018. Verify: `python3 -m unittest tests.test_corrections_corpus` → GREEN
- [x] T014 [REQ-011] Implement scaffold filter: structural exclusion of non-`message` records first, then the two DEC-009 markers plus config extras, with corpus test covering AC-019, AC-020, and AC-045 (a user message that merely discusses a marker is not scaffold). Verify: `python3 -m unittest tests.test_scaffold_corpus` → GREEN
- [x] T015 [REQ-012] Implement subagent exclusion keyed on the nested `<session-id>/<hash>/run-N/session.jsonl` path shape (origin flag from T005 gates backlog/corrections; config override) with test covering AC-021. Verify: `python3 -m unittest tests.test_detect` → GREEN

## Phase 6: Routing, staging, state (REQ-013–REQ-019 slice)

- [x] T016 [REQ-013] Implement `route.py` (4 routes, the REQ-013 target table with realpath → git-toplevel normalization, `MAX_CORRECTIONS_PER_SESSION` = 3, discard rules) with tests covering AC-022–AC-024 and AC-046 (a repo and its worktree share one target). Verify: `python3 -m unittest tests.test_route` → GREEN
- [x] T017 [REQ-014] Implement ext-family grouping (builtin denylist, first-underscore token, config map) with tests covering AC-025, AC-026. Verify: `python3 -m unittest tests.test_route` → GREEN
- [x] T018 [REQ-015] Implement `stage.py` (run metadata, proposal JSON, review packet with recurring-first ordering) with tests covering AC-027, AC-028. Verify: `python3 -m unittest tests.test_stage` → GREEN
- [x] T019 [REQ-016] Implement `state.py` seen-keys + deterministic proposal ids + recurrence history, with the fixed pipeline order resolution → seen → staging → recurrence, tests covering AC-029, AC-030, AC-047. Verify: `python3 -m unittest tests.test_state` → GREEN
- [x] T020 [REQ-017] Implement resolutions registry (watermark suppression, regression re-emission, `wontfix`/`ignored` permanent suppress, pre-watermark history trim, `decisions.json` import) with tests covering AC-031, AC-032, AC-049. Verify: `python3 -m unittest tests.test_state` → GREEN
- [x] T021 [REQ-018] Implement parser self-check: zero-tool-call warning in stderr + run metadata + packet, plus the counts block (root/subagent sessions, branch points, aborted/error turns, dangling toolCalls, skipped record types, non-canonical schema files) with tests covering AC-033, AC-050. Verify: `python3 -m unittest tests.test_state` → GREEN
- [x] T022 [REQ-019] Implement `config.py` load/validate/apply with test covering AC-034. Verify: `python3 -m unittest tests.test_config` → GREEN
- [x] T023 [REQ-001,REQ-015,REQ-016] Implement `cli.py` (scan flags, resolve subflows, `manual_approval_required` marking, `--dry-run` with zero state/output/log writes) wiring all modules; tests assert output-root confinement over a temporary HOME snapshot (AC-001), latest-N selection (AC-003), complete evidence fields (AC-004), and dry-run purity (AC-048). Verify: `python3 -m unittest tests.test_cli` → GREEN

## Phase 7: End-to-end and dogfood

- [ ] T024 [REQ-001,REQ-015,REQ-016] Write `tests/test_e2e.py`: full scan over fixtures → outputs exist, second scan dedups, resolution suppresses then regresses. Verify: `python3 -m unittest tests.test_e2e` → GREEN
- [ ] T025 [REQ-018] Dogfood run on the real corpus; assert the DEC-012 structural invariants (parse_errors = 0, `isError` coverage 100%, root + subagent counts sum to the file count) and record the run's counts block plus any cue-gate tuning as a dated note in the PR description. Verify: `pi-self-improvement --all --dry-run` → exit 0, parse_errors=0

## Phase 8: Closing half (REQ-020–REQ-022 slice)

- [ ] T026 [REQ-020] Write `skills/learn-loop/SKILL.md` (pi skill format: survey → select → approve → fix → verify → decide; store paths per DEC-010). Verify: `head -20 skills/learn-loop/SKILL.md` → valid frontmatter with name + description
- [ ] T027 [REQ-020] Write `templates/fixloop-prompt.md` and `templates/fixloop-run.sh` (`pi -p` with `--tools read,grep,find,ls`, shell-implemented wall-clock fuse, unconditional RUN line to `~/Library/Logs`). Verify: `bash -n templates/fixloop-run.sh` → exit 0; a test parses the runner's argv and asserts the `--tools` value is exactly `read,grep,find,ls` (AC-036), and a fake-pi empty-queue run still writes the RUN line (AC-035)
- [ ] T028 [REQ-022] Implement `src/pi_self_improvement/writer.py`: consume the headless triage result, write `queue/FIX-QUEUE.md` and `decisions/<ID>.json` under logical IDs, and reject every path outside the output root. Tests cover AC-051, AC-052. Verify: `python3 -m unittest tests.test_writer` → GREEN
- [ ] T029 [REQ-021] Write `templates/miner-run.sh` and `examples/*.plist` (miner twice weekly `--since-days 8`, fixloop daily) plus an install section in README including a bootstrap note: scheduled runs mine only the window; pre-existing history is backfilled once via explicit `--all` (preview with `--all --dry-run`, cap with `--max-sessions`). Verify: `plutil -lint examples/*.plist` → OK; a test asserts the coverage window strictly exceeds the maximum interval after one missed fire (AC-037)

## Human Acceptance

- [ ] H001 [AC-028] Read the first dogfooded review packet end to end: proposals are real friction, excerpts are readable and redacted, recurring ordering makes sense
- [ ] H002 [AC-016,AC-017] Spot-check bilingual correction hits on the real corpus: zh-Hant cues catch actual corrections without flagging instructions
- [ ] H003 [AC-035,AC-037] Load both launchd jobs on this machine, confirm RUN lines appear in `~/Library/Logs` after first scheduled fire
- [ ] H004 [AC-052] After the first scheduled fixloop fire, confirm nothing outside the output root changed: run `git status` in a repo the run could reach and check that the RUN line and the queue entry are the only new artifacts
