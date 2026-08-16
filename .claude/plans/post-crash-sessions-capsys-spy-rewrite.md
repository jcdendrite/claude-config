# Rewrite capsys-coupled main() wiring tests in test_post_crash_sessions.py

## Context

Seven tests in `test_post_crash_sessions.py` verify `main()`'s CLI-argument
and config-dir-resolution wiring by running the full CLI and string-scanning
`capsys`-captured stdout for path substrings, counts, or note text — coupling
assertions about *resolution logic* to `render_report`'s *presentation
layer*. A prior audit (on branch `test-pyramid-audit`, out of scope here)
flagged a differently-shaped capsys anti-pattern elsewhere and explicitly
carved these seven out as AMBIGUOUS, not a confirmed defect, because
`main()` has no standalone structured accessor for its resolved
`config_dirs` list to assert against directly. This plan performs the
per-test judgment call the audit deferred, using the same file's own
`test_main_threads_near_boot_hours_into_build_report` as the model spy
technique, and rewrites the tests confirmed to need it. No production
behavior changes.

## Approach

Six of the seven tests get rewritten to monkeypatch the function `main()`
calls immediately downstream (`build_report` or `render_report`) and assert
on the captured call kwargs instead of stdout text — the same technique the
file's own exemplar already uses. The seventh splits into two single-purpose
tests: a thin `main()`-level wiring assertion (via the same spy technique)
and a new direct `render_report()`-level unit test that closes a real,
previously-unexercised coverage gap in that function's own note-selection
logic. This is a strictly lighter mechanism than the current full-CLI +
capsys pattern (skips `render_report`'s formatting and, for one test, the
registry/transcript fixture setup entirely), so no over-powered-primitive
concern applies; the change is confined to the test file.

**Root problem:** capsys-scanning printed report text as a proxy for
`main()`'s own CLI-argument-handling and config-dir-resolution logic,
in the seven tests listed below.

**Givens:** none — every condition below is a scope choice this plan makes
deliberately, not a constraint beyond its own reach; see Out of scope for
both boundaries and why each is declined rather than crossed.

**Mechanisms:**

| # | Mechanism | Anchor | Why |
|---|-----------|--------|-----|
| M1 | Spy on `build_report`'s `config_dirs` kwarg for the 5 config-dir-resolution tests | row R1–R5 | `config_dirs` is the exact list under test (membership, order, dedup) and flows into `build_report` unmodified — asserting on the real Python list is a direct, exact check; the current capsys form can only prove indirect facts (e.g. one test's own comment: "Not asserted by raw path... The count line is the proof this root was actually unioned in") because `render_report` deliberately hides non-explicit declared-root paths |
| M2 | Spy on `render_report`'s `redact` kwarg for the redact-flag test, dropping its registry/transcript fixture setup | row R6 | `render_report`'s own ordinal-redaction behavior is already directly unit-tested (`test_render_report_redact_maps_cwd_and_session_to_ordinals_and_drops_branch`, line 1285) constructing a `Report` directly; this test's only remaining unique value is proving `args.redact` reaches `render_report`, which needs no session/transcript data at all — mirrors the exemplar's own stated rationale for `near_boot_hours` |
| M3 | Split the explicit-notes test into (a) a spy assertion that `config_dirs_explicit` reaches `render_report` as `True`, and (b) a new direct `render_report()`-level test of the note-text branch, with `config_dirs` set to more than one entry | row R7 | `config_dirs_explicit` is never passed to `build_report`, so M1's technique can't apply; spying on `render_report` alone would capture the kwarg but lose the actual note-text claim (the fake never computes real output) — the note-text logic is `render_report`'s own pure function (`_config_dirs_scanned_note`), already exercised directly by 8 sibling tests at lines 1355–1444, none of which cover the `config_dirs_explicit=True` + populated-and-contributing-roots-file combination this test actually guards. The multi-root constraint is load-bearing, not incidental — and the inverse of what an earlier draft of this plan claimed: `_config_dirs_scanned_note` checks `config_dirs_explicit` first, unconditionally (post-crash-sessions.py:978-979), so at `root_count=1` a reordered implementation that checks `root_count` first produces identical output to the correct one (`root_count=1` also satisfies its own `!= 1` guard) — only `root_count>1` makes the two orderings diverge, so the new test needs 2+ `config_dirs` entries, not 1, to actually pin the ordering it guards |

**Assumption ledger:**

- [verified: claude/.claude/scripts/post-crash-sessions.py:861-867] `build_report`'s only relevant kwarg is `config_dirs: list[Path]`, passed through unmodified from `main()`'s locally-built list.
- [verified: claude/.claude/scripts/post-crash-sessions.py:990,1277-1279] `render_report`'s `redact` and `config_dirs_explicit` kwargs come from `main()` as `args.redact` and `bool(args.extra_config_dirs)` respectively; neither reaches `build_report`.
- [verified: claude/.claude/scripts/post-crash-sessions.py:966-987] `_config_dirs_scanned_note` short-circuits to the "passed explicitly" note whenever `config_dirs_explicit` is `True`, before ever consulting `declared_roots_file_state()` — so the note-text claim is fully determined by `render_report`'s own inputs, not by anything computed inside `main()` beyond the boolean.
- [verified: claude/.claude/scripts/tests/test_post_crash_sessions.py:1285] `render_report`'s ordinal-redaction behavior (session-1/project-1 substitution) already has direct, non-`main()` coverage.
- [verified: claude/.claude/scripts/tests/test_post_crash_sessions.py:1355-1444] No existing `render_report`-level test exercises `config_dirs_explicit=True` together with a populated, contributing roots file — R7's proposed new test closes a real gap, not a duplicate. Of the 3 existing tests in that range using `config_dirs_explicit=True` (lines 1360, 1490, 1766), none sets a populated/contributing roots file or asserts note text.
- [unverified] Exact line numbers below are current as of this session's read and will shift again once earlier tests in the file are edited; re-grep before each edit rather than trusting a stale offset.

