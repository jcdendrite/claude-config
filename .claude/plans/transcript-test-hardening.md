# Transcript-analysis test hardening: land the nine follow-ups deferred from the dispatch-group move

## Context

Goal: land the nine test-hardening and docstring follow-ups in GH-1222 as one PR, with no change to `transcript-analysis.py` behavior.

Ask: the engineer said "Sure start 1222" in reply to the session listing GH-1222 as the next item. The ticket body lists the nine items and a "Check:" for each. It says "Do them in one PR, and land it before the next decomposition phase takes its baselines" and "Cite symbols, not line numbers".

Why now: the next decomposition phase (tracker GH-1116, session-signal) baselines `conftest.py` and the bootstrap test file, and this PR touches both.

Answers gathered: no clarifying questions were asked. The engineer has not approved editing GH-1222's body, so this plan does not edit the issue. Item 8 touches the `conftest.py` module docstring, which also names `test_transcript_denials.py`, a file that exists. Any fix to that mention is an in-file docstring edit inside item 8.

## Approach

All nine GH-1222 items land in one PR through one `code-writer` dispatch. The PR changes test files and makes one comment-only edit to `transcript_analysis/subagent_mix.py`. Each new or tightened test is proven by a mutation check that reproduces the exact regression the ticket names. Each corrected docstring or comment states one durable fact. Where the ticket's count of conftest importers does not match the tree (item 8), the plan fixes what the docstring needs and repeats no count.

Alternatives considered and set aside:
- **Item 1.** Two heavier ways to prove the corpus was read were rejected:
  - Spying on `scope._resolve_project_scope` is white-box, so it would tie the tests to the session iterator.
  - Printing a session count from `cmd_cost_counts` would change behavior.

  Instead, each test proves the read with a nonzero count in the same output, or with a positive-control run over the same corpus. That stays black-box and reuses the file's existing two-call shape (`test_prefix_round_and_prefix_spawn_count_under_the_entered_branch`).
- **Item 5.**
  - Injecting `today` into `cmd_subagent_mix` would change production code.
  - `monkeypatch.setattr` of the whole `_MODEL_RATE_EXPIRES` dict is the precedent in `test_transcript_review_rounds.py`, but it drops every other model's key.

  `monkeypatch.setitem` on one key is the lightest option that works.
- **Item 7.** Targeting `SCRIPTS_TESTS_DIR` would select the whole scripts suite on every agent edit. Exact targets name only the tests that read the directory.
- **Item 9.** Resolving aliases would need a resolver that also understands `ast.Import`. Forbidding the unresolved shapes is a single rule, as the ticket prescribes, and no production module uses those shapes today.

**Item 1's third test is settled by the ticket.** Its Check, "all three tests fail when `this_repo_slugs` names a slug with no corpus", counts the two existing tests plus the new real-path all-zero rounds-table test that item 1 asks for. `TestCombinedZeroState` is not one of the three: it runs `pr-cost-section.sh` against a stand-in `transcript-analysis.py` and has no slug to vary. M1 therefore applies as written, and no engineer decision remains open.

### Assumption ledger

**Root.** GH-1222 lists two kinds of defect:
- Tests that pass without exercising what they name: an unread corpus, an unfiltered arm, an unpinned banner or message, an unselected reader, an unchecked import shape.
- Docstrings and comments that state false facts.

The plan closes each with a test that fails on the named regression, or with a corrected one-line fact. It makes no change to `transcript-analysis.py` behavior.

