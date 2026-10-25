"""Unit tests for _lib.sh's _lib_default_branch_from_origin_head, _lib_default_branch_or_guess,
_lib_verification_cache_sentinel_present and _lib_head_tree_hash.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from helpers import (
    assert_cap_engaged,
    init_git_repo_with_commit,
    scaled_shim_sleep,
    write_scaled_timeout_shim,
)

from ._lib_sh_helpers import _LIB_SH

# --- _lib_default_branch_from_origin_head / _lib_default_branch_or_guess --
#
# guard-settings-session-keys.sh calls _lib_default_branch_or_guess to pick
# its git-show comparison branch. require-ready-for-review.sh resolves its
# own default branch the same way, inline, to decide its default-branch push
# bypass.


def _resolve_default_branch(
    repo_root: Path, func: str = "_lib_default_branch_or_guess"
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", f'. {_LIB_SH}; {func} "$1"', "bash", str(repo_root)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_resolve_default_branch_via_symbolic_ref_for_non_main_name(tmp_path: Path) -> None:
    """A repo whose default branch is neither main/master/develop still
    resolves correctly via the direct origin/HEAD symbolic ref, without
    ever reaching the candidate-probe fallback."""
    repo = tmp_path / "repo"
    init_git_repo_with_commit(repo, branch="trunk")
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/trunk", "HEAD"], cwd=repo, check=True
    )
    subprocess.run(
        ["git", "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk"],
        cwd=repo, check=True,
    )

    result = _resolve_default_branch(repo)

    assert result.returncode == 0
    assert result.stdout == "trunk"


def test_resolve_default_branch_falls_back_to_candidate_probe_for_develop(
    tmp_path: Path,
) -> None:
    """No origin/HEAD symbolic ref configured (common for a hand-built or
    shallow repo) — falls back to probing the conventional candidate names,
    landing on the first one (main, master, develop) with a matching
    origin/<candidate> ref."""
    repo = tmp_path / "repo"
    init_git_repo_with_commit(repo, branch="develop")
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/develop", "HEAD"], cwd=repo, check=True
    )

    result = _resolve_default_branch(repo)

    assert result.returncode == 0
    assert result.stdout == "develop"


def test_resolve_default_branch_candidate_probe_reads_remote_ref_not_local_branch(
    tmp_path: Path,
) -> None:
    """Local checked-out branch is `feature`, not one of the probed
    candidates, and origin/develop is the only live candidate ref (no
    origin/HEAD) -- the candidate probe must still return develop by
    reading the remote-tracking ref, not by reporting whatever branch
    happens to be checked out locally."""
    repo = tmp_path / "repo"
    init_git_repo_with_commit(repo, branch="feature")
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/develop", "HEAD"], cwd=repo, check=True
    )

    result = _resolve_default_branch(repo)

    assert result.returncode == 0
    assert result.stdout == "develop"


def test_resolve_default_branch_empty_when_unresolvable(tmp_path: Path) -> None:
    """No origin/HEAD symbolic ref and no origin/{main,master,develop} ref at
    all (e.g. a repo with no configured remote) — the helper reports "could
    not resolve" as empty stdout and a non-zero exit rather than guessing."""
    repo = tmp_path / "repo"
    init_git_repo_with_commit(repo, branch="main")

    result = _resolve_default_branch(repo)

    assert result.returncode != 0
    assert result.stdout == ""


def test_resolve_default_branch_empty_when_candidate_probe_finds_no_match(
    tmp_path: Path,
) -> None:
    """origin/trunk exists, but no origin/HEAD symbolic ref and none of the
    probed candidates (main, master, develop) do -- the candidate loop must
    report unresolvable, with a non-zero exit, rather than matching trunk by
    some other means."""
    repo = tmp_path / "repo"
    init_git_repo_with_commit(repo, branch="trunk")
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/trunk", "HEAD"], cwd=repo, check=True
    )

    result = _resolve_default_branch(repo)

    assert result.returncode != 0
    assert result.stdout == ""


