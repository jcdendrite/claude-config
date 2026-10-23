"""Tests for review-pr-check-attribution.sh -- the mechanical attribution
check run over /review-pr's findings-body file before the deliver step
posts it: the first-line prefix, the last-non-blank-line trailer, balanced
<details> tags, and (in diff-only mode only) the reduced-coverage
disclosure line.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from helpers import SCRIPTS_DIR

SCRIPT = SCRIPTS_DIR / "review-pr-check-attribution.sh"
TRAILER = "🤖 Generated with [Claude Code](https://claude.com/claude-code)"
DISCLOSURE_LINE = "Reviewed from the PR diff only — no checkout, no checks run."

# A harness bound so a hung bash fails one test instead of the suite.
_SUBPROCESS_TIMEOUT_SECONDS = 60


def _run(
    args: list[str],
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess:
    # DEVNULL stdin: a regression that makes awk read stdin fails fast instead of hanging.
    return subprocess.run(
        ["bash", str(SCRIPT), *args], capture_output=True, text=True, check=False,
        timeout=_SUBPROCESS_TIMEOUT_SECONDS, env=env, cwd=cwd, stdin=subprocess.DEVNULL,
    )


def _write(tmp_path: Path, content: str) -> Path:
    findings_body = tmp_path / "findings.body"
    findings_body.write_text(content)
    return findings_body


def _body_with(middle: str) -> str:
    return f"**[Claude Code]**\n\n{middle}\n\n{TRAILER}\n"


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


class TestDetailsBalance:
    def test_balanced_block_exits_zero(self, tmp_path):
        body = _write(tmp_path, _body_with("**Blocking: x**\n\n<details>\n<summary>Evidence</summary>\n\nproof\n\n</details>"))
        result = _run([str(body), "checkout"])
        assert result.returncode == 0, result.stderr

    def test_nested_balanced_blocks_exit_zero(self, tmp_path):
        body = _write(tmp_path, _body_with("<details>\n<details>\ninner\n</details>\nouter\n</details>"))
        result = _run([str(body), "checkout"])
        assert result.returncode == 0, result.stderr

    def test_nested_block_missing_one_closer_exits_one(self, tmp_path):
        body = _write(tmp_path, _body_with("<details>\n<details>\ninner\n</details>\nouter"))
        result = _run([str(body), "checkout"])
        assert result.returncode == 1
        assert str(body) in result.stderr
        assert "never closed" in result.stderr

    def test_unclosed_block_exits_one_and_names_the_file(self, tmp_path):
        body = _write(tmp_path, _body_with("<details>\n<summary>Evidence</summary>\n\nproof"))
        result = _run([str(body), "checkout"])
        assert result.returncode == 1
        assert str(body) in result.stderr
        assert "never closed" in result.stderr

    @pytest.mark.parametrize(
        "middle",
        [
            "proof\n\n</details>",
            "<details>\nproof\n</details>\n</details>\n\n<details>\nmore\n</details>",
        ],
        ids=["lone-closer", "duplicated-closer-before-later-balanced-block"],
    )
    def test_closer_without_opener_exits_one(self, tmp_path, middle):
        body = _write(tmp_path, _body_with(middle))
        result = _run([str(body), "checkout"])
        assert result.returncode == 1
        assert str(body) in result.stderr
        assert "no opener" in result.stderr

    def test_closer_before_opener_exits_one(self, tmp_path):
        """Equal counts are not enough -- a closer that precedes its opener
        leaves the later block unclosed on GitHub."""
        body = _write(tmp_path, _body_with("</details>\n\n<details>"))
        result = _run([str(body), "checkout"])
        assert result.returncode == 1
        assert "no opener" in result.stderr

    @pytest.mark.parametrize(
        ("middle", "expected_exit"),
        [
            ("<details open>\n<summary>Evidence</summary>\n\nproof", 1),
            ("<details open><summary>S</summary>body</details>", 0),
            ("<details\topen>\nproof", 1),
            ("<details >\nproof", 1),
            ("<details>\nproof\n</details >", 0),
            ("The <detailsfoo> element is unrelated.", 0),
        ],
        ids=[
            "attribute-opener-unclosed",
            "attribute-opener-closed-same-line",
            "tab-separated-attribute-unclosed",
            "whitespace-before-bracket-unclosed",
            "whitespace-before-bracket-closer",
            "lookalike-tag",
        ],
    )
    def test_tag_name_match_follows_html_tag_syntax(self, tmp_path, middle, expected_exit):
        body = _write(tmp_path, _body_with(middle))
        result = _run([str(body), "checkout"])
        assert result.returncode == expected_exit, result.stderr

    def test_opener_and_closer_on_one_line_exit_zero(self, tmp_path):
        body = _write(tmp_path, _body_with("<details><summary>S</summary>body</details>"))
        result = _run([str(body), "checkout"])
        assert result.returncode == 0, result.stderr

    def test_text_before_inline_opener_with_later_closer_exits_zero(self, tmp_path):
        body = _write(tmp_path, _body_with("Evidence: <details><summary>S</summary>\n\nproof</details>"))
        result = _run([str(body), "checkout"])
        assert result.returncode == 0, result.stderr

    @pytest.mark.parametrize(
        ("middle", "expected_exit"),
        [
            ("<DETAILS>\nproof", 1),
            ("<details>\nproof\n</DETAILS>", 0),
        ],
        ids=["uppercase-opener-unclosed", "lowercase-opener-uppercase-closer"],
    )
    def test_tag_match_is_case_insensitive(self, tmp_path, middle, expected_exit):
        body = _write(tmp_path, _body_with(middle))
        result = _run([str(body), "checkout"])
        assert result.returncode == expected_exit, result.stderr

    @pytest.mark.parametrize(
        "middle",
        [
            "The PR leaves `<details>` open in its template.",
            "The PR has `<details>` without a matching `</details>`.",
        ],
        ids=["one-span-opener", "two-spans-opener-and-closer"],
    )
    def test_tag_inside_single_backtick_span_is_ignored(self, tmp_path, middle):
        body = _write(tmp_path, _body_with(middle))
        result = _run([str(body), "checkout"])
        assert result.returncode == 0, result.stderr

    def test_bare_opener_after_single_backtick_span_on_one_line_exits_one(self, tmp_path):
        """A span strip that swallowed the rest of the line, or ran from the
        first backtick to the last, would hide the opener."""
        body = _write(tmp_path, _body_with("See `a` then <details> then `b`"))
        result = _run([str(body), "checkout"])
        assert result.returncode == 1

    @pytest.mark.parametrize(
        ("middle", "expected_message"),
        [
            ("```html\n<details>\n```", "never closed"),
            ("```html\n</details>\n```", "no opener"),
            ("The PR leaves ``<details>`` open.", "never closed"),
            ("It ends with a lone ` <details>", "never closed"),
        ],
        ids=[
            "fenced-lone-opener",
            "fenced-lone-closer",
            "double-backtick-span-opener",
            "unpaired-backtick-before-opener",
        ],
    )
    def test_unbalanced_tag_outside_single_backtick_span_still_counts(self, tmp_path, middle, expected_message):
        """Fence, double-backtick, and unpaired-backtick content is not
        skipped, so a quoted lone tag refuses the body and the message tells
        the author to quote it in single backticks."""
        body = _write(tmp_path, _body_with(middle))
        result = _run([str(body), "checkout"])
        assert result.returncode == 1
        assert expected_message in result.stderr
        assert "single backticks" in result.stderr

    def test_findings_path_shaped_like_a_variable_assignment_is_still_scanned(self, tmp_path):
        """awk treats a bare NAME=value operand as an assignment and reads
        stdin instead, so the file must reach awk through a redirect."""
        body = tmp_path / "a=b.md"
        body.write_text(_body_with("<details>\nproof"))
        result = _run(["a=b.md", "checkout"], cwd=tmp_path)
        assert result.returncode == 1
        assert "never closed" in result.stderr

    def test_unbalanced_tags_fail_in_diff_only_mode_too(self, tmp_path):
        body = _write(tmp_path, _body_with(f"{DISCLOSURE_LINE}\n\n<details>"))
        result = _run([str(body), "diff-only"])
        assert result.returncode == 1

    def test_awk_failure_exits_two_and_names_the_file(self, tmp_path):
        """An awk that cannot run must not read as balanced or as a tag defect."""
        stub_dir = tmp_path / "stub-bin"
        stub_dir.mkdir()
        stub_awk = stub_dir / "awk"
        stub_awk.write_text("#!/bin/sh\nexit 2\n")
        stub_awk.chmod(0o755)
        env = {**os.environ, "PATH": f"{stub_dir}{os.pathsep}{os.environ['PATH']}"}
        body = _write(tmp_path, _body_with("proof"))
        result = _run([str(body), "checkout"], env=env)
        assert result.returncode == 2
        assert str(body) in result.stderr
        assert "did not complete" in result.stderr


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

    def test_disclosure_sentence_inside_a_longer_line_exits_one(self, tmp_path):
        """The disclosure must stand as a whole line -- the same sentence
        embedded in a longer line must still fail."""
        body = _write(
            tmp_path,
            f"**[Claude Code]**\n\nNote: ({DISCLOSURE_LINE}) is what the skill requires.\n\n{TRAILER}\n",
        )
        result = _run([str(body), "diff-only"])
        assert result.returncode == 1
        assert str(body) in result.stderr

    def test_checkout_mode_body_missing_disclosure_line_still_passes(self, tmp_path):
        """The disclosure line is required only in diff-only mode -- a
        checkout-mode body carrying no such line must not fail on it."""
        body = _write(tmp_path, f"**[Claude Code]**\n\n# Findings\n\n{TRAILER}\n")
        result = _run([str(body), "checkout"])
        assert result.returncode == 0, result.stderr
