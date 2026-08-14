# Cold prompt-cache measurement and root cause

## Context

**Goal: establish a trustworthy way to tell a cold prompt-cache re-write
from an ordinary incremental append, then use it to root-cause the cold
re-writes that carry the largest addressable share of this repo owner's
Claude Code bill.**

Monthly spend on one private project's account exceeded $2,000 for August,
with commercial consequences. Prior cost work has closed seven plans' worth
of levers (`docs/cost-levers-considered.md`), and an external reviewer's
five suggestions (GH-638) were evaluated against the code and found to have
no net merit — one claim factually false (review gates do not read file
contents into context; hashing is shell-side `sha256sum` at
`claude/.claude/scripts/marker.sh:198,212`), two already implemented
(deterministic checks precede LLM review at
`claude/.claude/skills/ready-for-review/SKILL.md:44-68`; all twelve
reviewer agents already carry `model: sonnet` frontmatter), one already
rejected on stronger reasoning (severity tiers, rejected as a disposition
axis at `claude/.claude/skills/code-review/SKILL.md:306,310`), and one
withdrawn by its author.

Measuring across all six declared config roots surfaced a cost driver that
no closed lever addresses and no existing subcommand reports.
**87.7% of corpus spend is context tokens** (cache read plus both
cache-write tiers); output is 12.2%. An ad-hoc scan, using the provisional
`write > read` rule discussed below, attributes most cache-write tokens to
full-prefix re-writes concentrated on the main thread:

| Account | Thread | 1h-TTL tokens | write>read share | write>read / read | avg event |
|---|---|---|---|---|---|
| personal | main | 590,462,993 | 74.9% | 2.57% | 215,069 |
| project-A | main | **0** | **89.1%** | **5.32%** | 302,418 |
| project-B | main | 15,736,157 | 78.8% | 3.17% | 213,686 |
| project-C | main | 98,839,367 | 79.0% | 2.10% | 331,278 |
| *all six* | sidechain | **0** | 23.8–50.1% | ~2% | 16,815–62,445 |

Two findings from that scan are independent of the provisional rule, since
they read TTL fields directly rather than inferring cache state:

1. **No subagent stream on any account ever receives the one-hour cache
   TTL.** A harness-wide constant, not an account property.
2. **One account's main thread is the sole main thread with zero one-hour
   TTL.** Its harness is byte-identical to the other five (verified by
   filesystem inventory), so the difference is account-scoped — plan tier
   or usage-overage state — not config.

The remaining columns are *not* yet trustworthy. `write > read` cannot
distinguish a genuinely cold prefix from a warm prefix carrying a large
incremental append — a single large file read, a multi-breakpoint partial
refresh, or a 5-minute TTL lapse all land in the same bucket. That
ambiguity is the reason this plan exists in its current shape: it must be
resolved *before* anything durable is built on top of it.

**Why now:** the spend is actively costing project engagements, and the
driver carrying 87.7% of the bill is invisible to the weekly ledger.

**Intended outcome:** a validated cold-write classifier, a
`cache-efficiency` subcommand reporting it, and a named root cause — or an
explicitly falsified hypothesis set — for the cold re-write. This plan
deliberately stops before proposing cost *fixes*; the engineer directed
"instrument first, then plan."

## Approach

Validate the classifier first, build the measurement on whichever rule
survives, and defer every durable artifact until the rule is settled. No
workflow, skill, hook, or agent behavior changes in this plan.

The ordering is the design decision. An earlier draft added the
`cache-efficiency` subcommand and two ledger columns first and validated
the classifier last; review found that this durably records a metric
computed from an untested rule, into a file explicitly described as "what
survives transcript deletion," with no way to recompute it once the raw
transcripts age out. Validation therefore moves to Step 1 and the ledger
columns leave this plan entirely (see **Out of scope**).

**The candidate classifier.** A warm cache serves the *whole* matched
prefix as `cache_read_input_tokens`, so an incremental append leaves read
roughly at the prior turn's total while adding a small write on top. A cold
prefix cannot do that: its read collapses. The rule to test is therefore
**cold ⟺ this turn's `cache_read_input_tokens` falls below the prior turn's
`read + cache_creation` total by more than a margin T**, which separates
"more content was added" from "the prefix was not served" — the exact
confound `write > read` cannot resolve.

`T` is deliberately unset here. A bare `<` would let ordinary turn-to-turn
variation in a dynamic system prompt register as cold at the margin, so
Step 1 derives `T` from its replicate data rather than this plan asserting
a number it has not measured.

