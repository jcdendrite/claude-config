# Code-file size offenders: assessment for the 1,000-line limit

**Date:** 2026-11-19
**Pin:** `ab85a627` (merge-base with `main`). Every count below is read from blobs and history at this commit, not from a working tree.
**Scope:** every tracked `.py` and `.sh` file over 1,000 physical lines, as GH-1119 scopes it. Files without those suffixes are not measured.
**Reproduce:** the Appendix holds the measuring program and the cross-check commands. The program prints every measured figure; the three verdict and proposed-issue rows are tallied from the tables below.

## Summary for the Revisit ruling

`docs/design-decisions/code-file-line-limit.md` reconsiders the limit if this assessment finds "an unreasonable number or shape of exceptions". The Figures table below holds what that ruling weighs: the offender count by test code and production, the counts at three thresholds in both units, the files just under the limit, the verdict counts and the number of new issues that per-file filing would create. Every row except the last three carries a name the measuring program prints. The last three rows count verdicts and proposed issues from the tables below.

| Figure | Value | Meaning |
| --- | --- | --- |
| `tracked_code_files` | 685 | Tracked `.py` and `.sh` files at the pin |
| `offenders_all` | 112 | Files over 1,000 physical lines |
| `offenders_test` | 89 | Offenders that are test code: a `tests` directory component, or a `test_*.py` or `conftest.py` name |
| `offenders_production` | 23 | Offenders that are not test code |
| `offenders_named_test` | 88 | Offenders named `test_*.py` or `conftest.py` |
| `offenders_not_named_test` | 24 | Offenders with any other name |
| `offenders_shell` | 7 | Offenders that are `.sh` files |
| `physical_over_1000_all` | 112 | All offenders over 1,000 physical lines |
| `physical_over_1000_test` | 89 | Test-code offenders over 1,000 physical lines |
| `physical_over_1000_production` | 23 | Production offenders over 1,000 physical lines |
| `noncomment_over_1000_all` | 80 | All offenders over 1,000 non-comment lines |
| `noncomment_over_1000_test` | 71 | Test-code offenders over 1,000 non-comment lines |
| `noncomment_over_1000_production` | 9 | Production offenders over 1,000 non-comment lines |
| `shell_undetermined_over_1000` | `claude/.claude/hooks/_config.sh`, `claude/.claude/hooks/enforce-marker-script-shape.sh`, `claude/.claude/scripts/cleanup-merged-branches.sh`, `claude/.claude/scripts/marker.sh`, `install.sh` | Shell offenders whose non-comment lower bound is at or under 1,000 while the physical count is over it, so the file's side of that threshold is undetermined |
| `physical_over_1500_all` | 75 | All offenders over 1,500 physical lines |
| `physical_over_1500_test` | 65 | Test-code offenders over 1,500 physical lines |
| `physical_over_1500_production` | 10 | Production offenders over 1,500 physical lines |
| `noncomment_over_1500_all` | 46 | All offenders over 1,500 non-comment lines |
| `noncomment_over_1500_test` | 42 | Test-code offenders over 1,500 non-comment lines |
| `noncomment_over_1500_production` | 4 | Production offenders over 1,500 non-comment lines |
| `shell_undetermined_over_1500` | none | Shell offenders whose non-comment lower bound is at or under 1,500 while the physical count is over it, so the file's side of that threshold is undetermined |
| `physical_over_2000_all` | 49 | All offenders over 2,000 physical lines |
| `physical_over_2000_test` | 43 | Test-code offenders over 2,000 physical lines |
| `physical_over_2000_production` | 6 | Production offenders over 2,000 physical lines |
| `noncomment_over_2000_all` | 31 | All offenders over 2,000 non-comment lines |
| `noncomment_over_2000_test` | 29 | Test-code offenders over 2,000 non-comment lines |
| `noncomment_over_2000_production` | 2 | Production offenders over 2,000 non-comment lines |
| `shell_undetermined_over_2000` | none | Shell offenders whose non-comment lower bound is at or under 2,000 while the physical count is over it, so the file's side of that threshold is undetermined |
| `unparsed` | 0 | Python offenders that `tokenize` or `ast` rejected |
| `near_limit_files` | 22 | Files at 900 to 1,000 physical lines |
| `near_limit_at_exactly_limit` | 1 | Of those, files at exactly 1,000 physical lines, which pass |
| `ceiling_edit_load` | 334 | Distinct commits in the window that changed some offender's length |
| `window_non_merge_commits` | 509 | Non-merge commits in the window |
| `verdict_structural_exception` | 3 | Rows with the structural exception verdict |
| `verdict_split_pending` | 109 | Rows with the split pending verdict |
| `proposed_issues` | 104 | Split-pending rows whose last cell reads `proposed (not filed)`: the issues per-file filing would create |

The assessment's own reading follows. It is the assessment's judgment, not a measurement.

**What the figures show.**
- Count: every offender gets an exception row, so the exception table's size is `offenders_all`. Under the verdict rule almost all rows are split pending (`verdict_split_pending`) and few are structural exceptions (`verdict_structural_exception`). The rule gives a file the split-pending verdict whenever it has a named seam, so that count measures how many files have a seam to name, not how many splits are worth doing.
- Shape: test code is the large majority (`offenders_test` against `offenders_production`). The production share stays small at every threshold (`physical_over_1500_production`, `physical_over_2000_production`) next to the test share (`physical_over_1500_test`, `physical_over_2000_test`).
- Threshold: no threshold leaves a handful of files. `physical_over_1500_all` and `physical_over_2000_all` are each smaller than `offenders_all` but still tens of files, and most of what remains is test code.
- Unit: counting non-comment lines lowers the count (`noncomment_over_1000_all` against `physical_over_1000_all`) but leaves most offenders over the limit, and the gap narrows at 1,500 and 2,000 lines. The unit therefore does not change the shape much. The shell cell is a lower bound, so shell files' non-comment counts, and the `noncomment_over_*` rows, can only understate.
- Exposure: `near_limit_files` is a fraction of `offenders_all`, so the files that may cross next are fewer than the files already over.
- Churn: `ceiling_edit_load` is a majority of `window_non_merge_commits`. Under exact both-direction ceilings, most commits in the window would then have edited the exception table.
- Issues: `proposed_issues` is below `verdict_split_pending` only because some split-pending rows already name an existing issue. Per-file filing would create that many new issues.

**What the figures cannot show.**
- Verdicts are judgments from outlines and targeted reads, not whole-file reads. A build check confirmed that each named seam symbol occurs in the file at the pin. It did not confirm the seam is a good place to cut.
- Split pending says a seam exists, not that the split is cheap, safe or worth the churn.
- The commit counts cover one 90-day window at one pin. They cannot show whether that window is typical.
- `ceiling_edit_load` counts commits that changed an offender's length. It does not count the pull requests that would carry a ceiling edit, and a squash merge makes the two only roughly equal.
- The Figures table cannot say whether this number or shape of exceptions is "unreasonable". That ruling is the engineer's, and this assessment makes none.

**For the engineer.**
- `claude/.claude/tests/helpers.py` cites two issues its reason says disagree: #1015 and #1396. Its row records both and does not settle which governs.
- The `shell_undetermined_over_1000` row names five shell files whose side of the 1,000-line threshold is undetermined. The `shell_undetermined_over_1500` and `shell_undetermined_over_2000` rows leave no shell file undetermined.

## Defaults and consequences

GH-1119 lists the plan defaults and consequences below for the engineer to see when this assessment is presented. Each item points at the Figures-table row that applies, if one does.

**Plan defaults, as GH-1119 lists them (not engineer decisions):**
- Counting physical lines rather than non-comment lines. The `physical_over_*` rows and the `noncomment_over_*` rows give each threshold in both units. The non-comment unit is exact for Python and a lower bound for shell, and the `shell_undetermined_over_*` rows name each shell file whose side of a threshold that bound leaves open.
- The size check's failure message: its content, its remedy ordering and what it prohibits. That belongs to the size check, so no row here applies.
- Failing a row whose file no longer exists, or whose ceiling is at or under the general limit. That belongs to the size check, so no row here applies.

**Consequences to flag, as GH-1119 lists them:**
- A new-offender failure will land on whichever future PR crosses the 1,000-line mark. The `near_limit_files` and `near_limit_at_exactly_limit` rows and the Near-limit files table name the files closest to it. A file at exactly 1,000 lines passes.
- The 1,000-line limit for test files puts many test files over it. The `offenders_test` row gives the count, and the transcript-analysis decomposition under #1116 keeps creating test files over the limit, so each of its phases needs its own split-or-exception verdict for the tests it moves.
- Both-direction ceilings put a ceiling edit into every PR that changes an exception file's length, whether it grows or shrinks, including every phase of #1116. The `ceiling_edit_load` row sets the commits that changed an offender's length against the `window_non_merge_commits` row.
- A ceiling raise is a one-line table edit with a required reason field. The reviewed diff and the reason are the only friction for an agent doing it mechanically. No row applies.

**This assessment's own defaults:**
- Both line counts are reported. Each proposed ceiling is the physical count, because the size check counts physical lines.
- The commit window is the 90 days before the pin's committer date, with renames followed.
- A file is test code when its path has a `tests` directory component or its name is `test_*.py` or `conftest.py`. The `offenders_test` and `offenders_production` rows apply that rule. The `offenders_named_test` and `offenders_not_named_test` rows apply the name alone, so the two splits differ by one support module, `claude/.claude/tests/helpers.py`, which sits under a `tests` directory without a test name.
- One issue is proposed per split-pending file that has none.

## Method

**Units.**
- Physical lines are the newline count, which is what `wc -l` prints. The program splits on `\n` only, because `ast` and `tokenize` number lines the same way and `str.splitlines()` would also split on characters such as U+2028.
- Non-comment lines exclude blank lines, comment lines and, for Python, docstrings.
  - For Python, `tokenize` finds comments and `ast` finds docstrings, which are the string-constant first statements of a module, class or function body.
  - A line that holds a docstring and code counts.
  - A whitespace-only line inside a multi-line string does not.
  - A Python file that either parser rejects reads `unparsed`.
- For shell, a comment line is one whose first character other than a space, tab, vertical tab, form feed or carriage return is `#`. A `#`-led line inside a heredoc body or a multi-line quoted string is miscounted as a comment, so the shell cell can only understate and prints as a lower bound (`≥N`).
- The non-comment column feeds only the unit-sensitivity rows. No verdict or ceiling reads it.

**Window and commits.** The window is the 90 days before the pin's committer date.
- A counted commit meets all of these conditions:
  - It is reachable from the pin.
  - It is not a merge.
  - It falls inside the window.
  - It touches the file's history, followed across renames.
- The program prints counts only.
- The ceiling-edit load is the number of distinct commits in the window that changed some offender's length, meaning its added and deleted line counts differ. A pure rename does not count.
- The window holds no merge commit and every commit subject in it ends with a pull-request number, so a commit there is roughly a PR.

**Verdict rule.** Every row gets one verdict.
- **Structural exception.** Splitting is blocked by something outside the file's current state, and the reason cites a `file:line`, an issue, or the engineer's ruling on this report's PR. Three kinds qualify:
  - A loading or shipping constraint that requires one file.
  - A recorded decision that governs the file's shape, such as #1117's in-file reorganization of `_lib.sh` or #1015's gates on `helpers.py` and the `conftest.py` files it covers.
  - A body that is one data table or generated output.
- Where #1015 and #1396 disagree on `helpers.py`, its row names both and the Summary lists it for the engineer.
- **Split pending.** The file has at least one named seam along which parts can move to sibling files without changing how the file is loaded, invoked or installed. A seam is a subcommand, domain, detector, lifecycle stage or test-class family. Positional splits such as a `_part2` file do not count.
- Churn and margin do not change a verdict. A file barely over the limit with no churn is still split pending. The Figures table reports the counts over 1,000, 1,500 and 2,000 lines instead.
- Each verdict is a read-only judgment from pinned blobs, public issue bodies and `.claude/plans/f1-offender-assessment.md`, working from an outline and targeted reads for large files. It is judgment, not measurement, and holds no measured figure.

