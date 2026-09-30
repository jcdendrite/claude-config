"""Later-review-round miner: finds evals/review_bench candidates where a
later review round cited a path that an earlier round on the same branch
had already read.

Run this miner before `mine_szz.py`: session transcripts age out after
`cleanupPeriodDays` (default 30 days), while the git history `mine_szz.py`
reads does not.

Reuses transcript_analysis's session-scope, round-window, subagent-dispatch,
and reviewer-citation helpers rather than re-deriving them -- this module
never redefines round detection, branch attribution, or path normalization
of its own.
"""
from __future__ import annotations

import hashlib
import json
import posixpath
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS_DIR = _REPO_ROOT / "claude" / ".claude" / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from transcript_analysis import corpus, pricing, review_rounds, reviewer_yield, scope  # noqa: E402

from review_bench import mine_szz  # noqa: E402
from review_bench.defects import Candidate, assert_unique_ids, guess_lens  # noqa: E402


def resolve_scoped_sessions(roots: Sequence[Path] | None = None):
    """Sessions for mining, scoped to this repo's own worktrees on exactly
    one config-dir root.

    Exits 2 when more than one root is in scope -- production always calls
    this with roots=None, which resolves to (scope._projects_dir(),) alone
    by construction; `roots` is overridable only so a test can exercise the
    guard directly.
    """
    resolved_roots = tuple(roots) if roots is not None else (scope._projects_dir(),)
    if len(resolved_roots) > 1:
        print(
            "mine-rounds: sessions resolved to more than one config-dir root; a round's "
            "scope and its later citations must come from the same account's corpus, so "
            "this miner refuses to mine a cross-account corpus",
            file=sys.stderr,
        )
        sys.exit(2)
    slugs = scope._repo_scoped_project_slugs("mine-rounds")
    return scope._iter_scoped_sessions(slugs, include_subagents=False, roots=resolved_roots)


def _window_records(records: list[dict], window: tuple[int, int, str]) -> list[dict]:
    open_idx, window_end, _skill = window
    return records[open_idx:window_end]


def _read_keys_in_records(records: list[dict]) -> dict[str, str]:
    """Join-key -> raw path for every Read tool_use in `records`."""
    keys: dict[str, str] = {}
    for rec in records:
        if rec.get("type") != "assistant":
            continue
        cwd = rec.get("cwd") or ""
        for block in (rec.get("message") or {}).get("content") or []:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            if block.get("name") != "Read":
                continue
            raw_path = (block.get("input") or {}).get("file_path")
            if not isinstance(raw_path, str) or not raw_path:
                continue
            key = reviewer_yield._normalize_cited_path(raw_path, cwd)
            if key is not None:
                keys[key] = raw_path
    return keys


def _reviewer_dispatch_tool_use_ids(records: list[dict]) -> list[str]:
    ids: list[str] = []
    for rec in records:
        if rec.get("type") != "assistant":
            continue
        for block in (rec.get("message") or {}).get("content") or []:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            if block.get("name") not in pricing._SPAWN_TOOL_NAMES:
                continue
            stype = (block.get("input") or {}).get("subagent_type") or ""
            if not reviewer_yield._is_reviewer_subagent_type(stype):
                continue
            tool_use_id = block.get("id") or ""
            if tool_use_id:
                ids.append(tool_use_id)
    return ids


def _reviewer_subagent_read_keys(
    tool_use_id: str, dispatch_index: dict[str, tuple[Path, str | None]],
) -> dict[str, str]:
    """Join-key -> raw path for every Read inside one reviewer subagent's
    own transcript."""
    paired = dispatch_index.get(tool_use_id)
    if paired is None:
        return {}
    jsonl_path, _requested_model = paired
    sub_records = corpus._parse_jsonl_records(jsonl_path)
    if sub_records is None:
        return {}
    return _read_keys_in_records(sub_records)


