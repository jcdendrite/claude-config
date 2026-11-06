# Autonomous-shipping state is stated by hook context, not by CLAUDE.md prose

*2026-11-05.*

Where autonomous shipping is active, code resolves that fact and states it to the agent through every hook surface that shapes its commit, push and PR behavior.
`claude/.claude/CLAUDE.md` § "Shipping" already holds the rule, and it asks the agent to run `autonomous-shipping-active.sh` itself.
It adds delivery of the resolved state.
It adds no rule, no `CLAUDE.md` edit and no config key.

## The incident

A main interactive session, right after a compaction, committed locally and then ended its turn asking whether to push.
It never ran the resolver script, which exits 0 on that machine.
Three contributors compounded:

- No code-resolved statement of the shipping state reached the agent, so acting on it depended on recalling `CLAUDE.md`'s instruction to run a script.
- The post-compaction boundary text says actions with effects outside the repository need in-session confirmation. A session read that as covering a plain push or PR.
- The Stop hook meant to catch the resulting question read `last_assistant_message` with one `IFS= read -r`, so a question on a later line of a multi-line final message never reached the fire predicate.

## The decision

`_lib_autonomous_shipping_statement REPO_ROOT` in `_lib.sh` is the only home of the agent-facing text. It prints only when `_lib_autonomous_shipping_active` holds, so no caller can print it without the check passing.
Three hooks deliver it:

- `announce-autonomous-shipping.sh` on SessionStart `startup|resume|clear`.
- `restore-authorization-boundary-on-compact.sh` on `compact`, inside its one message.
- `advance-past-commit-stall.sh`, as the opening of its block reason.

The statement restates `CLAUDE.md` § "Shipping" on purpose. Hook context has to stand alone after compaction.
It names the resolver script as its source and does not tell the agent to skip `CLAUDE.md`'s verify step.
It never overrides an engineer's specific hold or an instruction given in the current session, and it covers only committing, a plain push of the current branch and opening a PR.

The Stop hook reads the whole message with `$(cat)`, because `read -d ''` reads a pipe one byte at a time.
It judges the last sentence of the last non-blank line.
It scans only the last 2048 characters of the message, because Bash's `##` and `%` pattern removal over one long line takes time quadratic in its length.
2048 is a chosen bound on the length of an ask sentence, not a measured knee.

`/handoff` and `/brief` each gain one exception sentence, so a continuity file stops filing a plain commit, push or PR under pending authorization when the filed step names the resumer's own `autonomous-shipping-active.sh` exit 0 as its precondition and the engineer stated no hold.
A resuming session reads the file's authorization line as its brief, and a blanket "push and PR are not authorized" line there recreated the incident's failure through a different channel.

## Why hook context instead of CLAUDE.md prose

- `CLAUDE.md` is loaded into every session, and `check-claude-md-length.sh` caps its size.
- The smallest edit that makes a hook statement an authority beside the script is a clause on the verify bullet, which would spend headroom under that cap in a file every stow consumer loads every session.
- Re-injection of user-level `CLAUDE.md` after compaction is unconfirmed, so a `CLAUDE.md` addition would not reach the incident's case.
- The rule was already in `CLAUDE.md`. What failed was recall, and hook context fixes recall with no new always-loaded prose.

## Why one helper with three callers, and one message after compaction

Three constant strings drift apart, and the single-source pin in each hook's test fails an inlined copy.
After compaction the agent would otherwise get one message saying an outside-repository effect needs confirmation and a second message overriding it. A second layer that corrects the first is the shape to avoid.
The compact hook therefore builds one message from the boundary text and the statement, and its kill switch silences only the boundary text.
The three hooks also resolve their repo root through one function, `_lib_autonomous_shipping_root_for_cwd`. The Stop hook's earlier root resolution fell back to `$PWD` and accepted a root equal to `$HOME`.
One shared function keeps the three hooks on one rule.

## Alternatives rejected

- **A `CLAUDE.md` addition.** See above.
- **Compact hook plus Stop hook only, no startup announcement.** The recall-based rule is the failure on startup, resume and `/clear` too, and a resumed session reads the same continuity-file line.
- **Folding the statement into `session-marker-dashboard.sh`.** It mixes marker state with authorization, and after compaction the statement would land in a second message beside the boundary text it contradicts.
- **Widening the compact hook's matcher to every source.** Its name, its silent-on-non-compact test contract and its kill switch all assume compaction, and the switch would then silence the statement too.
- **A new hook on all four sources with the compact hook untouched.** It leaves the two-message contradiction after compaction.
- **`UserPromptSubmit` on every turn.** It re-injects the full statement on every prompt.
- **`PostToolUse` after `git commit`.** It misses the stall that comes before any commit and needs Bash command parsing.
- **A new config key.** The statement's natural switches are `autonomous_shipping` and the repo optout. A separate key would add a schema row for a state nobody needs, shipping on but unannounced.
- **A 0x1f-joined read for the Stop capture.** It adds invisible delimiter bytes and an overflow check for one free-text field that can be read last.
- **A separate `_lib_jq` call for the message.** It adds a process to every Stop in an opted-in session.

## Revisit conditions

- A session is observed asking permission to commit, push or open a PR with the statement present in its context.
- The vendor confirms that user-level `CLAUDE.md` is re-injected after compaction, which would weaken the case for restating the rule in hook context.