**Cells.** A judgment gives each row a Subject, Seams, Verdict and last cell. No cell is empty or holds a `|`.
- A split-pending Seams cell names the seam's symbols in backticks. A build check, not committed with this report, confirmed each occurs as a whole word in the file's blob at the pin. That confirms the name is there, not that the seam is real.
- The Verdict cell reads exactly `split pending` or `structural exception`.
- A split-pending row's last cell reads `#<digits>` for an existing issue, or `proposed (not filed)`. The existing issues are #1117 with its sub-issues #1433 to #1436, #1530, #1531, #1532 and #1015, plus #1118, under which `test_skills.py`'s split was in progress at the pin.
- A structural exception's last cell is reason prose with at least one backticked `path:line` or `#<digits>` citation.
- A row the engineer declines to split becomes a structural exception whose reason reads `engineer's ruling on #<n>: "<approved text>"`, where `<n>` is this report's PR.

**Ceilings.** Each row's proposed ceiling is its Physical cell. The size check sets each ceiling again when it lands.

**Proposed issues.** This report files no issue. Proposed issues are filed after merge, outside this report, and GH-1113's task list lists each one filed. One issue is proposed per split-pending file whose last cell reads `proposed (not filed)`:
- Title: "Split `<path>` below 1,000 lines".
- Body: names the seam, this report's path and "Part of GH-1113".
- Closing condition: the PR that brings the file to 1,000 lines or fewer.
- For a closure file below, the body also carries the four windows and names #1114 and #1115 as where to read the review bench campaign's current state.

**Harness closure.** The review bench hashes a set of files when it freezes its baseline, and a change to any of them after the freeze invalidates that baseline. The set comes from `compute_harness_closure()` in `evals/review_bench/analysis.py`, which walks imports statically from the bench's runner, adjudicator and analysis modules. A closure file that is also an offender carries `; closure-windows` after its tracking value or reason. The windows, from `.claude/plans/measure-review-quality.md:364-368, :376`:
- Before the smoke campaign, splitting is free.
- After the smoke campaign and before `freeze`, splitting is allowed but forces a re-smoke.
- From `freeze` through #1115's acceptance campaign, splitting is forbidden. A closure change invalidates the baseline and reruns every arm.
- After #1115's acceptance campaign, splitting is free.

Closure files that are also offenders, from the closure function's output:
- `evals/measure_subagent_model_resolution.py`
- `evals/review_bench/analysis.py`
- `evals/review_bench/runner.py`
- `evals/run_skill_evals.py`

**Environment and checks.** The measurements ran on Python 3.14.10 (main, Oct 28 2026, 12:07:23) [Clang 21.0.0 (clang-2100.1.1.101)] and git version 2.50.1 (Apple Git-155). The 90-day window starts 2026-08-22T01:33:49+0000.

Each check below was run at the pin and passed.
- The offender set and every physical count equal what `wc -l` prints over `git ls-files` for the two suffixes. This proves the newline-based physical column and the offender set, including the one offender that holds a literal U+2028.
- The near-limit set equals the same `wc -l` listing filtered to the 900 to 1,000 range.
- The 90-day commit count for `claude-skills/skills/tests/test_skills.py` equals an independent `git log --follow` count over the same window. This proves rename-following and the window start for that file, which `c6fcd96e` renamed inside the window.
- The ceiling-edit load is no larger than the window's total non-merge commit count, as a count of a subset must be.
- Two runs of the program at the pin produced byte-identical output.
- The harness closure was computed once, while the working tree still held the pin's files. All four closure files listed above are in it, so the result is not empty.

## Structural exceptions

These files are over the limit and stay over it, each for a cited reason. Rows run by physical count, largest first.

| Path | Physical | Non-comment | 90-day commits | Subject | Seams | Verdict | Tracking or reason |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `claude/.claude/hooks/_lib.sh` | 6233 | ≥2918 | 74 | Shared library every hook sources, holding command parsing, push parsing, worktree locks, resume context and review helpers | In-file sections by concern and consumer-sourced siblings, both sequenced by #1117 | structural exception | In-file reorganization and the gate for moving any cluster are recorded in #1117, which governs this file's shape and ceiling. No further seam is assessed here. |
| `claude/.claude/tests/helpers.py` | 4124 | 2477 | 52 | Shared test helpers imported by hook, script and skill test trees: path constants, parsers, hook runners, repo builders, marker writers and shims | Families named in #1015 (path constants, parsers, `run_hook` runners, `init_git_repo` builders, `install_shell_exec_guard` shims, marker writers) | structural exception | #1015 declines decomposition until its data and mapping gates hold, and GH-1119 defers to it. #1396 proposes decomposing the file. The two issues disagree, so the engineer rules. |
| `claude/.claude/scripts/cursor-adapter/hook-shim.py` | 1897 | 1401 | 3 | Cursor hook shim that translates one Cursor payload into an unchanged Claude hook run and maps the output back | Sections inside the file are payload parsing (`parse_strict_json`), translation (`build_claude_payload`), output mapping (`map_hook_output`), the hook run (`run_wrapped_hook`) and the self-check (`self_check`) | structural exception | Generated commands run it as `python3 -I -S hook-shim.py`, so no neighbouring module reaches it (`docs/scripts.md:580`, `claude/.claude/scripts/cursor-adapter/hook-shim.py:9`). |

## Split pending

Each file below has a named seam. Rows run by physical count, largest first, in one table per directory group.

### `claude/.claude/hooks/`

