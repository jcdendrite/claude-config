"""Later-review-round miner (Source 2): finds evals/review_bench candidates
where a later review round cited a path that an earlier round on the same
branch had already read.

Run this miner before mine_szz.py, not after: session transcripts age out
on a rolling retention window (`cleanupPeriodDays`, default 30 days), so a
review-round candidate older than that window is unrecoverable once it
expires, while the git history mine_szz.py reads is durable and can wait.

See .claude/plans/measure-review-quality.md's Approach > Defect set >
"Source 2: later review round" for the full algorithm this module follows.

Reuses transcript_analysis's session-scope, round-window, subagent-dispatch,
and reviewer-citation helpers rather than re-deriving them (Critical files,
Dispatch 1a) -- this module never redefines round detection, branch
attribution, or path normalization of its own.
"""
from __future__ import annotations

import json
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
    one config-dir root (Approach > Defect set > Source 2, step 1).

    Exits 2 when more than one root is in scope (row 37) -- production
    always calls this with roots=None, which resolves to
    (scope._projects_dir(),) alone by construction (row 16); `roots` is
    overridable only so a test can exercise the guard directly.
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
    dispatched inside the window (Approach > Defect set > Source 2,
    step 3)."""
    window_records = _window_records(records, window)
    keys = _read_keys_in_records(window_records)
    for tool_use_id in _reviewer_dispatch_tool_use_ids(window_records):
        keys.update(_reviewer_subagent_read_keys(tool_use_id, dispatch_index))
    return keys


@dataclass
class _CitationHit:
    raw_path: str
    excerpt: str


def _later_round_citations(
    scans: dict[str, reviewer_yield._ReviewerTranscriptScan], tool_use_ids: list[str],
) -> dict[str, _CitationHit]:
    """Join-key -> (raw path, excerpt) for every path a later round's
    reviewer dispatches cited, from their final text and Write blobs
    (step 4). First occurrence of a key wins the excerpt."""
    hits: dict[str, _CitationHit] = {}
    for tool_use_id in tool_use_ids:
        scan = scans.get(tool_use_id)
        if scan is None or scan.read_error:
            continue
        for text in (scan.last_assistant_text, *scan.write_content_blobs):
            for raw_path in reviewer_yield._extract_cited_paths(text):
                key = reviewer_yield._normalize_cited_path(raw_path, scan.transcript_cwd)
                if key is not None and key not in hits:
                    hits[key] = _CitationHit(raw_path=raw_path, excerpt=text)
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
        windows = review_rounds._detect_round_windows(records)
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
    """Whether the main thread wrote to the cited path -- identified by its
    normalized join key, the same `key` `mine()`'s own citation/scope
    intersection matched on -- between the two rounds' windows: evidence
    the cited code is new rather than missed (step 5, second evidence
    bullet). Cross-session timing isn't comparable, so this is always False
    when the two rounds come from different transcripts.

    Each write target is normalized with its own record's `cwd` before
    comparison, matching every other path-matching site in this module. A
    raw citation commonly carries a ":line" suffix that a write target
    never does, so comparing the raw strings directly would never match.
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


# --- Fixture-commit resolution (step 6) -------------------------------------

@dataclass
class _CommitResolution:
    ref_status: str
    base_commit: str | None = None
    head_commit: str | None = None
    fix_commit: str | None = None
    fix_date: str | None = None
    branch_commits: list[dict] = field(default_factory=list)


# Every call in this section is local git (show-ref/merge-base/log/rev-parse),
# no network I/O. Reuses mine_szz's own local-git timeout rather than
# re-deriving it, so this repo has one local-git-call timeout value; see
# that constant's own citation for the rationale.
_LOCAL_GIT_TIMEOUT_S = mine_szz._LOCAL_GIT_TIMEOUT_S
_LOCAL_GIT_ERRORS = mine_szz._GIT_CALL_ERRORS


