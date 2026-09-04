# select-tests routing: declare the hooks-test read of both rules directories

## Context

Get `main`'s CI green again by declaring a cross-domain test read that
`select-tests.py`'s routing table is missing. `claude/.claude/hooks/tests/test_claude_md_excludes.py`
globs both `claude/.claude/rules` and `.claude/rules` by path, but
`CROSS_DOMAIN_EXCEPTIONS` routes both directories to `(SKILLS_TESTS_DIR,)` alone,
so a change under either rules directory never selects the hooks test directory
that reads it. `TestCrossDomainReadCompleteness::test_every_resolved_path_selects_a_target_covering_its_reading_test`
(added by #855) catches exactly this and fails on `main` at `0dc07ac`.

Why now: `main` is red. PR #854 merged green because its base (`010d4e4`) predated
the stricter completeness test added by #855, so that PR's own CI never ran the
check its change violates — a stale-base race, not a bypass and not a flaw in the check.
The routing fix is tracked as issue #860; the stale-base escape that let it reach
`main` is tracked separately as issue #861 and is not addressed here.

Intended outcome: both rules-directory rows declare the hooks-test read, the
completeness test passes, the full suite is green, and the comment in
`test_claude_md_excludes.py` that documents the gap as an accepted cost no longer
asserts something the fix makes false.

## Approach

Widen both rules-directory rows in `CROSS_DOMAIN_EXCEPTIONS` from `(SKILLS_TESTS_DIR,)` to `(SKILLS_TESTS_DIR, HOOKS_TESTS_DIR)` — two target tuples, nothing else in the table changes. Then correct the four prose sites the widening makes false: the two per-constant comments in `select-tests.py`, three test docstrings in `test_select_tests.py` (two of which also carry the exact-equality assertions being updated), and the routing-gap paragraph in `test_claude_md_excludes.py`. No new constant, predicate, row, helper, or shared abstraction.

**Widen in place, not a standalone row.** `select_pytest_targets` unions targets across every matching row rather than stopping at the first (`select-tests.py:480-498`), so a second row `(lambda p: _is_under(p, RULES_DIR), (HOOKS_TESTS_DIR,))` produces byte-identical selections to the widening — it is the same change plus a duplicate predicate for the next reader to reconcile. I assessed the `d40b162` precedent independently and agree it does not transfer: the row that fix declined to widen is `(lambda p: p in SKILL_FILES_READ_BY_HOOK_TESTS, (HOOKS_TESTS_DIR,))` (`select-tests.py:433`), a frozenset covering eight unrelated SKILL.md constants, so widening its tuple would have over-selected for the other seven. Each rules row is a standalone single-directory `_is_under` predicate (`select-tests.py:437`, `:446`), so its target tuple reaches only paths under that one directory. Multi-target `_is_under` rows are already the established shape at `:436` (`AGENTS_DIR`) and `:440` (`DOCS_DIR`).

**The two exact-equality assertions get updated, and that is not masking a regression.** `test_rules_dir_change_also_selects_skills_tests` (`test_select_tests.py:726-731`) and `test_root_rules_dir_change_selects_skills_tests` (`:856-862`) both encode the under-declaration as expected behavior, so both must change. What makes updating them legitimate rather than fitting-the-test-to-the-code is that neither is the oracle: `TestCrossDomainReadCompleteness::test_every_resolved_path_selects_a_target_covering_its_reading_test` (`:334-356`) derives the requirement from `test_claude_md_excludes.py`'s own module-level constants, independent of the table, and it is the check failing on `main`. The row-level tests are DAMP documentation of the concrete selection; the derived test is the invariant. Keep both as set-equality (not a subset check), so over-selection stays pinned in the other direction. Convert `:856-862` from tuple-equality to set-equality while updating it — `select_pytest_targets` returns `tuple(sorted(targets))`, so the tuple form would have to read `(HOOKS_TESTS_DIR, SKILLS_TESTS_DIR)` in sort order rather than in the order the docstring names the readers, and every multi-target sibling in the file already uses `set(result.target_paths) == {...}` (`:724`, `:739`, `:794`).

**`_KNOWN_CROSS_DOMAIN_READS` gets no entries — the original brief's prescription would break a sibling test.** `test_skill_files_read_by_hook_tests_equals_known_reads_under_hooks_tests_dir` (`test_select_tests.py:1116-1124`) builds its expectation as *every* entry whose reading test is under `HOOKS_TESTS_DIR` and asserts set equality with `SKILL_FILES_READ_BY_HOOK_TESTS`. `test_claude_md_excludes.py` is under `HOOKS_TESTS_DIR`, so an entry pairing it with `claude/.claude/rules` would put that directory into `expected` and fail the equality — and the only way to make it pass would be adding a directory to a frozenset of SKILL.md file paths, which `test_every_exact_match_literal_path_constant_exists_on_disk` (`:1165-1167`) then fails on `is_file()`. The single-source-of-truth reading points the same way: the DAMP exception covers hand-derived data *not* derived from the code under test (`:1070-1076` says so explicitly), and this particular fact is already derived and enforced by the AST scanner. Adding it by hand would be a second copy of a derived fact, in a list whose stated scope does not admit it.

That list's closing sentence, "Add a newly-mapped cross-domain read here too" (`:1075-1076`), over-generalizes its own scope statement two sentences earlier and is what set this session's open question in motion. Tighten it in place (Axis 2, in a file the change already touches):

```python
# Add a newly-mapped read here only when it fits that scope. A
# HOOKS_TESTS_DIR entry whose read path is not a
# SKILL_FILES_READ_BY_HOOK_TESTS member fails the equality test below.
```

**A fourth stale site the exploration did not flag.** `test_github_actions_workflows_rule_md_change_also_selects_hooks_tests` (`:733-739`) asserts `{SKILLS_TESTS_DIR, HOOKS_TESTS_DIR}` and still passes after the fix, but its docstring says "a sibling rule file under the same directory does not need HOOKS_TESTS_DIR, only this one does" — false the moment the `RULES_DIR` row widens. The assertion is fine; the prose is not. The same widening makes the `GITHUB_ACTIONS_WORKFLOWS_RULE_MD` row (`select-tests.py:438`) contribute no target for any input, since that constant is a file under `RULES_DIR`. Keep the row: it declares a genuinely distinct fact (`test_ci_path_filter.py` reads that one file), it costs nothing in a union-based table, and it becomes load-bearing again the day `test_claude_md_excludes.py` stops globbing the directory. Record the redundancy in the row's own header comment so a later reader does not delete it as dead — that comment is the single home for the fact, and the test docstring defers to it rather than restating it.

**Prescribed comment text.**

`select-tests.py:405-406` (header comment above `CROSS_DOMAIN_EXCEPTIONS`):
```python
# RULES_DIR: test_rules_frontmatter.py (SKILLS_TESTS_DIR) and
# test_claude_md_excludes.py (HOOKS_TESTS_DIR) each rglob
# claude/.claude/rules/*.md by path.
```

`select-tests.py:407-408`, gaining the redundancy fact:
```python
# GITHUB_ACTIONS_WORKFLOWS_RULE_MD: test_ci_path_filter.py (HOOKS_TESTS_DIR)
# reads this exact file. Subsumed by the RULES_DIR row above, and kept
# because its declaration is narrower and independent of it.
```

The "why keep a currently-inert row" argument stays in the commit message and PR body; the new direct-tuple test below is what actually enforces it.

`select-tests.py:168-171`, keeping the existing four lines and appending:
```python
# test_claude_md_excludes.py (HOOKS_TESTS_DIR) rglobs both directories as
# well.
```

`test_claude_md_excludes.py:48-56` collapses to the three surviving lines — the rename-robustness fact only. The routing declaration's home is the table, and a back-pointer here would be a second copy that drifts:
```python
# Same rationale as _STOW_SOURCE_RULE_FILES, applied to the sibling
# project-scope directory: globs rather than names a rule file, so a
# rule-file rename cannot break this module.
```

### Assumption ledger

**Root:** `CROSS_DOMAIN_EXCEPTIONS` under-declares both rules directories' readers — `test_claude_md_excludes.py` rglobs each by path, but neither row lists `HOOKS_TESTS_DIR` — so a rules-file diff never runs the test that reads it, and `TestCrossDomainReadCompleteness` fails on `main` at `0dc07ac`.

**Givens:** none. No condition this design treats as fixed lies beyond its own reach. The two conditions it could change and deliberately will not — the stale-base merge race (a branch-protection or merge-queue decision, reachable but owned by #861) and `test_claude_md_excludes.py`'s rglob-over-named-files discovery (reachable in a file this plan already edits) — are recorded in **Out of scope** with their reasons, which is where a declined-but-reachable condition belongs.

**Rows:**

1. `select_pytest_targets` unions targets across every matching `DOMAIN_RULES` and `CROSS_DOMAIN_EXCEPTIONS` row rather than stopping at the first match, so row order is irrelevant and a widened tuple composes with the exact-match rows for paths under the same directory. `[verified: select-tests.py:480-498]`
2. Each rules row's predicate is a standalone single-directory `_is_under`, so widening its target tuple can affect only paths under that one directory — unlike the frozenset-backed row at `select-tests.py:433`, which covers eight unrelated constants. `[verified: select-tests.py:234-235, 437, 446, against 433 and 101-110]`
3. `test_claude_md_excludes.py` is the only test with an undeclared read of either rules directory: `test_ci_path_filter.py:42-43` names the rules paths only as literal `grep -E` arguments, and `test_rules_frontmatter.py:54-55,126-131` is already declared. The completeness test turning green is the mechanical confirmation — a second undeclared reader would keep it red. `[verified: Step 3 exploration grep across claude/.claude/{hooks,scripts,skills}/tests and plugins/*/tests]`
4. Adding entries to `_KNOWN_CROSS_DOMAIN_READS` for these reads would fail `test_skill_files_read_by_hook_tests_equals_known_reads_under_hooks_tests_dir`: it filters entries on `_is_under(reading_test, HOOKS_TESTS_DIR)`, which `test_claude_md_excludes.py` satisfies, and asserts set equality against `SKILL_FILES_READ_BY_HOOK_TESTS`, which holds only SKILL.md file paths. `[verified: test_select_tests.py:1116-1124 against 1077-1098 and select-tests.py:101-110]`
5. `TestCrossDomainReadCompleteness` already derives this exact fact from `test_claude_md_excludes.py:31,34,35`'s module-level constants — which is why it fails today — so a hand-added DAMP entry would duplicate a derived fact rather than supply an independent one. `[verified: test_select_tests.py:334-356 with the resolver grammar pinned at 163-220; select-tests.py:62-66]`
6. Both rules-row assertions are exact-equality and must change; the independent oracle for the corrected expectation is the derived completeness test, not the assertion itself. `[verified: test_select_tests.py:726-731, 856-862]`
7. Widening the `RULES_DIR` row makes the `GITHUB_ACTIONS_WORKFLOWS_RULE_MD` row contribute no target for any input, since that constant names a file under `RULES_DIR`; its test still passes, so no test would fail if the row were later deleted. `[verified: select-tests.py:113, 437-438; test_select_tests.py:733-739]` Confirmed empirically at plan review: deleting that row after applying the fix leaves `test_select_tests.py` and `test_ci_path_filter.py` fully green. The plan closes this with a direct-tuple assertion — see row 13.
13. A test asserting `select_pytest_targets`' unioned output cannot pin a redundant row's existence, because the union hides which row supplied the target. Pinning it needs an assertion against the row's own tuple inside `CROSS_DOMAIN_EXCEPTIONS`, independent of any selection call. `[verified: select-tests.py:480-498; reproduced at plan review by deleting the row in a scratch copy and observing the suite stay green]`
8. `test_github_actions_workflows_rule_md_change_also_selects_hooks_tests`'s docstring claims a sibling rule file does not need `HOOKS_TESTS_DIR`; the fix makes that false. `[verified: test_select_tests.py:733-736]`
9. Editing `select-tests.py` forces a full-suite selection: `SELECT_TESTS_SCRIPT` is a `GLOBAL_TRIGGER_PATHS` member, and the global-trigger check runs before domain matching, returning `FULL_SUITE_TARGETS`. The documented command therefore widens on its own — CLAUDE.md Commands case 1, not a hand-widened run. `[verified: select-tests.py:60, 220, 227-231, 476-478]`
10. No CHANGELOG entry: #855, the larger cross-domain routing change this fix completes, added none, and the Unreleased section documents consumer-visible behavior changes rather than routing-table corrections. `[verified: grep of CHANGELOG.md for cross-domain/CROSS_DOMAIN/TestCrossDomainReadCompleteness — zero matches]`
11. No `README.md` change: its Tests section (`:530-536`) describes the mechanism generically and names no per-row targets; its Configuration files entries (`:248-249`) describe the two rules directories' contents, not their test routing. `[verified: README.md:245-249, 530-536]`
12. `test_claude_md_excludes.py:48-56` is the only durable source comment asserting the routing gap; every other repo hit is a committed plan file, preserved content under Axis 3. `[verified: Step 3 exploration repo-wide grep; CLAUDE.md scope Axis 3]`

**Mechanism justifications:**

- *Two target-tuple widenings, no new row, constant, predicate, or helper* — `anchors: root`. This is the lightest primitive the table offers, and the two heavier shapes were weighed against it. (a) A second standalone `_is_under(p, RULES_DIR)` row carrying `(HOOKS_TESTS_DIR,)`: rejected because the union semantics make its output identical while leaving two rows with the same predicate (row 1), and duplication with no named exception is a defect. (b) A shared constant or single predicate spanning both rules trees (a `RULES_DIRS` tuple, or `_is_rules_file_change`): rejected because the two directories are separate trees whose reader sets coincide today by accident, and `select-tests.py:168-171` already records that they are declared separately on purpose — collapsing them would erase a distinction the table deliberately holds.
- *Widening in place rather than following `d40b162`'s standalone-row precedent* — `anchors: row2`. That precedent turned on the widened row being a shared frozenset over eight unrelated constants; these are single-directory predicates with no spillover, so its reasoning does not carry.
- *Updating the two exact-equality assertions rather than relaxing them* — `anchors: row6`. Two lighter alternatives fail: relaxing to a membership check (`SKILLS_TESTS_DIR in set(...)`) stops catching over-selection, and deleting the tests leaves the rows' concrete behavior unpinned at the row level. Set equality against the corrected expectation keeps both directions pinned, with the derived completeness test as the independent check that the expectation is right.
- *No `_KNOWN_CROSS_DOMAIN_READS` entries* — `anchors: row4`, `anchors: row5`. The lighter option is also the correct one: the derived scanner already carries the fact, and the hand list mechanically rejects it.
- *Keeping the redundant `GITHUB_ACTIONS_WORKFLOWS_RULE_MD` row, and pinning it with a test rather than a comment* — `anchors: row7`, `anchors: row13`. Deleting it plus its constant, its `_EXACT_MATCH_LITERAL_PATH_CONSTANTS` entry (`test_select_tests.py:1054`), and its test is the heavier change and drops a true declaration whose lifetime differs from the directory row's; a redundant row in a union-based table costs nothing at selection time. But a comment alone is the wrong guardrail in a table-driven module whose whole point is mechanical enforcement: a future editor deletes the row as dead code, the suite stays green, and nothing connects that deletion to the later re-narrowing that makes it bite. Two lighter options fail — the existing `test_github_actions_workflows_rule_md_change_also_selects_hooks_tests` passes off the `RULES_DIR` contribution alone and so cannot see the deletion (row 7), and asserting on `select_pytest_targets`' output cannot attribute a target to a row (row 13). A direct assertion on the row's own tuple is the narrowest thing that pins it.

## Critical files

**One `code-writer` dispatch.** All three files carry one shared fact — which tests read the rules directories — and the test assertions are expectations *about* the table edit, so a split would force the same background into every prompt and let two agents resolve the redundancy question differently. This is exactly the "do not split" case in `plan-it`'s Name-the-dispatch-split rule.

**Modify `claude/.claude/scripts/select-tests.py`:**
- Line 437: `(lambda p: _is_under(p, RULES_DIR), (SKILLS_TESTS_DIR, HOOKS_TESTS_DIR)),`
- Line 446: `(lambda p: _is_under(p, ROOT_RULES_DIR), (SKILLS_TESTS_DIR, HOOKS_TESTS_DIR)),`
- Lines 405-406, 407-408, and 168-171: replace with the text prescribed in Approach. No new constant — `HOOKS_TESTS_DIR` (`:27`) already exists and is already a target on nine other rows.

**Modify `claude/.claude/scripts/tests/test_select_tests.py`:**
- `:726-731` → rename to `test_rules_dir_change_also_selects_hooks_and_skills_tests` (matching the `AGENTS_DIR` sibling at `:716`, its closest structural analogue), assert `set(result.target_paths) == {_mod.SKILLS_TESTS_DIR, _mod.HOOKS_TESTS_DIR}`, and name both readers in the docstring.
- `:856-862` → rename to `test_root_rules_dir_change_selects_hooks_and_skills_tests`, convert to set-equality with the same two members, and name both readers.
- `:733-736` → docstring only. Keep the first sentence; replace the trailing clause with a statement that the exact-match row and the `RULES_DIR` row both supply `HOOKS_TESTS_DIR` for this path, deferring to that row's comment in `select-tests.py` for why both are kept.
- `:1075-1076` → tighten the `_KNOWN_CROSS_DOMAIN_READS` "add a newly-mapped read here too" sentence per Approach. No entries added to the tuple itself, and `SKILL_FILES_READ_BY_HOOK_TESTS`, `_EXACT_MATCH_LITERAL_PATH_CONSTANTS`, `_FILE_TARGETS`, `MAPPED_TOP_LEVEL_DIRS`, and `MAPPED_ROOT_CLAUDE_DIRS` are all untouched.
- **New test**, added beside `test_github_actions_workflows_rule_md_change_also_selects_hooks_tests` at `:733`: `test_github_actions_workflows_rule_md_row_declares_hooks_tests_directly`. It scans `_mod.CROSS_DOMAIN_EXCEPTIONS` for the row whose predicate returns `True` for `_mod.GITHUB_ACTIONS_WORKFLOWS_RULE_MD` and `False` for a sibling path under `RULES_DIR`, and asserts that row's target tuple `== (_mod.HOOKS_TESTS_DIR,)`. Asserting the tuple inside the table, not `select_pytest_targets`' unioned output, is what makes it fail on the row's deletion (row 13). Docstring states the one durable fact: this row's declaration is narrower than the `RULES_DIR` row's and outlives changes to it.

**Modify `claude/.claude/hooks/tests/test_claude_md_excludes.py`:**
- `:48-56` → the three-line replacement in Approach. Nothing else in the file changes; the module docstring (`:1-22`) and the `_STOW_SOURCE_RULE_FILES` comment (`:43-44`) make no routing claim.

**Reuse:** `HOOKS_TESTS_DIR` (`select-tests.py:27`); `_is_under` (`:234-235`) unchanged; the multi-target row shape at `:436` and `:440` as the pattern to match. Nothing new is written.

Verification command for this dispatch (from the worktree root):

```bash
../../../.venv/bin/pytest claude/.claude/scripts/tests/test_select_tests.py claude/.claude/hooks/tests/test_claude_md_excludes.py
```

No `SKILL.md`, agent file, `.claude/rules/*.md`, or plugin-directory file is touched, so `/skill-review`, `/agent-review`, `ai-instruction-and-memory-files`, and `plugin-semver` do not apply. `/code-review` still runs before the commit.

## Verification

**Fail-first, before editing the table.** On this branch as it stands:

```bash
../../../.venv/bin/pytest claude/.claude/scripts/tests/test_select_tests.py -k TestCrossDomainReadCompleteness
```

It must fail naming exactly two candidates — `claude/.claude/rules` and `.claude/rules` — both attributed to `claude/.claude/hooks/tests/test_claude_md_excludes.py`. Paste that output into the PR body: it is the evidence that the fix's scope is two rows and not more, and it is unreconstructable once the table is fixed. A third path in that output means row 3 was wrong and the fix is wider than planned — declare the extra read rather than narrowing the test.

**After the change**, the project's own documented command plus lint:

```bash
../../../.venv/bin/python3 claude/.claude/scripts/select-tests.py
../../../.venv/bin/ruff check claude/.claude/
```

`select-tests.py` selects the full suite here on its own, because `claude/.claude/scripts/select-tests.py` is a `GLOBAL_TRIGGER_PATHS` member checked ahead of domain matching (`select-tests.py:227-231`, `:476-478`) — CLAUDE.md Commands case 1, so this is the tool widening, not a hand-widened run. ShellCheck is not needed: the diff contains no shell files. Worktree-relative `.venv` paths per `README.md:515`.

Beyond a green suite, confirm the fix at the selection level:

```bash
../../../.venv/bin/pytest claude/.claude/scripts/tests/test_select_tests.py -k "rules_dir or TestCrossDomainReadCompleteness or TestKnownCrossDomainReadsAudit"
```

`TestKnownCrossDomainReadsAudit` must stay green untouched — that is the direct check on row 4's decision not to add DAMP entries.

Then `/code-review` before the commit, per CLAUDE.md. The PR body must disclose that two passing exact-equality assertions were changed, state that they encoded the under-declaration as expected behavior, and point at the fail-first output as the independent evidence — a flipped assertion is the one diff shape a reviewer is right to read as a test bent to fit the code.

## Out of scope

- **The stale-base merge race tracked in #861** — branch protection, merge queues, required-status re-runs, or any check that a PR's base is current. Separate ticket, engineer-decided this session.
- **The `GH-846/findings-path-script` branch and commit `d40b162`.** Read as precedent for the widen-versus-standalone-row decision and set aside on the reasoning in row 2; nothing on that branch is re-worked here. The two changes have no functional dependency and no merge-order constraint: `d40b162` inserts a new row after `select-tests.py:433` and touches neither rules row nor `test_claude_md_excludes.py`, and `CROSS_DOMAIN_EXCEPTIONS` unions across rows, so whichever lands second takes at worst a line-shift merge. State this in the PR body — with many concurrent branches editing this one table, it saves the reviewer the same cross-branch check. `[verified: d40b162 diff, staff-platform-engineer plan review]`
- **Restructuring `CROSS_DOMAIN_EXCEPTIONS` beyond these two target tuples** — no collapsing of the two rules trees behind one predicate or shared constant, no conversion of the tuple-of-lambdas table to a declarative mapping, no reordering. The table's shape is not what failed.
- **Deleting the now-redundant `GITHUB_ACTIONS_WORKFLOWS_RULE_MD` row, constant, `_EXACT_MATCH_LITERAL_PATH_CONSTANTS` entry, and test.** A change the plan could make and deliberately will not (row 7): it drops a true, narrower declaration whose lifetime is independent of the directory row's, in exchange for no runtime benefit in a union-based table. The redundancy is recorded in the row's comment instead.
- **`docs/design-decisions.md` §47** — a dated design record covering the `claudeMdExcludes` worktree pattern. Preserved content under CLAUDE.md scope Axis 3.
- **Narrowing `test_claude_md_excludes.py`'s two rglobs to named rule files.** The globs are what make that module rename-robust (`:43-44`), and the fix declares the dependency rather than removing it.
- **A CHANGELOG entry.** Row 10: the larger #855 change added none, and this is an internal routing correction with no consumer-visible behavior change.
- **`.claude/plans/*.md` files mentioning the routing gap** — committed planning records, preserved under Axis 3. Row 12 confirms the only durable source comment asserting the gap is the one this plan rewrites.
- **Extending `TestCrossDomainReadCompleteness`'s resolver grammar or corpus.** It already caught this gap; nothing here argues it needs to see more.
