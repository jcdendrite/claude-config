# Document that Read-consumed skills show zero `/doctor` invocations by design

## Context

`/doctor` flagged three skills — `error-handling`, `sql-query-conventions`,
`git-state-safety` — as zero-invocation across 1,581 sessions and offered them
as cleanup candidates. Investigation confirmed this is a **false positive**, not
dead code:

- `error-handling` and `sql-query-conventions` are consumed primarily via
  `Read ~/.claude/skills/<name>/SKILL.md` from reviewer agents
  (`staff-backend-engineer`, `staff-frontend-engineer`, `code-writer`) — the
  deliberate least-privilege wiring recorded in `docs/design-decisions.md` §15.
  A `Read` never registers as a `Skill` invocation, so `/doctor` cannot count it.
  They also carry `invoke the X skill` pointers in `code-review`/`plan-review`,
  but those are gated on narrow conditions (error-envelope changes, SQL hot
  paths) that rarely coincide with a review.
- `git-state-safety` is a rare-scenario safety net (inspecting a ref mid-
  merge/rebase/cherry-pick); zero reflects that the condition hasn't occurred.

All three are referenced, two are test-enforced (`test_skills.py:422–442`), and
removing any would break the review pipeline, documented design, and the suite.
**No behavioral change is warranted.** The `Read`-vs-invoke choice was already
evaluated and settled: for a pure-knowledge skill, `Read` delivers identical
content, is available to tool-restricted agents (which lack the `Skill` tool by
design), is deterministic, and is the lighter primitive; converting to invoke
would require a privilege-escalating allowlist grant for zero knowledge gain and
would not even auto-trigger inside a subagent.

The one gap: nothing in the docs states the **telemetry corollary** of the §15
decision — that Read-path consumption produces zero `Skill`-invocations, so
`/doctor` will report these skills as zero by design. Absent that note, the same
investigation recurs on the next `/doctor` run.

Intended outcome: a one-sentence corollary appended to the §15 decision record
so the false positive is pre-answered at its single source.

## Approach

Add one sentence to `docs/design-decisions.md` §15 ("Convention skills wired by
explicit pointer, not description-based auto-trigger"), stating that because
these consumers `Read` rather than invoke, `/doctor` reports zero
`Skill`-invocations for the wired skills, and that this is the expected
consequence of the §15 decision — not a dead-skill signal. Attach it
immediately after the consumer-list paragraph (the one enumerating
`code-writer` / `staff-sdet` / `staff-backend-engineer` / `code-review`), where
the `Read`-consumption is established — not at the literal end of §15, which is
the unrelated `skill-review` plugin-exemption paragraph.

Single-source rationale: §15 already owns the "wired by `Read`, not invocation"
decision; the `/doctor`-zero fact is a direct corollary and belongs next to it,
not duplicated into `docs/skills.md` (which stays a catalog entry). `git-state-
safety`'s zero has a separate, self-evident cause (rare trigger condition) that
needs no doc treatment — documenting it would be over-documentation.

**Alternatives set aside:**
- *Add the note to `docs/skills.md:30`* — would split the rationale across two
  files; §15 is the canonical home for the underlying decision.
- *No change at all* — leaves the false positive to recur; a one-sentence note
  is cheap insurance at the correct source.

## Critical files

- `docs/design-decisions.md` — insert one sentence into §15, immediately after
  the consumer-list paragraph. No new file, no new section, no change to the
  enumerated consumer list (reuse the existing list rather than restating it).

## Verification

- Docs-only change. Scan the §15 paragraph for readability and that the corollary
  reads cleanly without the PR context (it must survive merge per the
  self-contained-prose rule).
- Run the existing suite to confirm no doc-referencing test breaks:
  `../../../.venv/bin/pytest claude/.claude/` (from the worktree). None expected —
  no test asserts on §15 prose.
- `ruff` N/A (Markdown).

## Out of scope

- No `Skill`-tool allowlist grants on any agent (`Read` stays).
- No verb change on the reviewer-agent or dispatcher pointers.
- No change to the gating conditions on the `code-review`/`plan-review` invoke
  pointers.
- No test changes.
