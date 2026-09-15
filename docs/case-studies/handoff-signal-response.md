# Does this repo's own corpus show the step-count-for-cost rationalization the handoff-nudge fix targets?

*Part of the [claude-config case studies](../case-studies.md).*

**The problem.** `.claude/plans/handoff-nudge-rationalization-gap.md` fixes an incident where a `ready-for-review` session, warned that context had crossed the handoff-nudge threshold, defended continuing by reasoning that the remaining named steps were "well-defined and bounded" and therefore cheap. That substituted remaining-step *count* for remaining token *cost*, an unsound proxy since several of those steps are themselves full agent-dispatch passes. That plan closes the gap by making `ready-for-review` Step 1 defer to a fresh session instead of freelancing a completion judgment. This page asks whether the same substitution recurred, unnoticed, elsewhere in this repo's own session history — or whether the corpus shows something else.

**Question.** Across every observed context-budget signal in this repo's own transcript corpus, and especially among the signals where continuing cost the most afterward, how often did the agent's own next words ground continuing in an actual cost or context-budget argument, cite bare step-count/"nearly done" framing with no cost reasoning, hand off as designed, or show no sign of having registered the signal at all?

**Short answer.**

- The single largest response to a signal, among the 40 highest-post-signal-spend signals, was none of the three the plan's rubric named going in: 22/40 (55.0%) show no acknowledgment anywhere in the observed window that the signal fired at all — the agent's text simply continues the task, unchanged, as if the nudge had not happened.
- The step-count-for-cost substitution the original incident showed is real but a minority of the sample: 11/40 (27.5%) continued citing only step-count or "nearly done" framing with no cost reasoning.
- 4/40 (10.0%) continued with an actual cost-grounded reason, and 3/40 (7.5%) handed off as designed.
- Across the full corpus, 1,490 context-budget signals were observed, 87.1% (1,298/1,490) followed by a same-session `/handoff` — a high aggregate conversion rate that, as the ignored share above shows, says little about whether a continuation was ever reasoned through at all.
- 97.9% (1,459/1,490) of signals' own post-signal spend exceeded this corpus's startup-burn benchmark (what a fresh session's own first turns would have cost instead) — continuing past a signal is expensive far more often than it is cheap, whether or not the decision was ever articulated.
- Every sampled signal predates the `ready-for-review` deferral fix, which ships unmerged in this same branch — this is a pre-fix baseline, not a measurement of whether the fix worked.

## How this was measured

- **Corpus.** This repo's own transcript corpus only, single account, single root (`~/.claude/transcript-config-dirs` declares no additional root contributing to this repo).
- **Census command:** `transcript-analysis.py --config-dir ~/.claude handoff-signal-response --this-repo`, run 2026-09-13. The resolved-scope banner reported 134 project dirs and 1 root.
- **Curation/classification sample command:** the same subcommand with `--sample 40 --seed 20260911 --format md --context-turns 3`, emitting 40 curation cards ranked by post-signal dollar spend (`dollars_after_signal`) descending. Each card carries the signal's metadata, the agent's own next eligible plain-text turn after it, and up to 3 further main-thread turns' `text` and `thinking` content. `--no-redact` was not used: the harness's own auto-mode tool classifier denies that flag combination for this subcommand outright on this machine, independent of anything in `transcript-analysis.py` itself, so cards carry opaque per-run session labels (`session-1`, `session-2`, ...) instead of real session IDs. The deck is internal working material only, was never committed, and no session label, file path, or other identifying field appears in anything quoted below.
- **Classification:** a `general-purpose` (Sonnet) dispatch read all 40 cards, including every forward-context turn's `thinking` field, against a rubric of (i) continued with a cost-grounded reason, (ii) continued citing step-count/"nearly done" with no cost reasoning, (iii) handed off, or (iv) no legible rationale in the observed window. None of the 40 cards' forward-context turns carried a non-empty `thinking` block in this run, so the `thinking`-field check added no additional classified cards, but the read was evidence-complete rather than a truncated sample.
- **Manual adjudication of category (iv).** The dispatch's own 22 "unclassifiable" cards were reviewed directly: every one of them is fully legible prose describing ordinary task continuation (spawning reviewers, reading a diff, waiting on a sub-agent, fixing a bug) with no reference anywhere to the threshold, the nudge, context, or a handoff decision. That is a distinct finding from "the instrument couldn't tell" — the text is plain, it simply never engages with the signal — so this page reports it as its own category, **ignored**, rather than folding it into an "unclassifiable" bucket that would understate what the transcripts actually show.
- **A rationale shape the rubric didn't name.** 4 of the 11 step-count/no-cost cards reason about *in-flight background-dispatch state* ("would lose track of in-flight agent state," "stranded in a fresh session") rather than step count or "nearly done" framing specifically. They're counted under (ii) here since they're non-cost but legible, but this is a distinct third non-cost rationale shape worth a rubric refinement if it recurs in a future rerun.
- **Named limitations:**
  - The 40-card sample is the highest-post-signal-spend slice of the corpus, chosen because that is where a wrong continue-decision actually costs something (the plan's own rationale) — it is not a random sample and does not estimate a population-wide base rate.
  - **Ignored** means no acknowledgment within the observed window: the excerpt turn plus up to 3 forward-context turns. It cannot rule out that a session engaged with the decision later, further along in the same transcript, than this window reaches. Even so, zero acknowledgment across every one of 22 cards — several of them spanning multiple substantive forward-context turns of continued, engaged work — is itself informative, not merely an artifact of a narrow window.
  - This sample (seeded `20260911`, drawn 2026-09-13) is a different 40-card slice than an earlier internal draft of this page drew with the same seed on 2026-09-12: the corpus keeps growing, so a fixed seed pulls a different top-40-by-spend slice on different days. The two are not comparable point-in-time reads of the same sample.
  - All 40 sampled signals predate the `ready-for-review` deferral fix landing, since it ships unmerged in this same branch. This page establishes a pre-fix baseline only.

## Numeric findings

**Reading these tables.** "Median $ after" is the central-tendency statistic reported for `dollars_after_signal` — priced main-thread dollars spent strictly after the signal's own turn, excluding whatever that turn itself cost (`docs/transcript-analysis.md` § "handoff-signal-response").

The `ready-for-review` marker-context split is a second, independent axis, not a signal kind of its own. It tracks one specific thing: whether a live `ready-for-review` active-bypass marker (`marker.sh activate ready-for-review` / `deactivate`) was armed at the moment the signal fired — i.e. the signal happened mid-run of that one skill. "Inactive" is not a single alternate pipeline stage; it's every other context a signal can fire in:

- ordinary implementation work
- `plan-it`
- a `code-review`-only pass outside `ready-for-review`
- the `handoff` skill's own `--check` query

No other skill's own marker (`plan-review`, `code-review`, `handoff`) is tracked by this axis.

The three signal kinds differ in the actual mechanism that surfaces the information to the agent, not just in label:

- **`check`** — the agent itself runs `nudge-handoff-near-context-cap.sh --check` as a Bash call at one of three skill-prescribed decision points (`plan-it` Step 7, the `handoff` skill, or `ready-for-review` Step 1) and reads back a JSON verdict. A self-initiated question. The table below pools all three call sites without breaking out which one asked.
- **`advisory`** — the hook itself injects an `additionalContext` reminder into the model's own context automatically, via the `PostToolBatch`/`Stop` hook, with no query from the agent. An unprompted nudge.
- **`hard-block`** — the same automatic mechanism as `advisory`, escalated: it force-stops the agentic loop (stderr + exit 2) instead of merely advising.

### Full corpus (1,490 signals)

| Signal kind | Signals | Same-session handoff rate | Median $ after |
|---|---|---|---|
| advisory | 1,026 | 83.3% | 3.79 |
| check | 441 | 95.2% | 0.78 |
| hard-block | 23 | 100.0% | 1.86 |

| `ready-for-review` marker context | Signals | Same-session handoff rate | Median $ after |
|---|---|---|---|
| active | 520 | 78.8% | 3.25 |
| inactive | 970 | 91.5% | 1.82 |

Sessions with at least one signal: 601. Operator-response-lag cross-check against `.handoff-nudge.log`'s own `nudged` lines: 1,040 joined (375 excluded — no matching session in scope), median lag 84,894 tokens past the fire point.

### Cost share and the startup-burn benchmark

`docs/transcript-analysis.md` § "handoff-signal-response" defines the startup-burn benchmark as a session-count-weighted average, across every branch in this scope, of that branch's own non-first sessions' first 5 main-thread turns' combined dollar cost — the price a fresh session on an already-active branch pays before it does anything else, independent of any signal. Comparing a signal's own post-signal spend against this one corpus-wide figure answers a directly actionable question: did staying past the signal cost more than a fresh session's own startup tax would have?

This run: **$0.31 per continuation session**. **97.9% (1,459/1,490)** of signals' own `dollars_after_signal` exceeded that figure — continuing past a signal costs more than a fresh start's own overhead in all but a small minority of cases, regardless of whether the continuation was ever reasoned through.

### Classification of the 40 highest-post-signal-spend signals

| Category | Count | Share |
|---|---|---|
| (i) continued, cost-grounded reason | 4 | 10.0% |
| (ii) continued, step-count/"nearly done" only | 11 | 27.5% |
| (iii) handed off | 3 | 7.5% |
| (iv) ignored — no acknowledgment of the signal | 22 | 55.0% |

## Excerpts

Numbered sequentially within this page; `.claude/plans/handoff-nudge-rationalization-gap.md` decided the published page carries no session id, truncated or otherwise, and no record number. Each is the agent's own verbatim text, elided with "…" only where trimmed.

**Category (iv) — ignored, for contrast with the other three:**

Excerpt 1:
> Four specialists spawned in parallel: `staff-platform-engineer` (shell discipline, temp-file lifetime, rollback), `staff-sdet` (test realism, the pinned-constant updates), `ciso-reviewer` (private-repo diff content persisting in `/tmp` uncleaned — the plan's weakest point in my read), `staff-product-engineer` (whether the cache-miss branch actually gets cheaper, and the deferred `/code-review` truncation defect). `skill-review` and `agent-review` run after, per the Claude Code config domain.

Excerpt 2:
> Marker hash matches the current staged diff exactly. Retrying the commit now — this was likely a stale read before the last marker write landed.

The second excerpt's forward context runs three further turns of substantive, engaged work — merging 24 commits, running a scoped `/code-review`, and a reviewer independently reproducing a real security bypass in a memory-file path check — all of it completed and disposed of without the signal that triggered this excerpt ever being mentioned again. Ignored, here, does not mean idle or terse; it means the decision itself never surfaced in the agent's own words.

**Category (ii) — step-count/"nearly done," no cost reasoning:**

Excerpt 3:
> Quick note: this session has crossed the handoff-nudge threshold. There are still a few bounded steps left (finish this plan-review, re-dispatch the fix agent, finish `/code-review`, commit, then `/ready-for-review`) — I'll keep going since stopping mid-flow would leave the plan-review gate armed, but say the word if you'd rather I `/handoff` and continue fresh.

**Category (i) — continued, with an actual cost argument, for contrast:**

Excerpt 4:
> Handoff nudge fired (past 150k). Staying in this session: what remains is one edit pass on the plan, the plan-review verdict, and the commit — a handoff would re-pay for an evidence base that took four discovery agents to build, for maybe four turns of benefit.

**Category (iii) — handed off, for contrast:**

Excerpt 5:
> Full diff read. Given the handoff-nudge threshold warning and the substantial work still remaining (spawning specialist reviewers, reconciliation, disposition, commit, then `/ready-for-review` steps 4–8), I'll hand off now rather than risk losing an in-flight review round to context exhaustion. Let me check the background test run first so the handoff captures its result.

## Synthesis

The plan that motivated this page hypothesized one failure mode: the agent notices the signal and rationalizes past it with an unsound step-count argument. That pattern is real — 11/40 (27.5%) — but it is not the dominant response, and it is not the most concerning one. More than twice as many of the same highest-spend continuations (22/40, 55.0%) show no sign the agent registered the signal at all. Some of those are short, routine continuations where a missed acknowledgment might plausibly matter little; others, like Excerpt 2 above, are long, high-effort, fully-engaged sessions that simply never returned to the decision the signal raised. Only 4/40 (10.0%) grounded the decision in an actual cost argument, and 3/40 (7.5%) handed off as designed.

That reframes the fix this page was meant to evaluate. The `ready-for-review` deferral fix targets the rationalization case directly — it removes the freelanced completion judgment the original incident showed. It does not, on its own, address a session that never engages with the signal in the first place, since there is no rationalization step to intercept. Whether the advisory/hard-block mechanisms themselves are salient enough to reliably prompt engagement — as opposed to a defensible reason to continue once noticed — is a separate question this page's data raises but does not answer.

This does not establish how often either pattern recurs across the full 1,490-signal population — the sample is deliberately the highest-spend slice, not a random draw. What it does establish is that the incident which motivated this plan was not the typical shape of what happens after a signal fires, even among the highest-spend continuations where getting the decision wrong costs the most: the more common outcome is that the decision doesn't visibly happen at all.

**Numeric revisit trigger.** Once the `ready-for-review` deferral fix has been merged and live long enough for this corpus to accumulate a comparably-sized post-fix top-40-by-spend sample (40 new signals with `dollars_after_signal` in a similar range to this study's), re-run the identical `handoff-signal-response --this-repo --sample 40 --context-turns 3` census and classification pass and compare all four categories' shares against this study's pre-fix baseline (10.0% / 27.5% / 7.5% / 55.0%). A meaningful drop in the ignored share, specifically, would be evidence the fix (or a follow-on to it) increased engagement with the signal itself, not merely rationalization quality once engaged.

## Sources

- **`claude/.claude/scripts/transcript-analysis.py`** — the `handoff-signal-response` subcommand (census and `--sample`/`--seed`/`--format md`/`--context-turns` curation modes), run directly this session.
- **`docs/transcript-analysis.md`** § "handoff-signal-response" — the subcommand's own documentation, including the excerpt source-turn eligibility restriction and the startup-burn benchmark definition this study relies on.
- **`docs/handoff-nudge.md`** — the nudge hook's own documentation, including the advisory clause the `ready-for-review` deferral fix qualifies.
- **`.claude/plans/handoff-nudge-rationalization-gap.md`** — this study's own plan, including the classification rubric, the excerpt-source-eligibility restriction (no `tool_use`/`tool_result`/user turn is ever excerpt-eligible) and the no-session-id publication decision, and the rationale for ranking the sample by post-signal spend.
- A `general-purpose` (Sonnet) dispatch, classifying the 40 sampled curation cards against the rubric above, followed by manual review of its 22 "unclassifiable" cards; not committed, reproducible from the sample command and rubric stated here.
