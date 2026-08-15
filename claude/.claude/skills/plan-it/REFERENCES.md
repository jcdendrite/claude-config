# References — /plan-it

Sources consulted during F-08 design. Read this file when editing SKILL.md
to verify a rule still holds or to add new guidance.

## Plan/spec/RFC section-structure surveys

### Google design doc (primary)
https://www.industrialempathy.com/posts/design-docs-at-google/

Sections: Context and scope · Goals and non-goals · The actual design ·
Alternatives considered · Cross-cutting concerns.

Key quote: "This isn't a requirements doc — keep it succinct!" Rationale is
required; length is not. Goals and non-goals appear in every format Google
uses.

### Squarespace RFC template (primary PDF)
https://engineering.squarespace.com/s/Squarespace-RFC-Template.pdf

Sections: Overview · Goals and Non-Goals · Background & Motivation · Design ·
Timeline · Dependencies · Alternatives Considered/Prior Art · Operations ·
Security/Privacy/Compliance · Risks · Revisions.

Heavy format — Timeline, Operations, Security sections exist because
Squarespace RFCs cover quarter-scale features needing org-wide signoff. Not
appropriate for single-PR scope.

### Michael Nygard ADR (primary)
https://github.com/joelparkerhenderson/architecture-decision-record/blob/main/locales/en/templates/decision-record-template-by-michael-nygard/index.md

Sections: Title · Status · Context · Decision · Consequences.

ADRs are decision records, not implementation plans. Useful framing for the
Context/Decision split but not directly applicable to plan-it's output format.

### Pragmatic Engineer — cross-company RFC survey (secondary)
https://blog.pragmaticengineer.com/rfcs-and-design-docs/

Covers Sourcegraph (Summary · Background · Problem · Proposal · Definition of
success) and HashiCorp (Background · Proposal · Abandoned ideas). Secondary
source — Sourcegraph/HashiCorp section names come from this survey, not the
companies' own published templates.

## AI-coder plan patterns

### superpowers writing-plans skill (primary)
https://github.com/obra/superpowers/blob/main/skills/writing-plans/SKILL.md

Key patterns: files-named-up-front per task, checkboxed steps sized 2-5
minutes each, "no placeholders" rule (bans TBD/TODO/implement later), literal
code blocks in each step, self-review checklist after writing.

**Not adopted:** dual artifact (spec then plan), 200+ line plans with TDD
step-by-step content. `claude-config` plans are denser, written for reviewer +
implementer, not executor.

### Devin session-prompt convention (primary)
https://docs.devin.ai/essential-guidelines/instructing-devin-effectively
https://cognition.ai/blog/devin-2

Format: Goal · Context · Acceptance criteria · Scope and non-goals ·
Environment hints · Closing step. Emphasizes acceptance criteria as verifiable
conditions, not prose.

Key insight: AI-era plans assume an implementer that will execute literally —
ambiguity is the bug, not flexibility.

### Aider architect mode (primary)
https://aider.chat/2024/09/26/architect.html

Separates planning from editing: a plan must be precise enough that a second
model can mechanically apply it. Motivates keeping plan-file as design
artifact (plan-it's job) separate from execution (F-09 /build-it's job).

### Cursor agent best practices (primary)
https://cursor.com/blog/agent-best-practices

Consistent with Devin: pre-specify files to touch, bound scope with non-goals,
acceptance criteria as the termination condition.

## Convergent core (what appears in all formats)

1. **Problem framing** — "Context", "Background", "Problem". Universal.
2. **Goals + explicit non-goals** — bounds the diff. Near-universal.
3. **Proposed design/approach** — Universal.
4. **Alternatives/rationale** — appears in Google, Squarespace, HashiCorp,
   Sourcegraph. Collapsed into Approach rationale for single-PR scope rather
   than a separate section (per plan-it's Approach section description).

## Assumption ledger — worked example and grammar

Step 5's assumption ledger exists because a plan revision can silently
contradict a fact the same session already verified — attention is
captured by whatever finding is currently active. The catch tends to come
from fresh context (a human's outside-view question, or a `plan-review`
reviewer subagent), not from the authoring session re-checking itself. The
ledger gives that fresh-context check something concrete to diff against.

### Grammar

```
Root: <one-line problem/threat statement>
Givens: <condition treated as fixed> — beyond reach: <one-line reason>

Row 1 [mechanism]: <name> — anchors: root — <one-line justification>
Row 2 [assumption]: <claim> [verified: <source>] — anchors: row1
Row 3 [assumption]: <claim> [unverified] — anchors: row1
Row 4 [assumption]: <claim> [engineer-verified] — anchors: root
```

Every row's `anchors:` value is either `root` or an already-numbered row —
this is what makes ledger completeness a parse (every row traces back to
root) rather than another judgment call.

### Worked example

```
Root: a plan revision can silently contradict a fact the same session
already verified, because attention is captured by whatever finding is
currently active.
Givens: plan-review markers live under ~/.claude/plan-review-markers/ —
beyond reach: the marker directory is a fixed harness convention this
plan does not touch.

Row 1 [mechanism]: content-addressed plan-review marker — anchors: root —
forces re-review on any plan edit; an existence-only marker does not.
Row 2 [assumption]: require-code-review.sh already content-addresses its
marker via `git diff --cached | sha256sum`
[verified: claude/.claude/hooks/require-code-review.sh] — anchors: row1
Row 3 [assumption]: no other repo mechanism relies on
plan-review-markers/ existence-only semantics [unverified] — anchors: row1
Row 4 [assumption]: the structural-completeness hook and Stop-hook +
cross-check subagent are deferred until this hypothesis validates on live
plans [engineer-verified] — anchors: root
```

### Why three tags, not two

A binary verified/unverified split loses the case where the *human* stated
the fact directly, as an utterance in the session — that source can't be
re-derived by a grep or doc lookup, but it also must never be silently
overridden by the agent's own investigation finding something that looks
contradictory. Splitting out `[engineer-verified]` gives a reviewer (per
`plan-review`) a clear job: resolve `[unverified]` rows by checking them,
but *escalate* `[engineer-verified]` contradictions to the human rather
than resolving them unilaterally.

File-sourced facts are always `[verified: <file>]`; the tag exists only for
utterances a grep can't re-derive.

## Anti-patterns confirmed across sources

- Hour/day estimates — no single-PR template uses them; Squarespace's Timeline
  exists for multi-week features only.
- Exhaustive alternatives-considered listings — keep to 1-2 actually weighed;
  fold into Approach rationale.
- Placeholders (TBD, TODO, "implement later") — banned by superpowers; avoided
  by all serious formats.
- Separate test-strategy sections for single-PR scope — Verification/acceptance
  criteria suffices.
- Cross-cutting concern sections (security, i18n, observability) for one-PR
  scope — surface in Verification if applicable; don't reserve empty headings.
