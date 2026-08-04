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

    @pytest.mark.parametrize(
        "path",
        [
            "/home/user/.ssh/deploy_key",
            "/home/user/.ssh/github_actions_key",
            "/home/user/.ssh/id_ed25519_github",
            "/home/user/.ssh/subdir/deploy_key",
        ],
    )
    def test_custom_named_ssh_key_denied(self, isolated_home, path):
        """Required regression test for a High-severity finding: a
        custom-named SSH key (no fixed basename to enumerate) previously
        bypassed both the basename blocklist and the .ssh directory-glob
        group, since that group deliberately excluded named-file
        references. Deny-by-default under .ssh, allowlisting only the
        known-safe basenames, closes this without reopening the
        id_rsa.pub/authorized_keys allowance. Every case here requires the
        new _lib_has_unsafe_ssh_dir_reference mechanism specifically -- a
        case satisfiable by the pre-existing basename/backup-suffix regex
        alone belongs in test_custom_named_ssh_key_in_backup_directory_denied
        instead."""
        assert run_hook(DENY_CREDENTIAL_FILE_READS_HOOK, read_input(path), home=isolated_home) == "deny"

    @pytest.mark.parametrize(
        "path",
        [
            "/home/user/.ssh.bak/id_rsa",
            "/home/user/.ssh_backup/id_rsa",
        ],
    )
    def test_custom_named_ssh_key_in_backup_directory_denied(self, isolated_home, path):
        """These already deny via the pre-existing bare-basename token
        match -- kept as an integration-level check on the hook's
        OR-combination, not a pin on the new mechanism specifically."""
        assert run_hook(DENY_CREDENTIAL_FILE_READS_HOOK, read_input(path), home=isolated_home) == "deny"

    @pytest.mark.parametrize(
        "path",
        [
            "/home/user/.ssh/deploy_key/",
            "/home/user/.ssh/id_rsa.bak",
            "/home/user/.ssh/id_rsa.old",
        ],
    )
    def test_trailing_slash_and_backup_suffix_key_denied(self, isolated_home, path):
        """Required regression test for a Critical finding: an earlier
        version of the deny-by-default mechanism treated any trailing-slash
        reference as an always-safe directory listing, fully reopening the
        custom-named-key bypass for one added character. Also pins a
        backup copy of a standard-named key (id_rsa.bak/id_rsa.old), the
        scenario this PR's own documentation names as newly closed."""
        assert run_hook(DENY_CREDENTIAL_FILE_READS_HOOK, read_input(path), home=isolated_home) == "deny"

    def test_ssh_directory_reference_accepted_false_positive_denied(self, isolated_home):
        """Documented accepted false positive: a directory reference under
        .ssh (a ControlMaster socket dir) is denied too, since its basename
        isn't on the safe allowlist and the mechanism can no longer
        special-case a trailing slash as proof of a safe directory listing."""
        assert run_hook(
            DENY_CREDENTIAL_FILE_READS_HOOK, read_input("/home/user/.ssh/sockets/"), home=isolated_home
        ) == "deny"

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
    # Leading-dot credentials.json — closes a dotfile-boundary gap        #
    # ------------------------------------------------------------------ #

    @pytest.mark.parametrize(
        "path",
        [
            "~/.credentials.json",
            "/home/user/.claude/.credentials.json",
        ],
    )
    def test_leading_dot_credentials_json_denied(self, isolated_home, path):
        """Required regression test: the credentials.json alternative's
        boundary required a non-dot character immediately before the match,
        so a leading-dot `.credentials.json` (Claude Code's own OAuth/API
        credential filename under CLAUDE_CONFIG_DIR) never matched -- the
        `.` of the dotfile prefix itself failed the boundary. Pinned so a
        future edit collapsing the new `\\.credentials\\.json` alternative
        back into the bare one doesn't silently reopen this."""
        assert run_hook(DENY_CREDENTIAL_FILE_READS_HOOK, read_input(path), home=isolated_home) == "deny"

    def test_credentials_json_substring_in_longer_token_allowed(self, isolated_home):
        """Required regression test proving the leading-dot fix stays
        narrowly scoped: a filename that merely ends in `.credentials.json`
        without being the dotfile itself (preceded by a non-boundary
        character) must not match, the same way the pre-existing bare
        `credentials.json` alternative never matched inside a longer
        token."""
        assert run_hook(
            DENY_CREDENTIAL_FILE_READS_HOOK, read_input("/foo/backup.credentials.json"), home=isolated_home
        ) == "allow"

    def test_leading_dot_credentials_json_backup_suffix_denied(self, isolated_home):
        """Required regression test: the new `\\.credentials\\.json`
        alternative inherits group 2's trailing boundary, which allows a
        following `.` -- closing a `credentials.json.bak`-style backup-copy
        bypass (per this constant's own header comment in _lib.sh) -- so a
        future edit that narrows the leading-dot alternative's own trailing
        boundary doesn't silently reopen this specifically for
        `.credentials.json`."""
        assert run_hook(
            DENY_CREDENTIAL_FILE_READS_HOOK, read_input("~/.credentials.json.bak"), home=isolated_home
        ) == "deny"

    # ------------------------------------------------------------------ #
    # Allow — no credential-shaped path, including the .pub exception     #
    # ------------------------------------------------------------------ #

    @pytest.mark.parametrize(
        "path",
        [
            "/home/user/.ssh/id_rsa.pub",
            "/home/user/.ssh/authorized_keys",
            "/home/user/.ssh/known_hosts",
            "/home/user/.ssh/known_hosts.old",
            "/home/user/.ssh/config",
            "/home/user/.ssh/subdir/id_rsa.pub",
            "/home/user/.ssh.bak/id_rsa.pub",
            "/home/user/.ssh_backup/authorized_keys",
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

    def test_symlink_to_guard_file_only_match_denied(self, isolated_home, tmp_path):
        """Required regression test: _matches_credential_path is shared by
        both the raw-path and symlink-resolved-path call sites specifically
        so a personal credential-file-guard.md entry also applies after
        symlink resolution, not just to the raw path. A symlink target that
        matches ONLY the guard-file glob (not the built-in regex) proves
        that composition actually fires post-resolution."""
        guard_file = isolated_home / ".claude" / "credential-file-guard.md"
        guard_file.write_text("**/my-org-secret-*\n")
        target = tmp_path / "my-org-secret-token"
        target.write_text("secret\n")
        link = tmp_path / "notes.txt"
        link.symlink_to(target)
        assert run_hook(DENY_CREDENTIAL_FILE_READS_HOOK, read_input(str(link)), home=isolated_home) == "deny"

    def test_credential_file_guard_armed_at_config_dir_only_denied(self, isolated_home, tmp_path):
        """Guard armed only at the resolved CLAUDE_CONFIG_DIR location (no
        legacy copy) -- confirms the new path is read."""
        config_dir = tmp_path / "profile"
        config_dir.mkdir()
        (config_dir / "credential-file-guard.md").write_text("**/my-org-token-*\n")
        assert run_hook(
            DENY_CREDENTIAL_FILE_READS_HOOK,
            read_input("/home/<user>/my-org-token-prod"),
            home=isolated_home,
            extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)},
        ) == "deny"

    def test_credential_file_guard_armed_at_legacy_location_falls_back_denied(self, isolated_home, tmp_path):
        """Regression test: a guard armed only at the legacy $HOME/.claude
        location must still fire when CLAUDE_CONFIG_DIR points at a
        directory with no copy of the file -- proves continuity for a user
        who armed the guard before CLAUDE_CONFIG_DIR support existed."""
        guard_file = isolated_home / ".claude" / "credential-file-guard.md"
        guard_file.write_text("**/my-org-token-*\n")
        config_dir = tmp_path / "profile"
        config_dir.mkdir()
        assert run_hook(
            DENY_CREDENTIAL_FILE_READS_HOOK,
            read_input("/home/<user>/my-org-token-prod"),
            home=isolated_home,
            extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)},
        ) == "deny"

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
    # Empty/missing file_path — intentional fail-open (nothing to check)  #
    # ------------------------------------------------------------------ #

    def test_empty_file_path_allowed(self, isolated_home):
        assert run_hook(DENY_CREDENTIAL_FILE_READS_HOOK, read_input(""), home=isolated_home) == "allow"

    def test_missing_file_path_allowed(self, isolated_home):
        payload = {"tool_name": "Read", "tool_input": {}}
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
