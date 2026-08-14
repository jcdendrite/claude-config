# transcript-analysis cost undercount: root cause and fixes

## Context

**Goal: find why `cost` under-reported spend for one account's Aug 1–13
window, and close whichever gaps turn out to be real bugs.**

A comparison surfaced what looked like a ~23% gap between `transcript-analysis
cost`'s output (~$1,979 for a 13-day window) and a figure the investigating
session was handed as ground truth ($2,555.49). That comparison turned out to
be against the wrong source: the $2,555.49 came from Anthropic's "Claude Code
usage" analytics dashboard, which Anthropic's own UI labels an *estimate*
("Spend figures are estimates for analytics purposes. For actual costs, refer
to the cost dashboard."). Checked against the actual Cost dashboard
(platform.claude.com/cost) instead, the account's real August-to-date figure
is $2,141.06 against this tool's $1,982.72 for the same window — a ~7.4% gap,
not ~23%.

This plan documents that correction, ranks every hypothesis this session
checked with its evidence, and fixes the concrete code gaps the investigation
surfaced along the way — real, currently-dormant pricing-multiplier and
pricing-table gaps that don't happen to explain this account's current
window, but would silently underprice any account/window where they *do*
apply.

## Findings — ranked, with evidence

Investigated one account's config dir on this machine (called "the account"
below — see redaction note); Anthropic's live pricing page was fetched fresh
this session for every rate cited.

**1. RESOLVED — wrong comparison baseline (primary cause of the reported
23%).** [engineer-verified: platform.claude.com/claude-code vs
platform.claude.com/cost, both checked directly by the engineer this session]
The "Claude Code usage" dashboard both parties were comparing against is
documented by Anthropic as an estimate; the Cost dashboard is the actual
billing source. Against the Cost dashboard the gap is ~7.4%, not ~23%.

**2. RULED OUT — stale/missing pricing rates.** [verified:
platform.claude.com/about-claude/pricing, fetched this session] Every rate
and multiplier in `_MODEL_BASE_INPUT_RATES` / `_model_rates`
(`transcript-analysis.py:4860-4950`) matches the vendor's current published
table exactly, including Sonnet 5's $2/$10 rate — which the vendor page now
states is permanent, not introductory (the previously-announced Sept 1, 2026
increase to $3/$15 "will not occur").

**3. RULED OUT — 1h-cache-write priced at $0.** [verified: code inspection +
direct corpus scan] `_cache_write_split` (`:4953-4965`) always derives
`cache_write_1h` alongside the other four classes from one base rate — there
is no code path that counts 1h-cache tokens but zero-prices them. Separately,
a direct scan of the account's full local corpus (515 files, 40,868+ usage
records) found zero non-zero `ephemeral_1h_input_tokens` — the $0.00 the
original report showed was a true negative, not a bug.

**4. RULED OUT (for this window) — missing model IDs.** [verified: code
inspection, cross-checked against vendor pricing] `_MODEL_BASE_INPUT_RATES`
prices exactly 5 model IDs; any other ID is excluded from the dollar total
entirely (not zero-priced-but-counted — see `_price_turn:5137-5139`). The
account's Aug 1–13 window reports "Unpriced tokens: 0", so this isn't active
here. It's a real gap for any account/window still on an unlisted model
(Sonnet 4.5 in particular — Claude Code's own codebase already tracks
`claude-sonnet-4-5` as a real, current, unretired model ID elsewhere in this
same file; see Approach below).

**5. RULED OUT — unmodeled fast-mode/data-residency multipliers.**
[verified: direct corpus scan] Anthropic bills `speed:"fast"` turns at 2x and
`inference_geo:"us"` turns at 1.1x, and both fields are present in every
usage record's schema, but `_price_turn`/`_model_rates` never read either
one. A direct scan of the account's Aug 1–13 window found `speed` was
`"standard"` on every record and `inference_geo` was never `"us"` — so this
isn't active here either, but it's a silent, permanent undercount risk the
moment either surface is used, on this account or any other.

