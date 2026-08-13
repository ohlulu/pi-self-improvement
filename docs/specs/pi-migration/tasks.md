---
summary: Ordered implementation checklist with verify commands for the pi-native port
read_when:
  - Executing or resuming the implementation
  - Checking progress or picking the next task
---

# Tasks: Pi-Native Self-Improvement Loop

## Phase 1: Foundation

- [ ] T001 [REQ-001] Scaffold package: `pyproject.toml` (entry point `pi-self-improvement`, Python >= 3.10), `src/pi_self_improvement/__init__.py`, GitHub Actions CI running unittest on 3.10/3.12. Verify: `python3 -m unittest discover -s tests` → runs (0 tests OK)
- [ ] T002 [REQ-003] Create `src/pi_self_improvement/model.py` with `Evidence`, `ToolCall`, `SessionSummary` dataclasses and `has_signal()`. Verify: `python3 -c "from pi_self_improvement.model import SessionSummary"` → no error
- [ ] T003 [REQ-002] Build synthetic pi transcript fixtures in `tests/fixtures/` covering: normal session, isError result, timeout output, empty-result call, SKILL.md read, zh/en corrections, scaffold injections, subagent artifact. No real transcript content. Verify: `python3 -m json.tool` on each fixture line → valid

## Phase 2: Parse (REQ-002, REQ-005 slice)

- [ ] T004 [REQ-002,REQ-005] Write failing tests for `parse.py`: session discovery windowing, role mapping, toolCall/toolResult pairing by `toolCallId`, cwd/timestamps, subagent origin flag. Verify: `python3 -m unittest tests.test_parse` → RED
- [ ] T005 [REQ-002,REQ-005] Implement `parse.py`. Verify: `python3 -m unittest tests.test_parse` → GREEN

## Phase 3: Redaction (REQ-004 slice)

- [ ] T006 [REQ-004] Write redaction corpus test: secret shapes (emails, phones, auth headers, cloud keys, JWT, private keys, long tokens) never survive into excerpts. Verify: `python3 -m unittest tests.test_redaction_corpus` → RED
- [ ] T007 [REQ-004] Implement `redact.py` (pattern set + excerpt shortening + `--full` bypass). Verify: `python3 -m unittest tests.test_redaction_corpus` → GREEN

## Phase 4: Friction detectors (REQ-005–REQ-008 slice)

- [ ] T008 [REQ-005,REQ-006] Write failing tests: isError precedence over text, heuristic fallback only when flag absent, hang from output not command. Covers AC-007–AC-010. Verify: `python3 -m unittest tests.test_detect` → RED
- [ ] T009 [REQ-005,REQ-006] Implement failure + hang detection in `detect.py`. Verify: `python3 -m unittest tests.test_detect` → GREEN
- [ ] T010 [REQ-008] Implement silent-empty detection (data-intent classifier, empty payload match, swallowed check, ignore lists) with tests covering AC-013, AC-014. Verify: `python3 -m unittest tests.test_detect` → GREEN
- [ ] T011 [REQ-007] Implement tracked-CLI attribution (config list + suffix) and retry-shape detection with tests covering AC-011, AC-012. Verify: `python3 -m unittest tests.test_detect` → GREEN

## Phase 5: Skill and corrections (REQ-009–REQ-012 slice)

- [ ] T012 [REQ-009] Implement skill-invocation detection from `read` paths ending in `SKILL.md` with test covering AC-015. Verify: `python3 -m unittest tests.test_detect` → GREEN
- [ ] T013 [REQ-010] Implement `cues.py` (pack model, `en` + `zh-Hant` per DEC-008 table, gates, negative guards) plus corrections corpus test including known false positives (pasted doc "instead", affirmative 「沒錯」). Covers AC-016–AC-018. Verify: `python3 -m unittest tests.test_corrections_corpus` → GREEN
- [ ] T014 [REQ-011] Implement scaffold filter (leading-tag rule + DEC-009 marker defaults + config extras) with corpus test covering AC-019, AC-020. Verify: `python3 -m unittest tests.test_scaffold_corpus` → GREEN
- [ ] T015 [REQ-012] Implement subagent exclusion (origin flag from T005 gates backlog/corrections; config override) with test covering AC-021. Verify: `python3 -m unittest tests.test_detect` → GREEN

