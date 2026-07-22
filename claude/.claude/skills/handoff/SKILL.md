---
name: handoff
description: Write a cross-session handoff file at ~/.claude/handoffs/<descriptive-slug>-handoff.md capturing goal, status, task list, next step, files modified, active markers, open questions, and resume command.
---

Write a cross-session handoff file at `~/.claude/handoffs/<descriptive-slug>-handoff.md`
using the structure below. Run the command below before writing — the
directory is not guaranteed to exist yet.

<!-- HOOK_TEST_FIXTURE: write-target — the skill test suite executes this exact recipe in an isolated $HOME to verify the directory is created at the expected path, not just that the prose says so. Do not duplicate the recipe elsewhere; the test re-reads it from here. -->
```bash
mkdir -p ~/.claude/handoffs
```

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
The exact command, file edit, or question to resume on. No vague "continue the work." Limit this section to reversible steps the resuming agent can execute without re-confirming with the engineer. Move irreversible or shared-state actions to §3.5.

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
Header line: working directory + current git branch. Then list paths edited this session and their state (staged / unstaged / committed). Include the most recent uncommitted work.

## §5 Active gates / markers
List active markers under `~/.claude/*-markers/` and `~/.claude/.*-active.d/` — filenames, which skill wrote each, and (for completion markers) the staged-diff hash the marker covers.

## §6 Open questions / decisions deferred
Open AskUserQuestion exchanges, pending decisions the user still owes a call on, and recent failed commands + root causes the resuming session needs to know. If the session is in plan mode and §3's next step will be delegated to sub-agents, add an explicit note here that the resuming agent must call `ExitPlanMode` before spawning sub-agents — sub-agents inherit plan-mode state and will refuse to execute otherwise.

## §7 Resume command
`resume-context ~/.claude/handoffs/<slug>-handoff.md` — moves the file to a
fresh temp path and launches a new session with it loaded, so nothing
depends on the resuming session remembering to read or delete the file.
Can be aliased for convenience.

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
- Markers in §5 use the globs `~/.claude/*-markers/` and `~/.claude/.*-active.d/` — not a hardcoded subdir list
- §2.5 is populated; if any prerequisite phases are incomplete or unverified, they are listed there, not silently omitted
- If the handoff reason is context-limit, §2.5 names what was mid-flight at the time of the handoff
- §2.6 is populated — a faithful task-list serialization with per-item ordinal, status, and blocking edges, or "None." — and carries the resume directive
- If this session pushed commits to a branch with an open PR and `/ready-for-review` did not run this session, run the `sync-pr-description` skill before writing this file
- Load-bearing claims in §2/§3/§6 carry a confidence tag — `[engineer-confirmed]`, `[verified: <evidence>]` (the command run, file read, or test output that established it), or `[assumed]` — so the resuming session re-verifies only what was never verified
- Every §3 step has been re-checked against the §3.5 categorization rule: a step matching any §3.5 anchor shape is mis-bucketed — move it to §3.5 (bulk deletes include removing many branches or worktrees in one command). A cited justification ("per repo convention", "per memory") does not downgrade a step's irreversibility; a step claiming a convention names the file that states it
