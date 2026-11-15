# Walk-test the appending `--config-dir` flag, then route its 15 registrations through one helper

## Context

Goal: replace the 15 hand-copied appending `--config-dir` argparse blocks in the `transcript_analysis` package with calls to one helper in `scope.py`, behind a walk test that pins the flag's registrants and its parse behaviour before any block moves.

Ask: "yes you should start, but probably in a new session because you're at 284k" — the engineer's reply, in the prior session, to the proposal to start follow-up item (b) of GH-1432 in a fresh worktree off current `main`. The order of the follow-ups was accepted earlier with "let's go with the architect's order". Item (b) there is the `--config-dir` walk as the first commit of #1138's dedupe. #1138 is the tracking issue and is still OPEN.

Why now: PR 1467, item (a), merged. #1138's body is stale. It was measured when the parser lived in `transcript-analysis.py`. That file is now a 14-line shim, and #1116 (the decomposition) is CLOSED. The count of 15 still holds, but the blocks now sit in 12 per-module registrars and 3 inline blocks in `transcript_analysis/cli.py`. #1138's reason for ordering this before #1116's last phase (the parser moves once) no longer applies.

Facts measured on `main` at `17fbfce4`, by `grep` and by walking `cli.build_parser()`:

- 15 subparsers register `--config-dir` with `action="append"`, `dest="extra_config_dirs"`, `metavar="DIR"` and default `None`. They are `subagents`, `subagent-mix`, `cost`, `context-distribution`, `edit-format`, `read-scope`, `instrument-authoring`, `context-composition`, `cache-efficiency`, `cost-trend`, `cache-rebuild`, `pr-cost`, `pr-cost-export`, `rearm-backtest` and `plan-boundary`.
- The parser has 46 subcommands. `_add_project_scope_args()` in `scope.py` has 40 call sites outside its definition. `pr-cost-export` never calls it, so the flag cannot be folded into that helper without changing which subparsers register it.
- Help text has two shapes. Thirteen blocks share a three-sentence preamble ("Additional Claude Code config directory to scan (repeatable). The default resolved config dir is always scanned first. Each supplied directory must contain a projects/ subdirectory, or it is rejected.") followed by a per-subcommand tail. Five distinct tails exist. `pr-cost` and `pr-cost-export` carry their own full text, opening with at most the preamble's first sentence.
- The top-level `--config-dir` at `cli.py:125` replaces the config dir (`metavar="PATH"`, no `dest`) and is a different flag. It stays as it is.
- `cli.main()` tests `hasattr(parsed, "extra_config_dirs")` at `cli.py:406`, so the set of subparsers that register the flag is behaviour, not just surface.

`tests/test_transcript_cli.py` already pins the complement: `_REFUSING_SUBCOMMANDS_WITHOUT_THEIR_OWN_CONFIG_DIR_FLAG` covers four refusing subcommands that lack the flag, and `TestBuildParser` walks every subparser for the scope flags. No test pins the set of registrants as a whole, the flag's `dest`, action and default, or that repeated `--config-dir` values accumulate. `test_transcript_cli_bootstrap.py` pins the `[--config-dir DIR]` usage text of two registrants and runs the flag through a subprocess for five.

Intended outcome: the 15 registrations go through one helper, the CLI surface and every help string are byte-identical to today, and the walk test, which lands first, passes before and after the move.

## Approach

The walk lands first and the dedupe second, in one PR. Commit 1 adds three tests to `tests/test_transcript_cli.py`. They pin which subparsers register the appending `--config-dir`, the flag's full argparse identity, and that repeated values accumulate, and they pass on today's code. Commit 2 adds `_add_extra_config_dirs_arg(parser, help_text)` and the shared help opening `_EXTRA_CONFIG_DIRS_HELP_PREAMBLE` to `scope.py`, and replaces each of the 15 blocks with one call in place. A scratch parser dump taken before and after proves that every parser's help text and actions are byte-identical.

A bare `:N` is a line at `<base>` (the SHA `git merge-base HEAD origin/main` prints) of the file its bullet names.

