"""Shared git-repo scaffolding helpers for the worktree-cleanup script tests
(test_cleanup_merged_branches.py, test_cleanup_idle_open_pr_worktrees.py),
plus suite-wide transcript-corpus isolation (see the autouse fixture below),
plus the transcript-record fixture builders shared by
test_transcript_analysis.py and test_context_composition.py (see the
extraction rationale on _write_jsonl below).

The scaffolding helpers are plain functions, not pytest fixtures — they take
`tmp_path` (or a repo built from it) as an explicit argument rather than
being injected, matching the calling convention already established in
test_cleanup_merged_branches.py. They have no shape-specific dependency on
either script's `gh` query: building a local git repo, a feature branch, and
a worktree is identical regardless of which script is under test.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest


def _write_jsonl(path: Path, records: list[dict]) -> None:
    """Write records as one-JSON-object-per-line, transcript-analysis.py's on-disk shape -- shared
    here (with _asst/_user_msg) so test_context_composition.py doesn't re-derive its own,
    possibly-drifting copy of the requestId run-merge shape _dedup_turns_by_request_id relies on."""
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")


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
