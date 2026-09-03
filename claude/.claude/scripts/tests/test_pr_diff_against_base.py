"""Tests for pr-diff-against-base.sh.

gh is replaced in every test by a PATH shim answering `gh pr view --json
baseRefName --jq .baseRefName` -- a different, narrower shape than
test_cleanup_merged_branches.py's `_gh_shim_source` (which simulates
`gh pr list --head`), so this file writes its own. Real git operations run
against temporary repos built via conftest.py's shared scaffolding.
"""
from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

from .conftest import _commit, _init_repo, _make_feature_branch, _make_repo_with_remote

# Path to the script under test (resolved relative to this file)
_SCRIPT = Path(__file__).parent.parent / "pr-diff-against-base.sh"


def _gh_shim_source(base_ref: str | None) -> str:
    """Return source for a gh shim answering `gh pr view --json baseRefName
    --jq .baseRefName`.

    base_ref=None models `gh pr view` failing (no PR open for this branch,
    or gh not authenticated) -- the shim exits 1 with no stdout, exercising
    pr-diff-against-base.sh's default-branch fallback path.
    """
    body = "sys.exit(1)" if base_ref is None else f"print({base_ref!r})"
    return textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import sys
        args = sys.argv[1:]
        if args[:2] == ["pr", "view"] and "--json" in args and "baseRefName" in args:
            {body}
        else:
            sys.exit(1)
    """)


def _env_with_gh_shim(tmp_path: Path, base_ref: str | None) -> dict:
    """Build an env with a gh shim reporting base_ref prepended to PATH."""
    shim_dir = tmp_path / "gh_shim"
    shim_dir.mkdir()
    gh_shim = shim_dir / "gh"
    gh_shim.write_text(_gh_shim_source(base_ref))
    gh_shim.chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join([str(shim_dir), env.get("PATH", "")])
    return env


def _run_script(repo: Path, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(_SCRIPT)], cwd=str(repo), env=env, capture_output=True, text=True, check=False,
    )


class TestNormalPathAgainstMain:
    def test_diverged_feature_branch_diff_mentions_changed_file(self, tmp_path):
        local, _bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/add-thing")
        subprocess.run(["git", "checkout", "-q", "feat/add-thing"], cwd=local, check=True)

        env = _env_with_gh_shim(tmp_path, "main")
        result = _run_script(local, env)

        assert result.returncode == 0
        assert "diff --git a/file.txt b/file.txt" in result.stdout
        assert "+work on feat/add-thing" in result.stdout


class TestGhPrViewFailureFallback:
    def test_gh_pr_view_failure_still_diffs_against_main(self, tmp_path):
        local, _bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/fallback")
        subprocess.run(["git", "checkout", "-q", "feat/fallback"], cwd=local, check=True)

        env = _env_with_gh_shim(tmp_path, None)
        result = _run_script(local, env)

        assert result.returncode == 0
        assert "diff --git a/file.txt b/file.txt" in result.stdout
        assert "+work on feat/fallback" in result.stdout

    def test_gh_pr_view_failure_resolves_non_main_default_branch(self, tmp_path):
        # "trunk" is outside main/master/develop so this only passes via
        # symbolic-ref, not the candidate loop.
        local, _bare = _make_repo_with_remote(tmp_path, default_branch="trunk")
        _make_feature_branch(local, "feat/on-trunk", return_to="trunk")
        subprocess.run(["git", "checkout", "-q", "feat/on-trunk"], cwd=local, check=True)

        env = _env_with_gh_shim(tmp_path, None)
        result = _run_script(local, env)

        assert result.returncode == 0
        assert "+work on feat/on-trunk" in result.stdout
        assert "defaulting base to trunk" in result.stderr

    def test_gh_pr_view_failure_resolves_slash_containing_default_branch(self, tmp_path):
        # ${origin_head#*/} strips only through the first "/", so a
        # multi-segment default branch name must survive intact.
        local, _bare = _make_repo_with_remote(tmp_path, default_branch="release/1.0")
        _make_feature_branch(local, "feat/on-release", return_to="release/1.0")
        subprocess.run(["git", "checkout", "-q", "feat/on-release"], cwd=local, check=True)

        env = _env_with_gh_shim(tmp_path, None)
        result = _run_script(local, env)

        assert result.returncode == 0
        assert "+work on feat/on-release" in result.stdout
        assert "defaulting base to release/1.0" in result.stderr


class TestMergeBaseFailure:
    def test_unresolvable_base_ref_aborts_naming_the_ref_on_stderr(self, tmp_path):
        # gh reports a base ref that was never fetched as origin/<name> locally --
        # distinct from the gh-failure fallback above, which always resolves against
        # the local origin/main that _make_repo_with_remote already sets up.
        local, _bare = _make_repo_with_remote(tmp_path)

        env = _env_with_gh_shim(tmp_path, "nonexistent-base")
        result = _run_script(local, env)

        assert result.returncode == 1
        assert result.stdout == ""
        assert "origin/nonexistent-base" in result.stderr


class TestCandidateLoopFallback:
    def test_missing_origin_head_falls_back_to_candidate_loop(self, tmp_path):
        # When origin/HEAD's symref is absent, the main/master/develop
        # candidate loop must still recover origin/main.
        local, _bare = _make_repo_with_remote(tmp_path)
        subprocess.run(["git", "remote", "set-head", "origin", "--delete"], cwd=local, check=True)
        _make_feature_branch(local, "feat/no-symref", return_to="main")
        subprocess.run(["git", "checkout", "-q", "feat/no-symref"], cwd=local, check=True)

        env = _env_with_gh_shim(tmp_path, None)
        result = _run_script(local, env)

        assert result.returncode == 0
        assert "+work on feat/no-symref" in result.stdout
        assert "defaulting base to main" in result.stderr

    def test_candidate_loop_prefers_main_when_multiple_candidates_exist(self, tmp_path):
        # main/master/develop are probed in that order, so main must win
        # when both exist as origin branches.
        local, _bare = _make_repo_with_remote(tmp_path)
        subprocess.run(["git", "checkout", "-q", "-b", "master"], cwd=local, check=True)
        subprocess.run(["git", "push", "-q", "origin", "master"], cwd=local, check=True)
        subprocess.run(["git", "checkout", "-q", "main"], cwd=local, check=True)
        subprocess.run(["git", "remote", "set-head", "origin", "--delete"], cwd=local, check=True)
        _make_feature_branch(local, "feat/multi-candidate", return_to="main")
        subprocess.run(["git", "checkout", "-q", "feat/multi-candidate"], cwd=local, check=True)

        env = _env_with_gh_shim(tmp_path, None)
        result = _run_script(local, env)

        assert result.returncode == 0
        assert "+work on feat/multi-candidate" in result.stdout
        assert "defaulting base to main" in result.stderr


class TestReportedBaseOverridesDefaultBranch:
    def test_stacked_pr_diffs_against_reported_base_not_repo_default(self, tmp_path):
        # gh's reported base must win over the repo's own default branch.
        local, _bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "staging")
        subprocess.run(["git", "checkout", "-q", "staging"], cwd=local, check=True)
        _make_feature_branch(local, "feat/stacked", return_to="staging")
        subprocess.run(["git", "checkout", "-q", "feat/stacked"], cwd=local, check=True)

        env = _env_with_gh_shim(tmp_path, "staging")
        result = _run_script(local, env)

        assert result.returncode == 0
        assert "+work on feat/stacked" in result.stdout
        assert "-work on staging" in result.stdout
        assert result.stderr == ""


class TestDefaultBranchUnresolvable:
    def test_repo_without_origin_aborts_naming_the_resolution_failure(self, tmp_path):
        # Regression test: no origin remote at all must produce a message
        # naming the resolution failure, not a stale "origin/main" guess.
        local = tmp_path / "no-remote"
        _init_repo(local)
        _commit(local, "init")

        env = _env_with_gh_shim(tmp_path, None)
        result = _run_script(local, env)

        assert result.returncode == 1
        assert result.stdout == ""
        assert "no default branch resolved" in result.stderr
