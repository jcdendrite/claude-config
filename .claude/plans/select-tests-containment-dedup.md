# select-tests.py: drop targets contained by another selected target

GH-882

## Context

`select-tests.py` hands pytest a target list that can contain both a
domain directory and a file inside that same directory; pytest silently
collects only the narrower argument and drops the directory, so a green
local run reports far less coverage than its own stderr line claims.
This surfaced on `hook-family-standardization-phase4`, where the hooks
domain collected 623 tests instead of 5267. It matters now because
`select-tests.py` is the documented verification command for every
`/code-review` and `/ready-for-review` run in this repo, so the under-run
silently weakens the gate every contributor relies on. The intended
outcome is that the target list handed to pytest never contains a path
strictly inside another selected target, that the scope printed to stderr
is the scope actually executed, and that a regression test pins both.

## Approach

Add one function that turns a selection into the concrete paths pytest receives — globs expanded, then any path another selected target already collects dropped — and route both `build_pytest_argv` and `main`'s stderr line through it, so the scope printed and the scope executed are the same list by construction. `select_pytest_targets` and `SelectionResult` are untouched: that function answers "which rules fired," and the new one answers "what does pytest get," which are two different questions that today's code conflates at the argv boundary.

Concretely, in `claude/.claude/scripts/select-tests.py`:

- `_covers(container, candidate)` — `container.rstrip("/")`, then `candidate != normalized and _is_under(candidate, normalized)`. It reuses `_is_under` rather than restating prefix math, and normalizes locally because `FULL_SUITE_TARGETS` carries trailing slashes (`select-tests.py:222`) that `_is_under`'s `directory + "/"` concatenation (`:236-237`) would turn into a never-matching `claude/.claude//` prefix.
- `resolve_target_paths(target_paths, *, repo_root) -> list[str]` — expands every target through the existing `_expand_target`, then applies **two distinct filters**, not one rule with a coda. They compose in either order; implement them as two named steps so neither is collapsed into the other:
  1. **Drop exact duplicates**, keeping the first occurrence. `_covers` excludes equality by definition, so the containment filter alone never removes a repeat.
  2. **Drop any path another surviving entry `_covers`.** Test each candidate against the whole expanded list, not against a progressively-shrinking working list — an all-pairs check against the original is what makes a three-level chain (`A`, `A/B`, `A/B/c.py`) collapse to `A` rather than leaving `A/B` behind.

  Output order: sorted, matching `select_pytest_targets`' own `tuple(sorted(targets))` and the sortedness `test_glob_target_expands_to_concrete_sorted_files_on_disk` already pins for glob expansion. State this in the docstring so a later refactor doesn't read an incidental order as contractual. The docstring's one durable fact: pytest collects nothing from a directory argument when another argument names a path inside it.
- `build_pytest_argv` becomes `[*resolve_target_paths(...), *list(passthrough_args)]` — same signature, same return type, so its three existing tests keep their shape.
- `main` computes `resolved_targets = resolve_target_paths(selection.target_paths, repo_root=repo_root)` **once**, immediately above the existing reason-printing chain, then uses that one value at both places that need it: the domain-selected print (`:642`, replacing `selection.target_paths`) and the `build_pytest_argv` call (`:644`, likewise). One value, reused — not two calls whose outputs happen to match. Placement matters, so it is specified rather than left to the implementer:
  - The full-suite branch's two prints are unchanged; they name the reason, not the targets.
  - The empty-target short-circuit stays gated on `selection.target_paths`, not on `resolved_targets`. Switching it would silently pull the zero-match-glob case (Out of scope) into this change.
  - The short-circuit's own invariant is that it precede `build_pytest_argv`, which it still does. Resolution running above it is harmless — an empty tuple resolves to an empty list with no `Path.glob` call.

  Passing an already-resolved list into `build_pytest_argv` is safe because `resolve_target_paths` is idempotent on its own output: nothing surviving it contains `*`, so the second pass makes no `Path.glob` call and removes nothing. Keeping the call inside `build_pytest_argv` rather than hoisting it out preserves that function's contract for its own tests and any future caller.

