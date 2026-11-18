"""Ceiling on the PR-task count of the comments-stripped A/B study.

The design page, docs/case-studies/comments-stripped-ab.md, defines the rules here:
"Eligible pull requests", "Prompt", "Filters" and "Margin and pre-count disclosure".
The script reads git history and GitHub through `gh` and nothing else. It reads no test result
and no outcome, writes nothing to GitHub, and calls no model. It skips the conditions in
SKIPPED_CONDITIONS, and its why-comment test checks only that a comment is present, where the page's
filter needs a rater to judge what the comment states. `judge_why_comment` holds the per-language rules
and names the files it cannot judge, which keep their task.
Every count is therefore an upper bound, and the last stage's count is the ceiling.

GraphQL answers are cached by default outside the repository, in the user's state directory, and may hold
issue text. Git and `gh` calls carry no timeout, since a person runs the script by hand and Ctrl-C ends
a hung call.
"""

from __future__ import annotations

import argparse
import ast
import io
import json
import os
import re
import subprocess
import sys
import tokenize
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath

from review_bench.local_git import DIFF_TEXT_ARGS, isolated_git_environment

STUDY_ARTIFACT_PATHS = (
    "docs/case-studies/comments-stripped-ab.md",
    ".claude/plans/comment-ab-experiment-design.md",
)
ENVIRONMENT_FILE_PATHS = frozenset({"requirements-dev.txt", "pyproject.toml", ".github/workflows/tests.yml"})
TEST_DIRECTORY_NAMES = frozenset({"tests", "fixtures"})
CONFTEST_FILE_NAME = "conftest.py"
TEST_MODULE_PREFIX = "test_"
PYTHON_SUFFIX = ".py"
SHELL_SUFFIX = ".sh"
# The interpreters whose shebang marks a file as shell or Python whatever its name. The design page lists none.
# The parser reads `env` with flags and `NAME=value` words only, so `env -u NAME bash` and an interpreter outside
# these sets and `python*` read as not a script.
SHELL_INTERPRETERS = frozenset({"sh", "bash", "dash", "zsh", "ksh"})
_PYTHON_INTERPRETER_RE = re.compile(r"python[0-9.]*")

# The page's "Margin and pre-count disclosure": below this many run tasks, a zero estimate cannot
# give U < 0.15 at psi = 0.20 and the pessimistic end.
MIN_RUN_TASKS_FOR_ZERO_ESTIMATE = 34.1

# What a "PR task" must pass on the page, and the script cannot evaluate.
SKIPPED_CONDITIONS = (
    "the fail-to-pass condition",
    "the specification filter",
    "the stripping checks",
    "the loaded-configuration check",
)

# Comments both arms keep. Each pattern is matched against a comment's text from its `#`.
# The list grows from stripping-check failures; the page's lock freezes it.
PRESERVED_COMMENT_PATTERNS = (
    re.compile(r"#!"),  # shebang
    re.compile(r"#\s*shellcheck\b"),
    re.compile(r"#\s*noqa\b", re.IGNORECASE),
    re.compile(r"#\s*type:"),
    re.compile(r"#\s*fmt:"),
    re.compile(r"#\s*pragma\b"),
    re.compile(r"#\s*(?:hook-class|tier-threat-model):"),
)
# PEP 263 honors an encoding cookie only on the first two lines of a file, so a later `coding:` is prose.
ENCODING_COOKIE_LAST_LINE = 2
_ENCODING_COOKIE_RE = re.compile(r"#.*?coding[:=]\s*[-\w.]+")

_PULL_REQUEST_SUFFIX_RE = re.compile(r"\(#(\d+)\)\s*$", re.ASCII)
_GH_ISSUE_RE = re.compile(r"\bGH-(\d+)(?!\w)", re.IGNORECASE | re.ASCII)
_CLOSING_KEYWORD_ISSUE_RE = re.compile(
    r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d+)(?!\w)", re.IGNORECASE | re.ASCII
)
# A branch name with none of these is a common word, such as `main`, that text can use without naming the branch.
_BRANCH_NAME_SEPARATOR_OR_DIGIT_RE = re.compile(r"[/_0-9-]")
_COMMIT_ABBREVIATION_RE = re.compile(r"(?<![\w-])[0-9a-f]{7,40}(?![\w-])")
_HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+\d+(?:,(\d+))? @@")
# A `#` at a line start or after whitespace or a shell control character. Quotes, heredocs and `${...}` are not
# modeled, so this accepts a superset of real comments.
_SHELL_COMMENT_CANDIDATE_RE = re.compile(r"(?:^|[\s;&|(])#")
_OBJECT_ID_RE = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")  # SHA-1 or SHA-256
_CAT_FILE_OBJECT_HEADER_RE = re.compile(rf"{_OBJECT_ID_RE.pattern} \w+ (\d+)")
# `git cat-file --batch` reads one spec per line, so a line break or carriage return in a path would split or alter its spec.
_LINE_BREAK_RE = re.compile(r"[\r\n]")
_DOCSTRING_OWNER_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)

# The repository name and host `gh repo view` must resolve from the checkout's remote, because a count may be
# published only when it is computed over this repository's own corpus (docs/private-project-redaction.md,
# "Publishing a tooling measurement"). The history under `--ref` is not verified to come from it.
STUDY_REPOSITORY = "jcdendrite/claude-config"
GITHUB_HOSTNAME = "github.com"
STUDY_REPOSITORY_URL = f"https://{GITHUB_HOSTNAME}/{STUDY_REPOSITORY}"
# `gh` reads these to pick another repository or host than the checkout's remote.
_GH_TARGET_OVERRIDE_VARIABLES = frozenset({"GH_REPO", "GH_HOST"})

