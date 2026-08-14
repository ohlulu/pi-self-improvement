# Parse transcripts in line order, not as a tree

Pi session entries form a tree via `id`/`parentId`, so rewinding and re-asking creates
sibling branches inside one file. We parse in file line order anyway and do not walk the
tree to the active leaf.

This is deliberate, and it is the decision most likely to look like a bug. Do not "fix" it
without reading this first.

## Considered Options

Walking `parentId` from the last entry back to the root would yield only the surviving
conversation, which is what pi itself does via `buildContextEntries()`. We rejected it
because session format v1 is a linear entry sequence with no `parentId` at all, and pi only
migrates a file to v3 when it *loads* it — a miner reading files directly never triggers
that migration. A tree-walking parser would therefore break on any pre-v2 transcript on a
machine other than the author's, while line-order parsing is version-agnostic by
construction.

## Consequences

Abandoned branches are mined as if they had happened. Concretely, a skill loaded on branch
A can be correlated with a correction made on branch B, producing a `skill_improvement`
proposal for two events that never met. On the reference corpus this affects 63 of 419
transcripts across 88 branch points.

We accept that error rather than hide it: the parser self-check reports branch-point counts
on every run, so the contamination is a number someone can look at instead of a silent
defect. An independent review flagged the linear-parse simplification as a blocking finding
and proposed per-branch detection with entry-ID deduplication of shared ancestors. We
rejected that fix on the version-compatibility argument above, with the self-check counts
as the mitigation.
