"""SZZ-style blame miner: finds evals/review_bench candidates by blaming
each fix commit's diff back to its introducing commit.

stdlib `subprocess` only -- no third-party SZZ implementation.
"""
from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from review_bench.defects import Candidate, assert_unique_ids, guess_lens
from review_bench.identifiers import validate_base_ref

# A candidate fix commit's subject must mention fix/bug/regression to be considered.
_FIX_SUBJECT_RE = re.compile(r"fix|bug|regression", re.IGNORECASE)
# `main` is squash-merged with a "(#N)" subject suffix.
_PR_SUFFIX_RE = re.compile(r"\(#(\d+)\)\s*$")
# A `git blame --porcelain` header line: "<40-hex sha> <orig-line> <final-line> [<group-count>]".
_BLAME_HEADER_RE = re.compile(r"^([0-9a-f]{40}) \d+ \d+")
_HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_OLD_FILE_HEADER_RE = re.compile(r"^--- (.+)$")

# Pre-insertion context lines blamed for an addition-only hunk, since SZZ
# cannot attribute a pure omission.
_ADDITION_ONLY_CONTEXT_LINES = 3

# read-scope's own chars-per-token estimate, reused here for ranking by
# files over one Read call.
_CHARS_PER_TOKEN = 4
_READ_CAP_TOKENS = 25_000

_COMMENT_LINE_RE = re.compile(r"^\s*(#|//|/\*|\*|<!--|--)")

# Every call here is local git (diff/blame/log/rev-parse/cat-file), no
# network I/O. Mirrors transcript_analysis/scope.py's own local-git timeout
# rationale: guards against a hung local git (stale lock, network-mounted
# .git) blocking a mining run with no exit, not a network SLA. No vendor
# documentation grounds the 10-second magnitude itself -- it is an
# empirical, considered guess against this repo's own git-call latency.
_LOCAL_GIT_TIMEOUT_S = 10.0

# One wedged or missing-parent git call must drop its own commit/hunk/file
# from the mining sweep, not crash the whole run -- every _run_git call site
# below except _iter_fix_commits catches this pair, plus FileNotFoundError/
# OSError (a missing/unresolvable `git` binary on PATH) for consistency with
# mine_review_rounds.resolve_pr_number's own wider `gh`-call catch set.
# _iter_fix_commits lets the error propagate: a base ref that does not
# resolve fails the whole run.
_GIT_CALL_ERRORS = (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError, OSError)


@dataclass
class _MineStats:
    """Counts for one `mine()` run, distinct from each other so a systemic
    git failure isn't indistinguishable from "this repository's history
    genuinely has no SZZ-mineable defects". Mirrors
    mine_review_rounds.mine()'s own ref_status_counts/skipped_unresolved
    reporting convention.

    Root-commit skips inside `_build_candidate` are counted as
    git_call_failures too: a root commit's `git rev-parse <sha>^` fails the
    same way a genuinely wedged call would. Disambiguating the two would
    need an extra git call per introducer, which no caller currently needs."""

    git_call_failures: int = 0
    non_fix_subject_commits: int = 0
    markdown_path_skips: int = 0


def _is_blank_or_comment(line: str) -> bool:
    return not line.strip() or bool(_COMMENT_LINE_RE.match(line))


def _is_markdown_path(path: str) -> bool:
    return path.endswith(".md")


def _run_git(args: Sequence[str], *, cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True, timeout=_LOCAL_GIT_TIMEOUT_S,
    )
    return result.stdout


@dataclass
class _Hunk:
    old_start: int
    old_count: int
    removed_lines: list[str] = field(default_factory=list)  # "-" line content, in old-file line order


@dataclass
class _FileDiff:
    old_path: str | None  # path at the pre-fix commit, or None when the file was newly added
    hunks: list[_Hunk] = field(default_factory=list)


def _parse_unified_diff(diff_text: str) -> list[_FileDiff]:
    """Parse a unified diff into per-file hunks.

    Hunk bodies are consumed by the counts in their `@@` header, so a body
    line whose text begins `--` or `++` is not read as a file header, as long
    as each diff line is one text line. A character that `str.splitlines()`
    also splits on, such as `\\x0b` or `\\x85`, breaks that.
    """
    files: list[_FileDiff] = []
    current: _FileDiff | None = None
    current_hunk: _Hunk | None = None
    old_remaining = 0
    new_remaining = 0
    for line in diff_text.splitlines():
        if old_remaining > 0 or new_remaining > 0:
            if line.startswith("-"):
                current_hunk.removed_lines.append(line[1:])
                old_remaining -= 1
            elif line.startswith("+"):
                new_remaining -= 1
            elif line.startswith(" "):
                old_remaining -= 1
                new_remaining -= 1
            # A "\ No newline at end of file" marker belongs to neither side.
            continue
        old_header = _OLD_FILE_HEADER_RE.match(line)
        if old_header:
            raw = old_header.group(1)
            old_path = None if raw == "/dev/null" else raw.removeprefix("a/")
            current = _FileDiff(old_path=old_path)
            files.append(current)
            continue
        if current is None:
            continue
        hunk_header = _HUNK_HEADER_RE.match(line)
        if hunk_header:
            old_start = int(hunk_header.group(1))
            old_count = int(hunk_header.group(2)) if hunk_header.group(2) is not None else 1
            new_count = int(hunk_header.group(4)) if hunk_header.group(4) is not None else 1
            current_hunk = _Hunk(old_start=old_start, old_count=old_count)
            current.hunks.append(current_hunk)
            old_remaining, new_remaining = old_count, new_count
    return files


