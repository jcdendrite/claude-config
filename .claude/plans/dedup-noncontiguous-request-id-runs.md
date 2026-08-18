# Fix `_dedup_turns_by_request_id` missing non-contiguous same-requestId runs

## Context

`_dedup_turns_by_request_id` in `claude/.claude/scripts/transcript-analysis.py`
only merges same-`requestId` assistant records when they are contiguous in
the transcript; Claude Code sometimes logs one real, once-billed API call's
multi-`tool_use` response as non-contiguous records instead (a `user`
tool_result record lands between two same-`requestId` assistant records
because the harness serializes tool execution one call at a time), and the
function treats the second block as a brand-new turn — double-counting both
turn counts and dollar totals across all 11 call sites built on this
function. The fix must generalize the merge to non-contiguous runs of any
size, while not silently merging a second, ambiguous pattern the brief also
found (two different real actions ~20–45s apart that coincidentally share a
requestId after a hook-denial retry) — so a corroboration check beyond bare
requestId equality gates the non-contiguous case specifically.

## Approach

Rewrite `_dedup_turns_by_request_id` from its current single-pass streaming
loop (which can only see contiguity, so it flushes on the first non-matching
record) to a two-pass grouping algorithm: pass 1 indexes every assistant
record with a non-empty `requestId` by that id, in first-seen order; pass 2
walks the records once more, emitting the merged turn at each group's first
member's position and skipping the rest. A group whose indices are
contiguous merges unconditionally, exactly as today. A non-contiguous group
merges only when every member's `usage` agrees on
`input_tokens`/`cache_creation_input_tokens`/`cache_read_input_tokens` (the
already-established `_USAGE_DRIFT_INVARIANT_KEYS`) *and* `output_tokens` —
the engineer confirmed this corroboration bar this session, over merging on
requestId equality alone, because the brief's evidence showed a genuinely
once-billed non-contiguous run carrying byte-identical usage on all four
classes (unlike a contiguous run, where `output_tokens` ascends and only
completes on the last record), while the ambiguous hook-denial-retry pattern
diverges on `output_tokens`. A non-contiguous group that fails this check is
left unmerged, same as today's behavior for any non-matching-requestId
records. `_merge_assistant_run` itself needs no change: it already just
concatenates whatever record list it's given in order and takes usage from
the last one, regardless of whether that list's members were contiguous in
the source.

