"""Tests for _lib.sh's _lib_sum_numstat_changed_lines, _lib_diff_size_measure and
_lib_diff_size_gate_state. The latch path helper is covered with the other
branch-state path helpers in test_lib_reviewer_round_state.py.

Every repository is built against a bare `origin` whose default branch is `trunk`, so a hard-coded `main` base
fails. The one exception is the default-branch-guess case, which uses `main` because the guess probes only `main`,
`master` and `develop`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from helpers import (
    _make_git_recording_argv,
    _run_git,
    absolute_git_dir,
    bare_remote_with_default_branch,
    build_path_without,
    git_toplevel,
    isolated_git_config_env,
    path_with_failing_tool_shim,
    push_conflicting_edit_to_origin,
    reviewer_round_state_key,
)

from ._lib_sh_helpers import _forge_state_marker, _gh_shim_dir, _run_lib_call

# The seed commit's file on `trunk`, from bare_remote_with_default_branch's default.
SEED_FILE = "f"


@pytest.fixture(autouse=True)
def isolated_git_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the host's git config out of fixture building and the measured calls."""
    for name, value in isolated_git_config_env(tmp_path / "home").items():
        monkeypatch.setenv(name, value)


def _lines(prefix: str, count: int) -> str:
    """`count` distinct lines, so a deleted line never equals an added one."""
    return "".join(f"{prefix}-{i}\n" for i in range(count))


def _stage_file(repo: Path, relative_path: str, content: str | bytes) -> None:
    target = repo / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        target.write_bytes(content)
    else:
        target.write_text(content)
    _run_git(repo, "add", "--", relative_path)


def _commit_staged(repo: Path) -> None:
    _run_git(repo, "commit", "-q", "-m", "work")


def _clone_with_trunk_files(tmp_path: Path, trunk_files: dict[str, str] | None = None) -> tuple[Path, Path]:
    """Bare origin and a clone on a new `feature` branch. `trunk_files` are committed and pushed to
    `trunk` first, so they exist at the branch point."""
    bare, clone = bare_remote_with_default_branch(tmp_path, branch="trunk", file_name=SEED_FILE)
    if trunk_files:
        for relative_path, content in trunk_files.items():
            _stage_file(clone, relative_path, content)
        _commit_staged(clone)
        _run_git(clone, "push", "-q", "origin", "trunk")
    _run_git(clone, "checkout", "-q", "-b", "feature")
    return bare, clone


def _measure(
    repo: Path, branch: str = "feature", env_overrides: dict[str, str] | None = None, call_prefix: str = ""
) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "TEST_REPO_ROOT": git_toplevel(repo),
        "TEST_GITDIR": str(absolute_git_dir(repo)),
        "TEST_BRANCH": branch,
        **(env_overrides or {}),
    }
    return _run_lib_call(f'{call_prefix}_lib_diff_size_measure "$TEST_REPO_ROOT" "$TEST_GITDIR" "$TEST_BRANCH"', env=env)


def _origin_head_resolves(repo: Path) -> bool:
    return subprocess.run(
        ["git", "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"], cwd=repo, capture_output=True
    ).returncode == 0


def _numstat_total(numstat_text: str) -> int:
    total = 0
    for row in numstat_text.splitlines():
        added, deleted, _path = row.split("\t", 2)
        if added != "-":
            total += int(added) + int(deleted)
    return total


def _diff_tree_total_after_commit(repo: Path) -> int:
    merge_base = _run_git(repo, "merge-base", "origin/trunk", "HEAD").strip()
    return _numstat_total(_run_git(repo, "diff-tree", "-r", "-M", "--numstat", merge_base, "HEAD"))


# --- _lib_sum_numstat_changed_lines ------------------------------------


