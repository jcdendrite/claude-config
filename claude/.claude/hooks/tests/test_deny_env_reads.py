"""Tests for deny-env-reads.sh."""
from __future__ import annotations

import json
import subprocess

import pytest
from helpers import (
    HOOKS_DIR,
    bash_input,
    edit_input,
    read_input,
    run_hook,
    run_hook_reason,
    write_input,
)

DENY_ENV_READS_HOOK = HOOKS_DIR / "deny-env-reads.sh"


class TestDenyEnvReads:
    # ------------------------------------------------------------------ #
    # Allow — safe template suffixes (regular files, no symlink)          #
    # ------------------------------------------------------------------ #

    @pytest.mark.parametrize(
        "path",
        [
            "/foo/.env.example",
            "/foo/.env.template",
            "/foo/.env.sample",
            ".env.example",
            "~/proj/.env.example",
        ],
    )
    def test_template_suffixes_allowed(self, path):
        assert run_hook(DENY_ENV_READS_HOOK, read_input(path)) == "allow"

    # ------------------------------------------------------------------ #
    # Allow — non-env-shaped paths                                        #
    # ------------------------------------------------------------------ #

    @pytest.mark.parametrize(
        "path",
        [
            "/foo/somefile.txt",
            "/foo/env",
            "/foo/.envrc",  # direnv config — out of scope; test documents current allow behavior
            "/foo/README.md",
        ],
    )
    def test_non_env_shaped_allowed(self, path):
        assert run_hook(DENY_ENV_READS_HOOK, read_input(path)) == "allow"

    # ------------------------------------------------------------------ #
    # Allow — path edge cases that still produce a safe basename          #
    # ------------------------------------------------------------------ #

    @pytest.mark.parametrize(
        "path",
        [
            "/foo/.env.example/",   # trailing slash stripped by basename
            "//foo//.env.example",  # double slashes normalized
        ],
    )
    def test_path_edge_cases_allowed(self, path):
        assert run_hook(DENY_ENV_READS_HOOK, read_input(path)) == "allow"

    # ------------------------------------------------------------------ #
    # Deny — bare .env and well-known secret variants                     #
    # ------------------------------------------------------------------ #

    @pytest.mark.parametrize(
        "path",
        [
            "/foo/.env",
            "./.env",
            "/foo/.env.local",
            "/foo/.env.production",
            "/foo/.env.development",
            "/foo/.env.staging",
            "/foo/.env.test",
        ],
    )
    def test_secret_variants_denied(self, path):
        assert run_hook(DENY_ENV_READS_HOOK, read_input(path)) == "deny"

    # ------------------------------------------------------------------ #
    # Deny — exact-match policy: close variants of allowlisted names      #
    # ------------------------------------------------------------------ #

    @pytest.mark.parametrize(
        "path",
        [
            "/foo/.env.example.local",    # customized copy, not a template
            "/foo/.env.local.example",    # suffix order matters
            "/foo/.env.examples",         # plural — not the allowlist entry
            "/foo/.env.example2",         # numeric suffix
        ],
    )
    def test_close_variants_denied(self, path):
        assert run_hook(DENY_ENV_READS_HOOK, read_input(path)) == "deny"

    # ------------------------------------------------------------------ #
    # Deny — basename traversal: basename of raw path still denied        #
    # ------------------------------------------------------------------ #

    def test_dotdot_traversal_denied(self):
        # basename("/foo/sub/../.env.production") == ".env.production" — denied
        assert run_hook(DENY_ENV_READS_HOOK, read_input("/foo/sub/../.env.production")) == "deny"

    # ------------------------------------------------------------------ #
    # Deny message content — allowlist named, path named                  #
    # ------------------------------------------------------------------ #

    def test_deny_message_names_allowlist(self):
        reason = run_hook_reason(DENY_ENV_READS_HOOK, read_input("/foo/.env.local"))
        assert reason is not None
        assert ".env.example" in reason

    def test_deny_message_names_path(self):
        reason = run_hook_reason(DENY_ENV_READS_HOOK, read_input("/project/.env.production"))
        assert reason is not None
        assert ".env.production" in reason

    # ------------------------------------------------------------------ #
    # Symlink cases (require real filesystem symlinks)                    #
    # ------------------------------------------------------------------ #

    def test_symlink_to_denied_target_denied(self, tmp_path):
        target = tmp_path / ".env.production"
        target.write_text("SECRET=value\n")
        link = tmp_path / ".env.example"
        link.symlink_to(target)
        assert run_hook(DENY_ENV_READS_HOOK, read_input(str(link))) == "deny"

    def test_symlink_deny_message_names_resolved_path(self, tmp_path):
        target = tmp_path / ".env.production"
        target.write_text("SECRET=value\n")
        link = tmp_path / ".env.example"
        link.symlink_to(target)
        reason = run_hook_reason(DENY_ENV_READS_HOOK, read_input(str(link)))
        assert reason is not None
        assert ".env.production" in reason

    def test_symlink_to_template_target_allowed(self, tmp_path):
        target = tmp_path / "shared.env.example"
        target.write_text("PORT=3000\n")
        link = tmp_path / ".env.example"
        link.symlink_to(target)
        assert run_hook(DENY_ENV_READS_HOOK, read_input(str(link))) == "allow"

    def test_symlink_to_non_env_file_allowed(self, tmp_path):
        target = tmp_path / "notes.txt"
        target.write_text("just notes\n")
        link = tmp_path / ".env.example"
        link.symlink_to(target)
        assert run_hook(DENY_ENV_READS_HOOK, read_input(str(link))) == "allow"

    def test_broken_symlink_denied(self, tmp_path):
        link = tmp_path / ".env.example"
        link.symlink_to(tmp_path / "nonexistent")
        assert run_hook(DENY_ENV_READS_HOOK, read_input(str(link))) == "deny"

    # ------------------------------------------------------------------ #
    # Defense-in-depth: non-Read tool names pass through                  #
    # ------------------------------------------------------------------ #

    @pytest.mark.parametrize(
        "payload",
        [
            bash_input("ls"),
            edit_input("/foo/.env.production"),
            write_input("/foo/.env.production"),
        ],
    )
    def test_non_read_tools_pass_through(self, payload):
        assert run_hook(DENY_ENV_READS_HOOK, payload) == "allow"

    def test_unknown_read_variant_tool_name_passes_through(self):
        payload = {"tool_name": "ReadFile", "tool_input": {"file_path": "/foo/.env.production"}}
        assert run_hook(DENY_ENV_READS_HOOK, payload) == "allow"

    # ------------------------------------------------------------------ #
    # Fail-closed on malformed JSON                                       #
    # ------------------------------------------------------------------ #

    def test_malformed_json_denied(self):
        result = subprocess.run(
            [str(DENY_ENV_READS_HOOK)],
            input="not valid json",
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.stdout.strip()
        payload = json.loads(result.stdout)
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"