Two limits of the rule, stated so later steps do not over-read it:

- It answers **"was this turn's prefix served,"** not **"why not."**
  Idle-expiry cold and structural-rewrite cold produce an identical
  read-collapse signature; separating them is Step 5's job via timestamp
  correlation, not the classifier's.
- It is **binary, and pools full with partial collapses.** When one
  breakpoint of several misses, read reflects only the matched prefix while
  `cache_creation` covers everything downstream — correctly cold, but
  indistinguishable from a whole-prefix rewrite. Step 5 must therefore not
  attribute a cause found in one population to the other; where the
  distinction matters, report the collapse magnitude alongside the count.

Correlation on the existing corpus has been taken as far as it goes. Cold
candidates correlate with observable preceding records at consistent lift
across two independently-billed accounts on the identical harness:

| Preceding signal | project-A lift | personal lift |
|---|---|---|
| `file-history-snapshot` record | 9.5x | 9.1x |
| `system` record | 5.6x | 7.9x |
| `AskUserQuestion` tool call | 7.3x | 4.2x |
| `Agent` spawn | 3.2x | 2.2x |
| `queue-operation` record | 2.3x | 3.7x |
| `ExitPlanMode` tool call | n/a | 16.5x |

Roughly 52% of cold candidates have no intervening record at all, at 1.1x
lift against warm turns — undiscriminated by record type. These lifts are
themselves computed under the provisional rule, so Step 1 re-derives the
table under whichever classifier survives before Step 5 acts on it.

**Alternatives set aside.** Acting on the correlation table alone was
rejected: a 9x lift does not establish causation over a shared third
factor, and it leaves the majority bucket untouched. Prefix-size reduction
(trimming `CLAUDE.md`, skill descriptions) was set aside as mistargeted —
the static instruction floor is roughly 11,000 tokens against candidate
events averaging 215,000–331,000, so trimming shrinks each event by a few
percent while leaving the event count unchanged. A new standalone
measurement script was rejected in favor of extending
`transcript-analysis.py`, which already owns corpus scanning, multi-root
resolution, pricing, redaction, and ISO-week windowing.

### Assumption ledger

**Root problem:** the metric that would rank every remaining cost lever —
how often the prompt prefix is re-written cold — has no validated
definition, so neither its size nor its cause is currently knowable.