def _round_scope(
    records: list[dict], window: tuple[int, int, str], dispatch_index: dict[str, tuple[Path, str | None]],
) -> dict[str, str]:
    """Join-key -> raw path for everything Read inside `window`'s round --
    the main thread's own Reads plus every reviewer-typed subagent it
    dispatched inside the window."""
    window_records = _window_records(records, window)
    keys = _read_keys_in_records(window_records)
    for tool_use_id in _reviewer_dispatch_tool_use_ids(window_records):
        keys.update(_reviewer_subagent_read_keys(tool_use_id, dispatch_index))
    return keys


@dataclass
class _CitationHit:
    raw_citation: str
    excerpt: str


def _later_round_citations(
    scans: dict[str, reviewer_yield._ReviewerTranscriptScan], tool_use_ids: list[str],
) -> dict[str, _CitationHit]:
    """Join-key -> (raw citation, excerpt) for every path a later round's
    reviewer dispatches cited, from their final text and Write blobs.

    The first text citing a key supplies the excerpt. Within that text, the
    lexicographically smallest raw variant of the key wins, so the result
    does not depend on set iteration order.
    """
    hits: dict[str, _CitationHit] = {}
    for tool_use_id in tool_use_ids:
        scan = scans.get(tool_use_id)
        if scan is None or scan.read_error:
            continue
        for text in (scan.last_assistant_text, *scan.write_content_blobs):
            for raw_citation in sorted(reviewer_yield._extract_cited_paths(text)):
                key = reviewer_yield._normalize_cited_path(raw_citation, scan.transcript_cwd)
                if key is not None and key not in hits:
                    hits[key] = _CitationHit(raw_citation=raw_citation, excerpt=text)
    return hits


@dataclass
class _RoundEntry:
    jsonl: Path
    records: list[dict]
    dispatch_index: dict[str, tuple[Path, str | None]]
    window: tuple[int, int, str]
    branch: str
    ts: float | None


def _collect_round_entries(session_iter) -> list[_RoundEntry]:
    entries: list[_RoundEntry] = []
    for jsonl, records in session_iter:
        windows = review_rounds.detect_round_windows(records)
        if not windows:
            continue
        branches = review_rounds._session_record_branches(records, windows)
        dispatch_index, _meta_errors = corpus._index_subagent_dispatches(jsonl)
        for window in windows:
            open_idx = window[0]
            ts = corpus._parse_ts(records[open_idx].get("timestamp"))
            entries.append(_RoundEntry(
                jsonl=jsonl, records=records, dispatch_index=dispatch_index,
                window=window, branch=branches[open_idx], ts=ts,
            ))
    return entries


def _main_thread_edited_between(earlier: _RoundEntry, later: _RoundEntry, key: str) -> bool:
    """Whether the main thread wrote to the path `key` between the two
    rounds' windows, which is evidence the cited code is new rather than
    missed.
    Always False when the rounds come from different transcripts, since
    cross-session timing isn't comparable.
    Each write target is normalized with its own record's `cwd` first,
    because a raw ":line" citation never equals a write target.
    """
    if earlier.jsonl != later.jsonl:
        return False
    start = earlier.window[1]  # earlier round's own window_end
    end = later.window[0]  # later round's own window open_idx
    for rec in earlier.records[start:end]:
        if rec.get("type") != "assistant":
            continue
        cwd = rec.get("cwd") or ""
        for block in (rec.get("message") or {}).get("content") or []:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            if block.get("name") not in corpus._CODE_WRITE_TOOLS:
                continue
            target = reviewer_yield._code_write_target_path(block.get("input") or {})
            if target and reviewer_yield._normalize_cited_path(target, cwd) == key:
                return True
    return False


# --- Fixture-commit resolution -----------------------------------------------

@dataclass
class _CommitResolution:
    ref_status: str
    base_commit: str | None = None
    head_commit: str | None = None
    fix_commit: str | None = None
    fix_date: str | None = None
    branch_commits: list[dict] = field(default_factory=list)