## Phase 6: Routing, staging, state (REQ-013–REQ-019 slice)

- [ ] T016 [REQ-013] Implement `route.py` (4 routes, per-cwd grouping with session cap, discard rules) with tests covering AC-022–AC-024. Verify: `python3 -m unittest tests.test_route` → GREEN
- [ ] T017 [REQ-014] Implement ext-family grouping (builtin denylist, first-underscore token, config map) with tests covering AC-025, AC-026. Verify: `python3 -m unittest tests.test_route` → GREEN
- [ ] T018 [REQ-015] Implement `stage.py` (run metadata, proposal JSON, review packet with recurring-first ordering) with tests covering AC-027, AC-028. Verify: `python3 -m unittest tests.test_stage` → GREEN
- [ ] T019 [REQ-016] Implement `state.py` seen-keys + deterministic proposal ids + recurrence history with tests covering AC-029, AC-030. Verify: `python3 -m unittest tests.test_state` → GREEN
- [ ] T020 [REQ-017] Implement resolutions registry (watermark suppression, regression re-emission, `decisions.json` import) with tests covering AC-031, AC-032. Verify: `python3 -m unittest tests.test_state` → GREEN
- [ ] T021 [REQ-018] Implement parser self-check warnings (stderr + run metadata + packet) with test covering AC-033. Verify: `python3 -m unittest tests.test_state` → GREEN
- [ ] T022 [REQ-019] Implement `config.py` load/validate/apply with test covering AC-034. Verify: `python3 -m unittest tests.test_config` → GREEN
- [ ] T023 [REQ-001,REQ-015] Implement `cli.py` (scan flags, resolve subflows, `--dry-run`, `manual_approval_required` marking) wiring all modules. Verify: `python3 -m unittest tests.test_cli` → GREEN

## Phase 7: End-to-end and dogfood

- [ ] T024 [REQ-001,REQ-015,REQ-016] Write `tests/test_e2e.py`: full scan over fixtures → outputs exist, second scan dedups, resolution suppresses then regresses. Verify: `python3 -m unittest tests.test_e2e` → GREEN
- [ ] T025 [REQ-018] Dogfood run on the real corpus; compare against DEC-012 baseline (sessions parsed, tool calls, zero parse errors); record numbers and any cue-gate tuning as a dated note in the PR description. Verify: `pi-self-improvement --all --dry-run` → exit 0, parse_errors=0

## Phase 8: Closing half (REQ-020, REQ-021 slice)

- [ ] T026 [REQ-020] Write `skills/learn-loop/SKILL.md` (pi skill format: survey → select → approve → fix → verify → decide; store paths per DEC-010). Verify: `head -20 skills/learn-loop/SKILL.md` → valid frontmatter with name + description
- [ ] T027 [REQ-020] Write `templates/fixloop-prompt.md` and `templates/fixloop-run.sh` (`pi -p` with `--tools read,grep,find,ls,write,edit`, shell-implemented wall-clock fuse, unconditional RUN line to `~/Library/Logs`). Verify: `bash -n templates/fixloop-run.sh` → exit 0; `grep -c bash templates/fixloop-run.sh` allowlist line → 0 occurrences of bash in `--tools` value
- [ ] T028 [REQ-021] Write `templates/miner-run.sh` and `examples/*.plist` (miner twice weekly `--since-days 4`, fixloop daily) plus an install section in README. Verify: `plutil -lint examples/*.plist` → OK

## Human Acceptance

- [ ] H001 [AC-028] Read the first dogfooded review packet end to end: proposals are real friction, excerpts are readable and redacted, recurring ordering makes sense
- [ ] H002 [AC-016,AC-017] Spot-check bilingual correction hits on the real corpus: zh-Hant cues catch actual corrections without flagging instructions
- [ ] H003 [AC-035,AC-037] Load both launchd jobs on this machine, confirm RUN lines appear in `~/Library/Logs` after first scheduled fire
