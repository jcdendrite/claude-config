---
paths:
  - "claude/.claude/skills/**/SKILL.md"
  - ".claude/skills/**/SKILL.md"
  - "plugins/**/skills/**/SKILL.md"
  - "claude/.claude/agents/*.md"
  - "plugins/*/agents/*.md"
---

## When editing a skill or agent, run the skill on its own diff

Before committing a skill or agent change, run `/skill-review` (or
`/agent-review`) on the diff — an edit can violate the very rules the
file enforces.

Rewording a checklist item near a fixture-verified clause can silently
change what it matches — re-run a fresh allow/deny fixture pair (kept
in a scratchpad, never committed, since a must-flag fixture is exactly
the content the item exists to exclude) before staging.
