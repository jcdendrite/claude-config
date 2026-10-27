"""Tests for branch-divergence-status.sh.

The script's external dependencies are git, run against a local bare-remote
fixture built by conftest.py's helpers, and `gh pr view` for the no-key read of
an open PR's base. Every run but the gh-absent one puts a `gh` shim first on
PATH. The shim records its calls, prints the reply the test configures, and by
default exits 1 with empty output. A run with a key set asserts the shim was
never called.
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest
from helpers import (
    _make_git_exiting_with_status,
    assert_cap_engaged,
    scaled_shim_sleep,
    write_scaled_timeout_shim,
)

from .conftest import (
    _STACKED_BRANCH,
    _commit,
    _curated_path_without_direnv,
    _init_repo,
    _make_feature_branch,
    _make_gh_pr_view_shim,
    _make_repo_with_remote,
    _make_stacked_repo,
    _read_logged_calls,
    _shim_call_logging_command,
)

# Path to the script under test (resolved relative to this file)
_SCRIPT = Path(__file__).parent.parent / "branch-divergence-status.sh"


def _branch_has_key(repo: Path) -> bool:
    """True when the repo's current branch has `branch.<branch>.gh-merge-base` set."""
    branch = subprocess.run(
        ["git", "symbolic-ref", "--short", "-q", "HEAD"], cwd=repo, capture_output=True, text=True, check=False
    ).stdout.strip()
    if not branch:
        return False
    key_read = subprocess.run(
        ["git", "config", "--get", f"branch.{branch}.gh-merge-base"], cwd=repo, capture_output=True, check=False
    )
    return key_read.returncode == 0


def _run_script(
    repo: Path,
    env: dict | None = None,
    *,
    gh_reply: str = "",
    gh_exit_status: int = 1,
    gh_stderr_line: str | None = None,
    gh_installed: bool = True,
) -> subprocess.CompletedProcess:
    """Run the script with a gh shim first on PATH (see conftest's
    _make_gh_pr_view_shim) that records each call, prints gh_reply, writes
    gh_stderr_line to stderr when given, and exits gh_exit_status (default:
    exit 1, no output). With gh_installed False no shim is installed, so gh is
    absent only when env's PATH holds none. When the current branch has a key
    set, assert no call was recorded: a key wins without reading the PR."""
    shim_dir = repo.parent / "gh-shim-bin"
    branch_has_key = _branch_has_key(repo)
    run_env = dict(os.environ) if env is None else dict(env)
    if gh_installed:
        _make_gh_pr_view_shim(shim_dir, reply=gh_reply, exit_status=gh_exit_status, stderr_line=gh_stderr_line)
        run_env["PATH"] = os.pathsep.join([str(shim_dir), run_env.get("PATH", "")])
    result = subprocess.run(
        [str(_SCRIPT)],
        cwd=str(repo),
        env=run_env,
        capture_output=True,
        text=True,
        check=False,
    )
    if branch_has_key:
        assert _gh_calls(repo) == [], f"script called gh with a key set: {_gh_calls(repo)}"
    return result


def _gh_calls(repo: Path) -> list[list[str]]:
    """The argument list of each call the gh shim recorded for the last _run_script call."""
    return _read_logged_calls(repo.parent / "gh-shim-bin" / "gh-calls.log")


def _advance_remote_default_branch(bare: Path, tmp_path: Path, message: str) -> None:
    """Push a new commit to bare's default branch from a second clone, simulating
    another contributor's push landing on origin after local last fetched."""
    bare_clone = tmp_path / "advance-clone"
    subprocess.run(["git", "clone", "-q", str(bare), str(bare_clone)], check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=bare_clone, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=bare_clone, check=True)
    _commit(bare_clone, message)
    subprocess.run(["git", "push", "-q", "origin", "main"], cwd=bare_clone, check=True)


class TestInSync:
    """Local HEAD already matches origin/main -- behind-count 0."""

    def test_reports_default_branch_and_zero_behind_count(self, tmp_path):
        local, _bare = _make_repo_with_remote(tmp_path)

        result = _run_script(local)

        assert result.returncode == 0
        assert "Default branch: main" in result.stdout
        assert "Behind: 0" in result.stdout


