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

import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest
from helpers import SKILLS_DIR

from .conftest import _base_test_env, _make_repo_with_remote

# Path to the script under test (resolved relative to this file)
_SCRIPT = Path(__file__).parent.parent / "pr-cost-section.sh"
_LIB_SH = Path(__file__).parent.parent.parent / "hooks" / "_lib.sh"
# _lib.sh sources _config.sh from its own directory (BASH_SOURCE-relative) --
# every fixture below that copies _lib.sh needs this sibling copied alongside
# it too, or that source fails outright.
_CONFIG_SH = Path(__file__).parent.parent.parent / "hooks" / "_config.sh"
# config-keys.psv is a required sibling of _config.sh for the same
# BASH_SOURCE-relative reason.
_CONFIG_KEYS_PSV = Path(__file__).parent.parent.parent / "hooks" / "config-keys.psv"

# The single-quoted literal pr-cost-section.sh substitutes for the counts
# slot when its own cost-counts call fails -- duplicated here on purpose
# rather than composed, under this module's own literal-over-composed
# rationale below.
_COUNTS_FAILURE_CAVEAT = (
    "Review-round and subagent-spawn counts didn't render this time, which doesn't affect"
    " the dollar figures above. Run `transcript-analysis.py cost-counts --this-repo"
    " --branches <branch>` to see the diagnostic."
)

# Stands in for an untracked subagent_type label the cost-counts backstop
# AssertionError must never echo. A fake transcript-analysis.py embeds this
# marker in its own crash message. The test asserts the marker stays absent
# from the wrapper's output.
_UNTRACKED_LABEL_MARKER = "UNTRACKED-LABEL-MARKER-9f3c"

# The complete block pr-cost-section.sh emits on exit 0. Written out as a
# literal rather than composed from parts, because composing it would
# re-implement the script's own layout and pass on a wrong shape. The four
# interior report lines come from _fake_transcript_analysis_source().
_EXPECTED_COST_BLOCK = (
    "<!-- pr-cost:start -->\n"
    "## Cost (list-price estimate)\n"
    "\n"
    "ARGS: cost --this-repo --branches main --summary\n"
    "total: $12.34\n"
    "\n"
    "ARGS: cost-counts --this-repo --branches main\n"
    "counts: 7 total\n"
    "\n"
    "Exact command that produced this: `~/.claude/scripts/pr-cost-section.sh`\n"
    "<!-- pr-cost:end -->\n"
)

# Same six-part shape as _EXPECTED_COST_BLOCK, with the metacharacter line
# from _fake_transcript_analysis_source_with_metacharacters() in place of
# the cost report body. The counts body stays the plain default -- this
# fixture's own concern is only whether $cost_output survives printf
# byte-identically.
_EXPECTED_COST_BLOCK_WITH_METACHARACTERS = (
    "<!-- pr-cost:start -->\n"
    "## Cost (list-price estimate)\n"
    "\n"
    "ARGS: cost --this-repo --branches main --summary\n"
    "$HOME `date` %s 50% C:\\path\n"
    "\n"
    "ARGS: cost-counts --this-repo --branches main\n"
    "counts: 7 total\n"
    "\n"
    "Exact command that produced this: `~/.claude/scripts/pr-cost-section.sh`\n"
    "<!-- pr-cost:end -->\n"
)

# Same six-part shape as _EXPECTED_COST_BLOCK, with the table-row-shaped
# line from _fake_transcript_analysis_source_ending_in_table_row() in place
# of the cost report body. Pins only the cost/counts seam -- see
# _EXPECTED_COST_BLOCK_BOTH_END_IN_TABLE_ROW for both new seams together.
_EXPECTED_COST_BLOCK_ENDING_IN_TABLE_ROW = (
    "<!-- pr-cost:start -->\n"
    "## Cost (list-price estimate)\n"
    "\n"
    "ARGS: cost --this-repo --branches main --summary\n"
    "| subagent | 2.82 | 43.5% |\n"
    "\n"
    "ARGS: cost-counts --this-repo --branches main\n"
    "counts: 7 total\n"
    "\n"
    "Exact command that produced this: `~/.claude/scripts/pr-cost-section.sh`\n"
    "<!-- pr-cost:end -->\n"
)