| Path | Physical | Non-comment | 90-day commits | Subject | Seams | Verdict | Tracking or reason |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `claude/.claude/hooks/tests/test_marker_script.py` | 7303 | 5208 | 31 | Tests for the marker.sh subcommands: write, check, status, clear-stale and review-pr | Test-class families following marker.sh's status and write halves: write (`TestMarkerScriptHappyPath`, `TestMarkerScriptVerification`), status (`TestMarkerScriptStatusCompletionMarkers`, `TestMarkerScriptStatusActiveBypass`), `TestMarkerScriptClearStale`, `TestMarkerScriptCheck` and `TestMarkerScriptReviewPr` | split pending | #1530 |
| `claude/.claude/hooks/tests/test_require_skill_review.py` | 6014 | 4380 | 15 | Tests for the gate that requires a skill review before committing skill changes | Test-class families `TestRequireSkillReview`, `TestSharedGateDiffBaseClosureResidual`, `TestSkillReviewGateMergeAwareVerdict`, `TestSkillReviewGateConflictMarkerHardDeny`, `TestSkillReviewPathspecParity` and `TestCorpusBudgetWarning` | split pending | proposed (not filed) |
| `claude/.claude/hooks/tests/test_deny_private_project_refs.py` | 5440 | 3982 | 24 | Tests for the redaction gate that denies private-project references in commits and gated commands | One test class, so the seam is test-method families inside `TestDenyPrivateProjectRefs`: structural detectors (`test_structural_ipv4_literal_denied`, `test_structural_long_hex_identifier_denied`), blocklist (`test_blocklist_entry_whitespace_trimmed`), gh dispatch (`test_gh_api_default_get_not_dispatched`) and opt-in marker (`test_optin_marker_allows_tracker_commit_when_staged_diff_is_empty`) | split pending | #1531 |
| `claude/.claude/hooks/tests/test_review_ledger_script.py` | 4937 | 3565 | 10 | Tests for the review-ledger.sh subcommands: append, show, render and carry | Test-class families following the ledger library's validation and render halves: append (`TestReviewLedgerAppendHappyPath`, `TestReviewLedgerRoundValidation`), render (`TestReviewLedgerRenderOutConfinement`), `TestReviewLedgerShow`, `TestReviewLedgerCarryAcrossSessions` and `TestReviewLedgerCreationMode` | split pending | proposed (not filed) |
| `claude/.claude/hooks/tests/test_enforce_marker_script_shape.py` | 4370 | 3129 | 17 | Tests for the gate that enforces marker.sh invocation shape and denies marker and ledger writes | Test-class families following the hook's write-scan and shape-matcher halves: shape (`TestEnforceMarkerScriptShape`, `TestPrescriptionAllowlistAlignment`), gate-release authority (`TestGateReleaseAuthority`, `TestGateReleaseAuthorityFileWrites`) and ledger paths (`TestReviewLedgerAppendAuthority`, `TestReviewLedgerStatePaths`) | split pending | proposed (not filed) |
| `claude/.claude/hooks/tests/test_hook_alignment.py` | 3756 | 2202 | 39 | Repo-wide alignment tests over hook files, registrations, regex portability and tier headers | Test families by concern: class headers (`TestHookClassHeader`), gate behavior (`TestGateHookBehavior`), regex portability (`test_no_gnu_backslash_s_regex_extension`) and registration shape (`test_every_registered_hook_entry_has_type_and_command`) | split pending | #1532 |
| `claude/.claude/hooks/tests/test_nudge_handoff_near_context_cap.py` | 3740 | 2430 | 19 | Tests for the nudge that asks for a handoff near the context cap | Test-class families `TestNudgeHandoffNearContextCap`, `TestHandoffActiveBypassMarkerSuppressesTheBlock`, `TestNudgeLogTelemetry` and `TestCheckMode`. Method families inside the main class: threshold and re-arm timing (`test_below_threshold_is_silent`, `test_rearm_boundary_at_last_fired_plus_spacing`), escalation and hard block (`test_escalation_ladder_blocks_once_estimate_reaches_block_at`, `test_hard_block_only_fires_on_post_tool_batch`), transcript reading (`test_incremental_read_stops_offset_before_incomplete_trailing_line`), fail-open paths (`test_jq_absent_fails_open_not_hard_blocked`, `test_transcript_file_absent`), per-model windows (`test_dated_snapshot_suffix_resolves_same_window_as_dateless`) and message wording (`test_message_states_the_resume_route_rule`). | split pending | proposed (not filed) |
| `claude/.claude/hooks/tests/test_require_plan_review.py` | 3049 | 2151 | 15 | Tests for the gate that requires a plan review before plan edits and plan-mode exit | Test-class families `TestRequirePlanReview`, `TestPlanFileAuthoringExemption`, `TestRequirePlanReviewExitPlanMode`, `TestRequirePlanReviewCrossWorktree` and `TestRequirePlanReviewPlanMode` | split pending | proposed (not filed) |
| `claude/.claude/hooks/tests/test_require_ready_for_review.py` | 2871 | 2178 | 18 | Tests for the gate that requires the ready-for-review marker before a push or PR create | Two test classes, one dominant, so the seam is the second class (`TestRequireReadyForReviewHonorsConfigDir`) plus method families inside `TestRequireReadyForReview`: push handling (`test_upstream_only_push_allowed`), marker checks (`test_no_marker_denies`), PR create (`test_gh_pr_create_with_completion_marker_allowed`) and deny wording (`test_deny_message_names_pr_create`) | split pending | proposed (not filed) |
| `claude/.claude/hooks/tests/test_config_lib.py` | 2631 | 1532 | 8 | Tests for the shell config library: resolution, legacy polarity, writes, scaffolding and memoization | Test-class families following the reader and writer halves of `_config.sh`: resolution (`TestExitCodeContract`, `TestLegacyPolarity`, `TestUnionSemantics`), writer (`TestConfigSet`, `TestConfigScaffold`) and memoization (`TestMemoization`, `TestMemoizationUnionBranch`) | split pending | proposed (not filed) |
| `claude/.claude/hooks/tests/test_cursor_adapter_gate_matrix.py` | 2388 | 1966 | 5 | Gate matrix tests that run each registered gate through the Cursor adapter shim | Test-class families `TestCursorPayloadBuilders`, `TestShimEnvelope`, `TestGateMatrix`, `TestGeneratedMatcherCoverage` and `TestSessionIdentityAcrossTheShim` | split pending | proposed (not filed) |
| `claude/.claude/hooks/tests/test_design_decision_files.py` | 2374 | 1675 | 13 | Tests that check the shape of each design-decision file and the checker's fault handling | Test-class families `TestPerFileShape` and `TestFaultInjection` | split pending | proposed (not filed) |
| `claude/.claude/hooks/tests/test_send_back_skipped_self_review.py` | 2320 | 1663 | 3 | Tests for the hook that sends a subagent back when its self-review was skipped | Test families by concern as plain functions: path floor (`test_path_floor_reviewers_table`), transcript parsing (`test_extract_tool_calls_is_error_absent_false_and_true`), assessment rules (`test_missing_diff_reread_after_last_edit_fails_r5_only`) and hook envelope handling (`test_allow_envelope_preserves_every_other_tool_input_field`) | split pending | proposed (not filed) |
| `claude/.claude/hooks/tests/test_lib_worktree_collision_guard.py` | 2177 | 1485 | 6 | Tests for the worktree collision guard and lock helpers in the hook library | Test-class families `TestConcurrentLockRace`, `TestCollisionGuardBranches`, `TestDeadLockReclaim`, `TestWorktreeLockPid`, `TestResolveClaudePid` and `TestRegisteredLiveSid` | split pending | proposed (not filed) |
| `claude/.claude/hooks/tests/test_lib_cursor_identity.py` | 2136 | 1747 | 1 | Tests for the Cursor identity helpers and session-keyed lock rules in the hook library | Test-class families `TestAdapterIdentity`, `TestSessionKeyedLockRule`, `TestSessionKeyedGuard`, `TestCopyAllowSuppressed` and `TestCursorPayloadDecline` | split pending | proposed (not filed) |
| `claude/.claude/hooks/tests/test_marker_lib.py` | 1967 | 1480 | 15 | Tests for the marker and diff-hash helpers in the hook library | Test-class families `TestLibHashDiffText`, `TestGitDiffEntryShapes`, `TestLibCumulativeReviewValue`, `TestLibActivePlanHash` and `TestLibRepoRoot` | split pending | proposed (not filed) |
| `claude/.claude/hooks/tests/test_require_code_review.py` | 1912 | 1362 | 14 | Tests for the gate that requires a code review before commit | Test-class families `TestRequireCodeReview`, `TestRequireCodeReviewComplianceLog`, `TestRequireCodeReviewMergeAwareBase` and `TestRequireCodeReviewForgedAnchorEmptyBaseDiff` | split pending | proposed (not filed) |
| `claude/.claude/hooks/tests/test_deny_pii_in_commits.py` | 1863 | 1193 | 14 | Tests for the gate that denies commits whose diff or message holds PII or credential patterns | One test class, so the seam is test-method families inside `TestDenyPiiInCommits`: card detection (`test_luhn_valid_13_digit_card_denied`), git failure handling (`test_staged_diff_git_timeout_denied`), commit-message forms (`test_F_pseudo_file_rejected`) and user patterns (`test_user_pattern_no_match_allowed`) | split pending | proposed (not filed) |
| `claude/.claude/hooks/tests/test_record_session_config_keys.py` | 1813 | 1353 | 3 | Tests for the hook that records session config keys to a log | Test-class families `TestRegistration`, `TestRowContent`, `TestLogFile`, `TestWriteGuard`, `TestRetriedAppend` and `TestReaderDocTrimRecipe` | split pending | proposed (not filed) |
| `claude/.claude/hooks/tests/test_require_worktree_for_git_writes.py` | 1793 | 1200 | 12 | Tests for the gate that requires a worktree for git writes, with its machine-level marker and collision guard | Test-class families `TestRequireWorktreeForGitWrites`, `TestMachineLevelMarker`, `TestMachineMarkerUnderConfigDir` and `TestWorktreeCollisionGuard` | split pending | proposed (not filed) |
| `claude/.claude/hooks/tests/test_bootstrap_project_plugins.py` | 1640 | 1171 | 3 | Tests for the session-start hook that bootstraps project plugins | Test-class families `TestLaunchContract`, `TestMissingIdDecision`, `TestPayloadResolution`, `TestTrustLayerOne`, `TestMachineLevelSwitch` and `TestAuditLog` | split pending | proposed (not filed) |
| `claude/.claude/hooks/tests/test_deny_reviewer_tree_mutation.py` | 1622 | 986 | 15 | Tests for the gate that denies reviewer agents from mutating the working tree | Test-class families `TestFileWriteTools`, `TestBashGitWrites`, `TestBashInPlaceEditFamily`, `TestCommandWordResolution`, `TestKnownGapBypass` and `TestFullRoster` | split pending | proposed (not filed) |
| `claude/.claude/hooks/tests/test_check_skill_length.py` | 1436 | 1070 | 25 | Tests for the length check on skill files, including merge-aware base resolution | Test-class families `TestCheckSkillLength` and `TestCheckSkillLengthMergeAwareBase`, with the merge builders (`_build_clean_merge_growing_skill`) moving with the second | split pending | proposed (not filed) |
| `claude/.claude/hooks/tests/test_ask_new_dependency_disclosure.py` | 1425 | 1034 | 4 | Tests for the hook that asks for disclosure when an edit adds a new dependency | Test-class families by manifest format (`TestComputeNewDependencyNamesRequirementsTxt`, `TestComputeNewDependencyNamesGoMod`, `TestComputeNewDependencyNamesCargoToml`) apart from the hook-level classes (`TestCliWrapperIO`, `TestHookFilterSteps`, `TestHookEnvelopeIntegrity`) | split pending | proposed (not filed) |
| `claude/.claude/hooks/tests/test_advance_past_commit_stall.py` | 1424 | 1015 | 10 | Tests for the stop hook that advances a stalled turn when work is pending | Test families by concern: fire predicate (`test_fire_predicate_corpus`), pending-work detection (`test_dirty_tree_is_pending`), disable switches (`test_kill_switch_disables`), degraded environments (`test_jq_absent_exits_zero_empty_stdout`) and state sweep (`test_sweep_deletes_entries_older_than_30_days`) | split pending | proposed (not filed) |
| `claude/.claude/hooks/tests/test_agent_roster.py` | 1385 | 826 | 16 | Tests that pin the reviewer agent roster, frontmatter, tool grants and prompt contracts | Test-class families `TestReviewerAgentRoster`, `TestAgentFrontmatter`, `TestNoGateReleaseRosterSync`, `TestCisoReviewerSecurityBulletsPin` and `TestScratchExecutionSection` | split pending | proposed (not filed) |
| `claude/.claude/hooks/tests/test_guard_settings_session_keys.py` | 1344 | 1007 | 10 | Tests for the commit gate that guards session-scoped settings keys | One test class, so the seam is test-method families inside `TestGuardSettingsSessionKeys`: top-level keys (`test_model_change_denies_commit`), nested keys (`test_nested_effort_level_added_where_env_absent_denies`), deny messages (`test_deny_message_names_both_nested_keys`) and passthrough (`test_non_commit_command_allows`) | split pending | proposed (not filed) |
| `claude/.claude/hooks/_config.sh` | 1337 | ≥667 | 7 | Config-key reader and writer shared by hooks, install.sh and the config scripts | Writer half (`_config_set`, `_config_scaffold`, `_config_quote_value_for_write`) apart from the reader (`_config_resolve`, `_config_lookup`); the writer sibling must stay independent of `_lib.sh` | split pending | #1117 |
| `claude/.claude/hooks/tests/test_require_respond_pr.py` | 1330 | 844 | 6 | Tests for the gate that requires respond-pr before PR comment reads and writes | Test-class families `TestRequireRespondPr`, `TestRespondPrStructuralInvariants`, `TestReviewPrBareReadsAreDenied` and `TestReviewPrWriteDeniedUnconditionally` | split pending | proposed (not filed) |
| `claude/.claude/hooks/tests/test_deny_invisible_commit_content.py` | 1323 | 742 | 11 | Tests for the gate that denies commits whose content a chained command changes invisibly | One test class, so the seam is test-method families inside `TestDenyInvisibleCommitContent`: chained mutation (`test_semicolon_chained_add_then_commit_denied`), multi-commit (`test_mutation_between_two_commits_denied`), continue forms (`test_mixed_commit_and_continue_fragments_denied`) and missing tools (`test_grep_absent_from_path_denied`) | split pending | proposed (not filed) |
| `claude/.claude/hooks/tests/test_nudge_long_turn_subagent.py` | 1305 | 830 | 4 | Tests for the nudge that suggests a subagent after a long turn | One test class, so the seam is test-method families inside `TestNudgeLongTurnSubagent`: firing (`test_fires_for_a_subagent_dispatch_above_threshold`), state and sweep (`test_stale_marker_dir_entries_swept_only_on_sampled_fire`) and fail-open paths (`test_malformed_stdin_fails_open`) | split pending | proposed (not filed) |
| `claude/.claude/hooks/tests/test_announce_resume_command.py` | 1146 | 904 | 4 | Tests for the hook that announces the resume command for a continuity file | One test class, so the seam is test-method families inside `TestAnnounceResumeCommand`: runner resolution (`test_runner_resolves_resume_context_to_the_recording_stub`), linked-worktree advertising (`test_linked_worktree_with_matching_cwd_advertises_slash_form_in_both_channels`) and refused-name mismatches (`test_refused_name_in_a_file_naming_another_directory_announces_the_mismatch_target`) | split pending | proposed (not filed) |
| `claude/.claude/hooks/tests/test_deny_default_branch_push.py` | 1137 | 883 | 1 | Tests for the gate that denies pushes to the default branch | Test-class families by push shape: `TestExplicitDestination`, `TestRefspecLessPush`, `TestRedirectingConfig`, `TestGhApi`, `TestAliases` and `TestFailClosed` | split pending | proposed (not filed) |
| `claude/.claude/hooks/enforce-marker-script-shape.sh` | 1121 | ≥401 | 21 | Gate hook that denies agent writes to review-marker and review-ledger state and enforces marker.sh invocation shape | Bash write-scan (`_bash_marker_fragment_candidates`, `_bash_marker_redirect_candidates`, `_script_op_scan`) apart from the shape matcher (`_marker_shape_match`, `_shape_match_denial_reason`) | split pending | proposed (not filed) |
| `claude/.claude/hooks/tests/test_check_claude_md_length.py` | 1103 | 801 | 13 | Tests for the length check on CLAUDE.md files, including merge-aware base resolution | Test-class families `TestCheckClaudeMdLength` and `TestCheckClaudeMdLengthMergeAwareBase`, with the merge builders (`_build_clean_merge_growing_claude_md`) moving with the second | split pending | proposed (not filed) |
| `claude/.claude/hooks/tests/test_nudge_memory_store_audit.py` | 1069 | 759 | 2 | Tests for the nudge that asks for a memory-store audit when a store grows large | Test-class families `TestNudgeMemoryStoreAudit` and `TestMemoryAuditGitignoreEntries`, with method families inside the first for thresholds (`test_threshold_boundary`) and fail-open reads (`test_jq_absent_fails_open_silent`) | split pending | proposed (not filed) |
| `claude/.claude/hooks/tests/test_lib_resume_context.py` | 1050 | 839 | 2 | Tests for the resume-context helpers in the hook library | Test-class families `TestLibResumeContextIndexDir`, `TestLibLinkedWorktreeRoot`, `TestLibContinuityFileResumeCwd` and `TestLibIsPendingContinuityFile` | split pending | proposed (not filed) |

### `claude/.claude/scripts/`

