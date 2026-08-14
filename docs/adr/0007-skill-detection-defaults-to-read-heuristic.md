# Skill detection defaults to the SKILL.md read heuristic

A skill invocation is detected by default from a `read` tool call whose path ends in
`SKILL.md`, taking the skill name from the parent directory.

A stronger signal exists and we deliberately did not make it the default. Sessions on the
author's machine contain `custom` entries of type `context:skill_loaded` carrying the skill
name directly — 337 of them — which is more precise than any path heuristic. But that entry
is emitted by a personal pi extension, not by pi itself. This repository is public, and a
public tool whose primary detector depends on the author's private extension silently
degrades to zero skill detection for everyone else.

So the generic signal is the specification's default, and the precise one is opt-in through
the `skill_loaded_custom_types` config key. This follows the rule the requirements already
state: defaults stay generic, personal workflow details live only in config.
