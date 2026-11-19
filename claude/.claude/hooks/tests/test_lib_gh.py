"""Unit tests for _lib.sh's _lib_gh, _lib_origin_owner_repo, _lib_origin_names_claude_config and
_lib_case_insensitive_ne."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from helpers import (
    init_git_repo,
    init_git_repo_with_commit,
    isolated_git_config_env,
    locale_that_folds_ascii_capital_i_to_a_non_ascii_letter,
)

from ._lib_sh_helpers import _LIB_SH, _gh_shim_dir, _run_lib_call

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

    def test_returns_zero_when_a_non_last_origin_url_value_contains_either_name(self, tmp_path: Path) -> None:
        """`remote.origin.url` is multi-valued and `git config --get` prints only the last value."""
        repo = self._repo_with_remote(tmp_path, "origin", "https://github.com/someone/claude-config.git")
        subprocess.run(
            ["git", "config", "--add", "remote.origin.url", "git@github.com:someone/other-app.git"],
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
