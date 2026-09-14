# `code-writer` agent and in-agent self-review

*Formerly `docs/design-decisions.md` §11.*

Code-writing delegated to a subagent ran on the built-in `general-purpose`
agent. That agent commits review-finding-class defects — N+1 query shapes,
missing idempotency on retryable writes, unhandled error and empty states —
that the parent's `/code-review` pipeline then catches. Each catch costs a
parent → review → re-dispatch round-trip. `general-purpose` also has no `model:`
of its own and inherits the parent — the footgun the Model Routing section of
the global `CLAUDE.md` already carries a standing workaround for.

Framed as feedforward *guides* versus feedback *sensors*: the parent-side
`/code-review` is a sensor that fires only after the subagent has returned, so
every defect it finds is a round-trip. The `code-writer` agent moves a sensor
earlier — into the writing agent's own context. After writing the change,
`code-writer` re-reads its own diff and verifies it against the review angles
the `staff-*` reviewers enumerate, then fixes what it finds before returning.
Reviewing a finished diff is a sharper, more focused task than writing it; the
defect is caught inside the agent rather than surfacing as a parent turn.

**The self-review reads the `staff-*` agent files live; it does not copy their
checklists.** With no copy there is nothing to drift: when a reviewer agent
gains a review angle, `code-writer` inherits it on its next run. This extends
[§8](project-layer-composition.md)'s principle — reading the file directly, so its content enters the agent's
own reasoning pass — and avoids the duplication [§4](no-shared-skill-partials.md) warns against. `code-writer`
therefore depends on the `staff-*` and `ciso-reviewer` files being co-present in
`~/.claude/agents/`; both they and `code-writer` ship from this repo via stow,
so the dependency holds for every user by construction.

**No `maxTurns`.** `check-runner`'s cap ([§10](check-runner-charter-scoping.md)) fences a deterministic, narrow
charter against runaway iteration. Code implementation is open-ended; a low cap
truncates legitimate work. Scope creep is bounded instead by charter prose —
implement only the dispatch spec, touch no unrelated files, fix only defects in
the agent's own diff, run no state-mutating setup commands (the [§10](check-runner-charter-scoping.md) lesson).

**Routing is substitute-only and advisory.** The `CLAUDE.md` rule sends
*delegated* code-writing to `code-writer` instead of `general-purpose`; it does
not change how often the parent delegates versus writes inline — that is a
separate, broader decision left unmade. A routing rule cannot be hook-enforced:
there is no tool-call boundary for "the parent is about to write code"
(`Edit` / `Write` fire identically for code, config, and docs), so unlike the
review gates ([§1](hook-enforced-gates.md)) it stays advisory.

**Narrowed 2026-08-21.** For the subcase of implementing a plan that has
already cleared `/plan-review`, "left unmade" no longer holds:
`subagent-delegation`'s decision-made test now states delegation as the
default for that case (scope and approach are already fixed by the
plan), with `plan-it` Step 7 and `handoff` [§3](specialist-reviewer-roster.md) pointing to it at the two
points a session decides where implementation runs. The general case —
any code-writing the parent might do inline, plan or no plan — stays
advisory for the reason above: the routing rule still cannot be
hook-enforced, since a hard deny still has no way to tell approved-plan
implementation apart from any other legitimate inline edit (fixing a
diff, editing the plan file itself, docs, config). See
`.claude/plans/handoff-code-writer-delegation.md` for the transcript
measurement that grounded this narrowing (102 plan-review-boundary
sessions, 90d, this repo: only 33% of handoff [§3](specialist-reviewer-roster.md) sections named
`code-writer`, 35% of sessions were inline-only with zero delegation
attempt).

**Narrowed further 2026-09-01.** The fix that follows `code-review`,
`ready-for-review`, or `respond-pr` feedback is also delegated by
default now: `subagent-delegation`'s "Implementation work →
`code-writer`" section dispatches one `code-writer` per review round,
carrying the round's ADDRESS rows verbatim. This supersedes the prior
paragraph's "fixing a diff" example of a legitimate inline edit — a
reviewer-dispositioned fix is no longer one of the cases a hard deny
couldn't distinguish from plan implementation, since it is itself
delegated-by-construction. The general case — inline code-writing
outside an approved plan or a review disposition — stays advisory for
the same reason above: the routing rule still cannot be hook-enforced.
The 90-day inline-vs-dispatch measurement in
`.claude/plans/handoff-code-writer-delegation.md` predates both
narrowings for all but a handful of its days, so it cannot serve as a
remeasurement of either; a windowed remeasurement keyed to each
narrowing's own merge date is needed instead.

The name `code-writer` is job-shaped — an action-noun (like `code-review`)
describing the work the agent does — not a persona job title.
Anthropic's subagent documentation treats the agent `name` as a pure
identifier; behavior comes from the system prompt.
