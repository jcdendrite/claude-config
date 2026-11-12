# transcript-analysis hygiene follow-ups (GH-1432 PR A)

## Context

Goal: land PR A of GH-1432, the prose, stale-reference, and missing-test cleanups left open after GH-1116 closed, without changing any production behavior.

Ask: GH-1432 "PR A: hygiene", scope confirmed via label "PR A only (Recommended)"; widened by the engineer's selection of label "Yes, add it" for `edit_format.py:84-86`; test placement left to an independent review by the engineer's words "The issue is just a starting point what is the correct solution here". PR B (moving the inline subparsers out of `cli.py`) gets its own plan after PR A merges, as the issue orders them.

Why now: the issue orders PR A first because PR B rewrites `cli.py`'s import block, and fixing prose there first would have the same text reviewed twice. Nothing here is broken.

Findings from reading each site on `main` (9b088214):
- `docs/transcript-analysis-architecture.md:835-836`: "Older test files that load their own copy of `transcript-analysis.py` ... keep working, because the script re-exports both names" compares to a prior state. It needs restating as a durable fact.
- `claude/.claude/scripts/tests/test_transcript_analysis_cost_import_direction.py` module docstring (lines 1-14): the "not covered" statements are spread through one paragraph (module-attribute reads; non-function constants/classes; other modules' by-name imports in `cli.py`; test files' own `cost` imports).
- `test_transcript_analysis_multi_root_scope.py:363` ("in the plan's line numbering", with `:2146`, `:2148` line numbers from a committed plan) and `:413` ("by this plan") cite a plan's line numbers and scope that no longer locate their code: those call sites now sit at `skill_invocation.py:106-110`.
- `test_transcript_shared_fixtures.py` near line 31: no plan reference found there (`grep -n plan` returns nothing), so that site needs no edit.
- `test_transcript_context_composition.py:652-653` cites `test_transcript_analysis.py TestCostMultiRootReport, e.g. :6419`. That file no longer exists. `TestCostMultiRootReport` now lives in `test_transcript_cost.py:861`.
- `claude/.claude/hooks/tests/test_check_claude_md_length.py:889-890` cites `test_transcript_analysis.py`'s `test_bootstrap_fallback_hooks_matches_every_hook_declaring_deny_gate_label`. That test now lives in `test_transcript_denials.py:332`.
- No test covers `sessions` without `--paths`: `sessions.py:33-35` prints an error and exits 2, and `git grep "paths is required"` finds only the production code. `test_transcript_analysis_module_test_files.py:16` maps `sessions` to `test_transcript_analysis_multi_root_scope.py`, and the issue proposes that file. The Approach chooses a different home and records why.
- `claude/.claude/scripts/transcript_analysis/edit_format.py:84-86` cites "this plan's headline token figures" and a `measure_overhead.py` that appears only in committed plans. The issue does not list it. The engineer widened PR A to include it.

## Approach

Fix each stale or past-tense site in place, keeping every fact and dropping only the dead pointer or the comparison to a prior state. Add the `sessions` refusal test in a new `tests/test_transcript_sessions.py`, not in `tests/test_transcript_analysis_multi_root_scope.py`, which GH-1432 proposes (rows 2, 9 and 20). The new file forces three coupled edits, listed below. Production code changes in one comment only, at `transcript_analysis/edit_format.py:84-86`. No other file under `transcript_analysis/` changes, and neither does `transcript-analysis.py`.

