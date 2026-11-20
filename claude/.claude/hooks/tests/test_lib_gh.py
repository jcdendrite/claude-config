"""Unit tests for _lib.sh's _lib_gh, _lib_origin_owner_repo, _lib_origin_names_claude_config,
_lib_repo_has_claude_config_layout, _lib_repo_is_claude_config and _lib_case_insensitive_ne."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from helpers import (
    REPO_ROOT,
    absolute_git_dir,
    init_git_repo,
    init_git_repo_with_commit,
    isolated_git_config_env,
    locale_that_folds_ascii_capital_i_to_a_non_ascii_letter,
)

from ._lib_sh_helpers import (
    _LIB_SH,
    _gh_shim_dir,
    _run_lib_call,
    claude_config_layout_path,
    repo_tracking_claude_config_layout,
)

# --- _lib_gh -----------------------------------------------------------


class TestLibGh:
    """Direct unit coverage for _lib_gh -- the one call every review-pr
    script routes its own `gh` invocations through."""

    def test_forwards_argv_and_ambient_gh_host_and_enterprise_token_unchanged(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        call_log = tmp_path / "calls.jsonl"
        shim_dir = _gh_shim_dir(tmp_path, call_log, monkeypatch)
        env = {
            **os.environ,
            "PATH": f"{shim_dir}{os.pathsep}{os.environ['PATH']}",
            "GH_HOST": "ghe.example.com",
            "GH_ENTERPRISE_TOKEN": "enterprise-token",
        }
        result = _run_lib_call('_lib_gh 5 pr view 42 -R foo/bar --json headRefOid', env=env)
        assert result.returncode == 0, result.stderr
        record = json.loads(call_log.read_text())
        assert record["args"] == ["pr", "view", "42", "-R", "foo/bar", "--json", "headRefOid"]
        assert record["GH_HOST"] == "ghe.example.com"
        assert record["GH_ENTERPRISE_TOKEN"] == "enterprise-token"

    def test_propagates_gh_own_nonzero_exit_status(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        call_log = tmp_path / "calls.jsonl"
        shim_dir = _gh_shim_dir(tmp_path, call_log, monkeypatch, exit_code=7)
        env = {**os.environ, "PATH": f"{shim_dir}{os.pathsep}{os.environ['PATH']}"}
        result = _run_lib_call('_lib_gh 5 pr view 42', env=env)
        assert result.returncode == 7
        assert call_log.exists()


# --- _lib_origin_owner_repo ----------------------------------------------


class TestLibOriginOwnerRepo:
    """Direct unit coverage for _lib_origin_owner_repo -- the one shared
    extraction review-pr-checkout.sh and review-pr-diff.sh (REPO_ROOT form)
    and review-pr-post.sh and require-respond-pr.sh's own cross-repo check
    (cwd form) all call."""

    @staticmethod
    def _origin_owner_repo(repo: Path, resolution_form: str) -> subprocess.CompletedProcess:
        """Run the function against repo in one of its two resolution forms.
        The ambient git config is isolated so a contributor's own `insteadOf`
        rules cannot change either form's answer."""
        env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
        if resolution_form == "repo_root":
            return _run_lib_call(f'_lib_origin_owner_repo "{repo}"', env=env)
        return subprocess.run(
            ["bash", "-c", f'. "{_LIB_SH}"; _lib_origin_owner_repo'],
            cwd=repo, capture_output=True, text=True, env=env,
        )

    @pytest.mark.parametrize("resolution_form", ["repo_root", "cwd"])
    @pytest.mark.parametrize(
        "origin_url",
        [
            "https://github.com/foo/bar.git",
            "https://github.com/foo/bar",
            "git@github.com:foo/bar.git",
            "ssh://git@github.com:22/foo/bar.git",
            "https://user:token@github.com/foo/bar.git",
            "/srv/remotes/foo/bar.git",
        ],
        ids=["https_dot_git", "https_no_suffix", "scp_like", "ssh_with_port", "https_with_userinfo", "local_path"],
    )
    def test_extracts_owner_repo_from_each_url_shape_in_both_resolution_forms(
        self, tmp_path: Path, origin_url: str, resolution_form: str
    ) -> None:
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo)
        subprocess.run(["git", "remote", "add", "origin", origin_url], cwd=repo, check=True)
        result = self._origin_owner_repo(repo, resolution_form)
        assert result.returncode == 0, result.stderr
        assert result.stdout == "foo/bar"

    @pytest.mark.parametrize("resolution_form", ["repo_root", "cwd"])
    @pytest.mark.parametrize(
        "origin_url",
        ["https://github.com/foo/bar/", "https://github.com/foo/bar.git/"],
        ids=["trailing_slash", "dot_git_trailing_slash"],
    )
    def test_a_trailing_slash_fails_closed_in_both_resolution_forms(
        self, tmp_path: Path, origin_url: str, resolution_form: str
    ) -> None:
        """A URL ending in `/` does not parse to an owner/repo shape, so a
        caller denies rather than comparing a wrong slug."""
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo)
        subprocess.run(["git", "remote", "add", "origin", origin_url], cwd=repo, check=True)
        result = self._origin_owner_repo(repo, resolution_form)
        assert result.returncode != 0
        assert result.stdout == ""

    @pytest.mark.parametrize("resolution_form", ["repo_root", "cwd"])
    def test_an_instead_of_rewrite_yields_the_same_owner_repo_in_both_resolution_forms(
        self, tmp_path: Path, resolution_form: str
    ) -> None:
        """`git remote get-url` applies a `url.<base>.insteadOf` rewrite and
        `git config --get` does not, so the two forms read different URLs here
        and must still agree on the slug."""
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo)
        subprocess.run(["git", "remote", "add", "origin", "alias:foo/bar.git"], cwd=repo, check=True)
        subprocess.run(["git", "config", "url.https://github.com/.insteadOf", "alias:"], cwd=repo, check=True)
        result = self._origin_owner_repo(repo, resolution_form)
        assert result.returncode == 0, result.stderr
        assert result.stdout == "foo/bar"

    def test_fails_closed_with_no_origin_remote(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo)
        result = _run_lib_call(f'_lib_origin_owner_repo "{repo}"', env=dict(os.environ))
        assert result.returncode != 0
        assert result.stdout == ""


