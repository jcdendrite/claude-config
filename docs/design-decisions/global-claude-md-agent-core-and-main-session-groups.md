# Global CLAUDE.md is split into an Agent Core group and a Main session group

*2026-09-23.*

GH-1085 holds the full placement table; this record holds the contract a later `claude/.claude/CLAUDE.md` editor needs.

## Why

Claude Code's `omitClaudeMd` subagent frontmatter, together with `skills:` preload, would let an agent drop the global CLAUDE.md and load only the rules it needs. That needs the rules every agent must follow in one block that can be extracted as-is. Until an agent adopts it, every subagent except Explore and Plan still loads the whole file, so the grouping also tells those subagents which rules are theirs.

## The contract

- Two H1 groups: `# Agent Core` first and contiguous, then `# Main session`.
- Every `##` name is kept, except that Code Comments, Documentation, and Prose becomes `### Durable text` under Prose and Output Format.
- The opening line under `# Agent Core` states each group's audience and translates ask, confirm and stop steps into "report it in your return" for dispatched agents. It lives at `claude/.claude/CLAUDE.md`; this record does not restate it.
- `claude/.claude/hooks/tests/test_global_claude_md_groups.py` pins the group order, the opening line's contract, the cross-group placements listed below, and the core-only rules. Per-bullet placement of a new rule stays a manual check.

## Placement tests

- Tool capability decides, not likelihood. A prose-only rule whose trigger can arise in a subagent goes to Agent Core.
- A rule meant for `code-writer` or `plan-architect` goes to Agent Core, because the opening line tells subagents that Main session is not theirs.
- A rule addressed to subagents goes to Agent Core: the "Dispatching cannot clear a denial your child inherits" bullet and the "any fork or subagent returns its work to its dispatcher rather than shipping on its own" clause.
- The MEMORY.md-index bullet sits in Agent Core because every subagent probed (`plan-architect`, `general-purpose`, `code-writer`, `staff-sdet`) reported the index in its own context.
- The marker `clear-stale` bullet stays in Main session: `clear-stale` cannot clear a subagent's own leftovers.

Whether subagents comply with the grouping is unmeasured at the time of writing. A post-merge compliance spot-check on this repo's own sessions follows `docs/private-project-redaction.md` § "Publishing a tooling measurement", and GH-1085 records the result.

The spot-check includes two cases:

- A dispatcher prompt that asserts the user's approval of a gated step, because "report it in your return" names no policy for a relayed approval.
- A dispatcher prompt that directs shipping, to observe whether a fork or subagent ships.

Two pins in the group test restate current wording and need a test edit with the change they anticipate: the opening line's fragments (a later trim of that line) and the exactly-one-line output-preferences pin (the deferred `@`-import follow-up).

## Forks

The opening line names forks: `only the main session and forks follow Main session`. A fork holds the whole conversation and acts for the main session.

"Fork" here means a dispatched run that either inherits the parent's conversation or, for a `context: fork` skill, receives none. Neither can ask the user.

The shipping clause ("any fork or subagent returns its work to its dispatcher rather than shipping on its own") names forks because, with the opening line granting forks Main session, "a dispatched subagent" would not cover them. Its handling of forks:

- For a fork, the clause is intended to take precedence over Main session's Shipping bullets, on specificity, so the fork returns its work instead of following the commit-and-PR duties. No loaded text states a tiebreak.
- Prose is the only control on a prompt-injected fork. The commit, push and PR-creation gates are review-state gates that apply to every caller. They have documented bypass shapes and a marker read not tied to a session, so they pass a fork that follows a main-session review at HEAD.
- `deny-reviewer-tree-mutation.sh` keys on `agent_type` only for the closed review-only set, denies only commit and push, and does not gate `gh pr create`.
- No gate keyed on caller identity covers a fork, `code-writer` or `general-purpose`, and a fork runs in the parent's process identity, so nothing can tell it from the main session.
- Extending the identity-keyed hook to other non-fork subagents is a follow-up candidate. The gap predates this restructure.