# GitHub requires `first` within 1-100 and caps one call at 500,000 total nodes:
# https://docs.github.com/en/graphql/overview/rate-limits-and-query-limits-for-the-graphql-api
GRAPHQL_PAGE_SIZE = 100
# Aliased PRs or issues per query, small enough that the nested edit nodes of one query stay far under GitHub's node limit.
GRAPHQL_BATCH_SIZE = 25
# Any value above 1 gives the same exclusion for a PR closing several issues, so no GitHub limit applies.
CLOSING_REFERENCES_PER_PULL_REQUEST = 20
GRAPHQL_NOT_FOUND_ERROR_TYPE = "NOT_FOUND"

DEFAULT_CACHE_DIR = Path.home() / ".local" / "state" / "claude-config" / "comment-ab-pool-count"

FIRST_REVISION_UNEDITED = "unedited"
FIRST_REVISION_OLDEST_EDIT = "oldest-edit-at-creation"
FIRST_REVISION_UNRECOVERABLE = "unrecoverable"
FIRST_REVISION_MISSING = "missing"
_RECOVERED_REASONS = frozenset({FIRST_REVISION_UNEDITED, FIRST_REVISION_OLDEST_EDIT})


class GitError(RuntimeError):
    """A git command that exited non-zero or printed output the script cannot read."""


class GitHubError(RuntimeError):
    """A `gh api graphql` call that returned no data."""


@dataclass(frozen=True)
class CommitRecord:
    sha: str
    parents: tuple[str, ...]
    subject: str
    changes: tuple[tuple[str, str], ...]  # (status letter, path) against the first parent

    @property
    def paths(self) -> tuple[str, ...]:
        return tuple(path for _, path in self.changes)


@dataclass(frozen=True)
class PullRequestInfo:
    head_ref: str | None
    closing_issues: tuple[tuple[str, int], ...]  # (owner/name, issue number)
    title: str | None  # None when GitHub holds no pull request with this number


@dataclass(frozen=True)
class RepositoryIdentity:
    name_with_owner: str
    url: str


@dataclass(frozen=True)
class FirstRevision:
    text: str | None
    reason: str


@dataclass(frozen=True)
class Region:
    """`touch_start`..`last_line` is what a diff must overlap to touch the region. It starts at the first line of the
    comment block directly above the function, so a diff touching only that comment touches the region."""

    touch_start: int
    last_line: int
    holds_comment: bool


@dataclass(frozen=True)
class WhyCommentResult:
    kept: bool
    new_files_only: bool
    unparsable_files: int = 0


@dataclass(frozen=True)
class StageResult:
    id: str
    name: str
    count: int
    detail: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class PoolCeiling:
    stages: tuple[StageResult, ...]
    informational: Mapping[str, int]
    surviving_pull_requests: tuple[int, ...]

    @property
    def ceiling(self) -> int:
        return self.stages[-1].count


def is_test_file(path: str) -> bool:
    pure_path = PurePosixPath(path)
    in_test_directory = any(part in TEST_DIRECTORY_NAMES for part in pure_path.parts[:-1])
    return (
        in_test_directory
        or pure_path.name == CONFTEST_FILE_NAME
        or (pure_path.name.startswith(TEST_MODULE_PREFIX) and pure_path.name.endswith(PYTHON_SUFFIX))
    )


def is_shell_or_python_file(path: str) -> bool:
    """Eligibility and the region judgment match by suffix, so a script without one is outside this test even when
    it has a shebang. `judge_why_comment` counts such a modified script as unjudged."""
    return path.endswith((PYTHON_SUFFIX, SHELL_SUFFIX))


def has_shell_or_python_shebang(source: str) -> bool:
    """Whether the first line is a shebang naming a shell or Python interpreter, directly or through `env`."""
    first_line = source.split("\n", maxsplit=1)[0]
    if not first_line.startswith("#!"):
        return False
    words = first_line[2:].split()
    if words and PurePosixPath(words[0]).name == "env":
        words = [word for word in words[1:] if not word.startswith("-") and "=" not in word]
    if not words:
        return False
    interpreter = PurePosixPath(words[0]).name
    return interpreter in SHELL_INTERPRETERS or _PYTHON_INTERPRETER_RE.fullmatch(interpreter) is not None


def issues_named_in(text: str, pull_request_number: int) -> frozenset[int]:
    """Issue numbers `text` names by `GH-<n>` or a closing keyword before `#<n>`. The PR's own number is no issue."""
    named = {int(number) for number in _GH_ISSUE_RE.findall(text)}
    named |= {int(number) for number in _CLOSING_KEYWORD_ISSUE_RE.findall(text)}
    named.discard(pull_request_number)
    return frozenset(named)


def prompt_issue_for(
    title: str, pull_request_number: int, closing_issues: Sequence[tuple[str, int]], repository: str
) -> tuple[str, int] | None:
    """The (repository, number) of the prompt issue, or None when the PR is excluded."""
    title_issues = issues_named_in(title, pull_request_number)
    if title_issues:
        return (repository, next(iter(title_issues))) if len(title_issues) == 1 else None
    return closing_issues[0] if len(closing_issues) == 1 else None


def is_preserved_comment(comment_text: str, line_number: int) -> bool:
    if line_number <= ENCODING_COOKIE_LAST_LINE and _ENCODING_COOKIE_RE.match(comment_text):
        return True
    return any(pattern.match(comment_text) for pattern in PRESERVED_COMMENT_PATTERNS)


