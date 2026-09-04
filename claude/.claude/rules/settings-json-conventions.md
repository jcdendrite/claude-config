---
paths:
  - "**/settings.json"
  - "**/settings.local.json"
---

## Settings.json conventions

- `.claude/settings.json` vs `.claude/settings.local.json` scoping: project-shared rules (permissions, hooks, skillOverrides that every engineer on the project needs) go in committed `.claude/settings.json`. Personal-machine-only rules (per-machine tooling, individual preferences) go in gitignored `.claude/settings.local.json`. Before adding a rule, ask: would another engineer on this project need this? If yes → `settings.json`. If no → `settings.local.json`.
- **Plugin config:** `enabledPlugins` only takes effect in
  `settings.json`, not `settings.local.json`.
- **Disabling a plugin: `false` vs. removing the entry.** Use
  `enabledPlugins[name]: false` only for plugins with a genuine
  re-enable case; remove entries with none, rather than leaving a
  `false` placeholder that implies future re-enable.
