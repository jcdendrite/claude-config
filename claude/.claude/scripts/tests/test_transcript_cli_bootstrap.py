"""Real-subprocess invocation of the CLI shim and its two sibling scripts.

Every other test in this suite loads these scripts via
`importlib.util.spec_from_file_location` + `exec_module`, with pytest's own
`pythonpath` ini entry making `from transcript_analysis import ...` resolve.
A real `python3 transcript-analysis.py ...` invocation has neither of those --
it resolves the package purely through `sys.path[0]` (the script's own
directory, which CPython sets for a directly-invoked script). This is the one
bootstrap path no `_mod.cmd_*`-style test exercises, and the one a later
phase's import changes could break silently while every in-process test still
passes.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from helpers import REPO_ROOT

SCRIPTS_DIR = REPO_ROOT / "claude" / ".claude" / "scripts"


def _run(
    *args: str, cwd: Path | None = None, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable] + list(args),
        cwd=cwd or SCRIPTS_DIR,
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )


def test_transcript_analysis_help_exits_zero():
    result = _run("transcript-analysis.py", "--help")
    assert result.returncode == 0, result.stderr
    assert "buckets" in result.stdout


def test_token_analyzer_help_exits_zero():
    result = _run("token-analyzer.py", "--help")
    assert result.returncode == 0, result.stderr


def test_analyze_context_help_exits_zero():
    result = _run("analyze-context.py", "--help")
    assert result.returncode == 0, result.stderr


def test_transcript_analysis_turn_shape_help_exits_zero():
    result = _run("transcript-analysis.py", "turn-shape", "--help")
    assert result.returncode == 0, result.stderr
    assert "--since" in result.stdout


def test_transcript_analysis_turn_shape_subprocess_finds_seeded_session(tmp_path):
    """A representative turn-shape run under a real subprocess, mirroring the
    buckets smoke test below for this newer subcommand."""
    config_dir = tmp_path / "account"
    proj = config_dir / "projects" / "-home-user-bootstraprepo"
    proj.mkdir(parents=True)
    session = {
        "type": "assistant",
        "gitBranch": "main",
        "isSidechain": False,
        "message": {
            "model": "claude-sonnet-5",
            "content": [{"type": "tool_use", "id": "b1", "name": "Bash", "input": {"command": "ls"}}],
            "usage": {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 0},
        },
    }
    (proj / "s.jsonl").write_text(json.dumps(session) + "\n")

    result = _run("transcript-analysis.py", "--config-dir", str(config_dir), "turn-shape")

    assert result.returncode == 0, result.stderr
    assert "Tool calls per turn" in result.stdout


def test_transcript_analysis_turn_shape_samples_help_exits_zero():
    result = _run("transcript-analysis.py", "turn-shape-samples", "--help")
    assert result.returncode == 0, result.stderr
    assert "--seed" in result.stdout


def test_transcript_analysis_turn_shape_samples_subprocess_finds_seeded_session(tmp_path):
    """A representative turn-shape-samples run under a real subprocess, mirroring
    the turn-shape smoke test above for this sibling subcommand."""
    config_dir = tmp_path / "account"
    proj = config_dir / "projects" / "-home-user-bootstraprepo"
    proj.mkdir(parents=True)
    session = {
        "type": "assistant",
        "gitBranch": "main",
        "isSidechain": False,
        "message": {
            "model": "claude-sonnet-5",
            "content": [{"type": "tool_use", "id": "b1", "name": "Bash", "input": {"command": "ls"}}],
            "usage": {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 0},
        },
    }
    (proj / "s.jsonl").write_text(json.dumps(session) + "\n")

    result = _run("transcript-analysis.py", "--config-dir", str(config_dir), "turn-shape-samples")

    assert result.returncode == 0, result.stderr
    assert "DO NOT PUBLISH" in result.stdout


def test_transcript_analysis_turn_shape_holdout_samples_help_exits_zero():
    result = _run("transcript-analysis.py", "turn-shape-holdout-samples", "--help")
    assert result.returncode == 0, result.stderr
    assert "--offset" in result.stdout


def test_transcript_analysis_turn_shape_holdout_samples_subprocess_finds_seeded_session(tmp_path):
    """A representative turn-shape-holdout-samples run under a real subprocess,
    mirroring the turn-shape-samples smoke test above for this sibling
    subcommand."""
    config_dir = tmp_path / "account"
    proj = config_dir / "projects" / "-home-user-bootstraprepo"
    proj.mkdir(parents=True)
    session = {
        "type": "assistant",
        "gitBranch": "main",
        "isSidechain": False,
        "message": {
            "model": "claude-sonnet-5",
            "content": [{"type": "tool_use", "id": "b1", "name": "Bash", "input": {"command": "ls"}}],
            "usage": {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 0},
        },
    }
    (proj / "s.jsonl").write_text(json.dumps(session) + "\n")

    result = _run("transcript-analysis.py", "--config-dir", str(config_dir), "turn-shape-holdout-samples")

    assert result.returncode == 0, result.stderr
    assert "DO NOT PUBLISH" in result.stdout


def test_transcript_analysis_buckets_subprocess_finds_seeded_session(tmp_path):
    """A representative subcommand run, not just --help: proves the package
    import resolves far enough for scope.PROJECTS_DIR, corpus.iter_sessions,
    and render's table formatting to all run correctly under a real subprocess,
    not only under pytest's import machinery."""
    config_dir = tmp_path / "account"
    proj = config_dir / "projects" / "-home-user-bootstraprepo"
    proj.mkdir(parents=True)
    session = {
        "type": "assistant",
        "gitBranch": "subprocess-bootstrap-marker",
        "isSidechain": False,
        "message": {"model": "claude-sonnet-5", "content": [], "usage": {}},
    }
    (proj / "s.jsonl").write_text(json.dumps(session) + "\n")

    result = _run("transcript-analysis.py", "--config-dir", str(config_dir), "buckets")

    assert result.returncode == 0, result.stderr
    assert "subprocess-bootstrap-marker" in result.stdout, (
        f"seeded session's branch never surfaced in `buckets` output: {result.stdout!r}"
    )


