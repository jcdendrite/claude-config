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

**2026-08-16 follow-up:** an idle gap past the 5-minute tier is necessary but
not sufficient for a rebuild. Reading `usage` fields directly for main-thread
calls preceded by a gap of at least 10 minutes but under an hour —
unambiguously past the 5-minute tier and inside the 1-hour one — large warm
cache reads outnumbered large cache writes by roughly 1.8 to 1 over 30 days. Both tiers are vendor-documented, so
a mixed corpus is expected; the rows above are unaffected, since they classify
calls that already crossed the write threshold rather than predicting which
gaps will. Two consequences for anyone reusing this section's numbers:
`cache-rebuild` sorts gaps against the documented tier boundaries rather than
discovering them (`transcript-analysis.py:7782-7783` hardcodes 300 and 3600
seconds), so its bucket labels restate the vendor's model rather than
confirming it; and a plan may not assume a given gap length forces a rebuild.
Counts here are a one-off scan, not a rerunnable script, and are undeduped by
`requestId` — treat the ratio as the finding and the absolutes as unreliable.

## From `binding-context-cap.md` — "Compel a handoff decision at a tail context threshold" (rejected before merge, 2026-08-16)

Unlike the other entries on this page, this plan never merged — it is not a
read-only historical record under `.claude/plans/`, so this row is the only
surviving record of it.

