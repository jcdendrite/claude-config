---
name: lovable-knowledge
description: >
  How Lovable's UI knowledge fields work (Project Knowledge vs Workspace
  Knowledge), the `.lovable/*.md` repo-mirror workflow, content scope
  split, precedence, character limits, and review criteria for `.lovable/**`
  changes.
  TRIGGER when: editing `.lovable/*.md`; drafting changes to Lovable
  Project Knowledge or Workspace Knowledge; deciding which Lovable
  knowledge field a given rule belongs in; reviewing `.lovable/**`
  changes in a PR.
  DO NOT TRIGGER when: the project-level CLAUDE.md does not identify
  this as a Lovable project; editing CLAUDE.md or AGENTS.md (use
  `ai-instruction-and-memory-files`); editing agent-specific rules files
  like `.cursorrules` or `.github/copilot-instructions.md` (out of
  current scope); or writing code.
user-invocable: false
---

# Lovable Knowledge — Architecture & Review

## 1. The four sources Lovable loads

Effective priority order:

1. **Project Knowledge** (Lovable UI field — highest priority)
2. **Workspace Knowledge** (Lovable UI field)
3. **AGENTS.md / CLAUDE.md** (repo files — AGENTS.md has the explicit "always read regardless of session length" guarantee)
4. **Project code**

All four are loaded every session. Lovable docs warn that in very long
conversations instructions can drift; the "always read" guarantee for
AGENTS.md is the defense-in-depth.

## 2. Project Knowledge vs Workspace Knowledge

| | Workspace Knowledge | Project Knowledge |
|---|---|---|
| Scope | Cross-project (the Lovable workspace) | Single-project |
| Use for | Coding style, naming, libraries/frameworks, architectural patterns, testing requirements, lint rules, brand/voice, **Lovable-platform behavior** | What the app does, user personas, schema/tables, architecture decisions, domain terminology, project-specific constraints, design specifics, security/compliance, links to important references |
| Precedence | — | **Prioritized on conflict** |
| Char limit | 10,000 | 10,000 |

When a rule could plausibly fit in either: ask whether it would apply to
a *different* project in the same Lovable workspace. If yes →
workspace. If no (it's tied to *this* product's domain, schema, or
constraints) → project.

## 3. `.lovable/` repo-mirror workflow

`.lovable/project-knowledge.md` and `.lovable/workspace-knowledge.md` are
**version-controlled mirrors** of the corresponding Lovable UI fields.
Lovable only loads the UI fields at runtime; the repo files are not
auto-loaded — they exist for diff review and history.

**Workflow** for updating Lovable knowledge:

1. Draft the change in a PR against the appropriate `.lovable/` file.
2. Bump the "Last synced to Lovable UI" date in the file header in the
   same PR.
3. After merge, the human pastes the merged content into Lovable →
   Settings → Knowledge.

**Do not** assume that writing to `.lovable/` propagates to Lovable. The
UI field is the only thing Lovable reads; the repo file is the
authoring/review surface.

**Do not** skip the repo step and paste directly into the UI without a
PR — that loses diff review, version history, and audit trail.

## 4. Authoring guidance

When writing content for either field:

- **Perspective.** Address Lovable in second person. Knowledge files
  reading as internal engineering notes will confuse Lovable.
- **Specificity.** Lovable follows literally and may over-apply vague
  guidance. "Be careful with auth" can become "add auth checks to public
  endpoints." Spell out the boundary cases.
- **Context budget.** Both fields cap at 10,000 chars. Beyond that,
  Lovable drops content. Even before the cap, length displaces active
  task instructions — apply the same length and behavior-test discipline
  as Claude Code skills (see `ai-instruction-and-memory-files` §3).

## 5. Review checklist for `.lovable/**` changes

When reviewing a PR that touches `.lovable/*.md`:

1. **Perspective** — Is the new content addressed to Lovable in second
   person?
2. **Specificity** — Could the instruction be over-applied (e.g., "be
   careful with auth" applied to public endpoints)?
3. **Scope** — Cross-project conventions → `workspace-knowledge.md`;
   this-app-specific → `project-knowledge.md`. Project knowledge wins
   on conflict.
4. **Char budget** — Is the file approaching the 10,000-char limit?
5. **Sync status** — If `project-knowledge.md` or
   `workspace-knowledge.md` changed, has the "Last synced" date been
   bumped in the same PR? Does the PR description note that the human
   needs to paste the merged content into the Lovable UI?
