# TTL-verdict idle-band and margin-footing fix

## Context

Goal: make `cache-rebuild --ttl-verdict`
(`claude/.claude/scripts/transcript-analysis.py`) compute its 1h→5m
verdict on consistent footing, by fixing two biases that both push the
verdict toward `adopt`.

Why now: the `promptCacheTtl: "5m"` setting for the main-conversation
cache bucket shipped on the strength of that verdict. A later read of the
accumulation code found that (1) the idle-band flags feeding `Z` and the
1h→5m switch delta inherit a cache-write-tier qualifier that belongs to
the opposite direction's question, and (2) the margin denominators omit
the fast-mode and US-inference-geo rate multipliers their numerators
carry.

Intended outcome: a corrected `--ttl-verdict` block with every
`cache-rebuild` figure printed without `--ttl-verdict` byte-identical.
This change affects every stow consumer who runs `transcript-analysis`,
since `claude/` installs to all of them.

Decisions taken by the engineer this session:

- Scope: `[engineer-verified: "Don't qualify the doc just yet. I don't want to add too many incremental changes. I want to fix the underlying bug and get a more accurate analysis."]`
- Fix shape, tentatively "I think ttl-verdict block only. What does plan-architect think?" — the architect proposed a shared band helper that also edits one line of the classifier; the engineer then selected the label "Shared helper (Recommended)" (ledger row M18).
- Multiplier mismatch included in this change: selected label "Include it (Recommended)".

## Approach

Two edits inside `--ttl-verdict`'s own per-root accumulation block put that verdict's numerator and denominator on the same footing. First, the idle-band flags that feed `Z` and both `switch_delta_1h_to_5m_*` accumulators become a gap-only test, dropping the cache-write-tier qualifier that belongs to the opposite direction's question. Second, both per-root dollar-volume denominators start reusing the `_price_turn` dollars the block already computes earlier in the same per-record loop, so they carry the same fast-mode and US-inference-geo multipliers their numerators do. The shared classifier `_classify_cache_rebuild_cause` keeps byte-identical behavior for every input, so every `cache-rebuild` figure printed without `--ttl-verdict` is unchanged.

**Why the write-tier qualifier is wrong for this direction.** `_classify_cache_rebuild_cause` answers "did a 5-minute TTL expiry force this call's cache *write*?" For a purely `ephemeral_1h`-tier write inside a sub-hour gap the answer is no — the 1h cache was still warm — so `pure_1h_tier_write` correctly routes it to `unexplained` (`transcript-analysis.py:6203`). The 1h→5m direction asks a different question: "did a live 1h tier serve this call's prefix as a warm *read*, which a 5-minute tier would have forced it to rebuild?" The call's own write tier does not bear on that. Two distinct predicates were sharing one label, and the wider one inherited the narrower one's guard. Today that leaves a warm 1h-tier call which read prefix P after an in-band gap and also wrote a small 1h increment contributing neither P to `Z` nor the `(1.25 − r)·P` expiry cost to the switch delta, while its increment still counts in `W1h` and still earns the full 0.75 tier saving.