**Givens.**
- G1. GH-1222's item list, its Checks, and its instructions are fixed inputs. Reason: changing the issue needs a decision outside this plan (the engineer's approval to edit it).
- G2. The session-signal phase (tracker GH-1116) takes baselines of `conftest.py` and `test_transcript_cli_bootstrap.py`, so this PR lands first. Reason: another work item owns that phase and its timing. `[verified: gh issue view 1222, "land it before the next decomposition phase takes its baselines"]`
- G3. The verification command is `select-tests.py`. Because this diff edits `select-tests.py`, which is in `GLOBAL_TRIGGER_PATHS`, the run is certainly the full suite (reason `global-trigger`). Reason: the repo's `CLAUDE.md` Commands section requires agents to use `select-tests.py`, and names a diff that selects the full suite as a legitimate full run. `[verified: CLAUDE.md; staff-platform-engineer trace of select_pytest_targets and test_select_tests_self_change_global_trigger_forces_full_suite]`
- G4. Linked worktrees have no `.venv`, so every command uses `../../../.venv/bin/...` from the worktree root, and no one creates or installs a venv. Reason: the repo README and the no-autonomous-install rule fix this. `[verified: ls of the worktree and main checkout; README Tests section]`

**Mechanisms.**

- **M1 (item 1). Each zero-shaped cost-counts test proves its corpus was read.** `anchors: row3, row4, row5, row6`
  - (a) `test_zero_spawns_renders_the_sentence_not_a_table`:
    - Seed the `feat` record with `content=[_skill_use("s1", "code-review")]` instead of `content=[]`.
    - Add `assert "| code-review | 1 |" in out` as the read proof.
    - Keep both existing assertions.
  - (b) `test_no_branch_name_appears_anywhere_in_output`:
    - Call `self._isolate_allowlist(tmp_path, monkeypatch, tracked=["staff-sdet"])`, so disclosure no longer depends on the real agents directory.
    - Seed the secret-branch record with a `code-review` `_skill_use` alongside the existing `staff-sdet` spawn.
    - Assert `"| code-review | 1 |"` and `"| staff-sdet | 1 |"`. Together these prove both sections read that branch's records.
    - Keep the branch-absent assertion.
  - (c) New `TestCostCounts` test, `test_all_zero_state_renders_zero_round_rows_and_the_no_spawns_sentence`.
    - Seed two session files under `fake_projects`:
      - `sess-other.jsonl`: branch `other`, one `code-review` `_skill_use`.
      - `sess-feat.jsonl`: branch `feat`, `content=[]`.
    - Call 1, `branches="other"`: assert `"| code-review | 1 |"`. This is the positive control: the slug resolves to a corpus that is actually read.
    - Call 2, `branches="feat"`: assert the contiguous seam text from the rounds table's first zero row through the `No subagent spawns found in scope.` sentence. Copy the expected slice from `_EXPECTED_COST_BLOCK_COMBINED_ZERO_STATE` in `test_pr_cost_section.py` (read it; do not paraphrase), with the three review-skill names hard-coded (`code-review`, `plan-review`, `ready-for-review`) and not derived from `REVIEW_SKILLS`. Also assert `"| Agent type | Spawns |"` is absent.
    - Docstring (content, not layout; wrap to 130 columns): `"""Real cmd_cost_counts renders the zero review-round rows and the no-spawns sentence as one body. The other-branch run proves the seeded corpus is read."""`
  - (d) In `test_pr_cost_section.py`, replace the `TestCombinedZeroState` docstring sentence that begins "At the Python level" with: `test_transcript_cost_counts.py's test_all_zero_state_renders_zero_round_rows_and_the_no_spawns_sentence pins the same state from real cmd_cost_counts; this test pins pr-cost-section.sh's handling of it.` The test body does not change.
  - (e) In `test_review_round_table_renders_actual_round_counts`, delete the first docstring sentence. It says every other fixture seeds spawns only, which is false today and more false after (a)–(c). The rest of the docstring stays.
- **M2 (item 2). New `TestSpawnCountsByAgentType` test for the branch filter.** `anchors: row7`
  - Name: `test_branch_filter_counts_only_the_named_branchs_spawn_of_a_shared_agent_type`.
  - Input: two sessions. `feat-a` holds one `staff-sdet` spawn: `(Path("a.jsonl"), [_asst(..., branch="feat-a", content=[_agent_use("a1", "staff-sdet")])])`. `feat-b` holds two spawns of the same type (`b1`, `b2`) in the same shape. Separate sessions keep carry-forward attribution out of the test.
  - Call `_spawn_counts_by_agent_type(..., {"feat-a"})` and assert the result `== {"staff-sdet": 1}`. A deleted filter returns 3 and an inverted filter returns 2, so both mutations fail.
  - Docstring: `"""A filter naming one of two branches counts only that branch's spawn of a shared agent type."""`