The fix does not depend on argv order. pytest's `Session.collect()` runs the matching walk for every initial argument, mutating the shared `_collection_cache`, before `genitems()` runs on any node, so the enclosing directory ends up cached whichever argument comes first. `select_pytest_targets`' sort happens to place a container before anything inside it (a container is always a lexicographic prefix of its own descendants), but that ordering is incidental, not load-bearing — say so in a comment near `resolve_target_paths` so a later switch to insertion order isn't mistaken for a behavior change.

The printed line therefore names concrete files where a glob target used to appear. That is the point rather than a side effect — the ticket's stated outcome is that stderr names the scope actually executed, and a glob pattern cannot express "expansions inside an already-selected directory were dropped."

Why resolution sits at the argv boundary and not inside `select_pytest_targets`: containment is only decidable once globs are concrete. `TRANSCRIPT_ANALYSIS_TEST_GLOB` (`:51`) happens to have a literal prefix under `SCRIPTS_TESTS_DIR`, so a raw-string check would work for all three collision shapes today — but the failure direction here is inverted from the usual selection tradeoff. A missed containment does not over-select safely; it silently deletes a whole domain's collection. A check that is only heuristically correct on its input is the wrong shape for that. Placing it earlier also has a concrete cost: six existing rule-table assertions pin `target_paths` containing a domain directory alongside a target inside it (`test_select_tests.py:387-388, 399, 547, 575, 670, 906`), and those assertions exist to express which rows fired. Dedup inside `select_pytest_targets` would rewrite them into something that no longer answers that question.

### Assumption ledger

**Root problem:** `select-tests.py` hands pytest a target list that can contain both a domain directory and a path inside it; pytest then collects nothing from the directory, so a green local run covers a fraction of what its own stderr line claims, silently weakening the `/code-review` and `/ready-for-review` gates.

**Givens:**

- **pytest's overlapping-initial-path collection semantics are upstream.** `_pytest/main.py`'s collection cache is pytest's, not this repo's, and the one upstream knob that changes it (`--keep-duplicates`) is priced and rejected below; the design can only avoid triggering the behavior.

Two further conditions this plan treats as fixed are *in* reach — `.github/workflows/tests.yml`'s full-suite invocation, and `select-tests.py`'s own `GLOBAL_TRIGGER_PATHS` membership are both this repo's files, changeable here. The plan declines to change either, so both are recorded in **Out of scope** with their reasons rather than as givens.

**Mechanism justifications:**

- `_covers` + `resolve_target_paths` at the argv boundary — `anchors: root`. This is the plan's one new mechanism, and it is heavier than a two-line set comprehension, so the lighter primitives were priced first:
  1. **`pytest --keep-duplicates`** (`anchors: row1`) — a real one-flag fix: `genitems` returns early only `if duplicate and not keepduplicates` (`_pytest/main.py:975-976`), so the flag restores the directory's children. It fails because the contained file is then collected twice (`handle_dupes` is disabled for `File` nodes at `:973`), so the printed scope and the executed scope still disagree — over-stating instead of under-stating — and it disables duplicate protection for every collection in every run, a wider blast radius than the bug.
  2. **Replace the file-shaped exception targets with their domain directories** (`anchors: row3`) — deletes the collision with no new code, and over-selects hard: any `.py` file under `claude/` would pull the full hooks domain instead of one file, which is the precise cost the narrow exception rows exist to avoid, on the most common single-file diff shape in the repo.
  3. **One pytest invocation per target** (`anchors: row1`) — pays N collections and N xdist spin-ups per run and needs exit-code aggregation, strictly worse than not passing overlapping arguments in the first place.
- Reusing `_is_under` inside `_covers` rather than a second prefix implementation — `anchors: row5`.
- Computing `resolve_target_paths` once in `main` and reusing the value at both the print site and the `build_pytest_argv` call — `anchors: row4`. Two separate calls would be equal only for as long as the filesystem holds still between them, which on a machine running several worktrees and agents against one checkout is an assumption rather than a guarantee. One value costs less and guarantees more.
- Two explicit filters inside `resolve_target_paths` (exact-duplicate, then containment) rather than one rule — `anchors: row15`. `_covers` excludes equality, so a single containment pass silently leaves repeats in.
- Deriving the regression invariant from `DOMAIN_RULES` and `CROSS_DOMAIN_EXCEPTIONS` only, excluding `FULL_SUITE_TARGETS` — `anchors: row13, row14`.
- One synthetic three-level containment fixture alongside the derived invariant — `anchors: row15`. The real corpus is uniformly one level deep, so transitive collapse has no derived coverage.

