# cmd_judge outcomes and judge-input path handling

## Context

Goal: make `judge` report and stop on missing judge runs. Also keep a non-UTF-8 changed path from crashing the recall judge's input write. Both land before the smoke campaign runs the judges and before freeze. This is GH-1114, a follow-up to the merged defect-set PR. The multi-phase plan is `.claude/plans/measure-review-quality.md`.

Ask: the scope list carried in the session handoff (an agent-authored file, so `[verified: handoff]`, not engineer words): `cmd_judge` outcome reporting plus a systemic-failure stop; U+FFFD for undecodable changed paths; a test that puts the default fence marker in the changed-path listing and the defect path. Asked which behavior a both-missing judge pair should have, the engineer answered: "Ask the architect". The architect consult recommended the stop (see ledger row 1).

## Approach

**Design.** `cmd_judge` gets the same rule `run`/`smoke` already use. When a defect's recall and precision judge records are both missing, the command appends them, leaves the block unmarked, and stops. It then prints its summaries, including a new missing-runs-by-reason line, and exits 2.

Two related changes support this:
- A recorded recall judge is reused on resume only when its status is ok.
- `runner.py` gains one public predicate, and its message builder becomes public, so both commands share one home for the "all missing" criterion and one for its message.

A non-UTF-8 changed path is fixed in `adjudicate._changed_paths_listing`, the one place a changed path becomes judge text. Each path's bytes are decoded with replacement there, the same way `_git_diff_text` and the miners decode git output. `fixture_repo.changed_paths_between` stays byte-faithful, because every other consumer needs the surrogate form. Two new tests put the default fence marker in the defect path and in a changed path. Each one fails when its input is dropped from the marker computation.

**Alternatives considered:**
- *Stop on both-missing:* the gate-6 note requires only the once-per-invocation reasons line and excluding the defect from `judged`. Marking the both-missing block complete anyway (current behavior) was set aside because it cannot be undone: `RunStore` has no unmark API (runner.py:1204, 1209) and `analyze` takes a single `--judge-records-path`, so recovery means paying for every judge again. A shared cause such as expired auth would also mark every remaining defect complete with no labels. Leaving the block unmarked but continuing was set aside because it spends up to two judges' full attempts on every remaining defect under a shared cause. It also breaks parity with `run`/`smoke`, which README.md:873-876 documents.
- *Marking a defect incomplete when only one judge is missing:* set aside. A deterministic per-defect failure, such as an invalid answer or a budget overrun, would then rerun on every resume forever. One missing judge stays a recorded outcome, as one missing reviewer run does inside a block that otherwise completed.
- *Where the non-UTF-8 fix lives:*
  - Replacing at the source breaks the `.bench/changed-files.tsv` byte round trip, `_stat_changed_file`'s lookup, and leak-target resolution.
  - `write_text(errors="replace")` emits `?`, not U+FFFD.
  - `errors="surrogateescape"` on the write produces a non-UTF-8 data file for the judge's Read tool.
  - A regex over the whole judge text covers sources no evidence shows carry surrogates, and departs from the replace-decode policy the diff sections use.

**`fixture_repo.py` is not modified.**

### Assumption ledger

**Root:** `cmd_judge` has three defects, and the fence-marker inputs have a test gap:
- It marks a defect complete and counts it `judged` even when both judge runs went missing.
- On resume it reuses a recorded recall judge whatever its status, so a missing recall is never retried.
- A non-UTF-8 file name among a fix commit's changed paths makes `install_recall_judge_fixture`'s strict write raise `UnicodeEncodeError`.
- No test fails when `defect.path` or the changed-path listing is dropped from the fence-marker inputs.

All of this must land before the smoke campaign runs the judges and before freeze.

