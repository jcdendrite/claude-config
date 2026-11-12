"""Mines a source repository's first-parent commits into replay tasks, and builds the
single-commit fixture a task runs in.

A task replays one commit: the fixture holds the commit's first-parent tree, the
brief is the commit message plus the merged commits' subjects for a merge commit, and
`reference_metrics` classifies what the commit itself added. Every git call here runs under
`local_git.isolated_git_environment()`. Mining calls carry no timeout, since a person
runs mining by hand and Ctrl-C ends a hung call. `build_task_fixture` bounds its calls by the caller's
`timeout_s`.
"""
from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
import shutil
import subprocess
import unicodedata
from collections import Counter
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from review_bench import defects, fixture_repo, identifiers, runner
from review_bench.local_git import DIFF_TEXT_ARGS, isolated_git_environment

from replay_bench import comment_metrics
from replay_bench.comment_metrics import CommentMetrics, LineClass

PROGRESS_INTERVAL_COMMITS = 50
BYTES_PER_MEGABYTE = 1_000_000

BEGIN_DESCRIPTION_LINE = "BEGIN CHANGE DESCRIPTION"
END_DESCRIPTION_LINE = "END CHANGE DESCRIPTION"
# `build_dispatcher_prompt` wraps the inner prompt between the dispatcher's marker lines.
_RESERVED_BRIEF_LINES = frozenset({
    BEGIN_DESCRIPTION_LINE, END_DESCRIPTION_LINE, runner._DISPATCH_PROMPT_MARKER_OPEN, runner._DISPATCH_PROMPT_MARKER_CLOSE,
})

# Names matched case-insensitively at any depth.
_INSTRUCTION_FILE_NAMES = frozenset({"claude.md", "claude.local.md", "agents.md"})
_ROOT_MCP_CONFIG_NAME = ".mcp.json"

_WORKTREE_OPTOUT_RELPATH = Path(fixture_repo._PROJECT_CONFIG_DIR_NAME) / "worktree-optout"
BARE_WRITER_RELPATH = Path(fixture_repo._PROJECT_CONFIG_DIR_NAME) / "agents" / "bare-writer.md"
FIXTURE_SETTINGS_RELPATH = Path(fixture_repo._PROJECT_CONFIG_DIR_NAME) / "settings.json"
_PROJECT_CONFIG_EXCLUDE_LINE = f"/{fixture_repo._PROJECT_CONFIG_DIR_NAME}/\n"

_TASK_ID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{12}")
_TASK_KEYS = (
    "id", "source_repo", "base_commit", "commit", "brief", "changed_paths", "author_email", "committer_email",
    "reference_metrics", "base_density", "base_tree_bytes", "base_tree_entries",
)

# Fields are NUL-framed because an ident cannot hold a NUL, so no author or committer value can shift a boundary.
_GIT_LOG_FORMAT = "%x00".join(["%H", "%P", "%ae", "%ce", "%B"])
_GIT_LOG_FIELD_COUNT = 5
_MERGED_COMMIT_LOG_FORMAT = "%x00".join(["%ae", "%s"])
_MERGED_COMMIT_LOG_FIELD_COUNT = 2
_SYMLINK_MODE = "120000"
# The side of a raw diff entry that holds no file, as on an added or a deleted path.
_ABSENT_MODE = "000000"
# Each option overrides a repo-local diff setting that changes the entry list or the added-line set.
_STABLE_DIFF_ARGS = ("--no-renames", "--diff-algorithm=myers", "--indent-heuristic", "--ignore-submodules=none")
_GITATTRIBUTES_NAME = ".gitattributes"
# `git archive` honors both attributes, so a fixture extracted from such a tree differs from the tree
# the miner sized and measured. The guard covers only these two: `git archive` also applies the `text`,
# `eol`, `ident` and `working-tree-encoding` conversions, which no check here compares.
_EXPORT_ATTRIBUTES = (b"export-ignore", b"export-subst")


class TaskSourceError(ValueError):
    """A source repository, rev, trusted-author or selection-limit argument the miner refuses, or a commit it
    cannot read safely."""


class TaskFileError(ValueError):
    """A task-set file that is unreadable or holds a task that breaks the task grammar."""


class MissingObjectError(Exception):
    """`git cat-file --batch` answered `missing`, as a partial clone does for an object it does not hold."""


class SkipReason(StrEnum):
    NO_PARENT = "no-parent"
    UNTRUSTED_AUTHOR = "untrusted-author"
    UNTRUSTED_MERGED_AUTHOR = "untrusted-merged-author"
    RESERVED_BRIEF_LINE = "reserved-brief-line"
    TOO_MANY_FILES = "too-many-files"
    UNSAFE_PATH = "unsafe-path"
    BINARY = "binary"
    TOO_FEW_CODE_LINES = "too-few-code-lines"
    TOO_MANY_CODE_LINES = "too-many-code-lines"
    PROMPT_TOO_LONG = "prompt-too-long"
    BASE_TREE_TOO_LARGE = "base-tree-too-large"
    BASE_TREE_TOO_MANY_ENTRIES = "base-tree-too-many-entries"
    MISSING_OBJECT = "missing-object"
    CLAUDE_DIR_SYMLINK = "claude-dir-symlink"
    ABSOLUTE_SYMLINK = "absolute-symlink"
    ESCAPING_SYMLINK = "escaping-symlink"
    GIT_PATH_COMPONENT = "git-path-component"
    BENCH_DIR_ENTRY = "bench-dir-entry"
    EXPORT_ATTRIBUTES = "export-attributes"


