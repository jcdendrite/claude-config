---
paths:
  - "claude/.claude/skills/**/SKILL.md"
  - ".claude/skills/**/SKILL.md"
  - "plugins/**/skills/**/SKILL.md"
  - "claude/.claude/agents/*.md"
  - "plugins/*/agents/*.md"
---

## When editing a skill or agent, run the skill on its own diff

A skill's body states the rules it enforces; an edit can violate
those rules unless you re-read the body with the diff in mind. Before
committing a skill change, invoke `/skill-review` via the `Skill`
tool and check the diff against its output — e.g. an edit adding
prose to a skill that argues for brevity is the kind of thing the
skill would flag against itself. The same rule applies to agent
files (`claude/.claude/agents/*.md`, `plugins/*/agents/*.md`):
invoke `/agent-review` and check the diff against its output before
staging.

Rewording a checklist item near a fixture-verified clause can silently
change what it matches, so re-run a fresh allow/deny fixture pair before
staging. Keep the fixtures in a scratchpad — a realistic must-flag
fixture is exactly the content the item exists to keep out of the repo.