**Commit 1: the walk** (M1). In `claude/.claude/scripts/tests/test_transcript_cli.py`:
- Add `_subparsers_by_name(parser)`, which returns the subparsers action's `choices`. Route the three existing inline lookups through it, at `:159-166`, `:296-300` and `:355-358`, so the new tests do not add a fourth copy.
- Add `_SUBCOMMANDS_WITH_THEIR_OWN_CONFIG_DIR_FLAG` right after `_REFUSING_SUBCOMMANDS_WITHOUT_THEIR_OWN_CONFIG_DIR_FLAG` (`:154-156`). Derive it as `scope._SUBCOMMANDS_REFUSING_TOP_LEVEL_CONFIG_DIR` minus that tuple instead of listing 15 names. Each name then keeps one home, and test 1 below also pins that every registrant is a refusing subcommand (row 9).
- Add `_extra_config_dirs_actions(subparser)`, which iterates `subparser._actions` and returns the actions whose option strings hold `--config-dir` or whose dest is `extra_config_dirs`. Matching either catches another flag writing the dest, and `--config-dir` writing another dest. It iterates `_actions`, not `_option_string_actions.values()`, because an aliased flag would appear twice in the latter.
- In `TestConfigDirRouting`, after the complement test at `:173-176`, add:
  1. `test_subcommands_registering_their_own_config_dir_are_exactly_the_expected_set`. It walks every subparser of one `cli.build_parser()`. The names whose `_extra_config_dirs_actions` is non-empty must equal `_SUBCOMMANDS_WITH_THEIR_OWN_CONFIG_DIR_FLAG`.
  2. `test_own_config_dir_is_one_appending_flag`, parametrized over `sorted(_SUBCOMMANDS_WITH_THEIR_OWN_CONFIG_DIR_FLAG)`. `_extra_config_dirs_actions` returns exactly one action, with these attributes:
     - `type(action)`: `argparse._AppendAction`
     - `option_strings`: `["--config-dir"]`
     - `dest`: `"extra_config_dirs"`
     - `metavar`: `"DIR"`
     - `required`: `False`
     - `default`, `nargs`, `const`, `type` and `choices`: each `None`
  3. `test_repeated_own_config_dir_values_accumulate_in_order`, parametrized the same way. On one parser, `parse_args([name]).extra_config_dirs` is `None`, and `parse_args([name, "--config-dir", "a", "--config-dir", "b"]).extra_config_dirs` equals `["a", "b"]`.
- No test catches `SystemExit`, so a parse that exits fails its case instead of passing it. None of the 15 needs any other argument to parse (row 10).
- Test 1's one-line docstring states that `cli.main()`'s refusal hint and `_resolve_cost_roots` both key on this dest. Tests 2 and 3 need nothing beyond their names.

**Commit 2: the helper** (M2, M3). In `claude/.claude/scripts/transcript_analysis/scope.py`, between `_SUBCOMMANDS_REFUSING_TOP_LEVEL_CONFIG_DIR` (`:702-708`) and `_resolve_cost_roots` (`:711`), add:
- `_EXTRA_CONFIG_DIRS_HELP_PREAMBLE`, the three preamble sentences copied byte for byte from any of the 13 blocks that carry them. It sits beside `_resolve_cost_roots` because it documents that function's checks at `:765-781`. The text "subdirectory, or it is rejected." stays on one source line, because Verification step 5 counts that phrase by line.
- `_add_extra_config_dirs_arg(parser: argparse.ArgumentParser, help_text: str) -> None`. Its body is one `parser.add_argument("--config-dir", action="append", dest="extra_config_dirs", metavar="DIR", help=help_text)`. Its one-line docstring states the fact a future caller needs: a subcommand that calls it must also be listed in `_SUBCOMMANDS_REFUSING_TOP_LEVEL_CONFIG_DIR`, because `cli.main()` and `_resolve_cost_roots` key on its dest.