@dataclass(frozen=True)
class SelectionLimits:
    rev: str = "HEAD"
    max_count: int = 500
    max_tasks: int = 30
    max_files: int = 10
    min_code_lines: int = 10
    max_code_lines: int = 300
    max_prompt_chars: int = 4000
    max_base_mb: int = 100
    # Basis: the fixture-build timing measured for 12,500 files totaling 100 MB, in the Fixture section of
    # .claude/plans/docstring-verbosity-experiment.md.
    max_base_entries: int = 12_500

    def __post_init__(self) -> None:
        """Raises TaskSourceError for an integer limit below 1, naming its flag, and for a minimum above the
        maximum. A negative `max_count` would make `git log -n` walk the whole history."""
        for field_name in _INTEGER_LIMIT_FIELDS:
            if getattr(self, field_name) < 1:
                raise TaskSourceError(f"{_flag_for(field_name)} must be at least 1, got {getattr(self, field_name)}")
        if self.min_code_lines > self.max_code_lines:
            raise TaskSourceError(
                f"--min-code-lines {self.min_code_lines} is above --max-code-lines {self.max_code_lines}"
            )


_INTEGER_LIMIT_FIELDS = (
    "max_count", "max_tasks", "max_files", "min_code_lines", "max_code_lines", "max_prompt_chars", "max_base_mb",
    "max_base_entries",
)


def _flag_for(field_name: str) -> str:
    return "--" + field_name.replace("_", "-")


DEFAULT_LIMITS = SelectionLimits()


@dataclass(frozen=True)
class ReplayTask:
    """`base_density` is the base files' comment-plus-docstring words per non-test code line,
    or None when the changed non-test files hold no code line in the base."""

    id: str
    source_repo: str
    base_commit: str
    commit: str
    brief: str
    changed_paths: tuple[str, ...]
    author_email: str
    committer_email: str
    reference_metrics: CommentMetrics
    base_density: float | None
    base_tree_bytes: int
    base_tree_entries: int

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "source_repo": self.source_repo,
            "base_commit": self.base_commit,
            "commit": self.commit,
            "brief": self.brief,
            "changed_paths": list(self.changed_paths),
            "author_email": self.author_email,
            "committer_email": self.committer_email,
            "reference_metrics": self.reference_metrics.to_dict(),
            "base_density": self.base_density,
            "base_tree_bytes": self.base_tree_bytes,
            "base_tree_entries": self.base_tree_entries,
        }

    @classmethod
    def from_dict(cls, data: Mapping) -> ReplayTask:
        """Raises ValueError for:
        - A missing key.
        - A malformed ID or SHA.
        - An ID other than the one `make_task_id` derives from `source_repo` and `commit`.
        - A field of the wrong type: a string field, a count, the density or `changed_paths`.
        - A `reference_metrics` that `CommentMetrics.from_dict` cannot read.

        The inner values of `reference_metrics` are not type-checked."""
        missing_keys = [key for key in _TASK_KEYS if key not in data]
        if missing_keys:
            raise ValueError(f"task lacks keys {missing_keys}")
        if not isinstance(data["id"], str) or not _TASK_ID_RE.fullmatch(data["id"]):
            raise ValueError(f"task ID {data['id']!r} does not match <8 hex>-<12 hex>")
        for string_field in ("source_repo", "base_commit", "commit", "brief", "author_email", "committer_email"):
            if not isinstance(data[string_field], str):
                raise ValueError(f"{string_field} must be a string, got {data[string_field]!r}")
        for sha_field in ("base_commit", "commit"):
            fixture_repo._validate_sha(sha_field, data[sha_field])
        for count_field in ("base_tree_bytes", "base_tree_entries"):
            if not _is_number(data[count_field], int):
                raise ValueError(f"{count_field} must be an integer, got {data[count_field]!r}")
        if data["base_density"] is not None and not _is_number(data["base_density"], (int, float)):
            raise ValueError(f"base_density must be a number or null, got {data['base_density']!r}")
        derived_id = make_task_id(Path(data["source_repo"]), data["commit"])
        if data["id"] != derived_id:
            raise ValueError(
                f"task ID {data['id']} is not {derived_id}, the ID of commit {data['commit']} in {data['source_repo']}"
            )
        changed_paths = data["changed_paths"]
        if not isinstance(changed_paths, list) or not all(isinstance(path, str) for path in changed_paths):
            raise ValueError(f"task {data['id']} changed_paths must be a list of strings")
        try:
            return cls(
                id=data["id"],
                source_repo=data["source_repo"],
                base_commit=data["base_commit"],
                commit=data["commit"],
                brief=data["brief"],
                changed_paths=tuple(changed_paths),
                author_email=data["author_email"],
                committer_email=data["committer_email"],
                reference_metrics=CommentMetrics.from_dict(data["reference_metrics"]),
                base_density=None if data["base_density"] is None else float(data["base_density"]),
                base_tree_bytes=data["base_tree_bytes"],
                base_tree_entries=data["base_tree_entries"],
            )
        except (KeyError, TypeError) as exc:
            raise ValueError(f"task {data['id']} holds a value of the wrong shape: {exc!r}") from exc


