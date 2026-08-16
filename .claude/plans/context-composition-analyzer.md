# Context composition analyzer

## Context

**Goal: make the dominant share of context growth measurable, so engineering
effort aimed at reducing token spend targets the part of the bill that is
actually large.**

This is an **internal engineering-prioritization tool**, not an
external-reporting one: its output is a category ranking, and this repo's
redaction posture bars per-project dollar figures, so it cannot produce a
dollar-denominated before/after. Dollar-denominated trend reporting already
exists in `cost`, `cost-trend`, and `cost-ledger`; this subcommand answers a
different question — *what is filling the context* — and feeds decisions about
which lever to build next.

Cache-read replay of accumulated context is the overwhelming majority of
prompt-side tokens (reproduce: `transcript-analysis.py cost --since 30d`,
"Cost by token class"). Every turn re-transmits everything resident, so what a
session accumulates sets what every later turn costs.

The obstacle is attribution. `read-scope` and `subagents` measure tool-result
content, and that is a minority of context growth: read-result tokens measure
at roughly a sixth of cumulative prompt-token growth, all tool results
combined at roughly a quarter (reproduce: `read-scope --since 30d`). The
remaining ~73% — conversation-history replay, assistant text and thinking, the
static system prefix, and skill-body loads — has **no shipped measurement of
any kind**. Reduction effort is currently aimed at the quarter that happens to
be instrumented.

The measurement runs retroactively over transcripts already on disk, so it
does not depend on new sessions being produced.

## Approach

Add a `context-composition` subcommand to `transcript-analysis.py` that
replays each context sequence in record order, classifies every content item
into a closed category taxonomy, and reports each category's share of
**rate-weighted token-turns**: for each turn an item is resident, its
estimated token size multiplied by the price multiplier that item incurs at
that turn, summed across the item's residency. Rate weighting is not optional
detail — the multiplier table spans 20× between a warm cache read and a 1h
cache write, so an unweighted residency count can mis-rank two categories by
an order of magnitude.

### The two aggregation levels, stated precisely

These are distinct quantities and correctness depends on not conflating them:

- **Per-turn resident size.** For turn *t*: Σ `size_i` over items resident at
  *t* — a tokens-only quantity. This is what reconciles against
  `_context_at_turn(t)`. Rate weighting plays no part here.
- **Rate-weighted token-turns.** For item *i*: Σ over each turn *t* of its
  residency of (`size_i` × `rate_multiplier(i, t)`) — a tokens×turns quantity.
  This is what gets aggregated by category and ranked.

**The multiplier is keyed on `(item, turn)`, not on turn alone.** A single
turn's `usage` carries several rate classes at once — the codebase's own
representative record (`test_transcript_analysis.py:7358-7364`) sets
`input_tokens`, `cache_read_input_tokens`, and both cache-write tiers
simultaneously — so no per-turn scalar can express the 20× split between an
item being written into cache and an item being replayed from it. Define:

- `t == item.turn_introduced` → the turn's cache-write rate (the item is what
  got written).
- `t > item.turn_introduced` → the cache-read rate (the item is replayed).

A turn can carry **both** cache-write TTL tiers at once — the cited
representative record has `ephemeral_1h_input_tokens=10` and
`ephemeral_5m_input_tokens=5` together, and `_price_turn` prices them as
separate buckets — so "the cache-write rate" is not a single tier. Define it
as that turn's token-weighted blend of the tiers actually present:
`(eph_1h × 2 + eph_5m × 1.25) / (eph_1h + eph_5m)`. This is a turn-level
scalar, so it does not reintroduce per-item resolution, and it reflects the
dollars actually written that turn. Note this blends *within* the write class
only — it is not the read/write blending rejected above, which diluted the
write rate across long-resident items that were never rewritten.

Then scale by the two turn-level adjustments `_price_turn` applies to every
class, keyed off `usage.speed` and `usage.inference_geo`: fast mode (2×) and
US inference-geo (1.1×). These are uniform within a turn, so they do not shift
ranking *within* one turn, but they do change weighting *across* turns — a
category concentrated in fast-mode turns would be under-weighted without them.

This split is an approximation: cache writes happen at block boundaries, not
exactly per item. It is checkable rather than assumed — the introduced-vs-
resident token split at each turn should approximately match that turn's own
`usage` class split, and a persistent mismatch is a signal the model is wrong.

**Reconciliation identity:** for every turn *t*,
`Σ size_i (items resident at t) + static_prefix = _context_at_turn(t)`.
The residual is therefore a direct measurement of the static prefix (system
prompt, tool definitions, skill listing) — a category no shipped tool
measures. If the residual is not approximately constant across turns within a
context sequence, the model is wrong and the subcommand must say so rather
than print a ranking.

