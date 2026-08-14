# Pi-native rewrite, not a fork of agent-improvement-loop

The concepts in this project — four-phase pipeline, four routes plus discard, resolutions
watermark, recurrence, redaction, precision guards — come from
[agent-improvement-loop](https://github.com/cathrynlavery/agent-improvement-loop), but we
reimplemented rather than forked. Vendoring the upstream 3097-line single file would have
carried Codex and Hermes support we will never run plus a permanent manual-sync burden,
and importing it as a library is not an option because it is a script with no stable
internal API.

The cost of a rewrite is losing upstream's hard-won detector precision. We pay that down
by porting each precision guard as a named test case rather than as code, so a regression
shows up as a failing test instead of as noise in a review packet.
