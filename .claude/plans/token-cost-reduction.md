# Token cost reduction: bound context growth

## Context

**Goal: cut per-PR API-list-price cost from a measured $128.02 as far toward the
$10-20 band as the available levers reach, without weakening any review gate.** On
current evidence that is roughly $30-40, not $10-20 — see Approach.

The engineer is at risk of losing a client over Claude Code spend. The working
hypothesis was that "prefix tokens" dominate. That is *mechanically* correct and
*diagnostically* misleading, and the difference decides what to fix.

Measured on PR #609 (`targeted-read-discipline`), the whole of which is one
session — re-derive with `transcript-analysis.py sessions --this-repo --paths`:

| Fact | Value | How verified |
| --- | --- | --- |
| Real API calls | **368** | dedupe by `requestId` over the session JSONL |
| Avg re-read prefix per call | **375,482 tok** | 138M cache-read tok ÷ 368 |
| Peak context | **551,352 tok**, monotonic, **never compacted** | trajectory scan of the JSONL |
| Static config prefix | **~16,000 tok (4.3% of avg prefix)** | CLAUDE.md ×3 + skills listing + agent roster + tool names |
| Accumulated conversation | **400,974 tok (95.7%)** | output 268,028 + tool_result 97,216 + user 35,730 |

Two conclusions follow, and both invert the intuitive fix:

1. **Trimming the static prefix is nearly worthless.** Deleting *every* byte of
   CLAUDE.md, the skills listing, and the agent roster would cut ~4% of cost.
   The prefix that is expensive is the conversation, not the configuration.
2. **Cost is quadratic in session length** *while nothing bounds accumulation*.
   Context grows ~1,090 tok per call and every subsequent call re-reads all of
   it, so total cache-read ≈ `N² × growth ÷ 2`. PR #609 never compacted, so it
   paid the full integral.

Composition of the ~401K accumulated conversation, by content-block type:

| Component | ~Tokens | Share of peak context |
| --- | --- | --- |
| Retained `thinking` blocks | ~147K | 27% |
| `tool_use` parameters (Edit/Write/Agent prompts) | ~109K | 20% |
| `tool_result` payloads | ~97K | 18% |
| User message text | ~36K | 7% |
| Visible assistant text | ~12K | 2% |
| Static config | ~16K | 3% |