def names_pull_request_branch_or_commit(
    text: str, pull_request_number: int, repository: str, head_ref: str | None, is_commit: Callable[[str], bool]
) -> bool:
    """Whether `text` holds a reference that cannot be a coincidence. A bare common-word branch, such as `main`,
    and a `/pull/<n>` link not qualified to this repository are ambiguous and never match, so they leave the task in."""
    pull_request_reference = re.compile(
        rf"(?<![\w/])#{pull_request_number}(?!\w)"
        rf"|{re.escape(GITHUB_HOSTNAME)}/{re.escape(repository)}/pull/{pull_request_number}(?!\d)",
        re.IGNORECASE,
    )
    if pull_request_reference.search(text):
        return True
    if (
        head_ref
        and _BRANCH_NAME_SEPARATOR_OR_DIGIT_RE.search(head_ref)
        and re.search(rf"(?<![\w/.-]){re.escape(head_ref)}(?![\w/-])", text)
    ):
        return True
    for candidate in _COMMIT_ABBREVIATION_RE.findall(text):
        mixes_digits_and_letters = any(char.isdigit() for char in candidate) and any(char.isalpha() for char in candidate)
        if mixes_digits_and_letters and is_commit(candidate):
            return True
    return False


def first_revision_from_graphql(issue: Mapping, edits: Sequence[Mapping]) -> FirstRevision:
    """`edits` holds every `userContentEdits` node of the issue, in any order."""
    if not edits:
        if issue["lastEditedAt"] is None:
            return FirstRevision(issue["body"], FIRST_REVISION_UNEDITED)
        return FirstRevision(None, FIRST_REVISION_UNRECOVERABLE)
    oldest = min(edits, key=lambda edit: _parse_timestamp(edit["editedAt"]))
    if _parse_timestamp(oldest["editedAt"]) != _parse_timestamp(issue["createdAt"]) or oldest["diff"] is None:
        return FirstRevision(None, FIRST_REVISION_UNRECOVERABLE)
    return FirstRevision(oldest["diff"], FIRST_REVISION_OLDEST_EDIT)


def _parse_timestamp(timestamp: str) -> datetime:
    return datetime.fromisoformat(timestamp)


# --- Region detection -------------------------------------------------------------------------


def _extend_over_comments_above(first_line: int, standalone_comment_lines: Iterable[int]) -> int:
    standalone = set(standalone_comment_lines)
    while first_line - 1 in standalone:
        first_line -= 1
    return first_line


def python_regions(source: str) -> list[Region] | None:
    """One region per function, async function and method, nested ones included. None when `source` does not parse."""
    try:
        tree = ast.parse(source)
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (SyntaxError, ValueError, tokenize.TokenError):
        return None
    standalone_lines: set[int] = set()
    counted_lines: set[int] = set()
    for token in tokens:
        if token.type != tokenize.COMMENT:
            continue
        if not token.line[: token.start[1]].strip():
            standalone_lines.add(token.start[0])
        if not is_preserved_comment(token.string, token.start[0]):
            counted_lines.add(token.start[0])
    regions = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        first_line = min([node.lineno, *(decorator.lineno for decorator in node.decorator_list)])
        comment_first_line = _extend_over_comments_above(first_line, standalone_lines)
        holds_docstring = any(
            isinstance(inner, _DOCSTRING_OWNER_NODES) and ast.get_docstring(inner, clean=False) is not None
            for inner in ast.walk(node)
        )
        holds_comment = holds_docstring or any(line in counted_lines for line in range(comment_first_line, node.end_lineno + 1))
        regions.append(Region(comment_first_line, node.end_lineno, holds_comment))
    return regions


def _git_lines(text: str) -> list[str]:
    """Lines as git numbers them: only "\\n" ends one, where `str.splitlines` also splits on \\x0b, \\x0c, \\x85 and U+2028."""
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    return [line.removesuffix("\r") for line in lines]


def shell_source_holds_comment(source: str) -> bool:
    """Whether `source` has a `#` comment candidate on any line other than a first-line shebang."""
    return any(
        _SHELL_COMMENT_CANDIDATE_RE.search(line) and not (line_number == 1 and line.startswith("#!"))
        for line_number, line in enumerate(_git_lines(source), start=1)
    )


# --- Diff reading -----------------------------------------------------------------------------


def parse_changed_base_lines(diff_text: str) -> dict[str, set[int]]:
    """Base-side line numbers each file's diff changes, from a `git diff -U0` text.

    A pure insertion after base line `n` counts as touching `n` and `n + 1`, so an insertion at a region's edge
    counts for both neighbors, which keeps the ceiling an upper bound. A file the diff adds has no entry.
    """
    changed: dict[str, set[int]] = {}
    current_path: str | None = None
    old_path: str | None = None
    remaining_old = remaining_new = 0
    for line in _git_lines(diff_text):
        if remaining_old > 0 or remaining_new > 0:
            if line.startswith("-"):
                remaining_old -= 1
            elif line.startswith("+"):
                remaining_new -= 1
            continue
        if line.startswith("\\"):
            continue
        if line.startswith("--- "):
            old_name = line[4:].split("\t", maxsplit=1)[0]
            # git quotes non-ASCII or special-character paths. The parser skips one, and the judge counts it as unjudged.
            old_path = None if old_name == "/dev/null" or old_name.startswith('"') else old_name.removeprefix("a/")
        elif line.startswith("+++ "):
            current_path = old_path
            if current_path is not None:
                changed.setdefault(current_path, set())
        else:
            hunk = _HUNK_HEADER_RE.match(line)
            if hunk is None or current_path is None:
                continue
            start = int(hunk.group(1))
            old_count = 1 if hunk.group(2) is None else int(hunk.group(2))
            remaining_old = old_count
            remaining_new = 1 if hunk.group(3) is None else int(hunk.group(3))
            if old_count == 0:
                changed[current_path] |= {start, start + 1}
            else:
                changed[current_path] |= set(range(start, start + old_count))
    return changed