class TestLibSumNumstatChangedLines:
    @pytest.mark.parametrize(
        ("numstat_text", "expected_total"),
        [
            pytest.param("", "0", id="empty input prints 0"),
            pytest.param("3\t4\tf\n", "7", id="one row sums its two columns"),
            pytest.param("1\t2\ta\n10\t20\tb\n5\t0\tc\n", "38", id="several rows sum every row"),
            pytest.param("-\t-\tlogo.png\n", "0", id="a binary row adds 0"),
            pytest.param("3\t3\told.txt => new.txt\n", "6", id="a rename row adds its two numbers"),
            pytest.param("3\t3\tdir/{a => b}/c\n", "6", id="a braced rename row adds its two numbers"),
            pytest.param("x\t4\tf\n", "0", id="a non-numeric added column adds 0"),
            pytest.param("4\ty\tf\n", "0", id="a non-numeric deleted column adds 0"),
        ],
    )
    def test_prints_the_sum_of_the_numeric_rows(self, numstat_text: str, expected_total: str) -> None:
        env = {**os.environ, "TEST_NUMSTAT": numstat_text}
        result = _run_lib_call('printf "%s" "$TEST_NUMSTAT" | _lib_sum_numstat_changed_lines', env=env)
        assert result.returncode == 0, result.stderr
        assert result.stdout == f"{expected_total}\n"


# --- _lib_diff_size_measure --------------------------------------------


class TestLibDiffSizeMeasureCounts:
    def test_counts_added_plus_deleted_lines_of_a_modified_file(self, tmp_path: Path) -> None:
        _bare, clone = _clone_with_trunk_files(tmp_path, {"mod.txt": _lines("old", 200)})
        _stage_file(clone, "mod.txt", _lines("new", 200))

        result = _measure(clone)

        assert (result.returncode, result.stdout) == (0, "400 trunk\n"), result.stderr

    def test_a_branch_with_no_changes_counts_0(self, tmp_path: Path) -> None:
        _bare, clone = _clone_with_trunk_files(tmp_path)

        result = _measure(clone)

        assert (result.returncode, result.stdout) == (0, "0 trunk\n"), result.stderr

    def test_committed_plus_staged_lines_count_the_same_before_and_after_the_commit(self, tmp_path: Path) -> None:
        _bare, clone = _clone_with_trunk_files(tmp_path)
        _stage_file(clone, "committed.txt", _lines("committed", 300))
        _commit_staged(clone)
        _stage_file(clone, "staged.txt", _lines("staged", 100))

        before_commit = _measure(clone)
        _commit_staged(clone)
        after_commit = _measure(clone)

        assert before_commit.stdout == "400 trunk\n", before_commit.stderr
        assert after_commit.stdout == "400 trunk\n", after_commit.stderr
        assert _diff_tree_total_after_commit(clone) == 400

    def test_a_deletion_a_rename_with_edits_and_a_binary_file_count_as_the_reviewed_diff_does(self, tmp_path: Path) -> None:
        # Without `-M` the rename would count the whole file twice, so this constant also pins rename detection.
        expected_count = 50 + (3 + 3) + 7
        renamed_lines = _lines("ren", 40)
        _bare, clone = _clone_with_trunk_files(tmp_path, {"gone.txt": _lines("gone", 50), "ren.txt": renamed_lines})
        _run_git(clone, "rm", "-q", "--", "gone.txt")
        _run_git(clone, "mv", "ren.txt", "moved.txt")
        edited = renamed_lines.splitlines(keepends=True)
        edited[0:3] = [f"edited-{i}\n" for i in range(3)]
        _stage_file(clone, "moved.txt", "".join(edited))
        _stage_file(clone, "added.txt", _lines("added", 7))
        _stage_file(clone, "logo.bin", b"\x00\x01\x02binary\x00" * 64)

        before_commit = _measure(clone)
        _commit_staged(clone)
        after_commit = _measure(clone)

        assert before_commit.stdout == f"{expected_count} trunk\n", before_commit.stderr
        assert after_commit.stdout == f"{expected_count} trunk\n", after_commit.stderr
        assert _diff_tree_total_after_commit(clone) == expected_count

    def test_a_pure_rename_of_a_300_line_file_counts_0(self, tmp_path: Path) -> None:
        _bare, clone = _clone_with_trunk_files(tmp_path, {"big.txt": _lines("big", 300)})
        _run_git(clone, "mv", "big.txt", "renamed.txt")

        result = _measure(clone)

        assert (result.returncode, result.stdout) == (0, "0 trunk\n"), result.stderr

    @pytest.mark.parametrize(
        "gitattributes",
        [
            pytest.param("*.dat -diff\n", id="diff attribute unset"),
            pytest.param("*.dat binary\n", id="binary attribute"),
        ],
    )
    def test_a_staged_text_file_git_treats_as_binary_by_attribute_adds_0_beside_a_counted_file(
        self, tmp_path: Path, gitattributes: str
    ) -> None:
        _bare, clone = _clone_with_trunk_files(tmp_path, {".gitattributes": gitattributes})
        _stage_file(clone, "payload.dat", _lines("payload", 10))
        _stage_file(clone, "plain.txt", _lines("plain", 7))

        result = _measure(clone)

        assert (result.returncode, result.stdout) == (0, "7 trunk\n"), result.stderr

    def test_counts_only_the_staged_lines_not_unstaged_edits_or_untracked_files(self, tmp_path: Path) -> None:
        _bare, clone = _clone_with_trunk_files(tmp_path)
        _stage_file(clone, "staged.txt", _lines("staged", 10))
        (clone / SEED_FILE).write_text(_lines("unstaged", 30))
        (clone / "untracked.txt").write_text(_lines("untracked", 25))

        result = _measure(clone)

        assert (result.returncode, result.stdout) == (0, "10 trunk\n"), result.stderr

    def test_counts_from_the_merge_base_not_the_base_tip(self, tmp_path: Path) -> None:
        bare, clone = _clone_with_trunk_files(tmp_path)
        _stage_file(clone, "work.txt", _lines("work", 12))
        push_conflicting_edit_to_origin(tmp_path, bare, "unrelated.txt", _lines("unrelated", 500), branch="trunk")
        _run_git(clone, "fetch", "-q", "origin")

        result = _measure(clone)

        assert (result.returncode, result.stdout) == (0, "12 trunk\n"), result.stderr