| Path | Physical | Non-comment | 90-day commits | Subject | Seams | Verdict | Tracking or reason |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `claude/.claude/scripts/tests/test_transcript_review_rounds.py` | 6612 | 4673 | 13 | Tests for the review-round cost subcommand: round windows, dispatch pricing, round-open detection, bootstrap intervals and the pooled block | Test-class families matching the module's parts: round costs (`TestComputeReviewRoundCosts`, `TestComputeReviewRoundCounts`, `TestGoldenRoundProjections`), round-open detection (`TestRoundOpenAgentSkills`, `TestSlashRoundOpenRecordClass`, `TestOrchestratorDispatchOpensCodeReviewRound`), command (`TestCmdReviewRoundCost`), bootstrap (`TestBootstrapShareIntervals`), pooled block (`TestPooledPricingTrustLines`, `TestCmdReviewRoundCostPooled`, `TestPooledShowWithheld`) | split pending | proposed (not filed) |
| `claude/.claude/scripts/tests/test_transcript_period_cost.py` | 6314 | 4909 | 8 | Tests for the period-cost subcommand: window validation, review windows, pull-request lookup, group assembly, Slack rendering and notices | Test-class families matching the module's parts: windows (`TestValidatedWindow`, `TestReviewPrWindows`), pull-request lookup (`TestValidatedPrList`, `TestFetchPrLists`), group assembly (`TestAssembleGroups`, `TestPeriodStreamTotals`), rendering (`TestRenderSlackReport`, `TestGoldenSample`), notices (`TestNoticeLines`, `TestNoticesAtReportLevel`), topology and command (`TestTopologyReport`, `TestCommandBehavior`, `TestCommandWiring`) | split pending | proposed (not filed) |
| `claude/.claude/scripts/tests/test_transcript_routing_guard_capture.py` | 5483 | 4223 | 1 | Tests for the routing-guard subcommand: capture, note parsing, deny reconciliation, the store, log reading and cause output | Test-class families by lifecycle stage: capture (`TestFirstCapture`, `TestLoops`, `TestGrowthAndLaterTurns`), note parsing (`TestNoteParser`, `TestNoteLocation`), deny reconciliation (`TestDenyExtraction`, `TestReconcileDenies`), store and rows (`TestStore`, `TestValidator`, `TestRowLines`), log reading and causes (`TestLogReader`, `TestKeyStateAt`, `TestCauses`, `TestLostEvents`), output (`TestOutput`, `TestFreeText`) | split pending | proposed (not filed) |
| `claude/.claude/scripts/tests/test_cleanup_merged_branches.py` | 4951 | 3375 | 14 | Tests for cleanup-merged-branches.sh: tier classification, worktree handling, direnv credentials, all-projects sweep and the removable listing | Test-class families by subcommand and signal: Tier-B and ancestor classification (`TestTierBReachableNoMergedPR`, `TestAncestorOfMergedHeadCleaned`, `TestDescendantOfMergedHeadPromptsAsTierB`), worktree locks (`TestLockedWorktreeUnlockedAndRemoved`, `TestWorktreeInUseGuard`), direnv credentials (`TestDirenvAbsentBehaviorUnchanged`, `TestEnvrcRedirectionGuard`), all-projects sweep (`TestAllProjectsRootsFileParsing`, `TestAllProjectsDedupOverlappingRoots`), list-removable (`TestListRemovable`, `TestListRemovableFailsClosed`), archive (`TestBranchRecordsArchive`) | split pending | proposed (not filed) |
| `claude/.claude/scripts/tests/test_post_crash_sessions.py` | 4631 | 3150 | 8 | Tests for post-crash-sessions.py: boot time, evidence readers, classification, report rendering and CLI wiring | Function groups by section: boot-time probes (`test_boot_time_darwin_parses_sysctl_kern_boottime_output`), registry and lock readers (`test_read_registry_unicode_digit_filename_stem_does_not_raise`, `test_read_lock_malformed_json_returns_none`), transcript and lookup readers (`test_read_transcript_head_exhausts_bound_without_finding_cwd`, `test_read_lookup_entries_excludes_file_outside_window`), session-end records (`test_read_session_end_records_missing_dir_reports_not_found`), classification (`test_classify_registry_dead_before_boot_with_transcript_is_resumable`), report rendering (`test_render_report_possible_crash_section_lists_rows_sorted_by_recency`), CLI wiring (`test_main_rejects_nonexistent_config_dir`) | split pending | proposed (not filed) |
| `claude/.claude/scripts/tests/test_transcript_cost.py` | 4459 | 3055 | 19 | Tests for the transcript-analysis cost and cost-trend subcommands: roots, scoping, branch filter, pricing, summary and banners | Test-class families by report facet: core and roots (`TestCost`, `TestCostResolveRoots`, `TestCostMultiRootReport`), scoping (`TestCostByProject`, `TestCostByAccount`, `TestCostBranchFilter`), pricing (`TestFablePricing`, `TestOpus55Pricing`, `TestSonnet55Pricing`), summary and banners (`TestCostSummary`, `TestExcludedSpendBanner`, `TestPricingIntegrityBanner`), trend (`TestCostTrend`, `TestCostTrendConfigDir`) | split pending | proposed (not filed) |
| `claude/.claude/scripts/tests/test_cursor_adapter_shim.py` | 4366 | 3707 | 3 | Tests for the Cursor hook shim: failure outcomes, payload validation, prologue, hook run, faults, interpreter isolation and directory pin | Test-class families by shim stage: failure outcomes and arguments (`TestShimFailureOutcomes`, `TestShimArguments`), payload validation and prologue (`TestPayloadValidation`, `TestShimPrologue`), hook run (`TestShimHookRun`), faults and isolation (`TestShimFaults`, `TestShimInterpreterIsolation`), directory pin (`TestDirectoryPin`) | split pending | proposed (not filed) |
| `claude/.claude/scripts/tests/test_author_outcome.py` | 3998 | 2816 | 8 | Tests for the author-outcome transcript analysis: round and marker detection, ledger lookup, outcome buckets and orchestrator dispatch handling | Test-class families by stage: round and marker detection (`TestIsCleanMarkerWrite`, `TestRoundHasMarkerWrite`, `TestClassifyRound`), ledger lookup and sweep (`TestLedgerIndex`, `TestLedgerPossiblySwept`, `TestMultiWorktreeLedgerMerge`), outcome buckets (`TestComputeAuthorOutcomesBuckets`, `TestRankMapping`), orchestrator projection (`TestOrchestratorTranscriptProjection`, `TestOrchestratorDispatchIsARoundOpen`) | split pending | proposed (not filed) |
| `claude/.claude/scripts/tests/test_review_pr_checkout.py` | 3996 | 3128 | 4 | Tests for the review-pr checkout step: trust classification, stacked-base audit, provenance, diff file, git-hook isolation and failure handling | Test-class families by checkout stage: identity checks (`TestOwnerRepoOriginMismatch`, `TestOwnerRepoCaseInsensitiveMatch`), trust and stacked-base audit (`TestTrustClassificationRefuses`, `TestStackedBaseDeltaAudit`, `TestStackedBaseGate`), diff file and provenance (`TestReviewDiffFile`, `TestProvenanceWrite`), fetch hardening (`TestCheckoutRunsNoGitHooks`, `TestFetchesNeverPromptForCredentials`), failure paths (`TestWorktreeAddFailure`, `TestSessionArtifactWriteFailure`) | split pending | proposed (not filed) |
| `claude/.claude/scripts/tests/test_select_tests.py` | 3887 | 2423 | 57 | Tests for select-tests.py: domain selection, rule-table fidelity, argv and worker sizing, git path computation and selection logging | Test-class families matching the script's parts: selection (`TestSelectPytestTargets`, `TestCursorAdapterSelection`, `TestResolveTargetPaths`), rule-table audits (`TestCrossDomainReadCompleteness`, `TestKnownCrossDomainReadsAudit`, `TestRuleTablePathFidelity`), run and workers (`TestBuildPytestArgv`, `TestComputeWorkerCount`, `TestPytestSubprocessEnv`), git paths (`TestComputeChangedPathsGitSmoke`, `TestComputeChangedPathsFailureModes`), logging (`TestRecordSelection`, `TestRecordSelectionReasonCoverage`), main (`TestMainComposition`) | split pending | proposed (not filed) |
| `claude/.claude/scripts/tests/test_review_ledger_lib.py` | 3847 | 2820 | 7 | Tests for _review-ledger-lib.sh: flag validation, site hash, tree pinning, carry checks, rendering and output files | Test-class families matching the library's functions: flag and lane validation (`TestValidateFlagsRejects`, `TestValidateLanesRejects`), site hash and location (`TestSiteHash`, `TestNormalizeLocation`), tree pinning (`TestReviewedTree`, `TestPinTree`), ref and carry checks (`TestCheckRef`, `TestCheckCarry`, `TestCarryLine`), render and output (`TestRenderBlock`, `TestCheckOut`, `TestTrend`) | split pending | proposed (not filed) |
| `claude/.claude/scripts/tests/test_transcript_pr_cost.py` | 3557 | 2753 | 7 | Tests for the pr-cost subcommand: branch totals, model-breakdown and review-rounds cells, recording and refusals | Test-class families by ledger cell: branch totals (`TestComputePrCostBranchTotals`, `TestPrCostBranchTotalsByModel`), branch-to-PR resolution (`TestResolveBranchPrTieBreak`, `TestResolveBranchPrJoinConfidence`), report and recording (`TestPrCostReportOrchestration`, `TestPrCostRecordWritesAndReportsTheBreakdown`), model-breakdown cell (`TestCheckModelBreakdownCell`, `TestSeededBreakdownSweep`), review-rounds cell (`TestReviewRoundsCellCapture`, `TestCheckReviewRoundsCell`, `TestReviewRoundsForceRefusal`) | split pending | proposed (not filed) |
| `claude/.claude/scripts/tests/test_review_pr_lib.py` | 3108 | 2375 | 7 | Tests for the review-pr shared library: jq filters, OID and file helpers, audit match reporting, authorship and stack-walk verdicts | Test-class families by helper domain: jq filters and decoding (`TestFileNamesJqFilter`, `TestReviewsJqFilter`, `TestDecodeFileNames`), audit matching (`TestAuditMatchLines`, `TestAuditMatchReport`, `TestAuditPathsMissingFromBody`), authorship (`TestAuthorLogin`, `TestAuthorship`), stack lookup verdicts (`TestStackLookupVerdictClassification`, `TestStackLookupVerdictChangesRequested`), stack document and walk (`TestStackDocumentShape`, `TestWalkStackGateMatrix`, `TestWalkStackCycles`) | split pending | proposed (not filed) |
| `claude/.claude/scripts/tests/test_reconcile_project_plugins.py` | 2354 | 1830 | 5 | Tests for reconcile-project-plugins: preconditions, marketplace filters, install argv, locking, wall-clock ceiling and audit log | Test-class families by concern: preconditions and scope (`TestPreconditionRefusals`, `TestScopeGuard`, `TestLocalOptOut`), marketplace (`TestMarketplacePrecondition`, `TestLocalFiltersRunBeforeMarketplaceListing`), install (`TestInstallArgv`, `TestInstallCapKill`), lock and ceiling (`TestLock`, `TestWallClockCeiling`), audit log (`TestAuditLogFields`, `TestAuditLogConcurrency`, `TestAuditLogSizeCap`) | split pending | proposed (not filed) |
| `claude/.claude/scripts/tests/test_transcript_reviewer_yield.py` | 2307 | 1783 | 9 | Tests for the reviewer-yield subcommand: reviewer detection, cited-path extraction and normalization, transcript scanning and edit indexing | Test-class families: reviewer detection (`TestIsReviewerSubagentType`), core yield (`TestReviewerYield`), cited paths (`TestExtractCitedPaths`, `TestNormalizeCitedPath`, `TestReviewerYieldCitedKeys`), transcript scanning (`TestScanReviewerTranscripts`, `TestScanReviewerTranscriptHandback`), edit indexing (`TestIndexSessionEdits`) | split pending | proposed (not filed) |
| `claude/.claude/scripts/tests/test_transcript_gh_cli.py` | 2283 | 1609 | 5 | Tests for the transcript-analysis gh and git-remote helpers: discovery, backoff, repo identity pinning and origin parsing | Test-class families by helper: discovery (`TestGhDiscoverMergedPrsPagination`, `TestGhDiscoveryArgv`, `TestGhPrList`), backoff (`TestGhCallWithBackoffFailureClassBehavior`, `TestGhCallWithBackoffMaxAttempts`), repo identity (`TestResolvePinnedGhRepoIdentity`, `TestGhHostQualifiedRepo`), origin parsing (`TestGitRemoteOriginHostAndOwnerRepoRegex`, `TestOriginIdentityFromUrl`, `TestOriginIdentityAtRealGit`) | split pending | proposed (not filed) |
| `claude/.claude/scripts/tests/test_ci_watch.py` | 2212 | 1552 | 6 | Tests for ci-watch.sh: usage errors, token override, direnv credential resolution and GH_HOST handling | Credential-source families as plain test functions: token override (`test_checks_gh_token_override_applies_to_watch_and_json_only`), direnv probe and token resolution (`test_direnv_supplies_token_when_ambient_unset`), real-direnv end-to-end (`test_resolve_ci_checks_gh_token_against_real_direnv_end_to_end`), GH_HOST resolution (`test_direnv_supplies_gh_host_when_ambient_unset`), cross-host mismatch gate (`test_ghe_remote_with_no_gh_host_anywhere_still_withholds_ambient_token`) | split pending | proposed (not filed) |
| `claude/.claude/scripts/tests/test_pr_diff_against_base.py` | 2178 | 1425 | 12 | Tests for pr-diff-against-base.sh: base resolution, fallbacks, record and diff-file flags and staged mode | Test-class families by mode and flag: base resolution and fallback (`TestNormalPathAgainstMain`, `TestGhPrViewFailureFallback`, `TestStackBaseKeyPath`), record flag (`TestRecordFlag`, `TestRecordFlagWithoutASession`), diff-file flag (`TestDiffFileFlag`, `TestDiffFileFlagWithoutASession`), staged mode (`TestStagedMode`, `TestStagedModeWithoutASession`) | split pending | proposed (not filed) |
| `claude/.claude/scripts/tests/test_transcript_routing_guard_compute.py` | 2164 | 1665 | 1 | Tests for the routing-guard compute stage: readiness, sample selection, pricing, net estimate, bootstrap and output | Test-class families by compute stage: readiness and sampling (`TestReadiness`, `TestSelectSample`, `TestSampleReasonsEndToEnd`), capture files and pricing (`TestCaptureFiles`, `TestPricing`), net estimate and bootstrap (`TestPointNet`, `TestBootstrap`, `TestBootstrapThroughCompute`), output (`TestOutput`, `TestOutPath`, `TestUnexpectedFailures`) | split pending | proposed (not filed) |
| `claude/.claude/scripts/transcript_analysis/routing_guard.py` | 2115 | 1664 | 1 | Routing-guard subcommand that captures review-round loops and computes a net-cost estimate | Lifecycle stages: capture (`_run_capture`, `_parse_capture_bytes`, `_load_capture_file`), session config-keys log reading (`_read_session_config_keys_log`), deny reconciliation (`_reconcile_denies`), compute (`_run_compute`, `_bootstrap_net`) | split pending | proposed (not filed) |
| `claude/.claude/scripts/post-crash-sessions.py` | 2047 | 1353 | 5 | Reports which Claude sessions ended in a crash from registry, lock, transcript and session-end evidence | Liveness probes (`_ps_lstart`, `_entry_liveness`, `_boot_time`), evidence readers (`_read_registry`, `_read_lock`, `_scan_transcripts`, `_read_session_end_records`), classification (`_classify_session`), rendering (`render_report`, `render_json`) | split pending | proposed (not filed) |
| `claude/.claude/scripts/tests/test_worktree_lib.py` | 1977 | 1456 | 6 | Tests for the worktree library: in-use detection, process cwd collection, branch resolution, removal verdict, filters and the ensure checks | Test-class families by helper: in-use detection (`TestWorktreeInUseIdlePath`, `TestWorktreeInUseLiveProcess`, `TestCollectProcessCwdsExcludesSelf`), resolution and listing (`TestResolveWorktreeForBranch`, `TestCollectAllWorktrees`, `TestWorktreeMatchesFilter`), state checks (`TestWorktreeIsClean`, `TestWorktreeRemovalVerdict`), ensure checks (`TestEnsureNameCheck`, `TestEnsureClassify`, `TestEnsureTargetCheck`, `TestEnsureDescribeRecord`) | split pending | proposed (not filed) |
| `claude/.claude/scripts/tests/conftest.py` | 1959 | 1139 | 44 | Shared scaffolding and autouse isolation fixtures for the scripts test tree. #1015's conftest section covers only select-tests fallback, so its gates do not govern a decomposition. | Transcript-record builders (`_asst`, `_priced`, `_user_msg`, `_write_jsonl`), git repo and worktree builders (`_make_worktree`, `_make_repo_with_remote`, `_init_repo`), direnv shim sources (`_shimmed_env`, `_noop_direnv_shim_source`), command shims (`_make_gh_pr_view_shim`, `_git_shim_that_fails_on_subcommand`) | split pending | proposed (not filed) |
| `claude/.claude/scripts/transcript_analysis/period_cost.py` | 1868 | 1417 | 6 | Period-cost subcommand reporting spend by repository and pull request for a time window | Review-window detection (`_review_pr_windows`, `_ReviewWindow`), pull-request lookup (`_fetch_pr_lists`, `_validated_pr_list`), group assembly (`_assemble_groups`), Slack rendering (`_render_slack_report`), report notices (`_notice_lines`) | split pending | proposed (not filed) |
| `claude/.claude/scripts/tests/test_cursor_adapter_translate.py` | 1857 | 1561 | 2 | Tests for the Cursor adapter's payload and output translation tables and their coverage guards | Test-class families by translation: output table (`TestOutputTable`), coverage guards (`TestHookOutputKeyCoverage`, `TestFieldTableCoverage`, `TestRequiredKeyPaths`), repo resolution (`TestRepoResolution`), tool translation (`TestDeleteTranslation`, `TestWriteTranslation`, `TestToolResponseTranslation`) | split pending | proposed (not filed) |
| `claude/.claude/scripts/tests/test_transcript_pr_cost_ledger.py` | 1854 | 1427 | 6 | Tests for the pr-cost ledger file: append, parse, frozen headers, model-breakdown and review-rounds cell codecs, and verified writes | Test-class families by ledger layer: rows (`TestAppendPrCostLedgerRow`, `TestPrCostLedgerConcurrentWrite`, `TestParsePrCostLedgerFileTextMalformed`), headers and migration (`TestPrCostLedgerFrozenHeaders`, `TestPrCostLedgerLegacyHostColumnMigration`), model-breakdown cell (`TestDecodeModelBreakdownCell`, `TestFormatModelBreakdownCell`), file write (`TestWritePrCostLedgerFileDurability`, `TestWritePrCostLedgerFileValuePostcondition`), review-rounds cell (`TestDecodeReviewRoundsCellAccepts`, `TestDecodeReviewRoundsCellRefuses`) | split pending | proposed (not filed) |
| `claude/.claude/scripts/tests/test_transcript_session_pricing.py` | 1838 | 1410 | 6 | Tests for the session-pricing function: windows, micro-dollar arithmetic, unpriced and unreadable inputs, collisions and the output contract | Test-class families by concern: window and arithmetic (`TestWindowAndDays`, `TestMicroDollars`), input defects (`TestUnpriced`, `TestReadability`, `TestUndatedTurns`, `TestStaleness`), request-id collisions (`TestRequestIdCollisions`), canaries and validator (`TestCanaries`, `TestValidator`), errors and output (`TestErrors`, `TestOutputContract`, `TestPublicSurfacePin`) | split pending | proposed (not filed) |
| `claude/.claude/scripts/tests/test_review_pr_diff.py` | 1788 | 1425 | 4 | Tests for the review-pr diff step: origin checks, head drift, base fetch hardening, audit handling and stacked-base gate | Test-class families by diff stage: origin and head drift (`TestOriginMismatch`, `TestHeadRefOidDrift`, `TestHeadRefOidDriftDuringDiffFetch`), base fetch (`TestBaseCommitFetch`, `TestBaseFetchRunsNoGitHooks`), audit handling (`TestAuditHitReportedNotFatal`, `TestAuditFindingKeepsEachMatchOnOneLine`, `TestAuditPathsRecordedInProvenance`), prechecks (`TestChangedFilesPrecheck`, `TestFileListIntegrity`), stacked base (`TestStackedBaseGate`) | split pending | proposed (not filed) |
| `claude/.claude/scripts/tests/test_plan_push_preflight.py` | 1787 | 1290 | 2 | Tests for the plan-push preflight: URL checks, gh host rule, allowlists and the read-only git argv guard | Test-class families by check: URL parsing and checks (`TestRawUrlCheck`, `TestParsePushUrl`, `TestRemoteUrlVariants`), host rule and allowlists (`TestGhHostRule`, `TestAllowlists`), argv guard (`TestReadOnlyGitArgvGuard`), output (`TestOutputSplitting`, `TestSanitizerAndLine`) | split pending | proposed (not filed) |
| `claude/.claude/scripts/tests/test_resume_context_same_session.py` | 1664 | 1288 | 2 | Tests for same-session resume-context handling: no-argument and explicit-argument runs, verdicts, consume and round trip | Test-class families by invocation shape: no argument (`TestNoArgument`), explicit argument (`TestExplicitArgument`), verdicts and targets (`TestVerdictsAndTargets`), pending set and consume (`TestPendingSet`, `TestConsume`), round trip and contracts (`TestRoundTrip`, `TestSkillContract`, `TestHookMatcherSemantics`) | split pending | proposed (not filed) |
| `claude/.claude/scripts/tests/test_review_pr_acquire.py` | 1662 | 1360 | 5 | Tests for the review-pr acquire step: gh failures, self-authored PRs, status-check rollup, list reconciliation and base stack | Test-class families by acquired datum: failure handling (`TestUsageErrors`, `TestGhFailureAborts`, `TestGhFailureNeverBypassesTheScrub`), status-check rollup (`TestStatusCheckRollupPassThrough`, `TestStatusCheckRollupUnavailable`, `TestStatusCheckRollupTokenContainment`), list reconciliation (`TestFilesReconciliation`, `TestCommitsReconciliation`), base stack (`TestBaseStack`) | split pending | proposed (not filed) |
| `claude/.claude/scripts/tests/test_resume_context.py` | 1621 | 1184 | 6 | Tests for resume-context.sh: launch mode, cwd and model flags, consume-only mode and legacy config fallback | Test-class families by flag and mode: launch mode (`TestLaunchMode`), flags (`TestCwdFlag`, `TestModelFlag`), consume-only mode and index (`TestConsumeOnlyMode`, `TestConsumedIndex`), config fallback (`TestLegacyConfigDirFallback`), skip behavior (`TestSkipIfBytesExceedPathMax`, `TestSkipCallSiteWiring`) | split pending | proposed (not filed) |
| `claude/.claude/scripts/transcript_analysis/review_rounds.py` | 1588 | 911 | 17 | Review-round cost and count computation with a pooled cross-account share block | Round-window detection (`detect_round_windows`), dispatch pricing (`compute_review_round_costs`, `_price_dispatch`), pooled block (`_render_pooled_block`, `_pooled_shares`, `_bootstrap_share_intervals`), subcommand entry (`cmd_review_round_cost`) | split pending | proposed (not filed) |
| `claude/.claude/scripts/tests/test_transcript_cli_bootstrap.py` | 1440 | 956 | 25 | Subprocess smoke tests that each transcript-analysis subcommand starts and finds a seeded session | Function groups per subcommand family as plain test functions: top-level help (`test_transcript_analysis_help_exits_zero`), cost and pr-cost (`test_transcript_analysis_pr_cost_help_exits_zero`), review trace (`test_transcript_analysis_review_trace_help_exits_zero`), period cost (`test_transcript_analysis_period_cost_help_exits_zero`), routing guard (`test_transcript_analysis_routing_guard_help_exits_zero`) | split pending | proposed (not filed) |
| `claude/.claude/scripts/select-tests.py` | 1430 | 623 | 66 | Selects the pytest domains a change implicates and runs them | Rule tables (`DOMAIN_RULES`, `CROSS_DOMAIN_EXCEPTIONS`, `GLOBAL_TRIGGER_PATHS`) apart from git plumbing (`compute_changed_paths`), worker sizing (`compute_worker_count`, `pytest_subprocess_env`) and selection logging (`record_selection`) | split pending | proposed (not filed) |
| `claude/.claude/scripts/tests/test_install_dev.py` | 1410 | 889 | 3 | Tests for install-dev.sh: venv health, pinned interpreter, opt-in gate and the pyright-lsp plugin step | Test-class families by install step: venv creation and recovery (`TestUnhealthyVenvIsRecreated`, `TestIdempotency`, `TestEnsurepipMissingDebian`), pinned interpreter (`TestPinnedInterpreter`, `TestPinnedInterpreterFailsToStart`), opt-in gate (`TestPrivateProjectsGate`), plugin step (`TestPyrightLspStep`, `TestConfirmPluginInstall`) | split pending | proposed (not filed) |
| `claude/.claude/scripts/tests/test_plan_shape.py` | 1393 | 1121 | 5 | Tests for the plan-shape checker: corpus cases, fence scan, path and file-type handling and the skill contract | Test-class families by checker stage: corpus (`TestCorpus`), fence scan (`TestFenceScan`), path and file type (`TestPath`, `TestFileType`, `TestLargeFile`), prepare and errors (`TestPrepare`, `TestInternalError`), contracts (`TestContract`, `TestSkillContract`) | split pending | proposed (not filed) |
| `claude/.claude/scripts/_review-ledger-lib.sh` | 1359 | ≥1041 | 7 | Validation, site hash, row reader and render helpers sourced by review-ledger.sh | Flag validation (`_review_ledger_validate_flags`, `_review_ledger_validate_lanes`), site hash and location (`_review_ledger_site_hash`, `_review_ledger_normalize_location`), tree pinning (`_review_ledger_reviewed_tree`, `_review_ledger_pin_tree`), carry checks (`_review_ledger_carry_line`), output and render (`_review_ledger_write_out`, `_review_ledger_render`) | split pending | proposed (not filed) |
| `claude/.claude/scripts/transcript_analysis/cost.py` | 1329 | 841 | 15 | Transcript-analysis cost and cost-trend subcommands | Cost report (`cmd_cost`, `_cost_report`) with its table printers (`_print_scan_coverage_table`, `_print_token_class_table`, `_print_excluded_spend_banner`) apart from the trend subcommand (`cmd_cost_trend`, `compute_cost_trend_data`, `_cost_trend_report`) and branch totals (`_compute_pr_cost_branch_totals`, `_compute_workstream_dollars`) | split pending | proposed (not filed) |
| `claude/.claude/scripts/tests/test_review_pr_post.py` | 1328 | 1017 | 4 | Tests for the review-pr post step: marker and body-hash checks, identity cross-check, authorship check and mode gating | Test-class families by check: usage and marker (`TestUsageErrors`, `TestMissingCompletionMarker`, `TestCompletionMarkerSelfConsuming`), hash and identity (`TestBodyHashMismatch`, `TestPrIdentityCrossCheck`), authorship (`TestRequestChangesAuthorshipCheck`), mode and target (`TestModeGating`, `TestTargetArgument`), allowlist (`TestAllowEntryMatcher`, `TestSettingsAllowlistKeepsReviewPrFetchAndPostBehindThePrompt`) | split pending | proposed (not filed) |
| `claude/.claude/scripts/cleanup-merged-branches.sh` | 1310 | ≥740 | 9 | Discovers merged branches by PR and ancestry signals and deletes them, for one repo or a sweep of configured roots | Branch classification (`classify_branch`, `merged_row_by_tip_ancestry`, `print_skip_reason_lines`, `checked_out_skip_line`) apart from root discovery (`discover_repo_roots`, `read_configured_roots`) and the per-repo body (`run_repo_cleanup`) | split pending | proposed (not filed) |
| `claude/.claude/scripts/transcript_analysis/pr_cost.py` | 1258 | 876 | 6 | Pr-cost subcommand building per-pull-request cost ledger rows | Model-breakdown cell (`_build_model_breakdown_cell`, `_check_model_breakdown_cell`), review-rounds cell (`_build_review_rounds_cell`, `_check_review_rounds_cell`), branch-to-PR resolution (`_resolve_branch_pr`, `_gh_pr_view_enrichment`) apart from the report body (`_pr_cost_report`) | split pending | proposed (not filed) |
| `claude/.claude/scripts/tests/test_transcript_denials.py` | 1250 | 853 | 10 | Tests for transcript-analysis denial classification and the hook-label enumeration against real hooks | Test-class families: label helpers (`TestDropDenialCommandFlagValues`, `TestIsNongateFrictionKind`, `TestFrictionKindLabel`), hook-label enumeration (`TestDenialHookLabelEnumeration`, `TestDenialHookLabelEnumerationRealHooks`), gate conformance (`TestDenyGateLabelConformance`), cause classification (`TestDenialCauseKind`) | split pending | proposed (not filed) |
| `claude/.claude/scripts/marker.sh` | 1187 | ≥650 | 25 | Writes, removes and reports review markers for workflow skills through one subcommand dispatcher | Status reporting (`_status_report_completion_marker`, `_status_report_review_pr_marker`, `_status_report_active_bypass`, `_status_reconciliation_flag`), marker freshness (`_marker_fresh_age`, `_marker_max_age_or_default`), and the write subcommand arm apart from activate, deactivate and clear-stale | split pending | proposed (not filed) |
| `claude/.claude/scripts/transcript_analysis/scope.py` | 1161 | 494 | 14 | Transcript scope resolution: project slugs, scan roots and branch attribution | Scan-root and project scope (`resolve_scan_roots`, `_resolve_project_scope`, `_resolve_cost_roots`), scoped session iteration (`_iter_scoped_sessions`, `_iter_project_dir_sessions`), branch attribution (`_session_branch_context`, `_attributed_branch`, `_pre_branch_run`) | split pending | proposed (not filed) |
| `claude/.claude/scripts/tests/test_stow_migration_lib.py` | 1085 | 731 | 0 | Tests for the stow migration library: backup and unadopt run selection, nested adoption repair and untracked package entries | Test-class families by migration step: backup selection (`TestNewestBackupSelection`, `TestNewestBackupNameBoundary`), unadopt run selection (`TestNewestUnadoptRunNameBoundary`, `TestNewestUnadoptRunSameNameSelection`), repair and entry (`TestRepairNestedAdoption`, `TestStowUnadoptEntry`, `TestStowUntrackedPackageEntries`), escape handling (`TestGlobEscapeAdversarial`, `TestRegexEscapeAdversarial`) | split pending | proposed (not filed) |
| `claude/.claude/scripts/tests/test_migrate_legacy_config.py` | 1050 | 609 | 4 | Tests for migrate-legacy-config.sh: migration matrix, rerun safety, delete confirmation and enforcement-critical key import | Test-class families by migration concern: migration matrix (`TestMigrationMatrixStateFileAbsent`, `TestMigrationMatrixStateFilePresentKeyRowPresent`), rerun safety (`TestHandEditSurvivesRerun`, `TestDoubleInvocationIdempotency`), delete prompt (`TestDeleteConfirmationPrompt`), enforcement-critical import (`TestTTYInvarianceOfEnforcementCriticalImport`, `TestEnforcementCriticalDirectionAwareImport`), failure isolation and validation (`TestPerKeyFailureIsolation`, `TestSchemaRowValidation`) | split pending | proposed (not filed) |
| `claude/.claude/scripts/tests/test_cleanup_idle_open_pr_worktrees.py` | 1011 | 677 | 3 | Tests for cleanup-idle-open-pr-worktrees.sh: idle-hour candidate selection, safety skips, gh failure modes and the branch-records archive | Test-class families by behavior: idle boundary and candidate selection (`TestIdleHoursBoundary`, `TestStaleOpenPrRemoved`, `TestMultiRowBulkResponse`), safety skips (`TestWorktreeInUseSkipped`, `TestSelfWorktreeNeverRemoved`, `TestDirtyCandidateMidBatchDoesNotAbort`), gh failure and argument handling (`TestGhFailureModes`, `TestGhMissing`, `TestInvalidArgs`), archive (`TestBranchRecordsArchive`) | split pending | proposed (not filed) |
| `claude/.claude/scripts/tests/test_transcript_pr_cost_gh.py` | 1005 | 737 | 4 | Tests for the pr-cost subcommand's gh calls: enrichment degradation, repo and host pinning, ledger isolation, redaction and all-accounts mode | Test-class families: enrichment failures (`TestPrCostPerPrEnrichmentRateLimitDegradesRowNotRun`), pinning and host (`TestPrCostGhCallsPinnedAfterRepoIdentityResolution`, `TestPrCostGheHostQualifiesGhRepoCalls`), ledger isolation (`TestPrCostCrossRepoLedgerIsolation`, `TestPrCostCrossHostLedgerIsolation`), refusal redaction (`TestPrCostMultiRootRefusalRedaction`, `TestPrCostReadModeRedaction`), all accounts and end to end (`TestPrCostAllAccounts`, `TestCmdPrCostEndToEndViaRealArgparse`) | split pending | proposed (not filed) |

