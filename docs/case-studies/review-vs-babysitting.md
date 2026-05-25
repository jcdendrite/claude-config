# Review gates and the agent-babysitting problem

*Part of the [claude-config case studies](../case-studies.md).*

**The problem.** In conversations with engineers about working alongside coding agents, one complaint recurs more than any other: *babysitting*. Not "the agent can't code" — the agent codes fine — but that it cannot be left alone. It writes code that wasn't asked for, edits the wrong file, ships a plausible-looking change with a defect the author would have caught, and the only defense the engineer trusts is watching every step. The hypothesis behind this repo's review pipeline is that the babysitting cost is largely a *missing-gates* cost: the agent is unsupervised at the moments that matter, and the fix is to put structure — review skills, enforcement hooks, specialist reviewers — at those moments rather than a human's attention.

**Question.** Does a workflow built from review gates and reviewer agents reduce the babysitting cost, or just relocate it?

**Short answer.** It relocates it — and the relocation is the point. The transcripts show review steps catching defects the agent had already declared finished: a missing API field, a wrong dispatch target, two exploitable bypass paths in a security gate, a structural bug in the agent's own review rule. The enforcement hooks fire constantly — 946 denials across 317 of 785 sessions — and repeatedly convert an agent's "this doesn't need review" into a review that finds something. Specialist reviewers catch real defects, and they also produce confidently wrong advice the agent must be equipped to reject. None of this is zero-supervision. What changes is *what* the human supervises: not keystrokes in real time, but findings, after the gate has already stopped the bad outcome.

## How this was measured

The evidence is this repo's own Claude Code transcript corpus: 785 sessions and 44,645 assistant turns on `claude-config` work, 2026-04-21 to 2026-05-22, analyzed with the `review-trace` subcommand of `transcript-analysis.py` (committed in this repo). Four honest limits on what the corpus can show:

- **The counts are a point-in-time snapshot.** A local Claude Code transcript store is mutable — sessions accrue continuously, and a worktree's transcript directory is removed when that worktree is cleaned up after a merge. The figures here were measured during this case study's authoring; the `review-trace` *method* reproduces, but a later run over the same machine reports different totals.
- **No pre-gate baseline.** The review pipeline was already in place before the corpus begins — the plan-review skill, the code-review gate, and the plan-review gate hook all merged at or before the corpus start. So this is not a before/after experiment. It measures the *steady state* of a mature gated workflow, not the transition into one.
- **Denial counts are best-effort.** Claude Code changed its transcript format mid-window; current-format hook denials carry no structured marker and are matched by message-text signature. Counts are accurate to within signature precision, not exact.
- **No model-versus-model claim.** The corpus spans both Opus and Sonnet sessions. Examples below are drawn from both; no Opus-vs-Sonnet comparison is asserted — the corpus has too few branches per model to support one.

Quotes are verbatim from transcripts and were scanned for credential material before inclusion.

## The numbers

Of 785 sessions, 332 contain at least one review event. Within them:

- **837 review-skill invocations** (`/code-review`, `/plan-review`, `/skill-review`, `/ready-for-review`, `/agent-review`, `/plan-it`) across 297 sessions.
- **142 specialist-reviewer spawns** (`staff-*`, `ciso-reviewer`) across 62 sessions.
- **946 hook denials** across 317 sessions — 40% of all sessions hit a gate at least once. By gate, attributed best-effort from each denial's message text: worktree-enforcement 387, marker-shape 213, ready-for-review 88, plan-review 69, code-review 62, redaction 39, respond-pr 35, memory-write 30, skill-review 14 — these nine sum to 937; the remaining 9 are denials whose message did not map cleanly to a single gate.

The denial total is the headline: 946 times, an agent attempted something a gate blocked. That is the load-bearing measurement — the gates are not decorative, they are interposed on real attempts. A second repository's private transcript corpus — 590 sessions over a comparable window — shows the same shape in aggregate (989 denials across 244 sessions, 712 review-skill invocations, 330 specialist spawns); it is not quoted here, only noted as corroboration at similar scale.

What the numbers cannot show is whether any given review *caught something material*. That is a question only a transcript read answers, and the three sections below work through it from primary sources.

