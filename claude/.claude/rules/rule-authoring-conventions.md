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
- **A `**/`-led pattern also matches a root-level file.** `**/CLAUDE.md`
  fires on a repo-root `CLAUDE.md`, so pairing it with a bare `CLAUDE.md`
  entry adds nothing. Established by measurement in both `-p` and
  interactive sessions, not stated by the source above (method and
  limits: `docs/rules-references.md`).
- **`?` support, leading-`/` anchoring, and trailing-`/` semantics are all
  `[unverified]`** — not stated at the primary source above; don't fill
  them in by inference.
- **A stowed rule's referent is every consumer's repo, not this one** —
  every `paths:` glob must be `**/`-led, with no leading literal path
  segment. A leading literal directory assumes some other repo's layout.
  A bare filename (`CLAUDE.md`) or a `.claude/`-anchored literal
  (`.claude/CLAUDE.md`) matches a strict subset of its `**/`-led form,
  which already covers the root-level file.
