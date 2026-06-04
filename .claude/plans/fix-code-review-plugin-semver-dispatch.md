# Plan: Add plugin-semver dispatch to code-review Domain: Claude Code config

## Context

Add a `plugin-semver` dispatch line to the Domain: Claude Code config section of
`code-review/SKILL.md` so that `/code-review` explicitly invokes `plugin-semver`
whenever changed files are inside a plugin directory. Without this line, the
structured skill dispatch never evaluates the `plugin-semver` auto-trigger — the
model follows the skill's explicit instructions, skipping the version bump check
entirely. Surfaced this session when a `SKILL.md` edit inside
`plugins/lovable-cloud/` passed `/code-review` and `/skill-review` cleanly, but
nobody caught the missing `plugin.json` version bump until after PR #378 merged.

## Approach

Add one prose line at the end of the "## Domain: Claude Code config" section in
`claude/.claude/skills/code-review/SKILL.md` (currently line 167), following the
same "For X, invoke Y" pattern used by the other dispatches in that section:

> When changed files are inside a plugin directory (identified by `.claude-plugin/plugin.json`
> present in the plugin's tree), or when `.claude-plugin/marketplace.json` is modified,
> invoke `plugin-semver` for version-bump discipline.

**Why this location:** The Domain: Claude Code config section is where all
Claude Code config file type dispatches live. The prose dispatch pattern is already
established for `skill-review`, `agent-review`, `ai-instruction-and-memory-files`,
and `claude-hook-review` — this adds `plugin-semver` to the same list at the same
granularity (file-path condition → skill invocation).

**Alternatives considered and set aside:**
- Adding to the Ripple effect triage Change-type table: that table governs
  specialist-reviewer spawns, not skill invocations. `plugin-semver` is a
  pre-commit discipline check, not a post-commit specialist reviewer. Wrong table.
- A new numbered checklist item (e.g. item 36): numbered items are for findings
  to flag; dispatching to another skill is already expressed as prose in this
  section. A numbered item would imply the model should flag something inline
  rather than route to the skill.

## Critical files

- **Modify:** `claude/.claude/skills/code-review/SKILL.md`
  — "## Domain: Claude Code config" section, after the final line (`For hook reviews...`
  at line ~167), add the one prose sentence above.

This change is in `claude/.claude/skills/`, so the stow path `~/.claude/skills/code-review/SKILL.md`
goes live on `git pull`. No other files change.

**Reuse:** No code reuse — prose addition only. The "For X, invoke Y" pattern
is the established dispatch shape (3 prior examples in the same section).

## Verification

1. Read the section back and confirm the new line appears after `claude-hook-review`
   and uses the "For X, invoke Y" voice.
2. Run `/skill-review` on the diff (hook-enforced gate; behavioral-equivalence
   audit required since this is a SKILL.md change).
3. Run `/code-review` on the cumulative diff before pushing.
4. Smoke test: mentally apply the updated skill to a hypothetical diff that edits
   `plugins/foo/skills/bar/SKILL.md` — the dispatch should now say "invoke
   `plugin-semver`" for that case.

## Execution sequence (post-approval)

1. Derive slug: `fix-code-review-plugin-semver-dispatch`. Create branch + worktree from
   fresh `main` tip (enforcement active):
   `git -C ~/MyCode/claude-config worktree add .claude/worktrees/fix-code-review-plugin-semver-dispatch -b fix-code-review-plugin-semver-dispatch`
2. Move this plan file to `.claude/plans/fix-code-review-plugin-semver-dispatch.md`.
3. Apply the one-line edit to `claude/.claude/skills/code-review/SKILL.md`.
4. Run `/skill-review` on the diff; address any findings.
5. Run `/code-review` (cumulative); address any findings.
6. Run `/ready-for-review` to commit, push, and open PR.

## Out of scope

- Updating the Ripple effect triage table (wrong surface for skill dispatches).
- Adding a test for the new dispatch line (there is no automated test harness
  for prose dispatch correctness; the smoke test in Verification is sufficient).
- Any other change to `code-review/SKILL.md` beyond this one sentence.
