"""Tests for pr-cost-section.sh.

transcript-analysis.py is replaced in every test by a fake stand-in
co-located with a copy of the script under test, since the script always
resolves it via $(dirname "$0")/transcript-analysis.py -- a fixed relative
path next to the script itself, not something PATH or env can swap out.
The script also sources ../hooks/_lib.sh relative to its own location, so
the fixture directory mirrors that layout with a copy of the real _lib.sh.

CLAUDE_CONFIG_DIR is pinned per test via subprocess env, isolated from this
machine's real ~/.claude, to control the pr-cost-disclosure sentinel file's
presence and content.
"""
from __future__ import annotations

import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from .conftest import _base_test_env, _make_repo_with_remote

# Path to the script under test (resolved relative to this file)
_SCRIPT = Path(__file__).parent.parent / "pr-cost-section.sh"
_LIB_SH = Path(__file__).parent.parent.parent / "hooks" / "_lib.sh"

# The complete block pr-cost-section.sh emits on exit 0. Written out as a
# literal rather than composed from parts, because composing it would
# re-implement the script's own layout and pass on a wrong shape. The two
# interior report lines come from _fake_transcript_analysis_source().
_EXPECTED_COST_BLOCK = (
    "<!-- pr-cost:start -->\n"
    "## Cost (list-price estimate)\n"
    "\n"
    "ARGS: cost --this-repo --branches main --summary\n"
    "total: $12.34\n"
    "\n"
    "Exact command that produced this: `~/.claude/scripts/pr-cost-section.sh`\n"
    "<!-- pr-cost:end -->\n"
)

# Same five-part shape as _EXPECTED_COST_BLOCK, with the metacharacter line
# from _fake_transcript_analysis_source_with_metacharacters() in place of
# the report body.
_EXPECTED_COST_BLOCK_WITH_METACHARACTERS = (
    "<!-- pr-cost:start -->\n"
    "## Cost (list-price estimate)\n"
    "\n"
    "ARGS: cost --this-repo --branches main --summary\n"
    "$HOME `date` %s 50% C:\\path\n"
    "\n"
    "Exact command that produced this: `~/.claude/scripts/pr-cost-section.sh`\n"
    "<!-- pr-cost:end -->\n"
)

# Same five-part shape as _EXPECTED_COST_BLOCK, with the table-row-shaped
# line from _fake_transcript_analysis_source_ending_in_table_row() in place
# of the report body.
_EXPECTED_COST_BLOCK_ENDING_IN_TABLE_ROW = (
    "<!-- pr-cost:start -->\n"
    "## Cost (list-price estimate)\n"
    "\n"
    "ARGS: cost --this-repo --branches main --summary\n"
    "| subagent | 2.82 | 43.5% |\n"
    "\n"
    "Exact command that produced this: `~/.claude/scripts/pr-cost-section.sh`\n"
    "<!-- pr-cost:end -->\n"
)


def _fake_transcript_analysis_source() -> str:
    """Source for a transcript-analysis.py stand-in: echoes its own argv (so
    a test can assert the exact invocation shape) plus a fixed cost-report
    body _EXPECTED_COST_BLOCK wraps."""
    return textwrap.dedent("""\
        #!/usr/bin/env python3
        import sys
        print("ARGS: " + " ".join(sys.argv[1:]))
        print("total: $12.34")
    """)


def _fake_transcript_analysis_source_with_stderr_diagnostics() -> str:
    """Source for a transcript-analysis.py stand-in that writes to both
    streams and exits 0 -- models the real tool's NOTICE/WARNING diagnostics
    on stderr alongside a clean cost report on stdout, for pinning that the
    wrapper's redirect discards the former without touching the latter."""
    return textwrap.dedent("""\
        #!/usr/bin/env python3
        import sys
        print("NOTICE: STDERR-MARKER non-contiguous requestId run merged", file=sys.stderr)
        print("ARGS: " + " ".join(sys.argv[1:]))
        print("total: $12.34")
    """)


def _fake_transcript_analysis_source_with_metacharacters() -> str:
    """Source for a transcript-analysis.py stand-in whose report body
    carries $, a backtick, a printf specifier, and a backslash. The
    generated line itself is a raw string, so the backslash reaches the
    fake's own stdout intact rather than being consumed as an escape
    sequence when the fake runs."""
    return textwrap.dedent("""\
        #!/usr/bin/env python3
        import sys
        print("ARGS: " + " ".join(sys.argv[1:]))
        print(r"$HOME `date` %s 50% C:\\path")
    """)


def _fake_transcript_analysis_source_ending_in_table_row() -> str:
    """Source for a transcript-analysis.py stand-in whose report body's
    last line is table-row-shaped -- models the "Cost by ..." tables'
    final row, distinct from a fixed non-table body."""
    return textwrap.dedent("""\
        #!/usr/bin/env python3
        import sys
        print("ARGS: " + " ".join(sys.argv[1:]))
        print("| subagent | 2.82 | 43.5% |")
    """)


