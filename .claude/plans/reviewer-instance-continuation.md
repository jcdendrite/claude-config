# Reviewer-instance continuation on same-branch, same-session re-dispatch

## Context

This plan designs a fix for `/code-review`'s reviewer-agent redundancy: of
573 reviewer-spawn events observed in this repo's own last ~60 days of
transcripts (actual timestamps 2026-08-30 to 2026-09-06, via
`transcript-analysis.py review-trace --this-repo --since 2026-07-07`, rerun
2026-09-05), 378 (66.0%) were a repeat of an agent type already dispatched on
that branch, and 108 of 195 distinct (agent-type, branch) pairs (55.4%) saw
2+ dispatches. Of those 378 repeats, 247 (65.3%) happened within the same
session as the prior dispatch of that pair — the slice a same-session
`SendMessage`-continuation fix could actually address — while 131 (34.7%)
crossed a session boundary, where prior research already in this repo
(`.claude/plans/handoff-hard-block.md:121,151,154`) established that no
session can recover a subagent it did not itself spawn once the spawning
process ends. The goal: when `/code-review`'s Ripple-effect-triage
re-dispatches a reviewer type already spawned on the current branch within
the current live session, continue that prior agent instance via
`SendMessage` — passing only the delta since its last pass — instead of
issuing a fresh stateless `Agent` dispatch that re-reads every changed file
from scratch. Cross-reviewer-type decorrelation in one round stays untouched
(`docs/design-decisions.md` §3: "Each reviewer reads the diff fresh and sees
no other reviewer's findings — reasoning contamination... is genuinely
broken") — this plan only touches same-type, same-branch, same-session
re-dispatch. Why now: this redundancy was identified as the root cause of a
third `read-discipline` trigger fire (PR #898,
`docs/case-studies/targeted-read-discipline.md`), and the engineer asked for
this follow-up design as the next concrete step.

## Approach

**Ship the savings measurement as a gate, and design the continuation mechanism behind it — because the analysis below shows the mechanism's sign, not just its magnitude, depends on one number nobody has measured.** A continued instance does not avoid *paying for* the prior read; it avoids *re-issuing* it. The prior read's tokens stay resident in that agent's conversation and are re-sent on every subsequent turn, cheaply (0.1x) while the cache entry lives and expensively (1.25x rebuild) once it lapses. Because a continued instance's carried prefix is strictly larger than what a fresh dispatch re-reads — it contains the prior read set *plus* the prior round's own output — continuation is cost-**negative** whenever the inter-round gap exceeds the cache TTL. The gate is therefore the distribution of that gap, and it is derivable today from `review-trace`'s per-event ISO timestamps with no new instrumentation.

### Deliverable 1 — the savings estimate

**The causal premise holds, with one correction.** The dispatch prompt's framing ("each fresh `Agent` dispatch starts an isolated conversation with no cache shared across separate dispatches") is too strong as stated, but true for the part that carries the money. Wire-level capture recorded in `docs/case-studies/cold-cache-attribution.md` § "Testing the leading hypothesis: wire-level capture" establishes the request structure by SHA-256 comparison across turns: the order is `tools → system[0..3] → messages[]`, and `system[2]` carries `cache_control: {scope: "global"}` — shared cross-session, with a brand-new session's first turn reading tools-plus-base-system as a cache *hit*, at a measured fixed survivor floor of 22,050 tokens. So a shared cross-dispatch cache does exist, and two fresh dispatches of one agent type additionally share a byte prefix through the agent body and both `CLAUDE.md` files, which can hit within TTL. But every `Read` tool result lands in `messages[]` *after* the divergent user prompt. **No file read is ever shared across two separate dispatches.** That is the whole savings claim, and it survives.

**The arithmetic.** Rates are derived, not assumed: `claude/.claude/scripts/transcript_analysis/pricing.py:53,60` prices `claude-sonnet-4-5` at $3.00/MTok base and `claude-sonnet-5` at $2.00; lines 24–27 give output 5x, cache-write-5m 1.25x, cache-write-1h 2x, cache-read 0.1x. At $3.00 base that is $3.75/MTok to write and $0.30/MTok to read.

Let `R` be one reviewer dispatch's file-read tokens, `P` its full round-1 conversation below the global breakpoint (agent body + both `CLAUDE.md` + prompt + `R` + its own output), `t` the assistant turns in the second pass.

| Arm | Cost shape |
|---|---|
| **A — fresh re-dispatch (today)** | `1.25·(S+U+R) + 0.1·(S+U+R)·(t−1)` |
| **B — continuation, cache warm** | `0.1·P·t′ + 1.25·(D+R′) + …` |
| **B′ — continuation, cache cold** | `1.25·(P − 22,050) + …` |

Arm B′ never beats Arm A: it pays the same 1.25x write multiplier on a strictly larger body of tokens, since `P ⊇ (S+U+R)` by construction. **On a cold cache the mechanism costs more than it saves, and a larger `R` makes it worse rather than better.** That is the robust result, and it is why the gate belongs on the gap distribution rather than on `R`.

**Magnitude, under the most favorable arm (100% warm).** `read-scope`'s whole-file cohort mean is 3,071 tokens/call (37,471,104 ÷ 12,201, `docs/case-studies/targeted-read-discipline.md`), so a reviewer doing 6–10 whole-file reads plus the `test-conventions/SKILL.md` body that `claude/.claude/agents/staff-sdet.md:19` mandates every dispatch lands `R ≈ 20,000–35,000`. At `R = 30,000`: avoided write `30,000 × $3.75/MTok = $0.1125`, plus avoided in-dispatch re-reads over ~3 further turns `30,000 × 3 × $0.30/MTok = $0.027`, gross `$0.14`; net of the continuation's own carried prefix (`P ≈ 42,000`, re-read at 0.1x over ~2 turns = `$0.025`) → **≈ $0.11 per addressable event**. Across the 245 same-session repeats in scope (247 minus `skill-fidelity-reviewer`'s 2): **≈ $28 over the ~60-day window, ~$14/month.** At an `R` of 100,000 — plausible in this repo, where a reviewer reading `transcript-analysis.py` "fully" is an 8,000-line read — the figure is ≈ $93. On `claude-sonnet-5` at $2.00 base, both drop by a third ($19–$62).

**Calibration against this repo's own bars.** This repo's own `pr-cost` ledger puts the median shipped PR at $30.79 before the 2026-08-23 handoff-cap boundary and $31.63 after (n=160 and n=26 captured rows, `docs/case-studies/handoff-threshold-impact.md:76-77`), so the entire favorable-case saving — ≈$14/month — is roughly one PR's cost per two months. `docs/cost-levers-considered.md` rejected delegating the `/code-review` diff read at 1.25% of main-context tokens and rejected LSP at a 1.7–3.8% ceiling as below "the double-digit bar." And `docs/case-studies/targeted-read-discipline.md:71` applies a further discount this saving cannot escape: every token here is subagent-resident, "discarded on return rather than re-billed for the rest of the session… A token saved there is worth less than one saved on the main thread."

**My recommendation: run the gate, and expect it to fail.** A round-2 dispatch follows round-1's return by however long the orchestrator takes to read findings, apply fixes, re-stage, and re-run the skill — almost certainly past the 300-second 5-minute tier boundary that `transcript-analysis.py:5711` (`_CACHE_REBUILD_IDLE_5M_SECONDS = 300`) hardcodes. If so the sign is wrong and the $28–$93 ceiling is never reached. The one thing that could rescue it — `experimental.cacheTtl: 1h` on the reviewer roster — costs 2x per write instead of 1.25x on *every* dispatch including the 66% that are first-of-type on a branch and gain nothing, and `.claude/plans/audit-cost-intelligence-guide.md:111` already put its adoption out of scope as "its own change with its own review." I do not recommend bundling it.

### Phase 0 — the gate (always runs)

One measurement, two readings, from `transcript-analysis.py review-trace --this-repo --since <window>` output already in hand. For each same-session repeat of an (agent-type, branch) pair, compute the wall-clock gap since that pair's prior dispatch. Report the distribution, plus the same-round (seconds-apart) versus cross-round (minutes-apart) split the existing 247 figure does not decompose.

**Pre-registered decision rule, fixed before the numbers are read** — the discipline `.claude/plans/delegate-instrument-authoring.md` and `.claude/plans/opus-plan-boundary-handoff.md` both used. Proceed to Phase 1 only if **both** hold:

1. **≥50% of the 245 in-scope same-session repeats fall under 300 seconds.** Below that, the majority arm is B′, where the mechanism loses money.
2. **The projected net, priced at the measured warm share and the measured `R`, exceeds $50 over the observed window.** The $50 floor is settled, not a placeholder — the engineer confirmed it as-is before Phase 0 runs, which is what keeps this criterion genuinely pre-registered (ledger row 15). It is roughly 1.6x this repo's $31.63 median cost per shipped PR (`docs/case-studies/handoff-threshold-impact.md:76-77`). Below that return, a nine-agent-file edit plus a new orchestrator lookup is not worth its maintenance surface.

Otherwise: **decline, and record the row in `docs/cost-levers-considered.md`** with the measured reason. That is a real deliverable, not a gap — the register exists so a later plan does not re-measure this.

### Phase 1 — the mechanism (only if the gate clears)

**Identity storage: none. Use `ListAgents`.** The dispatch prompt correctly notes there is nothing today and that `findings-path-suffix.sh` regenerates `${EPOCH}-${SLUG}` per round (`:37-43`) so it cannot serve. The conclusion to draw is not "build state" but "the harness already holds it." `ListAgents` is the harness's registry of in-process subagents this session spawned, already a live primitive in this repo with a documented unavailable-fallback (`claude-skills/skills/handoff/SKILL.md:41`). Using it needs no new file, no new script, no staleness policy, and no cross-session leakage — and it dissolves the cross-session fallback question by construction: a cross-session round simply finds no row and falls through. Leave `findings-path-suffix.sh` untouched.

**Delta content: already computed.** Step 0.6's staged-diff responsibility boundary (`code-review/SKILL.md:32`) already resolves the current staged increment and already passes it "as file paths with added/modified line ranges, not a ref expression, because not every reviewer carries `Bash`." The continuation message carries exactly that, plus the prior findings and what was applied that line 259 already mandates for re-review. Nothing new is computed.

**The behavioral half, which is the harder half.** Orchestrator-side continuation alone captures nothing: all nine in-scope agent bodies carry an unconditional full-read instruction (`ciso-reviewer.md:47`, `comment-discipline-reviewer.md:83`, and the equivalent in each `staff-*` file), so a continued instance handed a delta would re-read every changed file anyway. Phase 1 is two coordinated changes, not one. The carve-out must be phrased against **what the agent can still see in this conversation**, not against what it read earlier — a compaction inside the subagent could truncate the prior read while leaving the instance addressable, and "you read it earlier" would then authorize skipping a file the agent no longer holds.

**This carve-out does not collide with `code-review/SKILL.md:283`.** That rule bars one spawn's scrutiny substituting for another's. Here the same instance holds the file content in its own context; no file goes unscrutinized. Say so explicitly in the plan, so `/skill-review` and the spawned personas do not read it as a collision.

**Fallback: fall through to today's `Agent` dispatch, once, on every failure.** `ListAgents` errors or unavailable; no matching row; row present but `SendMessage` errors; row present but not addressable. The fallback *is* current behavior, so it is free and fail-safe. Never retry a failed `SendMessage` — one attempt, then fresh dispatch. A retry loop costs more than the mechanism saves.

### Assumption ledger

**Root problem.** A reviewer re-dispatched on the same branch in the same session re-reads files a prior instance already read, and separate `Agent` dispatches share no prompt cache below the global-scope breakpoint, so that re-read is billed at full cache-write price.

**Givens** (fixed beyond this design's reach):

- **G1 — Cross-session continuation is unreachable.** No session can recover a subagent it did not itself spawn once the spawning process ends; the harness owns subagent lifetime. `[verified: .claude/plans/handoff-hard-block.md:121,151]`
- **G2 — Cache TTL and its multipliers are vendor-set.** The 5-minute/1-hour tiers and the 1.25x/2x/0.1x rates are the vendor's; the only control in repo-tracked config is `experimental.cacheTtl` in subagent frontmatter. `[verified: pricing.py:24-27; docs/cost-levers-considered.md, 2026-09-01 "Pick the cache duration" row]`
- **G3 — Cross-reviewer-type decorrelation within a round stays untouched.** The engineer scoped it out. `[engineer-verified]`
- **G4 — Every token saved here is subagent-resident and discounted.** This repo's own case study prices a subagent-side saving below a main-thread one. `[verified: docs/case-studies/targeted-read-discipline.md:71]`

| # | Assumption | Tag | Anchors |
|---|---|---|---|
| 1 | A conversation continuation re-sends the full prior message history; there is no server-side state that avoids re-billing it. Cache makes the resend cost 0.1x, but only within TTL. | `[verified: transcript_analysis/pricing.py:509-551 prices every turn's full input-side token count per call, with no cross-call carry-forward]` | root |
| 2 | A continued instance's carried prefix `P` strictly exceeds a fresh dispatch's re-read `(S+U+R)`, because `P` contains both plus the prior round's own output — so on a cold cache the mechanism is cost-negative. | `[verified: derived from row 1 and the arm table above; no measurement needed, it is a containment argument]` | root; row 1 |
| 3 | Reviewer file reads are never shared across two separate `Agent` dispatches, because they land in `messages[]` after the divergent user prompt; the shared region is `tools` + `system[2]` (global scope, ~22,050 tokens). | `[verified: docs/case-studies/cold-cache-attribution.md § "Testing the leading hypothesis: wire-level capture", SHA-256 request-structure comparison]` | root |
| 4 | The inter-round gap for a same-session (agent-type, branch) repeat exceeds the 300-second 5-minute tier in the majority of cases. | `[unverified]` — this is the gate. Everything in Phase 1 inherits the flag. | row 2 |
| 5 | `review-trace`'s per-event ISO timestamps are sufficient to derive that gap without new instrumentation. | `[verified: docs/transcript-analysis.md:397-427 — per-event timestamps, per-event branch resolution, `--this-repo`/`--since` scoping]` | row 4 |
| 6 | `ListAgents` lists *completed* synchronous dispatches, not only `running` ones. | `[unverified]` — `.claude/plans/handoff-hard-block.md:127` documents only the `running` field. Phase 0 verification item; Phase 1 cannot start without it. | Phase 1 identity storage |
| 7 | Two dispatches of one agent type in a session are individually addressable (distinct name or ID), so a continuation targets the right instance. | `[unverified]` — the `Agent` tool description says "Names are the address," which implies a per-dispatch name, but does not state collision behavior. Phase 0 verification item. | row 6 |
| 8 | `SendMessage` continuation preserves the agent's prior tool results in context, not merely a message-level thread. | `[unverified]` — the tool description's "with its context intact" is a one-line claim, not a specification; no first-party doc in this repo settles it. If it is message-level only, the mechanism saves nothing and the plan stops. | root |
| 9 | Same-session addressability across a compaction boundary is undetermined, and the design does not depend on the answer. | `[unverified]`, deliberately — the fail-safe fallback (row 10) makes an errored `SendMessage` cost nothing. The residual risk is a *successful* `SendMessage` to a truncated instance, which the "still see in this conversation" phrasing of the agent-body carve-out closes. | Phase 1 fallback |
| 10 | Falling through to today's `Agent` dispatch on any continuation failure is free, because it is current behavior. | `[verified: code-review/SKILL.md:293-299 is the current dispatch path and is unmodified by this design]` | row 9 |
| 11 | Nine reviewer agents carry an unconditional full-file-read instruction with no re-review carve-out; `skill-fidelity-reviewer` carries none. | `[verified: agent-file greps reported in Step 3 evidence, cross-checked against ciso-reviewer.md:47, comment-discipline-reviewer.md:83, staff-sdet.md:9-19]` | Phase 1 behavioral half |
| 12 | Reviewer-dispatch read volume `R` is 20,000–35,000 tokens, extrapolated from `read-scope`'s 3,071-token whole-file mean at 6–10 reads. | `[unverified]` — an extrapolation from a corpus-wide mean, not a reviewer-scoped measurement. Phase 0 measures it directly; the estimate is used only for the magnitude range, never for the sign (row 2). | Deliverable 1 magnitude |
| 13 | The staged-diff boundary is already resolved and already passed as file paths with line ranges, so the delta needs no new computation. | `[verified: code-review/SKILL.md:32]` | Phase 1 delta content |
| 14 | The carve-out authorizes skipping a re-read only within one instance's own conversation, which is not the "Prior reviewer covered this" rationale the skill forbids. | `[verified: code-review/SKILL.md:283 — the rule governs one spawn substituting for another spawn, not an instance re-reading its own context]` | row 11 |
| 15 | The gate's dollar floor is $50 of projected net over the observed window: below it the mechanism is declined regardless of how the warm-share criterion lands. | `[engineer-verified]` — confirmed at $50, unchanged from the proposed value, before any Phase 0 measurement existed. Do not revise it from a later magnitude re-estimate; a contradiction pauses and asks. | Phase 0 gate criterion 2 |

### Over-powered-primitive check

The proposed mechanism — cross-round continuation plus nine agent-body carve-outs — is heavier than the task on its face. Four lighter primitives were examined against the source system:

1. **`ListAgents` instead of a new state file** — **adopted.** This check's own output: the first draft's per-round identity store dissolved into a harness primitive that is already in use, needs no staleness policy, and makes the cross-session case a no-op rather than a branch.
2. **Narrow the re-read to the already-computed staged-diff boundary, no continuation at all** — rejected. `code-review/SKILL.md:281-287` bars substituting reduced scrutiny for a dispatch, and no evidence exists that a boundary-scoped read preserves review quality. Cutting reads on cost grounds without that evidence is the wrong-foundation move the skill's own guardrail exists to stop.
3. **`experimental.cacheTtl: 1h` alone** — rejected as a substitute (kept in reach as a rescue, out of scope here). It warms only the small system-prompt prefix and never touches a `Read` result, while raising the write multiplier 1.25x→2x on every dispatch including the 66% first-of-type ones that gain nothing.
4. **Cut the round count (the round-3 `plan-architect MODE=consult` follow-up)** — rejected as a substitute. It targets the 29-PR 3+-round tail rather than the 2-round majority this plan addresses, and `docs/cost-levers-considered.md`'s `opus-frontload-review-rounds.md` section already owns it as its own scoped `/plan-it` run.

### Scope call the engineer invited: exclude `skill-fidelity-reviewer`

Exclude it, for three converging reasons rather than the addressable-slice count alone: 2 same-session repeats in the whole window; no per-file sweep to skip (it compares a skill-invocation list against skill bodies) and correspondingly no full-read instruction to carve out; and it holds no `Bash`, so it is handed literal diff text rather than fetching its own (`ready-for-review/SKILL.md:102`, per `docs/cost-levers-considered.md`'s 2026-08-15 section). Including it for uniformity buys nothing and adds a ninth file to the blast radius of an edit that already fires the *Reshapes reviewer ownership* row.

## Critical files

**Phase 0 — the gate.** Produces no repository change on the adopt path, and exactly one on the decline path:

- `docs/cost-levers-considered.md` — **create one new section** on decline, following the register's own format: source-plan name, a lever/verdict/measured-reason table, and the pre-registered rule stated as the rule rather than reconstructed after the fact. Reuse the framing precedent in the `delegate-instrument-authoring.md` and `opus-plan-boundary-handoff.md` sections, both of which record a pre-registered gate that failed.
- Reuse, do not rebuild: `transcript-analysis.py review-trace --this-repo --since <window>` supplies the per-event timestamps (`docs/transcript-analysis.md:397-427`). No new subcommand. The gap arithmetic is a one-off scan over its output, and should be labelled as such in the register row, matching how that page already qualifies its one-off scans.
- Phase 0 also resolves ledger rows 6, 7, and 8 by direct harness probe. Rows 6–8 are the hard gate: if `SendMessage` turns out to be message-level only, or a completed synchronous dispatch is unaddressable, Phase 1 cannot exist regardless of the cost arithmetic.

**Phase 1 — only if the gate clears.** Two dispatches, sequenced, because the second's correctness depends on the first's wording:

*Dispatch 1 — orchestrator:*
- `claude-skills/skills/code-review/SKILL.md`, § "Ripple effect triage" (lines 244–299) — insert the continuation lookup ahead of the existing spawn instruction at line 293, and extend line 259's re-review clause to name it. Keep line 293's `findings-path-suffix.sh` call and per-round suffix exactly as they are: a continued instance writes to the *current* round's findings path, so the suffix's per-round regeneration is correct and must not change. Add the continuation outcome to the mandatory **Spawn decisions:** line (line 219 § "Output format", format rule at line 230) as a short tag — the format already carries per-spawn rationale, and an unrecorded silent fallback is the failure mode that section exists to close.

*Dispatch 2 — agent bodies (disjoint file set, sequenced after dispatch 1 so the carve-out quotes the skill's settled wording):*
- `claude/.claude/agents/ciso-reviewer.md:47`
- `claude/.claude/agents/comment-discipline-reviewer.md:83`
- `claude/.claude/agents/staff-analytics-engineer.md:80`
- `claude/.claude/agents/staff-backend-engineer.md:63`
- `claude/.claude/agents/staff-data-engineer.md:56`
- `claude/.claude/agents/staff-frontend-engineer.md:57`
- `claude/.claude/agents/staff-platform-engineer.md:61`
- `claude/.claude/agents/staff-product-engineer.md` (second bullet, "How to work")
- `claude/.claude/agents/staff-sdet.md:55`

One shared clause, identical in all nine — no shared partial is available across agent files, so it is duplicated by necessity, not by choice. Phrase it against content the instance can still see in its own conversation, never against what it read earlier (ledger row 9).

**Explicitly unchanged:** `claude/.claude/scripts/findings-path-suffix.sh` — its per-round `${EPOCH}-${SLUG}` regeneration is correct for a continuation and needs no stability change. `claude/.claude/agents/skill-fidelity-reviewer.md` — excluded per the scope call above.

**Not created:** no state file, no new script, no `settings.json` change. `ListAgents` is the registry.

## Verification

Per this repo's `CLAUDE.md`, run the scoped selector rather than the full suite:

```
.venv/bin/python3 claude/.claude/scripts/select-tests.py
```

That maps `claude-skills/skills/code-review/SKILL.md` and the nine `claude/.claude/agents/*.md` files to the skills and agent-roster suites (`claude-skills/skills/tests/test_skills.py`, `claude/.claude/hooks/tests/test_agent_roster.py`). Do not widen by hand: a path it cannot map is a bug in its rule table, not a licence to run the full suite. Note the known under-collection bug tracked as GH-882 — if a selected domain directory and a contained file are both selected, re-verify with plain directories.

Lint the touched surfaces:

```
.venv/bin/ruff check claude/.claude/ claude-skills/
```

Phase 1 additionally requires the per-file-type review dispatches that `.claude/rules/review-pipeline-dispatch.md` mandates, not as optional polish:

- `/skill-review` on the `code-review/SKILL.md` diff — **hook-enforced**; `require-skill-review.sh` blocks `git commit` until the behavioral-equivalence marker is written.
- `/agent-review` on each of the nine agent files.
- `/code-review`'s *Reshapes reviewer ownership* row fires on this diff — spawn every persona named in the pre- and post-edit union, since the change alters what each reviewer's read instruction resolves to.

Phase 0's decline path touches only `docs/cost-levers-considered.md`, so `select-tests.py` on that diff plus `comment-discipline-reviewer` (durable-doc prose) is the whole verification surface.

## Out of scope

- **Cross-reviewer-type decorrelation within a round** (`docs/design-decisions.md` §3). Untouched by construction: the design keys on same-type, same-branch, same-session only.
- **Cross-session continuation.** Established infeasible; not re-derived (`.claude/plans/handoff-hard-block.md:121,151`). The `ListAgents` mechanism makes this a silent no-op rather than a case needing detection.
- **`experimental.cacheTtl: 1h` on the reviewer roster.** The one lever that could rescue a failed gate, and deliberately not bundled: it raises the write multiplier on every dispatch including the two-thirds that gain nothing, it sits in an `experimental.` namespace, and `.claude/plans/audit-cost-intelligence-guide.md:111` already scoped its adoption to "its own change with its own review." If Phase 0's gate fails on warm-share alone — with rows 6–8 verified and the magnitude otherwise acceptable — that is the follow-up to open, and the decline row should say so.
- **Reducing the number of `/code-review` rounds.** A different attack on the same waste, already recommended and separately owned as the round-3 `plan-architect MODE=consult` follow-up (`docs/cost-levers-considered.md`, `opus-frontload-review-rounds.md` section).
- **Narrowing any reviewer's read scope on cost grounds.** Rejected in the over-powered-primitive check; `code-review/SKILL.md:281-287` governs, and nothing here supplies the review-quality evidence that would be needed to revisit it.
- **`skill-fidelity-reviewer`.** Excluded with reasoning above.
- **`findings-path-suffix.sh` suffix stability.** Examined and deliberately unchanged — per-round regeneration is correct for a continuation.
- **Any change to `/code-review`'s spawn *decision* — which rows fire, or whether a matched row can be skipped.** This design changes only *how* a re-dispatch is issued, never *whether*. The five invalid skip rationales at `code-review/SKILL.md:281-287` are untouched and must stay untouched; a continuation that silently substituted for a matched spawn would be exactly the failure they exist to prevent.
