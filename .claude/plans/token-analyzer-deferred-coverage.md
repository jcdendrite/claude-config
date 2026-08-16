# Close PR #671's deferred token-analyzer.py test-coverage findings

## Context

PR #671 rewrote `test_main_scans_every_declared_root` off capsys stdout
parsing but deferred three `staff-sdet` findings as out of scope, since that
PR's own declared scope (per `test-pyramid-audit.md`) was test-only with "no
production code changes." This plan closes all three: extract main()'s
report-rendering block into a testable function and cover it, add a
`--since`-wiring test at the `main()` level, and add the zero-declared-roots
variant of the roots-wiring test.

## Approach

Extract `token-analyzer.py`'s report-printing block into a pure
`_render_report(ft, sessions, label) -> str` function that `main()` prints,
then test that function directly instead of capsys; add the two missing
`main()`-level wiring tests using the recording-stub pattern PR #671's own
`test_main_scans_every_declared_root` already established.

### Assumption ledger

**Root problem:** PR #671 deferred three `staff-sdet` test-coverage findings
(report-rendering, `--since` wiring, zero-root wiring) as out of scope; this
plan closes them without changing `token-analyzer.py`'s user-facing CLI
output.

**Mechanisms:**

1. **Extract `_render_report(ft, sessions, label) -> str`** from `main()`'s
   print block (`token-analyzer.py:163-206`), replacing the sequence of
   `print()` calls with one string built via `"\n".join(...)` that `main()`
   prints once. `anchors: root`. Enables asserting on a return value instead
   of capsys stdout, matching the anti-capsys-logic-proxy convention PR #671
   itself just established for `test_main_scans_every_declared_root`, and
   the existing build-string-then-print split
   `_resolved_scope_header`/`_print_resolved_scope` already uses in
   `transcript-analysis.py:2739-2766`.
   - Lighter primitive considered: keep the block inline and test it via
     `capsys`. Rejected — this is the exact anti-pattern PR #671's commit
     just removed from this same test file; reintroducing it for the
     remaining untested block would contradict the convention this repo just
     paid down.
   - Lighter primitive considered: monkeypatch `builtins.print` to record
     call arguments instead of extracting a function. Rejected — still
     couples tests to call count and ordering rather than the actual
     formatted content, and is more brittle than a plain return-value
     assertion for no benefit (no production code stays unchanged either
     way).

2. **Add `_render_report` unit tests**, each pinning a specific behavior
   rather than a loose "covers the section" assertion:
   - Per-model summary rows: a fixture with only `haiku` and `opus`
     populated (sonnet/other absent), asserting the opus row appears before
     the haiku row (iteration order `("opus", "sonnet", "haiku", "other")`
     is significant and easy to silently break under a future
     `ft.items()`-style refactor) and that the absent families produce no
     row.
   - Top-10 truncation boundary: 11 hand-built sessions with distinct `out`
     values, asserting the returned report lists exactly 10 rows *and* that
     the specific lowest-`out` (11th) session's id is absent — not just a
     row count.
   - Candidates exclusion-breakdown line: a fixture where every excluded
     session is excluded via exactly one dimension (e.g. only `edits`),
     asserting that dimension's label appears in the breakdown line and the
     other five `_excl_labels` do not — a shallow "some label appears"
     assertion would pass even if the filter always listed all six.
   - Candidates section empty branch: zero qualifying sessions, asserting
     the literal `"None found."` line appears.
   - Full-report whitespace fidelity: one end-to-end test exercising every
     section at once (≥2 model families, ≥11 sessions, both a non-empty
     `non_cands` and a non-empty `cands`) asserting the exact returned
     string — including blank-line spacing between sections and trailing
     newline count — against a literal expected string. Required because
     `"\n".join(...)`-based extraction does not automatically reproduce the
     current code's blank-line choreography (today's spacing comes from
     `\n` characters embedded inside individual f-strings, e.g. `f"\n##
     Top 10 sessions...{label}\n"`, combined with `print()`'s own trailing
     newline); an incorrect split could silently drop or double a blank
     line. This test is the actual fidelity check — the manual smoke run in
     Verification is a supplementary sanity check, not a substitute for it.
   - Hand-built `ft`/`sessions` fixtures reuse one local factory helper
     mirroring `_walk()`'s real session-dict shape (all of
     `id/proj/fam/out/inp/plan/edits/task/thinking/judgment_skill/sidechain`),
     not one-off per-test dict literals — avoids fixture drift from
     `_walk()`'s actual output shape if a field is added later.
   `anchors: row1` — these tests don't exist until mechanism 1 exists.