def _failing_transcript_analysis_source() -> str:
    """Source for a transcript-analysis.py stand-in that fails with no
    stdout -- models a downstream-tool failure distinct from the
    sentinel-disabled/malformed exit-1 path."""
    return textwrap.dedent("""\
        #!/usr/bin/env python3
        import sys
        print("transcript-analysis.py: boom", file=sys.stderr)
        sys.exit(1)
    """)


def _failing_transcript_analysis_source_with_partial_stdout() -> str:
    """Source for a transcript-analysis.py stand-in that prints part of a
    report to stdout before failing -- models a mid-report crash, distinct
    from a clean early failure with no stdout at all."""
    return textwrap.dedent("""\
        #!/usr/bin/env python3
        import sys
        print("cost: main: scanned 3 sessions")
        print("transcript-analysis.py: boom", file=sys.stderr)
        sys.exit(1)
    """)


@pytest.fixture()
def script_fixture(tmp_path) -> Path:
    """Build a fixture directory holding a copy of the script under test, a
    copy of _lib.sh at the relative path it sources, and a fake
    transcript-analysis.py. Returns the path to the copied script."""
    fixture_root = tmp_path / "fixture_root"
    scripts_dir = fixture_root / "scripts"
    hooks_dir = fixture_root / "hooks"
    scripts_dir.mkdir(parents=True)
    hooks_dir.mkdir(parents=True)

    script_copy = scripts_dir / "pr-cost-section.sh"
    shutil.copy(_SCRIPT, script_copy)
    script_copy.chmod(0o755)

    shutil.copy(_LIB_SH, hooks_dir / "_lib.sh")

    fake = scripts_dir / "transcript-analysis.py"
    fake.write_text(_fake_transcript_analysis_source())
    fake.chmod(0o755)

    return script_copy


@pytest.fixture()
def failing_script_fixture(tmp_path) -> Path:
    """Same layout as script_fixture, but transcript-analysis.py itself
    fails -- for the downstream-tool-failure path distinct from the
    sentinel-disabled/malformed exit-1 path."""
    fixture_root = tmp_path / "fixture_root"
    scripts_dir = fixture_root / "scripts"
    hooks_dir = fixture_root / "hooks"
    scripts_dir.mkdir(parents=True)
    hooks_dir.mkdir(parents=True)

    script_copy = scripts_dir / "pr-cost-section.sh"
    shutil.copy(_SCRIPT, script_copy)
    script_copy.chmod(0o755)

    shutil.copy(_LIB_SH, hooks_dir / "_lib.sh")

    fake = scripts_dir / "transcript-analysis.py"
    fake.write_text(_failing_transcript_analysis_source())
    fake.chmod(0o755)

    return script_copy


@pytest.fixture()
def partial_output_failing_script_fixture(tmp_path) -> Path:
    """Same layout as failing_script_fixture, but transcript-analysis.py
    prints part of a report to stdout before failing -- proves the script's
    stdout buffering suppresses a partial print, not just a clean early
    failure."""
    fixture_root = tmp_path / "fixture_root"
    scripts_dir = fixture_root / "scripts"
    hooks_dir = fixture_root / "hooks"
    scripts_dir.mkdir(parents=True)
    hooks_dir.mkdir(parents=True)

    script_copy = scripts_dir / "pr-cost-section.sh"
    shutil.copy(_SCRIPT, script_copy)
    script_copy.chmod(0o755)

    shutil.copy(_LIB_SH, hooks_dir / "_lib.sh")

    fake = scripts_dir / "transcript-analysis.py"
    fake.write_text(_failing_transcript_analysis_source_with_partial_stdout())
    fake.chmod(0o755)

    return script_copy


@pytest.fixture()
def stderr_diagnostics_script_fixture(tmp_path) -> Path:
    """Same layout as script_fixture, but transcript-analysis.py writes to
    both streams and exits 0 -- for pinning that the redirect discards
    stderr diagnostics without touching stdout."""
    fixture_root = tmp_path / "fixture_root"
    scripts_dir = fixture_root / "scripts"
    hooks_dir = fixture_root / "hooks"
    scripts_dir.mkdir(parents=True)
    hooks_dir.mkdir(parents=True)

    script_copy = scripts_dir / "pr-cost-section.sh"
    shutil.copy(_SCRIPT, script_copy)
    script_copy.chmod(0o755)

    shutil.copy(_LIB_SH, hooks_dir / "_lib.sh")

    fake = scripts_dir / "transcript-analysis.py"
    fake.write_text(_fake_transcript_analysis_source_with_stderr_diagnostics())
    fake.chmod(0o755)

    return script_copy