class TestLibDiffSizeMeasurePlanExclusion:
    @pytest.mark.parametrize(
        "env_overrides",
        [
            pytest.param({}, id="default environment"),
            pytest.param({"GIT_LITERAL_PATHSPECS": "1"}, id="inherited GIT_LITERAL_PATHSPECS"),
        ],
    )
    def test_leaves_out_only_files_under_the_repository_roots_plans_directory(
        self, tmp_path: Path, env_overrides: dict[str, str]
    ) -> None:
        _bare, clone = _clone_with_trunk_files(tmp_path)
        _stage_file(clone, ".claude/plans/root-plan.md", _lines("root-plan", 50))
        _stage_file(clone, ".claude/plans-old/x.md", _lines("plans-old", 3))
        _stage_file(clone, "sub/.claude/plans/x.md", _lines("nested-plan", 4))
        _stage_file(clone, "code.txt", _lines("code", 7))

        result = _measure(clone, env_overrides=env_overrides)

        assert (result.returncode, result.stdout) == (0, "14 trunk\n"), result.stderr

    def test_a_plan_file_moved_out_of_the_plans_directory_counts_as_a_whole_file_add(self, tmp_path: Path) -> None:
        _bare, clone = _clone_with_trunk_files(tmp_path, {".claude/plans/moved.md": _lines("moved", 30)})
        _run_git(clone, "mv", ".claude/plans/moved.md", "moved.md")

        result = _measure(clone)

        assert (result.returncode, result.stdout) == (0, "30 trunk\n"), result.stderr


class TestLibDiffSizeMeasureDefaultBranch:
    def test_a_clone_without_origin_head_measures_against_the_guessed_main(self, tmp_path: Path) -> None:
        _bare, clone = bare_remote_with_default_branch(tmp_path, branch="main", file_name=SEED_FILE)
        _run_git(clone, "checkout", "-q", "-b", "feature")
        _stage_file(clone, "work.txt", _lines("work", 5))
        assert _origin_head_resolves(clone)
        _run_git(clone, "remote", "set-head", "origin", "-d")
        assert not _origin_head_resolves(clone)

        result = _measure(clone)

        assert (result.returncode, result.stdout) == (0, "5 main\n"), result.stderr