- **M3 (item 3). Pin the backstop's message.** `anchors: row8`
  - In `test_backstop_assertion_fires_on_bypassed_partition_step`, use `pytest.raises(AssertionError, match=r"spawn row 0 is neither the withheld label nor a repo-tracked agent type")`.
  - Keep the assertion that the leaked label is absent.
- **M4 (item 4). The user arm's filters each get a test that seeds `tool_result` records.** `anchors: row9`
  - (a) In `TestSubagentsToolResultBytes`, add `test_branch_filter_applies_to_tool_result_bytes`.
    - Seed one session, timestamped with `_attr_ts`:
      - `_asst` on `branch-a` with `_read_use("t1", ...)`, then `_user_msg([_tool_result("t1", "x" * A)], branch="branch-a")`.
      - The same pair on `branch-b` with `t2` and a different size `B`.
    - Call with `_subagents_args(branches="branch-a")`.
    - Assert `"branch-b" not in out`, and assert the `branch-a` main row's `Bytes` equals `str(A)`.
    - Keep A and B under 1,000 so the cell has no thousands separator.
  - (b) In `TestSubagentsSince`, add `test_since_excludes_tool_result_bytes_older_than_window`.
    - Freeze `time.time` the way `test_since_window_leaves_a_later_default_branch_turn_on_the_default_branch` does.
    - Seed one non-default branch with an assistant/`tool_result` pair 20 days old and another pair 1 day old.
    - Call with `since="10d"`.
    - Assert the main row's `Bytes` equals the recent result's length only.
  - One-line docstrings:
    - (a): "The branch filter applies to tool_result bytes on user records, not only to assistant turns."
    - (b): the same sentence with `--since` in place of "The branch filter".
- **M5 (item 5). Pin the subagent-mix STALE PRICING banner and correct the comment.** `anchors: row10`
  - New `TestSubagentMixDollars` test, `test_past_rate_expiry_prints_stale_pricing_banner`.
    - Reuse the seed from `test_actual_dollars_match_hand_computed_usage`.
    - Add `monkeypatch.setitem(_mod.subagent_mix.pricing._MODEL_RATE_EXPIRES, "claude-sonnet-4-6", date(2000, 1, 1))`. Going through `subagent_mix`'s own binding guarantees the test patches the same dict `subagent_mix` reads.
    - Assert `"STALE PRICING — today is past the re-verify-by date for: claude-sonnet-4-6."` is in `out`.
  - New sibling test, `test_future_rate_expiry_prints_no_stale_pricing_banner`: same seed, `monkeypatch.setitem` on the same key to `date(2999, 1, 1)`, assert `"STALE PRICING" not in out`. Never rely on the unpatched default expiry, which flips on its date.
  - In `cmd_subagent_mix`, replace the four-line comment above `today = datetime.now(UTC).date()` with the two lines below. The code line does not change.
    ```python
    # Read once, never inside the per-record loop, matching cost.
    # Tests reach the STALE PRICING banner by moving pricing._MODEL_RATE_EXPIRES into the past, so no clock injection is needed.
    ```