**6. RULED OUT — multi-account misattribution.** [verified: ran `cost
--by-project` against every other Claude Code account on this machine
(personal + 4 client accounts), scoped to the same 13-day window] No
project matching the investigated account's name surfaced in any sibling
account.

**7. RULED OUT (for the 13-day window) — retention/rotation.** [verified:
`cleanupPeriodDays` unset in the account's own `settings.json` (30-day
default applies); the account's oldest local transcript is 12 days old, well
inside that floor] Nothing in the 13-day window could have rotated out.
Separately confirmed (not a bug, an inherent scope limit): the account's
local transcript corpus starts at 2026-08-01T18:29:56Z, and the account's
own per-client config-dir was first provisioned 2026-07-30 — this machine
never had July transcripts to scan for this account in the first place, which
is why a July comparison (not attempted by the original report) would show a
near-100% gap for reasons that have nothing to do with `cost`'s pricing
logic.

**8. OPEN — residual ~7.4% gap.** Not chased further per the engineer's
steer (see Approach). Plausible, unconfirmed contributors: live-accrual
timing drift between when each figure was captured (this account accrues
roughly $150/day, so an hours-wide gap between snapshots is real money), and
the same corpus-start boundary as finding 7 (any activity between account
creation on 2026-07-30 and the first local transcript on 2026-08-01 would
count on the Cost dashboard but has no local transcript to be scanned at
all).

## Approach

**Root problem:** the reported 23% gap was mostly a wrong-baseline artifact;
the smaller real gap plus two dormant pricing gaps and one observability gap
are worth fixing regardless of whether they explain today's number.

**Givens:**
- G1 — the Cost dashboard is the authoritative actual-billing source for
  this account; the Claude Code usage dashboard is not. [engineer-verified]
- G2 — this machine cannot recover pre-2026-08-01 transcripts for the
  account under investigation; no code change reconstructs data that was
  never written locally. [verified: filesystem scan, finding 7 above]
- G3 — Anthropic's published multiplier ratios (5x output, 1.25x
  cache_write_5m, 2x cache_write_1h, 0.1x cache_read, 2x fast mode, 1.1x
  `inference_geo:"us"`) are fixed vendor policy this plan can price against
  but not change. [verified: platform.claude.com/about-claude/pricing and
  .../build-with-claude/fast-mode, fetched this session]

**Decision (engineer, this session): don't chase finding 8 further.** The
corpus-coverage warning (M4 below) surfaces the boundary finding 7/8 both
depend on for every future run, which is a better use of effort than trying
to squeeze out the last ~7% by hand on one already-explained window.

### M1 — anchors: G1, G2. No code change.
The wrong-baseline correction and the corpus-start boundary are findings, not
bugs — nothing in `transcript-analysis.py` caused them. Captured here so this
plan is the durable record (per `docs/private-project-redaction.md`-style
conventions, this file avoids the account's identifying name — see
Verification).