# Both the cost body and the cost-counts body end in a table-row-shaped
# line -- the direct extension of TestCostBodyEndsWithTableRow that pins
# the two new GFM seams (cost/counts, counts/trailer) this feature
# introduces, not just the first.
_EXPECTED_COST_BLOCK_BOTH_END_IN_TABLE_ROW = (
    "<!-- pr-cost:start -->\n"
    "## Cost (list-price estimate)\n"
    "\n"
    "ARGS: cost --this-repo --branches main --summary\n"
    "| subagent | 2.82 | 43.5% |\n"
    "\n"
    "ARGS: cost-counts --this-repo --branches main\n"
    "| **total** | **6** |\n"
    "\n"
    "Exact command that produced this: `~/.claude/scripts/pr-cost-section.sh`\n"
    "<!-- pr-cost:end -->\n"
)

# The cost-counts body is the real zero-review-rounds-and-zero-subagent-
# spawns rendering: the fixed three-row zero-count rounds table immediately
# followed by the spawns section's bare sentence, no table at all -- the
# one table-to-non-table seam no other fixture in this suite reaches.
_EXPECTED_COST_BLOCK_COMBINED_ZERO_STATE = (
    "<!-- pr-cost:start -->\n"
    "## Cost (list-price estimate)\n"
    "\n"
    "ARGS: cost --this-repo --branches main --summary\n"
    "total: $12.34\n"
    "\n"
    "ARGS: cost-counts --this-repo --branches main\n"
    "### Review rounds\n"
    "\n"
    "Each invocation of a review skill is one round, whether or not it produced findings."
    " Counts reflect this section's last render; a review round that ran afterward may not"
    " be included yet.\n"
    "\n"
    "| Skill | Rounds |\n"
    "|---|---|\n"
    "| code-review | 0 |\n"
    "| plan-review | 0 |\n"
    "| ready-for-review | 0 |\n"
    "| **total** | **0** |\n"
    "\n"
    "### Subagent spawns\n"
    "\n"
    "Counts main-thread dispatches only; an agent spawned from inside another agent is not"
    " counted.\n"
    "\n"
    "No subagent spawns found in scope.\n"
    "\n"
    "Exact command that produced this: `~/.claude/scripts/pr-cost-section.sh`\n"
    "<!-- pr-cost:end -->\n"
)

# The cost call succeeds normally, but cost-counts fails -- the caveat
# paragraph fills the counts slot instead of the two report lines.
_EXPECTED_COST_BLOCK_WITH_COUNTS_CAVEAT = (
    "<!-- pr-cost:start -->\n"
    "## Cost (list-price estimate)\n"
    "\n"
    "ARGS: cost --this-repo --branches main --summary\n"
    "total: $12.34\n"
    "\n"
    f"{_COUNTS_FAILURE_CAVEAT}\n"
    "\n"
    "Exact command that produced this: `~/.claude/scripts/pr-cost-section.sh`\n"
    "<!-- pr-cost:end -->\n"
)


def _cost_counts_attempts_marker(script_copy: Path) -> Path:
    """Path to the cost-counts-attempts marker file every fake stand-in
    below appends one line to on each cost-counts invocation -- lives
    beside the fake itself, in the fixture's own scripts_dir. Its only
    remaining purpose is pinning invocation count/ordering: with no retry
    anywhere in the wrapper, there is no other invocation-count regression
    left to guard against."""
    return script_copy.parent / "cost-counts-attempts"


def _fake_transcript_analysis_source() -> str:
    """Source for a transcript-analysis.py stand-in that branches on
    sys.argv[1]: echoes its own argv (so a test can assert the exact
    invocation shape) plus a fixed report body, one shape for `cost` and a
    different one for `cost-counts` -- _EXPECTED_COST_BLOCK pins both via
    their two ARGS: lines. The cost-counts branch also appends one line to
    the cost-counts-attempts marker file beside itself."""
    return textwrap.dedent("""\
        #!/usr/bin/env python3
        import sys
        from pathlib import Path
        print("ARGS: " + " ".join(sys.argv[1:]))
        if sys.argv[1] == "cost-counts":
            marker = Path(__file__).with_name("cost-counts-attempts")
            with marker.open("a") as fh:
                fh.write("x\\n")
            print("counts: 7 total")
        else:
            print("total: $12.34")
    """)


