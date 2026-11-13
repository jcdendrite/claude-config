# Pin the scope-flag complement set and test `sessions --paths` through the parser

## Context

Goal: make a subcommand silently losing `--projects` or `--this-repo` fail a test by name, and cover plain `sessions --paths` in-process through the real parser, so a regression in either surface is caught without a subprocess.

Ask: "let's go with the architect's order" — the engineer's reply, in the prior session, to the `plan-architect` (`MODE=consult`) ordering of the three remaining GH-1432 follow-ups. The architect's first item is this change: a set-not-shrinking invariant test over the `--projects` / `--this-repo` commands, plus an in-process `cmd_sessions --paths` test.

Why now: PR 1460 merged and the follow-up order was accepted. No issue tracks these items; #1118, #1119, #1138 and #1157 were read this session and none covers them.

Today `tests/test_transcript_cli.py` pins only one side of the scope-flag walk. `test_subcommands_with_projects_but_no_this_repo_are_the_known_exemptions` pins the subcommands that have `--projects` without `--this-repo` as exactly `{"user-input"}`. `test_every_subcommand_with_both_scope_flags_refuses_them_together` checks only that four named subcommands (`cost`, `skill-invocation`, `skill-pair`, `subagents`) remain in the walked set, with a `<=` presence check. A subcommand that lost both flags, or a new one registered without them, passes unnoticed.

Enumerated from `cli.build_parser()` this session, the subcommands lacking at least one of the two flags are `user-input` (has `--projects`, lacks `--this-repo`) and `pr-cost-export`, `period-cost`, `friction-count` and `routing-guard` (neither flag).

Plain `sessions --paths` runs in-process today only through a hand-built `Namespace` (`tests/test_transcript_analysis_multi_root_scope.py:122-125`), which bypasses the parser's defaults, and through the parser only in a subprocess with `--include-subagents` (`tests/test_transcript_cli_bootstrap.py:641`). The existing `tests/test_transcript_sessions.py` already drives `build_parser().parse_args(["sessions"])` then `args.func(args)` for the missing-`--paths` refusal, so the new case belongs beside it. `.claude/rules/transcript-analysis-tests.md` puts one command's CLI wiring in the file of the module that registers its subparser. The same rule puts a walk across commands in `tests/test_transcript_cli.py`.

Intended outcome: the invariant pin lands in `tests/test_transcript_cli.py`, and the in-process `sessions --paths` test lands in `tests/test_transcript_sessions.py`. The handoff named `test_transcript_cli.py` for both; the placement rule moves the second.

## Approach

Pin every subcommand that lacks a scope flag in `tests/test_transcript_cli.py` as one module-level map from subcommand to the flags it lacks, assert that the registrar's map equals it, and run the exclusivity walk over every subcommand outside it. Add one parametrized test to `tests/test_transcript_sessions.py` that seeds two main sessions (one with a subagent transcript and its sidecar, one without a subagents directory) and an orphan subagent directory, runs `sessions --paths` through `cli.build_parser()` and `args.func(args)`, and asserts the exact stdout path list without and with `--include-subagents`.

Paths below are under `claude/.claude/scripts/`. A bare `:N` is a line, at `origin/main`, of the file its paragraph or bullet names.

**The scope-flag pin** (rows 1-7), in `tests/test_transcript_cli.py`.

A module-level constant lands after `test_read_cost_this_repo_runs_to_a_report_through_main` (ends :275) and before `class TestBuildParser:` (:278), with two blank lines on each side. It follows the file's one precedent, `_REFUSING_SUBCOMMANDS_WITHOUT_THEIR_OWN_CONFIG_DIR_FLAG` (:154-156):

```python
_SUBCOMMANDS_LACKING_A_SCOPE_FLAG = {
    "user-input": {"--this-repo"},
    "pr-cost-export": {"--projects", "--this-repo"},
    "period-cost": {"--projects", "--this-repo"},
    "friction-count": {"--projects", "--this-repo"},
    "routing-guard": {"--projects", "--this-repo"},
}
```

`test_subcommands_with_projects_but_no_this_repo_are_the_known_exemptions` (:293-297) is replaced by:

