"""Tests for review-pr-scan-findings-body.sh -- the mechanical secret scan
run over /review-pr's findings-body file before Step 9 posts it.

Reuses _LIB_CREDENTIAL_VALUE_REGEX (_lib.sh), the same credential-shape
pattern deny-pii-in-commits.sh and redact-credential-values.sh already use,
so these tests pin the wiring, not a second copy of the regex itself.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from helpers import SCRIPTS_DIR

SCRIPT = SCRIPTS_DIR / "review-pr-scan-findings-body.sh"


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


class TestCleanBodyPasses:
    def test_ordinary_findings_prose_exits_zero(self, tmp_path):
        findings_body = _write(
            tmp_path, "# Findings\n\n- nit: rename `x` to `count`\n"
        )
        result = _run([str(findings_body)])
        assert result.returncode == 0, result.stderr

    def test_empty_file_exits_zero(self, tmp_path):
        findings_body = _write(tmp_path, "")
        result = _run([str(findings_body)])
        assert result.returncode == 0, result.stderr


class TestCredentialShapedHitsFailClosed:
    def test_github_token_prefix_exits_one_and_names_the_line(self, tmp_path):
        findings_body = _write(
            tmp_path,
            "# Findings\n\nleaked: ghp_" + "a" * 36 + "\n",
        )
        result = _run([str(findings_body)])
        assert result.returncode == 1
        assert "line 3" in result.stderr

    def test_aws_access_key_id_exits_one(self, tmp_path):
        findings_body = _write(tmp_path, "key: AKIA" + "B" * 16 + "\n")
        result = _run([str(findings_body)])
        assert result.returncode == 1

    def test_pem_private_key_header_exits_one(self, tmp_path):
        findings_body = _write(
            tmp_path, "-----BEGIN RSA PRIVATE KEY-----\nMIIBogIBAAKCAQ==\n"
        )
        result = _run([str(findings_body)])
        assert result.returncode == 1

    def test_matched_value_never_appears_on_stdout_or_stderr(self, tmp_path):
        """The deny message names the location and type of the hit, never
        the credential value itself -- a scan whose own output leaked the
        secret would defeat the point of scanning before posting."""
        secret = "ghp_" + "z" * 36
        findings_body = _write(tmp_path, f"leaked: {secret}\n")
        result = _run([str(findings_body)])
        assert result.returncode == 1
        assert secret not in result.stdout
        assert secret not in result.stderr
