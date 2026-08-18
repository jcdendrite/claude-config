# Fix token-analyzer.py double-counting multi-block turns

## Context

`token-analyzer.py`'s per-model token totals are inflated whenever a single
API call's response splits across multiple JSONL records. Claude Code writes
one record per assistant content block (thinking / text / tool_use) and every
record from one API call shares a `requestId`; `token-analyzer.py` sums
`usage` off every such record instead of collapsing a `requestId` run into
one turn first, so a multi-block turn's `input_tokens` /
`cache_creation_input_tokens` / `cache_read_input_tokens` get counted once per
block instead of once per turn, and `output_tokens` (which ascends within a
run, completing only on the last record) gets summed across blocks instead of
read once. `transcript-analysis.py` already solves this with
`_dedup_turns_by_request_id`, called by every one of its own token-counting
call sites; `token-analyzer.py` imports `transcript-analysis.py` for
`_read_session_file` but never calls the dedup step. The fix is to call it too.

## Approach

Insert one call to `transcript_analysis._dedup_turns_by_request_id(records)`
in `token-analyzer.py`'s `_walk()`, immediately after
`_read_session_file(jsonl, include_subagents=True)` and before the record
loop — the same position every one of the 9 existing call sites in
`transcript-analysis.py` itself uses (`transcript-analysis.py:1146`, `4663`,
`5748`, `6135`, `6378`, `7215`, `8226`, `8463`, `9122`). No alternative was seriously considered:
`_dedup_turns_by_request_id` is the one existing, tested mechanism for this
exact problem, already a required import in this file, and the call-site
convention is already established elsewhere in the same file it's imported
from — reimplementing dedup locally would duplicate tested logic the
single-source-of-truth rule already forbids.

**Assumption ledger**

- Root problem: `token-analyzer.py` sums raw per-block `usage` records
  instead of per-`requestId` turns, inflating input/cache/output token totals
  on any multi-block assistant turn.
- Givens:
  - Claude Code's one-record-per-content-block, shared-`requestId`-per-API-call
    transcript format is fixed by the product writing the transcripts, not by
    this repo. [another party owns it]
- Mechanisms:
  - `_dedup_turns_by_request_id(records)` called right after
    `_read_session_file(...)`, mirroring every existing call site.
    [verified: transcript-analysis.py:1146,4663,5748,6135,6378 all follow this
    exact two-line sequence] — anchors: root
    - Lighter primitives considered and rejected: (1) dedup inline inside
      `_walk()`'s loop by tracking `requestId` — rejected, it would
      reimplement `_dedup_turns_by_request_id`'s already-tested merge
      semantics (which field comes from which record) a second time, the
      exact duplication CLAUDE.md's single-source-of-truth rule forbids;
      (2) move the dedup call inside `_read_session_file` itself so every
      caller gets it automatically — rejected, `_read_session_file` is also
      called by `transcript-analysis.py` call sites that want raw
      undeduplicated records for other purposes (e.g. `_read_session_file`
      docstring and its `_read_session_file_partitioned` sibling exist
      precisely to keep partitioning and dedup as separate, composable
      steps); baking dedup into the read step would be a wider-scope change
      than this bug needs and risks changing behavior for every existing
      caller, not just this one.
