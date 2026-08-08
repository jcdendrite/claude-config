# Cut Claude Code token spend — measure-then-act on session prefix growth

## Context

**Goal: reduce measured token spend across four Claude accounts from ~$10,757 per 8 days toward ~$5,400 per 8 days, measured at the post-September-1 rate card.**

Spend was investigated because one account's sessions appeared unusually expensive. Running `transcript-analysis.py cost --since 8d` against all four account config directories shows the problem is not scoped to that one account — it is distributed across all four, not concentrated in the one investigated, and all four are driven by the same mechanism. **Absolute and relative per-account figures are withheld from this file per this repo's redaction rules for confidential business shape; the aggregate total below is the engineer's own, not a client's.**

Composition, summed across all four accounts: cache_read 55.0%, cache_write 34.5%, output 10.3%, input 0.1%. By model: Sonnet 84.3%, Opus 15.7%, Haiku 0.0%. **~90% of spend is prompt-prefix cost, not generated work**, and 44% of all turns are subagent turns that each build their own prefix from scratch (measured via `transcript-analysis.py subagents`).

**The dominant term, measured directly:** the largest account's 8-day Sonnet+Opus turn count is 26,830, against 4,769,324,322 combined cache-read tokens. That is **~177,761 tokens of prefix paid on every single API call**. `claude/.claude/CLAUDE.md` is ~5,800 tokens of that — roughly 3%. The remaining ~172,000 tokens per call is accumulated conversation history and tool output, which is a **working-pattern variable, not a config-file variable**.

**This is the third version of this plan.** Two `/plan-review` rounds (8 specialist dispatches) found the original file-shrinking stage moved 5% or less against the measured baseline while risking always-on safety rules; a third round (4 more dispatches) then found the routing-precision fixes that survived round two were each individually unsound on inspection — one relied on a cap that was never actually shipped, another was falsified by the exact git workflow (`commit --amend`) the plan itself was recommending elsewhere. **What survived three rounds of adversarial review is smaller than what this plan originally proposed, and that is the honest, evidenced outcome, not a failure of the investigation.** The repo's review-routing config turned out to be closer to correctly tuned than the initial read suggested.

