# Fix: model path-prefixes plugin-skill names in worktree sessions

## Context

In two recent sessions running from a worktree cwd (`.claude/worktrees/GH-341-handoff-prerequisite-section/`), the model invoked the `skill-review` plugin skill with a malformed, path-prefixed name and got `Unknown skill`, then self-corrected on retry:

- Session A (`09c0872…jsonl:101`): `Skill(".claude/worktrees/GH-341-handoff-prerequisite-section/claude:skill-management:skill-review")` → error → retry `skill-management:skill-review` ✓
- Session B (`62ec673…jsonl:189`): `Skill("claude:skill-management:skill-review")` → error → retry `skill-management:skill-review` ✓

It self-corrects, so it's not breaking work — but it wastes a tool call, pollutes the transcript with an error, and is a latent footgun for the other plugin skills. The intended outcome: eliminate the malformed first attempt so plugin skills are invoked correctly on the first call.

## Root cause

Two factors compound:

1. **Platform mechanism (no opt-out — verified against primary docs).** Claude Code discovers project skills from `.claude/skills/` in the starting dir, every parent up to the repo root, **and nested `.claude/skills/` dirs below cwd on demand** (skills.md:136, verified verbatim). A nested skill is rendered under a **directory-qualified name only "when the name clashes with another skill"** (skills.md:114/253). This repo's stow source at `claude/.claude/skills/**` holds the same skill names that stow also installed at personal scope (`~/.claude/skills/**`) — so every nested copy clashes and is rendered as `<subdir-path>:<skill-name>`, where the subdir path is cwd-relative. With the session at the main repo root, the prefix becomes the long worktree path, so the listing reads e.g. `.claude/worktrees/GH-341-…/claude:code-review` and tells the model "use this instead of the unscoped code-review skill." The transcript's governing `skill_listing` attachment (`62ec…:25`, `09c…:94`) shows all 23 user skills carrying this prefix. The clash is structurally unavoidable in a stow repo (the personal copy must exist), and there is no setting to exclude a directory from discovery (see rejected alternatives).

2. **Plugin skills break the pattern.** The three marketplace plugins (`skill-management`, `claude-hook-review`, `plugin-semver`) are *not* directory-scoped — plugin skills are always `plugin:skill` and never path-prefixed. So `skill-management:skill-review` appears in the listing **without** a prefix (and in the later prefixed listing, drops out of the salient set entirely). When the `code-review` dispatcher told the model to "invoke `skill-review`," there was no exact prefixed entry to copy, so the model **synthesized** the name by over-applying the dominant prefix template: `<worktree-path>/claude:` + `skill-management:skill-review`.

The `require-skill-review` hook is **not** the source — its deny text says "Run /skill-review" (bare slash form), no path. The proximate trigger is the `code-review/SKILL.md` dispatch instruction (`:165`, `:171`, `:173`, `:241`) naming plugin skills by bare name while the listing trains the model to prefix everything.

**Sibling audit:** `code-review/SKILL.md` dispatches three plugin skills by bare name — `skill-review`, `claude-hook-review` (`:171`, `:241`, `:358`), and `plugin-semver` (`:173`). All three are exposed to the identical synthesis error; a `skill-review`-only fix would be too narrow.

## Approach

**Add one repo-root `CLAUDE.md` rule (single source of truth), under "Working in this repo."** It is always loaded into context for every session in this repo, covers all three plugins at once, and explains the *why* so future readers don't reintroduce the confusion:

> **Plugin skills are invoked by `plugin:skill`, never path-prefixed.** Because this repo's stow source `claude/.claude/skills/**` is itself a nested `.claude/skills/` tree, Claude Code discovers those skills as *directory-scoped project skills* — so in a worktree session the available-skills listing renders user skills with a worktree-path prefix (`.claude/worktrees/<branch>/claude:<skill>`) and tells you to prefer that form. That scoping applies **only to project skills**. The three marketplace plugins — `skill-management`, `claude-hook-review`, `plugin-semver` — are not directory-scoped: invoke their skills by the fully-qualified `plugin:skill` name (`skill-management:skill-review`, `claude-hook-review:claude-hook-review`, `plugin-semver:plugin-semver`) with **no** directory or worktree path prepended.