class TestLibOriginNamesClaudeConfig:
    """Direct unit coverage for _lib_origin_names_claude_config -- the one
    origin-scope test deny-private-project-refs.sh and require-stow-reminder.sh
    both call."""

    @staticmethod
    def _names_claude_config(repo: Path, tmp_path: Path, extra_env: dict[str, str] | None = None) -> int:
        """Return code of the helper against repo. Ambient git config is isolated so a global or system
        `remote.origin.url` value is not merged into the `--get-all` result."""
        env = {**os.environ, **isolated_git_config_env(tmp_path / "home"), **(extra_env or {})}
        return _run_lib_call(f'_lib_origin_names_claude_config "{repo}"', env=env).returncode

    @staticmethod
    def _repo_with_remote(tmp_path: Path, remote_name: str, url: str) -> Path:
        repo = init_git_repo(tmp_path / "repo")
        subprocess.run(["git", "remote", "add", remote_name, url], cwd=repo, check=True)
        return repo

    @pytest.mark.parametrize(
        "origin_url",
        [
            "https://github.com/someone/claude-config.git",
            "https://github.com/someone/claude-config/",
            "git@github.com:someone/workflow-gates.git",
            "https://github.com/Someone/Workflow-Gates.git",
            "git@github.com:SOMEONE/CLAUDE-CONFIG.git",
            "git@github.com:someone/claude-config-notes.git",
            "https://github.com/claude-config-fans/other.git",
            "../claude-config",
        ],
        ids=[
            "claude_config",
            "trailing_slash_that_owner_repo_rejects",
            "workflow_gates",
            "mixed_case_workflow_gates",
            "upper_case_claude_config",
            "superstring_of_claude_config",
            "owner_segment_containing_claude_config",
            "relative_path_origin",
        ],
    )
    def test_returns_zero_when_the_origin_url_contains_either_name(self, tmp_path: Path, origin_url: str) -> None:
        repo = self._repo_with_remote(tmp_path, "origin", origin_url)
        assert self._names_claude_config(repo, tmp_path) == 0

    @pytest.mark.parametrize(
        ("first_url", "last_url"),
        [
            ("https://github.com/someone/claude-config.git", "git@github.com:someone/other-app.git"),
            ("git@github.com:someone/other-app.git", "https://github.com/someone/claude-config.git"),
            ("git@github.com:someone/other-app.git", "git@github.com:someone/workflow-gates.git"),
        ],
        ids=[
            "matching_value_first",
            "matching_claude_config_value_last",
            "matching_workflow_gates_value_last",
        ],
    )
    def test_returns_zero_when_any_origin_url_value_contains_either_name(
        self, tmp_path: Path, first_url: str, last_url: str
    ) -> None:
        """`remote.origin.url` is multi-valued, and the helper must read every value, not only the first or last."""
        repo = self._repo_with_remote(tmp_path, "origin", first_url)
        subprocess.run(
            ["git", "config", "--add", "remote.origin.url", last_url],
            cwd=repo, check=True,
        )
        assert self._names_claude_config(repo, tmp_path) == 0

    def test_returns_one_for_an_origin_naming_neither(self, tmp_path: Path) -> None:
        repo = self._repo_with_remote(tmp_path, "origin", "git@github.com:someone/other-app.git")
        assert self._names_claude_config(repo, tmp_path) == 1

    @pytest.mark.parametrize(
        "origin_url",
        [
            "git@github.com:someone/claude-code.git",
            "git@github.com:someone/claude_config.git",
            "git@github.com:someone/workflow_gates.git",
            "git@github.com:someone/workflow-engine.git",
        ],
        ids=[
            "claude_code_shares_only_the_claude_prefix",
            "underscore_claude_config",
            "underscore_workflow_gates",
            "workflow_engine_shares_only_the_workflow_prefix",
        ],
    )
    def test_returns_one_for_a_near_miss_origin_that_shares_only_part_of_a_name(
        self, tmp_path: Path, origin_url: str
    ) -> None:
        """Pins whole-literal, hyphenated matching: a broader predicate such as `*claude*` or a looser separator
        would return 0 for these."""
        repo = self._repo_with_remote(tmp_path, "origin", origin_url)
        assert self._names_claude_config(repo, tmp_path) == 1

    def test_returns_zero_for_an_upper_case_origin_under_a_locale_that_folds_i_to_a_dotless_letter(
        self, tmp_path: Path
    ) -> None:
        """Without the helper's `LC_ALL=C` pin, `${var,,}` turns `CLAUDE-CONFIG` into `claude-confıg` here."""
        turkish_locale = locale_that_folds_ascii_capital_i_to_a_non_ascii_letter()
        repo = self._repo_with_remote(tmp_path, "origin", "git@github.com:SOMEONE/CLAUDE-CONFIG.git")
        assert self._names_claude_config(repo, tmp_path, extra_env={"LC_ALL": turkish_locale}) == 0

    def test_returns_one_when_the_origin_read_fails_for_a_nonexistent_repository_path(self, tmp_path: Path) -> None:
        """A nonexistent path makes `git -C` exit 128 and cannot reach an enclosing repository."""
        assert self._names_claude_config(tmp_path / "does-not-exist", tmp_path) == 1

    def test_returns_one_for_a_repository_with_no_remotes(self, tmp_path: Path) -> None:
        repo = init_git_repo(tmp_path / "repo")
        assert self._names_claude_config(repo, tmp_path) == 1

    def test_returns_one_when_only_a_non_origin_remote_names_the_repository(self, tmp_path: Path) -> None:
        """An accepted gap listed in the helper header: only the remote name differs from the `workflow_gates`
        row of `test_returns_zero_when_the_origin_url_contains_either_name`, which returns 0 under `origin`."""
        repo = self._repo_with_remote(tmp_path, "upstream", "git@github.com:someone/workflow-gates.git")
        assert self._names_claude_config(repo, tmp_path) == 1

    def test_returns_one_for_an_instead_of_alias_origin_that_hides_the_name(self, tmp_path: Path) -> None:
        """An accepted gap listed in the helper header: the stored URL hides the name, and the control shows
        the rewritten URL does contain it."""
        repo = self._repo_with_remote(tmp_path, "origin", "wg:")
        subprocess.run(
            ["git", "config", "url.git@github.com:someone/workflow-gates.git.insteadOf", "wg:"],
            cwd=repo, check=True,
        )
        env = {**os.environ, **isolated_git_config_env(tmp_path / "home")}
        rewritten_url = subprocess.run(
            ["git", "remote", "get-url", "origin"], cwd=repo, capture_output=True, text=True, check=True, env=env
        ).stdout
        assert "workflow-gates" in rewritten_url
        assert self._names_claude_config(repo, tmp_path) == 1

    def test_leaves_the_callers_locale_unchanged(self, tmp_path: Path) -> None:
        """The helper's `LC_ALL=C` is function-local, so it must not leak into the caller's own shell.

        Asserts the variable, not the effective locale: the two agree on the bash 5.2 floor.
        """
        repo = self._repo_with_remote(tmp_path, "origin", "git@github.com:someone/other-app.git")
        caller_locale = "en_US.UTF-8"
        helper_undefined_status = 91
        result = _run_lib_call(
            f"declare -F _lib_origin_names_claude_config >/dev/null || exit {helper_undefined_status}; "
            f'export LC_ALL={caller_locale}; _lib_origin_names_claude_config "{repo}"; '
            'printf %s "$LC_ALL"',
            env={**os.environ, **isolated_git_config_env(tmp_path / "home")},
        )
        assert result.returncode == 0, (
            f"exit {result.returncode} ({helper_undefined_status} means the helper is undefined after sourcing "
            f"_lib.sh); stderr: {result.stderr}"
        )
        assert result.stdout == caller_locale