@pytest.fixture()
def metacharacters_script_fixture(tmp_path) -> Path:
    """Same layout as script_fixture, but transcript-analysis.py's report
    body carries $, a backtick, a printf specifier, and a backslash -- for
    pinning that the wrapper passes the report as a printf argument, never
    as its format string or an unquoted heredoc body."""
    fixture_root = tmp_path / "fixture_root"
    scripts_dir = fixture_root / "scripts"
    hooks_dir = fixture_root / "hooks"
    scripts_dir.mkdir(parents=True)
    hooks_dir.mkdir(parents=True)

    script_copy = scripts_dir / "pr-cost-section.sh"
    shutil.copy(_SCRIPT, script_copy)
    script_copy.chmod(0o755)

    shutil.copy(_LIB_SH, hooks_dir / "_lib.sh")

    fake = scripts_dir / "transcript-analysis.py"
    fake.write_text(_fake_transcript_analysis_source_with_metacharacters())
    fake.chmod(0o755)

    return script_copy


@pytest.fixture()
def table_row_script_fixture(tmp_path) -> Path:
    """Same layout as script_fixture, but transcript-analysis.py's report
    body's last line is table-row-shaped -- for pinning that the blank line
    before the trailer keeps it from parsing as a phantom row of the
    preceding table."""
    fixture_root = tmp_path / "fixture_root"
    scripts_dir = fixture_root / "scripts"
    hooks_dir = fixture_root / "hooks"
    scripts_dir.mkdir(parents=True)
    hooks_dir.mkdir(parents=True)

    script_copy = scripts_dir / "pr-cost-section.sh"
    shutil.copy(_SCRIPT, script_copy)
    script_copy.chmod(0o755)

    shutil.copy(_LIB_SH, hooks_dir / "_lib.sh")

    fake = scripts_dir / "transcript-analysis.py"
    fake.write_text(_fake_transcript_analysis_source_ending_in_table_row())
    fake.chmod(0o755)

    return script_copy


def _run_script(script_copy: Path, cwd: Path, config_dir: Path) -> subprocess.CompletedProcess:
    env = {**_base_test_env(), "CLAUDE_CONFIG_DIR": str(config_dir)}
    return subprocess.run(
        [str(script_copy)], cwd=str(cwd), capture_output=True, text=True, check=False, env=env,
    )


def _write_sentinel(config_dir: Path, content: str) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "pr-cost-disclosure").write_text(content)


class TestSentinelEnabledBranchResolves:
    def test_prints_the_complete_cost_block_and_exit_zero(self, tmp_path, script_fixture):
        repo, _bare = _make_repo_with_remote(tmp_path)
        config_dir = tmp_path / "claude_config"
        _write_sentinel(config_dir, "dollars\n")

        result = _run_script(script_fixture, repo, config_dir)

        assert result.returncode == 0
        assert result.stdout == _EXPECTED_COST_BLOCK


class TestSentinelMixedCase:
    """The script explicitly lowercases before comparing -- a regression to
    that step would silently flip real users' DOLLARS/Dollars sentinels from
    enabled to disabled with no other test catching it."""

    def test_uppercase_sentinel_still_enables(self, tmp_path, script_fixture):
        repo, _bare = _make_repo_with_remote(tmp_path)
        config_dir = tmp_path / "claude_config"
        _write_sentinel(config_dir, "DOLLARS\n")

        result = _run_script(script_fixture, repo, config_dir)

        assert result.returncode == 0
        assert result.stdout == _EXPECTED_COST_BLOCK


class TestSentinelAbsent:
    def test_no_stdout_and_exit_one(self, tmp_path, script_fixture):
        cwd = tmp_path / "cwd"
        cwd.mkdir()
        config_dir = tmp_path / "claude_config"
        config_dir.mkdir()

        result = _run_script(script_fixture, cwd, config_dir)

        assert result.returncode == 1
        assert result.stdout == ""


class TestSentinelWrongValue:
    def test_no_stdout_and_exit_one(self, tmp_path, script_fixture):
        cwd = tmp_path / "cwd"
        cwd.mkdir()
        config_dir = tmp_path / "claude_config"
        _write_sentinel(config_dir, "usd\n")

        result = _run_script(script_fixture, cwd, config_dir)

        assert result.returncode == 1
        assert result.stdout == ""


class TestSentinelEnabledDetachedHead:
    def test_no_stdout_and_exit_two(self, tmp_path, script_fixture):
        repo, _bare = _make_repo_with_remote(tmp_path)
        head_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True,
        ).stdout.strip()
        subprocess.run(["git", "checkout", "-q", head_sha], cwd=repo, check=True)
        config_dir = tmp_path / "claude_config"
        _write_sentinel(config_dir, "dollars\n")

        result = _run_script(script_fixture, repo, config_dir)

        assert result.returncode == 2
        assert result.stdout == ""