### M2 — anchors: row 5. Add `speed`/`inference_geo` pricing multipliers.
`_price_turn` (`:5114-5148`) computes `dollars_by_class` from `_model_rates`
alone. Add a small modifier step: `usage.get("speed") == "fast"` → ×2
(vendor's fast-mode pricing table, platform.claude.com/docs/en/build-with-
claude/fast-mode: $10/$50 vs $5/$25 base for the only two fast-mode-eligible
models today, Opus 5/Opus 4.8 — exactly 2x on both input and output);
`usage.get("inference_geo") == "us"` → ×1.1 (vendor's data-residency pricing
section, platform.claude.com/docs/en/about-claude/pricing, applies to "all
token pricing categories"). Apply both to the five `dollars` values before
returning, since dollar-multiplier order is commutative and this avoids
threading a new parameter through `_model_rates`.

No per-model eligibility list needed for either check, and this is grounded
in the *outcome*, not just the request, on both fields — not merely
"succeeded so it must be eligible": the fast-mode doc's "checking which
speed was used" section states "The response `usage` object includes a
`speed` field that indicates **which speed was used**... When a request
with `speed: 'fast'` succeeds, `usage.speed` is `'fast'`." The data-
residency doc's response section states "The response `usage` object
includes an `inference_geo` field **indicating where inference ran**" (its
SDK example comments that line "Check where inference actually ran").
Both fields report settled outcome, not an echo of the request parameter —
the same page also confirms `claude-sonnet-4-5` (M3's new entry) is on the
pre-4.6 list that gets a 400 rather than a silently-accepted `inference_geo`,
consistent with leaving it out of any geo-eligible set.
A hand-maintained eligibility set would be the heavier, over-powered
primitive here: it needs updating every time Anthropic extends either
feature to a new model, and the API's own error-on-ineligible-request /
report-actual-outcome behavior already does that gating for free.
One gap this doesn't close: `_USAGE_DRIFT_INVARIANT_KEYS` (`:5076`), the
existing canary for "a merged run's usage fields stay invariant across its
records," does not include `speed`/`inference_geo` — so if either field
turns out not to be run-invariant the way the five keys it does cover are,
M2 gets no runtime signal. Add a one-line code comment at the multiplier
site naming this as a known, accepted gap (not a silent one).

*Lighter primitives considered and rejected:* (a) leave both fields unread,
since neither is active in the account/window audited here — rejected
because both are present in every usage record's schema today and
undercounts silently the moment either is used, on any account; (b) surface
a warning instead of computing the multiplier — rejected because the schema
already carries an exact, vendor-documented multiplier; a warning would
discard information the tool already has.

### M3 — anchors: row 4. Add `claude-sonnet-4-5` to the pricing table.
Add `"claude-sonnet-4-5": 3.00` to `_MODEL_BASE_INPUT_RATES` (`:4860-4866`).
Base rate verified against the vendor table's "Claude Sonnet 4.5" row ($3
input / $15 output / $3.75 5m-write / $6 1h-write / $0.30 cache-read — all
five match the existing derivation multipliers exactly, same as the sibling
`claude-sonnet-4-6` entry already in the table). The exact ID string is
grounded two ways, not guessed: it's already used as a prefix-match target in
this same file's `_200K_CONTEXT_MODEL_PREFIXES` (`:4892-4896`), and a direct
grep of this machine's own historical transcripts turned up the literal,
undated string `"model": "claude-sonnet-4-5"` in real records.

New entry gets a one-line code comment naming this asymmetry:
`_context_window_for_model` prefix-matches
`claude-sonnet-4-5` (so a dated-snapshot variant like
`claude-sonnet-4-5-20260115` still 200k-buckets correctly), but
`_MODEL_BASE_INPUT_RATES` is an exact-match dict — the same dated variant
would price as unpriced. The comment states this so a future "just add
prefix matching to pricing too" change is a deliberate decision, not a
rediscovery.

`claude-opus-4-5` and `claude-opus-4-1` are also listed in
`_200K_CONTEXT_MODEL_PREFIXES` and have vendor-published rates, but neither
turned up in any transcript this session could read to confirm the exact
on-disk string carries no dated-snapshot suffix — left out of this fix rather
than guessed; call out as a follow-up in the PR description.

*Lighter primitives considered and rejected:* (a) rely on the existing
"Unpriced tokens" footer instead of pricing it — rejected because that
footer already existed and didn't prevent this investigation's confusion
(it's a token count, not a dollar estimate, printed below the totals a
reader is scanning for); (b) fetch pricing live from the vendor page at
request time instead of a static table — rejected as the over-powered
primitive: this is a read-only local analysis tool following the existing
`_PRICING_FETCH_DATE`/`_MODEL_RATE_EXPIRES` manual-refresh convention
already in the file; a live dependency adds network I/O, caching, and a new
failure mode for no proportionate benefit here.

