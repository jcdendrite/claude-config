# Follow-ups left by the pooled review-round-cost PR

## Context

Goal: land the follow-up fixes the `review-round-cost --pooled` PR (#1009, merged as `d603ae99`) left behind, as one focused PR of three commits, in this order: (1) `select-tests.py` maps the two docs the new scripts tests read; (2) two text-mode transcript readers stop crashing on a non-UTF-8 byte; (3) the non-pooled `review-round-cost` footer label stops overstating what it counts.

Ask: "Yes the two PRs sound good" and "Sure that all sounds good" — the engineer's replies to a `plan-architect` triage that recommended two PRs. The triaged scope is the architect's recommendation, not the engineer's own wording. The flaky `sed` shim fix is the other PR and is out of scope here.

Why now: #1009 introduced the `select-tests.py` gap. On `origin/main` before it, no scripts test read either doc. The engineer said separate issues would not get done, so the fixes ship as one PR.

## Approach

The PR has three sequenced commits. Each one makes a minimal fix in its own domain:

1. `select-tests.py` maps `docs/transcript-analysis.md` and `docs/private-project-redaction.md` to `claude/.claude/scripts/tests/`. `test_transcript_review_rounds.py`'s three inline reads of those docs move up to module-level constants, so `TestCrossDomainReadCompleteness` enforces the mapping from then on.
2. `reviewer_yield._scan_reviewer_transcript` and `subagent_mix._dispatch_usage_summary` read through `corpus._parse_jsonl_records` instead of their own text-mode loops.
3. The non-pooled footer label becomes `Subagent-dispatch dollars: X% of branch dollars, inside round windows`.

Three of the session's four defaults stand: the label wording, `spend_reviewer_only` staying unchanged, and the None/[] mapping. The fourth, a single CHANGELOG entry, is replaced by one bullet per commit (M7). Commit 3 is pre-authorized to be dropped, and a shared entry would then need rewriting. The decode and label changes are also unrelated.

No open decision needs the engineer.

**Root problem.** PR #1009 left three defects on main:
- `select-tests.py` under-selects for two docs that a scripts test now reads.
- Two subagent-transcript readers abort on a non-UTF-8 byte.
- The non-pooled footer's "Reviewer-dispatch dollars" label names a narrower population than its numerator counts.

**Givens.**
- G1. Claude Code writes transcripts as `\n`-terminated JSONL that is normally UTF-8. The vendor owns the format, so a stray non-UTF-8 byte is input the readers must tolerate, not something this plan can prevent.
- G2. CI runs the full suite on every push (CLAUDE.md, Commands). The repo's CI configuration lies outside this plan, and it backstops the per-commit gate.

**Assumption ledger.**
1. The triaged scope is accepted: the three commits in this order, the out-of-scope list, and the rule to drop commit 3 rather than revise it if it draws a second round of wording comments. The quote answered the architect's triage recommendation, so the scope wording is the architect's, not the engineer's. `[engineer-verified: "Sure that all sounds good"]`
2. This is PR B. The sed-shim fix is a separate PR A. `[engineer-verified: "Yes the two PRs sound good"]`
3. `select_pytest_targets` unions every matching `DOMAIN_RULES`/`CROSS_DOMAIN_EXCEPTIONS` row. A new `SCRIPTS_TESTS_DIR` row therefore adds to the docs/ blanket's HOOKS/SKILLS targets rather than replacing them. `[verified: claude/.claude/scripts/select-tests.py:760-767]`
4. The scanner seeds `REPO_ROOT` as `""`. A module-level, unannotated, single-Name `X = REPO_ROOT / "docs" / "f.md"` resolves to `docs/f.md`, while an annotated or function-local expression is invisible to it. The completeness test then fails unless `select_pytest_targets(["docs/f.md"])` covers the reading test file. `[verified: claude/.claude/scripts/tests/test_select_tests.py:42-48, :116-139, :321-325, :341-358]`
5. The three reads in `test_transcript_review_rounds.py` are inline, function-local `REPO_ROOT` expressions, so nothing enforces their mapping today. `[verified: test_transcript_review_rounds.py:14, :2705, :2758, :2767]`
6. Only one scripts test reads a docs/ file by path besides `test_transcript_review_rounds.py`: `test_transcript_analysis_architecture_doc.py`, already mapped at `select-tests.py:702`. Every other docs/ hit in `claude/.claude/scripts/tests/` is a comment, an assertion string, or fixture data. `[verified: Grep of claude/.claude/scripts/tests for docs paths]`
7. The test at `test_select_tests.py:1055-1062` is wrong once row 3's rule lands. Its docstring says no test reads `docs/transcript-analysis.md`, and it asserts targets equal {HOOKS, SKILLS}. `[verified: test_select_tests.py:1055-1062]`
8. `select-tests.py` is a `GLOBAL_TRIGGER_PATHS` member, and `compute_changed_paths` diffs the whole branch against `origin/main`. So every `select-tests.py` run on this branch from commit 1 onward runs the full suite. That is CLAUDE.md's "select-tests.py itself selected the full suite" case. `[verified: select-tests.py:366-370, :815-837]`
9. `_parse_jsonl_records` behaves as follows. `[verified: claude/.claude/scripts/transcript_analysis/corpus.py:91-114]`
   - It opens the file in binary mode.
   - It skips a line on `UnicodeDecodeError` or `JSONDecodeError`.
   - It keeps a non-object top-level value as a record.
   - It returns `None` only on `OSError`, and `[]` for a readable file with no decodable record.
10. Both readers iterate a text-mode handle. Each catches only `JSONDecodeError` inside the loop and `OSError` outside it, so a decode error raised by the file iterator escapes both. `CHANGELOG.md:13` records the same abort for the readers already routed through the helper. `[verified: reviewer_yield.py:103-138; subagent_mix.py:624-632; CHANGELOG.md:13]` Within `claude/.claude/scripts/transcript_analysis/` these are the only two such loops. Two other loops exist outside it and stay unchanged:
    - `evals/measure_subagent_model_resolution.py:422-436` has the same loop, and its module header says it "mirrors (does not import)" the corpus tool.
    - `transcript-analysis.py:4141-4144` (friction-count) reads with `errors="replace"` on purpose, with a comment at `:4136-4140`.

    `[verified: Read of both sites by plan-review's backend reviewer]`
11. In each module, `import json` is used only by the loop being replaced, so both imports become unused (ruff F401). `[verified: reviewer_yield.py:12, :107-108; subagent_mix.py:14, :628-629]`
12. `test_file_that_fails_to_open_returns_empty_summary` patches `open` on `subagent_mix`'s namespace. After the change the open happens in `corpus`, so the patch no longer reaches it and the test would fail. `[verified: test_transcript_subagent_mix_dollars.py:500-512]`
13. `reviewer_yield`'s existing unreadable-transcript tests use a missing file, which the helper maps to `None`, so `read_error` stays True. They need no change. `[verified: test_transcript_reviewer_yield.py:926-951, :1769-1783]`
14. The helper loads the whole file into memory. `_read_session_file_partitioned` already does this for whole sessions, so loading one subagent transcript in `reviewer_yield` is no new memory pattern. `[verified: corpus.py:117-154]`
15. Binary-mode iteration splits only on `\n`, while text mode also splits on a lone `\r`. The two modes can disagree on a record only when a raw `\r` sits between JSON tokens, and G1's `\n`-terminated format never produces that. A raw `\r` inside a JSON string is rejected by both modes. The stronger reason to accept the difference: both readers then match every other helper-routed reader. `[verified: plan-review's backend reviewer ran text-mode and binary-mode parses on `{"c":\r3}` and `{"a":1}\r{"b":2}\n`; no test in the three affected test files writes `\r`]`
16. The test environment's default text encoding is UTF-8, so the new decode tests fail with `UnicodeDecodeError` before commit 2's reader edit. `UnicodeDecodeError` is a `ValueError`, not an `OSError`, so it escapes both readers' `except OSError`. Only the red step depends on the encoding, and Verification runs it with `PYTHONUTF8=1` to remove that dependency. `[verified: plan-review's backend reviewer, UTF-8 locale]`
17. `total_agent_dollars` sums every `pricing._SPAWN_TOOL_NAMES` dispatch inside a round window. Nothing in `review_rounds.py` filters by agent type. Main-thread turns go to `main_dollars`. `[verified: review_rounds.py:365-387, :1342, :1362-1365; Grep of review_rounds.py for subagent_type/staff-/_REVIEWER returns no filter]`
18. The pooled block labels the same in-window numerator "subagent dispatches only", under key `spend_reviewer_only`. `[verified: review_rounds.py:720, :1039]`
19. The exact text "Reviewer-dispatch dollars" appears in four files:
    - `review_rounds.py:1151, :1363`
    - `docs/transcript-analysis.md:1158, :1179`
    - `test_transcript_review_rounds.py:1149, :1212, :1216`
    - `docs/case-studies/review-loop-cost-forensics.md`, a dated record that stays unchanged

    `CHANGELOG.md` has no hit. `[verified: Grep "Reviewer-dispatch dollars" across the worktree]`
20. Inside the repo, only row 19's test assertions and docs read the footer label. `[verified: Grep of the repo by plan-review's backend reviewer; `review-loop-cost-audit/SKILL.md` does not cite it]` Whether a consumer outside this repo parses the label from stdout is unobservable from here. `[unverified]`
21. Four test comments call the figure reviewer-only or reviewer-dispatch: `:1159`, `:1780`, `:2367`, `:2568`. `:2604` already says "subagent dispatches only". `:3655` names the key `spend_reviewer_only`, which stays. `[verified: test_transcript_review_rounds.py at those lines]`
22. This repo records decode-behavior and select-tests-mapping changes under `[Unreleased]` → the first `### Changed`. `[verified: CHANGELOG.md:7, :13, :154]` The bullet order inside that section is `[unverified]`.
23. The three per-commit CHANGELOG bullets (M7) are in scope. The engineer answered the question "is each bullet in scope?" with the label `[engineer-verified: "Keep all three (Recommended)"]`.
24. Commit 2 changes more than the two readers' own tests. `_scan_reviewer_transcript` feeds `compute_reviewer_yield_data`, which `cmd_reviewer_yield`, `cost-ledger --record` (`cost_ledger.py:657`), and `evals/review_bench/mine_review_rounds.py:536` consume. `_dispatch_usage_summary` feeds only `cmd_subagent_mix` (`subagent_mix.py:210`). Today a non-UTF-8 byte aborts `cost-ledger --record` before it writes a row. After commit 2 it writes a row from a transcript with silently skipped lines, and the "N reviewer transcripts failed to read" line never fires for a skipped line. The plan accepts this: every other helper-routed reader already behaves the same way, and a skipped line is the stated policy of `corpus._parse_jsonl_records`. The commit-2 CHANGELOG bullet names `cost-ledger --record` and says an all-undecodable transcript reads as empty. `[verified: plan-review's backend reviewer, reviewer_yield.py:186, cost_ledger.py:657, mine_review_rounds.py:150/:536, subagent_mix.py:210]`
25. Dropping commit 3 follows a fixed procedure. The trigger is a second `/code-review` round whose only open findings on commit 3 are wording findings on the label or its CHANGELOG bullet. Before the commit exists, drop it by discarding its staged diff. After it exists, `git revert` it as a new commit. The plan file stays unedited, because an edit would re-trigger `/plan-review`, the extra round the rule exists to avoid. The session logs a `review-ledger.sh` DEFER row for the mislabel, with the defer criterion chosen then, so the PR body's "Deferred review findings" block records it. `[unverified]` It is the session's proposal for the review's drop-rule finding, and the engineer has not seen it.

**Mechanisms.**
- M1 (commit 1). Add `TRANSCRIPT_ANALYSIS_DOC_MD` and `PRIVATE_PROJECT_REDACTION_DOC_MD`. Add one row directly after `select-tests.py:702`: `(lambda p: p in (TRANSCRIPT_ANALYSIS_DOC_MD, PRIVATE_PROJECT_REDACTION_DOC_MD), (SCRIPTS_TESTS_DIR,))`. This mirrors the adjacent `:702` precedent, including selecting the whole directory for one reading file. anchors: row3, row6. Alternatives set aside:
  - Adding `SCRIPTS_TESTS_DIR` to the docs/ blanket at `:703` would run the whole scripts tree on every docs/ edit, when only two docs need it.
  - A frozenset that also folds in `TRANSCRIPT_ANALYSIS_ARCHITECTURE_DOC_MD` would reshape `:702` and its tests at `:1070` and `:1874` for no enforcement gain. Completeness comes from `TestCrossDomainReadCompleteness`, not from the table's shape.
- M2 (commit 1). Move the three reads up into two module-level, unannotated constants in `test_transcript_review_rounds.py`. The corrected selection tests (M3) already fail if M1's row is deleted. M2's own value is the coverage invariant: `TestCrossDomainReadCompleteness` then fails if the selected target stops containing the reading test file, including for a later inline reader that follows the same shape. Extending the scanner into function bodies is a heavier change to a shared test helper and is out of scope. anchors: row4, row5.
- M3 (commit 1). Correct the `:1055` test and add a sibling test for `docs/private-project-redaction.md`. Both keep the literal path, so drift in a constant still fails. anchors: row7.
- M4 (commit 2). Route both readers through `corpus._parse_jsonl_records`. anchors: row9, row10. Three lighter fixes each fail:
  - `open(..., errors="replace")`: a bad byte inside a JSON string decodes to U+FFFD and the record parses. The reader keeps a corrupted record where the helper skips the line, so it would diverge from every other reader under `transcript_analysis/`.
  - `errors="surrogateescape"`: the same divergence, with lone surrogates inside kept strings.
  - Catching `UnicodeDecodeError` around the loop: the file iterator raises it, so catching it ends the walk. A text-mode wrapper decodes per chunk, so on a small file the valid lines before the bad byte are lost too.
- M5 (commit 2). The helper's `None` maps to each reader's existing unreadable return: `_ReviewerTranscriptScan("", [], [], "", True, frozenset())` and `None, 0.0, {}, None, 0, 0, set()`. `[]` takes the readable-empty path, which is how both readers already treat a file with no parseable JSON. Remove both now-unused `json` imports. anchors: row9, row11, row13.
- M6 (commit 3). Change only the label text. Key names (`spend_reviewer_only`, `total_agent_dollars`) never print, so they stay. anchors: row1, row17, row18.
- M7 (all commits). Each commit adds its own bullet at the top of `[Unreleased]` → the first `### Changed`. Dropping commit 3 then drops its bullet cleanly. anchors: row1, row22.

