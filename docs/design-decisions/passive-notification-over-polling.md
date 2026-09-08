# Passive notification over polling for background work

*2026-09-07.*

This entry's reader is a maintainer auditing why the repo's no-polling rule exists, not a session mid-dispatch deciding whether to poll.

For a dispatched subagent, `code.claude.com/docs/en/sub-agents` ("Run subagents in foreground or background") states: "A background subagent's results reach Claude as a completion notification in a later turn. Claude waits for that notification before reporting the subagent's results, and if you ask about progress first, it reports that the subagent is still running." This page documents the notification mechanism only; on its own it states no rule against polling.

**The no-polling norm itself is documented by Anthropic, generalized past subagent dispatch, not invented by this repo.** Two first-party sources establish it. The Week 15 (2026-04-06–10) release digest describes the Monitor tool letting Claude react to background events "all without a Bash sleep loop holding the turn open." It also states that `/loop` "reaches for the Monitor tool to skip polling altogether." The `anthropics/claude-code` `CHANGELOG.md`, v2.1.246, names the opposite of the norm as the bug it fixed: "Fixed Proactive output style sessions busy-looping with filler messages and repeated log reads instead of idling while a background command or Monitor they started is still running." Idling, not polling, is therefore the behavior Anthropic confirms as correct for both a background command and a Monitor watch — not only for a dispatched subagent's completion.

The poll-style-wakeup prohibition for a dispatched subagent additionally lives in `ScheduleWakeup`'s own tool description. It is cited here as [§41](schedulewakeup-misapplied-documented.md) rather than re-quoted, since a version-bound tool description with no citable URL should have exactly one in-repo copy.

The Bash tool description documents the same norm for a session's own backgrounded command, more directly than the sources above: "If waiting for a background task you started with `run_in_background`, you will be notified when it completes — do not poll." This entry is that sentence's one in-repo copy. The mechanism half of the same description ("keeps running across turns and re-invokes you when it exits") is a separate sentence with its own existing copy at `.claude/plans/background-slow-bash-calls.md:95-97`, cited rather than re-quoted here for the reason [§41](schedulewakeup-misapplied-documented.md) gives for any version-bound, URL-less tool description: keep exactly one in-repo copy. The no-poll sentence is confirmed uniform, not agent-type-specific: as of 2026-09, it appears identically in both the top-level session's own Bash tool schema and a dispatched `general-purpose` agent's schema.

**Revisit** if any of:

- The sub-agents page stops stating that a completion notification reaches Claude in a later turn.
- The Bash tool description drops the `run_in_background` no-poll sentence, or a further agent-type check finds it non-uniform.

## Sources

- Anthropic, *Create custom subagents* — https://code.claude.com/docs/en/sub-agents — first-party documentation, "Run subagents in foreground or background" section.
- Anthropic, *Week 15 · April 6–10, 2026* — https://code.claude.com/docs/en/whats-new/2026-w15 — first-party release digest, "Monitor tool" section.
- `anthropics/claude-code`, `CHANGELOG.md`, v2.1.246 — first-party changelog entry naming busy-looping instead of idling as the fixed defect.
- [§41](schedulewakeup-misapplied-documented.md) — `ScheduleWakeup`'s own prohibition against a poll-style wakeup outside `/loop`.
- `.claude/plans/background-slow-bash-calls.md:95-97` — this repo's own verbatim copy of the Bash tool description's `run_in_background` re-invocation sentence, "keeps running across turns and re-invokes you when it exits."
