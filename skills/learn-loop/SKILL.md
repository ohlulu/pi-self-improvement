---
name: learn-loop
description: >-
  Use when acting on staged pi-self-improvement output — reading a review
  packet, working the fix queue, deciding what to do about a proposal, or
  recording an outcome for a target. Also use when a scheduled fixloop run
  has left entries in `queue/FIX-QUEUE.md`, or when asked what the miner
  found. NOT for running the miner itself (that is one CLI command).
  Trigger words: "learn loop", "learn-loop", "fix queue", "FIX-QUEUE",
  "review packet", "proposal", "self-improvement", "what did the miner
  find", "resolve this target", "修 queue", "審查封包", "提案",
  "自我改進", "決議", "標記已修".
---

# Learn Loop

## Overview

The miner stages friction it found in your own sessions; this skill is how a
human turns one of those proposals into a change. The pipeline is deliberately
split: everything before approval is automated and reversible, everything after
it is manual and permanent. Your job sits exactly on that seam.

Nothing here is applied for you, and nothing should be. A proposal is evidence
plus a suggestion, not a verdict — roughly one in three is noise on any real
corpus, and the packet is ordered so the ones most likely to matter come first.

## When to Use

Use when there is staged output to act on: a review packet you have not read,
a queue entry from a scheduled run, or a target whose outcome you want to
record.

Do not use for running the miner. That is `pi-self-improvement --since-days 7`
and needs no skill.

## The Process

Follow the order. Each step exists because skipping it produces a specific bad
outcome, named below.

### 1. Survey

Read the whole packet before touching anything:

```bash
ls -t ~/.pi-self-improvement/review-packets/ | head -1
```

Regressions first, then recurring, then new. Read all three sections before
deciding what to work on — the packet is sorted by likelihood of mattering, not
by effort, and the cheapest fix is rarely the top entry.

A first backfill packet can run to thousands of lines. Skim it for shape, then
work the regressions and the highest recurrence counts. Do not try to clear it.

### 2. Select

Pick **one** target. Prefer, in order: a regression (something you believed
fixed came back), then the highest `previous_runs` count, then a target whose
evidence you can reproduce right now.

Recurrence is the strongest signal in the packet. A target flagged across eight
runs has cost you eight sessions; a one-off with a vivid excerpt has cost you
one. Vividness is not frequency.

### 3. Approve

Say out loud what you are about to change and get explicit agreement before
editing anything. This tool never edits skills, memory files, config or source
code, and the moment you act on a proposal you are the one making that change —
the approval gate is you.

If the proposal is wrong, that is a normal outcome. Skip to Decide and record
`wontfix`. An unactionable proposal you leave open resurfaces every run.

### 4. Fix

Make the change by hand, in the smallest form that addresses the evidence:

- `tool:<executable>` — the CLI's own behaviour, its wrapper, or the instruction
  telling the agent to use it
- `tool:ext:<family>` — the extension's tool descriptions or error handling
- `skill_improvement:<skill>` — the skill body that failed to prevent the correction
- `memory_context:<repo>` — that repo's `AGENTS.md` or project docs
- `backlog:<executable>` — usually an upstream bug or a missing wrapper; often
  the right fix is `extra_backlog_ignore` in config

Read the evidence excerpts first. They carry `path:line` into the real
transcript, so when an excerpt is ambiguous, open the transcript at that line
rather than guessing what the agent was doing.

### 5. Verify

Reproduce the original friction and confirm it is gone. For a skill or memory
change that means running the scenario that triggered the correction, not
re-reading the file you just edited.

A change you cannot verify is a change you cannot record as `fixed`. Record it
as `wontfix` with a note saying why, or leave it open.

### 6. Decide

Record the outcome through the CLI. Never hand-edit `resolutions.json` — the
watermark and the recurrence trim are computed, and a hand-written file gets
one of them wrong:

```bash
pi-self-improvement --resolve tool:demo-cli --decision fixed --pr 42 \
  --note "pinned the version in the wrapper"
```

- `fixed` — a watermark. Older evidence stays suppressed; anything newer comes
  back flagged as a regression, which is how you learn the fix did not hold.
- `wontfix` — permanent. Use for working-as-intended and for noise.
- `ignored` — permanent. Use for "real, but not mine to fix".

Resolving trims that target's recurrence history, so its next appearance reads
as a first regression rather than an eighth sighting. That is the point: the
count you see is the count since the last fix.

## Common Mistakes

- **Reading only the first section.** The packet leads with regressions, so a
  reader who stops early sees only things that already failed once and misses
  the highest-recurrence new targets underneath.
- **Fixing the excerpt instead of the cause.** An excerpt is 360 characters
  around a match. Open the transcript at `path:line` before concluding what
  went wrong.
- **Recording `fixed` after editing, before verifying.** The watermark then
  suppresses the very evidence that would have told you the fix missed.
- **Hand-editing the store.** `resolutions.json` and `state.json` are computed
  artifacts. The CLI is the only supported writer.
- **Clearing the backlog route wholesale.** Most backlog entries are ordinary
  non-zero exits. If a program is noise for you, that is a config line
  (`extra_backlog_ignore`), not thirty `wontfix` decisions.

## Store Paths

Everything lives under the output root, default `~/.pi-self-improvement/`:

```
review-packets/<run-id>.md   what you read
proposals/<run-id>/<id>.json one proposal + its evidence
queue/FIX-QUEUE.md           entries from scheduled fixloop runs
decisions/<ID>.json          one file per logical incident
resolutions.json             route:target -> decision + watermark
```

Decision files are named by logical ID with no machine prefix, so two machines
deciding the same incident converge on one file instead of splitting into two
outcomes that later need reconciling.

## Quick Reference

```bash
# what is waiting
ls -t ~/.pi-self-improvement/review-packets/ | head -1
cat ~/.pi-self-improvement/queue/FIX-QUEUE.md

# after fixing and verifying
pi-self-improvement --resolve <route:target> --decision fixed --note "..."

# it was noise
pi-self-improvement --resolve <route:target> --decision wontfix --note "..."

# see what you have decided, or undo one
pi-self-improvement --list-resolutions
pi-self-improvement --unresolve <route:target>
```
