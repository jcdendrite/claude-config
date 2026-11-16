# CLI registrar flag, default and `func` round-trip tests

## Context

Goal: add an in-process test that walks every registered `transcript-analysis` subcommand and checks its `set_defaults(func=...)` binding, remove the one duplicated flag default the sweep found, and correct a stale test-placement rule line.

Ask: the engineer asked "Can you mine the transcripts for info?" about GH-1432 item (d), after saying "I have no idea what item (d) was meant to cover. An agent was supposed to write that down." The scope below is this session's reading of what the mining found, not the engineer's words. The label "(d) a per-registrar flags/defaults/`func` sweep, parked behind PR B" first appears in an earlier handoff. The only text describing its content is an earlier plan's sentence that the subprocess tests "prove registration and dispatch at default arguments only, not flags or defaults. An in-process round-trip of each registrar's flags, defaults and `func` is a durable gap this move does not widen, left to a follow-up."

Why now: the registrar moves (a PR for the 18 blocks, then one for the last five) are merged, so all 46 subcommands register through `_add_<command>_subparser` and a single walk can cover them uniformly.

Scope narrowing, decided this session: R2 is dropped, R3 is deferred to #1157, and R4 becomes a derived default. The first draft also carried three items from the closed decomposition issue #1116: a check that each `getattr`-read dest is registered (R2), a parsed `--no-redact` refusal test over ten commands (R3), and a test pinning the `--spacings` default against its constant (R4).

The engineer asked for an adversarial architect review of them against the open issues: "Can you have the architect adversarially review all of those to see which are really worth doing in light of the 100+ filed issues against the repo". The verdicts are the architect's, not the engineer's:
- R2: drop. Its widest tier checks shared dests against every command's dests combined, so it misses the case it targets.
- R3: defer to #1157, which is open and owns the shared `--no-redact` refusal helper. A broken wiring there fails closed.
- R4: replace the test with a small production change that removes the duplicated default. The engineer selected "Derive the default (Recommended)".

The engineer accepted the revised scope as a whole by selecting "Confirm as written (Recommended)" (row 20).

Intended outcome: a rebound or misnamed `func` fails an in-process test for every subcommand, `rearm-backtest`'s `--spacings` default and help text come from `_REARM_BACKTEST_DEFAULT_SPACINGS` with `--help` output unchanged, and `.claude/rules/transcript-analysis-tests.md` no longer describes inline registration in `build_parser()`.

Every stow consumer is affected through one production change in `rearm_backtest.py`, detailed under Approach, plus test and rule files.

Evidence from exploration, gathered by a subagent and not yet read by the author of the design:
- `tests/test_transcript_cli.py` (415 lines) already walks `build_parser()` output, using a file-private `_subparsers_by_name` helper, for `--config-dir` routing, scope-flag sets and `--projects` typing. Nothing round-trips defaults or `func` for all commands.
- The line limit is 1,000 lines for code and test files (`docs/design-decisions/code-file-line-limit.md`). `tests/test_transcript_cli_bootstrap.py` is 1,440 lines, already over it.
- `tests/_move_fidelity.py` `capture --all-help` and `compare-captures` dump every parser action's option strings, dest, nargs, const, default, type, choices and required, plus `set_defaults` entries.
- An existing subprocess test (`tests/test_transcript_cli_bootstrap.py:237-245`) asserts `cost-trend`'s output, so a `cost-trend` bound to `cmd_cost` is already caught. The seeded-defect proof for the `func` walk therefore needs a command whose subprocess test asserts no command-specific output.
- `--spacings` exists only on `rearm-backtest`, with the default `"40000,80000,120000"` duplicated in the parser and in `_REARM_BACKTEST_DEFAULT_SPACINGS`. No test pins the argparse default.

## Approach

Add one in-process test that checks every subcommand's `set_defaults(func=...)` binding by identity (R1). Derive `rearm-backtest`'s `--spacings` default and help text from `_REARM_BACKTEST_DEFAULT_SPACINGS` so the value has one home, and drop the stale inline-registration clause from `.claude/rules/transcript-analysis-tests.md`. The one production change leaves `rearm-backtest`'s CLI surface unchanged for every stow consumer: the same flags, the same parsed `--spacings` default `"40000,80000,120000"`, and byte-identical `--help` output.

Line numbers are at the branch's merge-base with `origin/main`. Tests are under `claude/.claude/scripts/tests/`, modules under `claude/.claude/scripts/transcript_analysis/`.

