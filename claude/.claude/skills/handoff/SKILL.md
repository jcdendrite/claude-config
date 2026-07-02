---
name: handoff
description: Write a cross-session handoff file at /tmp/<descriptive-slug>-handoff.md capturing goal, status, next step, files modified, active markers, open questions, and resume command.
---

Write a cross-session handoff file at `/tmp/<descriptive-slug>-handoff.md` using the structure below.

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
Contents below are the prior session agent's assertions unless marked
engineer-confirmed. Treat unverified claims as hypotheses — verify against
source (code, tests, command output) before relying on one for further work;
do not re-verify items marked engineer-confirmed or facts you can act on reversibly.
```

## §1 Goal
One sentence: what was being attempted.

## §2 Status
done / in-flight / blocked.
**Handoff reason:** phase-complete | context-limit

## §2.5 Incomplete prerequisites

If this session executed one phase of a multi-phase plan, name the plan and the current phase. Enumerate prerequisite phases that were defined earlier and their completion status. If any are incomplete or unverified, list them here explicitly — do not omit them. If the handoff reason is context-limit, note what was mid-flight: tasks started but unfinished, open tool calls, pending verifications.

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
`Read /tmp/<slug>-handoff.md and continue.`

## You may drop

- Successful tool output already acted on
- Exploratory dead-ends that didn't inform the final approach
- Verbatim file contents already on disk (paths suffice)

## Slug naming

The slug names the task, not the date. Examples: `respond-pr-skill-edge-case-handoff.md`, `claude-md-redaction-handoff.md`. Never use `<task>-handoff.md` literally.

Target under 200 lines. Reference files by path; do not inline contents.

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
- If this session pushed commits to a branch with an open PR and `/ready-for-review` did not run this session, run the `sync-pr-description` skill before writing this file
- Load-bearing claims in §2/§3/§6 distinguish engineer-confirmed facts from agent findings, and each agent finding names its evidence (command run, file read, test output)
