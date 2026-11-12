"""What a replay run changed, read after the session ends.

Every call uses the runner-owned copy of the fixture's pre-launch `.git` (`baseline.git`) with the
fixture as the work tree, so the diff compares the work tree with the pristine base whatever the agent did to
the fixture's own `.git`. A changed file is never read through its work-tree path: a blob is read from the
copy's object store, so a symlink the agent made is read as its link text and a FIFO is never opened.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from review_bench.local_git import DIFF_TEXT_ARGS, isolated_git_environment

from replay_bench import comment_metrics
from replay_bench.comment_metrics import CommentMetrics

IGNORED_PATHS_KEPT = 100
_PROJECT_CONFIG_DIR_PREFIX = ".claude/"
_DIFF_FLAGS = ("-U0", "--no-renames", "--src-prefix=a/", "--dst-prefix=b/")


@dataclass(frozen=True)
class ChangeCapture:
    diff_text: str
    metrics: CommentMetrics
    ignored_path_count: int
    ignored_paths: tuple[str, ...]


def _location_flags(baseline_git_dir: Path, fixture_dir: Path) -> list[str]:
    return [f"--git-dir={baseline_git_dir}", f"--work-tree={fixture_dir}"]


def _git(
    args: list[str], *, baseline_git_dir: Path, fixture_dir: Path, timeout_s: float, input_bytes: bytes | None = None,
) -> bytes:
    return subprocess.run(
        ["git", *_location_flags(baseline_git_dir, fixture_dir), *args], cwd=fixture_dir, check=True, capture_output=True,
        timeout=timeout_s, input=input_bytes, env=isolated_git_environment(),
    ).stdout


def _read_blobs(oids: list[str], **git_kwargs) -> dict[str, bytes]:
    """One `git cat-file --batch` call. Raises ValueError for a reply that does not name each object in order."""
    unique_oids = list(dict.fromkeys(oids))
    if not unique_oids:
        return {}
    reply = _git(["cat-file", "--batch"], input_bytes="".join(f"{oid}\n" for oid in unique_oids).encode(), **git_kwargs)
    blobs: dict[str, bytes] = {}
    position = 0
    for oid in unique_oids:
        header_end = reply.index(b"\n", position)
        header = reply[position:header_end].decode().split(" ")
        position = header_end + 1
        if header[0] != oid or len(header) != 3 or header[1] != "blob":
            raise ValueError(f"git cat-file --batch answered {header!r} for {oid}")
        size = int(header[2])
        blobs[oid] = reply[position:position + size]
        position += size + 1
    return blobs


def capture_change(baseline_git_dir: Path, fixture_dir: Path, base_commit: str, *, timeout_s: float) -> ChangeCapture:
    """The diff against `base_commit`, its classification, and the untracked paths the work tree's ignore
    rules kept out of the diff. Raises CalledProcessError, TimeoutExpired, OSError or ValueError when a git
    call or the classifier fails, such as `add -A` on a nested repository that has no commit."""
    git_kwargs = {"baseline_git_dir": baseline_git_dir, "fixture_dir": fixture_dir, "timeout_s": timeout_s}
    _git(["add", "-A"], **git_kwargs)
    diff_bytes = _git(
        ["-c", "core.quotePath=false", "diff", "--cached", *_DIFF_FLAGS, *DIFF_TEXT_ARGS, base_commit], **git_kwargs,
    )
    raw_listing = _git(
        ["diff", "--cached", "--raw", "-z", "--no-abbrev", "--no-renames", base_commit], **git_kwargs,
    ).decode("utf-8", errors="surrogateescape")
    regular_entries = [
        entry for entry in comment_metrics.parse_raw_listing(raw_listing)
        if entry.new_mode in comment_metrics.REGULAR_FILE_MODES
    ]
    blob_by_oid = _read_blobs([entry.new_blob for entry in regular_entries], **git_kwargs)
    diff_text = diff_bytes.decode("utf-8", errors="replace")
    metrics = comment_metrics.measure_diff(
        diff_text, raw_listing, {entry.path: blob_by_oid[entry.new_blob] for entry in regular_entries},
    )
    ignored_listing = _git(
        ["ls-files", "-z", "--others", "--ignored", "--exclude-standard", "--directory"], **git_kwargs,
    ).decode("utf-8", errors="surrogateescape")
    ignored_paths = tuple(
        path for path in ignored_listing.split("\0") if path and not path.startswith(_PROJECT_CONFIG_DIR_PREFIX)
    )
    return ChangeCapture(diff_text, metrics, len(ignored_paths), ignored_paths[:IGNORED_PATHS_KEPT])