**R1, `func` binding** (M1; rows 3-7, 22), in `test_transcript_cli.py`'s `TestBuildParser`: `test_every_subcommand_binds_its_own_modules_cmd_handler`, with the docstring "Each subcommand's registrar lives outside cli.py and binds the cmd_<name> handler its own module defines, checked by name and module rather than by a token the handler prints." For each `name, subparser` in `_subparsers_by_name(cli.build_parser())`, with `snake = name.replace("-", "_")`, it records the subcommand and what it found as an offender when any of these fails:
- `getattr(cli, f"_add_{snake}_subparser", None)` exists, and its `__module__` is not `cli.__name__`.
- `subparser.get_default("func")` exists, its `__name__` is `f"cmd_{snake}"`, and its `__module__` equals the registrar's.

The comparison is by `__name__` and `__module__` strings, not by object identity against `sys.modules`, so a test that loads a fresh module copy cannot make it flap.

An absent registrar, an absent `func` or a callable without `__name__` or `__module__` is recorded as an offender and the loop moves on, so one bad subcommand never hides the rest. A subcommand registered under an alias would be reported as having no registrar, which row 5 says cannot happen today.

The `cli.__name__` clause fails a subcommand registered inside `cli.py`, which is the invariant the rule edit below relies on. The registrar is looked up on `cli`, so `from ... import _add_<snake>_subparser` into `cli.py` is the checked wiring convention.

The test makes three assertions, and fails once with all of them in one message, collecting before asserting as `test_every_subcommand_with_both_scope_flags_refuses_them_together` does (:357-374):
- The walk is non-empty, as `assert both_flags` does (:363).
- The offender map is empty.
- The walked `snake` names equal the `<snake>` parts of the `_add_<snake>_subparser` callables the package defines. The message carries both set differences.

The defined set is discovered this way:
- Iterate `pkgutil.iter_modules` over the package's `__path__` (`sys.modules[cli.__package__].__path__`), since `cli.__package__` alone is a string, which `iter_modules` rejects.
- Include `cli` in the scan, and skip a module named `__main__`.
- Import each module as `f"{cli.__package__}.{module_info.name}"`.
- Count a callable only in the module its `__module__` names.

This set does not depend on `cli.py`'s import or call lists, so a registrar defined but never called, or a call and its import removed together, fails the test instead of shrinking the walk. The set is derived, not a literal table.

**What R1 adds over the subprocess tests.** `test_transcript_cli_bootstrap.py` infers each binding from a token the handler prints, so a token another handler also prints lets a wrong binding pass. Rows 6 and 7 hold the evidence: nine commands have an in-process assert on their `func`, and two commands' subprocess tests assert a token other handlers print. R1 makes that check in-process, by name and module, for all 46 commands and for every command added later. Verification step 4 binds `cost-ledger` to `cost.cmd_cost_trend`, which its subprocess test should not notice and R1 must (row 8).

**`--spacings` default** (M2; rows 9-13, 18, 19), in `rearm_backtest.py`:
- After `_REARM_BACKTEST_DEFAULT_SPACINGS` (:20), add `_REARM_BACKTEST_DEFAULT_SPACINGS_ARG: str = ",".join(str(spacing) for spacing in _REARM_BACKTEST_DEFAULT_SPACINGS)`.
- `_parse_rearm_spacings_arg`'s fallback (:256) becomes `getattr(args, "spacings", None) or _REARM_BACKTEST_DEFAULT_SPACINGS_ARG`, in place of its own inline join.
- The `--spacings` argument takes `default=_REARM_BACKTEST_DEFAULT_SPACINGS_ARG` (:598), and its help ends "(default: %(default)s)." in place of the literal (:599).

The constant renders as `"40000,80000,120000"`, the literal it replaces, and argparse expands `%(default)s` to that same string (row 10). So the parsed default and the `--help` bytes do not change, which Verification step 1 proves against the merge-base. No comment or docstring changes. No test is added. With one home for the value, a test comparing the argparse default against the constant compares the constant with itself. The existing subprocess run at default arguments already parses the derived default in a real process (row 13).

**Rule** (M3; row 15): `.claude/rules/transcript-analysis-tests.md:21-22` ends at "the file of the module that registers its subparser." and drops ", or `tests/test_transcript_cli.py` when `build_parser()` registers it inline". No other rule line changes. R1 is a walk across commands, which :23-24 already places in `test_transcript_cli.py`.