def _blame_range_shas(
    repo_dir: Path, commit: str, path: str, start: int, end: int, *, stats: _MineStats | None = None,
) -> list[str]:
    """Blame `path` at `commit` over old-file lines [start, end], returning
    one SHA per requested line in order -- `git blame -L` emits exactly
    that many lines for a non-boundary range. A failed or timed-out blame
    call drops this range's evidence rather than aborting the mining run."""
    try:
        output = _run_git(
            ["blame", "-w", "-M", "-C", "--porcelain", commit, "-L", f"{start},{end}", "--", path],
            cwd=repo_dir,
        )
    except _GIT_CALL_ERRORS:
        if stats is not None:
            stats.git_call_failures += 1
        return []
    return [m.group(1) for m in map(_BLAME_HEADER_RE.match, output.splitlines()) if m]


def _addition_only_context_range(hunk: _Hunk) -> tuple[int, int] | None:
    if hunk.old_count != 0 or hunk.old_start < 1:
        return None
    end = hunk.old_start
    start = max(1, end - _ADDITION_ONLY_CONTEXT_LINES + 1)
    return start, end


def _blame_file_diff(
    repo_dir: Path, fix_commit: str, file_diff: _FileDiff, *, stats: _MineStats | None = None,
) -> tuple[set[str], set[str]]:
    """Blame every eligible hunk in one file's diff at `<fix_commit>^`.

    Returns (confident, low_confidence): confident holds introducing SHAs
    from a modified/removed-line hunk; low_confidence holds introducing
    SHAs from an addition-only hunk's adjacent context -- disjoint sets,
    since a real removal always outranks an addition-only guess for the
    same file.
    """
    parent = f"{fix_commit}^"
    confident: set[str] = set()
    low_confidence: set[str] = set()
    for hunk in file_diff.hunks:
        if hunk.old_count > 0:
            shas = _blame_range_shas(
                repo_dir, parent, file_diff.old_path, hunk.old_start, hunk.old_start + hunk.old_count - 1,
                stats=stats,
            )
            if len(shas) != len(hunk.removed_lines):
                # A failed blame (already counted) returns no SHAs; pairing
                # a short list against the removed lines would misattribute.
                continue
            for content, sha in zip(hunk.removed_lines, shas, strict=True):
                if not _is_blank_or_comment(content):
                    confident.add(sha)
        else:
            context_range = _addition_only_context_range(hunk)
            if context_range is None:
                continue
            start, end = context_range
            low_confidence.update(_blame_range_shas(repo_dir, parent, file_diff.old_path, start, end, stats=stats))
    return confident, low_confidence


def blame_fix_commit(
    repo_dir: Path, fix_commit: str, path: str, *, stats: _MineStats | None = None,
) -> tuple[frozenset[str], bool]:
    """Public entry point for one (fix_commit, path) pair, independent of the
    full-repo mining sweep. `stats` is unset when called from
    mine_review_rounds, which reports through its own counters.

    Returns (introducing_shas, is_low_confidence).
    """
    try:
        diff_text = _run_git(["diff", "-U0", f"{fix_commit}^", fix_commit, "--", path], cwd=repo_dir)
    except _GIT_CALL_ERRORS:
        if stats is not None:
            stats.git_call_failures += 1
        return frozenset(), False
    file_diffs = [fd for fd in _parse_unified_diff(diff_text) if fd.old_path == path]
    if not file_diffs:
        return frozenset(), False
    confident, low_confidence = _blame_file_diff(repo_dir, fix_commit, file_diffs[0], stats=stats)
    introducers = confident or low_confidence
    return frozenset(introducers), not confident


def _iter_fix_commits(repo_dir: Path, base_ref: str, *, stats: _MineStats | None = None) -> list[tuple[str, str]]:
    """First-parent commits on `base_ref` whose subject matches
    fix|bug|regression."""
    log = _run_git(["log", "--first-parent", "--format=%H\x1f%s", base_ref], cwd=repo_dir)
    commits = []
    for line in log.splitlines():
        if not line:
            continue
        sha, _, subject = line.partition("\x1f")
        if _FIX_SUBJECT_RE.search(subject):
            commits.append((sha, subject))
        elif stats is not None:
            stats.non_fix_subject_commits += 1
    return commits