### M4 — anchors: G2, row 7/8. Corpus-coverage-vs-`--since`-window warning.
`cost`'s per-turn loop (`:5743-5789`) already parses each record's timestamp
when `--since` is given, but only to filter, and only when `since_ts` is set
— nothing tracks or reports the *earliest* turn actually present in the
scanned scope. Add that tracking (parse every in-scope record's timestamp
unconditionally, not just when `since_ts` is set) and, after the loop, when
`since_ts is not None` and the earliest timestamp found is more than a day
newer than the requested window start, print a `WARNING: cost: ...` line
(matching the existing warning style at `:5664-5667`) naming the earliest
date found and the requested window start. This would have surfaced finding
7/8's boundary the moment this exact investigation started, instead of
requiring a manual filesystem forensic pass.

**Track the minimum per root, not globally.** In a multi-root scan
(`--config-dir` extras, or declared `~/.claude/transcript-config-dirs`
roots) a single scope-wide minimum lets a well-covered account mask a short
one — exactly this investigation's scenario, generalized to two or more
accounts. Track the minimum per `account_ordinal` instead, mirroring the
existing per-root diagnostic loop's own `account-N` labeling (`:5647-5667`)
and the per-account accumulator already threaded through this same function
(`per_account[account_ordinal]`, `:5782`) — reuses an established pattern
rather than adding a new one. Warn once per short root, not once for the
whole scan.

*Lighter primitives considered and rejected:* (a) document the limitation in
`docs/transcript-analysis.md` only — rejected because docs are opt-in
reading, and this specific gap is what fed the original confusion; a runtime
warning reaches every future invocation without depending on the operator
remembering to check docs first. (b) hard-fail (exit 2) on short coverage —
rejected as too heavy: a short corpus is often legitimate (new account,
first day of real use), and the report is still the best available answer;
a warning keeps the tool usable while surfacing the caveat.