**Per site, in the Context's order** (paths under `claude/.claude/scripts/` unless named in full):
- **`docs/transcript-analysis-architecture.md:835-837`.** Replace the "Older test files ... keep working, because the script re-exports both names" sentence with a present-tense one: a test file that loads its own copy of `transcript-analysis.py` via `spec_from_file_location` can read `build_parser` and `main` from that copy instead, because the script re-exports them. The sentences before and after stay (row 3).
- **`tests/test_transcript_analysis_cost_import_direction.py` docstring (:1-16).** Restructure it without dropping a fact:
  - a lead naming the two pins as (a) cost.py's public function surface equals its sanctioned set and (b) no guarded name crosses into cli.py by a by-name import from cost.py, in either the absolute or the level-1 relative form;
  - the existing rationale: the package reads cost.py's public names as `cost.<name>`, so a patch reaches every reader, and a by-name import in cli.py would bind at import time and bypass it;
  - a `Not covered:` list of exactly four items, each keeping any reason the current text gives:
    - module-attribute reads such as `from transcript_analysis import cost` then `cost.X`;
    - cost.py's top-level constants and classes, which (a) skips while (b) still checks every name cli.py imports, whatever it denotes;
    - cli.py's by-name imports from other modules;
    - test files' own `from transcript_analysis import cost` imports, which reach private helpers for patching and stay unguarded on purpose.

  No code in that file changes (row 14).
- **`tests/test_transcript_analysis_multi_root_scope.py:362-366`.** Delete the parenthetical "(:2146, :2148 in the plan's line numbering)". The rest of the docstring holds (row 7).
- **`tests/test_transcript_analysis_multi_root_scope.py:410-413`.** Restate the docstring as: at one root, cmd_skill_invocation's `--projects` branch calls `iter_sessions(roots[0], ...)` directly (a single Path, not a list), so `iter_sessions`' documented flat-sort-over-full-paths ordering governs a single-root run. This drops "keeps calling" and "unaffected by this plan".
- **`tests/test_transcript_shared_fixtures.py`.** No edit, because it holds no plan reference (Context).
- **`tests/test_transcript_context_composition.py:652-653`.** Replace "(test_transcript_analysis.py TestCostMultiRootReport, e.g. :6419)" with "(test_transcript_cost.py's TestCostMultiRootReport)". The class name locates the target, and a line number would drift again (row 5).
- **`claude/.claude/hooks/tests/test_check_claude_md_length.py:889-890`.** Replace "test_transcript_analysis.py's" with "scripts/tests/test_transcript_denials.py's". The path prefix is there because the cited file lives in a different tests tree from the citing one (row 6).
- **The `sessions` refusal test**, in new `tests/test_transcript_sessions.py`:
  - a one-line module docstring saying the file tests the sessions command in `transcript_analysis/sessions.py`;
  - `import pytest` (the body uses `pytest.raises`), `from transcript_analysis import cli` and `from .conftest import _asst, _write_jsonl`, the conftest-import shape `tests/test_transcript_commit_gate.py:8` and `tests/test_transcript_pr_link.py:10` use. The file imports no `sessions`: the test reaches `cmd_sessions` only through `args.func`, and ruff's `F` rules (`pyproject.toml:6`) fail an unused import;
  - one test, `test_sessions_without_paths_exits_2_and_emits_no_path`, requesting `fake_projects` and `capsys`. Its name states the scenario and the outcome, per `claude-skills/skills/test-conventions/SKILL.md` § "5. Test naming and structure". Its one-line docstring says the seeded transcript would reach stdout if the refusal ran after the scan. Its body:
    - arrange: `_write_jsonl(fake_projects / "sess.jsonl", [_asst("claude-sonnet-5")])` (row 23);
    - act: `args = cli.build_parser().parse_args(["sessions"])`, then `args.func(args)` under `pytest.raises(SystemExit)`, the dispatch `main()` makes (row 22);
    - assert: exit code 2, `--paths is required` in stderr, and empty stdout.

  Parsing through `build_parser()` and dispatching through `args.func` pins the parser's real `--paths` default and the `set_defaults(func=cmd_sessions)` binding by behavior, so no identity assertion restates `sessions.py:69`. `fake_projects` points the scan at the seeded fixture corpus. The suite-wide autouse `_isolate_transcript_corpus_lookups` (`tests/conftest.py:1630`) already keeps every test off the developer's real config dir.
