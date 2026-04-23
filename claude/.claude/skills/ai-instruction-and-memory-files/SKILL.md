---
name: ai-instruction-and-memory-files
description: >
  How Claude Code, Lovable, and other AI coding agents load instruction
  files (CLAUDE.md, AGENTS.md, Cursor rules, Copilot instructions, Lovable
  project/workspace knowledge) and Claude Code auto-memory (MEMORY.md
  index + topic files): precedence, duplication rules, length targets,
  the `@AGENTS.md` import pattern Anthropic officially endorses, and the
  split between user-written instructions and Claude-written memory.
  TRIGGER when: editing CLAUDE.md or AGENTS.md, editing `.lovable/*.md`,
  creating a new instruction file, auditing or pruning Claude Code
  auto-memory in `~/.claude/projects/*/memory/`, deciding whether a rule
  belongs in CLAUDE.md vs auto-memory, evaluating whether rules should be
  duplicated across files, or debating file length and context budget for
  AI coding agents.
  DO NOT TRIGGER when: editing README.md or other project docs that are
  not loaded by AI coding agents, editing `.claude/skills/*/SKILL.md`
  frontmatter (that's a skill-frontmatter concern — use the
  skill-frontmatter reference instead), or writing code.
user-invocable: false
---

# AI Instruction & Memory Files — Architecture

The facts below come from primary sources (Anthropic docs, Lovable docs,
agents.md standard). Treat them as durable context: when CLAUDE.md /
AGENTS.md questions come up, start here rather than re-researching.

## 1. Claude Code loads CLAUDE.md only — NOT AGENTS.md

