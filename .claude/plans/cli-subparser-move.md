# transcript-analysis: move inline subparsers out of cli.py (GH-1432 PR B)

## Context

Goal: land PR B of GH-1432 by moving the 18 movable inline subparser blocks out of `transcript_analysis/cli.py`'s `build_parser()` into per-command `_add_<command>_subparser(subparsers)` registrars in their owning modules, so `cli.py` drops under the 1,000-line limit in `docs/design-decisions/code-file-line-limit.md` with no change to any command's parsed arguments or help output.

Ask: GH-1432, section "PR B: move the inline subparsers out of `cli.py`" (`gh issue view 1432`): "Move each into an `_add_<command>_subparser(sub)` in its command module, following the 22 registrars that already exist. Leave cost, cost-trend, pr-cost, review-round-cost and period-cost inline for now." Rides: the `cli.py` prose fixes, the `docs/transcript-analysis-architecture.md` updates for `cli.py` and each moved module, and wiring tests for moved commands going to their module's test file.

Why now: PR A (PR 1449) merged on 2026-11-11, and the issue orders PR B after it because PR B rewrites `cli.py`'s import block.

Findings from reading `main` (b1954cd0), gathered by an exploration subagent and carried as evidence, not verified line by line:
- `cli.py` is 1,029 lines. `build_parser()` starts at line 200. 23 subcommands register inline; five stay (`cost`, `cost-trend`, `pr-cost`, `review-round-cost`, `period-cost`), so 18 move.
- The 18 and their owning modules: `subagents` (subagents), `subagent-mix` and `cost-counts` (subagent_mix), `reviewer-yield` (reviewer_yield), `review-trace` (review_trace), `audit-routing`, `audit-routing-shape` and `audit-routing-samples` (audit_routing), `read-scope` (read_scope), `cache-rebuild` (cache_rebuild), `cost-ledger` (cost_ledger), `pr-cost-export` (pr_cost_export), `spend-over-threshold` (spend_over_threshold), `workstream-cost` (workstream_cost), `author-outcome` (author_outcome), `rearm-backtest` (rearm_backtest), `handoff-signal-response` (handoff_signal_response), `plan-boundary` (plan_boundary).
- Registration order interleaves inline blocks with existing `_add_*` calls (around lines 218-222, 327-330, 369, 459-461, 496-502, 757, 980-987) and determines help and parser output, so it must be preserved.
- Existing registrars take `subparsers: argparse._SubParsersAction`, call `scope._add_project_scope_args` and `scope._iso_date` as `scope.`-qualified names, and bind `cmd_<x>` through `set_defaults(func=...)`. The inline bodies call those two helpers bare. `cli.py` is the only importer of them bare, and the five staying blocks still use them.
- `author_outcome.py` is 968 lines, so a ~20-line registrar lands near the limit. `review_trace.py` (901) and `cache_rebuild.py` (876) also grow.
- `tests/_move_fidelity.py capture --all-help` then `compare-captures --moved-callable` is the issue's proof tool; its `remainder` check drops top-level imports, so the import-block rewrite needs hand review.
- `test_review_trace_parses_omit_plan_review_spawns` is at `tests/test_transcript_cli.py:335` and belongs in `tests/test_transcript_review_trace.py`. Module-to-test-file mapping is by file name (`test_transcript_<module>*.py`) and needs no new entry for any target module.
- `cli.py` carries 31 "Read bare by this file's own build_parser" comments and three "Both names below" comments (lines 25, 133, 174).

Defaults taken without asking (the engineer can overrule):
- The four stale plan citations PR A left out (`test_transcript_reviewer_yield.py`, `test_transcript_scope.py`, `test_transcript_cost.py`, `test_transcript_cache_rebuild_ttl_rules.py`) stay out of scope: PR B touches none of those files.
- The deferred in-process `cmd_sessions --paths` test stays out of scope: `sessions` is already a registrar command, so it is not a moved command's wiring test.
- The 8 pre-existing environmental test failures on this machine (`timeout` missing from PATH) are reported in verification, not fixed.

## Approach

Move each of the 18 inline blocks out of `build_parser()` verbatim into an `_add_<command>_subparser(subparsers)` appended to its owning module, and call that registrar from `build_parser()` where the block stood. `cli.py` falls to about 440 lines, and every command's parser configuration and `--help` bytes stay identical, which `tests/_move_fidelity.py compare-captures` proves against a capture taken at the merge-base.

