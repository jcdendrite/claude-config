---
name: ai-instruction-and-memory-files
description: >
  Editing rules for CLAUDE.md, AGENTS.md, and Claude Code auto-memory files.
  TRIGGER when: editing or auditing CLAUDE.md, AGENTS.md, or
  ~/.claude/projects/*/memory/, or deciding which surface a rule
  belongs in. DO NOT TRIGGER when: editing .lovable/*.md, .cursorrules,
  .github/copilot-instructions.md, README.md, or writing code.
user-invocable: false
---

## Step 0 — Activate gate session

<!-- HOOK_TEST_FIXTURE: activate-gate — the hook-alignment test suite reads this block from claude/.claude/skills/ai-instruction-and-memory-files/SKILL.md to verify it matches require-memory-skill.sh's active-marker layout. Do not duplicate elsewhere; the test re-reads it from here. -->
```
~/.claude/scripts/marker.sh activate memory-skill
```

While active, `require-memory-skill.sh` bypasses for this session (<60 min freshness; mtime refreshed on each pass). If the command fails (empty `SESSION_ID`), abort — `capture-session-id.sh` SessionStart hook did not run, and every gated memory write below will be blocked.

## Step 1 — Decide where this rule belongs before writing

Before writing any file under `memory/`, check in order:

1. **Grep CLAUDE.md and AGENTS.md** (project tree + `~/.claude/`) for keywords from the rule. If covered → edit the source, don't write to memory. Restating a covered rule is pure load. (See §3 compliance asymmetry, §5 anti-duplication heuristic.)
2. **Does this rule fire only inside a specific named skill's workflow?** If yes → that skill's `SKILL.md`, not memory. Skill bodies load at the moment the rule applies; memory loads every session whether the session uses that workflow or not.
3. **Confirm the content fits a genuine memory use:** personal preference (user type), feedback calibration with the *why* — corrections *and* validated judgment calls (feedback type), past-incident context not captured in code or commit messages (project type), or pointer to an external system (reference type).

If step 1 or 2 produces a destination, write there and stop. This pre-write check is placed here — before the architecture overview — because the Step 0 procedural form is what runs reliably before the writer decides what to do. See §4 and §5 below for the full routing tables.

# AI Instruction & Memory Files — Architecture

The facts below come from primary sources (URLs in co-located REFERENCES.md).
When CLAUDE.md / AGENTS.md questions arise, start here; open REFERENCES.md
only to verify a specific URL or quote.

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

For Lovable's UI knowledge fields (Project Knowledge, Workspace
Knowledge), the `.lovable/*.md` repo-mirror workflow, and review
criteria for `.lovable/**` changes, see the `lovable-cloud-knowledge` skill.

## 2. Length targets

Per [Claude Code Best Practices](https://code.claude.com/docs/en/best-practices),
[HumanLayer](https://www.humanlayer.dev/blog/writing-a-good-claude-md),
and [Chroma's Context Rot research](https://research.trychroma.com/context-rot):

- Target: **under 200 lines per file**.
- Diminishing returns past 300 lines. 1000+ words correlates negatively with compliance.
- Attention decay hits the **middle** of long files — "lost in the middle." Burying critical rules past line ~150 reduces their effective load.
- Compliance with prose rules tops out around **70%**. Structural tests and hooks hit 100%. When a rule can be encoded as either, prefer the mechanical enforcement.

### The behavior test

Every line should change Claude's behavior on at least one realistic
input. If removing the line wouldn't change any behavior, cut it.
Specific incident references, editorial meta-commentary, and narrative
case studies almost always fail this test; rationale that arms Claude
for an unenumerated edge case almost always passes.

### Don't embed PR or ticket refs in always-loaded files

`Precedent: PR #105` and `See TICKET-123` rot the moment the next PR
lands and cost per-session context budget. Put them in commit messages,
PR descriptions, or plan files — state the rule, not the precedent.

## 3. When to duplicate vs. reference

**Reference via `@AGENTS.md` import (Anthropic pattern):** default for
Claude Code when both files exist. Zero maintenance, single source.

**Duplicate (defense-in-depth) when:**
- The content is genuinely critical and one delivery mechanism could fail silently.
- Different agents reach the file through different load paths and the rule needs to fire in all of them.
- Example: a security-critical rule appearing in AGENTS.md (prose, ~70% compliance) AND enforced by a pre-commit hook (mechanical, 100% compliance) — the hook catches what prose drift misses.

**Do NOT duplicate when:**
- An AGENTS.md-aware agent already reads it natively and Claude Code can import it via `@AGENTS.md` — one canonical source covers both.
- The content is enforced structurally (a test, a hook) — prose duplication adds maintenance without raising compliance above the already-100% structural enforcement.
- The rule is process discipline enforced by `/pre-merge`, commit-review hooks, or other mechanical gates.

## 4. Quick decision flow when editing

| Question | Answer |
|---|---|
| Am I adding a new guardrail? | Put it in AGENTS.md (canonical, cross-agent). Claude Code gets it via `@AGENTS.md` import; other AGENTS.md-aware agents (Codex, Cursor, Aider, Gemini CLI, Windsurf, Amp, Lovable) read it natively. |
| The rule applies only when a specific skill is running (e.g., "write backticks literally when constructing a PR body via heredoc")? | Edit that skill's SKILL.md, not CLAUDE.md. CLAUDE.md is loaded every session — a context-specific rule there costs a global line of attention budget on every session that doesn't need it. The skill file is read at the exact moment the rule applies. |
| The repo has CLAUDE.md but no AGENTS.md — should I add AGENTS.md? | Only if a non-Claude AGENTS.md-aware agent (Lovable, Cursor, Codex, Aider, etc.) is also using the repo. Otherwise CLAUDE.md alone is fine. |
| CLAUDE.md is over 200 lines — what should I trim? | First: delete content that duplicates AGENTS.md (use `@AGENTS.md` import instead). Then: collapse narrative case studies into one-sentence principles. Leave only Claude-Code-specific project context. |
| A rule appears in two files — is that OK? | Only if (a) it's critical AND (b) the two files reach different agents / different load paths AND (c) one could silently fail. Otherwise use the import pattern. |
| Where should this rule live — CLAUDE.md or auto-memory? | Team rule → CLAUDE.md (or AGENTS.md). Personal preference / calibration → memory. If CLAUDE.md, AGENTS.md, or a hook already covers it → **neither**; delete the memory. See §5. |

## 5. Claude Code auto-memory (Claude-written, per-user)

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
| Rule that fires only inside a specific skill's flow              | That skill's SKILL.md (not CLAUDE.md, not auto-memory) |
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

## Final step — Deactivate gate session

<!-- HOOK_TEST_FIXTURE: deactivate-gate — the hook-alignment test suite reads this block from claude/.claude/skills/ai-instruction-and-memory-files/SKILL.md to verify it matches require-memory-skill.sh's active-marker cleanup. Do not duplicate elsewhere; the test re-reads it from here. -->
```
~/.claude/scripts/marker.sh deactivate memory-skill
```

Removes this session's bypass marker. If the skill errors before reaching this step, the gate will evict the orphan automatically once the session's process ends — the hook checks PID liveness on each gate hit.
