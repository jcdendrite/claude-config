---
paths:
  - "**/settings.json"
  - "**/settings.local.json"
---

## Settings.json conventions

**Plugin config:** `enabledPlugins` only takes effect in
`settings.json`, not `settings.local.json`.

**Disabling a plugin: `false` vs. removing the entry.** Use
`enabledPlugins[name]: false` only for plugins with a genuine
re-enable case; remove entries with none, rather than leaving a
`false` placeholder that implies future re-enable.