### `claude-skills/`

| Path | Physical | Non-comment | 90-day commits | Subject | Seams | Verdict | Tracking or reason |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `claude-skills/skills/tests/test_skills.py` | 3513 | 2134 | 100 | Contract tests over skill files: triggers, frontmatter, citations, handoff and resume wording, path templating | Test-class families `TestSpecialistSkillTriggerContracts`, `TestFrontmatterReaders`, `TestHandoffWarrantCheckCanonicalSection`, `TestResumeContextSkill`, `TestPerAccountStatePathContract` and the citation tests led by `test_skill_citations_resolve_to_real_headings` | split pending | #1118 |
| `claude-skills/skills/tests/test_trigger_detector.py` | 1400 | 1020 | 3 | Tests for the skill trigger detector: frontmatter parsing, dispatch detection, classification scoring and blocked-prompt handling | Test-class families `TestParseSkillFrontmatter`, `TestExtractGoverningRule`, `TestDetectDispatch`, `TestBuildDispatchCommand` and `TestRaiseIfBlockedPromptStream` | split pending | proposed (not filed) |

### `evals/`

| Path | Physical | Non-comment | 90-day commits | Subject | Seams | Verdict | Tracking or reason |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `evals/test_review_bench_cli.py` | 7124 | 5553 | 7 | Tests for the review bench command line: freeze, run, smoke, analyze, mining, spot-check and confirm subcommands | Freeze (`TestCmdFreezeManifestFields`, `TestCmdFreezeGuards`), run and smoke (`TestRunAndSmokeCampaignSelection`, `TestRunAndSmokeFlagsReachRunCampaign`), analyze (`TestCmdAnalyzeBaselineReport`, `TestCmdAnalyzePerGateFigures`, `TestAnalyzeLaterArmVerdicts`), mining (`TestCmdMineSzz`, `TestCmdMinePrComments`), judge (`TestCmdJudgeOutOfSessionReporting`, `TestJudgeVerifiesFrozenConditionsBeforeDispatch`), spot check (`TestCmdSpotCheckExport`, `TestCmdSpotCheckImport`), confirm (`TestConfirmRemembersDeclinedCandidates`, `TestConfirmAnswersFile`, `TestConfirmIdsFile`) | split pending | proposed (not filed) |
| `evals/test_review_bench_mining.py` | 6217 | 4788 | 12 | Tests for the review bench defect mining: candidate schema, SZZ mining, review-round mining, PR-comment mining and the confirm CLI | Candidate schema and description policy (`TestCandidateAndConfirmedDefectSchema`, `TestDescriptionPolicy`), SZZ miner (`TestMineSzz`, `TestMineSzzSkipsPythonDocstringLines`), review-round miner (`TestResolveDefectCommits`, `TestMineReviewRoundsCandidates`), PR-comment miner (`TestMinePrComments`, `TestClassifyReply`), confirm CLI (`TestConfirmCli`) | split pending | proposed (not filed) |
| `evals/test_measure_main_session_rule_adherence.py` | 5600 | 4229 | 1 | Tests for the main-session rule adherence measurement: statistics, scorers, arm construction, run log, stage loop and live run wiring | Statistics (`TestNewcombeDifferenceInterval`, `TestSizing`, `TestOperatingCharacteristics`), scorers and attempt classification (`TestScorers`, `TestClassifyAttempt`), arms and project files (`TestArms`, `TestProjectFiles`), run log and resume (`TestRunLog`, `TestResume`), stage loop under a scripted launcher (`TestCalibrationStages`, `TestCostAccounting`, `TestFaultsAndPauses`), live run (`TestLiveRun`), report (`TestReport`, `TestCli`) | split pending | proposed (not filed) |
| `evals/test_review_bench_runner.py` | 4497 | 3486 | 6 | Tests for the review bench runner: run validity checks, transcript extraction, environment record, campaign orchestration and run store | Run records (`TestRunRecordJsonlRoundTrip`, `TestCombineAttemptRecords`), run validity (`TestJudgeRunValidity`, `TestLeakAndOutOfSessionChecks`, `TestDefaultLiveCheckoutRoots`), transcript extraction (`TestExtractResultUsage`, `TestExtractReadLikeCallTargets`), environment record (`TestReadEnvironmentRecord`, `TestEnvironmentMismatchHaltsBlock`), campaign orchestration (`TestRunCampaignSystemicFailure`, `TestRunCampaignResume`), run store (`TestRunStoreResume`, `TestRunStoreLock`) | split pending | proposed (not filed) |
| `evals/measure_main_session_rule_adherence.py` | 3132 | 2446 | 1 | Measures main-session rule adherence across hook and memory arms with sequential-sampling statistics | Statistics (`wilson_interval`, `newcombe_difference_interval`), observables and scorers (`parse_stream`, `score_observable`), arm and project construction (`freeze_hooks_dir`, `write_project_files`), run log (`RunLog`), harness (`Harness`), report (`render_report`) | split pending | proposed (not filed) |
| `evals/test_review_bench_adjudicate.py` | 2784 | 2242 | 6 | Tests for the review bench adjudication module: judge input framing, answer parsers, spot-check sampling and judge run lifecycle | Judge input (`TestBlindOrdering`, `TestRecallJudgeInputLimitsTheFixDiffToTheDefectsPath`, `TestDescriptionAndPathsAreFramedAsData`), answer parsers (`TestRecallAnswerParser`, `TestPrecisionAnswerParser`), spot check (`TestPrecisionSpotCheckCandidates`, `TestSpotCheckSampling`), judge run lifecycle (`TestJudgeRunRetry`, `TestExecuteJudgeRunValidityWiring`, `TestCmdJudgeResume`) | split pending | proposed (not filed) |
| `evals/run_review_bench.py` | 2351 | 1880 | 6 | Review-bench command-line entry with its mining, confirm, run, judge, analyze and freeze subcommands | Subcommands: mining (`cmd_mine_szz`, `cmd_mine_pr_comments`), confirm (`cmd_confirm`), judge (`cmd_judge`), analyze (`cmd_analyze`), freeze (`cmd_freeze`), spot-check (`cmd_spot_check_export`) | split pending | proposed (not filed) |
| `evals/test_measure_explore_recall.py` | 2254 | 1544 | 3 | Tests for the Explore recall measurement: fixture schema, final-answer reading, scoring, aggregation, adoption gate and dispatch mechanics | Fixture schema (`TestParseFixtureCases`, `TestLoadFixture`), final-answer reader (`TestScanSubagentFinalAnswerText`, `TestReadFinalAnswer`), scoring and aggregation (`TestScoreSample`, `TestAggregateModelResults`), gate (`TestEvaluateGate`, `TestWilsonLowerBound`), dispatch mechanics (`TestExecuteOneSampleAttempt`, `TestRunBatch`, `TestRunRecallGate`) | split pending | proposed (not filed) |
| `evals/test_comment_ab_pool_count.py` | 2079 | 1775 | 1 | Tests for the comment A/B pool counter: comment classifiers, ceiling computation, git reading, GitHub fetching and the CLI | Classifier families (`TestPythonRegions`, `TestShellSourceHoldsComment`, `TestJudgeWhyComment`), ceiling computation (`TestComputeCeiling`, `TestReport`), git reading (`TestGitReading`), GitHub layer (`TestGitHubCache`, `TestPullRequestFetcher`, `TestGhTransport`), entry point (`TestMain`) | split pending | proposed (not filed) |
| `evals/review_bench/runner.py` | 2069 | 1315 | 5 | Review-bench run records, transcript parsing, validity checks, run store and campaign orchestration | Run records (`RunRecord`, `read_run_records`), transcript parsing (`extract_read_like_calls`, `extract_result_usage`), validity (`evaluate_run_validity`), run store (`RunStore`), campaign (`run_campaign`), environment record (`read_environment_record`) | split pending | proposed (not filed); closure-windows |
| `evals/test_replay_bench_runner.py` | 1732 | 1321 | 1 | Tests for the replay bench per-attempt runner: run validity, instrument reasons, worker failures and the command line | Run record and validity (`TestAValidRun`, `TestForgedLog`, `TestInstrumentReasons`), retry behavior (`TestRetryThenMissing`), worker failures (`TestWorkerFailures`, `TestAttemptsRunningTogether`), command line (`TestTheCommandLine`) | split pending | proposed (not filed) |
| `evals/test_replay_bench_tasks.py` | 1731 | 1254 | 2 | Tests for the replay bench task miner: commit selection, extraction parity, source repos, task files and fixture building | Selection (`TestQualifyingCommit`, `TestSkippedCommits`, `TestThresholdsAreInclusive`), extraction parity (`TestExtractionParity`), source repos and walking (`TestSourceRepos`, `TestTrustedAuthors`, `TestWalk`), task files and prompt (`TestTaskFiles`, `TestInnerPrompt`), prune and fixture (`TestPruneOnAPlainDirectory`, `TestBuildTaskFixture`) | split pending | proposed (not filed) |
| `evals/test_measure_subagent_model_resolution.py` | 1672 | 1170 | 8 | Tests for the subagent model resolution measurement: dispatch parsing, matrix execution, frontmatter parsing and reporting | Dispatch parsing and sidecar wait (`TestParseSubagentDispatches`, `TestWaitForSubagentSidecar`), process execution (`TestRunClaudeToCompletion`, `TestExecuteMatrixCell`), matrix control (`TestRunMatrix`, `TestShouldStopMatrixAfter`), frontmatter (`TestAgentFrontmatterParsing`), reporting (`TestPrintRunResult`, `TestResultToDict`) | split pending | proposed (not filed) |
| `evals/test_review_bench_fixtures.py` | 1624 | 1265 | 6 | Tests for the review bench fixture and arm builders: two-commit repos, bench artifacts, archive extraction hardening and arm agent rendering | Fixture repos and artifacts (`TestBuildTwoCommitRepo`, `TestWriteBenchArtifacts`, `TestBuildSingleCommitRepo`), archive extraction (`TestExtractArchiveRefusesAGitPathComponent`, `TestExtractArchiveRefusesALinkIntoGit`, `TestExtractArchiveRefusesWritesThroughHarnessAndConfigPaths`), arm rendering (`TestRenderArmAgent`, `TestSnapshotArmAndInstallArm`) | split pending | proposed (not filed) |
| `evals/test_review_bench_analysis.py` | 1597 | 1253 | 2 | Tests for the review bench analysis module: statistics, verdicts, harness closure, freeze checks and report columns | Statistics (`TestPercentile`, `TestRecallCountsAndDropRule`, `TestFixtureClusterBootstrap`), verdicts (`TestArmRecallAndSensitivityVerdict`, `TestCertifyLaterArm`), harness closure (`TestHarnessClosure`, `TestHarnessClosureOverASyntheticTree`), freeze checks (`TestManifestAndFreezeChecks`, `TestCheckAgainstFrozenConditions`), report columns (`TestSecondaryReportColumns`, `TestBaselineReportFreezeIdentity`) | split pending | proposed (not filed) |
| `evals/run_skill_evals.py` | 1524 | 964 | 3 | Skill evaluation runner for description-fidelity, behavioral-dispatch and disposition-fidelity measurements | Measurement families: description fidelity (`run_description_fidelity_sample`), behavioral dispatch (`run_dispatch_sample`), disposition fidelity (`run_disposition_case`), frontmatter helpers (`parse_skill_frontmatter`) | split pending | proposed (not filed); closure-windows |
| `evals/measure_explore_recall.py` | 1406 | 906 | 1 | Measures Explore-agent recall against a fixture and applies an adoption gate | Fixture schema (`parse_fixture_cases`, `load_fixture`), scoring (`score_sample`, `wilson_lower_bound`), gate (`evaluate_gate`), dispatch mechanics (`execute_one_sample_attempt`, `run_batch`), reporting (`print_gate_report`) | split pending | proposed (not filed) |
| `evals/test_replay_bench_campaign.py` | 1263 | 955 | 1 | Tests for the replay bench campaign orchestrator: completed runs, resume against the pinned manifest, spend guards and environment drift | Completed and per-arm campaign behavior (`TestACompletedCampaign`, `TestThePerArmStop`, `TestParallelWorkers`), manifest and smoke gate (`TestTheManifestAndTheSmokeGate`, `TestAResumeAgainstThePinnedManifest`), environment drift (`TestEnvironmentDrift`), attempts and resume (`TestResumeAndAttempts`), spend (`TestSpendGuards`, `TestTheSpendFile`) | split pending | proposed (not filed) |
| `evals/measure_subagent_model_resolution.py` | 1082 | 641 | 6 | Measures which model a dispatched subagent resolves to across a run matrix | Agent frontmatter (`agent_frontmatter_model`, `agent_frontmatter_tools`), transcript parsing (`parse_subagent_dispatches`), subprocess execution (`execute_matrix_cell`), reporting (`result_to_dict`, `print_run_result`) | split pending | proposed (not filed); closure-windows |
| `evals/review_bench/analysis.py` | 1063 | 674 | 2 | Review-bench statistics, verdicts, harness closure and freeze checks | Verdicts (`recall_noninferiority_verdict`, `certify_later_arm`), bootstrap (`bootstrap_interval`), harness closure (`compute_harness_closure`), freeze checks (`check_freeze_preconditions`), secondary columns (`read_token_stats`) | split pending | proposed (not filed); closure-windows |