- **M6 (item 6). Reword the first sentence at each of the twelve `no in-process` sites.** `anchors: row11, row12, row13`
  - Use the shape the file's rearm-backtest, spend-over-threshold, plan-boundary, and handoff-signal-response docstrings already use: `Proves, in a fresh interpreter, that <module> resolves through sys.path[0] alone and that real argparse dispatches <command> to <function> through set_defaults(func=...).`
  - Keep every later sentence at each site.
  - Substitutions (module / command / function):

    | Module | Command | Function |
    |---|---|---|
    | `cost.py` | `cost` | `cmd_cost` |
    | `reviewer_yield.py` | `reviewer-yield` | `cmd_reviewer_yield` |
    | `review_trace.py` | `review-trace` | `cmd_review_trace` |
    | `review_trace.py and its denials.py import` | `review-trace --deny-summary` | `cmd_review_trace` |
    | `read_scope.py` | `read-scope` | `cmd_read_scope` |
    | `cache_rebuild.py` | `cache-rebuild` | `cmd_cache_rebuild` |
    | `audit_routing.py` | `audit-routing` | `cmd_audit_routing` |
    | `cost_ledger.py` | `cost-ledger` | `cmd_cost_ledger` |
    | `workstream_cost.py` | `workstream-cost` | `cmd_workstream_cost` |
    | `subagents.py` | `subagents` | `cmd_subagents` |
    | `subagent_mix.py` | `subagent-mix` | `cmd_subagent_mix` |
    | `subagent_mix.py` | `cost-counts` | `cmd_cost_counts` |

  - The new text makes no claim about in-process coverage.
- **M7 (item 7). Agents-directory edits select the scripts tests that read the real agents directory.** `anchors: row14, row15`
  - In `select-tests.py`:
    - Add `TRANSCRIPT_COST_COUNTS_TEST_PATH = "claude/.claude/scripts/tests/test_transcript_cost_counts.py"` next to `TRANSCRIPT_DENIALS_TEST_PATH`, with the comment `# test_transcript_cost_counts.py reads claude/.claude/agents/ through redaction._REPO_AGENT_DEFINITIONS_DIR, a production constant TestCrossDomainReadCompleteness's scan cannot see.`
    - Append `TRANSCRIPT_COST_COUNTS_TEST_PATH`, `TRANSCRIPT_ANALYSIS_TEST_GLOB`, and the existing `MEASURE_SUBAGENT_MODEL_RESOLUTION_TEST` to the targets of the `_is_under(p, AGENTS_DIR)` row. The glob is the sibling fix: `test_transcript_analysis.py`'s `test_real_agents_directory_allowlists_code_writer` reads the same directory unpatched, and the glob also selects four other files. The evals target is the third reader: `TestAgentFrontmatterParsing` reads the real `staff-backend-engineer.md` frontmatter.
    - Add to the `AGENTS_DIR:` paragraph of the comment block above `CROSS_DOMAIN_EXCEPTIONS`: `TRANSCRIPT_COST_COUNTS_TEST_PATH, TRANSCRIPT_ANALYSIS_TEST_GLOB, and MEASURE_SUBAGENT_MODEL_RESOLUTION_TEST are also targets: each reads the real agents directory without patching it. This list is hand-audited, because the cross-domain scan cannot see a read through a production constant.`
  - In `test_select_tests.py`:
    - Add the new constant to `_FILE_TARGETS`.
    - In `test_agents_dir_change_also_selects_hooks_and_skills_tests`, add all three new targets to the expected set and name them in the docstring. Keep the test's name.
- **M8 (item 8). Mark `conftest.py`'s module docstring lists as examples.** `anchors: row16`
  - Rewrite the first paragraph as three roles:
    - git-repo scaffolding for the worktree-cleanup script tests (e.g. `test_cleanup_merged_branches.py`, `test_cleanup_idle_open_pr_worktrees.py`);
    - suite-wide transcript-corpus isolation (see the autouse fixture below);
    - transcript-record fixture builders (e.g. for `test_transcript_cost.py` and `tests/_handoff_nudge_helpers.py`; see the extraction rationale on `_write_jsonl` below).
  - Follow them with: `The file names above are examples, not a complete consumer list.`
  - This drops `test_transcript_denials.py` and `test_transcript_gh_cli.py`, which do not import `conftest.py`.
  - The second paragraph does not change. Both cleanup names stay because its "either script" refers back to them.