def _seed_priced_account(tmp_path: Path) -> Path:
    """Build a single-account config dir with one priced turn ($2.00 --
    claude-sonnet-5's $2/MTok input rate on 1M input tokens), seeded with a
    real timestamp so both cost's report and cost-trend's per-week bucketing
    pick it up."""
    config_dir = tmp_path / "account"
    proj = config_dir / "projects" / "-home-user-bootstraprepo"
    proj.mkdir(parents=True)
    turn = {
        "type": "assistant",
        "gitBranch": "main",
        "isSidechain": False,
        "timestamp": "2026-05-19T10:00:00.000Z",
        "message": {
            "model": "claude-sonnet-5",
            "content": [],
            "usage": {
                "input_tokens": 1_000_000,
                "output_tokens": 0,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
        },
    }
    (proj / "s.jsonl").write_text(json.dumps(turn) + "\n")
    return config_dir


def _isolated_config_env(config_dir: Path, tmp_path: Path) -> dict[str, str]:
    """cost/cost-trend resolve roots via _resolve_cost_roots, which reads
    config_dir() (CLAUDE_CONFIG_DIR) and declared_transcript_roots()
    (TRANSCRIPT_CONFIG_DIRS_FILE) directly -- neither goes through the
    top-level --config-dir flag main() refuses for this subcommand family
    (_SUBCOMMANDS_WITH_OWN_CONFIG_DIR), and a real subprocess doesn't inherit
    conftest.py's autouse env-isolation fixture, so both must be pinned here
    to avoid scanning this workstation's real ~/.claude."""
    return {
        **os.environ,
        "CLAUDE_CONFIG_DIR": str(config_dir),
        "TRANSCRIPT_CONFIG_DIRS_FILE": str(tmp_path / "nonexistent-transcript-config-dirs"),
    }


def test_transcript_analysis_cost_subprocess_finds_seeded_session(tmp_path):
    """Proves `from transcript_analysis import cost` resolves under a real
    subprocess -- no in-process `_mod.cmd_cost(...)` test can see a broken
    re-export in the real shim entrypoint."""
    config_dir = _seed_priced_account(tmp_path)

    result = _run("transcript-analysis.py", "cost", env=_isolated_config_env(config_dir, tmp_path))

    assert result.returncode == 0, result.stderr
    assert "claude-sonnet-5" in result.stdout


def test_transcript_analysis_cost_trend_subprocess_finds_seeded_session(tmp_path):
    """Same subprocess-bootstrap proof as the `cost` test above, for
    cmd_cost_trend's own re-export."""
    config_dir = _seed_priced_account(tmp_path)

    result = _run("transcript-analysis.py", "cost-trend", env=_isolated_config_env(config_dir, tmp_path))

    assert result.returncode == 0, result.stderr
    assert "2026-W21" in result.stdout  # ISO week of the seeded 2026-05-19 timestamp
