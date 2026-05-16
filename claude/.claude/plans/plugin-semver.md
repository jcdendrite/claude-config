# `plugin-semver` plugin + version-field cleanup for claude-config

## Context

A prior session shipped a `SKILL.md` edit to a claude-config plugin without
updating its version, and noticed the post-merge install command keeps being
re-derived. The original draft plan (`plugin-semver-management-skill.md`)
proposed a guidance skill plus a hook that keeps the `version` field in
`plugin.json` **and** the matching `marketplace.json` entry in sync.

Research into the official Claude Code docs
(`code.claude.com/docs/en/plugins-reference`, `/plugin-marketplaces`)
overturned that premise:

- Version resolution is a **fallback chain**, not a sync requirement:
  `plugin.json` → marketplace entry → git commit SHA → `unknown`.
- The docs say verbatim: *"Avoid setting `version` in both `plugin.json` and
  the marketplace entry. The `plugin.json` value always wins silently, so a
  stale manifest version can mask a version you set in `marketplace.json`."*
- The repo already exhibited this anti-pattern: `lovable-cloud` is `2.1.0`
  (marketplace) vs `2.1.1` (plugin.json); `skill-review` is `1.0.0` vs `1.0.1`.
  The drift is functionally harmless (`plugin.json` wins) but the
  `marketplace.json` numbers are dead, masked weight.