### Context-sequence boundaries

A context sequence ends at a compaction boundary, identified by the exact
structural marker the codebase already trusts for this purpose:
`type == "system"` and `subtype == "compact_boundary"`. Residency counters
close at that boundary; no item accrues token-turns past its eviction. This
applies **within** the main-thread file as well as across sidechain files —
file-level partitioning alone would score an in-session-compacted main thread
as one continuous prefix spanning the reset.

### Redaction contract

Mirror **`context-distribution`**, not `cost`. `cost` builds a redact map and
auto-emits per-project and per-account label tables; `context-distribution`
builds no redact map and emits no project label at all. Concretely: one
corpus-wide aggregate with **no per-root, per-account, or per-project
breakdown**; `--no-redact` registered for multi-root-refusal parity; and an
explicit `_DO_NOT_PUBLISH_BANNER` print — the banner is hand-duplicated across
the existing `cmd_*` call sites rather than centralized in
`_resolve_cost_roots`, so reusing that helper does not inherit it.

### Alternatives set aside

A `PostToolUse` hook appending a ledger row per ingestion was rejected on
three grounds: it can only observe tool results (the ~27%, not the ~73%); it
produces nothing until new sessions accumulate; and hook payloads carry no
token counts, so it would estimate anyway. Extending `read-scope` was rejected
because its cohort model is `Read`-call-specific and every non-tool category
falls outside its frame. `context-distribution` was rejected as the host
because it buckets sessions by peak context without decomposing what filled
it — though its *redaction* pattern is the one adopted above.

### Assumption ledger

**Root problem:** the majority of context growth — and therefore of cache-read
cost — is unattributed, so reduction effort cannot be aimed.

**Givens** (fixed, outside this plan's reach):

- The API reports `usage` once per assistant turn, never per content block.
  Anthropic sets this; no transcript field decomposes a turn's input tokens
  across the items composing it. Per-item token counts are estimates by
  construction, not a defect of this design.
- Claude Code's on-disk transcript format (record shapes, `requestId`
  semantics, `compact_boundary` emission, sidechain layout) is vendor-owned
  and may change without notice.

**Mechanisms:**

| Mechanism | Anchors | Justification |
|---|---|---|
| New `context-composition` subcommand | root | Composition is a new question, not a refinement of an existing report; no shipped subcommand decomposes non-tool context. |
| Rate-weighted token-turns, keyed `(item, turn)` | root | Cache-read is priced per token per turn resident and the multiplier spans 20×; size alone mis-ranks categories, and a per-turn scalar cannot express a mixed-class turn. |
| Reconciliation against `_context_at_turn` | row 1 | Converts an estimate-based report into a falsifiable one, and yields the static-prefix measurement as its residual. |
| `compact_boundary` for sequence splitting | row 3 | An exact structural marker already trusted at two sites; inferring compaction from a magnitude threshold would need empirical tuning this dissolves. |
| Reuse `_dedup_turns_by_request_id` | row 1 | Counting per raw record inflates by content-block count. |

**Assumptions:**