# The calls that use this timeout are local git (show-ref/merge-base/log/
# rev-parse) with no network I/O. It reuses mine_szz's own local-git timeout
# rather than re-deriving it, so this repo has one local-git-call timeout
# value; see that constant's own citation for the rationale.
_LOCAL_GIT_TIMEOUT_S = mine_szz._LOCAL_GIT_TIMEOUT_S
_LOCAL_GIT_ERRORS = mine_szz._GIT_CALL_ERRORS


def _local_branch_exists(repo_dir: Path, branch: str) -> bool:
    try:
        result = subprocess.run(
            ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
            cwd=repo_dir, capture_output=True, timeout=_LOCAL_GIT_TIMEOUT_S,
        )
    except _LOCAL_GIT_ERRORS:
        return False
    return result.returncode == 0


# Both timeouts below are empirical guesses; no vendor documentation grounds
# their magnitude.
# One `gh` REST round trip, sized to match transcript-analysis.py's own
# _GH_CALL_TIMEOUT_S.
_GH_CALL_TIMEOUT_S = 30.0

# One `git fetch` of a PR head ref. Its duration scales with the branch's
# object range, so it has its own constant rather than reusing _GH_CALL_TIMEOUT_S.
_GIT_FETCH_TIMEOUT_S = 30.0


def resolve_pr_number(repo_dir: Path, branch: str) -> int | None:
    """Best-effort PR-number lookup via `gh` -- returns None on any
    failure, which resolves to ref_status pr-unknown rather than raising.
    Never called when a local branch already resolves the round instead."""
    try:
        result = subprocess.run(
            ["gh", "pr", "list", "--head", branch, "--state", "all", "--json", "number", "--limit", "1"],
            cwd=repo_dir, capture_output=True, text=True, timeout=_GH_CALL_TIMEOUT_S, check=True,
        )
        payload = json.loads(result.stdout)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError, OSError) as exc:
        # Logged so "gh not authenticated"/"rate-limited"/"network
        # unreachable" is distinguishable after the fact from a genuine
        # "no PR for this branch" -- both otherwise resolve to the same
        # None -> ref_status pr-unknown.
        detail = exc.stderr.strip() if isinstance(exc, subprocess.CalledProcessError) and exc.stderr else str(exc)
        print(f"mine-rounds: gh pr list for branch {branch!r} failed ({type(exc).__name__}): {detail}", file=sys.stderr)
        return None
    if not payload:
        return None
    number = payload[0].get("number")
    return number if isinstance(number, int) else None


def resolve_branch_ref(repo_dir: Path, branch: str, pr_number: int | None) -> tuple[str, str | None]:
    """Resolve one round's branch to a ref_status and a reachable ref.

    A kept local branch is checked first and used instead of fetching; the
    PR head is fetched explicitly to a dedicated ref only when no local
    branch exists, since this repo's own fetch refspec is
    `+refs/heads/*:refs/remotes/origin/*` only and never reaches
    `refs/pull/<N>/head` on its own.
    """
    if _local_branch_exists(repo_dir, branch):
        return "local-branch", f"refs/heads/{branch}"
    if pr_number is None:
        return "pr-unknown", None
    dest_ref = f"refs/review-bench/pr/{pr_number}"
    try:
        # The `+` lets a re-mine move this ref after a force-pushed PR head;
        # `confirm` pins each defect's commits under its own ref, so moving
        # this one cannot orphan a confirmed defect.
        subprocess.run(
            ["git", "fetch", "--no-tags", "origin", f"+refs/pull/{pr_number}/head:{dest_ref}"],
            cwd=repo_dir, capture_output=True, text=True, timeout=_GIT_FETCH_TIMEOUT_S, check=True,
        )
    except subprocess.CalledProcessError as exc:
        # This repo's own .git is shared across every worktree, so a
        # ref-lock collision with a concurrent git operation elsewhere is a
        # real possibility. Logging the raw stderr lets an operator tell
        # that collision apart from a genuine missing/protected PR head --
        # both otherwise resolve to the same fetch-failed outcome.
        print(f"mine-rounds: fetch of PR #{pr_number}'s head failed: {exc.stderr.strip()}", file=sys.stderr)
        return "fetch-failed", None
    except subprocess.TimeoutExpired:
        print(f"mine-rounds: fetch of PR #{pr_number}'s head timed out after {_GIT_FETCH_TIMEOUT_S}s", file=sys.stderr)
        return "fetch-failed", None
    except (FileNotFoundError, OSError) as exc:
        # Matches this file's own _LOCAL_GIT_ERRORS convention (a
        # missing/unresolvable `git` binary on PATH) rather than crashing
        # the whole mining sweep over one round's fetch.
        print(f"mine-rounds: fetch of PR #{pr_number}'s head failed ({type(exc).__name__}): {exc}", file=sys.stderr)
        return "fetch-failed", None
    return "fetched", dest_ref