## Critical files

Paths are relative to the worktree root. This list is the change's file limit. The plan file `.claude/plans/pooled-followups.md` also ships in the PR, committed by `/plan-it` Step 7.

**Dispatch split.** Use three `code-writer` dispatches, run in sequence. After each one, the session runs `/code-review`, the commit gate, and the commit before starting the next. Splitting works because each dispatch's prompt needs only its own commit's context. They must not run in parallel: `test_transcript_review_rounds.py` is edited by dispatches 1 and 3, and `CHANGELOG.md` by all three. Keeping commit 3 in its own dispatch also keeps it droppable as a unit (row 25).

Line numbers below come from the merged tree before any commit here. Earlier dispatches shift them, so each dispatch prompt tells the writer to locate every edit by the quoted text or symbol and to re-grep before editing. Every command below uses the `../../../.venv/bin/` form that this linked worktree needs.

**Dispatch 1 (commit 1: select-tests mapping).**
- `claude/.claude/scripts/select-tests.py`
  - After `:267`, add `TRANSCRIPT_ANALYSIS_DOC_MD = "docs/transcript-analysis.md"` and `PRIVATE_PROJECT_REDACTION_DOC_MD = "docs/private-project-redaction.md"`. Put one comment above them: `# test_transcript_review_rounds.py (SCRIPTS_TESTS_DIR) reads each of these files by path.`
  - Add M1's row after `:702`.
  - In the rule-table comment, after `:626-628`, add: `# TRANSCRIPT_ANALYSIS_DOC_MD and PRIVATE_PROJECT_REDACTION_DOC_MD: see their own comment above for citation.`