Rationale: the malformed call is a name-synthesis error driven by an instruction-vs-listing mismatch. The only lever in our control is instruction text, and an always-loaded, plugin-agnostic rule is the lowest-churn fix that carves out the exact exception the in-listing prose omits. It conflicts with nothing (the listing's "use the prefixed form" guidance is about user skills only).

**Why not the alternatives (all rejected — verified against primary docs):**
- *Suppress the duplicate discovery with a directory exclusion.* Cleanest foundationally, but **no such mechanism exists.** The complete set of skill-related settings is `disableBundledSkills`, `disableSkillShellExecution`, `maxSkillDescriptionChars`, and `skillListingBudgetFraction` (settings.md, fetched in full) — none excludes a directory; there is no `.claudeignore` and no way to disable nested/directory-scoped discovery (skills.md, fetched in full). Not available.
- *Hide the duplicates via `skillOverrides`.* This is the one standard visibility lever (skills.md:561–585, `"on"`/`"name-only"`/`"off"`), but it doesn't apply cleanly: (a) skills.md:585 — "Plugin skills are not affected by `skillOverrides`"; (b) the duplicate's key is the directory-qualified form whose prefix is the worktree path, which varies per worktree — no stable key to target; (c) a bare `"off"` would also hide the legitimate same-named skill we rely on. Rejected.
- *Restructure the repo* so `claude/.claude/skills/` isn't discovered. Impossible — that path *is* the stow source, and the personal-scope copy it clashes with must also exist.
- *Fix only `code-review/SKILL.md`* (rewrite bare plugin-skill names to `plugin:skill` form). Helps but is narrower than the CLAUDE.md rule, doesn't cover the `require-skill-review` hook's "Run /skill-review" path, edits a brevity-sensitive SKILL.md, and trips the hook-enforced skill-review gate. Considered as an optional reinforcement (see Out of scope), not the core fix.

The misfire itself is the model constructing a *wrong invocation name*, which is governed by prompt/context, not settings — so the correct layer for the fix is an always-loaded instruction (CLAUDE.md), not a configuration knob. This is verified, not assumed: no configuration knob for it exists.

**Longer-term (out of our hands):** file Anthropic feedback (`/feedback`) requesting a skill-discovery ignore mechanism for stow-style repos that vendor a `.claude/skills/` tree.

## Critical files

- **Plan file placement:** this plan currently lives at `~/.claude/plans/` (harness path). On the implementation branch, move it to the repo at `.claude/plans/<topic-slug>.md` (per plan-it Step 1) so it ships in the same PR as the `CLAUDE.md` edit — that dir is not gitignored and is where this repo's plans live.
- `CLAUDE.md` (repo root) — add the rule above as a new bullet in the "Working in this repo" section (sits naturally alongside the existing "Worktree enforcement," "Footgun: never recommend `>>` writes," and "No shared partials" entries). Plugin identifiers must match `claude/.claude/skills/`-discovered names and `.claude/settings.json` `enabledPlugins`: `skill-management`, `claude-hook-review`, `plugin-semver`.

## Verification

- This is an instruction-text change; no automated test exercises model name-synthesis. Smoke test manually: from a worktree (`git worktree add .claude/worktrees/<slug> -b <slug>`), make a trivial SKILL.md edit and confirm the model invokes `skill-management:skill-review` directly (no `Unknown skill` first attempt). Repeat with a hook edit (`claude-hook-review:claude-hook-review`) and a plugin edit (`plugin-semver:plugin-semver`).
- Because the edited file is an AI-instruction file, run `/ai-instruction-and-memory-files` on the diff (per repo convention) before presenting; `/code-review` and `/plan-review` per the pipeline. No `SKILL.md`/agent files staged, so `/skill-review` and `/agent-review` are not triggered (assuming the optional `code-review/SKILL.md` reinforcement is *not* included).

## Out of scope (decided)

- **Not reinforcing** in `code-review/SKILL.md`. The fully-qualified-name rewrite at `:165`/`:171`/`:173`/`:241`/`:358` was considered and **deferred** — the always-loaded CLAUDE.md rule is sufficient, and the rewrite would add churn and trip the hook-enforced `/skill-review` gate.
- **Not drafting** Anthropic feedback. Noted as the real platform-level cause (no skill-discovery opt-out for stow repos that vendor a `.claude/skills/` tree); filing it is left to the owner.