```python
    def test_subcommands_lacking_a_scope_flag_are_exactly_the_expected_set(self):
        """The exclusivity walk below skips these subcommands, so each one's missing scope flags are pinned exactly."""
        scope_flags = {"--projects", "--this-repo"}
        flags = self._subcommand_flags(cli.build_parser())
        lacking = {name: missing for name, opts in flags.items() if (missing := scope_flags - opts)}
        assert lacking == _SUBCOMMANDS_LACKING_A_SCOPE_FLAG
```

In `test_every_subcommand_with_both_scope_flags_refuses_them_together`, :304-308 (the filter, the three-line comment and the `<=` assert) become the two lines below. Its docstring (:300-302) and loop (:309-319) stay. `sorted` keeps the failure message's name order stable, since a set difference has none. The `assert` keeps the walk from running over nothing if the parser ever yields no subcommands.

```python
        both_flags = sorted(self._subcommand_flags(parser).keys() - _SUBCOMMANDS_LACKING_A_SCOPE_FLAG.keys())
        assert both_flags
```

The walked set is unchanged today (row 3). A subcommand outside the map that loses a flag now fails both tests by name. An exempt subcommand gaining a flag, or `user-input` losing `--projects`, fails the pin by name. On failure, pytest's rewritten dict comparison names each differing key and each key only one side holds (rows 5, 6). The file's precedent at :173-176 relies on the same rewriting with no message, and a custom message would restate the diff.

**Alternatives set aside for the pin:**
- A flat set of names in place of the map misses `user-input` losing `--projects`, since the name stays in the set.
- Pinning the subcommands that hold both flags misses a new subcommand registered with neither flag, the gap this change closes. It also fails on every safe new registration.
- Keeping the `user-input` test and adding a neither-flag test splits one fact across two literals, and a subcommand lacking only `--projects` still passes both.
- Keeping the walk's own filter beside the map derives one set twice. A typo in the filter would empty the walk with no failure.
- Keeping the `<=` check: its comment (:305-307) states its purpose as catching flag loss on four registrar paths, and the pin now covers every registrar. It also fails when one of the four is unregistered, which other files already catch (row 8).
- A count assertion on the walked set fails without naming the change.

**The `sessions --paths` test** (rows 9-16), in `tests/test_transcript_sessions.py`. :3 becomes `from transcript_analysis import cli, corpus`, and :5 becomes `from .conftest import _asst, _write_jsonl, _write_subagent_dispatch, _write_subagent_jsonl`. After :19 come two blank lines, then:

```python
@pytest.mark.parametrize(
    ("argv", "expected_relative_paths"),
    [
        (["sessions", "--paths"], ["sess.jsonl", "tail.jsonl"]),
        (
            ["sessions", "--paths", "--include-subagents"],
            ["sess.jsonl", "sess/subagents/agent-probe.jsonl", "tail.jsonl"],
        ),
    ],
    ids=["paths", "paths-include-subagents"],
)
def test_sessions_paths_emits_subagent_paths_only_under_include_subagents(
    fake_projects, capsys, argv, expected_relative_paths
):
    """--include-subagents prints direct subagent transcripts only, not their sidecars or workflow-nested files.

    That case shows the seeded subagent file would print, so the plain case cannot pass vacuously.
    The orphan subagent directory has no main transcript, so it must not print in either case.
    The tail session sorts after the session with subagents.
    It has no subagents directory, so it prints only its own transcript.
    """
    _write_jsonl(fake_projects / "sess.jsonl", [_asst("claude-sonnet-5")])
    _write_jsonl(fake_projects / "tail.jsonl", [_asst("claude-sonnet-5")])
    _write_subagent_dispatch(
        fake_projects, "sess", "agent-probe", "toolu_probe", [_asst("claude-sonnet-5", sidechain=True)]
    )
    _write_subagent_jsonl(fake_projects, "orphan", "agent-orphan", [_asst("claude-sonnet-5", sidechain=True)])
    workflow_dir = fake_projects / "sess" / corpus.SUBAGENT_SUBDIR / "workflows" / "wf_probe"
    workflow_dir.mkdir(parents=True)
    _write_jsonl(workflow_dir / "agent-nested.jsonl", [_asst("claude-sonnet-5", sidechain=True)])
    args = cli.build_parser().parse_args(argv)

    args.func(args)

    captured = capsys.readouterr()
    assert captured.out.splitlines() == [str(fake_projects / path) for path in expected_relative_paths]
    assert "SESSIONS SOURCES (*; " in captured.err
```

