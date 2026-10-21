"""Tests for deny-env-reads.sh."""
from __future__ import annotations

import json
import re
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

    def test_deny_message_routes_template_rename_to_the_user(self):
        reason = run_hook_reason(DENY_ENV_READS_HOOK, read_input("/foo/.env.local"))
        assert reason is not None
        assert "ask the user to rename it to .env.example" in reason

    def test_deny_message_names_path(self):
        reason = run_hook_reason(DENY_ENV_READS_HOOK, read_input("/project/.env.production"))
        assert reason is not None
        assert ".env.production" in reason

    def test_deny_message_routes_inspection_to_separate_terminal_not_shell_escape(self):
        reason = run_hook_reason(DENY_ENV_READS_HOOK, read_input("/project/.env.production"))
        assert reason is not None
        # The shell escape's output enters the transcript, so the message must not route recourse there.
        assert "ask the user to inspect it in a separate terminal" in reason
        assert "`!" not in reason
        assert " ! " not in reason
        assert "shell escape" not in reason

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


# Near misses the hook's patterns do not name: `.env.vault` and `.env.` match `.env.*`; `.envrc` and `.ENV` do not.
_DOTENV_NEAR_MISS_BASENAMES = [".env.local", ".env.production", ".env.ci", ".env.vault", ".envrc", ".ENV", ".env."]
_PATTERN_SAMPLE_SUFFIX = "sample-suffix"
_BASENAME_CASE_ARM_HEADER = re.compile(r"(?m)^  (\S+)\)$")
_BASENAME_ARM_COUNT_FLOOR = 3
_BASENAME_ALTERNATIVE_COUNT_FLOOR = 6


def _hook_basename_corpus() -> list[str]:
    """One basename per alternative of both arms (allow and deny) of the hook's first `case "$BASENAME" in`.

    A literal alternative is used as is, and an `X*` alternative as `X` plus a sample suffix, so a pattern
    added to either arm is covered without editing this file.
    """
    source = DENY_ENV_READS_HOOK.read_text()
    case_body = source.partition('case "$BASENAME" in\n')[2].partition("\nesac")[0]
    arm_headers = _BASENAME_CASE_ARM_HEADER.findall(case_body)
    alternatives = [alternative for header in arm_headers for alternative in header.split("|")]
    assert len(arm_headers) >= _BASENAME_ARM_COUNT_FLOOR, arm_headers
    assert len(alternatives) >= _BASENAME_ALTERNATIVE_COUNT_FLOOR, alternatives
    return [
        alternative.removesuffix("*") + _PATTERN_SAMPLE_SUFFIX if alternative.endswith("*") else alternative
        for alternative in alternatives
    ]


# Every basename the hook and _lib_dotenv_basename_is_secret must agree on.
_DOTENV_PARITY_BASENAMES = list(dict.fromkeys([*_hook_basename_corpus(), *_DOTENV_NEAR_MISS_BASENAMES]))


@pytest.mark.parametrize("basename", _DOTENV_PARITY_BASENAMES)
def test_dotenv_helper_matches_hook_verdict(basename, tmp_path):
    """pr-diff-against-base.sh withholds a changed path by _lib_dotenv_basename_is_secret, which copies this
    hook's basename rule. A pattern the hook later adds fails this test until the helper follows.

    Each basename gets its own tmp_path, so `.env` and `.ENV` never share a directory on a case-insensitive volume.
    """
    hook_denies = run_hook(DENY_ENV_READS_HOOK, read_input(str(tmp_path / basename))) == "deny"

    helper = subprocess.run(
        ["bash", "-c", '. "$1"; _lib_dotenv_basename_is_secret "$2"', "bash", str(HOOKS_DIR / "_lib.sh"), basename],
        capture_output=True,
        text=True,
        check=False,
    )

    assert helper.returncode in (0, 1), repr(helper)
    assert (helper.returncode == 0) == hook_denies