**Evidence caveat surfaced in review (staff-backend-engineer):** the
contiguous-run invariant this bar extends (`_USAGE_DRIFT_INVARIANT_KEYS`) is
backed by a 150-transcript / 15,653-run sample; the non-contiguous
"byte-identical across all four usage classes" pattern this bar is modeled
on is backed by the brief's much smaller sample (12 pairs total: 8 in a
146-row window, 2 in a 182-row window, 2 in a 125-row window). The plan does
not claim the larger sample covers the non-contiguous case — cite the actual
12-pair sample when the corroboration bar is discussed in code/commit
message, not the 15,653-run figure, which is about contiguous runs only.
This asymmetry fails closed: if a genuine once-billed non-contiguous run
turns out to report ascending (not identical) `output_tokens` on some real
transcript outside this sample, the bar under-merges (leaves it as two
turns, reproducing today's bug for that subset) rather than over-merging
(collapsing two genuinely-different calls) — the safer of the two possible
error directions, and exactly what the NOTICE-on-rejection logging below is
for: surfacing that under-merge case for a later audit instead of masking
it.

**Two-pass rewrite removes a free safety bound (staff-backend-engineer):**
the current single-pass algorithm can only ever merge *adjacent* records, so
a stray `requestId` collision anywhere else in a session's records is
structurally inert regardless of whether "requestId is unique per API call"
actually holds. The two-pass rewrite indexes matching-`requestId` assistant
records across the *entire* input sequence — which, per the function's own
docstring, may already be a main transcript concatenated with its own
subagent transcripts — so safety for that concatenation now rests solely on
the corroboration bar, not on requestId uniqueness plus a free adjacency
bound. The docstring rewrite (see Critical files) must say this explicitly
rather than only restating "requestId is unique per API call," and must note
the `--since` staleness window (merged turn stamped with its first block's
timestamp) is now unbounded rather than a few records wide, since group
members can be arbitrarily far apart.

Alternative considered: merge non-contiguous runs on requestId equality
alone, matching the function's literally-documented invariant. Rejected —
confirmed by the engineer this session — because it would also merge the
ambiguous hook-denial-retry pattern, silently suppressing a possibly-real
second turn's cost from every downstream report with no way to detect it
happened.

The engineer also confirmed adding rate-limited stderr NOTICE logging (one
per process per decision kind, mirroring `_warn_if_run_usage_drift`'s
existing one-per-process pattern) for both non-contiguous merge and
non-contiguous rejection decisions, so a later audit can tell whether the
corroboration bar is miscalibrated without re-deriving this investigation.

**Assumption ledger**

Root problem: `_dedup_turns_by_request_id` fails to merge a same-`requestId`
assistant run when its records are non-contiguous, inflating turn counts and
dollar totals in every subcommand built on it.

Givens:
- Claude Code's harness sometimes logs one API call's multi-block response
  as non-contiguous JSONL records (interleaving a `tool_result` between
  same-`requestId` assistant records) — a harness logging behavior this fix
  must accommodate, not something this repo's code can change.
  `[verified: claude/.claude/scripts/transcript-analysis.py:4998-5027 docstring, corroborated by brief §2's raw-JSONL evidence]`
- `requestId` is unique per API call (the function's own documented
  invariant) — re-verifying this claim across the full transcript corpus is
  explicitly out of scope per the brief §7; this fix's corroboration bar is
  designed to stay safe without settling that broader question.
  `[unverified]`

Per mechanism:
- Two-pass grouping (index-by-requestId, then re-walk to emit) replaces the
  single-pass streaming loop because the streaming loop cannot see past the
  next record, so it structurally cannot detect a non-contiguous run without
  first materializing all of one requestId's positions. `anchors: root`
  Lighter alternatives considered and rejected: (1) a sliding lookahead
  window over the next N records — fails because a run's members can be
  arbitrarily far apart (the intervening span depends on tool execution
  time, not record count) and a bounded window would just move the same bug
  to "still misses runs wider than N"; (2) a second cleanup pass that only
  re-scans for orphaned non-contiguous singletons after the existing
  single-pass loop finishes — fails because it would need to re-derive the
  same full-corpus grouping this rewrite builds directly, just as an
  after-the-fact patch on top of the buggy pass instead of replacing it, and
  it also would not be able to preserve the merged turn's "appears at the
  first block's position" ordering guarantee that `--since` filtering
  already depends on (see docstring).
- Corroboration bar (`input_tokens` + `cache_creation_input_tokens` +
  `cache_read_input_tokens` + `output_tokens` must match) gates non-contiguous
  merges only; contiguous merges stay unconditional. `anchors: row2`
  `[engineer-verified]` (confirmed via this session's clarifying question)
- Rate-limited stderr NOTICE on both non-contiguous merge and non-contiguous
  rejection decisions, one per process per kind. `anchors: row2`
  `[engineer-verified]` (confirmed via this session's clarifying question)

## Critical files

- `claude/.claude/scripts/transcript-analysis.py`
  - `_dedup_turns_by_request_id` (line 4995): replace the single-pass loop
    with the two-pass grouping algorithm described above. Update the
    docstring — it currently asserts "a run's own records are always
    contiguous" (line 5020), which this fix disproves; also document (a) the
    new non-contiguous corroboration bar, (b) that a rejected non-contiguous
    group stays as separate one-record turns, same as any other
    non-matching records, (c) per the backend review above, that safety for
    concatenated main+subagent input now rests on the corroboration bar
    rather than a free contiguity-only bound, and (d) that the `--since`
    merged-turn-timestamp staleness window is now unbounded rather than a
    few records wide.
  - Reuse `_merge_assistant_run` (line 5054) unchanged — it already accepts
    any ordered list of same-requestId records.
  - Reuse `_USAGE_DRIFT_INVARIANT_KEYS` (line 5111): define the new
    corroboration key set (`_NON_CONTIGUOUS_MERGE_REQUIRED_MATCH_KEYS`) as
    that tuple plus `output_tokens`, rather than duplicating the three key
    names — single source of truth for "which usage classes this file
    treats as run-invariant." Its own comment must cite the 12-pair sample
    (8+2+2 across three windows, per brief §2) backing the non-contiguous
    "usage identical" pattern specifically, not the 15,653-run figure that
    backs the pre-existing contiguous-only invariant it extends.
  - Add one new helper, `_non_contiguous_run_usage_matches`, that checks the
    corroboration bar over a list of records, mirroring
    `_warn_if_run_usage_drift`'s existing pairwise-against-first-record loop
    shape (line 5119) — same shape, different purpose (gates a merge instead
    of emitting a drift warning), so keep them as two distinct functions
    rather than merging their bodies. Compare every member against the
    *first* member (not just adjacent pairs), so a 3+-record group with only
    one divergent member is caught regardless of its position in the group.
  - Add one new logging helper, `_log_non_contiguous_merge_decision(request_id,
    record_count, *, merged: bool)`, for the two NOTICE cases, rate-limited
    via one new module-level set (`_non_contiguous_merge_notices_logged`,
    keyed by decision kind: `"merged"` vs. `"rejected"`) — mirrors the
    existing `_usage_drift_warned` single-bool rate limiter, generalized to
    two independently-rate-limited kinds without duplicating the
    print-and-flag body twice. Each NOTICE's message text must name its
    decision kind explicitly (not just the word "NOTICE"), so a test — or a
    later audit grepping stderr — can distinguish a merge from a rejection.
- `claude/.claude/scripts/tests/test_transcript_analysis.py`
  - `TestDedupTurnsByRequestId` (line 7232): add tests for
    (a) a non-contiguous run (literally assistant → user tool_result →
    assistant, mirroring the brief §2 Pair 1 shape exactly, not two adjacent
    assistant records) with matching usage merging into one turn with
    concatenated content and usage counted once, with the user record's own
    position/content asserted unchanged;
    (b) the same assistant → user → assistant shape but with mismatched
    `output_tokens` — the ambiguous hook-denial-retry counter-case from
    brief §2 — asserting all three records stay separate turns in original
    order; keep (a) and (b) as two distinct tests, not one folded test, since
    they exercise independent mechanisms (type-boundary run termination is
    irrelevant to (a)'s outcome — the two-pass indexer only ever groups
    *assistant* records — while (b)'s non-merge is entirely due to the
    usage-corroboration check; a single folded test can't tell which
    mechanism is under test if a future regression breaks one but not the
    other);
    (c) a 3-record non-contiguous run merging, to confirm the fix isn't
    hardcoded to pairs;
    (d) a 3-record non-contiguous run where two members match and the third
    diverges on one invariant key — asserting no merge — to pin that
    `_non_contiguous_run_usage_matches` compares every member (not just
    adjacent pairs or a majority);
    (e) an interleaved-different-requestId case — records ordered
    `[A req-1, B req-2, C req-1]` where req-1's usage matches across A and
    C — asserting the result is `[merged(A, C) at A's original position, B
    unchanged in its own position]`, to pin that indexing by requestId
    across the full input doesn't disturb a different run's own record that
    happens to sit between this run's members;
    (f) both new NOTICE lines firing on stderr, asserting each message names
    its own decision kind (not just a generic "NOTICE" substring) so a merge
    NOTICE and a rejection NOTICE are distinguishable, and that the two are
    independently rate-limited (a merge NOTICE firing doesn't suppress a
    later rejection NOTICE, since they're keyed separately in
    `_non_contiguous_merge_notices_logged`) — reset that module-level set via
    `monkeypatch`, mirroring `test_non_identical_input_usage_within_run_emits_stderr_warning`'s
    reset of `_usage_drift_warned`.
  - `test_user_record_between_same_request_id_records_prevents_merge` (line
    7336): this test's current body uses *identical* usage on both records
    separated by a user record, so under the fixed behavior it should now
    merge — its docstring and assertion currently encode the exact bug this
    plan fixes. Repurpose it into new test (a) above (rename to reflect the
    new outcome, e.g. `test_non_contiguous_run_with_matching_usage_merges_across_user_record`)
    rather than leaving its old name/docstring in place asserting the
    opposite of what the fixed code does.
  - No other existing test in this class hard-codes contiguity as the merge
    condition (checked all of `TestDedupTurnsByRequestId`'s 10 other tests);
    the call-site tests elsewhere in this file that exercise multi-record
    request-id groups (e.g. `test_multi_record_request_id_group_priced_exactly_once`
    at lines 5841/8067/9592, `test_request_id_group_tool_use_on_later_record_still_one_turn_at_index_zero`
    at 12598, `test_shared_request_id_merges_across_main_and_sidechain_records`
    at 7364) all use contiguous fixtures, so they exercise the unchanged
    code path — the full suite run in Verification is what confirms none of
    them regress.

## Verification

1. `../../../.venv/bin/pytest claude/.claude/` from the worktree — full
   suite must pass, including the rewritten and new
   `TestDedupTurnsByRequestId` cases and every other test that calls
   `_dedup_turns_by_request_id` transitively (the 11 call sites'
   subcommand-level tests).
2. `../../../.venv/bin/ruff check claude/.claude/`.
3. `/code-review`, then `/ready-for-review` to open a PR — do not merge
   (engineer approval required per the brief).

## Out of scope

- The unrelated `transcript-analysis.py cost --top` `AssertionError` under
  multi-root scope (separate brief).
- Rebasing onto or merging with the in-flight
  `transcript-analysis-decomposition` branch — land against `main` as-is.
- Re-touching `token-analyzer.py` — its own double-counting bug (missing the
  dedup call entirely) was already fixed on `token-analyzer-dedup`
  (commit `c863286`, verified present on that branch this session) and is a
  distinct root cause; it inherits this fix automatically since it calls the
  shared function.
- Re-verifying "requestId is unique per API call" as a general claim across
  the full transcript corpus.
- Any change to how `cost`/`audit-routing`/other subcommands *display* their
  output — this fix only changes what counts as one turn.