Each block becomes one call at the same position, so the option order in every `format_help()` stays put:
- The 13 preamble blocks pass `f"{<preamble constant>} <tail>"`, with the tail copied byte for byte from the block it replaces (row 6).
- `pr-cost` (`cli.py:249-256`) and `pr-cost-export` (`pr_cost_export.py:413-416`) pass their own full text unchanged.
- The registrar modules reach both new names as `scope.<name>`, the way the 11 that call `scope._add_project_scope_args` do on the line before their block. `pr_cost_export.py` never calls that helper but imports `scope`, so `scope.<name>` resolves there too. `cli.py` adds both names to its `from transcript_analysis.scope import (...)` block (`:85-89`), the way it imports `_add_project_scope_args` (row 14).

`docs/transcript-analysis-architecture.md:57-59` names `_add_project_scope_args` as `scope.py`'s subparser registrar. Add one sentence there that names `_add_extra_config_dirs_arg` as the registrar of the repeatable subcommand-level `--config-dir`, whose `extra_config_dirs` values `_resolve_cost_roots` reads (M4, row 16).

**Byte-identity proof** (M5). The scratch script in Verification dumps the top-level parser and every subparser. For each it prints `format_help()` and, per action, the class, option strings, dest, metavar, default, nargs, const, type, choices, required and `repr(help)`. It also prints each `set_defaults` entry, with callables as `module:qualname`, and each mutually exclusive group's option strings, so a clipped `set_defaults(func=...)` next to a replaced block shows in the diff. The session dumps before dispatch B, the dispatch dumps after its edits, and the two outputs must match exactly. The dump prints `repr(help)` because `format_help()` rewraps help text (row 13).

**Alternatives set aside:**
- A `tail` parameter, with the helper prepending the preamble. `pr-cost` and `pr-cost-export` carry no preamble, so they would need a second, mutually exclusive parameter or would stay outside the helper. The goal is all 15 through one signature.
- Adding the preamble to `pr-cost` and `pr-cost-export`, so that a `tail` parameter fits all 15. Its facts hold for both (row 7), but it changes two help strings, which this refactor keeps byte-identical (Out of scope).
- A default `help_text` equal to the tail eight blocks share. That tail says `--no-redact` is refused, so a future caller with no `--no-redact` flag would inherit a false sentence silently.
- Folding the flag into `_add_project_scope_args()`. It would register the flag on every other subparser that calls that helper, which flips `cli.main()`'s `hasattr` branch for each of them. It also cannot reach `pr-cost-export`, which never calls that helper (row 11).
- A new module for the helper. `tests/test_transcript_analysis_module_test_files.py` would then require a test file or an exemption for it. `scope.py` already owns `_resolve_cost_roots`, the dest's reader, and all 12 registrar modules already import it.
- Pinning each subcommand's help text in the walk. That copies 15 production strings into the test, so every later help fix edits two files. Byte-identity is a property of this diff alone, and the dump proves it once.
- Named constants for the repeated tails (Out of scope).
- One commit. The walk would never be shown passing against the blocks before they move.
- One `code-writer` dispatch for both commits. The session could not show the test passing on the old blocks, or commit it alone, with the production edits already in the tree.
- Splitting commit 2 across dispatches. Every one of its files consumes the helper commit 2 creates, so every prompt would restate the same helper contract. Commit 2 totals about 225 changed lines, which fits one context.

**Assumption ledger.**

**Root:** Fifteen `transcript_analysis` subparsers each hand-copy one appending `--config-dir` registration (`action="append"`, `dest="extra_config_dirs"`, `metavar="DIR"`). Thirteen of them also copy a three-sentence help preamble that documents `_resolve_cost_roots`'s checks. `cli.main()` and `_resolve_cost_roots` key on that dest. No test pins which subparsers register it, its action, or how repeated values accumulate. This change pins those first, then routes all 15 through one `scope.py` helper, leaving every parser's help and actions byte-identical. `[verified: rows 5-9]`

