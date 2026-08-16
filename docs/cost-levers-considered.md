# Cost levers considered

A register of cost-reduction levers this repo has investigated and closed —
adopted, rejected, superseded, or declined — across prior plans. Six plans
each accumulated their own rejected-alternatives section; this page
consolidates them so a seventh plan doesn't re-measure ground already
covered. Sibling to [`design-decisions.md`](design-decisions.md), which
records the shorter-form architectural decisions this repo has made; this
page is scoped specifically to *cost* levers and keeps the verdict plus the
measured reason, not the full investigation.

Most entries name their source plan under `.claude/plans/`; a lever measured
and closed without one names the session date instead. A section's table may
carry a dated follow-up paragraph that supersedes a row — read to the end of a
section, not just its table. Merged plan files
are read-only historical records (see the repo CLAUDE.md, Axis 3) — this
register doesn't restate their content, it indexes it.

## From `token-spend-reduction.md` — "Cut Claude Code token spend"

| Lever | Verdict | Measured reason |
|---|---|---|
| Handoff-nudge threshold as a flat percent of context window | Partially adopted, later superseded | No data justified a specific percentage; the existing ≥200k dollar-share bucket was too coarse. Superseded by `absolute-token-handoff-threshold.md`'s absolute-cap approach. |
| Subagent-delegation byte measurement (extend the `subagents` subcommand) | Adopted (as a measurement tool) | 44% of turns are subagent turns, and roughly 90% of spend is prompt-prefix (cache-read), not newly generated work — worth instrumenting, not worth acting on without more data. |
| Cache-TTL selection (5-minute vs. 1-hour) as a configurable lever | Rejected, not deferred | TTL is set per-request by the API caller via `cache_control` — nothing in `settings.json`, hooks, or env vars exposes this field, and every account on this machine shares the identical stowed `settings.json`. There is no lever here to pull. |
| Skipping cumulative `/code-review` for single-commit PRs | Rejected (falsified) | `require-code-review.sh` hashes the staged increment, not the commit's resulting content — `git commit --amend` reviews only the amendment, leaving the rest of the commit's content unreviewed. |
| Capping reviewer-ownership fan-out in `/code-review` | Rejected | No cap exists today, and a checklist-item-touch filter would exclude exactly the reviewer positioned to catch cross-lane bleed. |
| Gating `skill-fidelity-reviewer` on diff file paths | Rejected (falsified twice) | Orthogonal to what the reviewer actually checks; `/ready-for-review` already implements the intended fix by a different mechanism. |
| Retiring `staff-analytics-engineer` from auto-routing | Rejected | `reviewer-yield` measurement: 6 dispatches, 6 found, 0 zero-finding, 0 unclassified — flagged concerns in every dispatch sampled. "Findings" is a documented lower bound on value, not a yield signal to cut against. |
| Model routing (reduce Opus usage) | No change needed | Opus measured at 15.7% of spend; the existing `model: opusplan` routing was already correct for that share. |

**2026-08-15 follow-up:** the "nothing in `settings.json`, hooks, or env
vars exposes this field" claim above is outdated — `ENABLE_PROMPT_CACHING_1H`
and `FORCE_PROMPT_CACHING_5M` are real, documented environment variables
(`code.claude.com/docs/en/prompt-caching`) that select TTL per session.
Verdict unchanged (rejected): main-thread 5-minute-tier writes are 96–97%
concentrated in idle gaps under 5 minutes on every account checked (personal-
subscription, small-subscription-client, API-key-client), where the pricier
1-hour breakpoint adds cost with no avoided-rebuild benefit — do not set
either variable.

## From `absolute-token-handoff-threshold.md` (PR #593) — "Re-unit the handoff nudge"