def _is_number(value: object, number_type: type | tuple[type, ...]) -> bool:
    """Whether `value` is a `number_type`, with a bool refused since it subclasses int."""
    return isinstance(value, number_type) and not isinstance(value, bool)


def make_task_id(source_repo: Path, commit: str) -> str:
    """The first 8 hex digits of the source path's sha256, a hyphen, and the commit's first 12 hex digits."""
    return f"{hashlib.sha256(os.fsencode(source_repo)).hexdigest()[:8]}-{commit[:12]}"


def task_set_path(tasks_dir: Path, name: str) -> Path:
    return tasks_dir / f"{identifiers.validate_campaign_id(name)}.json"


def _refuse_duplicate_task_ids(path: Path, tasks: Sequence[ReplayTask]) -> None:
    duplicate_ids = sorted(task_id for task_id, count in Counter(task.id for task in tasks).items() if count > 1)
    if duplicate_ids:
        raise TaskFileError(f"{path}: the task set holds duplicate task IDs {duplicate_ids}")


def write_task_set(tasks_dir: Path, name: str, tasks: Sequence[ReplayTask]) -> Path:
    """Raises TaskFileError for two tasks that share an ID."""
    path = task_set_path(tasks_dir, name)
    _refuse_duplicate_task_ids(path, tasks)
    defects.atomic_write_text(path, json.dumps([task.to_dict() for task in tasks], indent=2) + "\n")
    return path


def read_task_set(tasks_dir: Path, name: str) -> list[ReplayTask]:
    """Raises TaskFileError, naming the file, for:
    - An unreadable file or unparseable JSON.
    - A top-level value that is not a list, or an element that is not an object.
    - A task that breaks the task grammar.
    - Two tasks that share an ID."""
    path = task_set_path(tasks_dir, name)
    try:
        raw_tasks = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise TaskFileError(f"{path}: cannot read the task set: {exc}") from exc
    if not isinstance(raw_tasks, list):
        raise TaskFileError(f"{path}: the task set is not a JSON list")
    tasks = []
    for position, raw_task in enumerate(raw_tasks):
        if not isinstance(raw_task, dict):
            raise TaskFileError(f"{path}: task {position} is not a JSON object")
        try:
            tasks.append(ReplayTask.from_dict(raw_task))
        except ValueError as exc:
            raise TaskFileError(f"{path}: task {position}: {exc}") from exc
    _refuse_duplicate_task_ids(path, tasks)
    return tasks


# --- prune and fixture -------------------------------------------------------


def is_pruned_path(path: str, *, is_directory_or_link: bool = False) -> bool:
    """Whether the fixture prune removes `path`, a repository-relative POSIX path, either itself or
    through a parent directory it removes. `is_directory_or_link` says whether the last component
    is a directory or a symlink, since the prune keeps a regular file named `.claude`."""
    components = path.split("/")
    last_index = len(components) - 1
    return any(
        _is_pruned_entry(component, at_root=index == 0, is_directory_or_link=index < last_index or is_directory_or_link)
        for index, component in enumerate(components)
    )


def _raise_walk_error(error: OSError) -> None:
    raise error


def _is_pruned_entry(name: str, *, at_root: bool, is_directory_or_link: bool) -> bool:
    folded = name.casefold()
    return (
        folded in _INSTRUCTION_FILE_NAMES
        or (folded == fixture_repo._PROJECT_CONFIG_DIR_NAME and is_directory_or_link)
        or (at_root and folded == _ROOT_MCP_CONFIG_NAME)
    )


def prune_instruction_files(dest_dir: Path) -> None:
    """Removes the entries that would load a checkout's own instructions into a session:
    every `CLAUDE.md`, `CLAUDE.local.md` and `AGENTS.md`, every `.claude` directory, and the
    root `.mcp.json`, each matched case-insensitively. The walk never follows a link, and a
    matching symlink is unlinked, never its target. Raises UnsafeFixtureConfigError when a
    remaining symlink resolves outside `dest_dir`."""
    for current_dir, dirnames, filenames in os.walk(dest_dir, onerror=_raise_walk_error):
        at_root = Path(current_dir) == dest_dir
        if at_root and ".git" in dirnames:
            dirnames.remove(".git")
        for name in [*dirnames, *filenames]:
            entry_path = os.path.join(current_dir, name)
            is_link = os.path.islink(entry_path)
            if not _is_pruned_entry(name, at_root=at_root, is_directory_or_link=is_link or name in dirnames):
                continue
            if is_link or name in filenames:
                os.unlink(entry_path)
            else:
                shutil.rmtree(entry_path)
            if name in dirnames:
                dirnames.remove(name)
    _refuse_symlinks_leaving(dest_dir)


