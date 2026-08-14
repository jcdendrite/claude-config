# Context cost root cause: idle-gap cache rebuilds

## Context

**Goal: make the dominant avoidable context cost — full-prefix cache rebuilds
after the cache TTL expires — measurable and visible, and correct the
repo-recorded finding that wrongly ruled it out.**

The operator observed costs rising and suspected prompt-cache invalidation,
citing an external suggestion to route Claude Code through a MITM proxy and
inspect raw payloads for cache-busting bytes.

Ad-hoc analysis over the full declared-roots corpus (all 6 config dirs in
`~/.claude/transcript-config-dirs`; 30 days; 4,039 transcripts, 145,786
requestId-deduped API calls, 1,091,605,888 cache-write tokens) found the
suspicion is directionally right but the mechanism is not what was proposed,
and this repo's own docs record the opposite conclusion:

| Measure | Value |
|---|---|
| Total corpus spend, 30d, list price | $12,794 |
| Corpus-wide cache write:read token ratio | 3.6% |
| Calls writing >= 100k | 1,960 (1.3% of calls) |
| Share of that tail attributable to idle-gap TTL expiry | **81.1%** |
| Idle-gap rebuilds | 1,590 (~53/day) |
| Excess vs. a warm-cache read, 30 days, list price | **~$2,162** |
| That excess as a share of total spend | **16.9%** |

The mechanism is TTL expiry, not payload mutation: the prompt bytes across the
gap are identical, and two-thirds of cache writes go to the 5-minute tier, so a
five-minute pause is enough to force a rebuild of a median 315,221-token
prefix at 1.25x-2x input rate instead of a 0.1x read.

**The cause is concurrent-session switching, not operator breaks.** Classifying
each idle gap by whether any other transcript — in any account — was active
during it:

| During the gap | Rebuilds | Excess |
|---|---|---|
| Another session was active (terminal switching) | 1,480 (93.1%) | $2,022 |
| Everything idle (a real break) | 110 (6.9%) | $140 |

Sessions switched away from go cold within the 5-minute TTL and rebuild on
return. Breaks are under 7% of the cost and are not worth optimizing.

**Why now:** the repo records this hypothesis as tested and killed — in a merged
plan, with no counter-entry in the live `docs/` register — so the next
investigation will skip it. The measurement above is ad hoc and dies
with this session unless it becomes a subcommand.

**Intended outcome:** a reproducible `cache-rebuild` measurement, a statusline
field showing the context size that drives rebuild cost, and corrected docs
that name the concurrency finding. No gates, no nudges — the operator chose
measure-and-surface only.

## Approach

Add one `transcript-analysis.py` subcommand that reproduces the tail analysis
and cause classification, extend the existing statusline to show absolute
context tokens, and correct the register in `docs/`.

**Dollar figures stay in Python; the statusline shows tokens.** Rebuild cost is
linear in context size, so the token count carries the behavioral signal.
Putting dollars on the statusline would require importing the model→USD rate
table into bash — a table that lives in `transcript-analysis.py` anchored to
`_PRICING_FETCH_DATE` (`:4840`) with a re-verify mechanism bash has no access
to — plus float arithmetic bash lacks natively. That would either drift
silently from the dated table or add a Python process spawn to a path that
already forks 11 `jq` calls per render. The subcommand and docs carry the
dollar conversion, including the rule of thumb the operator needs: at list
Sonnet rates a 5m-tier rebuild costs roughly **$1 per 250k tokens** of context
abandoned and returned to.

The proxy path is declined outright rather than deferred. TTL expiry is
invisible to a payload capture — it would show two byte-identical payloads and
no explanation — so it cannot answer this question even in principle.

**Land order:** mechanism 1 (subcommand), then 2 (statusline), then 3 (docs).
Only mechanism 3 has a real dependency — it cites numbers mechanism 1 must
first reproduce. Mechanisms 1 and 2 share no artifact and could land in either
order.

### Assumption ledger

**Root problem:** ~$2,162 / 30 days of avoidable list-price spend — 16.9% of
total corpus spend — is spent
re-writing prefixes whose cached copies expired during gaps created by
switching between concurrent sessions, and the repo's own docs record this
cause as ruled out.

