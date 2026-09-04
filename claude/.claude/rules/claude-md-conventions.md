---
paths:
  - "**/CLAUDE.md"
  - "CLAUDE.md"
  - "**/AGENTS.md"
  - "AGENTS.md"
  - "**/CLAUDE.local.md"
  - "CLAUDE.local.md"
  - "**/.claude/CLAUDE.md"
  - ".claude/CLAUDE.md"
  - "**/.claude/AGENTS.md"
  - ".claude/AGENTS.md"
---

## CLAUDE.md and AGENTS.md conventions

How Claude Code loads these files. Length targets, the per-line behavior
test, the compression-diff audit, and duplicate-vs-reference judgment live
in the `ai-instruction-and-memory-files` skill — this rule doesn't overlap
them. Full citations and verbatim quotes live in `docs/rules-references.md`
in the claude-config repo.

### Claude Code loads CLAUDE.md only — not AGENTS.md

Per Anthropic's Claude Code memory docs, Claude Code reads CLAUDE.md, not
AGENTS.md. When a repo already uses AGENTS.md for other coding agents,
create a CLAUDE.md that imports it so both tools read the same instructions
without duplicating them.

The Anthropic-documented single-source-of-truth pattern is:

```
@AGENTS.md

# Claude-specific content below this line
```

Put `@AGENTS.md` as the first line of CLAUDE.md. Claude Code imports the
referenced file's content; maintenance is single-source, no duplication.

`@path` imports resolve relative to the file containing the import, not the
current working directory. A `@docs/x.md` in `.claude/CLAUDE.md` looks for
`.claude/docs/x.md`.

### Precedence within the CLAUDE.md family

Concatenated, not overridden:

1. Managed policy (enterprise)
2. Project `./CLAUDE.md` or `./.claude/CLAUDE.md`
3. User `~/.claude/CLAUDE.md` (global)
4. `CLAUDE.local.md`

Claude Code walks from the current working directory up to `/`,
concatenating every `CLAUDE.md` it finds along the way — ancestor
instructions are additive, not overridden. In monorepos this means
root-level CLAUDE.md, team-directory CLAUDE.md, and project-level CLAUDE.md
all load together.

### Adding a guardrail, or adding AGENTS.md to a repo that has neither

- **A new cross-agent guardrail goes in AGENTS.md** (canonical). Claude Code
  gets it via the `@AGENTS.md` import; other AGENTS.md-aware agents (Codex,
  Cursor, Aider, Gemini CLI, Windsurf, Amp, Lovable) read it natively.
- **Add AGENTS.md to a CLAUDE.md-only repo only if a non-Claude
  AGENTS.md-aware agent also uses the repo.** Otherwise CLAUDE.md alone is
  fine.
