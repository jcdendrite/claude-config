# Skill cleanup — Part A (claude-config, PR 1)

PR 1 of 2. The downstream project's PR 2 must land same-day (mid-window risk noted below).

## What and why

Three changes to prepare for the downstream project's PR 2:

1. **A1** — ready-for-review absorbs PR-creation. Currently it defers
   PR-creation to a project-specific pre-merge skill. The downstream
   pre-merge is being deleted (PR 2); the global skill should be capable of opening
   a PR if one doesn't exist, generalizing the tracker-prefix convention
   using the branch-creation naming rule (<TICKET-ID>/<topic-slug>).

2. **A2** — test-conventions adds parent-discovery. Currently uses
   trigger-text suppression ("DO NOT TRIGGER when project-level test skill
   already loaded"). Migrating to the Glob+load pattern that code-review
   and plan-review already use — more reliable, frees the project extension
   from needing to suppress the parent.

3. **A3** — lovable-knowledge plugin → lovable-cloud plugin. lovable-knowledge
   is being joined by two more skills (edge-functions, migration-sync) that
   are Lovable Cloud platform guidance. Consolidating into a single plugin
   with a coherent name.

## Mid-window risk

Between PR 1 landing and PR 2 landing, the downstream project's pre-merge
skill breaks: it invokes ready-for-review before committing (the "carve-out"),
but PR 1 tightens the clean-tree precondition. Mitigate by landing PR 2
same-day.

## Files changed

- claude/.claude/skills/ready-for-review/SKILL.md (A1)
- claude/.claude/skills/test-conventions/SKILL.md (A2)
- claude/.claude/skills/code-review/SKILL.md (A3 — update skill reference)
- claude/.claude/skills/ai-instruction-and-memory-files/SKILL.md (A3 — update skill reference)
- plugins/lovable-knowledge/ → plugins/lovable-cloud/ (A3 — directory rename)
- plugins/lovable-cloud/.claude-plugin/plugin.json (A3)
- plugins/lovable-cloud/skills/lovable-cloud-knowledge/SKILL.md (A3)
- plugins/lovable-cloud/skills/lovable-cloud-edge-functions/SKILL.md (A3 — new)
- plugins/lovable-cloud/skills/lovable-cloud-migration-sync/SKILL.md (A3 — new)
- .claude-plugin/marketplace.json (A3)
- README.md (A3 — docs)