### Any other path

| Path | Physical | Non-comment | 90-day commits | Subject | Seams | Verdict | Tracking or reason |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `install.sh` | 1080 | ≥610 | 22 | First-time setup script: stow, plugin registration, machine-level opt-ins and PATH checks | Sentinel reporting (`report_sentinel_inventory`, `_report_repo_sentinel`), opt-in prompts (`configure_machine_level_opt_ins`, `_prompt_sentinel_opt_in`), stale-copy cleanup (`_report_and_clean_stale_migration_copy`), PATH setup (`ensure_local_bin_on_path`), contributor prompt (`prompt_contributor_intent`) | split pending | proposed (not filed) |

## Near-limit files

These files sit at 900 to 1,000 physical lines, so one more change may cross the limit. A file at exactly 1,000 lines passes.

| Path | Physical |
| --- | --- |
| `evals/comment_ab_pool_count.py` | 1000 |
| `claude/.claude/scripts/transcript_analysis/author_outcome.py` | 993 |
| `claude/.claude/hooks/deny-private-project-refs.sh` | 987 |
| `claude/.claude/hooks/tests/_cursor_payloads.py` | 969 |
| `claude/.claude/hooks/tests/test_lib_reviewer_round_state.py` | 969 |
| `claude/.claude/scripts/tests/test_transcript_pr_cost_export.py` | 967 |
| `evals/test_replay_bench_checks_transcript.py` | 959 |
| `plugins/skill-management/hooks/_lib.sh` | 959 |
| `claude/.claude/scripts/tests/_move_fidelity.py` | 957 |
| `evals/review_bench/adjudicate.py` | 950 |
| `claude/.claude/hooks/tests/test_require_stow_reminder.py` | 946 |
| `claude/.claude/scripts/tests/test_transcript_move_fidelity.py` | 941 |
| `claude/.claude/scripts/tests/test_transcript_subagents.py` | 939 |
| `claude/.claude/scripts/transcript_analysis/review_trace.py` | 938 |
| `claude/.claude/scripts/tests/test_transcript_cache_rebuild_attribution.py` | 931 |
| `claude/.claude/scripts/transcript_analysis/cache_rebuild.py` | 925 |
| `claude/.claude/hooks/tests/test_log_reviewer_round.py` | 920 |
| `claude/.claude/scripts/tests/test_transcript_subagent_mix.py` | 910 |
| `claude/.claude/hooks/tests/test_session_marker_dashboard.py` | 906 |
| `claude/.claude/scripts/tests/test_transcript_handoff_signal_response.py` | 904 |
| `evals/replay_bench/tasks.py` | 904 |
| `claude/.claude/scripts/tests/test_transcript_analysis_branch_attribution.py` | 902 |