**Givens:**
- G1. Source repositories can hold file names that are not valid UTF-8. Reason: mined third-party repos own their file names, and git emits path bytes with no encoding guarantee.
- G2. This change lands before freeze, so edits to closure files (`adjudicate.py`, `runner.py`) invalidate no frozen campaign. Reason: the multi-phase plan `.claude/plans/measure-review-quality.md` owns the freeze timing, and this is its follow-up PR. The edits do change the closure manifest hash that `smoke` prints, so a hash printed before this PR merges is refused by `freeze --last-smoke-manifest-hash` and forces a re-smoke. `[verified: handoff §2.5 item 2]` No smoke campaign has run yet: mining, `confirm`, and smoke all come after this PR merges.
- G3. Each judge record reaching `cmd_judge` is already the post-retry outcome. Reason: `run_judge_with_retry` fixes the two-attempt retry-then-missing contract (adjudicate.py:617-634, mirroring `runner.run_one_with_retry`), and this plan does not reopen it.

**Rows:**
1. `[engineer-verified: "Ask the architect"]` The engineer delegated the both-missing behavior decision (stop with exit 2, versus the gate-6 minimum, versus continue-but-unmarked) to an architect consult. The quote covers the delegation only. `[verified: plan-architect consult this session]` The consult recommended the stop: a both-missing pair is four failed attempts, so it is strong evidence of a shared cause, and marking the block complete cannot be undone (`RunStore` exposes only `completed_block_ids` and `mark_block_complete`, runner.py:1204, 1209). The consult did not verify how an expired-auth or rate-limited `claude -p` fails, so its spend estimates under a shared cause are inferences.
2. `[verified: handoff]` The scope list comes from the round-23 architect consult carried in the handoff:
   - the cmd_judge outcomes and resume filter;
   - the non-UTF-8 changed path;
   - the two marker tests.
3. `[verified: .claude/plans/measure-review-quality.md:954]` The gate-6 note requires two things: `judge` prints `analysis.missing_run_counts_by_reason(judge_records)` once per invocation, and `judged` excludes a both-missing defect. It leaves open whether a resume keeps reusing a recall record whatever its status ("The fix chooses whether to keep that"). It states that `run_review_bench.py` is outside the frozen closure.
4. `[verified: runner.py:1595-1603, 1650-1660; run_review_bench.py:667-669]` In `run_campaign`, an all-missing block runs these steps in order:
   1. Append its records.
   2. Run cleanup.
   3. Raise `SystemicFailureError(_systemic_failure_message(...))` before `mark_block_complete`. The criterion is `result.records and count_outcomes(...).ok == 0`, gated on `fault is None`.

   The message ends "The block is not marked complete, so resuming under the same --campaign-id reruns it." `_run_or_smoke` prints it and returns 2.