Opus 5 and Sonnet 5 are documented **"keep-all"** models: every prior turn's
thinking block stays in context and is re-billed as input on every subsequent
request ([thinking-block-preservation-by-model](https://platform.claude.com/docs/en/build-with-claude/thinking)).
Nothing currently bounds that accumulation — `claude/.claude/settings.json` has
no `env` key at all, so auto-compaction sits at its model-tuned default near the
1M ceiling and never fires.

**A second finding blocks measurement itself.** `transcript-analysis.py` prices
**per JSONL record**, but Claude Code writes one record *per content block*
(thinking / text / tool_use), all sharing one `requestId` and carrying a
byte-identical `usage` dict. Verified: 760 assistant records → **368 distinct
`requestId`s; all 231 multi-record groups byte-identical**; the string
`requestId` appears nowhere in the 7,800-line script. Every cost figure this
workflow has produced — including the PR-body cost disclosure and the $250 that
prompted this work — is inflated **~2.1x**. PR #609's true cost is **~$124**,
not $260. Turn counts are inflated by the same factor.

Per the engineer: `claude-config` is the *lever* (stowed to every user and every
repo) but the cost *manifests* in other repos, so every change here must be
generic to all stow consumers, and validation must not come only from this repo.

## Approach

Attack the two terms of `cost ~= N x C_bar x rate`: cap `C_bar` (average context)
by ending sessions earlier, and cut `rate` with model routing. Leave `N` alone —
368 calls for a full plan->review->ship cycle is reasonable, and the engineer
elected to keep all review gates.

**The mechanism is handoff, not compaction.** An earlier draft made a lowered
auto-compaction threshold the dominant lever. The engineer rejects compaction for
this workflow, and on the substance they are right, though not for the reason
usually given: review markers are **not** the thing at risk. Markers are files
under `$CONFIG_DIR/*-markers/` keyed by `<repo-hash>.<session-id>`, matched on a
content hash of reviewed git state, explicitly designed to keep counting across
sessions — they survive compaction and handoff alike, and the gates re-hash the
working tree and fail closed regardless. What compaction actually destroys is
**review-narrative continuity**: which finding a reviewer raised, which were
addressed, what was decided and why. This pipeline is multi-round by construction
(plan-review -> code-review -> ready-for-review, with fix loops inside each), so a
lossy summary landing mid-loop causes re-litigation or silent skipping. Compaction
is also only weakly steerable — a persistent `# Compact instructions` section in
CLAUDE.md and `/compact <instructions>` both exist, so it is not strictly
"non-customizable," but they steer a summarizer rather than guarantee retention,
which does not meet a review gate's needs.

Handoff is strictly better here on both axes: it resets context to near-zero rather
than to a summary, and *what carries over is chosen deliberately* instead of being
summarized by a model. It is also already this repo's prescribed practice.

**The lever is therefore an existing mechanism that is measurably not working.**
`nudge-handoff-near-context-cap.sh` has three compounding defects, all verified:

1. **Threshold too high.** `THRESHOLD = min(40% of window, 360000)`. On Opus 5 and
   Sonnet 5 the window is 1,000,000, so the absolute cap governs at 360K — already
   past the point where a session has become expensive.
2. **Samples only at user-interaction boundaries.** It is registered on
   `UserPromptSubmit` and `Stop` only. An agentic stretch of many tool calls between
   two user turns cannot trigger it. Evidence from `.handoff-nudge.log`: four
   sessions were nudged at estimates of **521,321 / 521,618 / 445,559 / 436,162**
   tokens — 76K–161K *past* the 360K threshold, because the crossing happened
   mid-stretch.
3. **One-shot per session.** A `.handoff-nudge-fired.d/<session-id>` marker
   suppresses every later nudge. PR #609 peaked at 551K after its single fire at
   521K, so the tail ran unwarned. `docs/handoff-nudge.md:103` already names this
   ("the unwarned tail is now materially worse... Re-arming at escalating bands
   remains deferred pending frequency evidence"). The log above is that evidence.

**The target is probably not reachable, and the plan says so up front.** An early
draft multiplied `4.2x x 1.4x x 1.7x ~= 10x -> $12`. That was double-counting. A later
draft derated to 5-7x -> $18-25. Both assumed a lower fire threshold was available;
PR #605's U-shape analysis (see Phase 3) removes that assumption. What remains:

- **Session discipline.** PR #609 ran 368 calls to 551K — squarely in the U-shape's
  300+ turn / ~498K band at **3.14x** the cheapest zone. Running the same work in the
  40-80 turn band (~136K, 1.00x) is worth roughly **2.4-3x**, not 4.2x, because every
  additional session also pays the 3.55x rebuild ramp over its first five turns.
- **Model routing.** Opus cache-read is $0.50/MTok against Sonnet's $0.20 — up to 2.5x
  on affected turns, and the main thread (72.8% of this session's spend) billed 100%
  Opus. Blended, call it ~1.5-1.7x, pending Phase 5b establishing why `opusplan` did
  not route execution to Sonnet.

Against the corrected **$128.02** baseline, the traceable range is the product of
those two bands: `2.4 x 1.5 = 3.6x -> $36`, `3.0 x 1.7 = 5.1x -> $25`. So **$25-36**,
stated as the corners of the cited multipliers rather than a rounder-sounding figure
that no combination of them produces. **That is above the $10-20 target, and this plan
does not claim otherwise.** Phase 2 replaces the estimate with a backtest; if the
engineer needs $10-20, the remaining gap has to come from doing less work per PR —
fewer review rounds, smaller PRs, or fewer reviewer dispatches — which is a scope
decision the engineer has explicitly declined, not an efficiency one this plan can
deliver. Surface the number; do not quietly redefine the target to match it.

**Dropped from this plan: a committed global `effortLevel`/`CLAUDE_CODE_EFFORT_LEVEL`
default.** `claude/.claude/hooks/guard-settings-session-keys.sh` already blocks
committing a top-level `effortLevel` to `claude/.claude/settings.json`, with the
stated rationale that these keys "hold one machine's own state... committing them
ships your local state as the shipped config for every user." Its `GUARDED_KEYS_JSON`
check inspects top-level keys only, so writing the same value as
`env.CLAUDE_CODE_EFFORT_LEVEL` would produce exactly the harm the guard exists to
prevent while evading its literal key match. This repo already made this decision;
routing around it is not this plan's call. Phase 5a instead *closes* that gap.

**Alternatives set aside.** Trimming CLAUDE.md / skills / MCP surface — the
canonical "reduce context" advice — is quantified above at a ~4% ceiling and is not
worth the rigor it would cost. Cutting review gates was declined by the engineer and
is unnecessary: the reviewer fan-out is 24-27% of spend and runs on Sonnet at
roughly 1/4 the per-call cost of a main-thread turn.

### Assumption ledger

**Root problem:** a coding session's context grows unboundedly and is re-read in
full on every API call, making cost quadratic in session length.

**Givens** (fixed, outside this plan's reach):
- Keep-all thinking retention on Opus 5 / Sonnet 5 is an API-level per-model default
  with no Claude Code override — vendor-imposed. `[verified: platform docs, thinking-block-preservation-by-model]`
- Prompt-cache TTL cannot exceed 1 hour by any exposed setting; an idle gap past it
  forces a full cache-write rebuild — vendor-imposed. `[verified: code.claude.com/docs/en/prompt-caching]`
- A hook observes only the events it is registered for, and the harness fires no
  event between tool calls within an agentic stretch that is free of per-call cost —
  platform boundary on *cheap* mid-stretch sampling, not on sampling as such.
  `[verified: hook registration in claude/.claude/settings.json; PostToolUse exists but fires per tool call]`
- All review gates stay. `[engineer-verified]`
- The handoff fire point is not reducible to a single token threshold; breakeven
  depends on work remaining, which a token-threshold hook cannot observe.
  `[verified: .claude/plans/handoff-boundary-decision-rule.md (PR #605), U-shape table over 396 transcripts / 60 days]`
- `ABS_CAP` stays at 360000. Lowering it was evaluated at 300000 and rejected on
  evidence in PR #605; this plan does not reopen it.
  `[verified: same plan, "Out of scope" section]`
- Compaction is not an acceptable mechanism in this workflow; handoff is the
  prescribed context-reset primitive. `[engineer-verified]`

**Mechanisms:**
- *Harden the existing handoff nudge* (lower threshold, re-arm at escalating bands,
  widen sampling) — `anchors: root`. Lighter primitives considered and rejected:
  (a) leave the nudge as-is and rely on operator discipline — refuted by the log,
  which shows four sessions running 76K-161K past threshold and PR #609 continuing
  to 551K after its single fire; (b) lower only the threshold without re-arming —
  insufficient on its own, since the one-shot marker still leaves the entire tail
  unwarned, which is the defect `docs/handoff-nudge.md:103` already names.
- *`requestId` dedupe* — `anchors: root`. Not a saving; a precondition for verifying
  any saving.

**Assumptions:**
- A threshold in the 120-180K range materially lowers `C_bar` without forcing
  handoffs so often that re-establishment cost dominates. `[unverified]` — Phase 2
  backtests it against the recorded corpus and picks the value; nothing ships before that.
- The $30-40 projection above. `[unverified]` — modelled from #605's U-shape bands
  plus the measured Opus/Sonnet rate gap; explicitly superseded by Phase 2's backtest.
- Handoff re-establishment cost is lower than the context it avoids. `[unverified]`
  — Phase 2 must model it explicitly, since a too-low threshold inverts the trade.
- The subagent share of the 2.1x inflation matches the main thread's. `[unverified]`
  — verified on main-thread records only. Affects the $124 figure's precision, not
  any conclusion.
- The operator acts on the nudge reasonably promptly once it fires. `[unverified]`
  — and directly contradicted by today's evidence: four sessions ran 76K-161K past
  threshold and PR #609 continued to 551K after its single fire. Phase 2's backtest
  models a *perfect-compliance ceiling*, not the expected outcome; Verification item
  8 measures realized compliance via `handoff-ratio` rather than assuming it.
- `opusplan` did not route implementation to Sonnet in PR #609 — the main thread
  billed 100% Opus. `[unverified]` — cause not established; Phase 5b investigates
  before changing the setting.

## Phases

**Prerequisite — resolve the `cost-trend-ledger` collision first.** That branch is
**not** dormant. Verified: `git log main..cost-trend-ledger` shows two commits, and
its tip is a **1,406-line implementation** of
`claude/.claude/scripts/transcript-analysis.py` — the exact file Phase 1 edits —
plus 551 lines of new tests and a **`docs/cost-ledger.md` that already exists**
(48 lines). It extracts `_compute_cost_trend_data` and refactors the same pricing
call sites Phase 1 targets. Decide the disposition before writing any code: land
`cost-trend-ledger` first and rebase this work onto it, or explicitly supersede it.
Phase 6 must not create `docs/cost-ledger.md` — it already exists there. Cite that
branch by tip SHA resolved at implementation time, not from this plan: an earlier
draft cited a SHA that had been amended away, and a draft before that described the
branch as "plan-only, zero implementation," which was false.

**Prerequisite — verify the branch base before trusting any line citation.** This
plan's line numbers are verified against `main` at the time of writing. This work
was itself branched from a `main` that had already advanced three commits (two of
which rewrote `nudge-handoff-near-context-cap.sh` from 214 to 413 lines), and a
review round consequently read a stale copy and reported correct citations as
wrong. Re-verify each cited line against the branch's actual base before editing;
`git log <base>..main` is the check.

**Phase 1 — Fix the 2.1x cost accounting. Ships first, alone, immediately.** It is
a standalone bugfix that depends on nothing else in this plan, and it is what gives
the engineer a truthful number *today* — correcting the alarming $260/$250 headline
to ~$124 — instead of after a backtest completes. Every later phase's numbers, and
Phase 2's stop/go decision, are untrustworthy until it lands.

**Phase 2 — Backtest the threshold before shipping it.** Runs *after* Phase 1,
because its dollar output is inflated ~2.1x otherwise and would drive a wrong
decision at the one gate this plan hinges on. Reuses Phase 1's dedupe helper — it
must not reimplement a throwaway copy. Replay recorded session JSONL through a
**re-arm band spacing**, which is the only output Phase 3 can act on now that the fire
point is fixed at `360000`. Candidate spacings (e.g. +40K / +80K / +120K past the first
fire) replayed against each session's *actual* per-call context-growth trace, reporting
predicted `C_bar` and predicted dollars per spacing. Two constraints on the model:
- It must charge each modelled handoff a **re-establishment cost** — #605 measures the
  fresh-session ramp at 3.55x over turns 0-5, so a model that ignores it will recommend
  handing off far too often.
- It must report a **compliance-realistic** figure alongside the perfect-compliance
  ceiling. The ledger flags operator compliance as `[unverified]` and today's evidence
  contradicts it, so a ceiling-only number would overstate the benefit.

**Session-split threshold backtesting is explicitly dropped from Phase 2's scope** — it
would measure a knob this plan no longer turns. Only the re-arm leg is replay-testable;
model routing is not, and the report must say so.

**Phase 3 — Re-arm the handoff nudge. Do NOT change the threshold.** One change to
one hook: replace the one-shot-per-session suppression with re-arming at escalating
bands. `ABS_CAP` stays at `360000`.

An earlier draft proposed lowering it to ~150K. That is wrong, and this repo already
knows why. `.claude/plans/handoff-boundary-decision-rule.md` (PR #605) measured a
U-shaped cost-per-work curve over 396 transcripts / 60 days: turns 0-5 cost **3.55x**
the optimum because a fresh session pays a rebuild ramp; turns 40-80 (~136K context)
are cheapest at **1.00x**; turns 300+ (~498K) cost **3.14x**. Its stated conclusion is
that no threshold is the right instrument at all — *"That point is not a single
context number"*, because breakeven is `remaining_turns ~= ramp_excess / (rate - 1)`,
a function of context **and** work remaining, and *"at 199k with 135 turns left,
restarting wins; at 199k with 37 turns left, continuing wins. Same context, opposite
answers."* Three further facts kill the retune:
- **~150K sits in the cheap zone**, not a costly one. (#605's bands are indexed by
  *turn*, not token: turns 40-80 average ~136K context at 1.00x, turns 80-150 average
  ~199K at 1.31x. Interpolating to a 150K *token* figure puts it between those two —
  cheap either way.) Firing there would push handoffs into the region #605 identifies
  as *premature*, trading a cheap rate for the 3.55x rebuild ramp.
- **A 360K -> 300K retune was already proposed and rejected on evidence** in that same
  plan: the crossover falls inside the 300-400K bucket (midpoint ~350K, which brackets
  360000); session-share showed 300000 merely *permissible*, not better; and a hook
  cannot see work-remaining, so tuning it to a measured crossover "claims a resolution
  the hook cannot act on."
- **150000 has never existed in this repo** — no commit, plan, or doc. The threshold
  history is 120000 flat (#330) -> 60% of window (#561) -> 40% (#579) -> min(40%, 360000)
  (#593). The remembered "150k" most likely echoes #605's measured *median context at a
  plan-review boundary, 152k* — a description of where sessions already sit, not a
  proposed fire point.

**Re-arming fixes defect 3 only, not defect 1 or 2 — say so plainly.** The four logged
overshoots (fired at 521K/521K/445K/436K against a 360K threshold) are caused by
defect 2, the `UserPromptSubmit`/`Stop`-only sampling, which Phase 3 deliberately does
not touch (see Out of scope). So the 76K-161K *initial-detection lag persists*. What
re-arming removes is the unwarned tail *after* that first, correctly-late fire — which
is where PR #609 spent its most expensive stretch. For a session that ends shortly
after its first fire there may be no further user-turn boundary for a band to catch,
and Phase 3 will do nothing at all. This is a partial fix, and the plan does not claim
otherwise.

Re-arming is the intervention that survives, and it is **complementary** to #605 rather
than a fourth retune: one of #605's own objections to lowering the cap was that an
earlier single shot "leaves more post-fire runway" unwarned. Re-arming removes that
runway without touching the threshold, and closes the deficiency
`docs/handoff-nudge.md:103` explicitly defers "pending frequency evidence" — evidence
this plan now supplies (four sessions fired 76K-161K past threshold, then ran on
unwarned; PR #609 reached 551K after its single fire at 521K).

**Phase 4 — Phase-boundary `/clear` and pre-idle `/compact` guidance** (one line
each in `claude/.claude/CLAUDE.md`).

**Phase 5a — Close the `effortLevel` guard gap.** Independently shippable and
revertible; carries no dependency on any cost measurement. Extend
`guard-settings-session-keys.sh` so the nested `env.CLAUDE_CODE_EFFORT_LEVEL` and
`env.ANTHROPIC_MODEL` forms cannot bypass the top-level guard — a gap this plan's
own earlier draft found by walking into it.

**Phase 5b — Model routing investigation.** Establish *why* PR #609's main thread
billed 100% Opus under `model: opusplan` before changing any routing setting. This
is an investigation with no guaranteed code change; keeping it separate from 5a
keeps a security-control fix from being gated behind a cost question.

**Phase 6 — Measurement.**

## Critical files

| Path | Change |
| --- | --- |
| `claude/.claude/scripts/transcript-analysis.py` | **Phase 1.** `_price_turn` is called from **four independent loops** — `cmd_audit_routing` (:4049), `_cost_report` (:4856), `_context_distribution_report` (:5199), `_cost_trend_report` (:6263). There is **no shared turn iterator**; a new shared dedupe helper must be introduced and applied at each site, preserving each site's own filters (branch, `--since`, ISO-week bucketing, Opus-only). `cmd_subagents` (:1089) does not call `_price_turn` and counts per-record model family directly — it needs the same fix separately. **Dedupe key semantics: a missing/null `requestId` must never merge with another missing one** — each such record stands alone. (Measured: 17 of 53,485 sampled real records lack `requestId`; all are `<synthetic>` API-error records with zero usage, so dollars are unaffected but turn counts are not.) |
| `claude/.claude/scripts/transcript-analysis.py` — `cmd_audit_routing` | **Phase 1, explicit design decision required.** `_classify_opus_turn` (:3723) classifies on a *single record's* content block, and the judgment-span state machine (:4059) mutates mid-loop. Blocks of one `requestId` group can classify differently while sharing one dollar figure, and naively keeping only the first record per group would drop Skill/ExitPlanMode `tool_use` detection carried on a later record, breaking plan-mode span tracking. Decide and document how block-level signals combine per group *before* attributing dollars; do not let the dedupe silently pick a block. |
| `claude/.claude/scripts/tests/test_transcript_analysis.py` | **Phase 1.** (Corrected path — not `hooks/tests/`.) The file has **zero** occurrences of `requestId`; fixture builders `_asst` / `_priced` / `_opus` / `_priced_opus` (~400 call sites, ~85 dollar-bearing assertions) set none. A dedupe that merges absent keys collapses most multi-turn fixtures to a single turn and silently rewrites baselines. Required cases: missing/null `requestId` is a strict no-op; single-record request; sidechain records sharing a key; key scoped per-session not global; **turn counts asserted, not just dollars** — the byte-identical-`usage` invariant is what is being guarded. |
| Backtest module (new, path TBD at Phase 2 — name it before implementing) | **Phase 2.** Consumes Phase 1's dedupe helper; must not reimplement it. Produces the threshold recommendation and the measured projected saving. |
| `claude/.claude/hooks/nudge-handoff-near-context-cap.sh` | **Phase 3.** **Leave `ABS_CAP` at `360000` (:132) — do not retune it**; see Phase 3 for why. Replace the one-shot `.handoff-nudge-fired.d/<session-id>` existence check (`already_fired` :270-271, `MARKER_DIR` :366, `FIRED_MARKER` :388-389, `touch` :409) with a **single overwritten record holding the last band crossed** — not one file per band, which would multiply per-session files and turn an integer compare into an enumeration. Read it with shell builtins, not a `jq` subprocess (an unwrapped subprocess would invalidate the existing latency bound; if `jq` is used it needs the same `timeout 2` wrapping as :104/:199). Follow the hook's **write-failure precedent, not its read-failure one**: a corrupt or unreadable band value means "no prior band" and fires again — worst case a repeated nudge, never a stuck hook. The existing 30-day mtime sweep already covers the record unchanged, so the known `claude -p` marker leak does not worsen. **Must not introduce a `decision` key on any `Stop`-registered path** — "escalating bands" must not tempt a blocking final band. Keep `THRESHOLD = min(pct, abs)`, the informational-class contract, and the global disable switch. |
| `claude/.claude/hooks/tests/test_nudge_handoff_near_context_cap.py` | **Phase 3.** Test file already exists. Add cases: still fires at `360000` (unchanged); **re-arms at the next band rather than staying suppressed**; does not re-fire within a band; disable switch still wins. The re-arm case is the one guarding the actual defect. |
| `docs/handoff-nudge.md` | **Phase 3.** Line 103 documents the one-shot tail as a known deferred issue. Update it to record what shipped and the evidence that closed it. This is a current-behavior description, not a historical record, so it is in scope to edit (CLAUDE.md Axis 3). |
| `claude/.claude/hooks/guard-settings-session-keys.sh` | **Phase 5a.** Appending `"env.CLAUDE_CODE_EFFORT_LEVEL"` to `GUARDED_KEYS_JSON` **will not work**: `guarded_value` does `$settings \| has($key)` against top-level keys only, so a literal dotted key never matches a value living at `.env.CLAUDE_CODE_EFFORT_LEVEL` and the check silently never fires. Restructure `guarded_value` for path traversal (split the key, `getpath`). **No test file exists for this hook today** — an untested control extension is indistinguishable from a no-op, so add allow/deny cases asserting a nested `env.CLAUDE_CODE_EFFORT_LEVEL` / `env.ANTHROPIC_MODEL` commit is actually blocked. |
| `claude/.claude/CLAUDE.md` | **Phase 4.** Two lines, drafted as literal text and run through `ai-instruction-and-memory-files` before implementation — this file is itself always-loaded prefix. |
| `docs/cost-ledger.md` | **Phase 6.** **Already exists on `cost-trend-ledger` (48 lines) — do not create it.** Its schema there is a weekly aggregate and lacks the per-PR metric this plan's own verification needs; `usd_per_merged_pr` is that plan's Phase 2. Add that column to the existing file, on whichever branch the prerequisite decision settles on. |
| `.claude/plans/cost-trend-ledger.md` | **Phase 6, disposition.** This file does not exist on `main`, so an agent told to "amend it" from a branch cut off `main` would write a fresh file and silently lose the content needing revision. If it is to be edited here, pull it across first (`git checkout cost-trend-ledger -- .claude/plans/cost-trend-ledger.md`). The larger question the prerequisite settles: what happens to that branch's unmerged 1,406-line implementation — merge, rebase, or supersede. |

**Reuse:** `cost-trend` (#569) already computes weekly cost/context/Opus-share —
Phase 6 records its output, it does not reimplement it. The `_cost_report`
docstring's "sidechain turns priced exactly once" is a **different** mechanism
(dedupe of subagent *files*); the `requestId` fix is orthogonal and must not be
folded into it. `nudge-handoff-near-context-cap.sh` already owns a
context-threshold notion; reconcile the compaction threshold with it.

## Verification

1. **Phase 1 correctness.** `cost --this-repo --branches targeted-read-discipline --summary`
   must report ~$124, matching the independent dedupe already run against the raw
   JSONL (main thread $194.14 → $92.31). State explicitly in the PR body that
   reported **turn counts drop ~2.1x too**, since that is a second visible number
   change. Full `../../../.venv/bin/pytest claude/.claude/` + `ruff` + `shellcheck`.
2. **Phase 1 before Phases 2 and 6.** Phase 2's backtest and Phase 6's first ledger
   row both consume priced output; neither may run on 2.1x-inflated numbers. A wrong
   Phase 2 result drives a wrong stop/go decision, and a wrong ledger row becomes a
   preserved historical record that cannot be quietly corrected later.
3. **Efficacy, normalized.** Measure the 3 PRs after Phases 3–5 on `$ per API call`
   and `C̄`, not raw PR dollars — raw dollars is the most confounded of the three.
   **At least one of the 3 must be in a non-`claude-config` repo**, since every
   figure in this plan comes from meta-work on the very script being measured.
4. **Re-read churn, measured not eyeballed.** Define the metric as *distinct file
   paths `Read` a second time after a compaction boundary with no intervening write
   to that path*, computed mechanically from session JSONL, compared against a
   pre-compaction baseline rate. A binary "did `/code-review` pass" check cannot
   detect a partial, silent churn increase — which is exactly the failure mode that
   would erode the compaction saving without tripping any guard. Include at least
   one subagent-heavy session, since the compaction override applies to subagents too.
5. **Review-quality floor, distinct from gate hit-rate.** `review-trace` hit-rate
   (52–62% baseline) measures whether gates *fire*, not whether they *reason well* —
   a degraded reviewer still writes a marker. Track `reviewer-yield`'s findings rate
   as a second tripwire alongside it.
6. **Settings-merge semantics.** Before claiming per-repo opt-out works, verify
   against the vendor settings-precedence docs whether an `env` block merges per-key
   or replaces wholesale across user/project/local scopes. Currently unverified.
7. **Rollback and blast radius.** Phase 3 changes a hook every stow consumer runs.
   Reverting is a one-line threshold restore, but propagation is pull-based and
   non-atomic — a consumer who already pulled keeps the new behavior until their own
   next `git pull`. The nudge is informational (it cannot block work) and the
   existing global disable switch still applies, which is what bounds this blast
   radius; no new sentinel is introduced.
8. **Nudge-firing regression.** After Phase 3, `.handoff-nudge.log` entries must show
   more than one entry for sessions that keep running past the first fire. It must
   **not** expect `est` values nearer `360000` — Phase 3 does not touch the
   detection lag (see below), so first-fire overshoot is expected to persist. `handoff-ratio` (already in
   `transcript-analysis.py:6301`) must not fall.

## Where a handoff is safe to take

The nudge can fire mid-review-loop, so this needs an answer rather than an
assumption. It already has one: the handoff artifact carries review-loop state
across the reset by construction — `handoff/SKILL.md` §2.5 records incomplete
prerequisites and what was mid-flight, §5 records gates and markers, §3.5 records
pending engineer authorization, and §6 records deferred decisions. Markers
themselves are disk-backed and content-addressed, so they keep counting across the
session boundary. **No additional timing guidance is therefore needed, and Phase 3
adds none** — this is the substantive advantage of handoff over compaction, whose
lossy summary carries none of that structure. If a handoff is taken mid-loop and
the resumed session re-litigates a settled finding, that is a defect in the handoff
artifact's §2.5/§6 completeness, not a reason to delay the handoff.

## Residual risk

The cost thesis now rests on an **informational** hook that cannot block and that
the operator can ignore — and today's evidence is that it *was* ignored. Hardening
it raises nudge frequency; it does not guarantee proportional compliance, and
nothing stronger is available without breaking the informational-class contract the
engineer keeps deliberately. This is a real ceiling on the plan, named here rather
than left implicit alongside the "model routing is not replay-testable" caveat.
Verification item 8's `handoff-ratio` is the check that would expose it.

## Out of scope

- **Trimming CLAUDE.md / skills / agent roster / MCP surface.** Quantified at a ~4%
  ceiling. Deliberately declined, not overlooked.
- **Cutting or merging review gates.** Engineer-declined; also not cost-justified.
- **A committed global `effortLevel` default.** Blocked by an existing repo control
  whose rationale directly covers this case; see Approach. It remains available to
  the engineer as a per-machine `CLAUDE_CODE_EFFORT_LEVEL` env var they set
  themselves — documented as a recommendation, never committed.
- **A per-agent effort-override field** so reviewer agents could be exempted from a
  global effort reduction. No such frontmatter field exists today (only `model:`);
  inventing one is its own design change and is moot once the global default is dropped.
- **Lowering the auto-compaction threshold.** Declined: the engineer rejects
  compaction for this workflow, and review-narrative continuity is a real casualty
  of a lossy mid-loop summary. Handoff replaces it as the context-reset primitive.
- **Widening the nudge's event registration to `PostToolUse`.** Technically
  available (`PostToolUse` is already registered for other hooks), so this is a
  deliberate decline, not a platform limit: a matcherless `PostToolUse` fires on
  every tool call — 3-10x+ invocations per turn against today's 2 — and almost all
  of them do no new work because `.message.usage` has not changed. Phase 3 therefore
  does **not** widen sampling; it accepts the mid-stretch blind spot and compensates
  with re-arming alone. If a future revision does widen it, the
  hook's internal `HOOK_EVENT` `case` (:67-70) must gain the new arm explicitly or
  the event silently mislabels as `UserPromptSubmit`.
- **The idle-gap cache rebuild** (two events, ~910K tokens at cache-write rates,
  ~$9 of PR #609). Real but vendor-capped at a 1h TTL — addressed only by the
  Phase 4 guidance line, not by a mechanism.
- **Re-deriving the subagent share of the 2.1x inflation.** Flagged in the ledger;
  affects a headline figure's precision, no conclusion.
