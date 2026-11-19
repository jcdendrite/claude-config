# Detect a blocked prompt in the eval launchers

## Context

Goal: make every in-repo eval consumer that launches `claude -p` recognize a prompt blocked by a hook and stop or name the cause, instead of scoring the run as a model miss.

Ask: "Early PR plus one move PR (Recommended)" (the engineer's answer on how to split the Main session follow-up). Agreement to start this early PR first and independently: "Ok sounds good." and "Sounds good continue". The block this PR must recognize comes from the prompt gate that the follow-up move PR adds, planned in `.claude/plans/main-session-rules-hook-delivery.md` on branch `main-session-rules-hook-delivery`.

Why now: probe P3 showed a blocked `claude -p` run exits 0 and prints the block header, the reason and the original prompt on stdout, then ends its stream with a `success` result. The eval consumers read that as a run with no trigger or no dispatch, a plausible model miss. Once the move PR lands, any eval run on a session whose Main session delivery failed would score as a miss with no signal. This PR lands first and is independent of the adapter's Phase 4.

Evidence from exploration of `main` at `2319b09f`, outputs kept in the session:
- `evals/run_skill_evals.py` has no error or recognizer classes. It has seven `claude -p` command shapes at six call sites. Three shapes are stream-json, launched from two `Popen` sites and parsed by `detect_trigger_in_lines` and `detect_dispatch_in_lines`. The rest are plain-text `subprocess.run` calls. All discard stderr. The follow-up plan's cited line numbers and its count of seven are stale.
- `evals/measure_main_session_rule_adherence.py` already classifies a block (`blocked-prompt`) and never raises on one.
- Three more modules launch through their own command builders or through `msmr._run_claude_to_completion` (in `evals/measure_subagent_model_resolution.py`): the model-resolution harness, the explore-recall harness and `evals/review_bench/runner.py` with `adjudicate.py`. That launcher's `LaunchOutcome` records only an exit status and a stderr tail.
- There is no test file for `run_skill_evals`'s launch or detect functions under `evals/`. Its tests live in `claude-skills/skills/tests/test_trigger_detector.py`.
- The only block fixture, `evals/fixtures/main-session-rule-adherence/blocked-prompt.jsonl`, sits under a directory the planned harness-retirement PR deletes.

## Approach

`evals/run_skill_evals.py` gains one exception and a small set of recognizers that turn a blocked `claude -p` prompt into `BlockedPromptError`, which ends the eval run instead of letting it score. Stream-json output is recognized by the CLI's own `prompt_submit_outcome.kind` `blocked_by_hook` field, and plain-text output by the fixed prefix `main-session-rules: prompt blocked`. The two stream parsers raise it, which covers `run_skill_evals`' stream launches, the model-resolution harness and the explore-recall harness, and `run_skill_evals`' four plain-text calls raise it on stdout. review_bench instead records a blocked run missing with reason `blocked-prompt`, so its existing all-missing stop ends the campaign with exit 2 after its cleanup and names the hook's reason. The main session rule adherence harness does not change, and keeps its own classify-and-pause.

1. **Home: `evals/run_skill_evals.py`.** `evals/measure_subagent_model_resolution.py` (msmr) imports `run_skill_evals` at module top (row 6). A home beside msmr's launcher would make `run_skill_evals` import msmr, a cycle. `run_skill_evals` imports no evals module, and every launching consumer already reaches it (row 7). It gains:
   - `PROMPT_BLOCK_PREFIX = "main-session-rules: prompt blocked"`, commented `# Plain-text output carries no structured block field, so a prompt-blocking hook in this repo opens its reason with this line.`
   - `class BlockedPromptError(RuntimeError)`, docstring `A hook blocked the claude -p prompt, so the run produced nothing to score.` Its constructor takes one message string, so it pickles across `run_skill_evals`' process pool (row 17).
   - `raise_if_blocked_prompt_event(event)`: raises when `event` is a dict whose `type` is `system` and whose `prompt_submit_outcome` is a dict with `kind` `blocked_by_hook`. That is the check `measure_main_session_rule_adherence.py:453-456` makes (row 13), which stays there (item 5). The message carries the outcome's `reason` when it is a string.
   - `raise_if_blocked_prompt_stream(lines)`: decodes and parses each `str` or `bytes` line as `detect_dispatch_in_lines` does, and calls `raise_if_blocked_prompt_event` on each.
   - `raise_if_blocked_prompt_stdout(stdout)`: takes `str` or `bytes`, decoding with `errors="replace"`, and raises when `PROMPT_BLOCK_PREFIX` occurs anywhere in it. It matches a substring, not a line, because the reason can follow the CLI's `[<hook-command>]: ` on one line and a `_lib_capped_for` note can precede the prefix (rows 4, 5).
2. **The two forms.**
   - Stream-json keys on the structured field, not the prefix. The field is the CLI's own signal for any hook's block (row 1). Only the CLI writes `system` events, so a prompt or a model reply that quotes the prefix cannot trip it.
   - Plain text has no such field, so it keys on the prefix the follow-up's prompt gate prints (row 4). This PR owns its copy of the string and needs nothing from that PR to land (row 27).
   - A plain-text block from a hook that prints no prefix stays unrecognized (Out of scope; row 19).
3. **Where each consumer checks.**
   - `detect_trigger_in_lines` and `detect_dispatch_in_lines` call `raise_if_blocked_prompt_event` on each parsed object inside their existing loops, so neither iterates `lines` twice. The call sits right after `json.loads` and before the `event` guard (`:473-475`, `:603-605`), because the block event is top-level and has no `event` key (row 1). Each docstring gains `Raises BlockedPromptError when a hook blocked the prompt.`
   - These two parsers are where a blocked stream becomes "no trigger" or "no dispatch" (root). msmr (`:877`) and the explore-recall harness (`:826`) read `attempted` from `detect_dispatch_in_lines`, inside a `try` whose `finally` removes the temp project and session store (rows 8, 10). Neither module's source changes.
   - `detect_trigger_in_stream`, `detect_dispatch_in_stream` and `run_dispatch_sample` keep their kill, wait and `proc.stdout.close()` as they are. A raise from the parser skips them. The blocked child writes only its short `result` line after the block event and exits on its own (row 32), so no running child is left to kill.
   - `evals/review_bench/runner.py` `execute_run` and `evals/review_bench/adjudicate.py` `execute_judge_run` call `raise_if_blocked_prompt_stream(lines)` right after `launch(...)` (`runner.py:1372`, `adjudicate.py:544`) and catch its `BlockedPromptError`. The adjudicator reaches it as `runner.run_skill_evals`.
     - A caught block records the run missing, with reason `MISSING_REASON_BLOCKED_PROMPT = "blocked-prompt"`, new in `runner.py`, and the error's message as its detail (row 35).
     - The check comes before `wait_for_session_flush`, so a blocked run skips the up-to-10 s wait for a transcript (row 33).
     - It sits outside the injectable `launch`, so the tests' fake launchers exercise it (rows 11, 22).
   - Each plain-text call passes its stdout to `raise_if_blocked_prompt_stdout` as soon as `subprocess.run` returns: `run_description_fidelity_sample` (`:432`), `prime_dispatch_session` (`:718`, which keeps the `CompletedProcess` it discards today), `judge_disposition` (`:936`) and `_run_review` (`:979`). Without it, a blocked review reaches the judge as a review, and a blocked priming leaves every warm sample resuming a session that never ran (row 18).
4. **`LaunchOutcome` stays as it is.** It records what the launcher learns beyond stdout, and the block is on stdout, which every caller already holds (rows 1, 12). The source design's launcher outcome seam (`.claude/plans/main-session-instruction-split.md` Design item 10) is set aside for the same reason: a blocked run exits 0, so its exit status and stderr tail mark nothing.
5. **The adherence harness does not change.**
   - It classes a block as `blocked-prompt`, pauses the run with a resumable `RunPaused`, and reruns the slot on `--resume` (row 13). That already ends its run without scoring a miss, and the follow-up's gate run measures through that code (row 14).
   - Raising there would cut the attempt record and the pause its resume depends on. It calls neither parser (row 8).
   - Its own field check at `:454-456` stays. `parse_stream` is part of its hashed scorer source (row 36). Sharing a predicate with it would change that hash with no behavior change, and would move the predicate outside the hashed source. The retirement PR deletes the harness (Context).
6. **What a block does.** No entry point catches `BlockedPromptError`. The process ends with its traceback, whose last line carries the hook's reason, and a nonzero status (row 25).
   - `run_skill_evals`: `fut.result()` re-raises the worker's error. The pool's exit waits for queued samples (row 17), and a gate that blocks every prompt ends each one with no model turn (row 1). `main`'s `finally` then removes its temp projects and the dispatch session store (row 37).
   - msmr and explore recall: the matrix cell's or sample's `finally` cleans up, then the error ends `main` (row 10).
   - review_bench raises nothing new. A blocked run is retried once, as any missing run is, at no model cost (row 1).
     - When every run in a block is blocked, `run_campaign` appends the block's records, cleans it up and raises `SystemicFailureError`, whose message carries `blocked-prompt` and the hook's reason (rows 15, 35). `run_review_bench.py` then exits 2 with the block unmarked, so a resume reruns it (row 16).
     - A defect whose two judge runs are both blocked stops `judge` the same way (row 16).
     - One blocked run among others stays one missing run. One blocked judge leaves its defect marked complete and never retried, as any single missing judge does (row 34).
7. **Tests use inline synthetic events, not a fixture file.** A new file directly under `evals/fixtures/` matches no `select-tests.py` rule, so it would force a full-suite run and need a new row and its test (row 20). The retiring `evals/fixtures/main-session-rule-adherence/` is the wrong home (Context). An inline event follows `evals/test_main_session_rule_adherence_fixtures.py:455-461` and the DAMP test exception (row 23). `test_trigger_detector.py` holds the recorded three-line block stream once, and each consumer test builds its one-line block event inline rather than copying a helper across files (row 39). Every event carries synthetic values only (repo `CLAUDE.md` § "Redact private-project-identifying content").
8. **`select-tests.py` needs no change.** Every path the diff touches already maps to the tests that cover it (row 21).
9. **Docs.** `evals/README.md:102`'s "Exit code is always `0`" gains the blocked-prompt exception (row 24). A short section states how each instrument stops, both forms, the plain-text limits and the adherence gate's pause. `CHANGELOG.md` gains one `[Unreleased]` `### Changed` entry.

Alternatives set aside:
- **Raise inside `msmr._run_claude_to_completion`.** One site would cover msmr, explore recall and both review_bench launches. But the adherence harness launches through it and must keep classifying (rows 13, 14), so it would need an opt-out. review_bench's tests replace the launcher and would bypass the check (row 22). `run_skill_evals`' own launches never use it (row 9).
- **A `LaunchOutcome.blocked_prompt` field.** Item 4.
- **Keying stream-json on the prefix.** Item 2.
- **Converting the four plain-text calls to stream-json.** It rewrites four parse paths to gain a field the prefix already stands in for.
- **Catching `BlockedPromptError` in each entry point for a one-line message.** Three entry points, `run_skill_evals`, msmr and explore recall, change for a cosmetic gain, and the traceback already names the cause (rows 30, 31).
- **A committed fixture file.** Item 7.
- **Raising `BlockedPromptError` from review_bench's two launch sites.** `run_defect_block` re-raises a worker's error before `run_campaign` appends the finished runs' records or calls `cleanup_defect_block` (`runner.py:1521-1528`, `:1650-1659`). The judge's cleanup is not in a `finally` (`adjudicate.py:691-698`). So the raise would drop paid records, leave fixtures and session stores until a resume sweep, and exit 1 where README documents exit 2 (row 34).
- **Sharing the block check with the adherence harness.** Item 5.
- **Killing the child in a `finally` in both stream readers.** Item 3: a block leaves no running child.

### Assumption ledger

**Root problem:** A blocked `claude -p` prompt exits 0 and ends its stream with a `success` result, so each eval consumer scores it as no trigger, no dispatch, a format failure or a judged review, and nothing names the block.

**Givens:**
- **G1 — A blocked `claude -p` prompt exits 0, puts its block on stdout, and ends its stream with `result` `success` and `num_turns` 0.** The vendor owns the CLI's output (row 1).
- **G2 — The prompt gate and its prefix belong to the follow-up plan.** That plan defines the gate's reason text (row 4). This PR copies the one string and adds no hook.

**Mechanisms:**
- **M1 — `BlockedPromptError`, `PROMPT_BLOCK_PREFIX` and the three recognizer functions in `run_skill_evals`.** It adds a raise path. Lighter primitives, and why each fails:
  - excluding a blocked sample from the denominator, as disposition-fidelity excludes a failed call. Runtime and dispatch scoring have no exclusion branch, so each consumer would gain one. An all-blocked run would also still read as 0/K or inconclusive with no cause named (root);
  - a warning line and carrying on: the score still counts the miss (root);
  - a `LaunchOutcome` field: the outcome sees nothing that marks a block (row 12), and `run_skill_evals`' launches never pass one (row 9).

  `anchors: root, row1, row4, row5, row6, row7, row9, row12, row17, row26, row27, row28, row29`.
- **M2 — The two stream parsers raise, and the stream readers stay as they are.** `anchors: root, row8, row9, row10, row32`.
- **M3 — review_bench records a blocked run missing, before the flush wait.** review_bench is the one consumer that already has the exclusion branch and the all-missing stop M1's first alternative lacks elsewhere, so it gains a reason, not a stop path. `anchors: row11, row15, row16, row22, row33, row34, row35`.
- **M4 — The four plain-text calls raise on the prefix.** `anchors: row4, row5, row9, row18, row19`.
- **M5 — The adherence harness does not change.** `anchors: row8, row13, row14, row36`.
- **M6 — No entry point catches `BlockedPromptError`.** `anchors: row10, row17, row25, row30, row31, row37`.
- **M7 — Inline test events, and no `select-tests.py` change.** `anchors: row20, row21, row22, row23, row39`.
- **M8 — The docs and the probe.** `anchors: row2, row3, row24, row34`.

**Material assumptions:**

| # | Assumption | Tag |
|---|---|---|
| 1 | A blocked `claude -p` prompt exits 0 and prints the block header, the hook's reason and the original prompt on stdout. Its stream holds a `system` event whose `prompt_submit_outcome.kind` is `blocked_by_hook`, and a `result` event with `subtype` `success` and `num_turns` 0 | `[verified: .claude/plans/main-session-rules-hook-delivery.md row 22, from probe P3, on branch main-session-rules-hook-delivery]` |
| 2 | Every stream-json eval launch passes `--include-partial-messages` | `[verified: evals/run_skill_evals.py:563, 669, 678; evals/measure_subagent_model_resolution.py:289; evals/measure_explore_recall.py:701; evals/review_bench/runner.py:167]` |
| 3 | A stream run with `--include-partial-messages` still carries row 1's `system` event. P3 ran with `--output-format stream-json --verbose`, and the follow-up's row 23 does not say whether it passed that flag | `[unverified]`: Verification "Probe" checks it with `run_single_sample`'s flags only. The other stream launches add flags of their own, such as `--resume --fork-session`, `--append-system-prompt`, or `--session-id` and `--max-budget-usd` (`run_skill_evals.py:665-681`; `review_bench/runner.py:163-171`), which the probe does not run |
| 4 | The follow-up's prompt gate opens its reason with the fixed line `main-session-rules: prompt blocked`. On a host with neither `timeout` nor `gtimeout`, a `_lib_capped_for` note can print on stderr before it | `[verified: .claude/plans/main-session-rules-hook-delivery.md Approach item 1, on branch main-session-rules-hook-delivery]` |
| 5 | The CLI's block text puts `[<hook-command>]: ` before the hook's reason on one line, in a recording whose values were made synthetic | `[verified: evals/fixtures/main-session-rule-adherence/blocked-prompt.jsonl:2; evals/test_measure_main_session_rule_adherence.py:1038-1049]` |
| 6 | msmr imports `run_skill_evals` at module top, and `run_skill_evals` imports no evals module | `[verified: evals/measure_subagent_model_resolution.py:53; evals/run_skill_evals.py:31-57]` |
| 7 | Every launching consumer reaches `run_skill_evals`: msmr directly, explore recall as `msmr.run_skill_evals`, `review_bench/runner.py` directly, and `adjudicate.py` through `runner` | `[verified: measure_subagent_model_resolution.py:53; measure_explore_recall.py:54, 826; review_bench/runner.py:41-42; review_bench/adjudicate.py:36, 536]` |
| 8 | `detect_dispatch_in_lines` is called at `run_skill_evals.py:638`, `measure_subagent_model_resolution.py:877` and `measure_explore_recall.py:826`. `detect_trigger_in_lines` is called only inside `run_skill_evals.py` (`:539`, `:549`). The adherence harness calls neither | `[verified: a Grep of the repository's Python files for both names]` |
| 9 | `run_skill_evals` launches `claude -p` in seven command shapes at six call sites, each discarding stderr: stream-json `Popen` at `:558` and at `:751` (the warm and cold shapes built at `:665` and `:674`), and plain-text `subprocess.run` at `:432`, `:718`, `:936` and `:979`. None goes through msmr's launcher | `[verified: evals/run_skill_evals.py]` |
| 10 | msmr and explore recall launch through `msmr._run_claude_to_completion` and read `attempted` from `detect_dispatch_in_lines`, inside a `try` whose `finally` removes the temp project and session store | `[verified: measure_subagent_model_resolution.py:870-900; measure_explore_recall.py:820-893]` |
| 11 | review_bench's runner and adjudicator launch through an injectable `launch` that defaults to msmr's launcher, then wait for a session flush | `[verified: review_bench/runner.py:1354-1376; review_bench/adjudicate.py:531-548]` |
| 12 | `LaunchOutcome` holds only the exit status and a bounded stderr tail, and its docstring scopes it to what the launcher learns beyond stdout | `[verified: measure_subagent_model_resolution.py:696-702]` |
| 13 | The adherence harness launches through msmr's launcher with an outcome, reads the block from `prompt_submit_outcome.kind`, classes it `blocked-prompt`, pauses with `RunPaused`, and reruns that class on resume | `[verified: evals/measure_main_session_rule_adherence.py:453-456, 948-951, 1078, 1459-1462, 2198-2199]` |
| 14 | The follow-up relies on the harness still reading and classifying a block itself, because its gate run measures through that code | `[verified: .claude/plans/main-session-rules-hook-delivery.md Approach item 5, on branch main-session-rules-hook-delivery]` |
| 15 | When every run of a block is missing, `run_campaign` appends the block's records and runs `cleanup_defect_block`, then raises `SystemicFailureError` before marking the block complete. The message lists the missing reasons and up to three distinct run details | `[verified: review_bench/runner.py:1600-1608, 1655-1665]` |
| 16 | `run_review_bench.py` prints a `SystemicFailureError` and exits 2. Its judge loop stops with exit 2, the defect unmarked, when both judge runs of a defect are missing | `[verified: evals/run_review_bench.py:1205-1214, 1363-1366, 1385-1392]` |
| 17 | A process pool hands a worker's exception to `fut.result()` by pickling it, and the pool's context exit waits for queued work | `[unverified]`: Python's `concurrent.futures` behavior, not read this session. Critical files' pickle round-trip test checks the pickling half |
| 18 | A blocked `_run_review` returns the block text to the judge as a review, and a blocked priming call leaves the warm samples resuming its session id. `run_disposition_sample` runs `_run_review` for both prompts before either judge call | `[verified: evals/run_skill_evals.py:715-733, 977-1009, 1401-1407]` |
| 19 | No hook this repo registers blocks a prompt: its three `UserPromptSubmit` hooks are nudges with no `exit 2` | `[verified: claude/.claude/settings.json:275-291; a Grep of the three scripts for exit 2 and decision]` |
| 20 | A new file directly under `evals/fixtures/` matches no `select-tests.py` rule and falls to the unmatched-path full suite | `[verified: claude/.claude/scripts/select-tests.py:701-714, 921-1016, 1042-1060]` |
| 21 | Every path the diff touches maps: `evals/run_skill_evals.py` to `EVALS_TEST_TARGETS` and `SKILLS_TESTS_DIR`; `evals/review_bench/*` and the four evals test files through `_is_review_bench_change`; `test_trigger_detector.py` to `SKILLS_TESTS_DIR`; `evals/README.md` to `SKILLS_TESTS_DIR`; `CHANGELOG.md` to no targets | `[verified: select-tests.py:673-687, 707, 710, 712, 924, 926, 930]` |
| 22 | The four consumer test files inject or patch the launcher | `[verified: evals/test_measure_subagent_model_resolution.py:535; evals/test_measure_explore_recall.py:1311; evals/test_review_bench_runner.py:733-736; evals/test_review_bench_adjudicate.py:1511-1514]` |
| 23 | The adherence fixture test builds its block event inline, and the harness's tests assert `parse_stream` reads the recorded block as blocked | `[verified: evals/test_main_session_rule_adherence_fixtures.py:455-461; evals/test_measure_main_session_rule_adherence.py:1038-1059]` |
| 24 | `evals/README.md:102` says the exit code is always `0` | `[verified: evals/README.md:102]` |
| 25 | An uncaught exception ends a Python process with its traceback and exit status 1 | `[unverified]`: Python's documented behavior, not read this session |
| 26 | The work splits into this early PR and one move PR | `[engineer-verified: "Early PR plus one move PR (Recommended)"]` |
| 27 | Row 26's option, in the dispatching session's words: this PR delivers block detection for the eval consumers under its own plan and lands before the move PR, needing neither the move nor the adapter's Phase 4 | `[unverified]`: relayed option text, the follow-up's row 75 |
| 28 | The engineer agreed to the dispatching session's proposals, which included starting this PR now on its own branch and plan | `[engineer-verified: "Ok sounds good."]` |
| 29 | The engineer agreed to continue with this PR | `[engineer-verified: "Sounds good continue"]` |
| 30 | The engineer's judgment on heavy controls proposed earlier | `[engineer-verified: "extremely overengineered and atypical for sdlc in this repo"]` |
| 31 | Row 30 read as a preference for the smallest design that meets the Goal, which this plan applies to fixtures, entry-point catches and pool handling | `[unverified]`: the dispatching session's reading and this plan's |
| 32 | After its block event, a blocked `claude -p` writes only its `result` line, then exits 0 (row 1) | `[verified: evals/fixtures/main-session-rule-adherence/blocked-prompt.jsonl:2-3, a recording whose one stated omission is the init event, per evals/test_measure_main_session_rule_adherence.py:1040-1041]` |
| 33 | `wait_for_session_flush` waits up to `SESSION_FLUSH_TIMEOUT_S`, 10 s, for a run that did not time out | `[verified: review_bench/runner.py:388, 407-414; measure_subagent_model_resolution.py:802]` |
| 34 | README documents both review_bench stops as exit 2 with the block or defect unmarked, and says a defect with one missing judge run is marked complete and never retried | `[verified: evals/README.md:1249-1252, 1256-1259]` |
| 35 | `missing_reason` is an open string counted per arm. `_fail` and `RunValidity` carry a failure detail into `missing_detail`. A retried run keeps its last attempt's reason and detail | `[verified: review_bench/runner.py:187-192, 552-564, 832-843, 1399, 1410]` |
| 36 | The adherence harness's scorer hash is computed over the source of `parse_stream` and its other scorer parts | `[verified: evals/measure_main_session_rule_adherence.py:678-683, 694-700]` |
| 37 | `run_skill_evals.main` prints its report and writes `--json` only after its last skill. Its `finally` removes its temp projects and the dispatch session store | `[verified: evals/run_skill_evals.py:1431-1441, 1445-1460]` |
| 38 | The follow-up's dispatch gate opens its deny reason with `main-session-rules: dispatch denied`, and backstops the prompt gate only at `Agent` and `Task` calls | `[verified: .claude/plans/main-session-rules-hook-delivery.md Approach items 1 and 3, on branch main-session-rules-hook-delivery]` |
| 39 | The msmr and explore-recall happy-path fakes create the session store, and their tests assert it and the launched cwd are gone | `[verified: evals/test_measure_subagent_model_resolution.py:517-520, 545-548; evals/test_measure_explore_recall.py:1298-1299, 1321-1324]` |

## Critical files

One `code-writer` dispatch. Every file below uses Approach item 1's names, so a split would restate them in each prompt. Verify with select-tests and ruff (Verification).

- `evals/run_skill_evals.py` — Approach item 1's constant, error and three functions. Item 3's parser calls and the four plain-text calls.
- `evals/review_bench/runner.py` — `MISSING_REASON_BLOCKED_PROMPT` beside `:190-192`, with the comments at `:187-189` and `:195` counting it, and `execute_run`'s check after `:1372` (Approach item 3).
- `evals/review_bench/adjudicate.py` — `execute_judge_run`'s check after `:544`, building its failure as `:553-557` does.
- `claude-skills/skills/tests/test_trigger_detector.py` — two module constants, each defined once:
  - the three lines of `evals/fixtures/main-session-rule-adherence/blocked-prompt.jsonl`, copied inline with their synthetic values, so the recorded shape outlives the retirement PR;
  - a plain-text block stdout in row 5's shape, carrying the literal `main-session-rules: prompt blocked` after `[<hook-command>]: `, never built from `PROMPT_BLOCK_PREFIX`.

  Cases:
  - `raise_if_blocked_prompt_stream` raises on the three lines as `str` and as `bytes`, with the reason in the message. It still raises when a non-JSON line comes first, and does not raise on the existing no-trigger fixture;
  - `raise_if_blocked_prompt_event` raises `BlockedPromptError` on a block-kind outcome whose `reason` is absent or an int;
  - it does not raise on an event that is a list or a string, on a `system` event whose `prompt_submit_outcome` is `None`, a string, a list or a dict with another `kind`, or on a non-`system` event carrying a block-kind outcome;
  - `detect_trigger_in_lines` and `detect_dispatch_in_lines` raise on the three lines;
  - `raise_if_blocked_prompt_stdout` raises on the block stdout as `str` and as `bytes`. It does not raise on an ordinary answer, on `prompt blocked` alone or on `main-session-rules` alone;
  - a pickle round-trip of `BlockedPromptError` keeps its type and message (row 17);
  - a fake `subprocess.run` exits 0 with the block stdout, records each call, and raises on a command not starting `claude -p`. Under it `run_description_fidelity_sample`, `judge_disposition` and `prime_dispatch_session` each raise, the last instead of returning a session id;
  - under the same fake, `run_disposition_sample` raises after exactly one call. Without `_run_review`'s check the case fails on the call count, though `judge_disposition`'s check would raise on a later call (row 18).
- `evals/test_measure_subagent_model_resolution.py` — `execute_matrix_cell` raises `BlockedPromptError` with the launcher patched as at `:535` to return a one-line block event built inline. The fake creates the session store as `:517-520` does, and the test asserts the launched cwd and its store are gone as `:545-548` does (row 39).
- `evals/test_measure_explore_recall.py` — the same for `execute_one_sample_attempt`, patched as at `:1311`, creating the store as `:1298-1299` does and asserting as `:1321-1324` does.
- `evals/test_review_bench_runner.py` — `execute_run`, given a fake `launch` returning an inline block event as `:733-736` passes one, records `STATUS_MISSING` with `MISSING_REASON_BLOCKED_PROMPT` and the synthetic reason in `missing_detail`. A recording stub in place of `runner.wait_for_session_flush` records no call.
- `evals/test_review_bench_adjudicate.py` — the same for `execute_judge_run`, given a fake `launch` as `:1511-1514` passes one.
- `evals/README.md`:
  - `:102` says the exit code is `0` unless a hook blocks a `claude -p` prompt, and points to the new section by name, with no `§` citation;
  - a new `## A prompt blocked by a hook` section before `## Linting` states, one item each:
    - every instrument here that launches `claude -p` stops on a blocked prompt instead of scoring the run, because a blocked `claude -p` exits 0;
    - `run_skill_evals.py`, the subagent model resolution experiment and the explore recall gate stop with `BlockedPromptError` and a nonzero exit;
    - review bench records a blocked run missing, with reason `blocked-prompt` and the hook's reason as its detail, so a block or a judge pair whose runs are all blocked stops with exit 2, as any all-missing one does;
    - stream-json launches recognize any hook's block by the `system` event whose `prompt_submit_outcome.kind` is `blocked_by_hook`;
    - `run_skill_evals.py`'s plain-text calls recognize only stdout carrying `main-session-rules: prompt blocked`, so another hook's block goes unrecognized there;
    - plain-text output that echoes the prefix reads as a block;
    - the main session rule adherence gate instead pauses on a blocked prompt, and `--resume` reruns it.
- `CHANGELOG.md` — one `[Unreleased]` `### Changed` entry, one sentence for each of these README section items:
  - the stop on a blocked prompt;
  - the plain-text calls' prefix-only recognition;
  - the adherence gate's pause.

Reuse: `detect_dispatch_in_lines`' line decoding for `raise_if_blocked_prompt_stream`; review_bench's missing-run record and its all-missing stop; each consumer test file's existing launcher injection.

Projected diff: about 350 lines.

## Verification

- `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py`. Expected: reason `domain-selected`, not a full-suite fallback, and every selected test passes.
- `scripts/venv-run.sh ruff check claude/.claude/ claude-skills/ plugins/ evals/`. Expected: clean.
- **Probe** (the session, before the commit; a blocked prompt makes no model turn, row 1):
  - outside the repository, a scratch project whose `.claude/settings.json` registers one `UserPromptSubmit` command hook. The hook writes `main-session-rules: prompt blocked` as its first stderr line and one synthetic line after it, then exits 2, as the follow-up's prompt gate does (row 4);
  - through `scripts/venv-run.sh python3`, call `run_single_sample` and `run_description_fidelity_sample` once each with the scratch project as their project directory. These drive the real `Popen`, `select` and read path and a real `subprocess.run`;
  - expected: each raises `BlockedPromptError` (rows 3, 5);
  - the PR body records `claude --version`, the baseline for a later CLI change to `prompt_submit_outcome` (Out of scope);
  - the outputs stay outside the repository and are never committed;
  - a call that returns instead of raising stops the commit. The session then reruns that call's `claude -p` command by hand, and the captured output goes to the engineer, including whether the hook fired;
  - if that stream holds no `prompt_submit_outcome`, the fallback to propose keys stream-json on `PROMPT_BLOCK_PREFIX` inside a `system` event's text, which a model reply cannot trip. A stream holding neither leaves stream-json with no block signal, and the design goes back to the engineer.

## Out of scope

- A plain-text block from a hook that prints no `main-session-rules: prompt blocked` line. This repo registers no other prompt-blocking hook (row 19), and the CLI's own header is vendor text this repo does not own.
- Stopping queued `run_skill_evals` samples at the first block. A gate that blocks every prompt ends each one with no model turn (row 1), and cancelling would add pool handling to `run_case` and `run_disposition_case`.
- Keeping the results of skills that finished before a block. `run_skill_evals.main` prints its report and writes `--json` only after its last skill (row 37). Only an intermittent block loses any, because a gate that blocks every prompt raises on the first sample.
- Stopping review_bench on one blocked run among others. It stays one missing run with its cause named, like any other missing run (Approach item 6).
- A one-line message instead of a traceback in each entry point (Alternatives).
- The test tying `_lib.sh`'s prefix to `PROMPT_BLOCK_PREFIX`, which the move PR owns, because `_lib.sh` gains the prefix only there. That PR's prompt-gate test must assert the gate's first reason line equals `run_skill_evals.PROMPT_BLOCK_PREFIX`. Without that tie, a reword on either side returns the four plain-text calls to scoring a block as a miss. This PR's tests carry the literal string, so a change to the constant fails here.
- A dispatch the follow-up's `main-session-rules: dispatch denied` gate denies. It is not a blocked prompt. That gate backstops the prompt gate at `Agent` and `Task` calls (row 38), so a delivery failure present at launch blocks the first prompt before any dispatch.
- A CLI rename of `prompt_submit_outcome` or its `kind`. Every test here feeds the recorded or a synthetic shape, so a rename would leave them green. The PR body's `claude --version` is the baseline a later probe compares against.
- The adherence harness's pause behavior, its own block check and its retirement, which the follow-up plan and the retirement PR own (rows 13, 14, 36).
- The six existing synthetic fixtures directly under `evals/fixtures/` that map to no `select-tests.py` rule (row 20). They predate this change, and this diff touches none of them.
