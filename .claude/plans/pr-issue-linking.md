# GH-550 — PR bodies must use closing-keyword syntax to link/close issues

## Context

PR bodies in this repo never use GitHub's closing-keyword syntax (`Fixes #N` /
`Closes #N` / `Resolves #N`), so a merged PR neither shows up in the issue's
Development sidebar nor auto-closes the issue. PR #546 (fixing issue #544)
referenced its issue only as plain prose ("issue 544"), and #544 had to be
closed by hand after merge. A repo-wide check (issue #550) found the same gap
in PRs #536, #541, #543, #547, #548: none reference an issue in the body at
all, closing-keyword or otherwise. The `pr-description` skill
(`claude/.claude/skills/pr-description/SKILL.md`), which owns PR body content
and checks, has no mention of closing-keyword syntax anywhere. Outcome: this
repo's PRs reliably use closing-keyword syntax when they resolve a tracked
GitHub issue, without making that requirement apply to every other repo that
stows the base `pr-description` skill.

## Approach

**Add a project-layer skill, `pr-description-claude-config`, and give the
base `pr-description` skill a project-layer loading step to merge it in.**
This is the same composition mechanism `code-review`, `plan-it`,
`plan-review`, and `test-conventions` already use, and this repo already has
a live instance of the pattern for `/plan-review` at
`.claude/skills/plan-review-claude-config/SKILL.md` — same naming
(`<skill>-claude-config`), same `disable-model-invocation: true`, same
"loaded only for this repo" framing. `pr-description` is the only base skill
in the four-skill precedent group that does not yet have a "Load
project-specific layer" step; this change adds one, following the identical
wording and mechanics the other four already use, rather than inventing a new
composition shape.

**Why this repo qualifies for a project layer at all.** `pr-description`'s
own frontmatter description already says it is "Dispatched from
/ready-for-review step 5 and the /handoff pre-write checklist" — both fire on
every PR-bound branch in this repo. This repo tracks its own work in GitHub
issues (confirmed: issues #544, #545, #550 above, and the branch-naming
convention in `branch-management/SKILL.md` already documents ticket-ID-prefixed
branches like `GH-1234/checkout-redesign`). So the rule is both true and
enforceable here, and the mechanism that carries it — a project layer loaded
only inside this repo's own worktrees — is exactly the escape hatch CLAUDE.md
describes for "true here, not true everywhere."

**Alternatives weighed:**

- **(b) Put the rule in this repo's root `CLAUDE.md`.** Rejected. `CLAUDE.md`
  governs contributor workflow generally; it is not read by
  `pr-description`'s own checklist pass the way a project-layer skill file
  is deliberately globbed and merged. Landing the rule there would leave the
  skill's own "Checks" section — the mechanism that actually inspects a draft
  or existing body — silent on it, so enforcement would depend on the agent
  independently recalling a CLAUDE.md line while running an unrelated skill,
  rather than the skill's own checklist catching it. The project-layer
  mechanism exists precisely to avoid that gap.
- **(c) Add platform-agnostic phrasing directly to the base `pr-description`
  skill** (e.g., "if the PR resolves a tracked issue, use the tracker's
  closing-keyword or auto-link syntax"). Rejected as insufficiently
  actionable: trackers differ in whether such syntax exists at all (GitHub
  and GitLab support closing keywords; Jira and Linear typically rely on
  branch-name or commit-trailer conventions instead), so a generic sentence
  gives an agent nothing concrete to check or produce in a non-GitHub repo,
  while diluting the base skill with advice most consuming repos can't act
  on. The existing project-layer precedent (`plan-review-claude-config`) also
  establishes that this repo's own convention: keep the base skill's shared
  section free of even generic phrasing on a concern the project layer fully
  owns, and let the loading step alone live in the base file.

**How the check finds "the issue this PR resolves."** `ready-for-review`
step 6 already derives a generic `TICKET-ID` from the branch's first
slash-segment via `^[A-Za-z]+-[0-9]+$` (`ready-for-review/SKILL.md:136`) —
but that regex is platform-agnostic on purpose (`T-42` matches it too) and
step 6 runs *after* `pr-description` (step 5), so `pr-description` cannot
depend on step 6 having already computed it. The project-layer skill states
this repo's own specific fact — that a `GH-<N>` branch/ticket prefix names
GitHub issue `N` directly — and has `pr-description` derive it independently
from the branch name at the point the check runs, plus fall back to scanning
the draft/existing body for a bare `#<N>` or prose issue mention if the
branch carries no `GH-<N>` prefix. This keeps the numeric mapping
(`GH-<N>` → issue `N`) a project-layer fact, not a base-skill assumption.

### Assumption ledger

```
Root: pr-description's Checks section has no rule requiring closing-keyword
issue references, so PRs that resolve a GitHub issue in this repo routinely
under-link or under-close it, and the base skill can't carry that rule
unconditionally since it ships to non-GitHub repos.

Row 1 [mechanism]: add a "Load project-specific layer" step to
pr-description/SKILL.md, glob .claude/skills/pr-description-*/SKILL.md,
merge via Read tool — anchors: root — this is the repo's own established
composition mechanism (4 existing base skills), not a new one; using it
keeps pr-description consistent with its siblings instead of inventing a
second way to layer project-specific content onto a skill.
Row 2 [mechanism]: new pr-description-claude-config/SKILL.md carrying the
closing-keyword rule and the GH-<N>-branch-to-issue-<N> mapping — anchors:
row1 — this is where a GitHub-specific, this-repo-specific fact belongs per
CLAUDE.md's "Global skill bodies stay platform-agnostic... stack-specific
examples belong in a project-layer skill."
Row 3 [assumption]: this repo already has a live project-layer skill at this
exact naming shape [verified: .claude/skills/plan-review-claude-config/SKILL.md
— name `plan-review-claude-config`, disable-model-invocation: true, one-line
description "Project-specific layer for /plan-review, loaded only when
reviewing plans in the claude-config repo itself."] — anchors: row2
Row 4 [assumption]: four base skills already implement the "Load
project-specific layer" step with byte-for-byte-similar wording — code-review
Step 0.5 ("If a project-specific layer exists for this skill, load it now.
Glob for `.claude/skills/code-review-*/SKILL.md` from the repo root (resolved
via `git rev-parse --show-toplevel`); if exactly one matches, read it with
the Read tool and merge its checklist into the items below. If multiple
match, list them and stop... If none match, proceed without a layer.") and
matching steps in plan-it (Step 2.5), plan-review (Step 2.5), test-conventions
[verified: grep -rln "project-specific layer" claude/.claude/skills/*/SKILL.md
→ code-review, plan-it, plan-review, test-conventions] — anchors: row1
Row 5 [assumption]: a dedicated test class already enforces this mechanism's
shape and currently covers exactly those four skills
[verified: claude/.claude/skills/tests/test_skills.py:1056-1109,
TestProjectLayerUsesReadNotSkill.PARENT_SKILLS =
["code-review", "plan-it", "plan-review", "test-conventions"] — asserts each
parent's project-layer section contains "Read tool" and not "Skill tool"] —
anchors: row1 — pr-description must be added to this list or the addition is
untested by the repo's own existing coverage for this exact mechanism.
Row 6 [assumption]: pr-description/SKILL.md currently has no "## ...
project-specific layer ..." heading of any kind [verified: full read of
claude/.claude/skills/pr-description/SKILL.md, headings are "Resolve the
PR...", "What the body must carry", "Checks", "Delivering the body" — no
Step-numbered structure and no project-layer section] — anchors: row1
Row 7 [assumption]: pr-description is registered as skillOverrides:
"name-only" in claude/.claude/settings.json:51, and project-layer skills
(loaded via Read, never Skill()) require no settings.json registration of
their own [verified: settings.json grep; plan-review-claude-config has no
settings.json entry] — anchors: row2 — no settings.json change needed for
the new pr-description-claude-config skill directory.
Row 8 [assumption]: _model_invokable_skills() in test_skills.py excludes any
skill_dir whose frontmatter contains "disable-model-invocation: true" before
it would check for TRIGGER/DO NOT TRIGGER blocks [verified:
test_skills.py:192-194] — anchors: row2 — the new project-layer skill's
one-line description needs no TRIGGER block, matching
plan-review-claude-config's shape exactly.
Row 9 [assumption]: ready-for-review/SKILL.md:136 derives TICKET-ID from the
branch's first slash-segment via regex ^[A-Za-z]+-[0-9]+$, generically (not
GitHub-specific), and this derivation runs in step 6 — after pr-description
runs in step 5 [verified: ready-for-review/SKILL.md:125 (step 5 invokes
pr-description), :136 (step 6 TICKET-ID derivation)] — anchors: root —
pr-description cannot reuse step 6's already-computed value and must derive
its own GH-<N> extraction, scoped to this repo's project layer since the
generic regex alone doesn't establish that N is a GitHub issue number.
Row 10 [assumption]: branch-management/SKILL.md documents the GH-<N>/<slug>
ticket-prefixed branch-naming convention this repo actually uses (examples:
GH-1234/checkout-redesign; this session's own branch is
GH-550/pr-issue-linking) [verified: branch-management/SKILL.md:26,35 and
git branch --show-current] — anchors: row2
Row 11 [assumption]: PRs #536, #541, #543, #547, #548 reference no issue in
the body at all, and PR #546 references #544/#545 only as plain prose, never
via closing keyword [engineer-verified — cited in issue #550's body from a
prior session's `gh pr view --json body` grep] — anchors: root
Row 12 [assumption]: two doc sites enumerate the current four project-layer
parent skills by name and would go stale if pr-description becomes a fifth
without a matching update [verified: docs/design-decisions.md:59 —
"`/plan-it`, `/code-review`, `/plan-review`, and `/test-conventions` each
check for a project-specific layer..."; docs/skills.md:170 — same four-skill
list; docs/skills.md:172 — the four concrete `.claude/skills/<parent>-
<project>/SKILL.md` paths] — anchors: row1 — flagged in /plan-review per
CLAUDE.md's single-source-of-truth rule; these are the only sites found
enumerating this specific list (checked docs/skills.md, README.md,
docs/design-decisions.md, docs/transcript-analysis.md, test_skills.py via
grep for "plan-it.*code-review.*plan-review", "project-specific layer",
"project-layer").
```

## Critical files

- `claude/.claude/skills/pr-description/SKILL.md` — insert a "Load
  project-specific layer" section, placed after the mode-selection intro
  paragraph and before "## What the body must carry" (so a merged
  project-layer check item is available to both author-mode drafting and the
  Checks pass). Wording follows the existing four-skill precedent exactly
  (Row 4), substituting `pr-description-*` for the glob and "merge its check
  items into the Checks section below" for the merge target, since
  pr-description isn't numbered-step and its equivalent section is named
  "Checks," not "the items below."
- `claude/.claude/skills/pr-description-claude-config/SKILL.md` — new.
  Frontmatter: `name: pr-description-claude-config`, `disable-model-invocation:
  true`, one-line description mirroring `plan-review-claude-config`'s shape
  ("Project-specific layer for /pr-description, loaded only when authoring or
  syncing PR bodies in the claude-config repo itself."). Body states: (1)
  this repo tracks work in GitHub issues; (2) a branch/ticket prefix of the
  form `GH-<N>` names GitHub issue `N` directly (per
  `branch-management/SKILL.md`'s ticket-ID convention); (3) when the current
  branch carries a `GH-<N>` prefix, or the draft/existing body already
  mentions an issue by number in prose, the body must reference it using a
  GitHub closing keyword (`Fixes #N`, `Closes #N`, or `Resolves #N`) rather
  than plain prose — rewrite a prose mention into closing-keyword form rather
  than leaving both; (4) if the branch carries no ticket prefix and the body
  mentions no issue number, this check has nothing to do — most PRs
  legitimately close nothing.
- `claude/.claude/skills/tests/test_skills.py` —
  `TestProjectLayerUsesReadNotSkill.PARENT_SKILLS` (line 1066): add
  `"pr-description"`, so the repo's existing Read-tool-not-Skill-tool
  coverage extends to the new step (Row 5). No other test class needs a new
  entry: pr-description is already `name-only` (Row 7) and the new project
  layer is `disable-model-invocation: true` (Row 8), so both are excluded
  from `_model_invokable_skills()` on the same paths `plan-review-claude-config`
  already takes today.
- `docs/design-decisions.md:59` — the sentence "`/plan-it`, `/code-review`,
  `/plan-review`, and `/test-conventions` each check for a project-specific
  layer at skill start..." enumerates exactly the four current parent
  skills; add `` `/pr-description` `` to the list (Row 12).
- `docs/skills.md:170,172-173` — `:170` names the same four-skill list in
  matching prose; `:172` enumerates the concrete layer paths
  (`.claude/skills/plan-it-<project>/SKILL.md`,
  `.claude/skills/code-review-<project>/SKILL.md`, etc.) and `:173`'s
  surrounding text references the same prefix set. Add the
  `.claude/skills/pr-description-<project>/SKILL.md` path and
  `pr-description-` prefix alongside the other four (Row 12).

**Reuse, not new mechanism:** the glob-and-merge wording, the
`disable-model-invocation: true` + one-line-description shape, and the
`PARENT_SKILLS` test list are all existing patterns being extended, not
new design.

## Verification

Run from the worktree (contributor `.venv` lives at the main worktree root
only, three levels up):

1. `../../../.venv/bin/pytest claude/.claude/` — confirms the new
   `pr-description-claude-config/SKILL.md` passes existing frontmatter/shape
   tests and that the extended `PARENT_SKILLS` list passes
   `test_project_layer_uses_read_tool` / `test_project_layer_does_not_use_skill_tool`
   against the new pr-description section.
2. `../../../.venv/bin/ruff check claude/.claude/` — only touches
   `test_skills.py` on the Python side.
3. `/skill-review` on both changed/new `SKILL.md` files (hook-enforced) —
   confirm the new project-layer skill matches `plan-review-claude-config`'s
   established shape and the base-skill addition matches the other three
   parents' wording closely enough to read as one system.
4. **Dogfood on this very branch.** This PR is `GH-550/pr-issue-linking`,
   resolving issue #550 — when `/ready-for-review` runs on this branch,
   confirm step 5 (`pr-description`) loads the new project layer, detects
   the `GH-550` branch prefix, and produces a body containing `Fixes #550`
   (or `Closes #550` / `Resolves #550`), not a prose mention. This is the
   first real exercise of the new check, not a synthetic one.
5. Regression check: confirm `pr-description` invoked from a branch with no
   ticket-ID prefix (e.g. temporarily reason through a branch name without a
   `GH-<N>` segment) produces no spurious closing-keyword insertion — the
   check must be a no-op when there is nothing to close.

## Out of scope

- **Retrofitting closing-keyword references onto already-merged PRs**
  (#536, #541, #543, #546, #547, #548). Those are closed history; the fix is
  forward-looking only.
- **Extending the rule to non-`GH-<N>` tracker prefixes** (e.g. a
  hypothetical `T-<N>` Linear prefix). This repo only uses GitHub issues
  today (Row 10); adding cross-tracker mapping logic with no second tracker
  to validate against would be speculative.
