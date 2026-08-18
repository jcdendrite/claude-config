"""Shared git-repo scaffolding helpers for the worktree-cleanup script tests
(test_cleanup_merged_branches.py, test_cleanup_idle_open_pr_worktrees.py),
plus suite-wide transcript-corpus isolation (see the autouse fixture below),
plus the transcript-record fixture builders shared across
test_transcript_analysis.py, test_transcript_cost.py, and
test_context_composition.py (see the extraction rationale on _write_jsonl
below).

The scaffolding helpers are plain functions, not pytest fixtures — they take
`tmp_path` (or a repo built from it) as an explicit argument rather than
being injected, matching the calling convention already established in
test_cleanup_merged_branches.py. They have no shape-specific dependency on
either script's `gh` query: building a local git repo, a feature branch, and
a worktree is identical regardless of which script is under test.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest
from transcript_analysis.corpus import SUBAGENT_SUBDIR


def _write_jsonl(path: Path, records: list[dict]) -> None:
    """Write records as one-JSON-object-per-line, transcript-analysis.py's on-disk shape -- shared
    here (with _asst/_user_msg) so test_context_composition.py doesn't re-derive its own,
    possibly-drifting copy of the requestId run-merge shape _dedup_turns_by_request_id relies on."""
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")


def _write_subagent_jsonl(
    proj: Path, session_id: str, agent_id: str, records: list[dict]
) -> None:
    """Write records to the split subagent layout: <session_id>/subagents/<agent_id>.jsonl."""
    subdir = proj / session_id / SUBAGENT_SUBDIR
    subdir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(subdir / f"{agent_id}.jsonl", records)


def _write_cost_root(base: Path, name: str, proj_slug: str, session_id: str, records: list[dict]) -> Path:
    """Build one --config-dir root's project-dir tree — same shape as
    fake_projects' own PROJECTS_DIR (the root directly contains project-slug
    subdirectories, no extra projects/ layer), parameterized so multi-root
    tests can build more than one root under the same tmp_path."""
    root = base / name
    proj = root / proj_slug
    proj.mkdir(parents=True)
    _write_jsonl(proj / f"{session_id}.jsonl", records)
    return root


def _asst(
    model: str,
    *,
    branch: str = "main",
    sidechain: bool = False,
    ts: str | None = None,
    content: list | None = None,
    request_id: str | None = None,
) -> dict:
    rec: dict = {
        "type": "assistant",
        "gitBranch": branch,
        "isSidechain": sidechain,
        "message": {"model": model, "content": content or [], "usage": {}},
    }
    if ts:
        rec["timestamp"] = ts
    if request_id is not None:
        rec["requestId"] = request_id
    return rec


def _user_msg(content, *, branch: str = "main", ts: str | None = None) -> dict:
    rec: dict = {"type": "user", "gitBranch": branch, "message": {"content": content}}
    if ts:
        rec["timestamp"] = ts
    return rec


def _bash_use(tool_id: str, command: str) -> dict:
    return {"type": "tool_use", "id": tool_id, "name": "Bash", "input": {"command": command}}


def _tool_result(tool_id: str, text: str) -> dict:
    return {"type": "tool_result", "tool_use_id": tool_id, "content": text}


def _agent_use(tool_id: str, subagent_type: str, *, tool_name: str = "Agent", prompt: str = "y") -> dict:
    return {
        "type": "tool_use",
        "id": tool_id,
        "name": tool_name,
        "input": {"subagent_type": subagent_type, "description": "x", "prompt": prompt},
    }


def _opus(
    content: list, *, out: int = 100, cr: int = 0, ts: str = "2026-05-19T10:00:00.000Z",
    request_id: str | None = None,
) -> dict:
    """Build an Opus assistant record with explicit usage values -- shared by
    audit-routing's and cost's own tests."""
    rec = _asst(
        "claude-opus-4-7",
        branch="main",
        ts=ts,
        content=content,
        request_id=request_id,
    )
    rec["message"]["usage"] = {
        "input_tokens": 50,
        "output_tokens": out,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": cr,
    }
    return rec


def _priced(
    model: str,
    *,
    input: int = 0,
    cache_read: int = 0,
    ephemeral_1h: int = 0,
    ephemeral_5m: int = 0,
    output: int = 0,
    flat_cache_creation: int | None = None,
    ts: str = "2026-05-19T10:00:00.000Z",
    branch: str = "main",
    request_id: str | None = None,
    content: list | None = None,
    speed: str | None = None,
    inference_geo: str | None = None,
) -> dict:
    """Build an assistant record with explicit priced usage fields for cost tests.

    flat_cache_creation=None (the default) emits the nested cache_creation
    block from ephemeral_1h/ephemeral_5m, with the flat cache_creation_input_tokens
    field set to their sum — matching every real usage record sampled, where the
    two always agree. flat_cache_creation=N omits the nested block entirely and
    emits only the flat field (the pre-nested-block fallback shape), ignoring
    ephemeral_1h/ephemeral_5m. branch="main" by default so every pre-existing
    call site (predating --branches) is unaffected. content=None (the default)
    keeps every pre-existing call site's empty-content shape; rearm-backtest's
    boundary-detection tests pass real tool_use/tool_result blocks instead,
    needing both a realistic content shape and known, priced usage in one record.
    speed/inference_geo default to None (field absent), matching every usage
    record sampled outside fast-mode/data-residency requests.
    """
    rec = _asst(model, branch=branch, ts=ts, content=content if content is not None else [], request_id=request_id)
    usage: dict = {
        "input_tokens": input,
        "output_tokens": output,
        "cache_read_input_tokens": cache_read,
    }
    if flat_cache_creation is not None:
        usage["cache_creation_input_tokens"] = flat_cache_creation
    else:
        usage["cache_creation_input_tokens"] = ephemeral_1h + ephemeral_5m
        usage["cache_creation"] = {
            "ephemeral_1h_input_tokens": ephemeral_1h,
            "ephemeral_5m_input_tokens": ephemeral_5m,
        }
    if speed is not None:
        usage["speed"] = speed
    if inference_geo is not None:
        usage["inference_geo"] = inference_geo
    rec["message"]["usage"] = usage
    return rec


def _table_cols(out: str, *, header_contains: str, row_contains: str | list[str],
                drop_leading_labels: int = 0,
                max_labels: int | None = None,
                row_startswith: bool = False,
                occurrence: int | None = None) -> dict[str, str]:
    """Map column-label -> cell value for the data row matching `row_contains`.

    Anchors column positions to the header row (the line containing
    `header_contains`) instead of hard-coding indices, so a column reorder in
    the source output fails meaningfully rather than silently reading the wrong
    column.

    Precondition: every asserted column's header label AND cell value is a
    single whitespace token (true for all leading label/count columns; trailing
    free-text columns like "Top subagent types" are not assertable this way and
    are not asserted by any test). `drop_leading_labels` lets a caller declare
    that the row deliberately suppresses N leading left-aligned labels (the only
    case: cmd_subagents continuation rows blank the Branch column) — declared
    explicitly per call, never inferred.
    `max_labels` limits labels to only the first N single-token columns, required
    for tables whose header contains a trailing multi-word column name (e.g.,
    cmd_subagent_mix's "Top subagent types") whose tokens would otherwise inflate
    the label count beyond the data row's token count.
    `row_startswith=True` matches only lines where `row_contains` appears at
    column 0, filtering out indented summary/annotation lines that also contain
    the same text (e.g., cmd_skill_invocation summary section).

    Row search is always scoped to one table's own section -- from its header
    line through the next blank line or the next header-containing line -- so
    a second table elsewhere in the same output that happens to share row
    text (e.g. both tables use "main"/"sidechain" thread labels, or both
    start "AgentType") is never mistaken for this one's data. Without
    `occurrence`, `header_contains` must match exactly one line in the whole
    output. `occurrence` scopes to the Nth (1-indexed) line containing
    `header_contains` instead, for a header substring that legitimately
    repeats across tables (e.g. reviewer-yield's Table 1 and Table 2 both
    start "AgentType"). A table's own rule line ("-" * len(header), printed
    immediately after the header) is pure dashes, so it never matches a
    blank-line or header-match boundary check and needs no special-casing to
    stay inside the section.
    `row_contains` accepts a single string or a sequence of strings, all of
    which must appear on the matched line — needed once a table can hold more
    than one row per entity (e.g. two bucket rows per agent type), where a
    bare entity-name substring would match more than one line even within a
    single table's section.

    Fails loudly (AssertionError) when exactly one header / data row isn't
    found, or when token counts don't line up — a silent mismatch would
    reintroduce the GH-363 bug class under a new cause.
    """
    lines = out.splitlines()
    header_indices = [i for i, ln in enumerate(lines) if header_contains in ln]
    if occurrence is None:
        assert len(header_indices) == 1, f"header match not unique for {header_contains!r}: {len(header_indices)}"
        start = header_indices[0]
    else:
        assert len(header_indices) >= occurrence, (
            f"header occurrence {occurrence} requested but only {len(header_indices)} "
            f"found for {header_contains!r}"
        )
        start = header_indices[occurrence - 1]
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if not lines[i].strip() or header_contains in lines[i]:
            end = i
            break
    section_lines = lines[start:end]

    headers = [ln for ln in section_lines if header_contains in ln]
    assert len(headers) == 1, f"header match not unique for {header_contains!r}: {len(headers)}"
    header = headers[0]

    needles = (row_contains,) if isinstance(row_contains, str) else tuple(row_contains)
    if row_startswith:
        rows = [
            ln for ln in section_lines
            if ln != header and ln.startswith(needles[0]) and all(n in ln for n in needles)
        ]
    else:
        rows = [ln for ln in section_lines if ln != header and all(n in ln for n in needles)]
    assert len(rows) == 1, f"row match not unique for {row_contains!r}: {len(rows)}"
    labels = header.split()[drop_leading_labels:]
    if max_labels is not None:
        labels = labels[:max_labels]
    values = rows[0].split()
    assert len(values) >= len(labels), f"row has fewer cells than labels: {rows[0]!r}"
    return dict(zip(labels, values, strict=False))


def _extract_grand_total(out: str) -> float:
    """Read cost's grand-total row ('total  $X.XX') from the token-class table."""
    match = re.search(r"^total\s+([\d,]+\.\d\d)\s*$", out, re.MULTILINE)
    assert match is not None, "grand total row not found in output"
    return float(match.group(1).replace(",", ""))