**Alternatives set aside:**
- Leaving the subprocess tests as the binding check. A shared output token lets a wrong binding pass (row 7).
- More per-command `func` asserts like the nine that exist (row 6). Each covers one command, and a command added later gets none unless its author remembers.
- A 46-row literal table of every handler, flag and default. It restates every registrar value in a second home, against `CLAUDE.md` § "Single source of truth".
- A committed golden of `_move_fidelity.dump_parser` output per subcommand. It would update on every flag change and run to hundreds of lines, the two traits staff-sdet names as golden misuse (G5, row 16).
- Re-parsing every flag and checking its dest. That tests argparse's own contract (row 16).
- A separate AST test that `cli.py` holds no `add_parser` call. R1's module clause catches the same registration from the parser side.
- For `--spacings`, the inline join at :598 alone. It would leave :256 and :598 holding the same expression (row 19). The named constant is the same derivation with one home.
- For `--spacings`, keeping the literal and testing it against the constant, the first draft's R4. The engineer selected deriving the default instead (row 18).

**Size.** Both touched Python files stay under G1, and Critical files holds the projected diff.

**Assumption ledger.**

**Root:** Two parser-side facts hold only by care. Each subcommand's `func` binding is checked by identity for 9 of 46 commands, and otherwise only through a handler's output, which another handler can share. `--spacings`' default is a literal copy of `_REARM_BACKTEST_DEFAULT_SPACINGS` that no test compares with it. `[verified: rows 6, 7, 9, 13]`

**Givens:**
- G1. Code and test files are capped at 1,000 lines. Reason: `docs/design-decisions/code-file-line-limit.md` owns the cap. `[verified: .claude/rules/transcript-analysis-tests.md:37; the figure carried from .claude/plans/cli-remaining-subparsers-move.md G1]`
- G2. Agents run `select-tests.py`, not the full suite. Reason: the repo `CLAUDE.md` § "Commands" owns the rule. `[verified: CLAUDE.md]`
- G3. Test placement follows `.claude/rules/transcript-analysis-tests.md`. Reason: the rule owns placement. `[verified: read of :11-40]`
- G4. `require-diff-size-prompt.sh` gates a branch diff of 400 or more changed lines outside `.claude/plans/`. Reason: the hook owns the threshold. `[unverified]`, carried from `.claude/plans/cli-remaining-subparsers-move.md` G4 and not re-read.
- G5. staff-sdet flags "snapshots updating on every change (useless signal), snapshots larger than a screen (unreviewable)". Reason: `claude/.claude/agents/staff-sdet.md` owns that reviewer's criteria. `[verified: staff-sdet.md:43]`

**Rows:**
1. The engineer did not define item (d). `[engineer-verified: "I have no idea what item (d) was meant to cover."]`
2. Item (d) is the earlier plan's "in-process round-trip of each registrar's flags, defaults and `func`". This is the session's reading of mined transcripts, put to no clarifying question, and the engineer reviews the plan before implementation. `[unverified]`
3. All 46 subcommands register through `_add_<snake>_subparser` registrars that `build_parser()` calls by name (cli.py:118-163), and `build_parser()` holds no `add_parser` call. Each registrar's module binds `set_defaults(func=cmd_<snake>)`, where `<snake>` is the subcommand name with `-` replaced by `_`. `[verified: read of cli.py:100-165; Grep for "set_defaults\(func=", 46 bindings in 40 modules]`
4. `ArgumentParser.get_default(dest)` returns the first non-`None` default of an action with that dest, and otherwise the value `set_defaults` stored. `[verified: argparse.py:1486-1499, read in the venv's base interpreter, Python 3.14.10]`
5. No subparser registers an alias, so `_subparsers_by_name` yields each subcommand once. `[verified: Grep for "aliases=", no hits]`
6. Nine commands have an in-process test asserting their `func` by identity: `author-outcome`, `cache-efficiency`, `handoff-signal-response`, `plan-boundary`, `pr-cost`, `pr-cost-export`, `read-cost`, `review-cost-by-size` and `review-round-cost`. `cost-ledger` is not among them. `[verified: Grep under tests/ for "\.func\b" and "\"func\""]`
7. The subprocess tests infer a binding from a printed token, and some tokens are shared. `[verified: read of :60-250, :520-830, :960-1245]`
   - `cost-ledger`'s test (test_transcript_cli_bootstrap.py:1033-1053) asserts exit 0 and "2026-W21" on `_seed_priced_account`.
   - `cost-trend`'s test (:237-245) asserts the same token on the same seed and environment.
   - `read-cost`'s test (:1165-1173) asserts "requires --this-repo", which `cost-counts` (subagent_mix.py:512) and `review-cost-by-size` (review_cost_by_size.py:322) also print.
   - Same-module siblings are told apart by token: `cost` and `cost-trend`, the three `turn-shape` commands, and the three `audit-routing` commands except `audit-routing-samples`, which is told apart by its JSON output.