**Deadline unchanged.** Sonnet 5 introductory pricing ends August 31, 2026: base input $2→$3/MTok, output $10→$15/MTok ([pricing docs](https://platform.claude.com/docs/en/about-claude/pricing), fetched 2026-08-07). Sonnet is 84.3% of spend and every Sonnet rate scales by exactly 1.5x on that date (all four token classes derive from the same base-rate multiplier). At unchanged volume, today's spend becomes ~1.42x higher at September rates. **Reaching half of today's spend, measured at September rates, requires a 64.8% cut in blended token volume** — computed as `1 - (target / (sonnet_spend×1.5 + opus_spend + haiku_spend))`, both terms independently re-derivable from the `cost` subcommand's own model-split output. If the cut is achieved entirely through prefix reduction (holding output/input volume fixed, since Stage A below only targets prefix), the required prefix-specific cut is higher, approximately 74.5%, using the same derivation restricted to the cache_read + cache_write pool.

## Approach

### Root problem and givens

Cost = Σ over API calls of (prefix size × rate). A session that grows to prefix size *S* over *T* calls pays cache-read on the accumulating prefix on up to every one of those *T* calls, so total cache-read spend scales with prefix size **times** call count. Prompt caching's 0.1x-base cache-read rate makes each individual re-read cheap; it does not make an ever-larger prefix cheap to keep re-purchasing for the rest of a session's life. This is stated correctly today in `claude/.claude/hooks/nudge-handoff-near-context-cap.sh:186`'s own nudge text: *"Per-turn cost rises with carried context, but a fresh session pays a one-time rebuild cost first, so handoff pays off over the next several turns rather than immediately."*

| # | Given | Why it is fixed |
|---|---|---|
| G1 | Sonnet 5 base rate rises to $3/$15 on 2026-09-01 | Vendor-set. |
| G2 | Cache read is 0.1x base input, charged on the full prefix every call | Vendor pricing mechanic. |
| G3 | The 1M context window carries no per-token premium | Vendor pricing: "A 900k-token request is billed at the same per-token rate as a 9k-token request." |
| G4 | Subagent context is not shared with the parent | Harness architecture; each dispatch pays its own prefix. |

### Stage A — Reduce prefix carried into each call (measure, then act)

**A1. Ground a new handoff threshold with a distribution query, then make the six-site synchronized edit.** `nudge-handoff-near-context-cap.sh:163` currently fires the handoff nudge at 60% of context window (`THRESHOLD=$(( CONTEXT_WINDOW * 60 / 100 ))`) — the only data available today (`cost`'s ≥200k-context dollar-share bucket) is too coarse to justify any specific replacement percentage; picking one without the distribution would repeat the exact ungrounded-number defect this plan already had to cut twice.

Two-step, in order:
1. Add a `context-distribution` subcommand to `transcript-analysis.py`, reusing the existing per-turn `input_tokens + cache_read_input_tokens + ephemeral_1h + ephemeral_5m` context calculation already used by `cost`'s bucket logic (`:2760-2762`). Report, per session, the peak context-at-turn reached, bucketed at candidate threshold percentages (e.g. 30/40/50/60% of window). Run across all four accounts.
2. Pick the threshold from that curve — the value where a meaningful share of high-spend sessions still have runway to act on the nudge, not where they've already blown past it. Then edit these six sites together, in one change: `nudge-handoff-near-context-cap.sh:145` (comment), `:163` (the `THRESHOLD` calculation), `:186` (the injected `additionalContext` string, which currently asserts "60%" as literal fact to the model — shipping a threshold edit without this site is the exact bug class `.claude/plans/noble-sauteeing-dream.md` (GH-556) already fixed once); `docs/handoff-nudge.md:5,7-10,65,67`; `README.md:411,423`; and `test_nudge_handoff_near_context_cap.py`'s `LARGE_THRESHOLD`/`SMALL_THRESHOLD` constants (`:41-42`) plus the discriminating probe at `:541` (`LARGE_THRESHOLD - 1`), which must be **re-derived from the new percentage**, not renumbered — at a different threshold it silently stops testing the model-ID prefix-collision guard it exists for. Register the threshold percentage in `test_doc_counts.py`'s `DocCountFact` registry so a future edit to one site without the others fails a test instead of shipping a false string, per the pattern that test file already uses for this repo's other cross-file numeric facts.

**A2. Make subagent delegation for verbose tool output a measured practice, not prose-only.** `CLAUDE.md`'s existing "Default-consider delegation" bullet and the `subagent-delegation` skill already state the rule; the gap is that nothing measures whether it's followed. Extend the existing `subagents` subcommand (`transcript-analysis.py`) — which already discriminates `isSidechain` per record — to additionally report total tool-result bytes per thread-type (main vs. sidechain) per branch, reusing the existing `tool_result`-block walk (`:102-118`) rather than adding new parsing. Report **aggregate byte counts only** — no tool-result content, file paths, or session/cwd identifiers — inheriting the repo-scoped `--projects` minimization default the `skill-invocation` subcommand already documents as its own privacy control (`ready-for-review/SKILL.md:95-96`). Add unit tests to `test_transcript_analysis.py`: a record with `isSidechain: true`, one without, a record with and without a `tool_result` block, plus empty-transcript and unreadable-file returns. No new hook, no blocking mechanism — a measurement addition, not a new enforcement layer.

**Stage A has no independently computable savings percentage in advance** — A1's own threshold is deliberately left to be set by its own measurement step, and A2 is a behavior change. Both are measured after adoption via `cost-trend`, not estimated before it.

### Stage B — Cache-TTL investigation (run first, in parallel; no dependency on A)

cache_write is 34.5% of spend. One account books a meaningful share on 5-minute writes and **none** on 1-hour writes, while the other three book 12–20% of spend on the 1-hour tier. A 1-hour write costs 2x base against 1.25x for 5-minute, and pays for itself after two reads within the hour.

**Finding (resolves `B1`): not configurable from this repo's side.** Anthropic's prompt-caching docs are explicit that TTL is set per-request by the API caller via `cache_control: {type: "ephemeral", ttl: "1h"}` — omitting `ttl` defaults to 5 minutes. In Claude Code, "the caller" is the CLI itself; nothing in `settings.json`, hooks, or env vars exposes this field, and all four accounts share the identical stowed `settings.json` via symlink, so a per-account asymmetry cannot come from config divergence at that layer. The likely explanation is CLI-version drift across machines/sessions or genuine workload-length differences (the CLI may apply an internal heuristic tied to session duration), neither of which this repo can act on. **No fix proposed — closing this out as investigated, not deferred.**

### Considered and rejected — kept here so this ground isn't re-tread

Three routing-precision "fixes" were proposed across the first two plan revisions and each was individually falsified under review. Documenting the specific reason, not just the outcome:

- **Skipping the cumulative `/code-review` pass in `ready-for-review/SKILL.md` step 3 for single-commit PRs**, on the premise that a single commit's cumulative diff is byte-identical to what per-commit review already saw. Falsified: `require-code-review.sh:100` hashes the *staged increment*, not the commit's resulting content, so `git commit --amend` reviews the amendment alone — the commit ends up as content A+B while only B was ever reviewed. `git rebase --continue` fires no review gate at all. Both leave a single-commit PR with content no `/code-review` pass has ever seen as a whole, which the skip would then also exempt from the cumulative pass — losing coverage rather than avoiding duplication. A companion item recommending fix-commit batching (which nudges toward exactly the `--amend` path that breaks this) was dropped for the same reason.
- **Capping the reviewer-ownership fan-out at `code-review/SKILL.md:248`.** On inspection, no cap exists in the shipped file today — "Spawn every persona named in the pre- or post-edit table" is unconditional, and a narrower version (union of personas whose checklist items the diff touches) was proposed in its place. That narrowing was itself the defect: the row's own stated purpose is that each named persona evaluates whether their *unedited* row is now "bleeding into another lane" from an edit elsewhere in the table — the reviewer positioned to catch that is exactly the one a checklist-item-touch filter would exclude. No change proposed; the current unconditional behavior stands.
- **Gating `skill-fidelity-reviewer` dispatch on diff file paths** (`SKILL.md`/`agents/*.md`/`hooks/*.sh` in the diff). Falsified twice over: first because it's orthogonal to what the reviewer checks (invocation abbreviation, not file type touched), and second because `ready-for-review/SKILL.md:102-103` already implements the intended fix — dispatch only when the branch's `skill-invocation` query returns a non-empty list — so there was nothing left to change.
- **Retiring `staff-analytics-engineer` from auto-routing** on a reading of "6 dispatches, 0 findings." `reviewer-yield`'s full output is Dispatches 6 / Found 6 / Zero 0 / Unclass 0 / Findings 0 — the agent flagged concerns in all 6 dispatches; "Findings" is a documented lower bound, not a yield signal on its own. No change.

### Assumption ledger

| Row | Assumption | Tag |
|---|---|---|
| A1 | ~$10,757/8d aggregate is the real invoiced amount | `[engineer-verified]` — API pay-per-token billing |
| A2 | Price-weighted figures match the vendor rate card | `[verified: transcript-analysis.py:2690-2696 cross-checked against the pricing page fetched 2026-08-07]` |
| A3 | The 1M context window carries no long-context premium | `[verified: pricing page §Long context pricing]` |
| A4 | 44% of turns are subagent turns | `[verified: transcript-analysis.py subagents, two largest accounts independently ~44-45%]` |
| A5 | Prefix cost is ~177,761 tokens/call on the largest account | `[verified: cache-read tokens ÷ Sonnet+Opus turn count, largest account, 8-day window]` |
| A6 | 64.8% blended / ~74.5% prefix-specific volume cut needed to hit target at Sept rates | `[verified: computed from the plan's own model-split cost figures; both derivations shown in Context]` |
| B1 | The 1h-vs-5m cache TTL is influenceable by configuration | `[verified: pricing docs §Prompt caching — TTL is a per-request API-caller field, not exposed by Claude Code config]` — resolved false; see Stage B finding. |
| C1 | The nudge threshold's correct value can be read off a session-level distribution query | `[unverified]` — the query proposed in A1 doesn't exist yet; if the resulting distribution is too flat to pick a clear threshold, A1's second step has no grounded number to act on and stays a measurement-only deliverable. |

### Expected reduction

| Stage | Contribution |
|---|---|
| A — prefix reduction | Not estimable before the measurement steps run; measured via `cost-trend` 1-2 weeks after A1's threshold lands and A2's delegation practice is adopted. |
| B — cache TTL | 0% — B1 resolved: not configurable from this repo's side. |
| Rejected items | 0% — each would have added risk (coverage loss, marker/text divergence) for effect the plan itself rated "low single digits" before review found the defects. |

**This plan does not commit to a number for reaching 65%.** The only lever positioned against the dominant term is Stage A, and its size can only be known by running its own measurement steps and then measuring the outcome — the alternative, estimating first and editing second, is exactly what produced the two previous drafts' unsound estimates. If Stage A's measured effect after 1-2 weeks falls well short of the 64.8% figure above, the next conversation is about session-count and session-length distribution directly — visible today via `subagent-mix` and `cost-trend` — not further config edits.

## Critical files

Modify:
- `claude/.claude/scripts/transcript-analysis.py` — new `context-distribution` subcommand (A1 step 1); extend `subagents` with main/sidechain tool-result byte totals (A2)
- `claude/.claude/scripts/tests/test_transcript_analysis.py` — cases for both additions above
- `claude/.claude/hooks/nudge-handoff-near-context-cap.sh:145,163,186` — threshold value, once A1 step 1's output grounds it
- `docs/handoff-nudge.md:5,7-10,65,67`, `README.md:411,423` — threshold prose, same change
- `claude/.claude/hooks/tests/test_nudge_handoff_near_context_cap.py` — `LARGE_THRESHOLD`/`SMALL_THRESHOLD` (`:41-42`) and the re-derived collision probe (`:541`)
- `claude/.claude/scripts/tests/test_doc_counts.py` — register the threshold percentage as a `DocCountFact`
- `claude/.claude/CLAUDE.md` — one concrete threshold sentence added to the existing "Default-consider delegation" bullet, no relocation

Not modified — see "Considered and rejected" above for why:
- `ready-for-review/SKILL.md` step 3, `require-ready-for-review.sh`'s deny messages, `code-review/SKILL.md:248`, `skill-fidelity-reviewer`'s dispatch gate, `staff-analytics-engineer`'s routing.
- `claude/.claude/CLAUDE.md`'s Scope-discipline and credential-path blocks, and `require-routing-read.sh` — both already correctly scoped or already correctly enforced; established in the first review round.

Reuse rather than reimplement:
- `transcript-analysis.py`'s existing `cost`, `cost-trend`, `reviewer-yield`, `subagent-mix`, `subagents`, `skill-invocation` subcommands and their `_iter_records`/`isSidechain`/`tool_result`-walk primitives.

## Verification

1. **Baseline before any edit.** `cost-trend` (not `cost --since`, which is now-relative and can't be re-derived after a rate edit) for all four config dirs, plus raw per-class token counts.
2. **Per stage:** `.venv/bin/pytest claude/.claude/`, `.venv/bin/ruff check claude/.claude/`, `scripts/list-shell-files.sh | xargs -0 .venv/bin/shellcheck` from a worktree.
3. **A1's six-site edit lands as one change**, verified by the new `DocCountFact` test failing if any site is missed, and by `test_nudge_handoff_near_context_cap.py` passing with the re-derived probe.
4. **Cost measurement:** re-run `cost-trend` 1-2 weeks after Stage A lands, at constant (pre-Sept-1) rates. On 2026-09-01, in one edit: update `_MODEL_BASE_INPUT_RATES["claude-sonnet-5"]` to 3.00, update `_PRICING_FETCH_DATE` to the date of the fetch confirming the new rates (not backdated), and remove the `claude-sonnet-5` special case from the `_MODEL_RATE_EXPIRES` comprehension (it is a derived dict, not a directly-settable constant — the current code branches `_SONNET_5_PROMO_EXPIRES` only for that one model ID). Do this promptly on or after September 1: `cost-trend` computes no staleness banner (unlike `cost`, which does), so a late edit silently prices Sonnet at the expired rate with no warning at all.
5. **Confirm the handoff writer's file permissions** before A1 ships, given A1 roughly increases handoff frequency: the target directory the nudge points to should not be world-readable, independent of this plan.
6. **Regression guard:** `reviewer-yield` unaffected by this plan (no routing items in scope) — re-run once as a sanity check that nothing else drifted during the edit window.

## Out of scope

- **A two-tier nudge (an earlier informational fire plus the existing hard one)**, raised as a concern that a single one-shot nudge moved earlier may fire at a point still easy to dismiss ("plenty of headroom left"), while a nudge left at the original point may already be too late for the most expensive sessions. Real concern, but it is a second implementation surface (independent one-shot markers, two injected strings to keep in sync) on top of A1's own six-site synchronization; bundling both in one change is the over-elaboration this plan's own review process already cut twice. Revisit once A1's distribution query shows whether the concern is material.
- **Hard Read-tool byte/line cap enforcement.** The real downside is silent truncation — a capped read that stops early looks identical to a complete one, the exact failure mode this repo's own `root-cause-analysis` skill warns about. A2's delegation measurement is the cheaper first step.
- **Model routing.** Opus is 15.7% of spend; `"model": "opusplan"` is correct.
- **Separating accounts that share one config dir.** Within reach, but a business-relationship decision, not a cost one.
