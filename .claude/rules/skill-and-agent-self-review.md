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

## Skill and rule authoring conventions

**No shared partials across skills — but co-located auxiliary files are distinct.** `SKILL.md` has no `includes:`/`import:`/`extends:` field and the `@path` syntax is CLAUDE.md-only — duplicate shared rule text into each skill rather than extracting a `_shared/` file. Co-located auxiliary files (`REFERENCES.md`; a runtime auxiliary like `plan-review/ROUTING.md`) are a separate, permitted pattern — see `docs/skills.md`'s Skill architecture notes section.

**`REFERENCES.md` is the edit-time co-located reference for a skill.** It holds canonical URLs/quotes/framework notes for a skill, read manually at edit time — it is never loaded at runtime and must not be embedded in `SKILL.md`.

**Global skill bodies stay platform-agnostic.** Skills under `claude/.claude/skills/` install to every stack — don't hardcode engine/platform tokens (`pg_cron`, `net.http_post`, vendor API names); put stack-specific checks in a project-layer skill (`<skill>-<project>/SKILL.md`) loaded via the base skill's project-layer glob.

### When a skill is surfaced by real-world work, abstract first

Keep the failure mode and the fix; drop the trigger's identity.

- ✅ "Surfaced during a production incident where a mid-merge index was
  silently corrupted by a diagnostic `git checkout`."
- ❌ "Surfaced during the ExampleCo PROJ-123 review, where the
  mid-merge index was silently corrupted..."
