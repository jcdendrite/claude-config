# Fix conftest.py module-name collision between hooks/tests and scripts/tests

## Context

Fix a reproducible pytest bug: `claude/.claude/hooks/tests/conftest.py` and
`claude/.claude/scripts/tests/conftest.py` both register under the bare
module name `conftest` in pytest's default "prepend" import-mode, because
neither `claude/.claude/hooks/`, `claude/.claude/hooks/tests/`,
`claude/.claude/scripts/`, nor `claude/.claude/scripts/tests/` has an
`__init__.py`. Whichever one pytest's plugin manager loads first into
`sys.modules['conftest']` wins that slot for the whole process; any test
file in the *other* directory doing `from conftest import <name>` then gets
an `ImportError` for names that exist only in the shadowed file (or, worse,
silently reads the wrong file's contents when the two happen to define the
same name). This was flagged as an out-of-scope, informational finding by
another session while it was investigating an unrelated slow/flaky test
run; the user asked for it to be fixed as its own change now. Intended
outcome: a fix plus a regression test that makes this collision structurally
impossible to hit again — without breaking the full-suite invocation
(`pytest claude/.claude/ plugins/`), `select-tests.py`'s domain-scoped
invocations, or any of the existing `from conftest import <name>` call
sites.

### Evidence gathered before design

