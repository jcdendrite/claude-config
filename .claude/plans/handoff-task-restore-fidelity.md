# Plan: Make handoff task-list restoration reliable and faithful on resume

## Context

**Goal:** ensure that when a session is resumed from a `/handoff` file, the prior
session's task list actually comes back — reliably, and with its blocking graph intact.

A recent session reproduced the bug: a handoff was written with 8 live tasks
(1 in-progress, 7 pending, with a "blocked by #N" dependency chain), then resumed
via `resume-context`. The resuming agent found an empty task list, **ignored the
handoff's §2.6 resume directive, and reconstructed tasks from the plan file
instead** — losing the live state the prior session had captured.

**Why the current mechanism fails.** #446 added §2.6 to `handoff/SKILL.md`: the
writing session serializes the live task list into the handoff `.md`, and a resume
directive asks the resuming session to recreate the items via the task tool. This
is **pure prose on both ends**. The write side works and is test-enforced (a
post-#446 handoff, `emf-metrics-4repo-push-pr-handoff.md`, populated §2.6
correctly). The **read side has zero enforcement** and two structural weaknesses:

1. **Reliability.** The recreation directive is buried mid-document in a ~130-line
   handoff appended to the system prompt. On resume the agent orients from the top,
   sees an empty live task list, and rebuilds from the richest source it recognizes —
   the plan file — never acting on §2.6. `resume-context.sh`'s generic launch prompt
   ("Continue from the handoff/brief file…") gives no instruction to restore tasks first.
2. **Fidelity.** §2.6 serializes only status + order. It drops the `blockedBy`/`blocks`
   dependency edges (the incident's "blocked by #N" graph) and `activeForm`. Even a
   perfectly-followed §2.6 loses the blocking graph.

**Why not a mechanical bridge.** The harness stores tasks at
`~/.claude/tasks/<session_id>/<n>.json`, and SessionStart hooks receive `session_id`
— so an on-disk bridge looks tempting. It is infeasible and was already rejected in
#446's own plan (`.claude/plans/handoff-task-list-persistence.md`), confirmed against
Claude Code docs: (a) the harness snapshots the task list into memory **before**
SessionStart hooks run — SessionStart's documented outputs carry no task field and
there is no reload affordance, so hook-written task files are ignored; (b) the on-disk
task format is **undocumented internal state** that can change without notice; (c) only
the agent can call `TaskCreate` in a fresh session. Building on undocumented internals
would be the "over-powered primitive / wrong foundation" the global instructions warn
against. **The read side is inherently model-driven; the fix hardens the prose at the
highest-leverage point.**

**Intended outcome:** a resumed session restores the exact pending/in-progress tasks —
including their blocking edges — as its first action, without reconstructing from the
plan file or memory.

## Approach

Prose-only hardening across three surfaces. The launch prompt is the single
highest-leverage lever (it lands at the exact moment of failure — the first turn —
and is not buried); §2.6 changes carry the authoritative rules so knowledge has one home.

1. **Launch-prompt command (reliability, `resume-context.sh`).** Change the final
   `exec` prompt (currently line 163) from the generic "Continue from…" to instruct
   restoring the task list first — conditionally, so it is a no-op for `/brief` files
   (which have no §2.6 and use §6 "Steps to ship" instead). The prompt stays a short
   *pointer*; the full recreation rules live in §2.6 (DRY — no rule duplication into the
   script). Proposed: *"Continue from the handoff/brief file loaded into your system
   prompt. If it contains a task-list resume directive, follow that directive to
   recreate the task list via the task tool before taking any other action."*

2. **§2.6 anti-pattern guard (reliability, `handoff/SKILL.md`).** Extend the resume
   directive to state that on resume **§2.6 is the authoritative source of remaining
   task state — do not reconstruct the task list from the plan file or from memory**;
   recreate exactly the items listed. This directly counters the observed incident behavior.

3. **§2.6 fidelity (faithful round-trip, `handoff/SKILL.md`).** Extend the
   serialization format to give each item a stable ordinal and to capture its blocking
   edges (which items block it) and, for the in-progress item, its `activeForm` label.
   Add an **ordinal-remap recreation rule**: recreate pending/in-progress items in order,
   then wire `blockedBy`/`blocks` by mapping each serialized ordinal to the task created
   in that position. Kept compact to respect the skill's brevity ethos (e.g.
   `3. [pending] Phase B: … (blocked by 2)`).

