# Plan: collect and lint `evals/` in CI, and fix the four git-version-sensitive mining tests

## Context

Goal: close GitHub issue #1163. CI collects and lints the deterministic `evals/` tests, the test and lint docs match `tests.yml`, and the four `evals/test_review_bench_mining.py` tests that fail on git 2.45 and later pass.

Ask: "add evals tests that don't require Claude -p to the CI, and fix the four failing tests. Yeah that all is part of 1163" (engineer). Issue #1163 carries decisions D1, I4, C17, C26 and I2 from the 2026-08-22 discovery-audit triage.

CI runs pytest on `claude/.claude/ claude-skills/ plugins/` only, so `evals/` tests are never collected and the four failures were invisible. Git 2.45 changed `%aI` (strict ISO) to print a zero UTC offset as `Z` where earlier versions print `+00:00`. The miners store git's text unchanged as `fix_date`, so four tests fail on a machine with a new git. The architect's adversarial review of the "normalize in code" choice returned Modify: keep the choice, put the normalization in one shared helper, and unit-test it.

## Approach

Add `evals/` as a fourth collection root to both CI pytest passes and to the Lint step. Guard it with an `evals/fixtures` entry in `pyproject.toml`'s `norecursedirs`, then make `select-tests.py`'s full-suite set and every test/lint doc site match `tests.yml`. Fix the four failing tests in production code: a new `canonical_fix_date()` helper in `evals/review_bench/defects.py`, called by all three `%aI` producers, gives `fix_date` one spelling whichever git version is installed.

**Phase A: `fix_date` normalization (fixes the four failures)**

- `defects.py`: add a public `canonical_fix_date(git_iso: str) -> str` beside `_validate_fix_date` (`defects.py:64-68`). It returns `datetime.fromisoformat(git_iso).isoformat()`.
  - It keeps the author's offset and does not convert to UTC.
  - Unparseable input raises `ValueError`, the same exception `Candidate.__post_init__` already raises for a bad date.
  - Docstring: "Returns git's strict-ISO date in one spelling, whichever form of zero UTC offset (`Z` or `+00:00`) git printed."
- `mine_review_rounds.py:379-387` and `mine_pr_comments.py:496-498` (`_commit_date`): normalize non-empty output only, and keep the existing `or None` branch.
- `mine_szz.py:266`: normalize when building the `Candidate` (`fix_date=` at :284), after the `try` block. `ValueError` is not in `_GIT_CALL_ERRORS` (`mine_szz.py:53`), so a bad value propagates exactly as it does today.
- Leave `mine_review_rounds.py:354` (`--format=%H\x1f%aI`) unchanged. Its output only feeds `corpus._parse_ts` (`claude/.claude/scripts/transcript_analysis/corpus.py:215-221`), which already parses both spellings.
- Tests, all in `evals/test_review_bench_mining.py`:
  - A parametrized unit test of the helper next to `test_bad_fix_date_is_rejected` (:240). Cases: `Z` becomes `+00:00`; `+00:00` is unchanged; `-05:00` is unchanged, which pins "no UTC conversion"; garbage raises `ValueError`.
  - In `TestMineSzz.test_removed_line_blames_to_its_introducer` (:912-928), commit the fix with `_commit_at(repo, ..., "2026-01-04T00:00:00+0000")` and assert `candidate.fix_date == "2026-01-04T00:00:00+00:00"`. This closes the one producer with no `fix_date` assertion.
  - The expected values at :2040 and :2369 stay as they are.
  - Helper test additions:
    - Garbage and empty input both raise `ValueError` with `match="fix_date"`.
    - The match makes the helper's message name the field, as `test_bad_fix_date_is_rejected` at :241 does for `Candidate`.
    - `canonical_fix_date(canonical_fix_date(x)) == canonical_fix_date(x)`.
    - The helper raises its own `ValueError("fix_date ... is not a valid ISO 8601 ...")` rather than leaking `fromisoformat`'s raw message.
  - Git-version-independent producer pin:
    - For each of the three producers, stub the `%aI` git call to print a `...Z` date and assert the stored `fix_date` ends in `+00:00`. The seam is `_fail_git_calls` at :3300-3314.
    - The four real-git tests go red only on git 2.45 or later.
    - Without this pin, a producer could drop its `canonical_fix_date` call and stay green on older git.
  - Empty-output pin: with the stubbed git exiting 0 and printing nothing, both `_commit_date` helpers return `None`.
  - Normalization runs after `.strip()`: a trailing newline makes `fromisoformat` raise, and the real-git tests fail loudly if the order is wrong.
  - Red-first record: run the new `mine_szz` assertion and the producer pins on local git 2.56 before the helper lands and record that they fail, then record green after. A1 alone only demands green.
  - The error-path wording is "the raise site moves": review-rounds raises in `_introduction` before the memo write, and pr-comments raises inside `_commit_date`. No mining path catches `ValueError`, so abort semantics are unchanged.
