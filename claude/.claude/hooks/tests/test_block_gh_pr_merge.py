"""Tests for block-gh-pr-merge.sh."""
from __future__ import annotations

import json
import subprocess

import pytest
from helpers import (
    HOOKS_DIR,
    bash_input,
    build_path_without,
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

    # ------------------------------------------------------------------ #
    # GH-783 Phase 2: quote-split and command-matcher regression tests    #
    # ------------------------------------------------------------------ #

    def test_quoted_form_reaches_same_verdict_as_bare_form(self):
        """A quote-adjacent split (`"gh" pr merge 1`) must reach the same
        deny verdict as the unquoted form — the fragment matcher strips
        quote characters before word-walking, unlike a raw regex over
        unstripped $COMMAND."""
        assert run_hook(BLOCK_GH_PR_MERGE_HOOK, bash_input('"gh" pr merge 1')) == "deny"

    def test_quoted_subcommand_word_now_denied(self):
        """Closes a real gap the prior regex missed: `gh pr "merge"` quote-
        strips to a bare `merge` token, which the word-sequence matcher now
        catches — a genuine behavior change/gap-close, not a regression.
        See docs/hooks.md's entry for this hook."""
        assert run_hook(BLOCK_GH_PR_MERGE_HOOK, bash_input('gh pr "merge"')) == "deny"

    def test_echo_wrapped_form_still_allowed_via_command_word_resolution(self):
        """Re-pins the false-positive-avoidance property test_false_positive_
        shapes_allowed above already covers, naming the NEW mechanism: the
        stripped fragment's command word resolves to `echo`, not `gh`, not
        because a quote character survived stripping."""
        assert run_hook(BLOCK_GH_PR_MERGE_HOOK, bash_input('echo "gh pr merge"')) == "allow"

    def test_value_taking_global_flag_before_subcommand_denied(self):
        """Row 4's exact naive-implementation failure case: without
        skipping -R/--repo's own value, `o/r` would misread as the
        subcommand and the match would miss."""
        assert run_hook(BLOCK_GH_PR_MERGE_HOOK, bash_input("gh --repo o/r pr merge")) == "deny"

    def test_full_path_invocation_now_denied(self):
        """Closed as a side effect of the command-word matcher (see
        _lib_fragment_invokes_tool): a path ending in /gh resolves the same
        as the bare token, not only a literal `gh` word."""
        assert run_hook(BLOCK_GH_PR_MERGE_HOOK, bash_input("/usr/bin/gh pr merge 291")) == "deny"

    def test_sed_absent_from_path_denies(self, tmp_path):
        """Status-2 propagation: the matcher could not determine whether
        this command invokes gh pr merge, and this hook's documented
        fail-closed posture means an undetermined match denies rather than
        silently falling through to allow. Asserts the distinguishing
        reason text, not just the verdict, so this test cannot be
        satisfied by an ordinary merge-match deny reaching the same
        "deny" verdict for the wrong reason."""
        farm_dir = tmp_path / "path-without-sed"
        farm_dir.mkdir()
        restricted_path = build_path_without("sed", farm_dir)
        reason = run_hook_reason(
            BLOCK_GH_PR_MERGE_HOOK,
            bash_input("gh pr merge 291"),
            extra_env={"PATH": restricted_path},
        )
        assert reason is not None
        assert "could not determine" in reason