What each assertion catches:
- The plain case fails if `sessions.py:45`'s guard goes, or if the parser's `--include-subagents` default flips (`sessions.py:65-68`). The hand-built object at `tests/test_transcript_analysis_multi_root_scope.py:122-125` cannot see a flipped default.
- The exact stdout list also fails if the header moves to stdout (`sessions.py:42`).
- The stderr containment check pins the header to stderr and the `*` label in it, wherever it falls among other stderr output. It does not pin the parser's `--projects` default, because `scope.py:611` falls back to `*` when the default is `None`. The check does couple this file to `scope.py`'s `<COMMAND> SOURCES (<label>; ` header format. The rest of the header is `_root_count_desc`'s wording, already pinned elsewhere (row 12), and is left out so a change to it does not fail this file.
- The `.meta.json` sidecar beside the seeded subagent file sits in the real `subagents/` directory (`corpus.py:33`). The control case's exact list therefore fails if `sessions.py:48`'s `glob("*.jsonl")` widens to `glob("*")`.
- The oracle spells `subagents` literally while the seed, sidecar included, goes through `_write_subagent_dispatch` and `SUBAGENT_SUBDIR` (row 13). The control case therefore also fails if that constant drifts from the on-disk name.
- The orphan subagent directory has no main transcript. The control case fails if the subagent paths come from a scope-wide glob instead of from each emitted session.
- The workflow-nested file under `sess/subagents/workflows/wf_probe/` is not a direct child of `subagents/`, matching the reader's direct-children-only set (`tests/test_transcript_corpus.py:59-70`). The control case fails if `sessions.py:48`'s glob widens to a recursive pattern.
- The `tail.jsonl` session has no `subagents/` directory and sorts after `sess.jsonl`. The control case fails if the per-session derivation raises on a session without one, or carries the previous session's subagent paths into it.

**Alternatives set aside for the sessions test.** The control case is heavier than a single plain case, which passes vacuously if the seed misses the directory `cmd_sessions` reads. Lighter primitives checked for proving the seed:
- The subprocess test at `tests/test_transcript_cli_bootstrap.py:636-664` proves its own seed, built by `_seed_git_and_sessions_account` in another file, not this one.
- Asserting that the seeded file exists proves the file is there, not that `cmd_sessions` reads that directory.
- A temporary mutation of `sessions.py` to watch the test fail edits a file outside Critical files for a one-off proof. The control case proves the seed on every run.

Other drivers set aside:
- A hand-built `Namespace` bypasses the parser defaults, the gap this test closes.
- `cli.main()` with a patched `sys.argv`, as `tests/test_transcript_cli.py:262-275` does, adds `main()`'s top-level handling, which is not this command's wiring. The file's existing test drives `parse_args` then `args.func`.

**Placement** (rows 17, 18): the sessions test goes in `tests/test_transcript_sessions.py`, as G1 and row 17 argue and the engineer selected.

**Verification shape** (rows 20-24). `_move_fidelity.py test-ids` proves that a move kept every test ID and outcome. This change renames one test and adds two cases in about 50 authored lines, which `git diff` shows whole. A grep of the diff's added and removed `def test_` lines proves the ID set instead. A merge-base worktree is created only when `select-tests.py` reports a failure outside the two edited files.

**Assumption ledger.**

**Root:** `tests/test_transcript_cli.py` pins only the subcommands with `--projects` and without `--this-repo`. A subcommand that loses both scope flags, loses only `--projects`, or is registered with neither passes unnoticed, unless it is one of the four names the walk's `<=` check lists. Plain `sessions --paths` has no in-process test through the real parser. This change adds both pins and edits no production file. `[verified: read of tests/test_transcript_cli.py:287-319, tests/test_transcript_analysis_multi_root_scope.py:122-125, tests/test_transcript_cli_bootstrap.py:636-664]`