def _cost_args(
    *,
    projects: str = "*",
    this_repo: bool = False,
    since: str | None = None,
    top: int = 20,
    no_redact: bool = False,
    extra_config_dirs: list[str] | None = None,
    by_project: bool = False,
    branches: str | None = None,
    summary: bool = False,
) -> object:
    return type("A", (), {
        "projects": projects,
        "this_repo": this_repo,
        "since": since,
        "top": top,
        "no_redact": no_redact,
        "extra_config_dirs": extra_config_dirs,
        "by_project": by_project,
        "branches": branches,
        "summary": summary,
    })()


def _cost_trend_args(
    *, projects: str = "*", this_repo: bool = False, extra_config_dirs: list[str] | None = None,
) -> object:
    return type("A", (), {
        "projects": projects, "this_repo": this_repo, "extra_config_dirs": extra_config_dirs,
    })()


def _context_distribution_args(
    *,
    projects: str = "*",
    this_repo: bool = False,
    since: str | None = None,
    no_redact: bool = False,
    extra_config_dirs: list[str] | None = None,
) -> object:
    return type("A", (), {
        "projects": projects,
        "this_repo": this_repo,
        "since": since,
        "no_redact": no_redact,
        "extra_config_dirs": extra_config_dirs,
    })()


def _audit_routing_args(
    *,
    projects: str = "*",
    this_repo: bool = False,
    since: str | None = None,
    top: int = 20,
    redact: bool = False,
) -> object:
    return type("A", (), {
        "projects": projects,
        "this_repo": this_repo,
        "since": since,
        "top": top,
        "redact": redact,
    })()


