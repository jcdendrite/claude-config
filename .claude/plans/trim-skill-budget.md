# Trim skill-listing budget headroom

## Context

The skill-listing budget is at the ceiling — ~7,897 of 8,000 chars used, leaving
~103 free. The budget is a test (`TestTotalListingBudgetUnderSonnet`) that sums
`len(description) + len(when_to_use)` across every model-invokable skill and fails
if the total exceeds `int(200000 × 4 × 0.01) = 8000`. With only ~103 chars free,
any new skill or any added `TRIGGER`/`DO NOT TRIGGER` clause longer than ~103 chars
trips the test. **Goal:** recover meaningful headroom (~200–300 chars) by compressing
the phrasing of the top consumers, *without weakening routing fidelity*.

**Why this is delicate, not just "delete words":** these `description:` fields are
the model-invocation routing surface. The `TRIGGER when:` / `DO NOT TRIGGER when:`
conditions and the `(use sibling-skill)` redirects are exactly what the harness reads
to decide which skill to auto-select. Text that scans as prose verbosity is often the
sole routing discriminator. The trim test here is a **routing lens**: a phrase comes
out only if removing it changes no trigger condition and no sibling redirect. We
compress syntax (`(use X)` → `→X`), drop filler (`When to `, `doing`), and remove
**redundant restatements** (a definition repeated in both the positive and negative
clause) — never a discriminator.

## Approach

Targeted, routing-safe compression of the four largest consumers. No skill is moved to
`disable-model-invocation: true` (that lever was considered and declined — it would
remove a skill from model auto-selection, which most of these need).

`when_to_use:` is a real, documented frontmatter field (skills.md frontmatter reference:
"Appended to `description` in the skill listing and counts toward the 1,536-character
cap"), but no skill in this repo populates it — trigger logic is written inline inside
`description`. Migrating text into `when_to_use:` would not help: the test sums
`len(description) + len(when_to_use)`, so moving chars between the two is net-zero.

The live harness also exposes settings-based levers (skills.md: `skillOverrides:
"name-only"`, `skillListingBudgetFraction`, `SLASH_COMMAND_TOOL_CHAR_BUDGET`). **None
satisfy this repo's test** — `corpus_budget_violations` hardcodes the 8,000 bound and
reads frontmatter from files, ignoring settings. For a green test, text reduction (or
`disable-model-invocation`) is the only lever. Estimated total recovery **~190–250 chars**
(7,897 → ~7,650–7,700; ~300+ free). Exact figures are confirmed by re-running the
budget test after edits — the numbers below are estimates.

### Proposed edits

**1. code-review** (`claude/.claude/skills/code-review/SKILL.md`) — ~491 → ~428 (≈63 saved)

The DO-NOT-TRIGGER clause enumerates four sibling redirects with `(use X)` scaffolding.
Collapse to an arrow list; drop "tweaks". Every redirect target preserved.

> Principal-engineer review before presenting code. TRIGGER when: code is about to be presented, or the user asks for a code review. DO NOT TRIGGER when: cosmetic-only changes (typo, formatting, CSS with no behavioral delta); only one specialized file type staged (SKILL.md→skill-review, agent→agent-review, plan→plan-review, CLAUDE.md/AGENTS.md/memory→ai-instruction-and-memory-files); fresh review-markers/ entry covers the diff.

**2. plugin-semver** (`plugins/plugin-semver/skills/plugin-semver/SKILL.md`) — ~391 → ~315 (≈76 saved)

The positive clause defines a plugin directory as "tree contains
`.claude-plugin/plugin.json`"; the negative clause restates the inverse. Keep the
definition once; drop "a directory whose" and the redundant negative restatement.

> Semver and version-field discipline for Claude Code plugin changes. TRIGGER when: modifying any file inside a plugin directory (tree contains .claude-plugin/plugin.json), or editing a .claude-plugin/marketplace.json. DO NOT TRIGGER when: editing skills/agents outside any plugin — e.g. user-scope stowed skills.

**3. git-feature-branch-sync** (`claude/.claude/skills/git-feature-branch-sync/SKILL.md`) — ~389 → ~343 (≈46 saved)

Drop "When to "/"and how to"/"doing"; shorten the protected-branch parenthetical
(elaboration, not a trigger condition). The `git-state-safety` redirect stays.

> Rebase vs merge a feature branch; force-push safely. TRIGGER when: syncing a feature branch with the default branch or deciding whether and how to force-push. DO NOT TRIGGER when: routine work on a clean branch, operating on shared/default branches (never force-push them), or mid-merge/rebase/cherry-pick state (use git-state-safety instead).

**4. subagent-delegation** (`claude/.claude/skills/subagent-delegation/SKILL.md`) — ~439 → ~431 (≈8 saved, optional)

This one is near its floor — it's a dense list of load-bearing trigger conditions with
little safe fat. Only "When to " can come out cleanly. Included for completeness; skip
if the other three already give enough headroom.

> Dispatch to a subagent vs inline. TRIGGER when: full check suite or full-project verification; broad codebase search; first exploratory read (target unknown); 2nd/3rd Bash command toward same question; delegating implementation; reporting check-runner test counts. DO NOT TRIGGER when: single targeted lookup; comprehension read feeding your own writing/review/design; Edit/Write sequences; output requiring line-by-line reasoning.

## Critical files

- `claude/.claude/skills/code-review/SKILL.md` — frontmatter `description` only (line 3).
- `plugins/plugin-semver/skills/plugin-semver/SKILL.md` — `description` (lines 3–10). **See consequence below.**
- `claude/.claude/skills/git-feature-branch-sync/SKILL.md` — `description` (lines 3–9).
- `claude/.claude/skills/subagent-delegation/SKILL.md` — `description` (lines 3–11), optional.

Only the YAML `description:` value changes in each file. No skill body, no `name:`, no
other frontmatter key. Preserve each file's existing scalar style (code-review uses a
quoted single-line scalar; the other three use folded `>` blocks).