def _fake_transcript_analysis_source_with_stderr_diagnostics() -> str:
    """Source for a transcript-analysis.py stand-in that writes to both
    streams on its cost call and exits 0 -- models the real tool's
    NOTICE/WARNING diagnostics on stderr alongside a clean cost report on
    stdout, for pinning that the wrapper's redirect discards the former
    without touching the latter."""
    return textwrap.dedent("""\
        #!/usr/bin/env python3
        import sys
        from pathlib import Path
        if sys.argv[1] != "cost-counts":
            print("NOTICE: STDERR-MARKER non-contiguous requestId run merged", file=sys.stderr)
        print("ARGS: " + " ".join(sys.argv[1:]))
        if sys.argv[1] == "cost-counts":
            marker = Path(__file__).with_name("cost-counts-attempts")
            with marker.open("a") as fh:
                fh.write("x\\n")
            print("counts: 7 total")
        else:
            print("total: $12.34")
    """)


def _fake_transcript_analysis_source_with_metacharacters() -> str:
    """Source for a transcript-analysis.py stand-in whose cost report body
    carries $, a backtick, a printf specifier, and a backslash. The
    generated line itself is a raw string, so the backslash reaches the
    fake's own stdout intact rather than being consumed as an escape
    sequence when the fake runs. The cost-counts branch stays the plain
    default body -- this fixture's own concern is only $cost_output."""
    return textwrap.dedent("""\
        #!/usr/bin/env python3
        import sys
        from pathlib import Path
        print("ARGS: " + " ".join(sys.argv[1:]))
        if sys.argv[1] == "cost-counts":
            marker = Path(__file__).with_name("cost-counts-attempts")
            with marker.open("a") as fh:
                fh.write("x\\n")
            print("counts: 7 total")
        else:
            print(r"$HOME `date` %s 50% C:\\path")
    """)


def _fake_transcript_analysis_source_ending_in_table_row() -> str:
    """Source for a transcript-analysis.py stand-in whose cost report
    body's last line is table-row-shaped -- models the "Cost by ..."
    tables' final row, distinct from a fixed non-table body. The
    cost-counts branch stays the plain default body -- see
    _fake_transcript_analysis_source_both_seams for both bodies
    table-row-shaped together."""
    return textwrap.dedent("""\
        #!/usr/bin/env python3
        import sys
        from pathlib import Path
        print("ARGS: " + " ".join(sys.argv[1:]))
        if sys.argv[1] == "cost-counts":
            marker = Path(__file__).with_name("cost-counts-attempts")
            with marker.open("a") as fh:
                fh.write("x\\n")
            print("counts: 7 total")
        else:
            print("| subagent | 2.82 | 43.5% |")
    """)


def _fake_transcript_analysis_source_both_seams() -> str:
    """Source for a transcript-analysis.py stand-in whose cost report body
    AND cost-counts body each end in a table-row-shaped line -- the direct
    extension of the single-body table-row fixture above, pinning both new
    GFM seams (cost/counts, counts/trailer) this feature introduces."""
    return textwrap.dedent("""\
        #!/usr/bin/env python3
        import sys
        from pathlib import Path
        print("ARGS: " + " ".join(sys.argv[1:]))
        if sys.argv[1] == "cost-counts":
            marker = Path(__file__).with_name("cost-counts-attempts")
            with marker.open("a") as fh:
                fh.write("x\\n")
            print("| **total** | **6** |")
        else:
            print("| subagent | 2.82 | 43.5% |")
    """)


def _fake_transcript_analysis_source_combined_zero_state() -> str:
    """Source for a transcript-analysis.py stand-in whose cost-counts body
    is the real zero-review-rounds-and-zero-subagent-spawns rendering: the
    fixed three-row zero-count rounds table immediately followed by the
    spawns section's bare sentence, no table at all -- the one
    table-to-non-table seam no other fixture in this suite reaches."""
    return textwrap.dedent("""\
        #!/usr/bin/env python3
        import sys
        from pathlib import Path
        print("ARGS: " + " ".join(sys.argv[1:]))
        if sys.argv[1] == "cost-counts":
            marker = Path(__file__).with_name("cost-counts-attempts")
            with marker.open("a") as fh:
                fh.write("x\\n")
            print("### Review rounds")
            print()
            print(
                "Each invocation of a review skill is one round, whether or not it produced"
                " findings. Counts reflect this section's last render; a review round that ran"
                " afterward may not be included yet."
            )
            print()
            print("| Skill | Rounds |")
            print("|---|---|")
            print("| code-review | 0 |")
            print("| plan-review | 0 |")
            print("| ready-for-review | 0 |")
            print("| **total** | **0** |")
            print()
            print("### Subagent spawns")
            print()
            print(
                "Counts main-thread dispatches only; an agent spawned from inside another agent"
                " is not counted."
            )
            print()
            print("No subagent spawns found in scope.")
        else:
            print("total: $12.34")
    """)


