# References — lovable-cloud-knowledge

Reference material that informed this skill. Not loaded during skill execution — consult when editing the skill to verify a rule still holds or to add new guidance.

## Four load sources and priority order

Effective priority (highest to lowest):

1. **Project Knowledge** (Lovable UI field — highest priority)
2. **Workspace Knowledge** (Lovable UI field)
3. **AGENTS.md / CLAUDE.md** (repo files — AGENTS.md has the explicit "always read regardless of session length" guarantee)
4. **Project code**

All four are loaded every session. Lovable docs warn that in very long conversations instructions can drift; the "always read" guarantee for AGENTS.md is the defense-in-depth.

## Primary source

- [Lovable Docs — Knowledge](https://docs.lovable.dev/features/knowledge) — canonical reference for all Lovable knowledge-field behavior.

## Source quotes

### Four-source priority

> "When you send a message, Lovable reads your project knowledge, workspace knowledge, and project code... It also looks at instruction files in your project's GitHub repository such as AGENTS.md or CLAUDE.md."

> "Lovable is encouraged to prioritize the instructions defined in project knowledge, since they apply specifically to the current project."

> "Root-level AGENTS.md files are always read by the Lovable agent regardless of session length."

### Project vs Workspace tiebreaker (informs SKILL §1)

> "Keep shared rules in workspace knowledge and project-specific details in project knowledge to avoid confusion and maximize clarity."

## Cross-agent context

- [agents.md standard](https://agents.md) — the cross-agent AGENTS.md spec referenced in the four-source priority list above. Lovable, Codex, Cursor, Aider, etc. all read AGENTS.md per this standard.
