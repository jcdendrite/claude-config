---
paths:
  - "**/.claude/rules/*.md"
---

## `paths:` glob-dialect conventions

Sources verified against `code.claude.com/docs/en/memory` §"Path-specific
rules", fetched 2026-09-03. Full citations and verbatim quotes live in
`docs/rules-references.md` in the claude-config repo.

- **Brace expansion is supported.** `src/*.{ts,tsx}` expands to two
  patterns (full citation and verbatim quote: `docs/rules-references.md`).
- **Brace expansion is budget-bounded, and overflow fails silently.** A
  rule's whole `paths` list shares a budget of 1,000 expanded patterns and 4
  MiB. A pattern that would exceed the budget is used unexpanded, so its
  literal braces then match no files.
- **A malformed bracket expression also fails silently.** A `[` that can't
  be read as a bracket expression (e.g. `photos [2024/**`) matches nothing;
  the rule's other patterns keep working. Escape a literal `[` as `\[`.
- **A rule with no `paths:` key loads unconditionally at launch**, with the
  same priority as `.claude/CLAUDE.md` — a missing key is a context-budget
  event, not merely a scoping slip.
- **`?` support, leading-`/` anchoring, trailing-`/` semantics, and whether
  `**/` matches zero leading directory segments (i.e. also matches a
  root-level file) are all `[unverified]`** — not stated at the primary
  source above; don't fill them in by inference.
- **A stowed rule's referent is every consumer's repo, not this one** — a
  `paths:` glob with a wildcard must carry no literal segment before it
  (e.g. `**/`-led), since a literal prefix before a wildcard either is a
  typo or wrongly assumes some other repo's layout. A fully literal glob
  with no wildcard anywhere (e.g. `CLAUDE.md`) is exempt: it targets one
  exact path, not an assumed directory.