def judge_why_comment(
    changes: Sequence[tuple[str, str]],
    changed_base_lines: Mapping[str, set[int]],
    base_sources: Mapping[str, str | None],
) -> WhyCommentResult:
    """Applies the page's why-comment filter as a presence test: the task is kept when a touched region holds a
    comment or docstring outside the preserve list. A region is a function of a `*.py` file, test files included.
    A modified `*.sh` file holds a comment when its base source has any comment candidate besides the shebang,
    wherever the diff lands.

    A task is also kept, and the file counted as unjudged, for each touched file that is:
    - a `*.py` file whose base source does not parse, or any judged file whose base blob is missing,
    - a modified `*.py` or `*.sh` file with no entry in `changed_base_lines`, as a mode-only change or a quoted path gives,
    - a modified file with no `*.py` or `*.sh` suffix whose base blob is missing or whose base source starts with a
      shell or Python shebang.

    The result does not depend on the order of `changed_base_lines`."""
    non_test_changes = [(status, path) for status, path in changes if is_shell_or_python_file(path) and not is_test_file(path)]
    # A script without a `*.py` or `*.sh` suffix can hold a commented region the suffix test cannot see, so it
    # keeps the task even when every suffix file is new.
    unjudged_scripts = sum(
        1
        for status, path in changes
        if status != "A"
        and not is_shell_or_python_file(path)
        and ((source := base_sources.get(path)) is None or has_shell_or_python_shebang(source))
    )
    if unjudged_scripts == 0 and all(status == "A" for status, _ in non_test_changes):
        return WhyCommentResult(kept=False, new_files_only=True)
    unparsable_files = unjudged_scripts + sum(
        1
        for status, path in changes
        if status != "A" and is_shell_or_python_file(path) and path not in changed_base_lines
    )
    touches_commented_region = False
    for path, changed_lines in changed_base_lines.items():
        if not is_shell_or_python_file(path):
            continue
        source = base_sources.get(path)
        if source is None:
            unparsable_files += 1
            continue
        if path.endswith(SHELL_SUFFIX):
            touches_commented_region = touches_commented_region or shell_source_holds_comment(source)
            continue
        regions = python_regions(source)
        if regions is None:
            unparsable_files += 1
            continue
        touches_commented_region = touches_commented_region or any(
            region.holds_comment and any(region.touch_start <= line <= region.last_line for line in changed_lines)
            for region in regions
        )
    return WhyCommentResult(
        kept=touches_commented_region or unparsable_files > 0, new_files_only=False, unparsable_files=unparsable_files
    )


# --- Stage gating -----------------------------------------------------------------------------


@dataclass(frozen=True)
class PoolSources:
    """What the stages read from outside the commit list, injected so the gating runs without git or GitHub."""

    repository: str
    base_predates_study: Callable[[str], bool]
    fetch_pull_requests: Callable[[Sequence[int]], Mapping[int, PullRequestInfo]]
    fetch_first_revisions: Callable[[Sequence[int]], Mapping[int, FirstRevision]]
    is_commit: Callable[[str], bool]
    judge_why_comment: Callable[[CommitRecord], WhyCommentResult]


def pull_request_number(commit: CommitRecord) -> int | None:
    """The `(#N)` suffix of a one-parent commit's subject, which is how a squash merge names its PR."""
    if len(commit.parents) != 1:
        return None
    suffix = _PULL_REQUEST_SUFFIX_RE.search(commit.subject)
    return int(suffix.group(1)) if suffix else None


