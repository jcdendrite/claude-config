"""Synthetic two-commit fixture repositories for A-bench.

See .claude/plans/measure-review-quality.md's Approach > Fixtures and arms >
"Fixture" for the design this module follows: a fresh repository built from
`git archive` trees only (never a `git worktree`/clone of the real repo, so
a reviewer's `git log --all` cannot reach a later commit), holding exactly
`base_commit` then `head_commit`, plus a `.bench/` directory excluded via
`.git/info/exclude`.

Builds three kinds of directory:
- an arm fixture (`build_arm_fixture`): the two-commit tree plus an
  installed `bench-<lens>.md` arm file under `.claude/agents/`;
- the arm-neutral precision-judge fixture (`build_precision_judge_fixture`):
  the same tree, with no `bench-<lens>` file;
- the recall-judge directory (`build_recall_judge_dir`): no fixture tree at
  all -- `adjudicate.py` populates its judge file and `.bench/judge-recall.md`.
"""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import tarfile
from dataclasses import dataclass
from pathlib import Path

from review_bench.defects import ConfirmedDefect

# read-scope's own chars-per-token estimate, duplicated here rather than
# imported -- read_scope.py is mid-extraction by #1116 (small-duplicated-
# value exception).
_READ_SCOPE_CHARS_PER_TOKEN = 4

# A default Read truncates at this many estimated tokens.
_OVER_READ_CAP_TOKENS = 25_000

# Local git only (archive/diff/show), no network I/O. Mirrors mine_szz.py's
# own _LOCAL_GIT_TIMEOUT_S rationale -- guards a hung local git blocking a
# fixture build with no exit, not a network SLA. No vendor documentation
# grounds the 10-second magnitude itself -- it is an empirical, considered
# guess against this repo's own git-call latency.
_LOCAL_GIT_TIMEOUT_S = 10.0

# Fixed author and date for both fixture commits (Approach > Fixtures and
# arms > "Fixture"), so two builds of the same defect produce byte-identical
# commits -- a real author/date would leak when the fixture was actually
# built, which is not part of what a run measures.
_FIXTURE_AUTHOR_NAME = "A-bench fixture"
_FIXTURE_AUTHOR_EMAIL = "review-bench@localhost"
_FIXTURE_COMMIT_DATE = "2000-01-01T00:00:00+00:00"
_FIXTURE_BASE_COMMIT_MESSAGE = "base"

_BENCH_DIR_NAME = ".bench"
_GIT_INFO_EXCLUDE_LINE = f"/{_BENCH_DIR_NAME}/\n"


def _run_git(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, timeout=_LOCAL_GIT_TIMEOUT_S,
        capture_output=True, text=True,
    )


def _git_commit_env() -> dict[str, str]:
    return {
        "GIT_AUTHOR_NAME": _FIXTURE_AUTHOR_NAME,
        "GIT_AUTHOR_EMAIL": _FIXTURE_AUTHOR_EMAIL,
        "GIT_AUTHOR_DATE": _FIXTURE_COMMIT_DATE,
        "GIT_COMMITTER_NAME": _FIXTURE_AUTHOR_NAME,
        "GIT_COMMITTER_EMAIL": _FIXTURE_AUTHOR_EMAIL,
        "GIT_COMMITTER_DATE": _FIXTURE_COMMIT_DATE,
    }


def _clear_dir_contents(dest_dir: Path) -> None:
    """Remove everything under dest_dir except .git, so the second commit's
    tree extraction never leaves a file the head commit deleted."""
    for child in dest_dir.iterdir():
        if child.name == ".git":
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def _extract_commit_tree(source_repo: Path, commit: str, dest_dir: Path) -> None:
    """Extract `commit`'s tree from `source_repo` into `dest_dir` via `git
    archive` piped through `tarfile`, never a `git worktree`/clone -- the
    fixture repo must never share an object store with the real one,
    so a reviewer's `git log --all` cannot reach a commit past `head_commit`.
    """
    result = subprocess.run(
        ["git", "archive", commit], cwd=source_repo, check=True,
        timeout=_LOCAL_GIT_TIMEOUT_S, capture_output=True,
    )
    with tarfile.open(fileobj=io.BytesIO(result.stdout)) as tar:
        tar.extractall(dest_dir, filter="data")


def _commit_snapshot(dest_dir: Path, message: str) -> None:
    _run_git(["add", "-A"], cwd=dest_dir)
    subprocess.run(
        ["git", "commit", "-q", "--allow-empty", "-m", message], cwd=dest_dir,
        check=True, timeout=_LOCAL_GIT_TIMEOUT_S, capture_output=True, text=True,
        env={**os.environ, **_git_commit_env()},
    )


def _head_commit_subject(source_repo: Path, commit: str) -> str:
    result = _run_git(["log", "-1", "--format=%s", commit], cwd=source_repo)
    return result.stdout.strip()


def build_two_commit_repo(source_repo: Path, defect: ConfirmedDefect, dest_dir: Path) -> None:
    """Build `dest_dir` as a fresh git repository holding exactly two
    commits: `defect.base_commit`'s tree, then `defect.head_commit`'s tree
    (Approach > Fixtures and arms > "Fixture"). `dest_dir` must exist and be
    empty."""
    _run_git(["init", "-q"], cwd=dest_dir)
    _extract_commit_tree(source_repo, defect.base_commit, dest_dir)
    _commit_snapshot(dest_dir, _FIXTURE_BASE_COMMIT_MESSAGE)

    _clear_dir_contents(dest_dir)
    _extract_commit_tree(source_repo, defect.head_commit, dest_dir)
    subject = _head_commit_subject(source_repo, defect.head_commit)
    _commit_snapshot(dest_dir, subject)