- `claude/.claude/scripts/tests/test_select_tests.py`
  - Rename the `:1055` test to `test_transcript_analysis_doc_md_change_selects_scripts_hooks_and_skills_tests`.
  - Rewrite its docstring to cite `test_transcript_review_rounds.py` (SCRIPTS_TESTS_DIR) plus the docs/ blanket.
  - Change its expected set to {SCRIPTS_TESTS_DIR, HOOKS_TESTS_DIR, SKILLS_TESTS_DIR}.
  - Add `test_private_project_redaction_doc_md_change_selects_scripts_hooks_and_skills_tests`, written the same way with the literal path.
  - Add both new constants to `_EXACT_MATCH_LITERAL_PATH_CONSTANTS` after `:1874`.
- `claude/.claude/scripts/tests/test_transcript_review_rounds.py`
  - Near `:29-31`, add `_TRANSCRIPT_ANALYSIS_DOC = REPO_ROOT / "docs" / "transcript-analysis.md"` and `_PRIVATE_PROJECT_REDACTION_DOC = REPO_ROOT / "docs" / "private-project-redaction.md"`. Use plain assignments with no type annotation (row 4). Put one comment above them: `# Module-level so test_select_tests.py's TestCrossDomainReadCompleteness sees these reads.`
  - Use the constants at `:2705`, `:2758`, `:2767`.