Verbatim from [Claude Code — How Claude remembers your project](https://code.claude.com/docs/en/memory):

> "Claude Code reads CLAUDE.md, not AGENTS.md. If your repository already uses AGENTS.md for other coding agents, create a CLAUDE.md that imports it so both tools read the same instructions without duplicating them."

Confirming signals:
- Zero entries for "AGENTS.md" in the [claude-code changelog](https://github.com/anthropics/claude-code/blob/main/CHANGELOG.md) — the support was never added.
- Claude Code is explicitly absent from the [agents.md supporting-tools list](https://agents.md) (Codex, Cursor, Gemini CLI, Windsurf, Amp, Aider, etc. are listed).

**The Anthropic-documented single-source-of-truth pattern is:**

```
@AGENTS.md

# Claude-specific content below this line
```

Put `@AGENTS.md` as the first line of CLAUDE.md. Claude Code imports the
referenced file's content; maintenance is single-source, no duplication.

`@path` imports resolve relative to the file containing the import, not
the current working directory. A `@docs/x.md` in `.claude/CLAUDE.md`
looks for `.claude/docs/x.md`.

### Claude Code CLAUDE.md precedence (within the family)

Concatenated, not overridden:

1. Managed policy (enterprise)
2. Project `./CLAUDE.md` or `./.claude/CLAUDE.md`
3. User `~/.claude/CLAUDE.md` (global)
4. `CLAUDE.local.md`

Claude Code walks from the current working directory up to `/`,
concatenating every `CLAUDE.md` it finds along the way — ancestor
instructions are additive, not overridden. In monorepos this means
root-level CLAUDE.md, team-directory CLAUDE.md, and project-level
CLAUDE.md all load together.

## 2. Lovable loads all four sources, prioritizes project knowledge

From [Lovable Docs — Knowledge](https://docs.lovable.dev/features/knowledge):

> "When you send a message, Lovable reads your project knowledge, workspace knowledge, and project code... It also looks at instruction files in your project's GitHub repository such as AGENTS.md or CLAUDE.md."
> "Lovable is encouraged to prioritize the instructions defined in project knowledge, since they apply specifically to the current project."
> "Root-level AGENTS.md files are always read by the Lovable agent regardless of session length."

Effective order:

1. **project knowledge** (Lovable UI field — highest priority)
2. **workspace knowledge** (Lovable UI field)
3. **AGENTS.md / CLAUDE.md** (repo files — AGENTS.md has the explicit "always read regardless of session length" guarantee; CLAUDE.md priority vs. AGENTS.md is not documented by Lovable)
4. **project code**

All four are loaded every session. Lovable docs warn that in very long
conversations instructions can drift; the "always read" guarantee for
AGENTS.md is the defense-in-depth.

## 3. Length targets

Per [Claude Code Best Practices](https://code.claude.com/docs/en/best-practices),
[HumanLayer](https://www.humanlayer.dev/blog/writing-a-good-claude-md),
and [Chroma's Context Rot research](https://research.trychroma.com/context-rot):

- Target: **under 200 lines per file**.
- Diminishing returns past 300 lines. 1000+ words correlates negatively with compliance.
- Attention decay hits the **middle** of long files — "lost in the middle." Burying critical rules past line ~150 reduces their effective load.
- Compliance with prose rules tops out around **70%**. Structural tests and hooks hit 100%. When a rule can be encoded as either, prefer the mechanical enforcement.

### Don't embed PR or ticket refs in always-loaded files

Lines like `Precedent: PR #105` or `See TICKET-123 for context` belong
in commit messages, PR descriptions, or plan files — not in CLAUDE.md
or AGENTS.md. They rot the moment the next PR lands, and they cost
per-session context budget without giving future sessions actionable
signal. Future readers need the *rule* stated clearly.

## 4. When to duplicate vs. reference

**Reference via `@AGENTS.md` import (Anthropic pattern):** default for
Claude Code when both files exist. Zero maintenance, single source.

**Duplicate (defense-in-depth) when:**
- The content is genuinely critical and one delivery mechanism could fail silently.
- Different agents reach the file through different load paths and the rule needs to fire in all of them.
- Example: auth-removal prohibition appearing in workspace-knowledge AND AGENTS.md — workspace knowledge catches Lovable in long conversations where AGENTS.md drift is possible per Lovable's own docs.

**Do NOT duplicate when:**
- The import pattern covers both agents (Lovable reads AGENTS.md natively, Claude Code imports it via `@AGENTS.md`).
- The content is enforced structurally (a test, a hook) — prose duplication adds maintenance without raising compliance above the already-100% structural enforcement.
- The rule is process discipline enforced by `/pre-merge`, commit-review hooks, or other mechanical gates.

## 5. Quick decision flow when editing

| Question | Answer |
|---|---|
| Am I adding a new guardrail? | Put it in AGENTS.md (canonical). Claude Code gets it via `@AGENTS.md` import. Lovable reads it natively. |
| The repo has CLAUDE.md but no AGENTS.md — should I add AGENTS.md? | Only if a non-Claude agent (Lovable, Cursor, Codex) is also using the repo. Otherwise CLAUDE.md alone is fine. |
| CLAUDE.md is over 200 lines — what should I trim? | First: delete content that duplicates AGENTS.md (use `@AGENTS.md` import instead). Then: collapse narrative case studies into one-sentence principles. Leave only Claude-Code-specific project context. |
| Should I add Lovable-specific guidance to project-knowledge vs AGENTS.md? | project-knowledge for project-specific facts that only apply to Lovable's current project context; AGENTS.md for rules that apply across sessions and repos. |
| A rule appears in two files — is that OK? | Only if (a) it's critical AND (b) the two files reach different agents / different load paths AND (c) one could silently fail. Otherwise use the import pattern. |
| Where should this rule live — CLAUDE.md or auto-memory? | Team rule → CLAUDE.md (or AGENTS.md). Personal preference / calibration → memory. If CLAUDE.md, AGENTS.md, or a hook already covers it → **neither**; delete the memory. See §6. |

## 6. Claude Code auto-memory (Claude-written, per-user)

Auto-memory at `~/.claude/projects/<project>/memory/` is adjacent to
CLAUDE.md but serves a different role. It's machine-local and per
working tree — never a place for team rules. From
[Claude Code — memory](https://code.claude.com/docs/en/memory):

|                  | CLAUDE.md files                                   | Auto memory                                                      |
| :--------------- | :------------------------------------------------ | :--------------------------------------------------------------- |
| Who writes it    | You                                               | Claude                                                           |
| What it contains | Instructions and rules                            | Learnings and patterns                                           |
| Scope            | Project, user, or org                             | Per working tree (machine-local)                                 |
| Loaded into      | Every session                                     | Every session (first 200 lines or 25KB of `MEMORY.md`)           |
| Use for          | Coding standards, workflows, project architecture | Build commands, debugging insights, preferences Claude discovers |

> "Use CLAUDE.md files when you want to guide Claude's behavior.
> Auto memory lets Claude learn from your corrections without manual
> effort."

### `MEMORY.md` is an index, not a memory

> "The first 200 lines of `MEMORY.md`, or the first 25KB, whichever comes
> first, are loaded at the start of every conversation... Topic files
> like `debugging.md` or `patterns.md` are not loaded at startup. Claude
> reads them on demand..."

Index discipline:

- One line per entry, ≤150 characters: `- [title](file.md) — one-line hook`
- No frontmatter on `MEMORY.md` itself — it's an index, not a memory
- Substance lives in per-topic files; the index is pure routing
- Organize semantically, not chronologically
- Lines past 200 silently don't load — treat 200 as a hard ceiling

### Where does a given rule belong?

| Candidate content                                                | Goes in                                          |
| :--------------------------------------------------------------- | :----------------------------------------------- |
| Rule any contributor (or other agent) should follow              | CLAUDE.md, or AGENTS.md via `@AGENTS.md`         |
| Personal preference or workflow specific to this user            | Auto-memory                                      |
| Past incident "why" not captured in code, tests, or commit msgs  | Auto-memory (feedback or project type)           |
| Pointer to external systems (Linear, Grafana, etc.)              | Auto-memory (reference type)                     |
| Restatement of a rule already in CLAUDE.md / AGENTS.md           | **Nowhere — delete it** (§3 compliance asymmetry)|
| Rule already enforced by a hook or structural test               | **Nowhere — enforcement is 100%, prose is load** |

### Anti-duplication heuristic

If CLAUDE.md / AGENTS.md already covers a rule, the matching memory is
pure load: the rule fires every session through the instruction file,
the index line consumes one of the ~200 loaded lines, and any recall
reads a topic file that restates content already in context. **Delete
on contact.**

Memory earns its keep when it captures what the repo *doesn't*: who
the user is and how they prefer to collaborate, feedback calibration
(corrections **and** validated judgment calls) with the *why* story,
time-sensitive project context, and references to external systems.

## 7. Primary sources

- [Claude Code — How Claude remembers your project](https://code.claude.com/docs/en/memory)
- [Claude Code — Best Practices](https://code.claude.com/docs/en/best-practices)
- [claude-code CHANGELOG.md](https://github.com/anthropics/claude-code/blob/main/CHANGELOG.md)
- [Lovable Docs — Knowledge](https://docs.lovable.dev/features/knowledge)
- [agents.md standard](https://agents.md)
- [Context Rot — Chroma Research](https://research.trychroma.com/context-rot)
- [Writing a good CLAUDE.md — HumanLayer](https://www.humanlayer.dev/blog/writing-a-good-claude-md)