def _estimate_tokens(repo_dir: Path, commit: str, path: str, *, stats: _MineStats | None = None) -> int:
    try:
        size = int(_run_git(["cat-file", "-s", f"{commit}:{path}"], cwd=repo_dir).strip())
    except _GIT_CALL_ERRORS:
        if stats is not None:
            stats.git_call_failures += 1
        return 0
    return size // _CHARS_PER_TOKEN


def _build_candidate(
    repo_dir: Path, *, fix_commit: str, subject: str, path: str,
    introducing_sha: str, is_low_confidence: bool, multiple_introducers: bool,
    stats: _MineStats | None = None,
) -> Candidate | None:
    try:
        parent = _run_git(["rev-parse", f"{introducing_sha}^"], cwd=repo_dir).strip()
        fix_date = _run_git(["log", "-1", "--format=%aI", fix_commit], cwd=repo_dir).strip()
    except _GIT_CALL_ERRORS:
        # introducing_sha is a root commit with no parent, or a wedged git
        # call -- see _MineStats's own docstring for why these share one
        # counter rather than being disambiguated.
        if stats is not None:
            stats.git_call_failures += 1
        return None
    if not parent:
        return None
    pr_match = _PR_SUFFIX_RE.search(subject)
    return Candidate(
        id=f"szz:{fix_commit[:12]}:{introducing_sha[:12]}:{path}",
        source="szz",
        lens=guess_lens(path),
        base_commit=parent,
        head_commit=introducing_sha,
        fix_commit=fix_commit,
        fix_date=fix_date,
        lines_exist_at_introducing_head=True,
        reviewer_could_have_caught_it=True,
        file_is_markdown=False,
        ref_status=None,
        evidence={
            "path": path,
            "low_confidence": is_low_confidence,
            "multiple_introducers": multiple_introducers,
            "fix_subject": subject,
            "fix_pr": pr_match.group(1) if pr_match else None,
        },
    )


def _rank(repo_dir: Path, candidates: list[Candidate], *, stats: _MineStats | None = None) -> list[Candidate]:
    """Ranking rule: modified-line hits before adjacent-line hits; a
    single introducer before several; files over one Read call first. Age
    is deliberately not a ranking key."""

    def key(candidate: Candidate) -> tuple[bool, bool, bool]:
        tokens = _estimate_tokens(repo_dir, candidate.head_commit, candidate.evidence["path"], stats=stats)
        return (
            bool(candidate.evidence["low_confidence"]),
            bool(candidate.evidence["multiple_introducers"]),
            tokens <= _READ_CAP_TOKENS,
        )

    return sorted(candidates, key=key)


def mine(repo_dir: Path, *, base_ref: str = "origin/main") -> list[Candidate]:
    """Mine SZZ-style candidates from `repo_dir`'s history on `base_ref`."""
    validate_base_ref(base_ref)
    stats = _MineStats()
    candidates: list[Candidate] = []
    for fix_commit, subject in _iter_fix_commits(repo_dir, base_ref, stats=stats):
        try:
            diff_text = _run_git(["diff", "-U0", f"{fix_commit}^", fix_commit], cwd=repo_dir)
        except _GIT_CALL_ERRORS:
            # root commit, other parent-less edge case, or a wedged git call
            # -- see _MineStats's own docstring for why these share one
            # counter rather than being disambiguated.
            stats.git_call_failures += 1
            continue
        for file_diff in _parse_unified_diff(diff_text):
            if file_diff.old_path is None:
                continue
            if _is_markdown_path(file_diff.old_path):
                stats.markdown_path_skips += 1
                continue
            confident, low_confidence = _blame_file_diff(repo_dir, fix_commit, file_diff, stats=stats)
            introducers = confident or low_confidence
            if not introducers:
                continue
            is_low_confidence = not confident
            multiple = len(introducers) > 1
            for introducing_sha in sorted(introducers):
                candidate = _build_candidate(
                    repo_dir, fix_commit=fix_commit, subject=subject, path=file_diff.old_path,
                    introducing_sha=introducing_sha, is_low_confidence=is_low_confidence,
                    multiple_introducers=multiple, stats=stats,
                )
                if candidate is not None:
                    candidates.append(candidate)

    # Fails loudly on any id collision rather than let confirm's dedup silently
    # make one unconfirmable.
    assert_unique_ids(candidates, miner="mine-szz")

    ranked = _rank(repo_dir, candidates, stats=stats)
    print(
        f"mine-szz: {len(ranked)} candidate(s), git_call_failures={stats.git_call_failures}, "
        f"non_fix_subject_commits={stats.non_fix_subject_commits}, "
        f"markdown_path_skips={stats.markdown_path_skips}",
        file=sys.stderr,
    )
    return ranked