def _refuse_symlinks_leaving(dest_dir: Path) -> None:
    """A link chain can pass both the lexical check in `base_tree_refusal` and a tar filter that
    does not normalize link targets, and a write through it would escape the fixture."""
    resolved_root = os.path.realpath(dest_dir)
    for current_dir, dirnames, filenames in os.walk(dest_dir, onerror=_raise_walk_error):
        if Path(current_dir) == dest_dir and ".git" in dirnames:
            dirnames.remove(".git")
        for name in [*dirnames, *filenames]:
            entry_path = os.path.join(current_dir, name)
            if not os.path.islink(entry_path):
                continue
            resolved = os.path.realpath(entry_path)
            if os.path.commonpath([resolved, resolved_root]) != resolved_root:
                raise fixture_repo.UnsafeFixtureConfigError(
                    f"fixture symlink {os.path.relpath(entry_path, dest_dir)!r} resolves outside the fixture"
                )


def build_task_fixture(
    task: ReplayTask, dest_dir: Path, baseline_git_dir: Path, *, bare_writer_markdown: str, timeout_s: float,
    settings_text: str | None = None,
) -> str:
    """Builds `dest_dir` as a one-commit repository of the task's base tree, pruned by
    `prune_instruction_files`, and returns that commit's SHA.

    `dest_dir` must exist and be empty. `baseline_git_dir` must not exist.

    After the commit it writes the worktree opt-out and `.claude/agents/bare-writer.md`, plus
    `.claude/settings.json` when `settings_text` is given.
    It lists `.claude/` in `.git/info/exclude`, so none of them reaches a diff.
    It then copies the repository's `.git` to `baseline_git_dir`.

    Raises:
    - TaskSourceError: the source repository has become a partial clone or sets an export
      attribute in `info/attributes` since mining.
    - UnsafeFixtureConfigError: a refused archive member, or a symlink resolving outside the fixture.
    - ValueError: a commit that is not a 40-hex SHA.
    - CalledProcessError, TimeoutExpired or OSError: a failed step.

    A raise after the build starts leaves `dest_dir` and `baseline_git_dir` partly built."""
    source_repo = Path(task.source_repo)
    _refuse_partial_clone(source_repo, source_repo, timeout_s=timeout_s)
    _refuse_export_attributes_in_info_file(source_repo, source_repo, timeout_s=timeout_s)
    fixture_repo.build_single_commit_repo(
        source_repo, task.base_commit, dest_dir, prune=prune_instruction_files, timeout_s=timeout_s,
    )
    (dest_dir / BARE_WRITER_RELPATH).parent.mkdir(parents=True)
    (dest_dir / _WORKTREE_OPTOUT_RELPATH).write_text("")
    (dest_dir / BARE_WRITER_RELPATH).write_text(bare_writer_markdown)
    if settings_text is not None:
        (dest_dir / FIXTURE_SETTINGS_RELPATH).write_text(settings_text)
    exclude_path = dest_dir / ".git" / "info" / "exclude"
    exclude_path.parent.mkdir(parents=True, exist_ok=True)
    existing_excludes = exclude_path.read_text() if exclude_path.exists() else ""
    if _PROJECT_CONFIG_EXCLUDE_LINE not in existing_excludes:
        exclude_path.write_text(existing_excludes + _PROJECT_CONFIG_EXCLUDE_LINE)
    shutil.copytree(dest_dir / ".git", baseline_git_dir, symlinks=True)
    return fixture_repo._run_git(["rev-parse", "HEAD"], cwd=dest_dir, timeout_s=timeout_s).stdout.strip()


def render_inner_prompt(brief: str, changed_paths: Sequence[str]) -> str:
    return (
        "Implement the change described between the BEGIN and END lines below in this repository. "
        f"The original change touched these paths: {', '.join(changed_paths)}. "
        "Leave your changes in the working tree, uncommitted. "
        "Do not install packages or run anything that needs network access. "
        "When you finish, reply with the paths you changed.\n\n"
        f"{BEGIN_DESCRIPTION_LINE}\n{brief}\n{END_DESCRIPTION_LINE}"
    )


def build_inner_prompt(task: ReplayTask) -> str:
    return render_inner_prompt(task.brief, task.changed_paths)


# --- git reads ---------------------------------------------------------------


def _git(repo: Path, *args: str, input_bytes: bytes | None = None) -> bytes:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, input=input_bytes, env=isolated_git_environment(),
    ).stdout


def _read_objects(repo: Path, oids: Sequence[str]) -> dict[str, bytes]:
    """One `git cat-file --batch` call. Raises MissingObjectError for an object the repository does not hold."""
    unique_oids = list(dict.fromkeys(oids))
    if not unique_oids:
        return {}
    reply = _git(repo, "cat-file", "--batch", input_bytes="".join(f"{oid}\n" for oid in unique_oids).encode())
    objects: dict[str, bytes] = {}
    position = 0
    for oid in unique_oids:
        header_end = reply.index(b"\n", position)
        header = reply[position:header_end].decode().split(" ")
        position = header_end + 1
        if header[0] != oid or len(header) < 2:
            raise ValueError(f"git cat-file --batch answered {header!r} for {oid}")
        if header[1] == "missing":
            raise MissingObjectError(oid)
        size = int(header[2])
        objects[oid] = reply[position:position + size]
        position += size + 1
    return objects


@dataclass(frozen=True)
class TreeEntry:
    mode: str
    oid: str
    size: int
    path: str


