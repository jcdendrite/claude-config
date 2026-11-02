"""Unit tests for _lib.sh's _lib_trial_merge_verdict."""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest
from helpers import (
    _make_git_exiting_with_status,
    _run_git,
    init_git_repo_with_commit,
)

from ._lib_sh_helpers import (
    _LIB_SH,
)

# --- _lib_trial_merge_verdict -------------------------------------------------

# The two `set` modes the helper's callers run under: the divergence script
# (-e) and the SessionStart hook (no -e).
_TRIAL_MERGE_CALLER_SHELL_MODES = ["set -euo pipefail", "set -uo pipefail"]

_TRIAL_MERGE_TREE_OID_SHA1 = "a" * 40
_TRIAL_MERGE_TREE_OID_SHA256 = "b" * 64


def _trial_merge_verdict(
    repo: Path, upstream_ref: str, shell_mode: str, env: dict | None = None
) -> subprocess.CompletedProcess:
    """Run _lib_trial_merge_verdict inside an `if`, the way both callers do, so
    the helper's own status reaches the test unmasked by `set -e`."""
    harness = (
        f'{shell_mode}; . {_LIB_SH}; '
        'if _lib_trial_merge_verdict "$1"; then exit 0; else exit "$?"; fi'
    )
    return subprocess.run(
        ["bash", "-c", harness, "bash", upstream_ref],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        env=env if env is not None else dict(os.environ),
    )


def _commit_files(repo: Path, files: dict[str, str], message: str) -> None:
    for relative_path, content in files.items():
        file_path = repo / relative_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content)
        _run_git(repo, "add", "--", relative_path)
    _run_git(repo, "commit", "-qm", message)


def _repo_with_upstream_and_feature_edits(
    tmp_path: Path,
    base_files: dict[str, str],
    upstream_files: dict[str, str],
    feature_files: dict[str, str],
) -> Path:
    """A repo checked out on `feature`, with a diverged local branch `upstream`
    that both descend from one base commit holding base_files."""
    repo = tmp_path / "repo"
    init_git_repo_with_commit(repo, branch="main")
    _commit_files(repo, base_files, "base")
    _run_git(repo, "checkout", "-qb", "upstream")
    _commit_files(repo, upstream_files, "upstream edits")
    _run_git(repo, "checkout", "-qb", "feature", "main")
    _commit_files(repo, feature_files, "feature edits")
    return repo