def _local_branch_exists(repo_dir: Path, branch: str) -> bool:
    try:
        result = subprocess.run(
            ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
            cwd=repo_dir, capture_output=True, timeout=_LOCAL_GIT_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return False
    return result.returncode == 0


# transcript-analysis.py's own _GH_CALL_TIMEOUT_S: gh publishes no single
# per-call timeout recommendation, so this is a considered guess generous
# enough for one REST round trip, not a network SLA citation. Reused here
# rather than re-derived, so this repo has one `gh`-call timeout value.
_GH_CALL_TIMEOUT_S = 30.0

# `git fetch` of one PR head ref (below) is also network I/O, but its
# duration scales with the branch's object range rather than one REST round
# trip, so it gets its own constant instead of reusing _GH_CALL_TIMEOUT_S
# above -- the two operations have unrelated duration profiles. No vendor
# documentation grounds this magnitude either; it is an empirical,
# considered guess, sized the same as _GH_CALL_TIMEOUT_S for lack of a
# better basis.
_GIT_FETCH_TIMEOUT_S = 30.0


def resolve_pr_number(repo_dir: Path, branch: str) -> int | None:
    """Best-effort PR-number lookup via `gh` (row 27, [unverified]) --
    returns None on any failure, which resolves to ref_status pr-unknown
    rather than raising. Never called when a local branch already
    resolves the round (row 27's "a kept local branch is used instead when
    one exists")."""
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

    A kept local branch is checked first and used instead of fetching
    (row 27); the PR head is fetched explicitly to a dedicated ref only
    when no local branch exists, since this repo's own fetch refspec is
    `+refs/heads/*:refs/remotes/origin/*` only (row 38) and never reaches
    `refs/pull/<N>/head` on its own.
    """
    if _local_branch_exists(repo_dir, branch):
        return "local-branch", f"refs/heads/{branch}"
    if pr_number is None:
        return "pr-unknown", None
    dest_ref = f"refs/review-bench/pr/{pr_number}"
    try:
        subprocess.run(
            ["git", "fetch", "--no-tags", "origin", f"refs/pull/{pr_number}/head:{dest_ref}"],
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


def resolve_defect_commits(
    repo_dir: Path, *, branch: str, raw_path: str, after_ts: float, pr_number: int | None,
) -> _CommitResolution:
    """Resolve one review-round candidate's base/head/fix commits.

    The fix commit is the first branch commit touching `raw_path` after
    the later round's own timestamp -- the author's own response to that
    round. `head_commit` is derived by blaming the fix commit backward with
    `mine_szz.blame_fix_commit`, reusing Source 1's own helper (step 6);
    `base_commit` is that introducing commit's own parent. A candidate this
    can't fully resolve is left for the engineer to complete by hand and is
    not emitted by `mine()`.
    """
    ref_status, ref = resolve_branch_ref(repo_dir, branch, pr_number)
    if ref is None:
        return _CommitResolution(ref_status=ref_status)

    merge_base = _merge_base(repo_dir, "origin/main", ref)
    if merge_base is None:
        return _CommitResolution(ref_status=ref_status)

    touching = _commits_touching_path(repo_dir, merge_base, ref, raw_path)
    branch_commits = [{"sha": sha, "ts": ts} for sha, ts in touching]
    if not touching:
        return _CommitResolution(ref_status=ref_status, branch_commits=branch_commits)

    fix_commit = next((sha for sha, ts in touching if ts is not None and ts > after_ts), None)
    if fix_commit is None:
        return _CommitResolution(ref_status=ref_status, branch_commits=branch_commits)

    introducers, _low_confidence = mine_szz.blame_fix_commit(repo_dir, fix_commit, raw_path)
    if len(introducers) != 1:
        return _CommitResolution(ref_status=ref_status, branch_commits=branch_commits)
    head_commit = next(iter(introducers))
    base_commit = _rev_parse(repo_dir, f"{head_commit}^")
    if base_commit is None:
        return _CommitResolution(ref_status=ref_status, branch_commits=branch_commits)

    fix_date = _commit_date(repo_dir, fix_commit)
    if fix_date is None:
        return _CommitResolution(ref_status=ref_status, branch_commits=branch_commits)
    return _CommitResolution(
        ref_status=ref_status, base_commit=base_commit, head_commit=head_commit,
        fix_commit=fix_commit, fix_date=fix_date, branch_commits=branch_commits,
    )


def mine(repo_dir: Path, *, roots: Sequence[Path] | None = None) -> list[Candidate]:
    """Mine later-review-round candidates from this repo's own session
    corpus (Approach > Defect set > Source 2)."""
    session_iter = resolve_scoped_sessions(roots)
    entries = _collect_round_entries(session_iter)

    by_branch: dict[str, list[_RoundEntry]] = {}
    for entry in entries:
        by_branch.setdefault(entry.branch, []).append(entry)

    candidates: list[Candidate] = []
    skipped_unresolved = 0
    ref_status_counts: dict[str, int] = {}

    for branch, branch_entries in by_branch.items():
        ordered = sorted(branch_entries, key=lambda e: e.ts if e.ts is not None else float("inf"))
        scopes = [_round_scope(e.records, e.window, e.dispatch_index) for e in ordered]
        scan_cache: dict[Path, dict[str, reviewer_yield._ReviewerTranscriptScan]] = {}
        pr_number_cache: dict[str, int | None] = {}

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
                for key in citations.keys() & scopes[earlier_idx].keys():
                    hit = citations[key]
                    if branch not in pr_number_cache:
                        pr_number_cache[branch] = (
                            None if _local_branch_exists(repo_dir, branch) else resolve_pr_number(repo_dir, branch)
                        )
                    resolution = resolve_defect_commits(
                        repo_dir, branch=branch, raw_path=hit.raw_path, after_ts=later.ts,
                        pr_number=pr_number_cache[branch],
                    )
                    ref_status_counts[resolution.ref_status] = ref_status_counts.get(resolution.ref_status, 0) + 1
                    if resolution.base_commit is None or resolution.head_commit is None or resolution.fix_commit is None:
                        skipped_unresolved += 1
                        continue
                    candidates.append(Candidate(
                        # id carries both earlier.ts and later.ts -- each
                        # disambiguates one ordinary collision shape:
                        # - one later round citing a key two or more earlier
                        #   rounds each scoped (earlier.ts)
                        # - two later rounds citing the same key (later.ts)
                        id=f"review-round:{branch}:{key}:{earlier.ts}:{later.ts}",
                        source="review-round",
                        lens=guess_lens(hit.raw_path),
                        base_commit=resolution.base_commit,
                        head_commit=resolution.head_commit,
                        fix_commit=resolution.fix_commit,
                        fix_date=resolution.fix_date,
                        lines_exist_at_introducing_head=True,
                        reviewer_could_have_caught_it=True,
                        file_is_markdown=hit.raw_path.endswith(".md"),
                        ref_status=resolution.ref_status,
                        excerpt=hit.excerpt,
                        evidence={
                            "branch": branch,
                            "path": hit.raw_path,
                            "earlier_round_ts": earlier.ts,
                            "later_round_ts": later.ts,
                            "main_thread_edited_between": _main_thread_edited_between(earlier, later, key),
                            "branch_commits": resolution.branch_commits,
                        },
                    ))

    # Fails loudly on any id collision, rather than let confirm's excerpt/
    # existing-id lookups silently resolve it to the wrong sibling.
    assert_unique_ids(candidates, miner="mine-rounds")

    print(
        f"mine-rounds: ref_status counts {dict(sorted(ref_status_counts.items()))}, "
        f"{skipped_unresolved} match(es) skipped for unresolved commits",
        file=sys.stderr,
    )
    return candidates
