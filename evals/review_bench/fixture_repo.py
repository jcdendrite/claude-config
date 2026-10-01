"""Synthetic two-commit fixture repositories for the review bench: a fresh repository
built from `git archive` trees only (never a `git worktree`/clone of the real
repo, so a reviewer's `git log --all` cannot reach a later commit), holding
exactly `base_commit` then `head_commit`, plus a `.bench/` directory excluded
via `.git/info/exclude`.

Builds three kinds of directory:
- a defect fixture (`build_defect_fixture`): the two-commit tree, to which
  the caller adds a `bench-<lens>.md` arm file under `.claude/agents/`;
- the arm-neutral precision-judge fixture (`build_precision_judge_fixture`):
  the same tree, with no `bench-<lens>` file;
- the recall-judge directory (`build_recall_judge_dir`): no fixture tree at
  all -- `adjudicate.py` populates its judge file and `.bench/judge-recall.md`.
"""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import tarfile
from dataclasses import dataclass
from pathlib import Path

from measure_subagent_model_resolution import environment_without_git_local_vars

from review_bench.defects import ConfirmedDefect, _validate_sha

# Duplicated from read_scope's chars-per-token estimate to avoid importing that
# module; runner.py carries the same constant.
_READ_SCOPE_CHARS_PER_TOKEN = 4

# A default Read truncates at this many estimated tokens.
_OVER_READ_CAP_TOKENS = 25_000

# Local git only (archive/diff/show), no network I/O. Mirrors mine_szz.py's
# own _LOCAL_GIT_TIMEOUT_S rationale (guards a hung local git blocking a
# fixture build with no exit). No vendor documentation grounds the
# 10-second magnitude -- it's an empirical guess.
_LOCAL_GIT_TIMEOUT_S = 10.0

# Fixed author and date for both fixture commits, so two builds of the same
# defect produce byte-identical commits -- a real author/date would leak
# when the fixture was actually built, which is not part of what a run
# measures.
_FIXTURE_AUTHOR_NAME = "review-bench fixture"
_FIXTURE_AUTHOR_EMAIL = "review-bench@localhost"
_FIXTURE_COMMIT_DATE = "2000-01-01T00:00:00+00:00"
_FIXTURE_BASE_COMMIT_MESSAGE = "base"
# A constant, never the mined subject: the session's git-status snapshot and
# `git log` would carry it into the prompt context, and
# .bench/commit-subject.txt is its only intended route.
_FIXTURE_HEAD_COMMIT_MESSAGE = "head"

_BENCH_DIR_NAME = ".bench"
_COMMIT_SUBJECT_FILE_NAME = "commit-subject.txt"
_GIT_INFO_EXCLUDE_LINE = f"/{_BENCH_DIR_NAME}/\n"


# The top-level keys `.claude/settings.json` has held across this repository's
# history (`git log -p -- .claude/settings.json`). A key outside this set could
# carry executable config (hooks, env, apiKeyHelper, statusLine, MCP servers)
# into the session that runs in the fixture.
_ALLOWED_PROJECT_SETTINGS_KEYS = frozenset({"attribution", "claudeMdExcludes", "enabledPlugins", "permissions"})
_PROJECT_SETTINGS_RELPATH = Path(".claude") / "settings.json"
_FORBIDDEN_PROJECT_CONFIG_RELPATHS = (Path(".mcp.json"), Path(".claude") / "settings.local.json")


class UnsafeFixtureConfigError(ValueError):
    pass


def _run_git(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, timeout=_LOCAL_GIT_TIMEOUT_S,
        capture_output=True, text=True, env=environment_without_git_local_vars(),
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
        timeout=_LOCAL_GIT_TIMEOUT_S, capture_output=True, env=environment_without_git_local_vars(),
    )
    with tarfile.open(fileobj=io.BytesIO(result.stdout)) as tar:
        tar.extractall(dest_dir, filter="data")