**Givens:**
- G1. A test of `build_parser()` across commands goes in `tests/test_transcript_cli.py`. Reason: `.claude/rules/transcript-analysis-tests.md` owns test placement. `[verified: read]`
- G2. ruff runs with `line-length = 130` and the `E`, `F`, `B`, `I`, `UP` and `SIM` rules. Reason: `pyproject.toml` owns it. `[verified: pyproject.toml:2, :6]`
- G3. Agents run `select-tests.py`, not the full suite. Reason: the repo `CLAUDE.md` "Commands" section owns the rule. `[verified: CLAUDE.md]`
- G4. `/code-review` runs before every commit, and the code-review gate refuses a commit that holds only a plan file. Reason: the global `CLAUDE.md` "Code Review" section and the gate own both. `[verified: CLAUDE.md "Code Review"; unverified: the plan-only refusal, relayed by the dispatching session]`
- G5. `require-diff-size-prompt.sh` stops a commit that brings the branch's cumulative diff, minus `.claude/plans/`, to 400 or more changed lines. Reason: the hook owns the threshold. `[verified: require-diff-size-prompt.sh:4-5, :13-14]`
- G6. Code files are capped at 1,000 lines, and no check enforces the cap yet. Reason: `docs/design-decisions/code-file-line-limit.md` owns the cap and defers its check to an offender assessment. `[verified: read of the doc; a Glob for test_*line_limit*.py and a Grep for the doc's slug in *.py found no check]`
- G7. `claude/.claude/scripts/` installs to every stow consumer. Reason: the repo `CLAUDE.md` section "Plans in this repo affect all stow users" owns it. `[verified: CLAUDE.md]`

**Rows:**
1. The engineer's go-ahead to start item (b). `[engineer-verified: "yes you should start, but probably in a new session because you're at 284k"]`
2. The engineer accepted the follow-up order. `[engineer-verified: "let's go with the architect's order"]`
3. The engineer's go-ahead for item (b) covers the walk test and the dedupe of the 15 blocks through a helper, as two commits in one PR. `[engineer-verified: "Walk + dedupe, one PR (Recommended)"]`, selected in answer to whether the go-ahead covers both.
4. Byte-identical help strings and not folding into `_add_project_scope_args` were the dispatching session's proposals. `[unverified: relayed as its own proposals]` This plan keeps all three on the evidence in rows 6, 7 and 11 and under "Alternatives set aside".
5. Fifteen blocks register `"--config-dir", action="append", dest="extra_config_dirs", metavar="DIR"` with no `default=`, at the ranges under Critical files. `[verified: Grep for "--config-dir", action="append" over the package, 15 hits, read with context]`
6. Help text has these shapes. `[verified: read of all 15 blocks]`
   - Thirteen blocks open with the three sentences "Additional Claude Code config directory to scan (repeatable). The default resolved config dir is always scanned first. Each supplied directory must contain a projects/ subdirectory, or it is rejected." They are the eleven module blocks other than `pr_cost_export.py`, plus `cost` and `cost-trend`.
   - Their five distinct tails, one per item:
     - Tail 1, at `cost`, `context-distribution`, `context-composition`, `edit-format`, `cache-efficiency`, `cache-rebuild`, `plan-boundary` and `rearm-backtest`: "Composes with --this-repo, scoping to this repo across every resulting root; --no-redact is refused once this puts more than one root in scope."
     - Tail 2, at `cost-trend` and `instrument-authoring`: tail 1's first clause alone, ending in a period.
     - Tail 3, at `subagent-mix`: "Refused together with --per-session. Branch names are redacted and _DO_NOT_PUBLISH_BANNER is printed whenever more than one root is in scope."
     - Tail 4, at `subagents`: tail 3's second sentence alone.
     - Tail 5, at `read-scope`: "Refused together with --this-repo or --no-redact."
   - `pr-cost` and `pr-cost-export` open with the preamble's first sentence only, then diverge.
