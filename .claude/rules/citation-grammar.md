---
paths:
  - "claude-skills/skills/**/SKILL.md"
  - ".claude/skills/**/SKILL.md"
  - "plugins/**/skills/**/SKILL.md"
  - "docs/**/*.md"
  - "README.md"
  - "CONTRIBUTING.md"
  - "SECURITY.md"
  - "evals/README.md"
  - "CLAUDE.md"
  - "claude/.claude/CLAUDE.md"
  - ".claude/rules/*.md"
  - "claude/.claude/rules/*.md"
---

## Citing another skill or doc section

**`` `target` § "Heading" ``.** The backticked target file sits immediately before `§`, followed by the target's exact heading text in quotes — e.g. `` `subagent-delegation/SKILL.md` § "Heavy command output — run inline" ``.

Keep the whole citation on one line, past the wrap width if needed — the heading capture cannot span a newline, so a hard-wrap silently matches nothing at all, rather than resolving to a wrong target, and the miss has no reader-visible symptom.

`claude-skills/skills/tests/test_skills.py` mechanically enforces both halves of this grammar: that a citation resolves to a real heading, and that no citation — no-space or hard-wrapped — goes unextracted. See that file's own docstrings for which tests and which corpora.
