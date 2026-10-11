# Skill attribution follows the agent's edit, not the skill load

Supersedes [ADR-0007](0007-skill-detection-defaults-to-read-heuristic.md).

A correction is filed against a skill only when the agent answered it by changing that skill's files: a successful `edit` or `write` under `.../skills/<name>/` after the correction and before the next user message. A topic correction keeps its own target, `memory_context:style:<topic>`, whatever was edited. For any other correction, exactly one such skill routes the correction to `skill_improvement:<name>`. None, or more than one, routes it to `memory_context:<repo>`. Skill loads are no longer detected, and the `skill_loaded_custom_types` config key is removed.

The previous rule credited the most recently loaded skill with every later correction in the session, with no distance limit. On the author's corpus in 2026-10 that produced 235 skill_improvement evidence items, most of them product feedback or a new task that happened to follow a skill load. `commit` was the worst case: it is loaded at the end of a turn, so the next message is almost always new work, and `skill_improvement:commit` came back in 11 consecutive runs without one real correction of the commit skill.

Tightening the position did not fix it. Counting only the first user message after the load still left 13 `commit` items, all noise. Requiring an edit later in the same turn removed most of them, but checking the transcripts showed the remaining ground-truth cases were attributed to the wrong skill: the user corrected `deep-research`'s default output path while `doc-system` was the last skill loaded, and asked to adjust the release flow while `update-changelog` was. When the load point is right, the target can still be wrong, because a load records which skill was present, not which one the user objected to.

The skill the agent goes on to edit names the fix directly, and it is pi core behaviour, so the rule stays generic without a personal config key. Two costs are accepted:

- A skill failure the agent did not fix on the spot routes to `memory_context`. It stays visible, only without a skill key.
- A user asking to extend a skill counts the same as a correction of it. On the 2026-10 corpus about half of the 21 attributed items were requests like this.

Precision over recall is the deciding priority: a review packet is only worth reading if what it files under a skill is about that skill.