- **M9 (item 9). Forbid the sibling-import shapes the attribute check cannot resolve.** `anchors: row17`
  - In `test_transcript_package_module_prefixes.py`, add `_unresolvable_sibling_imports(tree, package_names) -> list[str]`. It flags each of these shapes:
    - `from transcript_analysis import <m> as <a>`, where `<m>` is in `package_names`;
    - any `ast.Import` of `transcript_analysis` or `transcript_analysis.<m>`, with or without `as`.
  - Add `test_production_modules_bind_siblings_only_by_unaliased_from_import`, which asserts the helper returns nothing for every `PRODUCTION_MODULES` member. Docstring: `"""test_production_modules_reference_only_real_sibling_attributes resolves only unaliased `from transcript_analysis import <m>` bindings, so every other sibling-binding shape is forbidden."""`
  - Add `test_unresolvable_sibling_imports_flags_aliased_and_plain_import_shapes`, following the synthetic-source pattern of `test_lazy_module_attrs_ignores_deny_list_shaped_getattr`:
    - It flags `from transcript_analysis import scope as s`, `import transcript_analysis.scope as s`, `import transcript_analysis.scope`, and `import transcript_analysis`.
    - It passes `from transcript_analysis import scope`, and as negative controls `import os as o` and `from collections import defaultdict as dd`.
    - The code-writer states in the helper's docstring whether `from transcript_analysis import scope as scope` is flagged; flagging it is the simpler rule.
    - The ticket's named test, `test_production_modules_reference_only_real_sibling_attributes`, stays green under the alias mutation, because the new test carries the rule. V14 records this.
- **M10. One dispatch, and mutation checks that leave no residue.** `anchors: root, row2`
  - The dispatch split is described under Critical files.
  - Before each mutation, save `git diff HEAD -- <file>` to a file under `/tmp`. After the inverse `Edit`, the same command must match that file byte for byte.
  - Never use `git stash`, because the stash is shared across worktrees.
  - Never run any command that rewrites tracked files from the index or HEAD (`git checkout`, `git restore`, `git reset --hard`, `git clean`), because the files carry intended edits.
  - Pin each mutation to its arm with surrounding lines, and confirm the diff shows exactly that arm.
  - The code-writer wraps every quoted docstring and comment to the repo's 130-column limit (the quoted text is content, not layout).
- **M11. Committed text cites symbols and nothing tied to the ticket.** `anchors: row2, row18`
  - Added comments and docstrings name symbols, never line numbers.
  - They carry no ticket identifier.