8. A `cost-ledger` bound to `cost.cmd_cost_trend` runs and prints "2026-W21".
   - `cost_ledger.py` imports `cost` by module (:30), binds at :733, and registers `--projects` and `--this-repo` (:717; scope.py:516-521). `[verified: read; scope._projects_glob (scope.py:907) not read]`
   - `cmd_cost_trend` reads `args.this_repo` directly (cost.py:1211) and its other flags through `getattr` (cost.py:1203; scope.py:773, :800). `[verified: read]`
   - A scratch process that rebound `cost_ledger.cmd_cost_ledger` to `cost.cmd_cost_trend` exited 0 with "2026-W21" in stdout, and the real subprocess test has not run under that binding. `[verified: staff-sdet's scratch run, this round]` Verification step 4 runs the real test.
9. `--spacings` defaults to `"40000,80000,120000"` (rearm_backtest.py:598), its help repeats the literal (:599), and `_REARM_BACKTEST_DEFAULT_SPACINGS` is `(40_000, 80_000, 120_000)` (:20). `_parse_rearm_spacings_arg` joins the constant itself when `spacings` is falsy (:256). `[verified: read]`
10. Expanding `%(default)s` in the `--spacings` help leaves the `--help` bytes unchanged. `[verified: read of the Python 3.14.10 source]`
    - The default `HelpFormatter` returns `action.help` as written (argparse.py:702-703).
    - `_expand_help` formats the help with `vars(action)` only when it holds a `%` (:663-676), and replaces only values carrying `__name__`, which a `str` does not.
    - No `transcript_analysis` parser sets `formatter_class`, so `ArgumentDefaultsHelpFormatter`'s appended default (:741-753) does not apply. `[verified: Grep for "formatter_class", hits only at analyze-context.py:276 and tests/_move_fidelity.py:879]`
    - The `--spacings` help holds no other `%`.
    - CI pins Python `'3.14'`, the minor read here. `[verified: .github/workflows/tests.yml:115]`
    - A scratch `format_help()` comparison was byte-identical between the literal and `%(default)s` forms at `COLUMNS` 60 and 200. `[verified: staff-sdet's scratch run, this round]`
11. No package parser uses `%(default)s` today. The one hit is a fixture string in test_transcript_move_fidelity_parser.py:260. `[verified: Grep]`
12. `docs/transcript-analysis.md:2255` documents the default as `40000,80000,120000`, and the value does not change. `[verified: Grep]`
13. Three existing tests touch the `--spacings` default. `[verified: read]`
    - `TestParseRearmSpacingsArg` (test_transcript_rearm_backtest.py:158-185) holds four tests. Its default test passes `spacings=None` (:159-163), so it covers the fallback and never the argparse default.
    - `test_transcript_analysis_rearm_backtest_help_exits_zero` (test_transcript_cli_bootstrap.py:1188-1191) asserts `--spacings` appears in `--help`.
    - `test_transcript_analysis_rearm_backtest_subprocess_finds_seeded_session` (:1194-1203) runs at default arguments, so a real process parses the argparse default.
14. The capture tooling proves the CLI surface is unchanged.
    - `_move_fidelity.py capture --all-help` runs `--help` for the top level and each subcommand in a subprocess, writing stdout, stderr and exit status. `[verified: read]`
    - It also writes a `.parser` dump whose per-action attributes include `default` and not `help` (:71, :605-660). `[verified: read]`
    - `compare-captures` compares every file of two capture directories byte for byte (`_move_fidelity.py:679-735`). `[verified: staff-sdet's read, this round]`
    - `.claude/plans/cli-subparser-move.md:204-211` ran both against a merge-base worktree. `[verified: read]`
