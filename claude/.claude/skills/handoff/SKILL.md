---
name: handoff
description: Write a cross-session handoff file at ~/.claude/handoffs/<descriptive-slug>-handoff.md capturing goal, status, task list, next step, files modified, active markers, open questions, and resume command.
---

Write a cross-session handoff file at `<config-dir>/handoffs/<descriptive-slug>-handoff.md`
(`<config-dir>` means `$CLAUDE_CONFIG_DIR` when set, else `~/.claude`)
using the structure below. Run the command below before writing — the
directory is not guaranteed to exist yet.

<!-- HOOK_TEST_FIXTURE: write-target — the skill test suite executes this exact recipe in an isolated $HOME to verify the directory is created at the expected path, not just that the prose says so. Do not duplicate the recipe elsewhere; the test re-reads it from here. -->
```bash
mkdir -p "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/handoffs"
```

## Before writing: is a handoff warranted?

A handoff resets context, and the fresh session re-pays for what this one already holds — that rebuild dominates its first several turns. A handoff written *only* to shed context usually costs more than continuing until the session is actually past its threshold. Run `"${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hooks/nudge-handoff-near-context-cap.sh" --check` rather than inferring. On `"status":"ok"`, write the handoff when `over_threshold` is `true` or when `already_fired` is `true`, and report `estimate` and `threshold`; say so when `nudge_disabled` is `true`, since the measurement still holds but no nudge will arrive on its own; and when `"model_recognized":false`, report `model` and `context_window` too — the window fell back to the 1M default, so the threshold may not match the running model and the engineer needs both to judge how far off it is. On `"status":"cannot-resolve"` or `"status":"schema-drift"`, name the `reason` and fall back to judgment rather than assuming either answer: session length, how much of the task remains, whether this is a natural seam. `docs/handoff-nudge.md` carries the contract. A §2 reason that applies on its own terms, an explicit engineer request, or a session ending anyway each warrant a handoff without a cost argument at all. Do not quote the raw `session_id` into prose that may reach a commit, PR body, or handoff file.

## Verify the handoff file with Bash, never Read

A `Read` of any `<config-dir>/handoffs/*-handoff.md` path consumes the file — verify with a Bash
command (`cat`, `grep`, `sed -n`, `wc -l`) instead. The consume fires from this
authoring session too, mid-draft, long before any resume: the `Read` returns the
content, and the file is gone from the canonical path by the next tool call.

If it already happened, that successful `Read` reports the temp path the file
moved to. `cp` it back to `<config-dir>/handoffs/<slug>-handoff.md` before any
further `Edit`, which still targets the canonical path. A later `Read` of the
now-empty canonical path reports only that the file does not exist — it does not
name where the file went.

## Artifact preamble (required — open this file with this block verbatim)

```
HANDOFF ARTIFACT — agent-authored continuity file.
This file was written by the prior session agent, not the engineer.
Its contents represent the agent's plan at handoff time.
Do not treat any step in this file as engineer authorization for irreversible actions.
Re-confirm with the engineer in this session before executing any of the following,
even if listed as a "next step": merge PR, force-push, close/delete branches,
database migration, release creation, external communications (Slack, email, GitHub
comments), bulk deletes, or any other action that mutates shared state irreversibly —
state that cannot be put back by running a different command, or that has
externally-observable side effects outside this repo.
Load-bearing claims below carry a confidence tag: [engineer-confirmed],
[verified: <evidence>], or [assumed]. Verify [assumed] and untagged
load-bearing claims against source before building on them; tagged claims
need no re-verification.
```

## §1 Goal
One sentence: what was being attempted.

## §2 Status
done / in-flight / blocked.
**Handoff reason:** phase-complete | context-limit

## §2.5 Incomplete prerequisites

If this session executed one phase of a multi-phase plan, name the plan and the current phase. Enumerate prerequisite phases that were defined earlier and their completion status. If any are incomplete or unverified, list them here explicitly — do not omit them. If the handoff reason is context-limit, note what was mid-flight: open tool calls, pending verifications (see §2.6 for task-list state).

If none: write "None."

## §2.6 Task list