**Givens** (fixed, beyond this plan's reach):

- **Cache TTL tiers are 5m and 1h, priced 1.25x and 2x base input; reads are
  0.1x.** Vendor-set. [verified: platform.claude.com/docs/en/build-with-claude/prompt-caching,
  quoted "5-minute cache write tokens are 1.25 times the base input tokens
  price", "1-hour cache write tokens are 2 times the base input tokens price",
  "Cache read tokens are 0.1 times the base input tokens price"]
- **Which TTL tier a request writes at is chosen by the API caller
  (Claude Code), not by repo config.** A platform boundary: no settings, env, or
  hook surface exposes `cache_control`. [verified: `docs/cost-levers-considered.md:22`,
  an entry already rejecting TTL selection as a lever for this reason]
- **Corpus scope is the declared-roots union, not one account.** Every
  `transcript-analysis.py` subcommand defaults to a union across every config
  dir listed in `~/.claude/transcript-config-dirs`; the new subcommand inherits
  that and must not narrow it. [verified: `skills/transcript-analysis/SKILL.md`
  Caveats, "Every subcommand's default scope is a union across every config dir
  listed in `~/.claude/transcript-config-dirs` ... not just the active profile"]

**Mechanisms:**

1. **`transcript-analysis.py cache-rebuild` subcommand** — anchors: root.
   The corpus numbers exist only in throwaway scripts; without a subcommand the
   finding cannot be re-checked after a workflow change.
   *Lighter primitives considered:* (a) a standalone script under `scripts/` —
   fails because it would duplicate `_dedup_turns_by_request_id` and the dated
   pricing table, and the 2.16x per-content-block overcount is exactly the trap
   a fresh script re-falls into; (b) extending `cost-trend` with a column —
   fails because the finding is a *distribution and cause classification*, not a
   scalar, and would not fit a ledger column.
2. **Statusline context-size field** — anchors: root.
   The existing bar shows percentage only, which is ambiguous across models with
   different window sizes; the absolute prompt size is what a rebuild re-writes,
   so it is the quantity proportional to the cost of walking away.
   *Lighter primitives considered:* (a) reuse the existing percentage bar
   unchanged — fails because 58% means 116k on a 200k window and 580k on a 1M
   window, a 5x difference in what a switch-away costs; (b) a hook that reports
   a rebuild after it happens — rejected: hook output is injected into the
   conversation and billed on every subsequent turn, so a cost-awareness feature
   would itself add context cost, and it can only report a rebuild already paid.
   Statusline output never enters the API payload, so this costs zero tokens.
   [verified: statusline stdin carries `context_window.total_input_tokens`,
   `context_window.context_window_size`, and `model.id` directly —
   code.claude.com/docs/en/statusline documented payload. No model→window lookup
   and no cross-script table are needed; an earlier draft proposed extracting one
   before these fields were confirmed present.]
3. **Register correction in `docs/`** — anchors: root.
   `docs/cost-levers-considered.md` is the live register; leaving it silent
   means the killed-hypothesis record stands unchallenged.

**Assumptions:**

- The 11.7k-tokens/turn mean that killed the prior hypothesis is a mean over a
  right-skewed distribution and cannot rule out a tail. Independently
  reproduced: a low-thousands mean coexists with roughly half of all cache-write
  volume sitting in calls above 100k.
  [verified: `.claude/plans/transcript-cost-subcommand.md:61-63` for the original
  claim; corpus re-measurement this session for the refutation]
- Cache writes/reads are recorded per API call as `cache_creation_input_tokens`
  / `cache_read_input_tokens` with a
  `cache_creation.{ephemeral_5m,ephemeral_1h}_input_tokens` tier breakdown, and
  must be deduped by `requestId` (2.16x overcount otherwise).
  [verified: live transcript inspection; `transcript-analysis.py`
  `_cache_write_split` ~:4953 (which falls back to the flat field, treating it
  as 5m-only, when the nested block is absent) and `_dedup_turns_by_request_id`
  ~:4985 already implement both]
- **The headline does not depend on the "large rebuild" threshold.** A
  sensitivity sweep over six definitions — absolute cutoffs at 50k/100k/200k and
  ratio cutoffs at 30/50/70/90% of prefix rewritten — puts idle-gap excess
  between $1,869 and $2,290, with the reported $2,162 inside that range; ratio
  definitions yield *more* rebuilds (up to 2,044), so 100k is conservative.
  [verified: sensitivity sweep this session, to be reproduced by the
  subcommand's `--threshold` flag per Verification item 5]
- Classifying a gap as "another session active" by scanning all transcripts for
  calls in the gap window attributes concurrency at ~1-call granularity. This is
  an association, not proof the operator was attending that other session.
  [unverified] — the subcommand must print the breakdown and this caveat rather
  than assert the conclusion.
- ~10.9% of the >=100k tail is "recent, same model, partial read" and remains
  unexplained. [unverified] — not chased here; the operator declined it.
- `--exclude-dynamic-system-prompt-sections` improves *cross-user* prefix reuse
  and does not address TTL expiry, since a rebuild rewrites the whole prefix
  regardless. [verified: `claude --help`] Set aside on that basis.

## Critical files

| Path | Change | Reuse |
|---|---|---|
| `claude/.claude/scripts/transcript-analysis.py` | Add `cache-rebuild` subcommand: per-call write distribution, cause classification (session start / idle 5m-1h / idle >1h / model switch / unexplained), concurrency split with its `[unverified]` caveat printed inline, priced excess, broken down by redacted `account-N` ordinal alongside the corpus fingerprint. Prints the resolved-scope header (root count) unconditionally, per the skill's Scope-confirmation rule. Builds one sorted global call-timeline index once and binary-searches it per gap — not a nested per-gap scan, which would be O(gaps x calls) and is the shape that degrades as roots accumulate. Target: under 30s for the default 30-day window at current corpus size (4,039 transcripts / 145,786 calls). Flags: `--threshold` (default 100000), `--since` (default 30d). Register beside `p_cost_trend` (~:10017). | `_read_session_file`, `_dedup_turns_by_request_id` (~:4985), `_cache_write_split` (~:4953), the `_PRICING_FETCH_DATE`-anchored rate table (~:4840), the declared-roots resolver and `--config-dir` handling shared with `cost-trend` |
| `claude/.claude/statusline-command.sh` | Render current context size beside the existing percentage (e.g. `[███░░░] 58% · 315k`), read from `.context_window.total_input_tokens`. **Not** `current_usage.cache_read_input_tokens`: that field counts tokens *not* rewritten this call, so it collapses to ~0 on the very turn a rebuild is paid — anti-correlated with the moment the field exists to flag. `total_input_tokens` reflects prompt size on every turn, warm or cold. Integer arithmetic only — no `bc`, no new dependency, no new subprocess (fold into an existing `jq` call). When `.context_window` is absent, follow the existing `[----------] --%` placeholder convention (`:52-54`). | Existing `jq` stdin parse (`:6-11`) |
| `claude/.claude/skills/transcript-analysis/SKILL.md` | Document the subcommand. | Existing subcommand entries |
| `docs/transcript-analysis.md` | Document `cache-rebuild` output columns and the $1-per-250k rule of thumb. | — |
| `docs/cost-levers-considered.md` | New entry recording that the killed cache-invalidation hypothesis was refuted, stating **the 93.1% / 6.9% concurrency-vs-breaks split as a named finding** — not merely "a concurrency split" — so a reader lands on the actionable conclusion without re-running the subcommand. | Existing one-line register format |
| `docs/cost-ledger.md` | Note that `context_pct` does not separate warm-read from rebuild-write cost. | — |

**Explicitly not edited:** `.claude/plans/transcript-cost-subcommand.md`. It is
a merged plan recording what was concluded at the time — a preserved record
under CLAUDE.md Axis 3. The correction goes in the live register, not by
rewriting history.

**No shared window table is needed.** An earlier draft proposed extracting
`resolve_context_window()` from `nudge-handoff-near-context-cap.sh` into a new
`lib/` file for the statusline to source. That is unnecessary: the statusline's
own stdin already carries `context_window_size` and the live
`current_usage.cache_read_input_tokens`, so no model→window lookup happens in
the statusline at all. The hook is left untouched and no new `lib/` convention
is introduced.

## Verification

1. **Unit fixture — classification and pricing.** Hand-built JSONL with, at
   minimum: a multi-record `requestId` run; a `<synthetic>` entry; the
   transcript's **first call** (`i==0`, no prior timestamp); a record with
   **absent/malformed timestamp**; a record with **no nested `cache_creation`**
   (flat-field fallback, asserted priced as 5m-tier); a record with
   **`cache_read == 0`**; an **out-of-order timestamp pair** (clock skew /
   negative gap); a 6-minute gap; and a model switch. Assert: dedup collapses
   the run, synthetic is excluded, first call classifies as *session start* and
   never as an idle bucket, the 6-minute gap classifies *idle 5m-1h*, malformed
   timestamps and negative gaps are explicitly handled (error or documented
   skip) rather than silently bucketed, and the priced excess matches a
   hand-computed value.
2. **Boundary fixture.** Records at exactly 99,999 and 100,000 tokens, asserting
   correct in/out-of-tail classification at the default threshold.
3. **Multi-root fixture — concurrency classifier.** Fake a multi-root scope with
   the suite's existing `extra_config_dirs=[...]` pattern (repeated
   `--config-dir`, as in `test_duplicate_root_deduped_by_resolve_not_string_equality`),
   not by writing a declared-roots file. Use three roots: two holding transcripts
   with controlled overlapping and non-overlapping timestamps, and one **valid
   but empty** — the realistic state of a rarely-used account, and the case where
   an empty root could be mis-scanned as "no other session ever active." Assert
   each gap buckets correctly, that a transcript's own later records **never
   self-match** as "another session," and that the output literally renders the
   `[unverified]` caveat. Cross-account concurrency is real — a gap in one
   account is routinely covered by activity in another — so a single-root fixture
   would not exercise the classifier at all. This drives the headline 93.1%
   figure and currently has no coverage.
   Root existence, unreadable roots, and duplicate-root dedup are **not** retested
   here: they already have coverage on `_resolve_cost_roots`, which this
   subcommand reuses.
4. **Synthetic large-corpus regression, spanning >=2 roots.** A generated corpus
   of several hundred calls with known composition, split across two synthetic
   roots, asserting a hand-computed rebuild count and dollar total **against the
   union** — not against one root's worth of calls. Aggregation across roots is
   exactly what this revision changed, and it is where a running total reset
   per-root, or `requestId` dedup scoped per-root instead of post-union, would
   hide. This replaces asserting the live figures: transcripts roll off on a
   ~30-day window, so a test pinned to "1,590 rebuilds / $2,162" would false-fail
   within days of merge regardless of code correctness.

   Separately, register `cache-rebuild` in the existing
   `_UNCONDITIONAL_HEADER_CASES` parametrized table so resolved-scope-header
   plumbing regresses in CI at near-zero cost. That is a mechanism check and does
   not belong in the manual live run below.
5. **One-time manual sign-off (not CI).** Before merge, run `cache-rebuild`
   against the live corpus and confirm it reproduces ~1,590 idle-gap rebuilds and
   ~$2,162, and that `--threshold` sweeps reproduce the $1,869-$2,290 band. Check
   the resolved-scope header's root count against the number of currently-valid
   lines in `~/.claude/transcript-config-dirs` at run time — that file is
   operator-editable, so a hardcoded expectation would go stale silently. Record
   wall-clock for this run and confirm the under-30s target in the subcommand's
   Critical-files row; a stated performance target with no measurement behind it
   is the same ungrounded-number defect this plan exists to correct.
   A mismatch means the subcommand and the ad-hoc script disagree, and the
   ad-hoc script is the one being retired — resolve before merge, do not
   annotate.
6. **Statusline.** Confirm the field renders against a stdin payload matching the
   documented schema; that the script exits 0 and shows the `--` placeholder when
   `.context_window` is absent; and — the case that caught the first field
   choice — that a payload with `cache_creation_input_tokens` large and
   `cache_read_input_tokens` exactly 0 (a turn that just paid a rebuild) still
   renders the full context size, not zero. Note `jq`'s `//` falls back only on
   `null`/`false`, so a legitimate `0` is distinguishable from an absent field.
   Measure render latency before and after, recording the baseline and asserting
   the added field costs no additional subprocess fork.
   The existing test fixture (`test_statusline_command.py:47`) sends only
   `model.display_name` — extend it to the fuller documented payload rather than
   asserting against a shape the real harness does not send.
7. **Lint:** `../../../.venv/bin/ruff check claude/.claude/` and
   `scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck`.

## Out of scope

- **Raising `cleanupPeriodDays`.** It is a `settings.json` key the operator
  could raise to widen the retention window, so it is a real lever rather than a
  given — but a longer window does not answer whether a given week's rebuild
  cost is real, and does not fix the register being wrong today. Not pursued
  here.
- **The MITM proxy runbook.** Declined by the operator; cannot observe TTL
  expiry in principle. It also requires installing new software and would route
  an authenticated API credential through a local proxy that writes flows to
  disk.
- **The ~10.9% unexplained rebuild tail.** Operator declined; separately
  ticketable.
- **Raw account names in output.** Account directory names are private-project
  identifiers under this repo's redaction rules and never appear. Per-account
  attribution itself is *in* scope: the subcommand reports the excess broken down
  by the redacted ordinal labels (`account-N`) the toolkit already uses for
  exactly this case, so an operator can see which account drives the cost without
  a name reaching a public repo. Ordinals renumber when the declared-roots set
  changes, so the output prints the corpus fingerprint alongside them, matching
  `cost`'s existing convention.
- **Any gate, nudge, or hook that acts on rebuild cost.** The operator chose
  measure-and-surface only.
- **Changing concurrency practice or handoff thresholds.** The measurement is
  the deliverable; acting on it is a later decision with its own evidence.
