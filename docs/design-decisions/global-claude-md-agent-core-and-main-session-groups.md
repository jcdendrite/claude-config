# Global CLAUDE.md is split into an Agent Core group and a Main session group

*2026-09-23.*

GH-1085 holds the full placement table; this record holds the contract a later `claude/.claude/CLAUDE.md` editor needs.

## Why

Claude Code's `omitClaudeMd` subagent frontmatter, together with `skills:` preload, would let an agent drop the global CLAUDE.md and load only the rules it needs. That needs the rules every agent must follow in one block that can be extracted as-is. Until an agent adopts it, every subagent except Explore and Plan still loads the whole file, so the grouping also tells those subagents which rules are theirs.

## The contract

- Two H1 groups: `# Agent Core` first and contiguous, then `# Main session`.
- Every `##` name is kept, except that Code Comments, Documentation, and Prose becomes `### Durable text` under Prose and Output Format.
- The opening line under `# Agent Core` states each group's audience and translates ask, confirm and stop steps into "report it in your return" for dispatched agents. It lives at `claude/.claude/CLAUDE.md`; this record does not restate it.
- `claude/.claude/hooks/tests/test_global_claude_md_groups.py` pins the group order, the opening line's contract, the cross-group placements decided below, and the core-only rules. Per-bullet placement of a new rule stays a manual check.

## Placement tests

- Tool capability decides, not likelihood. A prose-only rule whose trigger can arise in a subagent goes to Agent Core.
- A rule meant for `code-writer` or `plan-architect` goes to Agent Core, because the opening line tells subagents that Main session is not theirs.
- A rule addressed to subagents goes to Agent Core: the "Dispatching cannot clear a denial your child inherits" bullet and the "any fork or subagent returns its work to its dispatcher rather than shipping on its own" clause.
- The MEMORY.md-index bullet sits in Agent Core because every subagent probed (`plan-architect`, `general-purpose`, `code-writer`, `staff-sdet`) reported the index in its own context.
- The marker `clear-stale` bullet stays in Main session: `clear-stale` cannot clear a subagent's own leftovers.

Whether subagents comply with the grouping is unmeasured at the time of writing. A post-merge spot-check on this repo's own sessions, on a single account and with a scope command that refuses a wider corpus cited beside the figure, records its result in GH-1085. A wider read goes to the owner privately. If no scope-refusing instrument covers the compliance read, the result goes to the owner privately, and GH-1085 records only the decision (kept or reverted) and that the check ran, with no counts or rates. The spot-check should include a case where the dispatcher's prompt asserts the user's approval of a gated step, because "report it in your return" names no policy for a relayed approval.

Two pins in the group test restate current wording and need a test edit with the change they anticipate: the opening line's fragments (a later trim of that line) and the exactly-one-line output-preferences pin (the deferred `@`-import follow-up).

## Forks

The opening line names forks: `only the main session and forks follow Main session`. A fork holds the whole conversation and acts for the main session.

"Fork" here means a dispatched run that either inherits the parent's conversation or, for a `context: fork` skill, receives none. Neither can ask the user.

The "any fork or subagent returns its work to its dispatcher rather than shipping on its own" clause names forks, so for a fork it takes precedence over Main session's Shipping bullets and the fork returns its work instead of following Main session's commit-and-PR duties. It is the one line in the restructure whose wording changed: the move out of the Shipping bullet and the opening line's fork grant left "a dispatched subagent" ambiguous for a fork. No prose stops a prompt-injected fork. The commit, push and PR-creation gates are review-state gates that apply to every caller, with documented bypass shapes and a marker read that is not tied to a session, so they pass a fork that follows a main-session review at HEAD. No commit, push or PR-creation gate keyed on caller identity covers a fork, `code-writer` or `general-purpose`. `deny-reviewer-tree-mutation.sh` keys on `agent_type` only for the closed review-only set, denies only commit and push, and does not gate `gh pr create`. A fork runs in the parent's process identity, so nothing can tell it from the main session. A deny for other non-fork subagents could extend the identity-keyed hook that already covers the review-only agents. That gap predates this restructure and is a follow-up candidate.

Three readings of the reworded clause still let a fork ship: a fork may not know it is one, the loaded text has no tiebreak against Main session's Shipping bullet, and "on its own" can read as "on its own initiative". Closing them costs about 25 to 30 bytes against the byte margin, so the engineer accepted the residual. The post-merge fork spot-check is the control, and it should include a dispatcher prompt that directs shipping.

## Accepted risk: duplicate headings

`## Safety` and `## Working Style` each appear once per group until core moves into its own file. Until then a `§ "Safety"` citation can resolve to either heading. The "Dispatching cannot clear a denial your child inherits" bullet's phrase "Safety's marker bullet" is ambiguous, because Main session's Safety also holds a marker bullet. The citation resolver builds a set of headings and does not flag duplicates. Both are accepted.