@dataclass(frozen=True)
class ChangedFileStat:
    """One `.bench/changed-files.tsv` row: `path`'s line count and estimated
    token count at HEAD (characters / 4), and whether that estimate exceeds
    the default Read truncation threshold."""

    path: str
    line_count: int
    estimated_tokens: int
    over_read_cap: bool


def _changed_paths(dest_dir: Path) -> list[str]:
    result = _run_git(["diff", "--name-only", "HEAD~1", "HEAD"], cwd=dest_dir)
    return [line for line in result.stdout.splitlines() if line]


def _stat_changed_file(dest_dir: Path, rel_path: str) -> ChangedFileStat:
    abs_path = dest_dir / rel_path
    if not abs_path.is_file():
        # Deleted at HEAD -- nothing left to read, so it can never carry an
        # over-cap flag and contributes no line/token count.
        return ChangedFileStat(path=rel_path, line_count=0, estimated_tokens=0, over_read_cap=False)
    text = abs_path.read_text(errors="replace")
    line_count = text.count("\n") + (1 if text and not text.endswith("\n") else 0)
    estimated_tokens = len(text) // _READ_SCOPE_CHARS_PER_TOKEN
    return ChangedFileStat(
        path=rel_path, line_count=line_count, estimated_tokens=estimated_tokens,
        over_read_cap=estimated_tokens > _OVER_READ_CAP_TOKENS,
    )


def _write_changed_files_tsv(dest_dir: Path, stats: list[ChangedFileStat]) -> None:
    lines = ["path\tline_count\testimated_tokens\tover_read_cap"]
    for stat in stats:
        lines.append(f"{stat.path}\t{stat.line_count}\t{stat.estimated_tokens}\t{'1' if stat.over_read_cap else '0'}")
    (dest_dir / _BENCH_DIR_NAME / "changed-files.tsv").write_text("\n".join(lines) + "\n")


def _write_bench_diffs(dest_dir: Path) -> None:
    bench_dir = dest_dir / _BENCH_DIR_NAME
    bench_dir.mkdir(parents=True, exist_ok=True)
    change_diff = _run_git(["diff", "HEAD~1", "HEAD"], cwd=dest_dir).stdout
    (bench_dir / "change.diff").write_text(change_diff)
    # git-diff(1) -W: "Show whole function as context lines".
    function_context_diff = _run_git(["diff", "-W", "HEAD~1", "HEAD"], cwd=dest_dir).stdout
    (bench_dir / "change-function-context.diff").write_text(function_context_diff)


def _exclude_bench_dir(dest_dir: Path) -> None:
    """Excludes .bench/ via .git/info/exclude, never .gitignore -- the
    fixture's committed tree must match the real repo's history exactly at
    each commit, and .gitignore is part of that tree."""
    exclude_path = dest_dir / ".git" / "info" / "exclude"
    exclude_path.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude_path.read_text() if exclude_path.exists() else ""
    if _GIT_INFO_EXCLUDE_LINE not in existing:
        exclude_path.write_text(existing + _GIT_INFO_EXCLUDE_LINE)


def write_bench_artifacts(dest_dir: Path) -> list[ChangedFileStat]:
    """Write `.bench/change.diff`, `.bench/change-function-context.diff`, and
    `.bench/changed-files.tsv` into an already-built two-commit `dest_dir`,
    and exclude `.bench/` from git. Returns the per-file stats written to
    the TSV."""
    _write_bench_diffs(dest_dir)
    stats = [_stat_changed_file(dest_dir, path) for path in _changed_paths(dest_dir)]
    _write_changed_files_tsv(dest_dir, stats)
    _exclude_bench_dir(dest_dir)
    return stats


@dataclass(frozen=True)
class FixtureRepo:
    dest_dir: Path
    defect: ConfirmedDefect
    changed_files: tuple[ChangedFileStat, ...]


def build_defect_fixture(source_repo: Path, defect: ConfirmedDefect, dest_dir: Path) -> FixtureRepo:
    """Build the full two-commit tree plus `.bench/` artifacts for `defect`
    under `dest_dir`. Shared by arm fixtures and the precision-judge
    fixture -- callers install (or don't install) a `bench-<lens>.md` file
    afterward."""
    build_two_commit_repo(source_repo, defect, dest_dir)
    stats = write_bench_artifacts(dest_dir)
    return FixtureRepo(dest_dir=dest_dir, defect=defect, changed_files=tuple(stats))


def build_precision_judge_fixture(source_repo: Path, defect: ConfirmedDefect, dest_dir: Path) -> FixtureRepo:
    """The arm-neutral precision-judge fixture: `build_defect_fixture`'s
    same tree and `.bench/` artifacts, with no `bench-<lens>.md` installed
    (Approach > Runs and adjudication > "Precision judge") -- the judge
    agent file and `.bench/judge-precision.md` are `adjudicate.py`'s own
    responsibility."""
    return build_defect_fixture(source_repo, defect, dest_dir)


def build_recall_judge_dir(dest_dir: Path) -> Path:
    """The recall judge's own working directory: no fixture tree at all
    (Approach > Runs and adjudication > "Recall judge") -- just the
    directory itself. `adjudicate.py` installs the judge agent file and
    writes `.bench/judge-recall.md` into it."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    return dest_dir