- **Coupled edits for the new file:**
  - delete the `"sessions": "test_transcript_analysis_multi_root_scope.py",` line from `MODULES_TESTED_ELSEWHERE` (`tests/test_transcript_analysis_module_test_files.py:16`; row 10);
  - add `"test_transcript_sessions.py"` to `TEST_FILES` in `tests/test_transcript_package_module_prefixes.py` (row 11);
  - rewrite the `sessions` sentences at `docs/transcript-analysis-architecture.md:1009-1011` as these three, keeping the pr-link sentence that starts on :1011 (row 12):
    - `tests/test_transcript_sessions.py` covers `sessions.py`'s `--paths` refusal.
    - The cross-subcommand header table in `tests/test_transcript_analysis_multi_root_scope.py` covers the `sessions` resolved-scope header.
    - `tests/test_transcript_cli_bootstrap.py` covers `sessions`' dispatch and its `--paths --include-subagents` emission, in a subprocess.

    Naming the multi-root file gives "header table" its antecedent. "In a subprocess" keeps the doc from implying that the new file covers emission.
- **`transcript_analysis/edit_format.py:84-86`.** Cut the comment to `# ~4 chars/token, a standard rough estimate (not a per-tokenizer` / `# measurement) for English/code text.`, dropping "— the same ratio measure_overhead.py used to produce this plan's headline token figures." No code line changes (rows 15, 18 and 19).

**Placement alternatives set aside:**
- **`tests/test_transcript_analysis_multi_root_scope.py`**, GH-1432's proposal (row 2), which the engineer called "just a starting point" (row 20). At 549 lines it has room under the 1,000-line limit, but the placement rule puts a test in the file named for its subject. That file's subject is the cross-subcommand scope invariant, and a `--paths` refusal is not about scope (row 9). The `MODULES_TESTED_ELSEWHERE` entry records where sessions' only coverage lives today, and it does not decide where a sessions-only test goes.
- **`tests/test_transcript_cli.py`.** The rule reserves it for subcommands `build_parser()` registers inline and for tests across commands. `sessions.py:52-69` registers its own subparser.
- **A subprocess case in `tests/test_transcript_cli_bootstrap.py`.** It spawns a fresh interpreter for a check an in-process call makes completely.

**Assumption ledger.**

**Root:** Transcript-analysis tests and docs carry prose that cites a deleted test file, plan line numbers that no longer locate their code (row 25), or a prior state, and no test pins that a bare `sessions` invocation refuses with exit 2. This PR fixes the prose and adds the test, with no production behavior change: its one production edit is a comment. `[verified: read of each Context site; sessions.py:33-35]`

**Givens:**
- G1. Committed plans, `docs/case-studies/`, and `docs/reports/` are preserved records and stay read-only. Reason: `claude/.claude/CLAUDE.md` § "Working Style" (Scope discipline, Axis 3) imposes it, and relaxing it is a decision outside this plan. `[verified: CLAUDE.md Axis 3; the same classification at .claude/plans/edit-format-and-instrument-authoring.md:202]`
- G2. Agents run `select-tests.py`, not the full suite. Reason: the repo's `CLAUDE.md` § "Commands" sets it, and changing it is outside this plan. `[verified: CLAUDE.md "Commands"]`

