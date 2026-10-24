# Transcript-analysis decomposition, phase C: turn-shape, commit-gate, pr-link, sessions

## Context

Goal: finish phase C of tracker GH-1116 by moving the `turn_shape` trio (`cmd_turn_shape`, `cmd_turn_shape_samples`, `cmd_turn_shape_holdout_samples`) and the `commit_gate`, `pr_link` and `sessions` command groups out of the `transcript-analysis.py` shim into `transcript_analysis/` modules, as a pure verbatim move.

Ask: "Sure I accept the split and you can start with C" — the engineer's acceptance of the architect-consulted split of the remaining shim commands into phases C, B and A. The scope of C (the turn-shape trio in one module; `commit_gate.py`, `pr_link.py`, `sessions.py`; each with a `_add_<command>_subparser` registrar and its legacy test slice) is the session's wording, accepted by that reply.

Why now: phases 2 (cost family), 3 (reviewer-yield) and session-signal (#1297) are merged; this is the next phase in the governing plan (`.claude/plans/transcript-analysis-decomposition.md`), templated on `.claude/plans/session-signal.md`. The shim still holds 3,730 lines.

Intended outcome: the four command groups live in four package modules with registrars, their tests live in three new test files that load no shim, and CLI bytes are identical.

Decisions delegated or answered this session:
- Revert rehearsal: the engineer selected "Waive it (Recommended)".
- Bootstrap test placement: the engineer typed "Ask the architect. Don’t over optimize on the 1000 line rule if it doesn’t make architectural sense".

## Approach

The turn-shape trio moves into one package module, `turn_shape.py`. Commit-gate, pr-link, and sessions each move into a module of their own: `commit_gate.py`, `pr_link.py`, and `sessions.py`. Each command gets an `_add_<command>_subparser(subparsers)` registrar, six in all, and `build_parser()` calls each one at its old block's position. The tests move into three test files that load no shim, and `_gate_args` moves to a new `tests/_commit_gate_helpers.py`. Nothing moves into `conftest.py`.

This is a pure move, and no stow consumer sees a behavior change:
- Help, stdout, stderr, and exit codes stay byte-identical under a single-root and a multi-root corpus, with `gh` stubbed (Verification 6).
- The parser dumps differ only by six declared handler relocations.
- The `/transcript-narrative` and `/transcript-analysis` skills keep running `sessions --paths --include-subagents` and `pr-link` unchanged (row 32).
- The only visible difference is that an uncaught exception's traceback names the new module files instead of the shim.

**Part 1: production layout.** Copy moved code verbatim, extracting it by line range from Verification 1's scratch snapshot. Never retype it. Extract with a script that reads the scratch snapshot and writes the new file; if a write-shape hook denies that, stop and report instead of retyping. Shim line ranges are at `63dd57d1` (row 2); if HEAD has moved, re-resolve them by symbol. Only these edits are allowed:
- the production rename map below;
- each module's docstring and imports;
- the registrar wrappers and their parameter name;
- wrap-only reflow past 130 characters, with the count reported. Moved lines are expected to need none after the rename maps, so a nonzero count signals a bad substitution `[unverified: relayed from the backend review's check]`. The authored docstrings below are single logical lines past 130 columns; wrap them in `duration.py`'s shape, and do not count that wrapping in the reflow figure.

Every module starts with its docstring, then `from __future__ import annotations`, then stdlib imports, then one `from transcript_analysis import …` line (row 34). No `.py` text the plan adds names a ticket or a line number.

- **`transcript_analysis/turn_shape.py`** holds :2253-2619 verbatim: 15 names, each with its leading comment. They are `_TURN_SHAPE_MUTATING_GIT_SUBCOMMANDS`, `_TURN_SHAPE_ENV_ASSIGNMENT_TOKEN_RE`, `_command_segment_is_mutating_git`, `_bash_command_is_mutating_git`, `_TURN_SHAPE_CALL_COUNT_BUCKETS`, `_TURN_SHAPE_STREAK_BUCKETS`, `_turn_shape_call_count_bucket`, `_turn_shape_streak_bucket`, `_turn_shape_session_turns`, `_turn_shape_streaks`, `cmd_turn_shape`, `_TURN_SHAPE_SAMPLES_MIN_STREAK_LEN`, `_TURN_SHAPE_HOLDOUT_STREAK_LEN`, `cmd_turn_shape_samples`, and `cmd_turn_shape_holdout_samples`.
  - The registrars follow in this order: `_add_turn_shape_subparser`, `_add_turn_shape_samples_subparser`, `_add_turn_shape_holdout_samples_subparser`.
  - Package import: `from transcript_analysis import corpus, denials, pricing, render, scope`.
  - Docstring: `"""The turn-shape commands: cmd_turn_shape, the per-turn tool-call-count and single-call streak-length distributions weighted by priced dollars; cmd_turn_shape_samples and cmd_turn_shape_holdout_samples, random samples of flagged and unflagged streaks; the mutating-git classifier the delegation-rule streak excludes; and the three subparser registrations.\n\nImports corpus, denials, pricing, render, and scope by module (attribute access, not by name) -- see scope.py's own top-of-file comment for why."""`
- **`transcript_analysis/commit_gate.py`** holds :420-588 verbatim (the three-line leading comment, `_GIT_COMMIT_RE`, `_NO_VERIFY_RE`, and `cmd_commit_gate`), then `_add_commit_gate_subparser`.
  - Package import: `from transcript_analysis import corpus, scope`.
  - Docstring: `"""The commit-gate command: cmd_commit_gate, each ISO week's main-thread git commits with and without a prior invocation of a named skill, optionally split by permissionMode, and its subparser registration.\n\nImports corpus and scope by module (attribute access, not by name) -- see scope.py's own top-of-file comment for why."""`
- **`transcript_analysis/pr_link.py`** holds :308-417 (`_pr_link_gh_failure_kind`, `_pr_link_report_gh_failure`, and `cmd_pr_link`), then `_add_pr_link_subparser`.
  - Package import: `from transcript_analysis import gh_cli, render, scope`.
  - Docstring: `"""The pr-link command: cmd_pr_link, each named branch's GitHub PR number, main-thread model split, and per-PR comment counts from gh, and its subparser registration.\n\nImports gh_cli, render, and scope by module (attribute access, not by name) -- see scope.py's own top-of-file comment for why."""`
- **`transcript_analysis/sessions.py`** holds `cmd_sessions` (:2626-2660), then `_add_sessions_subparser`.
  - Package import: `from transcript_analysis import corpus, scope`.
  - Docstring: `"""The sessions command: cmd_sessions, the resolved scope's transcript paths one per line, plus split subagent files under --include-subagents, and its subparser registration.\n\nImports corpus and scope by module (attribute access, not by name) -- see scope.py's own top-of-file comment for why."""`
- **Stdlib imports.** ruff F821 and F401 decide each module's stdlib imports. Expected:
  - `turn_shape.py`: `argparse`, `os`, `random`, `re`, `sys`;
  - `commit_gate.py`: `argparse`, `re`, `defaultdict`, `UTC`/`datetime`, `Path`;
  - `pr_link.py`: `argparse`, `json`, `subprocess`, `sys`, `defaultdict`;
  - `sessions.py`: `argparse`, `sys`.
- **Registrars.** Each takes `build_parser()`'s subparsers action as `subparsers` and returns `None`, in the `duration.py:60-68` shape.
  - Docstring: `"""Register the <command> subparser on build_parser()'s subparsers action, binding cmd_<name> through set_defaults(func=...)."""`
  - Each body is its shim block verbatim, with two substitutions: the `sub.add_parser(` receiver `sub` → `subparsers` (no other text substitution, because the sessions block's help strings contain "subagent"), and `_add_project_scope_args` → `scope._add_project_scope_args`.
  - Blocks: pr-link :2790-2798, commit-gate :2802-2817, turn-shape :3603-3616, turn-shape-samples :3618-3638, turn-shape-holdout-samples :3640-3667, sessions :3669-3682.
- **Production rename map.** It applies to code only, never to docstrings or comments. It has 20 entries, and ruff F821 drives the prefixes. Never run a regex over bare names. (Row 6.)
  - To `scope.`: `_branch_filter`, `_resolve_project_scope`, `_session_branch_context`, `_attributed_branch`, `_parse_since_nd_arg`, `_DO_NOT_PUBLISH_BANNER`. Also `_resolve_scan_roots` → `scope.resolve_scan_roots` and `_print_resolved_scope` → `scope.print_resolved_scope`.
  - To `corpus.`: `_parse_ts`, `SUBAGENT_SUBDIR`. The existing `corpus.split_command_segments` read is already in module form.
  - To `pricing.`: `_price_turn`. Also `_dedup_turns_by_request_id` → `pricing.dedup_turns_by_request_id`.
  - To `render.`: `_fam`, `_fmt_usd`.
  - To `denials.`: `_drop_denial_command_flag_values`.
  - To `gh_cli.`: `_classify_gh_error`, `_GH_ERROR_KIND_NETWORK`, `_git_remote_origin_host_and_owner_repo`, `_gh_host_qualified_repo`, `_GH_CALL_TIMEOUT_S`.

**Part 2: shim.**
- Delete the 22 moved top-level names (Verification 7 lists them), the banners at :2249-2251 and :2622-2624, and the six blocks.
- In `build_parser()`:
  - The run :2790-2819 becomes four consecutive calls: `_add_pr_link_subparser(sub)`, `_add_skill_pair_subparser(sub)`, `_add_commit_gate_subparser(sub)`, `_add_skill_invocation_subparser(sub)`.
  - The run :3603-3682 becomes `_add_turn_shape_subparser(sub)`, `_add_turn_shape_samples_subparser(sub)`, `_add_turn_shape_holdout_samples_subparser(sub)`, `_add_sessions_subparser(sub)`.
  - Each run keeps one blank line on each side. `_add_friction_count_subparser(sub)` and `return parser` stay as they are.
- Add one by-name import block per module, each at its sorted position (ruff I001 decides):
  - **commit_gate, pr_link, sessions.** Each block imports the registrar and `cmd_<name>  # noqa: F401`, with this comment: `# _add_<name>_subparser is read bare by this file's own build_parser (the <command> subparser's registration). cmd_<name> is read only via _mod.cmd_<name> from test files.` (row 7)
  - **turn_shape.** The block imports only the three registrars, with this comment: `# Read bare by this file's own build_parser (each turn-shape subcommand's registration).` No staying test reads a turn-shape name (row 7).
- Rewrite the module-list comment above `from transcript_analysis import (` (:21-26). Its first sentence adds `corpus` to the `_mod.<module>`-only list, and its second sentence becomes `scope is the one module this file's own code reads bare, as scope.PROJECTS_DIR.` The `A module dropped from this list…` lines (:27-29) stay as written (row 12).
- **Orphaned imports.** For each name ruff F401 flags after the deletion, a `_mod.<name>` grep decides its fate. The grep is `git grep -n "_mod\.<name>\b" -- claude/.claude/scripts/tests`, and a hit counts only in a file whose `_SCRIPT` names `transcript-analysis.py` (row 11).
  - **A test reads it.** Keep the import with `# noqa: F401 -- read only via _mod.<name> from test files`. For `os`, append `, as a stdlib passthrough`. For the `_resolve_scan_roots` alias, put that sentence on the line above, as `# _resolve_scan_roots is read only via _mod._resolve_scan_roots from test files.`, and end the import line with a bare `# noqa: F401`.
  - **No test reads it.** Remove the import.
  - **At plan time (row 10):**
    - Keep: `os`, `SUBAGENT_SUBDIR`, `_fam`, `_resolve_scan_roots`.
    - Remove: `random`, `subprocess`, the `from datetime import UTC, datetime` line, the whole `transcript_analysis.denials` block (:100-103), the whole `transcript_analysis.gh_cli` block (:120-128), `_attributed_branch`, `_branch_filter`, `_session_branch_context`, and `_fmt_usd`.
  - **Unexpected names.** A flagged name outside these 18 gets the same rule, and the return lists it.

**Part 3: tests.** The new test files load no shim. They import package modules directly, as the `fake_projects` fixture permits.
- **Test rename map** (code only, 16 entries):
  - `_mod.cmd_pr_link` → `pr_link.cmd_pr_link`; `_mod._GH_CALL_TIMEOUT_S` → `gh_cli._GH_CALL_TIMEOUT_S`.
  - `_mod.cmd_commit_gate` → `commit_gate.cmd_commit_gate`.
  - `_mod.<name>` → `turn_shape.<name>` for `_TURN_SHAPE_MUTATING_GIT_SUBCOMMANDS`, `_bash_command_is_mutating_git`, `_turn_shape_call_count_bucket`, `_turn_shape_streak_bucket`, `_turn_shape_session_turns`, `_turn_shape_streaks`, `cmd_turn_shape`, `cmd_turn_shape_samples`, and `cmd_turn_shape_holdout_samples`.
  - `_mod._price_turn` → `pricing._price_turn`; `_mod._parse_ts` → `corpus._parse_ts`; `_mod._fmt_usd` → `render._fmt_usd`; `_mod._DO_NOT_PUBLISH_BANNER` → `scope._DO_NOT_PUBLISH_BANNER`.

  Nothing else on a moved line changes, apart from wrap-only reflow (row 8).
- **`tests/test_transcript_turn_shape.py`** holds legacy :3955-4488: `_turn_shape_args`, `_turn_shape_samples_args`, `_turn_shape_holdout_samples_args`, `TestBashCommandIsMutatingGit`, `TestTurnShapeBuckets`, `TestTurnShapeSessionTurns`, `TestTurnShapeStreaks`, `TestCmdTurnShape`, `TestTurnShapeSamples`, and `TestTurnShapeHoldoutSamples`. Docstring: `"""Tests for transcript_analysis/turn_shape.py: the mutating-git classifier, the call-count and streak buckets, per-session turn extraction and streaks, and the turn-shape, turn-shape-samples, and turn-shape-holdout-samples commands end to end."""`
- **`tests/test_transcript_pr_link.py`** holds `TestPrLink` (:737-1111). Docstring: `"""Tests for transcript_analysis/pr_link.py's cmd_pr_link against a fake subprocess.run: PR lookup, the origin-derived repo and the --repo override, gh failure diagnostics, comment-count failures, and the per-call gh timeout."""`
- **`tests/test_transcript_commit_gate.py`** holds `TestCommitGate` (:1132-1441). Docstring: `"""Tests for transcript_analysis/commit_gate.py's cmd_commit_gate: commit gating by a prior skill invocation, --no-verify, amend, and commit-tree handling, sidechain and exact-name skill matching, --by-permission-mode, ISO-week bins, and the --projects, --exclude-projects, and --branches filters."""`
- **`tests/_commit_gate_helpers.py`** holds `_gate_args` (:1119-1130) verbatim and needs no imports. Docstring: `"""Test helpers shared by test_transcript_commit_gate.py and by test_transcript_analysis.py's cross-subcommand tables."""` Both consumers import it as `from ._commit_gate_helpers import _gate_args` (row 14).
- **Legacy `test_transcript_analysis.py`**:
  - Delete the 13 moved names (Verification 7).
  - Import `_gate_args` from the helper module. Remove only the imports F401 then flags.
  - Banners:
    - Delete :732-734 (`# pr-link (stubbed gh)`) and :3950-3952 (`# turn-shape / turn-shape-samples`).
    - `# commit-gate` (:1115) becomes `# _sanitize_table_cell (render.py)`, since it now heads `TestSanitizeTableCell`.
  - These stay byte-identical: `_fake_gh_pr_list_run`, `_UNCONDITIONAL_HEADER_CASES` and its two classes (`TestAllSubcommandsSingleRootHeader` and `TestRootsThreadingSpy`), `TestBuildParser` (its two pr-link parse tests included), and `TestPerRecordConsumersCreditPrefixToTheBranchEntered`.
- **`test_transcript_analysis_branch_attribution_structure.py`.** In `_RAW_BY_DESIGN`, `"transcript-analysis.py:cmd_commit_gate"` becomes `"transcript_analysis/commit_gate.py:cmd_commit_gate"`, and `"transcript-analysis.py:_turn_shape_session_turns"` becomes `"transcript_analysis/turn_shape.py:_turn_shape_session_turns"`. Counts and reasons stay unchanged, and no test ID changes (row 16).
- **`test_transcript_package_module_prefixes.py`.** Append `turn_shape.py`, `commit_gate.py`, `pr_link.py`, and `sessions.py` to `PRODUCTION_MODULES`. Append the three new test files to `TEST_FILES`, which `test_loader_free_package_importing_test_files_are_in_test_files` requires (row 17).
- **`test_transcript_cli_bootstrap.py`** gains three definitions after the session-signal dispatch test (:547-573). The turn-shape trio's six tests (:61-149) stay as they are (rows 21, 31).
  - `_seed_git_and_sessions_account(tmp_path) -> Path` builds `tmp_path/"account"/projects/-home-user-bootstraprepo/bootstrap-sessions-probe.jsonl` and returns the config dir. The file holds one record: `_asst("claude-sonnet-5", branch="bootstrap-git", ts="2026-05-19T10:00:00.000Z", content=[_bash_use("t1", "git commit -m wip")])`. It also seeds `bootstrap-sessions-probe/subagents/agent-probe.jsonl` beside it, with one `_asst(..., sidechain=True)` record, so the `sessions` case reaches the `corpus.SUBAGENT_SUBDIR` read.
  - `test_transcript_analysis_git_and_sessions_help_exits_zero` is parametrized with `ids=["pr-link", "commit-gate", "sessions"]` over `("pr-link", "--author")`, `("commit-gate", "--by-permission-mode")`, and `("sessions", "--include-subagents")`. Docstring: `"""Proves, in a fresh interpreter, that build_parser calls the pr-link, commit-gate, and sessions registrars."""`
  - `test_transcript_analysis_git_and_sessions_subprocess_finds_seeded_session` uses the same `ids`.
    - Its cases: `pr-link --repo owner/repo --branches bootstrap-git` → `gh-err`; `commit-gate code-review` → `skill/1k`; `sessions --paths --include-subagents` → `agent-probe.jsonl`, a name only the `SUBAGENT_SUBDIR` branch prints.
    - Beyond the token, the commit-gate case asserts seed-derived cells with the existing `_table_cols(out, header_contains="skill/1k", row_contains="2026-W21", row_startswith=True)`: `commits == "1"` and `wo-skill == "1"`. The pr-link case asserts `_table_cols(out, header_contains="Branch", row_contains="bootstrap-git", max_labels=4)["Sonnet"] == "1"`, because the `gh-err` row has five cells against six header labels and `_table_cols` asserts `len(values) >= len(labels)`. Cell-level assertions go in the dispatch test, so the parametrized shape holds; a per-case callable carries them.
    - It runs `_run("transcript-analysis.py", "--config-dir", <account>, *argv, env={**os.environ, "PATH": <an existing empty directory created fresh under tmp_path>})`, then asserts exit 0 and that the token is in stdout. An empty-string PATH would mean the current directory to the exec search, so the directory must exist.
    - Docstring: `"""Proves, in a fresh interpreter, that real argparse dispatches pr-link, commit-gate, and sessions each to its own handler through set_defaults(func=...). PATH holds no gh binary, so pr-link prints its gh-err row without a network call."""`
  - The file passes 1,000 lines. That is this plan's decision (row 22, M8), and the PR body flags it.

**Part 4: docs and comments.**
- **`transcript_analysis/corpus.py:246`.** `` (`transcript-analysis.py`'s `` becomes `` (`turn_shape.py`'s ``. This is the only edit to the file (row 13).
- **`transcript_analysis/__init__.py`.** Append `turn_shape, commit_gate, pr_link, sessions` to the command-group module list.
- **`docs/transcript-analysis-architecture.md`**:
  - **Intro.**
    - Add the four modules to the imports-back-into list (:16-21).
    - Delete the sentence at :23-25 (`The still-unmigrated command-shape helper…`).
    - After :37, add: "`turn_shape.py` imports `denials.py` (for `_drop_denial_command_flag_values`)."
  - **Registrar paragraph (:55-63).** Add pr-link, commit-gate, turn-shape, turn-shape-samples, turn-shape-holdout-samples, and sessions to the registrar sentence. Add `cmd_commit_gate`, `cmd_pr_link`, and `cmd_sessions` to the test-only bindings sentence.
  - **`corpus.py` section (:78).** "the shim's own mutating-git classifier" becomes "`turn_shape.py`'s mutating-git classifier (`_bash_command_is_mutating_git`)".
  - **`denials.py` section (:327-329).** The `_drop_denial_command_flag_values…still-unmigrated…` sentence becomes "`turn_shape.py` reads `_drop_denial_command_flag_values` as `denials._drop_denial_command_flag_values`."
  - **`gh_cli.py` section (:380-382).** "The five names pr-link reaches bare from the shim: …" becomes "`pr_link.py` reads five of its names by attribute access (`gh_cli.<name>`): `_classify_gh_error`, `_GH_ERROR_KIND_NETWORK`, `_git_remote_origin_host_and_owner_repo`, `_gh_host_qualified_repo`, and `_GH_CALL_TIMEOUT_S`."
  - **Four new `### <module>.py` sections** go after `friction_count.py` (row 18). Each states:
    - the module's responsibilities;
    - its module imports;
    - that its registrar or registrars are the only names `build_parser()` reaches bare.

    The `turn_shape.py` section also states that its three commands share `_turn_shape_session_turns` and `_turn_shape_streaks`, and that its classifier reads `corpus.split_command_segments` and `denials._drop_denial_command_flag_values`.
  - **Tests section.** After the session-signal paragraph, add a paragraph that:
    - names the three new test files and states that none loads the shim;
    - names `tests/_commit_gate_helpers.py` as a plain module that the legacy file also imports, as `from ._commit_gate_helpers import ...` (see `.claude/rules/test-tree-packaging.md`);
    - states that `sessions.py` has no test file of its own, because the cross-subcommand header table covers its header and `tests/test_transcript_cli_bootstrap.py` covers dispatch and the `--paths --include-subagents` emission, while the `--paths` refusal stays untested;
    - states that the pr-link method of `TestPerRecordConsumersCreditPrefixToTheBranchEntered` stays with that class (see the paragraph above).

    The session-signal paragraph stays unchanged. Its sentence at :852-853 stays true.

**Decision: `TestPerRecordConsumersCreditPrefixToTheBranchEntered` stays in the legacy file, byte-identical.** Every group it spans has moved once this PR lands (row 15), so the governing rule now allows it to move. It does not require the move.
- The precedent is `TestMultiRootFormatOutliers`. It stayed after its last group moved, and the doc records only what it spans (doc :757-759).
- The shim keeps `cmd_pr_link` for `_UNCONDITIONAL_HEADER_CASES` anyway (row 7), so moving the class would buy no shim simplification in this phase.
- Moving it would widen the diff into `test_transcript_analysis_branch_attribution.py` and both helper modules' docstrings and imports.
- Retargeting only its pr-link method would mix `_mod.` and package reads inside one class.

Its future home is in Out of scope.

**Decision: `_gate_args` goes to `tests/_commit_gate_helpers.py`.** Two files read it (row 14), and one copy is required (CLAUDE.md § Engineering Judgment, "Single source of truth").
- A helper module is the series' shape, with seven `_<family>_helpers.py` modules.
- Importing from a test module has one precedent (`test_transcript_review_rounds.py:27`), and it couples two test files.
- Inlining a lambda in `_UNCONDITIONAL_HEADER_CASES` would edit a table the `cli.py` phase owns and write a second definition of the Namespace shape.
- `conftest.py` would contradict the basis of the revert-rehearsal waiver (row 28).

**Decision: the bootstrap tests stay in `test_transcript_cli_bootstrap.py`, and the file goes past 1,000 lines (about 1,045).** The engineer delegated this call (row 22). Nothing enforces the limit yet (row 20). The file is a flat list of independent, per-subcommand subprocess smokes that share `_run` (row 21), so no seam exists that is not a line-count cut.
- **(a) A sibling file** would split on line count, not concept, and the five unmoved shim commands would reopen the question each phase. It would also need `_run` extracted into a new helper module, which edits the 985-line file anyway.
- **(b) Moving the session-signal block out** needs the same `_run` extraction and moves tests merged one PR ago for size alone.

The series' convention is to flag an over-limit file in the PR body as an F1 candidate rather than split it ad hoc (row 21). The PR body therefore flags this file, and Out of scope records the deliberate fix.

**Decision: no revert rehearsal in this phase.** The engineer waived it by selecting "Waive it (Recommended)" (row 27). The reasons that follow are this plan's, not the engineer's (row 28):
- This PR adds nothing to `conftest.py`, so the governing plan's stated failure mode does not arise.
- While no later commit has touched a file this PR touches, a revert of its squash commit applies cleanly.
- The PR body's rollback statement covers the window after a later phase.

**Alternatives set aside.** At the whole-plan level:
- **One module for all four groups.** It would be about 800 lines, so it fits under the limit (row 5). It would still group commands that share no helper, against the package's seam rule (row 4).
- **Three turn-shape modules.** The trio shares `_turn_shape_session_turns` and `_turn_shape_streaks` (row 3). Splitting it needs a helper leaf that no other module reads, which is the case where `cost.py`, `subagent_mix.py`, and `audit_routing.py` group instead (row 4).
- **One `git.py` for pr-link and commit-gate.** The two share no helper (row 3).
- **One `_add_turn_shape_subparsers` for all three commands.** All nine existing registrars are named for one command each (row 4). Keeping per-command registrars lets the `cli.py` phase compose registration by command.
- **A dedicated sessions test file.** No legacy slice exists to move (row 33), and new tests are beyond a move. See Out of scope.

### Assumption ledger

**Root:** The shim (3,730 lines) still holds four command groups: the turn-shape trio, commit-gate, pr-link, and sessions. Their 22 top-level names and six `add_parser` blocks, about 800 lines in all, live there. Their tests, about 1,250 lines, live in the 7,176-line legacy test file. This PR moves them into four package modules and three test files and leaves one definition per name. CLI bytes stay identical, and the parser configuration changes only by six declared handler relocations. `[verified: transcript-analysis.py:308-588, :2249-2661, :2790-2817, :3603-3682, last line 3730; legacy :732-1441, :3950-4489; line count via grep]`

**Givens:**
- G1. The shim keeps `build_parser()`, `main()`, and every unmigrated `cmd_*` until the `cli.py` phase. Reason: the governing plan's phase order owns this. `[verified: transcript-analysis-decomposition.md:155-161]`
- G2. pytest's `prepend` import mode makes test basenames global, so new files need unique names. Reason: pytest owns this behavior. `[verified: transcript-analysis-decomposition.md:59-63; row 39]`
- G3. A monkeypatch reaches code only through the binding that code reads at call time. Reason: Python import semantics. `[verified: transcript-analysis-decomposition.md:113, quoting the Python reference: "a reference to that value is stored in the current namespace"]`
- G4. Agents run `select-tests.py`, not the full suite, outside CLAUDE.md's two named cases. Reason: this repo's CLAUDE.md § "Commands" imposes it. `[verified: CLAUDE.md "Commands"]`

**Rows:**
1. The Ask is the session's Context: move the turn-shape trio, commit-gate, pr-link, and sessions out of the shim as a pure verbatim move. Each module gets registrars, and each test slice gets its own file. `[unverified — relayed from the dispatch]`
2. Shim regions at `63dd57d1`:
   - pr-link :308-417;
   - commit-gate :420-588, including its leading comment at :420-422;
   - turn-shape banner :2249-2251, then the body :2253-2619;
   - sessions banner :2622-2624, then `cmd_sessions` :2626-2660;
   - blocks :2790-2798, :2802-2817, :3603-3616, :3618-3638, :3640-3667, :3669-3682.

   `[verified: read]`
3. Coupling:
   - `_turn_shape_session_turns` is called at :2446, :2525, and :2585.
   - `_turn_shape_streaks` is called at :2456, :2461, :2527, and :2587.
   - `_bash_command_is_mutating_git` is called only at :2380.
   - pr-link, commit-gate, and sessions read no name from each other or from the trio.

   `[verified: read of each range]`
4. Seam precedent:
   - 25 of 28 command modules define one `cmd_*`.
   - `cost.py` (2), `subagent_mix.py` (2), and `audit_routing.py` (3) group commands that share helpers.
   - All nine existing `_add_*_subparser` registrars are named for one command each.

   `[verified: grep "^def cmd_" and "def _add_\w+_subparser" over transcript_analysis/]`
5. Size arithmetic:
   - moved bodies: turn-shape 367 lines, commit-gate 169, pr-link 110, sessions 35;
   - blocks: 105 lines.

   One module would come to about 800 lines with its header. The four modules land at about 460, 205, 140, and 65 lines. `[verified: line arithmetic over row 2]`
6. Every name in the production rename map is already bound by its target module. The shim imports each one from that module at :74-79, :100-103, :120-128, :164-185, :208-218, and :235-256. Definitions confirmed: `scope.py` :472, :509, :524, :690, :919, :1034, :1071, :1088; `gh_cli.py` :194, :231, :234. `[verified: read]`
7. Staying readers of moved names, after the move:
   - `_mod.cmd_pr_link`: legacy :5160 and :7090;
   - `_mod.cmd_commit_gate`: :5165;
   - `_mod.cmd_sessions`: :5191.

   No staying test reads a turn-shape name, `_GH_CALL_TIMEOUT_S`, or any other moved name. `[verified: grep -o of every _mod.<name> read in the tests directory]`
8. The moved slices read only these from the shim:
   - the moved names;
   - `_mod._GH_CALL_TIMEOUT_S` (:953, :1069, :1111);
   - `_mod._price_turn` (:4088, :4209, :4298, :4348);
   - `_mod._parse_ts` (:4139);
   - `_mod._fmt_usd` (:4221-4349);
   - `_mod._DO_NOT_PUBLISH_BANNER` (:4270-4468).

   Their owners are `gh_cli`, `pricing`, `corpus`, `render`, and `scope`. `[verified: grep; shim :76, :123, :181, :213, :236]`
9. Only `TestRootsThreadingSpy` patches a shim binding that the moved code reads (:5386-5387, :5427-5428). It also patches `_mod.scope._resolve_project_scope` and `_mod.scope.print_resolved_scope` (:5391-5392), plus `subprocess.run` (:5393), so its pr-link, commit-gate, and sessions rows still intercept. `TestPrLink` patches `subprocess.run` on the module, which `pr_link.py` reads at call time. No other transcript test calls `setattr(_mod, …)`. `[verified: grep; read :5358-5404]`
10. After the deletion, shim code stops reading these names:
    - `os` (:2283, :2287), `random`, `subprocess`, and `UTC`/`datetime` (:474);
    - `SUBAGENT_SUBDIR`, `_drop_denial_command_flag_values`, and the five gh_cli names;
    - `_attributed_branch`, `_branch_filter`, and `_session_branch_context`;
    - `_fam` (:358), `_fmt_usd`, and `_resolve_scan_roots` (every call is in a moved range).

    Shim-loading tests still read four of them:
    - `_mod.os`: legacy :190 and :4521-4646; test_transcript_cost.py; test_transcript_ledger_common.py:195, :512;
    - `_mod.SUBAGENT_SUBDIR`: :3090, :3160, :4858, :4939, :5038;
    - `_mod._fam`: :357-366;
    - `_mod._resolve_scan_roots`: :6564-6618.

    `re`, `json`, `defaultdict`, `Path`, `_parse_ts`, `_price_turn`, `_parse_since_nd_arg`, `_DO_NOT_PUBLISH_BANNER`, and the `_dedup_turns_by_request_id` alias keep shim readers. `[verified: grep]` That ruff F401 flags exactly these 18 names `[verified: SDET review ran ruff F401,F821 on a scratch shim with the ranges deleted; backend review reproduced it by AST]`. Part 2's unexpected-names rule covers any other.
11. A bare `_mod.<name>` grep over-reports. `test_post_crash_sessions.py:589`'s `_mod.UTC` is post-crash-sessions.py's module (:26-28). `[verified: read]` `test_mark_terminal.py:412`'s `_mod.os` is presumably its own script's `[unverified]`; `os` stays either way.
12. After the move, `scope.` is the shim's only bare module-attribute read. `corpus.split_command_segments` (:2300) was the only bare `corpus.` read. Tests still read `_mod.corpus` (legacy :3075 and others; test_transcript_period_cost.py:5845), so `corpus` stays in the noqa'd list. `[verified: grep]`
13. The move makes these texts stale:
    - corpus.py:245-248;
    - architecture doc :23-25, :78, :327-329, :380-382;
    - shim :21-26;
    - shim :100-103 and :120-128, whose blocks are removed outright.

    No other package file names a moved symbol as shim-resident. `[verified: grep]` docs/case-studies/plan-mode-model-resolution.md and docs/reports/ name moved symbols but are preserved records `[verified: grep]`.
14. `_gate_args` is read by `TestCommitGate` (:1135-1435) and `_UNCONDITIONAL_HEADER_CASES` (:5165). The tests directory has seven `_<family>_helpers.py` modules and one test-to-test import (test_transcript_review_rounds.py:27). `[verified: Glob; grep]`
15. Cross-group placement. The doc states the rule as keeping such a test "in the legacy file until every group it references has moved" (doc :746-748). `TestMultiRootFormatOutliers` spans cost, audit-routing-samples, and judgment-pair, all of which have moved, and it still stays (doc :757-759). `TestPerRecordConsumersCreditPrefixToTheBranchEntered` (:6937-7094) spans buckets, duration, struggle, user-input, judgment-pair, and pr-link. `[verified: read]`
16. `_RAW_BY_DESIGN` holds both keys (:174, :177). Only `_POSITIONAL_CARRY_FORWARD` and `_RAW_READ_BESIDE_ATTRIBUTION` are parametrized (:221, :228), so the rename changes no test ID. The package glob (:144-146) scans new modules. The literal reads at :480 and :2374 move with their functions. `[verified: read]`
17. The prefix test requires the following:
    - every module in `PRODUCTION_MODULES` passes three import and attribute checks (:238-365);
    - every loader-free `test_*.py` that imports the package is listed in `TEST_FILES` (:467-478);
    - each listed file makes at least one resolvable read (:420-430).

    `_commit_gate_helpers.py` is not globbed as `test_*.py`. `[verified: read]`
18. The architecture-doc test requires a `` ### `<name>.py` `` heading for every package module. `[verified: test_transcript_analysis_architecture_doc.py:14-49]`
19. The import-cycle test discovers modules by glob (:25) and adds a `test_module_imports_first_in_fresh_interpreter[<module>]` ID per module (:142-145). Nothing imports the four new modules, so they can close no cycle. `[verified: read]`
20. The limit reads: "Limit: 1,000 lines, for production and test files alike." `[verified: docs/design-decisions/code-file-line-limit.md:19]` The same doc adds: "nothing enforces them until F1 (the offender assessment) records…" `[verified: :50-53]` No test or hook implements a length check: `code-file-line-limit` appears only in plans and docs. `[verified: grep]`
21. Bootstrap file facts:
    - It is 985 lines `[verified: grep count]`.
    - Its tests are flat functions sharing `_run` (:30-42), with per-group seeds `[verified: read]`.
    - The trio already has six tests (:61-149). No subprocess test covers pr-link, commit-gate, or sessions, nor context-distribution, edit-format, instrument-authoring, context-composition, or cache-efficiency, all still in the shim `[verified: grep of def names]`.
    - The series flags over-limit files rather than splitting ad hoc (pr-cost-decomposition.md:338; cache-rebuild-decomposition.md:382) `[verified: grep]`.
22. The engineer delegated the bootstrap test placement and said not to let the 1,000-line rule drive it where it lacks architectural sense. `[engineer-verified: "Ask the architect. Don’t over optimize on the 1000 line rule if it doesn’t make architectural sense"]` The tag covers only that, read against the session's placement question.
23. The three placement options the engineer saw were the session's own wording: a sibling file, moving the session-signal block, and accepting more than 1,000 lines. M8's choice and reasons are this plan's. `[unverified]`
24. Dispatch tokens and pr-link's offline path:
    - `skill/1k` is printed only by `cmd_commit_gate` (:560, :566).
    - `gh-err` is printed only by `cmd_pr_link` (:383).
    - pr-link with `--repo` makes no git call (:334-336) and catches a missing gh as `OSError`, then continues (:379-384).
    - None of the three commands is in `_SUBCOMMANDS_REFUSING_TOP_LEVEL_CONFIG_DIR` (scope.py:701-707).
    - `--config-dir` yields the unresolved `<dir>/projects` (scope.py:493-495).

    `[verified: grep; read]`
25. `capture` runs `sys.executable` (_move_fidelity.py:655-656) under the ambient environment minus PYTHONPATH, then applies the `--env` overrides (:502-503). A `PATH` override therefore controls gh and git without touching the interpreter. gh_cli's origin lookup catches `OSError` and exits 1 (gh_cli.py:206-217). `[verified: read]`
26. turn-shape-samples defaults `--seed` to `None` (:3634-3637) and shuffles with `random.Random(seed)` (:2531). `[verified: read]` A `None` seed draws from OS entropy `[unverified — Python docs not read here]`, so every capture passes `--seed`.
27. Revert rehearsal. Governing plan item 7 reads: "At each phase boundary, `git revert` the phase's merge commit on a scratch branch and run the full suite. Baseline parity alone does not prove a staged PR is independently revertible — a conftest fixture promotion in PR N can leave PR N−1's revert uncollectable." `[verified: transcript-analysis-decomposition.md:276-279]` The engineer waived it for this phase. `[engineer-verified: "Waive it (Recommended)"]` The tag covers only the waiver.
28. That option's description ("nothing here promotes into conftest") was the session's proposal, not the engineer's reason. `[unverified]` The fact it states holds for this plan: Critical files exclude `conftest.py`, and `_gate_args` goes to a helper module. `[verified: this plan]`
29. commit-gate's moved leading comment cites "require-code-review.sh line 38". That hook now gates through `_lib_command_concludes_marker_gated_commit` (:81) and holds no commit regex. The drift predates this move. `[verified: grep]`
30. Tools run through `scripts/venv-run.sh <tool>`, the form CLAUDE.md § Commands specifies. A relative `../../../../.venv` path prompts and breaks at other worktree depths `[verified: CHANGELOG.md:322-323, per the backend review]`. The shared `.venv` holds `bin/pytest`, `bin/ruff` and `bin/python3`, and Python is 3.12.3 `[verified: .venv/pyvenv.cfg:3; backend review confirmed bin/python3]`. `nodes --rename` needs 3.12's `tokenize` `[verified: _move_fidelity.py:16]`. The wrapper exits 127 with a message when a tool is missing `[unverified: relayed from the backend review]`.
31. The existing turn-shape-samples and holdout-samples bootstrap tests both assert `DO NOT PUBLISH` (:118, :149), and both handlers print it (:2535, :2608). A swapped binding between the two would pass bootstrap. Only the PR-time parser dump catches it. `[verified: read]`
32. No hook, plugin, or eval reaches a moved name. Two skills run the CLI: transcript-narrative/SKILL.md:30 and :74, and transcript-analysis/SKILL.md:129. `[verified: grep]`
33. sessions has no dedicated legacy test. Its only in-process coverage is the `_UNCONDITIONAL_HEADER_CASES` row (:5191-5194). `[verified: grep]`
34. Package shape: a module docstring, then `from __future__ import annotations`, stdlib imports, and one unaliased `from transcript_analysis import …` line, with registrars in the `duration.py:60-68` form. `[verified: duration.py:1-12, :60-68; prefix test :238-268]`
35. No moved test carries the `timing` marker. The legacy file's one timing test (:475) stays. `[verified: grep]`
36. `select-tests.py` needs no new row. The shim and package changes already select the scripts tests and the friction-count hook test (session-signal Part 5). `[unverified — relayed from session-signal.md row 40 and Part 5]`
37. `git diff --name-only A...B` lists only the changes on `B` since the merge-base. `[unverified — git docs not read]`
38. `test-ids` matches moved tests without a declaration, because an ID drops its module. `[unverified — relayed from session-signal.md rows 32, 45]`
39. None of the nine new basenames exists anywhere in the repo. `[verified: Glob]`
40. The Ask line's quote and the phase-C scope it covers were confirmed by the engineer when asked whether the wording and scope are theirs. `[engineer-verified: "Yes, that's my wording and scope"]` The tag covers only that confirmation.

**Mechanisms:**
- M1: Four modules: `turn_shape.py` for the trio, plus `commit_gate.py`, `pr_link.py`, and `sessions.py`. `anchors: row3, row4, row5`. Alternatives: see Alternatives set aside.
- M2: Six per-command registrars, called at the old blocks' positions. `anchors: row2, row4`.
- M3: The shim keeps `cmd_pr_link`, `cmd_commit_gate`, and `cmd_sessions` bindings, and none for turn-shape. `anchors: row7`.
- M4: F401 plus a grep limited to shim-loading files decides each orphaned import. The grep is `_mod\.<name>\b|\(_mod, *["']<name>["']`, so string-form patch targets count. `_resolve_project_scope` and `_print_resolved_scope` are patched that way (legacy :5386-5387, :5427-5428) and must stay bound until their last shim reader moves. `anchors: row10, row11, row12`.
- M5: Three test files that load no shim, with the test rename map. `anchors: G2, row8, row17`.
- M6: `tests/_commit_gate_helpers.py`. `anchors: row14, row28`. Its two lighter alternatives fail: a test-module import couples two test files, and an inline lambda writes a second definition.
- M7: `TestPerRecordConsumersCreditPrefixToTheBranchEntered` stays byte-identical. `anchors: row7, row15`.
- M8: Two parametrized bootstrap tests and one seed in the existing file, accepting more than 1,000 lines. `anchors: row20, row21, row22, row23, row24`. Both splits need a new `_run` helper module plus an edit to the existing file. Each cuts at line count, not at a concept.
- M9: Structure-test key rename, the corpus.py comment, the `__init__` docstring, and the doc edits. `anchors: row13, row16, row18`.
- M10: The unchanged move-fidelity harness with a `gh` stub on `PATH`. `anchors: G3, row25, row26, row30`. Lighter alternatives fail: an empty PATH reaches none of the success or classified-failure paths through the five gh_cli names, and leaving pr-link out drops CLI parity that governing plan Verification 2 requires.
- M11: One `code-writer` dispatch, with no stages. `anchors: row7, row9`.
- M12: The selected suite runs in two passes, split by the `timing` marker. `anchors: G4, row35, row36`.

## Critical files

**Dispatch: one `code-writer`, the move.** The shim, the legacy test file, the architecture doc, and the prefix test are shared by all four groups, so no split yields disjoint file sets (plan-it § "Name the dispatch split"). The brief requires:
- copy moved code verbatim, extracted by line range from the parent's `<scratch>/before-shim.py` and `<scratch>/before-legacy.py`, applying only Parts 1–4's edits;
- keep new production code runnable on Python 3.11, because `nudge-error-mode-analysis.sh` runs the shim, which imports every new module;
- run every tool through `scripts/venv-run.sh <tool>` (row 30); if the wrapper reports a tool missing, stop and return without substituting another interpreter;
- if F821 flags a name absent from the rename maps, the stdlib, conftest, or `_commit_gate_helpers.py`, stop and report;
- if any new file exceeds 1,000 lines, stop and report; `test_transcript_cli_bootstrap.py` is exempt from this rule (M8), and only its count is reported;
- do no staging and no commits.

Its return lists:
- every F401 disposition from Part 2;
- the wrap-only reflow count;
- every new bootstrap test ID;
- `wc -l` for each new and changed file.

Files:
- **Create:**
  - `claude/.claude/scripts/transcript_analysis/turn_shape.py`, `…/commit_gate.py`, `…/pr_link.py`, `…/sessions.py`;
  - `claude/.claude/scripts/tests/test_transcript_turn_shape.py`, `…/test_transcript_commit_gate.py`, `…/test_transcript_pr_link.py`, `…/_commit_gate_helpers.py`.
- **Modify:**
  - `claude/.claude/scripts/transcript-analysis.py`;
  - `claude/.claude/scripts/transcript_analysis/__init__.py`, `…/corpus.py` (one comment line);
  - `claude/.claude/scripts/tests/test_transcript_analysis.py`, `…/test_transcript_analysis_branch_attribution_structure.py`, `…/test_transcript_cli_bootstrap.py`, `…/test_transcript_package_module_prefixes.py`;
  - `docs/transcript-analysis-architecture.md`.
- **Also in the diff:** `.claude/plans/turn-shape-and-git-commands.md`.

Its verification runs from the worktree root:
- `scripts/venv-run.sh pytest` over:
  - the three new test files;
  - `test_transcript_analysis.py`, `test_transcript_analysis_branch_attribution_structure.py`, `test_transcript_cli_bootstrap.py`, `test_transcript_package_module_prefixes.py`, `test_transcript_analysis_architecture_doc.py`, `test_transcript_analysis_import_cycles.py`;
  - `test_transcript_cost.py`, `test_transcript_ledger_common.py`, and `test_transcript_period_cost.py`, the readers of retained shim bindings;
  - `claude/.claude/hooks/tests/test_nudge_error_mode_analysis.py`, which runs the real shim.
- `scripts/venv-run.sh ruff check claude/.claude/ claude-skills/ plugins/ evals/`, clean.
- `wc -l` on every new and changed file.

**Parent:** run Verification 0–1 before the dispatch, and Verification 3–9 after it, then `/code-review` and the commit. A review-round edit re-runs Verification 3, 5, 7, and 9 and the dispatch's test list above. It re-runs Verification 4 and 6 only when the round edits the shim or a package module.

**Rebase rule.** A rebase or merge that brings in a change under `claude/.claude/scripts`, `claude/.claude/hooks`, or `docs/transcript-analysis-architecture.md` invalidates every line range, snapshot, capture, and junit baseline. All of them are keyed to the pre-rebase `HEAD`. Verification 0 and 10 detect such a change.
- Before the commit: run the Abort procedure, then rebase, then restart at Verification 0 with a fresh `<scratch>`.
- After the commit: stop and report to the engineer.

**Abort procedure.** If the dispatch is interrupted, the code-writer cleans nothing, and the parent recovers:
1. `git status --short` must list only the eight modified files above, the eight created files, and the plan. On any other path, stop and report, touching nothing.
2. Restore each modified file with its own literal `git restore --source=HEAD -- <path>` call.
3. Delete each created file by its literal path.
4. Success: `git status --short` lists at most the plan.

Never use `git clean`, `git checkout .`, `git reset --hard`, or `git stash`.

**Explicitly unchanged:**
- `conftest.py`, `_move_fidelity.py`, `select-tests.py`, and `test_select_tests.py`;
- every other package module, and every line of `corpus.py` except :246;
- `test_transcript_analysis_branch_attribution.py` and `_session_signal_helpers.py`;
- `docs/transcript-analysis.md` and both skills, because the CLI is identical;
- every hook.

**Reuse:**
- `scope._add_project_scope_args`;
- the registrar shape at `duration.py:60-68`;
- conftest's `fake_projects`, `_write_jsonl`, `_asst`, `_bash_use`, `_priced`, `_skill_use`, `_table_cols`, `_tool_result`, and `_user_msg`;
- bootstrap's `_run`;
- `tests/_session_signal_helpers.py` as the helper-module shape;
- `_move_fidelity.py` unchanged.

**PR body carries:**
- each Verification result, with the harness counts;
- measured `wc -l` for every new or changed file, flagging the shim, the legacy file, and `test_transcript_cli_bootstrap.py` as over 1,000 lines and naming the bootstrap file as an F1 candidate (M8);
- the reflow count;
- that the engineer waived the revert rehearsal (row 27);
- the rollback statement: "A plain `git revert` of the squash commit applies cleanly and restores the pre-PR tree only while no later commit has touched a file this PR touches or read a name it adds (the four modules, their six registrars, `tests/_commit_gate_helpers.py`). The remaining decomposition phases edit the shim or the legacy test file, so once the next one merges, fix forward. Cross-phase reverts run last-in-first-out.";
- the re-stow instruction: after pulling, re-run `./install.sh` (or the installer `stow-packages.sh` lists). It is needed only when `scripts/transcript_analysis/` is not a folded directory symlink. Without it the shim raises `ModuleNotFoundError` at import and every subcommand fails. The PR-create gate `require-stow-reminder.sh` requires the body to name `install.sh` or `stow` `[verified: backend review read the hook]`;
- that no CHANGELOG entry is added, matching #1297, which added six modules without one;
- the follow-ups in Out of scope.

## Verification

Run everything from the worktree root. Notation:
- `<mf>` is `scripts/venv-run.sh python3 claude/.claude/scripts/tests/_move_fidelity.py`.
- `<pkg>` is `claude/.claude/scripts/transcript_analysis`, and `<tests>` is `claude/.claude/scripts/tests`.
- `<scratch>` is one `mktemp -d` directory, created before step 0. Use its printed path literally.
- `<attempt>` is a fresh `mktemp -d` directory for each dispatch attempt.

Run each git command and each capture as its own literal call. Seed files with the Write tool.

0. **Preconditions.**
   - `scripts/venv-run.sh python3 -c "__import__('tokenize').FSTRING_MIDDLE"`, `scripts/venv-run.sh pytest --version` and `scripts/venv-run.sh ruff --version` each exit 0 (row 30). Otherwise stop before the dispatch is paid for.
   - Check main drift, each as its own call: `git fetch origin main` (a non-zero exit stops the run, because a stale `origin/main` makes the next call print nothing), then `git diff --name-only HEAD...origin/main -- claude/.claude/scripts claude/.claude/hooks docs/transcript-analysis-architecture.md` (row 37). Any output invokes the Rebase rule.
   - `git status --short` shows nothing but the plan.
1. **Baseline, before the dispatch.**
   - Copy each of the eight files Parts 2–4 modify into `<scratch>`, each with its own plain `cp <path> <scratch>/before-<name>` call (`before-shim.py`, `before-legacy.py`, `before-corpus.py`, and so on). Create an empty `<scratch>/empty.py` with the Write tool.
   - **gh stub.** Write `<scratch>/gh-bin/gh`, then run `chmod 755 <scratch>/gh-bin/gh`. It is a `#!/bin/sh` script whose `case "$*" in` dispatches as follows:
     - `*"--head parity-git "*` prints `[{"number": 7}]`;
     - `*"--head parity-cmt "*` prints `[{"number": 8}]`;
     - `*"--head parity-err "*` writes `HTTP 404: Not Found` to stderr and exits 1;
     - any other `*"pr list "*` prints `[]`;
     - `*"issues/7/comments"*` prints `alice` and `bob` on two lines;
     - `*"pulls/7/comments"*` prints `alice`;
     - anything else writes `unexpected stub call` to stderr and exits 1.

     Git is absent from that PATH (row 25).
   - Seed `<scratch>/account/projects/-home-user-parityrepo/s1.jsonl`. Every record has `sessionId` `s1`, branch `parity-git`, a 2026-05-19 timestamp, and, on each assistant record, a distinct `requestId` and priced usage:
     - (r1) 10:00 user, string `"start the parity work"`, `permissionMode` `"acceptEdits"`;
     - (r2) 10:01 sonnet, one Skill tool_use `code-review`;
     - (r3) 10:02 sonnet, one Bash `ls`;
     - (r4) 10:03 sonnet, one Bash `cat README.md`;
     - (r5) 10:04 sonnet, one Bash `git commit -m wip`;
     - (r6) 10:05 sonnet, a Read and a Bash `ls`;
     - (r7) 10:06 opus, one Bash `FOO=1 git commit --no-verify -m x`;
     - (r8) 10:07 sonnet, no tool_use.

     Also seed `<scratch>/account/projects/-home-user-parityrepo/s1/subagents/agent-a1.jsonl` with one `isSidechain` sonnet record.
   - Write `<scratch>/runs.txt` with run-01 to run-21, in this order:
     - run-01 `turn-shape`
     - run-02 `turn-shape --since 3650d`
     - run-03 `turn-shape --this-repo --projects x`
     - run-04 `turn-shape-samples --seed 7`
     - run-05 `turn-shape-samples --seed 7 --sample 1`
     - run-06 `turn-shape-holdout-samples`
     - run-07 `turn-shape-holdout-samples --sample 0`
     - run-08 `turn-shape-holdout-samples --offset 99`
     - run-09 `turn-shape-holdout-samples --offset -1`
     - run-10 `commit-gate code-review`
     - run-11 `commit-gate code-review --by-permission-mode`
     - run-12 `commit-gate code-review --branches parity-git`
     - run-13 `commit-gate code-review --exclude-projects=*parityrepo`
     - run-14 `commit-gate`
     - run-15 `sessions --paths`
     - run-16 `sessions --paths --include-subagents`
     - run-17 `sessions`
     - run-18 `pr-link --repo owner/repo --branches parity-git,parity-nopr,parity-err,parity-cmt`
     - run-19 `pr-link --repo owner/repo --branches parity-git --author alice`
     - run-20 `pr-link --branches parity-git`
     - run-21 `pr-link`
   - Capture twice: `<mf> capture --script claude/.claude/scripts/transcript-analysis.py --account <scratch>/account --runs <scratch>/runs.txt --all-help --env PATH=<scratch>/gh-bin --out <scratch>/before-a`, then the same into `before-b`. Then `<mf> compare-captures <scratch>/before-a <scratch>/before-b` must be clean, with no mask. A difference means a nondeterministic run (row 26): stop and report.
   - Coverage checks on `before-a`:
     - It holds `help-top` plus one help capture and one `.parser` file per subcommand. `git grep -c "add_parser("` over the shim and `<pkg>` governs the count.
     - Each of the six moved commands' `.parser` files contains `default func=<callable <script>:cmd_<name>>`.
   - Corpus liveness: `before-a` must hold these tokens, each producible only by the seed:
     - `--- batching streak, length=4` and `--- delegation streak, length=2` in run-04;
     - `--- batching streak, length=1` in run-06;
     - `2026-W21` in run-10, and `acceptEdits` in run-11;
     - `No data found.` in run-13;
     - `s1.jsonl` in run-15, and `agent-a1.jsonl` in run-16;
     - in run-18, a `parity-git` row with `7`, a `none` cell, a `gh-err` cell, and `-1` cells;
     - `--repo` in run-20's stderr.

     If one is missing, fix the seed and recapture both sides.
   - **Multi-root.**
     - Seed `<scratch>/account2/projects/-home-user-foreignrepo/f1.jsonl` with one priced sonnet record carrying a Bash `ls`, on branch `foreign-only-probe`.
     - Write `<scratch>/roots.txt` holding the absolute path of `account2`.
     - Write `<scratch>/multi-runs.txt` with: `turn-shape`, `turn-shape-samples --seed 7`, `turn-shape-holdout-samples`, `commit-gate code-review`, `sessions --paths`, `pr-link --repo owner/repo --branches foreign-only-probe`. The two samples runs put their resolved-scope stderr headers in the compare, so all six moved commands have multi-root coverage. The foreign record's single Bash `ls` gives holdout one length-1 streak.
     - Capture twice, with `--env TRANSCRIPT_CONFIG_DIRS_FILE=<scratch>/roots.txt --env PATH=<scratch>/gh-bin`, into `multi-before-a` and `multi-before-b`. The two must compare clean.
     - Precondition: multi run-03 lists `f1.jsonl`.
   - **Junit baseline.** Run `scripts/venv-run.sh pytest` over `test_transcript_analysis.py`, `test_transcript_analysis_branch_attribution_structure.py`, `test_transcript_cli_bootstrap.py`, `test_transcript_package_module_prefixes.py`, and `test_transcript_analysis_architecture_doc.py`. Run it once with `-m "not timing" --junitxml=<scratch>/before-parallel.xml` and once with `-m timing -n0 --junitxml=<scratch>/before-timing.xml`. All tests pass.
2. **Dispatch** per Critical files, with a fresh `<attempt>`.
3. **Lint and sizes.**
   - `scripts/venv-run.sh ruff check claude/.claude/ claude-skills/ plugins/ evals/` is clean.
   - Record `wc -l` for every new and changed file. Every new file is at most 1,000 lines.
   - 3.11 evidence is an argument, not a parse check: `ast.parse(feature_version=(3, 11))` accepts PEP 701 f-string forms on 3.12 `[verified: backend review ran four such forms]`. The moved lines already run on 3.11, the rename map only prepends `module.` to a name, and the authored lines are docstrings, imports and registrar wrappers. Read the `git diff` of the four modules and confirm no added or changed line outside the verbatim moved text contains an f-string. If a 3.11 interpreter exists on the machine, `py_compile` the four modules with it. This environment has only 3.12.3, so otherwise record that the 3.11 compile did not run.
4. **Selected suite** (G4, M12). Run two passes, each as a Bash call with `run_in_background`, and start the second only after the first finishes:
   - `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py -m "not timing"`;
   - then `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py -m timing -n0`.

   Both must pass. Edit nothing until both finish. Before claiming or fixing a failure in a file outside Critical files, reproduce it at the merge-base, per CLAUDE.md:
   - Run `git merge-base HEAD origin/main` as its own call.
   - Run `git worktree add <scratch>/merge-base <sha>`.
   - Run the failing test there with `scripts/venv-run.sh pytest`, invoked from that worktree.
   - Run `git worktree remove <scratch>/merge-base`.
5. **Test-ID parity.**
   - Rerun step 1's two junit commands over those five files plus the three new test files, into `<attempt>/after-parallel.xml` and `<attempt>/after-timing.xml`.
   - `<mf> test-ids --before <scratch>/before-parallel.xml --before <scratch>/before-timing.xml --after <attempt>/after-parallel.xml --after <attempt>/after-timing.xml --file-prefix claude/.claude/scripts/tests/` must be clean, with one single-quoted `--expect-new` for each of the six new bootstrap IDs:
     - `test_transcript_analysis_git_and_sessions_help_exits_zero[<command>]` for pr-link, commit-gate, and sessions;
     - `test_transcript_analysis_git_and_sessions_subprocess_finds_seeded_session[<command>]` for the same three.

     No `--rename` is needed (row 16). If the dispatch returns any other set of bootstrap IDs, stop and report.
6. **CLI and parser parity.**
   - Capture into `<attempt>/after` and `<attempt>/multi-after` with step 1's flags.
   - Compare `before-a` with `<attempt>/after`, passing these six declarations:
     - `--moved-callable cmd_pr_link=transcript_analysis.pr_link`;
     - `cmd_commit_gate=transcript_analysis.commit_gate`;
     - `cmd_turn_shape=transcript_analysis.turn_shape`;
     - `cmd_turn_shape_samples=transcript_analysis.turn_shape`;
     - `cmd_turn_shape_holdout_samples=transcript_analysis.turn_shape`;
     - `cmd_sessions=transcript_analysis.sessions`.

     The compare must be clean.
   - The multi-root pair must compare clean.
   - No `.stdout`, `.stderr`, `.exit`, or `.argv` file may differ.
7. **Move fidelity.**
   - **Production nodes.**
     - Run `<mf> nodes --before <scratch>/before-shim.py` with one `--after` per new module and `--moved` set to the 22 names:
       - `_pr_link_gh_failure_kind _pr_link_report_gh_failure cmd_pr_link`;
       - `_GIT_COMMIT_RE _NO_VERIFY_RE cmd_commit_gate cmd_sessions`;
       - Part 1's 15 turn-shape names.
     - Pass `--verbatim` for the 13 names that read no renamed name: `_pr_link_report_gh_failure _GIT_COMMIT_RE _NO_VERIFY_RE _TURN_SHAPE_MUTATING_GIT_SUBCOMMANDS _TURN_SHAPE_ENV_ASSIGNMENT_TOKEN_RE _bash_command_is_mutating_git _TURN_SHAPE_CALL_COUNT_BUCKETS _TURN_SHAPE_STREAK_BUCKETS _turn_shape_call_count_bucket _turn_shape_streak_bucket _turn_shape_streaks _TURN_SHAPE_SAMPLES_MIN_STREAK_LEN _TURN_SHAPE_HOLDOUT_STREAK_LEN`.
     - Pass one `--rename` per entry of Part 1's 20-entry map.
     - It must be clean, with `compared-nodes: 22`.
   - **Shim remainder.**
     - Run `remainder --before <scratch>/before-shim.py --after <shim> --removed <the 22> --changed build_parser`.
     - Declare `--import-added` for the nine registrar and `cmd_*` triples.
     - Declare `--import-removed` for Part 2's removals.
     - It reports only `remainder-comments`, and each must match a `git diff` hunk that does one of these:
       - deletes a moved leading comment (:420-422, :2253-2256, :2265-2269, :2494-2497, :2500-2501);
       - deletes a banner (:2249-2251, :2622-2624);
       - rewrites the module-list comment (:21-26).

       Any other finding stops the run.
     - `git diff` of `build_parser` shows exactly the six blocks replaced by six calls. Check the import-line comments in `git diff` by eye.
   - **New modules.** For each new module, `remainder --before <scratch>/empty.py --after <module> --added __doc__ <its top-level names> --import-added <its triples>` is clean. The exceptions are the leading-comment `remainder-comments` findings: four in `turn_shape.py` and one in `commit_gate.py`.
   - **Test nodes.**
     - Run `nodes --before <scratch>/before-legacy.py`, with `--after` set to the three new test files, `_commit_gate_helpers.py`, and the final legacy file.
     - `--moved` the 13 names: `TestPrLink _gate_args TestCommitGate _turn_shape_args _turn_shape_samples_args _turn_shape_holdout_samples_args TestBashCommandIsMutatingGit TestTurnShapeBuckets TestTurnShapeSessionTurns TestTurnShapeStreaks TestCmdTurnShape TestTurnShapeSamples TestTurnShapeHoldoutSamples`.
     - `--verbatim` four names: `_gate_args _turn_shape_args _turn_shape_samples_args _turn_shape_holdout_samples_args`.
     - Pass Part 3's 16 renames.
     - It must be clean, with `compared-nodes: 13`.
   - **Other remainders.**
     - The legacy file, with `--removed <the 13>` and its import triples, reports only the three banner `remainder-comments`.
     - Each new test file and the helper module, compared against `empty.py`, is clean.
     - `remainder` is clean for:
       - `__init__.py` (`--changed __doc__`);
       - the prefix test (`--changed PRODUCTION_MODULES TEST_FILES`);
       - the structure test (`--changed _RAW_BY_DESIGN`);
       - bootstrap (`--added` its three new names).
     - `corpus.py` reports one `remainder-comments`, and `git diff` shows only :246 changed.
8. **Negative controls.** For each control:
   - run a plain `sha256sum <file>` first, and save its output line to `<attempt>/ctl-<x>.sha256` with the Write tool;
   - edit by hand, run the check, then undo by hand;
   - `sha256sum -c <attempt>/ctl-<x>.sha256` must then pass;
   - never use `git checkout` or `git stash`;
   - put each control capture in a fresh `<attempt>/ctl-<x>` directory.

   The controls:
   - (a) In `turn_shape.py`, bind `func=cmd_turn_shape_holdout_samples` inside the samples registrar. Exactly `help-turn-shape-samples.parser` and the run-04 and run-05 outputs differ, while the existing samples bootstrap test still passes. This demonstrates the swapped-binding gap row 31 names.
   - (b) In `turn_shape.py`, change the holdout registrar's `--offset` `default=0` to `1`. Exactly `help-turn-shape-holdout-samples.parser` and the outputs of run-06 and run-07 differ. `help-turn-shape-holdout-samples.stdout` stays equal.
   - The harness's own committed self-tests already pin `node-ast`, `moved-callable-unused` and `id-missing`, so this step adds no controls for them `[verified: SDET review read test_transcript_move_fidelity*.py]`.
   - (f) Two separate edits, each followed by a run of `test_transcript_cli_bootstrap.py` alone:
     - in `sessions.py`'s registrar, bind `func=lambda _args: None`; exactly the `sessions` dispatch ID fails;
     - delete `_add_commit_gate_subparser(sub)` from the shim's `build_parser`; both `commit-gate` IDs fail, and no other new ID does.
   - (g) Remove `"test_transcript_turn_shape.py"` from `TEST_FILES`. `test_loader_free_package_importing_test_files_are_in_test_files` fails.
9. **Leftovers and single homes.** Every `git grep` here passes `--untracked`, because the eight new files stay untracked until the commit. Each check that expects nothing is paired with a probe that must hit in the same paths.
   - `git grep --untracked -nE "^def (cmd_pr_link|cmd_commit_gate|cmd_turn_shape|cmd_turn_shape_samples|cmd_turn_shape_holdout_samples|cmd_sessions|_bash_command_is_mutating_git|_turn_shape_session_turns|_pr_link_gh_failure_kind)\b|^(_GIT_COMMIT_RE|_NO_VERIFY_RE|_TURN_SHAPE_MUTATING_GIT_SUBCOMMANDS)\b" -- claude/.claude/scripts` returns one hit per name, in its owning module.
   - `git grep --untracked -n "^def _gate_args" -- <tests>` returns one hit, in `_commit_gate_helpers.py`.
   - `git grep --untracked -nw "_mod" --` over the three new test files and the helper module returns nothing. Probe: `git grep --untracked -l "^from transcript_analysis import" --` over the three new test files lists all three.
   - `git grep --untracked -n "from _config_dir import" --` over the four new modules returns nothing. Probe: `^from transcript_analysis import` over the four new modules lists all four.
   - `git grep --untracked -nE "_mod\.(cmd_turn_shape|_turn_shape_|_TURN_SHAPE_|_bash_command_is_mutating_git|_GH_CALL_TIMEOUT_S)" -- <tests>` returns nothing. Probe: `git grep --untracked -n "turn_shape.cmd_turn_shape(" -- <tests>` hits.
   - `git grep --untracked -nE "_mod\.cmd_(pr_link|commit_gate|sessions)\b" -- <tests>` returns only row 7's sites.
   - `git grep -nE "still-unmigrated command-shape helper|the shim's own mutating-git|pr-link reaches bare from the shim" -- docs/transcript-analysis-architecture.md` returns nothing. Probe: `git grep -n "turn_shape.py" -- docs/transcript-analysis-architecture.md claude/.claude/scripts/transcript_analysis/corpus.py` hits both files.
   - `git grep -n "split_command_segments" -- claude/.claude/scripts/transcript-analysis.py` returns nothing. Probe: the same grep over `<pkg>/turn_shape.py` hits.
   - `git grep -n "transcript-analysis.py:" -- <tests>/test_transcript_analysis_branch_attribution_structure.py` returns nothing. Probe: both new keys are present.
   - Last, the parent stages the change with one literal `git add <path>` call per Critical file. Then `scripts/venv-run.sh pytest claude/.claude/tests/test_pytest_collection_config.py::TestNoBareSameDirectorySiblingImports claude/.claude/hooks/tests/test_ticket_reference_discipline.py` must pass. Both list files with `git ls-files`.
10. **Main re-check, before `/ready-for-review`.** Repeat step 0's `git fetch` and `git diff` calls. Any output invokes the Rebase rule's after-commit branch: stop and report that output to the engineer.

## Out of scope

- **commit-gate's stale "require-code-review.sh line 38" comment** (row 29). The drift predates this PR, and the comment moves verbatim so that `nodes --verbatim` stays a clean proof. Raise it as a follow-up.
- **Relocating the cross-group classes whose groups have all moved:** `TestPerRecordConsumersCreditPrefixToTheBranchEntered` and `TestMultiRootFormatOutliers` (rows 15, M7). `test_transcript_analysis_branch_attribution.py` is a candidate home, because its docstring covers pre-branch attribution. The move belongs to the phase that dissolves the legacy file.
- **Dedicated sessions unit tests** for the `--paths` refusal and `--include-subagents` emission (row 33). They are new tests, beyond a move. The bootstrap dispatch test pins `--paths`.
- **Strengthening the turn-shape-samples and holdout-samples bootstrap tokens** so that a swapped binding fails them (row 31). Raise it as a follow-up.
- **Splitting or consolidating `test_transcript_cli_bootstrap.py` below 1,000 lines,** for example into one parametrized help table. That rename touches existing test IDs, and F1's offender assessment owns the call (rows 20, 21; M8).
- **Splitting the shim or the legacy test file below 1,000 lines.** Both were already over the limit.
- **`_UNCONDITIONAL_HEADER_CASES` and its two classes, and `TestBuildParser`'s pr-link parse tests.** The `cli.py` phase owns them.
- **The governing plan's revert rehearsal.** The engineer waived it for this phase (row 27).
- **Any CLI, help-text, or output change, and public names for the registrars.**
- **Preserved records naming moved symbols** (docs/reports/, docs/case-studies/; row 13).