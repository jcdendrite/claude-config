# Mandate `code-writer` delegation for approved-plan implementation

## Context

**Goal:** make sessions reliably delegate implementation of an already-approved
(`/plan-review`-cleared) plan to the `code-writer` subagent, instead of the
main thread writing that code inline.

**Why now.** The engineer observed that after a plan is approved — whether
continuing in the same session or handing off to a resumed one — the main
thread frequently does the implementation itself rather than dispatching
`code-writer`, and asked where an "always delegate" instruction should live
(flagging that `CLAUDE.md` already feels bloated). A transcript measurement
this session confirms the observation and sizes it: across 102 sessions that
hit a `plan-review` boundary in this repo over the last ~90 days
(`transcript-analysis.py`, `--this-repo`), 55% made at least one inline
repo-code edit after the boundary, only 23% dispatched `code-writer` at all,
and only 3% show clean full delegation. Of the 61 sessions that wrote a
handoff file, only 33% (20/61) of the file's §3 "Next concrete step" actually
named `code-writer` — the rest just described the work — and that split is
stable across the whole sample window, not an early-adoption artifact (the
`CLAUDE.md` rule substituting `code-writer` for `general-purpose` predates the
entire window).

**Intended outcome:** implementation of an approved plan is delegated to
`code-writer` by default, with the mandate stated once (in `subagent-delegation`,
where the routing rule already lives) and surfaced at the two points a session
actually decides where implementation runs — `handoff` §3 and `plan-it` Step 7
— rather than left as a general judgment call a session can silently skip.

## Approach

State the mandate once in `subagent-delegation` (the file that already owns
the delegate-vs-inline decision), and have `handoff` §3 and `plan-it` Step 7 —
the two places a session actually decides where implementation runs — point to
it with a concrete instruction rather than restate it. No hook is added; the
mechanism stays prose, for reasons in the ledger below.

### Assumption ledger

