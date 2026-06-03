# Plan: Lovable skill files are editable — correct path + propagation in `lovable-cloud-knowledge`

## Context

A project-implementing session that had Lovable skills loaded got into a false scope panic:
it read its project's CLAUDE.md rule — *"treat this directory [`.agents/skills/`] as
Lovable-owned: do not edit"* — as an absolute prohibition, and flagged a prior session's
legitimate edit to those files (authored by `gpt-engineer-app[bot]`) as *"a direct violation."*

Clarification confirmed: **editing Lovable skill files is fine.** The repo file is a normal
review surface; the only requirement is that the PR description tell Lovable to **re-read the
changed files and update its skills** (Lovable confirmed in-product that it can apply changes
from the repo files itself). Two corrections fall out of this:

1. **Path.** The distributed `lovable-cloud-knowledge` skill documents Lovable skill files at
   `.lovable/skills/*.md`. That path was an arbitrary local convention — not derived from
   Lovable's own file-creation behavior. When Lovable creates a skill it **auto-writes it to
   `.agents/skills/`** — that is the canonical path, and it is what the skill (and related docs)
   should say.
2. **Propagation model.** The skill currently lumps skill bodies into the knowledge-field
   "repo-mirror, human pastes into the UI" model (§4 item 5: *"skill bodies → Settings →
   Skills"*). That is wrong for skills: `.agents/skills/` files are the source Lovable reads,
   not a mirror of a UI field. Propagation is *"instruct Lovable to re-read the repo files,"*
   not *"paste into a Settings field."*

**Intended outcome:** the reusable `lovable-cloud-knowledge` skill states plainly that
Lovable-authored skill files (`.agents/skills/`) are editable via PR and how their changes
reach Lovable — so no future session re-derives the false "do not edit" reading. Plus
drop-in replacement text for the private project's CLAUDE.md rule (the actual trigger), which
the user applies in that repo since it is not reachable from `claude-config`.

## Approach

Knowledge fields and skills are **two different models**; the current skill conflates them.
The edit separates them:

| | Knowledge fields | Skills |
|---|---|---|
| Canonical home | Lovable UI field (Settings → Knowledge) | `.agents/skills/*.md` in the repo |
| Repo file role | Manual version-controlled **mirror** (`.lovable/*.md`) | The **source** Lovable reads (auto-written by Lovable) |
| Editable in repo? | Yes, via PR | Yes, via PR — Lovable-authorship does **not** make them read-only |
| Propagation | Human pastes merged content into the UI | PR description instructs Lovable to re-read the changed files and update its skills |

Knowledge-field guidance (§1 table, §2 mirror workflow, the `.lovable/*.md` paths) is
**unchanged** — those files are UI-only and the manual-mirror convention stands. Only the
skill-file treatment changes: path `.lovable/skills/*.md` → `.agents/skills/*.md`, and the
propagation/editability correction.

Grounding: the Lovable Skills doc (docs.lovable.dev/features/skills) confirms an
"Import from GitHub" pull-based path exists but pins **neither** a repo directory nor an
auto-sync mechanism — so `.agents/skills/` as canonical and "Lovable re-reads the repo files"
rest on Lovable's observed in-product behavior, recorded as such in REFERENCES.md rather than
asserted as documented fact.

### Why the skill, not a memory or CLAUDE.md rule

`lovable-cloud-knowledge` is the single authoritative home for "how Lovable knowledge/skill
files work," distributed via the `lovable-cloud` plugin to every project that installs it.
Encoding the editability + propagation rule there fixes the general case for all consumers.
The private project's CLAUDE.md is the *trigger* but the wrong home for the general rule — it
gets a short rule that **defers to** the skill (see Deliverable 2), not a restatement.

## Critical files

**Edit — `plugins/lovable-cloud/skills/lovable-cloud-knowledge/SKILL.md`** (primary):
- **Frontmatter description** (lines 4–9): change the skill-file token `.lovable/skills/*.md`
  → `.agents/skills/*.md` in TRIGGER; keep `.lovable/*.md` for knowledge. Keep DO NOT TRIGGER
  coherent.
- **§1 table** (line 20, "Workspace Skills" column): note skills live in `.agents/skills/`.
- **§2 repo-mirror workflow** (lines 31–51): scope its "UI field is the only thing Lovable
  reads / writing to the repo does not propagate" claims explicitly to **knowledge fields**,
  so they no longer read as applying to skills.
- **New skill-file subsection** (after §2): skills live in `.agents/skills/` (Lovable writes
  them there — phrase as observed behavior, cross-linking REFERENCES.md, **not** as a
  doc-pinned spec, so a future reader re-verifies if Lovable's layout changes); they are
  normal repo files **editable via PR by humans or other agents** — being authored by Lovable
  / `gpt-engineer-app[bot]` does not make them read-only; propagation = the PR description must
  instruct Lovable (second person) to re-read the changed files and update its skills.
  **Carve-out:** the "Last synced to Lovable UI" header/date is a *knowledge-mirror* concept
  (§2) and does **not** apply to `.agents/skills/` files — they are the source Lovable reads,
  not a mirror of a UI field, so they carry no sync-date header.
- **§3 char budget** (line 63): `.lovable/skills/*.md` → `.agents/skills/*.md`.
- **§4 review checklist** (lines 70, 85): path token updates; **revise item 5** so skill files
  use "PR description instructs Lovable to re-read the changed repo files" propagation, while
  knowledge files keep "human pastes merged content into Settings → Knowledge." The skill-file
  branch checks for the **re-read instruction**, not a "Last synced" date (sync-date is
  knowledge-mirror-only, per the §2 carve-out above).

**Edit — `plugins/lovable-cloud/skills/lovable-cloud-knowledge/REFERENCES.md`**:
- Record the decision: `.agents/skills/` is canonical (observed from Lovable's own
  file-creation behavior); `.lovable/skills/` was a deprecated local convention; the
  GitHub-import / repo-read propagation path (docs confirm import-from-GitHub exists, but
  pins no path or auto-sync). Per the repo's "decision records go in REFERENCES.md" convention.

**Edit — `plugins/lovable-cloud/.claude-plugin/plugin.json`** (line: `"version": "2.4.1"`):
- Bump per the `plugin-semver` skill. New guidance + corrected behavior → likely **minor**
  (`2.5.0`); confirm at implementation by running the skill.

**Edit (secondary, consistency) — `claude/.claude/skills/ai-instruction-and-memory-files/SKILL.md`**
line 7: its `DO NOT TRIGGER when: editing .lovable/*.md` boundary should also exclude
`.agents/skills/*.md`, so it defers to `lovable-cloud-knowledge` for Lovable skill files
instead of firing on them. Small, in-the-same-spirit touch; in scope per the user's request to
update "related docs/instructions/skills."

**No change:** `README.md:179`, `plugin.json` description, `docs/skills.md:21`,
`lovable-cloud-edge-functions/*`, `lovable-cloud-migration-sync/*` — none reference a
skill-file path; grep-confirmed.

### Reuse / consistency opportunities
- Mirror the **second-person "message to Lovable"** pattern already used by
  `lovable-cloud-migration-sync/SKILL.md:49,54` (a copy-paste message addressed to Lovable)
  when wording the new "instruct Lovable to re-read the files" guidance — same family, keep
  the voice consistent.
- Reuse §3's existing **100,000-char skill limit** and behavior-test discipline; the new
  subsection should not restate them, only cross-link.

## Deliverables outside the edit

**Deliverable 2 — private project CLAUDE.md reconciliation text** (user applies it in that
repo; not reachable from here). Drop-in replacement for the *"treat this directory as
Lovable-owned: do not edit"* rule, roughly:

> `.agents/skills/` holds Lovable-authored workspace skills. You **may** edit them via PR —
> Lovable authorship does not make them read-only. When a PR changes them, add a note in the
> PR description instructing Lovable to re-read the changed files and update its skills. See
> the `lovable-cloud-knowledge` skill for the full workflow.

This is produced as text in the implementation output, not committed to `claude-config`.

## Verification

- `.venv/bin/pytest claude/.claude/` and `.venv/bin/ruff check claude/.claude/` (frontmatter /
  structural tests for skills) pass.
- **`/skill-review`** on the `lovable-cloud-knowledge` SKILL.md diff (hook-enforced) **and** on
  the `ai-instruction-and-memory-files` SKILL.md diff — run the skill on its own edited bodies
  per the repo's "run the skill on its own diff" rule; confirm the behavioral-equivalence
  markers are written.
- **`plugin-semver`** confirms the version bump is correct for the change class.
- **`/code-review`** (dispatches `/skill-review` per file type) before presenting.
- Manual read-through: knowledge-field guidance unchanged; skill-file guidance now says
  `.agents/skills/`, editable-via-PR, propagation-by-instructing-Lovable; no internal
  contradiction between §2's knowledge claims and the new skill subsection.
- Grep check: no remaining `.lovable/skills` outside REFERENCES.md's "deprecated convention"
  note.

## Out of scope

- **Editing the private project's CLAUDE.md** — different repo, unreachable from here;
  delivered as text (Deliverable 2) for the user to apply.
- **Reclassifying the knowledge mirror files** (`.lovable/project-knowledge.md`,
  `.lovable/workspace-knowledge.md`). The correction was about *skills*; knowledge fields
  are UI-only with no canonical repo path, so the `.lovable/*.md` mirror convention stays.
- Sibling skills (`edge-functions`, `migration-sync`) and README/plugin description — no
  skill-file path references; untouched.
