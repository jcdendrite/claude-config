"""Tests for review-pr-check-attribution.sh -- the mechanical attribution
check run over /review-pr's findings-body file before the deliver step
posts it: the first-line prefix, the last-non-blank-line trailer, and (in
diff-only mode only) the reduced-coverage disclosure line.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from helpers import SCRIPTS_DIR

SCRIPT = SCRIPTS_DIR / "review-pr-check-attribution.sh"
TRAILER = "🤖 Generated with [Claude Code](https://claude.com/claude-code)"
DISCLOSURE_LINE = "Reviewed from the PR diff only — no checkout, no checks run."

# A harness bound so a hung bash fails one test instead of the suite.
_SUBPROCESS_TIMEOUT_SECONDS = 60


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(SCRIPT), *args], capture_output=True, text=True, check=False,
        timeout=_SUBPROCESS_TIMEOUT_SECONDS,
    )


def _write(tmp_path: Path, content: str) -> Path:
    findings_body = tmp_path / "findings.body"
    findings_body.write_text(content)
    return findings_body


class TestUsageErrors:
    def test_no_arguments_exits_two_with_usage(self):
        result = _run([])
        assert result.returncode == 2
        assert "Usage" in result.stderr

    def test_one_argument_exits_two_with_usage(self, tmp_path):
        result = _run([str(_write(tmp_path, "x"))])
        assert result.returncode == 2
        assert "Usage" in result.stderr

    def test_too_many_arguments_exits_two_with_usage(self):
        result = _run(["one", "checkout", "extra"])
        assert result.returncode == 2
        assert "Usage" in result.stderr

    def test_invalid_mode_exits_two_with_usage(self, tmp_path):
        result = _run([str(_write(tmp_path, "x")), "not-a-mode"])
        assert result.returncode == 2
        assert "Usage" in result.stderr

    def test_unreadable_file_exits_two(self, tmp_path):
        result = _run([str(tmp_path / "does-not-exist.body"), "checkout"])
        assert result.returncode == 2


class TestCheckoutModeHappyPath:
    def test_prefix_and_trailer_present_exits_zero(self, tmp_path):
        body = _write(tmp_path, f"**[Claude Code]**\n\n# Findings\n\n{TRAILER}\n")
        result = _run([str(body), "checkout"])
        assert result.returncode == 0, result.stderr

    def test_trailing_blank_lines_after_trailer_still_pass(self, tmp_path):
        """A body whose only defect is trailing blank lines must still
        pass -- the trailer check reads the last NON-blank line, not
        simply the file's last line."""
        body = _write(tmp_path, f"**[Claude Code]**\n\n# Findings\n\n{TRAILER}\n\n\n")
        result = _run([str(body), "checkout"])
        assert result.returncode == 0, result.stderr

    def test_no_trailing_newline_after_trailer_still_passes(self, tmp_path):
        """A file with no trailing newline after its own last line must
        still be read correctly -- a naive `while read` loop silently
        drops a final line with no trailing newline."""
        body = _write(tmp_path, f"**[Claude Code]**\n\n# Findings\n\n{TRAILER}")
        result = _run([str(body), "checkout"])
        assert result.returncode == 0, result.stderr


class TestMissingPrefixFailsClosed:
    def test_missing_prefix_exits_one_and_names_the_file(self, tmp_path):
        body = _write(tmp_path, f"# Findings\n\n{TRAILER}\n")
        result = _run([str(body), "checkout"])
        assert result.returncode == 1
        assert str(body) in result.stderr

    def test_empty_file_exits_one(self, tmp_path):
        body = _write(tmp_path, "")
        result = _run([str(body), "checkout"])
        assert result.returncode == 1

    def test_prefix_on_a_later_line_does_not_count(self, tmp_path):
        body = _write(tmp_path, f"# Findings\n\n**[Claude Code]** noted below\n\n{TRAILER}\n")
        result = _run([str(body), "checkout"])
        assert result.returncode == 1


class TestMissingTrailerFailsClosed:
    def test_missing_trailer_exits_one(self, tmp_path):
        body = _write(tmp_path, "**[Claude Code]**\n\n# Findings\n")
        result = _run([str(body), "checkout"])
        assert result.returncode == 1
        assert str(body) in result.stderr

    def test_trailer_present_but_not_the_last_non_blank_line_fails(self, tmp_path):
        """The trailer must be the body's own final substantive line -- a
        body that appends more prose after it must still fail."""
        body = _write(tmp_path, f"**[Claude Code]**\n\n{TRAILER}\n\nOne more line after the trailer.\n")
        result = _run([str(body), "checkout"])
        assert result.returncode == 1


class TestDiffOnlyDisclosure:
    def test_disclosure_line_present_exits_zero(self, tmp_path):
        body = _write(
            tmp_path,
            f"**[Claude Code]**\n\n# Findings\n\n{DISCLOSURE_LINE}\n\n{TRAILER}\n",
        )
        result = _run([str(body), "diff-only"])
        assert result.returncode == 0, result.stderr

    def test_missing_disclosure_line_exits_one(self, tmp_path):
        body = _write(tmp_path, f"**[Claude Code]**\n\n# Findings\n\n{TRAILER}\n")
        result = _run([str(body), "diff-only"])
        assert result.returncode == 1
        assert str(body) in result.stderr

    def test_checkout_mode_body_missing_disclosure_line_still_passes(self, tmp_path):
        """The disclosure line is required only in diff-only mode -- a
        checkout-mode body carrying no such line must not fail on it."""
        body = _write(tmp_path, f"**[Claude Code]**\n\n# Findings\n\n{TRAILER}\n")
        result = _run([str(body), "checkout"])
        assert result.returncode == 0, result.stderr