| # | Assumption | Tag |
|---|---|---|
| 1 | Tool results ~27%, non-tool content ~73% of context growth | `[verified: read-scope --since 30d, this session]` |
| 2 | Cache-read dominates prompt-side token volume | `[verified: cost --since 30d, "Cost by token class"]` |
| 3 | Per-turn `usage` is a cumulative snapshot, not a delta | `[verified: transcript-analysis.py:4966 _context_at_turn]` |
| 4 | Assistant records split per content block sharing one `requestId`, identical input/cache values | `[verified: transcript-analysis.py:4983, measured across 150 transcripts / 15,653 multi-record runs]` |
| 5 | `token-analyzer.py` does not deduplicate by `requestId` | `[verified: no requestId reference in the file; _walk at :65]` |
| 6 | A single turn's `usage` routinely mixes rate classes — cache-read, both cache-write tiers, and fresh input together — so rate class is a property of `(item, turn)`, not of the turn | `[verified: test_transcript_analysis.py:7358-7364, the codebase's own "representative usage record"]`. `_cache_write_split`'s "never counts both" (:4951) establishes only nested-vs-flat cache-write exclusivity and does **not** support a cache-read/cache-write exclusivity claim. |
| 6a | The identity still holds on mixed turns, because `_context_at_turn` sums all four classes into one token total and the identity is tokens-only | `[verified: transcript-analysis.py:4966-4976]` |
| 7 | Compaction is marked by `type=="system"`, `subtype=="compact_boundary"` | `[verified: transcript-analysis.py:8048 ("Shape confirmed from transcripts"), :6753]` |
| 8 | Rate multipliers span 0.1× (cache_read, :4843) to 2× (cache_write_1h, :4842), with fast-mode 2× and US inference-geo 1.1× applied to every class on top | `[verified: transcript-analysis.py:4841-4843, :4848-4849, applied at :5151 and :5153]` |
| 9 | `chars // 4` is accurate enough to *rank* categories | `[unverified]` — BPE compresses JSON, code, and prose differently, so bias is plausibly systematic per category; turn-total reconciliation cannot detect offsetting per-category errors. Phase 3 tests ranking stability directly. |
| 10 | Thinking blocks stay resident until compaction | `[unverified]` — the harness may strip prior-turn thinking once a tool loop completes, which would shorten residency and concentrate variance in the longest sessions. Not verifiable from this codebase. |
| 11 | Analyzer-first sequencing, accepting that the spend trend continues while it is built and that already-designed levers stay unshipped meanwhile | `[engineer-verified]` — the engineer chose analyzer-first over shipping the levers named in Out of scope. |
| 12 | This tool's output serves internal prioritization only | `[engineer-verified]` |

## Critical files

**Create:**

- `claude/.claude/scripts/tests/test_context_composition.py` — see Verification.
  Fixtures are **synthetic only**; never sampled or copied from a real transcript.

**Modify:**

- `claude/.claude/scripts/transcript-analysis.py` — `_classify_content_item`,
  `_scan_context_composition_session`, `cmd_context_composition`,
  `_print_context_composition_report`, and parser registration (siblings
  :9656–:10264). **Also add `"context-composition"` to
  `_SUBCOMMANDS_WITH_OWN_CONFIG_DIR` (:5254).** Omitting it does not raise:
  the `main()` guard at :10304 exits 2 only for tuple members, so a
  non-member's top-level `--config-dir` falls through to reassigning
  `PROJECTS_DIR` (:10318) — which `_resolve_cost_roots` never reads, since it
  calls `config_dir()`. The result is exit 0, no warning, and the default
  account silently scanned instead of the requested one.
- `claude/.claude/scripts/tests/test_transcript_analysis.py` — register
  `context-composition` in `_UNCONDITIONAL_HEADER_CASES` (:15553). Required
  because `cmd_context_composition` uses `_resolve_project_scope`; without the
  entry, scope-header and multi-root threading correctness go silently
  uncovered — no failure, just no coverage.
- `docs/transcript-analysis.md` — document the subcommand.
- `docs/cost-levers-considered.md` — record the per-ingestion-hook rejection
  and its reason, so a later plan does not re-derive it.

**CLI surface** (match the multi-root sibling convention):
`_add_project_scope_args` (`--projects` / `--this-repo`), repeatable
`--config-dir` → `extra_config_dirs`, `--since Nd` (the cost-family form), and
`--no-redact` for multi-root-refusal parity plus an explicit banner print.

**Reuse (do not reimplement):** `_dedup_turns_by_request_id` (:4983),
`_context_at_turn` (:4966), `_cache_write_split` (:4951), `_price_turn`'s rate
table (:4841-4843), `_content_text` (:99), `_read_session_file` /
`_read_session_file_partitioned` (:438 / :403), `_resolve_cost_roots` (:5259),
`_resolve_project_scope` (:2619), `_READ_SCOPE_CHARS_PER_TOKEN` (:6609),
`_FAST_MODE_RATE_MULTIPLIER` (:4848) and `_INFERENCE_GEO_US_RATE_MULTIPLIER`
(:4849) with the `usage.get("speed")` / `usage.get("inference_geo")` checks
`_price_turn` already applies at :5151 and :5153 — read-only reuse, no
refactor of `_price_turn` needed — and the `_MCP_TOOL_BUCKET_LABEL` collapse
(:1196-1197; an MCP server name is a per-account integration identifier and
must not reach a category label).

**Fixture builders — extract to `conftest.py`, do not duplicate.** The
builders `_write_jsonl` (:28), `_asst` (:221), `_user_msg` (:243),
`_bash_use` (:250), `_tool_result` (:254), `_agent_use` (:258) currently live
in `test_transcript_analysis.py`. This repo's precedent is DAMP duplication —
`test_token_analyzer.py` defines its own local `_write_jsonl` — so extraction
is a deliberate departure, named here as CLAUDE.md requires: these builders
encode the `requestId` run-merge shape, which is load-bearing for this scan
and was established by measurement across 150 transcripts. A drifting copy
would keep passing while silently no longer exercising the merge path, which
is not what the readability-earns-repetition license is for.