class TestBehindWithCleanMerge:
    """Remote default branch advanced past local, no conflicting changes."""

    def test_reports_nonzero_behind_and_clean_trial_merge(self, tmp_path):
        local, bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/unrelated")
        subprocess.run(["git", "branch", "-D", "feat/unrelated"], cwd=bare, check=True)

        _advance_remote_default_branch(bare, tmp_path, "advance remote")

        result = _run_script(local)

        assert result.returncode == 0
        assert "Behind: 1" in result.stdout
        assert "CLEAN" in result.stdout


class TestBehindWithConflicts:
    """Remote default branch advanced with a change conflicting with an unpushed
    local commit on the same file."""

    def test_reports_nonzero_behind_and_names_conflicting_file(self, tmp_path):
        local, bare = _make_repo_with_remote(tmp_path)

        # Unpushed local commit editing file.txt.
        _commit(local, "local change")

        # Remote commit editing the same file.txt differently, from the same base.
        _advance_remote_default_branch(bare, tmp_path, "remote change")

        result = _run_script(local)

        assert result.returncode == 0
        assert "Behind: 1" in result.stdout
        assert "CONFLICT" in result.stdout
        assert "file.txt" in result.stdout


class TestBehindWithUnrelatedHistories:
    """HEAD is an orphan branch, so `git merge-tree` refuses to merge it with
    origin/main and no verdict can be computed."""

    def test_exits_nonzero_without_a_trial_merge_line_naming_the_failure_and_git(self, tmp_path):
        local, _bare = _make_repo_with_remote(tmp_path)
        subprocess.run(["git", "checkout", "-q", "--orphan", "orphan-feature"], cwd=local, check=True)
        _commit(local, "orphan work")

        result = _run_script(local)

        assert result.returncode == 1
        assert "Behind: 1" in result.stdout
        assert "Trial merge:" not in result.stdout
        assert "trial merge of origin/main into HEAD could not complete" in result.stderr
        assert "git version" in result.stderr


class TestNotAGitRepository:
    def test_exits_nonzero_with_message(self, tmp_path):
        not_a_repo = tmp_path / "not-a-repo"
        not_a_repo.mkdir()

        result = _run_script(not_a_repo)

        assert result.returncode == 1
        assert "not inside a git repository" in result.stderr


class TestNoOriginRemote:
    def test_exits_nonzero_with_message(self, tmp_path):
        repo = tmp_path / "repo"
        _init_repo(repo)
        _commit(repo, "init")

        result = _run_script(repo)

        assert result.returncode == 1
        assert "could not resolve origin/HEAD" in result.stderr


class TestOriginHeadUnset:
    def test_exits_nonzero_with_message(self, tmp_path):
        local, _bare = _make_repo_with_remote(tmp_path)
        subprocess.run(["git", "remote", "set-head", "origin", "--delete"], cwd=local, check=True)

        result = _run_script(local)

        assert result.returncode == 1
        assert "could not resolve origin/HEAD" in result.stderr


class TestDanglingOriginHeadWithRemotelyLiveTarget:
    """origin/HEAD's symref resolves, but its local tracking ref is gone
    while the branch is still live on the remote. The shared helper
    verifies the local ref resolves before this script's own recovering
    fetch ever runs, so this state is never recovered."""

    def test_exits_nonzero_before_the_recovering_fetch(self, tmp_path):
        local, _bare = _make_repo_with_remote(tmp_path)
        subprocess.run(
            ["git", "update-ref", "-d", "refs/remotes/origin/main"], cwd=local, check=True
        )

        result = _run_script(local)

        assert result.returncode == 1
        assert "could not resolve origin/HEAD" in result.stderr


class TestFetchFailure:
    def test_exits_nonzero_with_message(self, tmp_path):
        local, _bare = _make_repo_with_remote(tmp_path)
        subprocess.run(
            ["git", "remote", "set-url", "origin", str(tmp_path / "nonexistent.git")],
            cwd=local, check=True,
        )

        result = _run_script(local)

        assert result.returncode == 1
        assert "fetch of origin/main failed" in result.stderr


class TestNoTimeoutBinary:
    """_curated_path_without_direnv's tool list omits timeout/gtimeout (and
    gh/direnv), so it doubles as a minimal no-timeout PATH here."""

    def test_exits_nonzero_with_message(self, tmp_path):
        local, _bare = _make_repo_with_remote(tmp_path)
        env = {"PATH": _curated_path_without_direnv(tmp_path), "HOME": os.environ.get("HOME", "")}

        result = _run_script(local, env=env)

        assert result.returncode == 1
        assert "neither 'timeout' nor 'gtimeout' is available" in result.stderr
        assert _gh_calls(local) == []


