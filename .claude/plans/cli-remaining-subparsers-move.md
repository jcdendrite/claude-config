# transcript-analysis: move the five remaining inline subparsers out of cli.py (GH-1432 item c)

## Context

Goal: register the last five inline subcommands (`cost`, `cost-trend`, `pr-cost`, `review-round-cost`, `period-cost`) through `_add_<command>_subparser(subparsers)` registrars in their owning modules, so every subcommand in `transcript_analysis/cli.py`'s `build_parser()` registers the same way, with no change to any command's parsed arguments or `--help` output.

Ask: "What's the correct design here? Seems you are over-indexing on the line limit convention but keeping an inconsistent design with some functions inline and others in the module seems worse. Want to consult the architect?" (engineer, this session, in answer to a question offering to skip the move, move the five anyway, or split the four modules first). The architect consult recommended moving the five into their existing modules.

Why now: PR 1451 (GH-1432 PR B) moved the 18 other inline blocks and left these five inline. Their modules (`cost.py`, `pr_cost.py`, `review_rounds.py`, `period_cost.py`) are each already over the 1,000-line limit in `docs/design-decisions/code-file-line-limit.md`. No repo-local line-limit check exists yet, so nothing enforces that limit today. Item (d), the per-registrar flags/defaults/`func` sweep, is parked behind this move.

Evidence carried from the architect consult and a spot check, not independently re-verified line by line:
- `cli.py` is 419 lines. The five inline blocks run roughly lines 152 to 365 of `build_parser()`.
- 41 `_add_<command>_subparser` registrars exist in the package (counted with `git grep`), including `routing_guard.py:2084` inside a 2,115-line module.
- The five modules' line counts today: `cost.py` 1,255, `pr_cost.py` 1,186, `review_rounds.py` 1,550, `period_cost.py` 1,844.
- `.claude/plans/cli-subparser-move.md` is the merged record of PR 1451 and is not revised by this plan.

## Approach

Move each of the five inline blocks out of `build_parser()` into an `_add_<command>_subparser(subparsers)` appended to the module that defines its handler, and call that registrar from `build_parser()` where the block stood. All 46 subcommands then register the same way, and `tests/_move_fidelity.py compare-captures` proves, against a capture taken at the merge-base, that no command's parser configuration or `--help` bytes change.

A bare `:N` is a line, at `<base>` (the SHA `git merge-base HEAD origin/main` prints), of the file its bullet names. Modules are under `claude/.claude/scripts/transcript_analysis/`.

**The move** (M1; rows 3, 4, 7):
- `cost` (`cli.py:152-208`) → `cost.py`, `_add_cost_subparser`.
- `cost-trend` (`cli.py:218-228`) → `cost.py`, `_add_cost_trend_subparser`, after `_add_cost_subparser`.
- `pr-cost` (`cli.py:233-301`) → `pr_cost.py`, `_add_pr_cost_subparser`.
- `review-round-cost` (`cli.py:307-341`) → `review_rounds.py`, `_add_review_round_cost_subparser`.
- `period-cost` (`cli.py:347-365`) → `period_cost.py`, `_add_period_cost_subparser`.

**Registrar shape** (M1; rows 7-11). It is the shape PR 1451 gave its 18 moved registrars, `cost_ledger.py:709-733` among them:
- Signature: `def _add_<command>_subparser(subparsers: argparse._SubParsersAction) -> None:`, with no docstring.
- Placement: after the module's last top-level statement, with two blank lines before each `def`.
- Body: the block's lines at their current four-space indentation, with five substitutions and no other edit except the reflow below:
  - `sub.add_parser(` becomes `subparsers.add_parser(`.
  - `_add_project_scope_args(` becomes `scope._add_project_scope_args(`.
  - `_add_extra_config_dirs_arg(` becomes `scope._add_extra_config_dirs_arg(`.
  - `_EXTRA_CONFIG_DIRS_HELP_PREAMBLE` becomes `scope._EXTRA_CONFIG_DIRS_HELP_PREAMBLE`.
  - `_iso_date` becomes `scope._iso_date`.