7. All 15 registrants call `scope._resolve_cost_roots`. It validates each extra at `scope.py:771-781` and refuses `--no-redact` over more than one root at `:788-794`. `[verified: Grep for _resolve_cost_roots(, 15 call sites in 14 modules, two of them in cost.py]`
8. The package reads the dest at `cli.py:406` (`hasattr`) and at `scope.py:761` and `cost.py:557` (`getattr`), and nowhere else. `[verified: Grep for extra_config_dirs over the package]`
9. The 15 registrants are exactly `scope._SUBCOMMANDS_REFUSING_TOP_LEVEL_CONFIG_DIR` (19 names, `:702-708`) minus `_REFUSING_SUBCOMMANDS_WITHOUT_THEIR_OWN_CONFIG_DIR_FLAG` (4 names, `test_transcript_cli.py:154-156`). `[verified: read; the difference matches the Context's list]`
10. None of the 15 registers a required option or a positional, so both parses in test 3 need no other argument. The only `required=True` options and positionals in the package belong to `friction-count`, `routing-guard`, `fail-seq`, `pr-link`, `commit-gate` and `skill-pair`. `[verified: single-line and multiline Grep for required= and for positional add_argument calls; unverified at runtime until Verification step 1]`
11. `pr-cost-export` registers neither `--projects` nor `--this-repo` (`_SUBCOMMANDS_LACKING_A_SCOPE_FLAG`, `test_transcript_cli.py:278-284`), so it never calls `_add_project_scope_args`. A Grep finds 40 occurrences of `_add_project_scope_args(` outside its own def. `[verified: read; Grep]`
12. No test asserts any `--config-dir` help string. `[verified: Grep for "Additional Claude Code config" and "scanned first" outside .claude/plans/]`
   - Besides the 15 blocks, the hits are two standalone scripts, prose in `docs/transcript-analysis.md`, a `scope.py` docstring, and docstrings or comments in five test files.
   - Two tests read full `pr-cost --help` output, in `test_transcript_ledger_common.py` and `test_transcript_cli_bootstrap.py`, and neither asserts a `--config-dir` string.
   - `test_transcript_cli_bootstrap.py` asserts `[--config-dir DIR]` in the `edit-format` and `instrument-authoring` usage lines. The metavar and option string stay unchanged.
13. `format_help()` collapses runs of whitespace, so a doubled space shows only in `repr(help)`. `[verified: a staff-sdet reviewer ran the dump against a doubled-space mutation; a dropped space and a reordered option show in both columns]`
14. Every registrar module except `pr_cost_export.py` calls scope helpers as `scope._<name>`, and all 12 import `scope`. `cli.py` imports scope names by name (`:85-89`). `[verified: read of each block's preceding line and of cli.py]`
15. `scope.py` is 1,149 lines, `cli.py` 427, and `test_transcript_cli.py` 372. Commit 2 adds about 14 lines to `scope.py`. Across the 13 other code files it deletes about 137 lines and adds about 71. `[verified: line counts; unverified: the deltas, a per-block estimate]`
16. `docs/transcript-analysis-architecture.md:57-59` lists `_add_project_scope_args` as `scope.py`'s subparser registrar. `[verified: read]`
17. `post-crash-sessions.py:1933` and `mark-terminal.py:315` register the same flag shape, each with its own help text and resolver, and neither imports `transcript_analysis`. `[verified: Grep]`
18. The dump puts the checkout it is given first on `sys.path` and prints the loaded `cli.py` path to stderr, so a run against the wrong checkout shows. `[unverified until Verification step 3]`
19. The diff lands near 280 changed lines outside `.claude/plans/`, under G5's threshold. `[unverified: a per-site estimate; Verification step 8 measures it]`
20. No stow consumer sees a CLI change, because every parser's help and actions stay byte-identical. `[unverified until Verification step 4]`
21. The Ask line's two quotes, and ledger rows 1 and 2, are the engineer's own words. `[engineer-verified: "Yes, my words"]`, selected in answer to whether those quotes were theirs.
22. The one-sentence edit to `docs/transcript-analysis-architecture.md` stays in the diff. `[engineer-verified: "Include it (Recommended)"]`, selected in answer to whether to include a file the Ask does not name.

