"""The reviewer-yield command family: cmd_reviewer_yield and every helper
used only by it, plus the two symbols review-trace and subagent-mix still
reach back into.

Imports corpus, pricing, render, and scope by module (attribute access, not
by name) -- see scope.py's own top-of-file comment for why.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from collections import defaultdict
from pathlib import Path
from typing import NamedTuple

from transcript_analysis import corpus, pricing, render, scope

# Exact-name reviewers (no staff- prefix) counted in review-trace and reviewer-yield.
_REVIEWER_PREFIX = "staff-"
_REVIEWER_EXACT_NAMES: frozenset[str] = frozenset(
    {"ciso-reviewer", "comment-discipline-reviewer", "skill-fidelity-reviewer"}
)

# Reviewer verdict-text patterns for reviewer-yield's dispatch-outcome join.
# Loosened from each reviewer agent's documented `**No X concerns**` /
# `Found <N> issues.` / `**Approve with concerns**` / `**Request changes**`
# contract (claude/.claude/agents/*.md) to tolerate markdown-bold,
# singular/plural, and case variance.
_REVIEWER_NO_CONCERNS_GAP_MAX_CHARS = 40  # bounds "no <...> concerns" to one short phrase, not a whole paragraph
_REVIEWER_NO_CONCERNS_RE = re.compile(
    rf"\bno\b[\w\s/-]{{0,{_REVIEWER_NO_CONCERNS_GAP_MAX_CHARS}}}?\bconcerns\b", re.IGNORECASE
)
_REVIEWER_FOUND_ISSUES_RE = re.compile(r"found\s+(\d+)\s+issues?\b", re.IGNORECASE)
_REVIEWER_APPROVE_WITH_CONCERNS_RE = re.compile(r"\bapprove with concerns\b", re.IGNORECASE)
_REVIEWER_REQUEST_CHANGES_RE = re.compile(r"\brequest changes\b", re.IGNORECASE)

# _classify_reviewer_verdict's bucket labels, shared with cmd_reviewer_yield's
# aggregation branch — named so a typo in either can't silently fall through
# to the "unclassified" bucket.
_REVIEWER_VERDICT_FINDINGS_FOUND = "findings-found"
_REVIEWER_VERDICT_ZERO_FINDING = "zero-finding"
_REVIEWER_VERDICT_UNCLASSIFIED = "unclassified"

# Below this Active count, an Edited/Active ratio is too noisy to report —
# see cmd_reviewer_yield's own docstring for the "insufficient" fallback.
_REVIEWER_YIELD_ACTIVE_FLOOR = 10
_REVIEWER_YIELD_INSUFFICIENT = "insufficient"


def _index_subagent_dispatches(jsonl: Path) -> tuple[dict[str, tuple[Path, str | None]], int]:
    """Map each subagent dispatch's toolUseId to (its paired .jsonl path,
    requested model), for one session.

    Reads subagents/*.meta.json directly rather than through iter_sessions'
    include_subagents merge — that merge flattens every subagent file's
    records into one list with no per-file boundary, which cannot answer
    "this specific dispatch's own last assistant text." The requested model
    is meta.json's own "model" key (absent when the dispatch carried no
    explicit model request) — reading it here, alongside the toolUseId this
    function already parses meta.json for, avoids a second per-dispatch
    meta.json read in subagent-mix's model-mix join.

    Returns (index, meta_read_errors): meta_read_errors counts *.meta.json
    files present but unusable — invalid JSON, valid JSON missing a
    string-typed toolUseId, or valid JSON whose "model" key is present but
    not a string — distinct from a dispatch with no meta.json at all (the
    caller's own, separately-documented exclusion path). meta.json is
    written by Claude Code's own harness, not by this repo, so its "model"
    and "toolUseId" fields are external input: a non-string value for either
    (a future harness change, or a corrupted file) is excluded here rather
    than reaching a caller that would use it as a dict key and crash with an
    uncaught TypeError.
    """
    subagent_dir = jsonl.parent / jsonl.stem / corpus.SUBAGENT_SUBDIR
    index: dict[str, tuple[Path, str | None]] = {}
    meta_read_errors = 0
    if not subagent_dir.is_dir():
        return index, meta_read_errors
    for meta_path in sorted(subagent_dir.glob("*.meta.json")):
        try:
            meta = json.loads(meta_path.read_text())
        except (OSError, json.JSONDecodeError):
            meta_read_errors += 1
            continue
        tool_use_id = meta.get("toolUseId")
        if not isinstance(tool_use_id, str) or not tool_use_id:
            meta_read_errors += 1
            continue
        requested_model = meta.get("model")
        if requested_model is not None and not isinstance(requested_model, str):
            meta_read_errors += 1
            continue
        agent_id = meta_path.name.removesuffix(".meta.json")
        index[tool_use_id] = (meta_path.parent / f"{agent_id}.jsonl", requested_model)
    return index, meta_read_errors


class _ReviewerTranscriptScan(NamedTuple):
    """One reviewer subagent transcript's join inputs, gathered in a single walk.

    last_assistant_text: the last non-empty assistant text block, or ''. A
      trailing assistant record with no text (e.g. a final tool-only turn)
      does not blank out an earlier one — this walks the whole file and
      keeps the most recent non-empty text seen, matching "last assistant
      text block" rather than "last assistant record's text, possibly
      empty."
    write_content_blobs: every Write tool_use's input.content string found
      along the same walk, in file order.
    write_target_paths: every Write tool_use's input.file_path found along
      the same walk, in file order — this dispatch's own findings file is
      almost always among them, giving the caller a path-normalized
      set-membership exclusion (see _dispatch_self_reference_keys) instead
      of fragile free-text-prose matching against the dispatching prompt.
    transcript_cwd: the cwd field from the first record in this transcript
      that carries one, or '' if none do. Reviewer-cited relative paths
      were written from the reviewer subagent's own working directory, not
      the dispatching parent's, which can diverge under an
      isolation:worktree reviewer dispatch.
    read_error: True on OSError opening/reading the transcript. A read
      failure is not a legitimate zero-citation transcript, so the caller
      must exclude it from a coverage denominator rather than count it as
      one — every other field is ("", [], [], "", frozenset()) in this case.
    code_write_tool_use_ids: every code-write tool_use's own id (any of
      corpus._CODE_WRITE_TOOLS, not just Write) seen along the same walk,
      feeding _reviewer_write_tool_use_ids' edit-index exclusion.
      write_content_blobs/write_target_paths above stay on the narrower
      Write-only branch citation extraction needs.
    """

    last_assistant_text: str
    write_content_blobs: list[str]
    write_target_paths: list[str]
    transcript_cwd: str
    read_error: bool
    code_write_tool_use_ids: frozenset[str]


def _scan_reviewer_transcript(jsonl_path: Path) -> _ReviewerTranscriptScan:
    """Walk one transcript file once, collecting all reviewer-yield join inputs.

    See _ReviewerTranscriptScan for the field-by-field contract.
    """
    last_text = ""
    write_content_blobs: list[str] = []
    write_target_paths: list[str] = []
    transcript_cwd = ""
    code_write_tool_use_ids: set[str] = set()
    try:
        with open(jsonl_path) as fh:
            for raw in fh:
                try:
                    rec = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not transcript_cwd:
                    rec_cwd = rec.get("cwd")
                    if isinstance(rec_cwd, str) and rec_cwd:
                        transcript_cwd = rec_cwd
                if rec.get("type") != "assistant":
                    continue
                content = (rec.get("message") or {}).get("content", "")
                if isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict) or block.get("type") != "tool_use":
                            continue
                        if block.get("name") in corpus._CODE_WRITE_TOOLS:
                            block_id = block.get("id")
                            if block_id:
                                code_write_tool_use_ids.add(block_id)
                        if block.get("name") != "Write":
                            continue
                        block_input = block.get("input") or {}
                        blob = block_input.get("content")
                        if isinstance(blob, str):
                            write_content_blobs.append(blob)
                        target = block_input.get("file_path")
                        if isinstance(target, str) and target:
                            write_target_paths.append(target)
                text = render._content_text(content)
                if text.strip():
                    last_text = text
    except OSError:
        return _ReviewerTranscriptScan("", [], [], "", True, frozenset())
    return _ReviewerTranscriptScan(
        last_text, write_content_blobs, write_target_paths, transcript_cwd, False, frozenset(code_write_tool_use_ids)
    )


def _scan_reviewer_transcripts(
    records: list[dict], dispatch_index: dict[str, tuple[Path, str | None]]
) -> dict[str, _ReviewerTranscriptScan]:
    """Pre-pass over one session's merged records (main thread plus every
    subagent file, once the caller's session_iter was built with
    include_subagents=True): scan every reviewer-typed dispatch's own
    subagent transcript exactly once, keyed by its dispatch tool_use id.

    Walks assistant records regardless of isSidechain and regardless of any
    --since/--until window — a reviewer dispatched from inside another
    subagent, or one whose own dispatch record falls outside a --record
    week boundary, still writes into this session's merged records, and its
    writes must still reach _reviewer_write_tool_use_ids' exclusion set. A
    dispatch id absent from dispatch_index has no resolvable subagent
    transcript and is skipped — the same population the scoring loop below
    already excludes entirely, not "unclassified".
    """
    scans: dict[str, _ReviewerTranscriptScan] = {}
    for rec in records:
        if rec.get("type") != "assistant":
            continue
        for block in (rec.get("message") or {}).get("content") or []:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            if block.get("name") not in pricing._SPAWN_TOOL_NAMES:
                continue
            block_input = block.get("input") or {}
            stype = block_input.get("subagent_type") or ""
            if not _is_reviewer_subagent_type(stype):
                continue
            tool_use_id = block.get("id") or ""
            if not tool_use_id or tool_use_id in scans:
                continue
            paired = dispatch_index.get(tool_use_id)
            if paired is None:
                continue
            paired_jsonl, _requested_model = paired
            scans[tool_use_id] = _scan_reviewer_transcript(paired_jsonl)
    return scans


def _reviewer_write_tool_use_ids(scans: dict[str, _ReviewerTranscriptScan]) -> frozenset[str]:
    """Union of every code-write tool_use id recorded inside any
    reviewer-typed subagent transcript in scans — the edit index's
    exclusion set. Uniform across self and sibling: a reviewer's own
    findings write and a sibling reviewer's both land here, since neither
    reflects real fix work (see _index_session_edits)."""
    ids: set[str] = set()
    for scan in scans.values():
        ids |= scan.code_write_tool_use_ids
    return frozenset(ids)


def _classify_reviewer_verdict(text: str) -> tuple[str, int]:
    """Classify one reviewer subagent's final verdict text.

    Returns (bucket, findings): bucket is one of _REVIEWER_VERDICT_FINDINGS_FOUND,
    _REVIEWER_VERDICT_ZERO_FINDING, or _REVIEWER_VERDICT_UNCLASSIFIED; findings
    is the parsed N for a findings-found verdict, else 0.
    "Found 0 issues" is a zero-finding verdict, not findings-found, despite
    matching the found-issues pattern. "Approve with concerns"/"Request
    changes" verdicts carry real findings but no derivable count, so they
    land in findings-found with 0 — the caller's total-findings sum is a
    lower bound, not every findings-found dispatch's true count.
    """
    m = _REVIEWER_FOUND_ISSUES_RE.search(text)
    if m:
        n = int(m.group(1))
        return (_REVIEWER_VERDICT_FINDINGS_FOUND, n) if n > 0 else (_REVIEWER_VERDICT_ZERO_FINDING, 0)
    if _REVIEWER_NO_CONCERNS_RE.search(text):
        return (_REVIEWER_VERDICT_ZERO_FINDING, 0)
    if _REVIEWER_APPROVE_WITH_CONCERNS_RE.search(text) or _REVIEWER_REQUEST_CHANGES_RE.search(text):
        return (_REVIEWER_VERDICT_FINDINGS_FOUND, 0)
    return (_REVIEWER_VERDICT_UNCLASSIFIED, 0)


# Generous bound for any realistic cited path (worktree prefix + repo-relative
# suffix); still a hard cap so a run of pathish characters (a code-fence
# border, `tree` output) can't grow one match unboundedly.
_CITED_PATH_CANDIDATE_MAX_CHARS = 300
# One flat, bounded character class — no group is itself quantified, unlike
# the natural "(?:[\w.-]+/)+[\w.-]+" shape, which backtracks catastrophically
# on a long non-matching slash run. Same safety property as
# _DENIAL_HOOK_NAME_RE, copied for the same reason. Deliberately unselective:
# a bare word matches too (no `/` or `.` required here) — separator and
# extension filtering happens in _normalize_cited_path, not in extraction.
_CITED_PATH_CANDIDATE_RE = re.compile(rf"[\w./~:-]{{1,{_CITED_PATH_CANDIDATE_MAX_CHARS}}}")


def _extract_cited_paths(text: str) -> set[str]:
    """Extract raw candidate path strings from one blob of reviewer prose.

    Returns every run matched by _CITED_PATH_CANDIDATE_RE, deduplicated —
    including runs that turn out to be plain prose words or bare filenames.
    _normalize_cited_path is what decides whether a candidate is a real,
    join-able path; this function only tokenizes.
    """
    return set(_CITED_PATH_CANDIDATE_RE.findall(text))


# Strips a trailing ":line" or ":line:col" suffix (e.g. "foo.py:42" or
# "foo.py:42:7") — normalization step 1.
_CITED_PATH_LINE_SUFFIX_RE = re.compile(r":\d+(?::\d+)?$")
# Matches one ".claude/worktrees/<branch>/" segment, anywhere in the path —
# normalization step 6. `[^/]+` takes only the branch's first path segment,
# a documented bias toward under-stripping on a slash-containing branch slug
# (see _normalize_cited_path's docstring); this is not losslessly decidable
# from the path alone with zero filesystem access.
_CITED_PATH_WORKTREE_PREFIX_RE = re.compile(r"\.claude/worktrees/[^/]+/")


def _normalize_cited_path(candidate: str, cwd: str) -> str | None:
    """Normalize one raw candidate from _extract_cited_paths into a join key,
    or None if the candidate is discarded.

    Lexical only: no Path.resolve(), os.path.realpath, or stat — those chase
    symlinks (e.g. macOS's /tmp -> /private/tmp) and would make the join key
    depend on where each analyst's clone lives, and an OSError from that
    traversal would embed the offending path in its message with no
    top-level handler to catch it. The one exception is os.path.expanduser's
    own pwd.getpwnam lookup for an "~otheruser" candidate (step 3, below) —
    that candidate is discarded regardless of whether the lookup succeeds, so
    it never affects the key of a candidate this function actually resolves.

    Ordered steps (an implementer will get the order wrong otherwise):
      1. Strip a trailing ":line" or ":line:col" suffix.
      2. Reject a candidate with no directory separator — a bare "SKILL.md"
         is ordinary prose, not a path, and resolving it against `cwd` would
         manufacture a false in-repo match.
      3. Expand a leading "~" lexically (os.path.expanduser). A candidate
         still starting with "~" afterward is the unexpandable "~otheruser"
         form and is discarded, not resolved via a directory-service lookup.
         This runs before step 4 (relative-path resolution) because a
         "~"-prefixed candidate is neither absolute nor genuinely relative —
         expanduser is a no-op on a non-leading "~", so this must expand it
         before anything joins it to `cwd`.
      4. Resolve ".." and relative segments against the **unstripped** `cwd`,
         for a candidate still relative after step 3. Must precede step 6:
         "../../../.venv/bin/pytest" (this repo's own CLAUDE.md idiom) means
         three levels above the worktree, and resolving it against an
         already-worktree-stripped `cwd` would silently change what
         directory it names.
      5. Collapse a leading "/private/tmp" to "/tmp" (macOS-only aliasing;
         inert on Linux, where that prefix cannot appear in a transcript).
      6. Strip ".claude/worktrees/<branch>/" to fixpoint, not once, so a
         nested worktree (an isolation:worktree agent under a
         worktree-anchored parent) doesn't leave a dangling second segment.
    """
    path = _CITED_PATH_LINE_SUFFIX_RE.sub("", candidate)  # 1

    if "/" not in path:  # 2
        return None

    if path.startswith("~"):  # 3
        path = os.path.expanduser(path)
        if path.startswith("~"):
            return None  # unexpandable "~otheruser/..." form

    if not path.startswith("/"):  # 4 — still relative after step 3
        path = os.path.normpath(os.path.join(cwd, path))

    if path.startswith("/private/tmp"):  # 5
        path = "/tmp" + path[len("/private/tmp"):]

    while True:  # 6 — to fixpoint
        stripped = _CITED_PATH_WORKTREE_PREFIX_RE.sub("", path)
        if stripped == path:
            break
        path = stripped

    return hashlib.sha256(path.encode()).hexdigest()[:16]


def _is_reviewer_subagent_type(stype: str) -> bool:
    """True for a subagent_type in the shared reviewer-agent set
    (_REVIEWER_PREFIX/_REVIEWER_EXACT_NAMES), used by the
    dispatch-classification loop to decide which Agent/Task tool_use blocks
    to aggregate."""
    return stype.startswith(_REVIEWER_PREFIX) or stype in _REVIEWER_EXACT_NAMES


def _code_write_target_path(tool_input: dict) -> str | None:
    """A code-write tool_use's target path. NotebookEdit carries
    notebook_path instead of file_path; MultiEdit's single file_path already
    covers its own case."""
    return tool_input.get("file_path") or tool_input.get("notebook_path")


def _build_tool_result_ts_map(records: list[dict], since_ts: float | None) -> dict[str, float]:
    """Map each tool_use_id to its tool_result record's timestamp, for one
    session's already-materialized records — no new file I/O. tool_result
    blocks live on user-type records, not the assistant-type records the
    rest of reviewer-yield's loop filters to. A tool_result whose own
    timestamp is missing/unparseable, or outside the --since window, is
    omitted — the caller then treats that dispatch's Active/Edited ordering
    as undecidable rather than guessing at it.
    """
    tool_result_ts: dict[str, float] = {}
    for rec in records:
        if rec.get("type") != "user":
            continue
        rec_ts = corpus._parse_ts(rec.get("timestamp"))
        if rec_ts is None:
            continue
        if since_ts is not None and rec_ts < since_ts:
            continue
        content = (rec.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            tid = block.get("tool_use_id")
            if tid:
                tool_result_ts[tid] = rec_ts
    return tool_result_ts


def _index_session_edits(
    records: list[dict], since_ts: float | None, *, reviewer_write_tool_use_ids: frozenset[str]
) -> dict[str, float]:
    """Session-wide code-write edit index: normalized path key -> latest
    edit timestamp. A second pass over the same already-materialized
    records list iter_sessions handed the caller — no new file I/O.
    Session-wide because records already include every subagent file's own
    (isSidechain) edits, once the caller's session_iter was built with
    include_subagents=True.

    reviewer_write_tool_use_ids has no default: it is the
    _scan_reviewer_transcripts/_reviewer_write_tool_use_ids exclusion set,
    and a missing caller-supplied set would silently regress to indexing
    every reviewer-subagent write as if it were real fix work. A code-write
    block whose own id is in that set is skipped outright — it is either
    this dispatch's own findings-file write or a sibling reviewer's, neither
    of which reflects real fix work. A code-writer (or any non-reviewer)
    subagent's edits are never in that set, since _is_reviewer_subagent_type
    cannot match "code-writer", and are indexed normally.

    A record with no cwd field indexes its edit under a key normalized
    against "" rather than being skipped, so it can silently miss a join
    against a citation whose own cwd is a real path — low-likelihood, since
    Claude Code populates cwd on essentially every record, and not currently
    tested.
    """
    index: dict[str, float] = {}
    for rec in records:
        if rec.get("type") != "assistant":
            continue
        rec_ts = corpus._parse_ts(rec.get("timestamp"))
        if rec_ts is None:
            continue
        if since_ts is not None and rec_ts < since_ts:
            continue
        cwd = rec.get("cwd") or ""
        for block in (rec.get("message") or {}).get("content") or []:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            if block.get("name") not in corpus._CODE_WRITE_TOOLS:
                continue
            if block.get("id") in reviewer_write_tool_use_ids:
                continue
            raw_path = _code_write_target_path(block.get("input") or {})
            if not raw_path:
                continue
            key = _normalize_cited_path(raw_path, cwd)
            if key is not None:
                index[key] = max(index.get(key, float("-inf")), rec_ts)
    return index


# Both "~/.claude/plans/x.md" and a repo-relative ".claude/plans/x.md" share
# this literal tail, so a substring check needs no cwd or normalization.
_CITED_PATH_PLAN_FILE_MARKER = ".claude/plans/"


def _is_plan_file_candidate(candidate: str) -> bool:
    """True for a candidate citing a plan file under ~/.claude/plans/ or an
    in-repo .claude/plans/ — a /plan-review dispatch routinely cites the very
    plan the parent session then edits, a guaranteed self-match that would
    otherwise inflate the cited/edited overlap with no fix-work signal."""
    return _CITED_PATH_PLAN_FILE_MARKER in candidate


def _dispatch_self_reference_keys(write_target_paths: list[str], transcript_cwd: str) -> set[str]:
    """Normalized keys of this dispatch's own Write targets (its findings
    file and any other file it wrote) — a path-normalized set-membership
    exclusion, not free-text prose matching. The dispatching parent's prompt
    routinely names the very files under review ("review foo.py, bar.py"),
    so extracting candidates from that prompt text and excluding all of them
    would silently drop legitimate citations of files that really were the
    ones with the issue; the reviewer's own recorded Write targets carry no
    such false-positive risk.
    """
    keys: set[str] = set()
    for target in write_target_paths:
        key = _normalize_cited_path(target, transcript_cwd)
        if key is not None:
            keys.add(key)
    return keys


def _reviewer_yield_cited_keys(
    last_assistant_text: str, write_content_blobs: list[str], cwd: str, self_ref_keys: set[str]
) -> set[str]:
    """Normalized citation keys for one reviewer dispatch: candidates from
    both the last assistant text and every Write blob (deduplicated via set
    union), minus plan-file self-matches and the dispatch's own
    self-referenced paths (see _is_plan_file_candidate and
    _dispatch_self_reference_keys)."""
    raw_candidates = _extract_cited_paths(last_assistant_text)
    for blob in write_content_blobs:
        raw_candidates |= _extract_cited_paths(blob)
    keys: set[str] = set()
    for candidate in raw_candidates:
        if _is_plan_file_candidate(candidate):
            continue
        key = _normalize_cited_path(candidate, cwd)
        if key is None or key in self_ref_keys:
            continue
        keys.add(key)
    return keys


def compute_reviewer_yield_data(
    session_iter,
    since_ts: float | None = None,
    until_ts: float | None = None,
) -> dict:
    """Corpus-wide reviewer-dispatch accumulation behind both
    cmd_reviewer_yield's own report and cost-ledger's per-week
    reviewer_gap_pp, extracted so the two share one pass over session_iter
    instead of two implementations kept in sync by hand.

    since_ts/until_ts are explicit epoch-second boundaries (until_ts
    exclusive) rather than _parse_since_nd_arg's relative-day CLI parsing,
    which has no until concept, so a caller can bound an exact week. Only
    the reviewer-dispatch detection loop applies until_ts — the paired
    tool_result/edit-index helpers below it stay since_ts-only, matching
    cmd_reviewer_yield's own pre-existing (unwindowed) use of them.

    session_iter must be built with include_subagents=True — the edit index
    and the reviewer-write exclusion pre-pass both assume every subagent
    file's records are already merged into each session's records list. This
    is a documentation-only contract: session_iter is an opaque iterator with
    no type signal of how it was built, so nothing here enforces it mechanically.
    """
    # agent_type -> {dispatches, findings_found, zero_finding, unclassified, total_findings}
    agg: dict[str, dict[str, int]] = defaultdict(
        lambda: {"dispatches": 0, "findings_found": 0, "zero_finding": 0, "unclassified": 0, "total_findings": 0}
    )
    # (agent_type, bucket) -> {cited, active, edited}
    agg2: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: {"cited": 0, "active": 0, "edited": 0})
    meta_read_errors = 0
    transcript_read_errors = 0
    subagent_spawns = 0
    sidechain_turns = 0

    for jsonl, records in session_iter:
        dispatch_index, session_meta_read_errors = _index_subagent_dispatches(jsonl)
        meta_read_errors += session_meta_read_errors
        subagent_spawns += pricing._count_subagent_spawns(records)

        tool_result_ts = _build_tool_result_ts_map(records, since_ts)
        # Session-wide edit index: every code-write recorded inside a
        # reviewer-typed subagent transcript (its own findings write, or a
        # sibling reviewer's) is excluded below, so routine review
        # bookkeeping can't inflate Active for a dispatch with no real fix
        # work behind it.
        # Cost: measured at ~104s added wall-clock over a 6-root --since 30d run
        # (53.8s parent-only vs 157.7s subagent-inclusive; see docs/transcript-analysis.md).
        reviewer_scans = _scan_reviewer_transcripts(records, dispatch_index)
        reviewer_write_ids = _reviewer_write_tool_use_ids(reviewer_scans)
        edit_index = _index_session_edits(records, since_ts, reviewer_write_tool_use_ids=reviewer_write_ids)
        overall_max_edit_ts = max(edit_index.values()) if edit_index else None

        for rec in records:
            if rec.get("type") != "assistant":
                continue
            if bool(rec.get("isSidechain")):
                sidechain_turns += 1
                continue
            if since_ts is not None or until_ts is not None:
                rec_ts = corpus._parse_ts(rec.get("timestamp"))
                if rec_ts is None:
                    continue
                if since_ts is not None and rec_ts < since_ts:
                    continue
                if until_ts is not None and rec_ts >= until_ts:
                    continue
            for block in ((rec.get("message") or {}).get("content") or []):
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                if block.get("name") not in pricing._SPAWN_TOOL_NAMES:
                    continue
                block_input = block.get("input") or {}
                stype = block_input.get("subagent_type") or ""
                if not _is_reviewer_subagent_type(stype):
                    continue
                tool_use_id = block.get("id") or ""
                paired = dispatch_index.get(tool_use_id)
                if paired is None:
                    continue  # no matching meta.json — excluded entirely, not "unclassified"
                # The pre-pass above scans every reviewer dispatch resolvable
                # through dispatch_index with no isSidechain/window filter, a
                # strict superset of this loop's own filters — a lookup miss
                # here would be a programming error, not a legitimate gap.
                scan = reviewer_scans[tool_use_id]
                if scan.read_error:
                    transcript_read_errors += 1
                bucket, n = _classify_reviewer_verdict(scan.last_assistant_text)
                row = agg[stype]
                row["dispatches"] += 1
                if bucket == _REVIEWER_VERDICT_FINDINGS_FOUND:
                    row["findings_found"] += 1
                    row["total_findings"] += n
                elif bucket == _REVIEWER_VERDICT_ZERO_FINDING:
                    row["zero_finding"] += 1
                else:
                    row["unclassified"] += 1

                if bucket == _REVIEWER_VERDICT_UNCLASSIFIED:
                    continue  # table 2 reports "excluded" for this bucket — no citation scoring

                self_ref_keys = _dispatch_self_reference_keys(scan.write_target_paths, scan.transcript_cwd)
                cited_keys = _reviewer_yield_cited_keys(
                    scan.last_assistant_text, scan.write_content_blobs, scan.transcript_cwd, self_ref_keys
                )
                if not cited_keys:
                    continue
                row2 = agg2[(stype, bucket)]
                row2["cited"] += 1

                threshold = tool_result_ts.get(tool_use_id)
                if threshold is None:
                    continue  # no paired tool_result, or its timestamp was unparseable — ordering undecidable
                if overall_max_edit_ts is None or overall_max_edit_ts <= threshold:
                    continue  # no qualifying edit anywhere in the session after this dispatch returned
                row2["active"] += 1
                if any(edit_index.get(k, float("-inf")) > threshold for k in cited_keys):
                    row2["edited"] += 1

    return {
        "agg": agg,
        "agg2": agg2,
        "meta_read_errors": meta_read_errors,
        "transcript_read_errors": transcript_read_errors,
        "subagent_spawns": subagent_spawns,
        "sidechain_turns": sidechain_turns,
    }


def cmd_reviewer_yield(args: argparse.Namespace) -> None:
    """Per-reviewer-agent-type dispatch-to-verdict yield, plus cited-path edit overlap.

    Joins each main-thread reviewer-agent dispatch (Agent/Task tool_use with
    subagent_type in the reviewer set — _REVIEWER_PREFIX/_REVIEWER_EXACT_NAMES)
    to its own subagent
    transcript via subagents/<id>.meta.json's toolUseId field, then
    classifies that transcript's last assistant text block as findings-found,
    zero-finding, or unclassified. A dispatch with no matching meta.json is
    excluded entirely (not counted as unclassified) — meta.json is the only
    signal that a subagent transcript for this dispatch exists at all. A
    second, distinct exclusion path is a meta.json file that exists but is
    unreadable (invalid JSON) or missing toolUseId — also excluded entirely,
    and corpus-wide counted in the printed meta-read-errors line.

    A "findings-found" verdict comes from either a numeric "Found <N>
    issues" verdict (contributes N to the Findings column) or a bulleted
    "Approve with concerns"/"Request changes" verdict with no derivable
    count (contributes 0) — the printed Findings total is therefore a lower
    bound on actual findings, not an exact count.

    A second table reports, per (agent type, bucket), whether the dispatch's
    own cited paths were later edited: Cited (>=1 extracted citation after
    excluding the dispatch's own self-referenced/plan-file candidates),
    Active (of those, the session recorded ANY code edit afterward, the null
    control for "was the session still working at all"), and Edited (of the
    Active ones, a cited path itself was among the edited paths). Rate =
    Edited / Active, so it cannot exceed 100%. Active/Edited count edits
    inside subagent transcripts too, not just parent-main-thread ones. A
    reviewer agent's own writes — its own findings file and any sibling
    reviewer's, keyed by the dispatch's subagent_type — are excluded from the
    edit index, so routine review bookkeeping can't inflate Active for a
    dispatch with no real fix work behind it. The unclassified
    bucket is not scored (prints "excluded" for Cited/Active/Edited/Rate) —
    an unreadable subagent transcript lands there via its empty verdict text
    and is separately counted in the printed read-error line, never entered
    as a legitimate zero-citation dispatch.

    --redact is accepted for CLI parity with cost/audit-routing. Cited-path
    candidates are held only as sha256 digests (_normalize_cited_path), never
    as raw paths, so no path can reach this subcommand's aggregate-only
    output by construction — this does not cover the pre-existing
    --projects scope-header line (_print_resolved_scope), a separate,
    unfixed channel shared by every subcommand.

    Delegates its entire accumulation to compute_reviewer_yield_data instead
    of running its own pass over session_iter, so this report and
    cost-ledger's per-week reviewer_gap_pp can never drift apart.
    """
    since_ts, since_raw = scope._parse_since_nd_arg(args, "reviewer-yield")
    since_label = since_raw or ""

    roots = scope.resolve_scan_roots(args)
    session_iter, scope_label = scope._resolve_project_scope(
        args, "reviewer-yield", include_subagents=True, roots=roots
    )
    scope.print_resolved_scope("reviewer-yield", scope_label, roots)

    data = compute_reviewer_yield_data(session_iter, since_ts=since_ts)
    agg = data["agg"]
    agg2 = data["agg2"]
    meta_read_errors = data["meta_read_errors"]
    transcript_read_errors = data["transcript_read_errors"]
    pricing._warn_if_subagent_format_drift(data["subagent_spawns"], data["sidechain_turns"])

    title_since = f"last {since_label}" if since_label else "all time"
    print(f"\n## Reviewer-agent yield ({title_since})\n")

    if not agg:
        print("No reviewer-agent dispatches found.")
        if meta_read_errors:
            print(f"  ({meta_read_errors:,} meta.json files failed to parse, excluded)")
        return

    # Findings is a lower bound: it sums parsed "Found <N> issues" counts plus
    # 0 for each uncounted "Approve with concerns"/"Request changes" verdict.
    header = f"{'AgentType':<28} {'Dispatches':>10} {'Found':>7} {'Zero':>6} {'Unclass':>8} {'Findings':>9}"
    print(header)
    print("-" * len(header))
    for stype in sorted(agg):
        row = agg[stype]
        print(
            f"{stype:<28} {row['dispatches']:>10} {row['findings_found']:>7} "
            f"{row['zero_finding']:>6} {row['unclassified']:>8} {row['total_findings']:>9}"
        )
    if meta_read_errors:
        print(f"\n  ({meta_read_errors:,} meta.json files failed to parse, excluded)")

    print(f"\n## Reviewer-agent cited-path edit overlap ({title_since})\n")
    header2 = (
        f"{'AgentType':<28} {'Bucket':<15} {'Dispatches':>10} {'Cited':>6} {'Active':>6} {'Edited':>6} {'Rate':>12}"
    )
    print(header2)
    print("-" * len(header2))
    for stype in sorted(agg):
        row = agg[stype]
        for bucket, dispatches in (
            (_REVIEWER_VERDICT_FINDINGS_FOUND, row["findings_found"]),
            (_REVIEWER_VERDICT_ZERO_FINDING, row["zero_finding"]),
            (_REVIEWER_VERDICT_UNCLASSIFIED, row["unclassified"]),
        ):
            if dispatches == 0:
                continue
            if bucket == _REVIEWER_VERDICT_UNCLASSIFIED:
                cited_s = active_s = edited_s = rate_s = "excluded"
            else:
                row2 = agg2[(stype, bucket)]
                cited_s, active_s, edited_s = str(row2["cited"]), str(row2["active"]), str(row2["edited"])
                rate_s = (
                    _REVIEWER_YIELD_INSUFFICIENT
                    if row2["active"] < _REVIEWER_YIELD_ACTIVE_FLOOR
                    else f"{row2['edited'] / row2['active']:>6.1%}"
                )
            print(
                f"{stype:<28} {bucket:<15} {dispatches:>10} {cited_s:>6} {active_s:>6} {edited_s:>6} {rate_s:>12}"
            )
    print("\n  (Active/Edited count edits inside subagent transcripts too. A reviewer agent's own writes are excluded.)")
    if transcript_read_errors:
        print(f"\n  ({transcript_read_errors:,} reviewer transcripts failed to read, excluded from Cited)")