So the foundational fix is not tooling to sync two fields — it is **removing
the second field**. The plugins are client-facing (installed into client
project repos), so explicit semver is correct (docs: "published plugins with
stable release cycles"); commit-SHA versioning is not appropriate. The single
source of truth is `plugin.json` — resolution position #1, authoritative,
travels in the plugin cache.

This plan does the cleanup, then adds a guidance skill so the convention
holds going forward. No hook: bump *magnitude* is a judgment call a hook
cannot make, and once there is one version field there is nothing to "sync".

## Decisions locked (with the user)

- Explicit semver, **single source of truth = `plugin.json`**.
- Remove `version` from every `marketplace.json` plugin entry.
- Reconcile the existing version drift **and** description drift in this PR.
- Scope: cleanup + a guidance skill (new plugin). No enforcement hook.
- The skill is a **project plugin** (opt-in install, no global skill-listing
  budget cost) but its `SKILL.md` body is written in **universal Claude Code
  terms** — it never hardcodes `claude-config` or a marketplace name, so any
  repo that authors plugins can install and use it.

## Where to run

Fresh session in `~/MyCode/claude-config`. Worktree enforcement is active —
use a linked worktree (`git worktree add .claude/worktrees/<branch> -b <branch>`)
or an agent with `isolation: worktree`. This is a `claude-config` change: it
ships to every stow user on `git pull`, so weight findings accordingly.

---

## Part A — De-duplicate the version field (cleanup)

Edit `/.claude-plugin/marketplace.json`: delete the `"version"` key from all
three plugin entries (`lovable-cloud`, `skill-review`, `claude-hook-review`).
Leave `plugin.json` versions untouched — `2.1.1 / 1.0.1 / 1.0.0` become the
canonical versions with **no renumbering** (they already are what Claude Code
resolves).

Reconcile description drift: for `skill-review` and `claude-hook-review`, the
`marketplace.json` and `plugin.json` `description` strings differ. Set them
identical in both files per plugin — adopt the marketplace-entry wording
(discovery-facing, states when to install) into `plugin.json`.
`lovable-cloud` descriptions already match; leave them.

Files: `.claude-plugin/marketplace.json`,
`plugins/skill-review/.claude-plugin/plugin.json`,
`plugins/claude-hook-review/.claude-plugin/plugin.json`.

## Part B — New `plugin-semver` plugin

Layout (mirrors `skill-review` / `claude-hook-review`):

```
plugins/plugin-semver/
  .claude-plugin/plugin.json
  skills/plugin-semver/SKILL.md
```

`plugins/plugin-semver/.claude-plugin/plugin.json`:

```json
{
  "name": "plugin-semver",
  "description": "Semver and version-field discipline for Claude Code plugin changes. Install at project scope in any repo that authors plugins for a Claude Code marketplace.",
  "version": "1.0.0",
  "author": { "name": "Cordova Strategy" },
  "skills": "./skills/"
}
```

New entry in `.claude-plugin/marketplace.json` (after `claude-hook-review`) —
**no `version` field**, consistent with the Part A cleanup:

```json
{
  "name": "plugin-semver",
  "description": "Semver and version-field discipline for Claude Code plugin changes. Install at project scope in any repo that authors plugins for a Claude Code marketplace.",
  "author": { "name": "Cordova Strategy" },
  "source": "./plugins/plugin-semver",
  "category": "productivity"
}
```

### SKILL.md (`plugins/plugin-semver/skills/plugin-semver/SKILL.md`)

Frontmatter mirrors existing plugin skills — `name: plugin-semver`,
folded `description:` with embedded triggers, `user-invocable: false`:

```
TRIGGER when: modifying any file inside a plugin directory (a directory
  whose tree contains .claude-plugin/plugin.json), or editing a
  .claude-plugin/marketplace.json.
DO NOT TRIGGER when: editing skills or agents that are not part of any
  plugin (no .claude-plugin/plugin.json in their tree) — e.g. user-scope
  stowed skills.
```

The `.claude-plugin/` path segment is the reliable diff anchor — both
`plugin.json` and `marketplace.json` live under it, and it is not tied to
any particular plugin-root directory name.

Body must cover (keep under the 200-line cap — `check-skill-length.sh`):

1. **One version field, in `plugin.json`.** Every behavior-affecting change to
   a plugin requires bumping `version` in that plugin's
   `.claude-plugin/plugin.json` (plugins commonly live under `plugins/<name>/`,
   but the rule is keyed to the manifest, not the directory name). Never add a
   `version` field to that plugin's entry in `.claude-plugin/marketplace.json`
   — Claude Code resolves `plugin.json` first and silently masks any
   marketplace value, so two fields can only drift. State the rule plainly; do
   not narrate the prior repo state (per CLAUDE.md durable-doc rules).

2. **Bump magnitude — judged by backward compatibility, not change size**
   (semver.org spec items 6–8). The plugin is the versioned unit; its public
   API is the aggregate of every skill and hook it ships plus their documented
   triggers and behavior (semver.org item 1: a public API "could be declared
   in the code itself or exist strictly in documentation"). A
   backward-incompatible change to *one* skill is therefore a major change to
   the *whole plugin* — and per the semver.org FAQ, even the tiniest
   incompatible change requires a major bump.

   | Change to the plugin | Bump |
   |---|---|
   | Backward-compatible bug fix — typo, clarification, or correcting a skill to match its already-documented behavior | patch |
   | Backward-compatible addition — a new skill or hook, a new capability in an existing skill, or a trigger broadened to fire in strictly more cases | minor |
   | Backward-incompatible change — an existing skill's behavior or guidance changes the outcome for anyone who relied on the prior version; a trigger narrowed so it no longer fires where it used to; a skill or hook removed or renamed; the plugin renamed or split | major |

   Change size is not the axis. A large additive rewrite that still does
   everything the prior version did is `minor`; a one-line guidance reversal
   that breaks prior reliance is `major`.

3. **Post-merge install command.** After merge, consuming repos refresh with
   `claude plugin install <name>@<marketplace> --scope project`. Derive
   `<name>` from the plugin's directory and `<marketplace>` from the `name`
   field of the repo's `.claude-plugin/marketplace.json` — never hardcode a
   marketplace name. Surface the command verbatim for the PR description; the
   author owns knowing which repos consume the plugin.

4. **Checklist emitted on trigger:**

   ```
   Plugin semver checklist:
   - [ ] the plugin's .claude-plugin/plugin.json `version` bumped
   - [ ] Bump magnitude matches the change (see table)
   - [ ] No `version` field added to the plugin's marketplace.json entry
   - [ ] PR description includes the post-merge install command
   ```

The skill is itself a plugin and subject to its own rules.

## Part C — Enable the plugin and update docs

**Enable it.** Add to `enabledPlugins` in the repo-root `.claude/settings.json`
(project scope — the file already enables `skill-review@claude-config` and
`claude-hook-review@claude-config`; this is how repo-own plugins load in
claude-config sessions):

```json
"plugin-semver@claude-config": true
```

**Document it.** In `docs/skills.md` "Project-scoped plugins" section:
- add a table row — `plugin-semver@claude-config` | "Semver and version-field
  discipline for plugin manifests" | "Repos that author Claude Code plugins";
- update the "Two skills" / "Both plugins" wording to cover three;
- add the third install command to the fenced block;
- add one sentence stating the convention — a plugin's `version` is declared
  in its `plugin.json` only, never in a `marketplace.json` entry.

## Part D — Enforcement test for the new convention

Per the repo norm of landing a convention's test in the same PR: add
`claude/.claude/skills/tests/test_plugin_manifests.py` (collected by
`pytest claude/.claude/`, which discovers `test_*.py` anywhere under that
tree). Resolves repo root via path arithmetic from `__file__` and asserts:

- every entry in `.claude-plugin/marketplace.json` `plugins[]` has **no**
  `version` key;
- every `plugins/*/.claude-plugin/plugin.json` **has** a `version`.

No existing test asserts on these manifests, so there is no conflicting
assertion to update.

## Files touched

- `.claude-plugin/marketplace.json` — drop 3 `version` keys; add `plugin-semver` entry
- `plugins/skill-review/.claude-plugin/plugin.json` — align description
- `plugins/claude-hook-review/.claude-plugin/plugin.json` — align description
- `plugins/plugin-semver/.claude-plugin/plugin.json` — new
- `plugins/plugin-semver/skills/plugin-semver/SKILL.md` — new
- `.claude/settings.json` (repo root) — add to `enabledPlugins`
- `docs/skills.md` — table row, wording, convention sentence
- `claude/.claude/skills/tests/test_plugin_manifests.py` — new
- `claude/.claude/plans/plugin-semver.md` — this plan file

## Verification

1. `jq '.plugins[] | select(.version != null) | .name' .claude-plugin/marketplace.json`
   → empty (no entry carries `version`).
2. `jq -r '.version' plugins/*/.claude-plugin/plugin.json` → a version for each.
3. `pytest claude/.claude/` and `ruff check claude/.claude/` → green.
4. Open a PR; do not self-merge.

## Out of scope

- No enforcement hook. Bump *magnitude* is judgment a hook cannot evaluate;
  once `version` lives in one file there is nothing to "sync".
- No change to the commit-SHA-vs-explicit-version model — explicit semver
  stays (correct for client-facing plugins).
- No edit to root `CLAUDE.md`: the skill enforces the rule for agents and
  `docs/skills.md` documents it for humans; a third copy would only drift.
