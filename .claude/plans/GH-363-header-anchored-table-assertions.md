# Plan — Header-anchored table assertions in `test_transcript_analysis.py` (GH-363)

## Context

`TestSkillPair` and many sibling test classes in
`claude/.claude/scripts/tests/test_transcript_analysis.py` assert on
`cmd_*` table output by **positional column index** (`cols[1]`, `cols[2:6]`,
`line.split()[3]`). The brittleness GH-363 calls out: a column **reorder** in
the source output silently reads the wrong column, and a logic bug that shifts
a count into the wrong column produces a failure that reads like a formatting
regression instead of a correctness one. (Note: `.split()` already collapses
whitespace, so *width* changes don't break these tests today — column
**reordering** and **insertion/removal** are the real exposure.)

Intended outcome: replace positional indexing with **header-anchored** lookups
across the whole test file, so each assertion names the column it checks. A
column reorder then either keeps tests correct (lookup by name) or fails
meaningfully. Scope is **test-only** — no change to `transcript-analysis.py`.

Decision (confirmed with user): convert the **whole test file**, not just the
`TestSkillPair` suite, and package the parsing as a **shared module-level
helper**.

## Approach

Add one shared helper, `_table_cols`, at module level alongside the existing
`_*` helpers (near lines 18–76). It maps **header label → cell value** by
zipping the header row's tokens with a selected data row's tokens, and **fails
loudly** rather than silently truncating or picking the wrong line:

```python
def _table_cols(out: str, *, header_contains: str, row_contains: str,
                drop_leading_labels: int = 0) -> dict[str, str]:
    """Map column-label -> cell value for the data row matching `row_contains`.

    Anchors column positions to the header row (the line containing
    `header_contains`) instead of hard-coding indices, so a column reorder in
    the source output fails meaningfully rather than silently reading the wrong
    column.

    Precondition: every asserted column's header label AND cell value is a
    single whitespace token (true for all leading label/count columns; trailing
    free-text columns like "Top subagent types" are not assertable this way and
    are not asserted by any test). `drop_leading_labels` lets a caller declare
    that the row deliberately suppresses N leading left-aligned labels (the only
    case: cmd_subagents continuation rows blank the Branch column,
    transcript-analysis.py:688) — declared explicitly per call, never inferred.

    Fails loudly (AssertionError) when exactly one header / data row isn't
    found, or when token counts don't line up — a silent mismatch would
    reintroduce the GH-363 bug class under a new cause.
    """
    lines = out.splitlines()
    headers = [ln for ln in lines if header_contains in ln]
    assert len(headers) == 1, f"header match not unique for {header_contains!r}: {len(headers)}"
    header = headers[0]
    rows = [ln for ln in lines if row_contains in ln and ln is not header]
    assert len(rows) == 1, f"row match not unique for {row_contains!r}: {len(rows)}"
    labels = header.split()[drop_leading_labels:]
    values = rows[0].split()
    # Leading single-token columns must align 1:1 with their labels. A trailing
    # free-text column (more value tokens than labels) is tolerated only because
    # zip truncates from the left; a value SHORTER than its labels is a bug.
    assert len(values) >= len(labels), f"row has fewer cells than labels: {rows[0]!r}"
    return dict(zip(labels, values))
```

Call sites become e.g. `cols = _table_cols(out, header_contains="Lead", row_contains="2026-W20")`
then `assert cols["Lead"] == "1"`, `assert cols["Main"] == "0"`,
`assert "0.0%" in cols["Pair%"]`. This reads as the column's *meaning*, which is
the GH-363 goal. The `len(rows) == 1` / `len(headers) == 1` guards catch
row-selection collisions (e.g. a `row_contains` that also matches the summary
or separator line) loudly instead of reading the first match.

### Why zip-from-left is sufficient for the "trailing multi-word" tables

`zip` truncates to the shorter sequence and aligns from the left, so the
**leading single-token columns stay correctly mapped even when the header has a
multi-word trailing label** whose value contains spaces. This covers:
- `cmd_subagent_mix` — trailing `Top subagent types` (value has commas/spaces);
  asserted columns `Spawns/CR/PR/RR` are leading single tokens → fine.
- `cmd_audit_routing_shape` D1/D2/D3 — trailing `Output tokens`; the asserted
  output-token value sits one position after the single-token `Turns`/`Streaks`
  label. These route through the existing `_extract_shape_*` helpers (see below).

### The hard cases (4 sites the naive helper can't serve as-is)

1. **`cmd_subagents` sidechain row (test ~3379–3381) — the design crux.**
   The source blanks the `Branch` label on continuation rows
   (`transcript-analysis.py:688`), so the sidechain data row has **one fewer
   token** than the header and every column shifts left by one. The current test
   exploits this (`sidechain_cols[1]` = Opus). A plain header-zip misaligns here.
   **Fix:** the sidechain-row call passes `drop_leading_labels=1` **explicitly**
   — the caller declares the one known suppressed column rather than the helper
   inferring it from a token-count difference. (An earlier draft auto-dropped the
   first label whenever `len(data) == len(header) - 1`; rejected per SDET Finding
   2 — that arithmetic heuristic would silently compensate for a *genuine*
   missing-column bug elsewhere by shifting assertions right, re-opening the
   exact silent-misalignment failure GH-363 targets.) The main row
   (`row_contains="main"`, branch present, 6 tokens) uses the default
   `drop_leading_labels=0` and aligns 1:1. The two rows stay selected by the
   `"main"` / `"sidechain"` Thread-value substrings, which are unique.