def _list_tree(repo: Path, commit: str) -> list[TreeEntry]:
    """`git ls-tree -r -l -z`. With `-z` git prints each path verbatim instead of quoting an unusual
    name. A non-blob entry such as a submodule's gitlink prints `-` for its size, which counts as zero."""
    listing = _git(repo, "ls-tree", "-r", "-l", "-z", commit)
    entries = []
    for record in listing.decode("utf-8", errors="surrogateescape").split("\0"):
        if not record:
            continue
        metadata, path = record.split("\t", 1)
        mode, _object_type, oid, size = metadata.split()
        entries.append(TreeEntry(mode=mode, oid=oid, size=0 if size == "-" else int(size), path=path))
    return entries


def _changed_entries(
    repo: Path, base: str, commit: str, paths: Sequence[str] = (),
) -> tuple[str, list[comment_metrics.ChangedEntry]]:
    pathspec = ["--", *paths] if paths else []
    raw_listing = _git(
        repo, "--literal-pathspecs", "diff", "--raw", "-z", "--no-abbrev", *_STABLE_DIFF_ARGS, base, commit, *pathspec,
    ).decode("utf-8", errors="surrogateescape")
    return raw_listing, comment_metrics.parse_raw_listing(raw_listing)


def _is_directory_or_link_entry(entry: comment_metrics.ChangedEntry) -> bool:
    """Whether the entry is, on either side of the change, anything but a regular file. A submodule's
    gitlink extracts as a directory."""
    return any(
        mode not in comment_metrics.REGULAR_FILE_MODES and mode != _ABSENT_MODE for mode in (entry.old_mode, entry.new_mode)
    )


def _path_is_unsafe_in_prompt(path: str) -> bool:
    """The inner prompt lists paths comma-separated, and a path that does not round-trip through
    UTF-8 would not survive the task file."""
    try:
        path.encode("utf-8")
    except UnicodeEncodeError:
        return True
    return "," in path or any(ord(character) < 0x20 or character in '\x7f"\\' for character in path)


# --- base-tree refusals ------------------------------------------------------


def base_tree_refusal(path: str, link_target: str | None) -> SkipReason | None:
    """The reason extraction refuses a tree entry at `path`, or None when it extracts. `link_target`
    is a symlink's target text and None for any other entry. It mirrors
    `fixture_repo._extract_archive` and tarfile's `data` filter by their lexical rules, so a link
    chain that only resolution refuses fails at the fixture build instead."""
    if fixture_repo._has_git_path_component(path):
        return SkipReason.GIT_PATH_COMPONENT
    if link_target is not None and fixture_repo._has_git_path_component(link_target):
        return SkipReason.GIT_PATH_COMPONENT
    first_component = fixture_repo._first_path_component_casefolded(path)
    if first_component == fixture_repo._BENCH_DIR_NAME:
        return SkipReason.BENCH_DIR_ENTRY
    if link_target is None:
        return None
    if first_component == fixture_repo._PROJECT_CONFIG_DIR_NAME:
        return SkipReason.CLAUDE_DIR_SYMLINK
    if posixpath.isabs(link_target):
        return SkipReason.ABSOLUTE_SYMLINK
    resolved_lexically = posixpath.normpath(posixpath.join(posixpath.dirname(path), link_target))
    if resolved_lexically == ".." or resolved_lexically.startswith("../"):
        return SkipReason.ESCAPING_SYMLINK
    return None


# --- base density ------------------------------------------------------------


def _base_density_entries(changed_paths: Sequence[str], base_entries: Sequence[TreeEntry]) -> list[TreeEntry]:
    """The base versions of the changed non-test regular files."""
    changed = set(changed_paths)
    return [
        entry for entry in base_entries
        if entry.path in changed
        and entry.mode in comment_metrics.REGULAR_FILE_MODES
        and not comment_metrics.is_test_path(entry.path)
    ]


def _base_density(density_entries: Sequence[TreeEntry], base_blobs: Mapping[str, bytes]) -> float | None:
    # A base `.py` file that `ast.parse` rejects counts its docstring lines as code, which lowers density.
    # An undecodable base file adds nothing to either side of the ratio.
    words = code_lines = 0
    for entry in density_entries:
        data = base_blobs[entry.oid]
        every_line = range(1, data.count(b"\n") + 2)
        classification = comment_metrics.classify_file(entry.path, data, every_line)
        words += sum(classification.words.values())
        code_lines += sum(line_class is LineClass.CODE for line_class in classification.line_classes.values())
    return words / code_lines if code_lines else None


# --- mining ------------------------------------------------------------------


@dataclass(frozen=True)
class _LogRecord:
    commit: str
    parents: tuple[str, ...]
    author_email: str
    committer_email: str
    message: str


@dataclass
class MiningReport:
    tasks: list[ReplayTask] = field(default_factory=list)
    skip_counts: Counter[str] = field(default_factory=Counter)
    author_counts: Counter[str] = field(default_factory=Counter)
    committer_counts: Counter[str] = field(default_factory=Counter)