- Alternatives set aside:
  - The `.replace("Z", "+00:00")` idiom at `mine_pr_comments.py:343` handles one spelling, validates nothing, and would need three copies.
  - `git --date=format:...%z` prints `+0000` without a colon, so Python reformatting would still be needed.
  - Loosening the test assertions: the engineer selected "Normalize in code (Recommended)" over it.
  - Normalizing in `Candidate.__post_init__` would rewrite date-only values (row 8).

**Phase B: CI wiring, collection guard, select-tests, docs**

- `.github/workflows/tests.yml`:
  - Append `evals/` to both pytest lines (:159, :166) and the ruff line (:170).
  - C26: rename the step at :46 to "Detect which checks the diff needs" and the step at :117 to "Skip — no test-relevant changes". Change the :119 echo to "No test-relevant files changed; skipping pytest and ruff."
  - Keep `id: detect` (row 21).
- `pyproject.toml`:
  - Add `"evals/fixtures"` to `norecursedirs`.
  - Reword the comment at :34-37 to say the defaults are restated "alongside the repo's own entries", plus a one-line fact: "evals/fixtures holds a sample project whose test imports a module on no configured pythonpath."
  - Reword the comment at :19-21 to say "the documented full-suite pytest command", and drop the stale permission-grant parenthetical (row 20).
- `claude/.claude/tests/test_pytest_collection_config.py`: after :116, add `assert "evals/fixtures" in configured`, mirroring the existing `worktrees` pin.
- `claude/.claude/scripts/select-tests.py`:
  - Make `FULL_SUITE_TARGETS` (:403) `("claude/.claude/", "claude-skills/", "plugins/", "evals/")`.
  - Make the comment at :399-400 cite `tests.yml`'s pytest steps without restating the command.
  - Delete `_targets_outside_full_suite` (:817-823), its two call sites (:861, :865), and the docstring paragraph about it (:834-837).
- Wall-clock-bound evals tests get the `timing` marker so they run in the serial pass.
  - `pyproject.toml`'s marker definition covers tests that "assert a wall-clock bound".
  - `evals/test_review_bench_adjudicate.py`: `TestAnswerFormatMatchingIsLinearTime`, :804-815.
  - `evals/test_measure_subagent_model_resolution.py`: :465-478 and :513-528.
  - `evals/test_review_bench_runner.py`: :789-818.
  - The implementer also greps the other evals test files for `perf_counter`, `elapsed`, `time.sleep` and subprocess timeouts, and marks any further test that asserts a wall-clock bound.
  - Each addition is listed in the PR body.
- `install-dev.sh:113-114`: the printed "Run tests" and "Run lint" commands carry the same four roots as the root `CLAUDE.md` Commands block.
- `install.sh:954,970`: the printed `pytest` hints carry those four roots too.
  - The block's heading `Optional (contributors): run the hook test suite` stays unchanged.
  - `test_install_sh_contributor_intent_prompt.py:92-117` pins that heading.