3. **Add a `--since`-wiring test at `main()`** using the same
   recording-stub-on-`_walk` pattern `test_main_scans_every_declared_root`
   (`34a1ec2`) already established for roots, capturing the `since=` value
   `main()` actually passes. Bracket the assertion to avoid flakiness:
   capture `before = time.time() - 2 * 86400` immediately before calling
   `main()` and `after = time.time() - 2 * 86400` immediately after, then
   assert `before <= captured["since"] <= after` — a single-`time.time()`
   -call-with-tight-delta approach is a plausible (if rare) CI flake source.
   `anchors: root`.
   - Lighter primitive considered: infer `--since` wiring indirectly by
     asserting on the printed `"(activity in the last 2d)"` label. Rejected
     — reintroduces stdout-parsing for a value this file already has a
     direct, non-parsing capture mechanism for (the same recording stub used
     for roots).

4. **Add a zero-declared-roots single-root variant** of the roots-wiring
   test, mirroring `test_main_scans_every_declared_root` but relying on
   `conftest.py`'s existing autouse `_isolate_transcript_corpus_lookups`
   fixture default (no `TRANSCRIPT_CONFIG_DIRS_FILE` write → `declared_roots_file_state()`
   returns `"absent"`), asserting `_walk` is called with `roots == [active]`.
   `anchors: root`.

## Critical files

- `claude/.claude/scripts/token-analyzer.py` — extract `_render_report(ft,
  sessions, label) -> str` from `main()` (lines 163-206); `main()` calls
  `print(_render_report(ft, sessions, label))` in place of the removed
  `print()` sequence. No change to `_walk()`, `_pct()`, `_fam()`, or the
  resolved-scope header call.
- `claude/.claude/scripts/tests/test_token_analyzer.py` — add:
  - `_render_report` tests per mechanism 2 above (per-model row order incl.
    a skipped family, top-10 truncation boundary, exclusion-breakdown
    presence/absence, empty-candidates branch, full-report whitespace
    fidelity) — reuse `_make_assistant`/`_write_jsonl` where a test needs
    `_walk`'s real output, or the new shared session-dict factory helper
    where it doesn't.
  - `--since`-wiring test — reuse the recording-stub pattern already in
    `test_main_scans_every_declared_root` (same file, `_recording_walk`
    closure shape), capturing `since` instead of (or alongside) `roots`,
    bracketed per mechanism 3 above.
  - Zero-declared-roots wiring test — reuse
    `test_main_discloses_resolved_scope_at_one_root`'s no-declared-file setup
    for the isolation half; reuse `test_main_scans_every_declared_root`'s
    recording-stub for the wiring-assertion half.

## Verification

- `../../../.venv/bin/pytest claude/.claude/scripts/tests/test_token_analyzer.py -q`
  — the new full-report whitespace-fidelity test (mechanism 2) is the
  automated check that the extraction preserved blank-line spacing and
  trailing-newline count; it is not left to manual verification.
- `../../../.venv/bin/ruff check claude/.claude/scripts/token-analyzer.py claude/.claude/scripts/tests/test_token_analyzer.py`
- Manual smoke: `claude/.claude/scripts/token-analyzer.py --since 7d` against
  a real `~/.claude` config dir, as a supplementary sanity check alongside
  the automated whitespace-fidelity test above.

## Out of scope

- Further `_walk()` test coverage. `_walk()`'s token-accounting and
  session-derivation logic already has comprehensive test coverage
  (per-model totals, request-ID dedup, sidechain handling, `--since`
  windowing, multi-root union) in the existing suite — this plan's new tests
  add coverage for the rendering and `main()`-wiring layers only, which is
  what PR #671 actually deferred.
- A boundary test for the pre-existing `opus_high`'s `out >= 500` threshold
  (`token-analyzer.py`'s candidate-eligibility cutoff) — this threshold
  predates this plan and isn't one of PR #671's deferred findings; flagged
  as a cheap, in-scope-adjacent follow-up, not required here.