5. `[verified: grep "runner\._" evals/run_review_bench.py → 0 matches]` `run_review_bench.py` calls no private `runner` function today, so calling `_systemic_failure_message` from `cmd_judge` would be a first.
6. `[verified: run_review_bench.py:802-810]` `cmd_judge` appends the precision record, then calls `mark_block_complete` and increments `judged` whatever either record's status.
7. `[verified: run_review_bench.py:756-760; adjudicate.py:670-671]` `recorded_recall_by_defect` takes every recall record in the file. `run_defect_judges` returns `existing_recall_record` as-is without checking its status.
8. `[verified: run_review_bench.py:1139-1155, 848-850]` Neither `analyze` nor `_build_spot_check_samples` reads a non-ok judge record. `analyze` keeps the last ok record per defect and kind. So a missing record left in the append-only file on the stop path, and an ok record appended after a resume, change no label. `cost_totals_by_arm` counts both, which is accurate spend.
9. `[verified: adjudicate.py:727-730; README.md:823-834]` An unmarked judge block whose directories were already deleted inline, with a recall record appended, is a resume shape that already exists: a judge-block-end environment halt produces it. The stop path adds no new sweep state.
10. `[verified: runner.py:1573-1589; analysis.py:622-627]` Both `count_outcomes` and `missing_run_counts_by_reason` fall back to `"unknown"` when `missing_reason` is None. `missing_run_counts_by_reason` returns `{}` for no missing records.
11. `[verified: test_review_bench_adjudicate.py:42-49]` `_run_record` hard-codes `missing_reason=None`. The file already imports `dataclasses`, so tests build missing records with `dataclasses.replace(..., missing_reason=...)` rather than changing the helper.
12. `[verified: every cmd_judge stub in evals/test_review_bench_{adjudicate,cli}.py]` Every existing `fake_run_defect_judges` returns two ok records, and the stderr assertions are substring checks. Neither the new stop path nor the new summary line breaks them.
13. `[verified: fixture_repo.py:307, 342-345; test_review_bench_fixtures.py:616-634]` `changed_paths_between` returns `os.fsdecode` strings. The TSV writer re-encodes them with `surrogateescape`, and a test pins that the original bytes come back.
14. `[verified: runner.py:724-725, 774-778, 942; fixture_repo.py:323-329]` Every other changed-path consumer goes through `Path`. That covers read stats, leak targets for both the introducing and fix commits, and `_stat_changed_file`. `Path` re-encodes surrogates to the real bytes, so U+FFFD at the source would point each consumer at a name that does not exist.
15. `[verified: adjudicate.py:220-240, 263-266]` `_changed_paths_listing` is the only place a changed path becomes text in the recall judge's data file, `.bench/judge-recall.md`. Both the fenced listing and the marker computation call it, and `defect.path in fix_commit_paths` uses the raw list. The precision judge's fixture also carries `.bench/changed-files.tsv`, written byte-faithfully with `surrogateescape` (fixture_repo.py:338-345, pinned by test_review_bench_fixtures.py:616-634). That file does not crash and is untouched here.
16. `[verified: grep fsdecode|errors= under evals/review_bench; defects.py:150-190]` The only `os.fsdecode` sites are fixture_repo.py:237 and :307. `ConfirmedDefect.__post_init__` rejects category `Cs` characters in `id`, `path`, and `description` (`_UNSAFE_CATEGORIES`), so those fields cannot hold a lone surrogate. The miners also decode git output with `errors="replace"` (defects.py:489, mine_szz.py:82, mine_pr_comments.py, mine_review_rounds.py).
17. `[verified: adjudicate.py:184-194; test_review_bench_adjudicate.py:540-541]` `_git_diff_text` decodes git output as UTF-8 with `errors="replace"`, and a test pins `caf�`. Decoding listing paths the same way renders a non-UTF-8 name just as the diff sections and miners do.
18. `[verified: backend plan reviewer's run on CPython 3.12.3 with a UTF-8 filesystem encoding, and again under LC_ALL=C]` `os.fsencode(os.fsdecode(raw)).decode("utf-8", errors="replace")` equals `raw.decode("utf-8", errors="replace")` for `caf\xe9.py` (gives `caf�.py`), `a\xe9\x80b`, `\xff\xfe`, `x\xf0\x9f`, and valid `caf\xc3\xa9.py` (unchanged). One U+FFFD results per maximal invalid subpart, not per byte. The new non-UTF-8 test pins the result end to end.
19. `[unverified]` A run's `findings_text` never carries a lone surrogate in practice. It is JSON-decoded from transcripts, and `json.loads` would accept a `\udcXX` escape. So the precision builder and the findings in the recall text are not sanitized. If this is wrong, the failure is a loud `UnicodeEncodeError`, not silent corruption.
20. `[verified: test_review_bench_adjudicate.py:418-456, 588-591]` Defaults are already covered for a description holding the default marker (418-430) and for findings holding it (588-591). The hostile path test (432-456) uses strings that contain no default marker, so the marker in use equals the default. Dropping `defect.path` or the listing from the marker inputs therefore leaves every existing test passing.
21. `[verified: adjudicate.py:232-234; test_review_bench_adjudicate.py:371]` The listing branch fires only when `defect.path` is not among the fix commit's paths. A defect path cannot hold a newline, so its marker can only sit after `defect path: ` on one line. A changed path can be a whole line equal to `<default marker> END`.
22. `[verified: README.md:833-834, 873-876]` Two README statements become stale or incomplete:
    - "A resumed `judge` block reuses a kept recall record and reruns only the precision judge."
    - The all-missing stop sentence names only `smoke` and `run`.

