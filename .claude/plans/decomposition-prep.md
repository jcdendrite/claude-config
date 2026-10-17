# Decomposition prep: commit the move-fidelity harness, retarget the shared fixtures, move `_add_project_scope_args`

## Context

Goal: land the transcript-analysis "decomposition prep" PR (the tracker's unchecked "decomposition prep (2026-10-15 architect consult)" item) so later phases stop re-authoring tooling and can move code without shim friction.

Ask: "Plan for session signal phase", then, asked which phase to plan first because the tracker orders prep, then skill, then session-signal: selected the option labeled "Decomposition prep (Recommended)". The option's description was the session's own proposal.

Why now: GH-1222 (PR #1263) merged, so the `conftest.py` and `test_transcript_cli_bootstrap.py` baselines are settled. The tracker says prep lands "one PR, before any further group".

The tracker item has three parts, quoted: "(1) commit the move-fidelity harness (AST comparator, CLI capture, node-ID parity, negative controls) under the test tree so phase plans stop re-authoring it in scratch; (2) retarget `fake_projects` to import package modules directly instead of patching through `request.module._mod`, so each moved test file drops its shim loader as its subject moves (39 transcript test files still exec the shim); (3) move `_add_project_scope_args` out of the shim so later phases can move each command's subparser block with the command." The tracker also decides that, starting with the skill group, each phase moves its commands' `add_parser` blocks into the owning module in registration order, so `--help` stays byte-identical, and that each phase targets 1,000 lines per module and test file.

## Approach

Land all three tracker parts in one PR, built by four sequenced `code-writer` dispatches. Dispatch 1 commits a stdlib-only move-fidelity helper module plus its seeded-defect self-test under the scripts test tree. Dispatch 2 retargets the three shared fixtures to the package modules and moves `_add_project_scope_args` into `scope.py`. The new harness then runs on dispatch 2's own changes as its first real use. Dispatches 3 and 4 apply the cumulative review's findings: the self-test split and new seeds, then a loader-free fixture test, comment fixes, and docs.

**Part 1: the move-fidelity harness.** `claude/.claude/scripts/tests/_move_fidelity.py` imports only the standard library and uses no relative imports. That way it works both as an import (`from ._move_fidelity import ...`, per `.claude/rules/test-tree-packaging.md`) and as a script (`<venv>/bin/python3 claude/.claude/scripts/tests/_move_fidelity.py <subcommand>`). Every finding prints as one line prefixed with its check name. Exit codes: 0 when clean, 1 on any finding, 2 on bad input. It has five subcommands:

- `nodes --before FILE --after FILE [--after FILE…] --moved NAME… [--rename OLD=NEW…] [--verbatim NAME…]`
  - A top-level node is a def, async def, class, or single-Name `Assign`/`AnnAssign`.
  - Each moved name must be defined exactly once in `--before` (`node-missing`) and exactly once across all `--after` files (`node-missing`, `node-duplicate`).
  - `--rename` takes the phase's forward map (pre-move to post-move dotted names). The tool reverses it on the after text, longest NEW first, with identifier boundaries. If a NEW string already occurs in the matching before node, it reports `rename-ambiguous`.
  - It compares `ast.dump` of each node including its decorators (`node-ast`), and the `#` comments inside the node's span including decorators (`node-comments`). Comments are found with `tokenize`, so a `#` inside a string literal is not a comment.
  - For a `--verbatim` name it also compares the exact unreversed source lines (`verbatim`).
  - It prints the compared-node count.
- `remainder --before FILE --after FILE [--removed NAME…] [--added NAME…] [--changed NAME…] [--import-added TRIPLE…] [--import-removed TRIPLE…]`
  - Drops the named nodes and every import statement from each side.
  - Compares what is left of the top-level body, in order, by `ast.dump` (`remainder-ast`), and compares the comments outside the dropped spans (`remainder-comments`).
  - The import change is computed as triples: `module:name[:asname]` for `from` imports, and the bare name for plain imports. It must equal the declared added/removed sets exactly (`import-diff`).
  - The name `__doc__` refers to the module docstring. For a newly created module, `--before` names an empty scratch file.
  - Declared sets are checked against the files, not trusted: a `--removed` name still defined in `--after` or absent from `--before`, an `--added` name absent from `--after` or already in `--before`, and a `--changed` name missing on either side each give `declared-mismatch`.
- `capture --script FILE --account DIR --out DIR [--runs FILE] [--all-help] [--env NAME=VALUE…] [--unset NAME…]`
  - Environment: `os.environ` minus `PYTHONPATH`, plus `CLAUDE_CONFIG_DIR=<account>`, `TRANSCRIPT_CONFIG_DIRS_FILE=<a path under --account that is never created>`, so no before/after mask is needed for it, `COLUMNS=100`, and `PYTHONHASHSEED=0`. Then `--env`/`--unset` apply.
  - Runs use `sys.executable`, the caller's cwd, and stdin from `DEVNULL`. There is no timeout, so no timeout literal needs a citation.
  - `--all-help` captures top-level `--help`, then each subcommand's `--help` in registration order. It reads the subcommand names in-process from the script's `build_parser()`. The script is loaded via `spec_from_file_location` under a module name other than `transcript_analysis`, never registered in `sys.modules`, with the script's directory prepended to `sys.path` and `sys.path` restored in a `finally` block.
  - Runs file: one `shlex`-split argv per line. Blank lines and `#` lines are skipped.
  - Writes `<label>.argv`, `.stdout`, `.stderr`, and `.exit` per run. Labels are `help-top`, `help-<subcommand>`, and `run-NN`.
  - Refuses an existing `--out` with exit 2.
- `compare-captures DIR_A DIR_B [--mask REGEX…]` compares every file byte for byte, after replacing each mask match with a fixed token. A file present on only one side is `capture-missing`. A byte difference is `capture-bytes`, with a short unified diff.
- `test-ids --before XML… --after XML… [--file-prefix PATH] [--rename OLD=NEW…] [--expect-new ID…]`
  - Reads pytest's default junit XML.
  - A test's ID is the `classname` components after the last one that starts with `test_`, joined with `::`, then `::` and the testcase `name`. A module-level test function has no component after `test_<module>`, so its ID is the `name` alone. The ID drops the module name by design, since a move changes the module. A classname with no `test_` component exits 2 rather than guessing a key, but only for testcases that survive `--file-prefix`.
  - Outcome is the set of child tags among `failure`/`error`/`skipped`, or `passed` when that set is empty.
  - The before side and the after side are each a multiset, unioned across their XMLs. That is how the parallel pass and the `timing` pass combine.
  - `--file-prefix` keeps only testcases whose classname starts with PATH, with `/` turned into `.`.
  - Findings: `id-missing`, `id-unexpected` (not listed in `--expect-new`), and `id-outcome`.
  - A parametrize ID that varies run to run would show as a paired `id-missing` and `id-unexpected`. Investigate such a pair for non-determinism before attributing it to the move.

The module docstring gives one usage line per subcommand, then a limits paragraph, placed after the existing paragraph that describes how findings print, of exactly these two sentences: "`capture --all-help` captures rendered help only, so it cannot see an argparse default, type, or dest that the help output does not display." and "`remainder` drops every top-level import statement, with any comment on its lines, before comparing, so it checks neither import order nor import-line comments." It names no plan, ticket, or line number.

The permanent self-test is split by subcommand so each file stays at or under 1,000 lines:
- `claude/.claude/scripts/tests/test_transcript_move_fidelity.py` holds the `nodes`, `remainder`, `capture`, and `compare-captures` seeds and the two packaging tests. Its module docstring is "Seeded-defect self-test for tests/_move_fidelity.py's nodes, remainder, capture, and compare-captures subcommands.", a blank line, then "Every check these subcommands report is proved by a synthetic source or script written under tmp_path: a clean case yields no findings, and one seeded defect per check yields exactly the finding that names it. The test-ids seeds live in test_transcript_move_fidelity_test_ids.py."
- `claude/.claude/scripts/tests/test_transcript_move_fidelity_test_ids.py` holds the `test-ids` seeds. Its module docstring is "Seeded-defect self-test for tests/_move_fidelity.py's test-ids subcommand.", a blank line, then "Every test-ids check is proved by junit XML written under tmp_path: a clean case yields no findings, and one seeded defect per check yields exactly the finding that names it. The other subcommands' seeds live in test_transcript_move_fidelity.py."
- The `_test_ids` file carries verbatim copies of `_invoke` and `_checks`, docstring included, instead of importing them from the sibling test module. That duplication is CLAUDE.md's named DAMP-test-code exception.

Every test uses synthetic sources, XML strings, or scripts written under `tmp_path`, so it reads nothing outside its own domain. Each seeded defect asserts the check name that catches it:

- `nodes`:
  - Positive control: a clean move with one rename and one decorator yields no findings and the expected count.
  - A deleted statement, a deleted `assert`, a changed string literal, a removed `@pytest.mark.parametrize`, an extra method in a moved class, and a rename missing from the map each give `node-ast`.
  - A moved name that shares its line with another statement (`moved_fn = 1; other = 2` on both sides) gives `node-ast` saying the node's text holds 2 statements.
  - A changed `#` comment inside a moved node gives `node-comments`.
  - A dropped node gives `node-missing`. A name absent from `--before` and a name defined twice there each give `node-missing` too.
  - A node defined in two after files gives `node-duplicate`.
  - A whitespace-only change inside a `--verbatim` node gives `verbatim`, and no `node-ast`.
  - A rename whose NEW string is already in the before node gives `rename-ambiguous`.
  - A rename that no compared node applies gives `rename-unused`.
  - A NEW string that only appears inside a longer identifier is not reversed, so the node gives `node-ast`. Two overlapping NEW strings reverse longest first and compare clean.
  - A `#` inside a string literal that changes yields `node-ast` and no `node-comments`.
- `remainder`:
  - Positive control: one removed node whose name an added `from … import` re-binds (the shim shape), one added, and one changed node, plus the declared import change, yields no findings. "Defined" in the checks below means a top-level node, not any binding.
  - A deleted remaining statement and reordered remaining nodes each give `remainder-ast`.
  - A changed comment outside the dropped spans gives `remainder-comments`.
  - An undeclared added import, and a declared added import that was not added, each give `import-diff`.
  - One test is parametrized over five import changes. Each side's source is `VALUE = 1` plus the import lines the case names. Each case runs twice: the right declaration compares clean, and the wrong one gives `import-diff`.
    - `import os` present before and gone after: right `--import-removed os`, wrong nothing declared.
    - `import os` present on both sides: right nothing declared, wrong `--import-removed os`.
    - `from x import y as z` added: right `x:y:z`, wrong `x:y`.
    - `import x as y` added: right `x::y`, wrong `x`.
    - `from .x import y` added: right `.x:y`, wrong `x:y`.
  - A changed module docstring without `--changed __doc__` gives `remainder-ast`.
  - Each of these gives `declared-mismatch`, one seed apiece: a `--removed` name still defined in `--after`, a `--removed` name absent from `--before`, an `--added` name absent from `--after`, an `--added` name already defined in `--before`, and a `--changed` name missing on one side.
  - A two-line comment replaced by a one-line comment gives one `remainder-comments` finding: the unit is one finding per contiguous comment block.
- `capture` / `compare-captures`:
  - A synthetic script that echoes its argv and the pinned variables proves the environment. The test first sets hostile ambient values with `monkeypatch.setenv` for `PYTHONPATH`, `COLUMNS`, `PYTHONHASHSEED`, `CLAUDE_CONFIG_DIR`, and `TRANSCRIPT_CONFIG_DIRS_FILE`. `PYTHONPATH` is then absent and each pinned value overrides its hostile value. A nonzero exit is recorded.
  - `--env` overrides a pinned value and `--unset` removes one. `--env` applies first, then `--unset`, so `--unset` wins for the same name.
  - A runs file with blank lines, `#` lines, and a `shlex`-quoted `'*'` argument yields `run-01`, `run-02` in order with the `*` argv intact.
  - After `--all-help` returns, `sys.path` equals its value before the call.
  - Against a synthetic two-subcommand `build_parser()`, `--all-help` yields `help-top` followed by both subcommands in registration order.
  - An existing `--out` exits 2.
  - Identical directories compare clean. One changed byte gives `capture-bytes`. A one-sided file gives `capture-missing`. A difference covered by a mask compares clean.
- `test-ids`, all in the `_test_ids` file:
  - The same IDs under a different file compare clean.
  - A dropped ID gives `id-missing`.
  - An added ID gives `id-unexpected`, and compares clean once declared with `--expect-new`.
  - An `--expect-new` ID that never appears after, and one that occurs no more often after than before, each give `expect-new-mismatch`.
  - passed→skipped, passed→failure, and passed→error each give `id-outcome`, from one test parametrized over the three tags.
  - Two copies of an ID before and one after gives `id-missing`.
  - `--rename` compares clean for a class name that prefixes an ID, and for an unparametrized module-level test whose ID equals OLD exactly.
  - `--file-prefix` excludes another test tree.
  - A classname with no `test_` component exits 2.
  - A non-`test_` classname outside `--file-prefix` does not exit 2.
  - A module-level test function's ID is its `name` alone and compares clean when moved to another file.

**Stopping rule for the self-test.** Seeding is complete when both conditions below hold. Any reviewer FYI left after that goes to Out of scope, not into a new seed.
- Every distinct finding the helper emits has a seed that produces it. The unit is one `findings.append(` call site in `_move_fidelity.py`, and `grep -n "findings.append(" claude/.claude/scripts/tests/_move_fidelity.py` lists them: 27 at plan time (row 25). The three `findings.extend(` sites are not units. Two forward findings built elsewhere, and the third appends the diff lines the `capture-bytes` seed already asserts.
- Each key-rendering branch has a seed under which a wrong rendering changes the result. The branches are each import shape `_import_triples` renders (from-import with and without an alias, relative from-import, plain import with and without an alias) and each path through `_testcase_id` and `_rename_id`. A seed that asserts only a check name pins no rendering, so each rendering seed also asserts the right rendering clean.

Dispatch 3's return maps each `findings.append(` site to the test that seeds it. The parent checks that map before dispatch 4.

**Part 2: the fixture retarget.** This is limited to the fixtures. In `tests/conftest.py`, extend the existing `from transcript_analysis import pricing, scope` line to `cost_ledger, ledger_common, pr_cost_ledger, pricing, scope`. `fake_projects`, `cost_ledger_file`, and `cost_ledger_enabled` drop their `request` parameter and patch those modules directly. They patch the same objects and attributes as today, so behavior does not change (rows 10–11).

Docstrings to rewrite:
- Replace `fake_projects`' first paragraph with: "Isolated single-account corpus: patches scope.PROJECTS_DIR and each package module's by-name config_dir binding directly, so a test file needs no shim loader to use it." Its second paragraph stays.
- `cost_ledger_file` and `cost_ledger_enabled` lose their `request.module._mod` parentheticals. Each `_mod.cost_ledger` / `mod.ledger_common` mention becomes `cost_ledger` / `ledger_common`.
- `cost_ledger_enabled`'s docstring drops the bare "not `_mod`'s" clause outright, so its sentence reads: "`_cost_ledger_report`'s sentinel check resolves `config_dir` through `_config.py`'s own binding, so patching `cost_ledger.config_dir` alone has no effect on it."

In `test_transcript_analysis_branch_attribution.py`, the loader comment at :28–29 becomes false. It becomes the one line `# _mod.cmd_buckets below lives in the shim, so this file loads the shim itself.` The loader stays (row 13).

**Part 3: `_add_project_scope_args` moves into `scope.py`.** It moves verbatim, docstring included, to sit immediately above `_resolve_project_scope`, the function that reads the `this_repo` attribute it defines. The shim adds `_add_project_scope_args` to its existing `from transcript_analysis.scope import (...)` block, so all 38 `build_parser()` call sites stay unchanged.

That settles the "keep the shim re-export?" question: there is nothing to keep. The shim imports the name for its own `build_parser()` and needs no `# noqa`. `_mod._add_project_scope_args` still resolves. The import goes away when the last parser block leaves the shim.

Later phases call it as `scope._add_project_scope_args(p)` and need no new import (row 16).

Its docstring states the `--projects` default against a prior version (row 30). Dispatch 4 rewrites only that docstring, in a commit after the move commit, so the move itself stays verbatim for Verification 4. The docstring's summary line stays, and its body becomes: "The flags are mutually exclusive. --this-repo routes through _repo_scoped_project_slugs(), an identity-based minimization control. --projects defaults to the machine-wide glob "*"."

**Docs.** Two edits to `docs/transcript-analysis-architecture.md`:
- The `scope.py` section gains its own paragraph, after a blank line: "`scope.py` also owns `_add_project_scope_args`, which registers the mutually exclusive `--projects`/`--this-repo` flags on a subparser. `_resolve_project_scope` reads the `this_repo` attribute that function defines."
- The Tests section gains a paragraph with these sentences:
  - "`fake_projects`, `cost_ledger_file`, and `cost_ledger_enabled` patch the package modules directly, so a test file that uses them needs no `transcript-analysis.py` loader of its own."
  - "`tests/test_transcript_shared_fixtures.py` requests them from a file that loads no shim."
  - "`tests/_move_fidelity.py` checks a code move between files: moved-node AST equality modulo a rename map, what remains of each source file, byte-compared CLI captures under a pinned environment, and per-test outcomes across junit XML runs."
  - "Its seeded-defect self-test is split by subcommand: `tests/test_transcript_move_fidelity_test_ids.py` covers `test-ids`, and `tests/test_transcript_move_fidelity.py` covers the rest."
  - "Run the module with `--help` for usage."

**Why one PR, split into four dispatches.**
- One PR:
  - The tracker says one PR.
  - The diff is four new files plus five small edits.
  - Part 3's move gives the harness a real move to check before the skill phase depends on it.
  - None of the three parts depends on another at runtime, so splitting buys no revert independence.
- Four dispatches:
  - The harness and the retarget/move touch disjoint files and share no background.
  - The parent must take baselines with the harness after dispatch 1 and before dispatch 2 edits anything (Verification 0).
  - Dispatches 3 and 4 carry the review round's amendments: 3 owns the self-test files, 4 the fixture test, comment fixes, and docs. They run in sequence because dispatch 4's doc text names dispatch 3's new file, and each one's lint and scoped suite would otherwise see the other's half-written files.

**Stow surface.** Every stow consumer receives:
- the harness and its three test files, which do nothing unless run;
- the moved function, whose CLI output Verification 3 proves byte-identical.

**PR body carries:**
- each Verification result, including the harness's first-run node count and parity outcome;
- `scope.py`'s size (row 17);
- the rollback order: once a later phase drops a `fake_projects` consumer's shim loader, revert that phase before this PR.

**Open decisions: none.** The two files added after approval, `test_transcript_move_fidelity_test_ids.py` and `test_transcript_shared_fixtures.py`, are approved: `[engineer-verified: "Approve both (Recommended)"]`. The option's description was the session's own proposal and is not covered by this tag. Each question raised in the Context has a default backed by the tracker or a read, and each is recorded in the rows below.

### Assumption ledger

**Root:** Every later decomposition phase pays three avoidable costs:
- it re-authors a scratch move-fidelity harness;
- it cannot drop a moved test file's shim loader, because the shared fixtures read `request.module._mod`;
- it cannot move a command's subparser block, because `_add_project_scope_args` lives in the shim.

This PR removes all three without changing any CLI output or test outcome.

**Givens:**
- G1: The shim keeps `build_parser()`, `main()`, and every unmigrated `cmd_*` until the final `cli.py` phase. The governing plan's phase order owns this, and moving them is that phase's work. `[verified: .claude/plans/transcript-analysis-decomposition.md:144-161; docs/transcript-analysis-architecture.md:8-12]`
- G2: Each consumer test file keeps its own shim loader until its subject moves. The tracker gives loader removal to later phases ("so each moved test file drops its shim loader as its subject moves"). `[verified: gh issue view 1116 -R jcdendrite/claude-config, read by the dispatching session]`
- G3: Agents run `select-tests.py`, not the full suite, unless a plan's Verification needs a whole-repo claim. This repo's CLAUDE.md imposes it. `[verified: CLAUDE.md § Commands]`
- G4: Committed `.py` text carries no tracker IDs. A repo test imposes it (row 20).

**Rows:**
1. Plan decomposition prep before the session-signal phase. `[engineer-verified: "Decomposition prep (Recommended)"]`. The option description was the session's own proposal and is not covered by this tag.
2. The tracker item has three parts, in one PR, landing before any further group. Later phases move each command's `add_parser` block into its owning module in registration order and target 1,000 lines per module and test file. `[verified: gh issue view 1116 -R jcdendrite/claude-config, read by the dispatching session]`
3. No move-fidelity code is committed anywhere. Every phase plan specified it as scratch. `[verified: .claude/plans/audit-routing-decomposition.md:644-648; Grep "move-fidelity|move fidelity" outside .claude/plans/ matched no code]`
4. Move fidelity compares a pre-move snapshot with the post-move tree. Those two states coexist only during a phase, so only the harness's self-test can run permanently in CI. `[verified: .claude/plans/decompose-transcript-handoff-nudge-b.md:424-468, 505-541]`
5. The tracker's "negative controls" means the comparator's seeded-defect self-test. Per-phase mutation controls name phase-specific code and stay in each phase's plan. `[unverified]`: interpretation; the closest source is decompose-transcript-handoff-nudge-b.md:506-521.
6. A junit `classname` is the nodeid with the path's `/` turned into `.`, `.py` dropped, and class names appended. The parameter suffix stays in `name`. `pyproject.toml` overrides neither `python_files` nor `python_classes`. `[verified: .venv/lib/python3.12/site-packages/_pytest/junitxml.py:116-126, 447-455; Grep of pyproject.toml]`
7. `select-tests.py` counts untracked files as changed and forwards extra argv to pytest. `[verified: select-tests.py:880-908, 1120, 1177]`
8. addopts sets `-n auto`. `timing`-marked tests run serially via `-m timing -n0`. `[verified: pyproject.toml:25, 31-32]`
9. conftest already imports `scope` and `pricing` from the package at module level. `[verified: conftest.py:32-33]`
10. The shim's `from transcript_analysis import (...)` binds the same module objects conftest imports, because the test loader never registers the shim in `sys.modules`. `[verified: transcript-analysis.py:35-63; test_transcript_denials.py:14-20]`
11. Exactly four package modules bind `config_dir` by name (scope, cost_ledger, ledger_common, pr_cost_ledger). `fake_projects` patches those four plus `scope.PROJECTS_DIR`, five setattrs in all. `[verified: Grep "from _config_dir import" in transcript_analysis/ — scope.py:30-32, cost_ledger.py:29, ledger_common.py:16, pr_cost_ledger.py:22; conftest.py:1384-1388]`
12. Only conftest's three fixtures read `request.module._mod`. One test-file comment describes that dependency. `[verified: Grep "request\.module" outside .claude/plans/]`
13. `test_transcript_analysis_branch_attribution.py` still needs its loader for `_mod.cmd_buckets`. `[verified: that file :27-33, :580]`
14. `_add_project_scope_args` is pure argparse (shim :4047-4059) and has 38 call sites, all in `build_parser()`. No test reads `_mod._add_project_scope_args`. `[verified: Grep across claude/.claude/scripts/]`
15. `scope.py` imports argparse. Its `_resolve_project_scope` reads `args.this_repo` unguarded, and its docstring names `_add_project_scope_args`. `scope.py` already owns argparse-facing `--projects` validation. `[verified: scope.py:14, 507-521, 557; test_transcript_analysis.py:9010-9011]`
16. Package modules read sibling names by module attribute: `scope._*` has 129 reads across 19 modules. The prefix test forbids `from transcript_analysis.<m> import` in the modules it lists. `[verified: Grep; test_transcript_package_module_prefixes.py:173-189]`
17. `scope.py` is 1,122 lines, already over the 1,000-line target. This PR adds about 15. `[verified: line count]`
18. No byte-for-byte `--help` guard exists. The bootstrap tests assert exit status and seeded markers only. `[verified: test_transcript_cli_bootstrap.py:44-48, 151-172 and its test list]`
19. The architecture-doc drift test checks module sections only. `select-tests.py` maps the doc to the scripts test tree. `[verified: test_transcript_analysis_architecture_doc.py:1-52; select-tests.py:296-298]`
20. The ticket-reference discipline test covers `.py` text, and a scripts change selects it. `[verified: claude/.claude/hooks/tests/test_ticket_reference_discipline.py:124, 184; test_select_tests.py:446]`
21. No known negative control isolates `cost_ledger_file`'s `_cost_ledger_path` patch. While `fake_projects` is active, the unpatched path resolves to the same `tmp_path/cost-ledger.md`. A test that requests `cost_ledger_file` without `fake_projects` (`test_transcript_analysis.py:8067`) might fail without the patch, but its assertions are on roots lists and the reviewer could not settle it by tracing, so the plan claims no control among existing tests. Dispatch 4's loader-free test is a control for it, and Verification 5(g) proves that. `[verified: cost_ledger.py:55-64; conftest.py:1386, 1414-1420]`
22. The governing plan prescribes a full-suite revert rehearsal at each phase boundary, and names a conftest fixture change as its reason. `[verified: transcript-analysis-decomposition.md:276-279]`
23. A seeded `buckets` run under a real subprocess exercises scope resolution and table rendering. `[verified: test_transcript_cli_bootstrap.py:151-172]`
24. Walking `build_parser()`'s `_SubParsersAction` has in-repo precedent. `[verified: test_transcript_analysis.py:9031-9035]`
25. `_move_fidelity.py` has 27 `findings.append(` sites and three `findings.extend(` sites. Before dispatch 3, three append sites had no seed: the two removal-direction `import-diff` sites and the `node-ast` site for text that is not exactly one statement. `_rename_id`'s exact-match branch had no seed either. `[verified: Grep "findings\.(append|extend)\(" in _move_fidelity.py; read of test_transcript_move_fidelity.py at dispatch 2's commit]`
26. Hand-made mutants pass the pre-amendment self-test: dropping the `import-diff` removal loops, the from-import alias, the relative-import level, or the plain-import alias, or narrowing `OUTCOME_TAGS` to `skipped`. `[unverified]`: reported by the staff-sdet review's mutant run, not re-run. Verification 5(g) re-runs them against the final seeds.
27. `test_transcript_move_fidelity.py` is 993 lines at dispatch 2's commit. Its test-ids section runs from `TREE` through the last `test_test_ids_*` test, and only `_junit` uses `quoteattr`. `[verified: read of the file]`
28. argparse's default `HelpFormatter` shows an action's `default` or `type` only through a `%(...)s` interpolation in its help string. It shows the `dest` only as the default metavar when no `metavar` is set. `[verified: /usr/lib/python3.12/argparse.py:645-656, 685-689]`. The repo venv is Python 3.12.3 on `/usr/bin/python3` and loads that stdlib copy, and CI sets `python-version: '3.12'`. `[verified: staff-sdet plan-review round 3 read of the venv interpreter]`
29. Every test file that requests the three shared fixtures also defines a module-level `_mod` loader, so no test exercises them from a loader-free file. `[verified: staff-sdet plan-review round 3 grep: 37 test files request the fixtures, all 37 define `_mod` via `spec_from_file_location`]`
30. `_add_project_scope_args`'s docstring states its `--projects` default against a prior version ("pre-existing", "no existing invocation's behavior changes"), and Verification 4 checks the move with `--verbatim`. `[verified: scope.py:507-513; Verification 4]`

**Mechanisms:**
- M1: Commit the harness as a stdlib-only helper module in the scripts test tree. `anchors: row3, row4`. Lighter primitives considered:
  - Per-phase scratch scripts (the status quo) fail row 3: every phase re-derives the harness.
  - Shell `diff -r`/`sed`/`sort | diff` cover byte and ID-list diffs. They cannot do AST equality modulo renames or per-ID junit outcomes, and the worktree Bash guard refuses multi-step recipes (CLAUDE.md § Working Style).
  - A script under `claude/.claude/scripts/` would stow as user-facing tooling to every stow consumer. This is contributor-only tooling, and the tracker places it in the test tree.
  - A pytest test alone cannot see the pre-move state (row 4).
  - `sha256sum`/`sha256sum -c` are adopted as-is for negative-control hash guards instead of building a harness subcommand.
- M2: Identify nodes and remainders by name, not line span. `anchors: row4, row20`. Line spans drift between baseline and move and cannot appear in committed `.py` text.
- M3: `capture`/`compare-captures` with `--all-help`. `anchors: row18, row24`. Lighter alternatives:
  - Listing each `--help` by hand in the runs file misses any subcommand a phase forgets.
  - Parsing the top-level `--help` brace group depends on how argparse wraps one long choices token.
- M4: `test-ids` reads default junit XML and keys IDs through the last `test_` classname component. `anchors: row6, row7, row8`
- M5: A seeded-defect self-test, one defect per check. `anchors: row5`
- M6: Retarget the three fixtures to direct package-module imports. Consumer loaders stay. `anchors: row9, row10, row11, row12`
- M7: Rewrite the branch-attribution loader comment. `anchors: row13`
- M8: Move `_add_project_scope_args` into `scope.py` and import it into the shim's existing scope block. `anchors: row14, row15, row16, row17`. Rejected alternatives:
  - A new `cli_args.py` would add a module, an `__init__.py` docstring entry, a doc section, and a new import in every future owning module. It would not reduce `scope.py`, which already hosts argparse validation.
  - A public rename would churn 38 call sites when every consumer is inside the package.
- M9: The two architecture-doc edits. `anchors: row19`
- M10: Four sequenced `code-writer` dispatches, with the parent's baseline between the first two. `anchors: root, row7`
- M11: The harness's first real run verifies Parts 2 and 3. `anchors: row18, row23`
- M12: The post-merge revert rehearsal runs as the governing plan prescribes. `anchors: row22`
- M13: Split the self-test by subcommand into two files, copying `_invoke` and `_checks`. `anchors: row2, row27`. Alternatives set aside:
  - Keeping one file fails row 2's 1,000-line target once the new seeds land on a 993-line file.
  - A shared helper module adds a test-tree file for about ten lines, which CLAUDE.md's DAMP exception covers more cheaply.
  - Importing the helpers from the sibling test module couples two test modules.
- M14: Seed each unseeded append site and rendering branch, pairing a right and a wrong declaration for each import shape. `anchors: row25, row26`
- M15: The Stopping rule. `anchors: row25`
- M16: A loader-free shared-fixtures test. `anchors: row12, row29`. Lighter primitives set aside:
  - Verification 6's `git grep request.module` runs once by hand and is not committed.
  - The later phase that first drops a loader would catch a regression, but would attribute it to itself.
- M17: State `--all-help`'s and `remainder`'s blind spots in the module docstring, and defer parser introspection. `anchors: row28`
- M18: Rewrite the moved docstring and the review-flagged comments in a commit after the move commit. `anchors: row30`

## Critical files

**Dispatch 1: `code-writer`, the harness.** It owns only these files:
- Create `claude/.claude/scripts/tests/_move_fidelity.py`
- Create `claude/.claude/scripts/tests/test_transcript_move_fidelity.py`

Its brief states that each new file must stay at or under 1,000 lines.

Its verification, from the worktree root:
- `../../../.venv/bin/pytest claude/.claude/scripts/tests/test_transcript_move_fidelity.py`
- `../../../.venv/bin/ruff check claude/.claude/scripts/tests/`
- `../../../.venv/bin/python3 claude/.claude/scripts/tests/_move_fidelity.py --help`

The parent's Verification step 0 select-tests runs act as this dispatch's scoped-suite gate.

**Parent, between dispatches 1 and 2:** Verification step 0.

**Dispatch 2: `code-writer`, the retarget, the move, and docs.** It must not touch dispatch 1's files. Its commit is the move commit that Verification 4 reads.
- Modify `claude/.claude/scripts/tests/conftest.py`: the import line plus the three fixtures and their docstrings.
- Modify `claude/.claude/scripts/tests/test_transcript_analysis_branch_attribution.py`: the loader comment only.
- Modify `claude/.claude/scripts/transcript-analysis.py`: delete the def and add one name to the scope import block.
- Modify `claude/.claude/scripts/transcript_analysis/scope.py`: add the function above `_resolve_project_scope`.
- Modify `docs/transcript-analysis-architecture.md`: the `scope.py` sentence and the Tests paragraph.

Its verification:
- `../../../.venv/bin/python3 claude/.claude/scripts/select-tests.py`
- `../../../.venv/bin/ruff check claude/.claude/ claude-skills/`

**Dispatch 3: `code-writer`, the self-test split and its new seeds.** It runs after dispatch 2's commit and owns only these files. It lands as two commits so the parent can check the split on its own: the first is the verbatim split, the second adds the new seeds, the docstring changes, and the other edits below.
- Modify `claude/.claude/scripts/tests/_move_fidelity.py`: the module docstring only, adding Part 1's limits paragraph.
- Modify `claude/.claude/scripts/tests/test_transcript_move_fidelity.py`:
  - Move the test-ids section out: `TREE`, `OTHER_TREE`, `_junit`, `_run_test_ids`, and every `test_test_ids_*` test.
  - Drop the `quoteattr` import, which only `_junit` uses.
  - Add Part 1's new `nodes` and `remainder` seeds.
  - Replace the module docstring with Part 1's text.
- Create `claude/.claude/scripts/tests/test_transcript_move_fidelity_test_ids.py`: the moved section, Part 1's new `test-ids` seeds, the copied `_invoke` and `_checks`, and Part 1's module docstring.

Its brief states that each of the two test files must stay at or under 1,000 lines, and that no `.py` text it writes names a ticket or a line number. It also states that the outcome test's parametrize IDs are the bare tags `skipped`, `failure`, and `error`, and that no other existing test is parametrized or renamed.

Its verification:
- `../../../.venv/bin/pytest claude/.claude/scripts/tests/test_transcript_move_fidelity.py claude/.claude/scripts/tests/test_transcript_move_fidelity_test_ids.py`
- `../../../.venv/bin/ruff check claude/.claude/scripts/tests/`
- `../../../.venv/bin/python3 claude/.claude/scripts/tests/_move_fidelity.py --help` exits 0 and prints the limits paragraph.
- `wc -l` on both test files.

Its return lists every test ID it added or renamed (Verification 2) and maps each `findings.append(` site to its seed (Stopping rule).

**Dispatch 4: `code-writer`, the loader-free fixture test, the comment fixes, and docs.** It runs after dispatch 3's commit, because its doc text names dispatch 3's new file, and it must not touch dispatch 3's files. It lands as its own commit after the move commit, never folded into it, so Verification 4 can still check the move verbatim.
- Create `claude/.claude/scripts/tests/test_transcript_shared_fixtures.py`:
  - It imports `cost_ledger`, `ledger_common`, `pr_cost_ledger`, and `scope` from `transcript_analysis` and loads no `transcript-analysis.py` shim.
  - Its module docstring is "Pins that conftest's fake_projects, cost_ledger_file, and cost_ledger_enabled work in a test file that loads no transcript-analysis.py shim. This file deliberately loads none, so a fixture that comes to depend on the shim fails here first." No text in the file may contain `request.module`, which Verification 6 greps for.
  - Test 1 requests `fake_projects` and `tmp_path`. It asserts `"_mod" not in globals()`, `scope.PROJECTS_DIR == fake_projects.parent`, and that `config_dir()` returns `tmp_path` on each of `scope`, `cost_ledger`, `ledger_common`, and `pr_cost_ledger`.
  - Test 2 requests `cost_ledger_file` and `cost_ledger_enabled`. It asserts `cost_ledger._cost_ledger_path() == cost_ledger_file`, and that `config_dir()` on `cost_ledger` and on `ledger_common` returns `cost_ledger_enabled`. It asserts nothing about `CLAUDE_CONFIG_DIR`, because the autouse corpus-lookup fixture already sets the identical value, so the assertion could not fail.
- Modify `claude/.claude/scripts/tests/conftest.py`: `cost_ledger_enabled`'s docstring only, per Part 2.
- Modify `claude/.claude/scripts/tests/test_transcript_analysis_branch_attribution.py`: the loader comment only, per Part 2.
- Modify `claude/.claude/scripts/transcript_analysis/scope.py`: `_add_project_scope_args`'s docstring only, per Part 3.
- Modify `docs/transcript-analysis-architecture.md`: bring the `scope.py` sentence and the Tests paragraph to the **Docs** text.

Its verification:
- `../../../.venv/bin/python3 claude/.claude/scripts/select-tests.py`
- `../../../.venv/bin/ruff check claude/.claude/ claude-skills/`

**Also in the diff:** `.claude/plans/decomposition-prep.md`, this plan, committed at plan time.

**Reuse rather than reimplement:**
- conftest's existing `from transcript_analysis import pricing, scope` line: extend it.
- The shim's existing `from transcript_analysis.scope import (...)` block: add one name.
- `test_transcript_cli_bootstrap.py:33`'s PYTHONPATH-dropping environment and its `:159-164` seed shape: mirror both. A helper module must not import a test module, and the duplicated part is one line.
- `test_transcript_analysis.py:9031-9035`'s `_SubParsersAction` walk: the pattern for `--all-help`.
- `sha256sum` / `sha256sum -c`: the negative-control hash guards.
- `_run_remainder` and `_write` in `test_transcript_move_fidelity.py`: the new `remainder` seeds call them instead of building argv by hand.
- `_invoke` and `_checks`: copied into the `_test_ids` file, not imported (Part 1).
- Not reused: `test_transcript_package_module_prefixes.py`'s `_top_level_names`. It returns names, not nodes, and lives in a test module.

## Verification

Run everything from the worktree root. `<venv>` is `../../../.venv`. `<scratch>` is a fresh directory outside the repo, for example `/tmp/decomposition-prep`. Run each git command as its own literal call.

0. **Baseline.** The parent runs this after dispatch 1 returns and before dispatch 2.
   - Dispatch 1's two new files are committed, or staged with `git add` if the commit is still pending. The corpus tests in Verification 1 list files with `git ls-files`, so an untracked file would pass them unscanned. `git status --short` lists nothing else.
   - Snapshot the files dispatch 2 will change: `git show HEAD:claude/.claude/scripts/transcript-analysis.py > <scratch>/before-shim.py`. Do the same for `transcript_analysis/scope.py` → `before-scope.py`, `tests/conftest.py` → `before-conftest.py`, and `tests/test_transcript_analysis_branch_attribution.py` → `before-branch-attribution.py`.
   - Use the Write tool to seed `<scratch>/account/projects/-home-user-parityrepo/s1.jsonl` with one record shaped like `test_transcript_cli_bootstrap.py:159-164`, with `gitBranch` set to `feat`.
   - Write `<scratch>/runs.txt` with three lines: `buckets`, `buckets --this-repo`, and `buckets --projects '*' --this-repo`. The last one must exit 2 (argparse mutual exclusion).
   - Capture twice:
     - `<venv>/bin/python3 claude/.claude/scripts/tests/_move_fidelity.py capture --script claude/.claude/scripts/transcript-analysis.py --account <scratch>/account --runs <scratch>/runs.txt --all-help --out <scratch>/before-a`
     - the same command with `--out <scratch>/before-b`
   - `compare-captures <scratch>/before-a <scratch>/before-b` must be clean. If it is not, add a `--mask` for the varying substring and record it for step 3.
   - Confirm `before-a` holds `help-top` plus 42 `help-<name>` captures, matching `grep -c "add_parser(" claude/.claude/scripts/transcript-analysis.py` (42 at plan time; the grep result governs).
   - Run `<venv>/bin/python3 claude/.claude/scripts/select-tests.py -m "not timing" --junitxml=<scratch>/before-parallel.xml`, then `<venv>/bin/python3 claude/.claude/scripts/select-tests.py -m timing -n0 --junitxml=<scratch>/before-timing.xml`.
     - Every selected test passes in both.
     - The `select-tests: running` line names `claude/.claude/scripts/tests`.
   - Record `wc -l` for the shim, `scope.py`, and conftest.
1. **Scoped suite on the final tree.** After dispatch 4, run the same two `select-tests.py` commands, writing `after-parallel.xml` and `after-timing.xml`. Steps 2–8 also run on the final tree, except where a step names the move commit.
   - Every selected test passes in both.
   - The run includes `test_transcript_move_fidelity.py`, `test_transcript_move_fidelity_test_ids.py`, `test_transcript_shared_fixtures.py`, `test_transcript_analysis_architecture_doc.py`, and `claude/.claude/hooks/tests/test_ticket_reference_discipline.py`.
   - If `select-tests.py` cannot map a path, that is a bug in its rule table, not a reason to widen the run by hand.
2. **Test-ID parity.** `<venv>/bin/python3 claude/.claude/scripts/tests/_move_fidelity.py test-ids --before <scratch>/before-parallel.xml --before <scratch>/before-timing.xml --after <scratch>/after-parallel.xml --after <scratch>/after-timing.xml --file-prefix claude/.claude/scripts/tests/` once with no declarations.
   - Dispatch 3's brief pins the parametrize IDs to the bare tags `skipped`, `failure`, and `error`, and forbids it from parametrizing or renaming any other existing test. The run must then report exactly one `id-missing`, naming the passed→skipped outcome test that dispatch 3 parametrizes. Every `id-unexpected` it reports must name a test that dispatch 3's or dispatch 4's return lists as added.
   - Re-run it with `--rename '<old ID>=<new ID>[skipped]'` for that test and one `--expect-new` per remaining added ID. It reports no findings.
   - The `test-ids` tests that moved to the `_test_ids` file need no declaration, because an ID drops its module. The clean run therefore also proves the split dropped no test.
3. **CLI parity.** Re-run step 0's capture into `<scratch>/after`. `compare-captures <scratch>/before-a <scratch>/after` must be clean, using only step 0's masks. If a `--this-repo` capture differs, check whether `git worktree list` changed in between. If it did, re-run both captures.
4. **Move fidelity.**
   - Split check, on dispatch 3's first commit (`<split-commit>`): `nodes --before <pre-split test_transcript_move_fidelity.py> --after <split-commit's test_transcript_move_fidelity_test_ids.py> --moved <each moved test, helper, and constant name> --verbatim <the same names>` is clean, and `remainder --before <pre-split file> --after <split-commit's first file> --removed <the same names> --changed __doc__ --import-removed xml.sax.saxutils:quoteattr` is clean. The pre-split file is `git show <move-commit>:claude/.claude/scripts/tests/test_transcript_move_fidelity.py`.
   - `git show <move-commit>:claude/.claude/scripts/transcript_analysis/scope.py > <scratch>/move-scope.py`, where `<move-commit>` is dispatch 2's commit. Dispatch 4 rewrites the moved docstring afterward, so the verbatim check reads the move commit.
   - `nodes --before <scratch>/before-shim.py --after <scratch>/move-scope.py --moved _add_project_scope_args --verbatim _add_project_scope_args` is clean, with a compared-node count of 1.
   - `remainder --before <scratch>/move-scope.py --after claude/.claude/scripts/transcript_analysis/scope.py --changed _add_project_scope_args` is clean, and `git diff <move-commit> -- claude/.claude/scripts/transcript_analysis/scope.py` changes only lines inside `_add_project_scope_args`'s docstring.
   - `remainder --before <scratch>/before-shim.py --after claude/.claude/scripts/transcript-analysis.py --removed _add_project_scope_args --import-added transcript_analysis.scope:_add_project_scope_args` is clean.
   - `remainder --before <scratch>/before-scope.py --after claude/.claude/scripts/transcript_analysis/scope.py --added _add_project_scope_args` is clean.
   - `remainder --before <scratch>/before-conftest.py --after claude/.claude/scripts/tests/conftest.py --changed fake_projects --changed cost_ledger_file --changed cost_ledger_enabled --import-added transcript_analysis:cost_ledger --import-added transcript_analysis:ledger_common --import-added transcript_analysis:pr_cost_ledger` is clean.
   - `remainder --before <scratch>/before-branch-attribution.py --after claude/.claude/scripts/tests/test_transcript_analysis_branch_attribution.py` reports exactly one `remainder-comments` finding: the rewritten loader comment.
5. **Negative controls.** Before each control that edits the worktree, run `sha256sum <file> > <scratch>/ctl-<x>.sha256`. Edit by hand, run the check, undo by hand, then `sha256sum -c <scratch>/ctl-<x>.sha256` must pass. Never use `git stash` or `git checkout`.
   - (a) Delete `fake_projects`' `scope` `PROJECTS_DIR` and `config_dir` setattr lines. `<venv>/bin/pytest 'claude/.claude/scripts/tests/test_transcript_analysis_branch_attribution.py::TestConsumersAgreeOnWhichRecordsFallUnderTheBranchEntered::test_cost_puts_every_record_but_the_return_to_the_default_branch_under_the_branch_entered'` must fail. After the undo, it passes.
   - (b) In a scratch copy of `<scratch>/move-scope.py`, change `default="*"` to `default="**"` inside `_add_project_scope_args`. `nodes` with `--after` pointing at that copy must report `node-ast` naming the function. A copy of the final `scope.py` would not isolate the control, because its rewritten docstring already gives `node-ast`.
   - (c) In a copy of `<scratch>/after-parallel.xml`, delete one `<testcase>` whose `classname` starts with `claude..claude.scripts.tests.` (the `..` comes from the `.claude` directory). A hooks testcase is filtered out by `--file-prefix` and would pass vacuously. `test-ids` run with the copy must report `id-missing` naming that ID.
   - (d) In a scratch copy of the post-move shim, delete one statement inside `build_parser`. `remainder` against the copy, with step 4's shim arguments, must report `remainder-ast`.
   - (e) Copy `<scratch>/after` to `<scratch>/after-tampered` and change one byte in one `.stdout`. `compare-captures <scratch>/after <scratch>/after-tampered` must report `capture-bytes`.
   - (f) Identity: `remainder --before X --after X` with no declared names is clean for the post-move shim and for conftest.
   - Controls (d)–(f) use scratch copies only, so they need no hash guard.
   - (g) Hash-guarded controls for dispatch 4's loader-free test, run one at a time: delete, in turn, `fake_projects`' `scope.config_dir`, `cost_ledger.config_dir`, `ledger_common.config_dir`, and `pr_cost_ledger.config_dir` setattr lines, `cost_ledger_enabled`'s two `config_dir` setattr lines, and `cost_ledger_file`'s `_cost_ledger_path` setattr line. Each deletion must fail the matching test in `test_transcript_shared_fixtures.py`, and the hash check must pass after each undo.
   - (h) After dispatch 3, run the row 26 mutants as hash-guarded edits to `_move_fidelity.py`: drop the two removal loops in `_import_diff_findings`; drop the from-import alias; drop the relative level; drop the plain-import alias; narrow `OUTCOME_TAGS` to `skipped`. Each must fail the matching new seed, and the hash check must pass after each undo.
   - Controls (a) and (g) cover every `config_dir` patch in the three fixtures. Step 6's patch-parity comparison and step 2's per-ID parity remain as the whole-diff guards.
6. **Leftovers.**
   - `git grep -n "def _add_project_scope_args" -- claude/.claude/scripts` returns exactly one hit, in `transcript_analysis/scope.py`.
   - `git grep -n "request.module" -- claude/.claude/scripts` returns nothing.
   - `git grep -nE "(^|[^[:alnum:]_])_?mod([^[:alnum:]_]|$)" -- claude/.claude/scripts/tests/conftest.py` returns nothing. All 23 current hits lie inside the three fixtures and their docstrings.
   - Patch parity. `grep -n "monkeypatch.setattr(" <scratch>/before-conftest.py` and the same on the worktree conftest list the same `(target module, attribute)` pair for each of the 8 fixture patch lines, once `_mod.`/`mod.` is stripped from the before side. Compare by eye per fixture: 5 in `fake_projects`, 2 in `cost_ledger_enabled`, 1 in `cost_ledger_file`. The open paren excludes docstring mentions, and the two unrelated hits outside the fixtures are ignored. A count alone is not the check, because a body that patches one module twice and drops another keeps the same count.
7. **Lint.** `<venv>/bin/ruff check claude/.claude/ claude-skills/` is clean.
8. **Sizes.** Report `wc -l` for `_move_fidelity.py`, `test_transcript_move_fidelity.py`, `test_transcript_move_fidelity_test_ids.py`, and `test_transcript_shared_fixtures.py`, each of which must be at most 1,000 lines, and for `scope.py` and conftest.
9. **Post-merge revert rehearsal** (transcript-analysis-decomposition.md:276-279). After the human merges, in a scratch worktree at the merge commit, `git revert` the squash commit. Then run `<venv>/bin/pytest claude/.claude/ claude-skills/ -m "not timing"` and `-m timing -n0`. This is the governing plan's prescribed whole-repo case (CLAUDE.md § Commands, case 2). Report the result to the engineer.

## Out of scope

- **Removing shim loaders from consumer test files, and retargeting their `_mod.<module>.<name>` reads.** Each later phase owns this as its subject moves (G2).
- **A discovery-based check that `fake_projects` patches every package module binding `config_dir` by name.** `test_transcript_shared_fixtures.py` pins the four bindings known today (row 11) but would not notice a fifth module that starts binding it. A discovery-based check would let phases drop their hand-run "add a `config_dir` binding" control. Recommended as a follow-up.
- **Parser-introspection fidelity for moved `add_parser` blocks.** Help-byte parity cannot see a changed argparse default, type, or undisplayed dest (row 28), and the skill phase is the first to move parser blocks. Deferred to the skill-phase plan. The follow-up would extend `capture --all-help` to dump, per subcommand, each action's `dest`, `default`, `type`, `choices`, `nargs`, `required`, `const`, and action class. Callables would render by `__module__` and `__qualname__`, never by `repr`, which embeds a per-run memory address. Until it exists, a phase plan must not treat help-byte parity as parser-block fidelity.
- **A contract test that generates real junit XML from a nested pytest run.** The self-test's hand-written XML follows the classname scheme read from pytest's source (row 6).
- **Tightening the `rename-unused` filter in `test_nodes_flags_a_changed_node_as_node_ast`.** It drops `rename-unused` from all five parametrized seeds, although only the rename-missing-from-the-map seed expects it.
- **A `--help` smoke test for `_move_fidelity.py`.** Dispatch 1's and dispatch 3's verifications run `--help` by hand.
- **Comparing comments on import lines, and checking import order.** `remainder` drops each top-level import statement's lines before comparing, so it checks neither. An import nested in a `try`, `if`, or function stays in the compared AST. The module docstring's limits paragraph states this.
- **Seeding multi-tag outcomes**, where one testcase carries more than one of `failure`, `error`, and `skipped`. The single-tag outcomes are seeded (`skipped`, `failure`, `error`); only the multi-tag `"+".join` rendering is unseeded, and the Stopping rule does not count it as a key-rendering branch.
- **A committed golden `--help` snapshot.** Every help-text edit would have to regenerate it. The before/after capture covers phase moves.
- **Splitting `scope.py` under the 1,000-line target** (row 17).
- **Renaming `_add_project_scope_args` to a public name** (M8).
- **Corpus-seeding helpers in the harness.** Corpora are phase-specific, and conftest's builders already serve scratch seeding.
- **Records left untouched:** past phase plans; the tracker text and its checkbox, which the engineer edits; and `docs/reports/2026-08-10-repo-quality-audit/findings.md:312`, a dated report.
- **`test_transcript_cost_ledger_record_gates.py:150`'s "patching _mod's binding" docstring.** It stays true because `_mod.cost_ledger` is `cost_ledger` (row 10).