def test_resolve_default_branch_symbolic_ref_preserves_slash_in_branch_name(
    tmp_path: Path,
) -> None:
    """A default branch name containing "/" (e.g. release/v2) must come back
    unmangled -- the "refs/remotes/origin/" strip is a literal anchored
    prefix strip, not a strip of every slash in the string."""
    repo = tmp_path / "repo"
    init_git_repo_with_commit(repo, branch="release/v2")
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/release/v2", "HEAD"], cwd=repo, check=True
    )
    subprocess.run(
        ["git", "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/release/v2"],
        cwd=repo, check=True,
    )

    result = _resolve_default_branch(repo)

    assert result.returncode == 0
    assert result.stdout == "release/v2"


def test_resolve_default_branch_candidate_probe_prefers_earlier_candidate(
    tmp_path: Path,
) -> None:
    """Both origin/develop and origin/master exist (no origin/HEAD, no
    origin/main) — the candidate loop returns master, the earlier-listed
    candidate in the main/master/develop order, not develop."""
    repo = tmp_path / "repo"
    init_git_repo_with_commit(repo, branch="master")
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/master", "HEAD"], cwd=repo, check=True
    )
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/develop", "HEAD"], cwd=repo, check=True
    )

    result = _resolve_default_branch(repo)

    assert result.returncode == 0
    assert result.stdout == "master"


def test_resolve_default_branch_candidate_probe_prefers_main_over_master(
    tmp_path: Path,
) -> None:
    """Both origin/main and origin/master exist (no origin/HEAD) -- proves
    the full candidate order (main, then master, then develop), not just
    the master-over-develop pair
    test_resolve_default_branch_candidate_probe_prefers_earlier_candidate
    covers."""
    repo = tmp_path / "repo"
    init_git_repo_with_commit(repo, branch="main")
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/main", "HEAD"], cwd=repo, check=True
    )
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/master", "HEAD"], cwd=repo, check=True
    )

    result = _resolve_default_branch(repo)

    assert result.returncode == 0
    assert result.stdout == "main"


def test_resolve_default_branch_symbolic_ref_with_dangling_target_is_rejected(
    tmp_path: Path,
) -> None:
    """origin/HEAD points at origin/main via a symbolic ref, but
    origin/main itself was never created. The symbolic-ref path verifies
    the target resolves to a commit before trusting it, so a dangling
    target reports unresolvable, with a non-zero exit, exactly like any
    other unverified origin/HEAD (docs/design-decisions.md #54)."""
    repo = tmp_path / "repo"
    init_git_repo_with_commit(repo, branch="main")
    subprocess.run(
        ["git", "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main"],
        cwd=repo, check=True,
    )

    result = _resolve_default_branch(repo)

    assert result.returncode != 0
    assert result.stdout == ""


def test_default_branch_from_origin_head_resolves_verified_target_in_isolation(
    tmp_path: Path,
) -> None:
    """_lib_default_branch_from_origin_head in isolation (not through the
    guessing layer): a valid origin/HEAD symbolic ref pointing at a verified
    target resolves, with no candidate-probe fallback involved at all."""
    repo = tmp_path / "repo"
    init_git_repo_with_commit(repo, branch="trunk")
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/trunk", "HEAD"], cwd=repo, check=True
    )
    subprocess.run(
        ["git", "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk"],
        cwd=repo, check=True,
    )

    result = _resolve_default_branch(repo, func="_lib_default_branch_from_origin_head")

    assert result.returncode == 0
    assert result.stdout == "trunk"