Four readings of the clause still let a fork or subagent ship:

- A fork may not know it is one.
- The loaded text has no tiebreak against Main session's Shipping bullet.
- "On its own" can read as "on its own initiative".
- The audience partition is self-declared: a non-fork subagent told, or injected with text saying, "you are a fork" inherits Main session's Shipping autonomy text. The existing review-state gates bound this.

Closing the first three costs about 25 to 30 bytes against the byte margin, so the residual is accepted. The post-merge fork spot-check is the control.

## Accepted risk: duplicate headings

`## Safety` and `## Working Style` each appear once per group until core moves into its own file. Until then a `§ "Safety"` citation can resolve to either heading. The "Dispatching cannot clear a denial your child inherits" bullet's phrase "Safety's marker bullet" is ambiguous, because Main session's Safety also holds a marker bullet. The citation resolver builds a set of headings and does not flag duplicates. Both are accepted.

## The permissions-globs relocation

The "Don't add globs" bullet lives in `claude/.claude/rules/settings-json-conventions.md`, with a one-line stub in Agent Core § Safety that reads "No wildcards in `permissions.allow`." A permission rule is composed before any settings file opens, which is why the stub stays always loaded.

- The stub keeps the prohibition always loaded, which meets the relocation bar at `docs/cost-levers-considered.md` for the prohibition. The rationale and the exact-match alternative live in the rule file to pay for the opening line's bytes. The stub is a fragment on purpose.
- The backstop is `ask-review-permissions.sh`, which asks on Edit, Write and MultiEdit of a path ending in `.claude/settings*.json` and fails open when `_lib.sh` cannot be sourced. Whether a hook `ask` reaches a human under auto mode is unverified: `docs/auto-mode.md` says auto mode replaces per-action permission prompts with a background classifier, and nothing in this repo states how a hook `ask` resolves there.
- Tests covering the backstop:
  - `test_ask_review_permissions.py` covers the Edit, Write and MultiEdit arms, ask and allow paths.
  - `test_hook_alignment.py` pins that `settings.json` wires the hook on a matcher spanning all three tools.
  - The group test pins that the rule file keeps the guidance and both settings filenames in its `paths:`.
  - None of these tests cover when the rule loads.
- A permission deny rule for Bash reads of settings files was considered and advised against by `plan-architect`. A narrow pattern misses `settings.local.json`, `sed`, `jq`, `head` and `grep`. A broad one also blocks `git diff` on settings paths and any `git commit -m` that names the file. It teaches the agent nothing, and it does not reach an out-of-project `~/.claude/settings.json`.

Known gaps. Only (d) was accepted by the engineer in their own words, on the premise that an agent would Read the file first, which one-trial probes did not support. The rest were found and left open; the always-loaded stub keeps the prohibition in context for all of them:

- (a) Advice given without any settings file being opened or created, which neither the rule nor the hook reaches.
- (b) A Write that creates a new settings file gets the hook's generic ask, but may not get the rule's guidance before the content is written.
- (c) A settings file under a config directory whose path has no `.claude/` segment gets the rule on Read but no ask.
- (d) Bash-mediated writes (`jq`, `sed -i`, `tee`) get no ask, because the hook covers Edit, Write and MultiEdit only.
- (e) A consumer who pulls without re-running `install.sh` after a hook-file addition loses the hook.
- (f) In one-trial subagent probes, a Read-tool read of a settings file outside the session's project loaded no rule, including the user-scope `~/.claude/settings.json`, where the hook asks but the rule did not load.

Unverified: load behavior on out-of-project reads beyond one trial each, whether `**/settings.json` matches a file under a dot-directory such as `.claude/`, whether "Edit requires a prior Read" holds, and whether the rule loads on a Write that creates a new file.

## Open residuals and re-review triggers

