# Disable bundled skills to clear `/doctor` listing deficit

## Context

After PR #203 trimmed claude-config skill descriptions, `/doctor` still
reports 4 dropped descriptions at 1.2%/1% of context. The 4 dropped
skills (`test-conventions`, `git-state-safety`, `test-evaluation`,
`sql-query-conventions`) are all `user-invocable: false`, so they fire
**only** by auto-trigger. A dropped description effectively disables
them. Further trimming hit diminishing returns: every remaining
character in those descriptions is dense TRIGGER / DO NOT TRIGGER
routing signal.

This plan reclaims budget from the **other** direction — disabling 7
Claude Code bundled skills that overlap with the repo's custom
pipeline or are one-time setup utilities. The mechanism is
`skillOverrides` in `settings.json`, the documented field for
overriding skill visibility (https://code.claude.com/docs/en/skills.md
— "Override skill visibility from settings"). Setting a skill to
`"off"` removes both its description and `/`-menu entry,
freeing description budget.

## Approach

Add a top-level `skillOverrides` block to the stow-distributed
`claude/.claude/settings.json` setting all 7 candidates to `"off"`,
and document the why and the re-enable mechanism in `docs/skills.md`.

The disabled candidates (all bundled Claude Code skills, not plugins):

| Skill | Why disable in this repo |
|---|---|
| `claude-api` | Long description; only relevant for Claude API/SDK work. Out of scope for claude-config itself. |
| `simplify` | Overlaps with `/code-review`, which spins up domain specialists. |
| `review` | "Review a PR" — superseded by `/code-review` + `/ultrareview`. |
| `security-review` | Superseded by `/code-review` specialist routing (ciso-reviewer agent). |
| `init` | One-time setup; CLAUDE.md is established here, and `/init` advice may conflict with repo conventions. |
| `keybindings-help` | One-time setup utility; rarely fires in established sessions. |
| `fewer-permission-prompts` | One-time setup utility. |

### Precedent

The repo already disables plugin skills the same way:
- `claude-md-management@claude-plugins-official: false` and
  `claude-code-setup@claude-plugins-official: false` in
  `enabledPlugins` (settings.json:199–202).
- `skill-creator@claude-plugins-official` removed entirely, with
  rationale in `claude/.claude/skills/skill-review/REFERENCES.md`.
- The README's purpose section already says: "ships official plugins
  disabled by default; stow users can enable any of them via
  `enabledPlugins` in their settings" (README.md:51).

This plan extends that "disabled by default, opt-in per stow user"
pattern to bundled skills via `skillOverrides`.

### Why `"off"` (not `"name-only"` or `"user-invocable-only"`)

The `skillOverrides` field accepts four values per the primary source:

| Value | Listed to Claude | In `/` menu |
|---|---|---|
| `"on"` | Name + description | Yes |
| `"name-only"` | Name only | Yes |
| `"user-invocable-only"` | Hidden | Yes |
| `"off"` | Hidden | Hidden |

Choosing `"off"` because (a) the goal is full budget reclaim, and
`"name-only"` still consumes name tokens in the listing; (b) the goal
is to remove these skills from the workflow as redundant or
out-of-scope for the repo's purpose, not merely hide their auto-
trigger; (c) `"off"` is the symmetric inverse of the existing
plugin-level `enabledPlugins: false` precedent — same intent, same
strength. A stow user who wants any of them back gets the full
restoration via the documented mechanism.

### Stow-distribution consideration

`claude/.claude/settings.json` is stow-symlinked into `~/.claude/` for
every user who pulls this repo, so this change disables 7 skills for
all of them, not just the repo owner. Re-enable for an individual
stow user:

- Per-session: `/skills` → highlight skill → `Space` to cycle to
  `"on"` → `Enter` (writes to `settings.local.json`).
- Persistent: add to `~/.claude/settings.local.json` (gitignored):

  ```json
  { "skillOverrides": { "claude-api": "on" } }
  ```

`settings.local.json` overrides `settings.json` at the same scope.

## Files to modify

### `claude/.claude/settings.json` (stow target: `~/.claude/settings.json`)