**Rows:**
1. Scope is GH-1432's PR A only. `[engineer-verified: "PR A only (Recommended)"]` The option description under that label was the session's proposal, so this plan relies on the label alone.
2. GH-1432 proposes `tests/test_transcript_analysis_multi_root_scope.py` for the `sessions` refusal test, citing the `MODULES_TESTED_ELSEWHERE` mapping at `tests/test_transcript_analysis_module_test_files.py:16`. `[verified: gh issue view 1432]`
3. `transcript-analysis.py` re-exports `build_parser` and `main` (:7, `__all__` at :10). Transcript test files still load it via `spec_from_file_location`, e.g. `tests/test_transcript_cache_rebuild.py:28` and `tests/test_transcript_handoff_signal_response.py:19`. The doc sentence's claim holds today, and only its "Older ... keep working" framing compares to a prior state. `[verified: read; grep]`
4. No `.py` file pins the architecture-doc text at :835-837 or :1009-1011. `[verified: grep of *.py for "Older test files", "re-exports both", "No test covers", "no test file of its own"]`
5. `TestCostMultiRootReport` lives at `tests/test_transcript_cost.py:861` and asserts per-root `cost: account-N: scanned` lines (:872-873). `[verified: read]`
6. `test_bootstrap_fallback_hooks_matches_every_hook_declaring_deny_gate_label` lives at `tests/test_transcript_denials.py:332`. It regex-searches hook source with `_DENY_GATE_LABEL_DECLARATION_RE` (`tests/conftest.py:1440`), so the citing docstring's "extracts a bash constant out of hook source the same way" still holds. `[verified: read; grep]`
7. `skill_invocation.py` routes scope at :106 (`_iter_glob_scoped_sessions`), :108 (`corpus.iter_sessions(roots[0], ...)`), and :110 (`_iter_scoped_sessions`), and never calls `_resolve_project_scope`. `[verified: grep]`
8. `cmd_sessions` prints `sessions: --paths is required ...` to stderr and exits 2 at `sessions.py:33-35`, before `scope.resolve_scan_roots` at :38. No test asserts it. `tests/test_transcript_cli_bootstrap.py` covers only `sessions --help` (:607) and `--paths --include-subagents` (:641). `[verified: read; grep]`
9. `.claude/rules/transcript-analysis-tests.md:13-18` puts a test in the file named for its subject, `tests/test_transcript_<module>.py` for one package module. Its :21-22 sends one command's CLI wiring to the file of the module that registers its subparser, and `sessions.py:52-69` registers `sessions`' own. The refusal's subject is `sessions.py`. `tests/test_transcript_analysis_multi_root_scope.py:1` scopes that file to resolved-scope headers, roots threading, and SKILL.md scope text. `[verified: read]`
10. `tests/test_transcript_analysis_module_test_files.py:66-69` fails on a `MODULES_TESTED_ELSEWHERE` entry whose module has a file named for it, so creating `tests/test_transcript_sessions.py` forces deleting the entry at :16. No `test_transcript_sessions*.py` exists yet. `[verified: read; glob]`
11. `tests/test_transcript_package_module_prefixes.py:518-529` fails when a loader-free, package-importing `test_*.py` is missing from `TEST_FILES` (:61 onward). :467-477 requires each listed file to make at least one `<module>.<name>` read, which `cli.build_parser` satisfies. `[verified: read]`
12. `docs/transcript-analysis-architecture.md:1009-1011` says `sessions.py` has no test file of its own and that no test covers the `--paths` refusal. This change makes both false. `[verified: read]`
13. `select-tests.py:628` selects the whole `scripts/tests` directory for any path under `scripts/`. The new file reads no file by path, so it needs no exception row (:113-135). `[verified: read]`
14. The four uncovered cases in the cost_import_direction docstring are parallel exclusions, spread through one paragraph (:4-5, :6-9, :13, :13-15). `[verified: read]`
15. `transcript_analysis/edit_format.py:84-86` cites `measure_overhead.py` and "this plan's headline token figures", and names no plan. `measure_overhead.py` appears only there and in committed plans. `[verified: read; grep]`
16. The new file's first path component under `claude/.claude/` is `scripts`, which exists on main, so `require-stow-reminder.sh` (:28-30, :170-173) does not require the PR body to name stow. `[verified: read]`
17. A command's own test file already parses through the real parser in process: `tests/test_transcript_skill_invocation.py:661` calls `cli.build_parser().parse_args(["skill-invocation"])`. `[verified: read]`
18. `transcript_analysis/edit_format.py:84-86` is in PR A's scope. `[engineer-verified: "Yes, add it"]`
19. Cutting that clause changes no behavior and breaks no pin. `_EDIT_FORMAT_CHARS_PER_TOKEN` (:87, read at :388) keeps its value. `read_scope.py:18-20` cites that constant and its "rough English/code estimate", and both survive the cut. No `.py` file under `claude/` other than `edit_format.py` names `measure_overhead`. `[verified: read; grep]`
20. GH-1432's placement is a starting point and does not bind this plan. `[engineer-verified: "The issue is just a starting point what is the correct solution here you may want to get a different agent to take a look"]` The quote names no file, so the placement rests on row 9's reasoning.
21. An independent staff-sdet review this session reached the same home, `tests/test_transcript_sessions.py`. It is evidence for row 9, not the engineer's decision. `[verified: staff-sdet review, this session]`
22. `main()` runs `parsed.func(parsed)` at `cli.py:1029`, after a `--config-dir` branch (:995-1028) that a bare `sessions` skips. `sessions.py:69` binds `func=cmd_sessions`. `[verified: read]`
23. `fake_projects` creates its project dir empty (`tests/conftest.py:1539-1541`). A single-root run without `--this-repo` reads `iter_sessions` (`scope.py:611-624`), which yields only files with records (`corpus.py:214-217`). Unseeded, a bare `sessions` prints nothing to stdout wherever the refusal runs, so the empty-stdout assertion needs one seeded record before it can fail. `[verified: read]`
24. No repo script compares two versions of a file with docstrings stripped. The nearest, `tests/_move_fidelity.py remainder`, compares top-level statements by `ast.dump` (:451-469), so a method-docstring edit changes the whole enclosing class there. `[verified: grep of *.py and *.sh for get_docstring, "strip.*docstring" and "ast.dump.*docstring"; read]`
25. The `:2146` and `:2148` at `tests/test_transcript_analysis_multi_root_scope.py:363` come from `.claude/plans/transcript-corpus-multi-account-scope.md:181-187`, which exists. `skill_invocation.py` makes those calls at :106-110 today (row 7), so the numbers no longer locate their code. `[verified: read; grep]`