def _advance_remote_stack(bare: Path, tmp_path: Path) -> None:
    """Push a commit adding remote.txt to bare's `stack` branch from a second clone."""
    clone = tmp_path / "advance-stack-clone"
    subprocess.run(["git", "clone", "-q", "-b", "stack", str(bare), str(clone)], check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=clone, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=clone, check=True)
    (clone / "remote.txt").write_text("remote stack work\n")
    subprocess.run(["git", "add", "remote.txt"], cwd=clone, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "advance stack"], cwd=clone, check=True)
    subprocess.run(["git", "push", "-q", "origin", "stack"], cwd=clone, check=True)


class TestNoKeyKeepsTheDefaultBranchLabel:
    def test_reports_default_branch_and_no_base_branch_line(self, tmp_path):
        local, _bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/plain")
        subprocess.run(["git", "checkout", "-q", "feat/plain"], cwd=local, check=True)

        result = _run_script(local)

        assert result.returncode == 0
        assert "Default branch: main" in result.stdout
        assert "Base branch:" not in result.stdout


class TestStackBaseKey:
    def test_reports_base_branch_and_in_sync_with_the_stack_base(self, tmp_path):
        local, _bare = _make_stacked_repo(tmp_path)

        result = _run_script(local)

        assert result.returncode == 0, result.stderr
        assert "Base branch: stack" in result.stdout
        assert "Default branch:" not in result.stdout
        assert "Behind: 0" in result.stdout
        assert "In sync with origin/stack." in result.stdout

    def test_behind_count_is_measured_against_the_stack_base(self, tmp_path):
        local, bare = _make_stacked_repo(tmp_path)
        _advance_remote_stack(bare, tmp_path)

        result = _run_script(local)

        assert result.returncode == 0, result.stderr
        assert "Base branch: stack" in result.stdout
        assert "Behind: 1" in result.stdout
        assert "CLEAN" in result.stdout

    def test_behind_count_is_measured_under_a_narrowed_fetch_refspec(self, tmp_path):
        """A refspec narrowed to main leaves a plain `git fetch origin stack`
        unable to update origin/stack, so the script's named destination is
        what makes the count current."""
        local, bare = _make_stacked_repo(tmp_path)
        subprocess.run(
            ["git", "config", "remote.origin.fetch", "+refs/heads/main:refs/remotes/origin/main"],
            cwd=local, check=True,
        )
        _advance_remote_stack(bare, tmp_path)

        result = _run_script(local)

        assert result.returncode == 0, result.stderr
        assert "Behind: 1" in result.stdout

    def test_stack_base_deleted_on_origin_stops_even_with_its_local_ref_present(self, tmp_path):
        local, bare = _make_stacked_repo(tmp_path)
        subprocess.run(["git", "branch", "-D", "stack"], cwd=bare, check=True)

        result = _run_script(local)

        assert result.returncode == 1
        assert result.stdout == ""
        assert result.stderr.count("\n") == 1
        assert result.stderr.startswith("branch-divergence-status.sh:")
        assert f"branch.{_STACKED_BRANCH}.gh-merge-base" in result.stderr
        assert "fetch failed" in result.stderr
        assert "retry the command before changing the key" in result.stderr
        assert 'run "git ls-remote origin refs/heads/stack":' in result.stderr
        assert "if it prints nothing, the base is not on origin" in result.stderr
        assert '"git fetch origin +refs/heads/stack:refs/remotes/origin/stack" by hand' in result.stderr
        assert "is stack, which cannot be used as the stack base" in result.stderr
        assert "The engineer runs the fix, not the agent" in result.stderr

    def test_verify_stops_when_a_fetch_reports_success_but_the_ref_is_missing(self, tmp_path):
        """The verify, not the fetch status, is what keeps an unresolvable ref from being measured."""
        local, _bare = _make_stacked_repo(tmp_path)
        subprocess.run(["git", "update-ref", "-d", "refs/remotes/origin/stack"], cwd=local, check=True)
        bin_dir = tmp_path / "bin-fetch-succeeds-silently"
        _make_git_exiting_with_status(bin_dir, "fetch", 0)
        env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "REAL_GIT": shutil.which("git")}

        result = _run_script(local, env=env)

        assert result.returncode == 1
        assert result.stdout == ""
        assert result.stderr.startswith("branch-divergence-status.sh:")
        assert "fetch failed" in result.stderr

    def test_local_branch_named_like_the_remote_tracking_ref_does_not_replace_the_base(self, tmp_path):
        """A local branch `origin/stack` outranks `origin/stack` in git's short-name lookup, so
        the script must measure through the fully qualified remote-tracking ref."""
        local, _bare = _make_stacked_repo(tmp_path)
        subprocess.run(["git", "checkout", "-q", "--detach", "main"], cwd=local, check=True)
        _commit(local, "unreachable from the stacked branch")
        subprocess.run(["git", "branch", "origin/stack"], cwd=local, check=True)
        subprocess.run(["git", "checkout", "-q", _STACKED_BRANCH], cwd=local, check=True)

        result = _run_script(local)

        assert result.returncode == 0, result.stderr
        assert "Behind: 0" in result.stdout

    def test_trial_merge_reads_the_remote_tracking_ref_not_a_shadow_local_branch(self, tmp_path):
        """A local branch `origin/stack` at a non-conflicting commit would make a short-name
        trial merge report CLEAN, so the conflicting real stack tip must still report CONFLICT."""
        local, bare = _make_stacked_repo(tmp_path)
        (local / "conflict.txt").write_text("local content\n")
        subprocess.run(["git", "add", "conflict.txt"], cwd=local, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "local conflict.txt"], cwd=local, check=True)
        clone = tmp_path / "conflicting-stack-clone"
        subprocess.run(["git", "clone", "-q", "-b", "stack", str(bare), str(clone)], check=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=clone, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=clone, check=True)
        (clone / "conflict.txt").write_text("remote content\n")
        subprocess.run(["git", "add", "conflict.txt"], cwd=clone, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "remote conflict.txt"], cwd=clone, check=True)
        subprocess.run(["git", "push", "-q", "origin", "stack"], cwd=clone, check=True)
        subprocess.run(["git", "branch", "origin/stack", "main"], cwd=local, check=True)

        result = _run_script(local)

        assert result.returncode == 0, result.stderr
        assert "Behind: 1" in result.stdout
        assert "CONFLICT" in result.stdout
        assert "conflict.txt" in result.stdout

    def test_invalid_key_stops_with_this_scripts_prefix(self, tmp_path):
        local, _bare = _make_stacked_repo(tmp_path, key_value="-stack")

        result = _run_script(local)

        assert result.returncode == 1
        assert result.stdout == ""
        assert result.stderr.startswith("branch-divergence-status.sh:")
        assert f"branch.{_STACKED_BRANCH}.gh-merge-base" in result.stderr
        assert "starts with '-'" in result.stderr

    def test_force_pushed_stack_base_updates_to_the_new_tip(self, tmp_path):
        """The fetch refspec's leading + lets a rewritten stack base replace
        a tip that is not an ancestor of the new one."""
        local, bare = _make_stacked_repo(tmp_path)
        clone = tmp_path / "rewrite-stack-clone"
        subprocess.run(["git", "clone", "-q", "-b", "stack", str(bare), str(clone)], check=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=clone, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=clone, check=True)
        subprocess.run(["git", "reset", "-q", "--hard", "HEAD~1"], cwd=clone, check=True)
        (clone / "rewritten.txt").write_text("rewritten stack work\n")
        subprocess.run(["git", "add", "rewritten.txt"], cwd=clone, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "rewritten stack"], cwd=clone, check=True)
        subprocess.run(["git", "push", "-q", "--force", "origin", "stack"], cwd=clone, check=True)

        result = _run_script(local)

        assert result.returncode == 0, result.stderr
        assert "Base branch: stack" in result.stdout
        assert "Behind: 1" in result.stdout

    def test_origin_prefixed_key_stops_naming_the_prefix(self, tmp_path):
        local, _bare = _make_stacked_repo(tmp_path, key_value="origin/stack")

        result = _run_script(local)

        assert result.returncode == 1
        assert result.stdout == ""
        assert result.stderr.startswith("branch-divergence-status.sh:")
        assert "starts with 'origin/'" in result.stderr

    def test_key_set_still_reports_the_base_when_origin_head_is_unset(self, tmp_path):
        local, _bare = _make_stacked_repo(tmp_path)
        subprocess.run(["git", "remote", "set-head", "origin", "--delete"], cwd=local, check=True)

        result = _run_script(local)

        assert result.returncode == 0, result.stderr
        assert "Base branch: stack" in result.stdout


