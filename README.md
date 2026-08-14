# pi-self-improvement

A pi-native self-improvement loop: mine your [pi](https://github.com/badlogic/pi-mono) session transcripts for recurring friction — failing CLIs, hanging commands, silently empty results, corrections you keep typing — and stage approval-gated fix proposals. It never changes anything on its own.

**Status: design phase.** The full migration plan lives in [`docs/specs/pi-migration/`](docs/specs/pi-migration/) (requirements / plan / tasks; body text in Traditional Chinese). Project vocabulary is in [`CONTEXT.md`](CONTEXT.md); the decisions that outlive the plan are in [`docs/adr/`](docs/adr/).

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