def _merge_base(repo_dir: Path, a: str, b: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "merge-base", a, b], cwd=repo_dir, capture_output=True, text=True,
            timeout=_LOCAL_GIT_TIMEOUT_S, check=True,
        )
    except _LOCAL_GIT_ERRORS:
        return None
    return result.stdout.strip() or None


def _commits_touching_path(repo_dir: Path, base: str, ref: str, path: str) -> list[tuple[str, float | None]]:
    try:
        result = subprocess.run(
            ["git", "log", "--format=%H\x1f%aI", f"{base}..{ref}", "--", path],
            cwd=repo_dir, capture_output=True, text=True, timeout=_LOCAL_GIT_TIMEOUT_S, check=True,
        )
    except _LOCAL_GIT_ERRORS:
        return []
    commits: list[tuple[str, float | None]] = []
    for line in result.stdout.splitlines():
        if not line:
            continue
        sha, _, iso_ts = line.partition("\x1f")
        commits.append((sha, corpus._parse_ts(iso_ts) if iso_ts else None))
    return commits


def _rev_parse(repo_dir: Path, rev: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", rev], cwd=repo_dir, capture_output=True, text=True,
            timeout=_LOCAL_GIT_TIMEOUT_S, check=True,
        )
    except _LOCAL_GIT_ERRORS:
        return None
    return result.stdout.strip() or None


def _commit_date(repo_dir: Path, commit: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%aI", commit], cwd=repo_dir, capture_output=True, text=True,
            timeout=_LOCAL_GIT_TIMEOUT_S, check=True,
        )
    except _LOCAL_GIT_ERRORS:
        return None
    return result.stdout.strip() or None


def _paths_touched_on_branch(repo_dir: Path, base: str, ref: str) -> frozenset[str]:
    """Repo-relative paths any commit in `base..ref` touches. Renames are
    reported as a delete plus an add, so both names appear."""
    try:
        result = subprocess.run(
            ["git", "log", "-z", "--name-only", "--no-renames", "--format=", f"{base}..{ref}"],
            cwd=repo_dir, capture_output=True, text=True, timeout=_LOCAL_GIT_TIMEOUT_S, check=True,
        )
    except _LOCAL_GIT_ERRORS:
        return frozenset()
    return frozenset(name for name in (raw.strip("\n") for raw in result.stdout.split("\0")) if name)


def match_repo_path(citation: str, touched_paths: frozenset[str]) -> str | None:
    """Map one raw reviewer citation (`file:line`, possibly an absolute or
    worktree path) to the repo-relative path it names, or None.

    An absolute or home-relative citation resolves to the longest touched
    path that is a suffix of it, which absorbs a worktree prefix of any
    depth without parsing it. A relative citation resolves only to an exact
    touched path, or to the one touched path it is a suffix of.
    """
    path = posixpath.normpath(reviewer_yield._CITED_PATH_LINE_SUFFIX_RE.sub("", citation))
    if path.startswith(("/", "~")):
        suffix_matches = [touched for touched in touched_paths if path.endswith(f"/{touched}")]
        return max(suffix_matches, key=len) if suffix_matches else None
    if path in touched_paths:
        return path
    shorter_citation_matches = [touched for touched in touched_paths if touched.endswith(f"/{path}")]
    return shorter_citation_matches[0] if len(shorter_citation_matches) == 1 else None