**Per-test disposition** (line numbers current as of this plan; `main` grep may drift as edits land — re-check before each edit):

| Row | Test | Current line | Disposition |
|-----|------|--------------|-------------|
| R1 | `test_main_always_scans_default_config_dir_first` | 1879 | REWRITE (M1): assert `captured_kwargs["config_dirs"][0] == default_dir` — also newly proves *first*, which the current substring check never did despite the test's name |
| R2 | `test_main_dedupes_default_config_dir_supplied_again_explicitly` | 1890 | REWRITE (M1): assert `captured_kwargs["config_dirs"] == [default_dir]` — exact-list, not just length, for parity with R5's precision (a bare length check would pass if dedup dropped the correct dir but kept a wrong single element) |
| R3 | `test_main_scans_declared_roots_by_default_when_no_config_dir_flag` | 1901 | REWRITE (M1): assert `declared_dir in captured_kwargs["config_dirs"]` and `len(...) == 2` — replaces the current count+absence indirect proof (and its cross-referencing comment) with a direct membership check |
| R4 | `test_main_declared_sessions_only_root_is_not_silently_dropped` | 1923 | REWRITE (M1): same shape as R3, for `sessions_only_dir` |
| R5 | `test_main_explicit_config_dir_overrides_declared_roots_default` | 1947 | REWRITE (M1): assert `captured_kwargs["config_dirs"] == [default_dir, explicit_dir]` — exact list, order, and exclusion in one assertion |
| R6 | `test_main_redact_flag_produces_ordinal_output` | 2032 | REWRITE (M2): spy on `render_report`, assert `captured_kwargs["redact"] is True`; drop the registry/transcript/dead-pid fixture setup entirely (unneeded once `render_report` itself is stubbed) |
| R7 | `test_main_config_dir_explicit_notes_override_not_roots_file_state` | 1446 | SPLIT (M3): keep a thin `main()`-level spy test asserting `captured_kwargs["config_dirs_explicit"] is True`; add a new `render_report()`-level test (in the note-branch block, ~1391–1444) with a populated declared-roots file and `config_dirs_explicit=True`, asserting the note text |

No test is left capsys-based for the wiring facts these seven exist to
check — but `test_main_end_to_end_prints_resume_command_for_crashed_session`
and `test_main_smoke_against_live_environment_no_traceback` (not in this
list) legitimately stay as full-output integration/smoke checks and are
untouched.

## Critical files

- `claude/.claude/scripts/tests/test_post_crash_sessions.py` — all edits land
  here. Reuse: the `monkeypatch.setattr(_mod, "build_report"/"render_report",
  fake_fn)` pattern from `test_main_threads_near_boot_hours_into_build_report`
  (line 2062) as the literal template for M1/M2/M3's spy half. Fake return
  values differ by target — `main()` unconditionally does
  `print(render_report(...))`, so a stubbed `render_report` must return a
  `str` (a short literal is fine, its content is irrelevant to R6's
  assertion): R1–R5 (M1) fake `build_report` and reuse `_blank_report()`
  (already used at lines 1489, 1521, 2072) as its `Report` return value; R6
  (M2) fakes `render_report` and must return a plain string instead.
- No production file changes (`claude/.claude/scripts/post-crash-sessions.py`
  is read-only for this work).

## Verification

- `../../../.venv/bin/pytest claude/.claude/` — full suite, from the
  worktree.
- `../../../.venv/bin/ruff check claude/.claude/` — lint.
- Manually diff each rewritten test's assertions against its pre-rewrite
  version to confirm no scenario coverage was silently dropped. Every row
  (R1–R7) must keep its original `exit_code == 0` check — not only R6, whose
  setup simplification makes the risk most visible.

## Out of scope

- `TestReviewTrace` / `test_transcript_analysis.py` — separate, in-flight
  work on `test-pyramid-audit`.
- Any change to `post-crash-sessions.py`'s actual CLI behavior, including
  adding a standalone structured accessor for `main()`'s resolved
  `config_dirs` list. That refactor is technically reachable (it's a small
  addition to `main()`, not a platform boundary) and would let R1–R5 assert
  directly with no spy at all — but the brief scopes this pass to
  assertion-mechanism fixes in the test file only, not production refactors,
  so it's declined here rather than pursued.
- Blanket conversion of every capsys-based test in the file — only the seven
  rows above are touched; `test_main_end_to_end_prints_resume_command_for_crashed_session`
  and `test_main_smoke_against_live_environment_no_traceback` stay as-is.
