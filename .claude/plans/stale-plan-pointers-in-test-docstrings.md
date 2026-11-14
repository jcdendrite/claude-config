# State the fact in place of stale plan pointers in test docstrings

## Context

Goal: make each docstring and comment in the Python test files stand alone, by replacing pointers to a plan (`the plan's ...`, `ledger row N`, `Verification item N`, `.claude/plans/<name>.md`) with the fact the pointer stood for, or deleting the pointer where the fact is already stated beside it.

Ask: "let's go with the architect's order" — the engineer's reply, in the prior session, to the `plan-architect` (`MODE=consult`) ordering of the remaining GH-1432 follow-ups. Item (a) of that order is this change. On scope, the engineer selected the label "Test files, all shapes (Recommended)" in answer to how wide this PR should go.

Why now: PR 1462 merged and the follow-up order was accepted. #1118, #1119, #1138 and #1157 were read this session and none tracks this item.

A plan file is a preserved record, so a durable docstring that defers to it breaks for any reader who has not read the plan, and breaks silently when a plan is renamed. CLAUDE.md §Durable text bars PR-defined terminology and requires a comment to survive the PR description being lost.

Scope, from a read-only discovery scan this session and the plan author's wider greps (heuristic regexes, hits read in context):

- Shape A, unanchored phrasing such as `the plan's own framing`, `ledger row 12`, `Verification item 4:` and `must-not-regress #3`.
- Shape B, `.claude/plans/<name>.md` citations in prose. All cited plans still exist in the repo.
- The discovery scan read only `claude/.claude/` and found 45 test files. The wider greps put the scope at 57 test files across `claude/.claude/hooks/tests/`, `claude/.claude/scripts/tests/`, `claude/.claude/tests/`, `claude-skills/skills/tests/` and `evals/`.
- About 20 sites carry a fact only the plan states. They need the fact written in place rather than the pointer deleted.
- Functional mentions are out of scope: `.claude/plans/` paths used as fixture data or gate behaviour, `ledger row` as the review or cost ledger data model, and fixture row indexes.

Intended outcome: no docstring, comment or assertion message in the test files defers to a plan for its meaning. Code is unchanged apart from four named assertion-message strings that repeat a pointer being removed from a docstring.

## Approach

Each plan pointer in the Python test trees gets one of three dispositions. It is deleted where the fact it stands for already sits beside it. It is repointed where a durable file states the fact. It is replaced by the fact itself where the plan was the fact's only home. The whole paragraph holding the pointer is rewritten, so it also loses prior-state framing. A one-off scan script defines the site set: run at the merge-base it must hit exactly the files below, and run after the edits it must print nothing. An AST comparison proves that no code changed apart from four named assertion messages. Three parallel `code-writer` dispatches take disjoint file sets, and one sequenced dispatch then runs the whole-diff checks. The scoped `select-tests.py` run is left to `/ready-for-review`.

A bare `:N` is a line, at `<base>` (the SHA `git merge-base HEAD origin/main` prints), of the file its bullet names. Range starts are approximate. The scan's base run (Verification step 1) is the authoritative site list.

**The site set** (rows 3, 4, 8, 9). The discovery scan read `claude/.claude/` only, with a narrower regex set. This session's greps found the same shapes in 14 more test files:
- six in `claude/.claude/hooks/tests/`;
- four in `claude/.claude/scripts/tests/`;
- `claude/.claude/tests/test_statusline_command.py`;
- `claude-skills/skills/tests/test_skills.py`;
- two `evals/` test files.

The shapes it missed are `rowN` with no space (`row4`, `row2b`), `Mechanism N`, `plan M3` and `plan B11`, bare ledger IDs (`M1`, `M9`), `<name> plan`, and `assumption ledger`. Two files the discovery listed carry functional mentions only (row 4), so 57 test files are in scope.

**Dispositions** (rows 5, 10). For each site the scan reports:
1. **Delete** the pointer when the sentence or the code beside it already states the fact. That is the default for `ledger row N`, `rowNx`, `Row N's`, `Verification item N`, `Mechanism N`, `M<n>`, `plan B11`, `Phase 1's` and `must-not-regress #N` labels. It is also the default for a "see `.claude/plans/<name>.md`" sentence whose paragraph already explains what it defers to.
2. **Repoint** to the durable file named in the table below when that file states the fact.
3. **State** the fact in place, in one sentence, when the plan was its only home. The table below gives each such fact with its source.

If a deletion leaves a sentence that no longer says what the label stood for, the dispatch reads the cited plan and states the fact in one sentence. It lists the site and the plan line in its return.

**Rewrite unit and shape** (G1, G4, G5). The unit is the docstring paragraph or `#` comment block holding the pointer. Inside that unit:

Remove:
- prior-state framing (`now ships`, `the new loop`, `the removed --yes flag`, `the initial implementation`, `the fix`, `the O_EXCL rewrite`, `before row4 this never parsed`);
- PR narration (`flagged to the dispatching session`);
- labels the file does not define (`Bug 3 headline:` at `test_post_crash_sessions.py:675`).

Keep:
- paragraphs that hold no pointer, untouched;
- every line within 130 columns;
- each docstring as its node's first statement.

Never:
- reflow a paragraph: edit only the lines the change touches, and leave a shortened line short;
- turn a `#` comment into code or code into a `#` comment;
- write a tracker ID followed by `Phase` or `Step` and a number;
- write a numbered or lettered label (`row 4`, `item 2`, `M3`, `Phase 1`) that the same file does not define.