**Reuse / single source of truth:** the budget logic already lives in one place —
`plugins/skill-management/scripts/validate_skill_structure.py::corpus_budget_violations`
(imported by both the test and the `require-skill-review.sh` hook). No code change
there; we only feed it shorter inputs.

**Consequence — plugin-semver forces a plugin version bump.** Editing a file inside
`plugins/plugin-semver/` is a change inside a plugin directory, which the plugin-semver
discipline itself governs: bump the `version` in that plugin's `.claude-plugin/plugin.json`
in the same commit. If you'd rather avoid the version-bump ceremony for ~76 chars, drop
plugin-semver from scope — code-review + git-feature-branch-sync alone recover ~109 chars
with no plugin touched.

## Verification

1. **Routing-lens self-check** of each new description: read it as the harness would and
   confirm every original `TRIGGER`/`DO NOT TRIGGER` condition and every sibling redirect
   is still recoverable. This is the gate that matters most. Each trim **must** keep the
   literal `TRIGGER when:` and `DO NOT TRIGGER when:` strings — `test_skills.py`'s
   `TestModelInvokableSkillTriggerContracts` (lines 250–266) asserts both blocks exist in
   every model-invokable description. All four proposed texts above preserve them.
2. **Structural trigger tests** (same file): `test_trigger_covers_designated_surface`
   (lines 187–204) and `test_do_not_trigger_names_adjacent_skill` (lines 206–222) bind only
   a parametrized allowlist of skills — claude-hook-review, skill-review, agent-review,
   review-permissions, test-conventions, test-evaluation. **None of the four trim targets
   are in those lists**, so the surface-mention and adjacent-skill-naming assertions do not
   constrain these edits. (We still keep code-review's four sibling redirects and
   git-feature-branch-sync's git-state-safety redirect for routing fidelity, just not
   because a test demands it.) Stated here so the implementer doesn't over-fit the trims.
3. **Budget test** (from a linked worktree, venv is three levels up):
   `../../../.venv/bin/pytest claude/.claude/skills/tests/test_skills.py` — confirms the
   corpus total dropped below 8,000 and the per-skill 1,536 cap is untouched, and runs the
   structural tests above in the same suite. Capture the new total to report exact headroom
   recovered.
3. **Lint:** `../../../.venv/bin/ruff check claude/.claude/` (no code changed, but cheap).
4. **`/skill-review`** on the diff — hook-enforced (`require-skill-review.sh` blocks the
   commit until the behavioral-equivalence marker is written), and directly on point here:
   the skill will flag any trim that drops a routing discriminator.
5. **`/code-review`** before presenting.

## Out of scope

- Disabling model-invocation on any skill, or migrating trigger logic to a `when_to_use:`
  field — both considered and declined above.
- The aggressive ~10-skill sweep — the user chose the targeted approach.
- Any skill body, or any frontmatter key other than `description:`.