def test_default_branch_from_origin_head_rejects_dangling_target_in_isolation(
    tmp_path: Path,
) -> None:
    """_lib_default_branch_from_origin_head in isolation (not through the
    guessing layer): origin/HEAD points at refs/remotes/origin/main, but
    that target was never created (dangling), and no candidate ref exists
    either. Proves the narrow layer's own target-verification failure
    directly, rather than inferring it through _lib_default_branch_or_guess's
    separate candidate-loop failure, as
    test_resolve_default_branch_symbolic_ref_with_dangling_target_is_rejected
    does."""
    repo = tmp_path / "repo"
    init_git_repo_with_commit(repo, branch="main")
    subprocess.run(
        ["git", "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main"],
        cwd=repo, check=True,
    )

    result = _resolve_default_branch(repo, func="_lib_default_branch_from_origin_head")

    assert result.returncode != 0
    assert result.stdout == ""


def test_default_branch_or_guess_falls_through_on_dangling_origin_head_to_live_develop(
    tmp_path: Path,
) -> None:
    """origin/HEAD points at origin/main via a symbolic ref, but origin/main
    was never created (dangling) -- so the narrow layer fails -- while
    origin/develop is a real, resolvable ref. Proves
    _lib_default_branch_or_guess falls through to the candidate probe on the
    narrow layer's failure rather than returning empty outright."""
    repo = tmp_path / "repo"
    init_git_repo_with_commit(repo, branch="develop")
    subprocess.run(
        ["git", "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main"],
        cwd=repo, check=True,
    )
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/develop", "HEAD"], cwd=repo, check=True
    )

    result = _resolve_default_branch(repo)

    assert result.returncode == 0
    assert result.stdout == "develop"


# --- _lib_verification_cache_sentinel_present -------------------------------
#
# Pure predicate over local refs, unlike the hash-computing helpers above --
# tested directly at this layer rather than only through marker.sh's `check
# verification` arm.


def _sentinel_present(repo_root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", f'. {_LIB_SH}; _lib_verification_cache_sentinel_present "$1"',
         "bash", str(repo_root)],
        capture_output=True,
        text=True,
        check=False,
    )


def _commit_sentinel_at_ref(repo: Path, ref: str) -> None:
    """Creates a lone-file commit holding the verification-cache opt-in
    sentinel and points REF at it, via a throwaway index -- never touches
    the repo's own checked-out branch, working tree, or real index."""
    tmp_index = repo / ".git" / "sentinel-index"
    index_env = {**os.environ, "GIT_INDEX_FILE": str(tmp_index)}
    blob_oid = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"],
        cwd=repo, input="# sentinel\n", capture_output=True, text=True, check=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "update-index", "--add", "--cacheinfo",
         f"100644,{blob_oid},.claude/ready-for-review-verification-cache-optin"],
        cwd=repo, env=index_env, check=True,
    )
    tree_oid = subprocess.run(
        ["git", "write-tree"], cwd=repo, env=index_env,
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    tmp_index.unlink()
    commit_env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t.com",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t.com",
    }
    commit_oid = subprocess.run(
        ["git", "commit-tree", tree_oid, "-m", "sentinel"],
        cwd=repo, env=commit_env, capture_output=True, text=True, check=True,
    ).stdout.strip()
    subprocess.run(["git", "update-ref", ref, commit_oid], cwd=repo, check=True)


def test_verification_cache_sentinel_present_on_origin_default(tmp_path: Path) -> None:
    """The positive case the opt-in gate depends on: the sentinel committed
    at origin/<default-branch> reads present."""
    repo = tmp_path / "repo"
    init_git_repo_with_commit(repo, branch="main")
    _commit_sentinel_at_ref(repo, "refs/remotes/origin/main")
    subprocess.run(
        ["git", "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main"],
        cwd=repo, check=True,
    )

    result = _sentinel_present(repo)

    assert result.returncode == 0


def test_verification_cache_sentinel_absent_from_origin_default(tmp_path: Path) -> None:
    """origin/<default-branch> exists and resolves, but the sentinel was
    never committed to it -- the default-off gate's ordinary state."""
    repo = tmp_path / "repo"
    init_git_repo_with_commit(repo, branch="main")
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/main", "HEAD"], cwd=repo, check=True
    )
    subprocess.run(
        ["git", "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main"],
        cwd=repo, check=True,
    )

    result = _sentinel_present(repo)

    assert result.returncode == 1