def compute_ceiling(commits: Sequence[CommitRecord], sources: PoolSources) -> PoolCeiling:
    squash_merges = [(commit, number) for commit in commits if (number := pull_request_number(commit)) is not None]
    pull_requests = sources.fetch_pull_requests(sorted({number for _, number in squash_merges}))

    prompt_issued = []
    for commit, number in squash_merges:
        info = pull_requests.get(number, PullRequestInfo(None, (), None))
        # GitHub holds no pull request for a number it never served, so the commit subject stands in for its title.
        title = commit.subject if info.title is None else info.title
        issue = prompt_issue_for(title, number, info.closing_issues, sources.repository)
        if issue is not None:
            prompt_issued.append((commit, number, issue))

    with_code_change = [
        entry for entry in prompt_issued if any(is_shell_or_python_file(p) and not is_test_file(p) for p in entry[0].paths)
    ]
    without_environment_change = [
        entry for entry in with_code_change if ENVIRONMENT_FILE_PATHS.isdisjoint(entry[0].paths)
    ]
    before_study = [entry for entry in without_environment_change if sources.base_predates_study(entry[0].parents[0])]

    recoverable_issue_numbers = sorted({issue[1] for _, _, issue in before_study if issue[0] == sources.repository})
    first_revisions = sources.fetch_first_revisions(recoverable_issue_numbers)
    unrecoverable = outside_repository = names_pr_or_commit = 0
    first_revision_kept = []
    for entry in before_study:
        commit, number, (issue_repository, issue_number) = entry
        # A task whose prompt issue's first revision the script cannot recover stays in, since the names filter cannot run on it.
        if issue_repository != sources.repository:
            outside_repository += 1
            first_revision_kept.append(entry)
            continue
        revision = first_revisions.get(issue_number, FirstRevision(None, FIRST_REVISION_MISSING))
        if revision.reason not in _RECOVERED_REASONS or revision.text is None:
            unrecoverable += 1
            first_revision_kept.append(entry)
            continue
        head_ref = pull_requests.get(number, PullRequestInfo(None, (), None)).head_ref
        if names_pull_request_branch_or_commit(revision.text, number, sources.repository, head_ref, sources.is_commit):
            names_pr_or_commit += 1
            continue
        first_revision_kept.append(entry)

    also_changes_test_file = sum(1 for commit, _, _ in first_revision_kept if any(is_test_file(p) for p in commit.paths))

    new_files_only = no_region_comment = unparsable_files = 0
    surviving = []
    for commit, number, _ in first_revision_kept:
        verdict = sources.judge_why_comment(commit)
        # Counts files, not tasks, and sums them over every judged task.
        unparsable_files += verdict.unparsable_files
        if verdict.new_files_only:
            new_files_only += 1
        elif not verdict.kept:
            no_region_comment += 1
        else:
            surviving.append(number)

    # Stage ids are this script's own, not the page's.
    stages = (
        StageResult("1", "first-parent commits merged as one commit with a (#N) suffix", len(squash_merges)),
        StageResult("2", "PR title or closing reference names exactly one prompt issue", len(prompt_issued)),
        StageResult("3", "diff changes a non-test *.sh or *.py file", len(with_code_change)),
        StageResult("4", "diff changes none of the environment files", len(without_environment_change)),
        StageResult("5", "base predates the first study artifact", len(before_study)),
        StageResult(
            "6",
            "prompt issue's first revision, where recovered, names no PR, branch or commit",
            len(first_revision_kept),
            {"names_pr_branch_or_commit": names_pr_or_commit},
        ),
        StageResult(
            "8",
            "a touched region holds a comment or docstring (proxy for the why-comment filter)",
            len(surviving),
            {
                "non_test_changes_all_new_files": new_files_only,
                "no_touched_region_holds_comment": no_region_comment,
                "unparsable_base_files_unjudged": unparsable_files,
            },
        ),
    )
    return PoolCeiling(
        stages=stages,
        informational={
            "stage_6_first_revision_not_recovered": unrecoverable,
            "stage_6_prompt_issue_outside_repository": outside_repository,
            "stage_6_also_changes_a_test_file": also_changes_test_file,
        },
        surviving_pull_requests=tuple(surviving),
    )


def format_report(ceiling: PoolCeiling, context: Mapping[str, str]) -> str:
    lines = [
        "CEILING on the PR-task count: reads no test result and no outcome. Every figure is an upper bound.",
        f"Skipped conditions: {', '.join(SKIPPED_CONDITIONS)}.",
        *(f"{key}: {value}" for key, value in context.items()),
        "",
        "stage  count  name",
    ]
    for stage in ceiling.stages:
        lines.append(f"{stage.id:<5}  {stage.count:>5}  {stage.name}")
        lines.extend(f"                 {key}: {value}" for key, value in stage.detail.items())
    lines.extend(f"info   {value:>5}  {key} (not a gate)" for key, value in ceiling.informational.items())
    lines.append("")
    lines.append(comparison_line(ceiling.ceiling))
    return "\n".join(lines)


def comparison_line(ceiling_count: int) -> str:
    relation = "at or above" if ceiling_count >= MIN_RUN_TASKS_FOR_ZERO_ESTIMATE else "below"
    verdict = (
        "the ceiling alone does not rule the study out; the lock's count is the one that binds"
        if relation == "at or above"
        else "no run count can reach it"
    )
    return f"CEILING {ceiling_count} is {relation} the {MIN_RUN_TASKS_FOR_ZERO_ESTIMATE} threshold: {verdict}"


def report_json(ceiling: PoolCeiling, context: Mapping[str, str]) -> str:
    return json.dumps(
        {
            "label": "CEILING",
            "context": dict(context),
            "stages": [
                {"id": stage.id, "name": stage.name, "count": stage.count, "detail": dict(stage.detail)}
                for stage in ceiling.stages
            ],
            "informational": dict(ceiling.informational),
            "ceiling": ceiling.ceiling,
            "threshold": MIN_RUN_TASKS_FOR_ZERO_ESTIMATE,
            "ceiling_meets_threshold": ceiling.ceiling >= MIN_RUN_TASKS_FOR_ZERO_ESTIMATE,
            "surviving_pull_requests": list(ceiling.surviving_pull_requests),
        },
        indent=2,
    )


# --- Git ---------------------------------------------------------------------------------------


def _from_repository_root(pattern: str) -> str:
    """A pathspec matched from the repository root, so a `--repo` below the root reads the same paths."""
    return f":(top){pattern}"


def _run_git(repo: Path, args: Sequence[str], *, stdin: bytes | None = None) -> bytes:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args], env=isolated_git_environment(), input=stdin, capture_output=True, check=False
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode(errors="replace").strip()
        raise GitError(f"git {' '.join(args[:2])} exited {completed.returncode}: {detail}")
    return completed.stdout


