# Pi Self-Improvement

Mines pi session transcripts for recurring friction in the user's own setup and stages
approval-gated fix proposals. Nothing is ever applied automatically.

## Language

### Sessions and transcripts

**Session**:
One conversation with pi, persisted as a single transcript. A subagent's conversation is
also a session.
_Avoid_: run, chat, thread, conversation

**Root session**:
A session the user drives directly.
_Avoid_: primary session, parent session, main session

**Subagent session**:
A session a child agent drives, spawned from a root session.
_Avoid_: child session, sub-session, subagent run

**Transcript**:
The JSONL file a session is persisted to.
_Avoid_: log, history, session file

**Scaffold**:
Text present in a session that was injected by the harness or an extension rather than
typed by the user.
_Avoid_: noise, boilerplate, injected context

### Signals and proposals

**Friction**:
A recurring obstacle in the user's own setup — a tool, skill, or instruction file that
repeatedly costs effort across sessions. A one-off failure is not friction.
_Avoid_: issue, problem, pain point

**Skill invocation**:
An occasion where a skill's instructions were loaded into a session.
_Avoid_: skill use, skill call, skill trigger

**Evidence**:
A single observed occurrence backing a proposal, pinned to where in a transcript it
happened.
_Avoid_: sample, instance, occurrence

**Proposal**:
A staged, human-approvable suggestion to fix one target. Never applied automatically.
_Avoid_: finding, suggestion, recommendation

**Route**:
Which of the four destinations a proposal belongs to.
_Avoid_: category, bucket, type

**Target**:
The thing a proposal is filed against, and the identity that carries its history across
runs.
_Avoid_: subject, item, key
