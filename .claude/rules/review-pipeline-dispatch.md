---
paths:
  - "claude/.claude/skills/**/SKILL.md"
  - "claude/.claude/agents/*.md"
  - "plugins/**/*"
---

## Review pipeline: per-file-type dispatch

`/code-review` dispatches per file type when staged changes include skill,
agent, or plugin files:

- **SKILL.md** → `/skill-review` is also required and **hook-enforced**
  (`require-skill-review.sh` blocks `git commit` until the behavioral-equivalence
  marker is written).
- **agent file** (`claude/.claude/agents/*.md` or `plugins/*/agents/*.md`) →
  `/agent-review` is invoked by the dispatcher but **not hook-enforced**,
  since agent bodies are lazy-loaded and lower-blast-radius than skill
  descriptions.
- **any file under a plugin directory** (a tree containing
  `.claude-plugin/plugin.json`) → `plugin-semver` is also required and
  **hook-enforced** (`require-plugin-version-bump.sh` blocks `git commit`
  unless the plugin's `version` was strictly raised since the branch's
  merge-base with the default branch) — but this hook only activates once
  `plugin-semver` itself is installed/updated to a version carrying it, not
  on a bare `git pull`.

`/code-review` invokes whichever applies automatically.