def test_verification_cache_sentinel_absent_when_origin_head_unset(tmp_path: Path) -> None:
    """No origin/HEAD symbolic ref at all, even though the sentinel is
    committed somewhere reachable -- fails closed through
    _lib_default_branch_from_origin_head's own two-outcome contract rather
    than falling back to guessing a branch name."""
    repo = tmp_path / "repo"
    init_git_repo_with_commit(repo, branch="main")
    _commit_sentinel_at_ref(repo, "refs/remotes/origin/main")

    result = _sentinel_present(repo)

    assert result.returncode == 1


def test_verification_cache_sentinel_present_only_on_non_default_branch_reads_absent(
    tmp_path: Path,
) -> None:
    """The sentinel is committed to origin/other, but origin/HEAD points at
    origin/main, which lacks it -- presence on a non-default branch must not
    count as opted in."""
    repo = tmp_path / "repo"
    init_git_repo_with_commit(repo, branch="main")
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/main", "HEAD"], cwd=repo, check=True
    )
    subprocess.run(
        ["git", "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main"],
        cwd=repo, check=True,
    )
    _commit_sentinel_at_ref(repo, "refs/remotes/origin/other")

    result = _sentinel_present(repo)

    assert result.returncode == 1


def _commit_sentinel_tree_at_ref(repo: Path, ref: str) -> None:
    """Same throwaway-index technique as _commit_sentinel_at_ref, but nests a
    file one level under the sentinel path so the path itself resolves to a
    git tree (directory) rather than a blob."""
    tmp_index = repo / ".git" / "sentinel-tree-index"
    index_env = {**os.environ, "GIT_INDEX_FILE": str(tmp_index)}
    blob_oid = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"],
        cwd=repo, input="# nested\n", capture_output=True, text=True, check=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "update-index", "--add", "--cacheinfo",
         "100644,"
         f"{blob_oid},"
         ".claude/ready-for-review-verification-cache-optin/nested-file"],
        cwd=repo, env=index_env, check=True,
    )
    tree_oid = subprocess.run(
        ["git", "write-tree"], cwd=repo, env=index_env,
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    tmp_index.unlink()
    commit_env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t.com",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t.com",
    }
    commit_oid = subprocess.run(
        ["git", "commit-tree", tree_oid, "-m", "sentinel tree"],
        cwd=repo, env=commit_env, capture_output=True, text=True, check=True,
    ).stdout.strip()
    subprocess.run(["git", "update-ref", ref, commit_oid], cwd=repo, check=True)


def test_verification_cache_sentinel_present_when_path_is_a_tree(tmp_path: Path) -> None:
    """Pins the `cat-file -e` design choice documented in _lib.sh: it confirms
    an object exists at the sentinel path but not its type, so a tree
    (directory) committed there reads as present identically to a blob."""
    repo = tmp_path / "repo"
    init_git_repo_with_commit(repo, branch="main")
    _commit_sentinel_tree_at_ref(repo, "refs/remotes/origin/main")
    subprocess.run(
        ["git", "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main"],
        cwd=repo, check=True,
    )

    result = _sentinel_present(repo)

    assert result.returncode == 0


# --- _lib_head_tree_hash -----------------------------------------------
#
# Sibling of _lib_verification_cache_sentinel_present above: also read
# directly at this layer, since marker.sh's write/check verification arms
# only exercise it as one step inside a larger recipe.