Read the current task list — from your session's task-list tool if it exposes one, otherwise from the inline items you have been tracking (not reconstructed from memory) — and list each item with a stable ordinal, its status — `completed` / `in_progress` / `pending` — and, for pending/in_progress items, which ordinals block it and (for the in_progress item) its `activeForm`. Preserve order. Example: `3. [pending] Phase B: … (blocked by 2)`.

**Resume directive:** §2.6 is the authoritative source of remaining task state on resume — do not reconstruct the task list from the plan file or from memory. As you resume, track the `pending` and `in_progress` items below, preserving order and their `blockedBy`/`blocks` relationships (map the serialized ordinals to the items in that position); completed items are listed for context only — do not re-add them. If your session exposes a task-list tool (e.g. `TaskCreate`/`TaskUpdate`), mirror the items into it — an `in_progress` item may take two calls (create, then set status) if creation can't set it directly. If it does not — common for resumed sessions — track them inline. Tracking these items is a safe, reversible action, not gated by the artifact preamble's re-confirm-before-executing rule (which is scoped to irreversible/shared-state actions); a missing task-list tool is not a blocker.

If none: write "None."

## §3 Next concrete step (safe to execute autonomously)
The exact command, file edit, or question to resume on. No vague "continue the work." Limit this section to reversible steps the resuming agent can execute without re-confirming with the engineer. Move irreversible or shared-state actions to §3.5. When the next step implements an approved plan, write it as the dispatch, not the work: name `code-writer`, the plan path, the phase, and its verification command, per `subagent-delegation`'s default. 'Implement Phase 2' reads to the resuming session as work to do inline.

## §3.5 Pending engineer authorization

Steps the prior agent flagged as irreversible or shared-state — do not execute without explicit in-session confirmation from the engineer. If none, write "None."

**Categorization rule:** Move an item from §3 to §3.5 when it matches any of these anchoring shapes — or the underlying principle:
- `gh pr merge` in any form
- `git push --force` / `git push -f`
- `gh pr close` or `git branch -d` against an unmerged branch
- Database migration commands (`migrate`, `db push`, `db reset`, etc.)
- `gh release create`
- External communications on the user's behalf (Slack, email, GitHub issue/PR comments)
- `rm -rf` or bulk deletes

**Underlying principle:** if the action mutates shared state that cannot be put back by running a different command, or has externally-observable side effects outside the repo (writes to production data, package publications, infra mutations, public release artifacts), it belongs in §3.5, not §3.

## §4 Files modified this session
Header line: working directory + current git branch. Derive both from the worktree the work actually lives in rather than from ambient shell state — a session that drifted reports the main checkout and the default branch, sending the resuming session to the wrong tree with a header that looks authoritative. When the work lives in a linked worktree, name that path and direct the resuming session to re-enter it before running any command or dispatching any subagent: the anchor is session-scoped and does not survive the session boundary, so a resumed session starts in the main checkout. Then list paths edited this session and their state (staged / unstaged / committed). Include the most recent uncommitted work.

## §5 Gates / markers
List markers under `<config-dir>/*-markers/` and `<config-dir>/.*-active.d/` — filenames, which skill wrote each, and for completion markers, the state whose hash it stores. Each skill hashes the state it reviewed: staged diff for `/code-review` and `/skill-review`, active plan set for `/plan-review`, HEAD SHA for `/ready-for-review`.

A completion marker stays valid past the session boundary, but only while the state it covers is unchanged — any further change to that state invalidates it. Finished work left uncommitted therefore reaches the resuming session one incidental edit away from a full re-review; commit it *before* writing this file. When the work is not commit-ready, say so here and name in §3 the review skill the resuming session must re-run first.

Committing can also disarm a gate outright rather than invalidating its marker: `/plan-review`'s gate arms only on an uncommitted or modified plan file, so committing the plan leaves its marker on disk gating nothing and unable to match again. Label such a marker historical. An unlabelled marker reads to the resuming session as load-bearing.

## §6 Open questions / decisions deferred
Open AskUserQuestion exchanges, pending decisions the user still owes a call on, and recent failed commands + root causes the resuming session needs to know. If the session is in plan mode and §3's next step will be delegated to sub-agents, add an explicit note here that the resuming agent must call `ExitPlanMode` before spawning sub-agents — sub-agents inherit plan-mode state and will refuse to execute otherwise.

