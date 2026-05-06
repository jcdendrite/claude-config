# References — lovable-knowledge

Reference material that informed this skill. Not loaded during skill execution — consult when editing the skill to verify a rule still holds or to add new guidance.

## Primary source

- [Lovable Docs — Knowledge](https://docs.lovable.dev/features/knowledge) — canonical reference for all Lovable knowledge-field behavior.

## Source quotes

### Four-source priority (informs SKILL §1)

> "When you send a message, Lovable reads your project knowledge, workspace knowledge, and project code... It also looks at instruction files in your project's GitHub repository such as AGENTS.md or CLAUDE.md."

> "Lovable is encouraged to prioritize the instructions defined in project knowledge, since they apply specifically to the current project."

> "Root-level AGENTS.md files are always read by the Lovable agent regardless of session length."

### Project vs Workspace tiebreaker (informs SKILL §2)

> "Keep shared rules in workspace knowledge and project-specific details in project knowledge to avoid confusion and maximize clarity."

## Cross-agent context

- [agents.md standard](https://agents.md) — the cross-agent AGENTS.md spec referenced in SKILL §1's priority list. Lovable, Codex, Cursor, Aider, etc. all read AGENTS.md per this standard.
