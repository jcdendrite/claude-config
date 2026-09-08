# The no-op-dispatch guard is a CLAUDE.md rule, not a hook

*2026-09-03. Formerly `docs/design-decisions.md` §51.*

A session that spawns a subagent whose only instruction is to wait, occupy the turn, or report back immediately pays a full agent's context for an empty return. The prohibition first shipped as prose in `subagent-delegation/SKILL.md` Step 1 on 2026-09-02. It recurred on 2026-09-03 in a session that never loaded the skill body — only the one-line trigger description from the available-skills listing. That is the whole failure: a skill-body rule reaches a session only when the skill is in context, and this impulse arrives at moments that do not look like delegation decisions. Two occurrences are confirmed, both in this repo's own history:

- `memory-content-migration`, 2026-08-30
- `discovery-audit-remediation-plan`, 2026-09-03

That is a floor from keyword search and one direct admission, not an exhaustive count: transcripts do not record which skill bodies were in a session's context, so this repo's own history cannot be counted as exhaustive. The rule now also sits in `claude/.claude/CLAUDE.md`'s Agent Briefing section, which every session loads on every turn.

**[§1](hook-enforced-gates.md) prefers a hook, and no hook is available here.** A `PreToolUse` hook on the `Agent` tool receives `tool_name`, `tool_input` (`subagent_type`, `prompt`, `description`, `model`), `session_id`, `cwd`, and `transcript_path`. None of those carries a non-content signal for "this dispatch does no work." The one non-content fact reachable — that another `Agent` dispatch is already in flight, via transcript parsing — is not the defect: `plan-it` Step 3 encourages parallel fan-out, so gating on it would deny legitimate dispatches. A hook would therefore have to match on prompt text. Three hooks match the `Agent` tool today, and none supplies that predicate:

- `require-routing-read.sh` (`PreToolUse`) gates on a file-read marker.
- `require-architect-consult.sh` (`PreToolUse`) reads `tool_input.subagent_type` only to scope itself to reviewer-persona dispatches, then gates on a round-count state unrelated to no-op detection.
- `log-reviewer-round.sh` (`PostToolUse`, never denies) reads both `tool_input.subagent_type` and `tool_input.prompt` but only to record round state for the same gate.

Reusing any of the three would still mean adding new prompt-content matching for no-op intent specifically — the same cost as a fresh hook. [§1](hook-enforced-gates.md)'s preference holds where a mechanical predicate exists. Here there is none, so the real choice is between two advisory surfaces, and the always-loaded one wins.

**Partial duplication, accepted under a named exception.** CLAUDE.md carries the prohibition, the shapes it covers, and what to do instead. `subagent-delegation` keeps the return-value framing and the delegation-cost reasoning. Both surfaces must stand alone, because a session that loaded the skill and a session that did not are different readers — and the second reader is why this entry exists. That is CLAUDE.md §Engineering Judgment's "instructional prose that must stand alone" exception, not an unexamined copy. The overlapping sentences are worded identically on both surfaces so that `git grep` exposes drift.

**No mechanical enforcer.** The trigger is dispatch intent, not a file-content fact. A test asserting the bullet's literal string would be tautological and would break on any rewording.

**Revisit** if the behavior recurs after this rule ships. The next lever is a `PreToolUse` hook on `Agent`, accepting prompt-content matching and its false-positive cost. At that point two advisory surfaces will have failed, which is itself the evidence that no advisory surface reaches this impulse.

## Sources

- `claude/.claude/CLAUDE.md` §Agent Briefing — the rule's text.
- `claude/.claude/skills/subagent-delegation/SKILL.md` Step 1 — the same rule with its delegation-cost reasoning.
- `claude/.claude/hooks/require-routing-read.sh` — an `Agent`-matching `PreToolUse` hook gating on a file-read marker rather than on tool-input content.
- `claude/.claude/hooks/require-architect-consult.sh` — an `Agent`/`Task`-matching `PreToolUse` hook reading `tool_input.subagent_type` to scope itself, then gating on round-count state.
- `claude/.claude/hooks/log-reviewer-round.sh` — an `Agent`/`Task`-matching `PostToolUse` hook reading both `tool_input.subagent_type` and `tool_input.prompt` to record round state, never denies.
- `.claude/plans/guard-placeholder-wait-forks.md` — the first mechanism choice, including the ledger rows that rejected both a hook and CLAUDE.md at that time.
- `.claude/plans/guard-no-op-dispatch-rule.md` — this change's assumption ledger.