- Per-turn classification flags (`edits`, `task`, `thinking`,
  `judgment_skill`) are unaffected: they're computed with an `if not X: ... in
  content` idempotent check that already tolerates seeing the same block
  information redundantly, and post-merge a turn's `content` is the union of
  its own blocks, not a subset — same net set of blocks seen, so no behavior
  change there. [verified: token-analyzer.py:106-121, transcript-analysis.py
  `_merge_assistant_run` docstring] Test coverage below converts this from an
  inspected claim to a checked one.
- `_walk()`'s `is_side`-gated arithmetic (`fam_out_main[fam] += out`, the
  session-level `sidechain` flag) reads a merged turn's `isSidechain` field,
  which `_merge_assistant_run` takes from the run's *first* record only. A
  run whose records span both a main-thread block and a subagent-file block
  sharing one `requestId` — a real, already-tested case, see
  `transcript-analysis.py`'s `test_shared_request_id_merges_across_main_and_sidechain_records`,
  reachable here because `_read_session_file(..., include_subagents=True)`
  concatenates a session's main file with its own subagent files before
  dedup runs — is classified entirely by whichever file contributed the
  first block, potentially misattributing the rest of that run's tokens to
  the wrong side of `fam_out_main`'s main/sidechain split. This is inherited,
  already-tested behavior of `_dedup_turns_by_request_id` itself, shared by
  every other caller in `transcript-analysis.py`, not a regression this fix
  introduces — changing it is out of scope (see Out of scope). Documented
  here so the ledger's "no behavior change" claim above is scoped correctly:
  it covers the four content-based flags, not this sidechain attribution.
- `--since` windowing: post-merge, `_ts_in_window` is evaluated once per
  turn using the *first* block's timestamp (the merged record's `timestamp`
  field, per `_merge_assistant_run`), not per-block. This narrows edge-case
  inclusion (a turn that started before a `--since` cutoff but whose later
  block landed after it no longer counts) but matches the semantics
  `transcript-analysis.py`'s own `--since`-aware callers already accept.
  [verified: transcript-analysis.py `_dedup_turns_by_request_id` docstring,
  "Merging shifts --since semantics" paragraph] — anchors: root

## Critical files

- `claude/.claude/scripts/token-analyzer.py` — `_walk()`: add
  `records = _transcript_analysis._dedup_turns_by_request_id(records)` right
  after the existing `records = _transcript_analysis._read_session_file(...)`
  line. Reuse: `_dedup_turns_by_request_id` is already imported via the
  existing `_transcript_analysis` module-load shim at the top of the file —
  no new import needed.
- `claude/.claude/scripts/tests/test_token_analyzer.py` — extend
  `_make_assistant` with an optional `request_id: str | None = None`
  parameter (mirroring `test_transcript_analysis.py`'s `_asst` helper), then
  add:
  - **Core reproduction test.** A session whose one API call is split into
    three same-`requestId` assistant records with *ascending, non-identical*
    `output_tokens` (e.g. 50 / 140 / 200) and identical `input_tokens` /
    `cache_creation_input_tokens` / `cache_read_input_tokens` across all
    three — matching real Claude Code usage shape, not
    `test_multi_record_request_id_group_priced_exactly_once`'s
    all-zero-output fixture, which would pass both pre- and post-fix and
    prove nothing about the output-token half of the bug. Assert `_walk()`'s
    per-model `out` equals the *last* block's value (200) specifically —
    not merely less than the raw per-block sum — so a wrong-record-selected
    regression in the merge (e.g. reading usage from the first block instead
    of the last) is also caught, not just an undeduped regression. Assert
    `inp`/`cc`/`cr` equal the single shared per-block value.
  - **`--since` × merge interaction.** A merged run whose first block's
    timestamp is before a `--since` cutoff and whose last block's timestamp
    is after it — assert the whole turn is excluded (windowing follows the
    merged turn's first-block timestamp per `_dedup_turns_by_request_id`'s
    documented semantics), not partially counted. `token-analyzer.py`
    implements its own `_ts_in_window` call in `_walk()`, independent of
    transcript-analysis.py's own since-aware call sites, so their test
    coverage doesn't reach this file's windowing code.
  - **Sidechain multi-block crediting.** A multi-block, same-`requestId` run
    entirely inside a subagent file (`<session>/subagents/*.jsonl`) — assert
    the merged turn's per-family cache-token crediting is correct (once, not
    once per block). `test_walk_credits_subagent_dispatched_cache_tokens`
    (existing) only covers a single-block subagent record.
  - **Non-first-block classification.** A merged run whose qualifying
    `tool_use` block (e.g. an `Edit` or `Skill` invocation) lands on a
    non-first block of the run — assert the corresponding flag (`edits` /
    `task` / `thinking` / `judgment_skill`) is still set, converting the
    ledger's inspected claim above into a checked one.

## Verification

- New test fails against the pre-fix code (sanity-check by running it before
  the one-line fix) and passes after.
- `../../../.venv/bin/pytest claude/.claude/scripts/tests/test_token_analyzer.py -n0`
  (run from the worktree, per this repo's three-levels-deep `.venv` path
  convention).
- `../../../.venv/bin/ruff check claude/.claude/scripts/token-analyzer.py claude/.claude/scripts/tests/test_token_analyzer.py`.
- Manual spot check: run `token-analyzer.py` against a real local transcript
  corpus before and after the fix and confirm the per-model output totals
  drop (they should never rise) on any corpus containing multi-block turns.

## Out of scope

- Correcting previously-quoted token/dollar figures that came from the
  unfixed tool — those live in past chat/PR/report prose, not in this repo's
  tracked files, and re-deriving them isn't a code change.
- The unrelated `_content_text()` duplication between `token-analyzer.py` and
  `transcript-analysis.py` flagged in
  `docs/reports/2026-08-10-repo-quality-audit/findings.md` §7 — a distinct,
  pre-existing finding with no relationship to this bug.
- Changing `_dedup_turns_by_request_id`/`_merge_assistant_run`'s
  first-record-wins `isSidechain` semantics for a run spanning both a
  main-thread and a subagent-file block (see Approach) — that function is
  shared by 9 call sites in `transcript-analysis.py`, all of which already
  accept this behavior; changing it is a far larger blast-radius change than
  this bug's scope and belongs to a separate plan if it's ever worth fixing.