**Root problem:** implementation of an already-approved plan frequently
happens as inline main-thread `Edit`/`Write` instead of a `code-writer`
dispatch, because no site actually mandates delegating at all for this case —
`CLAUDE.md`'s existing rule only substitutes which agent to use once
delegation has already been chosen (`docs/design-decisions.md` §11: "it does
not change how often the parent delegates versus writes inline").

**Given:** a `PreToolUse`/`PostToolUse` hook cannot distinguish "this
`Edit`/`Write` implements an approved plan" from any other legitimate
main-thread edit (fixing a returned diff, editing the plan file itself, docs,
config, a PR body draft) — the tool call carries a path and content, not a
semantic label for what kind of edit it is. Out of reach: this is a property
of the `Edit`/`Write` tool schema itself, not something this repo's hooks
control, and no change here alters it.

| # | Assumption | Tag |
|---|---|---|
| 1 | Hook payloads expose actor identity (main session vs. subagent) via presence/absence of `.agent_type` | `[verified: claude/.claude/hooks/enforce-marker-script-shape.sh:32-34 — "Both arms key on `.agent_type`, which the harness populates only for subagents"]` — noted because it does *not* rescue hard-deny gating here: the missing signal is edit-*kind* (the root-problem given), not actor identity |
| 2 | 55% of 102 plan-review-boundary sessions (`--this-repo`, ~90d) made at least one inline repo-code edit post-boundary; 23% dispatched `code-writer`; 3% show clean full delegation | `[verified: transcript-analysis.py this session, purpose-built classifier over `review-trace --skill plan-review` output — see Verification]` |
| 3 | Of 61 sessions that wrote a handoff file post-boundary, only 33% (20/61) of §3 "Next concrete step" sections named `code-writer`; the split is stable across the sample window (the substitution rule predates the whole window, ruling out an adoption-lag explanation) | `[verified: transcript-analysis this session, JSONL extraction of each session's own §3 Write-tool-call content]` |
| 4 | The dominant driver of inline post-plan implementation is never attempting delegation (35% of the 102 sessions, zero `code-writer` dispatch), not `code-writer` producing unsatisfactory work that then gets redone inline (a minority pattern within the 20 sessions that did both) | `[verified: transcript-analysis this session — file-overlap check plus qualitative read of the 20 mixed sessions]` |
| 5 | `resume-context` delivers a handoff file's content via `--append-system-prompt-file`, so it structurally reaches a resumed session every turn rather than being skippable — but only ~17% (49/286) of this-repo sessions in the window actually resume that way; most post-boundary work continues in the *same* session, which `handoff` §3 never reaches and only `plan-it` Step 7 governs | `[verified: transcript-analysis this session — resume-context launcher-string search across 286 this-repo session files]` |
| 6 | `subagent-delegation`'s existing "decision-made test" already logically covers approved-plan implementation — condition (1) ("the change is already decided before you read") holds by construction once a plan clears `/plan-review` — but is phrased as a general judgment rule inside a skill body, not surfaced at either point a session decides where implementation runs | `[verified: subagent-delegation/SKILL.md read this session, cross-referenced against row 3's gap]` |
| 7 | `plan-it` Step 7's current final line ("a separate axis, not a tiebreaker") was added by a prior, already-merged plan and was deliberately scoped to the continue-vs-handoff decision, not a delegation mandate | `[verified: .claude/plans/handoff-boundary-decision-rule.md; plan-it/SKILL.md:92]` |
| 8 | A prior, already-merged plan established that a judgment-based rule buried in a general skill body has low real-world reliability (0%→39% for an analogous "read this before writing" case) and that converting it to a prominent, unconditional precondition at the exact decision point raised reliability — the same shape of fix this plan applies | `[verified: .claude/plans/code-writer-precondition-reads.md]` |
| 9 | `handoff/SKILL.md` (172/200 lines) and `subagent-delegation/SKILL.md` (174/200 lines) have headroom under `check-skill-length.sh`'s 200-line default for additions of ~3-6 lines each | `[verified: wc -l this session against check-skill-length.sh's limit_for() default]` |
| 10 | Splitting implementation across multiple `code-writer` dispatches — within or across phases — is currently left to the implementing session's discretion rather than decided when the plan is written, and senior engineers give explicit guidance on how to make that split | `[engineer-verified]` |
| 11 | `isolation: "worktree"` is prohibited for PR-bound implementation work — `CLAUDE.md`'s Agent Briefing routes that case to a session-created feature-branch worktree with dispatches run *without* `isolation: worktree`, reserving the ephemeral-isolation primitive for work that will not become a named PR branch | `[verified: claude/.claude/CLAUDE.md:80]` |

**M1 — `handoff/SKILL.md` §3: instruct writing the dispatch, not the work.**
`anchors: row3, row5`. §3 is what a resumed session reads verbatim as its
opening directive (row 5), yet only a third of real §3 sections name
`code-writer` (row 3) — this is the single highest-leverage site. *Lighter
primitives rejected:* a cross-reference to `subagent-delegation` alone —
rejected, because row 8's precedent is exactly that a cross-referenced
judgment rule doesn't reliably get applied at the moment of writing; the
instruction needs to be concrete at the point of writing §3, not delegated to
memory of another skill.

**M2 — `handoff/SKILL.md` pre-write checklist: enforce M1.** `anchors: row3`.
The pre-write checklist is the mechanism this repo's own precedent shows the
authoring session actually runs (it already has 15 items, including one for
the adjacent §3/§3.5 categorization rule) — cheapest, highest-yield addition
alongside M1.

**M3 — `plan-it/SKILL.md` Step 7: extend the final line into a directive.**
`anchors: root, row5`. Row 5 shows most post-boundary work continues in the
*same* session (83% don't use `resume-context`), a path `handoff` §3 never
reaches — only Step 7 governs it. Fixing only `handoff` §3 would leave the
majority of the measured 35% inline-only sessions unaddressed. *Lighter
primitives rejected:* leaving Step 7's existing "separate axis" line
unchanged — rejected as the status quo being fixed; per M4's DRY placement,
extending stays a one-sentence pointer, not a restatement.

**M4 — `subagent-delegation/SKILL.md`: state the actual rule and rationale.**
`anchors: root, row6`. Single source of truth: the rule belongs where the
delegate-vs-inline decision already lives, so M1 and M3 can point to it
instead of each restating it (three independent copies would drift the moment
one is edited). States delegation as the default once a plan clears review
(row 6), the unit of delegation as the phase (a whole-plan dispatch produces
an unreviewable mega-diff and exceeds `code-writer`'s bounded-spec charter per
`docs/design-decisions.md` §11's "No `maxTurns`" note), and carries forward
the decision-made test's first carve-out (a step the plan deliberately left
open) unchanged; the second carve-out is narrowed to point at this file's own
Debug-investigation probe section rather than blanket-exempting the whole
debug/verify loop — no new exception is added.
*Lighter primitives rejected against the heavier alternatives considered:*
(a) a hard `PreToolUse` deny hook — rejected per the root-problem given, no
tool-call-visible signal distinguishes plan-implementation from any other
legitimate inline edit; (b) a soft, non-blocking nudge hook (this repo has
several: `nudge-worktree-anchor.sh`, `nudge-handoff-near-context-cap.sh`) —
same given applies to false-positive risk (it would fire on legitimate
parent-inline edits made while a plan-review marker is fresh: applying a
review finding, fixing the plan file, drafting the PR body), and unlike row 8's
39%-reliability case this problem has no prior hook attempt to fall back from
— hold as a future escalation only if a remeasurement (same method as this
plan's grounding) shows the prose fix insufficient; (c) a new global
`CLAUDE.md` rule — rejected, loads in every session regardless of whether a
plan boundary is ever hit, the same reasoning `plan-it` Step 7's own M1
rationale already used to reject this site for an adjacent decision.

**M5 — `docs/design-decisions.md` §11: narrow the "left unmade" claim.**
`anchors: row7`. §11 currently asserts the delegation-rate question is "a
separate, broader decision left unmade" — after M4 that's stale for the
approved-plan subcase specifically. This repo's own design-decisions.md is
cited as a live source (both by me this session and by an Opus consult run
for this plan), so leaving a superseded claim there misleads the next reader
who cites it. Follows the file's existing "Retired 2026-06-23" annotation
pattern rather than silently rewriting the paragraph.

**M6 — `CLAUDE.md`: one-clause cross-reference, no new line.** `anchors:
row6`. The existing sentence ("it does not change when the parent delegates
versus writes inline") stays literally true — it describes what *that specific
rule* does — but reads as though nothing governs the delegate-or-not call.
Single source of truth: append a same-line pointer to `subagent-delegation`
rather than restating the rule, keeping the addition at zero net lines (the
engineer's own bloat concern).

**M7 — `reviewer_yield.py` comment: correct a false citation.** `anchors:
row6`. The comment at lines 560-561 already asserts "which this repo's own
CLAUDE.md mandates for implementation work" — that's inaccurate today (§11)
and stays inaccurate after M4-M6 (the mandate lands in `subagent-delegation`,
not `CLAUDE.md`, by design). Axis 1 bucket 2 (small, non-cosmetic, visible
value, keeps the PR coherent): fix the citation while touching this exact
mechanism, rather than leave a comment asserting a false authority.

**Not "ALWAYS," and no new exception either.** An unconditional rule gets
discounted exactly where it's wrong, which teaches a session to discount it
generally — so the mandate carries forward the decision-made test's two
carve-outs — a step the plan deliberately left open, and the parent's review
of what a dispatch returns (verification, diff reading, applying an
already-decided correction; root-causing an unexplained failure routes out
via the Debug-investigation probe) — instead of adding a size-based "trivial
fix" exception. A genuinely trivial, fully-decided step already stays inline
under the test's own condition (2) — a context-cost test, not a feels-small
test — so a new exception would only add a second escape hatch for the same
case.

**Unit of delegation is the phase, not the whole plan.** Dispatching an
entire multi-phase plan in one `code-writer` call produces an unreviewable
diff and exceeds the bounded-spec charter `docs/design-decisions.md` §11
documents ("implement exactly what the dispatch prompt specifies"). Each
dispatch names the plan path, the phase's steps, and its verification
command. See M8 for how a phase itself divides into more than one dispatch.

**M8 — `plan-it/SKILL.md` Step 5: name the dispatch split at plan-authoring
time.** `anchors: root, row10, row11`. Row 10 is the engineer's own
observation that splitting implementation across multiple agents is
currently left to the implementing session's discretion rather than decided
when the plan is written; row 11 grounds why parallel dispatches for this
work share one worktree rather than getting per-agent isolation. Adds one
paragraph to Step 5 (Architecture design) instructing the plan author to
name, for each phase whose steps partition into non-overlapping file sets,
whether it splits into more than one `code-writer` dispatch, whether those
dispatches run sequentially or in parallel, and why — closing the gap
between `plan-review`'s existing "Phase independence" check (B9) and
`plan-it`'s own prescribed plan sections, which today never ask the author
to make that call. *Lighter primitives rejected:* stating this in
`subagent-delegation` instead — rejected per this plan's own DRY split
(`subagent-delegation` owns the execution-time delegate-vs-inline question;
how to decompose a body of work you are specifying is a plan-authoring act,
and duplicating it into a fourth site would restate a rule this plan
otherwise consolidates).

## Critical files

- `claude/.claude/skills/subagent-delegation/SKILL.md` — add the canonical
  rule to the existing "### Implementation work → `code-writer`" section,
  directly after the "Read-then-edit: decision-made test" paragraph:

  > **Implementation of an approved plan is delegated by default.** A plan
  > that cleared `/plan-review` already fixed scope and approach, so condition
  > (1) of the decision-made test above holds by construction — dispatch
  > `code-writer` per phase, naming the plan path, the phase's steps, and its
  > verification command. Two things stay with the parent: a step the plan
  > deliberately left open for implementation-time discovery, and the review
  > of what a dispatch returns — running the phase's verification command
  > inline, reading the returned diff line by line, and applying a
  > correction whose content is already decided. Root-causing a failure the
  > returned diff does not explain is not parent work: dispatch it as a
  > **Debug-investigation probe** (above) and apply the fix the returned
  > diagnosis specifies.

- `claude/.claude/skills/handoff/SKILL.md` —
  - §3, appended as an additional sentence on the same paragraph line
    (matching this file's one-paragraph-per-line convention — no new line),
    after "Move irreversible or shared-state actions to §3.5.": "When the
    next step implements an approved plan, write it as the dispatch, not
    the work: name `code-writer`, the plan path, the phase, and its
    verification command, per `subagent-delegation`'s default. 'Implement
    Phase 2' reads to the resuming session as work to do inline."
  - Pre-write checklist, new bullet immediately after the existing §3/§3.5
    categorization-rule bullet: "If §3's next step implements an approved
    plan, it names `code-writer` as the dispatch rather than describing the
    work to do inline."
  - **Reuse:** the existing checklist mechanism and bullet style — no new
    structure.

- `claude/.claude/skills/plan-it/SKILL.md` — Step 5 (Architecture design),
  new paragraph inserted after the "External-pattern grounding" paragraph
  and before "Assumption ledger":

  > **Name the dispatch split.** Implementation of an approved plan is
  > delegated to `code-writer` per phase by default (`subagent-delegation`);
  > the plan decides how a phase divides further, not the session that
  > implements it. Split a phase into more than one dispatch only when its
  > steps partition into non-overlapping file sets that are each specifiable
  > without restating the other's context — then name each dispatch's files
  > and verification command in **Critical files**. Sequence them whenever
  > one dispatch's output is the next one's input (a signature and its
  > callers, a schema and its consumers); parallelize only for genuinely
  > disjoint file sets, and note that parallel dispatches share the parent's
  > feature worktree — `CLAUDE.md`'s Agent Briefing bars `isolation:
  > worktree` for PR-bound work, and overlapping edits in one tree clobber
  > silently rather than conflict. Do not split when the same shared-state
  > background would have to be restated in every dispatch prompt: each
  > agent re-reads the same files in its own context and can resolve the
  > same open question differently, and no agent's self-review sees the
  > other's.

  Step 7, extend the final line
  (currently line 92) by appending sentences to the same paragraph line —
  no new line — from:
  > Delegating implementation to `code-writer` is a separate axis, not a
  > tiebreaker: a subagent starts from a fresh context either way, so it
  > neither argues for handing off nor for staying.

  to also state the mandate and point to M4 rather than restate it:
  > Delegating implementation to `code-writer` is a separate axis, not a
  > tiebreaker: a subagent starts from a fresh context either way, so it
  > neither argues for handing off nor for staying. Whichever session
  > implements, dispatch `code-writer` per phase by default — the plan
  > already fixed scope and approach, so `subagent-delegation`'s
  > decision-made test is satisfied by construction. See
  > `subagent-delegation`'s "Implementation work → `code-writer`" section for
  > the two carve-outs.

- `docs/design-decisions.md` — §11, new paragraph appended directly after
  the "Routing is substitute-only and advisory" paragraph (which currently
  ends at line 138, right before "## 12."), following the file's existing
  "Retired 2026-06-23" annotation style:

  > **Narrowed 2026-08-21.** For the subcase of implementing a plan that has
  > already cleared `/plan-review`, "left unmade" no longer holds:
  > `subagent-delegation`'s decision-made test now states delegation as the
  > default for that case (scope and approach are already fixed by the
  > plan), with `plan-it` Step 7 and `handoff` §3 pointing to it at the two
  > points a session decides where implementation runs. The general case —
  > any code-writing the parent might do inline, plan or no plan — stays
  > advisory for the reason above: the routing rule still cannot be
  > hook-enforced, since a hard deny still has no way to tell approved-plan
  > implementation apart from any other legitimate inline edit (fixing a
  > diff, editing the plan file itself, docs, config). See
  > `.claude/plans/handoff-code-writer-delegation.md` for the transcript
  > measurement that grounded this narrowing (102 plan-review-boundary
  > sessions, 90d, this repo: only 33% of handoff §3 sections named
  > `code-writer`, 35% of sessions were inline-only with zero delegation
  > attempt).

- `claude/.claude/CLAUDE.md` — line 87, append to the existing sentence:
  "...it does not change when the parent delegates versus writes inline —
  see `subagent-delegation` for that call." Zero net new lines.

- `claude/.claude/scripts/transcript_analysis/reviewer_yield.py` — lines
  560-561, replace:
  ```
      happened inside a code-writer dispatch, which this repo's own CLAUDE.md
      mandates for implementation work. The unclassified
  ```
  with:
  ```
      happened inside a code-writer dispatch, which `subagent-delegation`
      mandates by default for approved-plan implementation. The unclassified
  ```
  Comment-only; no behavior change.

## Verification

- `/skill-review` on `handoff/SKILL.md`, `plan-it/SKILL.md`, and
  `subagent-delegation/SKILL.md` — hook-enforced on commit by
  `require-skill-review.sh` per `.claude/rules/review-pipeline-dispatch.md`.
- `../../../.venv/bin/pytest claude/.claude/` and
  `../../../.venv/bin/ruff check claude/.claude/` — must stay green. Relevant
  because `test_consume_durable_continuity_file_on_read.py` and
  `test_restore_authorization_boundary_on_compact.py` read `handoff/SKILL.md`
  as a fixture (the `mkdir` recipe and the §3.5 categorization list
  respectively); confirm the M1/M2 insertions land inside §3 and after §3.5
  without disturbing either anchor.
- `git diff --stat` names exactly the seven files listed above — any other
  file is scope creep.
- Confirm the `subagent-delegation` addition (M4) is the only place stating
  the rule itself — `handoff` §3 (M1) and `plan-it` Step 7 (M3) each carry a
  pointer plus the concrete dispatch instruction, not a restatement of the
  rationale.
- **Deferred, post-merge:** re-run this session's transcript-grounding method
  (plan-review-boundary session classification, §3-content extraction) after
  the changes accrue usage, to confirm the §3-naming rate rises above the
  measured 33% and the inline-only rate falls below the measured 35% —
  mirroring `code-writer-precondition-reads.md`'s own deferred-remeasurement
  step. If it doesn't move, that's the evidence needed to revisit M4's
  rejected nudge-hook alternative.

## Out of scope

- **No hook** (hard-deny or soft-nudge) — rejected per the root-problem given
  and M4's alternatives analysis; held as a future escalation only if the
  deferred remeasurement shows the prose fix insufficient.
- **No new size-based exception** ("trivial one-line fix") — deliberately
  rejected; the mandate reuses the decision-made test's two existing
  carve-outs only.
- **`code-writer`'s own dispatch-quality issues** — the minority
  redo-after-dispatch pattern found in row 4 (a handful of the 20 mixed
  sessions) is a separate problem from the delegation-rate gap this plan
  targets; not addressed here.
- **Which specific resume path corresponds to which handoff file**
  (`resume-context` vs. `--continue` vs. a fresh prompt referencing the plan)
  — flagged as an open measurement gap during this plan's grounding; not
  resolved here.
- **`plan-it` Steps 1-4 and 6, and any other skill body** — untouched; only
  Step 5 (new dispatch-split paragraph, M8) and Step 7 (final line extended,
  M3) are touched.
