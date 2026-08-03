"""Tests for scan-issue-body.sh's mechanical disclosure-control scan.

Layer 2 of the three-layer disclosure control in
.claude/plans/transcript-cost-subcommand.md: a pre-POST scan for
identifying-shape content in a GitHub issue/comment body, chained before
`gh api -F body=@<file>`. One allow (clean) and one deny (matching) fixture
per detection class below, invoked via subprocess + return-code assertion,
matching test_claude_auto.py's shape for a shell-script-under-test.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

_SCRIPT = Path(__file__).parent.parent / "scan-issue-body.sh"


def _run(body_text: str, tmp_path: Path) -> subprocess.CompletedProcess:
    body_file = tmp_path / "body.md"
    body_file.write_text(body_text)
    return subprocess.run([str(_SCRIPT), str(body_file)], capture_output=True, text=True, check=False)


class TestScanIssueBodyCleanFile:
    def test_clean_aggregate_only_body_exits_zero(self, tmp_path):
        """A corpus-aggregate-only body with no identifying-shape content is safe to publish."""
        result = _run(
            "This issue tracks the corpus-wide cost audit: 268 sessions,"
            " 56,358 priced turns, $5,906 at list price.\n",
            tmp_path,
        )
        assert result.returncode == 0


class TestScanIssueBodyIPv4:
    def test_allow_no_ip_literal(self, tmp_path):
        result = _run("Cache read is 51.4% of spend across 268 sessions.\n", tmp_path)
        assert result.returncode == 0

    def test_deny_ip_literal_present(self, tmp_path):
        result = _run("The internal service lives at 10.20.30.40 in the VPC.\n", tmp_path)
        assert result.returncode != 0


class TestScanIssueBodySshKeyPath:
    def test_allow_no_key_path(self, tmp_path):
        result = _run("Denials include Bash exact-match rules for git commit and git push.\n", tmp_path)
        assert result.returncode == 0

    def test_deny_ssh_key_path_present(self, tmp_path):
        result = _run("Reproduced with the key at ~/.ssh/id_ed25519 loaded.\n", tmp_path)
        assert result.returncode != 0


class TestScanIssueBodyHomeRootedPath:
    def test_allow_no_home_path(self, tmp_path):
        result = _run("The tool lives at claude/.claude/scripts/transcript-analysis.py.\n", tmp_path)
        assert result.returncode == 0

    def test_deny_home_rooted_path_present(self, tmp_path):
        result = _run("Session data was read from /Users/alice/.claude/projects/.\n", tmp_path)
        assert result.returncode != 0

    def test_deny_home_rooted_path_without_trailing_slash(self, tmp_path):
        """A bare username reference with no following path segment must still match."""
        result = _run("My home directory is /Users/jared, nothing else.\n", tmp_path)
        assert result.returncode != 0


class TestScanIssueBodyLongHexIdentifier:
    def test_allow_no_hex_identifier(self, tmp_path):
        result = _run("268 sessions, 56358 priced turns, 5906 dollars at list price.\n", tmp_path)
        assert result.returncode == 0

    def test_deny_hex_identifier_present(self, tmp_path):
        result = _run("Session 875cfbeb-f03e-4a12-9876-abcdef012345 drove the spike.\n", tmp_path)
        assert result.returncode != 0

    def test_allow_31_hex_chars_below_threshold(self, tmp_path):
        result = _run("Short id abcdef0123456789abcdef012345678 stays under the fencepost.\n", tmp_path)
        assert result.returncode == 0

    def test_deny_32_hex_chars_at_threshold(self, tmp_path):
        result = _run("Long id abcdef0123456789abcdef0123456789 hits the fencepost exactly.\n", tmp_path)
        assert result.returncode != 0


class TestScanIssueBodyInternalHostname:
    def test_allow_no_internal_hostname(self, tmp_path):
        result = _run("See platform.claude.com/docs/en/about-claude/pricing for rates.\n", tmp_path)
        assert result.returncode == 0

    def test_deny_internal_hostname_present(self, tmp_path):
        result = _run("The dashboard is hosted at metrics.eng.corp for this team.\n", tmp_path)
        assert result.returncode != 0

    def test_deny_internal_hostname_at_end_of_line(self, tmp_path):
        """The trailing-boundary check must not require a POSIX \\b extension (not
        portable across grep implementations) — a bare '(...|$)' alternation covers
        end-of-line the same way a word boundary would."""
        result = _run("Reachable internally at db.internal\n", tmp_path)
        assert result.returncode != 0

    def test_allow_word_containing_tld_as_prefix_not_flagged(self, tmp_path):
        """A word that merely starts with a TLD-like label ('internal...') must not
        match — the boundary check requires a non-word character or end of line
        after the TLD, not just any occurrence of the substring."""
        result = _run("This is handled internally, not by an internal.tld host.\n", tmp_path)
        assert result.returncode == 0


class TestScanIssueBodySlackChannelShape:
    def test_allow_no_slack_channel(self, tmp_path):
        result = _run("Cost audit findings. See F1 through F4 below.\n", tmp_path)
        assert result.returncode == 0

    def test_deny_slack_channel_present(self, tmp_path):
        result = _run("Discussed in #eng-platform-alerts before filing.\n", tmp_path)
        assert result.returncode != 0

    def test_allow_github_issue_reference_not_flagged_as_channel(self, tmp_path):
        """A plain issue reference (#421) is all-digits and must not collide with
        the Slack-channel shape — this is exactly the content Step 8 of the plan
        posts as a comment on issue #421."""
        result = _run("Posting the git mkdir misparse note as a comment on #421.\n", tmp_path)
        assert result.returncode == 0

    def test_deny_markdown_anchor_link_shares_channel_shape(self, tmp_path):
        """A markdown anchor fragment (#word-word) is indistinguishable from a real
        Slack channel by shape alone, and this repo's own docs use that shape
        (docs/skills.md#skill-architecture-notes) — deliberately still blocked,
        since loosening the charset to admit hyphenated words would defeat the
        detector's actual purpose of catching Slack channel names, which are
        themselves lowercase-hyphenated words. Rephrase around it rather than
        widen the pattern."""
        result = _run("See docs/skills.md#skill-architecture-notes for the breakdown.\n", tmp_path)
        assert result.returncode != 0


class TestScanIssueBodyFailsClosed:
    """The mid-loop rc>=2 branch (a detector's grep call itself erroring, as opposed
    to the upfront -r check below) has no direct fixture — reliably forcing a grep
    error on a file that passes the upfront readability check requires a TOCTOU race
    that isn't portable across filesystems/CI. Left as a known coverage gap rather
    than a flaky test."""

    def test_unreadable_file_exits_nonzero(self, tmp_path):
        """A missing/unreadable body file fails closed — never treated as clean."""
        missing = tmp_path / "does-not-exist.md"
        result = subprocess.run([str(_SCRIPT), str(missing)], capture_output=True, text=True, check=False)
        assert result.returncode != 0

    def test_missing_argument_exits_nonzero(self):
        """Wrong usage (no file argument) fails closed too."""
        result = subprocess.run([str(_SCRIPT)], capture_output=True, text=True, check=False)
        assert result.returncode != 0

    def test_exit_code_is_not_bare_grep_polarity(self, tmp_path):
        """A match must produce a non-zero exit — the opposite of what chaining
        grep's own exit status directly would give (grep exits 1 on *no* match)."""
        result = _run("Contact the VPC gateway at 10.0.0.1 for details.\n", tmp_path)
        assert result.returncode != 0