**The move, per command.** Line ranges are `cli.py`'s; modules are under `claude/.claude/scripts/transcript_analysis/`:
- `subagents` (:224-246) → `subagents.py`, `_add_subagents_subparser`.
- `subagent-mix` (:248-296) → `subagent_mix.py`, `_add_subagent_mix_subparser`.
- `reviewer-yield` (:298-325) → `reviewer_yield.py`, `_add_reviewer_yield_subparser`.
- `review-trace` (:332-367) → `review_trace.py`, `_add_review_trace_subparser`.
- `audit-routing` (:371-395) → `audit_routing.py`, `_add_audit_routing_subparser`.
- `read-scope` (:463-494) → `read_scope.py`, `_add_read_scope_subparser`.
- `cache-rebuild` (:520-569) → `cache_rebuild.py`, `_add_cache_rebuild_subparser`.
- `cost-ledger` (:571-594) → `cost_ledger.py`, `_add_cost_ledger_subparser`.
- `pr-cost-export` (:668-688) → `pr_cost_export.py`, `_add_pr_cost_export_subparser`.
- `spend-over-threshold` (:690-701) → `spend_over_threshold.py`, `_add_spend_over_threshold_subparser`.
- `workstream-cost` (:703-719) → `workstream_cost.py`, `_add_workstream_cost_subparser`.
- `author-outcome` (:759-777) → `author_outcome.py`, `_add_author_outcome_subparser`.
- `cost-counts` (:779-792) → `subagent_mix.py`, `_add_cost_counts_subparser`.
- `rearm-backtest` (:814-857) → `rearm_backtest.py`, `_add_rearm_backtest_subparser`.
- `handoff-signal-response` (:859-903) → `handoff_signal_response.py`, `_add_handoff_signal_response_subparser`.
- `plan-boundary` (:905-938) → `plan_boundary.py`, `_add_plan_boundary_subparser`.
- `audit-routing-shape` (:940-952) → `audit_routing.py`, `_add_audit_routing_shape_subparser`.
- `audit-routing-samples` (:954-978) → `audit_routing.py`, `_add_audit_routing_samples_subparser`.

**Registrar shape** (rows 3-6, 29):
- Signature: `def _add_<command>_subparser(subparsers: argparse._SubParsersAction) -> None:`, the shape all 23 existing registrars use.
- Body: the block's lines verbatim at their current four-space indentation, with exactly three substitutions and no other edit:
  - `sub.add_parser(` becomes `subparsers.add_parser(`.
  - `_add_project_scope_args(` becomes `scope._add_project_scope_args(`.
  - `_iso_date` becomes `scope._iso_date`.
- Local names (`p_sub`, `p_mix`, ...), help strings, and the module-local reads (`cmd_*`, `REVIEW_TRACE_SKILLS`, `_CACHE_REBUILD_DEFAULT_SINCE`, `_CACHE_REBUILD_DEFAULT_THRESHOLD`, `_AUTHORING_AGENT_CODE_WRITER`) stay as written.
- No docstring. The three-line docstring 21 of the 23 registrars carry restates the function's name and its `set_defaults` line, which CLAUDE.md §Durable text bars. `read_cost.py:616` and `routing_guard.py:2084` already omit it.
- Placement: after the module's last top-level statement, with two blank lines before each `def`. `audit_routing.py` takes audit-routing, audit-routing-shape, then audit-routing-samples. `subagent_mix.py` takes subagent-mix, then cost-counts.
- No module gains an import (row 4), and no module docstring changes.

A table-driven alternative, one generic helper fed per-command argument specs, was set aside: it is heavier than a move, it rewrites every block's shape so that nothing moves verbatim, and the 23 hand-written registrars would stay beside it.

**`build_parser()`** (rows 1-2): each block becomes one `_add_<command>_subparser(sub)` call at the same position, so registration order is unchanged. Consecutive registrar calls run without blank lines between them. One blank line separates each run from each of the five inline blocks that stay (cost, cost-trend, pr-cost, review-round-cost, period-cost).

**`cli.py` import block** (rows 6, 18-23):
- Drop every name only the 18 blocks read: their `cmd_*` handlers, `REVIEW_TRACE_SKILLS`, `_CACHE_REBUILD_DEFAULT_SINCE`, `_CACHE_REBUILD_DEFAULT_THRESHOLD`, and `_AUTHORING_AGENT_CODE_WRITER`.
- Add one `from transcript_analysis.<module> import ...` per target module, naming its registrars, on one line where it fits in 130 columns. `audit_routing`'s three names take the parenthesized form. ruff's `I` rules (`pyproject.toml:6`) govern order.
- Keep the names the `cost`, `period_cost`, `pr_cost`, `review_rounds` and `scope` imports carry. The five staying blocks and `main()` read them (row 6).
- Replace every per-import comment with one note, including the comments on imports this change otherwise leaves alone. The note is the single line `# Only this file's build_parser() and main() read the names imported below.`, directly above `from transcript_analysis import scope`, which ruff's `I` rules sort first in the first-party group. A statement that loses its comment keeps its parenthesized form, whose trailing comma ruff accepts.