## Review catching what the agent had already missed

The recurring shape: the agent finishes, tests pass, it declares the work done — and the review finds the defect anyway.

In one session the agent built a hook to re-inject context after compaction, ran it, and saw it work. `/code-review` spawned a `staff-platform-engineer` reviewer, which found the hook's JSON output omitted a required field. The agent verified the claim against primary documentation and confirmed it (session `6544551e`, record 423):

> Crucial finding from the superpowers code. Two things confirmed: 1. **`hookEventName: "SessionStart"` IS required** — superpowers line 51 explicitly emits it for Claude Code (`CLAUDE_PLUGIN_ROOT` detection). My implementation is missing it.

Without that field, the hook's output is silently discarded on every `/compact` and `/clear` — the feature would have shipped inert. Passing tests did not catch it because the tests exercised the script, not Claude Code's ingestion contract.

In another, the agent trimmed two `SKILL.md` files under a length budget, passed tests, and declared the trim complete. `/code-review`'s specialist panel found that words dropped as apparent filler were load-bearing (session `a061ff88`, record 175):

> All 8 specialists back. Two actionable concerns to fix, both in plan-review's Reviewer-roles Focus cells where I overcompressed: 1. **`staff-platform-engineer`**: dropped "runbook linkage" and "load characteristics" — those were the only routing surface for those concerns; no checklist item recovers them.

The dropped tokens read as narrative prose but were the only signal routing a concern to its reviewer — deleting them silently disabled a reviewer lane.

The pattern repeats across smaller catches: a `/code-review` pass found a plugin skill dispatching `general-purpose` where repo convention prescribes the `check-runner` agent (session `8a226a48`, record 184); a `/skill-review` and a follow-on `/code-review` together caught a private project's name — and a filesystem path embedding it — staged for commit into this *public* repository (session `0ce152ae`). In each case the agent had written the change and moved on; the review, not the agent, was the thing that noticed.

## The gate forcing a review the agent meant to skip

The case studies above are reviews the agent chose to run. The more pointed evidence is the reviews it chose *not* to run.

One session is close to a controlled demonstration. The agent wrote a plan to decompose a 4,458-line test file, judged a specialist reviewer unnecessary, and tried to save the plan. The `plan-review` gate hook denied the write. Forced to run `/plan-review`, the agent recorded — in its own words — that it had "rationalized skipping a named primary owner." The forced `staff-sdet` review then returned eight findings, four substantive (session `c12ee6b1`, record 116):

> SDET came back with 8 findings — 4 are substantive things I missed: **F2:** `TestDenyPrivateProjectRefs` has an autouse `_isolate_home_for_blocklist` fixture … Real correctness risk if the autouse drops or coexists wrong. **F3:** My grep missed multiple module-level fixtures … Plan needs an explicit per-fixture ownership rule.

The agent's own judgment was that the review was unnecessary. The gate disagreed, and the gate was right. A similar sequence: a push to a branch with an open PR was denied by the `ready-for-review` gate; the review it forced spawned a `staff-backend-engineer` that found an unquoted right-hand side in a bash `[[ ]]` test — capable of glob-expanding and targeting the wrong branch for `git branch -D` — and rated it MUST-FIX (session `de453486`, record 157). The denial did not catch the bug; it caused the review that did.

This is the mechanism that most directly displaces babysitting. The human's job in a prose-only workflow is to notice when the agent is about to skip a step it shouldn't. The gate does that noticing.

## Specialist judgment, in both directions

Specialist reviewers are not an unalloyed good, and the honest version of this case study has to say so. They are themselves agents; they are sometimes wrong; and a workflow that adopts every reviewer finding is its own failure mode.

The clearest instance: `/code-review` spawned a `ciso-reviewer` and a `staff-sdet` in parallel on a marker-expiry hook. Both flagged the same guard clause — with *opposite* advice. The agent did its own analysis rather than splitting the difference (session `1b1eebec`, record 381):