- Repro confirmed: `pytest claude/.claude/scripts/tests/test_transcript_analysis.py claude/.claude/hooks/tests/test_lib.py --co -q` fails collection with `ImportError: cannot import name '_agent_use' from 'conftest' (.../claude/.claude/hooks/tests/conftest.py)`.
- Full-suite invocation (`pytest claude/.claude/ --co -q`, matching CI's `pytest claude/.claude/ plugins/`) collects cleanly today (7054 tests) — the collision is specific to invocations passing explicit paths spanning both `hooks/tests/` and `scripts/tests/` as separate CLI arguments, which is exactly `select-tests.py`'s domain-target concatenation shape (`HOOKS_TESTS_DIR`, `SCRIPTS_TESTS_DIR` constants feeding `build_pytest_argv()`).
- Only two `conftest.py` files sit in the affected tree (a third, unrelated one lives under `plugins/lovable-cloud/tests/`); none of the intervening directories has an `__init__.py` today.
- No other `test_*.py` basename collides across directories in this repo.
- pytest's own docs (`docs.pytest.org/en/stable/explanation/pythonpath.html`) confirm the "prepend" mode collision mechanism and that `--import-mode=importlib` avoids it but at the documented cost that "test modules can't import each other."

This evidence, plus a from-source read of pytest 8.4.2's own import machinery, was handed to `plan-architect` to settle the mechanism; its findings superseded some of the above (notably the true call-site count) and are recorded in the Approach section below.

## Approach

Give `claude/.claude/hooks/` and `claude/.claude/scripts/` (and each one's `tests/` subdirectory) a real package identity with four empty-but-documented `__init__.py` marker files, so pytest's default prepend import mode names the two conftests `hooks.tests.conftest` and `scripts.tests.conftest` instead of both competing for the single `sys.modules["conftest"]` slot. Convert all 34 `from conftest import <name>` statements across 31 test files to the relative form `from .conftest import <name>`, which resolves through the real package parent that the markers create. Pin the result with two tests in the repo's existing pytest-collection-invariant file — one asserting every tracked `conftest.py` resolves to a unique, package-qualified module name, one running the actual multi-argument collection that reproduces the bug, in both argument orders.

**Correction to the evidence section above, established by grep this session:** the call-site count is **34 statements across 31 files** (14 files under `hooks/tests/`, 17 under `scripts/tests/`), not 28 across 14+14. Four of those statements are **inside function bodies**, not at module top level — `claude/.claude/scripts/tests/test_worktree_lib.py:280`, `:298`, `:318`, and `claude/.claude/scripts/tests/test_cleanup_idle_open_pr_worktrees.py:717`. A mechanical rewrite anchored on the naive zero-indentation pattern `^from conftest import` misses all four, and `test_worktree_lib.py` is invisible to that pattern entirely (it has no top-level conftest import). Those four would then fail at *test-execution* time rather than collection time — a much quieter failure than the one being fixed.

### Root problem

**Root.** Both `claude/.claude/hooks/tests/conftest.py` and `claude/.claude/scripts/tests/conftest.py` are non-package modules, so pytest names both `conftest`; only one can occupy `sys.modules["conftest"]` at a time, and every `from conftest import <name>` statement in a test body reads whichever one happens to be there. The fix must make the two names structurally distinct, not merely make the current invocations happen to order correctly.

### Givens

- **G1.** pytest's prepend import mode, its conftest-eviction behavior, and its package-root walk are pytest's own semantics; this repo can select among the modes pytest offers but cannot change what any mode does. *(Vendor-imposed.)*
- **G2.** `claude/` is a stow package mapping 1:1 to `~/.claude/`, so any file added under `claude/.claude/**` installs onto every contributor who runs `./install.sh`. Changing that distribution model is a separate decision outside this plan. *(Repo architecture, owned by `install.sh` and README.md's stow contract.)*
- **G3.** `claude/.claude/tests/` is listed in `select-tests.py`'s `DELIBERATELY_UNMAPPED_TOP_LEVEL_DIRS`, so any edit under it forces a full-suite selection. Narrowing that would require redesigning the rule table, which this plan does not touch. *(Owned by `select-tests.py:145-150`, deliberate by its own comment.)*

### Mechanisms

Each mechanism below cites an `anchors:` list — the specific claims it exists to satisfy. `root` points at the Root problem section above. `row N` points at item N in the Assumption ledger further below in this file.

- **M1 — Four `__init__.py` markers** at `claude/.claude/hooks/__init__.py`, `claude/.claude/hooks/tests/__init__.py`, `claude/.claude/scripts/__init__.py`, `claude/.claude/scripts/tests/__init__.py`. *anchors: root, row 6, row 7* — this is the only change that makes `resolve_package_path` return non-`None`, which is simultaneously the condition that suppresses pytest's `sys.modules["conftest"]` eviction and the condition that produces distinct dotted names.
- **M2 — Rewrite all 34 conftest imports to `from .conftest import ...`.** *anchors: row 8, row 4* — once the markers exist, the leaf `tests/` directory is no longer inserted into `sys.path`, so the bare form stops resolving entirely; the relative form is the only one that keeps working, and it must cover the four function-body sites too.
- **M3 — Structural uniqueness test** over every tracked `conftest.py`. *anchors: root, row 13* — asserts the invariant the root states, across the whole repo rather than the two directories that happen to collide today.
- **M4 — Two-argument collection test, both orders.** *anchors: row 3* — the structural test alone would keep passing if pytest changed its naming rule; this one exercises the literal `select-tests.py` argv shape that fails today.
- **M5 — Correct two claims in `claude/.claude/hooks/tests/conftest.py`.** *anchors: row 8, row 5* — its module docstring instructs readers to write `from conftest import _seed_session`, which M2 makes false, and its `_dead_pid` docstring asserts the two conftests are in separate pytest rootdirs, which is false today and more visibly false after M1.
- **M6 — One sentence in README.md's Tests section.** *anchors: root* — a contributor adding a third `claude/.claude/<domain>/tests/` tree needs to know the two markers are required; discovering it from a failing uniqueness assertion tells them something broke but not what to add.

### Alternatives weighed and set aside

The over-powered-primitive check here runs in the unusual direction: the question is whether anything is *lighter* than four marker files plus a mechanical rename. Three candidates, all read out of pytest 8.4.2's own source at `.venv/lib/python3.14/site-packages/_pytest/pathlib.py`:

**`--import-mode=importlib` — rejected.** It does fix the naming: `import_path` lines 535-566 fall through to `module_name_from_path`, yielding `claude._claude.hooks.tests.conftest` versus `claude._claude.scripts.tests.conftest`. But it never inserts anything into `sys.path` (its own docstring, lines 513-515: "uses more fine control mechanisms provided by `importlib` to import the module, which avoids having to muck with `sys.path` at all"), so all 34 bare imports break with no cheaper repair than M2 anyway. The relative-import repair is then *worse* under this mode than under M1: the parent package that `insert_missing_modules` creates is a bare `ModuleType` with no `__path__` (lines 824-827), so `from .conftest import X` only resolves if the conftest is already in `sys.modules` under exactly that generated name — order-dependent, versus M1's real package parent that can import its own submodule from disk unconditionally. This also matches pytest's published guidance for the mode ("Test modules can't import each other"; testing utility modules in the tests directories "are not importable"). Same call-site cost, strictly more fragile, and it changes import semantics for every file in the suite including `plugins/` and `evals/`.

**`consider_namespace_packages = true` plus adding `claude/.claude` to `pythonpath` — rejected, and it is the trap worth naming.** On paper this is the lightest option: two ini lines, zero new files. Reading `resolve_pkg_root_and_module_name` (lines 856-897) confirms it would work for the hooks tree — candidate `claude/.claude` yields `hooks.tests.conftest`, and `is_importable` succeeds once that directory is on `sys.path`. It fails on two counts. First, `claude/.claude/scripts` is *already* on `pythonpath`, so the loop at lines 885-890 breaks one level earlier for the scripts tree and names its conftest `tests.conftest` while the hooks tree gets `hooks.tests.conftest` — asymmetric names produced by which ancestor happens to be on `sys.path`. Second, prepend mode mutates `sys.path` during the run (`sys.path.insert(0, str(pkg_root))`, line 583), so which ancestor wins for a given file can depend on collection order. That is the same class of order-dependence the root problem describes, relocated rather than removed. And it still requires the identical 34-site rewrite, because neither leaf `tests/` directory ends up on `sys.path` under it either. By contrast `resolve_package_path` (lines 839-853) is a pure filesystem walk with no `sys.path` input at all, so M1's names are deterministic.

**Move the shared helpers into distinctly-named sibling modules** (`hook_test_helpers.py` / `script_test_helpers.py`) and leave the conftests as pure fixture files — rejected. Same order of churn as M2, and it removes the symptom without touching the mechanism: `sys.modules["conftest"]` stays last-writer-wins, so the day someone adds a third conftest and imports it by name, the bug returns. Holding it would need a "no test file imports `conftest` by name" lint — a convention plus a guard to enforce the convention, where M1 makes the language itself produce distinct names.

### Assumption ledger

1. **The collision mechanism is pytest's deliberate conftest eviction, not an import-cache accident.** `_importconftest` at `.venv/lib/python3.14/site-packages/_pytest/config/__init__.py:704-709` calls `resolve_package_path(conftestpath)` and, when it returns `None`, does `del sys.modules[conftestpath.stem]` before importing — its own comment reads "conftest.py files there are not in a Python package all have module name 'conftest', and thus conflict with each other. Clear the existing before loading the new one." So pytest never raises on the duplicate; it silently makes `sys.modules["conftest"]` last-writer-wins. `[verified: _pytest/config/__init__.py:700-720]`
2. **The full suite passing is an ordering accident, not evidence of safety.** With a single directory argument, each directory's conftest is loaded immediately before that directory's modules are imported, so the eviction happens to leave the right module in the slot at the right moment. Nothing in pytest guarantees that interleaving. `[verified: _pytest/config/__init__.py:638-668 (_loadconftestmodules) + the evidence section's second bullet, above, on the clean 7054-test collection]`
3. **The trigger is two or more initial path arguments, and the victim flips with argument order.** `_set_initial_conftests` iterates `args` in order at config time (`for initial_path in args`, line 572) and preloads each anchor's conftests *before* any test module is imported; the `_dirpath2confmods` short-circuit (line 653) and the `get_plugin` short-circuit (line 696) then prevent any reload during collection. So the last argument's conftest holds the slot for the whole run. The evidence section's repro, above, named scripts first and hooks second, and the hooks conftest won — exactly this path. Reversing the two arguments moves the `ImportError` to the hooks file. `[verified: _pytest/config/__init__.py:547-596, 638-668, 687-698; consistent with the evidence section's first bullet, above]`
4. **34 conftest-import statements across 31 files, 4 of them inside function bodies.** 14 files under `hooks/tests/` (14 statements), 17 under `scripts/tests/` (20 statements). The function-body sites are `test_worktree_lib.py:280,298,318` and `test_cleanup_idle_open_pr_worktrees.py:717`. This supersedes the evidence section's "28 call sites, 14/14," which came from the naive zero-indentation `^from conftest import` grep. `[verified: repo-wide grep for ^\s*(import conftest|from conftest import|from . import conftest), 34 matches]`
5. **`_dead_pid` is defined in *both* conftests, so the collision's failure mode is not uniformly loud.** `claude/.claude/hooks/tests/conftest.py:46` and `claude/.claude/scripts/tests/conftest.py:630`. In a run where the hooks conftest holds the slot, `scripts/tests/test_post_crash_sessions.py:22`'s `from conftest import _dead_pid` succeeds silently against the wrong module. The two implementations are behaviorally identical today, so no bug is currently observable — but that is luck, and it means "no ImportError" is not proof the collision is absent. `[verified: both files read this session]`
6. **The package root can never climb above `claude/.claude/`.** `resolve_package_path` breaks on any ancestor whose name is not a Python identifier (line 850-851), and `.claude` is not one. So the dotted names are pinned at exactly `hooks.tests.conftest` and `scripts.tests.conftest`, and `pkg_root` is pinned at `claude/.claude`, regardless of what appears above. `[verified: _pytest/pathlib.py:839-853]`
7. **Adding only `tests/__init__.py` reproduces the collision one level up.** With no `hooks/__init__.py`, the walk stops at `claude/.claude/hooks`, giving module name `tests.conftest` — and the scripts tree gives the same name. Both levels are required; this is the obvious half-measure and it silently fails. `[verified: _pytest/pathlib.py:839-853 + compute_module_name at 926-943]`
8. **After M1, the bare form stops resolving — the rewrite is mandatory, not cosmetic.** Prepend mode inserts `pkg_root` (`claude/.claude`), not the leaf `tests/` directory, so `conftest` is no longer a top-level name. `from .conftest import X` resolves because `hooks.tests` is a real package with a real `__path__`, and it is legal inside a function body as well as at module top level. `[verified: _pytest/pathlib.py:568-587]`
9. **`test_select_tests.py`'s hyphenated-script import is unaffected.** It resolves `select-tests.py` via `Path(__file__).parent.parent` (path-based), loads it with `importlib.util.spec_from_file_location("select_tests", ...)` under an explicit name, and never stores it in `sys.modules`. Its `sys.path.insert(0, str(_SCRIPT.parent))` puts `claude/.claude/scripts` ahead of `claude/.claude`, which is harmless: an `__init__.py` sitting in a `sys.path` *root* directory is inert, since a path entry is a directory and not itself a package. `[verified: claude/.claude/scripts/tests/test_select_tests.py:21-25]`
10. **The existing `pythonpath` entries keep working, and the one latent hazard is unrealized.** `transcript_analysis` stays importable as a top-level package via the `claude/.claude/scripts` entry, so `scripts/tests/conftest.py:28` and every `from transcript_analysis... import` site is untouched. With `claude/.claude` also on `sys.path`, `scripts.transcript_analysis` becomes *reachable* as a second, distinct module object — which would split the module identity that `scripts/tests/conftest.py`'s `fake_projects` fixture depends on. Nothing in the repo imports it that way, and nothing in this change introduces such an import. `[verified: pyproject.toml:18 + repo-wide grep found no scripts.transcript_analysis import]`
11. **`claude/.claude` newly appears at `sys.path[0]` during test runs, exposing `hooks`, `scripts`, `tests`, `skills`, `agents`, and `rules` as top-level namespace-package names.** I did not enumerate the venv's installed top-level module names, so I cannot rule out shadowing an installed distribution. Nothing in this repo imports any of those bare names. The full-suite run in Verification closes this: shadowing would surface as collection or import errors, and the collected-test count is a known quantity. `[unverified]`
12. **No test enumerates `*.py` files under the `hooks/` or `scripts/` roots, so the four new files trip no inventory assertion.** The directory-enumeration tests glob `*.sh` (`test_hook_alignment.py:49,53`, `test_skills.py:1866`, `test_no_bash4_constructs.py:40`) or scope to `transcript_analysis/` and explicitly skip `__init__.py` (`test_transcript_analysis_architecture_doc.py:32`). `[verified: repo-wide grep for .glob(/.rglob( across claude/.claude/**/*.py]`
13. **`plugins/lovable-cloud/tests/conftest.py` needs no change — but the evidence section's stated reason is wrong.** There is one `pyproject.toml` and therefore one rootdir; that conftest is not on a "different pytest rootpath branch." It carries `plugins/lovable-cloud/tests/__init__.py`, so `resolve_package_path` returns non-`None` for it and pytest's bare-`conftest` eviction never applies — it occupies `sys.modules["tests.conftest"]`, not the contested slot. It escapes the symptom for a further reason: nothing under `plugins/` imports `conftest` by name anywhere in the repo. Its name stays `tests.conftest` before and after M1, distinct from both dotted names, so the uniqueness test passes with it included. `[verified: repo-wide conftest.py glob (exactly 3) + repo-wide conftest-import grep (0 matches under plugins/) + pyproject.toml is the only pytest ini + repo-wide __init__.py glob (exactly 2, one of which is plugins/lovable-cloud/tests/__init__.py)]`
14. **`select-tests.py` will select the full suite for this diff, correctly and on its own.** The regression tests land in `claude/.claude/tests/`, whose top-level directory name is in `DELIBERATELY_UNMAPPED_TOP_LEVEL_DIRS`, so it falls open to `FULL_SUITE_TARGETS`. This is CLAUDE.md's documented carve-out #1 for a full-suite run, not a licence widened by hand. The `__init__.py` files themselves map narrowly: `hooks/__init__.py` selects `HOOKS_TESTS_DIR` plus `TRANSCRIPT_ANALYSIS_TEST_GLOB`, `scripts/__init__.py` selects `SCRIPTS_TESTS_DIR`. `[verified: select-tests.py:145-150, 168-179, 241-249, 305-306]`
15. **Ruff's isort rule will fire on the rewrite.** `from .conftest import X` is a local-folder import, which sorts into a block after third-party and first-party, so every top-level site currently sitting next to `import pytest` moves. `select = ["E", "F", "B", "I", "UP", "SIM"]` includes `I`; `TID252` (banned relative imports) is not selected, so the relative form itself is permitted. `[verified: pyproject.toml:5-6 + current import ordering at test_select_tests.py:18-19]`
16. **The four marker files ship to every stow consumer.** Per G2 they land at `~/.claude/hooks/__init__.py` and `~/.claude/scripts/__init__.py` on every machine running `./install.sh`. They are inert for hook execution (hooks are invoked as standalone scripts by path, never imported as package members) and inert as `sys.path`-root files per row 9. `[verified: G2's stow contract + row 9's path-root reasoning]`

### Regression-test shape: both, and neither one synthetic

This plan's first design pass read this file's convention as "spawn a tiny synthetic pytest run in a `tmp_path`." That is one of two shapes the file actually uses, and it is the wrong one here. `test_strict_markers_rejects_unregistered_marker` uses `tmp_path` because the thing under test is an ini option's effect on an arbitrary file. `TestNestedWorktreeExcludedFromCollection` and `TestTimingMarkerCoverageParity` both run against this repo's own live tree, because the thing under test is a property of *this* checkout. This bug is the second kind.

A synthetic two-package `tmp_path` fixture would prove that pytest's packaging rule separates two same-named conftests — a fact about pytest, which would keep passing after someone deleted `claude/.claude/hooks/__init__.py`. That is a regression test that cannot regress. So:

- **The structural test asserts against the repo's real conftest set**, discovered via `git ls-files` (the convention `test_shellcheck.py:63` and `test_ci_path_filter.py:74` already use for "every tracked file," and it excludes nested worktrees for free). It resolves each one through pytest's own `resolve_pkg_root_and_module_name`, passing `consider_namespace_packages=False` to match what pytest itself uses here (`pyproject.toml` sets no such key, so pytest's own default applies), and mirroring `import_path`'s prepend-mode fallback to `(path.parent, path.stem)` on `CouldNotResolvePathError` (`_pytest/pathlib.py:568-573`). It then asserts three things, not two:
  - **(a) Pairwise-unique module names** across every tracked `conftest.py` in the repo.
  - **(b) A dotted name** — every conftest under `claude/.claude/` resolves to a package-qualified name containing a `.`.
  - **(c) An exact package root** — every conftest under `claude/.claude/` resolves to a `pkg_root` equal to `REPO_ROOT / "claude" / ".claude"`.

  **(c) carries the guard; (a) and (b) do not subsume it, and dropping it as redundant re-opens the gap.** Row 6 establishes that a correctly-marked tree pins `pkg_root` at exactly `claude/.claude`, because `resolve_package_path` cannot climb past `.claude` (its name fails `isidentifier()`). So `pkg_root == claude/.claude` is an exact restatement of "both of this tree's markers are present," not a proxy for it. Now delete one *domain* marker and keep the leaf one — `claude/.claude/hooks/__init__.py` removed, `claude/.claude/hooks/tests/__init__.py` kept. The walk stops one level low, giving name `tests.conftest` and `pkg_root` `claude/.claude/hooks`: row 7's failure mode exactly, reproduced one level up. Against that state:
  - **(b) passes** — `tests.conftest` contains a dot.
  - **(a) is not a guard here on its own, only a coincidence** — it fires only because a *third* conftest also resolves to `tests.conftest` today: `plugins/lovable-cloud/tests/conftest.py` carries its own `tests/__init__.py` while its parent `lovable-cloud` fails `isidentifier()`, so its walk stops at the same shape and it resolves to `tests.conftest` with `pkg_root` `plugins/lovable-cloud` (row 13, corrected). That match is an accident of a directory this plan deliberately does not own (see Out of scope), and it disappears the moment that plugin's packaging changes — deleting *both* hooks-side domain markers instead (colliding hooks directly against scripts) or deleting a *leaf* marker (raising `CouldNotResolvePathError`, tripping (b)) are the other two single-file deletions, and only (c) catches all of them for a reason internal to the tree under test.
  - **(c) fires** — `claude/.claude/hooks != claude/.claude` — for a reason internal to the tree under test, with no dependency on a third party.

  Build each conftest path as `REPO_ROOT / <git ls-files output>` so both sides of the (c) comparison derive from the same `REPO_ROOT`; constructing one side by any other route risks a symlink-resolution mismatch in this repo's stow-symlinked, worktree-nested checkout.

  Dogfooding pytest's own resolver rather than reimplementing the `__init__.py` walk follows the precedent `TestNorecursedirsDefaultsPreserved` sets ("reading the default from pytest itself rather than a second hardcoded copy that could drift"), and carries the same accepted-trade-off comment that file already uses at lines 88-93 for private-API access.
- **The behavioral test pins the trigger shape**, parametrized over both argument orders per row 3, since a single order proves only half the invariant.

## Critical files

**Single `code-writer` dispatch.** The packaging change, the 34 import rewrites, and the two regression tests share one body of background — how pytest names a conftest, and why the markers must be paired. Splitting would force that background into two prompts and let two agents resolve the same detail differently, which `plan-it` Step 5 names as the case not to split. The whole change is roughly 40 small mechanical edits driven by one mechanism.

**Create:**

- `claude/.claude/hooks/__init__.py`
- `claude/.claude/hooks/tests/__init__.py`
- `claude/.claude/scripts/__init__.py`
- `claude/.claude/scripts/tests/__init__.py`

  Each contains exactly one line and nothing else — the full rationale lives in the regression test's class docstring, so these defer to it rather than restating it four times:

  ```python
  """Package marker for the conftest module-name invariant — see claude/.claude/tests/test_pytest_collection_config.py."""
  ```

  This cross-file deferral matches the pattern `pyproject.toml:26` and `:31` already use to point at the same test file. All four are required; three of four reintroduces the bug under a different name (row 7).

**Modify — 31 test files, 34 statements, `from conftest import` → `from .conftest import`:**

- Under `claude/.claude/hooks/tests/` (14 files, all top-level): `test_lib.py:22`, `test_lib_worktree_collision_guard.py:19`, `test_marker_script.py:12`, `test_marker_worktree_keying.py:24` (keeps its `as _seed_session_at` alias), `test_require_code_review.py:12`, `test_require_memory_skill.py:10`, `test_require_plan_review.py:13`, `test_require_ready_for_review.py:11`, `test_require_respond_pr.py:8`, `test_require_routing_read.py:7`, `test_require_skill_review.py:9`, `test_require_worktree_for_file_writes.py:9`, `test_require_worktree_for_git_writes.py:10`, `test_review_ledger_script.py:12`.
- Under `claude/.claude/scripts/tests/` (17 files, 20 statements): `test_autonomous_shipping_active.py:16`, `test_branch_divergence_status.py:12`, `test_cleanup_idle_open_pr_worktrees.py:21` **and `:717`**, `test_cleanup_merged_branches.py:20`, `test_context_composition.py:13`, `test_post_crash_sessions.py:22`, `test_pr_cost_section.py:22`, `test_pr_diff_against_base.py:16`, `test_respond_pr_safe_patch.py:18`, `test_select_tests.py:19`, `test_skill_fidelity_report.py:16`, `test_token_analyzer.py:10`, `test_transcript_analysis.py:18`, `test_transcript_cost.py:13`, `test_transcript_reviewer_yield.py:10`, `test_transcript_workstream_cost.py:10`, `test_worktree_lib.py:280`, **`:298`, `:318`**.

  The bolded line numbers are the four function-body imports (row 4). They take the same `.` prefix — relative imports are legal inside a function body. A naive zero-indentation anchor (`^from conftest import`, no leading whitespace) misses all four, which is why row 4's original `^`-anchored discovery pass missed them too. This plan's own whitespace-tolerant pattern (`^[[:space:]]*(import conftest|from conftest import)`, used in row 4's corrected discovery grep and the Verification completeness check) does match all four textually. But a text match only proves the import statement exists — it doesn't prove the import resolves, since a function-body statement only executes when its enclosing test runs, not at collection time. That gap is what the dedicated execution check in Verification closes, independent of any grep. After the rewrite, re-run the whitespace-tolerant search to confirm zero remaining bare `conftest` imports repo-wide.

**Modify — `claude/.claude/hooks/tests/conftest.py`:** two claims that M2 makes false.

- The module docstring (line 8) instructs readers to write `from conftest import _seed_session`. Update to the relative form.
- `_dead_pid`'s docstring (lines 47-51) asserts the two conftests live in a "separate pytest rootdir, so not importable directly." There is one rootdir; the parenthetical is wrong today and more conspicuously wrong after M1. Replace the reason with the true one in one sentence, e.g. *"Duplicated in scripts/tests/conftest.py rather than shared, so neither test tree's conftest imports the other's."* Keep the existing sentence about spawning a real process — that one is accurate and load-bearing.

**Modify — `claude/.claude/tests/test_pytest_collection_config.py`:** add two test classes following this file's existing shape (subprocess-based, live-tree, `REPO_ROOT` from `helpers`).

- `TestConftestModuleNamesAreUnique` — the structural test described above, asserting all three parts: **(a)** pairwise-unique resolved module names across every tracked `conftest.py`; **(b)** every conftest under `claude/.claude/` resolves to a name containing a `.`; **(c)** every conftest under `claude/.claude/` resolves to a `pkg_root` equal to `REPO_ROOT / "claude" / ".claude"`. Part (c) is the one that fails on a single missing domain marker — (a) and (b) both pass on that deletion, for the reason the Approach section spells out — so it is not redundant with the other two. Pass `consider_namespace_packages=False` explicitly, matching what pytest itself uses under this repo's `pyproject.toml`. Its class docstring is the canonical home for the mechanism and must state each of the following as its own sentence:
  1. Two sibling test trees each carry a `conftest.py`.
  2. Without a package identity, pytest's prepend mode names both `conftest` and evicts the previous one from `sys.modules` before loading the next, so that slot is last-writer-wins.
  3. A test file's own `from .conftest import X` is an ordinary Python import that reads that slot.
  4. The `__init__.py` markers give each tree a dotted name so both coexist.
  5. The package root is asserted as well as the name, because a tree missing only its domain marker still produces a dotted name.

  Each assertion's failure message names the two files a domain tree needs — `<domain>/__init__.py` and `<domain>/tests/__init__.py` — so a contributor who trips it learns what to add rather than only that something broke. Carry a one-line comment for the `_pytest.pathlib` private-API use naming the alternative (hand-reimplementing the `__init__.py` walk, which would drift from pytest's own rule), matching the existing precedent at lines 88-93.

  Also add a narrow unit test of the resolution helper's own `CouldNotResolvePathError` fallback branch (the `(path.parent, path.stem)` tuple mirroring `import_path`'s fallback, per row 8), run against a synthetic unpackaged path rather than the live tree. After M1, every tracked `conftest.py` resolves via `resolve_package_path` and never exercises this branch, so the live-tree assertions above give it no coverage — a bug in the fallback's own tuple handling would stay latent until a future unpackaged conftest is added, which is exactly the moment this guard needs to fire correctly.
- `TestMultiArgCollectionSpansTestDomains` — parametrized over both argument orders, running `[sys.executable, "-m", "pytest", first, second, "--collect-only", "-q", "--rootdir", str(REPO_ROOT)]` with `cwd=REPO_ROOT` and asserting exit 0. Pick one file per tree that imports a name existing *only* in its own conftest, so a wrong bind fails loudly rather than silently per row 5: `claude/.claude/scripts/tests/test_token_analyzer.py` (imports `_write_subagent_jsonl`) and `claude/.claude/hooks/tests/test_require_routing_read.py` (imports `_seed_session`). One comment line for why both orders. Do **not** copy the `-c str(REPO_ROOT / "pyproject.toml")` flag from `test_strict_markers_rejects_unregistered_marker` — that flag exists there because its argument lives in `tmp_path`; here both arguments are under `REPO_ROOT` and `cwd` is `REPO_ROOT`, so config discovery finds `pyproject.toml` on its own. `-n0` is unnecessary for the same reason the file already documents at lines 29-31. Give this class its own docstring, distinct from its sibling's: state the trigger shape it reproduces (two initial path arguments whose conftests both preload before either test module imports, order-dependent) and that it is independent of `TestConftestModuleNamesAreUnique` — a future pytest release could change the naming rule this plan targets while still preserving the order-dependent eviction behavior, and only this class would catch that regression.

  Neither class takes the `timing` marker, so `TestTimingMarkerCoverageParity`'s partition assertion is unaffected.

**Modify — `README.md`, Tests section (line 497+):** one sentence, placed with the layout/mechanism notes after the `pytest-xdist` sentence at line 513, stating that a test directory under `claude/.claude/` carrying a `conftest.py` needs an `__init__.py` in both itself and its parent domain directory, and pointing at `claude/.claude/tests/test_pytest_collection_config.py` for the invariant. Do not restate the mechanism here; the test class docstring owns it.

**Reuse opportunities (do not reimplement):**

- `git ls-files -z` with `cwd=REPO_ROOT` for tracked-file discovery — the established pattern at `claude/.claude/hooks/tests/test_shellcheck.py:63` and `test_ci_path_filter.py:74`. It excludes nested worktrees and untracked scratch files without a hand-written filter.
- `REPO_ROOT` from `claude/.claude/tests/helpers.py`, already imported at the top of the target test file.
- `_pytest.pathlib.resolve_pkg_root_and_module_name` and `CouldNotResolvePathError` for name resolution, rather than a hand-rolled `__init__.py` walk.
- The subprocess-invocation shape of `TestNestedWorktreeExcludedFromCollection` (lines 47-53) — `sys.executable -m pytest`, `cwd=REPO_ROOT`, `--rootdir`, `timeout=120`, assert on `returncode` with `stdout`/`stderr` in the message.

**Do not touch:** `pyproject.toml` (no `--import-mode` or `consider_namespace_packages` change — see the rejected alternatives), `plugins/lovable-cloud/tests/conftest.py`, `claude/.claude/tests/__init__.py` (must not be created — it would split `helpers` into two module objects and break the `REPO_ROOT` identity that 30+ files depend on), and `claude/.claude/scripts/select-tests.py`.

## Verification

Run the repo's own documented scoped command from the worktree:

```bash
../../../.venv/bin/python3 claude/.claude/scripts/select-tests.py
```

Per row 14 this will select the full suite on its own, because the regression tests land in `claude/.claude/tests/`, whose directory name is in `DELIBERATELY_UNMAPPED_TOP_LEVEL_DIRS`. That is CLAUDE.md's documented carve-out #1 — `select-tests.py` widened, not the agent. Do not run a bare `pytest claude/.claude/` in its place; the widening must come from the tool so the selection stays auditable. This run is also what closes row 11's unverified `sys.path` shadowing risk: a shadowed import surfaces as a collection error, and the collected count should match the 7054 the evidence section's baseline established.

Then the two targeted checks that prove the specific bug is gone, both of which fail on `main` today:

```bash
../../../.venv/bin/pytest claude/.claude/scripts/tests/test_token_analyzer.py claude/.claude/hooks/tests/test_require_routing_read.py --co -q
../../../.venv/bin/pytest claude/.claude/hooks/tests/test_require_routing_read.py claude/.claude/scripts/tests/test_token_analyzer.py --co -q
```

Both must exit 0. Confirm the reverse-order command fails on the merge-base before the fix as well as the forward-order one — that is what establishes row 3's order-symmetry claim empirically rather than by reading pytest's source alone.

Then **execute**, not merely collect, the four function-body import sites from row 4. Every command above and both new test classes stop at collection, and a relative import inside a function body resolves at call time, so a clean collection proves nothing about these four. The full-suite run does execute them, but a green aggregate does not name them — and these are the sites the naive zero-indentation anchor would have missed in the first place, so they get their own named check:

```bash
../../../.venv/bin/pytest -n0 -v \
  "claude/.claude/scripts/tests/test_worktree_lib.py::TestResolveWorktreeForBranch" \
  "claude/.claude/scripts/tests/test_cleanup_idle_open_pr_worktrees.py::TestGhFailureModes::test_no_upstream_remote_is_a_usage_error"
```

`TestResolveWorktreeForBranch` contains all three `test_worktree_lib.py` sites — `test_branch_with_worktree_resolves_path` (:280), `test_branch_without_worktree_resolves_empty` (:298), and `test_locked_worktree_resolves_lock_flag_and_pid` (:318). `test_no_upstream_remote_is_a_usage_error` (:717) is the fourth. `-n0` overrides `pyproject.toml`'s `-n auto` per that file's own comment (lines 19-22) and `-v` lists each test by name, so all four sites are individually visible in the output rather than aggregated across xdist workers. This is not a collision reproduction — both arguments sit under `scripts/tests/`, so no cross-domain conftest is involved — and it is not meant to be; it is a plain runtime check that the four rewritten imports resolve when their functions actually run.

Lint, which row 15 predicts will have something to say:

```bash
../../../.venv/bin/ruff check claude/.claude/
```

ShellCheck is not needed — no shell file changes.

Finally, confirm the rewrite is complete. Scope the search to **tracked** files: a bare `grep -r` over `claude/.claude/` already matches on an unmodified `CLAUDE_CONFIG_DIR`-mapped checkout, because the gitignored `claude/.claude/projects/**/tool-results/` and `claude/.claude/file-history/**` directories hold session-transcript and tool-cache text containing the literal string. Its "zero matches" reading is therefore false on arrival, and false for reasons unrelated to this change. `git grep` searches tracked working-tree files by default, which is the same tracked-file scoping the structural test uses:

```bash
git grep -nE '^[[:space:]]*(import conftest|from conftest import)' -- claude/.claude
```

Must print nothing. Two notes on reading it. Its exit status is 1 when there are no matches, so check the output rather than chaining it on `&&`. And run it once *before* the rewrite too: it should print exactly the 34 lines across 31 files that row 4 enumerates and nothing from `projects/` or `file-history/`, which is the cheapest confirmation that the scoping is right.

That pattern is anchored and so does not cover the one prose mention of the bare form — `claude/.claude/hooks/tests/conftest.py`'s module docstring has it inside backticks, mid-sentence. Confirm that one by reading the file, per M5.

## Out of scope

- **`plugins/lovable-cloud/tests/conftest.py` and its directory.** It keeps its existing `tests/__init__.py` and resolves to `tests.conftest`, unique against both dotted names (row 13). It has no name-importer anywhere in the repo, and packaging its parent directory further would put an `__init__.py` inside a distributed plugin tree for no present benefit. The new uniqueness test scans it along with everything else, so the day a second plugin gains a `conftest.py` the guard fires on its own.
- **Packaging `claude/.claude/skills/tests/` or any other test directory without a `conftest.py`.** Audited as a structural sibling; it is not the same bug shape, since it has no conftest to collide and no same-basename test module anywhere else in the tree. Scope follows the bug.
- **Creating `claude/.claude/tests/__init__.py`.** Actively harmful, not merely unnecessary: `helpers` would remain importable as a top-level module via the `pythonpath` entry *and* become importable as `tests.helpers`, producing two module objects with independent `REPO_ROOT` state.
- **Consolidating the duplicated `_dead_pid`.** M1 makes a shared home genuinely available for the first time — `claude/.claude/tests/helpers.py` is already on `pythonpath` and already imported by both trees — but doing it would touch six more files for a helper whose two copies are behaviorally identical, and DAMP test scaffolding is a named exception to the single-source-of-truth rule. M5 corrects the *reason* recorded in the docstring; the duplication itself stays.
- **A repo-wide same-basename audit beyond `conftest.py`.** The discovery pass's `find ... | uniq -d` check already came back empty for test-module basenames, and the new uniqueness test covers the conftest class specifically. Generalizing it to every collected module is a broader invariant than this bug justifies.
- **Narrowing `select-tests.py` so edits under `claude/.claude/tests/` stop forcing a full-suite run.** Real friction, and it makes every change to the collection-invariant file expensive — but it is a rule-table redesign with its own blast radius across `MAPPED_TOP_LEVEL_DIRS` and `DELIBERATELY_UNMAPPED_TOP_LEVEL_DIRS`, and the current behavior is deliberate per that file's own comment (G3). Raise separately.