def _head_tree_hash_result(
    repo_root: Path, cap_mode: str = "capped"
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", f'. {_LIB_SH}; _lib_head_tree_hash "$1" "$2"',
         "bash", cap_mode, str(repo_root)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_head_tree_hash_capped_matches_git_rev_parse(tmp_path: Path) -> None:
    """The capped path's stdout is exactly `git rev-parse HEAD^{tree}` --
    write and check's shared recipe depends on this matching bit-for-bit."""
    repo = tmp_path / "repo"
    init_git_repo_with_commit(repo)
    expected = subprocess.run(
        ["git", "rev-parse", "HEAD^{tree}"], cwd=repo,
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    result = _head_tree_hash_result(repo, "capped")

    assert result.returncode == 0
    assert result.stdout == expected


def test_head_tree_hash_uncapped_matches_git_rev_parse(tmp_path: Path) -> None:
    """Same recipe as the capped case, run through the uncapped branch --
    both cap_mode arguments must compute the identical hash."""
    repo = tmp_path / "repo"
    init_git_repo_with_commit(repo)
    expected = subprocess.run(
        ["git", "rev-parse", "HEAD^{tree}"], cwd=repo,
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    result = _head_tree_hash_result(repo, "uncapped")

    assert result.returncode == 0
    assert result.stdout == expected


def test_head_tree_hash_absent_on_commit_less_repo(tmp_path: Path) -> None:
    """No HEAD to resolve. `git rev-parse HEAD^{tree}` exits 128 but still
    echoes the literal string "HEAD^{tree}", so the two-outcome contract
    must check git's own exit status rather than stdout emptiness."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)

    result = _head_tree_hash_result(repo, "capped")

    assert result.returncode == 1
    assert result.stdout == ""


def test_head_tree_hash_invalid_cap_mode_exits_2_with_message(tmp_path: Path) -> None:
    """An unrecognized cap_mode argument is a caller bug, not a git failure --
    it exits 2 (distinct from the 1 a resolvable-but-absent HEAD returns) and
    names the bad value in its stderr message."""
    repo = tmp_path / "repo"
    init_git_repo_with_commit(repo)

    result = _head_tree_hash_result(repo, "sideways")

    assert result.returncode == 2
    assert (
        "_lib_head_tree_hash: invalid cap_mode sideways (want capped or uncapped)"
        in result.stderr
    )


@pytest.mark.timing
def test_head_tree_hash_capped_timeout_returns_absent_not_a_hang(tmp_path: Path) -> None:
    """A stalled `git rev-parse HEAD^{tree}` must not hang the caller past
    the 5s _lib_capped cap, and a killed call must fall through to the same
    absent outcome a commit-less repo gets, never a false hash."""
    repo = tmp_path / "repo"
    init_git_repo_with_commit(repo)
    real_git = shutil.which("git")
    if not real_git:
        pytest.skip("git not found in PATH")
    if not shutil.which("timeout") and not shutil.which("gtimeout"):
        pytest.skip("neither timeout(1) nor gtimeout(1) available — BSD/macOS without coreutils")

    stub_dir = tmp_path / "stub-bin"
    stub_dir.mkdir()
    write_scaled_timeout_shim(stub_dir)
    stub_git = stub_dir / "git"
    stub_git.write_text(
        '#!/bin/bash\n'
        'if [ "$1" = "-C" ] && [ "$3" = "rev-parse" ] && [ "$4" = "HEAD^{tree}" ] && [ "$#" -eq 4 ]; then\n'
        f'  sleep {scaled_shim_sleep(10)}\n'
        'fi\n'
        f'exec {real_git} "$@"\n'
    )
    stub_git.chmod(0o755)

    with assert_cap_engaged(stub_dir, production_cap=5):
        result = subprocess.run(
            ["bash", "-c", f'. {_LIB_SH}; _lib_head_tree_hash "$1" "$2"',
             "bash", "capped", str(repo)],
            capture_output=True,
            text=True,
            env={**os.environ, "PATH": f"{stub_dir}:{os.environ['PATH']}"},
            check=False,
        )

    assert result.returncode == 1
    assert result.stdout == ""