def read_first_parent_commits(repo: Path, ref: str) -> list[CommitRecord]:
    # `-z` NUL-frames the fields and \x01 starts each commit, which assumes no subject or path holds a 0x01 byte.
    # A record that breaks the assumption fails the object-id check below instead of reaching a later git argv.
    output = _run_git(
        repo,
        ["log", "--first-parent", "--no-renames", "--name-status", "-z", "--format=%x01%H%x00%P%x00%s", "--end-of-options", ref],
    ).decode(errors="replace")
    commits = []
    for chunk in output.split("\x01")[1:]:
        fields = chunk.split("\0")
        if len(fields) < 3:
            raise GitError(f"git log printed a record with {len(fields)} fields, fewer than 3: {chunk[:80]!r}")
        sha, parents, subject, *change_fields = fields
        sha, parent_ids = sha.strip(), tuple(parents.split())
        for object_id in (sha, *parent_ids):
            if not _OBJECT_ID_RE.fullmatch(object_id):
                raise GitError(f"git log printed {object_id[:80]!r} where a commit id belongs")
        pairs = tuple(zip(change_fields[0::2], change_fields[1::2], strict=False))
        changes = tuple((status.strip(), path) for status, path in pairs if path)
        commits.append(CommitRecord(sha, parent_ids, subject, changes))
    return commits


def is_shallow_repository(repo: Path) -> bool:
    return _run_git(repo, ["rev-parse", "--is-shallow-repository"]).decode().strip() == "true"


def find_study_start_commit(repo: Path, ref: str) -> str | None:
    """The earliest first-parent commit adding any study artifact, or None when `ref` holds none."""
    output = _run_git(
        repo,
        [
            "log", "--first-parent", "--no-renames", "--diff-filter=A", "--format=%H",
            "--end-of-options", ref, "--", *(_from_repository_root(path) for path in STUDY_ARTIFACT_PATHS),
        ],
    ).decode().split()
    return output[-1] if output else None


def read_ancestors_before(repo: Path, commit: str) -> frozenset[str]:
    return frozenset(_run_git(repo, ["rev-list", commit]).decode().split()) - {commit}


def git_names_commit(repo: Path) -> Callable[[str], bool]:
    memo: dict[str, bool] = {}

    def is_commit(candidate: str) -> bool:
        if candidate not in memo:
            probe = subprocess.run(
                ["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", f"{candidate}^{{commit}}"],
                env=isolated_git_environment(),
                capture_output=True,
                check=False,
            )
            memo[candidate] = probe.returncode == 0
        return memo[candidate]

    return is_commit


def read_blobs(repo: Path, specs: Sequence[str]) -> dict[str, str | None]:
    """Contents of each `<rev>:<path>` through one `git cat-file --batch`, None for a missing one.
    A spec must hold no line break, since git answers one spec per line, and a response that misaligns raises."""
    if not specs:
        return {}
    output = _run_git(repo, ["cat-file", "--batch"], stdin="".join(f"{spec}\n" for spec in specs).encode())
    blobs: dict[str, str | None] = {}
    position = 0
    for spec in specs:
        header_end = output.find(b"\n", position)
        if header_end == -1:
            raise GitError(f"git cat-file printed {len(blobs)} responses for {len(specs)} specs")
        header = output[position:header_end].decode(errors="replace")
        position = header_end + 1
        if header == f"{spec} missing":
            blobs[spec] = None
            continue
        object_header = _CAT_FILE_OBJECT_HEADER_RE.fullmatch(header)
        if object_header is None:
            raise GitError(f"git cat-file printed {header[:80]!r} where an object header or a missing notice belongs")
        blob_end = position + int(object_header.group(1))
        if output[blob_end : blob_end + 1] != b"\n":
            raise GitError(f"git cat-file printed a blob that does not match its header {header[:80]!r}")
        blobs[spec] = output[position:blob_end].decode(errors="replace")
        position = blob_end + 1
    if position != len(output):
        raise GitError(f"git cat-file printed more than {len(specs)} responses")
    return blobs


def git_why_comment_judge(repo: Path) -> Callable[[CommitRecord], WhyCommentResult]:
    def judge(commit: CommitRecord) -> WhyCommentResult:
        base = commit.parents[0]
        diff_text = _run_git(
            repo,
            [
                "diff", "--no-renames", "-U0", "--src-prefix=a/", "--dst-prefix=b/", "--text", *DIFF_TEXT_ARGS,
                "--end-of-options", base, commit.sha, "--",
                _from_repository_root(f"*{PYTHON_SUFFIX}"), _from_repository_root(f"*{SHELL_SUFFIX}"),
            ],
        ).decode(errors="replace")
        changed_base_lines = parse_changed_base_lines(diff_text)
        # Each modified non-suffix blob is read whole to inspect one line, which suits a history whose largest is about 1 MB.
        script_candidates = [path for status, path in commit.changes if status != "A" and not is_shell_or_python_file(path)]
        paths = [*changed_base_lines, *script_candidates]
        specs = {path: f"{base}:{path}" for path in paths if not _LINE_BREAK_RE.search(path)}
        blobs = read_blobs(repo, list(specs.values()))
        # A path that cannot go on one `cat-file` line reads as an unreadable blob, which the judge keeps and counts.
        base_sources = {path: blobs[specs[path]] if path in specs else None for path in paths}
        return judge_why_comment(commit.changes, changed_base_lines, base_sources)

    return judge


# --- GitHub ------------------------------------------------------------------------------------


def _gh_environment() -> dict[str, str]:
    """`gh` runs git to discover the remote, so it gets the same ambient-state stripping as the script's own git calls."""
    return {name: value for name, value in isolated_git_environment().items() if name not in _GH_TARGET_OVERRIDE_VARIABLES}