**Mechanisms:**
- M1: In-place prose rewrites at five sites: architecture doc :835-837, multi_root_scope :362-366 and :410-413, context_composition :652-653, and check_claude_md_length :889-890. `anchors: root, row3, row4, row5, row6, row7, row25`.
- M2: A `Not covered:` list in the cost_import_direction docstring, because `claude/.claude/CLAUDE.md` § "Durable text" asks for an explicit list when facts are parallel exclusions. `anchors: root, row14`.
- M3: A new module-named test file with one in-process test that parses through `build_parser()` and dispatches through `args.func`, plus its three coupled edits. `anchors: row2, row8, row9, row10, row11, row12, row13, row17, row20, row21, row22, row23`. The alternatives are under "Placement alternatives set aside" above.
- M4: One `code-writer` dispatch with no split. `anchors: row10, row11, row12`.
- M5: A comment-only cut at `edit_format.py:84-86` that keeps the ratio and its "rough estimate (not a per-tokenizer measurement)" hedge, and drops the clause naming `measure_overhead.py` and "this plan". `claude/.claude/CLAUDE.md` § "Durable text" bars a comment whose meaning depends on a planning document. Retargeting the clause at a committed plan was set aside: the hedge already states the value's basis, and a reader of the constant needs no plan's figures. `anchors: row15, row18, row19`.

## Critical files

**Dispatch: one `code-writer`.** The new test file and its three coupled edits share one background (rows 10-12), and each prose edit is a line or two, so a split would restate the same context in every prompt. The brief requires:
- no edit under `transcript_analysis/` or to `transcript-analysis.py`, except the comment at `edit_format.py:84-86` and Verification step 2's three temporary mutations, each restored before the next and all restored before returning;
- `wc -l` for the new file in the return;
- no staging and no commits.