class TestLibDiffSizeMeasureStackBase:
    def test_a_pushed_parent_with_a_slashed_name_is_the_base_and_later_parent_commits_do_not_change_the_count(
        self, tmp_path: Path
    ) -> None:
        _bare, clone = _clone_with_trunk_files(tmp_path)
        _run_git(clone, "checkout", "-q", "-b", "feat/parent", "trunk")
        _stage_file(clone, "parent.txt", _lines("parent", 30))
        _commit_staged(clone)
        _run_git(clone, "push", "-q", "origin", "feat/parent")
        _run_git(clone, "checkout", "-q", "feature")
        _run_git(clone, "reset", "-q", "--hard", "feat/parent")
        _run_git(clone, "config", "branch.feature.gh-merge-base", "feat/parent")
        _stage_file(clone, "child.txt", _lines("child", 5))
        _commit_staged(clone)

        before_parent_moves = _measure(clone)
        # push_conflicting_edit_to_origin advances only the default branch, so the parent moves in the clone.
        _run_git(clone, "checkout", "-q", "feat/parent")
        _stage_file(clone, "parent-later.txt", _lines("parent-later", 100))
        _commit_staged(clone)
        _run_git(clone, "push", "-q", "origin", "feat/parent")
        _run_git(clone, "checkout", "-q", "feature")
        after_parent_moves = _measure(clone)

        assert before_parent_moves.stdout == "5 feat/parent\n", before_parent_moves.stderr
        assert after_parent_moves.stdout == "5 feat/parent\n", after_parent_moves.stderr


def _toggle_stack_key_names_an_unpushed_parent(clone: Path, tmp_path: Path) -> tuple[str, dict[str, str]]:
    _run_git(clone, "config", "branch.feature.gh-merge-base", "never-pushed")
    return "feature", {}


def _toggle_stack_key_names_an_origin_ref(clone: Path, tmp_path: Path) -> tuple[str, dict[str, str]]:
    _run_git(clone, "config", "branch.feature.gh-merge-base", "origin/trunk")
    return "feature", {}


def _toggle_head_is_on_the_base_branch(clone: Path, tmp_path: Path) -> tuple[str, dict[str, str]]:
    _run_git(clone, "checkout", "-q", "trunk")
    return "trunk", {}


def _toggle_origin_remote_removed(clone: Path, tmp_path: Path) -> tuple[str, dict[str, str]]:
    _run_git(clone, "remote", "remove", "origin")
    return "feature", {}


def _toggle_no_common_ancestor_with_the_base(clone: Path, tmp_path: Path) -> tuple[str, dict[str, str]]:
    # origin/trunk still resolves, so only the merge-base call can refuse.
    _run_git(clone, "checkout", "-q", "--orphan", "unrelated")
    _commit_staged(clone)
    return "unrelated", {}


def _toggle_merge_in_progress(clone: Path, tmp_path: Path) -> tuple[str, dict[str, str]]:
    head_oid = _run_git(clone, "rev-parse", "HEAD").strip()
    _forge_state_marker(absolute_git_dir(clone), "merge", head_oid)
    return "feature", {}


def _toggle_diff_index_fails(clone: Path, tmp_path: Path) -> tuple[str, dict[str, str]]:
    return "feature", {"PATH": path_with_failing_tool_shim(tmp_path / "failing-diff-index-bin", "git", "diff-index")}


