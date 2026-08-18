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
import subprocess
import sys
from pathlib import Path

from helpers import REPO_ROOT

SCRIPTS_DIR = REPO_ROOT / "claude" / ".claude" / "scripts"


def _run(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable] + list(args),
        cwd=cwd or SCRIPTS_DIR,
        capture_output=True,
        text=True,
        timeout=30,
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
