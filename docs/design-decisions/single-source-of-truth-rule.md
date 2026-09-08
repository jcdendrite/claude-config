# Single source of truth elevated to a canonical CLAUDE.md rule

*2026-05-22. Formerly `docs/design-decisions.md` §13.*

Duplicated content — the same rule, value, or explanation copied across files — recurred as a PR-review finding. The global `CLAUDE.md` carried only a partial form ("avoid duplicating managed values across files where they can drift out of sync"), scoped to values and readable as code-only. The Engineering Judgment bullet was rewritten as a general single-source-of-truth rule.

SSOT and DRY are the same principle: DRY's canonical definition *is* the single-source-of-truth statement, and DRY is explicitly about *knowledge* — specifications and documentation included — not duplicated code text. That is why the rule covers prose and docs, where the recurring findings landed. A `/code-review` checklist item was considered and rejected: it would be a second copy of an always-loaded rule on a surface that can drift from it — the exact failure the rule names. `/code-review`'s existing item 9 (repeated logic) already operationalizes the principle for code.

The exceptions are deliberate, not loopholes: test code is DAMP rather than DRY (readability earns some repetition); load-bearing instructional prose duplicated across files that must each stand alone (this repo's skills — see [§4](no-shared-skill-partials.md)); and a small duplicated value over a bad abstraction built only to remove it.

## Sources

- Dave Thomas & Andy Hunt, *The Pragmatic Programmer* (20th Anniversary Edition, Addison-Wesley, 2020), "The Evils of Duplication" — Tip 15, DRY: "Every piece of knowledge must have a single, unambiguous, authoritative representation within a system." The chapter frames duplication as duplicated *knowledge* across specifications, code, and tests — not duplicated code text — which is why the rule extends to prose and docs. Verified against the publisher's chapter extract (media.pragprog.com).
- DAMP ("Descriptive And Meaningful Phrases") — the readability-over-deduplication counterpart for test code; a mid-2010s community term with no single canonical author. Already used uncited in `code-review`'s SKILL.md item 9.