class TestLibDiffSizeMeasureRefusals:
    @pytest.mark.parametrize(
        "toggle",
        [
            pytest.param(_toggle_stack_key_names_an_unpushed_parent, id="stack key names a parent never pushed"),
            pytest.param(_toggle_stack_key_names_an_origin_ref, id="stack key is unusable"),
            pytest.param(_toggle_head_is_on_the_base_branch, id="branch equals the base"),
            pytest.param(_toggle_origin_remote_removed, id="origin remote removed"),
            pytest.param(_toggle_no_common_ancestor_with_the_base, id="no common ancestor with the base"),
            pytest.param(_toggle_merge_in_progress, id="merge paused"),
            pytest.param(_toggle_diff_index_fails, id="count call fails"),
        ],
    )
    def test_returns_1_with_no_stdout(self, tmp_path: Path, toggle) -> None:
        _bare, clone = _clone_with_trunk_files(tmp_path)
        _stage_file(clone, "work.txt", _lines("work", 5))
        untoggled = _measure(clone)
        assert untoggled.stdout == "5 trunk\n", untoggled.stderr

        branch, env_overrides = toggle(clone, tmp_path)
        result = _measure(clone, branch=branch, env_overrides=env_overrides)

        assert (result.returncode, result.stdout) == (1, "")

    def test_a_stack_key_naming_an_unpushed_parent_returns_1_with_empty_stdout_and_stderr(self, tmp_path: Path) -> None:
        # The failing merge-base call prints `fatal:` text that only its `2>/dev/null` in _lib_diff_size_measure discards.
        _bare, clone = _clone_with_trunk_files(tmp_path)
        _stage_file(clone, "work.txt", _lines("work", 5))
        untoggled = _measure(clone)
        assert untoggled.stdout == "5 trunk\n", untoggled.stderr
        branch, env_overrides = _toggle_stack_key_names_an_unpushed_parent(clone, tmp_path)

        result = _measure(clone, branch=branch, env_overrides=env_overrides)

        assert (result.returncode, result.stdout, result.stderr) == (1, "", "")

    def test_a_corrupt_index_returns_1_with_empty_stdout_and_stderr(self, tmp_path: Path) -> None:
        # The failing diff-index call prints `fatal:` text that only its `2>/dev/null` in _lib_diff_size_measure discards.
        _bare, clone = _clone_with_trunk_files(tmp_path)
        _stage_file(clone, "work.txt", _lines("work", 5))
        untoggled = _measure(clone)
        assert untoggled.stdout == "5 trunk\n", untoggled.stderr
        (absolute_git_dir(clone) / "index").write_bytes(b"not a git index")

        result = _measure(clone)

        assert (result.returncode, result.stdout, result.stderr) == (1, "", "")

    def test_an_empty_changed_line_sum_returns_1_with_no_stdout(self, tmp_path: Path) -> None:
        _bare, clone = _clone_with_trunk_files(tmp_path)
        _stage_file(clone, "work.txt", _lines("work", 5))
        untoggled = _measure(clone)
        assert untoggled.stdout == "5 trunk\n", untoggled.stderr
        farm_dir = tmp_path / "path-without-awk"
        farm_dir.mkdir()

        result = _measure(clone, env_overrides={"PATH": build_path_without("awk", farm_dir)})

        assert (result.returncode, result.stdout) == (1, "")

    def test_unreadable_in_progress_state_returns_1_with_no_stdout(self, tmp_path: Path) -> None:
        # An empty gitdir makes _lib_git_inprogress_state resolve it itself, and the shim fails that one call.
        _bare, clone = _clone_with_trunk_files(tmp_path)
        _stage_file(clone, "work.txt", _lines("work", 5))
        untoggled = _measure(clone, env_overrides={"TEST_GITDIR": ""})
        assert untoggled.stdout == "5 trunk\n", untoggled.stderr
        failing_gitdir_read = {"PATH": path_with_failing_tool_shim(tmp_path / "failing-gitdir-bin", "git", "--absolute-git-dir")}

        result = _measure(clone, env_overrides={"TEST_GITDIR": "", **failing_gitdir_read})

        assert (result.returncode, result.stdout) == (1, "")

    def test_an_unusable_stack_key_writes_its_one_stderr_line(self, tmp_path: Path) -> None:
        _bare, clone = _clone_with_trunk_files(tmp_path)
        _stage_file(clone, "work.txt", _lines("work", 5))
        _toggle_stack_key_names_an_origin_ref(clone, tmp_path)

        result = _measure(clone)

        assert result.returncode == 1
        assert result.stderr.startswith("diff-size: branch.feature.gh-merge-base is origin/trunk"), result.stderr
        assert len(result.stderr.splitlines()) == 1, result.stderr