**Rows.**
1. `[engineer-verified: "Sure start 1222"]`, answering the session's listing of GH-1222 as the next item. It covers starting GH-1222 only. It does not approve editing the issue.
2. `[verified: gh issue view 1222]` The ticket's instructions: "Do them in one PR, and land it before the next decomposition phase takes its baselines" and "Cite symbols, not line numbers".
3. `test_zero_spawns_renders_the_sentence_not_a_table` and `test_no_branch_name_appears_anywhere_in_output` are in `test_transcript_cost_counts.py`. `test_pr_cost_section.py`'s `TestCombinedZeroState` runs a stand-in, never real `cmd_cost_counts`. The ticket names no file for the first two tests, so this is not a ticket discrepancy. Handling: M1. `[verified: Read of both files]`
4. With no top-level `--config-dir`, an empty `--this-repo` scope yields no sessions and raises no error. So today's two tests stay green under a slug with no corpus. `[verified: Read of scope._resolve_project_scope's docstring; V1(a) confirms by running]`
5. `[verified: gh issue view 1222, item 1]` The ticket's third item-1 test is the new real-path all-zero rounds-table test (M1(c)). Its Check, "all three tests fail when `this_repo_slugs` names a slug with no corpus", counts the two existing tests plus that new one. `TestCombinedZeroState` is not among them. The ticket asks for the docstring update that M1(d) makes.
6. `test_review_round_table_renders_actual_round_counts`'s docstring says every other fixture seeds spawns only. But `test_prefix_round_and_prefix_spawn_count_under_the_entered_branch` already seeds a round. `[verified: Read]`
7. `_spawn_counts_by_agent_type` skips a record whose attributed branch is outside a non-None `branch_filter`. No test seeds two named branches that share an agent type under a non-None filter. `[verified: Read of the function, of TestSpawnCountsByAgentType, and of _cost_counts_spawns_observation in test_transcript_analysis_branch_attribution.py; the full caller list is relayed from discovery]`
8. The backstop raises `AssertionError("cost-counts: spawn row {row_index} is neither the withheld label nor a repo-tracked agent type. Refusing to print the value; it is withheld from this message by design.")`. `[verified: Read of cmd_cost_counts]`
9. In `cmd_subagents`, the user/`tool_result` arm applies the same branch and `since_ts` filters as the assistant arm. The existing branch-filter and `--since` tests seed assistant records only. `[verified: Read of cmd_subagents, test_branch_filter_still_applies_to_output, and TestSubagentsSince]`
10. The banner prints when `all_stale_models` is non-empty. `_dispatch_usage_summary` fills it by comparing the wall-clock `today` with `pricing._MODEL_RATE_EXPIRES[model]`. Every default expiry is `_PRICING_FETCH_DATE + timedelta(days=90)`, which is 2026-12-04. So without the patch, the test's outcome would flip on that date. `[verified: Read of subagent_mix.py and pricing.py]`
11. `[verified: gh issue view 1222, item 6]` Item 6 reads: "Reword each site to what the subprocess run uniquely proves: the module resolves in a fresh interpreter through `sys.path[0]` alone, and real argparse dispatches to the command through `set_defaults(func=...)`."
12. There are twelve `no in-process` sites across eleven commands. Each of those commands registers through `set_defaults(func=cmd_<name>)` in the shim's parser. Four tests in the same file already use M6's shape. `[verified: Grep of test_transcript_cli_bootstrap.py; Grep of transcript-analysis.py for set_defaults(func=; Read]`
13. The ticket's "ten of the twelve sites have direct `_mod.cmd_<name>` readers" matches the tree: `cmd_workstream_cost` and `cmd_cost_counts` are the two sites without one. No discrepancy. M6's text makes no in-process claim either way. `[verified: staff-sdet grep of _mod.cmd_<name> readers across the tests directory]`
14. Facts behind M7:
    - The `AGENTS_DIR` row targets `HOOKS_TESTS_DIR`, `SKILLS_TESTS_DIR`, and `REVIEW_BENCH_TEST_GLOB`.
    - `TestTrackedAgentFilenamesMatchAgentTypeNameCharset` reads the real agents directory through `redaction._REPO_AGENT_DEFINITIONS_DIR`. So does `test_real_agents_directory_allowlists_code_writer`.
    - `test_every_directory_target_exists_on_disk` treats every file target missing from `_FILE_TARGETS` as a directory.

    `[verified: Read of select-tests.py, test_transcript_cost_counts.py, test_transcript_analysis.py, and test_select_tests.py]`
15. Those two tests are the only scripts tests whose outcome depends on the real agents directory. `[unverified — V7(c) audits]`
16. Item 8 discrepancy. More files import from `conftest.py` than either docstring list names. `test_transcript_denials.py` and `test_transcript_gh_cli.py` are named but import nothing from it. The fix states no count. `[verified: Grep for "from .conftest import" across claude/.claude/scripts/tests]`
17. Item 9 facts:
    - `imported_modules` keeps `alias.asname or alias.name` intersected with the package module names, so an aliased sibling drops out of the check.
    - `ast.Import` is never inspected.
    - Each of the nine `PRODUCTION_MODULES` binds its siblings with unaliased `from transcript_analysis import ...` lines only.

    `[verified: Read of the prefix test; Grep of the nine modules]`
18. `test_ticket_reference_discipline.py` scans every tracked `.py` file under `claude/` for ticket-prefixed identifiers. `[verified: select-tests.py's comment on TICKET_REFERENCE_DISCIPLINE_TEST_PATH]`

## Critical files

These are the change's file limit.

- `claude/.claude/scripts/tests/test_transcript_cost_counts.py`: M1(a)(b)(c)(e), M2, M3.
- `claude/.claude/scripts/tests/test_pr_cost_section.py`: M1(d), docstring only.
- `claude/.claude/scripts/tests/test_transcript_subagents.py`: M4.
- `claude/.claude/scripts/transcript_analysis/subagent_mix.py`: M5, comment only.
- `claude/.claude/scripts/tests/test_transcript_subagent_mix_dollars.py`: M5.
- `claude/.claude/scripts/tests/test_transcript_cli_bootstrap.py`: M6.
- `claude/.claude/scripts/select-tests.py`: M7.
- `claude/.claude/scripts/tests/test_select_tests.py`: M7.
- `claude/.claude/scripts/tests/conftest.py`: M8, module docstring only.
- `claude/.claude/scripts/tests/test_transcript_package_module_prefixes.py`: M9.
- `.claude/plans/transcript-test-hardening.md`: this plan.

**Reuse (do not reimplement):**
- `TestCostCounts._isolate_allowlist`, `_cost_counts_args(this_repo_slugs=...)`, and the `fake_projects` fixture.
- From `conftest.py`: `_asst`, `_agent_use`, `_skill_use`, `_write_jsonl`, `_user_msg`, `_tool_result`, `_read_use`, `_table_cols`, `_write_subagent_dispatch`, `_priced_sidechain_asst`.
- `_attr_ts` in `test_transcript_subagents.py`.
- `_subagents_args` and `_subagent_mix_args` from `_subagent_helpers.py`.
- The fresh-interpreter docstring shape already in `test_transcript_cli_bootstrap.py`.
- `TRANSCRIPT_ANALYSIS_TEST_GLOB` and `_FILE_TARGETS`.
- `_package_module_names` and the synthetic-source pattern of `test_lazy_module_attrs_ignores_deny_list_shaped_getattr`.

**Dispatch split: one `code-writer` dispatch** covering M1–M11. Its verification command is `../../../.venv/bin/python3 claude/.claude/scripts/select-tests.py` from the worktree root.

The items do touch mostly disjoint files. They are still kept together for these reasons:
- They share the same background: the `_mod` loading pattern, the `conftest.py` builders, the comment discipline, and the mutation hygiene. Every prompt would have to restate it.
- The whole diff is small.
- One self-review sees every edit.
- Parallel dispatches would share this one worktree, so each agent's `select-tests.py` run would see another agent's half-finished edits.

## Verification

Run single test nodes with `../../../.venv/bin/pytest <file> -k <name>`. Revert every mutation per M10.

- **V1 (item 1).**
  - (a) Before editing: change `this_repo_slugs=["-home-user-testrepo"]` to `["-home-user-no-corpus"]` in the two existing tests. Both pass, which confirms the gap in row 4. Revert.
  - (b) After M1: make the same swap in all three tests, including every call in the new test. Each fails. Revert.
- **V2 (item 2).** Delete the `branch_filter` skip in `_spawn_counts_by_agent_type`: M2's test fails because the result is `{"staff-sdet": 3}`. Revert. Then invert it (`branch in branch_filter`): the test fails because the result is `{"staff-sdet": 2}`. Revert.
- **V3 (item 3).** Change the message of the backstop `raise AssertionError(...)` in `cmd_cost_counts` (for example, to `"backstop"`). M3's test fails on `match`. Revert.
- **V4 (item 4).**
  - In the user arm only, delete ` or (branch_filter and branch not in branch_filter)` from the skip condition, pinned with the surrounding `elif rec_type == "user":` lines. M4(a) fails. Revert.
  - In the user arm only, delete the `since_ts` block. M4(b) fails. Revert.
  - Both tests pass on the unmutated code.
- **V5 (item 5).** Delete the `if all_stale_models:` banner block: the past-expiry test fails. Revert. Then make the banner condition unconditional: the future-expiry test fails. Revert.
- **V6 (item 6).** A multiline Grep for `no\s+in-process` in `test_transcript_cli_bootstrap.py` returns nothing. Each of the twelve sites opens with "Proves, in a fresh interpreter,".
- **V7 (item 7).**
  - (a) Remove each new target from the `AGENTS_DIR` row in turn (`TRANSCRIPT_COST_COUNTS_TEST_PATH`, `TRANSCRIPT_ANALYSIS_TEST_GLOB`, `MEASURE_SUBAGENT_MODEL_RESOLUTION_TEST`). `test_agents_dir_change_also_selects_hooks_and_skills_tests` fails each time. Revert.
  - (b) `test_every_file_target_exists_on_disk` and `test_every_directory_target_exists_on_disk` both pass.
  - (c) Grep the whole repo (`claude/.claude/scripts/tests`, `claude/.claude/hooks/tests`, `claude-skills`, `evals`) for `AGENTS_DIR`, `_REPO_AGENT_DEFINITIONS_DIR`, `_repo_tracked_agent_type_names`, and `STAFF_BACKEND_ENGINEER_AGENT_FILE`. A hit joins the row as a target only if its test reads the real agents directory without patching it. This touches no new file beyond the targets above.
- **V8 (item 8).** Every file named in `conftest.py`'s module docstring contains `from .conftest import`. The docstring says the names are examples and states no count.
- **V9 (item 9).** The synthetic-source test passes. As a one-time check, add `from transcript_analysis import scope as s` to `subagents.py`: the new production test fails. Revert.
- **V10 (no behavior change).** `git diff -U0 HEAD -- claude/.claude/scripts/transcript_analysis/ claude/.claude/scripts/transcript-analysis.py` changes only `#` comment lines, all in `subagent_mix.py`.
- **V11.** `../../../.venv/bin/python3 claude/.claude/scripts/select-tests.py` from the worktree root is green. The diff touches `select-tests.py` itself, a global trigger, so this is a full-suite run (reason `global-trigger`). Run it in the background or with an explicit timeout, because it can exceed the 600-second foreground cap, and record the pass count.
- **V12.** `../../../.venv/bin/ruff check` passes on every changed `.py` file.
- **V13.** `git diff -U0 HEAD -- '*.py' | grep -nE '^\+.*(\.py:[0-9]|[Ll]ines? [0-9]|#1222|GH-1222)'` prints nothing.
- **V14 (PR body).** The PR body records only facts verified against the tree:
  - the item 8 consumer-list mismatch and its handling (no count restated);
  - that the item 9 named test stays green under the alias mutation, because a new test carries the rule;
  - M7's two sibling targets (`TRANSCRIPT_ANALYSIS_TEST_GLOB`, which adds five files, and `MEASURE_SUBAGENT_MODEL_RESOLUTION_TEST`);
  - that the verification run was the full suite via the global trigger, not a scoped run;
  - M8's example framing, which means later phases no longer append file names to `conftest.py`'s docstring;
  - optionally, the mutation outcome for each of V1–V5 and V9.

## Out of scope

- **Editing GH-1222's body.** The engineer has not approved it.
- **Rewriting `TestCombinedZeroState` to run real `cmd_cost_counts`.** It pins `pr-cost-section.sh`'s output seams against a stand-in by design.
- **The bootstrap module docstring, and the cost-trend test's docstring.** Neither is a `no in-process` site. The module docstring's claim about `sys.path[0]`-only resolution is accurate. The cost-trend docstring refers to the `cost` test's text, so it picks up the correction without an edit.
- **Extending M9's alias guard to `TEST_FILES`.** Item 9 scopes the guard to production modules.
- **An enforcing test for `conftest.py`'s docstring lists.** Calling the lists examples removes the need for one.
- **Editing GH-1116 or the next phase's plan.** That tracker text belongs to another work item, and the PR body note (V14) covers the hand-off.