@dataclass
class _BranchGit:
    """One branch's git facts, resolved once and shared by every citation on
    the branch: the ref, its merge-base with origin/main, the paths its
    commits touch, and memoized per-path results."""

    ref_status: str
    ref: str | None = None
    merge_base: str | None = None
    touched_paths: frozenset[str] = frozenset()
    commits_by_path: dict[str, list[tuple[str, float | None]]] = field(default_factory=dict)
    introductions: dict[tuple[str, str], tuple[str, str, str] | None] = field(default_factory=dict)
    resolutions: dict[tuple[str, float], _CommitResolution] = field(default_factory=dict)


def open_branch_git(repo_dir: Path, branch_ref: tuple[str, str | None]) -> _BranchGit:
    """Resolve the merge-base and touched-path set for one branch's
    `resolve_branch_ref` result. A branch with no reachable ref, or no
    merge-base, has no touched paths, so nothing on it resolves."""
    ref_status, ref = branch_ref
    if ref is None:
        return _BranchGit(ref_status=ref_status)
    merge_base = _merge_base(repo_dir, "origin/main", ref)
    if merge_base is None:
        return _BranchGit(ref_status=ref_status, ref=ref)
    return _BranchGit(
        ref_status=ref_status, ref=ref, merge_base=merge_base,
        touched_paths=_paths_touched_on_branch(repo_dir, merge_base, ref),
    )


def _introduction(repo_dir: Path, branch: _BranchGit, path: str, fix_commit: str) -> tuple[str, str, str] | None:
    """(head_commit, base_commit, fix_date) for `fix_commit`'s change to
    `path`, or None when blame does not name exactly one introducing commit.
    Blames `fix_commit` backward with `mine_szz.blame_fix_commit`;
    `base_commit` is the introducing commit's own parent. Blame runs once
    per (path, fix_commit)."""
    memo_key = (path, fix_commit)
    if memo_key in branch.introductions:
        return branch.introductions[memo_key]
    introduction = None
    introducers, _low_confidence = mine_szz.blame_fix_commit(repo_dir, fix_commit, path)
    if len(introducers) == 1:
        head_commit = next(iter(introducers))
        base_commit = _rev_parse(repo_dir, f"{head_commit}^")
        fix_date = _commit_date(repo_dir, fix_commit)
        if base_commit is not None and fix_date is not None:
            introduction = (head_commit, base_commit, fix_date)
    branch.introductions[memo_key] = introduction
    return introduction


def resolve_defect_commits(
    repo_dir: Path, *, path: str, after_ts: float, branch: _BranchGit,
) -> _CommitResolution:
    """Resolve one review-round candidate's base/head/fix commits for the
    repo-relative `path`.

    The fix commit is the earliest branch commit touching `path` strictly
    after the later round's own timestamp -- the author's first response to
    that round. `head_commit` is derived by blaming the fix commit backward;
    `base_commit` is that introducing commit's own parent. A candidate this
    cannot fully resolve is skipped by `mine()` and counted in its
    `skipped_unresolved` figure.

    `branch` comes from `open_branch_git`, which `mine()` opens once per
    branch; results are memoized on it per (path, after_ts).
    """
    memo_key = (path, after_ts)
    if memo_key in branch.resolutions:
        return branch.resolutions[memo_key]

    resolution = _CommitResolution(ref_status=branch.ref_status)
    if branch.ref is not None and branch.merge_base is not None:
        if path not in branch.commits_by_path:
            branch.commits_by_path[path] = _commits_touching_path(repo_dir, branch.merge_base, branch.ref, path)
        touching = branch.commits_by_path[path]
        resolution.branch_commits = [{"sha": sha, "ts": ts} for sha, ts in touching]
        # `git log` lists newest first; reversing makes a timestamp tie
        # resolve to the older commit, since min keeps the first minimum.
        after_round = [(sha, ts) for sha, ts in reversed(touching) if ts is not None and ts > after_ts]
        if after_round:
            fix_commit, _fix_ts = min(after_round, key=lambda commit: commit[1])
            introduction = _introduction(repo_dir, branch, path, fix_commit)
            if introduction is not None:
                head_commit, base_commit, fix_date = introduction
                resolution.base_commit = base_commit
                resolution.head_commit = head_commit
                resolution.fix_commit = fix_commit
                resolution.fix_date = fix_date
    branch.resolutions[memo_key] = resolution
    return resolution