| Lever | Verdict | Measured reason |
|---|---|---|
| Percent-of-window threshold → `min(pct, absolute_cap)` | Adopted | 18 fires observed since PR #579, all on 1M-window models, mean 655,477 tokens (min 433,055 / max 879,994) — roughly $0.20 charged on every subsequent call at $0.30/MTok cache-read before the cap kicked in. |
| Delegation-discipline pilot (recurring measurement of delegation adherence) | Closed, not deferred | 71.2% of tool-result bytes already land in sidechains, and the effect size was unmeasurable in the available window: ISO-week granularity, ~25 days of history, >3x noise floor, and a September 1 repricing inside any window powered enough to detect it. |
| Sonnet→Haiku routing for verbose-output absorption | Closed on prior evidence | The retired Haiku check-runner agent (see [`case-studies/check-runner.md`](case-studies/check-runner.md)) already measured "cheap model absorbs verbose output" dead across six documented incidents. |
| Repo-set default model change (`settings.json`) | Declined deliberately, kept in reach | Changing a shared model default to simplify one threshold calculation inverts the priority — the default should serve model-routing intent, not be reshaped around a nudge's math. |
| Static session-prefix trimming | Named, not solved | ~15,700 of 177,761 tokens (~8.8%) is a fixed floor; ~1,950 tokens was identified as a clean, isolated win deserving its own change — left as a named follow-up, not implemented here. |
| Dollar-per-byte allocation model (derive a causal per-byte price from the main/sidechain split) | Declined | The main/sidechain split is an observed association across an uncontrolled mix, not a causal per-byte price — see the delegation-ratio note in [`design-decisions.md`](design-decisions.md). |

## From `noble-sauteeing-dream.md` (GH-556) — "Per-model context-window threshold for the handoff nudge"

| Lever | Verdict | Measured reason |
|---|---|---|
| "~25% cheaper per turn than waiting for auto-compaction" as the nudge's justification | Rejected, removed as unverifiable | Cold-start turn cost measured mean $0.153 / median $0.126, vs. mean $0.086 for continuing turns in the 200–300k window — a handoff is not a flat win at the point the nudge fires. |
| Models-API-backed context-window cache, or shelling out to `transcript-analysis.py`'s price table | Rejected as heavier than the bug requires | Adds a `python3` subprocess to a <500ms hook hot path, or needs credentials plus its own staleness policy, to fix a lookup-table bug. |
| Runtime staleness banner in hook output | Rejected | Would spend tokens on a maintainer-facing message every session and make hook stdout non-deterministic. |

## From `handoff-boundary-decision-rule.md` — "Plan-boundary continue-vs-handoff decision rule"

| Lever | Verdict | Measured reason |
|---|---|---|
| Retuning `HANDOFF_NUDGE_ABS_CAP` from 360,000 to 300,000 | Rejected, dropped | The measured cost-per-work crossover falls inside the 300–400k bucket (~350k midpoint), bracketing the current value rather than favoring 300,000. Cost-per-work buckets: 250–300k at 1.5x, 300–400k at 2.1x. A 45.5% session-share at 300,000 shows the value is merely permissible, not better. |
| Automating the continue-vs-handoff decision via a hook | Rejected | A hook cannot see how much work remains, which is half of the breakeven calculation. |
| "Always hand off before implementation" as blanket advice | Rejected as mispriced | Net-negative at the median plan boundary: 141 sessions, median context 152k (continued) vs. 175k (handed off) — inside a 1.00–1.31x cost band, against a 3.55x fresh-session ramp cost for turns 0–5. |

## From `hashline-edit-format.md` — "Hashline edit format: evaluate, decline, and document"

Full empirical record: [`case-studies/hashline-edit-format.md`](case-studies/hashline-edit-format.md).

| Lever | Verdict | Measured reason |
|---|---|---|
| Adopting Stencil's hashline content-hashed-line edit format, replacing `Edit`/`Write` | Rejected, declined | Benchmarked against `patch`, not `str_replace` (Δ REPLACE +3.3 on Sonnet 4.5 only, Opus unbenchmarked). `str_replace`-mechanical failures are 0.77% of `Edit` calls (57/7,428) in this repo's corpus; eliminating anchoring tokens saves only 0.67% of total assistant output tokens, with a 0.08% benefit ceiling (6/7,428). Governance hooks already block 4.4x more edits (252) than the edit format itself would fix (57). |
| Fixing the redaction-hook edit-failure defect (6 failures) by skipping mutation on `Read` | Rejected | Removing `Read` from the redaction matcher opens a real unredacted-credential residual for a 0.08% edit-failure gain — not a favorable trade. |
| `PostToolUse Edit` hook narrating the self-inflicted redaction failure | Rejected | A layer whose only purpose is to explain a failure the previous layer creates — a compounding-defensive-layers tell, not a fix. |

## From `subagent-delegation-debug-probe.md` — "Delegate debug-investigation reads to stop context-limit handoffs"

