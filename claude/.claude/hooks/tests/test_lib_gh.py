"""Unit tests for _lib.sh's _lib_gh, _lib_origin_owner_repo and _lib_case_insensitive_ne."""
from __future__ import annotations

import json
import os
import subprocess
import textwrap
from pathlib import Path

import pytest
from helpers import init_git_repo_with_commit

from ._lib_sh_helpers import _LIB_SH, _run_lib_call

# --- _lib_gh -----------------------------------------------------------


def _gh_shim_dir(tmp_path: Path, call_log: Path, exit_code: int = 0) -> Path:
    """PATH dir holding a fake `gh` that records argv and
    GH_HOST/GH_ENTERPRISE_TOKEN to call_log (one JSON object) and exits
    exit_code."""
    shim_dir = tmp_path / "gh-shim"
    shim_dir.mkdir()
    gh_shim = shim_dir / "gh"
    gh_shim.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import json
        import os
        import sys
        record = {{
            "args": sys.argv[1:],
            "GH_HOST": os.environ.get("GH_HOST"),
            "GH_ENTERPRISE_TOKEN": os.environ.get("GH_ENTERPRISE_TOKEN"),
        }}
        with open({str(call_log)!r}, "a") as f:
            f.write(json.dumps(record) + chr(10))
        sys.exit({exit_code})
    """))
    gh_shim.chmod(0o755)
    return shim_dir


class TestLibGh:
    """Direct unit coverage for _lib_gh -- the one call every review-pr
    script routes its own `gh` invocations through."""

    def test_forwards_argv_and_ambient_gh_host_and_enterprise_token_unchanged(self, tmp_path: Path) -> None:
        call_log = tmp_path / "calls.jsonl"
        shim_dir = _gh_shim_dir(tmp_path, call_log)
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

    def test_propagates_gh_own_nonzero_exit_status(self, tmp_path: Path) -> None:
        call_log = tmp_path / "calls.jsonl"
        shim_dir = _gh_shim_dir(tmp_path, call_log, exit_code=1)
        env = {**os.environ, "PATH": f"{shim_dir}{os.pathsep}{os.environ['PATH']}"}
        result = _run_lib_call('_lib_gh 5 pr view 42', env=env)
        assert result.returncode == 1


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