def resolve_source_repo(path: Path) -> Path:
    """The real path of `path`, which must be the top level of a git work tree. A linked
    worktree's top level is accepted.

    Raises TaskSourceError naming `path` for:
    - A subdirectory: `git archive` run from there would build a fixture rooted there.
    - A non-repository path or a bare repository.
    - A partial clone: git fetches a missing object on demand, which would put untimed network
      I/O into mining and fixture builds.
    - A repository whose `info/attributes` sets an export attribute: it would reach every
      commit's archive."""
    resolved = Path(os.path.realpath(path))
    result = subprocess.run(
        ["git", "-C", str(resolved), "rev-parse", "--show-toplevel"],
        capture_output=True, env=isolated_git_environment(),
    )
    if result.returncode != 0:
        raise TaskSourceError(
            f"{path} is not the top level of a git work tree: git rev-parse exited {result.returncode}: "
            f"{result.stderr.decode(errors='replace').strip()}"
        )
    if os.path.realpath(os.fsdecode(result.stdout).rstrip("\n")) != str(resolved):
        raise TaskSourceError(f"{path} is not the top level of a git work tree")
    _refuse_partial_clone(path, resolved)
    _refuse_export_attributes_in_info_file(path, resolved)
    return resolved


def _refuse_partial_clone(path: Path, repo: Path, *, timeout_s: float | None = None) -> None:
    """Either key marks a repository that fetches missing objects on demand. A `--filter` clone made by
    Apple Git 2.50.1 sets `remote.<name>.promisor` and no `extensions.partialClone`. `git config`
    exits 1 for no match, so any other nonzero status refuses rather than reads as no match."""
    result = subprocess.run(
        ["git", "-C", str(repo), "config", "--get-regexp", r"^(extensions\.partialclone|remote\..*\.promisor)$"],
        capture_output=True, env=isolated_git_environment(), timeout=timeout_s,
    )
    if result.returncode not in (0, 1):
        raise TaskSourceError(f"{path}: git config exited {result.returncode}: {result.stderr.decode(errors='replace').strip()}")
    if result.stdout.strip():
        raise TaskSourceError(f"{path} is a partial clone, which fetches missing objects over the network")


def _refuse_export_attributes_in_info_file(path: Path, repo: Path, *, timeout_s: float | None = None) -> None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--git-path", "info/attributes"],
            check=True, capture_output=True, env=isolated_git_environment(), timeout=timeout_s,
        )
    except subprocess.CalledProcessError as exc:
        raise TaskSourceError(f"{path}: git rev-parse failed: {exc.stderr.decode(errors='replace').strip()}") from exc
    info_attributes = repo / os.fsdecode(result.stdout).rstrip("\n")
    try:
        content = info_attributes.read_bytes()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise TaskSourceError(f"{path}: cannot read {info_attributes}: {exc}") from exc
    if _holds_export_attribute(content):
        raise TaskSourceError(f"{path}: {info_attributes} sets an export attribute, which would reach every archive")


def _holds_export_attribute(content: bytes) -> bool:
    """Matches the text anywhere on a line, a comment or an unset form included. That skips more than
    git would apply but needs no attribute parser."""
    return any(attribute in content for attribute in _EXPORT_ATTRIBUTES)


def _read_log(repo: Path, limits: SelectionLimits) -> list[_LogRecord]:
    try:
        identifiers.validate_base_ref(limits.rev)
    except identifiers.InvalidIdentifierError as exc:
        raise TaskSourceError(str(exc)) from exc
    try:
        output = _git(
            repo, "log", "--first-parent", "--no-use-mailmap", "--no-show-signature", "-z", f"--format={_GIT_LOG_FORMAT}",
            "-n", str(limits.max_count), limits.rev, "--",
        )
    except subprocess.CalledProcessError as exc:
        raise TaskSourceError(f"{repo}: git log {limits.rev} failed: {exc.stderr.decode(errors='replace').strip()}") from exc
    records = []
    for commit, parents, author_email, committer_email, message in _log_fields(repo, output, _GIT_LOG_FIELD_COUNT):
        _refuse_control_characters(repo, commit, author=author_email, committer=committer_email)
        records.append(_LogRecord(commit, tuple(parents.split()), author_email, committer_email, message))
    return records


def _log_fields(repo: Path, output: bytes, field_count: int) -> list[tuple[str, ...]]:
    """The NUL-framed fields of `git log -z` output, one tuple of `field_count` fields per commit."""
    fields = output.decode("utf-8", errors="replace").split("\0")
    if fields[-1] == "":
        fields.pop()
    if len(fields) % field_count:
        raise TaskSourceError(f"{repo}: git log output holds {len(fields)} fields, not a multiple of {field_count}")
    return [tuple(fields[start:start + field_count]) for start in range(0, len(fields), field_count)]


def _refuse_control_characters(repo: Path, commit: str, **fields: str) -> None:
    """Raises TaskSourceError for an identity field holding a control character, which no honest email does."""
    for field_name, value in fields.items():
        if any(unicodedata.category(character) == "Cc" for character in value):
            raise TaskSourceError(f"{repo}: commit {commit} has a control character in its {field_name}: {value!r}")


def _merged_commits(repo: Path, record: _LogRecord) -> list[tuple[str, str]]:
    """The (author email, subject) of each commit a merge brought in, newest first. Raises
    TaskSourceError for a control character in an author email. A subject is read as it is."""
    first_parent, *merged_parents = record.parents
    output = _git(
        repo, "log", "--no-use-mailmap", "--no-show-signature", "-z", f"--format={_MERGED_COMMIT_LOG_FORMAT}",
        *merged_parents, f"^{first_parent}",
    )
    merged = []
    for author_email, subject in _log_fields(repo, output, _MERGED_COMMIT_LOG_FIELD_COUNT):
        _refuse_control_characters(repo, record.commit, merged_author=author_email)
        merged.append((author_email, subject))
    return merged