15. The rule's inline clause (:21-22) matches no command, since `build_parser()` registers none inline (row 3). Fixing it was the session's proposal, which the engineer kept by selecting "Confirm as written (Recommended)" (row 20). `[verified: read of the rule and cli.py]`
16. A test re-parsing every flag would check argparse's own storing of values and defaults. `[unverified]` `dump_parser` writes one line per action, mutually exclusive group and `set_defaults` entry (_move_fidelity.py:593-602), so a golden of 47 parsers runs to hundreds of lines. `[verified: read; the total is an estimate]`
17. Tests pin a default only where a second copy exists, so a default with no second copy, such as `cost`'s `--top`, stays unpinned. `[engineer-verified: "Pin only duplicated defaults (Recommended)"]`, the label selected in answer to whether the tests pin every default or only duplicated ones.
18. `--spacings`' default derives from `_REARM_BACKTEST_DEFAULT_SPACINGS` rather than being pinned by a test. `[engineer-verified: "Derive the default (Recommended)"]`, the label selected for the `--spacings` change.
19. Three things in the `--spacings` change are the session's, not the engineer's. `[unverified]`
    - The selected option's description.
    - The architect-review verdicts behind it, which are the previous architect consult's.
    - The shape of the change: a `",".join(...)` default "or an equivalent derived from the constant", with `%(default)s` help.
    The named constant shared with :256 is this design's choice within that shape.