**State and repoint table** (rows 10-12). Each entry is a fact to write, not prescribed wording (`plan-it` Step 5's comment-content discipline). The plan filenames and line numbers in parentheses are sources for the dispatch to check the fact against. They are never written into a test file, because the scan cannot see a bare plan filename (row 16).

`claude/.claude/hooks/tests/`:
- `test_advance_past_commit_stall.py:39-44`: the quotes are verbatim from issue #526. The first is exactly 64 characters. A byte-count tail slice (`${msg: -600}`) returns empty for any message under 600 characters, so this quote pins final-sentence extraction (`commit-stall-block.md:586-593`). `:51`: the modal commit ask the hook must fire on, and an accepted miss (see `CORPUS`).
- `test_hook_alignment.py:616-620` and `:755-758`: keep "That live-session verification lives outside pytest" and delete the parenthetical. `:773-787`: the tools named must stay available. `ListAgents` and `TaskOutput` are guarded because the live-session check of the deny required them to remain available. `Agent` is guarded because the misfire most often comes while a backgrounded `Agent` dispatch runs, and the same check required it to remain available (`prevent-non-loop-schedulewakeup-calls.md:86`, `:199`).
- `test_hook_alignment.py:3317`: delete `GH-480 ledger row 4a: `. `:3331-3344`: `require-ready-for-review.sh` allows a push when `gh` is absent from PATH. `PR_NUMBER` comes back empty, as for "no open PR", which the hook documents as fail-open. So it does not fail closed the way the `sha256sum` gates do, and the test pins that deliberate behavior. The paragraph drops the plan's assumption narrative and its closing sentence about flagging the result and GH-480's scope.
- `test_nudge_transcript_toolkit.py:5-7`: a glob of that shape covers one config root, while every `transcript-analysis.py` subcommand unions every root `~/.claude/transcript-config-dirs` declares (`transcript-parsing-guardrails.md:9-15`).
- `test_lib_worktree_collision_guard.py`:
  - `:287-288`: delete the parenthetical, because the sentence already names bash's tail-call exec optimization (row 12).
  - `:357-367`: keep "confirmed non-atomic at git's own source", and drop the CI-flake history and the "rewrite" and "the fix" framing.
  - `:413-421`: the race is an evict-then-relock racing a second evictor, and the claim file's `O_EXCL` create closes it.
  - `:1263-1268`: the claim file's `O_EXCL` create lets only one evictor reclaim a dead lock, so an evict-then-relock cannot race a second evictor (`auto-clear-dead-worktree-locks.md:38`).
  - `:527-535` and `:1390`: delete `mechanism 2 of ...` and `mechanism 3 of ...`, because "post-write verification reread" and "main-tree precondition check" name them.
  - `:527-535`: also drop "without this test it was only asserted in the plan's prose".
- `test_marker_script.py:2734-2735`, `test_require_code_review.py:680-682`, `test_require_plan_review.py:1294-1296` and `test_require_ready_for_review.py:2801-2803`: a marker another account writes under its own config directory, in the same `$HOME`, never releases this account's gate (`honor-claude-config-dir.md:24-33`, `:206`).
- `test_enforce_marker_script_shape.py:768-769`: these forms stay denied because `>` in that tail class blocks every redirect around `git commit` (`marker-sh-allow-devnull-suffix.md:62-67`).
- `test_design_decision_files.py:439-441`: a static slug table has no row for a section numbered after the table was written.

`claude/.claude/scripts/tests/`:
- `test_transcript_cache_rebuild_switch_delta.py:276-279`: per-dispatch dispersion uses each subagent group's own X and W5m because it bounds what a selective, per-dispatch TTL switch could save. A pooled denominator would corrupt that bound (`subagent-idle-gap-cache-rebuild-split.md:52`, `:162`).
- `test_transcript_reviewer_yield.py:1494-1495`: the resolved-scope header prints the `--projects` value (`transcript_analysis/scope.py:676`), a channel outside this test.
- `test_transcript_scope.py:466-468`: the slug mapping `[/.]→-` is not injective, so `/a/b.c` and `/a/b/c` collide, and the test pins that accepted residual risk.
- `test_select_tests.py:3064-3067`: the printed scope line must match the pytest argv actually run, so a containment collision cannot go under-collected while stderr claims the wider scope.
- Repoints:
  - `test_transcript_handoff_signal_response.py:577-578` to `_startup_burn_benchmark`'s session-count-weighted average, which its docstring states (`transcript_analysis/handoff_signal_response.py:388-391`);
  - `test_transcript_rearm_backtest_nudge_log.py:15-16` to `_nudge_conversion_from_log`'s four conversion buckets;
  - `test_transcript_cache_rebuild.py:80-83` to the classification cases its test's docstring lists (`:88-104`).

Other trees:
- Repoint `claude/.claude/tests/test_pytest_collection_config.py:481-482` to `.claude/rules/test-tree-packaging.md`, which keeps `claude/.claude/tests/` unpackaged.

**Assertion messages** (rows 6, 7). Four string literals carry the same pointers. The two in `test_hook_alignment.py` restate the docstring that is being rewritten, so leaving them would make each failure message contradict its docstring. Each value changes exactly as follows, and no other string literal or code changes:
1. `test_advance_past_commit_stall.py:49`: `the plan's own 64-char quote` becomes `the issue's own 64-char quote`.
2. `test_scaled_timeout_shim.py:302`: ` (ledger row 4)` is deleted.
3. `test_hook_alignment.py:801-806`: `the plan's pre-implementation gate (Verification step 1 check 5) requires` becomes `the live-session check of the deny requires`.
4. `test_hook_alignment.py:807-810`: `the plan's Context section names it as the dispatch the ScheduleWakeup misfire follows` becomes `the ScheduleWakeup misfire most often comes while a backgrounded Agent dispatch runs`.

**Exempt functional mentions** (rows 4, 9). The scan skips these, and no edit touches them. Each is fixture data, a product string, a variable, or a vocabulary that has no other home:
- `hooks/tests/_cursor_payloads.py:57`. The phase numbers are the `phase` values of `scripts/cursor-adapter/parity-exceptions.json`, and `cursor-adapter.md` alone defines them. `docs/scripts.md:520` cites the same plan.
- `hooks/tests/test_agent_roster.py:857` and `hooks/tests/test_require_plan_review.py:930`. "the plan" there is the plan file under test.
- `hooks/tests/test_log_reviewer_round.py:584` (a fixture prompt) and `scripts/tests/test_transcript_pr_cost.py:993` (a fixture path).
- `evals/test_review_bench_cli.py`'s `row N` product error strings.
- `scripts/tests/test_transcript_cost_counts.py:702` (an error string).
- `scripts/tests/test_transcript_handoff_signal_response.py:453` and `:458` (fixture row indexes).
- `scripts/tests/test_cleanup_merged_branches.py:2566-2569` (fixture PR rows).
- `scripts/tests/test_transcript_pr_cost_ledger.py`'s `row1` and `row2` variables.
- `evals/test_measure_explore_recall.py:1228`, `:2004` and `:2224` (`for entry in plan`).
- `claude-skills/skills/tests/test_skills.py:1092`. "plan-it writes there in plan mode" names the skill plan-it, and the scan matches across the line break.
- `scripts/tests/test_relocate_claude_config.py:114`. The `(row H)` comment sits inside an f-string that builds a fake `claude` shim, so it is string data and not a comment of the test file. Editing it would change a string constant, and no planned literal substitution covers it.
- `hooks/tests/test_ticket_reference_discipline.py`'s `Phase N` fixtures.
- `evals/test_measure_subagent_model_resolution.py:98` and `:1033`. They name `M3` and `M8`, labels that module's own section comments define.

**Alternatives set aside:**
- Deleting every pointer without stating facts loses the facts in the table, which no file in the repository states.
- Repointing every pointer to a durable doc needs a doc entry for each fact. That widens the change into `docs/` and creates a second home for facts the test already holds.
- Rewriting only the sentence that carries the pointer leaves a "now ships" or "the fix" clause next to it. The edited paragraph would then still fail the §Durable text self-test the Context cites.
- Leaving the assertion messages makes the rewritten docstrings and their own failure messages disagree. The Context's "code unchanged outside docstrings and comments" therefore gets four named exceptions (see Verification step 4).
- One `code-writer` for all of it puts about 150 read-and-edit sites in one context. Splitting further makes each extra dispatch reread this plan to take on fewer sites.
- Running `select-tests.py` inside Dispatch D duplicates the run `/ready-for-review` makes, and a preview shows it selects nearly the whole suite (row 13).

**Verification shape** (rows 5, 7, 13). Every edit is to a docstring, a comment or one of four literals, so the AST check is the correctness proof. It shows that each file's code equals the base apart from the four literal substitutions. The scan proves completeness: its base run hits exactly the 57 files, and its final run hits none. The targeted pytest run (step 7) and the collection run (step 8) add what the AST check cannot see:
- every edited file still imports under the real conftests;
- the tests that read test files as data still pass, namely `test_ticket_reference_discipline.py`, `test_scaled_timeout_shim.py`, `test_select_tests.py` and `test_pytest_collection_config.py`.

The repo-mandated scoped `select-tests.py` run (G3) is deferred to `/ready-for-review` (step 10). The scan proves absence of 13 heuristic shapes, not of every plan reference. The roughly 20 facts written in place have no mechanical check, so the PR body's list of dispatch-stated facts is the control for them.

**Assumption ledger.**

**Root:** Docstrings, `#` comments and four assertion messages in 57 tracked Python test files defer to a plan for their meaning. They do it through `.claude/plans/<name>.md` paths, "the plan's ..." phrasing, `<name> plan` citations, and labels only a plan defines (`ledger row N`, `rowNx`, `Verification item N`, `Mechanism N`, `plan M3`, `M<n>`, `Phase N`, `must-not-regress #N`). A plan is a preserved record, so each pointer fails a reader who has not read it. This change states, repoints or deletes each pointer and changes no other code. `[verified: the greps and reads cited in rows 3-12]`

**Givens:**
- G1. CLAUDE.md §Durable text bars PR-defined terminology and "used to be X" framing in comments and docstrings. Reason: CLAUDE.md owns the rule. `[verified: CLAUDE.md § "Durable text"]`
- G2. Plan files are preserved records and stay unedited. Reason: CLAUDE.md "Scope discipline" Axis 3 owns it. `[verified: CLAUDE.md "Scope discipline"]`
- G3. Agents run `select-tests.py`, not the full suite, unless it widens on its own. A scoped run here takes 40-90 minutes and has known environmental failures. Reason: the rule is the repo CLAUDE.md's, and the machine's environment lies outside the repository. `[verified: CLAUDE.md "Commands" for the rule; unverified: the timing and failures, relayed by the dispatching session]`
- G4. `test_ticket_reference_discipline.py` fails any tracked `.py` under `claude/`, `claude-skills/` and `plugins/` whose comment or triple-quoted string holds a tracker ID followed by `Phase` or `Step` and a number. Reason: that test owns the rule. `[verified: read of :151-195]`
- G5. ruff runs with `line-length = 130` and the `E`, `F`, `B`, `I`, `UP` and `SIM` rules. Reason: `pyproject.toml` owns it. `[verified: staff-sdet's read of pyproject.toml, this review round]`
- G6. `require-diff-size-prompt.sh` stops a commit at 400 or more changed lines until the engineer answers `Proceed anyway`. Reason: the hook owns the threshold. `[verified: require-diff-size-prompt.sh:5-6]`

**Rows:**
1. On scope the engineer selected "Test files, all shapes (Recommended)". `[engineer-verified: "Test files, all shapes (Recommended)"]`
2. That option's description (roughly 150-250 changed lines, production modules deferred to a later PR) was the dispatching session's proposal, not the engineer's words. `[unverified: relayed by the dispatching session as its own text]` Shown the measured size (about 550 changed lines, 57 files), the engineer chose to ship it as one PR. `[engineer-verified: "One PR (Recommended)"]`
3. The same pointer shapes appear in 14 test files the discovery scan did not list. In `hooks/tests/` they are `test_ask_new_dependency_disclosure.py`, `test_deny_no_op_dispatch.py`, `test_deny_repo_relocation.py`, `test_install_sh_repo_relocation_support.py`, `test_lib_reviewer_round_state.py` and `test_nudge_handoff_near_context_cap.py`. In `scripts/tests/` they are `test_analyze_context.py`, `test_relocate_claude_config.py`, `test_transcript_cost_ledger.py` and `test_transcript_pr_cost_gh.py`. The rest are `claude/.claude/tests/test_statusline_command.py`, `claude-skills/skills/tests/test_skills.py`, `evals/test_measure_explore_recall.py` and `evals/test_measure_subagent_model_resolution.py`. `[verified: greps this session over every tests/ tree and evals/test_*.py]`
4. Two files the discovery listed hold no pointer. At `_cursor_payloads.py:57`, "phase" is the `phase` field of `parity-exceptions.json`, whose values only `cursor-adapter.md` defines. At `test_agent_roster.py:857`, "the plan's Context" is the plan file plan-architect edits. `[verified: grep for pending-phase; read of test_agent_roster.py:854-869]`
5. No test reads a test file's docstring through `__doc__`, `get_docstring` or `getdoc(`: every such hit reads a production module or a tool's own docstring. Two readers touch docstring text another way and neither is affected by this diff: `test_scaled_timeout_shim.py` calls `inspect.getsource` on functions in `test_lib.py` and `test_lib_capped_for.py`, which are not among the 57 files, and `test_shell_lib_function_liveness.py` counts docstring mentions as references, with no shell-lib function referenced only inside docstrings of the 57 files. `[verified: grep over claude/, claude-skills/, plugins/ and evals/; staff-sdet's replication of the liveness corpus rule]`
6. The four literals in "Assertion messages" carry the pointers named there. The two in `test_hook_alignment.py` are the `gate_required_available` and `dispatch_trigger` values, which restate the docstring at `:779-787`. `[verified: read of :770-821; read of test_advance_past_commit_stall.py:49; grep of test_scaled_timeout_shim.py]`
7. The AST check in `.claude/plans/transcript-analysis-hygiene.md:155-168` blanks the first-statement string of each Module, ClassDef, FunctionDef and AsyncFunctionDef and compares `ast.dump`, which carries no comments. A `#` edit therefore never changes its output, and any other string or code edit always does. `[verified: read]`
8. Every plan name the scan's `plan-path` and `named-plan` patterns match in these trees exists under `.claude/plans/`, except fixture names (`x.md`, `p.md`, `some-plan.md`, `review-narrative-ledger.md` and the like). `[verified: globs of 31 names]`
9. The exemptions in "Exempt functional mentions" are fixture data, product strings, variables, or a vocabulary with no other home. `[verified: read of each line]`
10. The table's facts come from the plan lines and production code each entry cites. `[verified: reads of commit-stall-block.md:584-623, :735-749; prevent-non-loop-schedulewakeup-calls.md:86, :189-203; atomic-worktree-lock-acquisition.md:100-204; auto-clear-dead-worktree-locks.md:31-41; transcript-parsing-guardrails.md:1-82; subagent-idle-gap-cache-rebuild-split.md:52-61, :162; honor-claude-config-dir.md:20-33, :206; marker-sh-allow-devnull-suffix.md:62-81; handoff_signal_response.py:387-402; scope.py:676; .claude/rules/test-tree-packaging.md]`
11. Issue #526 is the issue `commit-stall-block.md` names as the source of the three quotes. `[verified: commit-stall-block.md:739, :618-619]`
12. The demonstration `atomic-worktree-lock-acquisition.md:193-194` gives for bash's tail-call exec collapse runs `bash -c "echo inner pid=$$"` as a double-quoted word, so the outer shell expands both `$$` and the identical output shows nothing. `:287-288`'s parenthetical is therefore deleted, not replaced by that demonstration. `[verified: read; bash expands $$ inside double quotes before the inner bash starts]`
13. `select-tests.py` picks nearly the whole suite for this diff: all of the four `tests/` trees and 8 `evals/` files. `[verified: staff-sdet's preview of select_pytest_targets on the 57 paths plus the plan, this review round]`
14. The diff lands near 550 changed lines. `[unverified: a per-site estimate; Verification step 9 measures it]`
15. A grep for `this (PR|diff|change)` over the test trees returns about 30 lines, several of them fixture or product text. A grep for `(Bug|Fix|Finding) [0-9]` returns 5. `[verified: greps]`
16. The scan misses a bare plan name that has neither the `.claude/plans/` prefix nor a following "plan". `[unverified]`
17. Shown `/plan-review`'s size gate for the projected 550 changed lines, the engineer chose to proceed as one PR. `[engineer-verified: "Proceed anyway"]`
18. Asked whether the Ask line's prior-session quote is theirs and covers this change, the engineer confirmed it. `[engineer-verified: "Yes, that is mine"]`

**Mechanisms:**
- M1: the three dispositions and the paragraph rewrite unit. `anchors: root, row1, row5`.
- M2: the state and repoint table. `anchors: row10, row11, row12`.
- M3: the four literal edits. `anchors: row6, row7`.
- M4: the one-off scan script, with its base run as the completeness and non-vacuity proof. `anchors: row3, row8, row9, row16`. It is heavier than a list of greps. Lighter primitives checked:
  - `git grep -nE` per pattern. ERE has no lookahead, and grep cannot apply the plan-existence filter or the exemptions, so its output never comes back empty for a mechanical pass.
  - The discovery scan's own output. It is a one-time snapshot outside the repository and missed 14 files (row 3).
  - `/code-review`'s comment-discipline pass. It reads the diff, so it cannot find a site the diff never touched.
- M5: the AST check, extended with exact literal substitutions on the base side. `anchors: row5, row6, row7`. Lighter primitives checked:
  - The hygiene check unchanged. It fails on the four literals by design.
  - A `git diff -U0` filter for comment-only lines. It cannot tell a docstring line from a string literal or code.
- M6: the exemptions. `anchors: row4, row9`.
- M7: three parallel `code-writer` dispatches, then one sequenced dispatch that runs the whole-diff checks. `anchors: row3, row13, row14`. The reasons are under Critical files.

## Critical files

**Dispatch split** (M7). The file sets are disjoint, and no edit in one depends on another. The rules, the table and the exemptions live in this plan, which each dispatch reads by path, so no prompt restates them. The order is:
1. The session creates `<scratch>` with `mktemp -d` outside the repository, writes the two scripts from Verification into it verbatim, and runs Verification step 1.
2. Dispatch A, B and C run in parallel, in this worktree.
3. Dispatch D runs after A, B and C have all returned.

Constraints on Dispatch A, B and C:
- Each uses `model: sonnet` and no `isolation: "worktree"`.
- Each edits only its own files.
- Each runs Verification steps 2-6 on its files and puts each command's output and exit status in its return.
- Each lists every site where it stated a fact the table does not give, with the plan line it drew from.
- None runs `select-tests.py` or makes a commit.

Constraints on Dispatch D:
- It runs Verification steps 2-9 over the whole diff.
- It fixes any finding inside the files below by this plan's rules.
- It makes no commit.

The split rationale is under "Alternatives set aside" in Approach.

**Dispatch A: modify** (29 files, under `claude/.claude/hooks/tests/`):
- `test_advance_past_commit_stall.py` (:39-44, :49 literal, :51, :158, :159);
- `test_ask_new_dependency_disclosure.py` (:1408);
- `test_block_gh_pr_merge.py` (:225);
- `test_check_claude_md_length.py` (:866-868, :1084-1086);
- `test_check_skill_length.py` (:1349-1351);
- `test_claude_md_excludes.py` (:68);
- `test_deny_no_op_dispatch.py` (:562);
- `test_deny_private_project_refs.py` (:1733-1738);
- `test_deny_repo_relocation.py` (:140);
- `test_design_decision_files.py` (:439-441);
- `test_enforce_marker_script_shape.py` (:768-769);
- `test_hook_alignment.py` (:616-620, :755-758, :773-787, :801-810 literals, :3317, :3331-3344);
- `test_install_sh_project_scope_plugins.py` (:401-403);
- `test_install_sh_repo_relocation_support.py` (:103, :222, :239);
- `test_lib_reviewer_round_state.py` (:3);
- `test_lib_tool_argv.py` (:412);
- `test_lib_worktree_collision_guard.py` (:4-5, :52-56, :287-288, :304-309, :357-367, :413-421, :527-535, :788-790, :946-949, :1263-1268, :1390, :1526-1527, :1530, :1553, :1641);
- `test_log_reviewer_round.py` (:393);
- `test_marker_script.py` (:2734-2735, :2831-2832);
- `test_nudge_handoff_near_context_cap.py` (:2666);
- `test_nudge_transcript_toolkit.py` (:5-7);
- `test_require_architect_consult.py` (:597);
- `test_require_code_review.py` (:680-682);
- `test_require_memory_skill.py` (:309);
- `test_require_plan_review.py` (:1294-1296, :2185-2188);
- `test_require_ready_for_review.py` (:2801-2803);
- `test_require_worktree_for_file_writes.py` (:258, :367);
- `test_require_worktree_for_git_writes.py` (:1197);
- `test_scaled_timeout_shim.py` (:66-67, :109, :251, :277, :302 literal).

**Dispatch B: modify** (23 files, under `claude/.claude/scripts/tests/`):
- `test_analyze_context.py` (:217, :257, :280);
- `test_claude_enable_tool.py` (:195);
- `test_cleanup_merged_branches.py` (:182-183, :1284-1285, :1354-1355, :1596-1600);
- `test_post_crash_sessions.py` (:675-678, :1270, :1528, :2074, :2089, :2659, :4026);
- `test_pr_diff_against_base.py` (:1498);
- `test_relocate_claude_config.py` (:68, :85, :406, :482, :572; `:114` is exempt);
- `test_select_tests.py` (:72-73, :1870, :1880, :1900, :1947, :3064-3067);
- `test_transcript_cache_rebuild.py` (:80-83, :191, :232, :330, :388, :445);
- `test_transcript_cache_rebuild_attribution.py` (:52-55, :228-229, :430-431);
- `test_transcript_cache_rebuild_switch_delta.py` (:96, :142, :179, :276-279);
- `test_transcript_cache_rebuild_ttl_accumulation.py` (:472-473);
- `test_transcript_cache_rebuild_ttl_rules.py` (:196);
- `test_transcript_context_composition.py` (:3-4);
- `test_transcript_cost.py` (:156, :247, :345, :810, :843, :1589-1591, :1964, :2389, :2592, :2611, :4290);
- `test_transcript_cost_ledger.py` (:176-178);
- `test_transcript_handoff_signal_response.py` (:45, :313-314, :577-578);
- `test_transcript_handoff_signal_response_detection.py` (:283-284);
- `test_transcript_pr_cost.py` (:631-632);
- `test_transcript_pr_cost_gh.py` (:77);
- `test_transcript_rearm_backtest.py` (:189-191, :194, :213, :224);
- `test_transcript_rearm_backtest_nudge_log.py` (:15-16);
- `test_transcript_reviewer_yield.py` (:1494-1495);
- `test_transcript_scope.py` (:466-468, :541, :857).

**Dispatch C: modify** (5 files):
- `claude/.claude/tests/test_pytest_collection_config.py` (:481-482);
- `claude/.claude/tests/test_statusline_command.py` (:5, :162);
- `claude-skills/skills/tests/test_skills.py` (:8785, :8860-8861, :8935-8936, :8974-8975, :9060-9066);
- `evals/test_measure_explore_recall.py` (:1129, :1140, :1806);
- `evals/test_measure_subagent_model_resolution.py` (:10-12, :761).

**Also in the diff:** `.claude/plans/stale-plan-pointers-in-test-docstrings.md`.

**Reuse:** the AST check in `.claude/plans/transcript-analysis-hygiene.md:155-168`, extended in Verification. No new committed helper or test. Both scripts stay in `<scratch>`, uncommitted.

**PR body requirement.** The PR body contains:
- the four assertion-message edits, named as the change's only code edits;
- Verification steps 1-5 and 7-8 with their outputs;
- every site where a dispatch stated a fact the table does not give.

Projected diff: about 550 lines. About 310 change in Dispatch A's files, about 200 in Dispatch B's, and about 45 in Dispatch C's (row 14).

## Verification

Run from the worktree root. `<base>` is the SHA `git merge-base HEAD origin/main` prints, and `<scratch>` is the session's `mktemp -d` directory outside the repository. Write the two scripts below to `<scratch>/plan_pointer_scan.py` and `<scratch>/code_only_check.py` verbatim.

```python
# plan_pointer_scan.py [--rev=<sha>] [path ...]: plan-pointer hits in the test trees; exit 1 when any remains.
import os
import re
import subprocess
import sys
from pathlib import Path

SEP = r"[\s#]+"
PATTERNS = {
    "plan-path": r"\.claude/plans/([A-Za-z0-9_-]+)\.md",
    "named-plan": rf"\b([a-z0-9]+(?:-[a-z0-9]+)+){SEP}plan\b(?![-/\w])",
    "the-plans": rf"\b(?:the|this|that|pilot){SEP}plan's",
    "this-plan": rf"\bthis{SEP}plan\b(?![-/\w']|{SEP}(?:state|file|set|text|path)\b)",
    "prep-plan": rf"\b(?:per|in|see|from){SEP}(?:(?:the|this){SEP})?plan\b(?![-/\w']|{SEP}(?:file|path|set|state)\b)",
    "plan-label": rf"\b[Pp]lan{SEP}(?:mechanism{SEP}\d+|[A-Z]\d+[a-z]?)\b",
    "row-label": r"\b[Rr]ows?(?: (?:\d+[a-z]?|[A-Z])|\d+[a-z]?)\b",
    "verification-item": rf"\b[Vv]erification{SEP}(?:items?|steps?|cases?){SEP}\d",
    "must-not-regress": r"\b[Mm]ust-not-regress\b",
    "mechanism-n": rf"\b[Mm]echanism{SEP}\d",
    "assumption-ledger": rf"\b[Aa]ssumption{SEP}ledger\b",
    "phase-n": rf"\bPhase{SEP}\d",
    "ledger-id": r"\b[MG]\d\d?[a-z]?\b",
}
PLAN_NAME_PATTERNS = {"plan-path", "named-plan"}
WHOLE_FILE = "*"
HOOKS = "claude/.claude/hooks/tests/"
SCRIPTS = "claude/.claude/scripts/tests/"
EXEMPT = {
    (HOOKS + "_cursor_payloads.py", "plan-path"): {
        "# The highest phase of `.claude/plans/cursor-adapter.md` that this tree delivers.",
    },
    (HOOKS + "test_agent_roster.py", "the-plans"): {
        "Write would reproduce the plan's Context. Bash would bypass every Edit-path gate.",
    },
    (HOOKS + "test_require_plan_review.py", "the-plans"): {'exists to force that plan\'s review."""'},
    (HOOKS + "test_log_reviewer_round.py", "prep-plan"): {'prompt="Implement the change described in the plan.",'},
    (HOOKS + "test_ticket_reference_discipline.py", "phase-n"): {WHOLE_FILE},
    (SCRIPTS + "test_transcript_pr_cost.py", "plan-path"): {'".claude/plans/token-cost-per-pr-study.md",'},
    (SCRIPTS + "test_transcript_handoff_signal_response.py", "row-label"): {
        "# By signal kind: check={rows 0,2} advisory={row 1} hard-block={row 3}.",
        "# By ready-for-review active-marker context: active={rows 1,2} inactive={rows 0,3}.",
    },
    (SCRIPTS + "test_transcript_cost_counts.py", "row-label"): {
        'match=r"spawn row 0 is neither the withheld label nor a repo-tracked agent type",',
    },
    (SCRIPTS + "test_cleanup_merged_branches.py", "row-label"): {
        '"""Row A\'s head is a locally-present strict ancestor of the tip.',
        "Row B's head is a strict descendant of the tip that exists only on the",
        "remote. The forward pr-head-ancestor scan must fetch and confirm row B",
        "before the descendant scan can claim row A. The test would fail if the",
    },
    (SCRIPTS + "test_transcript_pr_cost_ledger.py", "row-label"): {WHOLE_FILE},
    ("evals/test_review_bench_cli.py", "row-label"): {WHOLE_FILE},
    ("evals/test_measure_explore_recall.py", "prep-plan"): {
        'assert all("command" in entry and "prompt" in entry for entry in plan)',
        "for entry in plan:",
        'assert {entry["case_id"] for entry in plan} == {"a0", "b0"}',
    },
    ("claude-skills/skills/tests/test_skills.py", "prep-plan"): {
        "file into a bare ~/.claude/-prefix dismissal (plan-it writes there in",
    },
    (SCRIPTS + "test_relocate_claude_config.py", "row-label"): {
        "# The path is passed as `--scope user -- <path>` (row H) so it is",
    },
    ("evals/test_measure_subagent_model_resolution.py", "ledger-id"): {
        'substitution to the built-in Plan agent — M3 detection method (b)."""',
        'the module\'s M8 comment for the corpus figure this was derived from."""',
    },
}
TREES = [
    "claude/.claude/hooks/tests", "claude/.claude/scripts/tests", "claude/.claude/tests",
    ":(glob)claude-skills/**/tests/**", ":(glob)plugins/**/tests/**", ":(glob)evals/**/test_*.py",
]


def scan(path, text):
    lines = text.splitlines()
    for name, pattern in PATTERNS.items():
        for match in re.finditer(pattern, text):
            if name in PLAN_NAME_PATTERNS and not (Path(".claude/plans") / f"{match.group(1)}.md").is_file():
                continue
            line_number = text.count("\n", 0, match.start()) + 1
            exempt = EXEMPT.get((path, name), set())
            if WHOLE_FILE in exempt or lines[line_number - 1].strip() in exempt:
                continue
            yield f"{path}:{line_number}:{name}:{' '.join(match.group(0).split())}"


REPO_ROOT = subprocess.run(
    ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True
).stdout.strip()
os.chdir(REPO_ROOT)  # the pathspecs and the .claude/plans existence filter are repo-root-relative
arguments = sys.argv[1:]
revision = arguments.pop(0).removeprefix("--rev=") if arguments and arguments[0].startswith("--rev=") else None
listed = subprocess.run(["git", "ls-files", "-z", "--", *TREES], capture_output=True, text=True, check=True).stdout
paths = arguments or sorted(path for path in listed.split("\0") if path.endswith(".py"))
hits = []
for path in paths:
    if revision:
        text = subprocess.run(["git", "show", f"{revision}:{path}"], capture_output=True, text=True, check=True).stdout
    else:
        text = Path(path).read_text(encoding="utf-8")
    hits.extend(scan(path, text))
if hits:
    print("\n".join(hits))
print(f"{len(hits)} hits in {len({hit.split(':')[0] for hit in hits})} files; scanned {len(paths)} files", file=sys.stderr)
if not paths or not Path(".claude/plans").is_dir():
    sys.exit(2)  # a scan of nothing must not read as a clean scan
sys.exit(1 if hits else 0)
```

```python
# code_only_check.py <base> <path> ...: lists each file whose code differs from <base> beyond docstrings,
# comments, and the planned literal edits.
import ast
import subprocess
import sys

LITERAL_EDITS = {
    "claude/.claude/hooks/tests/test_advance_past_commit_stall.py": [
        ("the plan's own 64-char quote", "the issue's own 64-char quote"),
    ],
    "claude/.claude/hooks/tests/test_scaled_timeout_shim.py": [(" (ledger row 4)", "")],
    "claude/.claude/hooks/tests/test_hook_alignment.py": [
        (
            "the plan's pre-implementation gate (Verification step 1 check 5) requires",
            "the live-session check of the deny requires",
        ),
        (
            "the plan's Context section names it as the dispatch the ScheduleWakeup misfire follows",
            "the ScheduleWakeup misfire most often comes while a backgrounded Agent dispatch runs",
        ),
    ],
}
DOCSTRING_OWNERS = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def code_only(source, literal_edits=()):
    tree = ast.parse(source)
    for node in ast.walk(tree):
        first = node.body[0] if isinstance(node, DOCSTRING_OWNERS) and node.body else None
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
            first.value.value = ""
    for old, new in literal_edits:
        holders = [n for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str) and old in n.value]
        if len(holders) != 1:
            return None
        holders[0].value = holders[0].value.replace(old, new)
    return ast.dump(tree)


base, paths = sys.argv[1], sys.argv[2:]
changed = []
for path in paths:
    before = subprocess.run(["git", "show", f"{base}:{path}"], capture_output=True, text=True, check=True).stdout
    with open(path, encoding="utf-8") as worktree_file:
        after = worktree_file.read()
    expected = code_only(before, LITERAL_EDITS.get(path, ()))
    if expected is None or expected != code_only(after):
        changed.append(path)
print(changed)
sys.exit(1 if changed else 0)
```

1. Base scan, run by the session before any dispatch: `scripts/venv-run.sh python3 <scratch>/plan_pointer_scan.py --rev=<base>` exits 1. The distinct paths in its stdout (`cut -d: -f1 | sort -u`) are exactly the 57 test files under Critical files. This proves the patterns fire on every file and that the list is complete for them.
   - The stderr line reports `scanned <N> files`. A run that scanned none exits 2 and is not a result.
   - A path outside the list in scope stops the run for the engineer, since Critical files is the file limit.
   - A hit that is a functional mention goes into `EXEMPT` and this plan's exemption list before any dispatch.
   - A listed file with no hit is dropped from the list.
   - Diff the `file:line` hits against the line ranges Critical files lists (within 3 lines). Every unlisted hit is exempted or added to the list before any dispatch.
   - Classify each hit by token position (docstring, `#` comment, other string, code). Every hit that is neither a docstring nor a comment must have a `LITERAL_EDITS` entry or an `EXEMPT` entry before any dispatch. Classify with a throwaway `tokenize` and `ast` script in `<scratch>`. With the final `EXEMPT` table the base run gives 183 hits in 57 files, of which 155 are in docstrings, 23 in `#` comments, 5 in other strings and none in code. The 5 strings are the four planned literals' hits. A different count stops the run for investigation.
2. Scan after the edits: `scripts/venv-run.sh python3 <scratch>/plan_pointer_scan.py <paths>` prints nothing to stdout and exits 0. Dispatch A, B and C pass their own files. Dispatch D passes no paths, which scans the whole corpus. Pass repo-relative paths from the worktree root, without a `./` prefix, because `EXEMPT` keys on the literal path string.
3. `scripts/venv-run.sh ruff check <paths>` exits 0. Dispatch A, B and C pass their own files. Dispatch D runs `scripts/venv-run.sh ruff check claude/.claude/ claude-skills/ plugins/ evals/`.
4. `scripts/venv-run.sh python3 <scratch>/code_only_check.py <base> <paths>` prints `[]` and exits 0. Dispatch D passes every path `git diff --name-only <base> -- '*.py'` prints. A listed file blocks, and the fix is to undo the non-docstring edit, never to add a substitution this plan does not list.
5. Directive check (a gate): `git diff -U0 <base> -- <paths> | grep -E '^-[^-]' | grep -nE 'noqa|type: ignore|pyright:|pragma|fmt:'` must print nothing. The AST check is blind to a dropped lint or type directive in a comment.
6. Added-line review (advisory, not a gate): `git diff -U0 <base> -- <paths> | grep -E '^\+[^+]' | grep -inE '\b(now|newly|was|were|used to|previously|no longer|initial|rewrite|the fix|this (pr|diff|change|plan))\b'`. Read every line it prints. Each is either rewritten or quoted in the return with one line on why it describes current behavior. It is advisory because `new-format lock` and similar uses are legitimate.
7. Tests that read test files as data: `scripts/venv-run.sh pytest claude/.claude/hooks/tests/test_ticket_reference_discipline.py claude/.claude/hooks/tests/test_scaled_timeout_shim.py claude/.claude/scripts/tests/test_select_tests.py claude/.claude/tests/test_pytest_collection_config.py -q` exits 0.
   - `test_ticket_reference_discipline.py` scans every tracked `.py` under `claude/`, `claude-skills/` and `plugins/`, so it covers 55 of the 57 files (G4). The two `evals/` files are covered by steps 2 and 4.
   - `test_scaled_timeout_shim.py` is itself one of the edited files, and it also reads other functions' source through `inspect.getsource`, so it must still collect and pass.
   - A failing node ID is compared against the merge-base: `git worktree add --detach <scratch>/base <base>`, then rerun those IDs with `scripts/venv-run.sh pytest <id>...` from `<scratch>/base`. Compare node IDs, not counts (G3). An ID that fails only on this tree blocks. Under `pyproject.toml`'s `-n auto`, `test_scaled_timeout_shim.py::TestCapMarkersDetectNonFiringCap::test_completed_not_killed_raises` is flaky at the merge-base and passes alone with `-n0`, so rerun any failing node with `-n0` before comparing.
   - Run `git worktree remove <scratch>/base` on every exit, a blocked run included, and state in the return whether it was removed.
8. Collection: `scripts/venv-run.sh pytest --collect-only -q <the 57 files>` exits 0, so every edited file still imports under the real conftests.
9. Scope:
   - `git diff <base> --name-only` prints exactly the 57 test files. The new plan file is untracked until the commit, so it appears only in `git status --porcelain`.
   - `git diff <base> --numstat` totals near 550 changed lines (row 14).
10. The scoped domain run, `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py`, is left to `/ready-for-review`, which CLAUDE.md already mandates for the pushed diff. A preview of `select_pytest_targets` on these 57 paths selects nearly the whole suite (row 13), so a second run inside Dispatch D adds little beyond steps 4, 7 and 8 and cannot finish inside a foreground tool call.

## Out of scope

- **Production modules.** The 11 under `claude/.claude/scripts/` are `cursor-adapter/hook-shim.py`, `select-tests.py`, and `transcript_analysis/`'s `cache_rebuild.py`, `cache_rebuild_rules.py`, `context_composition.py`, `cost_ledger.py`, `handoff_nudge.py`, `handoff_signal_response.py`, `instrument_authoring.py`, `rearm_backtest.py` and `turn_shape.py`. The `evals/` harness modules are `measure_explore_recall.py`, `measure_subagent_model_resolution.py`, `run_skill_evals.py` and `replay_bench/tasks.py`. The engineer's selected label scopes this PR to test files. Many of these pointers carry threshold rationale whose durable home needs its own decision. The option description deferred them to a later PR (row 2).
- **`hooks/tests/_cursor_payloads.py:57` and `docs/scripts.md:520`.** The phase numbers there have no home but `cursor-adapter.md` (row 4). Restating them needs that vocabulary given a durable home first, which reaches past test files.
- **PR narration and undefined labels in paragraphs that hold no plan pointer.** That covers `this PR`, `this diff`, `this change`, `Bug N`, `Fix N` and `Finding N` (row 15), and review-round narration such as `test_track_permission_prompts.py:241`. They are a sibling §Durable text shape, not a pointer to a plan. Inside a paragraph this change rewrites, the rewrite unit removes them.
- **Plan pointers in durable docs and skill bodies** (`docs/`, `SKILL.md`, `README.md`). They are not test files.
- **The `.claude/plans/` files themselves.** They are preserved records (G2).
- **A permanent repo test for the scan.**
  - Its patterns are heuristic and need a hand-kept exemption list, which would drift as gate tests keep describing "the plan file".
  - The 11 production modules still carry pointers until their own PR lands.
  - `/code-review`'s comment-discipline pass already reviews new pointers in each diff.
- **Bare plan names without the `.claude/plans/` prefix or a following "plan"** (row 16). The scan does not look for them.
- **Line-number pointers into hooks**, such as `test_hook_alignment.py:3334`'s "line ~183". They point at code, not at a plan.
- **The flawed tail-call demonstration in `atomic-worktree-lock-acquisition.md:193-194`** (row 12). The plan is a preserved record, and the test text that cited it is deleted.