2. **`_extract_corpus_class_tokens` (helper, ~1504–1514) → `cmd_audit_routing`
   corpus aggregate.** Header `Class  Output tokens  Cache read tokens` has
   multi-word labels, so label-keying by the *full* label is impossible. Keep
   this helper's *signature* (many callers) but re-anchor its internal index on
   the **single-token leading word** of the target label: `Output` is unique in
   the header, so `header.split().index("Output")` gives the output-token
   column index without hard-coding `parts[1]`. This keeps the corpus helper
   consistent with the shape helpers below (SDET Finding 4 — no positional
   asymmetry). Comment that `Output` anchors the `Output tokens` column.

3. **`_extract_shape_d1/d2/d3` (helpers, ~1850–1900) → D1/D2/D3 shape tables.**
   Trailing `Output tokens` only. Re-anchor internally: derive the turns/streaks
   index from `header.split().index("Turns"|"Streaks"|...)` and the
   output-token value as the next cell. Keeps the dedicated helpers (used by
   ~15 caller sites) but removes the hard-coded `parts[1]/parts[2]`.

4. **`_extract_shape_d3_xtab` (helper, ~1903–1918) → D3×D1 crosstab.** Header
   `Case  D1 bucket  Turns  Output tokens` — multi-word `D1 bucket` sits
   *before* the asserted `Turns`/output cells. Re-anchor on the single-token
   `Turns` label: `idx = header.split().index("Turns")`, then `parts[idx]` and
   `parts[idx+1]`. The row is already selected by `startswith(case) and bucket in
   line`, so this stays robust without depending on `D1 bucket`'s token count.

### Lighter alternatives considered

The task asks only to defeat positional brittleness; weigh the lightest
mechanism first.

- **Leave the 4 hard sites positional with an explanatory comment.** Lighter,
  but leaves the exact "wrong-column" trap GH-363 names in the highest-risk row
  (`cmd_subagents` sidechain). Rejected: the sidechain shift is precisely the
  silent-misalignment failure mode the issue exists to kill.
- **Change the source to stop blanking the `Branch` label** (so all rows have
  equal token counts). Cleanest conceptually but violates the test-only scope
  and changes user-facing CLI output for an unrelated reason. Rejected.

### Question on the ticket's prescribed snippet

The issue's snippet (`cols_header.index("Lead")` inline per test) is the same
idea but repeated in every method. The shared-helper packaging (user-confirmed)
is DRY where it helps readability and keeps the trailing-multiword / blank-label
handling in one audited place rather than copy-pasted. This is a deliberate
DRY choice over DAMP because the parsing logic (not the assertion intent) is
identical and non-trivial across ~50 sites.

## Critical files

- **`claude/.claude/scripts/tests/test_transcript_analysis.py`** (only file
  changed):
  - Add `_table_cols` module-level helper (with the suppressed-label handling).
  - Convert the **CLEAN sites** to `_table_cols`: `TestSkillPair` (10 sites),
    `TestSkillPairSubagentFile` (1), `TestSubagentMix` (1), commit-gate tests
    (16), handoff-ratio (2), `TestCmdStruggle` (1), `TestSkillInvocation` (10).
  - Re-anchor the **4 helper/hard sites** internally:
    `_extract_corpus_class_tokens`, `_extract_shape_d1/d2/d3`,
    `_extract_shape_d3_xtab`, and the `cmd_subagents` sidechain assertions.
- **Reuse**: keep existing row-selection idioms (`"2026-W20" in ln`,
  `startswith("feat")`, `data_lines[0]`) — they already pick the right row;
  only the *column extraction* changes. Keep the `_extract_*` helper signatures
  so their many callers are untouched.

## Verification

- `../../../.venv/bin/pytest claude/.claude/scripts/tests/test_transcript_analysis.py -q`
  from the worktree — full suite must pass unchanged (behavior-preserving
  refactor; same assertions, robust extraction).
- `../../../.venv/bin/ruff check claude/.claude/scripts/tests/test_transcript_analysis.py`.
- **Add a `TestTableColsHelper` class** with permanent unit tests for
  `_table_cols` itself (SDET Findings 1, 2, 5 — the helper now carries the
  refactor's whole safety property, so it must be tested, not just exercised):
  - column lookup by name on a normal single-token table;
  - `drop_leading_labels=1` correctly maps the `cmd_subagents` sidechain row
    (Opus/Sonnet land right) while the default maps the main row;
  - a non-unique `row_contains` (matches two lines) raises `AssertionError`;
  - a data row with fewer cells than labels (no `drop_leading_labels`) raises
    `AssertionError` — guards against the silent-truncation regression.
- Spot-confirm a reorder fails meaningfully: locally swap two columns in one
  `cmd_*` print and confirm the header-anchored test still reads the right
  values (or fails with a clear column-name mismatch), demonstrating the
  brittleness is gone. Revert the source tweak.

## Out of scope

- No change to `transcript-analysis.py` output format.
- `cmd_buckets`, `cmd_review_trace`, `cmd_judgment_pair`, `cmd_duration`,
  `cmd_pr_link` tables are asserted by substring (`in`), not positional index —
  not in the bug class, left as-is.
- The `ROUTED PAIRS` / `CLASSIFICATION SUMMARY` substring assertions in
  `TestSkillInvocation` stay substring-based.

## PR

Branch (created post-approval per branch-creation): `GH-363/header-anchored-table-assertions`.
PR body includes `Closes #363` to associate the PR with the issue.