- `CHANGELOG.md`: add a bullet saying that a `docs/transcript-analysis.md` or `docs/private-project-redaction.md` change now also selects `claude/.claude/scripts/tests/`, because `test_transcript_review_rounds.py` reads both by path.
- Dispatch verification: `../../../.venv/bin/pytest claude/.claude/scripts/tests/test_select_tests.py claude/.claude/scripts/tests/test_transcript_review_rounds.py`, then `../../../.venv/bin/ruff check claude/.claude/ claude-skills/`. Run the red step first (see Verification).

**Dispatch 2 (commit 2: decode-safe readers).**
- `claude/.claude/scripts/transcript_analysis/reviewer_yield.py` (`:93-141`):
  - Replace the open/loop with `records = corpus._parse_jsonl_records(jsonl_path)`.
  - If `records is None`, return the existing read-error scan.
  - Otherwise `for rec in records:` with the current body unchanged.
  - Delete `import json`.
- `claude/.claude/scripts/transcript_analysis/subagent_mix.py` (`:623-632`):
  - Replace the `records = []` init and the loop with the helper call.
  - If `records is None`, return the existing empty tuple.
  - Keep the `is_file()` precheck.
  - Delete `import json`.
- `claude/.claude/scripts/tests/test_transcript_reviewer_yield.py`: add two tests that call `_mod.reviewer_yield._scan_reviewer_transcript` directly.
  - The file holds a valid assistant record with text, then `b"\xff\xfe\x00\x01"` on its own line, then a second assistant record with different text. Assert `read_error is False` and `last_assistant_text` equals the second record's text, which proves the walk continued past the bad line.
  - A file of only `b"\xff\xfe\x00\x01"` scans to `read_error is False` with every other field empty.