def _graphql(repo: Path, query: str, variables: Mapping[str, str]) -> dict:
    """Runs a query and returns its payload, whose `data` is non-empty. A query can return data beside per-alias
    errors, such as a number that is not an issue, and `gh` exits non-zero then, so stdout decides and the exit
    status does not."""
    argv = ["gh", "api", "graphql", "--hostname", GITHUB_HOSTNAME, "-f", f"query={query}"]
    for name, value in variables.items():
        argv += ["-f", f"{name}={value}"]
    completed = subprocess.run(argv, cwd=repo, env=_gh_environment(), capture_output=True, encoding="utf-8", check=False)
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        payload = None
    if not isinstance(payload, dict) or not payload.get("data"):
        raise GitHubError(f"gh api graphql exited {completed.returncode}: {completed.stderr.strip()}")
    return payload


def _confirmed_repository_data(payload: Mapping, aliases: frozenset[str]) -> Mapping:
    """The `repository` data of a batch response. A null alias counts as absent only when a NOT_FOUND error's path
    names it. Any other error, or a null without that error, raises.
    Caching such an answer would make a transient or forbidden one permanent."""
    absent_aliases = set()
    for error in payload.get("errors") or []:
        path = error.get("path") or []
        names_alias = len(path) >= 2 and path[0] == "repository" and path[1] in aliases
        if error.get("type") != GRAPHQL_NOT_FOUND_ERROR_TYPE or not names_alias:
            raise GitHubError(f"graphql error {error.get('type')} at {path}: {error.get('message')}")
        absent_aliases.add(path[1])
    repository_data = payload["data"].get("repository")
    if repository_data is None:
        raise GitHubError("graphql returned no repository")
    for alias in aliases:
        if repository_data.get(alias) is None and alias not in absent_aliases:
            raise GitHubError(f"graphql returned null for {alias} without a NOT_FOUND error")
    return repository_data


def read_repository_identity(repo: Path) -> RepositoryIdentity:
    """`gh repo view` has no hostname flag, so `url` is the only field that names the host it resolved."""
    completed = subprocess.run(
        ["gh", "repo", "view", "--json", "nameWithOwner,url"],
        cwd=repo, env=_gh_environment(), capture_output=True, encoding="utf-8", check=False,
    )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        payload = None
    fields = payload if isinstance(payload, dict) else {}
    name, url = fields.get("nameWithOwner"), fields.get("url")
    well_formed = isinstance(name, str) and isinstance(url, str) and re.fullmatch(r"[\w.-]+/[\w.-]+", name)
    if completed.returncode != 0 or not well_formed:
        raise GitHubError(f"gh repo view failed: {completed.stderr.strip()}")
    return RepositoryIdentity(name, url)


class GitHubCache:
    """A JSON file, by default under the user's state directory. A pull request's closing references and title can change
    on GitHub, and a cached row is served until `--refresh`, so two runs on one ref can differ.
    An issue's first revision never changes."""

    def __init__(self, path: Path, *, refresh: bool):
        self._path = path
        self._refresh = refresh
        self._data: dict[str, dict] = {"pull_requests": {}, "issues": {}}
        if path.exists() and not refresh:
            self._data = json.loads(path.read_text())

    def get(self, table: str, number: int) -> dict | None:
        return self._data[table].get(str(number))

    def put(self, table: str, number: int, value: dict) -> None:
        self._data[table][str(number)] = value

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self._path.with_suffix(".tmp")
        temporary_path.write_text(json.dumps(self._data))
        os.replace(temporary_path, self._path)


def _batches(numbers: Sequence[int]) -> Iterable[Sequence[int]]:
    for start in range(0, len(numbers), GRAPHQL_BATCH_SIZE):
        yield numbers[start : start + GRAPHQL_BATCH_SIZE]


def github_pull_request_fetcher(
    repo: Path, repository: str, cache: GitHubCache
) -> Callable[[Sequence[int]], Mapping[int, PullRequestInfo]]:
    owner, name = repository.split("/")

    def fetch(numbers: Sequence[int]) -> Mapping[int, PullRequestInfo]:
        missing = [number for number in numbers if "title" not in (cache.get("pull_requests", number) or {})]
        for batch in _batches(missing):
            print(f"fetching pull requests {batch[0]}..{batch[-1]}", file=sys.stderr)
            selections = "\n".join(
                f"p{number}: pullRequest(number: {number}) {{ headRefName title "
                f"closingIssuesReferences(first: {CLOSING_REFERENCES_PER_PULL_REQUEST}) "
                "{ totalCount nodes { number repository { nameWithOwner } } } }"
                for number in batch
            )
            payload = _graphql(
                repo,
                f"query($owner: String!, $name: String!) {{ repository(owner: $owner, name: $name) {{ {selections} }} }}",
                {"owner": owner, "name": name},
            )
            data = _confirmed_repository_data(payload, frozenset(f"p{number}" for number in batch))
            for number in batch:
                node = data.get(f"p{number}")
                if node is None:
                    cache.put("pull_requests", number, {"head_ref": None, "title": None, "closing": []})
                    continue
                references = node["closingIssuesReferences"]
                closing = [[ref["repository"]["nameWithOwner"], ref["number"]] for ref in references["nodes"]]
                # References past the page size stay unresolved placeholders, so the PR still reads as closing several issues.
                closing += [["", 0]] * (references["totalCount"] - len(closing))
                cache.put("pull_requests", number, {"head_ref": node["headRefName"], "title": node["title"], "closing": closing})
            cache.save()
        return {
            number: PullRequestInfo(
                row["head_ref"], tuple((repo_name, issue) for repo_name, issue in row["closing"]), row["title"]
            )
            for number in numbers
            if (row := cache.get("pull_requests", number)) is not None
        }

    return fetch


