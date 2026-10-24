"""Unit tests for _lib.sh's _lib_worktree_lock_absent and _lib_first_live_linked_worktree."""
from __future__ import annotations

import os
import shlex
import subprocess
import textwrap
from pathlib import Path

import pytest
from helpers import init_git_repo_with_commit

from ._lib_sh_helpers import _LIB_SH, _run_lib_call
from .conftest import (
    REVIEW_WORKTREE_NAME,
    _add_worktree_under_worktrees_dir,
    _worktree_lock_reason,
)


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
def test_lib_worktree_lock_absent_reports_absent_under_eacces(tmp_path: Path) -> None:
    """`[ -e ... ]` can't distinguish "doesn't exist" from "exists but
    unreadable". chmod 000 on the git-dir denies stat(2) on the lock file
    inside it with EACCES. Empirically `[ -e ]` resolves that denial to
    false, so _lib_worktree_lock_absent reports the lock "absent" (returns
    0/true) even though it is actually present underneath. Pins this
    resolution so a future change to the pre-check's implementation can't
    silently flip it."""
    git_dir = tmp_path / "git-dir"
    git_dir.mkdir()
    (git_dir / "locked").write_text("claude-code pid 1\n")
    git_dir.chmod(0o000)
    try:
        result = _run_lib_call(f'_lib_worktree_lock_absent "{git_dir}"', env=dict(os.environ))
    finally:
        git_dir.chmod(0o755)
    assert result.returncode == 0, (
        f"expected _lib_worktree_lock_absent to report 'absent' (exit 0) under EACCES: {result!r}"
    )