def _branch_fingerprint(branch: str) -> str:
    """A short, unsalted-hash stand-in for a real git branch name in a
    committed candidate ID. A branch name is this account's own text, not
    project-owned data the way a commit SHA is, so it never appears in an
    ID's own bytes. The digest is unsalted and 48 bits, so it obscures a
    non-guessable branch name and only lets a guessable one be confirmed.
    Contrast mine_szz.py's SZZ-sourced IDs, which embed real (and
    already-public) commit SHAs directly."""
    return hashlib.sha256(branch.encode()).hexdigest()[:12]


def _path_fingerprint(path: str) -> str:
    return hashlib.sha256(path.encode()).hexdigest()[:16]


@dataclass(frozen=True)
class _CitationEvent:
    """One later-round citation of a path an earlier round had read. Carries
    only transcript facts; no git has been consulted."""

    raw_citation: str
    excerpt: str
    earlier_round_ts: float
    later_round_ts: float
    main_thread_edited_between: bool


def _branch_citation_events(branch_entries: list[_RoundEntry]) -> list[_CitationEvent]:
    """Every (later round, earlier round, cited path) match on one branch,
    from the transcripts alone."""
    ordered = sorted(branch_entries, key=lambda e: e.ts if e.ts is not None else float("inf"))
    scopes = [_round_scope(e.records, e.window, e.dispatch_index) for e in ordered]
    scan_cache: dict[Path, dict[str, reviewer_yield._ReviewerTranscriptScan]] = {}
    events: list[_CitationEvent] = []

    for later_idx, later in enumerate(ordered):
        if later.jsonl not in scan_cache:
            scan_cache[later.jsonl] = reviewer_yield._scan_reviewer_transcripts(later.records, later.dispatch_index)
        later_tool_use_ids = _reviewer_dispatch_tool_use_ids(_window_records(later.records, later.window))
        citations = _later_round_citations(scan_cache[later.jsonl], later_tool_use_ids)
        if not citations or later.ts is None:
            continue

        for earlier_idx in range(later_idx):
            earlier = ordered[earlier_idx]
            if earlier.ts is None or not (earlier.ts < later.ts):
                continue
            for key in sorted(citations.keys() & scopes[earlier_idx].keys()):
                hit = citations[key]
                events.append(_CitationEvent(
                    raw_citation=hit.raw_citation, excerpt=hit.excerpt,
                    earlier_round_ts=earlier.ts, later_round_ts=later.ts,
                    main_thread_edited_between=_main_thread_edited_between(earlier, later, key),
                ))
    return events