- Module-local reads stay bare: the `cmd_*` handlers, `REVIEW_SKILLS`, `_PR_COST_ASOF_WINDOW_DAYS_DEFAULT` and `_DEFAULT_PR_COST_PLAN_FILE_GLOB`. Local names (`p_cost`, `p_pr_cost`, ...) and every string literal stay as written.
- One reflow, in `_add_period_cost_subparser`. The `--since` and `--until` calls (`cli.py:355-360`) each take the four-line shape their `--sample` sibling uses (`cli.py:361-364`): `p_period_cost.add_argument(`, then `"--since", metavar="DATE", type=scope._iso_date,`, then the unchanged `help="..."` literal with a trailing comma, then `)`. The `scope.` prefix takes the one-line forms past ruff's 130 columns (row 11). Binding `_iso_date` by name is barred in `period_cost.py` (row 9), and a local alias would add a name only to dodge the line length.
- No module gains an import, and no module docstring changes (row 7).

**`build_parser()`** (M2; row 3): each block, with the blank line on either side of it, becomes one `_add_<command>_subparser(sub)` call at the same position. Registration order is unchanged, and the 46 calls run as one block with no blank lines between them.

**`cli.py` import block** (M3; rows 8, 12, 14):
- `cost`: `from transcript_analysis.cost import _add_cost_subparser, _add_cost_trend_subparser`.
- `pr_cost`, `review_rounds` and `period_cost`: one line each, naming its registrar. `cmd_pr_cost`, `cmd_review_round_cost`, `cmd_period_cost`, `REVIEW_SKILLS` and the two pr_cost constants leave.
- `scope`: `from transcript_analysis.scope import _SUBCOMMANDS_REFUSING_TOP_LEVEL_CONFIG_DIR`, the one name `main()` reads by name (`cli.py:393`). The four registration names leave.
- The note at `cli.py:7` and `from transcript_analysis import scope` (for `main()`'s `scope.PROJECTS_DIR`) stay. `main()` is not edited.
- Set aside: rewriting `main()`'s read as `scope._SUBCOMMANDS_REFUSING_TOP_LEVEL_CONFIG_DIR` so the by-name scope import goes. It edits `main()`, which the move otherwise leaves alone, and nothing reassigns the tuple, so the binding form changes nothing at runtime.

**Two test docstrings** (M4; rows 12, 13, 15). The move makes each false, and neither grows:
- `tests/test_transcript_analysis_cost_import_direction.py:36-38`, `_is_guarded_name`, becomes "A name counts toward the guarded surface unless it's `_`-prefixed / (private) or `cmd_`-prefixed (a subcommand handler, a sanctioned crossing).", where `/` marks the existing line break. The clause naming `cli.py` as the binder of `set_defaults(func=...)` goes. The guard's code stays.
- `tests/test_transcript_cli_bootstrap.py:239`, the second line of `test_transcript_analysis_cost_trend_subprocess_finds_seeded_session`'s docstring: "cmd_cost_trend's own re-export." becomes "cost-trend's dispatch to cmd_cost_trend.", with the closing quotes kept.

No test moves and none is added (row 15). The substitute verification is M6.

**Architecture doc** (M5; `docs/transcript-analysis-architecture.md`; rows 20, 21):
- :22-23: the sentence ends "at that command's place in registration order." The clause "except the five the `cli.py` section below names" goes.
- `cost.py` section: after :226, add "`_add_cost_subparser` and `_add_cost_trend_subparser` are the two names `build_parser()` reaches bare.", the form the `subagent_mix.py` section uses (:514-515).
- `review_rounds.py` section: delete the :263 bullet "`cli.py`'s `build_parser()`: review-round-cost's `--skill` choices.", since the module's own registrar now reads `REVIEW_SKILLS`. After :273, add "`_add_review_round_cost_subparser` is the one name `build_parser()` reaches bare."
- `pr_cost.py` section, :435-436: the sentence ending "are the three names reached bare from `cli.py`." becomes "`_add_pr_cost_subparser` is the one name `build_parser()` reaches bare."
- `period_cost.py` section, :619, becomes "`_add_period_cost_subparser` is the one name `build_parser()` reaches bare."
- `cli.py` section, :769-772: delete the two sentences from "`build_parser()` registers five subcommands inline" through "the form a new subcommand uses." :22-23 already states how every subcommand registers. "`transcript-analysis.py` re-exports both functions." stays.

**Alternatives set aside** (rows 1, 4, 5, 21, 22):
- Leaving the five inline. The engineer judged the mix of inline blocks and module registrars worse than the line-limit concern (row 1).
- Sibling `<command>_cli.py` modules holding only the registrars, so the four over-limit modules do not grow. Each of the 41 existing registrars sits in its handler's module (row 4), so this adds a second placement rule. Each new module also needs its own test file or a `MODULES_TESTED_ELSEWHERE` entry, and its own architecture-doc section (row 21).
- Splitting the four modules under 1,000 lines first. Each split is a seam-by-seam change of its own, and the move does not depend on one: a registrar appended beside its handler travels with that handler in any later split. Nothing enforces the limit yet (G1), and an existing registrar already sits in a 2,115-line module (row 5).
- One table-driven registrar fed per-command specs. Nothing would move verbatim, and the 41 hand-written registrars would stay beside it.
- Two PRs, each under G4's 400 lines. See row 22.

**Size.** `cli.py` falls from 419 lines to about 205. `cost.py` grows by about 74 lines to about 1,329, `pr_cost.py` by about 72 to about 1,258, `review_rounds.py` by about 38 to about 1,588, and `period_cost.py` by about 24 to about 1,868. All four are over 1,000 lines before and after (G1, row 6).

**Assumption ledger.**

**Root:** Five subcommands (`cost`, `cost-trend`, `pr-cost`, `review-round-cost`, `period-cost`) still register through 191 lines of inline `sub.add_parser` blocks in `transcript_analysis/cli.py`'s `build_parser()`, while the other 41 register through an `_add_<command>_subparser` in their handler's module. This change moves the five into registrars in their handlers' modules, with no change to any command's parsed arguments or `--help` output. `[verified: read of cli.py; rows 3, 4]`

**Givens:**
- G1. Code files are capped at 1,000 lines, and no check enforces the cap yet. Reason: `docs/design-decisions/code-file-line-limit.md` owns the cap and defers its check until an offender assessment exists. `[verified: code-file-line-limit.md:19, :50-57; Grep of *.py and *.sh for the doc's slug found none]`
- G2. Agents run `select-tests.py`, not the full suite. Reason: the repo `CLAUDE.md` § "Commands" owns the rule. `[verified: CLAUDE.md "Commands"]`
- G3. ruff runs at line length 130 with the `E`, `F`, `B`, `I`, `UP` and `SIM` rules, and its one per-file ignore covers none of these files. Reason: `pyproject.toml` owns it. `[verified: pyproject.toml:2, :6, :14-15]`
- G4. `require-diff-size-prompt.sh` denies a commit that brings the branch's cumulative diff, minus `.claude/plans/`, to 400 or more changed lines until the engineer answers `Proceed anyway`. Reason: the hook owns the threshold. `[verified: require-diff-size-prompt.sh:4-8, :13-14]`
- G5. `.claude/plans/cli-subparser-move.md` and GH-1432 stay as written. Reason: the plan is the merged record of PR 1451, read-only under `CLAUDE.md` § "Scope discipline" Axis 3, and the issue is a GitHub record outside this diff that the dispatching session's instructions keep unedited. `[verified: CLAUDE.md "Scope discipline"; the issue rule relayed by the dispatching session]`
- G6. `claude/.claude/scripts/` installs to every stow consumer. Reason: the repo `CLAUDE.md` section "Plans in this repo affect all stow users" owns it. `[verified: CLAUDE.md]`

**Rows:**
1. The engineer weighs the inline/registrar mix as worse than the line-limit concern. `[engineer-verified: "Seems you are over-indexing on the line limit convention but keeping an inconsistent design with some functions inline and others in the module seems worse."]`, typed in answer to a question offering to skip the move, move the five anyway, or split the four modules first.
2. This PR moves the five subparsers into their handlers' modules. The design above is an earlier `plan-architect` consult's recommendation. Asked at plan review whether this PR should do that move, the engineer selected "Yes, move the five (Recommended)". `[engineer-verified: "Yes, move the five (Recommended)"]` The label covers the move and its placement in the handlers' modules only, not the registrar shape, reflow or doc edits.
3. `cli.py` is 419 lines. `build_parser()` (:118-379) makes 41 registrar calls and holds five inline blocks: :152-208 (57 lines), :218-228 (11), :233-301 (69), :307-341 (35) and :347-365 (19), 191 lines in all, each with a blank line on either side. Registration order sets the top-level `--help` listing and the subparsers action's `choices`, and `capture --all-help` records both. `[verified: read of cli.py; the order claim carried from .claude/plans/cli-subparser-move.md row 2, unverified this session]`
4. The package holds 41 `def _add_<command>_subparser` registrars, and no `set_defaults(func=...)` in it names a module-qualified handler, so each registrar binds a handler its own module defines. `[verified: Grep for "^def _add_[a-z_]+_subparser", 41 hits; Grep for "set_defaults\(func=[A-Za-z_]+\.", no hits]`
5. `routing_guard.py` is 2,115 lines and defines `_add_routing_guard_subparser` at :2084. `[verified: Grep line count; Grep]`
6. `cost.py` is 1,255 lines, `pr_cost.py` 1,186, `review_rounds.py` 1,550 and `period_cost.py` 1,844. `[verified: Read of each file's end]`
7. Each target module already imports `argparse` and imports `scope` by module, and defines its handlers: `cost.py:11, :19, :263, :1122`; `pr_cost.py:10, :35, :647`, with its two constants at :44 and :46; `review_rounds.py:25, :39, :1294`, with `REVIEW_SKILLS` at :42; `period_cost.py:9, :27, :1817`. No registrar needs a new import. `[verified: Grep]`
8. The five blocks read four names from `scope`, each defined at top level there: `_add_project_scope_args` (:509), `_EXTRA_CONFIG_DIRS_HELP_PREAMBLE` (:711), `_add_extra_config_dirs_arg` (:718) and `_iso_date` (:1121). Every other name they read is defined in the block's target module. `[verified: read of cli.py:152-365; Grep of scope.py]`
9. `period_cost.py` is in `PRODUCTION_MODULES` (`tests/test_transcript_package_module_prefixes.py:46`), and that file's `test_production_modules_import_package_siblings_by_module_only` (:290) holds it to by-module sibling imports. `[verified: read]`
10. The 12 modules whose registrars register the subcommand-level `--config-dir` read `scope._EXTRA_CONFIG_DIRS_HELP_PREAMBLE` and `scope._add_extra_config_dirs_arg` qualified, 23 reads in all. `[verified: Grep]`
11. `cli.py:356` is 129 columns and :359 is 127, so `scope._iso_date` takes them to 135 and 133. Every other lengthened line stays at or under 130; the longest, :318, goes from 121 to 127. `[verified: read, counted by hand; Verification step 6 confirms]`
12. `tests/test_transcript_analysis_cost_import_direction.py` guards only names neither `_`- nor `cmd_`-prefixed, and requires `cli.py` to keep some by-name import from `cost`. The new names are `_`-prefixed and the import stays, so its three tests stay green. Its `_is_guarded_name` docstring (:36-38) says the `cmd_` names are the handlers "cli.py binds via `set_defaults(func=...)`", which turns false. `[verified: read of :1-108]`
13. `tests/test_transcript_cli_bootstrap.py:238-239` calls the cost-trend test's subject "cmd_cost_trend's own re-export", and after the move only `cost.py` binds `cmd_cost_trend`. No other test docstring or comment names `cli.py` as a binder of the five handlers. `[verified: read; Grep of tests/ for "re-export", "import cmd_" and "cli\.py (binds|imports|reads)"]`
14. Nothing reads a removed name through `cli`. The shim imports only `build_parser` and `main` (`transcript-analysis.py:7`), and no file reads or patches `cli.cmd_*`, `cli.REVIEW_SKILLS`, the pr_cost constants or the scope names through `cli`. `[verified: Grep of the repo outside .claude/plans/]`
15. The in-process wiring tests for cost, pr-cost and review-round-cost already sit in their modules' test files and drive a shim copy's `build_parser()` (`test_transcript_cost.py:1310`, `test_transcript_pr_cost_gh.py:965, :995`, `test_transcript_review_rounds.py:4722-4781`). `test_transcript_cli.py`'s uses of `cost` and `period-cost` (:136-145, :155, :330) test `main()` or walk every subcommand, which `.claude/rules/transcript-analysis-tests.md` keeps in that file. No test moves. `[verified: Grep; read of the rule]`
16. The rule's CLI-wiring bullet (`.claude/rules/transcript-analysis-tests.md:21-22`) ends "or `tests/test_transcript_cli.py` when `build_parser()` registers it inline", which matches no command after the move. `[verified: read]`
17. A `.parser` dump renders each callable as `<callable MODULE:QUALNAME>` by its defining module, and only the captured script's own module as `<script>`. The handlers and `scope._iso_date` render the same on both sides, so `compare-captures` needs no `--moved-callable`. `[verified: _move_fidelity.py:16-21, :29-30]`
18. `remainder` compares every kept top-level statement's AST and every comment block outside the dropped spans. It diffs the set of top-level import triples (`module:name`) and reports each added or removed triple not declared. `--changed NAME` drops a name defined on both sides from the comparison. `[verified: _move_fidelity.py:354-364, :430-477, :891-898]`
19. A doubled space inside a help string passes `compare-captures`, because `ACTION_ATTRIBUTES` omits `help` and `--help` output collapses whitespace runs. The move keeps every help literal on its own unchanged source lines, so it adds no joining space. `remainder` skips each `--added` registrar's span, so only that discipline guards the literals, and the re-typed `--since` and `--until` lines carry the most risk. The residual is accepted: it changes no `--help` byte and no test reads `.help`. `[verified: _move_fidelity.py:71, :463; a doubled space seeded in a scratch copy passed compare-captures, per the staff-sdet review; the collapse carried from .claude/plans/config-dir-flag-walk-and-dedupe.md row 13]`
20. The architecture doc states the five's registration at :22-23, :263, :435-436, :619 and :769-772. The `cost.py` (:215-226) and `review_rounds.py` (:240-273) sections name no registrar. "reached bare" occurs at :436, :452 and :619, and :452, `cache_rebuild_rules.py`'s, stays. `[verified: read; Grep]`
21. `tests/test_transcript_analysis_architecture_doc.py` requires a `### <name>.py` heading for every package module and reads nothing else in the doc. `.claude/rules/transcript-analysis-tests.md` says `tests/test_transcript_analysis_module_test_files.py` fails a package module with neither its own test file nor a `MODULES_TESTED_ELSEWHERE` entry. `[verified: read of the test's :1-47; read of the rule]`
22. The diff lands near 470 changed lines outside `.claude/plans/`, over G4's 400. No one-PR form of this move fits under it, because the 191 moved lines count twice, once deleted and once added. One PR needs the engineer's `Proceed anyway` at G4's gate. Two PRs (cost, cost-trend and period-cost first, then pr-cost and review-round-cost) each fit, at the cost of a second review cycle and fidelity proof, and an interim state with two commands still inline. Asked whether to split, the engineer selected "Proceed anyway", so this stays one PR. `[engineer-verified: "Proceed anyway"]` The per-site line estimate is `[unverified]` and is measured by Verification step 11.
23. `select-tests.py` maps any path under `claude/.claude/scripts/` to `claude/.claude/scripts/tests` (:693), maps the architecture doc there too (:972), and maps `docs/` paths to the hooks and skills test directories (:983). `review_rounds.py` is in `_REVIEW_BENCH_SCRIPTS_DEPENDENCIES` (:126-137), so the review-bench tests under `evals/` run as well. `[verified: read]`
24. Binding time shifts. `cli.py` binds the five handlers, `REVIEW_SKILLS`, the two pr_cost constants and the scope names once, at its import. Each registrar instead reads them as module globals (or `scope.<name>`) each time `build_parser()` runs, so a patch of those names on their own modules now reaches a parser built afterward. No file under the tests, `evals/`, `plugins/` or the scripts patches any of them, so the shift is inert today. `[verified: staff-backend-engineer's Grep of those trees for setattr and quoted-name patch forms, no hits]`
25. The removed `cli` names are internal, and removing them breaks any out-of-repo importer of `transcript_analysis.cli.cmd_*`. A `transcript-analysis.py` run started while `git pull` is mid-update can also fail on import once. PR 1451's 18-module move carried the same exposure, and the PR body states it. `[verified: Grep of the repo for importers of transcript_analysis.cli, only the shim; out-of-repo importers are unverifiable from here]`

**Mechanisms:**
- M1: each block moves into a registrar appended to its handler's module, with five token substitutions and one reflow. `anchors: root, row1, row4, row5, row6, row7, row8, row9, row10, row11`. The alternatives are under "Alternatives set aside".
- M2: `build_parser()` calls each registrar where its block stood. `anchors: row3`.
- M3: the import block drops every name only the five blocks read and imports the five registrars. `anchors: row8, row12, row14`.
- M4: two test docstrings drop the claims the move makes false. `anchors: row12, row13, row15`.
- M5: the architecture-doc edits. `anchors: row20, row21`.
- M6: the fidelity proof: `capture --all-help` at the merge-base and on this tree, `compare-captures` with no `--moved-callable`, and `remainder` on each module and on `cli.py`. `anchors: row3, row17, row18, row19`. The detached merge-base worktree is heavier than a one-time proof needs. Lighter primitives checked:
  - Capturing in this worktree before the first edit works once, but no reviewer can re-run the before side after the edits land.
  - `git stash` is barred, because the stash stack is shared across worktrees.
  - `git show <base>:<path>` into scratch files serves `remainder`, but `capture` imports the whole package from one directory.
- M7: one `code-writer` dispatch, without a split. `anchors: row3, row23`. The reason is under Critical files.

## Critical files

**Dispatch: one `code-writer`, without a split** (M7). The doc and docstring edits name registrars the move creates, every file selects the same `claude/.claude/scripts/tests` run (row 23), and one self-review then sees the move and the prose describing it. The brief requires:
- Verification steps 1-12, run by the agent, with these outputs in the return:
  - the `<base>` SHA, and the `captured N run(s)` line from each side (47 expected) and the `compare-captures` `compared-files` count (235 expected), so a truncated capture shows;
  - `compare-captures`' output and exit code, and each `remainder` exit code;
  - ruff's output and `wc -l` per file;
  - the numstat total and the `git ls-files --others --exclude-standard` output;
  - the `select-tests.py` summary with any failing node IDs;
- no edit in the four target modules except the appended registrars;
- no staging and no commits;
- remove `<scratch>/base` before returning, whatever the outcome;
- when step 1 is denied, return the denial verbatim, make no fidelity claim, and never derive the before side from the edited tree.

The session's commit then meets G4's gate (row 22).

**Modify, production** (under `claude/.claude/scripts/transcript_analysis/`):
- `cli.py`: `build_parser()` and the import block.
- `cost.py`, `pr_cost.py`, `review_rounds.py` and `period_cost.py`, each gaining only its registrars.

**Modify, tests** (under `claude/.claude/scripts/tests/`), one docstring each:
- `test_transcript_analysis_cost_import_direction.py` (:36-38).
- `test_transcript_cli_bootstrap.py` (:239).

**Modify, docs:**
- `docs/transcript-analysis-architecture.md`.

**Also in the diff:** `.claude/plans/cli-remaining-subparsers-move.md`.

**Reuse:** `scope._add_project_scope_args`, `scope._add_extra_config_dirs_arg`, `scope._EXTRA_CONFIG_DIRS_HELP_PREAMBLE` and `scope._iso_date`; the registrar signature at `cost_ledger.py:709`; and `tests/_move_fidelity.py`'s `capture`, `compare-captures` and `remainder`. No new helper, script or test.

Projected diff: about 470 lines. About 224 leave `cli.py` (the five blocks with their ten blank lines, and 23 import lines) and about 10 enter it. About 208 are added across the four modules, about 18 change in the architecture doc, and about 5 change across the two test docstrings (row 22).

## Verification

Run from the worktree root. `<base>` is the SHA `git merge-base HEAD origin/main` prints, and `<scratch>` is a new directory outside the repository. `<mf>` stands for `scripts/venv-run.sh python3 claude/.claude/scripts/tests/_move_fidelity.py`, and `<pkg>` for `claude/.claude/scripts/transcript_analysis`.

1. Capture the before side from the merge-base:
   - `git worktree add --detach <scratch>/base <base>`
   - `mkdir -p <scratch>/account`
   - `<mf> capture --script <scratch>/base/claude/.claude/scripts/transcript-analysis.py --account <scratch>/account --out <scratch>/before --all-help`
2. Capture the after side: the same `capture` command with `--script claude/.claude/scripts/transcript-analysis.py --out <scratch>/after`.
3. `<mf> compare-captures <scratch>/before <scratch>/after` exits 0 and prints no finding. It takes no `--moved-callable` (row 17). A `capture-bytes` finding that shows a checkout path blocks: report it rather than add a `--mask`.
4. For each of the four modules, `<mf> remainder --before <scratch>/base/<pkg>/<module>.py --after <pkg>/<module>.py --added <registrars>` exits 0. `<registrars>` is `_add_cost_subparser _add_cost_trend_subparser` for `cost.py` and the one registrar for each other module. It passes no import flag, so any import change in a module is a finding (row 18).
5. `<mf> remainder --before <scratch>/base/<pkg>/cli.py --after <pkg>/cli.py --changed build_parser` exits 0, with these two flags:
   - `--import-removed transcript_analysis.cost:cmd_cost transcript_analysis.cost:cmd_cost_trend transcript_analysis.period_cost:cmd_period_cost transcript_analysis.pr_cost:_DEFAULT_PR_COST_PLAN_FILE_GLOB transcript_analysis.pr_cost:_PR_COST_ASOF_WINDOW_DAYS_DEFAULT transcript_analysis.pr_cost:cmd_pr_cost transcript_analysis.review_rounds:REVIEW_SKILLS transcript_analysis.review_rounds:cmd_review_round_cost transcript_analysis.scope:_EXTRA_CONFIG_DIRS_HELP_PREAMBLE transcript_analysis.scope:_add_extra_config_dirs_arg transcript_analysis.scope:_add_project_scope_args transcript_analysis.scope:_iso_date`
   - `--import-added transcript_analysis.cost:_add_cost_subparser transcript_analysis.cost:_add_cost_trend_subparser transcript_analysis.period_cost:_add_period_cost_subparser transcript_analysis.pr_cost:_add_pr_cost_subparser transcript_analysis.review_rounds:_add_review_round_cost_subparser`

   That pins `main()`, the module docstring and the import note unchanged, and the import set exactly.
6. `scripts/venv-run.sh ruff check claude/.claude/scripts/transcript_analysis/ claude/.claude/scripts/tests/test_transcript_analysis_cost_import_direction.py claude/.claude/scripts/tests/test_transcript_cli_bootstrap.py` exits 0. It covers `E501` on the reflowed and lengthened lines (row 11), import order, and unused or undefined names.
7. `cli.py`'s shape:
   - `git grep -n -e "add_parser(" -e "cmd_" -- <pkg>/cli.py` prints nothing.
   - `git grep -c -F "_subparser(sub)" -- <pkg>/cli.py` prints a count of 46.
8. In the architecture doc:
   - `git grep -n "reached bare" -- docs/transcript-analysis-architecture.md` prints one line, `cache_rebuild_rules.py`'s.
   - `git grep -n -e "except the five" -e "subcommands inline" -e "choices" -- docs/transcript-analysis-architecture.md` prints nothing.
   - `git grep -n -e _add_cost_subparser -e _add_cost_trend_subparser -e _add_pr_cost_subparser -e _add_review_round_cost_subparser -e _add_period_cost_subparser -- docs/transcript-analysis-architecture.md` prints lines only in the `cost.py`, `review_rounds.py`, `pr_cost.py` and `period_cost.py` sections.
9. `wc -l` over `cli.py` and the four modules reports about 205, 1,329, 1,258, 1,588 and 1,868 (Size).
10. `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py` passes, apart from failures that also occur at the merge-base (G2, row 23):
    - Record the failing node IDs from this tree and rerun those exact IDs from `<scratch>/base`, per `CLAUDE.md`'s "Prove your change caused a failing check". The two sets must be identical. An ID that fails only on this tree blocks.
    - Report pass counts, with zero failed and zero deselected, for each of these files:
      - `test_transcript_cli.py`
      - `test_transcript_cli_bootstrap.py`
      - `test_transcript_analysis_cost_import_direction.py`
      - `test_transcript_package_module_prefixes.py`
      - `test_transcript_analysis_architecture_doc.py`
      - `test_transcript_analysis_module_test_files.py`
      - `test_transcript_analysis_shim_reexports_only.py`
      - every `test_transcript_cost*.py`, `test_transcript_pr_cost*.py`, `test_transcript_review_rounds*.py` and `test_transcript_period_cost*.py` file
11. Scope:
    - `git diff <base> --name-only` prints exactly the eight modified files under Critical files. The plan is untracked until the session commits, so it does not appear.
    - `git ls-files --others --exclude-standard` prints only `.claude/plans/cli-remaining-subparsers-move.md`. Any other line is a stray file, so report it and do not stage it.
    - `git diff <base> --numstat -- . ':!.claude/plans'` totals near 470 changed lines (row 22).
12. `git worktree remove <scratch>/base`.

## Out of scope

- **Editing GH-1432 or `.claude/plans/cli-subparser-move.md`.** Both still say "Leave cost, cost-trend, pr-cost, review-round-cost and period-cost inline for now": the issue in its PR B section, and that plan in its Ask line (:7) and its first Out-of-scope row (:231). The plan is a merged record and the issue lies outside the diff (G5). The PR body links the issue and states that the five moved.
- **Splitting `cost.py`, `pr_cost.py`, `review_rounds.py` or `period_cost.py` under the 1,000-line limit.** Each is over it before this change and grows by 24 to 74 lines (Size). The offender assessment G1 names owns each file's verdict, and a later split carries each registrar with its handler.
- **The inline clause in `.claude/rules/transcript-analysis-tests.md:21-22`** (row 16). It matches no command after the move, but it states nothing false, and the file lies outside what the change needs. Dropping the clause is a one-line follow-up, or a row of this plan if the engineer asks for it.
- **Tightening `_is_guarded_name` to guard `cmd_` names.** After the move no `cmd_` name crosses into `cli.py`, so the exemption goes unused. Removing it changes what the test accepts, a decision separate from a move.
- **"And its subparser registration" in the four modules' docstrings, and the three-line docstrings some older registrars carry.** No sentence in the four modules' docstrings becomes false, since each lists what its module computes, and the older registrars sit in modules this change does not touch.
- **A durable test that `cli.py` holds no inline `add_parser` and no by-name `cmd_*` import.** After the move only a one-time grep (Verification step 7) checks the invariant, and `_is_guarded_name` still exempts `cmd_` names. A test needs a new file or case, which the Ask does not call for. GH-1432's final `cli.py` phase already lists a table-driven test that each subcommand dispatches to its owning module's `cmd_*`, which would own this check. Raised by the staff-sdet review (B8).
- **`test-ids` comparison of before and after test populations.** No test body changes, and step 10 already compares failing IDs against the merge-base. Raised by the staff-sdet review (B10).
- **GH-1432 item (d), the per-registrar flags, defaults and `func` sweep.** It is parked behind this move (Context) and is a change of its own.
- **A CHANGELOG entry.** No command's arguments, output or help change, which Verification step 3 proves.