**Create:**
- `claude/.claude/scripts/tests/test_transcript_sessions.py`.

**Modify:**
- `docs/transcript-analysis-architecture.md` (:835-837, :1009-1011);
- `claude/.claude/scripts/tests/test_transcript_analysis_cost_import_direction.py` (docstring :1-16);
- `claude/.claude/scripts/tests/test_transcript_analysis_multi_root_scope.py` (:362-366, :410-413);
- `claude/.claude/scripts/tests/test_transcript_context_composition.py` (:652-653);
- `claude/.claude/hooks/tests/test_check_claude_md_length.py` (:889-890);
- `claude/.claude/scripts/tests/test_transcript_analysis_module_test_files.py` (:16);
- `claude/.claude/scripts/tests/test_transcript_package_module_prefixes.py` (`TEST_FILES`);
- `claude/.claude/scripts/transcript_analysis/edit_format.py` (comment at :84-86 only).

**Also in the diff:** `.claude/plans/transcript-analysis-hygiene.md`.

**Reuse:** the `fake_projects` fixture and the `_write_jsonl` (:40) and `_asst` (:832) helpers in `tests/conftest.py`, and `cli.build_parser()`. No new helper.

Projected diff: about 95 lines. That is about 36 in the cost_import_direction docstring, 20 in the new test file, 14 in the architecture doc, 10 in multi_root_scope, 10 across the two pointer fixes, 3 in the `edit_format.py` comment, and 2 across the two registries.

## Verification

Run from the worktree root:
1. `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py` passes. Its scripts-tests selection includes the new file, `test_transcript_analysis_module_test_files.py`, and `test_transcript_package_module_prefixes.py` (row 13).
2. Non-vacuity of the new test. Apply each mutation below to `claude/.claude/scripts/transcript_analysis/sessions.py` alone, run `scripts/venv-run.sh pytest claude/.claude/scripts/tests/test_transcript_sessions.py`, and confirm the named failure. After each one, run `git checkout -- claude/.claude/scripts/transcript_analysis/sessions.py` and confirm `git diff --exit-code -- claude/.claude/scripts/transcript_analysis/sessions.py` exits 0 before the next.
   - Delete :33-35. The test fails because no `SystemExit` is raised.
   - Change `sys.exit(2)` at :35 to `sys.exit(1)`. The test fails on the exit-code assertion.
   - Move :33-35 below the emission loop that ends at :49, at function-body indentation. The test fails on the empty-stdout assertion because the seeded path prints first, which shows the seed is load-bearing (row 23).
3. `scripts/venv-run.sh ruff check` over the eight changed or created `.py` files is clean.
4. Each of these prints nothing:
   - `git grep -nE "plan's line numbering|by this plan" -- claude/.claude/scripts/tests`
   - `git grep -n "Older test files" -- docs/transcript-analysis-architecture.md`
   - `git grep -n "No test covers the" -- docs/transcript-analysis-architecture.md`
   - `git grep -nE "measure_overhead|this plan" -- claude/.claude/scripts/transcript_analysis`
5. `git grep -n "test_transcript_analysis\.py" -- claude/` prints only `test_transcript_analysis_module_test_files.py` lines, where the name is the banned catch-all.
6. Production code changed only in the `edit_format.py` comment (steps 6 and 7 compare against local `main`, which must equal `origin/main` when they run):
   - `git diff --stat main -- claude/.claude/scripts/transcript_analysis claude/.claude/scripts/transcript-analysis.py` lists `transcript_analysis/edit_format.py` and no other file;
   - `git diff -U0 main -- claude/.claude/scripts/transcript_analysis/edit_format.py | grep -E '^[-+]([^-+#]|$)'` prints nothing, so every added or removed line in that file is a column-0 comment line.