class TestDownstreamCostCallFails:
    """Sentinel enabled and HEAD resolves to a branch, but the
    transcript-analysis.py cost call itself fails -- exit 3, distinct from
    the sentinel-disabled/malformed exit 1 the calling agent would otherwise
    silently reinterpret as intentional."""

    def test_no_stdout_and_exit_three(self, tmp_path, failing_script_fixture):
        repo, _bare = _make_repo_with_remote(tmp_path)
        config_dir = tmp_path / "claude_config"
        _write_sentinel(config_dir, "dollars\n")

        result = _run_script(failing_script_fixture, repo, config_dir)

        assert result.returncode == 3
        assert result.stdout == ""
        assert "pr-cost-section.sh: transcript-analysis.py cost call failed" in result.stderr
        assert "transcript-analysis.py cost --this-repo --branches main --summary" in result.stderr
        assert "transcript-analysis.py: boom" not in result.stderr


class TestDownstreamCostCallFailsAfterPartialOutput:
    """A transcript-analysis.py that crashes mid-report -- after already
    printing part of it to stdout -- must still surface no stdout from this
    script, proving the buffering fix suppresses partial output rather than
    only covering a clean early failure."""

    def test_no_stdout_and_exit_three(self, tmp_path, partial_output_failing_script_fixture):
        repo, _bare = _make_repo_with_remote(tmp_path)
        config_dir = tmp_path / "claude_config"
        _write_sentinel(config_dir, "dollars\n")

        result = _run_script(partial_output_failing_script_fixture, repo, config_dir)

        assert result.returncode == 3
        assert result.stdout == ""
        assert "transcript-analysis.py cost --this-repo --branches main --summary" in result.stderr
        assert "transcript-analysis.py: boom" not in result.stderr


class TestStderrDiagnosticsDiscardedOnSuccess:
    """The child's stderr -- request-ID-bearing NOTICE lines and the
    format-drift WARNINGs the PRICING INTEGRITY banner compensates for --
    is discarded on a successful run, not merged into this script's own
    stderr where a Bash-tool caller would read it alongside stdout."""

    def test_stdout_is_exact_cost_body_and_stderr_omits_child_diagnostics(
        self, tmp_path, stderr_diagnostics_script_fixture,
    ):
        repo, _bare = _make_repo_with_remote(tmp_path)
        config_dir = tmp_path / "claude_config"
        _write_sentinel(config_dir, "dollars\n")

        result = _run_script(stderr_diagnostics_script_fixture, repo, config_dir)

        assert result.returncode == 0
        assert result.stdout == _EXPECTED_COST_BLOCK
        assert "STDERR-MARKER" not in result.stderr


class TestSentinelBlankLineThenDollars:
    """Guards the deliberate narrowing: a leading blank line makes the
    sentinel read as two lines, judged disabled -- a future edit that
    widens the trim to collapse interior/leading newlines must fail this."""

    def test_judged_disabled_not_enabled(self, tmp_path, script_fixture):
        cwd = tmp_path / "cwd"
        cwd.mkdir()
        config_dir = tmp_path / "claude_config"
        _write_sentinel(config_dir, "\ndollars\n")

        result = _run_script(script_fixture, cwd, config_dir)

        assert result.returncode == 1
        assert result.stdout == ""


class TestCostBodyWithShellMetacharacters:
    """A report line carrying $, a backtick, a printf specifier, and a
    backslash survives byte-identically: the wrapper passes the report as a
    printf argument, never as its format string or an unquoted heredoc
    body."""

    def test_stdout_preserves_metacharacters_byte_identically(
        self, tmp_path, metacharacters_script_fixture,
    ):
        repo, _bare = _make_repo_with_remote(tmp_path)
        config_dir = tmp_path / "claude_config"
        _write_sentinel(config_dir, "dollars\n")

        result = _run_script(metacharacters_script_fixture, repo, config_dir)

        assert result.returncode == 0
        assert result.stdout == _EXPECTED_COST_BLOCK_WITH_METACHARACTERS


class TestCostBodyEndsWithTableRow:
    """No fixture in this suite otherwise ends its fake report body with a
    table-row-shaped line, so nothing else pins the central invariant: the
    blank line before the trailer keeps the reproducibility line from
    parsing as a phantom row of the preceding table."""

    def test_stdout_keeps_trailer_off_the_table_and_exit_zero(
        self, tmp_path, table_row_script_fixture,
    ):
        repo, _bare = _make_repo_with_remote(tmp_path)
        config_dir = tmp_path / "claude_config"
        _write_sentinel(config_dir, "dollars\n")

        result = _run_script(table_row_script_fixture, repo, config_dir)

        assert result.returncode == 0
        assert result.stdout == _EXPECTED_COST_BLOCK_ENDING_IN_TABLE_ROW