def github_first_revision_fetcher(
    repo: Path, repository: str, cache: GitHubCache
) -> Callable[[Sequence[int]], Mapping[int, FirstRevision]]:
    owner, name = repository.split("/")
    edits_selection = (
        f"userContentEdits(first: {GRAPHQL_PAGE_SIZE}%s) {{ nodes {{ editedAt diff }} pageInfo {{ hasNextPage endCursor }} }}"
    )

    # Edit pagination is unbounded and a failing batch is retried at its full size, so this suits only a few hundred issues.
    def fetch_remaining_edits(number: int, cursor: str) -> list[Mapping]:
        edits: list[Mapping] = []
        while True:
            query = (
                "query($owner: String!, $name: String!, $cursor: String!) { repository(owner: $owner, name: $name) "
                f"{{ issue(number: {number}) {{ {edits_selection % ', after: $cursor'} }} }} }}"
            )
            payload = _graphql(repo, query, {"owner": owner, "name": name, "cursor": cursor})
            page = _confirmed_repository_data(payload, frozenset())["issue"]["userContentEdits"]
            edits += page["nodes"]
            if not page["pageInfo"]["hasNextPage"]:
                return edits
            cursor = page["pageInfo"]["endCursor"]

    def fetch(numbers: Sequence[int]) -> Mapping[int, FirstRevision]:
        missing = [number for number in numbers if cache.get("issues", number) is None]
        for batch in _batches(missing):
            print(f"fetching issues {batch[0]}..{batch[-1]}", file=sys.stderr)
            selections = "\n".join(
                f"i{number}: issue(number: {number}) {{ createdAt body lastEditedAt {edits_selection % ''} }}" for number in batch
            )
            payload = _graphql(
                repo,
                f"query($owner: String!, $name: String!) {{ repository(owner: $owner, name: $name) {{ {selections} }} }}",
                {"owner": owner, "name": name},
            )
            data = _confirmed_repository_data(payload, frozenset(f"i{number}" for number in batch))
            batch_rows = {}
            for number in batch:
                issue = data.get(f"i{number}")
                if issue is None:
                    batch_rows[number] = {"text": None, "reason": FIRST_REVISION_MISSING}
                    continue
                edits = list(issue["userContentEdits"]["nodes"])
                page_info = issue["userContentEdits"]["pageInfo"]
                if page_info["hasNextPage"]:
                    edits += fetch_remaining_edits(number, page_info["endCursor"])
                revision = first_revision_from_graphql(issue, edits)
                batch_rows[number] = {"text": revision.text, "reason": revision.reason}
            for number, row in batch_rows.items():
                cache.put("issues", number, row)
            cache.save()
        return {
            number: FirstRevision(row["text"], row["reason"])
            for number in numbers
            if (row := cache.get("issues", number)) is not None
        }

    return fetch


# --- CLI ---------------------------------------------------------------------------------------


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Print a ceiling on the comments-stripped A/B study's PR-task count.")
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="a checkout of the repository (default: cwd)")
    parser.add_argument("--ref", default="origin/main", help="the ref whose first-parent history is mined (default: origin/main)")
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR, help="where GraphQL answers are cached")
    parser.add_argument("--refresh", action="store_true", help="ignore cached GraphQL answers")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    repo: Path = args.repo
    identity = read_repository_identity(repo)
    repository = identity.name_with_owner
    if repository != STUDY_REPOSITORY or identity.url != STUDY_REPOSITORY_URL:
        print(
            f"refusing: the checkout's remote does not resolve to {STUDY_REPOSITORY_URL}. "
            "Only that repository name and host are checked, not the history under --ref.",
            file=sys.stderr,
        )
        return 1
    if is_shallow_repository(repo):
        print("refusing: the checkout is a shallow clone, so its history is truncated.", file=sys.stderr)
        return 1
    cache = GitHubCache(args.cache_dir / f"{repository.replace('/', '__')}.json", refresh=args.refresh)
    commits = read_first_parent_commits(repo, args.ref)
    study_start = find_study_start_commit(repo, args.ref)
    ancestors_of_study_start = read_ancestors_before(repo, study_start) if study_start else None
    sources = PoolSources(
        repository=repository,
        base_predates_study=lambda base: ancestors_of_study_start is None or base in ancestors_of_study_start,
        fetch_pull_requests=github_pull_request_fetcher(repo, repository, cache),
        fetch_first_revisions=github_first_revision_fetcher(repo, repository, cache),
        is_commit=git_names_commit(repo),
        judge_why_comment=git_why_comment_judge(repo),
    )
    ceiling = compute_ceiling(commits, sources)
    context = {
        "repository": f"{repository} (name and host resolved by gh from the checkout's remote; history under --ref not verified)",
        "ref": f"{args.ref} at {commits[0].sha[:12]}" if commits else args.ref,
        "first_parent_commits": str(len(commits)),
        "first_study_artifact_commit": study_start[:12] if study_start else "none on this ref, so stage 5 passes everything",
    }
    print(format_report(ceiling, context))
    print()
    print(report_json(ceiling, context))
    return 0


if __name__ == "__main__":
    sys.exit(main())
