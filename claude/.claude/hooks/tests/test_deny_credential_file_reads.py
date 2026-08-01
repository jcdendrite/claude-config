"""Tests for deny-credential-file-reads.sh."""
from __future__ import annotations

import json
import subprocess

import pytest
from helpers import HOOKS_DIR, bash_input, edit_input, read_input, run_hook, run_hook_reason, write_input

DENY_CREDENTIAL_FILE_READS_HOOK = HOOKS_DIR / "deny-credential-file-reads.sh"


class TestDenyCredentialFileReads:
    # ------------------------------------------------------------------ #
    # Deny — credential-shaped file paths                                 #
    # ------------------------------------------------------------------ #

    @pytest.mark.parametrize(
        "path",
        [
            "/home/user/.ssh/id_rsa",
            "/home/user/.ssh/id_dsa",
            "/home/user/.ssh/id_ecdsa",
            "/home/user/.ssh/id_ed25519",
            "~/.netrc",
            "~/_netrc",
            "~/.git-credentials",
            "~/.aws/credentials",
            "~/.docker/config.json",
            "~/.kube/config",
            "~/.config/gh/hosts.yml",
            "/foo/credentials.json",
            "/foo/.env",
            "/foo/.env.local",
            "/foo/.env.production",
            "/foo/.env.development",
            "/foo/.env.staging",
            "/foo/.env.test",
        ],
    )
    def test_credential_path_denied(self, isolated_home, path):
        assert run_hook(DENY_CREDENTIAL_FILE_READS_HOOK, read_input(path), home=isolated_home) == "deny"

    # ------------------------------------------------------------------ #
    # Case-insensitivity — closes a case-insensitive-filesystem bypass    #
    # ------------------------------------------------------------------ #

    @pytest.mark.parametrize(
        "path",
        [
            "/home/user/.ssh/ID_RSA",
            "/home/user/.NETRC",
            "/home/user/.Aws/Credentials",
            "/foo/CREDENTIALS.JSON",
            "/foo/.ENV",
        ],
    )
    def test_case_varied_credential_path_denied(self, isolated_home, path):
        """Required regression test: the built-in path match is
        case-insensitive. On the default case-insensitive-but-case-
        preserving filesystem (macOS APFS/HFS+, Windows NTFS), a
        case-varied path opens the identical on-disk file as the
        canonical-case path — a case-sensitive match here would be a
        silent, unbounded bypass of a gate whose stated design is "no
        bypass valve." Pinned so a future edit reverting to a
        case-sensitive grep doesn't reopen this silently."""
        assert run_hook(DENY_CREDENTIAL_FILE_READS_HOOK, read_input(path), home=isolated_home) == "deny"

    # ------------------------------------------------------------------ #
    # Allow — no credential-shaped path, including the .pub exception     #
    # ------------------------------------------------------------------ #

    @pytest.mark.parametrize(
        "path",
        [
            "/home/user/.ssh/id_rsa.pub",
            "/home/user/.ssh/authorized_keys",
            "/foo/.env.example",
            "/foo/.env.template",
            "/foo/.env.sample",
            "/foo/README.md",
            "/foo/notes.txt",
            # Non-secret siblings of the directory-qualified alternatives —
            # these must NOT match, since the regex qualifies them by
            # directory specifically to avoid over-matching generic bare
            # filenames like "config" or "credentials".
            "/home/user/.aws/config",
            "/home/user/.docker/daemon.json",
            "/home/user/.kube/kubeconfig",
            "/home/user/.config/gh/config.yml",
        ],
    )
    def test_non_credential_path_allowed(self, isolated_home, path):
        assert run_hook(DENY_CREDENTIAL_FILE_READS_HOOK, read_input(path), home=isolated_home) == "allow"

    # ------------------------------------------------------------------ #
    # Symlink cases                                                       #
    # ------------------------------------------------------------------ #

    def test_symlink_to_ssh_key_target_denied(self, isolated_home, tmp_path):
        """Mirrors test_symlink_to_denied_target_denied in
        test_deny_env_reads.py: unlike deny-credential-bash-reads.sh (raw
        text, no filesystem resolution), this hook DOES resolve and deny a
        symlink named innocuously but pointing at a credential path."""
        target = tmp_path / "id_rsa"
        target.write_text("PRIVATE KEY DATA\n")
        link = tmp_path / "notes.txt"
        link.symlink_to(target)
        assert run_hook(DENY_CREDENTIAL_FILE_READS_HOOK, read_input(str(link)), home=isolated_home) == "deny"

    def test_symlink_deny_message_names_resolved_path(self, isolated_home, tmp_path):
        target = tmp_path / "id_rsa"
        target.write_text("PRIVATE KEY DATA\n")
        link = tmp_path / "notes.txt"
        link.symlink_to(target)
        reason = run_hook_reason(DENY_CREDENTIAL_FILE_READS_HOOK, read_input(str(link)), home=isolated_home)
        assert reason is not None
        assert "id_rsa" in reason

    def test_symlink_to_non_credential_file_allowed(self, isolated_home, tmp_path):
        target = tmp_path / "plain.txt"
        target.write_text("just notes\n")
        link = tmp_path / "notes.txt"
        link.symlink_to(target)
        assert run_hook(DENY_CREDENTIAL_FILE_READS_HOOK, read_input(str(link)), home=isolated_home) == "allow"

    def test_broken_symlink_denied(self, isolated_home, tmp_path):
        """No allowlist here (unlike deny-env-reads.sh): every symlinked
        Read target is resolved, so a broken symlink at any path fails
        closed regardless of its own name."""
        link = tmp_path / "notes.txt"
        link.symlink_to(tmp_path / "nonexistent")
        assert run_hook(DENY_CREDENTIAL_FILE_READS_HOOK, read_input(str(link)), home=isolated_home) == "deny"

    # ------------------------------------------------------------------ #
    # Personal/org-specific additions — credential-file-guard.md          #
    # ------------------------------------------------------------------ #

    def test_credential_file_guard_glob_match_denied(self, isolated_home):
        guard_file = isolated_home / ".claude" / "credential-file-guard.md"
        guard_file.write_text("**/my-org-token-*\n")
        assert run_hook(
            DENY_CREDENTIAL_FILE_READS_HOOK, read_input("/home/user/my-org-token-prod"), home=isolated_home
        ) == "deny"

    def test_credential_file_guard_case_varied_match_denied(self, isolated_home):
        """Required regression test: the personal-additions glob match is
        case-folded (scoped shopt -s nocasematch) the same as the built-in
        regex — a case-varied path must not bypass a user's own
        credential-file-guard.md entry any more than it bypasses the
        built-in set."""
        guard_file = isolated_home / ".claude" / "credential-file-guard.md"
        guard_file.write_text("**/my-org-token-*\n")
        assert run_hook(
            DENY_CREDENTIAL_FILE_READS_HOOK, read_input("/home/user/MY-ORG-TOKEN-prod"), home=isolated_home
        ) == "deny"

    def test_credential_file_guard_no_match_allowed(self, isolated_home):
        guard_file = isolated_home / ".claude" / "credential-file-guard.md"
        guard_file.write_text("**/my-org-token-*\n")
        assert run_hook(
            DENY_CREDENTIAL_FILE_READS_HOOK, read_input("/home/user/README.md"), home=isolated_home
        ) == "allow"

    # ------------------------------------------------------------------ #
    # Deny message content                                                #
    # ------------------------------------------------------------------ #

    def test_deny_message_names_path(self, isolated_home):
        reason = run_hook_reason(
            DENY_CREDENTIAL_FILE_READS_HOOK, read_input("/home/user/.ssh/id_rsa"), home=isolated_home
        )
        assert reason is not None
        assert "/home/user/.ssh/id_rsa" in reason

    # ------------------------------------------------------------------ #
    # Defense-in-depth: non-Read tool names pass through                  #
    # ------------------------------------------------------------------ #

    @pytest.mark.parametrize(
        "payload",
        [
            bash_input("cat ~/.ssh/id_rsa"),
            edit_input("/home/user/.ssh/id_rsa"),
            write_input("/home/user/.ssh/id_rsa"),
        ],
    )
    def test_non_read_tools_pass_through(self, isolated_home, payload):
        assert run_hook(DENY_CREDENTIAL_FILE_READS_HOOK, payload, home=isolated_home) == "allow"

    # ------------------------------------------------------------------ #
    # Fail-closed on malformed JSON                                       #
    # ------------------------------------------------------------------ #

    def test_malformed_json_denied(self):
        result = subprocess.run(
            [str(DENY_CREDENTIAL_FILE_READS_HOOK)],
            input="not valid json",
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.stdout.strip()
        payload = json.loads(result.stdout)
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"