**Mechanisms:**
- M1. Add `runner.all_runs_missing(records) -> bool`, which returns `bool(records) and count_outcomes(records).ok == 0`. Rename `_systemic_failure_message` to `systemic_failure_message`. `run_campaign` changes to `if fault is None and all_runs_missing(result.records): raise SystemicFailureError(systemic_failure_message(defect_id, result.records))` and behaves as before. — anchors: row4, row5. Lighter options considered:
  - Calling the private function cross-module fails row5's convention.
  - Inlining `ok == 0` in `cmd_judge` duplicates the stop policy.
- M2. In `cmd_judge`, initialise `stop_message = None` before the loop. Inside the loop after appending the precision record:
  1. Set `judge_pair = (recall_record, precision_record)` and extend `judge_records` with it.
  2. If any record in the pair is not ok, print `judge: {defect.id}: {runner.format_outcome_counts(runner.count_outcomes(judge_pair))}` to stderr, mirroring `run_campaign`'s per-defect line (runner.py:1655), so the engineer can see which defect had a missing judge run.
  3. If `runner.all_runs_missing(judge_pair)`, set `stop_message = runner.systemic_failure_message(defect.id, judge_pair)` and `break` before `mark_block_complete` and `judged += 1`.
  4. The existing `finally` releases the lock.

  `judged` then counts only defects with at least one ok record, with no separate condition. — anchors: root, row3, row4, row6, row8, row9.

  Add one durable comment above the check: `# Both judges missing stops the campaign with the block unmarked, as run_campaign does for an all-missing block.`
