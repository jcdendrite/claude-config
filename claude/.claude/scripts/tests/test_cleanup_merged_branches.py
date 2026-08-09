"""Tests for cleanup-merged-branches.sh.

The gh CLI is replaced in every test by a PATH shim that reads canned
JSON keyed on the --head <branch> argument. Real git operations run
against temporary repos created per-test.
"""
from __future__ import annotations

import contextlib
import json
import os
import pty
import re
import shlex
import shutil
import subprocess
import textwrap
import uuid
from pathlib import Path

import pytest
from conftest import (
    _commit,
    _dead_pid,
    _init_repo,
    _make_feature_branch,
    _make_repo_with_remote,
    _make_worktree,
)

# Path to the script under test (resolved relative to this file)
_SCRIPT = Path(__file__).parent.parent / "cleanup-merged-branches.sh"


def _rev_parse(repo: Path, ref: str) -> str:
    """Return the full 40-char SHA for ref in repo."""
    result = subprocess.run(
        ["git", "rev-parse", ref], cwd=repo, capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# fake_gh fixture
# ---------------------------------------------------------------------------

def _gh_shim_source(pr_data: dict[str, object]) -> str:
    """Return source for a gh shim script.

    pr_data maps branch_name -> PR record(s) for that head branch name.
    A value may be:
      None                     → no PR for this name (empty array; open/not found)
      "error"                  → the `gh` call itself fails (non-zero exit),
                                  for exercising the fail-closed path
      "malformed"              → the `gh` call exits zero but writes
                                  non-JSON to stdout, for exercising the
                                  second, independent fail-closed path
                                  (the parser's JSONDecodeError branch
                                  rather than the exit-code branch).
                                  Models real `gh` mixing a banner or a
                                  truncated body into stdout on a 0 exit.
      {"number": N, "mergedAt": "YYYY-MM-DD", ["headRefOid": SHA]}
                                → sugar for a single MERGED row. When
                                  headRefOid is omitted it defaults to the
                                  branch's actual current tip (`git
                                  rev-parse <branch>`, run with the shim's
                                  cwd — the repo under test) — the common
                                  case of a legitimate merge whose branch
                                  was never rewritten afterward.
      [{"number": N, "state": "OPEN"|"MERGED"|"CLOSED",
        ["mergedAt": "YYYY-MM-DD"], ["headRefOid": SHA]}, ...]
                                → multiple PR rows under one head name, for
                                  modeling a reused branch name (e.g. an
                                  open PR alongside an older merged PR).

    Every response includes headRefOid regardless of --state, since the
    script now always queries `--state all` and classifies every row
    itself; the shim does not filter on the requested --state.

    Divergence from real `gh`: the auto-fill above only fires for
    state == "MERGED" — an OPEN or CLOSED row without an explicit
    headRefOid serializes as headRefOid: null. Real `gh pr list` always
    returns the actual head commit SHA regardless of state. Harmless
    today since classify_branch never reads headRefOid off a non-MERGED
    row, but a future guard that inspects an open PR's headRefOid would
    be silently validated against a synthetic null here rather than a
    real SHA — pass headRefOid explicitly on OPEN/CLOSED rows if a test
    ever needs one.

    One further sentinel is keyed on "__auth__" rather than a branch name:
      "unauth"     → `gh auth status` exits non-zero
    """
    payload = json.dumps(pr_data)
    return textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import json, subprocess, sys

        PR_DATA = json.loads({payload!r})

        args = sys.argv[1:]

        # gh auth status
        if args and args[0] == "auth" and len(args) > 1 and args[1] == "status":
            if PR_DATA.get("__auth__") == "unauth":
                sys.exit(1)
            sys.exit(0)

        # gh pr list --head <branch> ...
        if args and args[0] == "pr" and "--head" in args:
            head_idx = args.index("--head")
            branch = args[head_idx + 1]
            info = PR_DATA.get(branch)

            if info == "error":
                sys.exit(1)

            if info == "malformed":
                # Exit 0 with an unparseable body, as real gh can when a
                # banner or a truncated response lands on stdout.
                print('[{{"number":')
                sys.exit(0)

            if info is None:
                print("[]")
                sys.exit(0)

            rows_in = info if isinstance(info, list) else [info]
            rows_out = []
            for row in rows_in:
                state = row.get("state", "MERGED")
                merged_at = row.get("mergedAt")
                head_ref_oid = row.get("headRefOid")
                if state == "MERGED" and head_ref_oid is None:
                    head_ref_oid = subprocess.run(
                        ["git", "rev-parse", branch],
                        capture_output=True, text=True, check=True,
                    ).stdout.strip()
                rows_out.append({{
                    "number": row["number"],
                    "headRefName": branch,
                    "state": state,
                    "mergedAt": (merged_at + "T00:00:00Z") if merged_at else None,
                    "headRefOid": head_ref_oid,
                }})
            print(json.dumps(rows_out))
            sys.exit(0)

        # Fallthrough: unknown subcommand
        sys.exit(0)
    """)


def _gh_shim_source_by_token(token_to_pr_data: dict[str, dict]) -> str:
    """gh shim variant for the per-repo-credential tests (verification
    cases 1, 2, 10 in the plan): selects its PR_DATA table by the current
    GH_TOKEN env var rather than by branch name alone, and never by cwd —
    keying on cwd would let a test pass without load_repo_environment's
    direnv call ever running. A GH_TOKEN with no matching table (including
    unset, which reads as "") gets an empty table: no PR found for any
    branch, matching a repo queried under the wrong (or no) identity."""
    payload = json.dumps(token_to_pr_data)
    return textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import json, os, subprocess, sys

        TOKEN_TABLES = json.loads({payload!r})
        PR_DATA = TOKEN_TABLES.get(os.environ.get("GH_TOKEN", ""), {{}})

        args = sys.argv[1:]

        if args and args[0] == "auth" and len(args) > 1 and args[1] == "status":
            sys.exit(0)

        if args and args[0] == "pr" and "--head" in args:
            head_idx = args.index("--head")
            branch = args[head_idx + 1]
            info = PR_DATA.get(branch)

            if info is None:
                print("[]")
                sys.exit(0)

            rows_in = info if isinstance(info, list) else [info]
            rows_out = []
            for row in rows_in:
                state = row.get("state", "MERGED")
                merged_at = row.get("mergedAt")
                head_ref_oid = row.get("headRefOid")
                if state == "MERGED" and head_ref_oid is None:
                    head_ref_oid = subprocess.run(
                        ["git", "rev-parse", branch],
                        capture_output=True, text=True, check=True,
                    ).stdout.strip()
                rows_out.append({{
                    "number": row["number"],
                    "headRefName": branch,
                    "state": state,
                    "mergedAt": (merged_at + "T00:00:00Z") if merged_at else None,
                    "headRefOid": head_ref_oid,
                }})
            print(json.dumps(rows_out))
            sys.exit(0)

        sys.exit(0)
    """)


def _noop_direnv_shim_source() -> str:
    """Default direnv shim installed for every test: `export bash` exits 0
    with no output, modeling a directory with no identity-bearing .envrc.
    Tests exercising direnv's own export payload pass their own source via
    _shimmed_env's direnv_source parameter."""
    return textwrap.dedent("""\
        #!/usr/bin/env python3
        import sys
        sys.exit(0)
    """)


def _direnv_shim_source_by_cwd(exports_by_cwd: dict[str, dict[str, str]]) -> str:
    """direnv shim modeling per-directory `.envrc` exports: `export bash`
    emits `export NAME=VALUE` for the current directory's configured table
    (looked up by os.getcwd(), matching real direnv's per-directory
    scoping) and nothing for a directory with no entry — matching real
    direnv exiting 0 with no exports outside any `.envrc`."""
    payload = json.dumps(exports_by_cwd)
    return textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import json, os, shlex, sys

        EXPORTS_BY_CWD = json.loads({payload!r})

        args = sys.argv[1:]
        if args[:2] == ["export", "bash"]:
            for name, value in EXPORTS_BY_CWD.get(os.getcwd(), {{}}).items():
                print(f"export {{name}}={{shlex.quote(value)}}")
        sys.exit(0)
    """)


def _direnv_shim_source_static_export(name: str, value: str) -> str:
    """direnv shim that unconditionally exports one NAME=VALUE on `export
    bash`, regardless of cwd — for tests that only need one export to
    reach (or be safely rejected by) the calling shell."""
    quoted_value = shlex.quote(value)
    return textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import sys
        args = sys.argv[1:]
        if args[:2] == ["export", "bash"]:
            print("export {name}={quoted_value}")
        sys.exit(0)
    """)


