---
name: plugin-semver
description: >
  Semver and version-field discipline for Claude Code plugin changes.
  TRIGGER when: modifying any file inside a plugin directory (a directory
  whose tree contains .claude-plugin/plugin.json), or editing a
  .claude-plugin/marketplace.json.
  DO NOT TRIGGER when: editing skills or agents that are not part of any
  plugin (no .claude-plugin/plugin.json in their tree) — e.g. user-scope
  stowed skills.
user-invocable: false
---

# Plugin semver

Fires when modifying files inside a Claude Code plugin or a marketplace manifest.

## Version field: one location only

Every behavior-affecting change to a plugin requires bumping `version` in that
plugin's `.claude-plugin/plugin.json`. Never add a `version` field to that
plugin's entry in `.claude-plugin/marketplace.json`. Claude Code resolves
`plugin.json` first and silently masks any marketplace value, so two version
fields can only drift — the marketplace entry becomes dead weight.

The canonical version is in `plugin.json`. The marketplace entry carries no
`version` key.

## Bump magnitude

The plugin is the versioned unit. Its public API is the aggregate of every
skill and hook it ships, plus their documented triggers and behavior. A
backward-incompatible change to one skill is therefore a major change to the
whole plugin — regardless of diff size.

Determine the bump by backward compatibility, not by change size:

| Change to the plugin | Bump |
|---|---|
| Backward-compatible bug fix — typo, clarification, or correcting a skill to match its already-documented behavior | patch |
| Backward-compatible addition — a new skill or hook, a new capability in an existing skill, or a trigger broadened to fire in strictly more cases | minor |
| Backward-incompatible change — an existing skill's behavior or guidance changes the outcome for anyone who relied on the prior version; a trigger narrowed so it no longer fires where it used to; a skill or hook removed or renamed; the plugin renamed or split | major |

A large additive rewrite that still does everything the prior version did is
`minor`. A one-line guidance reversal that breaks prior reliance is `major`.

## Post-merge install command

After merge, consuming repos refresh with:

```
claude plugin install <name>@<marketplace> --scope project
```

Derive `<name>` from the plugin's directory name. Derive `<marketplace>` from
the `name` field in the root `.claude-plugin/marketplace.json` of this repo.
Surface the command verbatim in the PR description. Which repos consume the
plugin is the author's knowledge — this skill cannot discover external
consumers.

## Checklist

Emit this on trigger and verify each item before committing:

```
Plugin semver checklist:
- [ ] the plugin's .claude-plugin/plugin.json `version` bumped
- [ ] Bump magnitude matches the change (see table above)
- [ ] No `version` field added to the plugin's marketplace.json entry
- [ ] PR description includes the post-merge install command
```
