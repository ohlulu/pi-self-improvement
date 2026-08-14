# A subagent's conversation is a session

Pi stores a subagent's transcript inside the spawning session's directory, as
`sessions/<cwd-slug>/<session-id>/<hash>/run-N/session.jsonl`. On the machine this was
designed against, 85 of 419 transcripts are subagent conversations. We treat every
transcript as a session and distinguish them with an `origin` attribute of
`root` or `subagent`, rather than reserving the word "session" for the top-level
conversation.

The alternative — subagent runs are a separate kind of thing — reads more naturally but
forces every count, cap, and identity rule to name both kinds explicitly. One term plus an
attribute keeps the model small. The consequence is that rules phrased "per session" must
say which origin they mean: `--max-sessions` counts root sessions only, and subagent
sessions ride along with their root instead of consuming budget.

Detection keys on the nested path shape, which lives under pi's session directory. It is
therefore unaffected by pi-subagents moving its artifacts directory from
`<repo>/.pi-subagents/` to `<repo>/.pi/subagents/`, and unaffected by that directory being
pruned — the nested transcripts persist after the artifacts are cleaned up.
