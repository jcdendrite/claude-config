---
paths:
  - "**/.claude/rules/*.md"
---

## `paths:` glob-dialect conventions

Sources verified against `code.claude.com/docs/en/memory` §"Path-specific
rules", fetched 2026-09-03; re-verify by 2026-12-03. Full citations and
verbatim quotes live in `docs/rules-references.md` in the claude-config
repo.

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
- **`?` support, leading-`/` anchoring, and trailing-`/` semantics are all
  `[unverified]`** — not stated at the primary source above; don't fill
  them in by inference.
- **A leading `**/` matches zero leading directory segments** — a
  `**/`-led glob also matches a project-root file. Not stated at the
  primary source above, but confirmed empirically
  (`docs/rules-references.md`).
- **A stowed rule's referent is every consumer's repo, not this one** — a
  `paths:` glob's literal prefix must carry no leading segment beyond two
  narrow exemptions, since any other literal prefix either is a typo or
  wrongly assumes some other repo's layout. Exempt: a bare filename with
  no wildcard anywhere (e.g. `CLAUDE.md`, which targets one exact path,
  not an assumed directory), and a two-segment `.claude/`-anchored literal
  path (e.g. `.claude/CLAUDE.md` — `.claude/` is a Claude Code convention
  directory present or absent uniformly in every repo, but a 3rd+ segment
  beneath it names something as repo-specific as any other literal path).