**Correction to a prior plan's bias disclosure, recorded here rather than by editing it.** `.claude/plans/cache-ttl-verdict-gate-fix.md` is a preserved record and is not edited by this change. Its line 68 enumerates "three bias channels" in the accumulation code, and its ledger row 5 concludes that the `Z`-inflation channel biases "toward declining to change, never toward a spurious adopt." Row 5's claim about its own channel (a mixed root's `Z` inflated by read-only in-band calls) still stands. The enumeration was not exhaustive: a fourth channel — `Z` deflation, plus the paired missing expiry cost, on pure-1h-tier writes inside the idle band — runs the opposite way and is exactly a bias toward a spurious `adopt` on the 1h→5m direction. That plan's Out-of-scope bullet at line 196 leans on row 5's direction-safety and inherits the same gap.

**Verdict-movement risk, stated without figures.** The two fixes move the 1h→5m direction and the margin gate in known directions:

- The expiry-cost term this fix restores is switch-cost-positive, so it can only reduce a 1h-tier root's `Net$`; the added `Z` can only move the raw-token tiebreaker (`Z < W1h`) away from favouring a drop to 5m. Both push a 1h-tier root away from clearing the margin. A 1h-tier root whose `favors` flips from `5m` to `1h` has `net_primary <= 0`, so it cannot clear whatever the tiebreaker says; `adopt` only ever means "every consistent root clears", which for a 1h-tier root means "drop to 5m". The main bucket was captured while main traffic was on the 1h tier (`docs/transcript-analysis.md:989`), so its `adopt` is the result this correction can move.
- Raising the margin denominator can only shrink `|net / volume|`, and `favors` depends on the net's sign alone, so the multiplier fix can only make a root harder to clear.
- Reachable movement, both fixes together: neither fix can newly produce `adopt` for any bucket. The only new outcomes are `decline` and `roots disagree`; the four reachable label changes are `adopt` → `decline`, `adopt` → `roots disagree`, `decline` → `roots disagree`, and `roots disagree` → `decline`. Both `roots disagree` outcomes come from the idle-band fix flipping a 1h-tier root's `favors` from `5m` to `1h`:
  - `decline` → `roots disagree` needs a second root whose `favors` stays `5m`.
  - `adopt` → `roots disagree` needs at least two 1h-tier roots.
  - `roots disagree` → `decline` happens when the flipped root's `favors` now matches the other roots', collapsing the favored-direction set to one value, and the flipped root cannot clear.
  - A bucket with a single 1h-tier root cannot change label from `decline`.

  A move away from `decline` for the `subagent` bucket would satisfy that bucket's Revisit trigger in `docs/design-decisions/main-bucket-prompt-cache-ttl-5m.md:35-36`. This rests on the premises in rows M19 and M20.
- One printed line outside the verdict also changes: the tier-split cross-check line (`:7297-7309`) reads `switch_delta_1h_to_5m_by_origin_root`, so on a mixed root with a pure-1h in-band read its 1h-slice `favors` and `agree`/`disagree` label can move. It is informational only and cannot move the verdict.

### Assumption ledger

**Root problem.** `cache-rebuild --ttl-verdict` computes the 1h→5m verdict from an under-counted `Z` and an under-costed switch delta (the idle-band test carries the opposite direction's write-tier qualifier), and compares both directions' net against a margin denominator that omits the fast-mode/US-geo multipliers the net itself carries — two independent biases, both pushing toward `adopt`.

**Givens** (conditions beyond this plan's reach):

- **G1.** The vendor's cache-tier multipliers (1.25x/2x/0.1x) and the fast-mode (2x) / US-inference-geo (1.1x) rate multipliers are the vendor's; this report can only mirror them. `[verified: claude/.claude/scripts/transcript_analysis/pricing.py:25-42]`
- **G2.** `cache_read_input_tokens` carries no tier split and cannot express partial-prefix survival, so `Z` stays an *estimate* of rescued read volume rather than an observation, whatever this plan fixes. Dissolving that needs a data field the transcript does not carry. `[verified: docs/transcript-analysis.md:993]`
- **G3.** No figure from a corpus run may appear in this plan, in tests, in the commit message, or in the PR body — the per-account dimension bar is absolute, `cache-rebuild` is not a publication instrument for a machine-wide figure, and this plan file ships in the same public PR as the implementation. Repo policy owns this. `[verified: CLAUDE.md § "Redact private-project-identifying content"; docs/private-project-redaction.md § "Publishing a tooling measurement"]`

**Mechanisms:**

| # | Mechanism | Tag | anchors |
|---|---|---|---|
| M1 | Extract `_cache_rebuild_in_idle_5m_1h_band(is_first_call, gap_seconds, *, idle_5m_boundary_seconds=_CACHE_REBUILD_IDLE_5M_SECONDS) -> bool` — the gap test alone, `False` for a first call or an unparseable gap, else `idle_5m_boundary_seconds <= gap_seconds < _CACHE_REBUILD_IDLE_1H_SECONDS` — and have `_classify_cache_rebuild_cause` call it in place of its own `gap_seconds >= idle_5m_boundary_seconds` test. Behavior-preserving there: the three guards above that line already establish `not is_first_call`, `gap_seconds is not None`, and `gap_seconds < 3600`. | `[verified: transcript-analysis.py:6196-6206, 6057-6066]` | root |
| M2 | In the `--ttl-verdict` block, compute `is_idle_primary` and `is_idle_sensitivity` from M1's predicate instead of from the classifier's label, replacing the two computations at `:6823-6827` and the reuse comment at `:6818-6822`. | `[verified: transcript-analysis.py:6815-6827]` | row M1 |
| M3 | M2 changes nothing in the 5m→1h direction: `pure_1h_tier_write` is `eph_1h > 0 and eph_5m == 0`, so it is `False` whenever `eph_5m > 0`, and every `X` / `switch_delta_5m_to_1h_*` accumulation sits inside `in_w5m_branch = eph_5m > 0`. Inside that branch `cause == _CAUSE_IDLE_5M_1H` and M1's predicate are the same boolean. | `[verified: transcript-analysis.py:6771, 6829, 6849-6868]` | row M2 |
| M4 | M2 also cannot move `in_w1h_branch` membership or the unpriced-disclosure counters. `in_w1h_branch = eph_1h > 0 or (read_tokens > 0 and is_idle_sensitivity)`; the only records whose `is_idle_sensitivity` widens are those with `pure_1h_tier_write` True, which requires `eph_1h > 0`, which already satisfies the first disjunct. The unpriced counters are guarded by `in_w5m_branch or in_w1h_branch`, so they are unchanged. `W1h` tokens and `W1h$` likewise depend only on that branch and `eph_1h`. | `[verified: transcript-analysis.py:6835, 6843-6847, 6870-6874]` | row M2 |
| M5 | Leave the cache-miss-reason cross-tab at `:6893` keyed on `cause`, not on the widened flag. Its population is calls whose *write* the gap is claimed to have forced, which is what a vendor `cache_miss_reason` can contradict; a warm in-band read has no cache miss to explain, so folding it in would inflate the "agree" count with vacuous agreements. | `[verified: transcript-analysis.py:6889-6897, 7323-7326]` | row M2 |
| M6 | Replace both per-root dollar-volume accumulations (`:6853`, `:6874`) with the `_price_turn` result the block already computes at `:6844`: hoist that result to a `dollars_by_class: dict[str, float] \| None = None` initialized before the `if in_w5m_branch or in_w1h_branch:` guard, then accumulate `dollars_by_class["cache_write_5m"]` and `dollars_by_class["cache_write_1h"]`. Delete the two now-unused `rates = _model_rates(model)` locals. | `[verified: transcript-analysis.py:6843-6874; pricing.py:536-551]` | root |
| M7 | The guard substitution is exact, not an approximation: `_model_rates` returns `None` iff the model is absent from `_MODEL_BASE_INPUT_RATES`, which is the same condition on which `_price_turn` returns `dollars_by_class is None`. The token-vs-dollar population mismatch already disclosed as row 3 of `.claude/plans/cache-ttl-verdict-gate-fix.md` is therefore preserved unchanged, not widened or narrowed. | `[verified: pricing.py:142-152, 532-534]` | row M6 |
| M8 | On a corpus with no `speed="fast"` and no `inference_geo="us"` records, M6 is bit-identical to today: `_price_turn` computes `eph_5m / 1_000_000 * rates["cache_write_5m"]` from the same `_cache_write_split` inputs and applies no multiplier. Every existing `--ttl-verdict` expectation therefore holds unchanged — no existing fixture sets either field. | `[verified: pricing.py:526, 536-551; grep for `speed=`/`inference_geo=` in test_transcript_analysis.py returns only direct-pricing unit tests at :5754-5782, :9145, :9345, :10633 — none in a `--ttl-verdict` fixture]` | row M6 |
| M9 | Structural-sibling audit for M6 is complete at two sites. `_cache_rebuild_margin_clears` has exactly two call sites, both inside `_cache_rebuild_root_verdict_input`, fed by `w5m_dollars_by_origin_root` and `w1h_dollars_by_origin_root`; within `_cache_rebuild_report` those two are the only `1_000_000 * rates[...]` accumulations outside `_price_turn` and `_cache_rebuild_excess_dollars` (whose own counterfactual leg already applies both multipliers). Both siblings carry the identical defect and take the identical fix. The same grep also matches `:11655-11657` in the plan-boundary Arm B repricer, which is a different subcommand's repricing, not a margin volume, and is out of scope. | `[verified: grep `_cache_rebuild_margin_clears\|1_000_000 \* rates\[` over transcript-analysis.py → :6313, :6397, :6465-6466, :6853, :6874, and the out-of-scope :11655-11657]` | row M6 |
| M10 | Over-powered-primitive check, M1. Two lighter primitives were available and both fail. (a) Pass `pure_1h_tier_write=False` at the two `--ttl-verdict` call sites, touching nothing outside the block: rejected because the call site would then assert something false about the record in order to obtain a different predicate, and would need a comment explaining the false argument — the escape-hatch shape CLAUDE.md § Engineering Judgment tells us to push one level up instead. (b) Inline the band comparison in the `--ttl-verdict` block without touching the classifier: rejected because the band's two bounds would then live in two places and could drift silently, against the single-source-of-truth rule. If the engineer wants the classifier body untouched regardless, (b) with the helper defined but not wired into `_classify_cache_rebuild_cause` is the fallback, at the cost of that duplication. | `[verified: transcript-analysis.py:6196-6206]` | row M1 |
| M11 | Over-powered-primitive check, M6. Two heavier options were weighed and set aside. (a) Generalize `_context_composition_turn_rate_scale` into a shared per-call multiplier helper used at all six sites: rejected — it renames a function two tests reference by name, it would touch `_cache_rebuild_excess_dollars` on the default path, and re-associating the two multiplications is a needless float-identity risk on default-path figures. (b) Copy the two `if usage.get("speed")...` lines to the two volume sites: rejected — a fifth and sixth copy of the same idiom, recomputing what `_price_turn` returned three lines earlier. M6 introduces no helper at all and deletes a duplicated rate expression instead of adding one. | `[verified: transcript-analysis.py:5449-5459, 6317-6320; test_context_composition.py:541, 563]` | row M6 |
| M12 | Docs: one clarifying clause each at `docs/transcript-analysis.md:993` and `:995`. No other doc edit. `:993`'s `Z` definition ("the read-token volume served during a gap in [300, 3600) seconds that a live 1-hour tier serves as a warm read") already carries no write-tier qualifier and is correct post-fix; it gains only a clause naming the asymmetry with `X`, since `:989` states that pure-1h-tier writes are excluded from `X` and a reader will otherwise assume the same of `Z`. `:995`'s "dollar-equivalent write volume" gains a clause naming that it is priced with the same fast-mode/US-geo multipliers the net carries. | `[verified: docs/transcript-analysis.md:989, 993, 995]` | root |
| M13 | No docstring edit at `_cache_rebuild_1h_to_5m_delta_dollars` (`:6357-6374`). Its `is_idle_5m_1h_cause` contract already reads "a call whose prior-call gap fell in [idle_5m_boundary, 3600)" with no write-tier qualifier — it was the caller that violated the stated contract, and the fix restores agreement rather than changing it. | `[verified: transcript-analysis.py:6357-6374]` | row M2 |
| M14 | Scope: no edit to `docs/design-decisions/main-bucket-prompt-cache-ttl-5m.md`, and no change to the shipped `promptCacheTtl` value. | `[engineer-verified: "Don't qualify the doc just yet. I don't want to add too many incremental changes. I want to fix the underlying bug and get a more accurate analysis."]` | root |
| M15 | The fast-mode / US-geo multiplier mismatch is fixed in this change rather than deferred. | `[engineer-verified: selected option label "Include it (Recommended)"]` | row M6 |
| M16 | The shipped `promptCacheTtl: "5m"` setting is neither changed nor reverted here. Relayed by the dispatching session as a scope statement, not quoted from the engineer, so it is flagged rather than treated as their decision; it is recorded in Out of scope with its reason. | `[unverified]` | root |
| M17 | Dispatch split: one `code-writer` dispatch covering all three files. Both fixes edit the same ~60-line block of `_cache_rebuild_report`, their tests belong in adjacent classes of one test file, and the doc clause restates the same invariant. Splitting would force identical shared background into two prompts, put two agents in the same file region of one shared worktree (where overlapping edits clobber silently), and leave neither agent's self-review able to see the other's edit. | `[verified: claude-skills/skills/plan-it/SKILL.md § "Name the dispatch split"]` | root |
| M18 | Fix shape: the shared helper of M1 is adopted, editing one line of `_classify_cache_rebuild_cause`, rather than the strictly-in-block fallback named in M10. Asked after the architect's return, since the engineer's earlier "ttl-verdict block only" preference was tentative. | `[engineer-verified: selected option label "Shared helper (Recommended)"]` | row M1 |
| M19 | Dominance metric and tier choice are token-based, so the set of roots feeding the verdict is fixed under both fixes. Tier choice (`root_w5m >= root_w1h`), the no-data test, and `Share` come from the token counters `w5m_by_origin_root` and `w1h_by_origin_root`, not from the dollar volumes M6 changes. If `Share` were dollar-based, a larger denominator could move a root into `excluded(near-tie)` and turn a `decline` into an `adopt`. | `[verified: transcript-analysis.py:7223-7241]` | rows M4, M6 |
| M20 | Per-root `clears` is monotone in both fixes. M6 only grows the volume (every multiplier is at least 1) and leaves the net unchanged. M2 can only lower `net_primary` and `net_sensitivity` and raise `Z`, with `W1h` fixed (M4). No root's `clears` therefore goes from False to True, which makes "neither fix newly produces `adopt`" a code-derived property rather than a claim about any corpus. | `[verified: transcript-analysis.py:6397-6408, 6463-6474; test_transcript_analysis.py:10903-10911 pins only the net_primary == 0 boundary]` | row M2 |
| M21 | The helper keeps its `gap_seconds is None` and `is_first_call` guards. `_cache_rebuild_gap_seconds` returns `None` for an unparseable or negative gap, and the classifier's own `None` short-circuit no longer runs first on the `--ttl-verdict` block's path, so dropping the helper's `None` guard makes the comparison `idle_5m_boundary_seconds <= None` raise `TypeError` and abort the whole report. A negative-gap record reaches the block under any `--since`; an unparseable-timestamp record reaches it only when `--since` is unset, because the block sits behind the `in_scope` check and the CLI default `--since 30d` puts such a record out of scope. | `[verified: transcript-analysis.py:6168-6178, 6198-6199]` | row M1 |
| M22 | Fact: after M2 no production call site passes `idle_5m_boundary_seconds` to `_classify_cache_rebuild_cause`; only the two direct classifier tests do. | `[verified: grep for idle_5m_boundary_seconds over transcript-analysis.py and tests; the only production passer is the call M2 removes]` | row M2 |
| M23 | Choice: the classifier keeps the parameter and forwards it to the helper, rather than retiring it and the two direct tests. Retiring is a net-negative diff that removes a forwarding guard, so it is set aside to keep the change small; the classifier's own docstring is reworded so it no longer says `--ttl-verdict` reaches the override. | `[unverified]` | row M22 |

### Prescribed code shape

New predicate, placed immediately above `_classify_cache_rebuild_cause`. Its docstring states the durable fact only — the band, and which question each caller asks:

```python
def _cache_rebuild_in_idle_5m_1h_band(
    is_first_call: bool, gap_seconds: float | None,
    *, idle_5m_boundary_seconds: float = _CACHE_REBUILD_IDLE_5M_SECONDS,
) -> bool:
    """Whether one call's own prior-call gap lands in
    [idle_5m_boundary_seconds, _CACHE_REBUILD_IDLE_1H_SECONDS) -- the gap
    test alone, with no cache-write-tier qualifier.

    _classify_cache_rebuild_cause asks whether a 5-minute TTL expiry forced
    this call's cache write, which the call's own write tier does bear on.
    --ttl-verdict's 1h-to-5m direction asks whether a live 1-hour tier
    served this call's prefix as a warm read, which it does not. Both share
    this band.
    """
```

In `_classify_cache_rebuild_cause`, replace `if gap_seconds >= idle_5m_boundary_seconds:` (`:6202`) with a call to that predicate, forwarding `idle_5m_boundary_seconds`. Reword the docstring sentence at `:6192-6194` that says the override serves `--ttl-verdict`'s two-point sensitivity check: the parameter is now forwarded to the band predicate and passed only by the direct tests. Nothing else in that function changes.

In the `--ttl-verdict` block, replace the comment at `:6818-6822` and both flag computations with two calls to the predicate. The replacement comment states two facts, one sentence each:

```python
# The idle flags here are the gap test alone.
# A call's own cache-write tier gates whether a 5-minute expiry
# forced its write, not whether a live 1-hour tier served its read.
```

For the volume fix, keep the name `dollars_by_class`, hoist its `None` initializer above the `if in_w5m_branch or in_w1h_branch:` guard, and add one comment at the first volume site:

```python
# Reuses _price_turn's own priced classes so the margin denominator
# carries the same fast-mode/US-geo multipliers the net's own
# per-call delta already applies.
```

## Critical files

Single `code-writer` dispatch; all paths relative to the repository root.

**Modify — `claude/.claude/scripts/transcript-analysis.py`**
- New `_cache_rebuild_in_idle_5m_1h_band` above `_classify_cache_rebuild_cause` (`:6181`); one-line substitution inside that classifier at `:6202`, plus the docstring rewording at `:6192-6194`.
- `--ttl-verdict` block in `_cache_rebuild_report`: comment + both flag computations at `:6818-6827`; `dollars_by_class` hoist at `:6843-6847`; volume accumulation and dead `rates` local at `:6851-6853` and `:6872-6874`.
- Reuse, do not reimplement: `_price_turn` (already called in this block — its `dollars_by_class["cache_write_5m"]` / `["cache_write_1h"]` are exactly the multiplied volumes needed), `_CACHE_REBUILD_IDLE_1H_SECONDS` / `_CACHE_REBUILD_IDLE_5M_SECONDS` / `_CACHE_REBUILD_TTL_SENSITIVITY_BOUNDARY_SECONDS` (`:6057-6066`), `_cache_rebuild_1h_to_5m_delta_dollars` and `_cache_rebuild_margin_clears` (unchanged — both already correct given correct inputs).
- Do not touch: `_classify_cache_rebuild_cause`'s `pure_1h_tier_write` semantics, the cache-miss-reason cross-tab at `:6893`, `in_w1h_branch`'s read-token clause at `:6835`, and every default-path accumulator at `:6784-6807`.

**Modify — `claude/.claude/scripts/tests/test_transcript_analysis.py`**, four new classes placed beside the existing `--ttl-verdict` classes (`TestCacheRebuildTtlVerdictSensitivityBoundary` at `:12026` is the nearest model for arithmetic-in-docstring style). Each class's docstring derives its fixture arithmetic, using synthetic token counts only. Tests marked *fail-first* must fail on the pre-fix tree; tests marked *guard* pass both before and after by design.

1. `TestCacheRebuildTtlVerdictPure1hWriteInIdleBand`
   - *fail-first:* an in-band (300s–3600s gap) call carrying both `ephemeral_1h` and `cache_read` on a 1h-tier root → the `X/Z` cell shows those read tokens (reads `0` today);
   - *fail-first:* same corpus → `Net$` carries the restored expiry term, with the arithmetic derived in the docstring;
   - *fail-first:* a call at exactly 300s with the same shape → its reads count toward `Z` (inclusive lower bound);
   - *fail-first:* a gap in [60s, 300s) with the same shape → `Z` stays `0` at the primary boundary but the 60s accumulator gains the expiry term, so `Clears` reads `False`. The sensitivity net is not printed, so `Clears` is the only observable. At default rates the 60s net falls under the 10% margin only when `read / W1h` exceeds `(1.5 − 0.4) / 2.3 ≈ 0.478` (`W1h` including the same call's own write), and cent rounding of `Net$` before the margin test can flip a fixture at that edge. Size the read well above it, for example 0.6 × `W1h`, and state the threshold and the chosen ratio in the docstring;
   - *guard:* a pure-1h write with reads at exactly 3600s (exclusive upper bound), above 3600s, and on a first call → contributes nothing to `Z`;
   - *guard:* the same corpus run **with and without** `--ttl-verdict` → the `idle 5m-1h` row stays `0` and the `unexplained` row stays `1`, restating for the flag-on path the invariant `TestCacheRebuildCacheTierGapMismatch.test_pure_1h_tier_write_in_5m_1h_gap_reclassifies_unexplained` (`:10370`) pins for the flag-off path;
   - *guard:* a `--ttl-verdict` corpus with one negative-gap record, run under a non-`None` `--since` (the production-shaped path) → the report completes and the record contributes nothing to `Z`. Give the record `ephemeral_1h > 0` and `cache_read > 0`: under a `None` gap `is_idle_sensitivity` is `False`, so `in_w1h_branch` needs `eph_1h > 0` and "contributes nothing" is vacuous without it. A second record with an unparseable timestamp runs with `--since` unset, the only form that reaches the block (M21);
   - *guard:* a pure-1h in-band write-plus-read carrying a vendor `model_changed` cache-miss reason, run with `--ttl-verdict` → no cache-miss-reason cross-tab line prints and the cause table's `idle 5m-1h` row stays `0`. This pins the decision in M5 that the cross-tab stays keyed on `cause`, which no existing test covers.
2. `TestCacheRebuildIdle5m1hBandPredicate` — direct unit tests of `_cache_rebuild_in_idle_5m_1h_band`: `300` is `True`, `299.999` is `False`, `3599.99` is `True`, `3600` is `False`, a first call with an in-band gap is `False`, a `None` gap is `False`, and the `idle_5m_boundary_seconds=60` override makes `60` `True` and `59` `False`. Modeled on `TestClassifyCacheRebuildCauseIdle5mBoundaryOverride` (`:10602`).
3. `TestCacheRebuildTtlVerdictTierSplitInBandPure1hRead` — *fail-first*. One mixed-root corpus (a 5m write, then a pure-1h write after a 6-minute gap with a read), asserting the 1h-slice `favors` and the `agree`/`disagree` label through `_extract_ttl_verdict_tier_split_line`. The 1h slice changes sign only when `read / W1h_slice` exceeds `(cache_write_1h − cache_write_5m) / (cache_write_5m − cache_read)`, about 0.652 at default rates; a smaller read prints the same line before and after the fix and pins nothing. Size the read above that ratio, and hand-derive the expected `favors` values and label in the docstring.
4. `TestCacheRebuildTtlVerdictRateMultiplierFooting` — *fail-first* for every test in the class. Build one corpus twice, once plain and once with `speed="fast"` on every record, and assert `W5m/W1h`, `X/Z`, `Favors`, `Clears`, `Share` and the bucket verdict are identical across the pair (only `Net$` differs, being a dollar figure). Repeat for `inference_geo="us"`, for both together, on a 5m-tier root (covering `w5m_dollars_by_origin_root`) and on a 1h-tier root (covering `w1h_dollars_by_origin_root`), and for both the `main` and `subagent` origin buckets.
   - **Fixture placement.** A fixture "comfortably away from the 10% margin" passes on the pre-fix tree, because the pre-fix ratio is `m` times the correct ratio and `Clears` differs only when the plain ratio lies in `[0.10/m, 0.10)`. Place each plain corpus just below the margin, inside that band, with headroom from both of its edges: `m = 2` gives `[0.05, 0.10)`, `m = 1.1` gives `[0.0909, 0.10)` (under one percentage point wide), and `m = 2.2` gives `[0.0455, 0.10)`.
   - **Scale.** `Net$` is rounded to cents before the margin comparison, so size the token counts about 100× larger than a minimal fixture so the rounding moves the ratio by well under 0.01 percentage points. Derive the window arithmetic in the docstring.
   - **1h-root fixtures use read-only in-band calls** (no write) so this class does not depend on the idle-band fix. `W1h` comes from a session-start 1h write on the first call, which the band predicate short-circuits.
   - **Subagent-origin cases** set `rec["isSidechain"] = True` on every record (the idiom at `test_transcript_analysis.py:11066-11072`) and write an empty main-session file. Without the flag every record lands in the `main` bucket and the pair passes vacuously.
   - **Absolute assertions on the plain run**, in addition to pair-equality: the intended bucket's `Tier`, a non-zero `W5m/W1h`, `Clears == "False"` on both runs, and a hand-derived plain-run `Net$` from the docstring arithmetic. The flip windows depend on the live price table, so a price change or a mis-bucketed fixture must fail loudly rather than degrade to a green class that guards nothing.
   - **One mixed fixture per denominator** (a 5m-tier root and a 1h-tier root) interleaves `fast`, `us` and default-rate records in a single root, with per-record weights and the correct-ratio window derived in the docstring, so the margin flips if any one record's multiplier is dropped. The window is `[0.10 × (1 − s_min), 0.10)`, where `s_min` is the smallest single-record multiplier-excess share of the volume.
   - Reuse: `_priced` (`claude/.claude/scripts/tests/conftest.py:368`, already takes `cache_read`, `ephemeral_1h`, `ephemeral_5m`, `speed`, `inference_geo`, `ts`), `_write_jsonl` / `_write_subagent_jsonl`, `_cache_rebuild_args(ttl_verdict=True)`, `_extract_ttl_verdict_root_row`, `_extract_ttl_verdict_summary`, `_extract_cache_rebuild_row`.
   - Existing fixtures stay untouched: `_ttl_verdict_1h_tier_non_wash_disagreement_records` (`:8715`), `_ttl_verdict_dominant_1h_mixed_root_records` (`:8756`) and `TestCacheRebuildTtlVerdictSensitivityBoundary` were each spot-checked against the new predicate — their pure-1h writes are either session-start calls (short-circuited by `is_first_call`) or carry `cache_read=0`, so no expectation moves.

**Also in `test_transcript_analysis.py`, two docstring updates to existing tests** (descriptions of current behavior, not records):
- `test_hand_computed_w1h_and_z_totals_match_known_fixture` (`:11219-11221`) describes a pure-1h write after a 6-minute gap as "reclassifies unexplained ... adds to W1h but not Z". After the fix that call adds `0` to `Z` only because its `cache_read` is `0`; reword the docstring to say so, so it does not read as contradicting the fix.
- `TestClassifyCacheRebuildCauseIdle5mBoundaryOverride` (`:10602-10607`) says the override is "exercised only indirectly elsewhere (via --ttl-verdict's sensitivity accumulation)". After M2 the `--ttl-verdict` block no longer calls the classifier, so reword it to say the override is forwarded to the band predicate, that the predicate's own class (item 2 above) covers the band edges, and that `TestCacheRebuildTtlVerdictSensitivityBoundary` covers the block's wiring of the sensitivity boundary.

**Modify — `docs/transcript-analysis.md`**: one clause at `:993` (the `X`/`Z` write-tier asymmetry) and one at `:995` (the margin denominator's multipliers). No other doc file is edited.

**Do not create**: no new module, no new helper file, no shared multiplier utility.

## Verification

From the worktree root:

```
../../../.venv/bin/python3 claude/.claude/scripts/select-tests.py
../../../.venv/bin/ruff check claude/.claude/
```

`select-tests.py` is the required local command for agents in this repo, and the `../../../` prefix is the documented worktree-relative substitution (`README.md:520`, `:544`). A change to `transcript-analysis.py` selects the whole `claude/.claude/scripts/tests` directory, so the full `--ttl-verdict`, default-path and pricing coverage runs. CI runs the whole suite on push; do not widen the local run by hand.

Behavioral checks the suite must demonstrate:

1. Every test marked *fail-first* in Critical files (in `TestCacheRebuildTtlVerdictPure1hWriteInIdleBand`, including the exact-300s case, all of `TestCacheRebuildTtlVerdictTierSplitInBandPure1hRead`, and every test in `TestCacheRebuildTtlVerdictRateMultiplierFooting`) fails on the pre-fix tree and passes after. Run them once before the `transcript-analysis.py` edit lands and record which failed. Tests marked *guard* pass on both trees by design; do not change a guard to make it fail.
2. `TestCacheRebuildTtlVerdictDefaultPathRegression` (`:12163`), `TestCacheRebuildCacheTierGapMismatch` (`:10364`), `TestClassifyCacheRebuildCauseIdle5mBoundaryOverride` (`:10602`, which fails if the implementer forgets to forward `idle_5m_boundary_seconds` to the helper) and every default-path cause-table test pass — the classifier's behavior did not move. The only edit to any existing test is the two docstring rewordings named in Critical files.
3. `TestCacheRebuildTtlVerdictUnpricedDisclosure` (`:12062`) passes unmodified — per M4, the unpriced counters cannot shift.
4. Every pre-existing `--ttl-verdict` expectation passes unmodified. Two premises carry this: per M8, on a corpus with neither `speed` nor `inference_geo` set the new volume expression is bit-identical to the old one; and no existing `--ttl-verdict` fixture has a pure-1h in-band write that also carries reads, so the idle-band fix moves no existing cell.
5. `git diff --stat` names exactly three implementation files (`transcript-analysis.py`, `test_transcript_analysis.py`, `docs/transcript-analysis.md`) plus this plan file. Nothing under `claude/.claude/settings.json`, `docs/design-decisions/`, or `.claude/plans/cache-ttl-verdict-gate-fix.md` appears.

Then `/code-review` before the commit, per CLAUDE.md. The engineer's own before/after corpus run is a separate local step after this lands; none of its output enters this repo (G3).

## Out of scope

- **Changing or reverting `promptCacheTtl: "5m"` in `claude/.claude/settings.json`.** Relayed by the dispatching session as out of scope (M16). Whether the corrected verdict warrants revisiting the setting is a decision the engineer takes after their own re-measurement, and it would be a separate PR against a different file. The engineer directed otherwise after seeing the corrected verdict: the revert is bundled into this same branch/PR (commit `c68b6f21`) instead of shipped as the separate PR this bullet anticipated — disclosed here rather than left silent.
- **Qualifying `docs/design-decisions/main-bucket-prompt-cache-ttl-5m.md`.** `[engineer-verified: "Don't qualify the doc just yet. I don't want to add too many incremental changes. I want to fix the underlying bug and get a more accurate analysis."]` The design-decision record stands as written until there is a corrected measurement to qualify it with.
- **Editing `.claude/plans/cache-ttl-verdict-gate-fix.md`.** A preserved historical record under CLAUDE.md's Axis 3. The fourth bias channel its line-68 enumeration missed is recorded in this plan's Approach instead.
- **Changing `in_w1h_branch`'s read-token clause (`:6835`) so a mixed root's `Z` is not inflated.** That is the prior plan's own disclosed channel and its own deferred item; it is a different predicate from the one this change fixes, and re-deriving the branch condition needs its own plan.
- **Re-keying the cache-miss-reason cross-tab (`:6893`) to the widened idle population.** Deliberately declined with its reason in M5; it would change a `--ttl-verdict` disclosure count for no gain in the verdict's accuracy.
- **Generalizing `_context_composition_turn_rate_scale` into a shared multiplier helper across all six multiplier sites.** Weighed and set aside in M11; it is a default-path refactor wearing a bugfix's clothes.
- **Re-litigating the dominance threshold, the 10% margin fraction, the 300s/60s boundary pair, the raw-token tiebreaker and its wash rule, or `Z`'s inferred-not-observed status.** All shipped, tested, and pre-registered; this change reopens none of them.
- **Any `docs/cost-levers-considered.md` row, case study, or published figure derived from a corrected run.** Owner-gated and subject to the per-account dimension bar (G3).
