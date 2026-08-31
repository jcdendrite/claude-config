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

from .conftest import _make_feature_branch, _make_repo_with_remote

# Path to the script under test (resolved relative to this file)
_SCRIPT = Path(__file__).parent.parent / "pr-diff-against-base.sh"


def _gh_shim_source(base_ref: str | None) -> str:
    """Return source for a gh shim answering `gh pr view --json baseRefName
    --jq .baseRefName`.

    base_ref=None models `gh pr view` failing (no PR open for this branch,
    or gh not authenticated) -- the shim exits 1 with no stdout, exercising
    pr-diff-against-base.sh's `|| echo main` fallback.
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
