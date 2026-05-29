---
name: lovable-cloud-knowledge
description: >
  Lovable's UI knowledge fields — `.lovable/*.md` repo-mirror workflow,
  Project/Workspace scope split, precedence, review criteria.
  TRIGGER when: editing `.lovable/*.md` or `.lovable/skills/*.md`; drafting
  Project/Workspace Knowledge or workspace-skill changes; reviewing `.lovable/**` PRs.
  DO NOT TRIGGER when: not a Lovable project; editing CLAUDE.md or AGENTS.md;
  editing agent rules files; writing code.
user-invocable: false
---

# Lovable Knowledge — Workflow & Review

## 1. Knowledge fields and Workspace Skills

| | Workspace Knowledge | Project Knowledge | Workspace Skills |
|---|---|---|---|
| Scope | Cross-project (the Lovable workspace) | Single-project | Cross-project (the Lovable workspace) |
| Use for | Coding style, naming, libraries/frameworks, architectural patterns, testing requirements, lint rules, brand/voice, **Lovable-platform behavior** | What the app does, user personas, schema/tables, architecture decisions, domain terminology, project-specific constraints, design specifics, security/compliance, links to important references | Reusable procedures Lovable runs on demand |
| Precedence | — | **Prioritized on conflict** | — |
| Char limit | 10,000 | 10,000 | **100,000** |

When a rule could plausibly fit in either: ask whether it would apply to
a *different* project in the same Lovable workspace. If yes →
workspace. If no (it's tied to *this* product's domain, schema, or
constraints) → project.

Project Knowledge is prioritized over Workspace Knowledge on conflict. See REFERENCES.md for the full four-source load priority (Project Knowledge → Workspace Knowledge → AGENTS.md/CLAUDE.md → project code).

## 2. `.lovable/` repo-mirror workflow

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

## 3. Authoring guidance

When writing content for either field:

- **Perspective.** Address Lovable in second person. Knowledge files
  reading as internal engineering notes will confuse Lovable.
- **Specificity.** Lovable follows literally and may over-apply vague
  guidance. "Be careful with auth" can become "add auth checks to public
  endpoints." Spell out the boundary cases.
- **Context budget.** Both knowledge fields cap at 10,000 chars. Beyond
  that, Lovable drops content. Skill bodies (`.lovable/skills/*.md`) have
  a separate 100,000-char limit. Even before either cap, length displaces
  active task instructions — apply the same length and behavior-test
  discipline as Claude Code skills (see `ai-instruction-and-memory-files` §3).

## 4. Review checklist for `.lovable/**` changes

When reviewing a PR that touches `.lovable/*.md` or `.lovable/skills/*.md`:

1. **Perspective** — Is the new content addressed to Lovable in second
   person?
2. **Specificity** — Could the instruction be over-applied (e.g., "be
   careful with auth" applied to public endpoints)?
3. **Scope** — Cross-project conventions → `workspace-knowledge.md`;
   this-app-specific → `project-knowledge.md`. Project knowledge wins
   on conflict.
4. **Char budget** — Is the file approaching its char limit? (Check the table above.)
5. **Sync status** — Does the PR description note that the human must
   paste the merged content into the Lovable UI after merge? (Knowledge
   files → Settings → Knowledge; skill bodies → Settings → Skills.) For
   knowledge files, also verify the "Last synced" date is bumped in the
   same PR.

For `.lovable/skills/*.md` changes, also check:

6. **Skill name** — Is the name 1–64 chars, lowercase letters / numbers /
   hyphens only, not starting or ending with a hyphen, with no consecutive
   hyphens?