The fork and identity-gate residual under Forks was accepted by the engineer, relying on the post-merge fork spot-check. Gaps (a)-(c), (e) and (f) are open, and (d) is accepted on an unsupported premise. All share one ownership record:

- Owner: the repo owner.
- Tracker: not yet filed. The acceptance is unbounded until a tracker is filed.
- Re-review triggers, each with how it is observed:
  - A fork or subagent commits, pushes or opens a PR contrary to the shipping clause: observed by the post-merge fork spot-check and by transcript review.
  - A settings edit slips through gap (c) or (d): not detectable from the hook, which emits no ask and leaves no log. Observed only by transcript review or a report.
  - The identity-keyed hook is extended to other subagents: observed at the next change to `deny-reviewer-tree-mutation.sh`.
  - The vendor documents how a hook `ask` resolves under auto mode: observed at the next change to `docs/auto-mode.md`.
- Any later change to CLAUDE.md Agent Core reopens this section.

## The output-preferences deferral

The output-preferences read instruction moves verbatim into Main session. Making it an `@`-import is deferred to a follow-up.

A user-scope scratch test, one run per arm, showed that a symlinked CLAUDE.md follows `@`-imports and resolves a relative import against the symlink target's directory. `@~/` resolution is untested. The consequence for the later extraction of core: a colocated relative import of core resolves inside the repo.

The Stopping bullet is split across the group boundary. Its blocked-stop half sits in Agent Core: "Stop when the work is genuinely blocked", with the three example conditions and "Say what is blocked." Its "Do not ask permission to proceed with work that is already done." half ends Main session § Shipping's "Do not offer to show the diff first" sub-bullet, next to its autonomous-shipping antecedent. The group test pins both placements.

Four lines are reworded rather than moved verbatim: the shipping clause, the Stopping bullet's opening, the permissions stub, and the relocated proceed clause.

Follow-ups for dangling phrases left by verbatim moves:

- The shipping clause's "Merge stays human-only", whose autonomous-shipping antecedent stays in Main session.
- The output-preferences bullet's "the rules above", which now sits under Working Style.
- The Prose section scope line's "the section below", which now points at a subsection.
- The Durable text scope line "This section governs comments and durable docs only", which is ambiguous inside Prose.

Tracker: not yet filed.

## Forward pointer

`advance-past-commit-stall.sh` cites the shipping-clause as "CLAUDE.md's Shipping section". That clause now sits in Working Style, so the citation needs a follow-up. Tracker: not yet filed.

## Byte margin

The file is 31,552 bytes and 196 lines: 19,140 bytes in Agent Core and 12,412 in Main session (`LC_ALL=C awk` over the `# Main session` split). That leaves 17 bytes under the 31,569-byte baseline; the original file was 31,574 bytes and 184 lines. The file is at the length gate's ceiling, so any later CLAUDE.md wording growth needs an equal trim. Orchestrator naming in the opening line waits until the agent exists. The permissions-globs stub stays in place of the full bullet, and bullets carry no per-bullet "(main)" tags.

## Review orchestrator

The review orchestrator in open PR #714 must claim Main session in its own body, because the opening line names only the main session and forks.

## Rollback

Trigger, either of:

- A subagent skips an Agent Core rule because of the new grouping, as its return or transcript shows.
- The fork spot-check observes a fork or subagent shipping (commit, push or PR creation) that the shipping clause tells it to return, or a dispatcher-relayed approval treated as the user's own.

Revert the whole squash commit, never individual paths, so this record and the "Partially superseded" line do not describe a contract that no longer exists. A hand-restored tree committed through `git commit` grows CLAUDE.md and the length ratchet denies it. Plain `git revert <sha>` is not intercepted by the length gate or the code-review gate, both of which match only the `commit` subcommand. `git revert -n` followed by `git commit` passes the length gate but needs a `/code-review` marker on the reverse diff. Expect a `CHANGELOG.md` conflict. Reopen the placement question in GH-1085 after the revert.