def test_lib_worktree_lock_absent_stale_read_survives_opposite_race(
    isolated_home: Path, opted_in_with_worktree: tuple[Path, Path]
) -> None:
    """Deterministic version of the "opposite-direction race" the header
    comments document:

    1. The pre-check reads "locked" (WAS_UNLOCKED=false).
    2. The lock is then cleared, standing in for a genuinely concurrent
       `git worktree unlock`.
    3. The guard performs a real fresh acquisition.

    WAS_UNLOCKED stays false, since the pre-check's stale read is never
    corrected retroactively. So a caller wired this way emits no
    fresh-lock message despite the real acquisition that just happened."""
    opted_in_repo, worktree = opted_in_with_worktree
    subprocess.run(
        ["git", "-C", str(worktree), "worktree", "lock", str(worktree), "--reason", "pre-existing"],
        check=True,
    )
    common_dir = subprocess.run(
        ["git", "-C", str(opted_in_repo), "rev-parse", "--path-format=absolute", "--git-common-dir"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    git_dir = subprocess.run(
        ["git", "-C", str(worktree), "rev-parse", "--path-format=absolute", "--git-dir"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    script = textwrap.dedent(f"""
        . "{_LIB_SH}"
        WAS_UNLOCKED=false
        _lib_worktree_lock_absent "{git_dir}" && WAS_UNLOCKED=true
        git -C "{worktree}" worktree unlock "{worktree}"
        _lib_worktree_collision_guard "{worktree}" "{common_dir}" >/dev/null
        printf '%s' "$WAS_UNLOCKED"
    """)
    env = {**os.environ, "HOME": str(isolated_home)}
    env.pop("CLAUDE_CONFIG_DIR", None)
    result = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, env=env, check=False
    )
    assert result.returncode == 0, repr(result)
    assert result.stdout == "false", (
        f"pre-check's stale WAS_UNLOCKED read must not be corrected by the "
        f"guard's later real acquisition: {result!r}"
    )
    assert _worktree_lock_reason(worktree) is not None, (
        "the guard's own call is expected to have genuinely re-locked the worktree"
    )


# --- _lib_first_live_linked_worktree --------------------------------------


def _first_live_linked_worktree(
    repo_root: Path, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", f'. {_LIB_SH}; _lib_first_live_linked_worktree "$1"', "bash", str(repo_root)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def test_first_live_linked_worktree_finds_a_present_worktree(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    init_git_repo_with_commit(repo)
    wt = tmp_path / "wt"
    subprocess.run(
        ["git", "worktree", "add", "-b", "feature", str(wt)], cwd=repo, check=True, capture_output=True
    )

    result = _first_live_linked_worktree(repo)

    assert result.returncode == 0
    assert result.stdout == str(wt)


def test_first_live_linked_worktree_ignores_a_stale_unpruned_entry(tmp_path: Path) -> None:
    """`git worktree list` still reports an entry whose directory was deleted
    without `git worktree prune` or `git worktree remove`. That stale entry
    must not count as a live worktree."""
    repo = tmp_path / "repo"
    init_git_repo_with_commit(repo)
    wt = tmp_path / "wt"
    subprocess.run(
        ["git", "worktree", "add", "-b", "feature", str(wt)], cwd=repo, check=True, capture_output=True
    )
    subprocess.run(["rm", "-rf", str(wt)], check=True)

    result = _first_live_linked_worktree(repo)

    assert result.returncode != 0
    assert result.stdout == ""


def test_first_live_linked_worktree_returns_not_found_with_no_worktree_at_all(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    init_git_repo_with_commit(repo)

    result = _first_live_linked_worktree(repo)

    assert result.returncode != 0
    assert result.stdout == ""


def _add_review_worktree(repo: Path, name: str) -> Path:
    return _add_worktree_under_worktrees_dir(repo, name)


def test_first_live_linked_worktree_skips_a_review_worktree(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    init_git_repo_with_commit(repo)
    _add_review_worktree(repo, REVIEW_WORKTREE_NAME)

    result = _first_live_linked_worktree(repo)

    assert result.returncode != 0
    assert result.stdout == ""


def _env_with_git_shim_printing_worktree_list(tmp_path: Path, porcelain_text: str) -> dict[str, str]:
    """An environment whose `git worktree list --porcelain` prints
    `porcelain_text` verbatim, so a test controls record order and the missing
    trailing blank line that real git always emits."""
    porcelain_file = tmp_path / "porcelain.txt"
    porcelain_file.write_text(porcelain_text)
    shim_dir = tmp_path / "bin"
    shim_dir.mkdir()
    git_shim = shim_dir / "git"
    git_shim.write_text(
        "#!/usr/bin/env bash\n"
        'case " $* " in\n'
        "  *' worktree list --porcelain '*)\n"
        f"    cat {shlex.quote(str(porcelain_file))}\n"
        "    exit 0\n"
        "    ;;\n"
        "esac\n"
        'echo "unexpected git call: $*" >&2\n'
        "exit 1\n"
    )
    git_shim.chmod(0o755)
    return {**os.environ, "PATH": f"{shim_dir}{os.pathsep}{os.environ['PATH']}"}


@pytest.mark.parametrize("review_entry_first", [True, False], ids=["review-first", "branch-first"])
def test_first_live_linked_worktree_returns_the_branch_worktree_in_either_listing_order(
    tmp_path: Path, review_entry_first: bool
) -> None:
    """`git worktree list` does not promise an order, so a skipped review entry
    must not end the scan whichever side of the branch worktree it lands on.
    A `git` shim prints the porcelain text in a fixed order over real
    directories. The last record has no trailing blank line, which pins the
    end-of-input flush."""
    repo = tmp_path / "repo"
    worktrees_dir = repo / ".claude" / "worktrees"
    review_worktree = worktrees_dir / REVIEW_WORKTREE_NAME
    branch_worktree = worktrees_dir / "feature"
    review_worktree.mkdir(parents=True)
    branch_worktree.mkdir()
    main_record = f"worktree {repo}\nbranch refs/heads/main\n"
    review_record = f"worktree {review_worktree}\ndetached\n"
    branch_record = f"worktree {branch_worktree}\nbranch refs/heads/feature\n"
    linked_records = [review_record, branch_record] if review_entry_first else [branch_record, review_record]

    result = _first_live_linked_worktree(
        repo, env=_env_with_git_shim_printing_worktree_list(tmp_path, "\n".join([main_record, *linked_records]))
    )

    assert result.returncode == 0
    assert result.stdout == str(branch_worktree)
    assert result.stderr == ""


def test_first_live_linked_worktree_returns_the_whole_path_of_a_worktree_whose_directories_contain_a_space(
    tmp_path: Path,
) -> None:
    """The porcelain record is one line, `worktree <path>`, so a path with a
    space must come back whole rather than cut at the first space."""
    repo = tmp_path / "my repo"
    branch_worktree = repo / ".claude" / "worktrees" / "my feature"
    branch_worktree.mkdir(parents=True)
    main_record = f"worktree {repo}\nbranch refs/heads/main\n"
    branch_record = f"worktree {branch_worktree}\nbranch refs/heads/feature\n"

    result = _first_live_linked_worktree(
        repo, env=_env_with_git_shim_printing_worktree_list(tmp_path, "\n".join([main_record, branch_record]))
    )

    assert result.returncode == 0
    assert result.stdout == str(branch_worktree)


def test_first_live_linked_worktree_skips_a_review_record_under_a_repo_path_containing_a_space_but_keeps_the_branch_record(
    tmp_path: Path,
) -> None:
    """The review-shape comparison matches the entry's path against
    `<repo>/.claude/worktrees`, so a space in the repo path must not defeat the
    skip. The review record alone is not-found; a branch record after it is
    returned whole."""
    repo = tmp_path / "my repo"
    worktrees_dir = repo / ".claude" / "worktrees"
    review_worktree = worktrees_dir / REVIEW_WORKTREE_NAME
    branch_worktree = worktrees_dir / "feature"
    review_worktree.mkdir(parents=True)
    branch_worktree.mkdir()
    main_record = f"worktree {repo}\nbranch refs/heads/main\n"
    review_record = f"worktree {review_worktree}\ndetached\n"
    branch_record = f"worktree {branch_worktree}\nbranch refs/heads/feature\n"

    # The shim helper owns a fixed layout under its directory, so each listing gets its own.
    review_alone_dir, review_then_branch_dir = tmp_path / "review-alone", tmp_path / "review-then-branch"
    review_alone_dir.mkdir()
    review_then_branch_dir.mkdir()
    review_alone = _first_live_linked_worktree(
        repo,
        env=_env_with_git_shim_printing_worktree_list(review_alone_dir, "\n".join([main_record, review_record])),
    )
    review_then_branch = _first_live_linked_worktree(
        repo,
        env=_env_with_git_shim_printing_worktree_list(
            review_then_branch_dir, "\n".join([main_record, review_record, branch_record])
        ),
    )

    assert review_alone.returncode != 0
    assert review_alone.stdout == ""
    assert review_then_branch.returncode == 0
    assert review_then_branch.stdout == str(branch_worktree)


def test_first_live_linked_worktree_does_not_carry_a_detached_record_over_to_the_next_record(
    tmp_path: Path,
) -> None:
    """A detached main checkout, common in CI, must not mark the record after
    it detached: a review-shaped worktree on a branch still counts."""
    repo = tmp_path / "repo"
    review_named_branch_worktree = repo / ".claude" / "worktrees" / REVIEW_WORKTREE_NAME
    review_named_branch_worktree.mkdir(parents=True)
    detached_main_record = f"worktree {repo}\ndetached\n"
    branch_record = f"worktree {review_named_branch_worktree}\nbranch refs/heads/feature\n"

    result = _first_live_linked_worktree(
        repo, env=_env_with_git_shim_printing_worktree_list(tmp_path, "\n".join([detached_main_record, branch_record]))
    )

    assert result.returncode == 0
    assert result.stdout == str(review_named_branch_worktree)


def test_first_live_linked_worktree_counts_a_review_named_directory_outside_the_worktrees_dir(
    tmp_path: Path,
) -> None:
    """Only a directory directly under .claude/worktrees is a review worktree,
    so a detached worktree elsewhere that merely shares the name stays live."""
    repo = tmp_path / "repo"
    init_git_repo_with_commit(repo)
    lookalike = tmp_path / REVIEW_WORKTREE_NAME
    subprocess.run(
        ["git", "worktree", "add", "--detach", str(lookalike)],
        cwd=repo, check=True, capture_output=True,
    )

    result = _first_live_linked_worktree(repo)

    assert result.returncode == 0
    assert result.stdout == str(lookalike)


def test_first_live_linked_worktree_returns_a_branch_worktree_directly_under_the_worktrees_dir(
    tmp_path: Path,
) -> None:
    """The repo's own layout is .claude/worktrees/<branch>; that worktree is
    the one a main-tree session should be told to enter."""
    repo = tmp_path / "repo"
    init_git_repo_with_commit(repo)
    branch_worktree = _add_worktree_under_worktrees_dir(repo, "feature", branch="feature")

    result = _first_live_linked_worktree(repo)

    assert result.returncode == 0
    assert result.stdout == str(branch_worktree)


def test_first_live_linked_worktree_skips_a_review_sibling_but_returns_the_branch_worktree(
    tmp_path: Path,
) -> None:
    """Real git porcelain output; the listing order is not pinned here (the
    shim test above pins both orders)."""
    repo = tmp_path / "repo"
    init_git_repo_with_commit(repo)
    _add_review_worktree(repo, REVIEW_WORKTREE_NAME)
    branch_worktree = _add_worktree_under_worktrees_dir(repo, "feature", branch="feature")

    result = _first_live_linked_worktree(repo)

    assert result.returncode == 0
    assert result.stdout == str(branch_worktree)


def test_first_live_linked_worktree_counts_a_user_named_review_prefixed_detached_worktree(
    tmp_path: Path,
) -> None:
    """A name that only starts with review-pr- is not the shape
    review-pr-checkout.sh creates, so it stays live even when detached."""
    repo = tmp_path / "repo"
    init_git_repo_with_commit(repo)
    user_worktree = _add_review_worktree(repo, "review-pr-hardening")

    result = _first_live_linked_worktree(repo)

    assert result.returncode == 0
    assert result.stdout == str(user_worktree)


def test_first_live_linked_worktree_counts_a_review_named_directory_nested_one_level_deeper(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    init_git_repo_with_commit(repo)
    nested = _add_review_worktree(repo, f"nested/{REVIEW_WORKTREE_NAME}")

    result = _first_live_linked_worktree(repo)

    assert result.returncode == 0
    assert result.stdout == str(nested)


def test_first_live_linked_worktree_counts_a_review_shaped_worktree_that_is_on_a_branch(
    tmp_path: Path,
) -> None:
    """Review checkouts are always detached, so a branch worktree with the
    review-shaped name is someone's real work tree."""
    repo = tmp_path / "repo"
    init_git_repo_with_commit(repo)
    branch_worktree = _add_worktree_under_worktrees_dir(
        repo, REVIEW_WORKTREE_NAME, branch="review-branch"
    )

    result = _first_live_linked_worktree(repo)

    assert result.returncode == 0
    assert result.stdout == str(branch_worktree)


def test_first_live_linked_worktree_counts_a_review_shaped_worktree_under_another_trees_worktrees_dir(
    tmp_path: Path,
) -> None:
    """Only the repo's own .claude/worktrees directory qualifies, not any
    directory with that tail."""
    repo = tmp_path / "repo"
    init_git_repo_with_commit(repo)
    elsewhere = _add_worktree_under_worktrees_dir(
        repo, REVIEW_WORKTREE_NAME, worktrees_root=tmp_path / "other-tree"
    )

    result = _first_live_linked_worktree(repo)

    assert result.returncode == 0
    assert result.stdout == str(elsewhere)