20. The engineer was asked whether the revised scope should stay: R1, the `--spacings` change and the rule fix, with R2 dropped and R3 deferred to #1157. They selected "Confirm as written (Recommended)". `[engineer-verified: "Confirm as written (Recommended)"]`, covering only that scope. The drop and the deferral are the previous architect consult's verdicts.
21. `select-tests.py` maps every path under `claude/.claude/scripts/` to the scripts tests (:703) and `.claude/rules/` to the skills and hooks tests (:1008). `[verified: read]`
22. The earlier plan's Out of scope says GH-1432's final `cli.py` phase lists "a table-driven test that each subcommand dispatches to its owning module's `cmd_*`". R1 checks that property without the table. `[unverified]`, carried from `.claude/plans/cli-remaining-subparsers-move.md:208`, with the issue not read.
23. `test_transcript_cli.py` is 415 lines, and `rearm_backtest.py` ends at :601. `[verified: Read]`
24. The diff lands near 45 changed lines, under G4's 400. `[unverified]`; Verification step 8 measures it.
25. The defined set the completeness assertion derives equals `build_parser()`'s calls on the clean tree. `[verified: staff-sdet's static scan, this round]`
    - All 53 package modules are imported at module scope from `cli`, so the new test adds no first-time import.
    - There are 46 module-level `_add_*_subparser` definitions, with no duplicates, aliases or non-command helpers.
    - The package holds no `__main__` block and no `__main__.py`.

**Mechanisms:**
- M1: R1, the `func` binding walk. `anchors: root, row3, row4, row5, row6, row7, row22`. Lighter primitives checked:
  - The subprocess tests prove some handler runs, and a shared output token lets the wrong one pass (row 7).
  - Per-command identity asserts cover only the nine commands that have one (row 6).
- M2: the `--spacings` default and help derived from one named constant. `anchors: root, row9, row10, row11, row18, row19`. It is a four-line change inside one file, the lightest that removes the second copy.
- M3: the rule edit. `anchors: row15`.
- M4: one `code-writer` dispatch, with a seeded-defect proof for R1 and a capture comparison for M2. `anchors: row8, row14, row21`.

## Critical files

**Dispatch: one `code-writer`, without a split** (M4). The three files select the same scripts test run (row 21), and the seeded-defect step edits a production file the same agent must restore. The brief requires:
- Verification steps 1-8, run by the agent, with these outputs in the return:
  - `compare-captures`' exit code and output, and step 1's `grep` lines;
  - under the seeded defect, R1's failing test ID and assertion message and the `cost-ledger` subprocess test's result, then step 4's `git diff --quiet` exit code;
  - ruff's output, `wc -l` per touched Python file, the pass counts from steps 3 and 5 with any failing node IDs, the numstat total and the untracked-file list.
- When `compare-captures` reports any finding, stop and return it. Never add a `--mask`, and never reshape the help or default to make the comparison pass.
- When R1 fails on the unmodified tree, return the failure. Never edit a production file to make it pass.
- No staging and no commits.

**Modify, production:** `claude/.claude/scripts/transcript_analysis/rearm_backtest.py` (:20-21, :256, :598-599). Every stow consumer runs it after `git pull`. Verification step 1 proves its CLI surface is unchanged.

**Modify, test:** `claude/.claude/scripts/tests/test_transcript_cli.py`: R1.

**Modify, rule:** `.claude/rules/transcript-analysis-tests.md` (:22).

**Also in the diff:** `.claude/plans/cli-registrar-flag-sweep.md`.

**Reuse:** `_subparsers_by_name` (test_transcript_cli.py:163); the non-empty guard and collect-every-offender loop (:357-374); `ArgumentParser.get_default`; argparse's `%(default)s` help expansion; `_move_fidelity.py capture --all-help` and `compare-captures`, with the merge-base recipe of `.claude/plans/cli-subparser-move.md:204-211`. No helper module or script changes.

Projected diff: about 45 lines. About 36 are in `test_transcript_cli.py`, about 7 in `rearm_backtest.py` (4 added, 3 removed), and 2 in the rule (row 24).

## Verification

Run from the worktree root. `<tests>` is `claude/.claude/scripts/tests`, `<pkg>` is `claude/.claude/scripts/transcript_analysis`, `<scratch>` is a new directory outside the repository, and `<base>` is the SHA that `git merge-base HEAD origin/main` prints.

1. `rearm-backtest`'s CLI surface is unchanged (rows 10, 14):
   - `git worktree add --detach <scratch>/base <base>`, then `mkdir -p <scratch>/account`.
   - Before side: `scripts/venv-run.sh python3 <tests>/_move_fidelity.py capture --script <scratch>/base/claude/.claude/scripts/transcript-analysis.py --account <scratch>/account --out <scratch>/before --all-help`
   - After side, once the edits are in: the same `capture` with `--script claude/.claude/scripts/transcript-analysis.py --out <scratch>/after`.
   - `scripts/venv-run.sh python3 <tests>/_move_fidelity.py compare-captures <scratch>/before <scratch>/after` exits 0 and prints no finding. It covers `help-rearm-backtest.stdout`, the `--help` bytes, and `help-rearm-backtest.parser`, whose `--spacings` line carries the parsed default, as well as every other subcommand.
   - `grep -- 40000,80000,120000 <scratch>/after/help-rearm-backtest.stdout <scratch>/after/help-rearm-backtest.parser` prints a line from each file, so the comparison did not pass over empty captures.
2. `scripts/venv-run.sh ruff check <tests>/test_transcript_cli.py <pkg>/rearm_backtest.py` exits 0.
3. Tests touching the two edited Python files pass (row 13):
   - `scripts/venv-run.sh pytest <tests>/test_transcript_cli.py <tests>/test_transcript_rearm_backtest.py -q`.
   - `scripts/venv-run.sh pytest <tests>/test_transcript_rearm_backtest.py -k TestParseRearmSpacingsArg -q` reports 4 passed.
   - `scripts/venv-run.sh pytest <tests>/test_transcript_cli_bootstrap.py -k rearm_backtest -q` reports 2 passed: the `--help` run and the run at default arguments.
4. Seeded defect for R1 (rows 7, 8). Apply it with `Edit`, run each named test with `scripts/venv-run.sh pytest <file> -k <test name>`, then restore the line with `Edit`:
   - `<pkg>/cost_ledger.py:733`: `set_defaults(func=cmd_cost_ledger)` becomes `set_defaults(func=cost.cmd_cost_trend)`.
   - `test_every_subcommand_binds_its_own_modules_cmd_handler` in `<tests>/test_transcript_cli.py` fails, and its message names `cost-ledger`.
   - `test_transcript_analysis_cost_ledger_subprocess_finds_seeded_session` in `<tests>/test_transcript_cli_bootstrap.py` passes, which shows the subprocess layer misses this binding. If it fails instead, report its output: row 8 does not hold, and the Approach's statement of what R1 adds needs revising before commit.

   Then `git diff --quiet -- <pkg>/cost_ledger.py` exits 0.

   Three further one-shot seeds, one at a time, each restored with `Edit` and followed by `git diff --quiet -- <pkg>/cli.py` exiting 0. Leave the unused import in seed A alone, since ruff is not part of this step. Run `scripts/venv-run.sh pytest <tests>/test_transcript_cli.py -k test_every_subcommand_binds_its_own_modules_cmd_handler` for each and report which part of the failure message names the subcommand:
   - Seed A, registrar defined but never wired: delete the line `_add_rearm_backtest_subparser(sub)` from `<pkg>/cli.py`'s `build_parser()`. Only the defined-minus-walked difference names `rearm_backtest`.
   - Seed B, registrar attributed to `cli.py`: add `_add_buckets_subparser.__module__ = __name__` after the imports in `<pkg>/cli.py`. Only the offender map names `buckets`, through its `cli.__name__` clause, because `cli` is in the scan.
   - Seed C, an inline registration: add `sub.add_parser("zz-inline")` at the end of `build_parser()` in `<pkg>/cli.py`. The offender map names `zz-inline` as having no registrar, and the walked-minus-defined difference names `zz_inline`.
5. `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py` passes (G2):
   - For any failure, record its node IDs and rerun them from `<scratch>/base`, per `CLAUDE.md`'s "Prove your change caused a failing check". An ID that fails only on this tree blocks.
   - Report pass counts, with zero failed, for `test_transcript_cli.py`, `test_transcript_rearm_backtest.py` and `test_transcript_cli_bootstrap.py`. Take them from step 3's runs, since `select-tests.py` runs under `-n auto` and may not print per-file counts.
6. `wc -l <tests>/test_transcript_cli.py <pkg>/rearm_backtest.py` reports each under 1,000 lines (G1).
7. `git grep -n "registers it inline" -- .claude/rules/` prints nothing.
8. Scope:
   - `git diff --name-only` prints exactly the three modified files under Critical files.
   - `git ls-files --others --exclude-standard` prints only the plan file. Report any other line as a stray file, and do not stage it.
   - `git diff --numstat` totals near 45 changed lines (row 24).
   - `git worktree remove <scratch>/base`.

## Out of scope

- **R2, a check that each silent-default `getattr` read names a registered dest.** Dropped on the previous architect consult's verdict (row 20). Its widest tier checks a shared dest against every command's dests combined, so it misses a dest one command renames while another still registers the old name, which is the case it targets.
- **R3, a parsed-argv `--no-redact` refusal test over the ten commands that register the flag.** Deferred to #1157, which is open and owns the shared `--no-redact` refusal helper (row 20). A broken wiring there fails closed, because a handler that never sees `no_redact` redacts by default. A test over ten per-module refusals written now would be rewritten when that helper lands.
- **A check that each `getattr`-read dest is registered by any subparser, and #1138's helper-based direction.** The any-subparser check passes the same rename R2's widest tier misses. Registering a shared flag through one helper, the direction #1138 tracks for `--config-dir` (`.claude/plans/config-dir-flag-walk-and-dedupe.md:7-9`; the issue not read), leaves one registration site per shared dest, and it is that issue's change.
- **Dropping the `getattr` defaults for direct reads, and building test Namespaces, the hand-built args factories included, from `build_parser()`.** This is the foundation fix: a renamed dest would then raise at its first read in any test. It touches most package modules and the args factories across the test tree.
- **Pinning every default, by a literal table or a committed golden** (row 17). The engineer chose to pin only duplicated defaults.
- **`--spacings ""` taking the default.** The falsy fallback at rearm_backtest.py:256 maps an empty value to the default, while a whitespace-only value exits 2 (test_transcript_rearm_backtest.py:177-183). This change keeps that behavior.
- **`docs/transcript-analysis.md:2255`'s stated default.** It documents the value for users, and the value does not change (row 12).
- **Splitting `test_transcript_cli_bootstrap.py` (1,440 lines) under G1.** This change runs some of its tests and edits none.
- **Editing GH-1432, #1116, #1138 or #1157, or filing an issue for any item here.** They are GitHub records outside the diff, and filing is the engineer's call.
- **A permanent in-suite negative control for R1's detector.** staff-sdet suggested a monkeypatched rebinding of `cost_ledger.cmd_cost_ledger`. Verification step 4's one-shot seeded defect, with its restore checked, is the proof here, and a permanent control needs the per-subcommand predicate split into a separately callable function.
- **The "proves ... dispatches X to cmd_X" docstrings in `test_transcript_cli_bootstrap.py`.** Rows 7-8 show some of them overclaim, since a shared token lets a wrong binding pass. R1's docstring says the binding is checked by name and module, so R1 is not redundant with them. Editing the bootstrap docstrings is a separate change.
- **A CHANGELOG entry.** No command's arguments, default values, output or `--help` bytes change (Verification step 1).
