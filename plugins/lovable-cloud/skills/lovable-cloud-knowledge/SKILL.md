---
name: lovable-cloud-knowledge
description: >
  Lovable's UI knowledge fields and workspace skill files — `.lovable/*.md`
  repo-mirror workflow, `.agents/skills/` propagation model,
  Project/Workspace scope split, precedence, review criteria.
  TRIGGER when: editing or reviewing PRs for `.lovable/*.md` or `.agents/skills/*.md`; drafting
  Project/Workspace Knowledge or workspace-skill changes.
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
| Repo path | `.lovable/workspace-knowledge.md` | `.lovable/project-knowledge.md` | `.agents/skills/<name>.md` |

When a rule could plausibly fit in either: ask whether it would apply to
a *different* project in the same Lovable workspace. If yes →
workspace. If no (it's tied to *this* product's domain, schema, or
constraints) → project.

Project Knowledge is prioritized over Workspace Knowledge on conflict. See REFERENCES.md for the full four-source load priority (Project Knowledge → Workspace Knowledge → AGENTS.md/CLAUDE.md → project code).

## 2. `.lovable/` repo-mirror workflow (knowledge fields only)

This workflow applies to the **knowledge fields** (`.lovable/project-knowledge.md`
and `.lovable/workspace-knowledge.md`). It does **not** apply to workspace skill
files — see §2.5 for the skill-file propagation model.

`.lovable/project-knowledge.md` and `.lovable/workspace-knowledge.md` are
**version-controlled mirrors** of the corresponding Lovable UI fields.
Lovable only loads the UI fields at runtime; the repo files are not
auto-loaded — they exist for diff review and history.

**Workflow** for updating Lovable knowledge fields:

1. Draft the change in a PR against the appropriate `.lovable/` file.
2. Bump the "Last synced to Lovable UI" date in the file header in the
   same PR.
3. After merge, the human pastes the merged content into Lovable →
   Settings → Knowledge.

**Do not** assume that writing to a `.lovable/` knowledge file propagates to
Lovable. The UI field is the only thing Lovable reads; the repo file is the
authoring/review surface. (Skill files in `.agents/skills/` work differently —
see §2.5.)

**Do not** skip the repo step and paste knowledge-field content directly into
the UI without a PR — that loses diff review, version history, and audit trail.

## 2.5. `.agents/skills/` — repo-native skill files

`.agents/skills/` holds the workspace skill files that Lovable auto-creates when
you define a new skill. Unlike knowledge fields, these files are **not** a
version-controlled mirror of a UI field — they are the source Lovable reads
directly from the repo.

**Editing:** editing `.agents/skills/*.md` via PR is permitted and encouraged.
A file being authored by Lovable (`gpt-engineer-app[bot]`) does **not** make it
read-only — it is a normal repo file on a normal review surface.

**Propagation:** changes reach Lovable when the PR description instructs Lovable
(in second person) to re-read the changed skill files and update its skills.
Example PR description note: *"@Lovable: please re-read `.agents/skills/<name>.md`
and update your `<name>` skill to match."*

**No sync-date header:** the "Last synced to Lovable UI" date header used in
knowledge mirror files does **not** apply here — there is no UI field to sync to.

> **Observation note:** `.agents/skills/` is the path Lovable uses when it
> auto-creates skill files. This is observed behavior, not a documented spec in
> the Lovable docs — re-verify if Lovable changes its file layout. See
> REFERENCES.md for the grounding.

## 3. Authoring guidance

When writing content for either field:

- **Perspective.** Address Lovable in second person. Knowledge files
  reading as internal engineering notes will confuse Lovable.
- **Specificity.** Lovable follows literally and may over-apply vague
  guidance. "Be careful with auth" can become "add auth checks to public
  endpoints." Spell out the boundary cases.
- **Context budget.** Both knowledge fields cap at 10,000 chars. Beyond
  that, Lovable drops content. Skill bodies (`.agents/skills/*.md`) have
  a separate 100,000-char limit. Even before either cap, length displaces
  active task instructions — apply the same length and behavior-test
  discipline as Claude Code skills (see `ai-instruction-and-memory-files` §3).

## 4. Review checklist for `.lovable/**` and `.agents/skills/**` changes

When reviewing a PR that touches `.lovable/*.md` or `.agents/skills/*.md`:

1. **Perspective** — Is the new content addressed to Lovable in second
   person?
2. **Specificity** — Could the instruction be over-applied (e.g., "be
   careful with auth" applied to public endpoints)?
3. **Scope** — Cross-project conventions → `workspace-knowledge.md`;
   this-app-specific → `project-knowledge.md`. Project knowledge wins
   on conflict.
4. **Char budget** — Is the file approaching its char limit? (Check the table above.)
5. **Sync status:**
   - *Knowledge files (`.lovable/*.md`):* Has the "Last synced" date been bumped
     in the same PR? Does the PR description note that the human must paste the
     merged content into Lovable → Settings → Knowledge after merge?
   - *Skill files (`.agents/skills/*.md`):* Does the PR description include an
     instruction to Lovable (second person) to re-read the changed skill files
     and update its skills? (No sync-date header needed for skill files.)

For `.agents/skills/*.md` changes, also check:

6. **Skill name** — Is the name 1–64 chars, lowercase letters / numbers /
   hyphens only, not starting or ending with a hyphen, with no consecutive
   hyphens?