The issue asks for exactly this replacement (row 23). Each per-import comment names the `build_parser()` line that reads its names, which `build_parser()` itself shows (row 20). The note states the one fact no line of `cli.py` shows: nothing outside `cli.py` reads these names through it, because tests and shim copies read only `build_parser` and `main` from it (row 18). Set aside:
- Giving each new import the "Read bare by this file's own build_parser (…)" comment the other imports carry. It writes 15 new restatements, and the issue asks for one note.
- Leaving the new imports bare and the old comments in place. One block then carries two conventions, with nothing to tell a reader which applies.
- Rewording only the variant comments: the three "Both names below" comments, `reviewer_yield`'s "own code, via", and `review_rounds`' comment that covers `REVIEW_SKILLS` alone. The move deletes most of them anyway, and rewording the rest keeps a convention whose content is restatement.
- Deleting every comment with no note. The issue asks for one note in their place.
- A note stating that a test steers a command by patching its own module rather than `cli.py`. That fact's home is `docs/transcript-analysis-architecture.md:44-45` (row 21), and the note would restate it.

**`main()`** (rows 24, 31): at `cli.py:1026-1027`, replace "-- so this can no longer diverge from a subcommand's actual scan roots the way it could before." with ", so this cannot diverge from a subcommand's actual scan roots.", rewrapped. The rest of that comment stays. The issue's `cli.py` prose ride does not name this comment (row 23), so it is in-file cleanup of a file the ticket scopes, not an incidental edit to an unscoped one (row 31).

**Tests** (rows 12-17, 30):
- `tests/test_transcript_cli.py:307`: `"subagents"` becomes `"cost"`. The comment at :305-306 has the assertion sample one subcommand per registration path, one of them "`cli.py`'s `_add_project_scope_args` call". After the move subagents no longer takes that path, and cost does (`cli.py:404`).
- `tests/test_transcript_cli_bootstrap.py:1015-1018`: the docstring's first sentence becomes "Proves, in a fresh interpreter, that audit_routing.py resolves through sys.path[0] alone and that real argparse dispatches audit-routing-samples to cmd_audit_routing_samples through set_defaults(func=...).", the shape its sibling at :964-965 uses. The rest stays, rewrapped so the docstring keeps its four lines in a file already over the limit.
- `test_review_trace_parses_omit_plan_review_spawns` stays in `tests/test_transcript_cli.py`, and the architecture doc records it as a second exception beside the review-cost-by-size one. A separate follow-up PR splits `tests/test_transcript_review_trace.py` at a named seam and moves it there (row 30).
- No new test. The substitute verification is the merge-base `capture --all-help` and `compare-captures` pair (M7), which proves the argparse surface of every moved block is unchanged. Two existing checks add to it:
  - The top-level `--help` subprocess test (`tests/test_transcript_cli_bootstrap.py:48-51`) builds the whole parser in a fresh interpreter, which runs every new registrar through the shim's real import path.
  - `TestConfigDirRouting` and `TestConfigDirFlag` in `tests/test_transcript_cli.py` drive `cli.main()` through the real parser for eight moved commands.
  - The subprocess tests (row 17) prove registration and dispatch at default arguments only, not flags or defaults. An in-process round-trip of each registrar's flags, defaults and `func` is a durable gap this move does not widen, left to a follow-up.

Placements set aside for the review-trace test (row 30):
- `tests/test_transcript_review_trace.py`, which the Ask's ride names. That file is 2,695 lines. The rule's Size paragraph sends a file that would cross the limit to a seam split instead, and the move departs from the precedent at architecture doc :798-801.
- A new seam file such as `tests/test_transcript_review_trace_parser.py` holding only this test. It is the one-test file the review-cost-by-size precedent declined to create, and it needs a `TEST_FILES` entry in `tests/test_transcript_package_module_prefixes.py`. The seam split belongs to the follow-up PR.