def _direnv_shim_source_unconditional_unset(name: str) -> str:
    """direnv shim that unconditionally emits `unset NAME` on `export
    bash`, regardless of cwd — models direnv leaving a container's
    identity behind when the current directory has no matching .envrc."""
    return textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import sys
        args = sys.argv[1:]
        if args[:2] == ["export", "bash"]:
            print("unset {name}")
        sys.exit(0)
    """)


def _direnv_shim_source_exits_nonzero_with_unset_payload() -> str:
    """direnv shim modeling a non-`allow`ed .envrc: `export bash` exits 1
    but still writes an unset payload to stdout — the exit-status guard in
    load_repo_environment must discard this cleanly."""
    return textwrap.dedent("""\
        #!/usr/bin/env python3
        import sys
        args = sys.argv[1:]
        if args[:2] == ["export", "bash"]:
            print("unset GH_TOKEN")
            sys.exit(1)
        sys.exit(0)
    """)


def _direnv_shim_source_reads_stdin() -> str:
    """direnv shim modeling an .envrc that reads stdin — if
    load_repo_environment omitted `</dev/null`, this call would hang
    waiting for input that never comes."""
    return textwrap.dedent("""\
        #!/usr/bin/env python3
        import sys
        args = sys.argv[1:]
        if args[:2] == ["export", "bash"]:
            sys.stdin.read()
        sys.exit(0)
    """)


# gh-credential env vars that must never leak from a contributor's real
# shell into a test's PATH-shimmed subprocess (see _base_test_env).
_SENSITIVE_ENV_VARS = frozenset({
    "GH_TOKEN", "GH_HOST", "GITHUB_TOKEN",
    "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN", "GH_CONFIG_DIR",
})


def _base_test_env() -> dict:
    """Inherited env with DIRENV_* and gh-credential vars stripped.

    Left as inherited, `direnv export bash` run from a test's tmp_path
    would emit the *revert* half of a contributor's real DIRENV_* diff,
    restoring a PATH without the test's own shim dir — the script's next
    `gh` call would be the contributor's real gh with their real token,
    against real GitHub.
    """
    return {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("DIRENV_") and key not in _SENSITIVE_ENV_VARS
    }


# Tools the script and _worktree-lib.sh need on a normal (non-lsof,
# non-usage-error) run — mirrors TestGhMissing's min_bin list. Symlinking
# only these into a curated directory keeps the absent-direnv PATH free of
# a real direnv without also losing any other tool that happens to share
# direnv's install directory (e.g. git, via the same package-manager prefix).
_TOOLS_NEEDED_WITHOUT_DIRENV = ("git", "python3", "bash", "grep", "awk", "sed", "dirname")


def _curated_path_without_direnv(tmp_path: Path) -> str:
    curated_dir = tmp_path / f"curated_bin_{uuid.uuid4().hex}"
    curated_dir.mkdir()
    for tool in _TOOLS_NEEDED_WITHOUT_DIRENV:
        tool_path = shutil.which(tool)
        if tool_path:
            (curated_dir / tool).symlink_to(tool_path)
    return str(curated_dir)


def _shimmed_env(
    tmp_path: Path,
    gh_shim_source_text: str,
    *,
    direnv_source: str | None = None,
    direnv_present: bool = True,
) -> dict:
    """Build the credential-scrubbed, PATH-shimmed env every test's `gh`
    invocation must use — the single seam `fake_gh` and every
    hand-rolled shim site route through, so none can skip the
    DIRENV_*/token scrubbing.

    direnv_present=False replaces the inherited PATH with a curated
    directory holding only the tools the script needs, none of them
    `direnv` — deterministic on machines with and without direnv actually
    installed, and immune to direnv sharing an install prefix with a tool
    the script does need (e.g. git).
    """
    shim_dir = tmp_path / f"shim_{uuid.uuid4().hex}"
    shim_dir.mkdir()

    gh_shim = shim_dir / "gh"
    gh_shim.write_text(gh_shim_source_text)
    gh_shim.chmod(0o755)

    if direnv_present:
        direnv_shim = shim_dir / "direnv"
        direnv_shim.write_text(direnv_source or _noop_direnv_shim_source())
        direnv_shim.chmod(0o755)
        base_path = os.environ.get("PATH", "")
    else:
        base_path = _curated_path_without_direnv(tmp_path)

    new_path = os.pathsep.join([str(shim_dir), base_path])
    return {**_base_test_env(), "PATH": new_path}


@pytest.fixture()
def fake_gh(tmp_path):
    """Yield a factory that installs a gh shim (and a default no-op direnv
    shim, via _shimmed_env) and returns the env dict.

    Usage in tests:
        env = fake_gh({"feat/foo": {"number": 1, "mergedAt": "2026-05-01"}})
        result = _run_script(repo, env)
    """
    def _make_env(pr_data: dict, **kwargs) -> dict:
        return _shimmed_env(tmp_path, _gh_shim_source(pr_data), **kwargs)

    return _make_env


class TestShimmedEnvScrubsCredentials:
    """The shared shim-env helper (fake_gh and every hand-rolled site route
    through it) must strip DIRENV_* and gh-credential vars inherited from
    the contributor's real shell. Left in place, a real `direnv export
    bash` run from a test's tmp_path would emit the *revert* half of the
    contributor's own DIRENV_* diff, restoring a PATH without the test's
    shim dir — the script's next `gh` call would be the contributor's real
    gh with their real token, against real GitHub."""

    def test_direnv_and_credential_vars_are_stripped(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DIRENV_DIR", "-/some/container")
        monkeypatch.setenv("GH_TOKEN", "contributors-real-token")
        monkeypatch.setenv("GH_HOST", "github.example.com")
        monkeypatch.setenv("GITHUB_TOKEN", "contributors-real-github-token")
        monkeypatch.setenv("GH_ENTERPRISE_TOKEN", "contributors-real-enterprise-token")
        monkeypatch.setenv("GITHUB_ENTERPRISE_TOKEN", "contributors-real-gh-enterprise-token")
        monkeypatch.setenv("GH_CONFIG_DIR", "/some/contributor/gh-config")

        env = _shimmed_env(tmp_path, _gh_shim_source({}))

        for leaked_var in (
            "DIRENV_DIR", "GH_TOKEN", "GH_HOST", "GITHUB_TOKEN",
            "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN", "GH_CONFIG_DIR",
        ):
            assert leaked_var not in env, f"{leaked_var} must be scrubbed from the shimmed env"


def _run_script(
    repo: Path,
    env: dict,
    args: list[str] | None = None,
    stdin=subprocess.DEVNULL,
) -> subprocess.CompletedProcess:
    """Run the cleanup script in repo with the given environment.

    stdin defaults to DEVNULL (non-TTY) — every existing caller relies on
    non-TTY behavior; the TTY/pty tests bypass this helper entirely via
    direct subprocess.Popen.
    """
    cmd = [str(_SCRIPT)] + (args or [])
    return subprocess.run(
        cmd,
        cwd=str(repo),
        env=env,
        capture_output=True,
        text=True,
        check=False,
        stdin=stdin,
    )


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

class TestNoCandidates:
    """Case 1: no merged PRs for any local branch."""

    def test_exit_zero_non_tty(self, tmp_path, fake_gh):
        """Non-TTY (pipe): no output, exit 0."""
        local, _ = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/open")
        env = fake_gh({})  # no merged branches
        result = _run_script(local, env)
        assert result.returncode == 0
        assert result.stdout == ""

    def test_nothing_to_clean_message_suppressed_for_non_tty(self, tmp_path, fake_gh):
        """stdout is captured (non-TTY): the "nothing to clean" message is suppressed."""
        local, _ = _make_repo_with_remote(tmp_path)
        env = fake_gh({})
        result = _run_script(local, env)
        assert result.returncode == 0
        # Non-TTY: no output expected
        assert result.stdout.strip() == ""


class TestOneMergedBranch:
    """Case 2: one merged branch, no worktree, remote auto-pruned, default current."""

    def test_branch_deleted_summary_correct(self, tmp_path, fake_gh):
        """The one merged branch is cleaned; summary lines are present."""
        local, bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/done")

        # Simulate auto-prune by deleting the remote ref before the script runs
        subprocess.run(["git", "branch", "-D", "feat/done"], cwd=bare, check=True)

        env = fake_gh({"feat/done": {"number": 42, "mergedAt": "2026-05-01"}})
        result = _run_script(local, env)

        assert result.returncode == 0
        assert "feat/done" in result.stdout
        assert "local branch:   deleted" in result.stdout
        # Branch no longer exists locally
        branches = subprocess.run(
            ["git", "branch", "--list", "feat/done"],
            cwd=local, capture_output=True, text=True
        ).stdout
        assert branches.strip() == ""


class TestMultipleMergedBranches:
    """Case 3: multiple merged branches cleaned in a single pass."""

    def test_all_cleaned(self, tmp_path, fake_gh):
        local, bare = _make_repo_with_remote(tmp_path)
        for branch in ("feat/alpha", "feat/beta"):
            _make_feature_branch(local, branch)
            subprocess.run(["git", "branch", "-D", branch], cwd=bare, check=True)

        env = fake_gh({
            "feat/alpha": {"number": 1, "mergedAt": "2026-05-01"},
            "feat/beta": {"number": 2, "mergedAt": "2026-05-02"},
        })
        result = _run_script(local, env)

        assert result.returncode == 0
        assert "feat/alpha" in result.stdout
        assert "feat/beta" in result.stdout
        for branch in ("feat/alpha", "feat/beta"):
            remaining = subprocess.run(
                ["git", "branch", "--list", branch],
                cwd=local, capture_output=True, text=True
            ).stdout
            assert remaining.strip() == ""


class TestMixedMergedAndOpen:
    """Case 4: only merged branches cleaned; open branch untouched."""

    def test_only_merged_cleaned(self, tmp_path, fake_gh):
        local, bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/merged-one")
        _make_feature_branch(local, "feat/still-open")
        subprocess.run(["git", "branch", "-D", "feat/merged-one"], cwd=bare, check=True)

        env = fake_gh({"feat/merged-one": {"number": 10, "mergedAt": "2026-05-01"}})
        result = _run_script(local, env)

        assert result.returncode == 0
        assert "feat/merged-one" in result.stdout

        # Open branch still exists locally
        open_branch = subprocess.run(
            ["git", "branch", "--list", "feat/still-open"],
            cwd=local, capture_output=True, text=True
        ).stdout
        assert "feat/still-open" in open_branch


class TestBranchWithWorktree:
    """Case 5: branch with a worktree — path resolved via --porcelain, then removed."""

    def test_worktree_removed(self, tmp_path, fake_gh):
        local, bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/wt-branch")
        subprocess.run(["git", "branch", "-D", "feat/wt-branch"], cwd=bare, check=True)

        wt_path = tmp_path / "feat-wt-branch-tree"
        _make_worktree(local, "feat/wt-branch", wt_path)
        assert wt_path.exists()

        env = fake_gh({"feat/wt-branch": {"number": 20, "mergedAt": "2026-05-01"}})
        result = _run_script(local, env)

        assert result.returncode == 0
        assert "worktree:       removed:" in result.stdout
        assert not wt_path.exists()


class TestSlashedBranchName:
    """Case 6: branch name containing slashes — worktree lookup via --porcelain."""

    def test_slashed_branch_worktree_resolved(self, tmp_path, fake_gh):
        local, bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/scope/deep-name")
        subprocess.run(["git", "branch", "-D", "feat/scope/deep-name"], cwd=bare, check=True)

        wt_path = tmp_path / "deep-name-tree"
        _make_worktree(local, "feat/scope/deep-name", wt_path)
        assert wt_path.exists()

        env = fake_gh({"feat/scope/deep-name": {"number": 30, "mergedAt": "2026-05-01"}})
        result = _run_script(local, env)

        assert result.returncode == 0
        assert "feat/scope/deep-name" in result.stdout
        assert not wt_path.exists()


class TestRemoteNotAutoPruned:
    """Case 7: remote ref survives the merge — script deletes it."""

    def test_remote_deleted(self, tmp_path, fake_gh):
        local, bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/no-auto-prune")

        # Confirm the remote ref exists before the run
        remote_refs = subprocess.run(
            ["git", "ls-remote", "--heads", "origin", "feat/no-auto-prune"],
            cwd=local, capture_output=True, text=True
        ).stdout
        assert "feat/no-auto-prune" in remote_refs

        env = fake_gh({"feat/no-auto-prune": {"number": 50, "mergedAt": "2026-05-01"}})
        result = _run_script(local, env)

        assert result.returncode == 0
        # The remote ref should be gone
        remote_refs_after = subprocess.run(
            ["git", "ls-remote", "--heads", "origin", "feat/no-auto-prune"],
            cwd=local, capture_output=True, text=True
        ).stdout
        assert remote_refs_after.strip() == ""


class TestDefaultBranchFastForward:
    """Case 8: default branch lags origin — ff-merge runs once after cleanup."""

    def test_default_branch_fast_forwarded(self, tmp_path, fake_gh):
        local, bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/ff-test")
        subprocess.run(["git", "branch", "-D", "feat/ff-test"], cwd=bare, check=True)

        # Advance the remote default branch past the local one
        # (simulate another commit landing on origin/main after local last pulled)
        bare_clone = tmp_path / "clone"
        subprocess.run(
            ["git", "clone", "-q", str(bare), str(bare_clone)], check=True
        )
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=bare_clone, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=bare_clone, check=True)
        _commit(bare_clone, "advance remote")
        subprocess.run(["git", "push", "-q", "origin", "main"], cwd=bare_clone, check=True)

        env = fake_gh({"feat/ff-test": {"number": 60, "mergedAt": "2026-05-01"}})
        result = _run_script(local, env)

        assert result.returncode == 0
        assert "fast-forwarded" in result.stdout or "already current" in result.stdout

    def test_fast_forwards_while_on_feature_branch(self, tmp_path, fake_gh):
        """ff-update must not touch the currently checked-out feature branch."""
        local, bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/ff-from-feature")
        subprocess.run(["git", "branch", "-D", "feat/ff-from-feature"], cwd=bare, check=True)

        # Advance the remote default branch
        bare_clone = tmp_path / "clone"
        subprocess.run(["git", "clone", "-q", str(bare), str(bare_clone)], check=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=bare_clone, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=bare_clone, check=True)
        _commit(bare_clone, "advance remote")
        subprocess.run(["git", "push", "-q", "origin", "main"], cwd=bare_clone, check=True)

        # Stay on an unrelated feature branch (simulates the common real-world invocation)
        subprocess.run(["git", "checkout", "-b", "feat/still-open"], cwd=local, check=True)
        _commit(local, "open branch commit")
        open_sha_before = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=local, capture_output=True, text=True
        ).stdout.strip()

        env = fake_gh({"feat/ff-from-feature": {"number": 61, "mergedAt": "2026-05-01"}})
        result = _run_script(local, env)

        assert result.returncode == 0
        # Default branch was advanced
        assert "fast-forwarded" in result.stdout or "already current" in result.stdout
        # The currently checked-out branch was NOT moved
        open_sha_after = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=local, capture_output=True, text=True
        ).stdout.strip()
        assert open_sha_before == open_sha_after, (
            "fast-forward must not mutate the currently checked-out branch"
        )


class TestCurrentlyOnCandidateBranch:
    """Case 9: currently checked-out branch is a candidate — skipped; others proceed."""

    def test_checked_out_skipped_others_cleaned(self, tmp_path, fake_gh):
        local, bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/checked-out-merged")
        _make_feature_branch(local, "feat/other-merged")
        subprocess.run(["git", "branch", "-D", "feat/other-merged"], cwd=bare, check=True)
        subprocess.run(["git", "branch", "-D", "feat/checked-out-merged"], cwd=bare, check=True)

        # Check out the candidate branch
        subprocess.run(["git", "checkout", "-q", "feat/checked-out-merged"], cwd=local, check=True)

        env = fake_gh({
            "feat/checked-out-merged": {"number": 70, "mergedAt": "2026-05-01"},
            "feat/other-merged": {"number": 71, "mergedAt": "2026-05-02"},
        })
        result = _run_script(local, env)

        assert result.returncode == 0
        # The checked-out branch is reported as skipped
        assert "Skipped" in result.stdout
        assert "currently checked out" in result.stdout
        # The other merged branch was cleaned
        other_remaining = subprocess.run(
            ["git", "branch", "--list", "feat/other-merged"],
            cwd=local, capture_output=True, text=True
        ).stdout
        assert other_remaining.strip() == ""
        # The checked-out branch still exists
        checked_remaining = subprocess.run(
            ["git", "branch", "--list", "feat/checked-out-merged"],
            cwd=local, capture_output=True, text=True
        ).stdout
        assert "feat/checked-out-merged" in checked_remaining


class TestMasterDefaultRepo:
    """Case 10: repo whose default branch is 'master', not 'main'."""

    def test_fast_forwards_master_not_main(self, tmp_path, fake_gh):
        bare = tmp_path / "remote.git"
        bare.mkdir()
        subprocess.run(["git", "init", "--bare", "-q", "--initial-branch=master"], cwd=bare, check=True)

        local = tmp_path / "local"
        _init_repo(local)
        # Create initial commit on master
        subprocess.run(["git", "checkout", "-b", "master"], cwd=local, check=True)
        _commit(local, "init")
        subprocess.run(["git", "remote", "add", "origin", str(bare)], cwd=local, check=True)
        subprocess.run(["git", "push", "-q", "-u", "origin", "master"], cwd=local, check=True)
        subprocess.run(["git", "remote", "set-head", "origin", "master"], cwd=local, check=True)

        _make_feature_branch(local, "feat/master-test", return_to="master")
        subprocess.run(["git", "branch", "-D", "feat/master-test"], cwd=bare, check=True)

        env = fake_gh({"feat/master-test": {"number": 80, "mergedAt": "2026-05-01"}})
        result = _run_script(local, env)

        assert result.returncode == 0
        # Should mention master (default), not main
        assert "feat/master-test" in result.stdout


class TestOriginHeadUnset:
    """Case 11: origin/HEAD unset — script runs set-head and resolves."""

    def test_resolves_default_when_origin_head_unset(self, tmp_path, fake_gh):
        local, bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/no-head")
        subprocess.run(["git", "branch", "-D", "feat/no-head"], cwd=bare, check=True)

        # Remove origin/HEAD so symbolic-ref returns empty
        subprocess.run(
            ["git", "remote", "set-head", "origin", "--delete"],
            cwd=local, check=False  # OK if already unset
        )

        env = fake_gh({"feat/no-head": {"number": 90, "mergedAt": "2026-05-01"}})
        result = _run_script(local, env)

        # Script should succeed (set-head auto re-populates or falls back to main)
        assert result.returncode == 0
        assert "feat/no-head" in result.stdout


class TestIdempotentRerun:
    """Case 12: second run reports zero candidates."""

    def test_idempotent(self, tmp_path, fake_gh):
        local, bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/idempotent")
        subprocess.run(["git", "branch", "-D", "feat/idempotent"], cwd=bare, check=True)

        env = fake_gh({"feat/idempotent": {"number": 100, "mergedAt": "2026-05-01"}})

        # First run — cleans up
        first = _run_script(local, env)
        assert first.returncode == 0
        assert "feat/idempotent" in first.stdout

        # Second run — nothing left (local branch gone, so gh never queried for it)
        second = _run_script(local, env)
        assert second.returncode == 0
        assert second.stdout.strip() == ""  # non-TTY: silent on no-op


class TestDryRun:
    """Case 13: --dry-run prints candidates without acting."""

    def test_dry_run_no_destructive_action(self, tmp_path, fake_gh):
        local, bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/dry")
        _make_feature_branch(local, "feat/dry2")

        env = fake_gh({
            "feat/dry": {"number": 110, "mergedAt": "2026-05-01"},
            "feat/dry2": {"number": 111, "mergedAt": "2026-05-02"},
        })
        result = _run_script(local, env, args=["--dry-run"])

        assert result.returncode == 0
        assert "Would clean up" in result.stdout
        assert "feat/dry" in result.stdout
        assert "feat/dry2" in result.stdout
        assert "PR #110" in result.stdout

        # Branches still exist locally
        for branch in ("feat/dry", "feat/dry2"):
            remaining = subprocess.run(
                ["git", "branch", "--list", branch],
                cwd=local, capture_output=True, text=True
            ).stdout
            assert branch in remaining


class TestInvalidArgs:
    """Case 14: invalid arguments exit 2 with usage on stderr; no git ops."""

    @pytest.mark.parametrize("bad_args", [
        ["foo"],
        ["--bar"],
        ["--dry-run", "x"],
        ["--yes"],
    ])
    def test_invalid_args_exit_2(self, tmp_path, fake_gh, bad_args):
        """--yes is asserted here as a regression guard: it is a removed flag,
        not merely an unused one — this fails if it is ever reintroduced
        without an accompanying test update."""
        local, _ = _make_repo_with_remote(tmp_path)
        env = fake_gh({})
        result = _run_script(local, env, args=bad_args)
        assert result.returncode == 2
        assert "Usage" in result.stderr

    @pytest.mark.parametrize("valid_dup_args", [
        ["--dry-run", "--dry-run"],
    ])
    def test_duplicate_flags_are_idempotent(self, tmp_path, fake_gh, valid_dup_args):
        local, _ = _make_repo_with_remote(tmp_path)
        env = fake_gh({})
        result = _run_script(local, env, args=valid_dup_args)
        assert result.returncode == 0


class TestGhMissing:
    """Case 15: gh not in PATH — exits non-zero with install instructions."""

    def test_gh_missing_exits_nonzero(self, tmp_path):
        local, _ = _make_repo_with_remote(tmp_path)
        # _curated_path_without_direnv's tool list omits `gh` (and `direnv`),
        # so it doubles as a minimal no-gh PATH here — reusing it, rather than
        # hand-rolling a second symlink loop, keeps this site routed through
        # the same credential-scrubbed base env as every other test.
        env = {**_base_test_env(), "PATH": _curated_path_without_direnv(tmp_path)}
        result = _run_script(local, env)
        assert result.returncode != 0
        assert "gh" in result.stderr.lower() or "install" in result.stderr.lower()


class TestGhUnauthenticated:
    """Case 16: gh is present but auth status returns non-zero."""

    def test_gh_unauth_exits_nonzero(self, tmp_path, fake_gh):
        local, _ = _make_repo_with_remote(tmp_path)
        # Special sentinel: make auth status fail
        env = fake_gh({"__auth__": "unauth"})
        result = _run_script(local, env)
        assert result.returncode != 0
        assert "auth" in result.stderr.lower() or "login" in result.stderr.lower()


class TestLockedWorktreeUnlockedAndRemoved:
    """Case 17: branch with a locked worktree is unlocked and fully removed (worktree, local branch, remote)."""

    def test_locked_worktree_is_unlocked_and_removed(self, tmp_path, fake_gh):
        local, bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/locked-wt")
        subprocess.run(["git", "branch", "-D", "feat/locked-wt"], cwd=bare, check=True)

        wt_path = tmp_path / "locked-tree"
        _make_worktree(local, "feat/locked-wt", wt_path)
        dead = _dead_pid()
        subprocess.run(
            ["git", "worktree", "lock", str(wt_path), "--reason", f"test (pid {dead})"],
            cwd=local, check=True, capture_output=True,
        )

        env = fake_gh({"feat/locked-wt": {"number": 200, "mergedAt": "2026-05-01"}})
        result = _run_script(local, env)

        assert result.returncode == 0
        assert "unlocked stale lock" in result.stdout
        assert "removed:" in result.stdout
        assert not wt_path.exists(), "locked worktree on a merged branch should be removed"
        ref_check = subprocess.run(
            ["git", "rev-parse", "--verify", "refs/heads/feat/locked-wt"],
            cwd=local, capture_output=True,
        )
        assert ref_check.returncode != 0, "local branch should be deleted after worktree removed"

    def test_locked_annotated_in_dry_run(self, tmp_path, fake_gh):
        local, bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/locked-dry")
        subprocess.run(["git", "branch", "-D", "feat/locked-dry"], cwd=bare, check=True)

        wt_path = tmp_path / "locked-dry-tree"
        _make_worktree(local, "feat/locked-dry", wt_path)
        dead = _dead_pid()
        subprocess.run(
            ["git", "worktree", "lock", str(wt_path), "--reason", f"test (pid {dead})"],
            cwd=local, check=True, capture_output=True,
        )

        env = fake_gh({"feat/locked-dry": {"number": 210, "mergedAt": "2026-05-01"}})
        result = _run_script(local, env, args=["--dry-run"])

        assert result.returncode == 0
        assert "Would clean up" in result.stdout
        assert "feat/locked-dry" in result.stdout
        assert "locked" in result.stdout and "will unlock and remove" in result.stdout
        # Dry-run must not touch the worktree or branch
        assert wt_path.exists()
        ref_check = subprocess.run(
            ["git", "rev-parse", "--verify", "refs/heads/feat/locked-dry"],
            cwd=local, capture_output=True,
        )
        assert ref_check.returncode == 0


class TestLockedWorktreeMixedWithUnlocked:
    """Case 18: locked and unlocked merged branches in same run — both fully cleaned."""

    def test_mixed_locked_and_unlocked(self, tmp_path, fake_gh):
        local, bare = _make_repo_with_remote(tmp_path)

        # Branch 1: locked worktree
        _make_feature_branch(local, "feat/locked-mix")
        subprocess.run(["git", "branch", "-D", "feat/locked-mix"], cwd=bare, check=True)
        locked_wt = tmp_path / "locked-mix-tree"
        _make_worktree(local, "feat/locked-mix", locked_wt)
        dead = _dead_pid()
        subprocess.run(
            ["git", "worktree", "lock", str(locked_wt), "--reason", f"test (pid {dead})"],
            cwd=local, check=True, capture_output=True,
        )

        # Branch 2: unlocked worktree
        _make_feature_branch(local, "feat/unlocked-mix")
        subprocess.run(["git", "branch", "-D", "feat/unlocked-mix"], cwd=bare, check=True)
        unlocked_wt = tmp_path / "unlocked-mix-tree"
        _make_worktree(local, "feat/unlocked-mix", unlocked_wt)

        env = fake_gh({
            "feat/locked-mix": {"number": 201, "mergedAt": "2026-05-01"},
            "feat/unlocked-mix": {"number": 202, "mergedAt": "2026-05-01"},
        })
        result = _run_script(local, env)

        assert result.returncode == 0

        # Locked branch: unlocked and fully cleaned
        assert "unlocked stale lock" in result.stdout
        assert not locked_wt.exists(), "locked worktree on merged branch should be removed"
        ref_locked = subprocess.run(
            ["git", "rev-parse", "--verify", "refs/heads/feat/locked-mix"],
            cwd=local, capture_output=True,
        )
        assert ref_locked.returncode != 0, "locked local branch should be deleted after removal"

        # Unlocked branch: fully cleaned
        assert not unlocked_wt.exists(), "unlocked worktree should be removed"
        ref_unlocked = subprocess.run(
            ["git", "rev-parse", "--verify", "refs/heads/feat/unlocked-mix"],
            cwd=local, capture_output=True,
        )
        assert ref_unlocked.returncode != 0, "unlocked local branch should be deleted"


class TestLockedWorktreeRemoveFailsCleanly:
    """Case 19: locked worktree with untracked content — unlock attempted, remove refused, worktree relocked."""

    def test_locked_dirty_worktree_is_refused_and_relocked(self, tmp_path, fake_gh):
        local, bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/locked-dirty")
        subprocess.run(["git", "branch", "-D", "feat/locked-dirty"], cwd=bare, check=True)

        wt_path = tmp_path / "locked-dirty-tree"
        _make_worktree(local, "feat/locked-dirty", wt_path)
        # Untracked file causes git worktree remove to refuse without --force
        (wt_path / "leftover.txt").write_text("in-progress work")
        dead = _dead_pid()
        subprocess.run(
            ["git", "worktree", "lock", str(wt_path), "--reason", f"test (pid {dead})"],
            cwd=local, check=True, capture_output=True,
        )

        env = fake_gh({"feat/locked-dirty": {"number": 220, "mergedAt": "2026-05-01"}})
        result = _run_script(local, env)

        assert result.returncode == 0
        assert "unlocked stale lock" in result.stdout
        assert "remove failed (manual step needed)" in result.stdout
        assert "contains modified or untracked files" in result.stdout
        # Worktree dir and its content must survive
        assert wt_path.exists()
        assert (wt_path / "leftover.txt").exists()
        # Local branch must survive (script continue'd before branch -D)
        ref_check = subprocess.run(
            ["git", "rev-parse", "--verify", "refs/heads/feat/locked-dirty"],
            cwd=local, capture_output=True,
        )
        assert ref_check.returncode == 0, "branch ref must survive when worktree remove fails"
        # Worktree must be relocked (best-effort restore of prior state)
        porcelain = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=local, capture_output=True, text=True, check=True,
        )
        records = porcelain.stdout.strip().split("\n\n")
        wt_str = str(wt_path)
        wt_is_locked = any(
            f"worktree {wt_str}" in record and "locked" in record
            for record in records
        )
        assert wt_is_locked, "worktree should be relocked after failed remove"


# ---------------------------------------------------------------------------
# Tier B helpers and tests (reachable from origin/main, no merged PR)
# ---------------------------------------------------------------------------

def _make_tier_b_branch(repo: Path, remote: Path, branch_name: str) -> None:
    """Feature branch whose commits are reachable from origin/main, but gh returns no PR."""
    subprocess.run(["git", "checkout", "-q", "-b", branch_name], cwd=repo, check=True)
    _commit(repo, f"work on {branch_name}")
    subprocess.run(["git", "checkout", "-q", "main"], cwd=repo, check=True)
    subprocess.run(["git", "merge", "-q", "--ff-only", branch_name], cwd=repo, check=True)
    subprocess.run(["git", "push", "-q", "origin", "main"], cwd=repo, check=True)


class TestTierBReachableNoMergedPR:
    """Branches reachable from origin/main but with no merged PR for this name."""

    @pytest.mark.parametrize("reply", [b"y\n", b"Y\n"], ids=["lowercase-y", "uppercase-Y"])
    def test_reachable_no_pr_tty_y_deletes(self, fake_gh, tmp_path, reply):
        """Tier B branch: TTY stdin prompts [y/N]; replying y or Y deletes it."""
        local, remote = _make_repo_with_remote(tmp_path)
        _make_tier_b_branch(local, remote, "tier-b-branch")
        env = fake_gh({})

        master_fd, slave_fd = pty.openpty()
        try:
            proc = subprocess.Popen(
                [str(_SCRIPT)], cwd=local,
                env=env,
                stdin=slave_fd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            os.close(slave_fd)
            os.write(master_fd, reply)
            proc.wait(timeout=30)
            os.close(master_fd)
        except Exception:
            with contextlib.suppress(OSError):
                os.close(master_fd)
            with contextlib.suppress(OSError):
                os.close(slave_fd)
            raise

        assert proc.returncode == 0
        stdout = proc.stdout.read().decode()
        assert "[y/N]" in stdout
        branches = subprocess.run(
            ["git", "branch"], cwd=local, capture_output=True, text=True
        ).stdout
        assert "tier-b-branch" not in branches

    def test_reachable_no_pr_tty_n_survives(self, fake_gh, tmp_path):
        """Tier B branch: TTY stdin prompts [y/N]; replying n keeps it."""
        local, remote = _make_repo_with_remote(tmp_path)
        _make_tier_b_branch(local, remote, "tier-b-branch")
        env = fake_gh({})

        master_fd, slave_fd = pty.openpty()
        try:
            proc = subprocess.Popen(
                [str(_SCRIPT)], cwd=local,
                env=env,
                stdin=slave_fd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            os.close(slave_fd)
            os.write(master_fd, b"n\n")
            proc.wait(timeout=30)
            os.close(master_fd)
        except Exception:
            with contextlib.suppress(OSError):
                os.close(master_fd)
            with contextlib.suppress(OSError):
                os.close(slave_fd)
            raise

        assert proc.returncode == 0
        stdout = proc.stdout.read().decode()
        assert "[y/N]" in stdout
        branches = subprocess.run(
            ["git", "branch"], cwd=local, capture_output=True, text=True
        ).stdout
        assert "tier-b-branch" in branches

    def test_two_tier_b_branches_prompt_independently(self, fake_gh, tmp_path):
        """Two Tier B branches in one TTY run are prompted one at a time,
        not decided by a single batch answer: a y for the first and an n
        for the second must produce two separate [y/N] prompts and two
        independent outcomes."""
        local, remote = _make_repo_with_remote(tmp_path)
        # Branch names are chosen so git for-each-ref's default alphabetical
        # sort prompts "tier-b-first" before "tier-b-second"; that ordering is
        # what maps the "y\n" reply to the first branch and "n\n" to the second.
        # Renaming to non-alphabetically-ordered names would silently invert
        # which branch gets which reply (same dependency the sibling
        # TestTierBPromptEOFDoesNotAbortPendingTierADeletes class documents).
        _make_tier_b_branch(local, remote, "tier-b-first")
        _make_tier_b_branch(local, remote, "tier-b-second")
        env = fake_gh({})

        master_fd, slave_fd = pty.openpty()
        try:
            proc = subprocess.Popen(
                [str(_SCRIPT)], cwd=local,
                env=env,
                stdin=slave_fd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            os.close(slave_fd)
            os.write(master_fd, b"y\n")
            os.write(master_fd, b"n\n")
            proc.wait(timeout=30)
            os.close(master_fd)
        except Exception:
            with contextlib.suppress(OSError):
                os.close(master_fd)
            with contextlib.suppress(OSError):
                os.close(slave_fd)
            raise

        assert proc.returncode == 0
        stdout = proc.stdout.read().decode()
        assert stdout.count("[y/N]") == 2
        branches = subprocess.run(
            ["git", "branch"], cwd=local, capture_output=True, text=True
        ).stdout
        assert "tier-b-first" not in branches
        assert "tier-b-second" in branches

    def test_reachable_no_pr_non_tty_skips_with_warning(self, fake_gh, tmp_path):
        """Tier B branch: non-TTY stdin skips with a warning, never deletes."""
        local, remote = _make_repo_with_remote(tmp_path)
        _make_tier_b_branch(local, remote, "tier-b-branch")
        env = fake_gh({})
        result = subprocess.run(
            [str(_SCRIPT)], cwd=local,
            env=env,
            stdin=subprocess.DEVNULL, capture_output=True,
        )
        assert result.returncode == 0
        stdout = result.stdout.decode()
        assert "no TTY for prompt" in stdout
        assert "--yes" not in stdout
        branches = subprocess.run(
            ["git", "branch"], cwd=local, capture_output=True, text=True
        ).stdout
        assert "tier-b-branch" in branches

    def test_tier_a_deletes_tier_b_skipped_non_tty(self, fake_gh, tmp_path):
        """A Tier A (gh-confirmed) branch is deleted and a Tier B
        (reachable, no PR) branch is skipped with a warning in the same
        non-TTY run, with no cross-contamination between the parallel
        MERGED_BRANCHES/MERGED_PR_INFO_VALUES/TIER_VALUES arrays."""
        local, remote = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "tier-a-branch")
        _make_tier_b_branch(local, remote, "tier-b-branch")
        env = fake_gh({"tier-a-branch": {"number": 42, "mergedAt": "2026-01-01"}})
        result = subprocess.run(
            [str(_SCRIPT)], cwd=local,
            env=env,
            stdin=subprocess.DEVNULL, capture_output=True,
        )
        assert result.returncode == 0
        stdout = result.stdout.decode()
        assert "tier-a-branch" in stdout
        assert "tier-b-branch" in stdout
        # The removed --yes flag must not resurface in the end-of-run skip
        # report (plan B11: strip "rerun with --yes" at all three report sites).
        assert "--yes" not in stdout
        branches = subprocess.run(
            ["git", "branch"], cwd=local, capture_output=True, text=True
        ).stdout
        assert "tier-a-branch" not in branches
        assert "tier-b-branch" in branches

    def test_open_pr_survives_alongside_tier_b_skip_non_tty(self, fake_gh, tmp_path):
        """The reachability-path analogue of the 2026-07-19 incident: an
        open-PR branch and a separate Tier-B-shaped (reachable, no PR)
        branch in the same non-TTY run. The open-PR branch survives via
        the open-PR guard, and the Tier-B branch survives via the no-TTY
        skip — the two guards act independently per branch."""
        local, remote = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/in-review")
        _make_tier_b_branch(local, remote, "tier-b-branch")
        env = fake_gh({"feat/in-review": [{"number": 21, "state": "OPEN"}]})
        result = subprocess.run(
            [str(_SCRIPT)], cwd=local,
            env=env,
            stdin=subprocess.DEVNULL, capture_output=True,
        )
        assert result.returncode == 0
        stdout = result.stdout.decode()
        assert "open PR #21" in stdout
        branches = subprocess.run(
            ["git", "branch"], cwd=local, capture_output=True, text=True
        ).stdout
        assert "feat/in-review" in branches, "branch with an open PR must survive"
        assert "tier-b-branch" in branches, "Tier B branch must survive the non-TTY skip"

    def test_unmerged_branch_not_touched(self, fake_gh, tmp_path):
        """Tier C branch (not reachable from origin/main, no merged PR) is never deleted."""
        local, remote = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "unmerged-branch")
        env = fake_gh({})
        result = subprocess.run(
            [str(_SCRIPT)], cwd=local,
            env=env,
            stdin=subprocess.DEVNULL, capture_output=True,
        )
        assert result.returncode == 0
        branches = subprocess.run(
            ["git", "branch"], cwd=local, capture_output=True, text=True
        ).stdout
        assert "unmerged-branch" in branches

    def test_dry_run_separates_confirmed_and_probable(self, tmp_path):
        """Dry-run shows Tier A under 'confirmed merged' and Tier B under 'probable merges'."""
        local, remote = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "tier-a-branch")
        _make_tier_b_branch(local, remote, "tier-b-branch")
        _make_feature_branch(local, "tier-c-branch")

        env = _shimmed_env(tmp_path, _gh_shim_source({"tier-a-branch": {"number": 1, "mergedAt": "2026-05-01"}}))

        result = subprocess.run(
            [str(_SCRIPT), "--dry-run"], cwd=local,
            env=env, stdin=subprocess.DEVNULL, capture_output=True,
        )
        assert result.returncode == 0
        output = result.stdout.decode()
        assert "Would clean up (confirmed merged):" in output
        assert "Probable merges (would prompt):" in output
        assert "tier-a-branch" in output
        assert "tier-b-branch" in output
        assert "reachable from origin/main; no merged PR for this name" in output
        assert "tier-c-branch" not in output
        # The removed --yes flag must not resurface in the dry-run headings
        # (plan B11: strip "rerun with --yes" at all three report sites).
        assert "--yes" not in output

    def test_existing_tier_a_silent_clean_preserved(self, tmp_path):
        """Tier A branch (gh-confirmed merged) cleans silently, no prompt needed."""
        local, remote = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "tier-a-branch")

        env = _shimmed_env(tmp_path, _gh_shim_source({"tier-a-branch": {"number": 1, "mergedAt": "2026-05-01"}}))

        result = subprocess.run(
            [str(_SCRIPT)], cwd=local,
            env=env,
            stdin=subprocess.DEVNULL, capture_output=True,
        )
        assert result.returncode == 0
        branches = subprocess.run(
            ["git", "branch"], cwd=local, capture_output=True, text=True
        ).stdout
        assert "tier-a-branch" not in branches
        assert b"prompt" not in result.stdout.lower()


class TestTierBPromptEOFDoesNotAbortPendingTierADeletes:
    """A closed pty master before any reply reaches read() (EOF, e.g.
    Ctrl-D) must resolve each pending Tier B prompt as "keep", not abort
    the script under set -e — which, since Tier A and Tier B share one
    confirmation loop, would otherwise drop a not-yet-appended Tier A
    delete. Branch names are chosen so both Tier B branches sort before
    the Tier A branch in git for-each-ref's alphabetical order: if Tier A
    sorted first it would already be in TO_DELETE before the loop reaches
    the EOF-triggering Tier B branch, and this test would pass even
    without the `read -r _REPLY || _REPLY=""` guard."""

    def test_eof_on_prompt_keeps_tier_b_and_still_deletes_later_tier_a(self, fake_gh, tmp_path):
        local, remote = _make_repo_with_remote(tmp_path)
        _make_tier_b_branch(local, remote, "aaa-tier-b-one")
        _make_tier_b_branch(local, remote, "aaa-tier-b-two")
        _make_feature_branch(local, "zzz-tier-a")
        env = fake_gh({"zzz-tier-a": {"number": 55, "mergedAt": "2026-02-01"}})

        master_fd, slave_fd = pty.openpty()
        try:
            proc = subprocess.Popen(
                [str(_SCRIPT)], cwd=local,
                env=env,
                stdin=slave_fd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            os.close(slave_fd)
            os.close(master_fd)  # EOF before any reply is sent
            proc.wait(timeout=30)
        except Exception:
            with contextlib.suppress(OSError):
                os.close(master_fd)
            with contextlib.suppress(OSError):
                os.close(slave_fd)
            raise

        assert proc.returncode == 0, "EOF on the prompt must not abort the script under set -e"
        branches = subprocess.run(
            ["git", "branch"], cwd=local, capture_output=True, text=True
        ).stdout
        assert "aaa-tier-b-one" in branches, "EOF resolves to keep, not delete"
        assert "aaa-tier-b-two" in branches, "EOF resolves to keep, not delete"
        assert "zzz-tier-a" not in branches, "Tier A delete must not be dropped by the EOF abort"


class TestLockedWorktreeLiveness:
    """Live vs dead pid liveness check for locked worktrees."""

    def test_locked_worktree_live_pid_skipped(self, tmp_path):
        """Locked worktree with live pid (current process) is not removed."""
        local, remote = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "locked-branch")
        wt_path = tmp_path / "locked-wt"
        _make_worktree(local, "locked-branch", wt_path)
        subprocess.run(
            ["git", "worktree", "lock", "--reason", f"test (pid {os.getpid()})", str(wt_path)],
            cwd=local, check=True,
        )

        env = _shimmed_env(tmp_path, _gh_shim_source({"locked-branch": {"number": 300, "mergedAt": "2026-05-01"}}))

        result = subprocess.run(
            [str(_SCRIPT)], cwd=local,
            env=env,
            stdin=subprocess.DEVNULL, capture_output=True,
        )
        assert wt_path.exists()
        output = result.stdout.decode()
        assert "live" in output.lower() or "locked" in output.lower()

    def test_locked_worktree_dead_pid_removed(self, tmp_path):
        """Locked worktree with dead pid is unlocked and removed."""
        local, remote = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "stale-locked-branch")
        wt_path = tmp_path / "stale-wt"
        _make_worktree(local, "stale-locked-branch", wt_path)
        dead = _dead_pid()
        subprocess.run(
            ["git", "worktree", "lock", "--reason", f"test (pid {dead})", str(wt_path)],
            cwd=local, check=True,
        )

        env = _shimmed_env(tmp_path, _gh_shim_source({"stale-locked-branch": {"number": 301, "mergedAt": "2026-05-01"}}))

        result = subprocess.run(
            [str(_SCRIPT)], cwd=local,
            env=env,
            stdin=subprocess.DEVNULL, capture_output=True,
        )
        output = result.stdout.decode()
        assert "stale" in output.lower() or "dead" in output.lower()


class TestWorktreeInUseGuard:
    """A worktree that a live process is working inside is not removed."""

    def test_worktree_with_live_process_is_skipped(self, tmp_path, fake_gh):
        """A worktree holding a live process's cwd is skipped; branch survives."""
        local, bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/in-use")
        subprocess.run(["git", "branch", "-D", "feat/in-use"], cwd=bare, check=True)

        wt_path = tmp_path / "in-use-tree"
        _make_worktree(local, "feat/in-use", wt_path)

        env = fake_gh({"feat/in-use": {"number": 400, "mergedAt": "2026-05-01"}})
        # A live process whose working directory is inside the worktree.
        holder = subprocess.Popen(["sleep", "120"], cwd=str(wt_path))
        try:
            result = _run_script(local, env)
        finally:
            holder.terminate()
            holder.wait()

        assert result.returncode == 0
        assert "in use by a live process" in result.stdout
        assert wt_path.exists(), "worktree in use must not be removed"
        ref_check = subprocess.run(
            ["git", "rev-parse", "--verify", "refs/heads/feat/in-use"],
            cwd=local, capture_output=True,
        )
        assert ref_check.returncode == 0, "branch must survive when its worktree is in use"

    def test_worktree_in_use_via_subdirectory_is_skipped(self, tmp_path, fake_gh):
        """A process cwd'd into a subdirectory of the worktree also skips it."""
        local, bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/in-use-sub")
        subprocess.run(["git", "branch", "-D", "feat/in-use-sub"], cwd=bare, check=True)

        wt_path = tmp_path / "in-use-sub-tree"
        _make_worktree(local, "feat/in-use-sub", wt_path)
        subdir = wt_path / "nested"
        subdir.mkdir()

        env = fake_gh({"feat/in-use-sub": {"number": 401, "mergedAt": "2026-05-01"}})
        holder = subprocess.Popen(["sleep", "120"], cwd=str(subdir))
        try:
            result = _run_script(local, env)
        finally:
            holder.terminate()
            holder.wait()

        assert result.returncode == 0
        assert "in use by a live process" in result.stdout
        assert wt_path.exists(), "worktree with an in-use subdirectory must not be removed"

    def test_idle_worktree_still_removed(self, tmp_path, fake_gh):
        """A worktree with no live process inside it is removed as before."""
        local, bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/idle-wt")
        subprocess.run(["git", "branch", "-D", "feat/idle-wt"], cwd=bare, check=True)

        wt_path = tmp_path / "idle-tree"
        _make_worktree(local, "feat/idle-wt", wt_path)

        env = fake_gh({"feat/idle-wt": {"number": 403, "mergedAt": "2026-05-01"}})
        result = _run_script(local, env)

        assert result.returncode == 0
        assert "worktree:       removed:" in result.stdout
        assert "in use by a live process" not in result.stdout
        assert not wt_path.exists(), "idle worktree must still be removed"

    def test_worktree_in_use_annotated_in_dry_run(self, tmp_path, fake_gh):
        """--dry-run flags an in-use worktree and changes nothing."""
        local, bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/in-use-dry")

        wt_path = tmp_path / "in-use-dry-tree"
        _make_worktree(local, "feat/in-use-dry", wt_path)

        env = fake_gh({"feat/in-use-dry": {"number": 402, "mergedAt": "2026-05-01"}})
        holder = subprocess.Popen(["sleep", "120"], cwd=str(wt_path))
        try:
            result = _run_script(local, env, args=["--dry-run"])
        finally:
            holder.terminate()
            holder.wait()

        assert result.returncode == 0
        assert "feat/in-use-dry" in result.stdout
        assert "worktree in use" in result.stdout and "would skip" in result.stdout
        assert wt_path.exists()
        ref_check = subprocess.run(
            ["git", "rev-parse", "--verify", "refs/heads/feat/in-use-dry"],
            cwd=local, capture_output=True,
        )
        assert ref_check.returncode == 0


# ---------------------------------------------------------------------------
# Open-PR guard and Tier-A tip verification (classify_branch)
#
# Regression coverage for the 2026-07-19 incident: Tier A matched a merged
# PR by branch *name* only, with no check that the current tip belonged to
# that merge. A branch name reused across PRs (old PR merged, new PR opened
# on the same head name) was deleted, which GitHub recorded as closing the
# new, still-open PR. See ~/.claude/plans/cleanup-branches-open-pr-guard.md.
# ---------------------------------------------------------------------------

class TestOpenPRGuardWinsOverStaleMergedMatch:
    """An open PR on this head branch name must survive even when an older
    PR merged under the same name — the exact incident shape (open PR #14
    alongside merged PR #7, same head branch)."""

    def test_open_pr_survives_despite_same_named_merged_pr(self, tmp_path, fake_gh):
        local, bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "reused-name-with-open-pr")

        env = fake_gh({
            "reused-name-with-open-pr": [
                {"number": 14, "state": "OPEN"},
                {"number": 7, "state": "MERGED", "mergedAt": "2026-06-16", "headRefOid": "a" * 40},
            ],
        })
        result = _run_script(local, env)

        assert result.returncode == 0
        ref_check = subprocess.run(
            ["git", "rev-parse", "--verify", "refs/heads/reused-name-with-open-pr"],
            cwd=local, capture_output=True,
        )
        assert ref_check.returncode == 0, "branch with an open PR must survive"
        remote_refs = subprocess.run(
            ["git", "ls-remote", "--heads", "origin", "reused-name-with-open-pr"],
            cwd=local, capture_output=True, text=True,
        ).stdout
        assert "reused-name-with-open-pr" in remote_refs, "remote branch of an open PR must survive"
        assert "open PR #14" in result.stdout

    def test_open_pr_guard_skip_reason_rendered_in_dry_run(self, tmp_path, fake_gh):
        """The skip-reason line renders on the dry-run code path too:
        print_skip_reason_lines runs from a separate branch in the script
        (the dry-run preview) from the real-run summary path, and dry-run
        must never delete regardless."""
        local, bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "reused-name-with-open-pr")

        env = fake_gh({
            "reused-name-with-open-pr": [
                {"number": 14, "state": "OPEN"},
                {"number": 7, "state": "MERGED", "mergedAt": "2026-06-16", "headRefOid": "a" * 40},
            ],
        })
        result = _run_script(local, env, args=["--dry-run"])

        assert result.returncode == 0
        assert "open PR #14" in result.stdout
        ref_check = subprocess.run(
            ["git", "rev-parse", "--verify", "refs/heads/reused-name-with-open-pr"],
            cwd=local, capture_output=True,
        )
        assert ref_check.returncode == 0, "dry-run must never delete"


class TestOpenPROnlyNoSameNamedMergedPR:
    """The common, non-incident shape: a branch with only an open PR and no
    same-named merged PR at all must survive. Behaviorally covered by the
    classifier returning the open verdict before merged rows are even
    considered — locked here by an assertion so a future reordering of
    that check can't silently break the common case."""

    def test_open_pr_only_no_merged_row_survives(self, tmp_path, fake_gh):
        local, bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/in-review")

        env = fake_gh({
            "feat/in-review": [{"number": 21, "state": "OPEN"}],
        })
        result = _run_script(local, env)

        assert result.returncode == 0
        ref_check = subprocess.run(
            ["git", "rev-parse", "--verify", "refs/heads/feat/in-review"],
            cwd=local, capture_output=True,
        )
        assert ref_check.returncode == 0, "branch with an open PR must survive"
        assert "open PR #21" in result.stdout


class TestFailClosedOnGhError:
    """A `gh` failure must never be read as "no PR found" — the branch is
    skipped even when it would otherwise qualify via Tier B reachability,
    proving fail-closed overrides Tier B rather than falling through to it.

    Two independent failure paths reach the same verdict: a non-zero exit
    from `gh` itself, and a zero exit whose body will not parse. Both are
    covered here, because reading either as "no PR found" is the mistake
    that lets a branch with an open PR be deleted."""

    def test_gh_error_skips_even_reachable_branch(self, tmp_path, fake_gh):
        local, remote = _make_repo_with_remote(tmp_path)
        _make_tier_b_branch(local, remote, "reachable-but-erroring")

        env = fake_gh({"reachable-but-erroring": "error"})
        result = _run_script(local, env)

        assert result.returncode == 0
        ref_check = subprocess.run(
            ["git", "rev-parse", "--verify", "refs/heads/reachable-but-erroring"],
            cwd=local, capture_output=True,
        )
        assert ref_check.returncode == 0, "gh failure must fail closed, not fall through to Tier B"
        assert "gh lookup failed" in result.stdout

    def test_gh_error_on_one_branch_does_not_block_sibling_cleanup(self, tmp_path, fake_gh):
        """A `gh` failure on one branch must not poison or abort the sweep:
        a sibling branch in the same run that is normally Tier-A-mergeable
        is still cleaned."""
        local, remote = _make_repo_with_remote(tmp_path)
        _make_tier_b_branch(local, remote, "reachable-but-erroring")
        _make_feature_branch(local, "feat/healthy-merged")

        env = fake_gh({
            "reachable-but-erroring": "error",
            "feat/healthy-merged": {"number": 42, "mergedAt": "2026-05-01"},
        })
        result = _run_script(local, env)

        assert result.returncode == 0
        erroring_ref = subprocess.run(
            ["git", "rev-parse", "--verify", "refs/heads/reachable-but-erroring"],
            cwd=local, capture_output=True,
        )
        assert erroring_ref.returncode == 0, "erroring branch must survive"
        healthy_ref = subprocess.run(
            ["git", "rev-parse", "--verify", "refs/heads/feat/healthy-merged"],
            cwd=local, capture_output=True,
        )
        assert healthy_ref.returncode != 0, "healthy sibling branch must still be cleaned"

    def test_malformed_json_on_zero_exit_skips_even_reachable_branch(self, tmp_path, fake_gh):
        """`gh` exits 0 but its body will not parse: the branch is skipped,
        not treated as "no PR found". Substituting an empty PR list here
        would bypass the open-PR guard for any branch whose response is
        truncated or polluted, which is the shape of the original
        incident."""
        local, remote = _make_repo_with_remote(tmp_path)
        _make_tier_b_branch(local, remote, "reachable-but-malformed")

        env = fake_gh({"reachable-but-malformed": "malformed"})
        result = _run_script(local, env)

        assert result.returncode == 0
        ref_check = subprocess.run(
            ["git", "rev-parse", "--verify", "refs/heads/reachable-but-malformed"],
            cwd=local, capture_output=True,
        )
        assert ref_check.returncode == 0, "unparseable gh output must fail closed, not fall through to Tier B"
        assert "gh lookup failed" in result.stdout


class TestAggregatedSkipLinesSingleRepo:
    """print_skip_reason_lines groups by identical reason text — a repo
    where every branch shares one skip reason reports it once, with a
    count and the branch names, not once per branch."""

    def test_single_skip_reason_uses_count_of_one_phrasing(self, tmp_path, fake_gh):
        local, remote = _make_repo_with_remote(tmp_path)
        _make_tier_b_branch(local, remote, "solo-erroring-branch")

        env = fake_gh({"solo-erroring-branch": "error"})
        result = _run_script(local, env)

        assert result.returncode == 0
        assert (
            "Skipped 1 branch(es) (gh lookup failed; skipping to fail closed): "
            "solo-erroring-branch" in result.stdout
        )

    def test_gh_failures_aggregate_while_open_pr_skip_stays_per_branch(self, tmp_path, fake_gh):
        """Distinct-PR-number skips (open PR, stale name) carry different
        text per branch and so are never collapsed, while byte-identical
        `gh` failure reasons are."""
        local, remote = _make_repo_with_remote(tmp_path)
        _make_tier_b_branch(local, remote, "erroring-one")
        _make_tier_b_branch(local, remote, "erroring-two")
        _make_feature_branch(local, "feat/in-review")

        env = fake_gh({
            "erroring-one": "error",
            "erroring-two": "error",
            "feat/in-review": [{"number": 30, "state": "OPEN"}],
        })
        result = _run_script(local, env)

        assert result.returncode == 0
        # Membership, not hardcoded join order — the grouping invariant
        # doesn't care which erroring branch is listed first.
        gh_failure_lines = [
            line for line in result.stdout.splitlines()
            if line.startswith("Skipped 2 branch(es) (gh lookup failed; skipping to fail closed): ")
        ]
        assert len(gh_failure_lines) == 1, f"expected exactly one aggregated skip line; got: {result.stdout!r}"
        listed_branches = gh_failure_lines[0].rsplit(": ", 1)[1].split(", ")
        assert set(listed_branches) == {"erroring-one", "erroring-two"}
        assert "Skipped 1 branch(es) (open PR #30): feat/in-review" in result.stdout
        # No causal wording added to the aggregated line — the reason text
        # must stay accurate for non-credential gh failures too (rate
        # limit, network, a non-GitHub or local-only remote).
        assert "check your credentials" not in result.stdout.lower()


class TestStaleNameNoOpenPR:
    """A merged PR sharing this branch name, but whose headRefOid does not
    match the current tip, is not a Tier-A match — the name was reused
    after that PR merged."""

    def test_stale_name_match_not_cleaned(self, tmp_path, fake_gh):
        local, bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "reused-branch-name")

        env = fake_gh({
            "reused-branch-name": [
                {"number": 7, "state": "MERGED", "mergedAt": "2026-06-16", "headRefOid": "a" * 40},
            ],
        })
        result = _run_script(local, env)

        assert result.returncode == 0
        ref_check = subprocess.run(
            ["git", "rev-parse", "--verify", "refs/heads/reused-branch-name"],
            cwd=local, capture_output=True,
        )
        assert ref_check.returncode == 0, "stale name-only match must not be deleted"
        assert "likely a reused branch name" in result.stdout


class TestMultipleMergedRowsScanFullHistory:
    """Guard 2's tip-vs-headRefOid match scans every MERGED row for a head
    branch name, not just the first — a name can accumulate more than one
    merged PR over its history, and the matching row is not always first."""

    def test_tip_matches_second_of_two_merged_rows_cleans_branch(self, tmp_path, fake_gh):
        local, bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/reused-name-twice")
        tip = _rev_parse(local, "feat/reused-name-twice")

        env = fake_gh({
            "feat/reused-name-twice": [
                {"number": 5, "state": "MERGED", "mergedAt": "2026-04-01", "headRefOid": "a" * 40},
                {"number": 9, "state": "MERGED", "mergedAt": "2026-06-01", "headRefOid": tip},
            ],
        })
        result = _run_script(local, env)

        assert result.returncode == 0
        ref_check = subprocess.run(
            ["git", "rev-parse", "--verify", "refs/heads/feat/reused-name-twice"],
            cwd=local, capture_output=True,
        )
        assert ref_check.returncode != 0, "a tip match on the second MERGED row must still be Tier A"

    def test_tip_matches_neither_of_two_merged_rows_skipped_stale(self, tmp_path, fake_gh):
        local, bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/reused-name-neither-matches")

        env = fake_gh({
            "feat/reused-name-neither-matches": [
                {"number": 5, "state": "MERGED", "mergedAt": "2026-04-01", "headRefOid": "a" * 40},
                {"number": 9, "state": "MERGED", "mergedAt": "2026-06-01", "headRefOid": "b" * 40},
            ],
        })
        result = _run_script(local, env)

        assert result.returncode == 0
        ref_check = subprocess.run(
            ["git", "rev-parse", "--verify", "refs/heads/feat/reused-name-neither-matches"],
            cwd=local, capture_output=True,
        )
        assert ref_check.returncode == 0, "no matching MERGED row among several must not be Tier A"
        assert "likely a reused branch name" in result.stdout


class TestSquashMergeStillCleanedAfterTipGuard:
    """Regression: a legitimately squash/rebase-merged branch (tip not
    reachable from origin/<default>, but tip == the merged PR's
    headRefOid) is still cleaned once the tip-verification guard is in
    place — the guard must not reject the common case it was added
    alongside."""

    def test_squash_merged_branch_with_matching_tip_is_cleaned(self, tmp_path, fake_gh):
        local, bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/squash-merged")
        tip = _rev_parse(local, "feat/squash-merged")
        subprocess.run(["git", "branch", "-D", "feat/squash-merged"], cwd=bare, check=True)

        env = fake_gh({
            "feat/squash-merged": {"number": 500, "mergedAt": "2026-05-01", "headRefOid": tip},
        })
        result = _run_script(local, env)

        assert result.returncode == 0
        ref_check = subprocess.run(
            ["git", "rev-parse", "--verify", "refs/heads/feat/squash-merged"],
            cwd=local, capture_output=True,
        )
        assert ref_check.returncode != 0, "matching-tip merged PR must still be cleaned"


class TestClosedUnmergedOnlyLeftUntouched:
    """A branch whose sole PR record is closed without merging (mergedAt is
    null) is Tier C — left untouched, and not misreported as a stale-name
    skip (there is no merged PR to have reused the name from)."""

    def test_closed_unmerged_pr_not_cleaned_and_not_reported_stale(self, tmp_path, fake_gh):
        local, bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/closed-unmerged")

        env = fake_gh({
            "feat/closed-unmerged": [
                {"number": 9, "state": "CLOSED", "mergedAt": None},
            ],
        })
        result = _run_script(local, env)

        assert result.returncode == 0
        ref_check = subprocess.run(
            ["git", "rev-parse", "--verify", "refs/heads/feat/closed-unmerged"],
            cwd=local, capture_output=True,
        )
        assert ref_check.returncode == 0, "closed-unmerged-only branch is Tier C, never touched"
        assert "stale" not in result.stdout.lower()


class TestCheckedOutIncidentBranchReportsOpenPRSkip:
    """The checked-out-branch message site must honor the open-PR guard
    too: an incident-shaped branch that happens to be checked out is
    reported via its open-PR reason, never deleted."""

    def test_checked_out_open_pr_branch_reports_reason_and_survives(self, tmp_path, fake_gh):
        local, bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "reused-name-with-open-pr")
        _make_feature_branch(local, "feat/other-merged")
        subprocess.run(["git", "branch", "-D", "feat/other-merged"], cwd=bare, check=True)

        subprocess.run(["git", "checkout", "-q", "reused-name-with-open-pr"], cwd=local, check=True)

        env = fake_gh({
            "reused-name-with-open-pr": [
                {"number": 14, "state": "OPEN"},
                {"number": 7, "state": "MERGED", "mergedAt": "2026-06-16", "headRefOid": "a" * 40},
            ],
            "feat/other-merged": {"number": 71, "mergedAt": "2026-05-02"},
        })
        result = _run_script(local, env)

        assert result.returncode == 0
        assert "open PR #14" in result.stdout
        ref_check = subprocess.run(
            ["git", "rev-parse", "--verify", "refs/heads/reused-name-with-open-pr"],
            cwd=local, capture_output=True,
        )
        assert ref_check.returncode == 0, "checked-out branch must never be deleted regardless of verdict"
        other_remaining = subprocess.run(
            ["git", "branch", "--list", "feat/other-merged"],
            cwd=local, capture_output=True, text=True,
        ).stdout
        assert other_remaining.strip() == "", "the unrelated tier-a branch should still be cleaned"


class TestCheckedOutTierBBranchReportsSkip:
    """The checked-out-branch message site must also honor a Tier-B
    verdict: a branch reachable from origin/<default> with no PR match at
    all that happens to be checked out is reported as skipped, never
    deleted. TestCurrentlyOnCandidateBranch already covers the Tier-A
    verdict at this same call site; this fills the Tier-B gap."""

    def test_checked_out_tier_b_branch_reports_reason_and_survives(self, tmp_path, fake_gh):
        local, remote = _make_repo_with_remote(tmp_path)
        _make_tier_b_branch(local, remote, "tier-b-checked-out")
        # A second, ordinary Tier-A candidate keeps the run past the
        # early "nothing to clean" exit so checked_out_skip_line() at the
        # end of the real-run path actually runs.
        _make_feature_branch(local, "feat/other-merged")
        subprocess.run(["git", "branch", "-D", "feat/other-merged"], cwd=remote, check=True)
        subprocess.run(["git", "checkout", "-q", "tier-b-checked-out"], cwd=local, check=True)

        env = fake_gh({
            "feat/other-merged": {"number": 71, "mergedAt": "2026-05-02"},
        })
        result = _run_script(local, env)

        assert result.returncode == 0
        assert "Skipped: tier-b-checked-out (currently checked out)" in result.stdout
        ref_check = subprocess.run(
            ["git", "rev-parse", "--verify", "refs/heads/tier-b-checked-out"],
            cwd=local, capture_output=True,
        )
        assert ref_check.returncode == 0, "checked-out branch must never be deleted regardless of verdict"


class TestCheckedOutNonCandidateStaysSilent:
    """The checked-out-branch message is a cleanup-candidate notice, not a
    running commentary: a checked-out branch that is not a candidate at all
    (a stale name-only match) produces no line. Locks the silent arm so a
    future edit does not start reporting every checked-out branch."""

    def test_checked_out_stale_name_branch_reports_nothing(self, tmp_path, fake_gh):
        local, bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "checked-out-stale-name")
        # A second, ordinary Tier-A candidate keeps the run past the early
        # "nothing to clean" exit so the checked-out message site runs at all.
        _make_feature_branch(local, "feat/other-merged")
        subprocess.run(["git", "branch", "-D", "feat/other-merged"], cwd=bare, check=True)
        subprocess.run(["git", "checkout", "-q", "checked-out-stale-name"], cwd=local, check=True)

        env = fake_gh({
            "checked-out-stale-name": [
                {"number": 7, "state": "MERGED", "mergedAt": "2026-06-16", "headRefOid": "a" * 40},
            ],
            "feat/other-merged": {"number": 71, "mergedAt": "2026-05-02"},
        })
        result = _run_script(local, env)

        assert result.returncode == 0
        assert "checked-out-stale-name" not in result.stdout, (
            "a checked-out branch that is not a cleanup candidate must not be reported"
        )
        ref_check = subprocess.run(
            ["git", "rev-parse", "--verify", "refs/heads/checked-out-stale-name"],
            cwd=local, capture_output=True,
        )
        assert ref_check.returncode == 0, "checked-out branch must never be deleted regardless of verdict"


class TestTierBWithStaleMergedRowReportsBothSignals:
    """A branch can be both reachable from origin/<default> and carry a
    same-named merged PR whose tip does not match — Tier B by reachability,
    stale by name. Under non-TTY stdin it is skipped with a warning, not
    cleaned; a sibling test covers the --dry-run preview, where the reason
    line names the same-named merged PR rather than claiming no merged PR
    exists for the name."""

    _STALE_MERGED_ROW = {
        "tier-b-with-stale-name": [
            {"number": 33, "state": "MERGED", "mergedAt": "2026-04-01", "headRefOid": "a" * 40},
        ],
    }

    def test_tier_b_reachable_with_stale_merged_row_skipped_non_tty(self, tmp_path, fake_gh):
        local, remote = _make_repo_with_remote(tmp_path)
        _make_tier_b_branch(local, remote, "tier-b-with-stale-name")

        env = fake_gh(self._STALE_MERGED_ROW)
        result = _run_script(local, env)

        assert result.returncode == 0
        assert "no TTY for prompt" in result.stdout
        ref_check = subprocess.run(
            ["git", "rev-parse", "--verify", "refs/heads/tier-b-with-stale-name"],
            cwd=local, capture_output=True,
        )
        assert ref_check.returncode == 0, (
            "reachability qualifies the branch for Tier B, but non-TTY stdin skips it rather than deleting"
        )

    def test_tier_b_reason_names_the_same_named_merged_pr(self, tmp_path, fake_gh):
        """The reason string only appears in the dry-run preview; a plain
        run's "Cleaned up:" per-branch block never prints it."""
        local, remote = _make_repo_with_remote(tmp_path)
        _make_tier_b_branch(local, remote, "tier-b-with-stale-name")

        env = fake_gh(self._STALE_MERGED_ROW)
        result = _run_script(local, env, args=["--dry-run"])

        assert result.returncode == 0
        assert "a merged PR #33 shares this name" in result.stdout
        assert "no merged PR for this name" not in result.stdout


class TestClassifierEmitsNoShellDiagnostics:
    """classify_branch builds its parser source as a double-quoted shell
    string, so anything shell-active in that source is expanded before
    python sees it. A stray backtick or dollar sign there is silently eaten
    (and could be executed), announcing itself only as shell noise on
    stderr — which nothing else in this suite would notice."""

    def test_normal_run_writes_nothing_to_stderr(self, tmp_path, fake_gh):
        local, bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/ordinary-merged")
        subprocess.run(["git", "branch", "-D", "feat/ordinary-merged"], cwd=bare, check=True)

        env = fake_gh({"feat/ordinary-merged": {"number": 88, "mergedAt": "2026-05-02"}})
        result = _run_script(local, env)

        assert result.returncode == 0
        assert result.stderr == "", (
            f"classification must not emit shell diagnostics; got: {result.stderr!r}"
        )


# ---------------------------------------------------------------------------
# load_repo_environment — applies to both the single-repo and sweep paths;
# the cases below that don't need a multi-repo sweep run single-repo.
# ---------------------------------------------------------------------------

class TestDirenvAbsentBehaviorUnchanged:
    """With no direnv on PATH, load_repo_environment is a no-op — behavior
    is identical to before this feature existed."""

    def test_absent_direnv_cleans_normally(self, tmp_path, fake_gh):
        local, bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/done")
        subprocess.run(["git", "branch", "-D", "feat/done"], cwd=bare, check=True)

        env = fake_gh(
            {"feat/done": {"number": 1, "mergedAt": "2026-05-01"}},
            direnv_present=False,
        )
        result = _run_script(local, env)

        assert result.returncode == 0
        branches = subprocess.run(
            ["git", "branch", "--list", "feat/done"],
            cwd=local, capture_output=True, text=True,
        ).stdout
        assert branches.strip() == ""


class TestDirenvNonAllowedEnvrcBehaviorUnchanged:
    """A non-`allow`ed .envrc (direnv exits 1 with an unset payload on
    stdout) is discarded cleanly — behavior identical to
    the absent-direnv case."""

    def test_direnv_exit_nonzero_cleans_normally(self, tmp_path, fake_gh):
        local, bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/done")
        subprocess.run(["git", "branch", "-D", "feat/done"], cwd=bare, check=True)

        env = fake_gh(
            {"feat/done": {"number": 1, "mergedAt": "2026-05-01"}},
            direnv_source=_direnv_shim_source_exits_nonzero_with_unset_payload(),
        )
        result = _run_script(local, env)

        assert result.returncode == 0
        branches = subprocess.run(
            ["git", "branch", "--list", "feat/done"],
            cwd=local, capture_output=True, text=True,
        ).stdout
        assert branches.strip() == ""


class TestLoadedCredentialNeverAppearsInOutput:
    """load_repo_environment captures `direnv export bash`'s stdout straight
    into a shell variable via `eval` and never echoes it — a credential the
    per-repo .envrc exports must not reach the script's own stdout/stderr,
    including on the abort/error paths (git-identity guard, gh-auth
    downgrade warning) that run after the eval."""

    def test_direnv_exported_token_value_is_never_printed(self, tmp_path, fake_gh):
        local, bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/done")
        subprocess.run(["git", "branch", "-D", "feat/done"], cwd=bare, check=True)

        # A distinctive sentinel, not a real-token-shaped string (e.g. no
        # `ghp_` prefix) — this repo's own credential-redaction hook rewrites
        # token-shaped literals in source on write, which would otherwise
        # silently replace this fixture with a placeholder and defeat the
        # test's purpose.
        exported_value = "direnv-export-value-must-never-leak-9f3a7c21b6"
        env = fake_gh(
            {"feat/done": {"number": 1, "mergedAt": "2026-05-01"}},
            direnv_source=_direnv_shim_source_static_export("GH_TOKEN", exported_value),
        )
        result = _run_script(local, env)

        assert exported_value not in result.stdout
        assert exported_value not in result.stderr


class TestDirenvStdinReadingEnvrcDoesNotHangScript:
    """An .envrc that reads stdin must not hang the script —
    load_repo_environment's `</dev/null` isolates the direnv call from
    whatever the harness's own stdin is doing. A pipe kept open with
    nothing written proves this: `_run_script`'s default
    stdin=DEVNULL would hit EOF immediately either way, passing on unfixed
    code (a tautological test), which is why this needs a held-open fd."""

    def test_stdin_reading_envrc_does_not_hang(self, tmp_path, fake_gh):
        local, bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/done")
        subprocess.run(["git", "branch", "-D", "feat/done"], cwd=bare, check=True)

        env = fake_gh(
            {"feat/done": {"number": 1, "mergedAt": "2026-05-01"}},
            direnv_source=_direnv_shim_source_reads_stdin(),
        )

        read_fd, write_fd = os.pipe()
        proc = subprocess.Popen(
            [str(_SCRIPT)], cwd=local, env=env,
            stdin=read_fd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        os.close(read_fd)
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            pytest.fail(
                "script hung on a stdin-reading .envrc — "
                "load_repo_environment's </dev/null guard regressed"
            )
        finally:
            os.close(write_fd)

        assert proc.returncode == 0
        branches = subprocess.run(
            ["git", "branch", "--list", "feat/done"],
            cwd=local, capture_output=True, text=True,
        ).stdout
        assert branches.strip() == "", "Tier A branch must still be cleaned"


class TestEnvrcCannotOverrideDryRun:
    """An .envrc exporting DRY_RUN=0 must not convert `--dry-run` into a
    real deletion run — readonly DRY_RUN aborts the eval under set -e
    instead. Asserted behaviorally: bash's `readonly variable` diagnostic
    text and line number differ between bash 3.2 (macOS) and 5.x (CI)."""

    def test_envrc_dry_run_override_is_rejected(self, tmp_path, fake_gh):
        local, bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/would-delete")

        env = fake_gh(
            {"feat/would-delete": {"number": 1, "mergedAt": "2026-05-01"}},
            direnv_source=_direnv_shim_source_static_export("DRY_RUN", "0"),
        )
        result = _run_script(local, env, args=["--dry-run"])

        assert result.returncode != 0
        local_branches = subprocess.run(
            ["git", "branch", "--list", "feat/would-delete"],
            cwd=local, capture_output=True, text=True,
        ).stdout
        assert "feat/would-delete" in local_branches, "readonly override must prevent a real delete"
        remote_refs = subprocess.run(
            ["git", "ls-remote", "--heads", "origin", "feat/would-delete"],
            cwd=local, capture_output=True, text=True,
        ).stdout
        assert "feat/would-delete" in remote_refs, "remote branch must survive too — no push --delete issued"


_REDIRECTION_GUARD_BRANCH = "shared-branch-name"


def _build_redirection_guard_repos(tmp_path: Path):
    """Two independent repos sharing one candidate branch name, for the
    GIT_DIR/GIT_WORK_TREE/GIT_COMMON_DIR redirection-guard tests (case 9).
    Repo B carries a real local branch and a real pushed remote ref under
    that name so a redirected destructive op has something to actually
    delete — an empty or differently-named repo B would pass the
    assertions below on both correct and broken code."""
    repo_a_dir = tmp_path / "repo-a"
    repo_a_dir.mkdir()
    repo_b_dir = tmp_path / "repo-b"
    repo_b_dir.mkdir()
    local_a, bare_a = _make_repo_with_remote(repo_a_dir)
    local_b, bare_b = _make_repo_with_remote(repo_b_dir)
    _make_feature_branch(local_a, _REDIRECTION_GUARD_BRANCH)
    _make_feature_branch(local_b, _REDIRECTION_GUARD_BRANCH)
    return local_a, bare_a, local_b, bare_b


def _assert_redirection_guard_did_not_touch_either_repo(local_a, local_b):
    branches_a = subprocess.run(
        ["git", "branch", "--list", _REDIRECTION_GUARD_BRANCH],
        cwd=local_a, capture_output=True, text=True,
    ).stdout
    assert _REDIRECTION_GUARD_BRANCH in branches_a, "repo A's own branch must be untouched"

    branches_b = subprocess.run(
        ["git", "branch", "--list", _REDIRECTION_GUARD_BRANCH],
        cwd=local_b, capture_output=True, text=True,
    ).stdout
    assert _REDIRECTION_GUARD_BRANCH in branches_b, "repo B's local branch must survive"

    remote_refs_b = subprocess.run(
        ["git", "ls-remote", "--heads", "origin", _REDIRECTION_GUARD_BRANCH],
        cwd=local_b, capture_output=True, text=True,
    ).stdout
    assert _REDIRECTION_GUARD_BRANCH in remote_refs_b, "repo B's remote ref must survive"


class TestEnvrcRedirectionGuard:
    """An .envrc exporting GIT_DIR, GIT_WORK_TREE, or GIT_COMMON_DIR must
    not repoint the script's destructive git ops at a different repository
    — the post-eval re-check in run_repo_cleanup aborts this repo instead.
    Three sub-cases share one fixture (_build_redirection_guard_repos)
    since each env var evades a different subset of the three
    `git rev-parse` checks."""

    def test_git_dir_alone_aborts(self, tmp_path, fake_gh):
        local_a, bare_a, local_b, bare_b = _build_redirection_guard_repos(tmp_path)
        env = fake_gh(
            {_REDIRECTION_GUARD_BRANCH: {"number": 1, "mergedAt": "2026-05-01"}},
            direnv_source=_direnv_shim_source_static_export(
                "GIT_DIR", str(local_b / ".git"),
            ),
        )
        result = _run_script(local_a, env)

        assert result.returncode != 0
        assert "environment changed git's repo root" in result.stderr, (
            "GIT_DIR-only sub-case: the guard's distinct abort message must fire "
            "(--show-toplevel alone does not change under GIT_DIR)"
        )
        _assert_redirection_guard_did_not_touch_either_repo(local_a, local_b)

    def test_git_work_tree_aborts(self, tmp_path, fake_gh):
        local_a, bare_a, local_b, bare_b = _build_redirection_guard_repos(tmp_path)
        env = fake_gh(
            {_REDIRECTION_GUARD_BRANCH: {"number": 1, "mergedAt": "2026-05-01"}},
            direnv_source=_direnv_shim_source_static_export(
                "GIT_WORK_TREE", str(local_b),
            ),
        )
        result = _run_script(local_a, env)

        assert result.returncode != 0
        assert "environment changed git's repo root" in result.stderr, (
            "GIT_WORK_TREE sub-case: the guard's distinct abort message must fire "
            "(caught by the --show-toplevel check)"
        )
        _assert_redirection_guard_did_not_touch_either_repo(local_a, local_b)

    def test_git_common_dir_alone_aborts(self, tmp_path, fake_gh):
        local_a, bare_a, local_b, bare_b = _build_redirection_guard_repos(tmp_path)
        env = fake_gh(
            {_REDIRECTION_GUARD_BRANCH: {"number": 1, "mergedAt": "2026-05-01"}},
            direnv_source=_direnv_shim_source_static_export(
                "GIT_COMMON_DIR", str(local_b / ".git"),
            ),
        )
        result = _run_script(local_a, env)

        assert result.returncode != 0
        assert "environment changed git's repo root" in result.stderr, (
            "GIT_COMMON_DIR-only sub-case: the guard's distinct abort message must "
            "fire (caught by neither --show-toplevel nor --absolute-git-dir)"
        )
        _assert_redirection_guard_did_not_touch_either_repo(local_a, local_b)


class TestEnvrcNoMatchingEntryGetsUnsetPayload:
    """A repo under no .envrc, with the invoking shell's own credentials
    already loaded, must have them unset by direnv's scripted `unset`
    payload. This exercises load_repo_environment's handling of an
    `unset` line — it cannot pin real direnv's own behavior outside any
    .envrc, which isn't reproducible in a shim."""

    def test_no_envrc_entry_unsets_the_invoking_shells_token(self, tmp_path):
        local, bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/only-under-parent-token")

        gh_shim_source = _gh_shim_source_by_token({
            "loaded-parent-token": {
                "feat/only-under-parent-token": {"number": 1, "mergedAt": "2026-05-01"},
            },
        })
        env = _shimmed_env(
            tmp_path, gh_shim_source,
            direnv_source=_direnv_shim_source_unconditional_unset("GH_TOKEN"),
        )
        # Models the invoking shell already having a container's GH_TOKEN
        # loaded before the script starts, e.g. this repo is a container
        # sibling but itself carries no matching .envrc.
        env["GH_TOKEN"] = "loaded-parent-token"

        result = _run_script(local, env)

        assert result.returncode == 0
        branches = subprocess.run(
            ["git", "branch", "--list", "feat/only-under-parent-token"],
            cwd=local, capture_output=True, text=True,
        ).stdout
        assert "feat/only-under-parent-token" in branches, (
            "the unset payload must remove the invoking shell's GH_TOKEN — "
            "if it survived, this branch would show up as merged and be deleted"
        )


# ---------------------------------------------------------------------------
# --all-projects: sweeps the cleanup across every repo under configured roots
#
# CLEANUP_MERGED_BRANCHES_ROOTS_FILE is the test seam for the roots config
# file (mirrors resume-context.sh's RESUME_CONTEXT_TMPDIR) — production runs
# never set it, reading ~/.claude/cleanup-merged-branches-roots instead.
# ---------------------------------------------------------------------------

def _run_all_projects(
    tmp_path: Path,
    env: dict,
    roots_file: Path,
    extra_args: list[str] | None = None,
) -> subprocess.CompletedProcess:
    """Invoke the script with --all-projects from an arbitrary cwd (tmp_path).

    Root discovery doesn't depend on the invoking cwd, unlike the single-repo
    path's `git rev-parse --show-toplevel` — tmp_path itself is never a repo.
    """
    run_env = {**env, "CLEANUP_MERGED_BRANCHES_ROOTS_FILE": str(roots_file)}
    return subprocess.run(
        [str(_SCRIPT), "--all-projects"] + (extra_args or []),
        cwd=str(tmp_path),
        env=run_env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
    )


class TestAllProjectsLoadsPerRepoEnvironment:
    """Each repo in a --all-projects sweep is queried with that repo's own
    direnv-sourced identity, not the invoking shell's (or a sibling
    repo's). The gh shim is keyed on GH_TOKEN, never cwd — keying on cwd
    would let this pass without direnv ever running. Fails on code that
    never calls load_repo_environment."""

    def test_two_repos_each_use_their_own_direnv_identity(self, tmp_path):
        root = tmp_path / "root"
        root.mkdir()
        repo_a_dir = root / "repo-a"
        repo_a_dir.mkdir()
        repo_b_dir = root / "repo-b"
        repo_b_dir.mkdir()
        local_a, bare_a = _make_repo_with_remote(repo_a_dir)
        local_b, bare_b = _make_repo_with_remote(repo_b_dir)
        _make_feature_branch(local_a, "feat/a-merged")
        _make_feature_branch(local_b, "feat/b-merged")

        direnv_source = _direnv_shim_source_by_cwd({
            str(local_a.resolve()): {"GH_TOKEN": "token-a"},
            str(local_b.resolve()): {"GH_TOKEN": "token-b"},
        })
        gh_shim_source = _gh_shim_source_by_token({
            "token-a": {"feat/a-merged": {"number": 1, "mergedAt": "2026-05-01"}},
            "token-b": {"feat/b-merged": {"number": 2, "mergedAt": "2026-05-02"}},
        })
        env = _shimmed_env(tmp_path, gh_shim_source, direnv_source=direnv_source)

        roots_file = tmp_path / "roots"
        roots_file.write_text(f"{root}\n")
        result = _run_all_projects(tmp_path, env, roots_file)

        assert result.returncode == 0
        branches_a = subprocess.run(
            ["git", "branch", "--list", "feat/a-merged"],
            cwd=local_a, capture_output=True, text=True,
        ).stdout
        assert branches_a.strip() == "", "repo A must be cleaned under its own identity"
        branches_b = subprocess.run(
            ["git", "branch", "--list", "feat/b-merged"],
            cwd=local_b, capture_output=True, text=True,
        ).stdout
        assert branches_b.strip() == "", "repo B must be cleaned under its own identity"


class TestAllProjectsNoCrossRepoCredentialLeak:
    """A repo with no matching .envrc entry must not inherit an earlier
    repo's exported identity in the same sweep."""

    def test_middle_repo_without_envrc_entry_does_not_inherit_earlier_identity(self, tmp_path):
        root = tmp_path / "root"
        root.mkdir()
        repo_dirs = {}
        for name in ("repo-1", "repo-2", "repo-3"):
            repo_dir = root / name
            repo_dir.mkdir()
            repo_dirs[name] = repo_dir
        local_1, bare_1 = _make_repo_with_remote(repo_dirs["repo-1"])
        local_2, bare_2 = _make_repo_with_remote(repo_dirs["repo-2"])
        local_3, bare_3 = _make_repo_with_remote(repo_dirs["repo-3"])
        # Same branch name in every repo: if repo-2 inherited repo-1's
        # identity, gh would report it merged there too.
        shared_branch = "feat/shared-name"
        _make_feature_branch(local_1, shared_branch)
        _make_feature_branch(local_2, shared_branch)
        _make_feature_branch(local_3, shared_branch)

        direnv_source = _direnv_shim_source_by_cwd({
            str(local_1.resolve()): {"GH_TOKEN": "token-1"},
            # local_2 deliberately absent: models a repo under no .envrc.
            str(local_3.resolve()): {"GH_TOKEN": "token-3"},
        })
        gh_shim_source = _gh_shim_source_by_token({
            "token-1": {shared_branch: {"number": 1, "mergedAt": "2026-05-01"}},
        })
        env = _shimmed_env(tmp_path, gh_shim_source, direnv_source=direnv_source)

        roots_file = tmp_path / "roots"
        roots_file.write_text(f"{root}\n")
        result = _run_all_projects(tmp_path, env, roots_file)

        assert result.returncode == 0
        branches_1 = subprocess.run(
            ["git", "branch", "--list", shared_branch],
            cwd=local_1, capture_output=True, text=True,
        ).stdout
        assert shared_branch not in branches_1, "repo-1 must still be cleaned under its own identity"
        branches_2 = subprocess.run(
            ["git", "branch", "--list", shared_branch],
            cwd=local_2, capture_output=True, text=True,
        ).stdout
        assert shared_branch in branches_2, (
            "repo-2 has no .envrc entry and must not inherit repo-1's identity"
        )


class TestAggregatedSkipLinesAcrossSweep:
    """Aggregation, sweep variant, plus fail-closed preserved: a repo
    where every branch's gh lookup fails reports one skip line, the sweep
    still exits 0, and a healthy sibling repo in the same sweep is still
    fully cleaned."""

    def test_all_gh_failures_aggregate_and_sibling_repo_still_cleaned(self, tmp_path, fake_gh):
        root = tmp_path / "root"
        root.mkdir()
        failing_dir = root / "failing"
        failing_dir.mkdir()
        local_failing, bare_failing = _make_repo_with_remote(failing_dir)
        _make_tier_b_branch(local_failing, bare_failing, "reachable-erroring-one")
        _make_tier_b_branch(local_failing, bare_failing, "reachable-erroring-two")

        healthy_dir = root / "healthy"
        healthy_dir.mkdir()
        local_healthy, bare_healthy = _make_repo_with_remote(healthy_dir)
        _make_feature_branch(local_healthy, "feat/healthy-merged")
        subprocess.run(["git", "branch", "-D", "feat/healthy-merged"], cwd=bare_healthy, check=True)

        env = fake_gh({
            "reachable-erroring-one": "error",
            "reachable-erroring-two": "error",
            "feat/healthy-merged": {"number": 90, "mergedAt": "2026-05-01"},
        })
        roots_file = tmp_path / "roots"
        roots_file.write_text(f"{root}\n")
        result = _run_all_projects(tmp_path, env, roots_file)

        assert result.returncode == 0
        skip_lines = [
            line for line in result.stdout.splitlines()
            if line.startswith("Skipped 2 branch(es)")
        ]
        assert len(skip_lines) == 1, f"expected exactly one aggregated skip line; got: {result.stdout!r}"
        assert "reachable-erroring-one" in skip_lines[0]
        assert "reachable-erroring-two" in skip_lines[0]
        assert "gh lookup failed; skipping to fail closed" in skip_lines[0]

        # Fail-closed preserved: both erroring branches survive untouched.
        for branch in ("reachable-erroring-one", "reachable-erroring-two"):
            ref_check = subprocess.run(
                ["git", "rev-parse", "--verify", f"refs/heads/{branch}"],
                cwd=local_failing, capture_output=True,
            )
            assert ref_check.returncode == 0, f"{branch} must survive a gh failure"

        healthy_branches = subprocess.run(
            ["git", "branch", "--list", "feat/healthy-merged"],
            cwd=local_healthy, capture_output=True, text=True,
        ).stdout
        assert healthy_branches.strip() == "", "sibling repo must still be fully cleaned"


class TestAllProjectsGhAuthDowngradeWithDirenv:
    """Under --all-projects, an unauthenticated invoking-shell gh is
    downgraded to a warning when direnv is present — the sweep still
    proceeds."""

    def test_unauth_with_direnv_present_warns_and_proceeds(self, tmp_path, fake_gh):
        root = tmp_path / "root"
        root.mkdir()
        local, bare = _make_repo_with_remote(root)
        _make_feature_branch(local, "feat/done")
        subprocess.run(["git", "branch", "-D", "feat/done"], cwd=bare, check=True)

        env = fake_gh({
            "__auth__": "unauth",
            "feat/done": {"number": 1, "mergedAt": "2026-05-01"},
        })
        roots_file = tmp_path / "roots"
        roots_file.write_text(f"{root}\n")
        result = _run_all_projects(tmp_path, env, roots_file)

        assert result.returncode == 0
        assert "not authenticated" in result.stderr.lower()
        branches = subprocess.run(
            ["git", "branch", "--list", "feat/done"],
            cwd=local, capture_output=True, text=True,
        ).stdout
        assert branches.strip() == "", "the sweep must still proceed and clean the repo"


class TestAllProjectsGhAuthHardExitsWithoutDirenv:
    """Companion to TestAllProjectsGhAuthDowngradeWithDirenv: with no
    direnv, the invoking shell's credentials are what every repo actually
    gets, so an unauthenticated gh must still hard-exit — matching
    test_gh_unauth_exits_nonzero's single-repo behavior."""

    def test_unauth_without_direnv_still_exits_nonzero(self, tmp_path, fake_gh):
        # The auth check runs before roots-file discovery, so no repo needs
        # to exist under this root for this test to reach its assertion.
        root = tmp_path / "root"
        root.mkdir()

        env = fake_gh({"__auth__": "unauth"}, direnv_present=False)
        roots_file = tmp_path / "roots"
        roots_file.write_text(f"{root}\n")
        result = _run_all_projects(tmp_path, env, roots_file)

        assert result.returncode != 0
        assert "not authenticated" in result.stderr.lower()


class TestAllProjectsRootsFileMissing:
    """--all-projects with CLEANUP_MERGED_BRANCHES_ROOTS_FILE pointed at a
    file that doesn't exist on disk."""

    def test_missing_roots_file_exits_nonzero_and_explains_format(self, tmp_path, fake_gh):
        env = fake_gh({})
        roots_file = tmp_path / "cleanup-merged-branches-roots"  # never created
        result = _run_all_projects(tmp_path, env, roots_file)

        assert result.returncode != 0
        assert str(roots_file) in result.stderr
        assert "per line" in result.stderr.lower()
        assert "#" in result.stderr  # comment-syntax mentioned


class TestAllProjectsRootsFileUnreadable:
    """Companion to TestAllProjectsRootsFileMissing: the roots file exists
    but isn't readable by the invoking user (e.g. after a restrictive
    umask or an accidental chmod) — `[ ! -r "$ROOTS_FILE" ]` is true for
    both cases, and both must take the same error path."""

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_unreadable_roots_file_exits_nonzero_and_explains_format(self, tmp_path, fake_gh):
        env = fake_gh({})
        roots_file = tmp_path / "cleanup-merged-branches-roots"
        roots_file.write_text("/some/root\n")
        roots_file.chmod(0o000)
        try:
            result = _run_all_projects(tmp_path, env, roots_file)
        finally:
            roots_file.chmod(0o644)  # restore before tmp_path teardown

        assert result.returncode != 0
        assert str(roots_file) in result.stderr
        assert "per line" in result.stderr.lower()
        assert "#" in result.stderr  # comment-syntax mentioned


class TestAllProjectsRootsFileParsing:
    """Blank lines, `#`-comments, a CRLF-terminated line, and a `~`-prefixed
    line in the roots file are all handled per the parsing spec (mirrors
    deny-private-project-refs.sh's private-projects.md parsing)."""

    def test_blank_comment_crlf_and_tilde_lines_all_handled(self, tmp_path, fake_gh):
        fake_home = tmp_path / "fake-home"
        code_root = fake_home / "code"
        code_root.mkdir(parents=True)

        local, bare = _make_repo_with_remote(code_root)
        _make_feature_branch(local, "feat/tilde-root-merge")
        subprocess.run(["git", "branch", "-D", "feat/tilde-root-merge"], cwd=bare, check=True)

        roots_file = tmp_path / "roots"
        roots_file.write_bytes(
            b"# repo roots for cleanup-merged-branches --all-projects\r\n"
            b"\n"
            b"   \n"
            b"~/code\r\n"
        )

        env = fake_gh({"feat/tilde-root-merge": {"number": 700, "mergedAt": "2026-05-01"}})
        env["HOME"] = str(fake_home)
        result = _run_all_projects(tmp_path, env, roots_file, extra_args=["--dry-run"])

        assert result.returncode == 0
        assert "not a directory" not in result.stderr.lower(), (
            "a blank or comment line must never be treated as a configured root"
        )
        assert f"== {local.resolve()} ==" in result.stdout
        assert "feat/tilde-root-merge" in result.stdout
        ref_check = subprocess.run(
            ["git", "rev-parse", "--verify", "refs/heads/feat/tilde-root-merge"],
            cwd=local, capture_output=True,
        )
        assert ref_check.returncode == 0, "dry-run must never delete"

    def test_bare_tilde_line_resolves_to_home_itself(self, tmp_path, fake_gh):
        """A roots-file line that is exactly `~` resolves to $HOME itself —
        distinct from the `~/`-prefixed form tested above."""
        fake_home = tmp_path / "fake-home"
        fake_home.mkdir()

        local, bare = _make_repo_with_remote(fake_home)
        _make_feature_branch(local, "feat/bare-tilde-root")
        subprocess.run(["git", "branch", "-D", "feat/bare-tilde-root"], cwd=bare, check=True)

        roots_file = tmp_path / "roots"
        roots_file.write_text("~\n")

        env = fake_gh({"feat/bare-tilde-root": {"number": 704, "mergedAt": "2026-05-01"}})
        env["HOME"] = str(fake_home)
        result = _run_all_projects(tmp_path, env, roots_file, extra_args=["--dry-run"])

        assert result.returncode == 0
        assert f"== {local.resolve()} ==" in result.stdout
        assert "feat/bare-tilde-root" in result.stdout
        ref_check = subprocess.run(
            ["git", "rev-parse", "--verify", "refs/heads/feat/bare-tilde-root"],
            cwd=local, capture_output=True,
        )
        assert ref_check.returncode == 0, "dry-run must never delete"

    def test_padded_whitespace_real_path_line_is_trimmed(self, tmp_path, fake_gh):
        """A roots-file line with real path content plus leading/trailing
        spaces exercises the trim logic itself, not just the all-whitespace
        blank-skip branch tested above."""
        root = tmp_path / "padded-root"
        root.mkdir()
        local, bare = _make_repo_with_remote(root)
        _make_feature_branch(local, "feat/padded-whitespace-root")
        subprocess.run(["git", "branch", "-D", "feat/padded-whitespace-root"], cwd=bare, check=True)

        roots_file = tmp_path / "roots"
        roots_file.write_text(f"  {root}  \n")

        env = fake_gh({"feat/padded-whitespace-root": {"number": 705, "mergedAt": "2026-05-01"}})
        result = _run_all_projects(tmp_path, env, roots_file, extra_args=["--dry-run"])

        assert result.returncode == 0
        assert "not a directory" not in result.stderr.lower(), (
            "leading/trailing whitespace on a real path line must be trimmed"
        )
        assert f"== {local.resolve()} ==" in result.stdout
        assert "feat/padded-whitespace-root" in result.stdout
        ref_check = subprocess.run(
            ["git", "rev-parse", "--verify", "refs/heads/feat/padded-whitespace-root"],
            cwd=local, capture_output=True,
        )
        assert ref_check.returncode == 0, "dry-run must never delete"


class TestAllProjectsMissingConfiguredRoot:
    """A configured root that no longer exists on disk (typo, deleted since
    configured) is warned to stderr and skipped; the sweep continues with
    the remaining roots."""

    def test_nonexistent_root_warns_and_sweep_continues(self, tmp_path, fake_gh):
        existing_root = tmp_path / "existing-root"
        existing_root.mkdir()
        local, bare = _make_repo_with_remote(existing_root)
        _make_feature_branch(local, "feat/in-existing-root")
        subprocess.run(["git", "branch", "-D", "feat/in-existing-root"], cwd=bare, check=True)

        missing_root = tmp_path / "does-not-exist"
        roots_file = tmp_path / "roots"
        roots_file.write_text(f"{missing_root}\n{existing_root}\n")

        env = fake_gh({"feat/in-existing-root": {"number": 701, "mergedAt": "2026-05-01"}})
        result = _run_all_projects(tmp_path, env, roots_file, extra_args=["--dry-run"])

        assert result.returncode == 0
        assert "not a directory" in result.stderr.lower()
        assert str(missing_root) in result.stderr
        assert f"== {local.resolve()} ==" in result.stdout
        assert "feat/in-existing-root" in result.stdout


class TestAllProjectsNestedRepoDiscoveryAndWorktreeExclusion:
    """Discovery finds a repo nested a couple of levels under a configured
    root, and never descends into a matched repo's own
    .claude/worktrees/<branch>/ subdirectory as if it were a second repo —
    that subdirectory's `.git` is a linked-worktree *file*, not a directory,
    and pruning already stopped descent one level up at the parent's own
    .git directory."""

    def test_nested_repo_found_worktree_subdir_not_double_counted(self, tmp_path, fake_gh):
        root = tmp_path / "root"
        nested_repo_dir = root / "org" / "team"
        nested_repo_dir.mkdir(parents=True)
        local, bare = _make_repo_with_remote(nested_repo_dir)
        _make_feature_branch(local, "feat/for-worktree")

        wt_path = local / ".claude" / "worktrees" / "feat-for-worktree"
        wt_path.parent.mkdir(parents=True)
        _make_worktree(local, "feat/for-worktree", wt_path)

        roots_file = tmp_path / "roots"
        roots_file.write_text(f"{root}\n")

        env = fake_gh({})
        result = _run_all_projects(tmp_path, env, roots_file, extra_args=["--dry-run"])

        assert result.returncode == 0
        header_lines = [line for line in result.stdout.splitlines() if line.startswith("== ")]
        assert header_lines == [f"== {local.resolve()} =="], (
            f"expected exactly one discovered repo; got headers: {header_lines!r}"
        )
        assert wt_path.exists(), "dry-run must never remove a worktree"
        ref_check = subprocess.run(
            ["git", "rev-parse", "--verify", "refs/heads/feat/for-worktree"],
            cwd=local, capture_output=True,
        )
        assert ref_check.returncode == 0, "dry-run must never delete"


class TestAllProjectsMaxRepoDiscoveryDepth:
    """MAX_REPO_DISCOVERY_DEPTH bounds how far discover_repo_roots descends
    under a configured root, so a root pointed at (e.g.) $HOME by mistake
    cannot make --all-projects walk the whole filesystem."""

    def test_repo_at_depth_limit_found_one_level_deeper_not(self, tmp_path, fake_gh):
        depth_match = re.search(
            r"^MAX_REPO_DISCOVERY_DEPTH=(\d+)", _SCRIPT.read_text(), re.MULTILINE
        )
        assert depth_match, "MAX_REPO_DISCOVERY_DEPTH constant not found in script"
        max_depth = int(depth_match.group(1))

        root = tmp_path / "root"
        root.mkdir()

        # _make_repo_with_remote adds two more path components below its
        # argument (a "local" subdir, then ".git" inside it), so the
        # argument itself needs max_depth - 2 nested levels to land the
        # repo's .git exactly at the maxdepth boundary.
        within_bound_parent = root
        for level in range(max_depth - 2):
            within_bound_parent = within_bound_parent / f"lvl{level}"
        within_bound_parent.mkdir(parents=True)
        local_in, _ = _make_repo_with_remote(within_bound_parent)

        beyond_bound_parent = root
        for level in range(max_depth - 1):
            beyond_bound_parent = beyond_bound_parent / f"lvl{level}"
        beyond_bound_parent.mkdir(parents=True)
        local_out, _ = _make_repo_with_remote(beyond_bound_parent)

        roots_file = tmp_path / "roots"
        roots_file.write_text(f"{root}\n")

        env = fake_gh({})
        result = _run_all_projects(tmp_path, env, roots_file, extra_args=["--dry-run"])

        assert result.returncode == 0
        header_lines = [line for line in result.stdout.splitlines() if line.startswith("== ")]
        assert f"== {local_in.resolve()} ==" in header_lines, (
            f"repo at the maxdepth boundary must be discovered; got: {header_lines!r}"
        )
        assert f"== {local_out.resolve()} ==" not in header_lines, (
            f"repo one level past maxdepth must not be discovered; got: {header_lines!r}"
        )


class TestAllProjectsDedupOverlappingRoots:
    """Two configured roots whose trees overlap (one nested inside the
    other) both reach the same repo — it is processed exactly once."""

    def test_overlapping_roots_process_repo_once(self, tmp_path, fake_gh):
        outer_root = tmp_path / "outer"
        inner_root = outer_root / "inner"
        inner_root.mkdir(parents=True)
        local, bare = _make_repo_with_remote(inner_root)
        _make_feature_branch(local, "feat/dedup-once")
        subprocess.run(["git", "branch", "-D", "feat/dedup-once"], cwd=bare, check=True)

        roots_file = tmp_path / "roots"
        roots_file.write_text(f"{outer_root}\n{inner_root}\n")

        env = fake_gh({"feat/dedup-once": {"number": 702, "mergedAt": "2026-05-01"}})
        result = _run_all_projects(tmp_path, env, roots_file)

        assert result.returncode == 0
        header_lines = [line for line in result.stdout.splitlines() if line.startswith("== ")]
        assert header_lines == [f"== {local.resolve()} =="], (
            f"overlapping roots must contribute the same repo only once; got: {header_lines!r}"
        )
        remaining = subprocess.run(
            ["git", "branch", "--list", "feat/dedup-once"],
            cwd=local, capture_output=True, text=True,
        ).stdout
        assert remaining.strip() == ""


class TestAllProjectsOneRepoFailureDoesNotStopSweep:
    """A repo whose cleanup body errors under `set -e` must not abort the
    sweep. A freshly `git init`'d repo with no commits reproduces this: its
    HEAD is an unborn symref, so the unguarded
    `CURRENT_HEAD=$(git rev-parse --abbrev-ref HEAD)` line fails outright —
    the healthy sibling repo in the same sweep must still be fully cleaned,
    and the overall exit code must reflect the one failure."""

    def test_broken_repo_does_not_block_healthy_sibling(self, tmp_path, fake_gh):
        root = tmp_path / "root"
        root.mkdir()

        healthy_dir = root / "healthy"
        healthy_dir.mkdir()
        local, bare = _make_repo_with_remote(healthy_dir)
        _make_feature_branch(local, "feat/healthy-in-sweep")
        subprocess.run(["git", "branch", "-D", "feat/healthy-in-sweep"], cwd=bare, check=True)

        broken_dir = root / "broken"
        broken_dir.mkdir()
        subprocess.run(["git", "init", "-q", "--initial-branch=main"], cwd=broken_dir, check=True)

        roots_file = tmp_path / "roots"
        roots_file.write_text(f"{root}\n")

        env = fake_gh({"feat/healthy-in-sweep": {"number": 703, "mergedAt": "2026-05-01"}})
        result = _run_all_projects(tmp_path, env, roots_file)

        assert result.returncode == 1, "one repo's failure must be reflected in the overall exit code"
        assert "cleanup failed" in result.stderr.lower()
        assert str(broken_dir.resolve()) in result.stderr
        assert "feat/healthy-in-sweep" in result.stdout
        remaining = subprocess.run(
            ["git", "branch", "--list", "feat/healthy-in-sweep"],
            cwd=local, capture_output=True, text=True,
        ).stdout
        assert remaining.strip() == ""


class TestAllProjectsTierBPromptReachesOperator:
    """The sweep loop's `<&0` stdin-reattachment (see the script's own
    comment above the sweep loop) exists to keep the Tier B [y/N] prompt
    interactive under a backgrounded per-repo subshell — proves it actually
    delivers keystrokes to the prompt, mirroring
    TestTierBReachableNoMergedPR's single-repo pty convention."""

    def test_tier_b_prompt_delivered_and_answered_under_sweep(self, tmp_path, fake_gh):
        root = tmp_path / "root"
        root.mkdir()
        local, remote = _make_repo_with_remote(root)
        _make_tier_b_branch(local, remote, "tier-b-branch")

        roots_file = tmp_path / "roots"
        roots_file.write_text(f"{root}\n")

        env = {**fake_gh({}), "CLEANUP_MERGED_BRANCHES_ROOTS_FILE": str(roots_file)}

        master_fd, slave_fd = pty.openpty()
        try:
            proc = subprocess.Popen(
                [str(_SCRIPT), "--all-projects"], cwd=str(tmp_path),
                env=env,
                stdin=slave_fd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            os.close(slave_fd)
            os.write(master_fd, b"y\n")
            proc.wait(timeout=30)
            os.close(master_fd)
        except Exception:
            with contextlib.suppress(OSError):
                os.close(master_fd)
            with contextlib.suppress(OSError):
                os.close(slave_fd)
            raise

        assert proc.returncode == 0
        stdout = proc.stdout.read().decode()
        assert "[y/N]" in stdout
        branches = subprocess.run(
            ["git", "branch"], cwd=local, capture_output=True, text=True
        ).stdout
        assert "tier-b-branch" not in branches


class TestAllProjectsNoReposDiscovered:
    """Zero repos found across every configured root is a no-op, not an
    error: exit 0 with an explanatory stderr note."""

    def test_no_repos_under_any_root_exits_zero(self, tmp_path, fake_gh):
        empty_root = tmp_path / "empty-root"
        empty_root.mkdir()
        roots_file = tmp_path / "roots"
        roots_file.write_text(f"{empty_root}\n")

        env = fake_gh({})
        result = _run_all_projects(tmp_path, env, roots_file)

        assert result.returncode == 0
        assert "no git repos found" in result.stderr.lower()
