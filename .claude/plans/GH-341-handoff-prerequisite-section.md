# Handoff skill: flag incomplete prerequisite work

GitHub issue: jcdendrite/claude-config#341

## Context

When a session writes a handoff mid-task, the file captures *what happened* but
not *what was decided and then scoped out*. A receiving session reads the
handoff faithfully and executes from §3, with no signal that a prerequisite
phase was never done. The issue's reproduction: a handoff scoped to a client-side
fix omitted an earlier-defined "server-side investigation required before server
fix" phase; the receiving session went 75 tool calls deep — tests, code-review
fixes, a PR under human review — before the user caught that the prerequisite
investigation (which could have invalidated the whole fix) never ran.

The receiving session **cannot recover what was scoped out** — the information
isn't in the handoff. Only the writing session has that context, so the fix is
writer-side. **Intended outcome:** the handoff writer is required to name the
broader plan, enumerate prerequisite phases with their completion status, flag
incomplete ones in a dedicated section, and distinguish *why* the handoff is
being written (phase-complete vs. ran out of context).

This is stow-distributed: `claude/.claude/skills/handoff/SKILL.md` is symlinked
to `~/.claude/skills/handoff/SKILL.md` for every user who clones and stows this
repo. The change ships to all of them on `git pull`.

## Approach

Single-file edit to `claude/.claude/skills/handoff/SKILL.md` (currently 81 lines,
well under the 200-line skill cap). Three additive changes, matching the existing
terse imperative voice and the established `§3.5` half-section pattern:

1. **§2 Status** — add a `**Handoff reason:** phase-complete | context-limit`
   line. The reason is a whole-handoff property that shapes the receiver's posture
   for the entire file, so it lives at the top with Status rather than buried in a
   subsection. (Confirmed with user over the issue's literal placement, which
   folded the reason into §2.5.)

2. **New §2.5 Incomplete prerequisites** — inserted between §2 Status and §3 Next
   concrete step. Mirrors §3.5's shape: a required section with an explicit
   "write 'None.'" fallback. Content:
   - Name the broader plan this handoff is a stop-point inside of, and the current
     phase, if this session executed one phase of a multi-phase plan.
   - Enumerate prerequisite phases defined earlier with their completion status;
     incomplete/unverified ones appear here, not silently omitted.
   - If the handoff reason is context-limit, note what was mid-flight (started but
     unfinished tasks, open tool calls, pending verifications).

3. **Pre-write checklist** — add two verify-items in the existing bullet voice:
   - §2.5 is populated; incomplete prerequisites appear there, not silently omitted.
   - If the handoff reason is context-limit, §2.5 notes what was mid-flight.

**Why a new §2.5 rather than folding into §3.5 or §6:** §3.5 is scoped to
irreversible/shared-state *action authorization*; §6 is a catch-all for open
questions and deferred decisions. The whole point of the fix is to make skipped
prerequisites *prominent* — burying them in §6 reproduces the failure. A
dedicated section positioned right after Status, before the receiver reaches the
"next step" they'd otherwise execute, is the correct altitude. §2.5 also parallels
the existing §3.5 precedent, so it reads as a known pattern, not a novelty.

**Out-of-scope guards held:** the verbatim artifact preamble is not touched (it
governs irreversible-action authorization, a different failure mode). No
reader-side / receiving-session behavior change. No cross-session prerequisite
tracking. These match the issue's own "Out of scope" list.

**Frontmatter `description` is deliberately left unchanged.** It already
enumerates §1–§7 but omits the existing §3.5 half-section, so omitting §2.5 from
it is the consistent choice — do not add §2.5 to the description. No `docs/` or
README sync is needed either: `docs/skills.md` describes handoff at a one-line
role-summary altitude, not as an exhaustive section list.

## Critical files

- `claude/.claude/skills/handoff/SKILL.md` — the only file changed. Edit §2,
  insert §2.5, extend the Pre-write checklist.

**Reuse / patterns to mirror (no new code):**
- §3.5 "Pending engineer authorization" (same file, lines 32–45) — copy its
  section shape: heading, one-line purpose, `If none, write "None."` fallback.
- Existing Pre-write checklist bullets (lines 71–80) — match their terse,
  imperative phrasing for the two new items.

## Verification

1. Re-read the edited `SKILL.md` end-to-end: section numbering reads
   §1 → §2 → §2.5 → §3 → §3.5 → §4…; §2.5 has the "None." fallback; the two new
   checklist items match the voice of the surrounding bullets.
2. `.venv/bin/ruff check claude/.claude/` and `.venv/bin/pytest claude/.claude/`
   (run from a linked worktree as `../../../.venv/bin/...`). No test asserts
   handoff body structure, so the suite should pass unchanged; this confirms no
   collateral breakage (e.g. the frontmatter/trigger-block tests in
   `skills/tests/test_skills.py`).
3. **Required gate:** editing a `SKILL.md` triggers the `require-skill-review`
   hook, which blocks `git commit` until `/skill-review` writes its
   behavioral-equivalence marker. Run `/skill-review` on the diff (per the repo
   rule "when editing a skill, run the skill on its own diff") and confirm the
   added prose survives the skill's own brevity/voice checks before committing.
4. `/code-review` before presenting (dispatches `/skill-review` for the SKILL.md
   file type automatically).
5. Sanity dry-run: mentally apply the new §2.5 to the issue's reproduction case —
   a writer following it would have listed the server-side investigation phase as
   an incomplete prerequisite, giving the receiver the missing signal.

## Notes

- **No regression test added** (confirmed with user). The change is content
  within one skill, not a cross-file convention other files must follow; the
  hook-enforced `/skill-review` is the real quality gate. A string-presence test
  (`assert "§2.5" in body`) would be a weak tautology guard with little value.
- Worktree enforcement is active — do this on a linked worktree
  (`git worktree add .claude/worktrees/<slug> -b <slug>`). The branch slug derives
  from this plan file once plan mode exits.
