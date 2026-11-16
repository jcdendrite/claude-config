"""Unit tests for _lib.sh's _lib_path_ignore_status."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from helpers import (
    _make_git_exiting_with_status,
    _run_git,
    build_path_without,
    init_git_repo_with_commit,
    unwrapped_check_ignore_status,
)

from ._lib_sh_helpers import _LIB_SH

# --- _lib_path_ignore_status ---------------------------------------------------


def _init_ignoring_repo(repo: Path, *, ignore_rules: str = "agent-reviews/\n") -> Path:
    init_git_repo_with_commit(repo, branch="main")
    (repo / ".git" / "info" / "exclude").write_text(ignore_rules)
    return repo


def _path_ignore_status(
    directory: str, path: str, *, env: dict | None = None, cwd: Path | None = None
) -> subprocess.CompletedProcess:
    """Run `_lib_path_ignore_status DIRECTORY PATH` in a fresh shell that sourced _lib.sh.

    The status is printed to stderr after the call, so a test can tell the function's own status from the shell's.
    """
    harness = '. "$1"; _lib_path_ignore_status "$2" "$3"; printf "STATUS=%s" "$?" >&2'
    return subprocess.run(
        ["bash", "-c", harness, "bash", str(_LIB_SH), directory, path],
        capture_output=True,
        text=True,
        check=False,
        env=env if env is not None else dict(os.environ),
        cwd=cwd,
    )


def _status_of(result: subprocess.CompletedProcess) -> int:
    match = re.search(r"STATUS=(\d+)$", result.stderr)
    assert match, repr(result.stderr)
    return int(match.group(1))


@pytest.mark.usefixtures("isolated_git_environment")
class TestLibPathIgnoreStatus:
    def test_ignored_path_returns_0(self, tmp_path: Path) -> None:
        repo = _init_ignoring_repo(tmp_path / "repo")

        result = _path_ignore_status(str(repo), "agent-reviews/x.md")

        assert _status_of(result) == 0
        assert result.stdout == ""

    def test_path_no_rule_ignores_returns_1(self, tmp_path: Path) -> None:
        repo = _init_ignoring_repo(tmp_path / "repo")

        result = _path_ignore_status(str(repo), "src/x.md")

        assert _status_of(result) == 1
        assert result.stdout == ""

    def test_tracked_file_under_an_ignored_directory_returns_1(self, tmp_path: Path) -> None:
        """git check-ignore reports a tracked path as not ignored whatever the rules say."""
        repo = _init_ignoring_repo(tmp_path / "repo")
        (repo / "agent-reviews").mkdir()
        (repo / "agent-reviews" / "kept.md").write_text("x\n")
        _run_git(repo, "add", "-f", "agent-reviews/kept.md")

        result = _path_ignore_status(str(repo), "agent-reviews/kept.md")

        assert _status_of(result) == 1

    def test_path_a_negation_rule_re_includes_returns_1(self, tmp_path: Path) -> None:
        repo = _init_ignoring_repo(tmp_path / "repo", ignore_rules="agent-reviews/*\n!agent-reviews/keep.md\n")

        result = _path_ignore_status(str(repo), "agent-reviews/keep.md")

        assert _status_of(result) == 1

    def test_empty_directory_returns_3_even_from_inside_a_repo_that_ignores_the_path(self, tmp_path: Path) -> None:
        """`cd ''` succeeds in place on bash 5.2, so a missing empty-directory guard would check the cwd's repo and return 0."""
        repo = _init_ignoring_repo(tmp_path / "repo")

        result = _path_ignore_status("", "agent-reviews/x.md", cwd=repo)

        assert _status_of(result) == 3
        assert result.stdout == ""

    def test_nonexistent_directory_returns_3_with_no_output(self, tmp_path: Path) -> None:
        result = _path_ignore_status(str(tmp_path / "missing"), "agent-reviews/x.md")

        assert result.stdout == ""
        assert result.stderr == "STATUS=3"

    def test_symlinked_agent_reviews_returns_128(self, tmp_path: Path) -> None:
        """git check-ignore exits 128 for a path beneath a symbolic link (observed on git 2.43.0)."""
        repo = _init_ignoring_repo(tmp_path / "repo")
        (tmp_path / "elsewhere").mkdir()
        (repo / "agent-reviews").symlink_to(tmp_path / "elsewhere")

        result = _path_ignore_status(str(repo), "agent-reviews/x.md")

        assert _status_of(result) == 128

    @pytest.mark.parametrize(
        "pathspec_variable",
        ["GIT_LITERAL_PATHSPECS", "GIT_GLOB_PATHSPECS", "GIT_NOGLOB_PATHSPECS", "GIT_ICASE_PATHSPECS"],
    )
    def test_inherited_pathspec_mode_variable_fails_the_check_closed_with_128(
        self, tmp_path: Path, pathspec_variable: str
    ) -> None:
        """git check-ignore dies on a pathspec-mode variable, so an ignored path reports 128 and not 0."""
        repo = _init_ignoring_repo(tmp_path / "repo")
        env = {**os.environ, pathspec_variable: "1"}

        result = _path_ignore_status(str(repo), "agent-reviews/x.md", env=env)

        assert _status_of(result) == 128

    @pytest.mark.parametrize("shim_status", [3, 124, 128, 137, 143])
    def test_the_cap_or_gits_own_status_passes_through_the_function(self, tmp_path: Path, shim_status: int) -> None:
        repo = _init_ignoring_repo(tmp_path / "repo")
        shim_dir = tmp_path / "shim"
        _make_git_exiting_with_status(shim_dir, "check-ignore", shim_status)
        real_git = shutil.which("git")
        assert real_git is not None
        env = {**os.environ, "PATH": f"{shim_dir}{os.pathsep}{os.environ['PATH']}", "REAL_GIT": real_git}

        result = _path_ignore_status(str(repo), "agent-reviews/x.md", env=env)

        assert _status_of(result) == shim_status

    def test_inherited_foreign_git_dir_does_not_launder_the_result(self, tmp_path: Path) -> None:
        """A GIT_DIR naming a repo that ignores the path must not make a repo that does not ignore it report 0."""
        target = _init_ignoring_repo(tmp_path / "target", ignore_rules="")
        foreign = _init_ignoring_repo(tmp_path / "foreign")
        poison = {"GIT_DIR": str(foreign / ".git")}
        env = {**os.environ, **poison}
        assert unwrapped_check_ignore_status(target, "agent-reviews/x.md", poison) == 0

        result = _path_ignore_status(str(target), "agent-reviews/x.md", env=env)

        assert _status_of(result) == 1

    def test_inherited_foreign_work_tree_does_not_launder_the_result(self, tmp_path: Path) -> None:
        """A GIT_WORK_TREE naming a directory whose .gitignore ignores the path must not make a repo that does not
        ignore it report 0, because git reads ignore rules from the work tree the variable names."""
        target = _init_ignoring_repo(tmp_path / "target", ignore_rules="")
        foreign = tmp_path / "foreign"
        foreign.mkdir()
        (foreign / ".gitignore").write_text("agent-reviews/\n")
        poison = {"GIT_WORK_TREE": str(foreign)}
        env = {**os.environ, **poison}
        assert unwrapped_check_ignore_status(target, "agent-reviews/x.md", poison) == 0

        result = _path_ignore_status(str(target), "agent-reviews/x.md", env=env)

        assert _status_of(result) == 1

    def test_inherited_index_file_does_not_hide_a_tracked_path(self, tmp_path: Path) -> None:
        """A GIT_INDEX_FILE naming a nonexistent index makes git see no tracked paths, so a force-added file under an
        ignored directory would report 0 if the variable were not unset."""
        repo = _init_ignoring_repo(tmp_path / "repo")
        tracked_path = "agent-reviews/staff-sdet-1700000000-branch.md"
        (repo / "agent-reviews").mkdir()
        (repo / tracked_path).write_text("x\n")
        _run_git(repo, "add", "-f", tracked_path)
        poison = {"GIT_INDEX_FILE": str(tmp_path / "no-such-index")}
        env = {**os.environ, **poison}
        assert unwrapped_check_ignore_status(repo, tracked_path, poison) == 0

        result = _path_ignore_status(str(repo), tracked_path, env=env)

        assert _status_of(result) == 1

    def test_inherited_foreign_common_dir_does_not_launder_the_result(self, tmp_path: Path) -> None:
        """A GIT_COMMON_DIR naming a repo whose info/exclude ignores the path must not make a repo that does not ignore
        it report 0, because git reads info/exclude from the common directory."""
        target = _init_ignoring_repo(tmp_path / "target", ignore_rules="")
        foreign = _init_ignoring_repo(tmp_path / "foreign")
        poison = {"GIT_COMMON_DIR": str(foreign / ".git")}
        env = {**os.environ, **poison}
        assert unwrapped_check_ignore_status(target, "agent-reviews/x.md", poison) == 0

        result = _path_ignore_status(str(target), "agent-reviews/x.md", env=env)

        assert _status_of(result) == 1

    def test_inherited_config_count_excludes_file_does_not_launder_the_result(self, tmp_path: Path) -> None:
        """A core.excludesFile injected through GIT_CONFIG_COUNT must not make a repo that does not ignore the path
        report 0."""
        target = _init_ignoring_repo(tmp_path / "target", ignore_rules="")
        excludes_file = tmp_path / "injected-excludes"
        excludes_file.write_text("agent-reviews/\n")
        poison = {
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "core.excludesFile",
            "GIT_CONFIG_VALUE_0": str(excludes_file),
        }
        env = {**os.environ, **poison}
        assert unwrapped_check_ignore_status(target, "agent-reviews/x.md", poison) == 0

        result = _path_ignore_status(str(target), "agent-reviews/x.md", env=env)

        assert _status_of(result) == 1

    def test_inherited_config_count_safe_directory_entry_is_not_honored(self, tmp_path: Path) -> None:
        """A safe.directory entry injected through GIT_CONFIG_COUNT is dropped for the check, so a repo git rates as
        owned by another user exits 128. GIT_TEST_ASSUME_DIFFERENT_OWNER is a git-internal test knob, so the
        two unwrapped premise assertions are what keep this test honest."""
        repo = _init_ignoring_repo(tmp_path / "repo")
        knob = {"GIT_TEST_ASSUME_DIFFERENT_OWNER": "1"}
        poison = {
            **knob,
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "safe.directory",
            "GIT_CONFIG_VALUE_0": str(repo),
        }
        env = {**os.environ, **poison}
        assert unwrapped_check_ignore_status(repo, "agent-reviews/x.md", knob) == 128, "premise: git honors the knob"
        assert unwrapped_check_ignore_status(repo, "agent-reviews/x.md", poison) == 0, "premise: the entry is honored unwrapped"

        result = _path_ignore_status(str(repo), "agent-reviews/x.md", env=env)

        assert _status_of(result) == 128

    def test_global_config_safe_directory_entry_still_applies_to_a_dubious_ownership_repository(
        self, tmp_path: Path
    ) -> None:
        """A safe.directory entry in the global git config is honored by the check, so it is the remedy for a repo git
        rates as owned by another user."""
        repo = _init_ignoring_repo(tmp_path / "repo")
        global_config = tmp_path / "gitconfig"
        global_config.write_text(f"[safe]\n\tdirectory = {repo}\n")
        knob = {"GIT_TEST_ASSUME_DIFFERENT_OWNER": "1"}
        remedy = {**knob, "GIT_CONFIG_GLOBAL": str(global_config)}
        env = {**os.environ, **remedy}
        assert unwrapped_check_ignore_status(repo, "agent-reviews/x.md", knob) == 128, "premise: git honors the knob"
        assert unwrapped_check_ignore_status(repo, "agent-reviews/x.md", remedy) == 0, "premise: the global entry is honored"

        result = _path_ignore_status(str(repo), "agent-reviews/x.md", env=env)

        assert _status_of(result) == 0

    def test_repository_local_variable_list_covers_every_name_git_prints(self, tmp_path: Path) -> None:
        """Membership pin for every name `git rev-parse --local-env-vars` prints, including GIT_CONFIG_PARAMETERS,
        which gets no behavioral pin because its value syntax is internal to git."""
        plain = tmp_path / "plain"
        plain.mkdir()
        printed = subprocess.run(
            ["git", "rev-parse", "--local-env-vars"], cwd=plain, capture_output=True, text=True, check=False
        )
        assert printed.returncode == 0, repr(printed)
        harness = '. "$1"; printf "%s\\n" "${_LIB_GIT_REPOSITORY_LOCAL_ENV_VARS[@]}"'

        listed = subprocess.run(
            ["bash", "-c", harness, "bash", str(_LIB_SH)], capture_output=True, text=True, check=False
        )

        assert listed.returncode == 0, repr(listed)
        printed_names = set(printed.stdout.split())
        assert "GIT_DIR" in printed_names, repr(printed)
        assert printed_names <= set(listed.stdout.split())

    def test_command_config_names_sit_outside_the_selecting_array(self) -> None:
        harness = (
            '. "$1"; printf "%s\\n" "${_LIB_GIT_REPOSITORY_SELECTING_ENV_VARS[@]}"; printf "%s\\n" "--"; '
            'printf "%s\\n" "${_LIB_GIT_COMMAND_CONFIG_ENV_VARS[@]}"; printf "%s\\n" "--"; '
            'printf "%s\\n" "${_LIB_GIT_REPOSITORY_LOCAL_ENV_VARS[@]}"'
        )

        listed = subprocess.run(
            ["bash", "-c", harness, "bash", str(_LIB_SH)], capture_output=True, text=True, check=False
        )

        assert listed.returncode == 0, repr(listed)
        selecting_block, command_config_block, local_block = listed.stdout.split("--\n")
        selecting, command_config, local = (set(block.split()) for block in (selecting_block, command_config_block, local_block))
        assert selecting and command_config and local, repr(listed)
        command_config_names = {"GIT_CONFIG", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT"}
        assert command_config_names <= command_config
        assert not command_config_names & selecting
        assert selecting | command_config == local

    def test_stderr_is_empty_with_timeout_on_path(self, tmp_path: Path) -> None:
        if not (shutil.which("timeout") or shutil.which("gtimeout")):
            pytest.skip("neither timeout(1) nor gtimeout(1) available — BSD/macOS without coreutils")
        repo = _init_ignoring_repo(tmp_path / "repo")

        result = _path_ignore_status(str(repo), "agent-reviews/x.md")

        assert result.stderr == "STATUS=0"

    def test_neither_timeout_nor_gtimeout_on_path_still_checks_and_stays_silent(self, tmp_path: Path) -> None:
        """_lib_capped_for's uncapped-run notice goes to the git call's own 2>/dev/null, so the helper writes nothing."""
        repo = _init_ignoring_repo(tmp_path / "repo")
        farm = tmp_path / "farm"
        farm.mkdir()
        build_path_without("timeout", farm)
        (farm / "gtimeout").unlink(missing_ok=True)
        assert shutil.which("timeout", path=str(farm)) is None
        assert shutil.which("gtimeout", path=str(farm)) is None
        env = {**os.environ, "PATH": str(farm)}

        result = _path_ignore_status(str(repo), "agent-reviews/x.md", env=env)

        assert _status_of(result) == 0
        assert result.stdout == ""
        assert result.stderr == "STATUS=0"


# --- _lib_dotenv_basename_is_secret ---------------------------------------------


@pytest.mark.parametrize(
    ("basename", "expected_secret"),
    [
        (".env", True),
        (".env.local", True),
        (".env.production", True),
        (".env.ci", True),
        (".env.vault", True),
        (".env.", True),
        (".env.example", False),
        (".env.template", False),
        (".env.sample", False),
        (".envrc", False),
        (".ENV", False),
        ("env", False),
        ("package.env", False),
        ("", False),
    ],
)
def test_dotenv_basename_is_secret_matrix(basename: str, expected_secret: bool) -> None:
    result = subprocess.run(
        ["bash", "-c", '. "$1"; _lib_dotenv_basename_is_secret "$2"', "bash", str(_LIB_SH), basename],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == (0 if expected_secret else 1), repr(result)
    assert result.stdout == ""