**Rows:**

1. `[verified: pytest 8.4.2's _pytest/main.py:964-976 and :916 in the project venv; counts from the four --collect-only commands below]` — the narrower argument's matching walk populates `_collection_cache` for the enclosing `Dir` node, so `genitems` later sees `duplicate=True` for that directory and returns without yielding anything. No warning, no error; the directory contributes zero tests. Reproduce the counts at this branch's merge-base with:

    ```bash
    pytest claude/.claude/hooks/tests --collect-only -q | tail -1                # 5267
    pytest claude/.claude/hooks/tests \
           claude/.claude/hooks/tests/test_ticket_reference_discipline.py \
           --collect-only -q | tail -1                                          # 623
    pytest claude/.claude/scripts/tests --collect-only -q | tail -1             # 2718
    pytest claude/.claude/scripts/tests \
           claude/.claude/scripts/tests/test_select_tests.py \
           --collect-only -q | tail -1                                          # 126
    ```
2. `[verified: select-tests.py:320-325, 343, 467 for the collision shape; the count is reproducible with the sweep below]` — a single `.py` file under `claude/` is enough to trigger it, because `_is_py_source_under_claude_or_plugins` fires alongside the domain rule for the same path. The ticket's "a diff touching both a hooks `.sh` and a hooks test file" understates the trigger. 175 of the 736 paths in `git ls-files` collide as single-file diffs, in three shapes. To re-derive: for each tracked path, resolve `build_pytest_argv(select_pytest_targets([path]).target_paths, [], repo_root=root)`, skipping full-suite results, and flag any argv entry that is a strict sub-path of another entry in the same argv. Both totals move as the tree changes; the shapes are what matter.
3. `[verified: select-tests.py:51, 584-592]` — `TRANSCRIPT_ANALYSIS_TEST_GLOB` reaches concrete paths only through `_expand_target`, called from `build_pytest_argv`; `select_pytest_targets` never sees the expansion.
4. `[verified: select-tests.py:642 vs :644]` — the printed line and the pytest argv are built from two separately derived lists today, which is why the stderr line survived the under-run intact.
5. `[verified: select-tests.py:222 vs :236-237]` — `FULL_SUITE_TARGETS` entries end in `/`, and `_is_under` would build a `claude/.claude//` prefix from one. Unreachable through today's rule table (a full-suite selection short-circuits before any other target joins it), which is why `_covers` normalizes rather than `_is_under` changing for every caller.
6. `[verified: repo-wide grep for "select-tests: running"]` — no test asserts the domain-selected stderr text; only the two full-suite lines are pinned (`test_select_tests.py:1565, 1577`). The print change adds a test rather than editing one.
7. `[verified: test_select_tests.py:387-388, 399, 547, 575, 670, 906]` — six rule-table assertions pin a `target_paths` tuple holding a directory plus a target inside it. Leaving `SelectionResult` unchanged keeps all of them meaningful.
8. `[verified: .github/workflows/tests.yml:160, 166 and :89]` — CI runs `pytest claude/.claude/ plugins/` twice and never `select-tests.py`. Its skip gate is a five-entry deny-list (`LICENSE`, `CHANGELOG.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`, `CONTRIBUTING.md`); any diff capable of triggering this bug contains a `.py` or a hooks/skills path, none of which is deny-listed, so no affected PR could have skipped the full suite.
9. `[verified: requirements-dev.txt:1]` — the declared pin is `pytest==8.*`, not a patch pin, and `claude/.claude/tests/test_pytest_collection_config.py:91-96` records 8.4.2 as the version its internals-introspection was checked against. A fresh `pip install -r requirements-dev.txt` can therefore land a different 8.x whose collection semantics differ, which is what makes pinning the behavior with a test worth its cost.
10. `[verified: claude/.claude/tests/test_pytest_collection_config.py:49-55, 206-220]` — that file already shells out to a real `[sys.executable, "-m", "pytest", ..., "--collect-only", "-q"]` subprocess and parses node ids by filtering on `"::"`, so the premise test has an established home and idiom there. `test_select_tests.py` does not: its module docstring commits to synthetic path lists and git fixture repos, and `TestRunPytest`'s docstring states the real pytest subprocess is never shelled out to.
11. `[unverified]` — `Path.glob` against a non-existent `repo_root` returns empty rather than raising. Nothing in the plan depends on it: the new `main`-level test uses `_REPO_ROOT` so expansion is real, and the existing fake-root tests carry no glob target.
12. `[verified: _pytest/main.py:775, :806-809, :843-961, :911-916]` — `perform_collect` drains `Session.collect()` for every initial argument, mutating the shared `_collection_cache`, before `genitems()` runs on any yielded node. The file argument's walk reaches the enclosing directory as an intermediate node with one remaining part that `.is_file()`, which sets `handle_dupes = False` and forces that directory to be cached. The bug is therefore order-independent, and `select_pytest_targets`' sort is not load-bearing for the fix.
13. `[verified: select-tests.py:494-496 vs :498-516]` — the global-trigger early return and the domain-target accumulation branch are mutually exclusive, so `select_pytest_targets` never returns `FULL_SUITE_TARGETS` mixed with domain targets; a full-suite result returns those two entries alone. Any regression test whose universe unions `FULL_SUITE_TARGETS` with the rule-table targets is therefore checking a state pytest never receives.
14. `[verified: union every target tuple in FULL_SUITE_TARGETS, DOMAIN_RULES, and CROSS_DOMAIN_EXCEPTIONS, expand globs against the repo root, then drop each entry another entry covers]` — resolving that mixed universe leaves exactly two survivors, `claude/.claude/` and `plugins/`, because `claude/.claude/` covers every other table target. The containment assertion then compares only those two disjoint directories and passes regardless of whether `_covers` is correct. Excluding `FULL_SUITE_TARGETS` reduces 9 entries to 4 survivors and drops all 5 contained paths, which is the check the test is for.
15. `[verified: DOMAIN_RULES and CROSS_DOMAIN_EXCEPTIONS target tuples]` — every container/contained pair in the current tables is exactly one level deep, so no derived test over the real corpus can exercise transitive collapse. That case needs a synthetic fixture or it is untested.
16. Nothing here is `[engineer-verified]`; no decision in this plan came from a direct utterance this session.