def _repo_checked_out_before_the_layout_path_was_added(path: Path) -> Path:
    """A repository whose second commit adds the layout path, checked out at its first commit."""
    repo = init_git_repo_with_commit(path)
    layout_path = claude_config_layout_path()
    layout_file = repo / layout_path
    layout_file.parent.mkdir(parents=True)
    layout_file.write_text("x\n")
    subprocess.run(["git", "add", "--", layout_path], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "add layout"], cwd=repo, check=True)
    subprocess.run(["git", "checkout", "-q", "HEAD~1"], cwd=repo, check=True)
    assert not layout_file.exists(), "premise: the older commit has no layout file in the working tree"
    return repo


class TestLibRepoHasClaudeConfigLayout:
    """Direct unit coverage for _lib_repo_has_claude_config_layout: 0 when the layout path is a regular file
    in the working tree, 1 when it is not."""

    @staticmethod
    def _layout_status(repo: Path) -> int:
        """Return code of the helper against repo."""
        return _run_lib_call(f'_lib_repo_has_claude_config_layout "{repo}"', env=dict(os.environ)).returncode

    @staticmethod
    def _write_layout_file(repo: Path) -> Path:
        layout_file = repo / claude_config_layout_path()
        layout_file.parent.mkdir(parents=True, exist_ok=True)
        layout_file.write_text("x\n")
        return layout_file

    def test_returns_zero_when_the_layout_path_is_committed_and_checked_out(self, tmp_path: Path) -> None:
        repo = repo_tracking_claude_config_layout(tmp_path / "repo")
        assert self._layout_status(repo) == 0

    def test_layout_path_constant_names_the_redaction_hook(self) -> None:
        """Pins the constant's value: a path common in unrelated repositories would widen both gates."""
        assert claude_config_layout_path() == "claude/.claude/hooks/deny-private-project-refs.sh"

    def test_returns_zero_for_this_repository_s_own_root(self) -> None:
        """Self-check: the path the helper tests exists in this repository's working tree, so a typo in it fails here."""
        assert self._layout_status(REPO_ROOT) == 0

    def test_returns_zero_when_the_layout_path_is_only_untracked_in_the_working_tree(self, tmp_path: Path) -> None:
        """Pins a documented gap: an untracked copy at the path matches."""
        repo = init_git_repo_with_commit(tmp_path / "repo")
        self._write_layout_file(repo)
        assert self._layout_status(repo) == 0

    def test_returns_zero_for_a_directory_that_is_not_a_repository_and_holds_the_layout_path(
        self, tmp_path: Path
    ) -> None:
        plain_directory = tmp_path / "plain-directory"
        self._write_layout_file(plain_directory)
        assert self._layout_status(plain_directory) == 0

    def test_returns_zero_in_a_linked_worktree_of_a_repository_that_has_the_layout_path(
        self, tmp_path: Path
    ) -> None:
        repo = repo_tracking_claude_config_layout(tmp_path / "repo")
        linked_worktree = tmp_path / "linked"
        subprocess.run(
            ["git", "worktree", "add", "-q", "-b", "linked-branch", str(linked_worktree)], cwd=repo, check=True
        )
        assert self._layout_status(linked_worktree) == 0

    def test_returns_zero_when_a_symlink_to_a_regular_file_holds_the_layout_path(self, tmp_path: Path) -> None:
        """Pins a documented gap: a symlink that resolves to a regular file matches."""
        repo = tmp_path / "repo"
        link = repo / claude_config_layout_path()
        link.parent.mkdir(parents=True)
        target = repo / "real-file"
        target.write_text("x\n")
        link.symlink_to(target)
        assert self._layout_status(repo) == 0

    def test_returns_one_for_a_fresh_repository_with_nothing_committed(self, tmp_path: Path) -> None:
        repo = init_git_repo(tmp_path / "repo")
        assert self._layout_status(repo) == 1

    def test_returns_one_for_an_unrelated_repository_with_commits(self, tmp_path: Path) -> None:
        repo = init_git_repo_with_commit(tmp_path / "repo")
        assert self._layout_status(repo) == 1

    def test_returns_one_when_the_layout_path_is_deleted_from_the_working_tree_but_still_tracked(
        self, tmp_path: Path
    ) -> None:
        """Pins a documented gap: an unstaged deletion answers 1."""
        repo = repo_tracking_claude_config_layout(tmp_path / "repo")
        (repo / claude_config_layout_path()).unlink()
        assert self._layout_status(repo) == 1

    def test_returns_one_when_the_removal_of_the_layout_path_is_staged(self, tmp_path: Path) -> None:
        repo = repo_tracking_claude_config_layout(tmp_path / "repo")
        subprocess.run(["git", "rm", "-q", "--", claude_config_layout_path()], cwd=repo, check=True)
        assert self._layout_status(repo) == 1

    def test_returns_one_for_a_no_checkout_clone_of_a_repository_that_has_the_layout_path(
        self, tmp_path: Path
    ) -> None:
        """Pins a documented gap: a clone made with --no-checkout has no working-tree files."""
        source = repo_tracking_claude_config_layout(tmp_path / "source")
        clone = tmp_path / "clone"
        subprocess.run(["git", "clone", "-q", "--no-checkout", str(source), str(clone)], check=True)
        assert not (clone / claude_config_layout_path()).exists(), "premise: the clone has no working-tree file"
        assert self._layout_status(clone) == 1

    def test_returns_one_when_a_sparse_checkout_leaves_the_layout_path_out_of_the_working_tree(
        self, tmp_path: Path
    ) -> None:
        """Pins a documented gap: a tracked path outside the sparse-checkout cone is absent from the working tree."""
        repo = repo_tracking_claude_config_layout(tmp_path / "repo")
        (repo / "other.txt").write_text("other\n")
        subprocess.run(["git", "add", "other.txt"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "add other"], cwd=repo, check=True)
        subprocess.run(["git", "sparse-checkout", "set", "--no-cone", "/other.txt"], cwd=repo, check=True)
        assert not (repo / claude_config_layout_path()).exists(), "premise: the sparse checkout removed the path"
        assert self._layout_status(repo) == 1

    def test_returns_one_when_a_directory_holds_the_layout_path(self, tmp_path: Path) -> None:
        """Only a regular file matches, so a directory at the path does not."""
        repo = init_git_repo(tmp_path / "repo")
        inner_file = repo / claude_config_layout_path() / "inner.txt"
        inner_file.parent.mkdir(parents=True)
        inner_file.write_text("x\n")
        assert self._layout_status(repo) == 1

    def test_returns_one_for_a_directory_that_is_not_a_repository_and_lacks_the_layout_path(
        self, tmp_path: Path
    ) -> None:
        plain_directory = tmp_path / "plain-directory"
        plain_directory.mkdir()
        assert self._layout_status(plain_directory) == 1

    def test_returns_one_for_a_nonexistent_repository_path(self, tmp_path: Path) -> None:
        assert self._layout_status(tmp_path / "does-not-exist") == 1

    def test_returns_one_when_neighbouring_paths_are_tracked_but_the_layout_path_is_not(self, tmp_path: Path) -> None:
        """The layout path must be specific to this repository: a repository tracking a README and a sibling
        hook under the same directory is not matched."""
        repo = init_git_repo(tmp_path / "repo")
        layout_directory = Path(claude_config_layout_path()).parent
        neighbours = ["README.md", str(layout_directory / "deny-pii-in-commits.sh"), str(layout_directory / "_lib.sh")]
        for neighbour in neighbours:
            neighbour_file = repo / neighbour
            neighbour_file.parent.mkdir(parents=True, exist_ok=True)
            neighbour_file.write_text("x\n")
        subprocess.run(["git", "add", "--", *neighbours], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "add neighbours"], cwd=repo, check=True)
        assert self._layout_status(repo) == 1

    def test_reads_the_given_repository_regardless_of_the_callers_working_directory(self, tmp_path: Path) -> None:
        repo = repo_tracking_claude_config_layout(tmp_path / "repo")
        elsewhere = init_git_repo_with_commit(tmp_path / "elsewhere")
        result = subprocess.run(
            ["bash", "-c", f'. {_LIB_SH}; _lib_repo_has_claude_config_layout "$1"', "bash", str(repo)],
            cwd=elsewhere, env=dict(os.environ), check=False,
        )
        assert result.returncode == 0

    def test_returns_one_for_an_empty_root_even_when_the_callers_working_directory_holds_the_layout_path(
        self, tmp_path: Path
    ) -> None:
        """An empty root tests the path under the filesystem root, not under the caller's working directory."""
        repo = repo_tracking_claude_config_layout(tmp_path / "repo")
        result = subprocess.run(
            ["bash", "-c", f'. {_LIB_SH}; _lib_repo_has_claude_config_layout ""'],
            cwd=repo, env=dict(os.environ), check=False,
        )
        assert result.returncode == 1

    def test_returns_one_for_a_checkout_of_a_commit_that_predates_the_layout_path(self, tmp_path: Path) -> None:
        """Pins a documented gap: the test reads the working tree, not history."""
        repo = _repo_checked_out_before_the_layout_path_was_added(tmp_path / "repo")
        assert self._layout_status(repo) == 1