**Mechanisms:**
- M1: the three walk tests, `_subparsers_by_name`, `_extra_config_dirs_actions` and the derived set, in commit 1. `anchors: root, row8, row9, row10`.
- M2: `_EXTRA_CONFIG_DIRS_HELP_PREAMBLE` and `_add_extra_config_dirs_arg` in `scope.py`. `anchors: row5, row6, row7, row14, row15`.
- M3: the in-place replacement at all 15 sites, with `pr-cost` and `pr-cost-export` passing their own text. `anchors: row5, row6, row11`.
- M4: the architecture doc sentence. `anchors: row16`.
- M5: the scratch parser dump, run before and after. `anchors: row12, row13, row18, row20`. It is heavier than a test run. Lighter primitives checked:
  - `transcript-analysis.py <name> --help` per subcommand. It needs a 46-iteration shell loop, rewraps help text (row 13), and shows no dest, default or action class.
  - `tests/_move_fidelity.py capture --all-help` plus `compare-captures` (documented in `docs/transcript-analysis-architecture.md`). It pins `COLUMNS` and the hash seed and already records every action, mutex group and `set_defaults` entry. It records neither `help` nor `metavar` as raw values, and `--help` collapses whitespace, so a doubled or dropped joining space in a tail is invisible to it. The scratch dump adds only that `repr(help)` column, and borrows the tool's pinned environment and its `set_defaults` and mutex coverage.
  - The walk tests alone. They pin the flag's identity but, by design, no help string.
  - Reading the diff. Thirteen tails move by hand, and a dropped joining space does not show in review.
- M6: two sequenced `code-writer` dispatches, one per commit. `anchors: row4, row19`. The reasons are under "Alternatives set aside" and Critical files.

## Critical files

**Dispatch split** (M6). Two `code-writer` dispatches run in sequence, each with `model: sonnet` and without `isolation: "worktree"`. Neither commits or runs `select-tests.py`. Each reads this plan by path, so neither prompt restates it. The order is:
1. Dispatch A makes commit 1's test edits and runs Verification steps 1-2. The session reruns step 1, runs `/code-review`, and commits the test file with this plan (G4).
2. The session creates `<scratch>`, writes the dump script into it, and runs Verification step 3.
3. Dispatch B, whose prompt carries `<scratch>`'s path, makes commit 2's edits and runs Verification steps 4-8. The session runs `/code-review` and commits. `/ready-for-review` then runs step 9.

Commit 2 is not split further; see "Alternatives set aside".

**Dispatch A: modify**
- `claude/.claude/scripts/tests/test_transcript_cli.py` (`:154-166`, `:173-176`, `:296-300`, `:355-358`, plus the new tests).

**Dispatch B: modify.** Under `claude/.claude/scripts/transcript_analysis/`, each range is one block that becomes one helper call:
- `scope.py`: the new constant and helper between `:708` and `:711`.
- `cli.py`: the import block `:85-89`; `cost` `:158-167`; `cost-trend` `:225-233`; `pr-cost` `:249-256`, which keeps its own text.
- `cache_efficiency.py` `:275-284`.
- `cache_rebuild.py` `:889-898`.
- `context_composition.py` `:474-483`.
- `context_distribution.py` `:227-236`.
- `edit_format.py` `:436-445`.
- `instrument_authoring.py` `:411-419`.
- `plan_boundary.py` `:346-355`.
- `pr_cost_export.py` `:413-416`, which keeps its own text.
- `read_scope.py` `:606-613`.
- `rearm_backtest.py` `:572-581`.
- `subagent_mix.py` `:695-704`.
- `subagents.py` `:244-252`.

Outside that directory, Dispatch B also modifies `docs/transcript-analysis-architecture.md` (`:57-59`).

**Also in the diff:** `.claude/plans/config-dir-flag-walk-and-dedupe.md`, in commit 1.

**Reuse:**
- `scope._SUBCOMMANDS_REFUSING_TOP_LEVEL_CONFIG_DIR` and `_REFUSING_SUBCOMMANDS_WITHOUT_THEIR_OWN_CONFIG_DIR_FLAG`, to derive the registrant set.
- `_add_project_scope_args` (`scope.py:509-521`), as the shape of a shared subparser registrar.
- `test_every_subcommand_with_both_scope_flags_refuses_them_together` (`test_transcript_cli.py:309-326`), as the walk style.

**PR body requirement.** The PR body contains:
- the two-commit structure, and that commit 1 passes on the unmoved blocks;
- Verification steps 3-5 with their outputs: the 15 count, the empty dump diff, and the single-source greps;
- that the CLI's help and parse behaviour do not change for any stow consumer;
- links to #1138 and to GH-1432's item (b).