def _refuse_unsafe_project_settings(raw_settings: bytes) -> None:
    """Fails closed on a `.claude/settings.json` that is unparseable, is not a
    JSON object, or holds a top-level key outside this repository's history."""
    try:
        settings = json.loads(raw_settings.decode())
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise UnsafeFixtureConfigError(f"fixture tree's {_PROJECT_SETTINGS_RELPATH} is unparseable: {exc}") from exc
    if not isinstance(settings, dict):
        raise UnsafeFixtureConfigError(f"fixture tree's {_PROJECT_SETTINGS_RELPATH} is not a JSON object")
    unexpected_keys = sorted(set(settings) - _ALLOWED_PROJECT_SETTINGS_KEYS)
    if unexpected_keys:
        raise UnsafeFixtureConfigError(
            f"fixture tree's {_PROJECT_SETTINGS_RELPATH} has top-level key(s) {unexpected_keys} outside the "
            f"set seen in this repository's history {sorted(_ALLOWED_PROJECT_SETTINGS_KEYS)}"
        )


def _refuse_executable_project_config(dest_dir: Path) -> None:
    """Fails closed on an extracted head tree whose project config a session
    would load with more than this repository's own settings history has ever
    held."""
    for relpath in _FORBIDDEN_PROJECT_CONFIG_RELPATHS:
        if os.path.lexists(dest_dir / relpath):
            raise UnsafeFixtureConfigError(f"fixture tree holds {relpath}, which a session would load")
    settings_path = dest_dir / _PROJECT_SETTINGS_RELPATH
    if settings_path.is_file():
        _refuse_unsafe_project_settings(settings_path.read_bytes())


def refuse_executable_project_config_at_commit(source_repo: Path, commit: str) -> None:
    """The refusal `build_two_commit_repo` applies to the extracted head tree,
    read straight from `commit`'s tree so a preflight can run it before any
    fixture exists. Raises ValueError for a commit that is not a 40-hex SHA,
    so none can read as a git option, UnsafeFixtureConfigError, or
    CalledProcessError / TimeoutExpired when git fails."""
    _validate_sha("commit", commit)
    config_relpaths = [relpath.as_posix() for relpath in (*_FORBIDDEN_PROJECT_CONFIG_RELPATHS, _PROJECT_SETTINGS_RELPATH)]
    listing = subprocess.run(
        ["git", "ls-tree", "-z", "--name-only", commit, "--", *config_relpaths], cwd=source_repo, check=True,
        timeout=_LOCAL_GIT_TIMEOUT_S, capture_output=True, env=environment_without_git_local_vars(),
    ).stdout
    present = {os.fsdecode(raw_path) for raw_path in listing.split(b"\0") if raw_path}
    for relpath in _FORBIDDEN_PROJECT_CONFIG_RELPATHS:
        if relpath.as_posix() in present:
            raise UnsafeFixtureConfigError(f"fixture tree holds {relpath}, which a session would load")
    if _PROJECT_SETTINGS_RELPATH.as_posix() in present:
        raw_settings = subprocess.run(
            ["git", "show", f"{commit}:{_PROJECT_SETTINGS_RELPATH.as_posix()}"], cwd=source_repo, check=True,
            timeout=_LOCAL_GIT_TIMEOUT_S, capture_output=True, env=environment_without_git_local_vars(),
        ).stdout
        _refuse_unsafe_project_settings(raw_settings)


def _commit_snapshot(dest_dir: Path, message: str) -> None:
    # --force so a snapshot's tree equals its source commit's tree whatever
    # `.gitignore` or global excludes say: git archive emits only tracked
    # paths, and `add` never descends into `.git`.
    _run_git(["add", "-A", "--force"], cwd=dest_dir)
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null",
         "commit", "-q", "--allow-empty", "-m", message], cwd=dest_dir,
        check=True, timeout=_LOCAL_GIT_TIMEOUT_S, capture_output=True, text=True,
        env={**environment_without_git_local_vars(), **_git_commit_env()},
    )


def _head_commit_subject(source_repo: Path, commit: str) -> str:
    result = _run_git(["log", "-1", "--format=%s", commit], cwd=source_repo)
    return result.stdout.strip()


def build_two_commit_repo(source_repo: Path, defect: ConfirmedDefect, dest_dir: Path) -> None:
    """Build `dest_dir` as a fresh git repository holding exactly two
    commits: `defect.base_commit`'s tree, then `defect.head_commit`'s tree.
    `dest_dir` must exist and be empty. Raises UnsafeFixtureConfigError, before
    committing the head tree, when its project config is not inert."""
    _run_git(["init", "-q"], cwd=dest_dir)
    _extract_commit_tree(source_repo, defect.base_commit, dest_dir)
    _commit_snapshot(dest_dir, _FIXTURE_BASE_COMMIT_MESSAGE)

    _clear_dir_contents(dest_dir)
    # An empty index forces `git add -A` to hash every head file from content:
    # `git archive` stamps both extractions with the commit time, so a
    # same-size file edited within one commit second would otherwise match its
    # stale index stat entry and keep the base blob.
    _run_git(["read-tree", "--empty"], cwd=dest_dir)
    _extract_commit_tree(source_repo, defect.head_commit, dest_dir)
    _refuse_executable_project_config(dest_dir)
    _commit_snapshot(dest_dir, _FIXTURE_HEAD_COMMIT_MESSAGE)