- `claude/.claude/scripts/tests/test_transcript_analysis.py`, beside the existing `_parse_jsonl_records` decode tests: add one test with an invalid byte inside an otherwise valid record's JSON string, for example a multi-byte character cut mid-sequence. Assert that record is dropped and its neighbours on both sides survive. This pins the skip-the-record policy once, at the helper layer, so a later switch to a lossy decode fails. Do not repeat it per reader.
- `claude/.claude/scripts/tests/test_transcript_subagent_mix_dollars.py`:
  - In a new class `TestDispatchUsageSummaryUndecodableLines`, since the existing class's contract is dedup-before-pricing: write two `_two_block_run` runs with distinct `request_id`s around a non-UTF-8 line. Assert the result equals `_dispatch_usage_summary` on the same two runs written without the bad line. Also assert the dollars equal 2 x `per_dispatch`, so two empty summaries cannot satisfy the test.
  - In the same class, add a test for a readable transcript that decodes to nothing (only a bad line). Assert `observed_bucket == "other"` and `0.0` dollars, not the `(None, 0.0, {}, None, 0, 0, set())` unreadable tuple. This pins M5's `[]` mapping for `_dispatch_usage_summary`.
  - Re-target the patch in `test_file_that_fails_to_open_returns_empty_summary` (its `open` patch near `:510`) to `monkeypatch.setattr(_mod.subagent_mix.corpus, "open", _open_failing, raising=False)` (row 12). Replace the comment above it with `# Shadows the builtin only inside corpus, which opens the transcript.` Update the test's docstring, which still says the reader handles the `OSError` itself.