Projected diff: about 280 lines. About 60 change in commit 1's test file. About 225 change in commit 2: about 186 across the 13 preamble sites, about 20 at `pr-cost` and `pr-cost-export`, about 14 in `scope.py`, and about 5 in `cli.py`'s imports and the doc (row 19).

## Verification

Run from the worktree root. `<base>` is the SHA `git merge-base HEAD origin/main` prints. `<scratch>` is the session's `mktemp -d` directory outside the repository. Every dump command below runs under `env COLUMNS=100 NO_COLOR=1`, because `format_help()` wraps at the terminal width and colours by environment, so two shells could otherwise differ with no code cause. Write this script to `<scratch>/parser_dump.py` verbatim:

```python
# parser_dump.py <scripts-dir>: every transcript-analysis parser's help text and actions, for a before/after diff.
import argparse
import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(sys.argv[1]).resolve()))
cli = importlib.import_module("transcript_analysis.cli")
print(f"loaded {cli.__file__}", file=sys.stderr)
parser = cli.build_parser()
subparsers = next(action for action in parser._actions if isinstance(action, argparse._SubParsersAction))
for name, each_parser in [("<top level>", parser), *sorted(subparsers.choices.items())]:
    print(f"=== {name}")
    print(each_parser.format_help())
    for action in each_parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            continue
        action_type = getattr(action.type, "__qualname__", action.type)
        print(
            type(action).__name__, action.option_strings, action.dest, action.metavar, repr(action.default),
            action.nargs, action.const, action_type, action.choices, action.required, repr(action.help),
        )
    for key, value in sorted(each_parser._defaults.items()):
        shown = f"{value.__module__}:{value.__qualname__}" if callable(value) else repr(value)
        print("default", key, shown)
    for group in each_parser._mutually_exclusive_groups:
        print("mutex", sorted(option for action in group._group_actions for option in action.option_strings))
```

1. Walk on unmoved code (Dispatch A, then the session before commit 1):
   - `git diff --quiet <base> -- claude/.claude/scripts/transcript_analysis` exits 0, so production code is untouched.
   - `scripts/venv-run.sh pytest claude/.claude/scripts/tests/test_transcript_cli.py -q` exits 0.
   - `scripts/venv-run.sh pytest -n0 --collect-only -q "claude/.claude/scripts/tests/test_transcript_cli.py::TestConfigDirRouting::test_own_config_dir_is_one_appending_flag" "claude/.claude/scripts/tests/test_transcript_cli.py::TestConfigDirRouting::test_repeated_own_config_dir_values_accumulate_in_order"` lists 30 node IDs, 15 per test.
   - Negative control, run once after the green run. Temporarily delete the whole `p_cost_trend.add_argument("--config-dir", ...)` call in `transcript_analysis/cli.py`. Rerun the file. The failing node IDs must be exactly test 1 and the `cost-trend` cases of tests 2 and 3, and no other case. Revert the edit, then `git diff --quiet <base> -- claude/.claude/scripts/transcript_analysis` exits 0 again. The failing IDs are the control's pass condition, so a walk that stays green here is vacuous.
2. `scripts/venv-run.sh ruff check claude/.claude/scripts/tests/test_transcript_cli.py` exits 0.
3. Baseline dump (the session, after commit 1 and before Dispatch B):
   - `git diff --quiet <base> -- claude/.claude/scripts/transcript_analysis` exits 0.
   - `env COLUMNS=100 NO_COLOR=1 scripts/venv-run.sh python3 <scratch>/parser_dump.py claude/.claude/scripts > <scratch>/before.txt` exits 0, and its stderr line names this worktree's `transcript_analysis/cli.py`.
   - `grep -cF "_AppendAction ['--config-dir'] extra_config_dirs DIR None" <scratch>/before.txt` prints `15`.
   - If production files changed before this step ran, take the baseline from the base instead: `git worktree add --detach <scratch>/base <base>`, dump `<scratch>/base/claude/.claude/scripts`, then `git worktree remove <scratch>/base`.