**Givens** (fixed beyond this plan's reach):

- Prompt-cache TTL is selected per-request by the API caller via
  `cache_control`; nothing in `settings.json`, hooks, or env vars exposes
  the field. *Reason: the vendor owns the request-construction path, which
  is the platform this harness runs on top of.*
- Anthropic exposes no cache hit/miss diagnostic beyond the token counts
  already present in `usage`. *Reason: vendor-owned API surface — it is
  why the classifier must be inferred and validated rather than read.*

Transcript retention is deliberately **not** a given: `cleanupPeriodDays`
is a settings key within reach, and Step 0 changes it.

**Mechanisms:**

- *Validate the classifier by controlled experiment (Step 1)* — anchors:
  root. Lighter primitives considered: (a) further correlation mining on
  the existing corpus — rejected, the residual sits at 1.1x lift, so record
  type carries no signal there, and mining cannot validate the measure it
  is computed from; (b) reading Claude Code's cache-breakpoint
  implementation — rejected as not observable from this repo, and it would
  yield a vendor-internal claim this plan could not verify.
- *Add a `cache-efficiency` subcommand (Step 2)* — anchors: root. Lighter
  primitives considered: (a) a standalone script — rejected, would
  duplicate multi-root resolution and the redaction contract
  `docs/private-project-redaction.md` makes load-bearing for a public repo;
  (b) reporting from the existing `cost` subcommand with no new code —
  rejected, `cost` buckets by token class and never distinguishes a cold
  write from an incremental append.
- *Extend the drift canary to `cmd_cost` (Step 3)* — anchors: row 3.
  Lighter primitives considered: (a) leave it — rejected, `cmd_cost`'s
  split would silently degrade to subagent≈0% with no warning if the
  `subagents/` write format changed; (b) one corpus-wide check at startup —
  rejected, it would fire for subcommands that legitimately never read
  sidechain records.

**Assumptions:**

| # | Assumption | Tag |
|---|---|---|
| 1 | All six accounts run the byte-identical stowed harness | `[verified: filesystem inventory — same 24,707-byte CLAUDE.md symlink, 27 skills, 40 hooks, 12 agents, 4 rules per root]` |
| 2 | No sidechain stream on any account receives 1h TTL, and exactly one account's main thread receives none | `[verified: direct read of cache_creation.ephemeral_1h_input_tokens over 155,000+ assistant turns across all six roots, including the four-level subagent path]` |
| 3 | The existing main-vs-subagent attribution is correct; the gap is that `cmd_cost` carries no drift canary — only `cmd_subagents` and `cmd_skill_pair` call it, at `transcript-analysis.py:1200,4098` | `[verified: code trace]` |
| 4 | Cache TTL differs by account despite identical settings, so it is account-scoped (plan tier or usage-overage state) rather than config-scoped | `[verified: one account's main thread shows 0 one-hour-TTL tokens across 22,290 turns while the other five all show non-zero]` |
| 5 | `write > read` indicates a cold prefix | `[unverified — known-defective]` Named misclassification modes: a large single tool result appended to a short warm prefix; a multi-breakpoint request refreshing some breakpoints while hitting others; a 5-minute TTL lapse, which is cold but for a different reason than a prefix rewrite. Step 1 replaces or confirms it; every Context-table column derived from it is provisional until then. |
| 6 | The read-collapse rule stated under Approach separates append from cold, at some margin `T` | `[unverified]` — Step 1's primary hypothesis, tested against row 5 as the incumbent. `T` is derived in Step 1, not assumed here. The rule determines *whether* a prefix was served, not *why* it was not, and does not separate full from partial-breakpoint collapse. |
| 7 | The ~52% residual is one mechanism rather than several | `[unverified]` — Step 5 is designed to falsify this, not assume it |
| 8 | Cold re-writes are addressable rather than intrinsic to vendor-side caching | `[unverified]` — a negative result is a valid, plan-closing outcome |
| 9 | The outlier account's spend problem shares a cause with the other accounts | `[engineer-verified]` — the engineer's stated leading hypothesis; row 1 and the matched lift table are consistent with it, and row 4's TTL gap is an additional, separable factor |

## Implementation

Steps land as **three separate commits** — Step 0 alone (different blast
radius), Steps 1+5 (experiment and its case study), Steps 2–4 (tooling) —
so Step 0 is revertable without a hand-split.

### Step 0 — Stop clipping the evidence window

**File:** `settings.local.json` in each config dir the experiment will
sample — **not** the committed stowed `claude/.claude/settings.json`.
**Change:** set `cleanupPeriodDays` to `180`. The key is unset today, so
Claude Code applies its 30-day default; the corpus spans roughly 45 days as
a result. `0` is rejected by Claude Code's own validation, so a finite
value is required.
**Why personal, not shared:** the shared-settings framing was rejected in
review. `cost-ledger --record` runs weekly, which the 30-day default
already satisfies with ~4x margin — the 180-day need is *this experiment's*
baseline requirement, not one every stow user shares. Shipping it stowed
would impose six months of retained prompt and tool-output content at rest
on every stow user's disk to serve one time-bounded study, which is a
data-exposure increase, not merely a disk cost.
**Footprint:** the sweep covers `~/.claude/tasks/`,
`~/.claude/shell-snapshots/`, and `~/.claude/backups/` as well as
transcripts (`claude/.claude/cache/changelog.md:2331`), so the growth is
across four directories, not transcripts alone. Measure actual on-disk size
before and after rather than asserting a multiple.
**Revert:** unset the key to return to the 30-day default. Note this is a
one-way ratchet on data already captured — reverting narrows the future
window only.

### Step 1 — Validate the classifier

**File:** new `docs/case-studies/cold-cache-attribution.md` (created here,
extended by Step 5).
**Change:** score both candidate rules — `write > read` (incumbent, row 5)
and read-collapse (row 6) — against two constructed cases whose true cache
state is fixed by the experiment's setup rather than inferred from either
rule's own output. Record per turn: `input_tokens`,
`cache_read_input_tokens`, both `cache_creation` tiers, the prior turn's
totals, and the wall-clock delta from the prior turn.

- **Known-warm case:** append a single large synthetic tool result to a
  prefix in the same session and same account, issued immediately after the
  prior turn with no idle gap. Warmth here rests on the vendor's documented
  cache-TTL behavior (a Given), not on either candidate rule — this is what
  makes the case ground truth rather than circular. `write > read` is
  expected to misclassify it; row 6 is expected not to.
- **Known-cold case:** the matched counterpart — same session, same
  account, but deliberately idle past the ephemeral-TTL boundary before the
  next turn, so the prefix is expected to be evicted. Without this, a rule
  that never fires cold would pass on the warm case alone.

**Replicates:** at least 10 turns per case, not one scripted run — the
correlation table already shows the same signal producing 2.2x vs 3.2x lift
across accounts on identical harnesses, so single-trial results cannot
separate a real effect from noise.
**Accept / falsify:** a rule is falsified by any misclassification of
either constructed case, since ground truth is deterministic by
construction rather than statistical. A rule is accepted only on a clean
sweep of both cases across all replicates. Record the observed read-shortfall
distribution and set `T` from the gap between the two cases' distributions;
if they overlap, `T` does not exist and row 6 is falsified along with row 5.
**Redaction:** use synthetic content for the appended tool result and
generic `account-N` labels; source nothing from a real private-project
edit.
**Why first:** every number this plan would otherwise publish depends on
this rule being right, and the ledger columns that would have encoded it
durably are out of scope precisely because it is not yet settled.

### Step 2 — Add the `cache-efficiency` subcommand

**File:** `claude/.claude/scripts/transcript-analysis.py`.
**Change:** add `cmd_cache_efficiency` plus its subparser entry, following
`cmd_cost`'s shape. It calls `_resolve_project_scope(args,
"cache-efficiency", include_subagents=True, roots=roots)` and, per (account,
thread) pair, emits: assistant turns, `cache_read_input_tokens`, both
`cache_creation` tiers, cold tokens, cold share of write tokens, cold/read
ratio, cold event count, and mean tokens per cold event — classifying cold
by **whichever rule Step 1 validated**.
**Redaction:** reuse `_build_redact_map`, `_redaction_ordinals`, and
`_redact_proj_label` verbatim from `cmd_cost`, including the default-redact
behavior, the `--no-redact` multi-root refusal (`sys.exit(2)`), and the
`DO NOT PUBLISH` banner (`transcript-analysis.py:5555-5611`). A per-account
tabular subcommand is the shape most likely to leak a raw project label by
omission.
**Why:** `cost` buckets by token class only; nothing today separates a cold
prefix re-write from an incremental append.

### Step 3 — Widen the drift canary

**File:** `claude/.claude/scripts/transcript-analysis.py`.
**Change:** call `_warn_if_subagent_format_drift(total_spawns,
total_sidechain_turns)` from `cmd_cost` and `cmd_cache_efficiency`. This is
**not** a one-line addition: unlike `cmd_subagents` (`:1200`) and
`cmd_skill_pair` (`:4098`), neither counter is accumulated today. Note that
`cmd_cost` (`:5481`) is a thin wrapper — the per-session loop and the
`isSidechain` split (`:5786`) live in its delegate `_cost_report`
(`:5531`), which is where both counters must be threaded.
**Why:** both commands key a thread split on `isSidechain` but neither can
warn; if the `subagents/` write format changed, their splits would silently
degrade to subagent≈0%.

### Step 4 — Tests

**File:** `claude/.claude/scripts/tests/test_transcript_analysis.py`.
**Change:** add
(a) classification tests with explicit boundary rows — first turn with
`read == 0` (must not be cold, per the first-turn carve-out), non-first
turn with `read == 0` (must be cold), and both sides of the `T` margin
derived in Step 1 — so the test cannot pass on a fixture that avoids every
case the rule depends on;
(b) a canary test asserting the warning fires from `cmd_cost` on a fixture
whose subagent records lack `isSidechain`, **written before** Step 3's code
change so its red state is observed rather than asserted;
(c) a canary negative test asserting silence on a healthy corpus;
(d) redaction allow/deny pair for `cache-efficiency` — default output
redacts labels, and `--no-redact` with two roots in scope exits 2 —
matching the coverage `cost`, `edit-format`, and `read-scope` already carry.
**Why:** the canary's current gap is invisible precisely because no test
asserts it fires from these call sites, and a canary that fires on healthy
data gets ignored.

### Step 5 — Attribute the residual

**File:** `docs/case-studies/cold-cache-attribution.md` (extends Step 1).
**Change:** re-derive the correlation table under the validated classifier,
then vary one factor at a time against a defined control: (a) an idle gap
crossing 5 minutes, (b) an `AskUserQuestion` call, (c) an `Agent` spawn
whose subagent performs **zero** file edits, (d) an edit producing a
`file-history-snapshot`. The control is a session of matched turn count and
matched wall-clock duration that crosses no 5-minute boundary and varies
nothing else — condition (a) needs it specifically, since the idle gap is
itself the ephemeral-cache boundary.
**Account pinning:** run each condition and its control on the **same**
account. Assumption 4 establishes account-scoped TTL as a verified
confound, so a cross-account comparison could read caching differences as a
condition effect.
**Reporting:** state replicate count per condition and report a null result
as null. Report collapse magnitude alongside cold counts, so a cause found
in partial-breakpoint collapses is not attributed to whole-prefix rewrites
(see the classifier's stated limits under Approach). With the validated
classifier, a condition that merely adds prompt content no longer registers
as cold, which is what makes (b) and (d) interpretable at all.
**Why:** the residual sits at 1.1x lift, so only holding the prefix fixed
and varying one factor separates a real trigger from co-occurrence.

## Critical files

| Path | Change |
|---|---|
| `settings.local.json` (per config dir, not committed) | Step 0 |
| `docs/case-studies/cold-cache-attribution.md` | Steps 1, 5 |
| `claude/.claude/scripts/transcript-analysis.py` | Steps 2, 3 |
| `claude/.claude/scripts/tests/test_transcript_analysis.py` | Step 4 |
| `docs/transcript-analysis.md` | Document `cache-efficiency` and the widened canary |
| `docs/cost-levers-considered.md` | Add a row recording that cache TTL is account-scoped in practice, correcting the `:22` row's "no lever exists" conclusion while leaving its premise intact |

**Reuse opportunities:** `_resolve_project_scope(..., include_subagents=True)`
(`:2617-2699`), the pricing table, `_build_redact_map` /
`_redaction_ordinals` / `_redact_proj_label`, and ISO-week windowing all
exist in `transcript-analysis.py` and must be called, not reimplemented —
Step 2 adds only the cold classification and the TTL split.

## Verification

Run from the implementation worktree. **Check the relative depth first:** a
branch slug containing `/` puts the worktree four levels below the repo
root, not the three the repo `CLAUDE.md` documents — confirmed on this
plan's own branch, where `../../../.venv/` lands in `.claude/` and the venv
is at `../../../../.venv/`.

1. `<venv>/bin/pytest claude/.claude/` and `<venv>/bin/ruff check
   claude/.claude/` pass; `scripts/list-shell-files.sh | xargs -0
   <venv>/bin/shellcheck` clean.
2. Step 4(b)'s canary test is observed red before Step 3 lands and green
   after — recorded, not asserted.
3. Step 1 reports which classifier survived, the scored result for both the
   known-warm and known-cold constructed cases, and the derived value of
   `T`. If neither rule cleanly separates the two cases, Steps 2–5 do not
   proceed and the plan closes with that finding.
4. `cache-efficiency` output is redacted by default, and `--no-redact`
   across two roots exits 2.
5. Step 5 reaches a mechanism reproduced on demand, or an explicit record
   that the residual is not attributable with available instruments. An
   inconclusive run is reported as inconclusive.

## Out of scope

- **Ledger columns for cold-write share and TTL mix.** Originally Step 4 of
  this plan; removed. `_COST_LEDGER_COLUMNS` drives a fixed-width parser
  that fails closed — `_parse_cost_ledger_row_cells` raises on any row whose
  cell count differs (`transcript-analysis.py:7401-7405`) and the header is
  matched byte-exact (`:7302`, `:7485`) — so adding columns makes every
  existing account's `cost-ledger.md` unparseable on the next `--record`,
  with no migration path. Deferred to the follow-up plan, where the
  classifier is settled and a versioned-schema migration can be designed on
  its own terms rather than smuggled in behind a metrics change.
- **All cost fixes.** Delegating `/code-review`'s inline Base checklist to a
  Sonnet subagent, reducing the 1,452 measured hook denials, and prefix
  trimming are gated on this plan's findings.
- **The outlier account's missing one-hour TTL.** Real, separable, and the
  clearest single lever found, but it is an account-plan or usage-overage
  question to resolve with the vendor — no repo change fixes it. Recorded
  so it is not lost.
- **Meta-work volume.** Nearly half of the personal account's spend goes to
  work on this repo itself. A budget and discipline question, not a code
  change.
- **Changing any skill, hook, or agent behavior.** This plan adds
  measurement only.
