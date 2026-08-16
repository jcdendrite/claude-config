# State the delegate-the-investigation / read-the-artifact boundary in one place

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

Extend the existing Step 1 "Output test" in `subagent-delegation/SKILL.md`
with one sentence naming the split explicitly — **delegate the investigation,
read the artifact** — then trim the "Stays inline" list's existing artifact
bullet (line 59) to reference that test instead of restating it, so the rule
has exactly one full statement. `docs/cost-levers-considered.md`'s
"Unreconciled, noted rather than resolved" closing paragraph gets updated to
record the resolution, following that file's own dated-follow-up convention.
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

- **Extend the Output test, don't add a new block** (`anchors: root`). The
  Output test ("will my reasoning consume this command's *output*, or only a
  *conclusion drawn from it*?") already asks the question the discriminator
  answers — a diff read line by line is output consumed directly (artifact);
  a state survey is a conclusion drawn from output (investigation). A
  standalone new paragraph would restate this test in different words for no
  added coverage, at higher line cost against the 27-line headroom.
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
- **Trim line 59 to reference the Output test rather than restate it**
  (`anchors: row A2` below). Once Step 1 states the general rule, line 59's
  existing "the artifact itself, not the investigation that precedes it"
  phrasing duplicates it in different words — CLAUDE.md's single-source-of-
  truth rule treats that as a defect absent a named exception, and none
  applies (this is not DAMP test code, not per-file-standalone instructional
  prose, and not a case where the duplicate is cheaper than an abstraction —
  a one-clause reference costs less than the restatement it replaces).
- **Update `docs/cost-levers-considered.md`'s closing paragraph, not delete
  or leave it** (`anchors: row A5`). The paragraph describes current
  (pre-fix) file state — "no single file states where the boundary falls" —
  not a past event, so CLAUDE.md's Scope discipline Axis 3 preserved-record
  exception (historical incident records, changelog entries, migration
  content, stable anchor comments, commit-log narration) does not cover it;
  the file's own convention for a superseding fact is a dated follow-up
  paragraph (see the 2026-08-11, 2026-08-14, and 2026-08-15 follow-ups
  already in this file), which this edit follows rather than inventing a new
  convention.

**Assumption ledger:**

| # | Assumption | Tag |
|---|---|---|
| A1 | `subagent-delegation/SKILL.md` is 173 lines pre-edit; cap is 200; headroom is 27 lines. | `[verified: wc -l claude/.claude/skills/subagent-delegation/SKILL.md; claude/.claude/hooks/check-skill-length.sh:64-71]` |
| A2 | Line 59 ("the failure output or diff you reason over line by line — the artifact itself, not the investigation that precedes it") already uses artifact/investigation language, but as one list item's parenthetical, not a rule visible from Step 2's routing sections. | `[verified: claude/.claude/skills/subagent-delegation/SKILL.md:59]` |
| A3 | `ready-for-review/SKILL.md:51` already cites `subagent-delegation` § "Heavy command output — run inline" by name; Step 3 (lines 68–76, cumulative diff read inline) does not cite anything. | `[verified: claude/.claude/skills/ready-for-review/SKILL.md:51,68-76]` |
| A4 | `code-review/SKILL.md` reads the diff inline at Step 0 (line 11) and checklist item 12 (line 106); neither cites `subagent-delegation`. | `[verified: claude/.claude/skills/code-review/SKILL.md:11,106]` |
| A5 | `docs/cost-levers-considered.md`'s closing paragraph is the last content in the file and describes present-tense file state, not a recorded past event. | `[verified: docs/cost-levers-considered.md, full read]` |
| A6 | No other file in the repo restates the artifact/investigation split in wording this change would need to reconcile, except `docs/design-decisions.md:223` (§18), which independently states an equivalent rule in the context of a different decision (read-only probe vs. debug-and-fix agent) and is dated ADR-style prose — an Axis 3 preserved record, out of this task's scope. | `[verified: git grep "delegate.*investigation\|artifact.*investigation\|state-survey" --include=*.md .]` |
| A7 | Line 170 ("verbose `git diff` / state-survey bursts" as delegation candidates) needs no wording change — a state survey is investigation under the stated rule, so it's already correct; it only lacked the discriminator stated elsewhere. | `[engineer-verified — brief §5, "the line may be correct as written and need only the discriminator stated elsewhere"]` |

## Critical files

- `claude/.claude/skills/subagent-delegation/SKILL.md` — canonical rule owner.
  Two edits. No other lines change — Step 2's subsections (line 66 onward,
  including line 170's "Everything else") are not restructured, per brief §7.

  1. Step 1, Output test bullet (line ~27) — before:
     ```
     - **Output test:** will my reasoning consume this command's *output*, or
       only a *conclusion drawn from it*? Conclusion-only ⇒ the output is
       scratch.
     ```
     after:
     ```
     - **Output test:** will my reasoning consume this command's *output*, or
       only a *conclusion drawn from it*? Conclusion-only ⇒ the output is
       scratch. This is the delegate-the-investigation / read-the-artifact
       split: output reasoned over directly — a diff, log, or failure text
       read line by line — is an artifact and stays inline; output that only
       narrows down what to look at next is investigation and delegates.
     ```
  2. Step 1, "Stays inline" list, artifact bullet (line 59) — before:
     ```
     - The failure output or diff you reason over line by line — the artifact itself, not the investigation that precedes it.
     ```
     after:
     ```
     - The failure output or diff you reason over line by line — the artifact side of the output test above, not the investigation that precedes it.
     ```
- `docs/cost-levers-considered.md` — replace the closing paragraph (final
  section, "From a 2026-08-15 session measurement") — before:
  ```
  **Unreconciled, noted rather than resolved:** `subagent-delegation/SKILL.md`
  names "verbose `git diff` / state-survey bursts" as delegation candidates,
  while `ready-for-review` Step 3 runs the cumulative branch diff inline and
  `/code-review` reads the staged diff in the main session by design. Both
  behaviors are correct — the same skill's frontmatter excludes the diff you
  reason over line by line — but no single file states where the boundary falls.
  ```
  after:
  ```
  **2026-08-16 follow-up:** resolved. `subagent-delegation/SKILL.md`'s Step 1
  Output test now states the discriminator directly — delegate the
  investigation, read the artifact — so `ready-for-review` Step 3's inline
  cumulative diff and `/code-review`'s inline staged-diff reads follow the
  same stated rule as the "verbose `git diff` / state-survey bursts"
  delegation candidates in Step 2, rather than each being correct for reasons
  left implicit.
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
  existing citation pattern; line 59's shortened bullet still reads as a
  complete instruction on its own (not truncated mid-thought).
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