class TestLibDiffSizeMeasureCountCap:
    def test_the_sourced_cap_exceeds_the_default_cap(self) -> None:
        result = _run_lib_call('printf "%s\\n" "$_LIB_DIFF_SIZE_COUNT_CAP_SECONDS"', env=dict(os.environ))

        assert result.returncode == 0, result.stderr
        assert int(result.stdout) > 5  # _lib_capped's cap

    def test_a_count_call_that_outlives_the_cap_is_killed_and_the_commit_is_unmeasured(self, tmp_path: Path) -> None:
        if not shutil.which("timeout") and not shutil.which("gtimeout"):
            pytest.skip("neither timeout(1) nor gtimeout(1) available — BSD/macOS without coreutils")
        real_git = shutil.which("git")
        assert real_git is not None
        _bare, clone = _clone_with_trunk_files(tmp_path)
        _stage_file(clone, "work.txt", _lines("work", 5))
        bin_dir = tmp_path / "stalling-diff-index-bin"
        call_log = tmp_path / "git-calls.log"
        # `exec` so the stalled process is the one the cap signals; a forked sleep would hold the
        # command substitution open past the cap.
        _make_git_recording_argv(bin_dir, call_log, action='case "$*" in *diff-index*) exec sleep 4 ;; esac')
        env_overrides = {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "REAL_GIT": real_git}

        result = _measure(clone, env_overrides=env_overrides, call_prefix="_LIB_DIFF_SIZE_COUNT_CAP_SECONDS=1; ")

        assert (result.returncode, result.stdout) == (1, "")
        assert any("diff-index" in call for call in call_log.read_text().splitlines())


class TestLibDiffSizeMeasureLazyFetch:
    def test_the_count_call_runs_with_lazy_fetch_disabled(self, tmp_path: Path) -> None:
        real_git = shutil.which("git")
        assert real_git is not None
        _bare, clone = _clone_with_trunk_files(tmp_path)
        _stage_file(clone, "work.txt", _lines("work", 5))
        bin_dir = tmp_path / "lazy-fetch-recording-bin"
        argv_log = tmp_path / "git-calls.log"
        lazy_fetch_log = tmp_path / "lazy-fetch-values.log"
        _make_git_recording_argv(
            bin_dir,
            argv_log,
            action=f'case "$*" in *diff-index*) printf "%s\\n" "$GIT_NO_LAZY_FETCH" >> "{lazy_fetch_log}" ;; esac',
        )
        # An ambient `1` would satisfy the assertion without the export under test.
        env_overrides = {
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "REAL_GIT": real_git,
            "GIT_NO_LAZY_FETCH": "0",
        }

        result = _measure(clone, env_overrides=env_overrides)

        assert (result.returncode, result.stdout) == (0, "5 trunk\n"), result.stderr
        assert lazy_fetch_log.read_text().splitlines() == ["1"]


class TestLibDiffSizeMeasureNeverRunsGh:
    def test_a_measured_call_never_runs_gh(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # The shim records GH_ENTERPRISE_TOKEN, so no ambient credential may reach it.
        for ambient_name in [key for key in os.environ if key.startswith(("GH_", "GITHUB_"))]:
            monkeypatch.delenv(ambient_name)
        _bare, clone = _clone_with_trunk_files(tmp_path)
        _stage_file(clone, "work.txt", _lines("work", 5))
        gh_call_log = tmp_path / "gh-calls.jsonl"
        env_overrides = {"PATH": f"{_gh_shim_dir(tmp_path, gh_call_log)}{os.pathsep}{os.environ['PATH']}"}

        result = _measure(clone, env_overrides=env_overrides)

        assert result.stdout == "5 trunk\n", result.stderr
        assert not gh_call_log.exists()
        # Premise: the shim does record a call made in the same environment.
        subprocess.run(["bash", "-c", "gh pr view"], env={**os.environ, **env_overrides}, check=True)
        assert gh_call_log.exists()


# --- _lib_diff_size_gate_state -----------------------------------------


def _gate_state(
    repo: Path,
    tmp_path: Path,
    start_dir: Path | str | None = None,
    env_overrides: dict[str, str] | None = None,
    process_cwd: Path | None = None,
) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "CLAUDE_CONFIG_DIR": str(tmp_path / "cfg"),
        "TEST_PROCESS_CWD": str(process_cwd) if process_cwd is not None else git_toplevel(repo),
        "TEST_START_DIR": str(start_dir if start_dir is not None else repo),
        **(env_overrides or {}),
    }
    return _run_lib_call('cd "$TEST_PROCESS_CWD" && _lib_diff_size_gate_state "$TEST_START_DIR"', env=env)


def _latch_path(repo: Path, tmp_path: Path) -> Path:
    return tmp_path / "cfg" / ".diff-size-prompt-latch.d" / reviewer_round_state_key(repo)