- `CHANGELOG.md`: add a bullet saying that `reviewer-yield`, `cost-ledger --record`, and `subagent-mix` now skip a non-UTF-8 line in a dispatched subagent's transcript, as the other transcript readers do, where they previously aborted the run. The bullet also says a transcript with no decodable line reads as empty.
- Dispatch verification: `../../../.venv/bin/pytest claude/.claude/scripts/tests/test_transcript_analysis.py claude/.claude/scripts/tests/test_transcript_reviewer_yield.py claude/.claude/scripts/tests/test_transcript_subagent_mix.py claude/.claude/scripts/tests/test_transcript_subagent_mix_dollars.py`, then `../../../.venv/bin/ruff check claude/.claude/ claude-skills/`. Ruff catches a leftover `json` import.

**Dispatch 3 (commit 3: footer label; drop it whole if it draws a second round of wording comments).**
- `claude/.claude/scripts/transcript_analysis/review_rounds.py`: change `:1151` (docstring) and `:1363` (f-string) from `Reviewer-dispatch dollars` to `Subagent-dispatch dollars`. The two prefixes are the same length, so no line re-wraps.
- `docs/transcript-analysis.md`: rename the label token at `:1158` and the sample line at `:1179`. Add one clause to the `:1158` sentence: the footer counts every Agent/Task dispatch spawned in a round window, nested dispatches included, not only reviewers. This sentence is the canonical home for that fact, and the CHANGELOG bullet defers to it. The rest of the `:1158` prose ("the subagent-only component of round dollars") is already accurate and stays.
- `claude/.claude/scripts/tests/test_transcript_review_rounds.py`
  - Update the assertions at `:1149`, `:1212`, `:1216`.
  - In the `:1159` docstring, change "Reviewer-dispatch-dollars figure" to "Subagent-dispatch-dollars figure".
  - Change "reviewer-dispatch" to "subagent-dispatch" at `:1780` and `:2568`.
  - Change "reviewer-only" to "subagent-dispatch-only" at `:2367`.
  - Leave `:2604` and `:3655` unchanged (row 21).
  - Add one test to `TestCmdReviewRoundCost`. A single-root branch has a round window containing `_agent_use("a1", "code-writer")`, with a paired `_write_subagent_dispatch(..., agent_type="code-writer")` priced inside the window. Assert `Subagent-dispatch dollars: {render._pct_of(agent, branch)} of branch dollars, inside round windows`, and assert the expected percent string differs from `"0.0%"`, which is what a reviewer-only filter would print. This pins that a non-reviewer dispatch counts, which is the fact the rename rests on.
- `CHANGELOG.md`: add a bullet saying that `review-round-cost`'s non-pooled footer line `Reviewer-dispatch dollars:` now reads `Subagent-dispatch dollars:`. Keep the old label string verbatim in the bullet, since the case-study docs quote it. The figure is unchanged: it sums the priced dollars of every Agent/Task dispatch spawned in a round window, nested dispatches included, whatever the agent type. Dangling dispatches and unpriced turns add $0 and show on their own lines. State the fact once in `docs/transcript-analysis.md`; the bullet points there.
- Dispatch verification: `../../../.venv/bin/pytest claude/.claude/scripts/tests/test_transcript_review_rounds.py`, then `../../../.venv/bin/ruff check claude/.claude/ claude-skills/`.