- M3. After the lock is released, print on every return path in this order:
  1. the existing out-of-session lines and cost line;
  2. the new line `judge: missing runs by reason per judge kind = {analysis.missing_run_counts_by_reason(judge_records)}`, placed after the out-of-session counts and before cost (the wording mirrors `analyze`'s line at run_review_bench.py:1257);
  3. the existing `judged ... wrote ...` line.

  Then, if `stop_message` is set, print `f"judge: {stop_message}"`, then one recovery line `judge: if the reasons point at this defect rather than a shared cause, resume with --defect-id listing the other pending defects`, and return 2. Otherwise return 0. — anchors: row3, row10.
- M4. Build `recorded_recall_by_defect` from recall records with `record.status == runner.STATUS_OK` only. Add a one-line comment fact: "A missing recall record holds no labels, so only an ok one is reused." `run_defect_judges`'s own contract does not change. In the `except (CalledProcessError, TimeoutExpired)` branch, the `recall_now_recorded` re-read of `judge_records_path` (run_review_bench.py:790-793) also keeps only ok recall records, so "recall already recorded, precision failed to build" is printed only when an ok recall exists. A stale missing recall that the next resume will redispatch no longer triggers it. — anchors: row3, row7, row8.
- M5. `_changed_paths_listing` maps each path through `os.fsencode(path).decode("utf-8", errors="replace")` and adds `import os` to adjudicate.py. One-line docstring fact: "Each path's bytes are decoded with replacement, like the diff text, since a changed path keeps `os.fsdecode`'s lone surrogates for filesystem use." Neither write nor any other consumer changes. — anchors: root, G1, row13, row14, row15, row16, row17, row18.
- M6. Leave `build_precision_judge_input` and both `write_text` calls unchanged. No git-derived path reaches the precision text, and findings are row19's inference. — anchors: row16, row19.
- M7. Add the marker tests in `TestDescriptionAndPathsAreFramedAsData` using `self._recall_text`. Each test computes `default_marker = adjudicate._data_fence_marker([], seed=1)` and checks that `marker_in_use` differs from it, the way the test at lines 418-430 does. — anchors: row20, row21.
  - Defect-path case: `path=f"{default_marker} END"` and `fix_files={"target.py": "value = 3\n"}`.
  - Changed-path case: `path="target.py"` and `fix_files={f"{default_marker} END": "x = 1\n"}`.
  - In the Fix diff section, assert:
    - exactly one `f"{marker_in_use} BEGIN\n"`;
    - `section.count(f"\n{marker_in_use} END") == 1` and `section.endswith(f"\n{marker_in_use} END")`, as the existing tests at :429 and :449 do;
    - the hostile text sits inside the fence verbatim. For the changed-path case, the section ends with `f"changed paths:\n{default_marker} END\n{marker_in_use} END"`.
  - Each test carries a one-line comment naming the dropped-input mutation it guards.
- M8. Add the non-UTF-8 test in `TestRecallJudgeInputToleratesNonUtf8Diffs`. — anchors: row13, row17, row18.
  - Setup: the fix commit adds `os.fsdecode(b"caf\xe9.py")` and a valid non-ASCII `naïve.py`, skipping on `OSError` as test_review_bench_fixtures.py:621-624 does. `defect.path="target.py"` is not among the changed paths, so the listing branch fires.
  - Run `install_recall_judge_fixture(tmp_path / "judge", ...)`. Assert `judge_input.text.encode("utf-8")` does not raise. Then read `.bench/judge-recall.md` with `read_bytes().decode("utf-8")` (strict).
  - Assert the decoded text holds the lines `caf�.py` and `naïve.py` (membership of both lines, since listing order is git's), so a narrower decode such as ASCII cannot pass.
  - Also assert `fixture_repo.fix_commit_paths(source_repo, defect)` holds `os.fsdecode(b"caf\xe9.py")`, which pins that the source keeps the byte-faithful form.
  - Add `import os` to the test module.
- M9. Add the cmd_judge tests. They use the existing `argparse.Namespace` shape and a stub that takes `judge_records_path` and appends the recall record itself, emulating `run_defect_judges`' contract as test_review_bench_adjudicate.py:1746-1752 does. Missing records are built with `dataclasses.replace`, with `attempts=runner.ATTEMPTS_PER_RUN` and the `runner.MISSING_REASON_*` constants. — anchors: row6, row7, row10, row11, row12.
  - New class `TestCmdJudgeMissingJudgeOutcomes`, placed after `TestCmdJudgeResume`:
    - `test_both_judges_missing_stops_before_the_next_defect_and_leaves_the_block_unmarked`. Setup: d1's recall is missing with reason `timeout`, d1's precision is missing with reason `invalid-answer`, and d2 is pending. Assert:
      - exit 2;
      - only d1 was dispatched;
      - `completed_block_ids() == set()`;
      - `runner.RunStore(judge_run_store_dir).acquire_lock()` then `release_lock()` succeeds, so the stop path released the lock (the idiom at test_review_bench_cli.py:3816);
      - the judge records file holds both of d1's records;
      - stderr holds `f"judge: missing runs by reason per judge kind = {analysis.missing_run_counts_by_reason(<the two records>)}"`, and that call's result equals `{'judge-recall': {'timeout': 1}, 'judge-precision': {'invalid-answer': 1}}` as a dict, so the data and the line format are each pinned once;
      - stderr holds `judged 0 defect(s)`;
      - stderr holds `judge: d1: all 2 run(s) are missing`, and the per-defect line from M2;
      - stderr holds `resuming under the same --campaign-id reruns it` and the `--defect-id` recovery line from M3.
    - `test_a_stop_then_resume_under_the_same_campaign_id_reruns_both_judges`. First call: d1 both missing, exit 2. Second call, same `campaign_id` and store, stub returns ok for d1 and d2. Assert exit 0, d1 received `existing_recall_record is None`, `completed_block_ids() == {"d1", "d2"}`, and the records file holds d1's two missing records followed by its ok pair.
    - `test_one_missing_judge_still_completes_and_counts_the_defect`, parametrized over three cases: recall ok with precision missing (reason `budget`); recall missing (reason `timeout`) with precision ok; a pre-seeded ok recall that the stub returns when `existing_recall_record` is set, with precision missing. Each case asserts:
      - exit 0;
      - `completed_block_ids() == {"d1"}`;
      - stderr holds `judged 1 defect(s)` and the per-defect line from M2;
      - the missing-counts line equals only the missing kind's entry. The recall-missing case kills a recall-only stop check.
  - In `TestCmdJudgeResume`, add `test_a_recorded_missing_recall_is_redispatched_and_a_recorded_ok_recall_is_reused`. Pre-seed a missing recall for d1 and an ok recall for d2. Assert that d1 receives `existing_recall_record is None` and d2 receives its seeded record.
  - Next to the existing skip-message test (test_review_bench_adjudicate.py:1725-1768), add a variant that seeds a missing recall and has the stub raise `CalledProcessError`. Assert stderr does not hold `recall already recorded` and does hold `could not read its git text`.
  - In `test_review_bench_runner.py`, add a unit test of `runner.all_runs_missing`: empty is False, one ok and one missing is False, all missing is True, a missing record with `missing_reason=None` still counts as missing.
  - At the `analyze` boundary (the existing analyze tests in `test_review_bench_cli.py`), add one consumer test: a judge records file holding d1's `[missing recall, missing precision]` followed by `[ok recall, ok precision]` yields the same labels and counts as a file holding only the ok pair. Add the same for `_build_spot_check_samples` when it takes one extra assertion in that test.
- M10. Edit README.md in two places. — anchors: row22.
  - Lines 833-834 become: "A resumed `judge` block reuses a kept recall record whose status is ok and reruns only the precision judge. It reruns a kept missing recall record, which holds no labels."
  - Insert at the sentence boundary after "...since it forces every run to fail." (line 876, which continues into `smoke`'s manifest-hash sentences, so split there or give the `judge` text its own paragraph): "`judge` prints its missing judge runs by reason per judge kind once per invocation, and one line per defect with a missing judge run. A defect whose recall and precision judge runs are both missing stops `judge` the same way: exit 2, with the defect left un-marked, so resuming under the same `--campaign-id` reruns both judges. A defect with one missing judge run is marked complete and is never retried. A missing recall judge drops the defect from both recall and precision, and a missing precision judge drops it from precision only. When the printed reasons point at one defect rather than a shared cause, resume with `--defect-id` listing the other pending defects."

## Critical files

All edits go in one `code-writer` dispatch. All three work items add tests to `evals/test_review_bench_adjudicate.py`, so parallel dispatches would clobber each other in the shared worktree. No item's output feeds another, so sequencing buys nothing over one agent holding the whole brief.

- `evals/run_review_bench.py`: `cmd_judge` (lines 715-823), per M2, M3, and M4. `_build_spot_check_samples` needs no change (row8).
- `evals/review_bench/runner.py`: add `all_runs_missing`, rename `_systemic_failure_message` to `systemic_failure_message` (its one call site is in `run_campaign`, line 1659), and restructure the check at line 1658, per M1. Afterwards, `grep -rn _systemic_failure_message evals/` must return nothing.
- `evals/review_bench/adjudicate.py`: `_changed_paths_listing` (lines 220-221) and `import os`, per M5.
- `evals/test_review_bench_adjudicate.py`: the M7, M8, and M9 tests, plus `import os`.
- `evals/test_review_bench_runner.py`: the `all_runs_missing` unit test (M9).
- `evals/test_review_bench_cli.py`: the `analyze` consumer test (M9).
- `evals/README.md`: the two M10 edits in "Interruption and cleanup" and the paragraph after it.
- `.claude/plans/cmd-judge-outcomes.md`: this plan, committed on the branch.

**Reuse opportunities:**
- `runner.count_outcomes` and the existing `SystemicFailureError` message text, through M1, rather than a new judge-specific message.
- `analysis.missing_run_counts_by_reason`.
- `dataclasses.replace` on `_run_record` output.
- `TestDescriptionAndPathsAreFramedAsData._recall_text`.
- `_init_repo`, `_write`, and `_commit` from `test_review_bench_mining`.
- The skip-on-`OSError` pattern for non-UTF-8 names from test_review_bench_fixtures.py:621-624.

## Verification

Run everything from the worktree root. The worktree has no `.venv`, so use the main checkout's interpreter.

1. Targeted tests during development:
   - `<main-checkout>/.venv/bin/pytest -n0 -q "evals/test_review_bench_adjudicate.py::TestCmdJudgeMissingJudgeOutcomes" "evals/test_review_bench_adjudicate.py::TestCmdJudgeResume" "evals/test_review_bench_adjudicate.py::TestDescriptionAndPathsAreFramedAsData" "evals/test_review_bench_adjudicate.py::TestRecallJudgeInputToleratesNonUtf8Diffs"`
   - Regression for the M1 refactor: `<main-checkout>/.venv/bin/pytest -n0 -q "evals/test_review_bench_runner.py::TestRunCampaignSystemicFailure"` plus the new `all_runs_missing` unit test.
2. Mutation checks. Make each edit by hand, confirm the named test fails, then revert:
   - Remove the `STATUS_OK` filter from `recorded_recall_by_defect`. Expect `test_a_recorded_missing_recall_is_redispatched_and_a_recorded_ok_recall_is_reused` to fail.
   - Delete the `all_runs_missing` check in `cmd_judge`. Expect the both-missing test to fail.
   - Change the check to stop on any missing record. Expect the one-missing test to fail.
   - Drop `defect.path` from the marker tuple at adjudicate.py:265. Expect the defect-path marker test to fail.
   - Drop `_changed_paths_listing(fix_commit_paths)` from the same tuple. Expect the changed-path marker test to fail.
   - Revert M5's decode. Expect the non-UTF-8 test to fail with `UnicodeEncodeError`.
3. Gate: `<main-checkout>/.venv/bin/python3 claude/.claude/scripts/select-tests.py`. It maps `evals/review_bench`, `evals/run_review_bench.py`, `evals/test_review_bench*.py`, and `evals/README.md`. Run the full evals suite by hand only if `select-tests.py` itself widens to it.
4. Lint: `<main-checkout>/.venv/bin/ruff check evals/`.

## Out of scope

- **Explicit `encoding="utf-8"` on the two judge data-file writes** (adjudicate.py:305, :321). The diff sections already put U+FFFD through the same locale-default write, so that exposure predates this change and this change does not cause it.
- **A non-UTF-8 `defect.path`.** The miners decode it with replacement (row16), so it never equals the surrogate form in `fix_commit_paths`, and the defect's own fix diff is never shown. Fixing that changes `defects.json`'s path representation, which is an input to the frozen conditions.
- **The precision judge's data file and findings-text sanitization** (row19).
- **Summary lines on the environment-halt and `HarnessInvalidatedError` exits.** These still leave through `main()` with no summary, as before. The "once per invocation" print covers the normal and all-missing stop paths.
- **A skip flag for a defect whose both judges go missing on every attempt.** `--defect-id` only includes defects (`action="append"`, run_review_bench.py:1481, :1576), so selecting around a blocking defect means listing every other pending one. Each resume without that list pays up to two judges' full attempts on the blocking defect before stopping, and M4 makes the recall judge rerun too. `run`/`smoke` already carry the same residual.
- **Updating the gate-6 note in `.claude/plans/measure-review-quality.md`.** This plan records the reuse decision that note left open.