Add a `skillOverrides` block near `enabledPlugins` (alphabetical-ish
placement matches the existing structure):

```json
"skillOverrides": {
  "claude-api": "off",
  "fewer-permission-prompts": "off",
  "init": "off",
  "keybindings-help": "off",
  "review": "off",
  "security-review": "off",
  "simplify": "off"
},
```

### `docs/skills.md`

Add a new section after "## Skills (slash commands)" titled
**"## Bundled skills disabled by default"**. Pattern matches the
README's existing "plugins disabled by default" disclosure and the
`skill-review/REFERENCES.md` rationale doc.

Content:
- One short paragraph: why disabled (budget reclaim + redundancy with
  repo's review pipeline), pointer to `/doctor` warning history.
- A table identical to the "Why disable" table above (skill name +
  rationale).
- "## Re-enable for your session" subsection with the
  `~/.claude/settings.local.json` snippet and the `/skills` UI
  instruction.
- Link to the Claude Code primary doc for `skillOverrides`.

### `README.md`

Extend the existing plugin-disablement disclosure at README.md:51.
Current text: "ships official plugins disabled by default; stow users
can enable any of them via `enabledPlugins` in their settings."

Add a parallel sentence in the same paragraph for bundled skills, e.g.:
"It also disables seven bundled Claude Code skills that overlap with
its review pipeline or are one-time setup utilities (see [docs/skills.md
— Bundled skills disabled by default](docs/skills.md)); stow users can
re-enable any of them via `skillOverrides` in `settings.local.json`."

Without this disclosure, a stow user reading only the README would not
know bundled skills are disabled and would attribute the missing
auto-trigger to a bug, not policy.

## Implementation steps

1. Create a feature branch via the `branch-creation` skill (suggested
   slug: `disable-bundled-skills-budget`). Start from a clean
   `main` tip; use a worktree per the repo's worktree-enforcement gate.
2. **Move this plan file from `~/.claude/plans/i-worked-with-a-humble-ember.md`
   to `.claude/plans/disable-bundled-skills-budget.md` inside the worktree**
   (rename slug to match the branch). This plan was authored at user-scope
   plans/; the PR must ship the plan alongside the implementation per B17.
3. Edit `claude/.claude/settings.json` — add `skillOverrides` block.
4. Edit `docs/skills.md` — add the new section.
5. Edit `README.md` — add the bundled-skill disclosure sentence at line 51.
6. Run `/code-review` (no SKILL.md changes, so `/skill-review` won't
   apply).
7. Run `/ready-for-review` before pushing.
8. Open PR; do **not** merge (the repo owner lands their own PRs).

## Verification

1. After merge and `git pull`, start a **fresh** Claude Code session
   (the skill listing is computed once per session).
2. Run `/doctor`. Expected: no "descriptions dropped" warning. The 4
   previously-dropped claude-config skills (`test-conventions`,
   `git-state-safety`, `test-evaluation`, `sql-query-conventions`)
   should now appear with full descriptions in `/skills`.
3. Confirm disabled skills behave correctly: `/claude-api`,
   `/simplify`, `/review`, `/security-review`, `/init`,
   `/keybindings-help`, `/fewer-permission-prompts` should not auto-
   trigger and should not appear in the `/` menu.
4. Sanity-check re-enable: add `{"skillOverrides": {"claude-api":
   "on"}}` to `~/.claude/settings.local.json`, start a new session,
   confirm `claude-api` reappears.

## Out of scope

- Raising `skillListingBudgetFraction` — rejected in favor of the
  disable approach so stow users pay no extra token cost.
- Disabling further built-in or plugin skills beyond the 7 chosen.
- Trimming any remaining claude-config skill descriptions — PR 203
  already pushed those to the floor.
- Adding tests for `skillOverrides` (it's a Claude Code-validated
  setting; no skill body or hook logic changes here).

## Primary source

https://code.claude.com/docs/en/skills.md — sections "Bundled skills"
and "Override skill visibility from settings". Verified that
`skillOverrides` accepts `"on"` / `"name-only"` /
`"user-invocable-only"` / `"off"` and that it works in committed
`settings.json` (not just `settings.local.json`).