@pytest.fixture()
def fake_projects(tmp_path, monkeypatch, request):
    """Isolated single-account corpus: patches scope.PROJECTS_DIR and
    scope.config_dir on the calling test file's own loaded transcript-analysis.py
    module (`request.module._mod`) -- every test file loads its own independent
    copy via spec_from_file_location, but each copy's `from transcript_analysis
    import scope` resolves to the one process-wide transcript_analysis.scope
    module, so patching `mod.scope` here is visible regardless of which file's
    `_mod` requested the fixture.

    _resolve_cost_roots (subagents, subagent-mix, cost, context-distribution)
    derives its default root from a fresh config_dir() call, not from the
    PROJECTS_DIR patch above — without this, a subcommand routed through
    _resolve_cost_roots would silently fall back to this machine's real
    config dir instead of this fixture's isolated tmp_path. cost-ledger,
    spend-over-threshold, and rearm-backtest stay in the shim (not yet moved into
    the package) and call config_dir() via their own separate import, so
    mod.config_dir is patched too -- two bindings of the same initial value,
    each the sole read path for its own still-independent call sites.
    """
    mod = request.module._mod
    projects = tmp_path / "projects"
    proj = projects / "-home-user-testrepo"
    proj.mkdir(parents=True)
    monkeypatch.setattr(mod.scope, "PROJECTS_DIR", projects)
    monkeypatch.setattr(mod.scope, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(mod, "config_dir", lambda: tmp_path)
    return proj


@pytest.fixture()
def fake_config_dir_factory(tmp_path):
    """Factory for extra --config-dir roots: each call builds a fresh config
    dir (with its own projects/ subdirectory) under tmp_path, independent of
    fake_projects' own PROJECTS_DIR — cost's multi-root tests use this to
    build a second (and third) account profile."""
    def _make(name: str) -> Path:
        config_dir_path = tmp_path / name
        (config_dir_path / "projects").mkdir(parents=True)
        return config_dir_path
    return _make


@pytest.fixture()
def cost_ledger_file(tmp_path, monkeypatch, request):
    """Isolated docs/cost-ledger.md: a fresh file with the canonical header/
    separator and zero data rows, matching the real committed file's own
    shape. _cost_ledger_path is monkeypatched (on the calling test file's own
    `_mod` -- see fake_projects above for why `request.module._mod`) so every
    test in this section reads/writes this file, never this repo's own
    tracked ledger."""
    mod = request.module._mod
    ledger_path = tmp_path / "cost-ledger.md"
    ledger_path.write_text(
        "# Cost-trend ledger\n\n"
        + mod._COST_LEDGER_HEADER_LINE + "\n"
        + mod._COST_LEDGER_SEPARATOR_LINE + "\n"
    )
    monkeypatch.setattr(mod, "_cost_ledger_path", lambda: ledger_path)
    return ledger_path


@pytest.fixture(autouse=True)
def _isolate_transcript_corpus_lookups(tmp_path, monkeypatch):
    """Pin both env vars transcript-analysis.py's root resolution reads, so no
    test in this suite can accidentally scan or declare against this
    workstation's real ~/.claude.

    Pinning only TRANSCRIPT_CONFIG_DIRS_FILE is insufficient:
    _resolve_scan_roots/_resolve_cost_roots' base is PROJECTS_DIR/config_dir()
    (config_dir()/"projects" at import), and config_dir() reads $HOME when
    CLAUDE_CONFIG_DIR is unset — on a real workstation with a populated
    ~/.claude, an unpinned test would scan the real corpus; in CI, where
    $HOME/.claude is simply absent, the same test would pass for an unrelated
    reason. Both must be pinned for the isolation to be real rather than
    CI-only. TRANSCRIPT_CONFIG_DIRS_FILE points at a nonexistent path by
    default (declared_transcript_roots() treats a missing file as a silent
    single-root no-op), so an ordinary test never sees a declared root unless
    it opts in by writing that path itself.

    test_post_crash_sessions.py's own
    test_main_smoke_against_live_environment_no_traceback is a deliberate,
    documented exception to this isolation (it asserts no hardcoded counts,
    only a clean run) — this fixture does not special-case it, since pinning
    CLAUDE_CONFIG_DIR to an empty tmp dir still satisfies that test's actual
    assertions.
    """
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "isolated-claude-config"))
    monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(tmp_path / "nonexistent-transcript-config-dirs"))