def _measure_change(
    repo: Path, base_commit: str, commit: str, changed_paths: Sequence[str],
) -> CommentMetrics | SkipReason:
    """The classifier run over the commit's diff of `changed_paths`, or why the diff cannot be measured."""
    # The binary skip comes from `--numstat`, which reads attributes from the source repo's checked-out work tree, so
    # re-mining from another checked-out branch may skip a different set.
    numstat = _git(
        repo, "--literal-pathspecs", "diff", "--numstat", "-z", *_STABLE_DIFF_ARGS, base_commit, commit,
        "--", *changed_paths,
    )
    if any(row.startswith("-\t-\t") for row in numstat.decode("utf-8", errors="surrogateescape").split("\0")):
        return SkipReason.BINARY
    raw_listing, entries = _changed_entries(repo, base_commit, commit, changed_paths)
    diff_text = _git(
        repo, "--literal-pathspecs", "-c", "core.quotePath=false", "diff", "-U0", "--inter-hunk-context=0", "--submodule=short",
        *_STABLE_DIFF_ARGS, *DIFF_TEXT_ARGS, "--src-prefix=a/", "--dst-prefix=b/", base_commit, commit, "--", *changed_paths,
    ).decode("utf-8", errors="replace")
    regular_entries = [entry for entry in entries if entry.new_mode in comment_metrics.REGULAR_FILE_MODES]
    try:
        post_change_blobs = _read_objects(repo, [entry.new_blob for entry in regular_entries])
    except MissingObjectError:
        return SkipReason.MISSING_OBJECT
    return comment_metrics.measure_diff(
        diff_text, raw_listing, {entry.path: post_change_blobs[entry.new_blob] for entry in regular_entries},
    )


def _inspect_base_tree(repo: Path, base_commit: str, limits: SelectionLimits) -> list[TreeEntry] | SkipReason:
    """The base tree's entries, or the first bound, export-attribute or extraction refusal the tree hits."""
    # Known boundary: the entry bound is checked after the whole listing is read, and changed blobs have no
    # size ceiling. Both are acceptable at the interactive, local scale this miner runs at.
    base_entries = _list_tree(repo, base_commit)
    base_tree_bytes = sum(entry.size for entry in base_entries)
    if base_tree_bytes > limits.max_base_mb * BYTES_PER_MEGABYTE:
        return SkipReason.BASE_TREE_TOO_LARGE
    if len(base_entries) > limits.max_base_entries:
        return SkipReason.BASE_TREE_TOO_MANY_ENTRIES
    symlink_entries = [entry for entry in base_entries if entry.mode == _SYMLINK_MODE]
    attribute_entries = [
        entry for entry in base_entries
        if entry.mode in comment_metrics.REGULAR_FILE_MODES and posixpath.basename(entry.path) == _GITATTRIBUTES_NAME
    ]
    try:
        blobs = _read_objects(repo, [entry.oid for entry in [*symlink_entries, *attribute_entries]])
    except MissingObjectError:
        return SkipReason.MISSING_OBJECT
    if any(_holds_export_attribute(blobs[entry.oid]) for entry in attribute_entries):
        return SkipReason.EXPORT_ATTRIBUTES
    link_target_by_path = {
        entry.path: blobs[entry.oid].decode("utf-8", errors="surrogateescape") for entry in symlink_entries
    }
    for entry in base_entries:
        refusal = base_tree_refusal(entry.path, link_target_by_path.get(entry.path))
        if refusal is not None:
            return refusal
    return base_entries


def _evaluate_commit(
    repo: Path, record: _LogRecord, limits: SelectionLimits, trusted_authors: Collection[str],
) -> ReplayTask | SkipReason:
    if not record.parents:
        return SkipReason.NO_PARENT
    if record.author_email not in trusted_authors:
        return SkipReason.UNTRUSTED_AUTHOR
    brief = record.message.strip()
    if len(record.parents) > 1:
        merged = _merged_commits(repo, record)
        if any(author_email not in trusted_authors for author_email, _subject in merged):
            return SkipReason.UNTRUSTED_MERGED_AUTHOR
        brief += "\n\nMerged commits:\n" + "\n".join(f"- {subject}" for _author_email, subject in merged)
    if any(line.strip() in _RESERVED_BRIEF_LINES for line in brief.splitlines()):
        return SkipReason.RESERVED_BRIEF_LINE

    base_commit = record.parents[0]
    _, all_entries = _changed_entries(repo, base_commit, record.commit)
    changed_paths = [
        entry.path for entry in all_entries
        if not is_pruned_path(entry.path, is_directory_or_link=_is_directory_or_link_entry(entry))
    ]
    if len(changed_paths) > limits.max_files:
        return SkipReason.TOO_MANY_FILES
    if any(_path_is_unsafe_in_prompt(path) for path in changed_paths):
        return SkipReason.UNSAFE_PATH
    if not changed_paths:
        return SkipReason.TOO_FEW_CODE_LINES

    metrics = _measure_change(repo, base_commit, record.commit, changed_paths)
    if isinstance(metrics, SkipReason):
        return metrics
    code_lines = metrics.line_counts["non_test"][LineClass.CODE.value]
    if code_lines < limits.min_code_lines:
        return SkipReason.TOO_FEW_CODE_LINES
    if code_lines > limits.max_code_lines:
        return SkipReason.TOO_MANY_CODE_LINES
    if len(render_inner_prompt(brief, changed_paths)) > limits.max_prompt_chars:
        return SkipReason.PROMPT_TOO_LONG

    base_entries = _inspect_base_tree(repo, base_commit, limits)
    if isinstance(base_entries, SkipReason):
        return base_entries
    density_entries = _base_density_entries(changed_paths, base_entries)
    try:
        base_blobs = _read_objects(repo, [entry.oid for entry in density_entries])
    except MissingObjectError:
        return SkipReason.MISSING_OBJECT
    return ReplayTask(
        id=make_task_id(repo, record.commit),
        source_repo=str(repo),
        base_commit=base_commit,
        commit=record.commit,
        brief=brief,
        changed_paths=tuple(changed_paths),
        author_email=record.author_email,
        committer_email=record.committer_email,
        reference_metrics=metrics,
        base_density=_base_density(density_entries, base_blobs),
        base_tree_bytes=sum(entry.size for entry in base_entries),
        base_tree_entries=len(base_entries),
    )


