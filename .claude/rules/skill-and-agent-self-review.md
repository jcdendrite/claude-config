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
change what it matches, so re-run a fresh allow/deny fixture pair before
staging. Keep the fixtures in a scratchpad — a realistic must-flag
fixture is exactly the content the item exists to keep out of the repo.

## Skill and rule authoring conventions

**No shared partials across skills — but co-located auxiliary files are distinct.** `SKILL.md` has no `includes:`/`import:`/`extends:` field and the `@path` syntax is CLAUDE.md-only — duplicate shared rule text into each skill rather than extracting a `_shared/` file. Co-located auxiliary files are a separate, permitted pattern — `REFERENCES.md` (edit-time) and `plan-review/ROUTING.md` (runtime) are the two in use. Add one only for content that is genuinely load-bearing and can't be shortened, never as a way to route around a file's length cap. See `docs/skills.md`'s Skill architecture notes section for the full distinction.

**`REFERENCES.md` is the edit-time co-located reference for a skill.** It holds canonical URLs/quotes/framework notes for a skill, read manually at edit time — it is never loaded at runtime and must not be embedded in `SKILL.md`.

**Citing another skill or doc section: `` `target` § "Heading" ``.** The backticked target file sits immediately before `§`, followed by the target's exact heading text in quotes — e.g. `` `subagent-delegation/SKILL.md` § "Heavy command output — run inline" ``. `test_skill_citations_resolve_to_real_headings` (`claude/.claude/skills/tests/test_skills.py`) resolves every such citation in the skill tree against a real file and an exact heading, so a rename on either side fails CI instead of leaving a silent dead pointer.

**Global skill bodies stay platform-agnostic.** Skills under `claude/.claude/skills/` install to every stack — don't hardcode engine/platform tokens (`pg_cron`, `net.http_post`, vendor API names); put stack-specific checks in a project-layer skill (`<skill>-<project>/SKILL.md`) loaded via the base skill's project-layer glob. The project-layer glob is for *additive* refinements of a base flow that already runs a complete pass on its own. A flow with no standalone base to layer onto belongs in the consuming repo as its own skill instead.

### When a skill is surfaced by real-world work, abstract first

Keep the failure mode and the fix; drop the trigger's identity.

- ✅ "Surfaced during a production incident where a mid-merge index was
  silently corrupted by a diagnostic `git checkout`."
- ❌ "Surfaced during the ExampleCo PROJ-123 review, where the
  mid-merge index was silently corrupted..."