def _failing_transcript_analysis_source() -> str:
    """Source for a transcript-analysis.py stand-in that fails with no
    stdout -- models a downstream-tool failure distinct from the
    sentinel-disabled/malformed exit-1 path. Fails unconditionally
    regardless of which subcommand is passed, but the wrapper's own exit-3
    short-circuit means only the cost invocation ever reaches it."""
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


def _fake_transcript_analysis_source_failing_cost_counts() -> str:
    """Source for a transcript-analysis.py stand-in whose cost call
    succeeds but whose cost-counts call fails -- appending one line to the
    cost-counts-attempts marker on its single invocation, for the
    caveat-substitution path and the marker's one-line, no-retry
    assertion."""
    return textwrap.dedent("""\
        #!/usr/bin/env python3
        import sys
        from pathlib import Path
        if sys.argv[1] == "cost-counts":
            marker = Path(__file__).with_name("cost-counts-attempts")
            with marker.open("a") as fh:
                fh.write("x\\n")
            print("transcript-analysis.py: cost-counts boom", file=sys.stderr)
            sys.exit(1)
        print("ARGS: " + " ".join(sys.argv[1:]))
        print("total: $12.34")
    """)


def _fake_transcript_analysis_source_cost_counts_backstop_crash() -> str:
    """Source for a transcript-analysis.py stand-in whose cost-counts call
    crashes on stderr with a message embedding _UNTRACKED_LABEL_MARKER.
    The marker stands in for a raw subagent_type label, of the kind
    cmd_cost_counts's own render-time backstop AssertionError must never
    echo. This fake does not invoke that real backstop -- it verifies the
    wrapper's own content-agnostic stderr discard on any child failure,
    not the reworded AssertionError text itself; see
    test_transcript_analysis.py's test_backstop_assertion_fires_on_bypassed_partition_step
    for the test that exercises the real AssertionError's message."""
    return textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import sys
        if sys.argv[1] == "cost-counts":
            print("transcript-analysis.py: cost-counts: {_UNTRACKED_LABEL_MARKER}", file=sys.stderr)
            sys.exit(1)
        print("ARGS: " + " ".join(sys.argv[1:]))
        print("total: $12.34")
    """)


def _build_fixture(tmp_path, source: str) -> Path:
    """Shared fixture-directory builder: a copy of the script under test, a
    copy of _lib.sh at the relative path it sources, and a fake
    transcript-analysis.py built from `source`. Returns the path to the
    copied script."""
    fixture_root = tmp_path / "fixture_root"
    scripts_dir = fixture_root / "scripts"
    hooks_dir = fixture_root / "hooks"
    scripts_dir.mkdir(parents=True)
    hooks_dir.mkdir(parents=True)

    script_copy = scripts_dir / "pr-cost-section.sh"
    shutil.copy(_SCRIPT, script_copy)
    script_copy.chmod(0o755)

    shutil.copy(_LIB_SH, hooks_dir / "_lib.sh")
    shutil.copy(_CONFIG_SH, hooks_dir / "_config.sh")
    shutil.copy(_CONFIG_KEYS_PSV, hooks_dir / "config-keys.psv")

    fake = scripts_dir / "transcript-analysis.py"
    fake.write_text(source)
    fake.chmod(0o755)

    return script_copy


@pytest.fixture()
def script_fixture(tmp_path) -> Path:
    return _build_fixture(tmp_path, _fake_transcript_analysis_source())


@pytest.fixture()
def failing_script_fixture(tmp_path) -> Path:
    """Same layout as script_fixture, but transcript-analysis.py itself
    fails -- for the downstream-tool-failure path distinct from the
    sentinel-disabled/malformed exit-1 path."""
    return _build_fixture(tmp_path, _failing_transcript_analysis_source())


@pytest.fixture()
def partial_output_failing_script_fixture(tmp_path) -> Path:
    """Same layout as failing_script_fixture, but transcript-analysis.py
    prints part of a report to stdout before failing -- proves the script's
    stdout buffering suppresses a partial print, not just a clean early
    failure."""
    return _build_fixture(tmp_path, _failing_transcript_analysis_source_with_partial_stdout())


@pytest.fixture()
def stderr_diagnostics_script_fixture(tmp_path) -> Path:
    """Same layout as script_fixture, but transcript-analysis.py's cost
    call writes to both streams and exits 0 -- for pinning that the
    redirect discards stderr diagnostics without touching stdout."""
    return _build_fixture(tmp_path, _fake_transcript_analysis_source_with_stderr_diagnostics())


@pytest.fixture()
def metacharacters_script_fixture(tmp_path) -> Path:
    """Same layout as script_fixture, but transcript-analysis.py's cost
    report body carries $, a backtick, a printf specifier, and a backslash
    -- for pinning that the wrapper passes the report as a printf argument,
    never as its format string or an unquoted heredoc body."""
    return _build_fixture(tmp_path, _fake_transcript_analysis_source_with_metacharacters())


@pytest.fixture()
def table_row_script_fixture(tmp_path) -> Path:
    """Same layout as script_fixture, but transcript-analysis.py's cost
    report body's last line is table-row-shaped -- for pinning that the
    blank line before the counts subsection keeps it from parsing as a
    phantom row of the preceding table."""
    return _build_fixture(tmp_path, _fake_transcript_analysis_source_ending_in_table_row())


@pytest.fixture()
def both_seams_script_fixture(tmp_path) -> Path:
    """Same layout as script_fixture, but both the cost report body and the
    cost-counts body end in a table-row-shaped line -- pins both new GFM
    seams together."""
    return _build_fixture(tmp_path, _fake_transcript_analysis_source_both_seams())


@pytest.fixture()
def combined_zero_state_script_fixture(tmp_path) -> Path:
    """Same layout as script_fixture, but the cost-counts body is the real
    zero-review-rounds-and-zero-subagent-spawns rendering -- pins the one
    table-to-non-table seam no other fixture in this suite reaches."""
    return _build_fixture(tmp_path, _fake_transcript_analysis_source_combined_zero_state())


@pytest.fixture()
def failing_cost_counts_script_fixture(tmp_path) -> Path:
    """Same layout as script_fixture, but transcript-analysis.py's
    cost-counts call fails while its cost call still succeeds -- for the
    caveat-substitution path, distinct from failing_script_fixture (which
    fails the cost call itself)."""
    return _build_fixture(tmp_path, _fake_transcript_analysis_source_failing_cost_counts())


@pytest.fixture()
def backstop_crash_script_fixture(tmp_path) -> Path:
    """Same layout as script_fixture, but the cost-counts call fails with a
    message embedding _UNTRACKED_LABEL_MARKER. Used by the test that
    verifies the wrapper discards a failing child's stderr regardless of
    its content."""
    return _build_fixture(tmp_path, _fake_transcript_analysis_source_cost_counts_backstop_crash())


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
    silently reinterpret as intentional. cost-counts must never be invoked
    in this path -- its own failure caveat is a distinct, exit-0 path
    (TestCountsCallFails)."""

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
        assert not _cost_counts_attempts_marker(failing_script_fixture).exists()


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