- `claude/.claude/scripts/tests/test_select_tests.py`:
  - Replace the two tests at :976-996 (and fold in the :998 claude-path variant) with one test parametrized over both fail-open trigger kinds, `pyproject.toml` and `.gitignore`: an evals path beside the trigger selects exactly `FULL_SUITE_TARGETS`. The parametrization keeps both return sites (`select-tests.py:861` global trigger, :865 unmatched path) covered, so a regression in either alone fails.
  - Replace :1572-1581 with a test that the `evals/` root absorbs `REVIEW_BENCH_TEST_GLOB` and `MEASURE_SUBAGENT_MODEL_RESOLUTION_TEST`.
  - Fix the docstrings at :1259-1263 and :1628-1639.
  - Add two tests to `TestRuleTablePathFidelity` (:2036):
    - (a) Every `DOMAIN_RULES`/`CROSS_DOMAIN_EXCEPTIONS` target sits under a `FULL_SUITE_TARGETS` root.
    - (b) `FULL_SUITE_TARGETS` equals the positional roots of each `tests.yml` pytest step and of the Lint step.
      - Locate steps by parsed YAML step name, as `test_ci_watch_workflow_parity.py:_if_values_by_name` does, with the same missing-step assertion.
      - Assert exactly two steps whose `run` starts with `pytest`.
      - The roots are the `shlex` tokens between `pytest` (or `ruff check`) and the first `-`-prefixed token.
      - Compare the roots as sets of `rstrip("/")` roots.
      - Both pytest passes must carry identical roots.
      - The `WORKTREES_DIR_NAME`↔`norecursedirs` pin at :2048-2058 is the precedent.
    - Factor the root-extraction and the target-under-root predicate into small functions so a unit test can feed synthetic input: one case drops `evals/` from one `run` line and expects (b) to fail, one adds an out-of-root target and expects (a) to fail. These are drift pins and pass on the pre-change tree, so the synthetic cases are what show they are not vacuous.
- Docs:
  - Root `CLAUDE.md:13-14`: append ` plugins/ evals/` to both commands and drop "(hooks + skills)". Realign the comment column inside those lines only, so no lines are added (200-line cap).
  - `README.md`:
    - :515-516: same roots as `CLAUDE.md`.
    - :529: "The bare `.venv/bin/pytest` command above".
    - :539: one added sentence on CI's two-pass split, `timing` tests run serially because they flake under sibling-worker load, plus how to reproduce locally with `-m timing -n0` (I2).
    - :541: drop "plus any domain-selected targets outside its roots (`evals/`)".
    - :101: replace the inline `pytest claude/.claude/` with a pointer to the Tests section.
  - `evals/README.md`:
    - After :32-34, add one sentence: the harness scripts never run in CI; the `evals/test_*.py` unit tests do, because they stub every `claude` subprocess (D1's clarification).
    - Rewrite the Linting section (:392-400): CI lints `evals/` with the rest of the repo, and the local command lives in README's Tests section.
- Alternatives for the CI shape:
  - A separate evals step: a fourth gated step would join the sync set that `test_ci_watch_workflow_parity.py` pins, with no isolation benefit.
  - An explicit list of the seven test files: a new evals test file would silently go uncollected. `select-tests.py:401-402` avoids that same drift for `plugins/` by targeting the directory.
  - D1's single file only: fails row 1.
  - Guard alternatives are under M3.

**Assumption ledger**

Root: CI never collects or lints `evals/`, so four git-version-sensitive `fix_date` tests fail unseen, and the test/lint commands in `CLAUDE.md`, `README.md`, and `evals/README.md` disagree with `tests.yml`.

Givens:
- G1: Git's `%aI` spelling is whatever the installed git prints. The git project owns the format, and this repo pins neither contributors' git versions nor the runner image's.
- G2: The `tests` job id is the required status-check context on `main`. Branch protection owns it (`tests.yml:1-7`), so the job id stays.
- G3: pytest's `norecursedirs` replaces its built-in defaults rather than extending them. pytest imposes this (`pyproject.toml:34-37`), so the new entry joins the restated list.
- G4: The harness scripts spawn real `claude -p` sessions and stay out of CI. `docs/design-decisions/skill-evals-local-only.md` records that decision on cost grounds, and reversing it needs a decision outside this plan.

Mechanisms:
- M1: `canonical_fix_date()` in `defects.py`, called by the three producers. anchors: row3, row6, row7, row8. Lighter primitives checked:
  - The string `.replace` idiom: one spelling only, no validation, three copies.
  - A fixed git `--date=format`: `%z` lacks the colon, so it still needs Python [unverified].
  - Normalizing at construction: fails row 8.
- M2: `evals/` as a root in both CI passes, the Lint step, `FULL_SUITE_TARGETS`, and the docs. anchors: row1, row12, row14, row18. Lighter primitives checked:
  - The single D1 file: fails row 1.
  - An explicit file list: drifts silently.
  - A separate step: heavier, since it widens the parity-pinned `if:` set.
- M3: `evals/fixtures` in `pyproject.toml` `norecursedirs`. anchors: row12, row16, row17, row18. Lighter primitives checked:
  - `--ignore=evals/fixtures` on the `tests.yml` lines: fails row 18.
  - `collect_ignore = ["fixtures"]` in `evals/conftest.py`: works and is narrower, but it is not among D1's confirmed guard forms (:1222-1225) and would be a second exclusion mechanism beside row 16's.
- M4: delete `_targets_outside_full_suite` and pin its invariant with a static test. anchors: row19. Keeping the function leaves dead code whose docstring names `evals/` as its example. The static test turns a future out-of-root domain target into a CI failure instead of a silent gap.
- M5: a parity test between `FULL_SUITE_TARGETS` and `tests.yml`. anchors: root, row18. The only alternative is the comment-only contract at `select-tests.py:399-400`, which this PR would otherwise have to hand-sync. Precedent: `test_select_tests.py:2048-2058`.
- M6: keep `id: detect`. anchors: row21.

Rows:
1. [engineer-verified: "add evals tests that don't require Claude -p to the CI, and fix the four failing tests"] Scope is every deterministic `evals/test_*.py` module plus the four failures.
2. [engineer-verified: "Yeah that all is part of 1163"] #1163's D1, I4, C17, C26, and I2 are in scope.
3. [engineer-verified: "Normalize in code (Recommended)"] The fix lands in production code, not in test expectations. The architect's adversarial review of this choice returned Modify (shared helper plus helper unit test), which Phase A carries.
4. [unverified] The canonical spelling is `+00:00` and test expectations stay unchanged. This comes from the session's option description, not the engineer's words. Supporting evidence: every hand-written `fix_date` literal in `test_review_bench_mining.py` uses `+00:00` (:201, :215, :252, :355, :1709, :2040, :2369, :3877) [verified: grep].
5. Local git prints a zero offset as `Z`, and the producers return git's text unchanged.
    - [verified: session traceback at `test_review_bench_mining.py:2040`] Local git 2.56.0 prints a zero offset as `Z`.
    - [verified: session traceback at `test_review_bench_mining.py:2040`] The producers return git's text unchanged.
    - The architect cites git's `RelNotes/2.45.0` for the change from `+00:00` to `Z` [unverified: relayed from the architect's read of the installed git docs].
    - Whether the runner's git prints `Z` or `+00:00` is [unverified].
    - That question stops mattering once the producers normalize.