def _make_stacked_repo_without_key(tmp_path: Path) -> tuple[Path, Path]:
    """The stacked layout of _make_stacked_repo with the gh-merge-base key unset,
    so the script's no-key read of an open PR's base is what selects `stack`."""
    local, bare = _make_stacked_repo(tmp_path)
    subprocess.run(["git", "config", "--unset", f"branch.{_STACKED_BRANCH}.gh-merge-base"], cwd=local, check=True)
    return local, bare


_PR_VIEW_ARGUMENTS = ["pr", "view", "--json", "state,baseRefName", "--jq", ".state, .baseRefName"]


_GH_PR_VIEW_CAP_SECONDS = 10


def _make_logging_timeout_shim(shim_dir: Path) -> Path:
    """Write a `timeout` into shim_dir that appends its arguments, one per line then a blank
    line, to the returned log and then runs the real timeout(1)/gtimeout(1) with the same
    arguments, so the cap still applies."""
    shim_dir.mkdir(parents=True, exist_ok=True)
    calls_log = shim_dir / "timeout-calls.log"
    real_timeout = shutil.which(_installed_timeout_name())
    (shim_dir / "timeout").write_text(
        "#!/bin/sh\n"
        f"{_shim_call_logging_command(calls_log)}"
        f"exec {shlex.quote(str(real_timeout))} \"$@\"\n"
    )
    (shim_dir / "timeout").chmod(0o755)
    return calls_log