def _clone_with_staged_lines(tmp_path: Path, line_count: int) -> Path:
    _bare, clone = _clone_with_trunk_files(tmp_path)
    _stage_file(clone, "work.txt", _lines("work", line_count))
    return clone


class TestLibDiffSizeGateState:
    def test_an_unlatched_branch_at_the_threshold_prints_the_latch_path_and_the_measurement(self, tmp_path: Path) -> None:
        clone = _clone_with_staged_lines(tmp_path, 400)
        subdirectory = clone / "sub"
        subdirectory.mkdir()
        expected_stdout = f"{_latch_path(clone, tmp_path)}\n400 trunk\n"

        from_root = _gate_state(clone, tmp_path)
        from_subdirectory = _gate_state(clone, tmp_path, start_dir=subdirectory)

        assert (from_root.returncode, from_root.stdout) == (0, expected_stdout), from_root.stderr
        assert (from_subdirectory.returncode, from_subdirectory.stdout) == (0, expected_stdout), from_subdirectory.stderr
        assert from_root.stderr == ""
        assert from_subdirectory.stderr == ""

    def test_a_branch_one_line_below_the_threshold_returns_2_with_no_stdout(self, tmp_path: Path) -> None:
        clone = _clone_with_staged_lines(tmp_path, 399)

        result = _gate_state(clone, tmp_path)

        assert (result.returncode, result.stdout) == (2, ""), result.stderr
        assert result.stderr == ""

    def test_a_latch_file_returns_3_before_any_measurement(self, tmp_path: Path) -> None:
        clone = _clone_with_staged_lines(tmp_path, 400)
        failing_diff_index = {"PATH": path_with_failing_tool_shim(tmp_path / "failing-diff-index-bin", "git", "diff-index")}
        unlatched = _gate_state(clone, tmp_path, env_overrides=failing_diff_index)
        assert unlatched.returncode == 1, "premise: the unlatched branch needs the measurement the shim fails"
        latch_file = _latch_path(clone, tmp_path)
        latch_file.parent.mkdir(parents=True)
        latch_file.write_text("recorded\n")

        result = _gate_state(clone, tmp_path, env_overrides=failing_diff_index)

        assert (result.returncode, result.stdout) == (3, ""), result.stderr
        assert result.stderr == ""

    def test_a_directory_at_the_latch_path_reads_as_no_latch(self, tmp_path: Path) -> None:
        clone = _clone_with_staged_lines(tmp_path, 400)
        _latch_path(clone, tmp_path).mkdir(parents=True)

        result = _gate_state(clone, tmp_path)

        assert (result.returncode, result.stdout) == (0, f"{_latch_path(clone, tmp_path)}\n400 trunk\n"), result.stderr

    def test_an_empty_start_dir_returns_1_with_no_stdout(self, tmp_path: Path) -> None:
        # The process cwd stays inside the repository, so only the empty-argument check can refuse.
        clone = _clone_with_staged_lines(tmp_path, 400)
        untoggled = _gate_state(clone, tmp_path)
        assert untoggled.returncode == 0, untoggled.stderr

        result = _gate_state(clone, tmp_path, start_dir="")

        assert (result.returncode, result.stdout) == (1, "")

    def test_an_unusable_stack_key_returns_1_and_the_measurements_stderr_reaches_the_caller(self, tmp_path: Path) -> None:
        clone = _clone_with_staged_lines(tmp_path, 400)
        untoggled = _gate_state(clone, tmp_path)
        assert untoggled.returncode == 0, untoggled.stderr
        _toggle_stack_key_names_an_origin_ref(clone, tmp_path)

        result = _gate_state(clone, tmp_path)

        assert (result.returncode, result.stdout) == (1, "")
        assert result.stderr.splitlines()[0].startswith("diff-size:"), result.stderr

    def test_a_linked_worktree_is_measured_until_its_own_gitdir_holds_a_paused_merge(self, tmp_path: Path) -> None:
        _bare, clone = _clone_with_trunk_files(tmp_path)
        linked = tmp_path / "linked"
        _run_git(clone, "worktree", "add", "-q", "-b", "linked-branch", str(linked), "trunk")
        _stage_file(linked, "work.txt", _lines("work", 400))

        measured = _gate_state(clone, tmp_path, start_dir=linked)
        head_oid = _run_git(linked, "rev-parse", "HEAD").strip()
        _forge_state_marker(absolute_git_dir(linked), "merge", head_oid)
        paused = _gate_state(clone, tmp_path, start_dir=linked)

        assert measured.returncode == 0, measured.stderr
        assert measured.stdout == f"{_latch_path(linked, tmp_path)}\n400 trunk\n"
        assert (paused.returncode, paused.stdout) == (1, "")

    def test_a_paused_merge_in_a_main_checkout_is_seen_from_an_unrelated_process_cwd(self, tmp_path: Path) -> None:
        clone = _clone_with_staged_lines(tmp_path, 400)
        unrelated_cwd = tmp_path / "unrelated-cwd"
        unrelated_cwd.mkdir()
        unpaused = _gate_state(clone, tmp_path, process_cwd=unrelated_cwd)
        head_oid = _run_git(clone, "rev-parse", "HEAD").strip()
        _forge_state_marker(absolute_git_dir(clone), "merge", head_oid)

        paused = _gate_state(clone, tmp_path, process_cwd=unrelated_cwd)

        assert unpaused.returncode == 0, unpaused.stderr
        assert (paused.returncode, paused.stdout) == (1, "")