## Appendix

The measuring program, saved as `measure.py` and run as `python3 measure.py ab85a627` from a checkout of the repository. Python 3.12 changed how `tokenize` splits f-strings (PEP 701), so another interpreter may print different non-comment counts.

~~~python
"""Print code-file size measurements for one commit as tab-separated records.

Usage: measure.py <ref>. Every count comes from blobs and history at <ref>,
never from the working tree. Records, each led by its type:
  meta      name, value
  figure    name, value
  offender  path, physical lines, non-comment lines, 90-day commits
  near      path, physical lines
"""
import ast
import io
import subprocess
import sys
import tokenize
from datetime import datetime, timedelta, timezone

LIMIT = 1000
NEAR_FLOOR = 900
THRESHOLDS = (1000, 1500, 2000)
WINDOW_DAYS = 90
BLOB_MODES = {b"100644", b"100755"}
SHELL_WHITESPACE = b" \t\v\f\r"
NON_CODE_TOKENS = {
    tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE,
    tokenize.INDENT, tokenize.DEDENT, tokenize.ENDMARKER,
}


def git(*args, stdin=None):
    return subprocess.run(
        ["git", "--literal-pathspecs", *args],
        input=stdin, capture_output=True, check=True,
    ).stdout


def git_text(*args):
    return git(*args).decode("utf-8", "replace")