@dataclass(frozen=True)
class ChangedFileStat:
    """One `.bench/changed-files.tsv` row: `path`'s line count and estimated
    token count at HEAD (characters / 4), and whether that estimate exceeds
    the default Read truncation threshold."""

    path: str
    line_count: int
    estimated_tokens: int
    over_read_cap: bool


def changed_paths_between(repo_dir: Path, base: str, head: str) -> list[str]:
    """Paths that differ between base and head. NUL-delimited so git never
    C-quotes a non-ASCII or special-character name, and rename-free so a
    rename lists both its old and new path."""
    result = subprocess.run(
        ["git", "diff", "-z", "--name-only", "--no-renames", base, head], cwd=repo_dir, check=True,
        timeout=_LOCAL_GIT_TIMEOUT_S, capture_output=True, env=environment_without_git_local_vars(),
    )
    return [os.fsdecode(raw_path) for raw_path in result.stdout.split(b"\0") if raw_path]


def fix_commit_paths(repo_dir: Path, defect: ConfirmedDefect) -> list[str]:
    """Paths the defect's fix commit changed against its first parent."""
    return changed_paths_between(repo_dir, f"{defect.fix_commit}^", defect.fix_commit)


def _changed_paths(dest_dir: Path) -> list[str]:
    return changed_paths_between(dest_dir, "HEAD~1", "HEAD")


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


def _write_commit_subject(dest_dir: Path, subject: str) -> None:
    """The mined commit subject reaches the reviewer only as this data file,
    never through a session prompt or the fixture's git history."""
    (dest_dir / _BENCH_DIR_NAME / _COMMIT_SUBJECT_FILE_NAME).write_text(subject + "\n")


def _exclude_bench_dir(dest_dir: Path) -> None:
    """Excludes .bench/ via .git/info/exclude, never .gitignore -- the
    fixture's committed tree must match the real repo's history exactly at
    each commit, and .gitignore is part of that tree."""
    exclude_path = dest_dir / ".git" / "info" / "exclude"
    exclude_path.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude_path.read_text() if exclude_path.exists() else ""
    if _GIT_INFO_EXCLUDE_LINE not in existing:
        exclude_path.write_text(existing + _GIT_INFO_EXCLUDE_LINE)


def write_bench_artifacts(dest_dir: Path, commit_subject: str) -> list[ChangedFileStat]:
    """Write `.bench/change.diff`, `.bench/change-function-context.diff`,
    `.bench/changed-files.tsv`, and `.bench/commit-subject.txt` (holding
    `commit_subject`) into an already-built two-commit `dest_dir`, and exclude
    `.bench/` from git. Returns the per-file stats written to the TSV."""
    _write_bench_diffs(dest_dir)
    _write_commit_subject(dest_dir, commit_subject)
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
    fixture -- callers copy in (or don't copy in) a `bench-<lens>.md` file
    afterward."""
    build_two_commit_repo(source_repo, defect, dest_dir)
    stats = write_bench_artifacts(dest_dir, _head_commit_subject(source_repo, defect.head_commit))
    return FixtureRepo(dest_dir=dest_dir, defect=defect, changed_files=tuple(stats))


def build_precision_judge_fixture(source_repo: Path, defect: ConfirmedDefect, dest_dir: Path) -> FixtureRepo:
    """The arm-neutral precision-judge fixture: `build_defect_fixture`'s
    same tree and `.bench/` artifacts, with no `bench-<lens>.md` installed --
    the judge agent file and `.bench/judge-precision.md` are `adjudicate.py`'s
    own responsibility."""
    return build_defect_fixture(source_repo, defect, dest_dir)


def build_recall_judge_dir(dest_dir: Path) -> Path:
    """The recall judge's own working directory: no fixture tree at all,
    just the directory itself. `adjudicate.py` installs the judge agent file
    and writes `.bench/judge-recall.md` into it."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    return dest_dir
