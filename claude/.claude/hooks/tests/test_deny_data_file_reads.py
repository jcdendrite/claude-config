"""Tests for deny-data-file-reads.sh."""
from __future__ import annotations

import json
import subprocess

import pytest
from helpers import HOOKS_DIR, bash_input, edit_input, read_input, run_hook, run_hook_reason, write_input

DENY_DATA_FILE_READS_HOOK = HOOKS_DIR / "deny-data-file-reads.sh"

DATA_EXTENSIONS = [
    "csv", "tsv", "parquet", "avro", "xlsx", "ndjson", "jsonl",
    "dump", "bak", "sqlite", "db", "dta", "sav", "pkl",
]


class TestDenyDataFileReads:
    @pytest.fixture
    def read_guard(self, isolated_home):
        """Writer for ~/.claude/data-file-read-guard.md inside the isolated
        $HOME. Calling it arms the hook; tests that never call it run
        against an unarmed (no-op) hook."""
        guard_file = isolated_home / ".claude" / "data-file-read-guard.md"

        def _write(content: str = ""):
            guard_file.write_text(content)
            return guard_file

        return _write

    # ------------------------------------------------------------------ #
    # Arming — opt-in via ~/.claude/data-file-read-guard.md presence      #
    # ------------------------------------------------------------------ #

    def test_unarmed_data_file_allowed(self, isolated_home):
        """No guard file — the hook is a no-op even for a .csv Read."""
        assert run_hook(DENY_DATA_FILE_READS_HOOK, read_input("/tmp/export.csv")) == "allow"

    def test_non_regular_config_file_treated_as_unarmed(self, isolated_home):
        """A broken symlink at the guard-file path is not a regular file:
        `[ -f ]` is false, so the hook stays dormant (allow) rather than
        erroring — the same guard that keeps a FIFO from blocking the read."""
        guard_file = isolated_home / ".claude" / "data-file-read-guard.md"
        guard_file.symlink_to("/nonexistent/data-file-read-guard-target")
        assert run_hook(DENY_DATA_FILE_READS_HOOK, read_input("/tmp/export.csv")) == "allow"

    # ------------------------------------------------------------------ #
    # Built-in rule: data-file extensions                                 #
    # ------------------------------------------------------------------ #

    @pytest.mark.parametrize("ext", DATA_EXTENSIONS)
    def test_data_extension_denied(self, isolated_home, read_guard, ext):
        read_guard()
        assert run_hook(DENY_DATA_FILE_READS_HOOK, read_input(f"/tmp/export.{ext}")) == "deny"

    @pytest.mark.parametrize(
        "path",
        [
            "/proj/src/main.py",
            "/proj/README.md",
            "/proj/config.json",
            "/proj/notes.txt",
        ],
    )
    def test_non_data_file_allowed(self, isolated_home, read_guard, path):
        read_guard()
        assert run_hook(DENY_DATA_FILE_READS_HOOK, read_input(path)) == "allow"

    # ------------------------------------------------------------------ #
    # Built-in rule: under a Downloads/ directory                         #
    # ------------------------------------------------------------------ #

    def test_downloads_directory_denied(self, isolated_home, read_guard):
        read_guard()
        assert run_hook(DENY_DATA_FILE_READS_HOOK, read_input("/home/dev/Downloads/notes.txt")) == "deny"

    def test_non_downloads_text_file_allowed(self, isolated_home, read_guard):
        read_guard()
        assert run_hook(DENY_DATA_FILE_READS_HOOK, read_input("/home/dev/Documents/notes.txt")) == "allow"

    def test_lowercase_downloads_allowed(self, isolated_home, read_guard):
        """The built-in rule matches `Downloads/` case-sensitively (the OS
        capitalization); a lowercase `downloads/` directory is not matched."""
        read_guard()
        assert run_hook(DENY_DATA_FILE_READS_HOOK, read_input("/home/dev/downloads/notes.txt")) == "allow"

    # ------------------------------------------------------------------ #
    # Built-in rule: file size over the 5 MB threshold                    #
    # ------------------------------------------------------------------ #

    def test_oversized_file_denied(self, isolated_home, read_guard, tmp_path):
        read_guard()
        big = tmp_path / "big.log"
        big.write_bytes(b"x" * (6 * 1024 * 1024))
        assert run_hook(DENY_DATA_FILE_READS_HOOK, read_input(str(big))) == "deny"

    def test_small_file_allowed(self, isolated_home, read_guard, tmp_path):
        read_guard()
        small = tmp_path / "small.log"
        small.write_bytes(b"x" * 1024)
        assert run_hook(DENY_DATA_FILE_READS_HOOK, read_input(str(small))) == "allow"

    def test_file_exactly_at_threshold_allowed(self, isolated_home, read_guard, tmp_path):
        """The size comparison is strict `-gt`: a file of exactly 5 MB
        (5242880 bytes) is not over the threshold."""
        read_guard()
        at = tmp_path / "at.log"
        at.write_bytes(b"x" * 5242880)
        assert run_hook(DENY_DATA_FILE_READS_HOOK, read_input(str(at))) == "allow"

    def test_file_one_byte_over_threshold_denied(self, isolated_home, read_guard, tmp_path):
        """One byte over 5 MB crosses the strict `-gt` threshold."""
        read_guard()
        over = tmp_path / "over.log"
        over.write_bytes(b"x" * 5242881)
        assert run_hook(DENY_DATA_FILE_READS_HOOK, read_input(str(over))) == "deny"

    # ------------------------------------------------------------------ #
    # Configured path globs                                               #
    # ------------------------------------------------------------------ #

    def test_config_glob_match_denied(self, isolated_home, read_guard):
        read_guard("**/patient-exports/**\n")
        assert run_hook(DENY_DATA_FILE_READS_HOOK, read_input("/srv/patient-exports/2024/jan.txt")) == "deny"

    def test_config_glob_no_match_allowed(self, isolated_home, read_guard):
        read_guard("**/patient-exports/**\n")
        assert run_hook(DENY_DATA_FILE_READS_HOOK, read_input("/srv/code/jan.txt")) == "allow"

    def test_config_comments_and_blanks_ignored(self, isolated_home, read_guard):
        read_guard("# a comment\n\n*.weirddata\n")
        assert run_hook(DENY_DATA_FILE_READS_HOOK, read_input("/tmp/x.weirddata")) == "deny"

    def test_config_glob_armed_at_config_dir_only_denied(self, isolated_home, tmp_path):
        """Guard armed only at the resolved CLAUDE_CONFIG_DIR location (no
        legacy copy) -- confirms the new path is read."""
        config_dir = tmp_path / "profile"
        config_dir.mkdir()
        (config_dir / "data-file-read-guard.md").write_text("**/patient-exports/**\n")
        assert run_hook(
            DENY_DATA_FILE_READS_HOOK,
            read_input("/srv/patient-exports/2024/jan.txt"),
            extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)},
        ) == "deny"

    def test_config_glob_armed_at_legacy_location_falls_back_denied(self, isolated_home, read_guard, tmp_path):
        """Regression test: a guard armed only at the legacy $HOME/.claude
        location must still fire when CLAUDE_CONFIG_DIR points at a
        directory with no copy of the file -- proves continuity for a user
        who armed the guard before CLAUDE_CONFIG_DIR support existed."""
        read_guard("**/patient-exports/**\n")
        config_dir = tmp_path / "profile"
        config_dir.mkdir()
        assert run_hook(
            DENY_DATA_FILE_READS_HOOK,
            read_input("/srv/patient-exports/2024/jan.txt"),
            extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)},
        ) == "deny"

    # ------------------------------------------------------------------ #
    # Deny message names the path and the matched rule                    #
    # ------------------------------------------------------------------ #

    def test_deny_message_names_path(self, isolated_home, read_guard):
        read_guard()
        reason = run_hook_reason(DENY_DATA_FILE_READS_HOOK, read_input("/data/patients.csv"))
        assert reason is not None
        assert "/data/patients.csv" in reason

    # ------------------------------------------------------------------ #
    # Defense-in-depth: only the Read tool is gated                       #
    # ------------------------------------------------------------------ #

    @pytest.mark.parametrize(
        "payload",
        [
            bash_input("cat /tmp/export.csv"),
            edit_input("/tmp/export.csv"),
            write_input("/tmp/export.csv"),
        ],
    )
    def test_non_read_tools_pass_through(self, isolated_home, read_guard, payload):
        read_guard()
        assert run_hook(DENY_DATA_FILE_READS_HOOK, payload) == "allow"

    def test_non_string_file_path_handled(self, isolated_home, read_guard):
        """A non-string `file_path` field is a malformed Read tool call that
        the Read tool itself cannot execute — there is no file to gate, so
        the hook produces a clean allow without erroring under `set -u`."""
        read_guard()
        payload = {"tool_name": "Read", "tool_input": {"file_path": {"unexpected": "object"}}}
        assert run_hook(DENY_DATA_FILE_READS_HOOK, payload) == "allow"

    # ------------------------------------------------------------------ #
    # Fail-closed on malformed JSON                                       #
    # ------------------------------------------------------------------ #

    def test_malformed_json_denied(self):
        result = subprocess.run(
            [str(DENY_DATA_FILE_READS_HOOK)],
            input="not valid json",
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.stdout.strip()
        payload = json.loads(result.stdout)
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"
        # Assert the fail-closed path specifically fired — not some other deny.
        assert "could not parse" in payload["hookSpecificOutput"]["permissionDecisionReason"]