class TestCountsCallFails:
    """Sentinel enabled, HEAD resolves to a branch, and the cost call
    succeeds -- but the downstream cost-counts call fails. This must not
    change the wrapper's own exit code: the caveat paragraph fills the
    counts slot in the same printf call instead of the two report lines,
    and the dollar tables above it are unaffected."""

    def test_exit_zero_with_caveat_and_marker_pins_no_retry(
        self, tmp_path, failing_cost_counts_script_fixture,
    ):
        repo, _bare = _make_repo_with_remote(tmp_path)
        config_dir = tmp_path / "claude_config"
        _write_sentinel(config_dir, "dollars\n")

        result = _run_script(failing_cost_counts_script_fixture, repo, config_dir)

        assert result.returncode == 0
        assert result.stdout == _EXPECTED_COST_BLOCK_WITH_COUNTS_CAVEAT
        marker = _cost_counts_attempts_marker(failing_cost_counts_script_fixture)
        assert marker.read_text().splitlines() == ["x"]
        assert "pr-cost-section.sh: transcript-analysis.py cost-counts call failed" in result.stderr
        assert "transcript-analysis.py cost-counts --this-repo --branches main" in result.stderr
        assert "transcript-analysis.py: cost-counts boom" not in result.stderr

    def test_untracked_label_marker_never_reaches_stdout_or_stderr(
        self, tmp_path, backstop_crash_script_fixture,
    ):
        """Verifies the wrapper's own stderr discard on a failing
        cost-counts call, independent of what the child's crash message
        says. A stand-in child crashes with a message embedding a marker
        that represents an untracked-label leak; the marker must not
        surface in either stream. This is a wrapper-level, content-agnostic
        guarantee -- see test_transcript_analysis.py's own
        test_backstop_assertion_fires_on_bypassed_partition_step for the
        test that pins the real backstop AssertionError's own message
        content."""
        repo, _bare = _make_repo_with_remote(tmp_path)
        config_dir = tmp_path / "claude_config"
        _write_sentinel(config_dir, "dollars\n")

        result = _run_script(backstop_crash_script_fixture, repo, config_dir)

        assert result.returncode == 0
        assert _UNTRACKED_LABEL_MARKER not in result.stdout
        assert _UNTRACKED_LABEL_MARKER not in result.stderr


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
    """No fixture in this suite otherwise ends its fake cost report body
    with a table-row-shaped line, so nothing else pins this seam: the
    blank line before the counts subsection keeps the two from merging into
    one table."""

    def test_stdout_keeps_counts_off_the_table_and_exit_zero(
        self, tmp_path, table_row_script_fixture,
    ):
        repo, _bare = _make_repo_with_remote(tmp_path)
        config_dir = tmp_path / "claude_config"
        _write_sentinel(config_dir, "dollars\n")

        result = _run_script(table_row_script_fixture, repo, config_dir)

        assert result.returncode == 0
        assert result.stdout == _EXPECTED_COST_BLOCK_ENDING_IN_TABLE_ROW