6. [verified: grep for `%aI`] Exactly three producers store `%aI` text as `fix_date`: `mine_review_rounds.py:379-387`, `mine_pr_comments.py:496-498`, `mine_szz.py:266`. The fourth site, `mine_review_rounds.py:354`, only feeds `corpus._parse_ts`, which accepts both spellings.
7. [verified: `mine_szz.py:15`, `mine_review_rounds.py:35`, `mine_pr_comments.py:28`] All three miners already import shared candidate-field helpers (`guess_lens`, `is_markdown_path`) from `review_bench.defects`, which also owns `_validate_fix_date`.
8. [verified: `defects.py:64-68`, `test_review_bench_cli.py:3507`] `fix_date` accepts date-only values (`"2024-02-02"`). Normalizing inside `Candidate`/`ConfirmedDefect` construction would rewrite them to `…T00:00:00`.
9. [unverified] On Python 3.11 and later, `datetime.fromisoformat` accepts a trailing `Z`, and `isoformat()` renders an aware offset as `+HH:MM`. CI runs 3.12 [verified: `tests.yml:127`], and the review bench needs 3.12 or newer [verified: `evals/README.md:444`]. Phase A's helper unit test checks this directly.
10. [verified: grep for `fix_date ==`] No test pins `mine_szz`'s `fix_date` today. The only equality pins are `test_review_bench_mining.py:2040`, `:2369`, and `test_review_bench_cli.py:3507`.
11. [verified: `mine_szz.py:53`] `ValueError` is not in `_GIT_CALL_ERRORS`, so the helper's `ValueError` propagates exactly as the existing validator's does.
12. [engineer-verified: "Guarded evals/ root (Recommended)"] A guarded `evals/` root is the collection scope, in place of D1's recorded single explicit file. D1 (`.claude/plans/discovery-audit-remediation-plan.md:1205-1229`) forbade a bare root because of the fixtures collection error and named `norecursedirs` or `--ignore` as the guard. This plan applies that guard.
13. [verified: `tests.yml:74-98`] `SKIP_REGEX` is a deny-list, so an `evals/`-only diff already sets `changed=true`. No gate change is needed.
14. The `timing` marker is absent from evals, and the roots have no other pin.
    - [verified: grep for `mark.timing|pytestmark` in `evals/`] No evals test carries the `timing` marker today.
    - [verified: both reviewers' reads] Three evals tests assert wall-clock bounds: `test_review_bench_adjudicate.py:805,814`, `test_measure_subagent_model_resolution.py:514-518` and `test_review_bench_runner.py:795-816`.
    - Those three tests get the marker (row 32).
    - `test_pytest_collection_config.py:191-236` hard-codes `claude/.claude/` at :207 and does not check the roots.
    - So the parity test in Phase B is the only pin on four-root identity.
15. [verified: glob] Multi-root collection hits neither conftest eviction nor an import-file mismatch.
    - `evals/conftest.py` resolves to bare `conftest`.
    - The other three tracked conftests are packaged: `hooks/tests`, `scripts/tests`, and `plugins/lovable-cloud/tests/__init__.py`.
    - No evals test basename recurs elsewhere.
    - The earlier full-suite run with evals targets collected cleanly (20971 passed, 4 failed).
16. [verified: grep for `collect_ignore|--ignore` across *.py/*.toml/*.yml] `pyproject.toml`'s `norecursedirs` is the repo's only pytest collection-exclusion mechanism.
17. [unverified] A `norecursedirs` pattern containing `/` matches against the full path, so `evals/fixtures` prunes that directory. The prior session recorded this form fixing the bare-root case (remediation plan :1226-1228). Verification re-runs the check.
18. [verified: `select-tests.py:399-403`, `:841-866`] `FULL_SUITE_TARGETS` is documented to equal CI's pytest scope, and it goes straight to pytest on every fail-open path. A guard that lived only on the `tests.yml` lines would leave local fail-open runs aborting at collection.
19. [verified: target constants at `select-tests.py:34-222`] Every domain target sits under `claude/.claude/`, `claude-skills/`, `plugins/`, or `evals/`. Once `evals/` joins `FULL_SUITE_TARGETS`, `_targets_outside_full_suite` returns `()` for every input. The new invariant test re-checks this mechanically.
20. [verified: grep for `Bash(...(pytest|ruff)` across the repo] No `permissions.allow` entry matches a documented full-suite command.
    - `.claude/settings.json:10-13` holds `Bash(ruff check claude/.claude/)`, two `select-tests.py` grants, and one single-file pytest grant.
    - `claude/.claude/settings.json` holds none.
    - So no `settings.json` edit and no `/review-permissions` run are needed.
    - The comment at `pyproject.toml:19-21` claiming "an exact-match permission grant in two settings.json files" is stale.
21. [engineer-verified: "Keep id, rename step names (Recommended)"] `id: detect` stays and only the step names and echo are renamed. The id is looked up by id (`helpers.py:1923-1933`), pinned literally (`test_ci_watch_workflow_parity.py:32`), and cited in a dated record [verified]. Only the names at `tests.yml:46`, `:117` and the echo at `:119` carry "hook" framing.
22. No evals test is known to spawn a real `claude`.
    - [verified: grep for `"claude"` in `evals/test_*.py`] `claude` appears as an argv in an argv-construction assertion (`test_review_bench_runner.py:308`).
    - [verified: same grep] `claude` also appears in stubbed `claude --version` probes (`:1639`, `:1691`; `test_measure_subagent_model_resolution.py:866`).
    - [verified: same grep] No test skips on `shutil.which("claude")`.
    - [unverified] No test reaches a real spawn indirectly through production code.
    - Verification step B5 checks that last point.
23. [unverified: prior-session report] `ruff check evals/` is clean today.
24. [unverified] `requirements-dev.txt` covers every evals test import. The local venv may carry extra packages, so the pushed CI run is the check.
25. [unverified] The job stays inside `timeout-minutes: 20` with roughly 2175 more tests, many of which run real git. The cited figure for the two pytest passes is 12m45s (`tests.yml:26-33`).
26. [unverified] No `CHANGELOG.md` entry is needed: this is a contributor-facing CI, test, and docs change, following Phase 9's precedent (remediation plan :125-127).
27. [verified: `tests.yml:106`] `SHELL_REGEX` matches `install-dev.sh` (any `.sh` file), which this diff now edits (row 33), so Verification runs shellcheck. `install.sh` also matches.
28. [engineer-verified: "select-tests parity test (M5)"] The `FULL_SUITE_TARGETS`↔`tests.yml` parity test stays in scope (selected in plan-review's scope check).
29. [engineer-verified: "Delete _targets_outside_full_suite (M4)"] The helper's deletion, with its invariant test, stays in scope.
30. [engineer-verified: "mine_szz fix_date assertion"] The `mine_szz` `fix_date` assertion stays in scope.
31. [engineer-verified: "README :101 pointer"] The README :101 pointer edit stays in scope.
32. [engineer-verified: "Mark them timing (Recommended)"] The wall-clock-bound evals tests get the `timing` marker, which adds three test files to Critical files.
33. [engineer-verified: "Include them"] `install-dev.sh:113-114` and `install.sh:954,970` are updated in this PR.
    - `install-dev.sh` matches `SHELL_REGEX`, so Verification adds a shellcheck run.
    - `test_install_sh_contributor_intent_prompt.py:92-117` pins only the hint block's heading, not the command lines [verified: grep].
34. Pytest prunes `evals/fixtures`, and ruff may still lint it.
    - [verified: pytest 8.4.2 `_pytest/pathlib.py:446-452`, `_pytest/main.py:427-428`, relayed from the SDET reviewer's read] A `norecursedirs` pattern containing `/` matches the full path, so `evals/fixtures` prunes that directory.
    - That resolves row 17's unverified status for pytest.
    - `pyproject.toml` has no `[tool.ruff] exclude`, so `ruff check evals/` lints the `.py` files under `evals/fixtures/` [unverified: the reviewer read, not run].
    - If B1 flags fixture files, the fix is a ruff `exclude` for `evals/fixtures` in `pyproject.toml` (already in Critical files), not editing fixtures.

## Critical files

Two `code-writer` dispatches, run in sequence. Their file sets don't overlap, but Dispatch B's full-suite and CI verification needs Dispatch A's fix to be in place.

**Dispatch A: `fix_date` normalization**
- `evals/review_bench/defects.py`: add `canonical_fix_date()`.
- `evals/review_bench/mine_review_rounds.py`: `_commit_date`.
- `evals/review_bench/mine_pr_comments.py`: `_commit_date`.
- `evals/review_bench/mine_szz.py`: `_build_candidate`.
- `evals/test_review_bench_mining.py`: helper unit test and `mine_szz` `fix_date` assertion.
- Verify: `.venv/bin/python3 claude/.claude/scripts/select-tests.py`. The four named tests must pass.

**Dispatch B: CI wiring, guard, select-tests, docs**
- `.github/workflows/tests.yml`
- `pyproject.toml`
- `claude/.claude/tests/test_pytest_collection_config.py`
- `claude/.claude/scripts/select-tests.py`
- `claude/.claude/scripts/tests/test_select_tests.py`
- `CLAUDE.md`
- `README.md`
- `evals/README.md`
- `install-dev.sh` and `install.sh` (printed command hints)
- `evals/test_review_bench_adjudicate.py`, `evals/test_measure_subagent_model_resolution.py`, `evals/test_review_bench_runner.py` (the `timing` marker only), plus any further wall-clock-bound evals test the implementer's grep finds
- Verify: steps B0–B6 below.

Commit order: Dispatch A lands as its own commit before Dispatch B, so no intermediate commit collects the four failing tests. Rollback unit: Dispatch B reverts as a whole, because the parity test ties `FULL_SUITE_TARGETS` to `tests.yml`. Dispatch A stays. The PR body names both.

Reuse: `datetime.fromisoformat`/`isoformat` (standard library). The existing `TestNorecursedirsDefaultsPreserved` and `TestRuleTablePathFidelity` classes host the new pins.

## Verification

- Command paths: this worktree has no `.venv`, so every `.venv/bin/...` below runs as `../../../.venv/bin/...` from the worktree root (README's Tests section). That venv runs Python 3.14 and CI runs 3.12, so local green does not cover 3.12 and B6 is the only check there.
- B0 (before wiring): record the latest green `main` run's total and per-step durations (`gh run view <id> --json jobs`) as the baseline. The `tests.yml:26-33` figure of 12m45s is stale and came from a run that hit a 15-minute cap.
- A1: `.venv/bin/python3 claude/.claude/scripts/select-tests.py`, run first with only the new tests and producer pins in place (red on local git 2.56, per the red-first record), then green after the helper lands. The three parametrizations of `test_worktree_path_citation_across_two_earlier_rounds_yields_one_candidate_at_the_earliest_fix`, `test_owner_comment_with_a_fixed_reply_yields_a_candidate_with_blamed_and_replied_commits`, and the new helper and `mine_szz` tests all pass.
- B1 (before wiring): `.venv/bin/ruff check evals/` is clean. If findings sit only under `evals/fixtures/`, fix them with a ruff `exclude` for `evals/fixtures` in `pyproject.toml` (row 34). If any other `evals/` file has findings, stop and raise it, because fixing them would widen Critical files.
- B2: `.venv/bin/pytest evals/ --collect-only -q` exits 0 and lists no node under `evals/fixtures/` (row 17).
- B3: `.venv/bin/python3 claude/.claude/scripts/select-tests.py`. `pyproject.toml` and `select-tests.py` are global triggers, so it runs the full suite, now including `evals/` (CLAUDE.md case 1).
- B4: `.venv/bin/ruff check claude/.claude/ claude-skills/ plugins/ evals/` is clean.
- B5 (fail-closed spawn detector):
  - Put a stub `claude` first on `PATH`, in an empty temporary directory outside the repo. The stub appends its argv to a log file and exits 127.
  - Run `.venv/bin/pytest evals/ -q` once with that `PATH`, `HOME` set to an empty temporary directory, `CLAUDE_CONFIG_DIR` unset and `TZ=UTC` (the runner's shape).
  - Expect the log to stay empty (row 22). A non-empty log is a real spawn and stops the work.
  - Absence of `claude` from `PATH` would pass whether or not a spawn was attempted, so it is not the check.
  - The local wall time does not transfer to a 4-vCPU runner, so it is not evidence for row 25.
- B6: push, then check the `tests` job log:
  - The parallel pass shows `evals/test_measure_subagent_model_resolution.py` and `evals/test_review_bench_mining.py` node ids passing (D1's explicit check).
  - The timing pass finishes without a collection error.
  - The Lint step's command includes `evals/`.
  - The job is green.
  - Report the job and per-step durations against the B0 baseline and the 20-minute cap in the PR body.
  - Refresh the `tests.yml:26-33` rationale comment in the same PR if the duration changed materially.
  - If an evals failure appears only on CI (the runner's git differs, or a dependency is missing), stop and report to the engineer instead of fixing it ad hoc.
  - If the job times out, stop and report to the engineer instead of fixing it ad hoc.
- B7: `scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck` is clean, because `install-dev.sh` and `install.sh` change (rows 27, 33), then `.venv/bin/pytest claude/.claude/hooks/tests/test_install_sh_contributor_intent_prompt.py -n 0` passes.

## Out of scope

- EM1 (the git-subcommand tokenizer misparse): the engineer says it needs separate discussion.
- `docs/design-decisions/ci-stays-an-unconditional-full-suite-backstop.md:13,33` and `docs/design-decisions/skill-evals-local-only.md`: preserved decision records. Both stay accurate.
- Illustrative `pytest claude/.claude/` mentions that are not documented full-suite commands: `docs/skills.md:117`, `docs/transcript-analysis.md:1967`, `claude-skills/skills/subagent-delegation/REFERENCES.md:23`, and `docs/reports/**` (preserved).
- Merging the two `_commit_date` helpers: they use different git runners and error semantics.
- Removing the now-redundant `.replace("Z", "+00:00")` calls at `mine_pr_comments.py:343` and `corpus.py:219`: both are working parsers of other inputs.
- The `Bash(ruff check claude/.claude/)` grant at `.claude/settings.json:10`: it stays as it is.
- A parity test between the `CLAUDE.md`/`README.md` prose and `tests.yml`: too brittle.
- Raising `timeout-minutes`: only if step B6 shows a need, and then as the engineer's call.
