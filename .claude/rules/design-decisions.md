---
paths:
  - "docs/design-decisions/**"
  - "docs/design-decisions.md"
---

## Design-decision files

Each decision lives in its own file at `docs/design-decisions/<slug>.md`,
one file per decision.

- **Filename grammar.** `^[a-z][a-z0-9-]*\.md$` — lowercase, digits, and
  hyphens only, no leading digit, no date or number prefix. Choose the slug
  from the decision's title and never change it after the file merges:
  other files, docs, and preserved records may link to it by path.
- **Format.** An H1 title, a blank line, then an italic provenance line
  recording the date (when known) and the legacy section number the
  decision was migrated from, e.g. `*2026-09-01. Formerly
  `docs/design-decisions.md` §41.*`. A decision recorded after the split
  carries no `Formerly §N` clause — that phrase is reserved for content
  that actually occupied a numbered section in the pre-split file. A
  migration that reassigns a legacy §N to a different decision (resolving a
  duplicate-numbering collision) must add the reassigned number to
  `_REASSIGNED_LEGACY_NUMBERS` in
  `claude/.claude/hooks/tests/test_design_decision_files.py`. The legacy
  `§N` set is closed at `§63` — the retired monolith cannot grow, so no
  file will ever carry a `Formerly §N` clause above that number.
- **Supersession.** A decision that a later one overturns is edited in
  place to say so, immediately under its own provenance line — e.g.
  `**Superseded by [§49](schedulewakeup-denied-by-bare-tool-name.md)
  (2026-09-04):** ...` — rather than left to silently contradict the newer
  file.
- **No index.** Find a decision by its slug or by `git grep` across the
  directory. A hand-maintained index is another shared append surface that
  goes stale, as `docs/case-studies.md`'s index already has.
