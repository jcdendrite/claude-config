# References — ai-instruction-and-memory-files

Primary sources that informed the rules in SKILL.md. Not loaded at skill runtime;
read manually when verifying a rule or adding new guidance. Tier matters — an
official Anthropic doc outweighs a practitioner blog; keep that distinction when
citing a claim.

**Official (Anthropic):**

- [Claude Code — How Claude remembers your project](https://code.claude.com/docs/en/memory)
  — the "under 200 lines per CLAUDE.md file" size threshold, CLAUDE.md-loaded-not-AGENTS.md,
  the `@AGENTS.md` import pattern, the auto-memory role split, the MEMORY.md 200-line/25KB load limit.

  > "Use CLAUDE.md files when you want to guide Claude's behavior.
  > Auto memory lets Claude learn from your corrections without manual
  > effort."

  > "The first 200 lines of `MEMORY.md`, or the first 25KB, whichever comes
  > first, are loaded at the start of every conversation... Topic files
  > like `debugging.md` or `patterns.md` are not loaded at startup. Claude
  > reads them on demand..."
- [Claude Code — Best Practices](https://code.claude.com/docs/en/best-practices)
  — CLAUDE.md is advisory while hooks are deterministic; the over-specified-CLAUDE.md failure mode.
- [claude-code CHANGELOG.md](https://github.com/anthropics/claude-code/blob/main/CHANGELOG.md)
  — zero AGENTS.md entries, confirming AGENTS.md support was never added.

**Practitioner / research (not Anthropic — weigh accordingly):**

- [agents.md standard](https://agents.md) — supporting-tools list; Claude Code is absent from it.
- [Context Rot — Chroma Research](https://www.trychroma.com/research/context-rot)
  — model performance degrades as input length grows. Uses no word-count compliance threshold.
- [Writing a good CLAUDE.md — HumanLayer](https://www.humanlayer.dev/blog/writing-a-good-claude-md)
  — practitioner "under 300 lines" consensus, explicitly described as not rigorously investigated.
