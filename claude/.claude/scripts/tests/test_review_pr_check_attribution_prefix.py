"""Tests for review-pr-check-attribution-prefix.sh -- the mechanical
attribution-prefix check run over /review-pr's findings-body file before
Step 9 posts it, structurally parallel to
test_review_pr_scan_findings_body.py's own credential-scan tests.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from helpers import SCRIPTS_DIR

SCRIPT = SCRIPTS_DIR / "review-pr-check-attribution-prefix.sh"


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(SCRIPT), *args], capture_output=True, text=True, check=False
    )


def _write(tmp_path: Path, content: str) -> Path:
    findings_body = tmp_path / "findings.body"
    findings_body.write_text(content)
    return findings_body


class TestUsageErrors:
    def test_no_argument_exits_two_with_usage(self):
        result = _run([])
        assert result.returncode == 2
        assert "Usage" in result.stderr

    def test_too_many_arguments_exits_two_with_usage(self):
        result = _run(["one", "two"])
        assert result.returncode == 2
        assert "Usage" in result.stderr

    def test_unreadable_file_exits_two(self, tmp_path):
        result = _run([str(tmp_path / "does-not-exist.body")])
        assert result.returncode == 2


class TestPrefixPresentPasses:
    def test_prefix_on_its_own_first_line_exits_zero(self, tmp_path):
        findings_body = _write(tmp_path, "**[Claude Code]**\n\n# Findings\n")
        result = _run([str(findings_body)])
        assert result.returncode == 0, result.stderr

    def test_prefix_followed_by_inline_content_exits_zero(self, tmp_path):
        findings_body = _write(tmp_path, "**[Claude Code]** # Findings\n\n- nit: rename `x`\n")
        result = _run([str(findings_body)])
        assert result.returncode == 0, result.stderr


class TestPrefixMissingFailsClosed:
    def test_missing_prefix_exits_one_and_names_the_file(self, tmp_path):
        findings_body = _write(tmp_path, "# Findings\n\n- nit: rename `x`\n")
        result = _run([str(findings_body)])
        assert result.returncode == 1
        assert str(findings_body) in result.stderr

    def test_empty_file_exits_one(self, tmp_path):
        """An empty file has no first line starting with the prefix -- must
        fail the same as any other missing-prefix body, never pass by
        vacuous truth."""
        findings_body = _write(tmp_path, "")
        result = _run([str(findings_body)])
        assert result.returncode == 1

    def test_prefix_on_a_later_line_does_not_count(self, tmp_path):
        """Only the FIRST line is checked -- a body whose opening line is
        something else and mentions the prefix later must still fail, since
        a reader scanning the top of the thread would see unattributed
        content first."""
        findings_body = _write(tmp_path, "# Findings\n\n**[Claude Code]** noted below\n")
        result = _run([str(findings_body)])
        assert result.returncode == 1