def _grouped_candidate(
    branch: str, path: str, resolution: _CommitResolution, events: list[_CitationEvent],
) -> Candidate:
    """One candidate for every citation of `path` whose earliest post-round
    fix is the same commit. Those citations describe one defect, so their
    round pairs and excerpts are kept as evidence rather than emitted as
    look-alike candidates."""
    events = sorted(events, key=lambda e: (e.later_round_ts, e.earlier_round_ts))
    edited_by_round_pair: dict[tuple[float, float], bool] = {}
    for event in events:
        pair = (event.earlier_round_ts, event.later_round_ts)
        edited_by_round_pair[pair] = edited_by_round_pair.get(pair, False) or event.main_thread_edited_between
    return Candidate(
        id=f"review-round:{_branch_fingerprint(branch)}:{_path_fingerprint(path)}:{resolution.fix_commit[:12]}",
        source="review-round",
        lens=guess_lens(path),
        base_commit=resolution.base_commit,
        head_commit=resolution.head_commit,
        fix_commit=resolution.fix_commit,
        fix_date=resolution.fix_date,
        lines_exist_at_introducing_head=True,
        reviewer_could_have_caught_it=True,
        file_is_markdown=path.endswith(".md"),
        ref_status=resolution.ref_status,
        # Every distinct excerpt stays, so confirm's provenance check still
        # covers text from the round pairs this candidate absorbed.
        excerpt="\n\n".join(dict.fromkeys(event.excerpt for event in events)),
        evidence={
            "branch": branch,
            "path": path,
            "raw_citation": min(event.raw_citation for event in events),
            "round_pairs": [
                {"earlier_round_ts": earlier_ts, "later_round_ts": later_ts, "main_thread_edited_between": edited}
                for (earlier_ts, later_ts), edited in edited_by_round_pair.items()
            ],
            "branch_commits": resolution.branch_commits,
        },
    )


def _resolve_branch(repo_dir: Path, branch: str) -> tuple[str, str | None]:
    pr_number = None if _local_branch_exists(repo_dir, branch) else resolve_pr_number(repo_dir, branch)
    return resolve_branch_ref(repo_dir, branch, pr_number)


def mine(repo_dir: Path, *, roots: Sequence[Path] | None = None) -> list[Candidate]:
    """Mine later-review-round candidates from this repo's own session
    corpus.

    Two phases: a transcript pass that emits citation events without
    touching git, then a per-branch git pass that maps each citation to a
    repo path, finds its earliest post-round fix commit, and emits one
    candidate per (path, fix commit).
    """
    session_iter = resolve_scoped_sessions(roots)
    entries = _collect_round_entries(session_iter)

    by_branch: dict[str, list[_RoundEntry]] = {}
    for entry in entries:
        by_branch.setdefault(entry.branch, []).append(entry)

    candidates: list[Candidate] = []
    skipped_unresolved = 0
    skipped_empty_branch_rounds = 0
    ref_status_counts: dict[str, int] = {}

    for branch, branch_entries in by_branch.items():
        if not branch:
            skipped_empty_branch_rounds += len(branch_entries)
            continue
        events = _branch_citation_events(branch_entries)
        if not events:
            continue

        branch_git = open_branch_git(repo_dir, _resolve_branch(repo_dir, branch))
        ref_status_counts[branch_git.ref_status] = ref_status_counts.get(branch_git.ref_status, 0) + 1

        groups: dict[tuple[str, str], tuple[_CommitResolution, list[_CitationEvent]]] = {}
        for event in events:
            path = match_repo_path(event.raw_citation, branch_git.touched_paths)
            if path is None:
                skipped_unresolved += 1
                continue
            resolution = resolve_defect_commits(
                repo_dir, path=path, after_ts=event.later_round_ts, branch=branch_git,
            )
            if resolution.base_commit is None or resolution.head_commit is None or resolution.fix_commit is None:
                skipped_unresolved += 1
                continue
            groups.setdefault((path, resolution.fix_commit), (resolution, []))[1].append(event)

        for (path, _fix_commit), (resolution, group_events) in groups.items():
            candidates.append(_grouped_candidate(branch, path, resolution, group_events))

    # Fails loudly on any id collision, rather than let confirm's excerpt/
    # existing-id lookups silently resolve it to the wrong sibling.
    assert_unique_ids(candidates, miner="mine-rounds")

    print(
        f"mine-rounds: ref_status counts per branch {dict(sorted(ref_status_counts.items()))}, "
        f"{skipped_unresolved} citation(s) skipped for unresolved commits, "
        f"{skipped_empty_branch_rounds} round(s) skipped for an empty branch name",
        file=sys.stderr,
    )
    return candidates