### M5 — proposed addition, flag for approval. Fix the now-wrong Sonnet 5
STALE PRICING banner setup.
While verifying M3's rates, found `_SONNET_5_PROMO_EXPIRES = date(2026, 8,
31)` and `_SONNET_5_SUCCESSOR_BASE_RATE = 3.00` (`:4848-4853`) encode a rate
increase the vendor has since cancelled (`.../about-claude/pricing`: "The
previously scheduled increase to $3/$15 per million input/output tokens on
September 1, 2026 will not occur"). Left as-is, the STALE PRICING banner
fires in ~18 days and directs whoever sees it to bump Sonnet 5's base rate
to $3 — which is now wrong. Not one of the three fixes originally scoped
with the engineer; flagging here and in the PR description rather than
folding it in silently. Fix, if approved: drop the Sonnet-5-specific entry
in `_MODEL_RATE_EXPIRES` and `_SONNET_5_SUCCESSOR_BASE_RATE` so Sonnet 5
uses the same `_DEFAULT_REVERIFY_BY` schedule as every other model.

## Critical files

- `claude/.claude/scripts/transcript-analysis.py`
  - `:4848-4874` (`_MODEL_BASE_INPUT_RATES`, `_MODEL_RATE_EXPIRES`,
    `_SONNET_5_*`) — M3, M5
  - `:5114-5148` (`_price_turn`) — M2
  - `:5734-5789` (per-turn loop inside `_cost_report`, incl.
    `account_ordinal` resolution) — M4
  - `:5661-5667` (existing warning-print style to match) — M4
  - **`_price_turn`'s other five call sites** (M2/M3 change the dollar
    amount this shared function returns for any matching turn, so every
    consumer needs at least a read-only re-check, not just `cost`):
    `cmd_subagent_mix` (`:3443,3453`),
    `cmd_audit_routing` (`:4730`), `_context_distribution_report`
    (`:6123`), `_cost_trend_report` (`:7197`), and
    `_rearm_backtest_report`/`_extract_rearm_session_turns` (`:9047`).
    No code change needed at these sites — they all call the same
    now-corrected function — but Verification below re-runs the two that
    have a durable-output dimension (`cost-trend`, `cost-ledger`).
- `claude/.claude/scripts/tests/test_transcript_analysis.py` — extend the
  `_priced` fixture (`:5249`) with `speed: str | None = None,
  inference_geo: str | None = None` kwargs first (the fixture has no seam
  for M2's inputs today), then add:
  - M2: fast-mode ×2 alone, `inference_geo:"us"` ×1.1 alone, both stacked;
    a regression-anchor test pinning current (accepted) behavior when an
    ineligible model carries `speed`/`inference_geo` anyway (documents the
    trust boundary M2's design accepts, not a new runtime check); an
    integration-level `_cost_report` case combining a `claude-sonnet-4-5`
    turn with `speed:"fast"` in a sidechain record, asserting the sidechain
    accumulator reflects the ×2 multiplier (not just the grand total) —
    the multi-accumulator fan-out (`main_total`/`subagent_total`/
    `bucket_totals`/`per_account`, all fed from the same `turn_total`) is
    where a partial-application bug would hide.
  - M3: `claude-sonnet-4-5` now priced at the vendor rate; a dated-snapshot
    variant (e.g. `claude-sonnet-4-5-20260115`) still prices as unpriced
    (exact-match miss) while still 200k-context-bucketing correctly
    (prefix-match hit) — pins the asymmetry called out in M3 above.
  - M4: warning fires when corpus is short of the window, silent when it
    isn't, silent when `--since` isn't given; boundary cases at exactly
    24h and 24h+ε (not just a coarse short-corpus fixture — this
    codebase's existing boundary tests, e.g. `test_since_date_boundary_is_
    inclusive`, are the precedent for exact-edge assertions); a two-root
    case (`TestCostMultiRootReport`-style) where one root is short and one
    is fully covered, asserting the warning names the short root and isn't
    masked by the well-covered one.
  - M5 if approved: Sonnet 5 no longer flagged stale by the old Aug 31
    date.
- `docs/transcript-analysis.md` — document the new warning's trigger
  condition and the two new pricing multipliers, mirroring how the existing
  cache-multiplier behavior is documented.

**Reuse:** M2 and M4 both extend functions that already do 90% of the
relevant work (`_price_turn`'s existing dollars dict; the per-turn loop's
existing per-record timestamp parse) — no new data flow, just a modifier
factor and a per-root running minimum.

## Verification

- Test-first per `test-conventions`: for each of M2/M3/M4/M5, write the
  failing unit test against current behavior before editing production
  code.
- `../../../.venv/bin/pytest claude/.claude/scripts/tests/test_transcript_analysis.py`
  (worktree-relative venv path per this repo's CLAUDE.md) — full suite green.
- `../../../.venv/bin/ruff check claude/.claude/scripts/transcript-analysis.py`
- Re-run `cost --since 13d` against the investigated account after the
  change and confirm: (a) the total is unchanged (none of M2–M5 are active
  in this specific window, so this is a no-regression check, not a
  fix-confirmation one — the ~7.4% residual gap is expected to remain per
  finding 8); (b) the new corpus-coverage warning fires, naming
  2026-08-01 as the earliest turn against the requested window start.
- Read-only re-check of `_price_turn`'s two durable-output consumers:
  `cost-trend` against the same account, confirming no crash/shape change;
  `cost-ledger` in dry-run only (no
  `--record`) given the ledger's own documented unrecoverability — do not
  append a real ledger row as part of this verification.
- This plan file, its commit, and the eventual PR description avoid the
  account's identifying name throughout (per this repo's
  redact-private-project-refs policy) — every reference above uses "the
  account" or "the account under investigation."

## Out of scope

- Re-deriving or disputing the Cost-dashboard figures themselves.
- Chasing the residual ~7.4% gap further (engineer's explicit steer, this
  session).
- Adding `claude-opus-4-5` / `claude-opus-4-1` to the pricing table (no
  grounded on-disk ID string found this session — see M3).
- Any multi-account reconciliation feature (ruled out as this gap's cause,
  and out of scope regardless of cause).
- Redesigning `--since`/`--summary`/scoping semantics generally — those are
  working as documented; M4 only adds a new warning on top.
