"""Behavioural-parity tests for the shared default-branch-resolution
helper's four gh-free entry points, against a single non-`main`-default
fixture: branch-divergence-status.sh, check-branch-divergence.sh,
set-session-title-from-branch.sh, and a direct `bash -c '. _lib.sh; ...'`
call on each of _lib.sh's two layers.

pr-diff-against-base.sh is excluded -- its own test file already carries
deeper non-`main`-default coverage (trunk, slash-containing names,
multi-candidate precedence) that duplicating here would add nothing to.
cleanup-merged-branches.sh is excluded -- test_cleanup_merged_branches.py's
"resolves and excludes" test already covers its resolve/exclude parity,
with the `gh` scaffolding that already exists in its own file.
guard-settings-session-keys.sh is also gh-free and also calls the guess
layer, but is excluded here too -- test_guard_settings_session_keys.py
already carries its own dedicated non-`main`-default and candidate-probe
coverage.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from helpers import HOOKS_DIR, run_hook_context, run_hook_session_start

from .conftest import _commit, _make_repo_with_remote

_BRANCH_DIVERGENCE_STATUS = Path(__file__).parent.parent / "branch-divergence-status.sh"
_CHECK_BRANCH_DIVERGENCE = HOOKS_DIR / "check-branch-divergence.sh"
_SET_SESSION_TITLE = HOOKS_DIR / "set-session-title-from-branch.sh"
_LIB_SH = HOOKS_DIR / "_lib.sh"


def _run_branch_divergence_status(repo: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(_BRANCH_DIVERGENCE_STATUS)], cwd=repo, capture_output=True, text=True, check=False,
    )


def _run_check_branch_divergence(repo: Path) -> str | None:
    return run_hook_context(_CHECK_BRANCH_DIVERGENCE, {}, cwd=repo)


def _title(repo: Path, isolated_home: Path) -> str | None:
    return run_hook_session_start(
        _SET_SESSION_TITLE,
        {"source": "startup", "cwd": str(repo)},
        cwd=repo,
        home=isolated_home,
    )


def _run_layer(func: str, repo_root: Path) -> subprocess.CompletedProcess:
    """Invoke one of _lib.sh's two default-branch layers directly, with no
    script or hook wrapper around it -- matches test_lib.py's
    _resolve_default_branch helper."""
    return subprocess.run(
        ["bash", "-c", f'. {_LIB_SH}; {func} "$1"', "bash", str(repo_root)],
        capture_output=True,
        text=True,
        check=False,
    )


def _advance_remote_default_branch(
    bare: Path, tmp_path: Path, default_branch: str, message: str
) -> None:
    """Push a new commit to bare's default branch from a second clone,
    simulating another contributor's push landing on origin after local
    last fetched."""
    push = tmp_path / "advance-clone"
    subprocess.run(["git", "clone", "-q", str(bare), str(push)], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=push, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=push, check=True)
    _commit(push, message)
    subprocess.run(["git", "push", "-q", "origin", default_branch], cwd=push, check=True)


class TestOriginHeadSetReportsRealDefault:
    """origin/HEAD resolved: every entry point reports the fixture's real
    default (develop), never the conventional-name literal (main)."""

    def test_branch_divergence_status_reports_develop(self, tmp_path):
        local, _bare = _make_repo_with_remote(tmp_path, default_branch="develop")

        result = _run_branch_divergence_status(local)

        assert result.returncode == 0
        assert "Default branch: develop" in result.stdout
        assert "Default branch: main" not in result.stdout

    def test_check_branch_divergence_reports_develop(self, tmp_path):
        local, bare = _make_repo_with_remote(tmp_path, default_branch="develop")
        subprocess.run(["git", "checkout", "-q", "-b", "feature"], cwd=local, check=True)
        _advance_remote_default_branch(bare, tmp_path, "develop", "advance remote")

        ctx = _run_check_branch_divergence(local)

        assert ctx is not None
        assert "origin/develop" in ctx
        assert "origin/main" not in ctx

    def test_set_session_title_does_not_treat_main_as_default(self, tmp_path):
        """A local branch literally named `main` is not this fixture's real
        default (`develop`) -- if the hook wrongly resolved the default to
        `main` it would treat this branch as the default and stay silent."""
        local, _bare = _make_repo_with_remote(tmp_path, default_branch="develop")
        subprocess.run(["git", "checkout", "-q", "-b", "main"], cwd=local, check=True)
        isolated_home = tmp_path / "home"
        isolated_home.mkdir()

        title = _title(local, isolated_home)

        assert title == f"{local.name}/main"

    def test_direct_layer_calls_report_develop(self, tmp_path):
        local, _bare = _make_repo_with_remote(tmp_path, default_branch="develop")

        no_guess = _run_layer("_lib_default_branch_from_origin_head", local)
        guess = _run_layer("_lib_default_branch_or_guess", local)

        assert no_guess.returncode == 0
        assert no_guess.stdout == "develop"
        assert guess.returncode == 0
        assert guess.stdout == "develop"


class TestOriginHeadUnsetNoGuessSitesRefuse:
    """origin/HEAD unset: the no-guess layer and its three callers refuse
    rather than guess a conventional name. The guessing layer still
    answers here, because `develop` happens to be one of its probed
    candidates -- the split is that it tries at all, not that it's wrong.
    The direct-layer test below also runs against a fixture whose local
    checked-out branch is still `develop`, so it cannot independently
    distinguish a correct `origin/develop` probe from a hypothetical
    regression that reads the local branch name instead -- that
    distinction is closed at the unit layer, in test_lib.py."""

    def test_branch_divergence_status_aborts(self, tmp_path):
        local, _bare = _make_repo_with_remote(tmp_path, default_branch="develop")
        subprocess.run(["git", "remote", "set-head", "origin", "--delete"], cwd=local, check=True)

        result = _run_branch_divergence_status(local)

        assert result.returncode == 1
        assert "could not resolve origin/HEAD" in result.stderr

    def test_check_branch_divergence_stays_silent(self, tmp_path):
        local, _bare = _make_repo_with_remote(tmp_path, default_branch="develop")
        subprocess.run(["git", "checkout", "-q", "-b", "feature"], cwd=local, check=True)
        subprocess.run(["git", "remote", "set-head", "origin", "--delete"], cwd=local, check=True)

        ctx = _run_check_branch_divergence(local)

        assert ctx is None

    def test_set_session_title_stays_silent(self, tmp_path):
        local, _bare = _make_repo_with_remote(tmp_path, default_branch="develop")
        subprocess.run(["git", "checkout", "-q", "-b", "main"], cwd=local, check=True)
        subprocess.run(["git", "remote", "set-head", "origin", "--delete"], cwd=local, check=True)
        isolated_home = tmp_path / "home"
        isolated_home.mkdir()

        title = _title(local, isolated_home)

        assert title is None

    def test_direct_no_guess_layer_fails_while_guess_layer_still_answers(self, tmp_path):
        local, _bare = _make_repo_with_remote(tmp_path, default_branch="develop")
        subprocess.run(["git", "remote", "set-head", "origin", "--delete"], cwd=local, check=True)

        no_guess = _run_layer("_lib_default_branch_from_origin_head", local)
        guess = _run_layer("_lib_default_branch_or_guess", local)

        assert no_guess.returncode != 0
        assert no_guess.stdout == ""
        assert guess.returncode == 0
        assert guess.stdout == "develop"
