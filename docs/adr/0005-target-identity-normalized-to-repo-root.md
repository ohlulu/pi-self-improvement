# Target identity is normalized to the repo root

A target is the thing a proposal is filed against. `route:target` is simultaneously the
proposal id, the recurrence key, and the resolutions registry key, so its exact spelling is
a data format: changing it invalidates every stored proposal and every recorded decision.

| Route | Target |
|-------|--------|
| `tool` | tracked executable basename, or `ext:<family>` for extension tools |
| `skill_improvement` | skill name |
| `memory_context` | repository root |
| `backlog` | normalized executable name |

Normalization resolves symlinks with `realpath`, then walks up to the git toplevel, then
preserves the original case. When no root can be determined the target is `<unknown>`.

The repo-root step is the non-obvious one. Grouping by raw working directory splits a
single project across its subdirectories and, worse, across git worktrees — the author's
machine already has `lang-evo-ios/` and `lang-evo-ios.worktrees/<branch>/` as separate
paths for the same codebase and the same `AGENTS.md`. Left unnormalized, each fragment
accumulates evidence separately and none of them ever reaches the recurrence threshold, so
the friction that motivated this project would be the friction it fails to report.

Case is preserved rather than lowercased so review packets show a path that can be copied
and used. macOS is case-insensitive but case-preserving, so if key collisions ever show up
in practice, add a lowercased comparison key rather than mangling the displayed target.