def _make_git_printing_on_merge_tree(bin_dir: Path, stdout_text: str, exit_status: int) -> Path:
    """Shim at bin_dir/git: `merge-tree` prints stdout_text and exits
    exit_status; every other subcommand proxies to the real git. Callers must
    set REAL_GIT in the shim's environment."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    shim = bin_dir / "git"
    shim.write_text(
        '#!/bin/bash\n'
        'if [ "$1" = "merge-tree" ]; then\n'
        f'  printf "%s\\n" {shlex.quote(stdout_text)}\n'
        f'  exit {exit_status}\n'
        'fi\n'
        'exec "$REAL_GIT" "$@"\n'
    )
    shim.chmod(0o755)
    return shim


def _env_with_shim_first(bin_dir: Path) -> dict:
    return {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "REAL_GIT": shutil.which("git")}


@pytest.mark.parametrize("shell_mode", _TRIAL_MERGE_CALLER_SHELL_MODES)
class TestTrialMergeVerdict:
    """The helper's contract: exit 0 with a CLEAN or CONFLICT line, or exit 1
    with empty stdout and one stderr line when git merge-tree cannot complete."""

    def test_clean_merge_reports_clean(self, tmp_path: Path, shell_mode: str) -> None:
        repo = _repo_with_upstream_and_feature_edits(
            tmp_path, {"shared.txt": "base\n"}, {"upstream.txt": "u\n"}, {"feature.txt": "f\n"}
        )
        result = _trial_merge_verdict(repo, "upstream", shell_mode)
        assert result.returncode == 0
        assert result.stdout == "Trial merge: CLEAN\n"

    def test_one_file_conflict_names_the_file(self, tmp_path: Path, shell_mode: str) -> None:
        repo = _repo_with_upstream_and_feature_edits(
            tmp_path, {"shared.txt": "base\n"}, {"shared.txt": "upstream\n"}, {"shared.txt": "feature\n"}
        )
        result = _trial_merge_verdict(repo, "upstream", shell_mode)
        assert result.returncode == 0
        assert result.stdout == "Trial merge: CONFLICT in: shared.txt\n"

    def test_three_file_conflict_lists_paths_sorted_with_a_space_kept_whole(
        self, tmp_path: Path, shell_mode: str
    ) -> None:
        conflicted_paths = ["z.txt", "dir/b c.txt", "a.txt"]
        repo = _repo_with_upstream_and_feature_edits(
            tmp_path,
            {path: "base\n" for path in conflicted_paths},
            {path: "upstream\n" for path in conflicted_paths},
            {path: "feature\n" for path in conflicted_paths},
        )
        result = _trial_merge_verdict(repo, "upstream", shell_mode)
        assert result.returncode == 0
        assert result.stdout == "Trial merge: CONFLICT in: a.txt, dir/b c.txt, z.txt\n"

    def test_modify_delete_conflict_reports_the_path_when_stage_2_is_absent(
        self, tmp_path: Path, shell_mode: str
    ) -> None:
        """Upstream, the first argument of `git merge-tree --write-tree upstream HEAD`,
        deleting the file leaves stage lines 1 and 3 only, because stage numbers
        follow merge-tree's argument order. The path must still be reported,
        so the filter cannot key on a single stage number."""
        # The upstream.txt decoy only makes upstream's commit non-empty before the
        # delete. It must stay dissimilar to shared.txt, or rename detection
        # would pair the two and the merge would come out clean.
        repo = _repo_with_upstream_and_feature_edits(
            tmp_path, {"shared.txt": "base\n"}, {"upstream.txt": "u\n"}, {"shared.txt": "feature\n"}
        )
        _run_git(repo, "checkout", "-q", "upstream")
        _run_git(repo, "rm", "-q", "--", "shared.txt")
        _run_git(repo, "commit", "-qm", "upstream deletes shared.txt")
        _run_git(repo, "checkout", "-q", "feature")
        result = _trial_merge_verdict(repo, "upstream", shell_mode)
        assert result.returncode == 0
        assert result.stdout == "Trial merge: CONFLICT in: shared.txt\n"

    def test_add_add_conflict_reports_the_path_when_stage_1_is_absent(
        self, tmp_path: Path, shell_mode: str
    ) -> None:
        """A path added on both branches has no merge base, so stage 1 is absent
        and only stage lines 2 and 3 appear. The path must still be reported,
        so the filter cannot key on a single stage number."""
        repo = _repo_with_upstream_and_feature_edits(
            tmp_path, {"shared.txt": "base\n"}, {"added.txt": "upstream\n"}, {"added.txt": "feature\n"}
        )
        result = _trial_merge_verdict(repo, "upstream", shell_mode)
        assert result.returncode == 0
        assert result.stdout == "Trial merge: CONFLICT in: added.txt\n"

    def test_cleanly_merged_path_with_a_forged_stage_line_in_its_name_is_not_reported(
        self, tmp_path: Path, shell_mode: str
    ) -> None:
        """Without --no-messages, the messages section prints a path unquoted, so
        a newline in a cleanly merged path's name would forge a stage line there."""
        forged_stage_line = "100644 " + "a" * 40 + " 1\tFORGED"
        forged_stage_line_path = "notes\n" + forged_stage_line
        # Both branches edit the forged-name path in non-overlapping hunks, so git
        # content-merges it cleanly and prints an "Auto-merging" message for it.
        # a.txt conflicts on purpose, because merge-tree prints the messages section
        # by default only when the merge has conflicts.
        base_lines = [f"line {number}\n" for number in range(1, 6)]
        upstream_lines = ["line 1 upstream\n", *base_lines[1:]]
        feature_lines = [*base_lines[:4], "line 5 feature\n"]
        repo = _repo_with_upstream_and_feature_edits(
            tmp_path,
            {"a.txt": "base\n", forged_stage_line_path: "".join(base_lines)},
            {"a.txt": "upstream\n", forged_stage_line_path: "".join(upstream_lines)},
            {"a.txt": "feature\n", forged_stage_line_path: "".join(feature_lines)},
        )
        # The merge exits 1 on conflict, so _run_git (check=True) does not fit.
        messages_merge = subprocess.run(
            ["git", "merge-tree", "--write-tree", "upstream", "HEAD"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        )
        assert messages_merge.returncode == 1, (
            "the fixture must contain a real conflict, or merge-tree omits the messages section"
        )
        assert forged_stage_line in messages_merge.stdout.splitlines(), (
            "the forged line is missing from raw merge-tree output: either git changed how "
            "the messages section prints paths, or the fixture lost one of its preconditions, "
            "so this test would pass without --no-messages"
        )
        result = _trial_merge_verdict(repo, "upstream", shell_mode)
        assert result.returncode == 0
        assert result.stdout == "Trial merge: CONFLICT in: a.txt\n"

    def test_unknown_upstream_ref_fails_with_one_stderr_line(
        self, tmp_path: Path, shell_mode: str
    ) -> None:
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        result = _trial_merge_verdict(repo, "no-such-ref", shell_mode)
        assert result.returncode == 1
        assert result.stdout == ""
        assert len(result.stderr.splitlines()) == 1

    def test_unrelated_histories_fail_with_one_stderr_line(
        self, tmp_path: Path, shell_mode: str
    ) -> None:
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        _run_git(repo, "checkout", "-q", "--orphan", "orphan")
        _commit_files(repo, {"orphan.txt": "o\n"}, "orphan commit")
        result = _trial_merge_verdict(repo, "main", shell_mode)
        assert result.returncode == 1
        assert result.stdout == ""
        assert len(result.stderr.splitlines()) == 1

    @pytest.mark.parametrize("merge_tree_status", [1, 128])
    def test_merge_tree_exiting_with_no_output_fails_naming_status_git_and_readme(
        self, tmp_path: Path, shell_mode: str, merge_tree_status: int
    ) -> None:
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        bin_dir = tmp_path / "bin-merge-tree-exits"
        _make_git_exiting_with_status(bin_dir, "merge-tree", merge_tree_status)
        result = _trial_merge_verdict(repo, "HEAD", shell_mode, env=_env_with_shim_first(bin_dir))
        assert result.returncode == 1
        assert result.stdout == ""
        assert len(result.stderr.splitlines()) == 1
        assert f"exited {merge_tree_status}" in result.stderr
        assert "git version" in result.stderr
        assert "README.md" in result.stderr

    def test_exit_1_with_a_fatal_message_on_line_1_is_not_a_conflict(
        self, tmp_path: Path, shell_mode: str
    ) -> None:
        """A bad ref also makes merge-tree exit 1, so only a tree OID on line 1
        marks a conflict."""
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        bin_dir = tmp_path / "bin-merge-tree-fatal"
        _make_git_printing_on_merge_tree(bin_dir, "fatal: bad revision 'nope'", 1)
        result = _trial_merge_verdict(repo, "HEAD", shell_mode, env=_env_with_shim_first(bin_dir))
        assert result.returncode == 1
        assert result.stdout == ""

    @pytest.mark.parametrize(
        "tree_oid",
        [_TRIAL_MERGE_TREE_OID_SHA1, _TRIAL_MERGE_TREE_OID_SHA256],
        ids=["sha1", "sha256"],
    )
    def test_exit_1_with_a_tree_oid_and_no_stage_lines_reports_a_conflict_without_paths(
        self, tmp_path: Path, shell_mode: str, tree_oid: str
    ) -> None:
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        bin_dir = tmp_path / "bin-merge-tree-oid"
        _make_git_printing_on_merge_tree(bin_dir, tree_oid, 1)
        result = _trial_merge_verdict(repo, "HEAD", shell_mode, env=_env_with_shim_first(bin_dir))
        assert result.returncode == 0
        assert result.stdout == "Trial merge: CONFLICT\n"