**Reuse:**
- `corpus._parse_jsonl_records`, already called across modules at `review_rounds.py:233`.
- conftest helpers `_write_jsonl`, `_write_subagent_dispatch`, `_agent_use`, `_priced`.
- `_two_block_run` (`test_transcript_subagent_mix_dollars.py:352`).
- `_review_round_cost_args`.
- `render._pct_of` for expected percents.

## Verification

- **Commit 1, red then green.** First apply only M2's move to module-level constants. Then `../../../.venv/bin/pytest claude/.claude/scripts/tests/test_select_tests.py -k test_every_resolved_path_selects_a_target_covering_its_reading_test` must fail, and its output must name both docs against `test_transcript_review_rounds.py`. A command-not-found exit does not count as the red. That shows the enforcement is live. After M1 the same command passes. Then run the dispatch-1 command.
- **Commit 2, red then green.** Write the new reader tests before the reader edit, and run them with `PYTHONUTF8=1`. Their output must show `UnicodeDecodeError` (row 16). They pass after M4/M5. The helper-layer mid-record test pins behavior the helper already has, so it is green both before and after. Then run the dispatch-2 command.
- **Commit 3.**
  - Grep `Reviewer-dispatch dollars` across the worktree, excluding `.claude/plans/` and `agent-reviews/`. It must match only `docs/case-studies/review-loop-cost-forensics.md` and the commit-3 bullet in `CHANGELOG.md`, which keeps the old label on purpose.
  - Grep `reviewer-only|reviewer-dispatch|Reviewer-dispatch` in `claude/.claude/scripts/tests/test_transcript_review_rounds.py`. It must match nothing.
  - Then run the dispatch-3 command.
- **Per-commit gate (the session, before each commit).** Run `../../../.venv/bin/python3 claude/.claude/scripts/select-tests.py` and `../../../.venv/bin/ruff check claude/.claude/ claude-skills/`. From commit 1 onward, `select-tests.py` runs the full suite on its own (row 8). That is the project's documented command for this case, not a widening by hand. The dispatches run only their targeted files, so the full suite isn't run twice per commit. No ShellCheck: no shell file changes.
- **Branch.** `/ready-for-review` runs `select-tests.py` again, and CI runs the full suite on push (G2).

## Out of scope

- **Triaged out (row 1):**
  - The OVERRIDDEN ROOTS marker under a HOME redirect.
  - A single-root silent-skip pin test.
  - The claim in the redaction doc at `:100-109`.
  - A CI guard against root skipping.
  - Literal `""` test cases.
  - The old label in the shipped plan sample.
  - The 8-branch-unit derivation.
  - The flaky sed shim (PR A).
  - Replacing the figures in `docs/cost-levers-considered.md` (a separate policy-gated stream).
- **Dated records, left unchanged:** `docs/case-studies/review-loop-cost-forensics.md` (`:110`, `:146`), `docs/cost-levers-considered.md:624`, and `.claude/plans/review-round-cost-pooled.md`. Nothing under `.claude/plans/` is edited except this plan's own file.
- **`CHANGELOG.md:154`'s clause "No test in the suite reads any of these by path".** It records a past change, so it is read-only (CLAUDE.md, Scope discipline, Axis 3). Commit 1's new bullet supersedes it.
- **Extending `TestCrossDomainReadCompleteness` to function-local reads.** This is a shared test-helper change. M2 covers the two known reads.
- **A structural guard against text-mode transcript opens.** Row 10 found only these two remaining loops under `claude/.claude/scripts/transcript_analysis/`.
- **`evals/measure_subagent_model_resolution.py:422-436` and the `errors="replace"` read in `transcript-analysis.py:4141-4144`.** The first deliberately mirrors the corpus tool without importing it, and the second is deliberately lossy (row 10).
- **A comment at `review_rounds.py:720` saying `spend_reviewer_only` counts every dispatch.** The key name stays (M6), and the new footer test pins the behavior.
- **A single-file `select-tests.py` target for the two docs.** The `:702` precedent selects the whole directory, and the directory also covers a later inline reader.
- **Renaming `spend_reviewer_only`, `total_agent_dollars`, or the per-round `agent $` column.** None of them is the mislabeled printed text.
- **Rewording `docs/transcript-analysis.md:1158` beyond the label token.** The surrounding prose is already accurate.