4. After Dispatch B's edits:
   - `env COLUMNS=100 NO_COLOR=1 scripts/venv-run.sh python3 <scratch>/parser_dump.py claude/.claude/scripts > <scratch>/after.txt` exits 0, and its stderr line names this worktree.
   - `diff <scratch>/before.txt <scratch>/after.txt` prints nothing and exits 0. A difference blocks, and the fix goes in the call site, never in the baseline.
5. Single source, over the working tree:
   - `git grep -n -F -e '"--config-dir"' -- claude/.claude/scripts/transcript_analysis/` prints two lines: the top-level flag in `cli.py` and the helper in `scope.py`.
   - `git grep -c -F -e 'or it is rejected' -- claude/.claude/scripts/transcript_analysis/` prints only `claude/.claude/scripts/transcript_analysis/scope.py:1`.
   - `git grep -c -F -e 'dest="extra_config_dirs"' -- claude/.claude/scripts/transcript_analysis/` prints only `claude/.claude/scripts/transcript_analysis/scope.py:1`.
6. `scripts/venv-run.sh ruff check claude/.claude/scripts/transcript_analysis/ claude/.claude/scripts/tests/test_transcript_cli.py` exits 0.
7. Targeted tests: `scripts/venv-run.sh pytest -q` over these files under `claude/.claude/scripts/tests/` exits 0: `test_transcript_cli.py`, `test_transcript_cli_bootstrap.py`, `test_transcript_analysis_multi_root_scope.py`, `test_transcript_scope.py`, `test_transcript_analysis_architecture_doc.py`, `test_transcript_cache_efficiency.py`, `test_transcript_cache_rebuild.py`, `test_transcript_context_composition.py`, `test_transcript_context_distribution.py`, `test_transcript_cost.py`, `test_transcript_edit_format.py`, `test_transcript_instrument_authoring.py`, `test_transcript_plan_boundary.py`, `test_transcript_pr_cost.py`, `test_transcript_pr_cost_export.py`, `test_transcript_read_scope.py`, `test_transcript_rearm_backtest.py`, `test_transcript_subagent_mix.py` and `test_transcript_subagents.py`. Before claiming a failing node ID as in scope, rerun it at the merge-base and compare node IDs, per `CLAUDE.md`.
8. Scope:
   - `git diff <base> --name-only` prints exactly the 16 files under Critical files plus this plan.
   - `git diff <base> --numstat -- . ':!.claude/plans'` totals near 280 changed lines (row 19).
9. `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py` exits 0, run inside `/ready-for-review` (G3). Because `scope.py` changes, it also selects the review-bench tests under `evals/` through `_REVIEW_BENCH_SCRIPTS_DEPENDENCIES` (`select-tests.py:108-119`).

## Out of scope

- **The repeated tails.** "Composes with --this-repo, scoping to this repo across every resulting root; --no-redact is refused once this puts more than one root in scope." stays a literal at eight call sites, and its first clause stays a literal at two more (row 6). A constant would either split the sentence mid-clause or need a help rewrite. Either is a separate change from routing the flag through one helper.
- **The preamble on `pr-cost` and `pr-cost-export`.** Its facts hold for both (row 7), so adding it would improve their help. It changes two help strings, which this refactor keeps byte-identical.
- **`post-crash-sessions.py:1933` and `mark-terminal.py:315`.** They register the same flag shape, but each is a standalone tool with its own help text and resolver that imports nothing from the package (row 17). Routing them through `scope.py` would tie each tool to the package for one `add_argument` call.
- **The top-level `--config-dir` at `cli.py:124-131`.** It replaces the config dir instead of adding to it, and it stays as it is.
- **A permanent guard against a future hand-copied registrant.** After this change, a 16th hand-copied `--config-dir` block that is also added to `scope._SUBCOMMANDS_REFUSING_TOP_LEVEL_CONFIG_DIR` passes every walk test, because the tests pin parser identity and not the call path. Verification step 5 checks the single-source state once. A standing AST check needs its own seeded-violation control and is a separate change.
- **Splitting `scope.py` under the 1,000-line cap** (G6). It is 1,149 lines before this change and grows by about 14 (row 15).
- **Rewriting #1138's stale body.** The PR body links the issue and describes the current layout.