> Both agents flagged the `-ge 0` guard independently, but with conflicting recommendations. CISO says "remove it — it's dead logic." SDET says "add a test for it." … Without `-ge 0`, the expression `-600 -lt 5400` is **true** — bypass would be granted. So the guard is **load-bearing**, not dead. CISO's analysis miscalculated the sign. Removing it would be a bug.

The security reviewer's advice, taken literally, would have introduced the security regression it existed to prevent. The agent kept the guard, added the test the SDET asked for, and recorded why it rejected the CISO finding. Judgment cut the other way too: in a CI-change review, a `staff-platform-engineer` raised a mutable-action-tag supply-chain risk *and* queried a widened path regex; the agent adopted the supply-chain finding and explicitly declined the regex query as intentional — "that's the whole point of the PR" (session `f8781720`, record 233).

And the reviewers do catch what no single pass would. A one-line addition to a review-routing table triggered a recursive seven-specialist panel, which found that the new rule — "spawn every persona listed in the table being edited" — let a reviewer be silently exempted by *deleting* their table row, because a deletion removes the persona from the list the rule reads (session `9850cf69`, record 95). That is a structural bug in a review rule, found by reviews.

The disposition discipline matters here as much as the spawning. A reviewer finding is an input to a decision, not the decision. The repo later made this explicit — `/code-review` requires an explicit keep/fix/reject disposition for every reviewer finding — precisely so that "a specialist said so" cannot substitute for the judgment the transcripts above show the agent exercising.

## What this says about babysitting

The babysitting hypothesis was that the cost comes from the agent being unsupervised at the moments that matter. The corpus supports a sharper version: the agent is not unskilled at those moments, it is *overconfident* — it finishes, tests pass, and it declares done while a real defect remains. Review gates do not make the agent more careful. They interpose a second look at exactly the point where the agent's own confidence is highest and least reliable, and 946 denials across the corpus show how often that point is reached.

This does not eliminate supervision. A human still reads the findings, still arbitrates when a specialist is wrong, still owns the disposition. But the supervision moves off the critical path: it happens against a diff a gate has already frozen, not against a stream of keystrokes that has to be watched live. That is the relocation in the short answer — and for the engineers describing the babysitting cost, reading a review finding after the fact is a categorically cheaper posture than watching to prevent one.

## Attribution notes

The quoted text is the Claude agent's, drawn from sessions the repo owner was supervising — first-person narration ("things I missed," "my implementation is missing it") refers to the agent in that session, not to the repo owner. The repo owner's role is directing the work and approving what merges.

Transcript sessions are local Claude Code logs, not public artifacts; they are cited by session-id stem and record number so the owner can re-locate each quote, and the `review-trace` tool makes the aggregate-count *method* reproducible (the counts themselves are a point-in-time snapshot — see *How this was measured*). The `transcript-analysis.py` tool, the hooks, and the skills referenced are committed code — primary sources without attribution ambiguity.

## Sources

- **`claude/.claude/scripts/transcript-analysis.py`** — the `review-trace` subcommand: per-session timeline of review-skill invocations, hook denials, and specialist-reviewer spawns. All aggregate counts in this case study come from `review-trace` over the transcript corpus.
- **Transcript corpus** — 785 `claude-config` sessions, 2026-04-21 to 2026-05-22, in the repo owner's local Claude Code history. Quoted sessions, by id stem and record number: `6544551e`:423, `a061ff88`:175, `8a226a48`:184, `0ce152ae` (redaction catch), `c12ee6b1`:116, `de453486`:157, `1b1eebec`:381, `f8781720`:233, `9850cf69`:95.
- **`claude/.claude/skills/code-review/SKILL.md`** — the `/code-review` skill, including the specialist-reviewer routing and the explicit per-finding disposition requirement.
- **`claude/.claude/skills/plan-review/SKILL.md`** — the `/plan-review` skill.
- **`claude/.claude/hooks/require-plan-review.sh`, `require-code-review.sh`, `require-ready-for-review.sh`** — the enforcement-gate hooks whose denials are counted above.
- **[Worktree enforcement: hook vs. CLAUDE.md prose](worktree-enforcement.md)** — companion case study; its 387-denial worktree-enforcement measurement is one row of the denial breakdown here.