def list_code_blobs(commit):
    blobs = {}
    for entry in git("ls-tree", "-r", "-z", commit).split(b"\0"):
        if not entry:
            continue
        info, path_bytes = entry.split(b"\t", 1)
        info_fields = info.split(b" ")
        mode, blob_id = info_fields[0], info_fields[2]
        path = path_bytes.decode("utf-8")
        if mode in BLOB_MODES and path.endswith((".py", ".sh")):
            blobs[path] = blob_id
    return blobs


def read_blobs(blob_ids):
    output = git("cat-file", "--batch", stdin=b"\n".join(blob_ids) + b"\n")
    contents, position = {}, 0
    for blob_id in blob_ids:
        header_end = output.index(b"\n", position)
        size = int(output[position:header_end].split(b" ")[2])
        contents[blob_id] = output[header_end + 1:header_end + 1 + size]
        position = header_end + 1 + size + 1
    return contents


def character_position(lines, line_number, byte_offset):
    """Convert an ast byte column to the character column tokenize reports."""
    prefix = lines[line_number - 1].encode("utf-8")[:byte_offset]
    return (line_number, len(prefix.decode("utf-8", "ignore")))


def docstring_spans(tree, lines):
    spans = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        first = node.body[0] if node.body else None
        if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            spans.append((
                character_position(lines, first.lineno, first.col_offset),
                character_position(lines, first.end_lineno, first.end_col_offset),
            ))
    return spans


def python_noncomment(source):
    """Lines holding code, counting string bodies; None when the file does not parse."""
    try:
        text = source.decode("utf-8-sig")
        lines = text.split("\n")
        spans = docstring_spans(ast.parse(text), lines)
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (UnicodeDecodeError, SyntaxError, tokenize.TokenError, ValueError, RecursionError):
        return None
    counted = set()
    for token in tokens:
        if token.type in NON_CODE_TOKENS:
            continue
        if token.type == tokenize.STRING and any(
                start <= token.start and token.end <= end for start, end in spans):
            continue
        first_line, last_line = token.start[0], token.end[0]
        if first_line == last_line:
            counted.add(first_line)
        else:
            counted.update(n for n in range(first_line, last_line + 1) if lines[n - 1].strip())
    return len(counted)


def shell_noncomment_lower_bound(source):
    """Non-blank lines not led by `#`. Heredoc and quoted `#` lines make this a lower bound."""
    stripped_lines = (line.lstrip(SHELL_WHITESPACE) for line in source.split(b"\n"))
    return sum(1 for line in stripped_lines if line and not line.startswith(b"#"))


def window_history(commit, path, window_start):
    """Return (window commits, commits whose added and deleted counts differ) for one file."""
    log = git_text(
        "log", commit, "--no-merges", "--follow", "--since=" + window_start,
        "--numstat", "--format=%x01%H", "--", path,
    )
    commits, edits, current = [], set(), None
    for line in log.split("\n"):
        if line.startswith("\x01"):
            current = line[1:]
            commits.append(current)
        elif "\t" in line:
            added, deleted = line.split("\t")[:2]
            if added != deleted:
                edits.add(current)
    return commits, edits


def is_named_test(path):
    name = path.rsplit("/", 1)[-1]
    return (name.startswith("test_") and name.endswith(".py")) or name == "conftest.py"


def is_test_code(path):
    return "tests" in path.split("/")[:-1] or is_named_test(path)


def format_noncomment(offender):
    if offender["noncomment"] is None:
        return "unparsed"
    return ("≥" if offender["path"].endswith(".sh") else "") + str(offender["noncomment"])


def threshold_figures(offenders):
    figures = []
    groups = {"all": offenders,
              "test": [o for o in offenders if is_test_code(o["path"])],
              "production": [o for o in offenders if not is_test_code(o["path"])]}
    for threshold in THRESHOLDS:
        for group, members in groups.items():
            figures.append((f"physical_over_{threshold}_{group}",
                            sum(o["physical"] > threshold for o in members)))
        for group, members in groups.items():
            figures.append((f"noncomment_over_{threshold}_{group}",
                            sum(o["noncomment"] is not None and o["noncomment"] > threshold
                                for o in members)))
        undetermined = sorted(o["path"] for o in offenders
                              if o["path"].endswith(".sh") and o["noncomment"] <= threshold < o["physical"])
        figures.append((f"shell_undetermined_over_{threshold}", ",".join(undetermined) or "none"))
    return figures


def main(ref):
    if ref.startswith("-"):
        sys.exit("ref must not start with '-'")
    commit = git_text("rev-parse", "--verify", ref + "^{commit}").strip()
    committer_epoch = int(git_text("log", "-1", "--format=%ct", commit))
    window_start = (datetime.fromtimestamp(committer_epoch, timezone.utc)
                    - timedelta(days=WINDOW_DAYS)).strftime("%Y-%m-%dT%H:%M:%S+0000")

    blobs = list_code_blobs(commit)
    contents = read_blobs(sorted(set(blobs.values())))
    physical = {path: contents[blob_id].count(b"\n") for path, blob_id in blobs.items()}

    offenders = []
    for path in sorted(p for p, n in physical.items() if n > LIMIT):
        source = contents[blobs[path]]
        noncomment = (python_noncomment(source) if path.endswith(".py")
                      else shell_noncomment_lower_bound(source))
        window_commits, edits = window_history(commit, path, window_start)
        offenders.append({"path": path, "physical": physical[path], "noncomment": noncomment,
                          "commits": len(window_commits), "edits": edits})
    near = [(p, n) for p, n in sorted(physical.items()) if NEAR_FLOOR <= n <= LIMIT]
    listed = [o["path"] for o in offenders] + [p for p, _ in near]
    if any(c in p for p in listed for c in "\t\n,"):
        sys.exit("a listed path holds a tab, newline or comma")

    ceiling_edit_commits = set().union(*(o["edits"] for o in offenders)) if offenders else set()
    window_total = int(git_text("rev-list", "--count", "--no-merges", "--since=" + window_start, commit))
    records = [
        ("meta", "pin", git_text("rev-parse", "--short=8", commit).strip()),
        ("meta", "pin_committer_date", git_text("log", "-1", "--format=%cs", commit).strip()),
        ("meta", "window_start", window_start),
        ("meta", "python_version", " ".join(sys.version.split())),
        ("meta", "git_version", git_text("--version").strip()),
        ("figure", "tracked_code_files", len(physical)),
        ("figure", "offenders_all", len(offenders)),
        ("figure", "offenders_test", sum(is_test_code(o["path"]) for o in offenders)),
        ("figure", "offenders_production", sum(not is_test_code(o["path"]) for o in offenders)),
        ("figure", "offenders_named_test", sum(is_named_test(o["path"]) for o in offenders)),
        ("figure", "offenders_not_named_test", sum(not is_named_test(o["path"]) for o in offenders)),
        ("figure", "offenders_shell", sum(o["path"].endswith(".sh") for o in offenders)),
        *(("figure", name, value) for name, value in threshold_figures(offenders)),
        ("figure", "unparsed", sum(o["noncomment"] is None for o in offenders)),
        ("figure", "near_limit_files", len(near)),
        ("figure", "near_limit_at_exactly_limit", sum(n == LIMIT for _, n in near)),
        ("figure", "ceiling_edit_load", len(ceiling_edit_commits)),
        ("figure", "window_non_merge_commits", window_total),
        *(("offender", o["path"], o["physical"], format_noncomment(o), o["commits"]) for o in offenders),
        *(("near", path, lines) for path, lines in near),
    ]
    lines = ("\t".join(str(field) for field in record) + "\n" for record in records)
    sys.stdout.buffer.write("".join(lines).encode("utf-8"))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: measure.py <ref>")
    main(sys.argv[1])
~~~

Cross-check commands, which compare the program's output with independent listings. They read the working tree and index, so run them with the checkout at the pin.
- `git ls-files -z -- '*.py' '*.sh' | xargs -0 wc -l | awk '$2 != "total" && 1000 < $1 {print $2 "\t" $1}' | LC_ALL=C sort` against the program's `offender` records, reduced to path and physical count by `awk -F'\t' '$1 == "offender" {print $2 "\t" $3}'` and the same sort.
- The same listing with the predicate `899 < $1 && $1 < 1001` against the `near` records.
- `git log ab85a627 --no-merges --follow --since=<window_start> --format=%h -- claude-skills/skills/tests/test_skills.py | wc -l` against that file's `offender` record, with `<window_start>` taken from the `meta` records.
- `git rev-list --count --no-merges --since=<window_start> ab85a627` against the `window_non_merge_commits` figure.
- `python3 -B -c "import sys; sys.path.insert(0, 'evals'); from review_bench.analysis import compute_harness_closure; print(chr(10).join(sorted(compute_harness_closure())))"`, run from the repository root, for the closure list.
