# State the locate-and-report / read-and-reason boundary in one place

## Context

`claude/.claude/skills/subagent-delegation/SKILL.md` gives three separate
instructions about verbose tool output (a diff, log, or check result) — one
says the artifact under direct reasoning stays inline (line 59, echoed in the
frontmatter's DO NOT TRIGGER clause), one inlines check output for a cost
reason (line 68's "Heavy command output"), one names diffs and state surveys
as delegation candidates (line 170) — but no sentence states the test that
tells a reader which instruction applies to the output in front of them. A
session that reads only one of the three delegates a diff it should have read,
or inlines a state survey it should have delegated. This work states that
discriminator once, in the file CLAUDE.md already routes readers to for
delegation decisions, so a session facing verbose output can resolve the
question from one place instead of cross-reading three skill bodies.

## Approach

Point the existing Step 1 "Output test" in `subagent-delegation/SKILL.md`
at the split the file already names elsewhere — **locate-and-report
(delegable) vs. read-and-reason (not)**, defined at Step 2's "Codebase
discovery" (lines 125-126) — rather than coining a new label. Line 59 is
left untouched: its existing "artifact... not the investigation" wording
already applies the same test to its own case without contradicting it.
`docs/cost-levers-considered.md`'s closing section gets a new dated
paragraph appended below the existing "Unreconciled, noted rather than
resolved" paragraph, which stays in place unedited — matching the
append-below-without-deleting shape every other dated follow-up in that
file already uses for a revised entry, and naming the same reused term.
No other file changes.

**Root problem:** `subagent-delegation/SKILL.md` states a verbose-output
delegation rule in three places with no named discriminator connecting them.

No givens beyond reach: the 200-line cap this plan works under
(`check-skill-length.sh`'s `limit_for()` default case, row A1 below) is a
condition this repo's own script controls, so this plan could change it
instead of compressing to fit — it deliberately doesn't, and that decision
is recorded in **Out of scope** with its reason, not carried here as a
given.

**Per-mechanism justification:**

- **Point the Output test at the existing locate-and-report / read-and-
  reason split, don't coin a new label** (`anchors: root`). The Output
  test ("will my reasoning consume this command's *output*, or only a
  *conclusion drawn from it*?") already asks the question that split
  answers — a diff read line by line is read-and-reason; a state survey
  is locate-and-report. Naming the same distinction with a new label
  (e.g. "delegate-the-investigation / read-the-artifact") would give the
  file a third phrasing for one discriminator, alongside line 59's plain
  description and Step 2's named split — a duplication CLAUDE.md's
  single-source-of-truth rule forbids absent a named exception, and none
  applies here. A one-clause pointer to the existing name costs fewer
  lines than a restated example or an invented label either would.
  Over-powered-primitive check: two lighter alternatives considered and
  rejected —
  1. *Put the statement in CLAUDE.md's Working Style bullet instead* —
     heavier: CLAUDE.md loads every session in every repo on this machine,
     while the skill loads only when a session is actively making a
     delegation decision. The existing CLAUDE.md bullet already defers to
     this skill by name; duplicating the content back into CLAUDE.md would
     make CLAUDE.md the second site restating what the skill states once.
  2. *Add cross-reference footnotes at `ready-for-review` Step 3 and
     `code-review` Step 0/12* — heavier: costs lines in three more files
     (`ready-for-review/SKILL.md`, `code-review/SKILL.md`, and this file) to
     restate a single-sentence rule already stated once at its canonical
     home, which CLAUDE.md already routes readers to. `ready-for-review:51`'s
     existing citation of the "Heavy command output" section by name is a
     narrower, load-bearing case (a specific command class, not the general
     rule) and isn't disturbed.
- **Leave line 59 untouched** (`anchors: row A2`). Line 59's existing
  wording ("the artifact itself, not the investigation that precedes it")
  already uses the terms the Output test now defines; it is an application
  of the general rule to a specific case, not a restatement of the rule's
  definition, so CLAUDE.md's single-source-of-truth rule doesn't reach it —
  reusing a defined term isn't duplicating the knowledge that defines it.
  Editing it anyway would be a no-op change with review cost and no benefit.
- **Append below `docs/cost-levers-considered.md`'s closing paragraph,
  don't replace or delete it** (`anchors: row A5`). The existing
  "Unreconciled, noted rather than resolved" paragraph is a dated record of
  a prior investigation's conclusion; every other revision in this file
  (the 2026-08-11, 2026-08-14, and 2026-08-15 follow-ups) appends a new
  dated paragraph beneath the row or paragraph it revises rather than
  deleting it — replacing the paragraph outright would break that
  established append-only pattern and lose the file's own historical trail
  ("consolidates them," per its header). The new paragraph states the fact
  plainly (where the discriminator now lives, named with the same
  locate-and-report / read-and-reason term SKILL.md uses) without
  narrating the fix's own before/after history, which belongs in the
  commit message.

**Assumption ledger:**

| # | Assumption | Tag |
|---|---|---|
| A1 | `subagent-delegation/SKILL.md` is 173 lines pre-edit; cap is 200; headroom is 27 lines. | `[verified: wc -l claude/.claude/skills/subagent-delegation/SKILL.md; claude/.claude/hooks/check-skill-length.sh:64-71]` |
| A2 | Line 59 ("the failure output or diff you reason over line by line — the artifact itself, not the investigation that precedes it") already uses artifact/investigation language, but as one list item's own case, not a rule visible from Step 2's routing sections; it is left unedited by this change. | `[verified: claude/.claude/skills/subagent-delegation/SKILL.md:59]` |
| A3 | `ready-for-review/SKILL.md:51` already cites `subagent-delegation` § "Heavy command output — run inline" by name; Step 3 (lines 68–76, cumulative diff read inline) does not cite anything. | `[verified: claude/.claude/skills/ready-for-review/SKILL.md:51,68-76]` |
| A4 | `code-review/SKILL.md` reads the diff inline at Step 0 (line 11) and checklist item 12 (line 106); neither cites `subagent-delegation`. | `[verified: claude/.claude/skills/code-review/SKILL.md:11,106]` |
| A5 | `docs/cost-levers-considered.md`'s closing paragraph is the last content in the file; every prior revision in the file appends a new dated paragraph beneath the row/paragraph it revises rather than replacing it. | `[verified: docs/cost-levers-considered.md, full read]` |
| A6 | No other file in the repo restates the artifact/investigation split in wording this change would need to reconcile, except `docs/design-decisions.md:223` (§18), which independently states an equivalent rule in the context of a different decision (read-only probe vs. debug-and-fix agent) and is dated ADR-style prose — an Axis 3 preserved record, out of this task's scope. | `[verified: git grep "delegate.*investigation\|artifact.*investigation\|state-survey" --include=*.md .]` |
| A7 | Line 170 ("verbose `git diff` / state-survey bursts" as delegation candidates) needs no wording change — a state survey is locate-and-report under the stated split, so it's already correct; it only lacked the discriminator stated elsewhere. | `[engineer-verified — brief §5, "the line may be correct as written and need only the discriminator stated elsewhere"]` |
| A8 | `subagent-delegation/SKILL.md:125-126` already names "the split" as "locate-and-report (delegable) vs. read-and-reason (not)," in Step 2's Codebase discovery section — the Output test can point at this existing name instead of coining one. | `[verified: claude/.claude/skills/subagent-delegation/SKILL.md:125-126]` |

## Critical files

- `claude/.claude/skills/subagent-delegation/SKILL.md` — canonical rule
  owner. One edit; line 59 stays untouched (see Approach). No other lines
  change — Step 2's subsections (line 66 onward, including line 170's
  "Everything else") are not restructured, per brief §7.

  Step 1, Output test bullet (line ~27) — before:
  ```
  - **Output test:** will my reasoning consume this command's *output*, or
    only a *conclusion drawn from it*? Conclusion-only ⇒ the output is
    scratch.
  ```
  after:
  ```
  - **Output test:** will my reasoning consume this command's *output*, or
    only a *conclusion drawn from it*? Conclusion-only ⇒ the output is
    scratch — the same locate-and-report vs. read-and-reason split named
    under Codebase discovery below.
  ```
- `docs/cost-levers-considered.md` — append below the closing paragraph
  (final section, "From a 2026-08-15 session measurement"); the existing
  "Unreconciled, noted rather than resolved" paragraph is unchanged:
  ```
  **2026-08-16:** `subagent-delegation/SKILL.md` Step 1's Output test now
  names the discriminator directly — the locate-and-report vs.
  read-and-reason split Step 2 already defines — so `ready-for-review`
  Step 3 and `/code-review`'s inline diff reads are governed by that
  stated rule.
  ```
  Reuse: matches the existing dated-follow-up paragraph format used three
  times earlier in the same file rather than inventing new prose shape.

**Not edited** (decided against in Approach): `ready-for-review/SKILL.md`,
`code-review/SKILL.md`, `docs/design-decisions.md`,
`claude/.claude/hooks/check-skill-length.sh`.

## Verification

- `wc -l claude/.claude/skills/subagent-delegation/SKILL.md` ≤ 200 (the
  enforced cap) after editing.
- Diff is markdown-only — no test suite or lint run needed;
  `/ready-for-review` step 2 exempts non-executable diffs. If
  `check-skill-length.sh` ends up touched for any reason (not expected),
  run `../../../.venv/bin/pytest claude/.claude/` and
  `scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck`
  from the worktree.
- Read the edited Step 1 block end-to-end and confirm: the new sentence
  states the split without contradicting line 170 or `ready-for-review:51`'s
  existing citation pattern, and without duplicating line 59's existing
  wording.
- `/skill-review` (hook-enforced on `git commit` for `SKILL.md` changes),
  then `/code-review`.

## Out of scope

- Editing `docs/design-decisions.md:223` (§18) to cross-reference the new
  canonical statement — it is a dated architecture-decision record (Axis 3
  preserved content), and reconciling its overlapping phrasing isn't required
  for this task's correctness. Raised here for a human call, not actioned.
- File-path handoff of diff text to reviewers lacking `Bash`
  (`skill-fidelity-reviewer`, `comment-discipline-reviewer`) — measured and
  rejected in the same `docs/cost-levers-considered.md` section this task
  edits; not reopened.
- Promoting the one-off transcript scanner into
  `claude/.claude/scripts/transcript-analysis.py` — separate, larger change.
- Restructuring subagent-delegation's "Step 2 — Pick the right subagent"
  subsections.
- Raising the 200-line cap in `check-skill-length.sh`.
- `docs/cost-levers-considered.md:38`'s "small-subscription-client" /
  "API-key-client" wording — pre-existing, unrelated issue.
- Editing the frontmatter `DO NOT TRIGGER` clause's "the specific failure
  output or diff you reason over line by line" phrase — it's already a
  one-clause summary, not a restatement needing compression, and the
  description is always-loaded budget (every session, every repo) where a
  cross-reference to the body costs more than the clause it would replace.