class TestCostBodyEndsWithTableRowBothSeams:
    """Direct extension of TestCostBodyEndsWithTableRow: both the cost body
    and the cost-counts body end in a table-row-shaped line, pinning the
    two new GFM seams this feature introduces (cost/counts, counts/trailer)
    rather than just the first."""

    def test_stdout_keeps_both_seams_off_the_table_and_exit_zero(
        self, tmp_path, both_seams_script_fixture,
    ):
        repo, _bare = _make_repo_with_remote(tmp_path)
        config_dir = tmp_path / "claude_config"
        _write_sentinel(config_dir, "dollars\n")

        result = _run_script(both_seams_script_fixture, repo, config_dir)

        assert result.returncode == 0
        assert result.stdout == _EXPECTED_COST_BLOCK_BOTH_END_IN_TABLE_ROW


class TestCombinedZeroState:
    """The one table-to-non-table seam the rounds-table zero-state test and
    the spawns-sentence zero-state test each cover separately at the Python
    level (test_transcript_analysis.py) but never together as one rendered
    body: the rounds table's fixed zero-count rows immediately followed by
    the spawns section's bare sentence with no table at all."""

    def test_stdout_matches_combined_zero_rendering(
        self, tmp_path, combined_zero_state_script_fixture,
    ):
        repo, _bare = _make_repo_with_remote(tmp_path)
        config_dir = tmp_path / "claude_config"
        _write_sentinel(config_dir, "dollars\n")

        result = _run_script(combined_zero_state_script_fixture, repo, config_dir)

        assert result.returncode == 0
        assert result.stdout == _EXPECTED_COST_BLOCK_COMBINED_ZERO_STATE


class TestCostHeadingLiteralMatchesSkillBody:
    """Tripwire, not a behavioral test: pr-cost-section.sh's printf argument
    and pr-description/SKILL.md's descriptive mention of the heading are two
    independently-maintained copies of the same string, introduced by moving
    the heading into the script. A rename of one copy without the other would
    leave both this suite's byte-exact block assertions and test_skills.py's
    test_declares_cost_heading_literal green while the two diverge."""

    def test_heading_literal_appears_identically_in_both_places(self):
        script_source = _SCRIPT.read_text()
        heading_match = re.search(r"'(## Cost \(list-price estimate\))'", script_source)
        assert heading_match, "printf's heading argument not found in pr-cost-section.sh"

        skill_body = (SKILLS_DIR / "pr-description" / "SKILL.md").read_text()
        assert heading_match.group(1) in skill_body