### Dispatch split

One `code-writer` dispatch covering all four files. The tests name the new function and its semantics, so a second dispatch would have to restate the same design in its own prompt and could resolve the naming or the printed-scope question differently — the case `plan-it` Step 5 names as a do-not-split.

## Critical files

**Modify — `claude/.claude/scripts/select-tests.py`**
Add `_covers` and `resolve_target_paths` next to `_expand_target` in the `# --- pytest invocation ---` block; rewrite `build_pytest_argv`'s body to delegate; change `main`'s domain-selected print (`:642`) to name the resolved list. Reuse: `_is_under` (`:236-237`) for the prefix test, `_expand_target` (`:584-592`) for glob expansion — do not add a second glob or `fnmatch` path. Leave `select_pytest_targets`, `SelectionResult`, `DOMAIN_RULES`, and `CROSS_DOMAIN_EXCEPTIONS` untouched; this change alters no selection semantics.

**Modify — `claude/.claude/scripts/tests/test_select_tests.py`**
Add `TestResolveTargetPaths` covering the two measured real-world collision shapes plus three structural cases the real corpus cannot produce:

- **Measured:** a directory absorbing a file target inside it (`HOOKS_TESTS_DIR` + `TICKET_REFERENCE_DISCIPLINE_TEST_PATH` → `[HOOKS_TESTS_DIR]`), and a directory absorbing a glob's expansions (`SCRIPTS_TESTS_DIR` + `TRANSCRIPT_ANALYSIS_TEST_GLOB` → `[SCRIPTS_TESTS_DIR]`).
- **Structural, synthetic:** a three-level chain (`A`, `A/B`, `A/B/c.py` → `[A]`), which is the case row 15 shows no derived test can reach; a repeated target appearing once, which `select_pytest_targets`' `set` makes unreachable in production and which exists to pin the exact-duplicate filter that `_covers` structurally cannot; and a trailing-slash container absorbing a directory inside it, with a docstring naming `FULL_SUITE_TARGETS` as the shape that requires the normalization.
- **Negative:** two sibling directories both surviving, asserted as an exact ordered list against the documented sorted-output contract.
- **Idempotency:** feed `resolve_target_paths`' own output back into it and assert the result is unchanged. `main` passing an already-resolved list into `build_pytest_argv` rests on this, and nothing else pins it directly.