## The permissions-globs relocation

The "No globs in `permissions.allow`" bullet moved from CLAUDE.md to `claude/.claude/rules/settings-json-conventions.md`, with a one-line stub kept in Agent Core § Safety. This partly reverses the earlier decision to keep the bullet in CLAUDE.md, which relied on a permission rule being composed before any settings file is opened.

- The stub keeps the prohibition always loaded, which meets the relocation bar at `docs/cost-levers-considered.md` for the prohibition. The rationale and the exact-match alternative relocate to the rule file to pay for the opening line's bytes. The stub is a fragment on purpose.
- The backstop is `ask-review-permissions.sh`, which asks on Edit, Write and MultiEdit of a path ending in `.claude/settings*.json` and fails open when `_lib.sh` cannot be sourced. `test_ask_review_permissions.py` now covers the `MultiEdit` arm, `test_hook_alignment.py` pins that `settings.json` wires the hook on a matcher spanning all three tools, and the group test pins that the rule file keeps the guidance and both settings filenames in its `paths:`. None of these tests cover when the rule loads.
- A permission deny rule for Bash reads of settings files was considered and advised against by `plan-architect`, not decided by the engineer. A narrow pattern misses `settings.local.json`, `sed`, `jq`, `head` and `grep`. A broad one also blocks `git diff` on settings paths and any `git commit -m` that names the file. It teaches the agent nothing, and it does not reach an out-of-project `~/.claude/settings.json`.

Gaps the plan found, of which the engineer's round-2 answer covered only (d):

- (a) Advice given without any settings file being opened or created, which neither the rule nor the hook reaches.
- (b) A Write that creates a new settings file gets the hook's generic ask, but may not get the rule's guidance before the content is written.
- (c) A settings file under a config directory whose path has no `.claude/` segment gets the rule on Read but no ask.
- (d) Bash-mediated writes (`jq`, `sed -i`, `tee`) get no ask, because the hook covers Edit, Write and MultiEdit only.
- (e) A consumer who pulls without re-running `install.sh` after a hook-file addition loses the hook.
- (f) A Read-tool read of a settings file outside the session's project loaded no rule.

In one-trial subagent probes, a Read-tool read outside the project and a Bash read inside it loaded no rule, while a Read-tool read of an existing matching file inside the project did. That the engineer's premise "Edit requires a prior Read" holds is unverified, and so is whether the rule loads on a Write that creates a new file.

The highest-blast-radius target, the user-scope `~/.claude/settings.json`, is the case the out-of-project result covers: the hook asks there, but the rule did not load. Whether `**/settings.json` matches a file under a dot-directory such as `.claude/` was not recorded for the in-project probe, so the path shape that loaded the rule is unconfirmed.

## The output-preferences deferral

The output-preferences read instruction moves verbatim into Main session. Making it an `@`-import is deferred to a follow-up.

A user-scope scratch test, one run per arm, showed that a symlinked CLAUDE.md follows `@`-imports and resolves a relative import against the symlink target's directory. `@~/` resolution is untested. The consequence for the later extraction of core: a colocated relative import of core resolves inside the repo.

Phase 2 follow-ups for dangling phrases left by verbatim moves:

- The Stopping bullet's "still", whose Shipping antecedent stays in Main session.
- The output-preferences bullet's "the rules above", which now sits under Working Style.
- The Prose section scope line's "the section below", which now points at a subsection.
- The Durable text scope line "This section governs comments and durable docs only", which is ambiguous inside Prose.

Tracker: not yet filed.

## Forward pointer

`advance-past-commit-stall.sh` cites the shipping-clause as "CLAUDE.md's Shipping section". That clause now sits in Working Style, so the citation needs a follow-up. Tracker: not yet filed.

## Byte margin

The file is at the length gate's ceiling, so any later CLAUDE.md wording growth needs an equal trim, which phase 2 pays for. Nothing is deferred for bytes. The engineer deferred naming the review orchestrator on scope, because the agent does not exist yet. The engineer preferred the stub to the full permissions-globs bullet on practice, and called per-bullet "(main)" tags excessive.

## Review orchestrator

The review orchestrator in open PR #714 must claim Main session in its own body, because the opening line names only the main session and forks.

## Rollback

Trigger: a subagent skips an Agent Core rule because of the new grouping, as its return or transcript shows.

Revert the whole squash commit, never individual paths, so this record and the "Partially superseded" line do not describe a contract that no longer exists. A hand-restored tree committed through `git commit` grows CLAUDE.md and the length ratchet denies it. Plain `git revert <sha>` is not intercepted by the length gate or the code-review gate, both of which match only the `commit` subcommand. `git revert -n` followed by `git commit` passes the length gate but needs a `/code-review` marker on the reverse diff. Expect a `CHANGELOG.md` conflict. Reopen the placement question in GH-1085 after the revert.