**Architecture doc** (`docs/transcript-analysis-architecture.md`; rows 12, 25-26):
- :13-14: delete the sentence starting "`build_parser()` wires up `review_trace.py`'s". The no-cycle sentence at :12-13 stays.
- :23-43: replace the six "`build_parser()` likewise wires up" sentences (:23-37) and the 23-name registrar list (:38-43) with: "`build_parser()` registers every subcommand by calling its owning module's `_add_<command>_subparser` from `cli.py` at that command's place in registration order, except the five the `cli.py` section below names. Each module's own registration binds `set_defaults(func=...)` to its own handler." :44-45 stay. A list naming every registrar would restate `build_parser()` and need an edit for each new command.
- In each moved module's section, the sentence naming what `cli.py` reads bare becomes "`_add_<command>_subparser` is the one name `build_parser()` reaches bare.", the form 17 registrar-module sections use (e.g. :609, :639). The sites:
  - review_trace :361-362
  - read_scope :370-371
  - pr_cost_export :457-458
  - cache_rebuild :475-477
  - cost_ledger :496
  - workstream_cost :504-505
  - subagents :513-514
  - rearm_backtest :555-556
  - spend_over_threshold :564-565
  - plan_boundary :576
  - handoff_signal_response :589-590

  The subagents section's `_MCP_TOOL_BUCKET_LABEL` sentence stays.
- audit_routing :484 becomes "Its three registrars, `_add_audit_routing_subparser`, `_add_audit_routing_shape_subparser`, and `_add_audit_routing_samples_subparser`, are the only names `build_parser()` reaches bare." subagent_mix :527-528 becomes "`_add_subagent_mix_subparser` and `_add_cost_counts_subparser` are the two names `build_parser()` reaches bare."
- The author_outcome and reviewer_yield sections name no bare read today. Each gains the one-name sentence: author_outcome's after "matching `review_rounds.py`'s own convention." (:327), reviewer_yield's after "`cli.py` does not import it." (:254).
- :781: "`build_parser()` registers 23 subcommands inline." becomes "`build_parser()` registers five subcommands inline: cost, cost-trend, pr-cost, review-round-cost, and period-cost."
- :798-801 becomes: "`tests/test_transcript_cli.py` holds two tests that are exceptions to the rule's bullet on one command's CLI wiring, both in `TestBuildParser`. `test_review_cost_by_size_all_accounts_flag_wires_to_all_accounts` belongs in `tests/test_transcript_review_cost_by_size.py`, and `test_review_trace_parses_omit_plan_review_spawns` belongs in `tests/test_transcript_review_trace.py`. Both files are over the line limit, so each move waits for a split of its file at a named seam."
- The cost (:242), review_rounds (:279), pr_cost (:449-450) and period_cost (:632) sentences describe staying commands and stay.

**Size.** No module crosses 1,000 lines. `author_outcome.py`, the tightest, goes from 968 to about 990 (row 11). The limit has no mechanical check yet, so no exception row needs an update (row 10).

**Assumption ledger.**

**Root:** `transcript_analysis/cli.py` is 1,029 lines, over the 1,000-line limit `docs/design-decisions/code-file-line-limit.md` sets, because `build_parser()` still registers 23 subcommands inline. This change moves 18 of them into registrars in their owning modules, with no change to any command's parsed arguments or help output. `[verified: read of cli.py; code-file-line-limit.md:19]`

