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


def _seed_pr_cost_export_account(config_dir: Path) -> None:
    """Build one pr-cost-export account: a `projects/` subdirectory
    (declared_transcript_roots' own is_valid check requires it, even though
    pr-cost-export never scans it), the .pr-cost-enabled opt-in sentinel, and
    a single-row pr-cost ledger. The header and row are literal here rather
    than derived from the module under test -- this test invokes it as an
    opaque subprocess, so it can't import _PR_COST_LEDGER_COLUMNS."""
    (config_dir / "projects").mkdir(parents=True)
    (config_dir / ".pr-cost-enabled").touch()
    header = (
        "host\trepo\tpr_number\tmachine\thead_branch\tmerged_at\trate_stamp\tcaptured_at"
        "\tjoin_confidence\tsupersedes\tstatus\tcache_read_usd\tcache_write_5m_usd\tcache_write_1h_usd"
        "\toutput_usd\tinput_usd\tcache_read_tokens\tcache_write_5m_tokens\tcache_write_1h_tokens"
        "\toutput_tokens\tinput_tokens\tunpriced_turns\tunpriced_tokens\tturn_count\tsession_count"
        "\topus_dollars\topus_dollar_share_pct\tsum_context_at_turn\tmean_context_at_turn"
        "\tadditions\tdeletions\tchanged_files\tcommit_count\treview_comment_count"
        "\tdistinct_top_level_dirs\tdistinct_file_extensions\ttests_changed\tplan_file_added\trisk_surface_flag"
    )
    row = (
        "github.com\towner/repo\t42\tci1\tbranch-1\t2026-01-01T00:00:00Z\t2026-08-02\t2026-01-02T00:00:00Z"
        "\thigh\t\tok\t1.500000\t0.250000\t0.100000\t2.000000\t0.500000\t1000\t200\t100\t500\t300\t0\t0\t5\t2"
        "\t0.000000\t0.000000\t1500\t300.000000\t42\t10\t3\t4\t1\t2\t3\ttrue\ttrue\tfalse"
    )
    (config_dir / "pr-cost-ledger.tsv").write_text(header + "\n" + row + "\n")


def test_transcript_analysis_pr_cost_export_subprocess_writes_synthetic_two_account_rows(tmp_path):
    """Automated replacement for the plan's earlier manual smoke-check step --
    that step had no visible way to confirm the tester was actually pointed at
    synthetic roots rather than silently scanning real accounts. Seeds two
    synthetic accounts via CLAUDE_CONFIG_DIR (the active profile) and
    TRANSCRIPT_CONFIG_DIRS_FILE (one declared root), matching the recipe
    docs/transcript-analysis.md documents for smoke-testing any
    _SUBCOMMANDS_WITH_OWN_CONFIG_DIR subcommand against synthetic data, and
    asserts the export's provenance line flags the run as corpus_override=1."""
    acct_a = tmp_path / "acct-a"
    acct_b = tmp_path / "acct-b"
    _seed_pr_cost_export_account(acct_a)
    _seed_pr_cost_export_account(acct_b)
    roots_file = tmp_path / "roots"
    roots_file.write_text(f"{acct_b}\n")
    out_path = tmp_path / "export.tsv"
    env = {
        **os.environ,
        "CLAUDE_CONFIG_DIR": str(acct_a),
        "TRANSCRIPT_CONFIG_DIRS_FILE": str(roots_file),
    }

    result = _run("transcript-analysis.py", "pr-cost-export", "--out", str(out_path), env=env)

    assert result.returncode == 0, result.stderr
    written = out_path.read_text()
    provenance = written.splitlines()[0]
    assert "corpus_override=1" in provenance
    account_cells = [line.split("\t")[0] for line in written.splitlines()[2:]]
    assert account_cells == ["account-1", "account-2"]
    # Proves redaction actually happens across the subprocess boundary, not
    # just in-process (every other pr-cost-export test in this suite invokes
    # the module directly): the seeded ledger's raw host/repo/branch literals
    # must not survive into the written export as their own cell value.
    # Exact-cell comparison, not substring containment, since the redacted
    # head_branch_label token (account-N/branch-M) legitimately contains
    # "branch-1" as a substring for the first account.
    written_cells = {cell for line in written.splitlines()[2:] for cell in line.split("\t")}
    assert "github.com" not in written_cells
    assert "owner/repo" not in written_cells
    assert "branch-1" not in written_cells


def test_transcript_analysis_reviewer_yield_help_exits_zero():
    result = _run("transcript-analysis.py", "reviewer-yield", "--help")
    assert result.returncode == 0, result.stderr
    assert "--since" in result.stdout


def _seed_reviewer_dispatch_account(tmp_path: Path) -> Path:
    """Build a single-account config dir with one reviewer-agent dispatch and
    its paired subagent transcript -- reviewer-yield's join needs both the
    main-thread Agent tool_use and subagents/<id>.meta.json."""
    config_dir = tmp_path / "account"
    proj = config_dir / "projects" / "-home-user-bootstraprepo"
    session_id = "s"
    proj.mkdir(parents=True)
    tool_use_id = "t1"
    main_record = {
        "type": "assistant",
        "gitBranch": "main",
        "isSidechain": False,
        "timestamp": "2026-05-19T10:00:00.000Z",
        "message": {
            "model": "claude-opus-4-7",
            "content": [{
                "type": "tool_use", "id": tool_use_id, "name": "Agent",
                "input": {"subagent_type": "staff-backend-engineer", "description": "review", "prompt": "review it"},
            }],
            "usage": {},
        },
    }
    (proj / f"{session_id}.jsonl").write_text(json.dumps(main_record) + "\n")

    subagent_dir = proj / session_id / "subagents"
    subagent_dir.mkdir(parents=True)
    subagent_record = {
        "type": "assistant",
        "gitBranch": "main",
        "isSidechain": True,
        "message": {"model": "claude-sonnet-4-6", "content": [{"type": "text", "text": "No concerns found."}], "usage": {}},
    }
    (subagent_dir / "agent-t1.jsonl").write_text(json.dumps(subagent_record) + "\n")
    meta = {"agentType": "staff-backend-engineer", "description": "review", "toolUseId": tool_use_id, "spawnDepth": 1}
    (subagent_dir / "agent-t1.meta.json").write_text(json.dumps(meta))
    return config_dir


def test_transcript_analysis_reviewer_yield_subprocess_finds_seeded_dispatch(tmp_path):
    """Proves `from transcript_analysis import reviewer_yield` resolves under a
    real subprocess -- no in-process `_mod.cmd_reviewer_yield(...)` test can see
    a broken re-export in the real shim entrypoint."""
    config_dir = _seed_reviewer_dispatch_account(tmp_path)

    result = _run("transcript-analysis.py", "--config-dir", str(config_dir), "reviewer-yield")

    assert result.returncode == 0, result.stderr
    assert "staff-backend-engineer" in result.stdout