| Lever | Verdict | Measured reason |
|---|---|---|
| Revive check-runner-style delegation for check-running itself | Rejected | Inline checks are cheap (~6K tokens, harness-truncated) against a ~61K-token investigation read — the check output was never the cost driver. |
| Instruct the parent to write tests to a file before authoring | Rejected | Test-authoring content measured at ~2.3K characters — not a problem this needs to solve. |
| Delegate debug-investigation reads (read-only) to a subagent | Adopted | Investigation reads measured ≈244K characters (~61K tokens), roughly 10x the check output (~6K tokens); a multi-session chain compacted three times and handed off before shipping because of this read weight. |
| Write-capable debug-and-fix agent | Rejected as the heavier primitive | Reintroduces the model-agency failure class documented in check-runner Incident 1 (see [`case-studies/check-runner.md`](case-studies/check-runner.md)) — an agent that can edit files while looking at a failing check will attempt to fix it, defeating the separation delegation exists to provide. |

## From `context-composition-analyzer.md` — "Context composition analyzer"

| Lever | Verdict | Measured reason |
|---|---|---|
| `PostToolUse` hook appending a ledger row per ingestion | Rejected | Can only observe tool results — the ~27% minority of context growth, not the ~73% majority (`read-scope --since 30d` measures tool-result content at roughly a sixth of cumulative prompt-token growth, all tool results combined at roughly a quarter). Produces nothing until new sessions accumulate, and hook payloads carry no token counts, so it would estimate anyway — no advantage over a retroactive scan. |
| Extending `read-scope` to cover non-tool categories | Rejected | Its cohort model is `Read`-call-specific; every non-tool category (conversation-history replay, assistant text/thinking, the static system prefix) falls outside its frame. |
| `context-distribution` as the host subcommand | Rejected | Buckets sessions by peak context without decomposing what filled it — a different question. Its redaction pattern (no redact map, no per-project label) was adopted anyway; see `docs/transcript-analysis.md`'s "Corpus scope" section. |

## From `pin-explore-to-sonnet.md` (this plan) — Step 1's superseded earlier draft

| Lever | Verdict | Measured reason |
|---|---|---|
| Flip `claude/.claude/settings.json`'s shared `model` default from `opusplan` to `sonnet` | Superseded, dropped after four revisions | Collided with `guard-settings-session-keys.sh`, which hard-blocks any Claude-Code-authored commit touching that key with no in-session bypass, requiring the engineer to commit it manually outside the harness. Also touched 11 sites across 5 files asserting "opusplan is the default," needed a new CHANGELOG entry and an escalation wrapper for Opus-during-planning users, and only fixed `Explore` for sessions already anchored to Sonnet — not one started with `--model opus`. Dropped in favor of the agent-owned override in `Explore.md`, which needs none of the above. |
| `ANTHROPIC_MODEL=sonnet` environment variable | Rejected, sibling of the above | Sits above the `model` setting in Claude Code's own precedence order and only fixes one machine, not the repo-owned agent. |
| `CLAUDE_CODE_SUBAGENT_MODEL` global override | Rejected, sibling of the above | A global override `docs/auto-mode.md` already advises against — it would force every subagent to one model, not just `Explore`. |