Docstrings must not present the repeated-target or three-level cases as observed collisions — they are structural coverage for branches the corpus doesn't exercise.

Add the derived invariant — build the target universe from `DOMAIN_RULES` and `CROSS_DOMAIN_EXCEPTIONS` only, resolve it, and assert no surviving pair satisfies `_covers`. **Exclude `FULL_SUITE_TARGETS`**: rows 13 and 14 show that including it collapses the check to two disjoint survivors and makes the assertion pass regardless of whether `_covers` works, while approximating a selection `select_pytest_targets` never returns. Guard the exclusion directly — `assert not (set(_mod.FULL_SUITE_TARGETS) & universe)` — rather than by any assertion on survivor counts. Row 14 shows why: the masked universe still collapses 11→2, so a count-based guard holds in both the healthy and the regressed state and distinguishes nothing, while a zero-pruning failure is already caught by the containment assertion itself. This self-updates when a row gains a target, which the alternative — row 2's per-tracked-path sweep — does not do any better while costing a full `select_pytest_targets` pass per file. Reuse `_REPO_ROOT` (`:32`), and follow `TestCrossDomainReadCompleteness` (`:323-356`) for the collect-violations-then-assert-once shape.

Add to `TestBuildPytestArgv` (`:989`) one test that `build_pytest_argv` resolves before appending passthrough args — asserting a directory-plus-contained-file input yields `[directory, *passthrough]`, with the expected value written literally rather than re-derived from `resolve_target_paths`. Do not assert `build_pytest_argv(t, p, r) == [*resolve_target_paths(t, r), *p]`: both sides call the same function on the same inputs, so no defect inside it can fail that test, and `build_pytest_argv` never touches the print site whose drift it would claim to pin.

Add to `TestMainComposition` (`:1430`) the test that actually pins printed-equals-executed, parametrized over **two** selection shapes so the pin is not single-instance: `["claude/.claude/hooks/__init__.py"]` (file-inside-directory) and a path selecting the scripts/glob pair. Monkeypatch `resolve_repo_root` to `_REPO_ROOT` (not a fake root — these selections contain a glob that must really expand) and `compute_changed_paths` to each shape. For the first, assert the recorded argv equals `[HOOKS_TESTS_DIR, *_mod._expand_target(TRANSCRIPT_ANALYSIS_TEST_GLOB, repo_root=_REPO_ROOT)]` and that `TICKET_REFERENCE_DISCIPLINE_TEST_PATH` is absent. For both, assert stderr's `select-tests: running ...` line names exactly the recorded argv's target entries — that comparison across two independent call sites in `main` is the regression test for row 4's historical bug. Reuse the existing `fake_run_pytest` recorder idiom (`:1466-1473`) and `capsys` (`:1551`).

**Modify — `claude/.claude/tests/test_pytest_collection_config.py`**
Add one class pinning the pytest premise: write two synthetic test files under `tmp_path` (`test_a.py`, `test_b.py`), run `--collect-only -q` twice — the directory alone, then the directory plus `test_b.py` — and assert the **exact** node-id set, not a count inequality. The first run must collect both files' tests; the second must collect exactly `test_b.py`'s, with `test_a.py`'s fully absent. Row 1 establishes that the directory contributes zero tests, so "strictly fewer" would stay green against a partial fix or an unrelated one-item collection change; the exact set is what the evidence supports.

Reuse the file's established subprocess idiom — `[sys.executable, "-m", "pytest", ..., "--collect-only", "-q", "-n0", "--rootdir", str(tmp_path)]`, `capture_output=True`, `timeout=120`, and `"::" in line` node-id filtering (`:49-55`, `:206-220`) — rather than inventing a new timeout value. Use `tmp_path` as `--rootdir`, since the collected files live there. Run with `cwd=tmp_path` so the repo's own `addopts` are out of scope; `-n0` is belt-and-braces there, matching the sibling's own comment.

Put the interpretation in the **assertion message**, not only a docstring: a pytest release that fixes this upstream turns the test red, and a red here means an upstream assumption changed, not that `resolve_target_paths` broke — its dedup stays correct either way. A traceback shows the assertion message, never the docstring, so the reader who hits this needs the disambiguation inline. This file needs no rule-table entry: `claude/.claude/tests/` is in `DELIBERATELY_UNMAPPED_TOP_LEVEL_DIRS` and falls open to the full suite by design.