def _installed_timeout_name() -> str:
    """The first of `timeout` and `gtimeout` on PATH, the pair the script's own check accepts."""
    return next(name for name in ("timeout", "gtimeout") if shutil.which(name))


def _remote_tracking_refs(repo: Path) -> str:
    """Every ref under refs/remotes/origin/ with its object name, for before/after comparison."""
    return subprocess.run(
        ["git", "for-each-ref", "--format=%(refname) %(objectname)", "refs/remotes/origin/"],
        cwd=repo, capture_output=True, text=True, check=True,
    ).stdout


def _local_config_listing(repo: Path) -> str:
    """Every entry of the repo's local git config, for before/after comparison."""
    return subprocess.run(
        ["git", "config", "--local", "--list"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout


class TestNoKeyReadsAnOpenPrsBase:
    def test_open_pr_on_another_base_prints_base_branch_and_writes_no_key(self, tmp_path):
        local, _bare = _make_stacked_repo_without_key(tmp_path)
        local_config_before = _local_config_listing(local)

        result = _run_script(local, gh_reply="OPEN\nstack\n", gh_exit_status=0)

        assert result.returncode == 0, result.stderr
        assert "Base branch: stack" in result.stdout
        assert "Default branch:" not in result.stdout
        assert "In sync with origin/stack." in result.stdout
        assert result.stderr == ""
        assert _gh_calls(local) == [_PR_VIEW_ARGUMENTS]
        key_read = subprocess.run(
            ["git", "config", "--get", f"branch.{_STACKED_BRANCH}.gh-merge-base"], cwd=local, check=False
        )
        assert key_read.returncode == 1
        assert _local_config_listing(local) == local_config_before

    def test_open_pr_base_using_every_allowed_punctuation_class_is_fetched_and_measured(self, tmp_path):
        """The push's own tracking-ref update is deleted first, so the script's fetch is
        the only thing that can create `refs/remotes/origin/feat/base-2.x_y`."""
        punctuated_base = "feat/base-2.x_y"
        local, _bare = _make_stacked_repo_without_key(tmp_path)
        subprocess.run(["git", "push", "-q", "origin", f"stack:refs/heads/{punctuated_base}"], cwd=local, check=True)
        subprocess.run(["git", "update-ref", "-d", f"refs/remotes/origin/{punctuated_base}"], cwd=local, check=True)
        assert f"origin/{punctuated_base}" not in _remote_tracking_refs(local)

        result = _run_script(local, gh_reply=f"OPEN\n{punctuated_base}\n", gh_exit_status=0)

        assert result.returncode == 0, result.stderr
        assert f"Base branch: {punctuated_base}" in result.stdout
        assert f"In sync with origin/{punctuated_base}." in result.stdout
        assert result.stderr == ""

    def test_behind_count_is_measured_against_an_open_prs_advanced_base(self, tmp_path):
        """`main` has not advanced, so a count taken against the default branch would be 0."""
        local, bare = _make_stacked_repo_without_key(tmp_path)
        _advance_remote_stack(bare, tmp_path)

        result = _run_script(local, gh_reply="OPEN\nstack\n", gh_exit_status=0)

        assert result.returncode == 0, result.stderr
        assert "Base branch: stack" in result.stdout
        assert "Behind: 1" in result.stdout
        assert "CLEAN" in result.stdout
        assert result.stderr == ""

    def test_open_pr_on_the_default_branch_prints_default_branch(self, tmp_path):
        local, _bare = _make_stacked_repo_without_key(tmp_path)

        result = _run_script(local, gh_reply="OPEN\nmain\n", gh_exit_status=0)

        assert result.returncode == 0, result.stderr
        assert "Default branch: main" in result.stdout
        assert "Base branch:" not in result.stdout
        assert result.stderr == ""

    @pytest.mark.parametrize(
        "gh_reply",
        [
            pytest.param("MERGED\nstack\n", id="merged-pr"),
            pytest.param("CLOSED\nstack\n", id="closed-pr"),
            pytest.param("", id="empty-reply"),
            pytest.param("OPEN\n", id="one-line-open"),
            pytest.param("OPEN\nstack\nextra\n", id="three-lines"),
        ],
    )
    def test_reply_that_is_not_an_open_prs_base_measures_the_default_branch_with_no_notice(self, tmp_path, gh_reply):
        """`stack` exists on origin, so a reply read as an open PR's base would print `Base branch: stack`."""
        local, _bare = _make_stacked_repo_without_key(tmp_path)

        result = _run_script(local, gh_reply=gh_reply, gh_exit_status=0)

        assert result.returncode == 0, result.stderr
        assert "Default branch: main" in result.stdout
        assert "Base branch:" not in result.stdout
        assert result.stderr == ""

    def test_failed_gh_measures_the_default_branch_and_prints_the_notice(self, tmp_path):
        local, _bare = _make_stacked_repo_without_key(tmp_path)

        result = _run_script(local, gh_reply="", gh_exit_status=1)

        assert result.returncode == 0, result.stderr
        assert "Default branch: main" in result.stdout
        assert result.stderr.count("\n") == 1
        assert "gh pr view failed or found no PR; will measure against origin/main" in result.stderr

    def test_base_failing_the_character_check_falls_back_with_a_notice_that_does_not_echo_it(self, tmp_path):
        local, _bare = _make_stacked_repo_without_key(tmp_path)

        result = _run_script(local, gh_reply="OPEN\nstack;touch x\n", gh_exit_status=0)

        assert result.returncode == 0, result.stderr
        assert "Default branch: main" in result.stdout
        assert result.stderr.count("\n") == 1
        assert "is not a usable branch name" in result.stderr
        assert "will measure against origin/main" in result.stderr
        assert "touch" not in result.stderr

    def test_base_with_a_leading_dash_falls_back_with_a_notice_that_does_not_echo_it(self, tmp_path):
        local, _bare = _make_stacked_repo_without_key(tmp_path)

        result = _run_script(local, gh_reply="OPEN\n--upload-pack=x\n", gh_exit_status=0)

        assert result.returncode == 0, result.stderr
        assert "Default branch: main" in result.stdout
        assert result.stderr.count("\n") == 1
        assert "is not a usable branch name" in result.stderr
        assert "upload-pack" not in result.stderr

    def test_origin_prefixed_base_falls_back_and_fetches_no_remote_tracking_ref_for_it(self, tmp_path):
        """A remote branch `origin/stack` exists, so accepting the base would fetch and measure it
        instead of the default branch. The push's own tracking-ref update is deleted first, so
        a fetch of the base is the only thing that could bring `refs/remotes/origin/origin/stack` back."""
        local, _bare = _make_stacked_repo_without_key(tmp_path)
        subprocess.run(["git", "push", "-q", "origin", "stack:refs/heads/origin/stack"], cwd=local, check=True)
        subprocess.run(["git", "update-ref", "-d", "refs/remotes/origin/origin/stack"], cwd=local, check=True)
        refs_before = _remote_tracking_refs(local)
        assert "origin/origin/stack" not in refs_before

        result = _run_script(local, gh_reply="OPEN\norigin/stack\n", gh_exit_status=0)

        assert result.returncode == 0, result.stderr
        assert "Default branch: main" in result.stdout
        assert "Base branch:" not in result.stdout
        assert result.stderr.count("\n") == 1
        assert "is not a usable branch name" in result.stderr
        assert _remote_tracking_refs(local) == refs_before

    def test_head_base_falls_back_to_the_default_branch(self, tmp_path):
        """No remote branch `HEAD` is pushed: a push to one writes through the local
        origin/HEAD symref. Accepting the base would make the fetch of the missing
        `refs/heads/HEAD` exit 1."""
        local, _bare = _make_stacked_repo_without_key(tmp_path)

        result = _run_script(local, gh_reply="OPEN\nHEAD\n", gh_exit_status=0)

        assert result.returncode == 0, result.stderr
        assert "Default branch: main" in result.stdout
        assert "Base branch:" not in result.stdout
        assert result.stderr.count("\n") == 1
        assert "is not a usable branch name" in result.stderr

    def test_base_with_a_non_breaking_space_falls_back_without_echoing_it(self, tmp_path):
        local, _bare = _make_stacked_repo_without_key(tmp_path)

        result = _run_script(local, gh_reply="OPEN\nstack\u00a0x\n", gh_exit_status=0)

        assert result.returncode == 0, result.stderr
        assert "Default branch: main" in result.stdout
        assert result.stderr.count("\n") == 1
        assert "is not a usable branch name" in result.stderr
        assert "stack" not in result.stderr
        assert "\u00a0" not in result.stderr

    def test_failed_gh_stderr_is_not_forwarded_so_only_the_scripts_own_notice_remains(self, tmp_path):
        local, _bare = _make_stacked_repo_without_key(tmp_path)

        result = _run_script(local, gh_exit_status=1, gh_stderr_line="gh: no pull requests found for branch")

        assert result.returncode == 0, result.stderr
        assert "Default branch: main" in result.stdout
        assert result.stderr.count("\n") == 1
        assert "gh pr view failed or found no PR; will measure against origin/main" in result.stderr
        assert "no pull requests found" not in result.stderr

    def test_successful_gh_stderr_is_not_forwarded_so_a_run_that_reads_the_base_leaves_stderr_empty(self, tmp_path):
        local, _bare = _make_stacked_repo_without_key(tmp_path)

        result = _run_script(
            local, gh_reply="OPEN\nstack\n", gh_exit_status=0, gh_stderr_line="gh: a warning on a successful call"
        )

        assert result.returncode == 0, result.stderr
        assert "Base branch: stack" in result.stdout
        assert result.stderr == ""

    def test_gh_absent_from_path_measures_the_default_branch_with_one_notice(self, tmp_path):
        local, _bare = _make_stacked_repo_without_key(tmp_path)
        path_without_gh = Path(_curated_path_without_direnv(tmp_path))
        timeout_name = _installed_timeout_name()
        (path_without_gh / timeout_name).symlink_to(shutil.which(timeout_name))
        env = {"PATH": str(path_without_gh), "HOME": os.environ.get("HOME", "")}

        result = _run_script(local, env=env, gh_installed=False)

        assert result.returncode == 0, result.stderr
        assert "Default branch: main" in result.stdout
        assert result.stderr.count("\n") == 1
        assert "gh pr view failed or found no PR; will measure against origin/main" in result.stderr

    def test_gh_pr_view_runs_under_a_10_second_cap_with_a_2_second_kill_grace(self, tmp_path):
        local, _bare = _make_stacked_repo_without_key(tmp_path)
        timeout_shim_dir = tmp_path / "timeout-shim-bin"
        timeout_calls_log = _make_logging_timeout_shim(timeout_shim_dir)
        env = {**os.environ, "PATH": os.pathsep.join([str(timeout_shim_dir), os.environ.get("PATH", "")])}

        result = _run_script(local, env=env, gh_reply="OPEN\nstack\n", gh_exit_status=0)

        assert result.returncode == 0, result.stderr
        assert "Base branch: stack" in result.stdout
        capped_gh_calls = [call for call in _read_logged_calls(timeout_calls_log) if "gh" in call]
        assert capped_gh_calls == [["-k", "2", str(_GH_PR_VIEW_CAP_SECONDS), "gh", *_PR_VIEW_ARGUMENTS]]

    def test_hung_gh_is_killed_at_the_cap_and_measures_the_default_branch_with_one_notice(self, tmp_path):
        local, _bare = _make_stacked_repo_without_key(tmp_path)
        shim_dir = tmp_path / "hung-gh-bin"
        shim_dir.mkdir()
        hung_gh = shim_dir / "gh"
        hung_gh.write_text(f"#!/bin/sh\nexec sleep {scaled_shim_sleep(_GH_PR_VIEW_CAP_SECONDS + 2)}\n")
        hung_gh.chmod(0o755)
        write_scaled_timeout_shim(shim_dir)
        env = {**os.environ, "PATH": os.pathsep.join([str(shim_dir), os.environ.get("PATH", "")])}

        with assert_cap_engaged(shim_dir, production_cap=_GH_PR_VIEW_CAP_SECONDS, command="gh"):
            result = _run_script(local, env=env, gh_installed=False)

        assert result.returncode == 0, result.stderr
        assert "Default branch: main" in result.stdout
        assert result.stderr.count("\n") == 1
        assert "gh pr view failed or found no PR; will measure against origin/main" in result.stderr

    def test_reply_with_carriage_returns_is_unparsed_and_measures_the_default_branch_with_no_notice(self, tmp_path):
        """`$(...)` strips only newlines, so the state reads `OPEN\\r`, which is not `OPEN`."""
        local, _bare = _make_stacked_repo_without_key(tmp_path)

        result = _run_script(local, gh_reply="OPEN\r\nstack\r\n", gh_exit_status=0)

        assert result.returncode == 0, result.stderr
        assert "Default branch: main" in result.stdout
        assert "Base branch:" not in result.stdout
        assert result.stderr == ""

    def test_detached_head_with_no_key_attempts_one_gh_pr_view_call(self, tmp_path):
        """A detached HEAD has no branch to hold a key, so the script still makes its one gh read.
        What `gh pr view` returns there is gh's to decide, so only the attempt is asserted."""
        local, _bare = _make_stacked_repo_without_key(tmp_path)
        subprocess.run(["git", "checkout", "-q", "--detach"], cwd=local, check=True)

        _run_script(local, gh_reply="OPEN\nstack\n", gh_exit_status=0)

        assert _gh_calls(local) == [_PR_VIEW_ARGUMENTS]

    def test_base_git_rejects_as_a_ref_name_exits_1_with_the_open_prs_base_fetch_line(self, tmp_path):
        """`a..b` passes the shape check, so the fetch's own refspec validation is what stops it."""
        local, _bare = _make_stacked_repo_without_key(tmp_path)

        result = _run_script(local, gh_reply="OPEN\na..b\n", gh_exit_status=0)

        assert result.returncode == 1
        assert result.stdout == ""
        assert result.stderr.count("\n") == 1
        assert "fetch of the open PR's base origin/a..b failed" in result.stderr

    def test_pr_base_deleted_on_origin_prints_the_open_prs_base_fetch_line_and_exits_1(self, tmp_path):
        local, bare = _make_stacked_repo_without_key(tmp_path)
        subprocess.run(["git", "branch", "-D", "stack"], cwd=bare, check=True)

        result = _run_script(local, gh_reply="OPEN\nstack\n", gh_exit_status=0)

        assert result.returncode == 1
        assert result.stdout == ""
        assert result.stderr.count("\n") == 1
        assert result.stderr.startswith("branch-divergence-status.sh:")
        assert "fetch of the open PR's base origin/stack failed" in result.stderr
        assert '"git fetch origin +refs/heads/stack:refs/remotes/origin/stack" by hand' in result.stderr
        assert "gh-merge-base" not in result.stderr

    def test_key_set_wins_and_makes_no_gh_call(self, tmp_path):
        local, _bare = _make_stacked_repo(tmp_path)

        result = _run_script(local, gh_reply="OPEN\nmain\n", gh_exit_status=0)

        assert result.returncode == 0, result.stderr
        assert "Base branch: stack" in result.stdout
        assert _gh_calls(local) == []