def mine_tasks(
    source_repos: Sequence[Path],
    trusted_authors: Collection[str],
    limits: SelectionLimits = DEFAULT_LIMITS,
    *,
    progress: Callable[[str], None] = print,
) -> MiningReport:
    """Walks each repo's first-parent history newest first and keeps up to `limits.max_tasks`
    qualifying commits per repo. `progress` receives a line for each repo every
    `PROGRESS_INTERVAL_COMMITS` commits examined and when the repo finishes.

    Raises TaskSourceError for:
    - No trusted author, or an empty one.
    - A source repo that is not a work tree's top level.
    - Two source repos that resolve to one path.
    - One commit examined from two source repos, such as a linked worktree and its main checkout
      or a clone and its origin. The task ID hashes the repo path, so the commit would enter the
      task set twice under two IDs.
    - An invalid or unknown `limits.rev`, or a `git log` that fails.
    - A control character in an author, committer or merged-author email.
    - A partial clone, or a repo whose `info/attributes` sets an export attribute, as
      `resolve_source_repo` refuses.
    - A commit whose git read or parse fails, naming the repo and commit."""
    if not trusted_authors or "" in trusted_authors:
        raise TaskSourceError("--trusted-author must name at least one non-empty author email")
    resolved_repos = [resolve_source_repo(path) for path in source_repos]
    repeated_repos = sorted(str(repo) for repo, count in Counter(resolved_repos).items() if count > 1)
    if repeated_repos:
        raise TaskSourceError(f"--source-repo names the same repository more than once: {repeated_repos}")
    report = MiningReport()
    repo_by_examined_commit: dict[str, Path] = {}
    for repo in resolved_repos:
        examined = kept = 0
        for record in _read_log(repo, limits):
            if kept >= limits.max_tasks:
                break
            first_repo = repo_by_examined_commit.setdefault(record.commit, repo)
            if first_repo != repo:
                raise TaskSourceError(f"commit {record.commit} is examined from both {first_repo} and {repo}")
            examined += 1
            report.author_counts[record.author_email] += 1
            report.committer_counts[record.committer_email] += 1
            try:
                outcome = _evaluate_commit(repo, record, limits, trusted_authors)
            except TaskSourceError:
                raise
            except subprocess.CalledProcessError as exc:
                raise TaskSourceError(
                    f"{repo}: commit {record.commit}: git exited {exc.returncode}: {exc.stderr.decode(errors='replace').strip()}"
                ) from exc
            except ValueError as exc:
                raise TaskSourceError(f"{repo}: commit {record.commit}: {exc}") from exc
            if isinstance(outcome, SkipReason):
                report.skip_counts[outcome.value] += 1
            else:
                report.tasks.append(outcome)
                kept += 1
            if examined % PROGRESS_INTERVAL_COMMITS == 0:
                progress(f"{repo}: examined {examined} commits, kept {kept}")
        progress(f"{repo}: finished, examined {examined} commits, kept {kept}")
    return report


def format_mining_summary(report: MiningReport) -> list[str]:
    """The kept and skipped counts, the largest kept tasks, and every raw author and committer identity."""
    lines = [f"kept {len(report.tasks)} tasks, skipped {sum(report.skip_counts.values())} commits"]
    lines.extend(f"  skipped {reason}: {count}" for reason, count in sorted(report.skip_counts.items()))
    lines.append(f"kept tasks with a null base density: {sum(task.base_density is None for task in report.tasks)}")
    if report.tasks:
        for label, key in (("size", lambda task: task.base_tree_bytes), ("entry count", lambda task: task.base_tree_entries)):
            largest = max(report.tasks, key=key)
            lines.append(
                f"largest kept task by base-tree {label}: {largest.id} "
                f"({largest.base_tree_bytes} bytes, {largest.base_tree_entries} entries)"
            )
    for role, counts in (("author", report.author_counts), ("committer", report.committer_counts)):
        lines.append(f"{role} identities among the commits examined:")
        lines.extend(f"  {email!r}: {count}" for email, count in counts.most_common())
    return lines
