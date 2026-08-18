# Extend the 🤖 attribution trailer to PR comments and the top of PR descriptions

## Context

Right now the `🤖 Generated with [Claude Code](https://claude.com/claude-code)`
disclosure line only appears at the bottom of PR descriptions
(`pr-description/SKILL.md`); PR comment replies carry a separate
`**[Claude Code]**` inline prefix instead. The user wants the same trailer
also posted on every PR comment reply, and also placed at the top of every
PR description (in addition to the existing bottom placement), so that AI
authorship is disclosed consistently across both surfaces. This plan edits
`respond-pr/SKILL.md` and `pr-description/SKILL.md` only.

## Approach

Add the trailer as an additional, literal-text requirement in both skills —
appended to every posted PR comment reply in `respond-pr`, and duplicated as
the first line (in addition to the existing last line) of every PR
description body in `pr-description`. Nothing about either skill's existing
mechanisms changes; this is copy added to bodies the skills already
construct.

**Root problem:** PR comment replies and the top of PR descriptions don't
carry the same AI-authorship disclosure line already required at the bottom
of PR descriptions.

**Givens:**
- G1: The literal trailer text is already the canonical disclosure string —
  this plan reuses it verbatim, it doesn't invent new copy.
  [verified: claude/.claude/skills/pr-description/SKILL.md:69]
- G2: The `**[Claude Code]**` prefix in `respond-pr` is not interchangeable
  with the trailer — it's the authorship marker the Guidelines section's
  PATCH-safety check matches against (`case "$TARGET_BODY" in '**[Claude
  Code]**'*) ...`), so it must stay; the trailer is additive.
  [verified: claude/.claude/skills/respond-pr/SKILL.md:101-109]
- G3: `require-respond-pr.sh`'s enforcement is a per-session marker/PID
  check, not a body-content check, so the hook needs no change.
  [verified: claude/.claude/hooks/require-respond-pr.sh]

**Mechanisms:**
- M1 (respond-pr): append the trailer, on its own line after a blank line,
  to every reply body posted or PATCH-corrected — in addition to the
  existing `**[Claude Code]**` prefix. anchors: row G2
- M2 (pr-description): add the trailer as the first line of the body
  (blank line, then existing content), keeping the existing last-line
  placement — both, not a move. anchors: root
  `[engineer-verified]` — user chose "both top and bottom" over "top only"
  when asked directly this session.
- M3 (pr-description): reword the existing "A first line that stands
  alone" requirement to mean the first line of prose *after* the trailer,
  so the two requirements don't contradict each other (a literal top
  trailer would otherwise become the "first line" that's supposed to
  summarize the change for a reader skimming a PR list / search snippet).
  anchors: row M2

No mechanism here is heavier than a copy edit to two existing skill
bodies — the over-powered-primitive check doesn't apply.

## Critical files

- `claude/.claude/skills/respond-pr/SKILL.md`
  - **Attribution** section: state the trailer requirement alongside the
    existing prefix requirement; update the example call to show both.
  - **Step 7** (the three `gh api ... replies` / `.../comments` snippets):
    append the trailer to each example body.
  - **Guidelines** PATCH-correction snippet: the corrected-text example
    should also end with the trailer, since it's a full-body replacement.
  - Leave the Step 5 "Worked examples" prose blocks untouched — they
    illustrate required-field depth for the classification table, not
    literal call bodies, and already carry a disclaimer to that effect.
- `claude/.claude/skills/pr-description/SKILL.md`
  - **"What the body must carry"**: change the trailer bullet from
    "as the last line" to "at both the top and the bottom."
  - Same section: reword "A first line that stands alone" per M3.
  - **Checks / "Flag and fix"**: add a bullet so sync mode repairs older
    PR bodies that only carry the bottom trailer (add the missing top
    one) instead of just flagging author-mode drafts.

Reuse: no new helpers or shared files — per this repo's CLAUDE.md, the
duplicated trailer literal across the two skill bodies is intentional
(no shared partials across skills).

## Verification

- Read both edited files end-to-end to confirm the two attribution
  requirements in `pr-description` don't contradict each other (the
  specific risk this plan calls out in M3).
- `../../../.venv/bin/pytest claude/.claude/hooks/tests/test_hook_alignment.py claude/.claude/hooks/tests/test_require_respond_pr.py` —
  confirms the `HOOK_TEST_FIXTURE` fenced blocks in `respond-pr/SKILL.md`
  (untouched by this change) still match what the hook-alignment suite
  expects, and that `require-respond-pr.sh` behavior is unaffected.
- `/skill-review` on both files (hook-enforced by
  `.claude/rules/skill-and-agent-self-review.md` / `require-skill-review.sh`
  before commit).
- `/code-review` before commit, per repo CLAUDE.md.

## Out of scope

- `README.md`, `docs/skills.md`, `docs/hooks.md`, `docs/walkthrough.md` —
  their one-line mentions of "`[Claude Code]` attribution" stay accurate at
  their existing level of generality and aren't restated per line item here.
- `docs/case-studies/worktree-enforcement.md` and
  `.claude/plans/GH-477-pr-description-authoring.md` — preserved historical
  record of past PR/plan state, not descriptions of current behavior to
  keep in sync.