**Brevity discipline (plan-review B6 / skill brevity).** The handoff skill values
brevity; the changes above must not add new paragraphs. Fold the anti-pattern guard
(change 2) into the *existing* resume-directive sentence, and hold the fidelity format
(change 3) to the single compact example line — no separate format spec. `/skill-review`
is hook-enforced on the real §2.6 diff at commit time (`require-skill-review.sh`) and
will independently gate bloat; treat its brevity lens as the acceptance bar for the wording.

*Alternatives set aside:* on-disk task bridge (infeasible + fragile, see Context);
strengthening only the write side (already enforced — not the gap); extending the fix
to `/brief` (its §6 already serves as its task list; #446 deliberately scoped task
persistence to `/handoff`).

## Critical files

- **`claude/.claude/scripts/resume-context.sh`** — launch-prompt string at the final
  `exec` (line ~163). One-line change; respect the script's security-conscious style.
- **`claude/.claude/skills/handoff/SKILL.md`** — §2.6 body + resume directive
  (lines ~59–65); pre-write checklist §2.6 entry (line ~129) updated to require the
  dependency-edge capture, not just per-item status.
- **`claude/.claude/skills/tests/test_skills.py`** — update **all three** existing pinned
  assertions to the new wording so none go stale and fail CI: the two exact-string asserts
  in `TestHandoffTaskListPersistence` (`test_handoff_task_list_reads_live_state_not_memory`,
  `test_handoff_task_list_has_resume_directive`) and the §2.6 entry in the checklist-crosscheck
  test (`test_handoff_prewrite_checklist_crosschecks_section2_6`). Pin the new wording
  (anti-pattern guard, ordinal + dependency capture, remap rule). **Reuse** the existing
  `_skill_file("handoff")` helper and class structure.
- **`claude/.claude/scripts/tests/test_resume_context.py`** — add an assertion that the
  launched prompt instructs task restoration. **Reuse** the existing argv-recording launcher
  stub (the fixture at `test_resume_context.py:23` records the launcher's argv; the launch
  prompt is the positional arg after `--append-system-prompt-file <dest>` — verified this
  harness captures it, so the assertion is feasible).
- **`docs/skills.md`** — verify the §2.6 one-line description still matches; update only
  if the change makes it stale (no assumption it must change).

## Verification

- From the implementation worktree: `../../../.venv/bin/pytest claude/.claude/` (hooks +
  skills + scripts) and `../../../.venv/bin/ruff check claude/.claude/` — both green.
- Because a `SKILL.md` is edited: run `/skill-review` on the diff (hook-enforced —
  `require-skill-review.sh` blocks commit without the marker) and `/code-review` (which
  dispatches skill-review automatically). Re-read §2.6 against the handoff skill's brevity
  ethos per the repo's "run the skill on its own diff" rule.
- Manual smoke (no automated `claude -p` harness in CI, by prior decision): write a
  handoff containing an in-progress task plus a blocked pending task, run
  `resume-context` on it, and confirm (a) the launched first-turn prompt instructs task
  restoration, and (b) a fresh session recreates the tasks with the blocking edge intact
  and does not fall back to the plan file.

## Out of scope

- The on-disk / SessionStart mechanical task bridge (rejected — infeasible + fragile).
- `/brief` task handling (unchanged; §6 "Steps to ship" suffices).
- The same-session `/clear`+Read consume path: it does not use `resume-context.sh`'s
  launch prompt (the user types their own message), so it relies on the in-file §2.6
  directive alone. Left as a named residual — it is the less-common path, and `/clear`
  keeps the same session id so its task store may not even be cleared.

## Distribution note

This is a `claude-config` change under `claude/` — stow-distributed to **every** user
who clones and pulls this repo, and public. Weight review accordingly (plan-review Step 2
user surface): the prose lands in every stow user's `~/.claude/` on `git pull`.
