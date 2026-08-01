"""Tests for deny-credential-bash-reads.sh."""
from __future__ import annotations

import json
import subprocess

import pytest
from helpers import HOOKS_DIR, bash_input, edit_input, read_input, run_hook, run_hook_reason, write_input

DENY_CREDENTIAL_BASH_READS_HOOK = HOOKS_DIR / "deny-credential-bash-reads.sh"


class TestDenyCredentialBashReads:
    # ------------------------------------------------------------------ #
    # Deny — credential-shaped path tokens                                #
    # ------------------------------------------------------------------ #

    @pytest.mark.parametrize(
        "command",
        [
            "cat ~/.ssh/id_rsa",
            "cd ~/.ssh && cat id_rsa",
            "cat ~/.ssh/id_dsa",
            "cat ~/.ssh/id_ecdsa",
            "cat ~/.ssh/id_ed25519",
            "cat ~/.netrc",
            "cat ~/_netrc",
            "cat ~/.git-credentials",
            "cat ~/.aws/credentials",
            "cat ~/.docker/config.json",
            "cat ~/.kube/config",
            "cat ~/.config/gh/hosts.yml",
            "cat /foo/credentials.json",
            "cat /foo/.env",
            "cat /foo/.env.local",
            "cat /foo/.env.production",
            "cat /foo/.env.development",
            "cat /foo/.env.staging",
            "cat /foo/.env.test",
        ],
    )
    def test_credential_path_denied(self, isolated_home, command):
        assert run_hook(DENY_CREDENTIAL_BASH_READS_HOOK, bash_input(command), home=isolated_home) == "deny"

    def test_no_bypass_valve_ssh_add_chmod_ssh_i_denied(self, isolated_home):
        """Required regression test: commands that reference a credential
        path without exposing its content (ssh-add, chmod, ssh -i) are
        still denied — the accepted false-positive cost of matching on the
        path token alone, with no content-exposing-verb carve-out. Pinned
        so it is never silently "fixed" back into a bypass."""
        for command in (
            "ssh-add ~/.ssh/id_rsa",
            "chmod 600 ~/.ssh/id_rsa",
            "ssh -i ~/.ssh/id_rsa host",
        ):
            assert run_hook(DENY_CREDENTIAL_BASH_READS_HOOK, bash_input(command), home=isolated_home) == "deny", command

    @pytest.mark.parametrize(
        "command",
        [
            "cat ~/.ssh/ID_RSA",
            "cat ~/.NETRC",
            "cat ~/.Aws/Credentials",
            "cat /foo/CREDENTIALS.JSON",
            "cat /foo/.ENV",
        ],
    )
    def test_case_varied_credential_path_denied(self, isolated_home, command):
        """Required regression test: the built-in path match is
        case-insensitive. On the default case-insensitive-but-case-
        preserving filesystem (macOS APFS/HFS+, Windows NTFS), a
        case-varied path opens the identical on-disk file as the
        canonical-case path — a case-sensitive match here would be a
        silent, unbounded bypass of a gate whose stated design is "no
        bypass valve." Pinned so a future edit reverting to a
        case-sensitive grep doesn't reopen this silently."""
        assert run_hook(DENY_CREDENTIAL_BASH_READS_HOOK, bash_input(command), home=isolated_home) == "deny"

    def test_grep_search_pattern_residual_denied(self, isolated_home):
        """Required regression test pinning the documented residual: a
        `grep` command searching FOR the literal string "id_rsa" (not
        opening a file by that name) is also denied, since the hook has no
        shell semantics to distinguish a filename argument from a
        search-pattern argument."""
        assert run_hook(DENY_CREDENTIAL_BASH_READS_HOOK, bash_input('grep "id_rsa" .'), home=isolated_home) == "deny"

    # ------------------------------------------------------------------ #
    # Documented residual — symlink/rename bypass                         #
    # ------------------------------------------------------------------ #

    def test_symlink_rename_bypass_allowed(self, isolated_home):
        """Required regression test pinning the documented symlink/rename
        residual: once a credential path has been symlinked to an
        innocuous name in an earlier, separate Bash call, a later command
        that only references the innocuous name (`cat notes.txt`) contains
        no credential-path token in its own command text, and this
        raw-text-only hook cannot catch it. Pins the accepted gap as a
        known, tracked limitation rather than an unnoticed one."""
        assert run_hook(DENY_CREDENTIAL_BASH_READS_HOOK, bash_input("cat notes.txt"), home=isolated_home) == "allow"

    # ------------------------------------------------------------------ #
    # Allow — no credential-shaped token present                          #
    # ------------------------------------------------------------------ #

    @pytest.mark.parametrize(
        "command",
        [
            "ls -la",
            "git status",
            "cat ~/.ssh/id_rsa.pub",
            "cat ~/.ssh/authorized_keys",
            "cat /foo/.env.example",
            "cat /foo/README.md",
            "echo my_credentials_variable",
            # Non-secret siblings of the directory-qualified alternatives —
            # these must NOT match, since the regex qualifies them by
            # directory specifically to avoid over-matching generic bare
            # filenames like "config" or "credentials".
            "cat ~/.aws/config",
            "cat ~/.docker/daemon.json",
            "cat ~/.kube/kubeconfig",
            "cat ~/.config/gh/config.yml",
        ],
    )
    def test_non_credential_command_allowed(self, isolated_home, command):
        assert run_hook(DENY_CREDENTIAL_BASH_READS_HOOK, bash_input(command), home=isolated_home) == "allow"

    # ------------------------------------------------------------------ #
    # Deny message content                                                #
    # ------------------------------------------------------------------ #

    def test_deny_message_names_no_bypass_valve(self, isolated_home):
        reason = run_hook_reason(DENY_CREDENTIAL_BASH_READS_HOOK, bash_input("cat ~/.ssh/id_rsa"), home=isolated_home)
        assert reason is not None
        assert "shell escape" in reason

    # ------------------------------------------------------------------ #
    # Personal/org-specific additions — credential-file-guard.md          #
    # ------------------------------------------------------------------ #

    def test_credential_file_guard_glob_match_denied(self, isolated_home):
        guard_file = isolated_home / ".claude" / "credential-file-guard.md"
        guard_file.write_text("my-org-token-*\n")
        assert run_hook(
            DENY_CREDENTIAL_BASH_READS_HOOK, bash_input("cat ~/.config/my-org-token-prod"), home=isolated_home
        ) == "deny"

    def test_credential_file_guard_case_varied_match_denied(self, isolated_home):
        """Required regression test: the personal-additions glob match is
        case-folded (scoped shopt -s nocasematch) the same as the built-in
        regex — a case-varied command must not bypass a user's own
        credential-file-guard.md entry any more than it bypasses the
        built-in set."""
        guard_file = isolated_home / ".claude" / "credential-file-guard.md"
        guard_file.write_text("my-org-token-*\n")
        assert run_hook(
            DENY_CREDENTIAL_BASH_READS_HOOK, bash_input("cat ~/.config/MY-ORG-TOKEN-prod"), home=isolated_home
        ) == "deny"

    def test_credential_file_guard_no_match_allowed(self, isolated_home):
        guard_file = isolated_home / ".claude" / "credential-file-guard.md"
        guard_file.write_text("my-org-token-*\n")
        assert run_hook(DENY_CREDENTIAL_BASH_READS_HOOK, bash_input("ls -la"), home=isolated_home) == "allow"

    def test_credential_file_guard_absent_no_error(self, isolated_home):
        """No guard file at all — the hook still runs its built-in check
        cleanly rather than erroring on a missing optional file."""
        assert run_hook(DENY_CREDENTIAL_BASH_READS_HOOK, bash_input("ls -la"), home=isolated_home) == "allow"

    # ------------------------------------------------------------------ #
    # Defense-in-depth: non-Bash tool names pass through                  #
    # ------------------------------------------------------------------ #

    @pytest.mark.parametrize(
        "payload",
        [
            read_input("/foo/.env.production"),
            edit_input("~/.ssh/id_rsa"),
            write_input("~/.ssh/id_rsa"),
        ],
    )
    def test_non_bash_tools_pass_through(self, isolated_home, payload):
        assert run_hook(DENY_CREDENTIAL_BASH_READS_HOOK, payload, home=isolated_home) == "allow"

    # ------------------------------------------------------------------ #
    # Fail-closed on malformed JSON                                       #
    # ------------------------------------------------------------------ #

    def test_malformed_json_denied(self):
        result = subprocess.run(
            [str(DENY_CREDENTIAL_BASH_READS_HOOK)],
            input="not valid json",
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.stdout.strip()
        payload = json.loads(result.stdout)
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"
