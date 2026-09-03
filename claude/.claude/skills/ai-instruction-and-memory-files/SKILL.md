---
name: ai-instruction-and-memory-files
description: >
  Audit of CLAUDE.md, AGENTS.md, .claude/rules/*.md, and Claude Code
  auto-memory files. TRIGGER when: authoring/reviewing a CLAUDE.md,
  AGENTS.md, .claude/rules/*.md, or ~/.claude/projects/*/memory/ change,
  or deciding which surface a rule belongs in. DO NOT TRIGGER when:
  editing .lovable/*.md, .agents/skills/*.md, .cursorrules,
  .github/copilot-instructions.md, README.md, or writing code.
user-invocable: false
---

## Step 0 — Activate gate session

<!-- HOOK_TEST_FIXTURE: activate-gate — the hook-alignment test suite reads this block from claude/.claude/skills/ai-instruction-and-memory-files/SKILL.md to verify it matches require-memory-skill.sh's active-marker layout. Do not duplicate elsewhere; the test re-reads it from here. -->
```
~/.claude/scripts/marker.sh activate memory-skill
```

While active, `require-memory-skill.sh` bypasses for this session (<60 min freshness; mtime refreshed on each pass). If the command fails (empty `SESSION_ID`), abort — `marker.sh` could not resolve this session's id, and every gated memory write below will be blocked.

## Step 1 — Decide where this rule belongs before writing

Before writing any file under `memory/`, check in order:

1. **Grep CLAUDE.md and AGENTS.md** (project tree + `~/.claude/`) for keywords from the rule. If covered → edit the source, don't write to memory. Restating a covered rule is pure load. (See §2 advisory vs deterministic, §3 anti-duplication heuristic.)
2. **Does this rule apply automatically whenever a specific file type or path pattern is open, regardless of which skill or workflow is running?** If yes → a path-scoped rule (`.claude/rules/*.md`), not memory — it auto-loads via `paths` frontmatter matching with no explicit invocation and no every-session cost.
3. **Does this rule fire only inside a specific named skill's workflow?** If yes → that skill's `SKILL.md`, not memory. Skill bodies load at the moment the rule applies; memory loads every session whether the session uses that workflow or not.
4. **Confirm the content fits a genuine memory use, not a rule.** Personal preference is always memory (user type). Feedback, project, and reference content are memory only when they don't generalize to a rule any contributor should follow (→ edit CLAUDE.md/AGENTS.md instead, per item 1): a correction or judgment call specific to how this user works (feedback type), time-sensitive state that fails CLAUDE.md's evergreen/behavior-test bar (project type), or a pointer unsafe to publish in a public repo — e.g. a private dashboard URL (reference type).
5. **Will this be wrong one commit from now?** Bug state, an env-var fix, a failing test, mid-debug context — write `/tmp/<task>-handoff.md` and tell the user the path. Memory outlives the fix; the brief does not.

If step 1, 2, 3, or 5 produces a destination, write there and stop — see §3 for the full routing table.

# AI Instruction & Memory Files — Architecture

The facts below come from primary sources (URLs in co-located REFERENCES.md);
open REFERENCES.md only to verify a specific URL or quote.

How Claude Code loads CLAUDE.md/AGENTS.md — the `@AGENTS.md` import, `@path`
resolution, precedence, and when a repo should add an AGENTS.md — lives in
`~/.claude/rules/claude-md-conventions.md`. It auto-loads whenever one of
those files is open. Read it directly when reviewing a diff with neither open.

## 1. Length targets

Official threshold: **under 200 lines per CLAUDE.md file** — longer files
consume more context and reduce adherence. The unit is **lines** — no
Anthropic source attaches a word-count threshold. Within that cap, line
count undercounts density (long-paragraph lines bury rules), and
attention decays in the middle ("lost in the middle"). Apply the
**behavior test** below per line; place critical rules near start or end. CLAUDE.md is **advisory** while
hooks are **deterministic**; they guarantee the action happens — prefer a
hook or structural test when a rule can be encoded as one. The 200-line cap is hook-enforced at commit time by `check-claude-md-length.sh` for CLAUDE.md and AGENTS.md.

### The behavior test

Every line should change Claude's behavior on at least one realistic
input. If removing the line wouldn't change any behavior, cut it.
Specific incident references, editorial meta-commentary, and narrative
case studies almost always fail this test; rationale that arms Claude
for an unenumerated edge case almost always passes.

**Compression-diff audit.** For any diff that removes or shortens lines, fill this table before declaring the review complete:

| Removed/shortened text | Surviving line | Behavior-preserving? |
|---|---|---|
| (quote) | (cite) | Y — (quote surviving line) / N — (what is lost) |

Any N is a finding; restore the dropped instruction. Y requires citing the specific surviving line — "the meaning is the same" is not sufficient. Typical CLAUDE.md/AGENTS.md/memory drops: symptom triggers, explicit action instructions, escape-hatch exceptions, verification commands.

### Don't embed PR or ticket refs in always-loaded files

`Precedent: PR #105` and `See TICKET-123` rot the moment the next PR
lands and cost per-session context budget. Put them in commit messages,
PR descriptions, or plan files — state the rule, not the precedent.

**Over the cap — trim in this order:**

1. Delete content duplicating AGENTS.md; use the `@AGENTS.md` import instead.
2. Collapse narrative case studies into one-sentence principles.
3. Keep only Claude-Code-specific project context.

## 2. When to duplicate vs. reference

**Reference via `@AGENTS.md` import (Anthropic pattern):** default for
Claude Code when both files exist. Zero maintenance, single source.

**Duplicate (defense-in-depth) when:**
- The content is genuinely critical and one delivery mechanism could fail silently.
- Different agents reach the file through different load paths and the rule needs to fire in all of them.
- Example: a security-critical rule appearing in AGENTS.md (advisory) AND enforced by a pre-commit hook (deterministic) — the hook catches what prose drift misses.

**Do NOT duplicate when:**
- An AGENTS.md-aware agent already reads it natively and Claude Code can import it via `@AGENTS.md` — one canonical source covers both.
- The content is enforced structurally (a test, a hook) — prose duplication adds maintenance without raising adherence above the deterministic enforcement.
- The rule is process discipline enforced by `/pre-merge`, commit-review hooks, or other mechanical gates.

## 3. Claude Code auto-memory (Claude-written, per-user)

Auto-memory at `<config-dir>/projects/<project>/memory/` (`<config-dir>`
means `$CLAUDE_CONFIG_DIR` when set, else `~/.claude`) is adjacent to
CLAUDE.md but serves a different role. It's machine-local and per
working tree — never a place for team rules.

|                  | CLAUDE.md files                                   | Auto memory                                                      |
| :--------------- | :------------------------------------------------ | :--------------------------------------------------------------- |
| Who writes it    | You                                               | Claude                                                           |
| What it contains | Instructions and rules                            | Learnings and patterns                                           |
| Scope            | Project, user, or org                             | Per working tree (machine-local)                                 |
| Loaded into      | Every session                                     | Every session (first 200 lines or 25KB of `MEMORY.md`)           |
| Use for          | Coding standards, workflows, project architecture | Build commands, debugging insights, preferences Claude discovers |

### `MEMORY.md` is an index, not a memory

Index discipline:

- One line per entry, ≤150 characters: `- [title](file.md) — one-line hook`
- Hooks for action-prescribing memories keep the body's guard: "run X when asked", never bare "run X" — if the guard will not fit the character cap, the entry is not indexable as action-prescribing; split the memory or drop the imperative from the hook
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
| Rule that applies only while a specific file type or path pattern is open, independent of which skill runs | `.claude/rules/*.md` (path-scoped, auto-loads via `paths` frontmatter) |
| Past incident "why" not captured in code, tests, or commit msgs  | Auto-memory (feedback or project type)           |
| Pointer to external systems (Linear, Grafana, etc.)              | Auto-memory (reference type)                     |
| Restatement of a rule already in CLAUDE.md / AGENTS.md           | **Nowhere — delete it** (§2 advisory vs deterministic)|
| Rule already enforced by a hook or structural test               | **Nowhere — the hook enforces it; prose is load**|

### Anti-duplication heuristic

If CLAUDE.md / AGENTS.md already covers a rule, the matching memory is
pure load: the rule fires every session through the instruction file,
the index line consumes one of the ~200 loaded lines, and any recall
reads a topic file that restates content already in context. **Delete
on contact.** Confirm the covering text still exists before deleting —
re-read the CLAUDE.md, skill, or hook line said to cover the rule. A
redirect can outlive the content it pointed at, and deleting against a
stale pointer drops the rule from every surface at once.

Memory earns its keep when it captures what the repo *doesn't*: who
the user is and how they prefer to collaborate, feedback calibration
(corrections **and** validated judgment calls) with the *why* story,
time-sensitive project context, and references to external systems —
provided the content doesn't generalize into a rule any contributor
should follow (edit CLAUDE.md/AGENTS.md instead) and, in a public
repo, is safe to publish. Scope a saved decision to the decision, not
to the ambient condition that made it easy — "the X path accepts Y
tradeoff" survives, "the user's environment has property Z" goes
stale silently and biases later design.

## 4. Path-scoped rule files (`.claude/rules/*.md`)

Carries over — plus, for a rule file, *which globs it needs*:
- The behavior test and compression-diff audit (§1).
- Duplicate-vs-reference (§2).
- Anti-duplication (§3).
- Step 1 item 2 placement.

Does NOT carry over: §1's 200-line CLAUDE.md cap, and §3's auto-memory
mechanics — a rule file is neither.

Two checks apply only to rule files:
1. **Glob-set/body-applicability match** — every `paths:` glob must have the whole body applicable to what it matches, or the body must qualify bullets by name.
2. **Stowed-rule portability** — a stowed rule's globs carry no literal segment before a wildcard (a fully literal glob with no wildcard is exempt); its referent is every consumer's repo.

## Final step — Deactivate gate session

<!-- HOOK_TEST_FIXTURE: deactivate-gate — the hook-alignment test suite reads this block from claude/.claude/skills/ai-instruction-and-memory-files/SKILL.md to verify it matches require-memory-skill.sh's active-marker cleanup. Do not duplicate elsewhere; the test re-reads it from here. -->
```
~/.claude/scripts/marker.sh deactivate memory-skill
```

Removes this session's bypass marker. If the skill errors before reaching this step, the gate will evict the orphan automatically once the session's process ends — the hook checks PID liveness on each gate hit.