def _toggle_sha256sum_missing(clone: Path, tmp_path: Path) -> tuple[Path | None, dict[str, str]]:
    farm_dir = tmp_path / "path-without-sha256sum"
    farm_dir.mkdir()
    return None, {"PATH": build_path_without("sha256sum", farm_dir)}


def _toggle_relative_config_dir(clone: Path, tmp_path: Path) -> tuple[Path | None, dict[str, str]]:
    return None, {"CLAUDE_CONFIG_DIR": "relative/cfg"}


def _toggle_start_dir_outside_any_repository(clone: Path, tmp_path: Path) -> tuple[Path | None, dict[str, str]]:
    # The process cwd stays inside the repository, so only the start dir's own rev-parse can refuse.
    outside = tmp_path / "outside-any-repository"
    outside.mkdir()
    return outside, {}


def _toggle_detached_head(clone: Path, tmp_path: Path) -> tuple[Path | None, dict[str, str]]:
    _run_git(clone, "checkout", "-q", "--detach")
    return None, {}


def _toggle_count_call_fails(clone: Path, tmp_path: Path) -> tuple[Path | None, dict[str, str]]:
    return None, {"PATH": path_with_failing_tool_shim(tmp_path / "failing-diff-index-bin", "git", "diff-index")}


class TestLibDiffSizeGateStateRefusals:
    @pytest.mark.parametrize(
        "toggle",
        [
            pytest.param(_toggle_count_call_fails, id="count call fails"),
            pytest.param(_toggle_sha256sum_missing, id="state key does not compute"),
            pytest.param(_toggle_relative_config_dir, id="relative CLAUDE_CONFIG_DIR"),
            pytest.param(_toggle_start_dir_outside_any_repository, id="start dir outside any repository"),
            pytest.param(_toggle_detached_head, id="detached HEAD"),
        ],
    )
    def test_returns_1_with_no_stdout(self, tmp_path: Path, toggle) -> None:
        clone = _clone_with_staged_lines(tmp_path, 400)
        untoggled = _gate_state(clone, tmp_path)
        assert untoggled.returncode == 0, untoggled.stderr

        start_dir, env_overrides = toggle(clone, tmp_path)
        result = _gate_state(clone, tmp_path, start_dir=start_dir, env_overrides=env_overrides)

        assert (result.returncode, result.stdout) == (1, "")

    def test_a_start_dir_outside_any_repository_returns_1_with_empty_stdout_and_stderr(self, tmp_path: Path) -> None:
        # The failing rev-parse call prints `fatal:` text that only its `2>/dev/null` in _lib_diff_size_gate_state discards.
        clone = _clone_with_staged_lines(tmp_path, 400)
        untoggled = _gate_state(clone, tmp_path)
        assert untoggled.returncode == 0, untoggled.stderr
        start_dir, env_overrides = _toggle_start_dir_outside_any_repository(clone, tmp_path)

        result = _gate_state(clone, tmp_path, start_dir=start_dir, env_overrides=env_overrides)

        assert (result.returncode, result.stdout, result.stderr) == (1, "", "")