class TestLibRepoIsClaudeConfig:
    """Direct unit coverage for _lib_repo_is_claude_config, the one scope predicate both hooks call: the
    origin test first, then the layout test, with a status other than 0 or 1 passed through from the origin test."""

    UNRELATED_ORIGIN = "git@github.com:someone/other-app.git"

    @staticmethod
    def _scope_status(repo: Path, tmp_path: Path, call_prefix: str = "") -> int:
        env = {**os.environ, **isolated_git_config_env(tmp_path / "home")}
        return _run_lib_call(f'{call_prefix}_lib_repo_is_claude_config "{repo}"', env=env).returncode

    @staticmethod
    def _set_origin(repo: Path, url: str) -> None:
        subprocess.run(["git", "remote", "add", "origin", url], cwd=repo, check=True)

    def test_returns_zero_for_a_matching_origin_without_the_layout(self, tmp_path: Path) -> None:
        repo = init_git_repo_with_commit(tmp_path / "repo")
        self._set_origin(repo, "git@github.com:someone/claude-config.git")
        assert self._scope_status(repo, tmp_path) == 0

    def test_returns_zero_for_the_layout_under_an_unrelated_origin(self, tmp_path: Path) -> None:
        repo = repo_tracking_claude_config_layout(tmp_path / "repo")
        self._set_origin(repo, self.UNRELATED_ORIGIN)
        assert self._scope_status(repo, tmp_path) == 0

    def test_returns_zero_for_the_layout_with_no_remote(self, tmp_path: Path) -> None:
        repo = repo_tracking_claude_config_layout(tmp_path / "repo")
        assert self._scope_status(repo, tmp_path) == 0

    def test_returns_one_when_neither_the_origin_nor_the_layout_matches(self, tmp_path: Path) -> None:
        repo = init_git_repo_with_commit(tmp_path / "repo")
        self._set_origin(repo, self.UNRELATED_ORIGIN)
        assert self._scope_status(repo, tmp_path) == 1

    def test_returns_one_for_a_repository_with_no_commit_and_an_unrelated_origin(self, tmp_path: Path) -> None:
        repo = init_git_repo(tmp_path / "repo")
        self._set_origin(repo, self.UNRELATED_ORIGIN)
        assert self._scope_status(repo, tmp_path) == 1

    def test_returns_the_origin_test_s_status_when_it_is_neither_zero_nor_one(self, tmp_path: Path) -> None:
        """Status 127 stands for an origin test missing from a stale `_lib.sh`; the layout arm must not mask it."""
        repo = repo_tracking_claude_config_layout(tmp_path / "repo")
        status = self._scope_status(repo, tmp_path, call_prefix="unset -f _lib_origin_names_claude_config; ")
        assert status == 127

    def test_returns_127_when_the_layout_test_is_missing_and_the_origin_does_not_match(self, tmp_path: Path) -> None:
        """The origin test answers 1, so the predicate reaches the layout call. The repository keeps the layout
        path, so a predicate that masked the missing function with `||` would answer 0."""
        repo = repo_tracking_claude_config_layout(tmp_path / "repo")
        self._set_origin(repo, self.UNRELATED_ORIGIN)
        status = self._scope_status(repo, tmp_path, call_prefix="unset -f _lib_repo_has_claude_config_layout; ")
        assert status == 127

    def test_returns_one_for_a_checkout_of_a_commit_that_predates_the_layout_path(self, tmp_path: Path) -> None:
        repo = _repo_checked_out_before_the_layout_path_was_added(tmp_path / "repo")
        self._set_origin(repo, self.UNRELATED_ORIGIN)
        assert self._scope_status(repo, tmp_path) == 1

    def test_returns_zero_for_a_checkout_of_a_commit_that_predates_the_layout_path_under_a_matching_origin(
        self, tmp_path: Path
    ) -> None:
        repo = _repo_checked_out_before_the_layout_path_was_added(tmp_path / "repo")
        self._set_origin(repo, "git@github.com:someone/claude-config.git")
        assert self._scope_status(repo, tmp_path) == 0

    def test_falls_through_to_the_layout_test_when_the_origin_read_fails(self, tmp_path: Path) -> None:
        """A syntactically invalid `.git/config` makes git's config read exit 128 or higher, which the origin
        test counts as no match. The layout file in the repository then answers 0."""
        repo = repo_tracking_claude_config_layout(tmp_path / "repo")
        (absolute_git_dir(repo) / "config").write_text("[unterminated\n")
        env = {**os.environ, **isolated_git_config_env(tmp_path / "home")}
        origin_read = subprocess.run(
            ["git", "-C", str(repo), "config", "--get-all", "remote.origin.url"],
            capture_output=True, text=True, check=False, env=env,
        )
        assert origin_read.returncode >= 128, f"premise: the origin read must fail; stderr: {origin_read.stderr}"
        assert self._scope_status(repo, tmp_path) == 0

    @staticmethod
    def _scope_arm_after(calls: str, tmp_path: Path) -> str:
        """The value of `_LIB_REPO_SCOPE_ARM` after running `calls`, each a predicate call separated by `;`."""
        env = {**os.environ, **isolated_git_config_env(tmp_path / "home")}
        result = _run_lib_call(f'{calls}; printf %s "$_LIB_REPO_SCOPE_ARM"', env=env)
        assert result.returncode == 0, result.stderr
        return result.stdout

    def test_reports_the_origin_arm_for_a_matching_origin(self, tmp_path: Path) -> None:
        repo = repo_tracking_claude_config_layout(tmp_path / "repo")
        self._set_origin(repo, "git@github.com:someone/claude-config.git")
        assert self._scope_arm_after(f'_lib_repo_is_claude_config "{repo}"', tmp_path) == "origin"

    def test_reports_the_layout_arm_for_a_layout_only_repository(self, tmp_path: Path) -> None:
        repo = repo_tracking_claude_config_layout(tmp_path / "repo")
        self._set_origin(repo, self.UNRELATED_ORIGIN)
        assert self._scope_arm_after(f'_lib_repo_is_claude_config "{repo}"', tmp_path) == "layout"

    def test_reports_no_arm_for_an_unrelated_repository(self, tmp_path: Path) -> None:
        repo = init_git_repo_with_commit(tmp_path / "repo")
        self._set_origin(repo, self.UNRELATED_ORIGIN)
        assert self._scope_arm_after(f'_lib_repo_is_claude_config "{repo}"', tmp_path) == ""

    def test_reports_no_arm_after_an_origin_answer_when_the_next_call_answers_one(self, tmp_path: Path) -> None:
        """The global is reset on every call, so an `origin` answer for one repository does not leak into the next."""
        matching_repo = init_git_repo_with_commit(tmp_path / "matching")
        self._set_origin(matching_repo, "git@github.com:someone/claude-config.git")
        unrelated_repo = init_git_repo_with_commit(tmp_path / "unrelated")
        self._set_origin(unrelated_repo, self.UNRELATED_ORIGIN)
        calls = f'_lib_repo_is_claude_config "{matching_repo}"; _lib_repo_is_claude_config "{unrelated_repo}"'
        assert self._scope_arm_after(calls, tmp_path) == ""