def _init_repo(path: Path) -> None:
    """Initialise a git repo with one commit and a remote pointing at itself."""
    path.mkdir(parents=True, exist_ok=True)
    # --initial-branch=main avoids depending on the system's init.defaultBranch setting,
    # which varies across git versions and CI environments.
    subprocess.run(["git", "init", "-q", "--initial-branch=main"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=path, check=True)


def _commit(repo: Path, message: str = "commit") -> None:
    (repo / "file.txt").write_text(message + "\n")
    subprocess.run(["git", "add", "file.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, check=True)


def _make_repo_with_remote(tmp_path: Path) -> tuple[Path, Path]:
    """Return (local_repo, bare_remote) with origin configured and default branch set."""
    bare = tmp_path / "remote.git"
    bare.mkdir()
    subprocess.run(["git", "init", "--bare", "-q", "--initial-branch=main"], cwd=bare, check=True)

    local = tmp_path / "local"
    _init_repo(local)
    _commit(local, "init")
    subprocess.run(["git", "remote", "add", "origin", str(bare)], cwd=local, check=True)
    subprocess.run(["git", "push", "-q", "-u", "origin", "main"], cwd=local, check=True)
    # Set origin/HEAD so a caller relying on it can resolve the default branch
    subprocess.run(["git", "remote", "set-head", "origin", "main"], cwd=local, check=True)
    return local, bare


def _make_feature_branch(repo: Path, branch_name: str, return_to: str = "main") -> None:
    """Create and push a feature branch in repo, then return to return_to."""
    subprocess.run(["git", "checkout", "-q", "-b", branch_name], cwd=repo, check=True)
    _commit(repo, f"work on {branch_name}")
    subprocess.run(["git", "push", "-q", "origin", branch_name], cwd=repo, check=True)
    subprocess.run(["git", "checkout", "-q", return_to], cwd=repo, check=True)


def _make_worktree(repo: Path, branch_name: str, wt_path: Path) -> None:
    """Add a linked worktree for branch_name at wt_path."""
    subprocess.run(
        ["git", "worktree", "add", str(wt_path), branch_name],
        cwd=repo,
        check=True,
    )


def _dead_pid() -> int:
    """Return a pid that is guaranteed not to be running."""
    proc = subprocess.Popen(["true"])
    proc.wait()
    return proc.pid