**Givens:**
- G1. `.claude/rules/transcript-analysis-tests.md` sets placement. One command's CLI wiring goes in the file of the module that registers its subparser, one package module's tests go in `tests/test_transcript_<module>.py`, and a walk across `build_parser()`'s subcommands goes in `tests/test_transcript_cli.py`. Reason: the rule file owns placement, and changing it is outside this plan. `[verified: read of the rule]`
- G2. Agents run `select-tests.py`, not the full suite, unless it selects the full suite itself. Reason: the repo `CLAUDE.md` § "Commands" sets it. `[verified: CLAUDE.md "Commands"]`
- G3. Some tests fail on this machine for environmental reasons, at the merge-base as on a branch, and a scoped `select-tests.py` run takes about 1h27m here. Reason: the machine's environment lies outside the repository. `[unverified: relayed by the dispatching session from the handoff]`

**Rows:**
1. Five subcommands lack a scope flag. `user-input` lacks `--this-repo`, since `transcript_analysis/user_input.py:359` registers `--projects` alone. `pr-cost-export`, `period-cost`, `friction-count` and `routing-guard` register neither (`transcript_analysis/cli.py:355-373` for `period-cost`). `[verified: the dispatching session's run of cli.build_parser() in this worktree, relayed; grep of friction_count.py, pr_cost_export.py, routing_guard.py and user_input.py for both flags and _add_project_scope_args; read of cli.py:355-373]`
2. `_subcommand_flags` (:287-291) maps each subcommand to its option strings from the real subparsers action, and has no caller outside :296 and :304. The known-exemptions test is :293-297. The walk is :299-319, with its filter at :304, a three-line comment at :305-307, the `<=` assert of four names at :308 and the loop at :309-319. `[verified: read of tests/test_transcript_cli.py:278-326; grep of claude/.claude/scripts]`
3. :304 selects the subcommands holding both flags. Row 1's five are exactly the ones lacking either, so the map's complement is the set :304 selects today. `[verified: rows 1 and 2]`
4. `tests/test_transcript_cli.py` already pins one subcommand set as a module-level constant, `_REFUSING_SUBCOMMANDS_WITHOUT_THEIR_OWN_CONFIG_DIR_FLAG` (:154-156). Its test (:173-176) asserts bare set equality with no message. It is the file's only module-level constant. `[verified: read; grep of the file's module-level assignments]`
5. pytest's assertion rewriting is on, since `pyproject.toml:24`'s `addopts` carries no `--assert` option. `[verified: read]`
6. At default verbosity, pytest's dict-equality explanation lists the differing items and the items only one side holds. `[verified: staff-sdet's plan-review run on a scratch test file under pytest 8.4.2, reported to the dispatching session]`
7. The old test name occurs only at its definition and in this plan. The walk's name occurs outside its file only in `.claude/plans/cli-subparser-move.md:142`, which cites the comment this change deletes. That plan is a preserved record. `[verified: grep of the repository]`
8. Each of the four names in the deleted `<=` check is still parsed or run elsewhere. `cost` and `skill-invocation` go through `parse_args` in `tests/test_transcript_cost.py` and `tests/test_transcript_skill_invocation.py`. `skill-pair` and `subagents` run in a subprocess at `tests/test_transcript_cli_bootstrap.py:463`, `:493`, `:1074` and `:1085`. `[verified: grep]`
9. `cmd_sessions` (`transcript_analysis/sessions.py:33-49`) exits 2 without `--paths` and prints each yielded session path. It prints `<session>/subagents/*.jsonl` paths only when `include_subagents` is true (:45-49), and prints the resolved-scope header to stderr (:42). `_add_sessions_subparser` (:52-69) registers the scope flags, `--paths` and `--include-subagents` as `store_true`, and `set_defaults(func=cmd_sessions)`. `[verified: read]`
10. On a single root, `_resolve_project_scope` reads `corpus.iter_sessions` (`transcript_analysis/scope.py:611-624`). That globs `<root>/<projects_glob>/*.jsonl` and yields only files with records (`transcript_analysis/corpus.py:212-217`). A subagent file two levels under the project directory is never yielded as a session, so only `sessions.py:45-49` can print it. `[verified: read]`
11. `fake_projects` (`tests/conftest.py:1525-1547`) points `scope.PROJECTS_DIR` at `tmp_path/"projects"` and returns its one project directory. The autouse `_isolate_transcript_corpus_lookups` (`tests/conftest.py:1630-1656`) points `TRANSCRIPT_CONFIG_DIRS_FILE` at a nonexistent path, so `resolve_scan_roots` (`scope.py:472-506`) returns that one root. `[verified: read]`
12. The header is `SESSIONS SOURCES (<label>; <root clause>)` (`scope.py:676`). On the single-root branch the label is the `--projects` value (`scope.py:611`, `:624`), which `_add_project_scope_args` defaults to `"*"` (`scope.py:517`). The root clause is `_root_count_desc`'s (`scope.py:635-657`), pinned by `tests/test_transcript_analysis_multi_root_scope.py:136`. `[verified: read]`
13. `_write_subagent_jsonl(proj, session_id, agent_id, records)` (`tests/conftest.py:781-787`) writes `<proj>/<session_id>/<SUBAGENT_SUBDIR>/<agent_id>.jsonl`, and `SUBAGENT_SUBDIR` is `"subagents"` (`corpus.py:13`). Outside `tests/conftest.py`, 25 test files call it on 122 lines. `_write_subagent_dispatch` (`tests/conftest.py:790-809`) calls it, then writes `<agent_id>.meta.json` into the same `SUBAGENT_SUBDIR` directory. `[verified: read; grep count]`
14. Plain `sessions --paths` runs in-process only through a hand-built object that sets `include_subagents` to `False` (`tests/test_transcript_analysis_multi_root_scope.py:122-125`). Through the parser it runs only in a subprocess with `--include-subagents`, seeded by `_seed_git_and_sessions_account` (`tests/test_transcript_cli_bootstrap.py:583-599`, `:636-664`). `[verified: read]`
15. `tests/test_transcript_sessions.py` is 19 lines with one test. It imports `pytest` and `cli`, imports `_asst` and `_write_jsonl` from `.conftest` at :5, and drives `cli.build_parser().parse_args([...])` then `args.func(args)`. `[verified: read]`
16. `cmd_sessions` prints paths built on `scope.PROJECTS_DIR` as given, so each printed line equals `str(fake_projects / <relative path>)`. `[verified: staff-sdet's plan-review run of the original test body through fake_projects, reported to the dispatching session; Verification step 3 reruns it on the final body]`
17. The sessions test's subjects fall under G1's first two bullets, both naming `tests/test_transcript_sessions.py`. `--include-subagents`'s default is the `sessions` command's CLI wiring, registered in `sessions.py:52-69`, and `cmd_sessions`'s guard is that module's code. The scope-flag pin walks every subcommand, so it falls under G1's third bullet. `[verified: read of the rule and sessions.py]`
18. The handoff named `tests/test_transcript_cli.py` for both tests. Putting the sessions test in `tests/test_transcript_sessions.py` is the dispatching session's reading of G1. Asked which placement they want, the engineer selected "Rule's placement (Recommended)". `[engineer-verified: "Rule's placement (Recommended)"]`
19. The Ask line's "let's go with the architect's order" is the engineer's reply in a prior session, relayed through the handoff. Asked in this session whether they said it and whether it covers this first PR, the engineer selected "Yes, I said it". `[engineer-verified: "Yes, I said it"]`
20. This diff's `select-tests.py` targets come from the `SCRIPTS_DIR` rule (`select-tests.py:639`), the ticket-reference row (:941), `_is_test_tree_packaging_change` (:589-595, :942) and `_is_test_source_change` (:604-610, :943). `.claude/plans/` maps to no tests (:645). `[verified: read]`
21. None of this diff's paths triggers a full-suite run. `[verified: staff-sdet's in-process call of select_pytest_targets on this diff's paths returned is_full_suite=False, reported to the dispatching session; Verification step 6 observes it]`
22. Both test files are tracked, so `test_ticket_reference_discipline.py`'s `git ls-files` corpus covers them with no staging step. `[verified: the session-start git status lists only this plan file as untracked]`
23. `_move_fidelity.py test-ids` compares per-test outcomes across junit runs, with `--rename` and `--expect-new` (`tests/_move_fidelity.py:9`, `:928-941`). `[verified: read]`
24. ruff runs with `line-length = 130` and the rules `E`, `F`, `B`, `I`, `UP` and `SIM` (`pyproject.toml:1-6`). Every prescribed line is under 130 columns. `[verified: read; column counts of the prescribed lines]`

**Mechanisms:**
- M1: The `_SUBCOMMANDS_LACKING_A_SCOPE_FLAG` map, and the renamed test asserting the registrar's map equals it. `anchors: root, row1, row2, row4, row7`. The alternatives are under "Alternatives set aside for the pin".
- M2: The walk runs over the map's complement, and the filter, comment and `<=` assert go. `anchors: row2, row3, row8`.
- M3: No custom assertion message, since pytest's dict diff names the change. `anchors: row4, row5, row6`.
- M4: One parametrized sessions test, with a plain case and an `--include-subagents` control case. `anchors: root, row9, row10, row14`. The lighter primitives checked are under "Alternatives set aside for the sessions test".
- M5: The `sess` seed goes through `_write_subagent_dispatch` and the orphan seed through `_write_subagent_jsonl`, stdout is compared as an exact list, and stderr only by containment of the header. `anchors: row11, row12, row13, row15, row16`.
- M6: Placement per G1. `anchors: row17, row18`.
- M7: Verification by the two files' collection and run, a grep of the diff's `def test_` lines and `select-tests.py`, with no `test-ids` proof. `anchors: row20, row21, row22, row23`. The conditional merge-base worktree is heavier than rerunning a failure in place. Lighter primitives checked:
  - Rerunning a failing ID on this tree cannot tell a pre-existing failure from one this change caused (G3).
  - `git stash` is unsafe here, because the stash stack is shared across worktrees.
- M8: One `code-writer` dispatch. `anchors: root, row20`. The reason is under Critical files.

## Critical files

**Dispatch: one `code-writer`, without a split.** The two files are disjoint. Both changes still share one `select-tests.py` run of about 1h27m on this machine (G3), which two dispatches would each repeat. Dispatch it with `model: sonnet` and without `isolation: "worktree"`. The brief requires:
- the code in Approach verbatim, with no other edit to either file;
- Verification steps 1-7, run by the agent, with each command's output and exit status in the return, including the `select-tests.py` summary, any failing node IDs, and their merge-base result;
- no commits.

**Modify:**
- `claude/.claude/scripts/tests/test_transcript_cli.py` (the new constant before :278, :293-297 and :304-308 only);
- `claude/.claude/scripts/tests/test_transcript_sessions.py` (:3, :5, and the new test after :19).

**Also in the diff:** `.claude/plans/projects-this-repo-invariant-test.md`.

**Reuse:** `fake_projects`, `_asst`, `_write_jsonl`, `_write_subagent_dispatch` and `_write_subagent_jsonl` from `tests/conftest.py`; `TestBuildParser._subcommand_flags`; `cli.build_parser()` and `corpus.SUBAGENT_SUBDIR`. No new helper or fixture.

**PR body requirement.** The PR body names the renamed test ID, old and new, so a reviewer comparing CI test lists reads the rename as intended.

Projected diff: about 70 lines. About 25 change in `test_transcript_cli.py` and about 45 in `test_transcript_sessions.py`.

## Verification

Run from the worktree root. `<tests>` is `claude/.claude/scripts/tests`.

1. `scripts/venv-run.sh ruff check claude/.claude/ claude-skills/ plugins/ evals/` exits 0.
2. `scripts/venv-run.sh pytest -n0 --collect-only -q <tests>/test_transcript_cli.py <tests>/test_transcript_sessions.py` lists IDs ending in each of these, and none ending in `test_subcommands_with_projects_but_no_this_repo_are_the_known_exemptions`:
   - `TestBuildParser::test_subcommands_lacking_a_scope_flag_are_exactly_the_expected_set`;
   - `TestBuildParser::test_every_subcommand_with_both_scope_flags_refuses_them_together`;
   - `test_sessions_paths_emits_subagent_paths_only_under_include_subagents[paths]`;
   - `test_sessions_paths_emits_subagent_paths_only_under_include_subagents[paths-include-subagents]`;
   - `test_sessions_without_paths_exits_2_and_emits_no_path`.
3. `scripts/venv-run.sh pytest <tests>/test_transcript_cli.py <tests>/test_transcript_sessions.py` exits 0 with no failure, which also proves row 16.
4. `git diff origin/main -- <tests>/test_transcript_cli.py <tests>/test_transcript_sessions.py | grep -E '^[+-][[:space:]]*def test_'` prints exactly three lines: the removed `test_subcommands_with_projects_but_no_this_repo_are_the_known_exemptions`, the added `test_subcommands_lacking_a_scope_flag_are_exactly_the_expected_set`, and the added `test_sessions_paths_emits_subagent_paths_only_under_include_subagents`.
5. Scope of the diff:
   - `git diff origin/main --name-only -- claude/` prints exactly the two test files.
   - `git diff origin/main --numstat -- claude/` totals near 70 changed lines.
   - `git grep -n test_subcommands_with_projects_but_no_this_repo -- ':!.claude/plans/'` prints nothing.
6. `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py`. Its stderr names `claude/.claude/scripts/tests` among its targets and does not report the full suite (rows 20, 21). Then:
   - These pass, including their cases for both edited files: `claude/.claude/hooks/tests/test_ticket_reference_discipline.py`'s `test_no_ticket_prefixed_identifier` and `test_no_duplicate_top_level_test_name_within_a_file` (row 22), and `claude/.claude/tests/test_pytest_collection_config.py`'s `TestNoBareSameDirectorySiblingImports`.
   - A failure in either edited file blocks.
   - For any other failing node ID, apply the sub-steps below in order, per `CLAUDE.md`'s "Prove your change caused a failing check":
     1. `git worktree add --detach <scratch>/base <base>`. `<scratch>` is a new directory outside the repository from `mktemp -d`, and `<base>` is the SHA `git merge-base HEAD origin/main` prints.
     2. With `<scratch>/base` as the working directory, rerun those IDs with `scripts/venv-run.sh pytest <id>...`.
     3. Compare node IDs, not counts (G3).
     4. An ID that fails only on this tree blocks.
7. If step 6 created `<scratch>/base`, run `git worktree remove <scratch>/base` on every exit, a blocked run included, and state in the return whether it was removed.

## Out of scope

- **The other GH-1432 follow-ups:** the four stale plan citations and the in-process round-trip tests of each registrar. Each is its own change.
- **Changing which subcommands carry scope flags**, such as giving `user-input` `--this-repo`. The map records today's registrations and changes none.
- **Pinning that `cost`, `skill-invocation`, `skill-pair` and `subagents` stay registered.** The deleted `<=` check did so only incidentally, and other files already parse or run each one (row 8).
- **The hand-built `sessions` case at `tests/test_transcript_analysis_multi_root_scope.py:122-125` and the subprocess case at `tests/test_transcript_cli_bootstrap.py:636-664`.** Their subjects are the header across subcommands and fresh-interpreter dispatch, and both stay.
- **Pinning the order `--paths` emits subagent paths in.** The seed holds one subagent transcript, so dropping the `sorted()` at `sessions.py:48` passes. Order is not part of this change's subject.
- **`test_every_projects_registration_across_every_subcommand_has_no_argparse_type` (:352-359).** It has the same filter-then-`<=` shape, and was looked at. It filters on `action.dest == "projects"`, not the option string, and its `<=` keeps that walk from running empty, so the pin does not cover it and it stays.
- **The walk's docstring (:300-302).** It stays accurate, because a subcommand registered later still lands in the walk unless it is added to the map.
- **Mentions of the old test layout in `.claude/plans/*.md`**, `.claude/plans/cli-subparser-move.md:142` included. They are preserved records (`CLAUDE.md` §Working Style "Scope discipline", Axis 3).
