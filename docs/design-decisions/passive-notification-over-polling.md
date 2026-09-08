# Passive notification over polling for background work

*2026-09-07.*

This entry's reader is a maintainer auditing why the repo's no-polling rule exists, not a session mid-dispatch deciding whether to poll.

For a dispatched subagent, `code.claude.com/docs/en/sub-agents` ("Run subagents in foreground or background") states: "A background subagent's results reach Claude as a completion notification in a later turn. Claude waits for that notification before reporting the subagent's results, and if you ask about progress first, it reports that the subagent is still running." This page documents the notification mechanism only. It states no rule against polling. The no-polling rule below is this repo's own, grounded in its cost register rather than in this source.

The poll-style-wakeup prohibition for a dispatched subagent already lives in `ScheduleWakeup`'s own tool description. It is cited here as [§41](schedulewakeup-misapplied-documented.md) rather than re-quoted, since a version-bound tool description with no citable URL should have exactly one in-repo copy.

The Bash tool description documents the same re-invocation mechanism for a session's own backgrounded command. `.claude/plans/background-slow-bash-calls.md` already holds that description's `run_in_background` sentence verbatim under its `[verified: the Bash tool's own description — a backgrounded command "keeps running across turns and re-invokes you when it exits"]` tag, cited here rather than re-quoted for the same reason as above.

**Revisit** if any of:

- The sub-agents page stops stating that a completion notification reaches Claude in a later turn.

## Sources

- Anthropic, *Create custom subagents* — https://code.claude.com/docs/en/sub-agents — first-party documentation, "Run subagents in foreground or background" section.
- [§41](schedulewakeup-misapplied-documented.md) — `ScheduleWakeup`'s own prohibition against a poll-style wakeup outside `/loop`.
- `.claude/plans/background-slow-bash-calls.md`, `[verified: the Bash tool's own description — a backgrounded command "keeps running across turns and re-invokes you when it exits"]` — this repo's own verbatim copy of the Bash tool description's `run_in_background` re-invocation sentence.