**Modify — `CHANGELOG.md`**
One entry under `[Unreleased]` → `### Changed`, in the register of the existing `select-tests.py` entry at line 14: the scope printed to stderr is now the scope pytest executes, a domain directory is no longer silently dropped when a selected file sits inside it, and the printed line now names concrete files where a glob pattern used to appear.

The entry must also name the **magnitude**, not only the mechanism: affected diffs will see a substantially longer run than before, because the domain was previously under-collected — 623 tests where 5267 were intended for the hooks pair, 126 where 2718 were intended for the scripts pair. Without that sentence the first contributor to hit a post-fix run reads an 8–20x slowdown as a new performance regression rather than as restored coverage. No README.md or CLAUDE.md edit is warranted alongside it: both describe the tool qualitatively ("just the test domains implicated by what you changed") and neither statement becomes false. `CHANGELOG.md` maps to `()` in `DOMAIN_RULES` (`:350`), so including it widens nothing.

## Verification

Run from the worktree root. README.md's Tests section (line 515) documents the worktree-relative form — linked worktrees are exactly three levels deep and never carry their own `.venv`, so `../../../.venv/bin/...` resolves to the main checkout's interpreter.

```bash
../../../.venv/bin/python3 claude/.claude/scripts/select-tests.py
../../../.venv/bin/ruff check claude/.claude/
```

The first is the repo's documented required local command (README.md:533-536, CLAUDE.md Commands). Expect it to print `select-tests: running the full suite (global-trigger: claude/.claude/scripts/select-tests.py)` and run `pytest claude/.claude/ plugins/`: this diff touches a `GLOBAL_TRIGGER_PATHS` member (`select-tests.py:229-233`), which is CLAUDE.md's named exception 1 — `select-tests.py` widened on its own, so no by-hand full-suite invocation is warranted or needed. The second is README.md:508's lint command; no shell files change, so ShellCheck does not apply.

During iteration, `../../../.venv/bin/pytest claude/.claude/scripts/tests/test_select_tests.py claude/.claude/tests/test_pytest_collection_config.py` narrows the loop (two file arguments, no containment between them). The gate is the `select-tests.py` run above, not this one.

Expected results: the full suite green, including the six existing rule-table assertions that pin a directory alongside a target inside it (unchanged by design), the three existing `TestBuildPytestArgv` cases, and `test_empty_target_selection_with_passthrough_args_still_skips_run_pytest`. Ruff clean.

## Out of scope

- **A remediation sweep of already-merged branches**, and any change to `.github/workflows/tests.yml`. Row 8 holds the evidence: CI runs the full suite independently of `select-tests.py`, and no diff capable of triggering this bug could have skipped it. The damage is therefore confined to local pre-push signal, and the workflow needs no change to bound it.
- **Removing `select-tests.py` from `GLOBAL_TRIGGER_PATHS`** to get a narrower verification run on this branch. The entry declares that the rule table cannot be trusted to select tests for itself once changed (`select-tests.py:229-233`) — this branch is that entry's exact use case, so removing it would delete the guard at the moment it applies.
- **Changing the file-shaped exception targets to their domain directories.** Priced in the ledger and rejected on over-selection; it would also invalidate the narrow-target rationale recorded at `select-tests.py:52-58` and `:62-66`.
- **A glob target that expands to nothing.** Resolution cannot empty a non-empty list — only strict descendants are dropped, so a maximal element always survives — but a glob matching no files on disk still can, and `main` would then hand pytest only passthrough args and collect the whole rootdir. Distinct trigger, pre-existing, and it needs its own fail-closed decision rather than riding along here.
- **Tightening `pytest==8.*` to a patch pin.** The new premise test converts a silent semantics change in a future 8.x into a red test, which catches more than a pin would (a pin bounds the version, not the behavior) at no dependency-policy cost.
- **A `docs/design-decisions.md` entry.** §53's own precedent keeps the ledger and rejected alternatives in the plan file on its branch rather than restating them there; the CHANGELOG entry covers the consumer-visible change.
- **Any change to `DOMAIN_RULES`, `CROSS_DOMAIN_EXCEPTIONS`, or `SelectionResult`.** This plan changes what pytest receives, not what the rules select.