| Lever | Verdict | Measured reason |
|---|---|---|
| `Stop`-hook `{"decision":"block"}` on `nudge-handoff-near-context-cap.sh`, compelling a handoff decision past an escalating tail-context threshold | Rejected, never merged | Failed `/plan-review` twice: three reviewers across two rounds established that a `Stop`-hook block is satisfiable by any one-sentence reply from the agent — a stronger nudge at best, not the binding mechanism the plan claimed. |
| Reversing `token-cost-reduction.md` (PR #622)'s informational-only decision on the same hook to enable the above | Rejected, not shown necessary | PR #622 documents an engineer-verified decision to keep this hook informational-only ("nothing stronger is available without breaking the informational-class contract the engineer keeps deliberately"), made under the same project-engagement risk PR #622's own opening line already states. Measuring the real nudge-to-handoff conversion rate (344 distinct nudged sessions across all 6 declared config accounts, joined against `handoff session=` log lines) found 5.51% before PR #622's escalating-band re-arm shipped vs. 33.93% after — a ~6x improvement. Confounded (not a controlled experiment) and concentrated in one account (307 of 344 nudged sessions), but large and directional enough to undercut the premise that the informational lever had already failed and needed replacing. |

## From `lsp-token-reduction-feasibility.md` — "LSP as a token-reduction lever"

Whether routing code navigation through the Language Server Protocol would
cut billed tokens enough to be worth building. Figures below are shares of a
rolling 30-day transcript corpus measured across two accounts; re-measure
before citing them forward.

| Lever | Verdict | Measured reason |
|---|---|---|
| Building an LSP integration via an MCP bridge (Serena, `mcp-language-server`) | Rejected as the heavier primitive | Claude Code ships a built-in LSP tool, dormant until a code-intelligence plugin is installed — a bridge duplicates a first-party capability with third-party code and a new runtime. Schema cost is *not* the objection: MCP tool schemas are deferred by default under tool search, so idle tools do not sit in the per-turn baseline. |
| Symbol-level navigation as a token-reduction lever | Rejected, below the double-digit bar | Upper bound ≈3.8% of billed input tokens on the most code-dense account, ≈1.7% on the other measured. `Read` output is ≈11.7% of billed input tokens, and only the whole-file-indexable-code slice — 32.6% of all read volume — is addressable at all. Markdown is the largest read bucket at 42.6% and LSP does not touch it. Both bounds carry an unverified read-to-billed conversion that could move in either direction, and no discount for comprehension reads a symbol lookup cannot replace. The 11.7% is against billed input tokens; the `context-composition-analyzer.md` entry above measures the same content against cumulative prompt-token growth, which counts a chunk once on entry rather than on every subsequent turn — different denominators, not conflicting figures. |
| Code-dense repositories as a proxy for code-dense reading | Refuted | The account with by far the most statically-analyzable source still read 42.6% Markdown against 40.8% code. Portfolio composition does not predict read composition; measure the transcripts, not the tree. |
| Enabling the native code-intelligence plugins | Adopted for diagnostics, not for tokens | Post-edit type errors without a compiler or linter run (vendor-documented, unmeasured here). Installed at user scope per account, so `claude/.claude/settings.json` is untouched and adoption is deliberately unrecorded in-repo. Carries a documented memory cost on large projects and false-positive import diagnostics in misconfigured monorepos. |
## From `background-slow-bash-calls.md` — "Default slow/network-bound Bash calls to `run_in_background`"

| Lever | Verdict | Measured reason |
|---|---|---|
| Defaulting a Bash call expected to be slow or network-bound (branch push, PR create, CI-check poll) to `run_in_background: true`, so the main thread does not sit idle long enough to expire the prompt cache | Rejected, premise falsified | The multi-minute call durations that motivated it are not execution time. A transcript records only the assistant `tool_use` timestamp and the matching `tool_result` timestamp — there is no per-call execution field in the schema — so any duration derived from it includes permission-prompt wait and operator idle. Scanning that window across the default declared-root scope found main-thread Bash calls at 5 minutes or more whose command shapes include `cd`, `pwd`, `echo` and `git status` at the same magnitude as `gh pr view` and `git push`, with the longest exceeding 20 hours; `cd` has no execution cost to background. The named commands measure sub-second on this machine (`git push --dry-run`, `gh pr list`). Figures are a one-off scan, not a rerunnable script — treat the precision accordingly. |
| `run_in_background: true` for a Bash call whose execution genuinely does take minutes (not merely wait-inflated) | Rejected, mechanism does not reach the wait | `run_in_background` governs whether a call detaches after dispatch, not whether it is approved, so it does not shorten an approval wait. A backgrounded call also re-invokes the main thread on completion, so absent independent work to interleave it converts a blocking wait into an idle wait of the same length. The residual driver is the concurrent-session-switching finding in the `context-cost-root-cause.md` section above, not the shape of the Bash call. |

## From `delegate-instrument-authoring.md` — "Delegate the instrument along with the objective"

The plan fixed its go/no-go rule before running the measurement: ship a
`subagent-delegation` rule if inline instrument-authoring mass concentrates
in sessions that never dispatched a subagent; record this row instead if
authoring mass is not concentrated there. Reproducible
via `transcript-analysis.py instrument-authoring` (default corpus scope, no
`--since`) — unlike several entries above, this is a standing subcommand, not
a one-off scan.

| Lever | Verdict | Measured reason |
|---|---|---|
| `subagent-delegation/SKILL.md` rule routing instrument-authoring to `general-purpose` | Rejected (measured) | Across 622 scanned sessions, zero-dispatch sessions (189, 30.4% of sessions) carried only 6.6% of main-thread authored-payload mass (~312K of ~4.74M chars); sessions that dispatched at least once (433, 69.6%) carried the other 93.4% (~4.43M chars). The plan's rule fires on concentration in the zero-dispatch cohort; the measured concentration runs the opposite direction — sessions that already delegate account for the overwhelming majority of inline-authoring mass, so inline authoring is not tracking the pattern the rule would exist to catch (a session answering its own question by writing the tool it needs, rather than delegating the build). |

## From `cost-attribution-integrity.md` — "Cold prompt-cache measurement and root cause"

Full empirical record: [`case-studies/cold-cache-attribution.md`](case-studies/cold-cache-attribution.md).

| Lever | Verdict | Measured reason |
|---|---|---|
| Cache TTL as a uniform, non-account-scoped property | Corrected, not a lever this repo can pull | Direct reads of `cache_creation.ephemeral_1h_input_tokens` across 22,290 turns on one account's main thread show zero one-hour-TTL tokens, while the other five accounts on the byte-identical stowed harness all show non-zero. The earlier "Cache-TTL selection (5-minute vs. 1-hour) as a configurable lever" row's "nothing in `settings.json`, hooks, or env vars exposes this field" premise still holds — this corrects only its "no lever exists" framing: a real, lever-shaped difference exists between accounts, but it tracks plan tier or usage-overage state, which is a vendor account question to resolve outside this repo, not a config gap inside it. |

## From `token-cost-reduction.md` — "Token cost reduction: bound context growth"

| Lever | Verdict | Measured reason |
|---|---|---|
| `CLAUDE.md` guidance telling the agent to `/clear` at a phase boundary and `/compact` before idling (that plan's Phase 4) | Rejected, written then dropped | `/clear` and `/compact` are harness built-in CLI commands with no tool binding, so `CLAUDE.md`'s only reader cannot execute either. The `/clear` half also re-proposed blanket reset-at-a-boundary advice already rejected as mispriced above (`handoff-boundary-decision-rule.md`); the `/compact` half contradicted that plan's own `[engineer-verified]` compaction stance. Full rationale in that plan's Phase 4 entry. |
| The idle-gap cache rebuild | Named, not solved | Two events, ~910K tokens at cache-write rates, ~$9 of PR #609 as recorded by the source plan. The prompt-cache TTL cannot exceed 1 hour by any exposed setting, so an idle gap past it forces a full cache-write rebuild. The rejected Phase 4 guidance was the only thing aimed at it; nothing replaced it, and nothing agent-side can, because only the operator decides when a session sits idle. |

## From `handoff-nudge-cap-recalibration.md` — "Re-ground the handoff-nudge absolute cap against cost-per-work, not nudge frequency" (2026-08-22)

| Lever | Verdict | Measured reason |
|---|---|---|
| Retuning `HANDOFF_NUDGE_ABS_CAP` from 360,000 to 150,000 | Adopted | `pr-cost --record` populated a 145-row ledger (PRs #278–#698, this repo's own corpus) bucketed by `mean_context_at_turn`: the 100–150k bucket (n=23) is the cheapest bucket with a trustworthy sample, and both $/PR and $/1k output tokens rise monotonically through every larger bucket (150–200k through 300k+). 150,000 is that bucket's upper edge — sessions crossing it roll into the next, more expensive bucket. Supersedes the 2026-08-08 session-share-frequency basis the prior 360,000 default was grounded on (`docs/handoff-nudge.md`'s original "Why this cap" section): that basis measured nudge-dismissal risk, not cost per unit of delivered work, which is the question this retune answers instead. |

## From `handoff-threshold-impact-analysis.md` — "Did the 360,000→150,000 handoff-nudge cap retune actually help?"

Full empirical record: [`case-studies/handoff-threshold-impact.md`](case-studies/handoff-threshold-impact.md).

| Question | Verdict | Headline figure (scope) |
|---|---|---|
| Does the nudge's mechanism-engagement gate (share of session dollars spent past the fire threshold) actually improve under the lower cap? | Yes, robustly | 89.5%→58.3% before/after (machine-wide); direction holds at every cap tested from 100,000 to 360,000, no sign flip |
| Does cost per shipped PR improve? | No clean win — a real, non-dominant cost shift | Median flat ($30.79→$31.63, claude-config-only), but mean +24% and upper quartile +60% ($56.04→$89.55); does not trigger the study's own pre-registered overhead-dominance rule |
| Does review quality decline under the new cap? | No | Reviewer-spawn intensity per branch rose, 8.72→9.95 (claude-config-only) — more scrutiny, not less |
| Does handoff/continuation overhead explain the rise in cost per shipped PR? | Yes — grows monotonically across the transition | Startup-burn share of branch dollars: 1.7%→2.4%→3.4% across before/excluded/after (machine-wide) |
| Was the earlier 1.25x–4x session-share risk (`absolute-token-handoff-threshold.md`) ever measured for this specific 360,000→150,000 drop? | Yes, this study measured it | 1.69x (53.1%→89.7%), each window at its own real governing cap |

## From `trim-global-claude-md.md` — "Trim and reorganize both CLAUDE.md files"

| Lever | Verdict | Measured reason |
|---|---|---|
| Relocate hook-backed and doc-backed rationale out of the two always-loaded `CLAUDE.md` files onto surfaces that load only when the content's own trigger fires (`.claude/rules/`, `pr-description/SKILL.md`) — root `CLAUDE.md` | Adopted; landed on target | Root `CLAUDE.md`: 182 lines / 13,061 chars → 137 lines / 8,376 chars (-45 lines / -4,685 chars), matching the plan's projection. Four items (marker-mechanism sentence, settings.json/skill-authoring conventions, PR-merge pointer, redaction-section trim) relocated as scoped. |
| Same relocation — global `claude/.claude/CLAUDE.md` | Adopted; net landed larger than projected | Global `claude/.claude/CLAUDE.md`: 141 lines / 27,332 chars (this plan's stated baseline) → 146 lines / 28,450 chars (+5 lines / +1,118 chars net, the opposite of the projected cut). See breakdown below. |

Breakdown for the global-file row above:

- Unrelated intervening commits grew the file +10 lines / +1,347 chars between the plan's baseline measurement and implementation (151 lines / 28,679 chars immediately before the relocation began).
- The relocation's own cuts against that pre-relocation state undershot the estimate (-5 lines / -229 chars).
- The marker-mechanism sentence's cut was dropped entirely: a pinned test in `claude/.claude/skills/tests/test_skills.py` asserts that sentence survives verbatim.
- The "Ground every choice" category-6 bullet's ticket-prose coverage was kept rather than deleted, since no other skill picks up that surface.

Saving is session-shape-split, not uniform, because idle-gap rebuild cost scales with rebuild magnitude (byte count), not frequency — see "Context cost root cause" above.

## From `disable-artifact-workflow-default.md` — "Disable Artifact/Workflow by default, with per-session opt-back-in" (2026-08-25)

| Lever | Verdict | Measured reason |
|---|---|---|
| Default `disableArtifact`/`disableWorkflows` to `true` in the shared `claude/.claude/settings.json`, paired with a per-session CLI-scope opt-back-in (`claude-workflow`, `claude-artifact`) | Accepted, shipped | Pre-committed go/no-go gate required a ≥5,000-token Tools drop; measured drop was 16k tokens (23.8k → 7.8k) — see [`design-decisions.md` §31](design-decisions.md). |

## From `skill-fidelity-reviewer-yield-gap.md` — "skill-fidelity-reviewer zero-finding cited-path edit rate: correct the record" (2026-08-30)

| Lever | Verdict | Measured reason |
|---|---|---|
| Demoting `skill-fidelity-reviewer` from auto-dispatch, or refining its trigger prose, on the strength of its zero-finding-bucket cited-path edit rate | Rejected | The two agents' cited-path populations aren't comparable — this agent cites the specifications it checked, not the branch's diff — so the rate is a citation-genre artifact, not a reviewer-value signal; see [`design-decisions.md` §33](design-decisions.md). |

## From `opus-frontload-review-rounds.md` — "Front-loading Opus into authoring: does it cut review rounds?" (2026-09-01)

Full empirical record: [`case-studies/opus-frontload-review-rounds.md`](case-studies/opus-frontload-review-rounds.md).

| Lever | Verdict | Measured reason |
|---|---|---|
| Front-loading Opus into an earlier authoring point (beyond `plan-architect` and `consult`-mode) to cut review-and-fix rounds | Inconclusive | The pre-registered observational screen doesn't clear the adopt bar in any stratum. It ran against a 186-row ledger, stratified by `changed_files`, with a 2,000-resample bootstrap on the direction. Every high-stability pair moves in the confound-consistent direction (`/plan-it`'s own complexity selection), which the plan's asymmetric verdict rule excludes from reading as refutation. |
| `code-writer` dispatches that resolved to Opus despite a `sonnet` pin, as the one plausible within-agent natural-experiment channel (the plan's within-agent gate) | Instrument-blocked, not tested | `subagent-mix` redacts `subagent_type`, not only branch names, once more than one account root resolves — unconditional on this machine (6 declared roots). The 12 `Declared: sonnet` rows in the 30-day window can't be individually attributed to `code-writer`; a superset bound (245 sonnet-declared/opus-observed dispatches across all 12 agent types) exceeds the ≥20 floor but isn't code-writer-specific. |
| A forward-looking controlled pilot flipping a `model:` pin for a treatment arm | Declined, not deferred | Requires the pin flip the plan's Out-of-scope excludes; a structurally identical pilot was already closed as unmeasurable in this repo's available window (`absolute-token-handoff-threshold.md`), and the outcome variable's variance puts a sub-one-round effect out of reach at achievable n. |
| A round-3-triggered, hook-enforced, unspecialized `plan-architect MODE=consult` dispatch, aimed at the 29-PR 3+-round tail specifically rather than the whole distribution | Recommended follow-up, not built here | The tail's `commit_count` jumps discontinuously (2.33→2.54→2.86→6.31 across rounds 0–3+), the signature of a non-converging review loop rather than a first-pass-authoring-quality gradient. A failure-mode classification of all 29 tail PRs found no single mode at ≥half (plan-quality 39%, oscillation 29%, context-loss 29%, scope-growth 4%), which under `plan-architect`'s own pre-registered decision rule recommends an unspecialized consult over a mode-tailored one. Ships as its own scoped `/plan-it` run — see the full empirical record's "Follow-up diagnostic" section. |

## From a 2026-09-01 session — cross-check against Anthropic's "Optimizing for cost and intelligence" guide

Source: https://platform.claude.com/docs/en/about-claude/models/optimizing-for-cost-and-intelligence,
read 2026-09-01. The table below carries one row per lever the guide names,
in the guide's own order. The guide's own "Measure on Your Own Workload"
method prices production-weighted tasks with outcome checks across model
tiers and effort levels before any multi-model arrangement is added — that
is what this register already does, so the method gets no row of its own.

| Guide lever | Verdict | Coverage, or why not reachable |
|---|---|---|
| Prompt caching (cache repeated context) | Already covered, root-caused | The "Context cost root cause" section above traced idle-gap rebuilds to concurrent-session switching (92.9%), not operator breaks (7.1%). `transcript_analysis/pricing.py` independently encodes the same multipliers the guide prices against: 5-minute write 1.25x, 1-hour write 2x, cache read 0.1x. |
| Pick the cache duration (5-minute vs. 1-hour) | Reopened on a new mechanism; verdict unchanged | Supersedes the cache-TTL thread running through three earlier entries (the original "Cache-TTL selection" row, its 2026-08-15 follow-up, and the "Cache TTL as a uniform, non-account-scoped property" row under `cost-attribution-integrity.md`). A mechanism none of them covered exists: `experimental.cacheTtl` in subagent frontmatter selects `5m` or `1h` per agent (Anthropic, *Create custom subagents*). It is the first cache-duration control living in repo-tracked config rather than per-machine environment. Still not worth pulling: the 1-hour tier costs 2x base per write against the 5-minute tier's 1.25x, so it pays only when a prefix is re-read after a gap past five minutes. The field also sits in an `experimental.` namespace, not a stable surface for repo-shared config. Not measured for subagents: the 96–97% sub-5-minute idle-gap concentration behind the 2026-08-15 verdict was taken on main-thread calls. `cache-rebuild` includes subagent groups but reports no main/sidechain split, so a subagent-scoped figure needs a `transcript-analysis.py` change, not a rerun. |
| Trim tokens | Already covered; all three sub-levers adopted by a different mechanism | Deferring unused tool definitions: `disableArtifact`/`disableWorkflows` default to `true`, measured 16k-token drop (23.8k→7.8k), see the `disable-artifact-workflow-default.md` section above. Keeping bulk data out of the prompt: the guide's Files-API-plus-code-execution mechanism has no Claude Code surface, and the reachable equivalent is already mandatory — "Locate before a whole-file read" in the global `CLAUDE.md`, plus pass-a-path-not-the-content in `brief` and `handoff`, with one documented exception in `ready-for-review` for a subagent holding no `Bash` to resolve a path itself. Managing the context lifecycle: the handoff-nudge program, retuned against `$/PR` (`handoff-nudge-cap-recalibration.md` above). |
| Audit prompts against the current model | Swept 2026-09-01, nothing found | The guide targets stale over-specification: "verify twice," "maximally thorough," mandatory step-by-step, hand-rolled reasoning scratchpads, retired thinking-budget settings, contradictory rules. A sweep across `claude/.claude/`, both `CLAUDE.md` files, and `docs/` returned zero instructional hits for any of those shapes. The only "step-by-step" and "scratchpad" matches found are descriptive prose or unrelated senses (a filesystem scratch path, the ephemeral-isolation worktree primitive). Recorded so the next reader of this guide doesn't re-run the sweep. |
| Batch processing (50% discount, 24-hour turnaround) | Rejected, no exposed surface | Nothing in `settings.json`, hooks, or agent frontmatter exposes a batch-submission path, so a session cannot route its own turns through it — the same no-surface shape as the original `cache_control` rejection above. The guide forecloses it directly: "Not available for Claude Managed Agents sessions (interactive by design)." Zero prior mention anywhere in this repo, which is why it gets a row rather than a pointer. |
| Workspace spend limits | Outside this repo's surface — observability yes, enforcement no | A workspace spend limit is an Anthropic Console/account setting, not a `settings.json`, hook, or frontmatter one. What this repo owns is observation: the opt-in local ledgers (`docs/cost-ledger.md`, `docs/pr-cost.md`) and the statusline dollar display. No hook here enforces a spend cap, and building one is a separate decision. |
| Model choice priced per completed task, not per token | Already practiced, under different names | The guide's phrasing appears nowhere here, but the method is in use twice. `opus-plan-boundary-handoff.md` above priced three model arms against 95 sessions' real post-plan-boundary turn sequences in dollars with a work-inflation breakeven, not per-token rates. `handoff-nudge-cap-recalibration.md` above tuned a threshold off a 145-row `$/PR` ledger. The per-token framing that does appear (`docs/auto-mode.md`'s "roughly 5x the per-token cost of Sonnet") is a routing heuristic for an accidental Opus dispatch, not a model-selection methodology. |
| Effort level tuned per workload shape | Already covered; one vendor level deliberately out of reach | Per-agent `effort:` pins are enforced by `EXPECTED_EFFORT` in `claude/.claude/hooks/tests/test_agent_roster.py`, with the task-shape-over-inheritance rationale in the global `CLAUDE.md` and `design-decisions.md` §24, which already cites the same Anthropic Effort and model-configuration docs this guide sits beside. One delta worth recording: Anthropic's *Model configuration* doc lists a sixth level, `ultracode`, absent from that test's `VALID_EFFORT_LEVELS`. The omission is deliberate: `ultracode` plans a dynamic workflow per task and this repo disables the `Workflow` tool by default (`design-decisions.md` §31). `VALID_EFFORT_LEVELS` exists to catch a typo in a value the repo actually assigns, not to mirror the vendor's list. |
| Re-run failures at higher effort | Named, not investigated | The one guide lever with no prior coverage and no verdict here. Effort pins are static per agent, and nothing re-dispatches the same agent at a higher effort after a failed outcome check, though the guide's precondition (checkable outcomes) holds via tests and the review gates. The nearest existing arrangement differs in kind: `code-writer` runs at `high` with a downstream review pass as backstop (`design-decisions.md` §24), escalating to a different agent rather than re-running the same one. Closing this needs a measurement nobody has run — what share of `code-writer` dispatches fail their own downstream check, and what a lower starting tier would cost against that share. |
| Set budgets and output caps | Rejected, no exposed surface | Subagent frontmatter (Anthropic, *Create custom subagents*) carries no `task_budget`, `max_tokens`, or output-token-cap field; the only budget-adjacent fields are `maxTurns` (a turn count, not a token limit) and `experimental.cacheTtl` (the cache-duration lever above). `max_tokens` is set by the API caller, which is Claude Code, not this repo. `maxTurns` is reachable and truncates a dispatch rather than pricing it — a safety cap, not a cost lever. |
| Multi-model architectures (Advisor, Orchestrator) | Already implemented, under different names | Advisor: `plan-architect`'s `MODE=consult` dispatch is a cheap executor escalating one hard design decision to an Opus-pinned read-only agent on demand (`design-decisions.md` §37), and `MODE=plan-sections` is the same escalation on a fixed trigger (§30). Orchestrator: a Sonnet-default parent decomposes and delegates bulk work to `code-writer` and the reviewer roster, with the global `CLAUDE.md`'s Model & Effort Routing rule fixing which tier each dispatch gets. Recorded honestly: the guide's precondition — a multi-model configuration must beat a single model's entire score-vs-spend curve — was never measured here. Both arrangements were adopted on other grounds, not as a validated curve win. |

## From `markdown-context-ingestion-cost.md` — "Markdown context-ingestion cost"

Full empirical record: [`case-studies/markdown-context-ingestion.md`](case-studies/markdown-context-ingestion.md). Supersedes nothing in the `lsp-token-reduction-feasibility.md` section above — that entry already rejected LSP as a general token lever and named markdown as the bucket it doesn't touch; this row closes the follow-up that finding implies, a markdown-specific LSP.

| Lever | Verdict | Measured reason |
|---|---|---|
| Markdown LSP / MCP section server, retrieving one heading's text instead of a whole file | Rejected, declined outright | No LSP request returns document text — `documentSymbol` and siblings return ranges and labels only — so a markdown language server would still need a `Read` to retrieve the located section. Two lighter primitives already do this in two calls with zero new infrastructure: `Grep '^#{1,6} '` then a ranged `Read`, or a ranged `Read` directly against a cited `§N`. Both are already mandatory via the global `CLAUDE.md` "Locate before a whole-file read" rule. |
| A section-extract script or new markdown-parsing dependency | Rejected | The lighter primitives above succeed on their own; the waste heuristic found no evidence of waste for such a tool to recover (weaker than evidence of no waste, so corroborating only); this repo declares no npm toolchain and no markdown parser today. |
| `context: fork`, a `skills:` preload, or switching agents from `Read` to `Skill` invocation | Rejected | An invoked skill body and a read file both persist in-conversation identically until compaction, so switching between them saves no in-turn bytes. All 812 sampled multi-read subagent dispatches favored observed, as-needed reads over a turn-1 preload of every skill body; zero favored preload. Omitting `Skill` from a subagent's `tools:` disables invocation entirely, with no per-skill allowlist to selectively restrict it. |
| Trimming the always-loaded `CLAUDE.md`/skill/doc baseline | Named, not solved — owned elsewhere | Weighted in byte-turns, the always-loaded baseline is the single largest measured item (1.41× every main-thread markdown read combined). It sits inside its 200-line commit-time cap today and reclaiming it is scoped to a separate, already-in-flight trimming effort; this plan only records the finding. |
