---
paths:
  - "docs/design-decisions/**"
  - "docs/design-decisions.md"
---

## Design-decision files

Each decision lives in its own file at `docs/design-decisions/<slug>.md`,
one file per decision, migrated from the single-file `docs/design-decisions.md`
to remove the append-at-EOF conflict a shared file forced on concurrent
branches.

- **Filename grammar.** `^[a-z0-9-]+\.md$` — lowercase, digits, and hyphens
  only, no date or number prefix. Choose the slug from the decision's title
  and never change it after the file merges: other files, docs, and
  preserved records may link to it by path.
- **Format.** An H1 title, a blank line, then an italic provenance line
  recording the date (when known) and the legacy section number the
  decision was migrated from, e.g. `*2026-09-01. Formerly
  `docs/design-decisions.md` §41.*`. A decision recorded after the split
  carries no `Formerly §N` clause — that phrase is reserved for content
  that actually occupied a numbered section in the pre-split file.
- **Supersession.** A decision that a later one overturns is edited in
  place to say so, immediately under its own provenance line — e.g.
  `**Superseded by [§49](schedulewakeup-denied-by-bare-tool-name.md)
  (2026-09-04):** ...` — rather than left to silently contradict the newer
  file.
- **No index.** Find a decision by its slug or by `git grep` across the
  directory; a hand-maintained index is another shared append surface and
  goes stale (`docs/case-studies.md`'s index already has).
