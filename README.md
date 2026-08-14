# pi-self-improvement

A pi-native self-improvement loop: mine your [pi](https://github.com/badlogic/pi-mono) session transcripts for recurring friction — failing CLIs, hanging commands, silently empty results, corrections you keep typing — and stage approval-gated fix proposals. It never changes anything on its own.

**Status: the miner works; the closing half is not built yet.** Scanning, detection, routing, staging, state and the CLI are implemented and tested. The scheduled triage pass and the `learn-loop` skill are still to come. The full migration plan lives in [`docs/specs/pi-migration/`](docs/specs/pi-migration/) (requirements / plan / tasks; body text in Traditional Chinese). Project vocabulary is in [`CONTEXT.md`](CONTEXT.md); the decisions that outlive the plan are in [`docs/adr/`](docs/adr/).

## Why

Most people point AI at their work. The higher-leverage loop points it at your own setup: every fix to a skill, a tool, or an instruction file pays off in every future session. This project is a ground-up, pi-native rewrite of the ideas in [agent-improvement-loop](https://github.com/cathrynlavery/agent-improvement-loop), with two deliberate differences:

- **Pi-first detection.** Pi has no `Skill` tool and no `mcp__server__tool` naming — skills are detected from `SKILL.md` reads, extension tools are grouped by family from pi's flat tool names, and `toolResult.isError` drives failure detection.
- **Bilingual corrections.** Correction detection is built on language cue packs (English and Traditional Chinese built in), because an English-only cue set is blind to a CJK-speaking user's corrections.

## Design at a glance

1. **Collect** pi session JSONL from `~/.pi/agent/sessions/`.
2. **Normalize** into a small, redacted event model (evidence = `path:line` + short excerpt, never full transcripts).
3. **Detect** real tool usage friction and user corrections — not prose mentions.
4. **Stage** proposals and a human-readable review packet. Apply stays manual, always.

Plus a closing half: a scheduled headless triage pass (`pi -p` with a read-only tool allowlist — no shell, no `write`, no `edit`; a deterministic host-side writer is the only thing that touches disk) and an interactive `learn-loop` skill that executes the approved queue.

## Usage

```bash
pip install -e .

# preview the last 7 days without writing anything
pi-self-improvement --dry-run

# scan and stage
pi-self-improvement --since-days 7
```

A first run scans only the default window. History outside it enters **only** through an explicit `--all`, so installing this tool never triggers a surprise backfill. Preview one first — `--dry-run` writes nothing at all, not even state, so the preview cannot suppress the real run:

```bash
pi-self-improvement --all --dry-run | less
pi-self-improvement --all
```

### Reviewing and deciding

Read `review-packets/<run-id>.md`, then record what you decided. Nothing is applied by this tool — `fixed` is you telling it you already fixed the thing.

```bash
pi-self-improvement --resolve tool:demo-cli --decision fixed --pr 42 --note "pinned the version"
pi-self-improvement --resolve-from decisions.json     # batch import
pi-self-improvement --list-resolutions
pi-self-improvement --unresolve tool:demo-cli
```

`fixed` is a watermark: evidence from before it stays suppressed, and anything newer comes back flagged as a regression. `wontfix` and `ignored` suppress permanently.

### Flags

| Flag | Effect |
|------|--------|
| `--since-days N` | Time window, default 7. Filters root sessions by mtime. |
| `--all` | Ignore the window. Never happens automatically. |
| `--max-sessions N` | Keep the newest N **root** sessions; their subagent sessions come along and consume no quota. |
| `--dry-run` | Print the packet to stdout. Writes no files, no state, no logs. |
| `--full` | Keep excerpts unredacted. Marks every output `local_only` — **do not share those files.** |
| `--include-seen` | Stage proposals already staged in an earlier run. |
| `--include-resolved` | Ignore the resolutions registry for this run. |
| `--output-root PATH` | Where to write. Default `~/.pi-self-improvement/`. |
| `--config PATH` | Config file. Default `<output-root>/config.json`. |
| `--machine NAME` | Recorded in run metadata, for multi-machine setups. |

## Output layout

Everything is written under the output root and nowhere else — the tool never modifies a skill, a memory file, config, or source code.

```
~/.pi-self-improvement/
  config.json               # yours to write; optional
  state.json                # seen proposal ids + per-target recurrence history
  resolutions.json          # route:target -> decision + watermark
  runs/<run-id>.json        # metadata, counts, parser warnings
  proposals/<run-id>/*.json # one per proposal, with its evidence
  review-packets/<run-id>.md# what a human actually reads
```

Evidence is a `path:line` reference plus a short excerpt (360 characters by default), never a copy of the transcript. Every proposal carries `manual_approval_required: true`.

## Configuration

Optional, at `<output-root>/config.json`. Defaults are deliberately generic: anything tied to your personal extensions belongs here, never in a default. Unknown keys are rejected rather than ignored, because a silently dropped key is indistinguishable from one that had no effect.

| Key | Type | Purpose |
|-----|------|---------|
| `tracked_clis` | list | CLIs whose failures and retries you care about. |
| `tracked_cli_suffix` | list | Suffix rule for the same, default `["-cli"]`. |
| `cue_packs` | object | Enable, disable or extend correction cue packs (`en`, `zh-Hant`). |
| `extra_scaffold_markers` | list | Extra markers identifying injected scaffold text. |
| `extra_redaction_patterns` | list | Additional secret shapes to mask; compiled at load. |
| `ext_family_map` | object | Override how flat tool names group into `ext:<family>`. |
| `extra_backlog_ignore` | list | Programs never worth a backlog entry. |
| `include_subagent_failures` | bool | Let subagent failures reach the backlog. Default false. |
| `skill_loaded_custom_types` | list | Custom record types that signal a skill load. |
| `detect_silent_empty` | bool | Toggle silent-empty detection. |
| `silent_empty_fetch_verbs` | list | Verbs that imply a call should have returned data. |
| `silent_empty_ignore` | list | Commands whose empty result is a valid answer. |

```json
{
  "tracked_clis": ["my-deploy-tool"],
  "extra_backlog_ignore": ["cat", "sed"],
  "cue_packs": { "en": { "strong": ["that is backwards"] } }
}
```

## Scheduling (macOS)

Two launchd jobs: the miner twice weekly, the triage pass daily. Examples are in [`examples/`](examples/), runners in [`templates/`](templates/).

```bash
mkdir -p ~/.pi-self-improvement/bin
cp templates/miner-run.sh templates/fixloop-run.sh templates/fixloop-prompt.md ~/.pi-self-improvement/bin/
chmod +x ~/.pi-self-improvement/bin/*.sh

# replace CHANGEME with your home directory, then load
sed "s|/Users/CHANGEME|$HOME|g" examples/com.pi-self-improvement.miner.plist \
  > ~/Library/LaunchAgents/com.pi-self-improvement.miner.plist
sed "s|/Users/CHANGEME|$HOME|g" examples/com.pi-self-improvement.fixloop.plist \
  > ~/Library/LaunchAgents/com.pi-self-improvement.fixloop.plist

launchctl load ~/Library/LaunchAgents/com.pi-self-improvement.miner.plist
launchctl load ~/Library/LaunchAgents/com.pi-self-improvement.fixloop.plist
```

Both runners write one `RUN` line per fire to `~/Library/Logs/`, on every exit path including failure. That line is how you tell "ran, found nothing" apart from "never fired":

```bash
tail ~/Library/Logs/pi-self-improvement-miner.log
tail ~/Library/Logs/pi-self-improvement-fixloop.log
```

**The window overlaps the schedule on purpose.** The miner fires Monday and Thursday with `--since-days 8`. Those gaps are 3 and 4 days, so if one fire is missed the longest interval between two successful runs is 7 days — and 8 > 7, so a missed fire cannot leave a blind spot. Change the schedule and you must change the window with it; `tests/test_scheduling.py` computes this from the plists and fails if the two drift apart.

### First run after installing

Scheduled runs mine the window only. They will never reach back over history you already have, so a fresh install quietly ignores everything older than 8 days.

Backfill once, explicitly, and preview it first:

```bash
pi-self-improvement --all --dry-run | less   # writes nothing at all
pi-self-improvement --all                    # or --max-sessions 200 to cap it
```

The preview is genuinely inert — it records no state — so running it cannot cause the real backfill to suppress itself.

## The closing half

The daily job runs `pi -p` with `--tools read,grep,find,ls` to triage the newest review packet. It has no shell and no write access, and its output is structured triage that a deterministic host-side writer turns into files. That split is the safety model: a tool allowlist can say which tools, not which paths, so a model with `write` could edit skills and source directly no matter what the prompt says.

Triage lands in `queue/FIX-QUEUE.md` and one `decisions/<ID>.json` per incident. Work through the queue interactively with the [`learn-loop`](skills/learn-loop/SKILL.md) skill, which is where a human approves and applies anything.

## Privacy

Every transcript-derived string passes one redaction boundary before it reaches disk — commands, arguments, excerpts, summaries, paths. Emails, phone numbers, auth headers, cloud keys, private-key blocks, JWTs and long opaque tokens are masked. A corpus test asserts that none of a canary set survives into a written output root; a second test asserts `--full` *does* leak them, so the first cannot pass by staging nothing.

## Development

Python >= 3.10, standard library only — no runtime or test dependencies.

```bash
# run the suite from a bare checkout (tests/__init__.py puts src/ on sys.path)
python3 -m unittest discover -s tests -t .

# or install the package first, then discovery works from anywhere
pip install -e .
python3 -m unittest discover -s tests
```

Ad-hoc imports from a checkout need `src/` on the path: `PYTHONPATH=src python3 -c "..."`.

## Credits

Concept, safety model, and many hard-won precision lessons come from [agent-improvement-loop](https://github.com/cathrynlavery/agent-improvement-loop) by Little Might (MIT). This is an independent reimplementation for the pi ecosystem.

## License

MIT. See [LICENSE](LICENSE).
