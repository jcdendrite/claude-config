# Extend the `error-handling` skill with a propagation & capture axis

## Context

The global `error-handling` skill (`claude/.claude/skills/error-handling/`) today has
eight rules and three anti-patterns, all governing the **response envelope** — the shape
that crosses the wire to a consumer. It is silent on the orthogonal axis: how an error
travels from origin to response (throw vs. return), where telemetry capture lives, and how
serverless/edge runtimes must flush before termination. That silence let a real system ship
a wrong design (telemetry capture wired into a pure response formatter instead of a handler
boundary). This change adds a second, primary-source-backed axis so future work has one
authoritative reference for both the error *envelope* and the error *propagation/capture*
model. The content is engineer-approved as drafted and vendor-neutral — no project names,
no secrets (this repo is public).

Scope is two files in one skill directory plus its frontmatter description.

## Approach

Add the new axis as a distinct top-level section so it reads as orthogonal to the eight
envelope rules, not as more envelope rules. Section placement and the frontmatter
description update were confirmed with the user.

Line numbers below are pre-edit locators for the current file; anchor each Edit on the
surrounding heading text, not absolute line numbers (editing the frontmatter in step 4 shifts
every line below it).

**1. `SKILL.md` — new `## Error propagation & capture (server-side)` section.**
Insert immediately after "The eight rules" (ends ~line 72) and before "## Anti-patterns"
(~line 74). Four rules **P1–P4** as `### P1` … `### P4` to match the existing `### Rule N`
heading level. The operational-vs-programmer-error distinction is cited under **P2** (triage),
not P1.

**2. `SKILL.md` — fold anti-patterns D–F into the existing `## Anti-patterns` section.**
Append `### D`, `### E`, `### F` after the current `### C` (line 97).

**3. `SKILL.md` — seven new Review-checklist items (13–19).** Append after the current
item 12 (line 112). P1–P4 and anti-patterns D, E, F each get a corresponding checklist item.

**4. `SKILL.md` — extend the frontmatter `description:`.** The description is the skill's
trigger surface, loaded every session. It currently names only the envelope axis, so the
skill would not surface for propagation/capture/telemetry-flush prompts despite now covering
them. Reword to add the propagation axis (throw-vs-return, centralized capture boundary,
serverless flush) alongside the existing envelope phrasing. No change to envelope wording.

**5. `REFERENCES.md` — append the citation dossier.** Add the 16-source dossier matching
the file's existing per-source block format (`## Source`, `**URL:**`,
`**Status:**`, verbatim quote, `**Cited for**`). Mark Joyent/Node.js **PARTIAL** (canonical
host is JS-gated; verbatim confirmed via author Dave Pacheco's blog mirror + corroborating
sources); all others **VERIFIED**. Map each quote to the rule it grounds (P1–P4).

Why this shape rather than alternatives: a separate section (vs. extending "The eight rules"
to a ninth+ rule) keeps the two axes visually and conceptually distinct — envelope = what
crosses the wire, propagation = how the error gets there and where it's reported. Folding
D–F into the existing Anti-patterns block (vs. a second anti-patterns block) keeps one
anti-pattern list the reader scans once.

## Critical files

- `claude/.claude/skills/error-handling/SKILL.md` — new section (after line 72), three
  anti-patterns (after line 97), checklist items 13–19 (after line 112), frontmatter
  `description:` reword (lines 3–5). **Do not touch** the eight envelope rules or their
  wording.
- `claude/.claude/skills/error-handling/REFERENCES.md` — append 16-source dossier after the
  current last block (line 86). **Do not reword** the existing envelope citations.

Edit the **repo source** under `claude/.claude/skills/...`, never the stowed mirror at
`~/.claude/skills/error-handling/` (it's a symlink into this repo; the worktree path resolves
to the linked worktree, not the main tree).

Reuse: the new content mirrors the file's existing idioms — `### Rule N` heading level for
`### PN`, the numbered checklist continuation, and the REFERENCES per-source block format. No
new files; no shared partials (this repo forbids cross-skill `_shared/`).

## Verification

- `../../../.venv/bin/pytest claude/.claude/` — skill/hook test suite passes (run from the
  worktree; the `.venv` lives at the main worktree root, three levels up).
- `../../../.venv/bin/ruff check claude/.claude/` — lint clean.
- Manual read-through: confirm P1–P4 and D–F contain no PR-defined terminology or "used to
  be X" framing, and that every internal cross-ref (Rule 1, Rule 4, Rule 8, Anti-pattern C)
  resolves within the file.
- Invoke `/skill-review` against the SKILL.md diff and address findings. This is
  **hook-enforced** (`require-skill-review.sh` blocks `git commit` on a SKILL.md change until
  the behavioral-equivalence marker is written). Watch for a verbosity finding — the body
  grows ~50 lines; each rule must earn its place or skill-review will flag it.
- Run `/code-review`; it dispatches `/skill-review` for the SKILL.md file automatically.

## Execution order (post-approval)

1. `branch-creation` skill → slug, then `git worktree add .claude/worktrees/<slug> -b <slug>`
   from the main tree (worktree enforcement is active).
2. Move this plan into the worktree's **repo-root** `.claude/plans/<slug>.md` (tracked — see
   `.gitignore`: `.claude/plans/` ships with the PR). Do **not** leave it at
   `~/.claude/plans/` → that resolves to the stow-package path `claude/.claude/plans/`, which
   is gitignored; a plan left there is an orphaned scratch file the PR won't include (B17).
3. Edits 1–5 above, targeting the worktree path under `claude/.claude/skills/error-handling/`.
4. `/skill-review` → fix findings; `/code-review` → fix findings.
5. Run pytest + ruff from the worktree.
6. **Confirm with the engineer before pushing / opening the PR** (external action; shared
   state). PR per repo conventions; vendor-neutral, no project names/secrets.
7. **Do not merge** — engineer-only (repo rule: AI agent that opens a PR does not merge it).

## Out of scope

- Project-specific implementation (a concrete error-boundary wrapper or typed error class for
  any particular codebase) — belongs in the consuming repo, not this vendor-neutral skill.
- The existing eight envelope rules and their REFERENCES citations — not reworded.
- Adding a pointer to P1–P4 from the `lovable-cloud` plugin's edge-functions skill — kept
  out of scope; file separately if desired.