## Phases

All three phases land in **one PR**, matching how every sibling subcommand
(`context-distribution`, `edit-format`, `read-scope`) shipped its scan, gate,
and redaction logic together. The ordering constraint is mechanical rather
than a matter of discipline: Phase 2's refusal-gate tests and Phase 3's
redaction tests live in the same new test file the PR must ship, so the suite
cannot pass with the subcommand registered but those behaviors absent.

**Phase 1 — Composition scan.** Classify items into a closed, name-agnostic
taxonomy and accumulate per-turn resident size plus rate-weighted token-turns,
per context sequence (main thread and each sidechain scored separately; split
within the main thread at each `compact_boundary`). Residency is computed
**single-pass**: record each item's introduction turn index and its closing
index — the next `compact_boundary` after introduction, **or the sequence's
final turn when no subsequent boundary exists**, which is the common case
since most sessions never compact. Accumulate a prefix sum of the per-turn
**read-class** multiplier across turns, so each item's weighted total is
`size_i × (prefix[close] − prefix[intro] + write_rate_at[intro])` — a
subtraction, an addition for the introduction turn's write-class term, and one
multiply.

Prefix-summing is valid here for a specific structural reason worth stating:
because a sequence ends at the *first* `compact_boundary` after it starts,
`close` is **uniform across every item in that sequence**. "Next boundary
after introduction" is therefore the sequence's own ending turn for every
item, never a per-item search, and the read-class multiplier stays a pure
function of turn. Item-dependence collapses to the single boolean "is this the
item's own introduction turn," handled by the `write_rate_at[intro]` term.
This is `O(items + turns)` per session, matching
the file's existing idiom; a per-item loop over subsequent turns would be
`O(items × turns)` and is not acceptable on 3,000-turn sessions. Retain only
`(category, estimated_tokens, turn_introduced)` per item; discard item text
once sized.

**Phase 2 — Reconciliation gate.** Compare reconstructed per-turn resident
size against `_context_at_turn`. Print the residual as the static-prefix
measurement and its variance as a confidence signal. Above a named threshold
constant, **refuse to print category rankings**. A residual that is negative,
or that grows with turn index, falsifies the model — stop, do not tune.

**Phase 3 — Report.** Aggregate-only, corpus-wide, no per-root or per-project
breakdown. Includes the ranking-stability check below. Naming which lever to
pull is a judgment made from the report, not encoded here.

## Verification

- `../../../.venv/bin/pytest claude/.claude/` and
  `../../../.venv/bin/ruff check claude/.claude/` from the worktree.
- **Reconciliation identity, non-tautologically.** The fixture's `usage`
  numbers and its target static-prefix constant must be hand-picked
  independently of `chars // 4` applied to the same fixture text; assert the
  code recovers that exact constant. Deriving fixture `usage` from the same
  formula proves only self-consistency.
- **Ranking stability under estimation bias.** Two categories of near-equal
  character count but deliberately different content shape (dense multibyte /
  JSON vs. ASCII prose). Totals reconciling is not sufficient — offsetting
  per-category errors cancel in the sum. The "uncertain-ranking condition" is
  the Phase 2 refusal gate, not a second mechanism. Assert **both** sides
  separately: a well-separated pair ranks correctly, and an adversarial pair
  trips the gate. A single disjunctive assertion would pass an implementation
  that defaults to "uncertain" for every input without ever ranking anything.
  Additionally spot-check a near-monomorphic category against a real tokenizer
  as a one-time validation, recorded in the PR description — that write-up
  meets the same no-session-ID / no-absolute-path / no-verbatim-excerpt bar as
  every other output here.
- **Refusal gate, both branches.** Below threshold prints rankings; at or above
  refuses. Threshold as a named constant, not inline.
- **Compaction.** Assert an item's counted residency never extends past its
  eviction turn, and that a mid-file main-thread `compact_boundary` splits the
  sequence. Include a large context drop that is *not* a compaction and assert
  it is not misclassified.
- **Last-turn residency.** An item introduced on a sequence's final turn has
  zero subsequent turns; assert the intended treatment explicitly.
