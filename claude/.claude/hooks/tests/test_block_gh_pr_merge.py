"""Tests for block-gh-pr-merge.sh."""
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

BLOCK_GH_PR_MERGE_HOOK = HOOKS_DIR / "block-gh-pr-merge.sh"


class TestBlockGhPrMerge:
    # ------------------------------------------------------------------ #
    # Deny — brief-listed shapes                                          #
    # ------------------------------------------------------------------ #

    @pytest.mark.parametrize(
        "command",
        [
            "gh pr merge 291 --squash",
            "gh pr merge 291 --squash --delete-branch",
            "gh pr merge --auto 291",
            "gh pr merge",
            "gh pr merge --rebase 291",
        ],
    )
    def test_brief_shapes_denied(self, command):
        assert run_hook(BLOCK_GH_PR_MERGE_HOOK, bash_input(command)) == "deny"

    # ------------------------------------------------------------------ #
    # Deny — additional valid shapes                                      #
    # ------------------------------------------------------------------ #

    @pytest.mark.parametrize(
        "command",
        [
            "gh pr merge 291",
            "(cd /repo && gh pr merge 291)",
            "gh pr merge 291; echo done",
            "git push && gh pr merge 291",       # &&-chained form (canonical agent push-then-merge)
            "gh pr merge https://github.com/owner/repo/pull/291",  # URL-argument form
            "gh\tpr\tmerge 291",                 # tab-separated tokens (\s+ matches tabs)
            "gh  pr  merge 291",                 # multiple spaces (\s+ matches runs)
            "git status\ngh pr merge 291",       # newline-separated multi-step command
        ],
    )
    def test_additional_shapes_denied(self, command):
        assert run_hook(BLOCK_GH_PR_MERGE_HOOK, bash_input(command)) == "deny"

    # ------------------------------------------------------------------ #
    # Allow — sibling gh pr subcommands                                   #
    # ------------------------------------------------------------------ #

    @pytest.mark.parametrize(
        "command",
        [
            "gh pr view 291",
            "gh pr list",
            "gh pr checks 291",
            "gh pr edit 291 --body x",
            "gh pr comment 291 --body x",
            "gh pr create --title x --body y",
        ],
    )
    def test_sibling_subcommands_allowed(self, command):
        assert run_hook(BLOCK_GH_PR_MERGE_HOOK, bash_input(command)) == "allow"

    # ------------------------------------------------------------------ #
    # Allow — false-positive shapes (word-boundary defense)              #
    # ------------------------------------------------------------------ #

    @pytest.mark.parametrize(
        "command",
        [
            'echo "gh pr merge"',     # quoted, not invoked
            "gh pr mergefoo",          # word-boundary defense
            "git merge main",          # different command entirely
        ],
    )
    def test_false_positive_shapes_allowed(self, command):
        assert run_hook(BLOCK_GH_PR_MERGE_HOOK, bash_input(command)) == "allow"

    # ------------------------------------------------------------------ #
    # Allow — nearest-neighbor gh pr subcommands                         #
    # (catch over-broad regex regression faster than the sibling list)    #
    # ------------------------------------------------------------------ #

    @pytest.mark.parametrize(
        "command",
        [
            "gh pr close 291",
            "gh pr review 291 --approve",
            "gh pr diff 291",
        ],
    )
    def test_nearest_neighbor_subcommands_allowed(self, command):
        assert run_hook(BLOCK_GH_PR_MERGE_HOOK, bash_input(command)) == "allow"

    # ------------------------------------------------------------------ #
    # Allow — documented bypass shapes (known gaps, not enforced)        #
    # ------------------------------------------------------------------ #

    @pytest.mark.parametrize(
        "wrapped_command,inner_command",
        [
            ('bash -c "gh pr merge 291"', "gh pr merge 291"),
            ('eval "gh pr merge 291"', "gh pr merge 291"),
        ],
    )
    def test_documented_bypass_structural_pair(self, wrapped_command, inner_command):
        # The wrapped form is a known gap: the hook inspects tool_input.command only
        # and cannot see subshell contents. The inner command IS denied, confirming
        # the bypass is structural (subshell indirection), not a regex gap.
        assert run_hook(BLOCK_GH_PR_MERGE_HOOK, bash_input(wrapped_command)) == "allow"
        assert run_hook(BLOCK_GH_PR_MERGE_HOOK, bash_input(inner_command)) == "deny"

    # ------------------------------------------------------------------ #
    # Allow — non-Bash tools pass through                                 #
    # ------------------------------------------------------------------ #

    @pytest.mark.parametrize(
        "payload",
        [
            read_input("/some/file"),
            edit_input("/some/file"),
            write_input("/some/file"),
        ],
    )
    def test_non_bash_tools_pass_through(self, payload):
        assert run_hook(BLOCK_GH_PR_MERGE_HOOK, payload) == "allow"

    # ------------------------------------------------------------------ #
    # Deny message content                                                #
    # ------------------------------------------------------------------ #

    def test_deny_message_names_blocked_command(self):
        reason = run_hook_reason(BLOCK_GH_PR_MERGE_HOOK, bash_input("gh pr merge 291 --squash"))
        assert reason is not None
        assert "gh pr merge 291 --squash" in reason

    def test_deny_message_cites_claude_md_rule(self):
        reason = run_hook_reason(BLOCK_GH_PR_MERGE_HOOK, bash_input("gh pr merge 291 --squash"))
        assert reason is not None
        assert "AI agents: don't merge your own PRs" in reason

    # ------------------------------------------------------------------ #
    # Allow — structural edge cases (null-safety of jq extraction)       #
    # ------------------------------------------------------------------ #

    def test_empty_json_object_denied(self):
        # {} has no tool_name → _lib_parse_tool_input_or_deny denies on empty TOOL_NAME
        payload = {}
        assert run_hook(BLOCK_GH_PR_MERGE_HOOK, payload) == "deny"

    def test_bash_tool_missing_command_allowed(self):
        # tool_name is Bash but tool_input.command absent → COMMAND empty → exit 0 (allow)
        payload = {"tool_name": "Bash", "tool_input": {}}
        assert run_hook(BLOCK_GH_PR_MERGE_HOOK, payload) == "allow"

    # ------------------------------------------------------------------ #
    # Fail-closed on malformed JSON                                       #
    # ------------------------------------------------------------------ #

    def test_malformed_json_denied(self):
        # run_hook calls json.dumps() on its dict input, which would re-serialize the
        # malformed string as a valid JSON string. Must use raw subprocess to supply
        # genuine non-JSON bytes to stdin.
        result = subprocess.run(
            [str(BLOCK_GH_PR_MERGE_HOOK)],
            input="not valid json",
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.stdout.strip(), "Hook must emit a deny message on malformed JSON, not silent exit"
        payload = json.loads(result.stdout)
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "could not parse" in payload["hookSpecificOutput"]["permissionDecisionReason"]

    # ------------------------------------------------------------------ #
    # Allow — documented out-of-scope bypass shapes                      #
    # ------------------------------------------------------------------ #

    def test_gh_api_merge_path_is_allow(self):
        # Known gap documented in hook header: `gh api .../merge` is a different
        # command shape and is excluded by the implementation brief. This test
        # asserts the boundary so a future pattern expansion doesn't silently capture it.
        assert run_hook(BLOCK_GH_PR_MERGE_HOOK, bash_input("gh api repos/owner/repo/pulls/291/merge")) == "allow"