**Givens:**
- G1. The limit is 1,000 lines for production and test files alike. Reason: `docs/design-decisions/code-file-line-limit.md` owns it, and revisiting it is outside this plan. `[verified: code-file-line-limit.md:19]`
- G2. Agents run `select-tests.py`, not the full suite. Reason: the repo `CLAUDE.md` § "Commands" sets it, and changing it is outside this plan. `[verified: CLAUDE.md "Commands"]`
- G3. Eight tests fail on this machine because `timeout` is missing from `PATH`. Reason: the machine's `PATH` lies outside the repository, and `CLAUDE.md` § "Safety" bars installing software autonomously. `[unverified: the count and the cause come from the session's own run, relayed in the Context]`

**Rows:**
1. `cli.py` is 1,029 lines. `build_parser()` (:200-989) registers 23 subcommands in inline `sub.add_parser` blocks and 23 through registrar calls at :218-222, :327-330, :369, :459-461, :496-502, :757 and :980-987. The 18 moved blocks sit at the ranges the per-command list gives, and the five staying blocks at :397-457, :504-518, :596-666, :721-755 and :794-812. `[verified: read]`
2. Registration order sets the order of the top-level `--help` listing and of the subparsers action's `choices`. `capture --all-help` records both, in the top-level `--help` stdout and its `.parser` dump. `[verified: _move_fidelity.py:587-606, :633-644]`
3. All 23 existing registrars take `subparsers: argparse._SubParsersAction` and return `None`. 21 carry a three-line docstring restating their name and their `set_defaults` line, and `read_cost.py:616` and `routing_guard.py:2084` carry none. `[verified: grep of transcript_analysis/ for "def _add_[a-z_]+_subparser"]`
4. Each of the 15 target modules already has `from __future__ import annotations`, `import argparse`, and an unaliased `from transcript_analysis import ...` that names `scope`, so no registrar needs a new import. `[verified: grep of the 15 modules' import lines; review_trace.py:17-26]`
5. Nine target modules (cost_ledger, workstream_cost, subagents, subagent_mix, rearm_backtest, spend_over_threshold, plan_boundary, handoff_signal_response, read_scope) are in `PRODUCTION_MODULES`. That test fails any `from transcript_analysis.<m> import` line, and any `scope.<name>` read naming something `scope.py` does not bind at top level. `_add_project_scope_args` (scope.py:509) and `_iso_date` (scope.py:1109) are top-level defs. `[verified: tests/test_transcript_package_module_prefixes.py:27-60, :286-302, :393-413; grep of scope.py]`
6. The five staying blocks read `_add_project_scope_args` and `_iso_date` bare (`cli.py:404`, :508, :605, :730, :732-733, :803, :806), and `main()` reads `_SUBCOMMANDS_REFUSING_TOP_LEVEL_CONFIG_DIR` (:1003). So `cli.py`'s `scope` import keeps all three names. `[verified: read]`
7. A `.parser` dump renders each callable as `<callable MODULE:QUALNAME>` by its defining module. Only a callable the captured shim itself defines renders as `<script>`, and `transcript-analysis.py` defines none (:7-14). The moved handlers and `scope._iso_date` therefore render the same on both sides. `compare-captures` needs no `--moved-callable`, and passing one reports `moved-callable-unused`. `[verified: _move_fidelity.py:17-22, :578-580, :735-738]`
8. No parser default or help string depends on the checkout path. The package's two `__file__`-derived values (redaction.py:259, routing_guard.py:75) are read only inside function bodies (redaction.py:293, routing_guard.py:950). A capture from a detached merge-base worktree can then compare byte for byte with one from this worktree. `[verified: grep of transcript_analysis/ for __file__, Path.home() and expanduser]`
9. `remainder` drops every top-level import, with its comments, from its body comparison, but still diffs the set of import triples (`--import-added`, `--import-removed`). So step 4 catches an undeclared import in the 15 modules, and the `cli.py` import block rests on ruff F401/F821 plus the step 5 greps. `[verified: _move_fidelity.py:32-33; staff-sdet read of `_import_diff_findings`]`
10. Nothing enforces the line limit yet. The decision defers its check until an offender assessment exists, and no `.py` file names `code-file-line-limit`, so no exception row exists for `cli.py` or any target module. `[verified: code-file-line-limit.md:50-57; grep of *.py]`
11. `author_outcome.py` is 968 lines, and its block is 19 lines (`cli.py:759-777`). With a `def` line, two blank lines and no docstring, it reaches about 990. `[verified: line count from the Context's exploration; block range read]`
12. `tests/test_transcript_review_trace.py` is 2,695 lines, `tests/test_transcript_review_cost_by_size.py` 1,107, and `tests/test_transcript_cli_bootstrap.py` 1,440. The architecture doc (:798-801) keeps the review-cost-by-size wiring test in `tests/test_transcript_cli.py` because its module's test file is over the limit, until that file splits at a named seam. `[verified: line counts by grep; read]`
13. `.claude/rules/transcript-analysis-tests.md` sends one command's CLI wiring to the test file of the module that registers its subparser (:21-22). Its Size paragraph sends a test file that would cross the limit to a split at a named seam. `[verified: read]`
14. `test_review_trace_parses_omit_plan_review_spawns` (`tests/test_transcript_cli.py:335-339`) is the only test there that pins one moved command's wiring. The file's other tests walk every subparser or run `main()`. `[verified: grep of the file's test names; read of :150-379]`
15. `test_every_subcommand_with_both_scope_flags_refuses_them_together` names `subagents` as its sample of "`cli.py`'s `_add_project_scope_args` call" path (:305-307). Cost registers `--projects`/`--this-repo` that way (`cli.py:404`) and stays inline. `[verified: read]`
16. `tests/test_transcript_cli_bootstrap.py:1015` says its test proves `from transcript_analysis.audit_routing import cmd_audit_routing_samples` resolves. After the move `cli.py` imports the registrar instead, so that claim goes false. `[verified: read]`
17. `test_transcript_analysis_help_exits_zero` (`tests/test_transcript_cli_bootstrap.py:48-51`) runs the shim's top-level `--help` in a fresh interpreter, which builds the whole parser. Every moved command except author-outcome also has a subprocess test in that file, and author-outcome's in-process parser tests sit in `tests/test_author_outcome.py:2644-2666`. `[verified: read; grep of the file's test names]`
18. Tests read only `build_parser` and `main` from `cli`, and a shim copy may read only those two, so dropping the moved names from `cli.py` breaks no test binding. `[verified: grep of tests/ for "cli." reads; tests/test_transcript_package_module_prefixes.py:533, :617-648]`
19. ruff selects `E`, `F`, `B`, `I`, `UP` and `SIM` at line length 130. The moved lines keep their indentation, so only the `scope.` prefix lengthens any of them, and Verification step 7 checks `E501`. `[verified: pyproject.toml:2, :6]`
20. 38 of `cli.py`'s 41 `from transcript_analysis.<m> import` statements carry a comment naming the `build_parser()` line that reads their names, and the `author_outcome`, `cost` and `scope` ones carry none. `CLAUDE.md` §Durable text says "Delete a restatement", and `comment-discipline-reviewer.md:148-151` makes deletion the fix. `[verified: read of cli.py:3-197; read]`
21. The steering fact behind `cli.py`'s by-name imports lives at `docs/transcript-analysis-architecture.md:44-45`, and `tests/test_transcript_analysis_cost_import_direction.py` pins `cli.py`'s `cost` import. `[verified: read]`
22. No skill, rule, test or doc reads the "Read bare by" comment text. Outside committed plans, the phrase occurs only in `cli.py`. `[verified: grep of the repo excluding .claude/plans/]`
23. GH-1432's PR B "Rides with this PR" list reads: "`cli.py` prose: the "Both names below" comment at the first of its three sites lists three names, and a "Read bare by this file's own build_parser" comment repeats about 33 times. Replace the per-import comments with one note." That ride names the import-block comments and no other `cli.py` comment. `[verified: gh issue view 1432]`
24. `cli.py:1024-1027` ends "so this can no longer diverge from a subcommand's actual scan roots the way it could before", a prior-state comparison `CLAUDE.md` §Durable text bars. `[verified: read]`
25. `docs/transcript-analysis-architecture.md` names the moved commands' bare reads at :13-14, :23-37 and in the per-module sections the Approach lists. It enumerates 23 registrar commands at :38-41 and says "registers 23 subcommands inline" at :781. The author_outcome (:301-329) and reviewer_yield (:245-254) sections name no bare read. `[verified: grep with context; read]`
26. `tests/test_transcript_analysis_architecture_doc.py` reads only the doc's `` ### `<name>.py` `` headings. The only other test reading the doc, `tests/test_transcript_session_pricing.py`, pins the session_pricing section, which this change leaves alone. `[verified: read; grep of *.py for the doc path]`
27. `select-tests.py` maps any path under `claude/.claude/scripts/` to `claude/.claude/scripts/tests` (:628). It maps the architecture doc to that directory too (:891), and any `docs/` path to the hooks and skills test directories (:896). `[verified: read]`
28. The moved blocks pass `add_parser` only a name and `help=`, and set none of the settings the dump omits (`fromfile_prefix_chars`, `conflict_handler`, a version string). `ACTION_ATTRIBUTES` omits `help` and `metavar`, and each subcommand's `--help` stdout carries both. `[verified: read of cli.py:224-978; _move_fidelity.py:27-28, :71-72]`
29. The 18 moved blocks contain no comment lines, so the move drops none. `[verified: read of cli.py:224-978]`
30. The split of `tests/test_transcript_review_trace.py` that would take `test_review_trace_parses_omit_plan_review_spawns` lands in a separate follow-up PR, so this PR keeps the test in `tests/test_transcript_cli.py` and records it as a second exception at architecture doc :798-801. `[engineer-verified: "Yeah, separate follow-up PR makes sense."]`
31. `CLAUDE.md` §Working Style "Scope discipline" licenses refactoring inside a file the ticket scopes (Axis 2), and reserves the "Incidental edits" bucket for files outside it (Axis 1). Axis 3's decision test treats a comment describing how the code currently behaves as fair game, and `main()`'s comment at `cli.py:1024-1027` is one. `[verified: CLAUDE.md "Scope discipline"; read of cli.py:1024-1027]`

32. This plan proceeds as one PR at its projected diff. `[engineer-verified: "Proceed anyway"]`
33. The `main()` comment rewrite at `cli.py:1024-1027` stays in this PR. `[engineer-verified: "Keep it (Recommended)"]`

**Mechanisms:**
- M1: Each registrar is its block verbatim with three token substitutions, appended at its module's end, with no docstring and no new import. `anchors: root, row3, row4, row5, row6, row10, row11, row19, row29`. The docstring and the table-driven alternatives are set aside under "Registrar shape" above.
- M2: `build_parser()` calls each registrar where its block stood. `anchors: row1, row2`.
- M3: The import-block rewrite: the moved names go out, the registrar imports come in, and one note replaces every per-import comment. `anchors: row6, row18, row20, row21, row22, row23`. The alternatives are under "`cli.py` import block" above.
- M4: A present-tense rewrite of `main()`'s last comment, as in-file cleanup the issue's ride does not name. `anchors: row23, row24, row31`.
- M5: Two test edits, and the review-trace test staying put until the follow-up split. `anchors: row12, row13, row14, row15, row16, row17, row30`.
- M6: Architecture doc edits that name the registrars and the five inline commands. `anchors: row12, row25, row26`.
- M7: The fidelity proof, which pairs `capture --all-help` at the merge-base and on this tree, `compare-captures` with no `--moved-callable`, `remainder` per module, and greps for the import block. `anchors: row2, row7, row8, row9, row28`. The detached merge-base worktree is heavier than a one-time proof needs. Lighter primitives checked:
  - Capturing in this worktree before the first edit works once, but no reviewer can re-run the before side after the move lands.
  - `git stash` to restore the base tree is barred here, because the stash stack is shared across worktrees.
  - `git show <base>:<path>` into scratch files serves `remainder`, but `capture` imports the whole package from one directory.

  `CLAUDE.md`'s "Prove your change caused a failing check" uses the same `git worktree add` recipe, and Verification step 9 reuses the worktree for that purpose.
- M8: One `code-writer` dispatch, without a split. `anchors: row1, row25, row27`. The reason is under Critical files.

## Critical files

**Dispatch: one `code-writer`, without a split.** The doc and test edits name registrars and positions the move fixes, so a second dispatch would need the first's output and the same table. Both halves also select the same `claude/.claude/scripts/tests` run (row 27), and one self-review then sees the move and the prose describing it. The brief requires:
- Verification steps 1-9, run by the agent, with their output in the return: `compare-captures`' output and exit code, each `remainder` exit code, `wc -l` per file, and the `select-tests.py` summary with any failing node IDs;
- no edit in the 15 modules except the appended registrars;
- no staging and no commits.

**Modify, production** (under `claude/.claude/scripts/transcript_analysis/`):
- `cli.py`;
- `audit_routing.py`, `author_outcome.py`, `cache_rebuild.py`, `cost_ledger.py`, `handoff_signal_response.py`, `plan_boundary.py`, `pr_cost_export.py`, `read_scope.py`, `rearm_backtest.py`, `review_trace.py`, `reviewer_yield.py`, `spend_over_threshold.py`, `subagent_mix.py`, `subagents.py`, `workstream_cost.py`, each gaining only its registrars.

**Modify, tests:**
- `claude/.claude/scripts/tests/test_transcript_cli.py` (:307 only);
- `claude/.claude/scripts/tests/test_transcript_cli_bootstrap.py` (the docstring at :1015-1018 only).

**Modify, docs:**
- `docs/transcript-analysis-architecture.md`.

**Also in the diff:** `.claude/plans/cli-subparser-move.md`.

**Reuse:** `scope._add_project_scope_args` (scope.py:509), `scope._iso_date` (scope.py:1109), the existing registrar signature, and `tests/_move_fidelity.py`'s `capture`, `compare-captures` and `remainder`. No new helper, script or test.

Projected diff: about 1,350 lines. About 640 lines leave `cli.py` (the 18 blocks, the moved imports and the import-block comments) and about 45 enter it. About 565 are added across the 15 modules, about 100 change in the architecture doc, and about 6 change across the two test files.

## Verification

Run from the worktree root. `<scratch>` is a new directory outside the repository, and `<base>` is the SHA that `git merge-base HEAD origin/main` prints.

1. Capture the before side from the merge-base:
   - `git worktree add --detach <scratch>/base <base>`
   - `mkdir -p <scratch>/account`
   - `scripts/venv-run.sh python3 claude/.claude/scripts/tests/_move_fidelity.py capture --script <scratch>/base/claude/.claude/scripts/transcript-analysis.py --account <scratch>/account --out <scratch>/before --all-help`
2. Capture the after side: the same `capture` command with `--script claude/.claude/scripts/transcript-analysis.py --out <scratch>/after`.
3. `scripts/venv-run.sh python3 claude/.claude/scripts/tests/_move_fidelity.py compare-captures <scratch>/before <scratch>/after` exits 0 and prints no finding. It takes no `--moved-callable` (row 7). It compares the top-level and every subcommand's `.parser` dump and `--help` stdout, stderr and exit status. A `capture-bytes` finding that shows a checkout path contradicts row 8: stop and report it rather than add a `--mask`.
4. For each of the 15 modules, `scripts/venv-run.sh python3 claude/.claude/scripts/tests/_move_fidelity.py remainder --before <scratch>/base/claude/.claude/scripts/transcript_analysis/<module>.py --after claude/.claude/scripts/transcript_analysis/<module>.py --added <registrars>` exits 0. `<registrars>` is that module's names from the per-command list: three for `audit_routing.py`, two for `subagent_mix.py`, and one for each other module. Then `git diff -U0 <base> -- claude/.claude/scripts/transcript_analysis ':!claude/.claude/scripts/transcript_analysis/cli.py' | grep -E '^\+ *(from|import) '` prints nothing, which covers the imports `remainder` drops (row 9).
5. `cli.py`'s shape:
   - `git grep -c "add_parser(" -- claude/.claude/scripts/transcript_analysis/cli.py` reports 5.
   - `git grep -n "cmd_" -- claude/.claude/scripts/transcript_analysis/cli.py` prints only lines naming `cmd_cost`, `cmd_cost_trend`, `cmd_pr_cost`, `cmd_review_round_cost` or `cmd_period_cost`.
   - `sed -n '1,/^def build_parser/p' claude/.claude/scripts/transcript_analysis/cli.py | grep -n '#'` prints exactly one line, the note, and the line after it in the file is `from transcript_analysis import scope`.
   - `git grep -n "could before" -- claude/.claude/scripts/transcript_analysis/cli.py` prints nothing.
6. `wc -l` over `cli.py` and the 15 modules: every count is at most 1,000, `cli.py` is about 440, and `author_outcome.py` is about 990.
7. `scripts/venv-run.sh ruff check claude/.claude/scripts/transcript_analysis/ claude/.claude/scripts/tests/test_transcript_cli.py claude/.claude/scripts/tests/test_transcript_cli_bootstrap.py` is clean.
8. In the architecture doc:
   - `git grep -nE "wires up|reached" -- docs/transcript-analysis-architecture.md` prints exactly three lines: pr_cost's "three names reached bare from `cli.py`", cache_rebuild_rules' "is reached bare from `cli.py`", and period_cost's "one name reached bare from `cli.py`".
   - `git grep -n "23 subcommands" -- docs/transcript-analysis-architecture.md` prints nothing.
9. `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py` passes, apart from failures that also occur at the merge-base.
   - Record the failing node IDs from this tree and rerun those exact IDs from `<scratch>/base`, per `CLAUDE.md`'s "Prove your change caused a failing check". The two sets must be identical. An ID that fails only on this tree blocks. G3's count of eight is a prediction, not the criterion.
   - Report pass counts, with zero failed and zero deselected, for `test_transcript_cli.py`, `test_transcript_cli_bootstrap.py`, `test_transcript_package_module_prefixes.py`, `test_transcript_analysis_architecture_doc.py`, `test_author_outcome.py`, the `test_transcript_<module>*.py` files of the 15 modules, and `test_transcript_analysis_module_test_files.py`, `test_transcript_analysis_cost_import_direction.py` and `test_transcript_analysis_shim_reexports_only.py` (row 27).
   - Once the IDs and their failure text are recorded, the return reports G3 as verified or contradicted.
10. `git worktree remove <scratch>/base`.

## Out of scope

- **Moving cost, cost-trend, pr-cost, review-round-cost and period-cost out of `build_parser()`.** The Ask says "Leave cost, cost-trend, pr-cost, review-round-cost and period-cost inline for now."
- **A follow-up PR that splits `tests/test_transcript_review_trace.py` (2,695 lines) at a named seam and moves `test_review_trace_parses_omit_plan_review_spawns` into the new file.** It proves the split with `tests/_move_fidelity.py test-ids`. That PR is not done here (row 30).
- **Splitting `tests/test_transcript_review_cost_by_size.py` (1,107) or `tests/test_transcript_cli_bootstrap.py` (1,440) at a named seam.** Each is over the limit today, and each split is a change of its own (row 12).
- **"And its subparser registration" in the 15 modules' docstrings and architecture-doc opening lines.** 20 of the 21 existing registrar modules' docstrings say it. No sentence in the 15 becomes false, since each lists what its module computes.
- **The three-line docstrings on 21 of the 23 existing registrars.** They restate their functions, but they sit in 19 modules this change does not otherwise touch.
- **The four stale plan citations and the in-process `cmd_sessions --paths` test**, per the Context's defaults.
- **A CHANGELOG entry.** No command's arguments, output or help change, which Verification step 3 proves.