7. The four prose-edited test files keep their code unchanged: the command below prints `[]` and exits 0. It blanks every module, class, and function docstring, then compares each file's `ast.dump` at `main` with its worktree copy, so a string literal or statement changed outside a docstring fails it. `edit_format.py` is step 6's, and the two registry files carry code edits by design. The check runs inline because no repo script makes this comparison (row 24) and it runs once.

```bash
scripts/venv-run.sh python3 -c "
import ast, subprocess, sys
def code_only(source):
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant) and isinstance(node.body[0].value.value, str):
            node.body[0].value.value = ''
    return ast.dump(tree)
changed = [path for path in sys.argv[1:] if not code_only(subprocess.run(['git', 'show', 'main:' + path], capture_output=True, text=True, check=True).stdout) == code_only(open(path).read())]
print(changed)
sys.exit(1 if changed else 0)
" claude/.claude/scripts/tests/test_transcript_analysis_cost_import_direction.py claude/.claude/scripts/tests/test_transcript_analysis_multi_root_scope.py claude/.claude/scripts/tests/test_transcript_context_composition.py claude/.claude/hooks/tests/test_check_claude_md_length.py
```

## Out of scope

- **PR B, moving the inline subparsers out of `cli.py`.** GH-1432 orders it after PR A, and it gets its own plan (row 1).
- **Pointers to `test_transcript_analysis.py` in preserved records.** These are `docs/case-studies/delegate-instrument-authoring.md:68`, `docs/case-studies/targeted-read-discipline.md:105`, `docs/case-studies/hashline-edit-format.md:29` and :140, and two dated audits under `docs/reports/` (G1).
- **Plan citations in `transcript_analysis/cache_rebuild_rules.py`.**
  - Its five cites by path, at :22, :38, :105, :310 and :344, name four distinct plans, because :22 and :344 both cite `.claude/plans/cache-ttl-tuning-analysis.md`.
  - All four plans exist under `.claude/plans/`, so none of the five is a dead pointer.
  - The bare "the plan's" at :352 shares a docstring with the :344 cite.
  - The bare cites at :31 and :476 name no plan, and `cache-ttl-tuning-analysis.md` holds what each cites: the 0.10 per-root margin (:42) and the ship rule (:40).
  - Editing this file would widen the Ask past the one production site the engineer added (row 18).
- **Four more unnamed plan pointers in transcript-analysis test docstrings.**
  - None is in the Context's findings, so each stays out only because the Ask is GH-1432's PR A plus `edit_format.py` (rows 1 and 18).
  - Each names no plan, and a text search of `.claude/plans/` finds the committed plan holding what it cites.
  - That is the shape of the two multi_root_scope sites PR A fixes, whose plan also still exists (row 25).
  - The engineer may widen PR A to cover them.

  The four sites:
  - `tests/test_transcript_reviewer_yield.py:1495` ("ledger row O", "this plan does not fix") matches `.claude/plans/gh558-reviewer-yield-cited-path.md:281` and :726. Its claim that the leak is "unfixed" was not checked.
  - `tests/test_transcript_scope.py:541` ("per the plan's own framing") matches `.claude/plans/transcript-corpus-multi-account-scope.md:177`. A docstring in the same test class names that plan at :467.
  - `tests/test_transcript_cost.py:1590` ("promoted from the plan's manual verification check") matches `.claude/plans/multi-account-cost-breakdown.md:66`. It also narrates the test's history, a prior-state comparison.
  - `tests/test_transcript_cache_rebuild_ttl_rules.py:196` ("the plan's 10% margin threshold") matches `.claude/plans/cache-ttl-tuning-analysis.md:42`.
- **`tests/test_cleanup_merged_branches.py:183`** ("in the plan"). A grep of `claude/.claude/scripts/tests` for `this plan|the plan's|in the plan` also finds it, but it does not test transcript-analysis, so GH-1432 does not reach it.