- **Rate weighting, including the mixed-class turn.** Two fixtures. First, the
  easy case: two categories with equal raw token-turns but different rate
  classes; assert weighted totals differ in the expected direction. Second —
  the case that actually exercises the `(item, turn)` keying — a single item
  whose residency spans its cache-write introduction turn followed by two or
  more cache-read replay turns, on a session where another item is introduced
  later so that turn's `usage` genuinely mixes classes (mirror the shape at
  `test_transcript_analysis.py:7358-7364`). Assert the weighted total reflects
  the per-turn split, not one blended rate applied across the whole span. The
  first fixture alone passes while proving nothing about the realistic case.
  Because that fixture carries both cache-write TTL tiers at once, also assert
  the write-tier blend resolves as specified — otherwise the test cannot say
  anything about the write side of the split.
- **Introduced-vs-resident split check.** Assert the per-turn split of tokens
  between newly-introduced and already-resident items approximately matches
  that turn's own `usage` class split, and that a persistent mismatch surfaces
  rather than being absorbed silently. The two sides are independently
  sourced — the left from transcript-order bookkeeping, the right from the
  API's own `usage` fields — so this is a real cross-check, not a restatement
  of the model. But it tests the *conjunction* of the write-timing rule and
  the `chars // 4` estimator (assumption 9): a mismatch cannot by itself
  distinguish a wrong write-timing rule from estimation bias that correlates
  with introduced-vs-resident content. Report it as ambiguous between the two
  rather than as a rate-classification bug.
- **Fast-mode and inference-geo weighting.** A fixture with `usage.speed` and
  `usage.inference_geo` set; assert those turns weigh 2× and 1.1× and that a
  category concentrated in them is not under-weighted.
- **Redaction, with a needle.** Inject a recognizable fake absolute path and
  fake session UUID into the fixture; assert those exact strings are absent
  from output. Cover the refusal and warning paths too, not only the happy
  path — no session ID, absolute path, or verbatim excerpt in any message.
  Assert no per-account or per-project **composition** row appears at any root
  count. Scope the assertion to exclude `context-distribution`-style per-root
  scan-summary diagnostic lines (`<subcommand>: account-N: scanned N
  transcripts`), which carry counts only and no composition data — an
  unscoped assertion would either forbid a line the siblings all emit or, if
  loosened to match, stop catching the row it exists to catch.
- **`--config-dir` routing.** Assert `context-composition` is in
  `_SUBCOMMANDS_WITH_OWN_CONFIG_DIR`, so a top-level `--config-dir` exits 2
  with the redirect message rather than silently scanning the default account.
  This failure mode is exit-0 with wrong data, so nothing else would catch it.
- **Category coverage.** Every item lands in exactly one category; an
  unrecognized shape increments an `unclassified` **counter** — count only,
  never content. Debug printing of unclassified item text is an explicit
  non-goal, not merely an oversight to avoid.
- **Real-corpus reconciliation** (manual, not CI — a one-time validation step
  that must not later be cited as regression coverage): confirm the per-turn
  residual is approximately constant within a sequence and plausible in
  magnitude for a static prefix.

## Out of scope

- **The `token-analyzer.py` `requestId` double-count fix.** Real and
  independently verified (`_walk` at :65 has no `requestId` handling, so
  multi-block turns count cache tokens once per block), but the import runs
  one way — `token-analyzer.py` loads `transcript-analysis.py`, not the
  reverse — so it is not required for this plan's correctness. Ships as its
  own PR. Its fixture needs an *ascending* `output_tokens` sequence across
  blocks, since that field's error is superlinear rather than a flat multiple.
- **Acting on the findings.** Choosing and implementing a lever is a separate
  change.
- **Already-designed unshipped levers**, notably `token-cost-reduction.md:303`
  Phase 4 (`/clear` and `/compact` guidance, verified absent from
  `claude/.claude/CLAUDE.md`). A faster path to a lower bill than building
  this analyzer; the engineer elected analyzer-first (assumption 11).
- **Publishing absolute spend or per-project cost tables in this repo.** The
  repo owns its redaction policy and could change it, so this is a declined
  change rather than a fixed condition: `cost-ledger-storage-redesign.md`
  relocated the weekly cost ledger out of the public tree, and per-project rows
  expose engagement timing.
- **Replacing `chars // 4` with a real tokenizer.** Only in scope if the
  ranking-stability check fails; it would add a dependency requiring its own
  approval.
- **Reconciling the stale cost target in `token-spend-reduction.md`**, which
  rests on a vendor price increase `cost-undercount-root-cause.md` later found
  was cancelled.
- **Unimplemented later phases** of `cost-trend-ledger.md` (2–3) and
  `token-cost-reduction.md` (4, 5a). Flagged; not adopted here.