class TestLibCaseInsensitiveNe:
    """Direct unit coverage for _lib_case_insensitive_ne -- the shared
    nocasematch comparison for owner/repo slugs, since GitHub treats them
    case-insensitively."""

    def test_exact_match_returns_false(self) -> None:
        result = _run_lib_call('_lib_case_insensitive_ne "foo/bar" "foo/bar"', env=dict(os.environ))
        assert result.returncode == 1

    def test_case_differing_match_returns_false(self) -> None:
        result = _run_lib_call('_lib_case_insensitive_ne "Foo/Bar" "foo/bar"', env=dict(os.environ))
        assert result.returncode == 1

    def test_genuine_mismatch_returns_true(self) -> None:
        result = _run_lib_call('_lib_case_insensitive_ne "foo/bar" "foo/baz"', env=dict(os.environ))
        assert result.returncode == 0

    @pytest.mark.parametrize("initial_option_state", ["-s", "-u"], ids=["caller_set", "caller_unset"])
    def test_leaves_the_callers_nocasematch_setting_as_it_found_it(self, initial_option_state: str) -> None:
        result = _run_lib_call(
            f'shopt {initial_option_state} nocasematch; _lib_case_insensitive_ne "foo/bar" "foo/baz"; '
            "shopt -p nocasematch",
            env=dict(os.environ),
        )
        assert result.stdout == f"shopt {initial_option_state} nocasematch\n"