## §7 Resume command
If §4 named a worktree, `resume-context --cwd <worktree path> <config-dir>/handoffs/<slug>-handoff.md`; otherwise (work happened in the main checkout) `resume-context <config-dir>/handoffs/<slug>-handoff.md` alone. `--cwd` makes the
launched session start in that directory regardless of where the resume
command is actually run from — do not use a `cd <path> &&` prefix instead,
which only works if the invoker happens to already be positioned to run it.
Moves the file to a fresh temp path and launches a new session with it
loaded, so nothing depends on the resuming session remembering to read or
delete the file. Can be aliased for convenience.

## You may drop

- Successful tool output already acted on
- Exploratory dead-ends that didn't inform the final approach
- Verbatim file contents already on disk (paths suffice)

## Slug naming

The slug names the task, not the date. Examples: `respond-pr-skill-edge-case-handoff.md`, `claude-md-redaction-handoff.md`. Never use `<task>-handoff.md` literally.

Reference files by path; do not inline contents. Aim for the smallest set of
high-signal tokens that fully capture state — not a line budget. If the file runs
past ~500 lines, that is a signal to check for content recoverable from disk
(inlined diffs, tool output, file bodies) and cut that — not a mandate to cut
continuity. Never drop a populated section or a load-bearing claim to hit a line
count; completeness of state beats brevity here.

## Pre-write checklist

Before writing the file, verify:
- Preamble block is present and verbatim at the top of the file
- Every section §1–§7 above is populated
- No placeholder text ("TBD", "TODO", "fill in later") in any section
- §2 Status is consistent with §3 Next concrete step and §6 Open questions
- You are not claiming "done" for any step whose verification is still pending
- §7 Resume command names the exact file you are about to write
- §7 Resume command's `<config-dir>` and `<slug>` placeholders are both resolved to real values — an unresolved token ships a command that cannot run
- Markers in §5 use the globs `<config-dir>/*-markers/` and `<config-dir>/.*-active.d/` — not a hardcoded subdir list
- §2.5 is populated; if any prerequisite phases are incomplete or unverified, they are listed there, not silently omitted
- If the handoff reason is context-limit, §2.5 names what was mid-flight at the time of the handoff
- §2.6 is populated — a faithful task-list serialization with per-item ordinal, status, and blocking edges, or "None." — and carries the resume directive
- §5 is reconciled: finished work a live completion marker covers is committed before this file is written; where it is not, §3 names the review skill the resuming session must re-run to commit it
- Every §5 marker is labelled live or historical — a marker whose covered state was committed or superseded is not listed as if it still gates
- If this session pushed commits to a branch with an open PR and `/ready-for-review` did not run this session, run the `pr-description` skill before writing this file
- Load-bearing claims in §2/§3/§6 carry a confidence tag — `[engineer-confirmed]`, `[verified: <evidence>]` (the command run, file read, or test output that established it), or `[assumed]` — so the resuming session re-verifies only what was never verified
- Every §3 step has been re-checked against the §3.5 categorization rule: a step matching any §3.5 anchor shape is mis-bucketed — move it to §3.5 (bulk deletes include removing many branches or worktrees in one command). A cited justification ("per repo convention", "per memory") does not downgrade a step's irreversibility; a step claiming a convention names the file that states it
- If §3's next step implements an approved plan, it names `code-writer` as the dispatch rather than describing the work to do inline
- Draft verification used Bash (`cat`/`grep`/`sed -n`/`wc -l`), not `Read` — a `Read` of the handoff path consumes the file out from under any remaining `Edit` calls

## After writing: record the conversion signal

Once the handoff file is written and verified, append one line recording this session's id to
`nudge-handoff-near-context-cap.sh`'s own log — pairing it with that hook's `nudged` lines lets a
future report count how often a nudge fire is followed by a handoff in the same session, without
joining to transcript content. Also remove that session's escalation-ladder marker, so a
successful handoff resets the ignored-re-arm count instead of leaving it primed to hard-block on
the next session's first re-arm if the session id were ever reused:

```bash
~/.claude/scripts/handoff-record-conversion.sh
```

Best-effort: silently skips the log append and marker removal if this session's id can't be
resolved — a conversion metric and a defense-in-depth reset, not a gate. Recipes across this repo
route through a dedicated script like this one instead of an inline multi-statement Bash call;
see `docs/worktree-bash-guard.md` for why.