**2026-08-11 follow-up, confirmed 2026-08-12:** the first row's "needs none
of the above" claim is partly falsified — `Explore.md`'s `model: sonnet`
override is not honored during harness plan mode (92/95 dispatches resolved
to Opus anyway, ~75% of `Explore`'s corpus), confirmed independent of the
`opusplan`-default confound by a falsification test (178/178 non-plan-mode
dispatches with an Opus-anchored parent still honored their pin) — see
[`case-studies/plan-mode-model-resolution.md`](case-studies/plan-mode-model-resolution.md).
Verdict unchanged: the `opusplan`-flip complications this row recorded are
unaffected by this finding; reopening the flip is a separate decision, not
made here.

**2026-08-14 follow-up:** `plan-mode-workflow-discipline.md` reopened this
flip and landed it — resolving the question the note above left open. The
falsification test cited there does more than confirm the pin holds outside
plan mode: it removes this row's own cost-lever rationale for the flip,
since the flip changes nothing about plan-mode-honored `model:` pins — the
override that breaks them is gated on `permissionMode`, not on any model
setting. `plan-mode-workflow-discipline.md` ships the flip instead as a
coherence fix: once agent-initiated planning is kept out of harness plan
mode entirely (that plan's actual fix, via an advisory `CLAUDE.md` bullet
and an `EnterPlanMode` deny rule), `opusplan`'s Opus half is reachable only
through a path the agent no longer takes, so the default no longer needs to
advertise it. Next person proposing this flip as a cost win: the rationale
above is refuted, not merely stale — reuse the coherence framing instead.

The "11 sites across 5 files" figure above is also superseded — that count
was taken during an earlier PR's editing process rather than measured
against a stable point-in-time state.
`plan-mode-workflow-discipline.md`'s own count, run against the tree it
modified, found five files carrying a live "opusplan is the default"
assertion in editable prose (README.md, `claude-auto.sh`, `settings.json`,
`docs/auto-mode.md`, `docs/scripts.md`), plus four further hits in Axis-3
preserved records left untouched. The commit-gate collision this row
recorded is still real and still resolved the same way (the engineer
commits the one-line flip manually) — only the cost-lever framing and the
"separate decision" status change.

## From a 2026-08-15 session measurement — "Git-diff output in main-session context"

Whether sessions load diffs into the main context to do work a script could do
deterministically, or work a cheaper subagent could absorb.

Figures are a one-off scan of main-thread `Bash` results across every Claude
Code account on this machine — 587 sessions, 77,515 turns, a 30-day window
ending 2026-08-15 — not a rerunnable script, so treat the precision
accordingly. "Amplification-weighted" means each result's tokens multiplied by
the turns remaining in its session, since a tool result is re-sent on every
subsequent turn.

| Lever | Verdict | Measured reason |
|---|---|---|
| Delegating the diff read in `/code-review` to a cheaper subagent | Rejected | Diff-family output is 1.25% of cumulative main-context tokens amplification-weighted, against `Read` output at 7.95x that volume. The diff `/code-review` reads is the artifact under review, which `subagent-delegation`'s own frontmatter already excludes from delegation by name — delegating it delegates the review. |
| Replacing in-context diff reads with deterministic scripts | No change needed | Already the case wherever it applies. Every marker and hook hash computes `git diff --cached \| sha256sum` inside a subprocess with output redirected to a file, so no diff bytes reach the model; only a deny message can surface. The seven `staff-*` agents and `ciso-reviewer` all carry `Bash` and re-fetch the diff inside their own contexts, so the parent never relays to them. |
| File-path handoff of diff text to the reviewers that lack `Bash` | Rejected as below the noise floor | `skill-fidelity-reviewer` is handed literal diff text the parent already holds (`ready-for-review/SKILL.md:102`); `comment-discipline-reviewer` has the same no-`Bash` shape. The duplication lands at handoff time, when few turns remain to re-amplify it. Full-patch dumps are 24.6% of diff calls and 56.5% of diff bytes; the largest single call measured ~7,500 tokens. |

**Unreconciled, noted rather than resolved:** `subagent-delegation/SKILL.md`
names "verbose `git diff` / state-survey bursts" as delegation candidates,
while `ready-for-review` Step 3 runs the cumulative branch diff inline and
`/code-review` reads the staged diff in the main session by design. Both
behaviors are correct — the same skill's frontmatter excludes the diff you
reason over line by line — but no single file states where the boundary falls.

**2026-08-16:** `subagent-delegation/SKILL.md` Step 1's Output test now
names the discriminator directly — the locate-and-report vs.
read-and-reason split Step 2 already defines — so `ready-for-review`
Step 3 and `/code-review`'s inline diff reads are governed by that
stated rule.

## From `opus-plan-boundary-handoff.md` — "Opus-anchored plan boundary: continue, switch, or hand off"

`plan-boundary` re-priced 95 Opus-anchored sessions' own post-plan-boundary
main-thread turn sequences (15,047 turns, 16,455,317 output tokens) under
three arms, out of 611 sessions scanned and 117 Opus-anchored (~90 days of
current retention — the corpus is rolling and self-deleting, so these counts
will drift on a later rerun).

| Lever | Verdict | Measured reason |
|---|---|---|
| Model-conditional branch in `plan-it` Step 7 and `handoff`'s warrant section, gated on the plan's own pre-committed five-part criterion | Rejected — null result, all five required and two failed | Continue on Opus totaled $2,464.44, switch to Sonnet in place $1,855.80, fresh Sonnet handoff $1,643.25 (cheapest). The handoff arm's advantage over switching in place breaks even at only +12.9% work-inflation, below the required 20% stress-margin floor, and the winning arm flips depending on whether the context-rebuild ramp curve is scoped to Sonnet-anchored sessions or pooled across model families — both required checks, neither holds. The other three required checks do pass: the corpus-denominator concern traced to subagent sidechain transcripts this subcommand already excludes, and is consistent across a 90-day and an all-time window; the switch-in-place simulation matched the 8 sessions with a real observed switch to within a mean deviation of -0.1% (worst single session ±2.3%); and 95 sessions clears the 20-session floor, with the fresh-handoff-cheaper-than-switch-in-place direction surviving a 2,000-resample bootstrap in 98.2% of resamples. |
| Continuing on Opus past the plan boundary (today's default behavior) | Not favored, direction robust | Loses to both alternatives in 100% of 2,000 session-level bootstrap resamples. Breakeven against switching in place is +32.8% work-inflation, against a fresh handoff +50.0% — the direction that continuing is the costliest arm is not the part this measurement leaves undecided. |
| Family-pooled context-rebuild ramp curve for the fresh-handoff arm's pricing | Rejected in favor of a Sonnet-anchored-only curve | The Opus/Sonnet per-turn-position rate ratio ranges 1.14x–2.28x across buckets, not a flat ~2.5x matching the vendor price ratio. Pricing the handoff arm from a family-pooled curve overstates its cost by roughly 12% and changes which of switch-in-place/handoff wins; `plan-boundary` derives it from the 472 Sonnet-anchored sessions (~54M output tokens) in scope instead. |

**Verdict:** because the plan's gate requires all five criteria and two do
not hold, this is a null result — no model-conditional branch ships in
`plan-it` Step 7 or `handoff`'s warrant section, and the current
context-only rule in both is unchanged. The reusable `plan-boundary`
subcommand is the durable output of this measurement; the verdict is
re-runnable against a fresher corpus if a later measurement's breakeven
clears the 20% margin under both ramp-curve methodologies.

## From `context-cost-root-cause.md` — "Context cost root cause: idle-gap cache rebuilds"

| Lever | Verdict | Measured reason |
|---|---|---|
| Cache-invalidation-by-payload-mutation (`.claude/plans/transcript-cost-subcommand.md`'s "Prefix-cache invalidation" hypothesis, killed via an 11.7k-tokens/turn corpus mean) | Refuted, mechanism reclassified | The prompt bytes across an idle gap are byte-identical; the tokens are re-billed because the vendor's 5-minute/1-hour cache TTL lapsed during the gap, not because the payload changed. A right-skewed distribution hides a tail a mean can't see: `cache-rebuild --since 30d --threshold 100000` measured 1,577 idle-gap rebuilds / $1,513.55 excess at list price (as measured; reproduce against the current rolling 30-day window — 165,303 calls scanned, 2,261 (1.4%) writing >= 100,000 tokens). |
| Idle-gap rebuild cause: concurrent-session switching vs. operator breaks | **Concurrent-session switching (92.9%), not breaks (7.1%)** | Classifying each idle-gap rebuild by whether another Claude Code session, in any account, had a call during the gap: 1,465 rebuilds / $1,406.52 occurred with another session active; 112 rebuilds / $107.03 occurred with everything idle, a real break. Breaks are almost exactly 7% of this cost and are not worth optimizing against. |

## From `binding-context-cap.md` — "Compel a handoff decision at a tail context threshold" (rejected before merge, 2026-08-16)

Unlike the other entries on this page, this plan never merged — it is not a
read-only historical record under `.claude/plans/`, so this row is the only
surviving record of it.

| Lever | Verdict | Measured reason |
|---|---|---|
| `Stop`-hook `{"decision":"block"}` on `nudge-handoff-near-context-cap.sh`, compelling a handoff decision past an escalating tail-context threshold | Rejected, never merged | Failed `/plan-review` twice: three reviewers across two rounds established that a `Stop`-hook block is satisfiable by any one-sentence reply from the agent — a stronger nudge at best, not the binding mechanism the plan claimed. |
| Reversing `token-cost-reduction.md` (PR #622)'s informational-only decision on the same hook to enable the above | Rejected, not shown necessary | PR #622 documents an engineer-verified decision to keep this hook informational-only ("nothing stronger is available without breaking the informational-class contract the engineer keeps deliberately"), made under the same project-engagement risk PR #622's own opening line already states. Measuring the real nudge-to-handoff conversion rate (344 distinct nudged sessions across all 6 declared config accounts, joined against `handoff session=` log lines) found 5.51% before PR #622's escalating-band re-arm shipped vs. 33.93% after — a ~6x improvement. Confounded (not a controlled experiment) and concentrated in one account (307 of 344 nudged sessions), but large and directional enough to undercut the premise that the informational lever had already failed and needed replacing. |
