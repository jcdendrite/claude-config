"""Tests for enforce-marker-script-shape.sh."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import time

import pytest
from helpers import (
    CLAUDE_DIR,
    HOOKS_DIR,
    bash_input,
    edit_input,
    multiedit_input,
    run_hook,
    run_hook_reason,
    write_input,
)

ENFORCE_MARKER_SCRIPT_SHAPE_HOOK = HOOKS_DIR / "enforce-marker-script-shape.sh"

# The 15 single-command tilde-form shapes the hook accepts — single source of
# truth for both test_valid_shapes_allowed (which pins hook acceptance) and
# TestPrescriptionAllowlistAlignment (which cross-checks permissions.allow
# coverage over this same set), so the two can't silently drift apart.
TILDE_MARKER_SHAPES = [
    "~/.claude/scripts/marker.sh write code-review",
    "~/.claude/scripts/marker.sh write skill-review",
    "~/.claude/scripts/marker.sh write plan-review",
    "~/.claude/scripts/marker.sh write ready-for-review",
    "~/.claude/scripts/marker.sh activate plan-review",
    "~/.claude/scripts/marker.sh activate ready-for-review",
    "~/.claude/scripts/marker.sh activate respond-pr",
    "~/.claude/scripts/marker.sh activate memory-skill",
    "~/.claude/scripts/marker.sh deactivate plan-review",
    "~/.claude/scripts/marker.sh deactivate ready-for-review",
    "~/.claude/scripts/marker.sh deactivate respond-pr",
    "~/.claude/scripts/marker.sh deactivate memory-skill",
    "~/.claude/scripts/marker.sh clear-stale",
    "~/.claude/scripts/marker.sh clear-stale --dry-run",
    "~/.claude/scripts/marker.sh resolve-session-id",
]


class TestEnforceMarkerScriptShape:
    # ------------------------------------------------------------------ #
    # Valid shapes — 15 single-command shapes, each must be allowed       #
    # ------------------------------------------------------------------ #

    @pytest.mark.parametrize("command", TILDE_MARKER_SHAPES)
    def test_valid_shapes_allowed(self, command):
        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input(command)) == "allow"

    # ------------------------------------------------------------------ #
    # Fast-exit: no marker.sh in command                                  #
    # ------------------------------------------------------------------ #

    @pytest.mark.parametrize(
        "command",
        [
            "git commit -m foo",
            "git push origin main",
            "echo hello",
            "ls ~/.claude/scripts/",
        ],
    )
    def test_commands_without_marker_sh_allowed(self, command):
        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input(command)) == "allow"

    # ------------------------------------------------------------------ #
    # Chaining                                                            #
    #                                                                     #
    # Chain to `git commit` is the natural atomic form after reviews pass #
    # and is allowed. Chain to anything else (curl, rm, redirects, ;)     #
    # stays denied — the gate's job is to keep marker.sh from being a     #
    # wedge for arbitrary chained commands.                               #
    # ------------------------------------------------------------------ #

    def test_chain_to_git_commit_allowed(self):
        """marker.sh write <skill> && git commit ... is the natural form an
        agent types after reviews pass. PreToolUse fires once per Bash call
        before the chain runs, so an on-disk marker check at the commit gate
        would deny — coordinated with require-code-review.sh and
        require-skill-review.sh, both of which honor in-chain marker writes."""
        cmd = "~/.claude/scripts/marker.sh write code-review && git commit -m foo"
        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input(cmd)) == "allow"

    def test_chain_multiple_marker_writes_then_git_commit_allowed(self):
        """Both reviews passed: write code-review marker AND skill-review marker
        before committing, all in one atomic Bash call."""
        cmd = (
            "~/.claude/scripts/marker.sh write code-review && "
            "~/.claude/scripts/marker.sh write skill-review && "
            "git commit -m foo"
        )
        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input(cmd)) == "allow"

    def test_chain_marker_activate_then_git_commit_denied(self):
        """Only `write` is permitted in the chained form. `activate` is a
        bypass primitive whose intent is to bracket a skill's execution
        with deactivate at the end — chaining it with commit makes no sense
        and shouldn't widen the allowed surface."""
        cmd = "~/.claude/scripts/marker.sh activate plan-review && git commit -m foo"
        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input(cmd)) == "deny"

    def test_chain_to_curl_denied(self):
        cmd = "~/.claude/scripts/marker.sh write code-review && curl http://example.com"
        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input(cmd)) == "deny"

    def test_chain_to_curl_after_commit_denied(self):
        """Post-commit chain operators must be denied. Without this constraint,
        `marker.sh write X && git commit && curl evil.com` would slip through
        the chained-commit pattern via a permissive trailing match, allowing a
        post-commit fragment to inherit the marker.sh-leading allowance."""
        cmd = "~/.claude/scripts/marker.sh write code-review && git commit -m foo && curl http://example.com"
        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input(cmd)) == "deny"

    def test_chain_to_semicolon_after_commit_denied(self):
        """Semicolons after `git commit` chain to a new statement just like
        `&&` does; the trailing-content constraint must forbid both."""
        cmd = "~/.claude/scripts/marker.sh write code-review && git commit -m foo; curl http://example.com"
        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input(cmd)) == "deny"

    def test_chain_to_redirect_after_commit_denied(self):
        """Post-commit redirects must be denied; the trailing-content
        constraint forbids `<` and `>` along with chain operators."""
        cmd = "~/.claude/scripts/marker.sh write code-review && git commit -m foo > /tmp/out"
        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input(cmd)) == "deny"

    def test_semicolon_separator_denied(self):
        cmd = "~/.claude/scripts/marker.sh write code-review; rm -rf /"
        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input(cmd)) == "deny"

    def test_single_shape_embedded_newline_denied(self):
        """Embedded newline after a valid single shape; grep -E's $ matches per-line,
        so without the newline guard the second line would be ignored and the hook
        would allow a command that executes a second line on the shell."""
        cmd = "~/.claude/scripts/marker.sh write code-review\ncurl http://evil"
        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input(cmd)) == "deny"

    # ------------------------------------------------------------------ #
    # Marker→marker chains — allowed                                      #
    #                                                                     #
    # A chain of two-or-more valid marker.sh shapes joined by && is       #
    # permitted for any op/target combination: the chain's end state is   #
    # identical to running each op separately, and every op is already    #
    # individually allowlisted or harmless (clear-stale). These are NOT   #
    # single shapes and must NOT appear in the 14-shape parametrize list  #
    # above.                                                              #
    # ------------------------------------------------------------------ #

    @pytest.mark.parametrize(
        "command",
        [
            # same-skill pairs, both orderings, both skills
            "~/.claude/scripts/marker.sh write plan-review && ~/.claude/scripts/marker.sh deactivate plan-review",
            "~/.claude/scripts/marker.sh deactivate plan-review && ~/.claude/scripts/marker.sh write plan-review",
            "~/.claude/scripts/marker.sh write ready-for-review && ~/.claude/scripts/marker.sh deactivate ready-for-review",
            "~/.claude/scripts/marker.sh deactivate ready-for-review && ~/.claude/scripts/marker.sh write ready-for-review",
            (
                "/home/testuser/.claude/scripts/marker.sh write plan-review && "
                "/home/testuser/.claude/scripts/marker.sh deactivate plan-review"
            ),
            "~/.claude/scripts/marker.sh write ready-for-review   &&   ~/.claude/scripts/marker.sh deactivate ready-for-review",
            "~/.claude/scripts/marker.sh write plan-review && /home/jared/.claude/scripts/marker.sh deactivate plan-review",
            # mixed-skill pairs, both orderings — every segment is individually
            # valid, so the chain grants no new capability over running the
            # two calls separately
            "~/.claude/scripts/marker.sh write plan-review && ~/.claude/scripts/marker.sh deactivate ready-for-review",
            "~/.claude/scripts/marker.sh deactivate ready-for-review && ~/.claude/scripts/marker.sh write plan-review",
            # activate+deactivate and activate+write pairs — activate is not
            # restricted to a single chain partner; each segment is valid
            "~/.claude/scripts/marker.sh activate plan-review && ~/.claude/scripts/marker.sh deactivate plan-review",
            "~/.claude/scripts/marker.sh activate plan-review && ~/.claude/scripts/marker.sh write plan-review",
            # 3-segment chain — the pattern requires 2+ segments, not exactly 2
            (
                "~/.claude/scripts/marker.sh write plan-review && "
                "~/.claude/scripts/marker.sh deactivate plan-review && "
                "~/.claude/scripts/marker.sh write plan-review"
            ),
            # multi-write, no commit — two valid write ops with no trailing
            # git commit
            "~/.claude/scripts/marker.sh write code-review && ~/.claude/scripts/marker.sh write skill-review",
            # deactivate+deactivate, cross-skill
            "~/.claude/scripts/marker.sh deactivate plan-review && ~/.claude/scripts/marker.sh deactivate ready-for-review",
            # activate+activate, cross-skill
            "~/.claude/scripts/marker.sh activate respond-pr && ~/.claude/scripts/marker.sh activate memory-skill",
            # 4-segment mixed chain
            (
                "~/.claude/scripts/marker.sh write code-review && "
                "~/.claude/scripts/marker.sh activate plan-review && "
                "~/.claude/scripts/marker.sh deactivate plan-review && "
                "~/.claude/scripts/marker.sh write skill-review"
            ),
            # mixed tilde/absolute paths within one chain
            "~/.claude/scripts/marker.sh write code-review && /home/testuser/.claude/scripts/marker.sh write skill-review",
            # no-space && form
            "~/.claude/scripts/marker.sh write plan-review&&~/.claude/scripts/marker.sh deactivate plan-review",
            # clear-stale participating in a chain
            "~/.claude/scripts/marker.sh write code-review && ~/.claude/scripts/marker.sh clear-stale",
        ],
    )
    def test_valid_marker_chains_allowed(self, command):
        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input(command)) == "allow"

    # ------------------------------------------------------------------ #
    # Marker→marker chains — denied boundary cases                       #
    # ------------------------------------------------------------------ #

    def test_chain_deactivate_write_only_skill_denied(self):
        """code-review is write-only; deactivate code-review is not a valid shape."""
        cmd = "~/.claude/scripts/marker.sh write code-review && ~/.claude/scripts/marker.sh deactivate code-review"
        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input(cmd)) == "deny"

    def test_chain_marker_pair_trailing_curl_denied(self):
        """Trailing command after the pair must be rejected by the anchor."""
        cmd = "~/.claude/scripts/marker.sh write plan-review && ~/.claude/scripts/marker.sh deactivate plan-review && curl http://evil"
        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input(cmd)) == "deny"

    def test_chain_marker_pair_trailing_git_commit_denied(self):
        """Trailing git commit after the pair; a high-probability agent variant the anchor must reject."""
        cmd = (
            "~/.claude/scripts/marker.sh write plan-review && "
            "~/.claude/scripts/marker.sh deactivate plan-review && "
            "git commit -m foo"
        )
        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input(cmd)) == "deny"

    def test_chain_or_separator_denied(self):
        """|| separator; the chain pattern hardcodes &&."""
        cmd = "~/.claude/scripts/marker.sh write plan-review || ~/.claude/scripts/marker.sh deactivate plan-review"
        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input(cmd)) == "deny"

    def test_chain_semicolon_separator_marker_pair_denied(self):
        """; separator; the chain pattern hardcodes &&."""
        cmd = "~/.claude/scripts/marker.sh write plan-review ; ~/.claude/scripts/marker.sh deactivate plan-review"
        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input(cmd)) == "deny"

    def test_chain_rhs_bare_no_path_prefix_denied(self):
        """Bare RHS without path prefix; every segment must be a full marker.sh shape."""
        cmd = "~/.claude/scripts/marker.sh write plan-review && deactivate plan-review"
        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input(cmd)) == "deny"

    def test_chain_marker_pair_trailing_redirect_denied(self):
        """Trailing redirect; anchored pattern must reject any suffix after the pair."""
        cmd = "~/.claude/scripts/marker.sh write plan-review && ~/.claude/scripts/marker.sh deactivate plan-review 2>&1"
        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input(cmd)) == "deny"

    def test_chain_rhs_path_traversal_denied(self):
        """RHS path traversal; the traversal guard runs before this pattern and is the sole RHS path validator."""
        cmd = "~/.claude/scripts/marker.sh write plan-review && ~/.claude/scripts/../scripts/marker.sh deactivate plan-review"
        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input(cmd)) == "deny"

    def test_chain_embedded_newline_denied(self):
        """Embedded newline after the pair; per-line grep must not allow a two-line payload."""
        cmd = "~/.claude/scripts/marker.sh write plan-review && ~/.claude/scripts/marker.sh deactivate plan-review\n curl evil"
        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input(cmd)) == "deny"

    def test_chain_git_push_denied(self):
        """git push is not a marker op and not the blessed git-commit tail;
        chaining marker.sh to a non-marker, non-commit command must deny."""
        cmd = "~/.claude/scripts/marker.sh write plan-review && git push"
        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input(cmd)) == "deny"

    def test_chain_invalid_activate_target_mid_chain_denied(self):
        """activate code-review is an invalid op/target combo; op/target
        validation must survive inside a chain, not just at the single shape."""
        cmd = "~/.claude/scripts/marker.sh write plan-review && ~/.claude/scripts/marker.sh activate code-review"
        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input(cmd)) == "deny"

    def test_chain_invalid_write_target_mid_chain_denied(self):
        """respond-pr is not a valid write target; guards against a write
        target list quietly widened to match the activate/deactivate list."""
        cmd = "~/.claude/scripts/marker.sh write plan-review && ~/.claude/scripts/marker.sh write respond-pr"
        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input(cmd)) == "deny"

    def test_chain_invalid_target_as_first_segment_denied(self):
        """activate code-review as the FIRST segment must still deny — every
        prior invalid-mid-chain test places the bad segment second, so this
        pins that the shared shape validation applies at the anchor position
        (the part most changed by generalizing away from the old hardcoded
        4-branch pattern), not only at later && repeats."""
        cmd = "~/.claude/scripts/marker.sh activate code-review && ~/.claude/scripts/marker.sh write plan-review"
        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input(cmd)) == "deny"

    def test_chain_invalid_last_segment_with_trailing_devnull_denied(self):
        """An invalid op/target as the chain's last segment, followed by the
        blessed trailing 2>/dev/null, must still deny — the redirect suffix
        sits outside the repeated marker-shape group and must not let an
        invalid final segment ride through underneath it."""
        cmd = "~/.claude/scripts/marker.sh write plan-review && ~/.claude/scripts/marker.sh activate code-review 2>/dev/null"
        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input(cmd)) == "deny"

    def test_chain_non_marker_middle_segment_denied(self):
        """A non-marker command between two valid marker shapes must deny;
        the chain pattern requires every segment to be a marker shape."""
        cmd = "~/.claude/scripts/marker.sh write plan-review && ls && ~/.claude/scripts/marker.sh deactivate plan-review"
        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input(cmd)) == "deny"

    def test_chain_rhs_bare_arbitrary_command_denied(self):
        """Bare non-marker RHS with no path prefix at all — the symmetric
        partner to test_chain_rhs_bare_no_path_prefix_denied, which uses a
        marker-op-shaped-but-prefix-less RHS."""
        cmd = "~/.claude/scripts/marker.sh write plan-review && rm -rf /"
        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input(cmd)) == "deny"

    def test_chain_extra_arg_mid_chain_denied(self):
        """An extra arg on a mid-chain segment must still deny; the shape
        pattern's anchors apply per-segment, not just at the start/end of
        the whole chain."""
        cmd = "~/.claude/scripts/marker.sh write plan-review extra && ~/.claude/scripts/marker.sh deactivate plan-review"
        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input(cmd)) == "deny"

    def test_chain_three_segments_broken_by_trailing_semicolon_denied(self):
        """A trailing ; after a 3-segment chain must still deny — separators
        terminate a multi-segment chain the same way they terminate a
        2-segment one."""
        cmd = "~/.claude/scripts/marker.sh write code-review && ~/.claude/scripts/marker.sh write skill-review; curl http://evil"
        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input(cmd)) == "deny"

    # ------------------------------------------------------------------ #
    # Redirect                                                            #
    # ------------------------------------------------------------------ #

    def test_redirect_denied(self):
        cmd = "~/.claude/scripts/marker.sh write code-review > /tmp/out"
        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input(cmd)) == "deny"

    # ------------------------------------------------------------------ #
    # Extra args                                                          #
    # ------------------------------------------------------------------ #

    def test_extra_arg_denied(self):
        cmd = "~/.claude/scripts/marker.sh write code-review extra"
        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input(cmd)) == "deny"

    # ------------------------------------------------------------------ #
    # Unknown subcommand / skill / mismatch                               #
    # ------------------------------------------------------------------ #

    def test_unknown_subcommand_denied(self):
        cmd = "~/.claude/scripts/marker.sh forge code-review"
        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input(cmd)) == "deny"

    def test_unknown_skill_denied(self):
        cmd = "~/.claude/scripts/marker.sh write nonsense"
        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input(cmd)) == "deny"

    def test_mismatched_subcommand_skill_pair_denied(self):
        """code-review does not support activate — must be denied."""
        cmd = "~/.claude/scripts/marker.sh activate code-review"
        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input(cmd)) == "deny"

    def test_memory_skill_extra_arg_denied(self):
        """activate memory-skill with a trailing arg must be denied."""
        cmd = "~/.claude/scripts/marker.sh activate memory-skill extra"
        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input(cmd)) == "deny"

    def test_memory_skill_underscore_denied(self):
        """Underscore form (memory_skill) is not in the allowlist."""
        cmd = "~/.claude/scripts/marker.sh activate memory_skill"
        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input(cmd)) == "deny"

    # ------------------------------------------------------------------ #
    # Bare script (no args)                                               #
    # ------------------------------------------------------------------ #

    def test_bare_script_denied(self):
        cmd = "~/.claude/scripts/marker.sh"
        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input(cmd)) == "deny"

    # ------------------------------------------------------------------ #
    # Absolute path form                                                  #
    # ------------------------------------------------------------------ #

    def test_absolute_path_form_allowed(self):
        cmd = "/home/jared/.claude/scripts/marker.sh write code-review"
        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input(cmd)) == "allow"

    def test_path_traversal_denied(self):
        cmd = "/home/evil/../../home/jared/.claude/scripts/marker.sh write code-review"
        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input(cmd)) == "deny"

    # ------------------------------------------------------------------ #
    # False-positive regressions — commands that must NOT be blocked      #
    # ------------------------------------------------------------------ #

    def test_heredoc_commit_mentioning_marker_sh_in_body_allowed(self):
        """A git commit whose heredoc body mentions marker.sh must not be inspected.

        The activation guard (Stage 2) uses bash =~ which anchors at
        start-of-subject, so a heredoc body line starting with the script
        path does NOT activate the deeper validator — only a command that
        itself starts with the path does.
        """
        cmd = "git commit -m \"$(cat <<'EOF'\nfix: prevent marker.sh hook from blocking heredoc commits\nEOF\n)\""
        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input(cmd)) == "allow"

    def test_heredoc_inner_line_starting_with_marker_path_allowed(self):
        """A heredoc body whose inner line starts with ~/.claude/scripts/marker.sh must allow.

        Verifies that bash =~ anchors at start-of-subject (the entire
        multi-line string), not at start-of-line. If grep -E were used
        instead, the inner line would match '^...' and over-activate.
        """
        cmd = "git commit -m \"$(cat <<'EOF'\n~/.claude/scripts/marker.sh activate code-review\nEOF\n)\""
        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input(cmd)) == "allow"

    def test_git_log_range_two_dots_allowed(self):
        """git log a..b must not trip the path-traversal check.

        The traversal check matches '..' only as a path segment
        (preceded or followed by '/'), not as a range operator.
        """
        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input("git log a..b")) == "allow"

    def test_git_diff_triple_dot_allowed(self):
        """git diff main...HEAD must not trip the path-traversal check."""
        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input("git diff main...HEAD")) == "allow"

    def test_gh_pr_create_with_marker_sh_in_body_allowed(self):
        """gh pr create --body mentioning marker.sh inline must allow."""
        cmd = 'gh pr create --body "mentions marker.sh inline"'
        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input(cmd)) == "allow"

    # ------------------------------------------------------------------ #
    # Bypass shapes — intentionally allowed; pin the behavior             #
    # ------------------------------------------------------------------ #

    def test_wrapped_bash_c_intentionally_not_gated_relies_on_permissions_allow(self):
        """The shape hook intentionally does not gate this form; permissions.allow denies the
        wrapping executable. Do not change this test without first confirming the
        permission-layer gate still applies."""
        cmd = 'bash -c "~/.claude/scripts/marker.sh activate code-review"'
        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input(cmd)) == "allow"

    def test_env_var_prefix_bypass_intentionally_not_gated_relies_on_permissions_allow(self):
        """The shape hook intentionally does not gate this form; permissions.allow denies the
        wrapping executable. Do not change this test without first confirming the
        permission-layer gate still applies."""
        cmd = "FOO=bar ~/.claude/scripts/marker.sh activate code-review"
        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input(cmd)) == "allow"

    def test_semicolon_prefix_bypass_intentionally_not_gated_relies_on_permissions_allow(self):
        """The shape hook intentionally does not gate this form; permissions.allow denies the
        wrapping executable. Do not change this test without first confirming the
        permission-layer gate still applies."""
        cmd = "true; ~/.claude/scripts/marker.sh activate code-review"
        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input(cmd)) == "allow"

    def test_subshell_bypass_intentionally_not_gated_relies_on_permissions_allow(self):
        """The shape hook intentionally does not gate this form; permissions.allow denies the
        wrapping executable. Do not change this test without first confirming the
        permission-layer gate still applies."""
        cmd = "$(~/.claude/scripts/marker.sh write code-review somehash)"
        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input(cmd)) == "allow"

    def test_relative_path_bypass_intentionally_not_gated_relies_on_permissions_allow(self):
        """The shape hook intentionally does not gate this form; permissions.allow denies the
        wrapping executable. Do not change this test without first confirming the
        permission-layer gate still applies."""
        cmd = "./marker.sh activate code-review"
        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input(cmd)) == "allow"

    # ------------------------------------------------------------------ #
    # Real invocations — must still reach deep validation                 #
    # ------------------------------------------------------------------ #

    def test_dollar_home_form_denied_by_deep_validator(self):
        """$HOME literal activates Stage 2 (anchored-path check matches \\$HOME),
        but VALID_PATTERN only accepts ~ and absolute /... paths. The deep
        validator must deny it."""
        cmd = "$HOME/.claude/scripts/marker.sh activate code-review"
        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input(cmd)) == "deny"

    def test_dollar_home_form_denied_has_shape_validation_reason(self):
        """$HOME form reaches the deep validator, which emits the shape-validation deny reason."""
        cmd = "$HOME/.claude/scripts/marker.sh activate code-review"
        reason = run_hook_reason(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input(cmd))
        assert reason is not None
        assert "marker.sh invocation denied" in reason

    def test_path_traversal_in_real_invocation_denied(self):
        """~/.claude/scripts/../scripts/marker.sh must be denied via the traversal check.

        The traversal check runs before Stage 2, so tilde-form paths with '../'
        segments are caught even though Stage 2's anchored regex would not match them.
        """
        cmd = "~/.claude/scripts/../scripts/marker.sh activate code-review"
        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input(cmd)) == "deny"


class TestDevNullRedirectAllowed:
    """Trailing 2>/dev/null on an otherwise-valid shape is allowed.
    The literal is matched exactly; stderr is suppressed by shell fd-2
    redirect semantics, which do not affect exit-code propagation."""

    @pytest.mark.parametrize(
        "command",
        [
            # write — all four targets
            "~/.claude/scripts/marker.sh write plan-review 2>/dev/null",
            "~/.claude/scripts/marker.sh write skill-review 2>/dev/null",
            "~/.claude/scripts/marker.sh write ready-for-review 2>/dev/null",
            "~/.claude/scripts/marker.sh write code-review 2>/dev/null",
            # deactivate — all four targets
            "~/.claude/scripts/marker.sh deactivate plan-review 2>/dev/null",
            "~/.claude/scripts/marker.sh deactivate ready-for-review 2>/dev/null",
            "~/.claude/scripts/marker.sh deactivate respond-pr 2>/dev/null",
            "~/.claude/scripts/marker.sh deactivate memory-skill 2>/dev/null",
            # activate — all four targets
            "~/.claude/scripts/marker.sh activate plan-review 2>/dev/null",
            "~/.claude/scripts/marker.sh activate ready-for-review 2>/dev/null",
            "~/.claude/scripts/marker.sh activate respond-pr 2>/dev/null",
            "~/.claude/scripts/marker.sh activate memory-skill 2>/dev/null",
            # clear-stale — both forms
            "~/.claude/scripts/marker.sh clear-stale 2>/dev/null",
            "~/.claude/scripts/marker.sh clear-stale --dry-run 2>/dev/null",
            # absolute-path form — confirms path parity under the 2>/dev/null arm
            "/home/testuser/.claude/scripts/marker.sh write code-review 2>/dev/null",
            # chained-marker pairs — all four orderings (two skills × write-first/deactivate-first)
            (
                "~/.claude/scripts/marker.sh write plan-review && "
                "~/.claude/scripts/marker.sh deactivate plan-review 2>/dev/null"
            ),
            (
                "~/.claude/scripts/marker.sh deactivate plan-review && "
                "~/.claude/scripts/marker.sh write plan-review 2>/dev/null"
            ),
            (
                "~/.claude/scripts/marker.sh write ready-for-review && "
                "~/.claude/scripts/marker.sh deactivate ready-for-review 2>/dev/null"
            ),
            (
                "~/.claude/scripts/marker.sh deactivate ready-for-review && "
                "~/.claude/scripts/marker.sh write ready-for-review 2>/dev/null"
            ),
            # mixed-skill chain (not a same-skill pair) with trailing 2>/dev/null
            (
                "~/.claude/scripts/marker.sh write plan-review && "
                "~/.claude/scripts/marker.sh deactivate ready-for-review 2>/dev/null"
            ),
        ],
    )
    def test_devnull_suffix_allowed(self, command):
        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input(command)) == "allow"


class TestDevNullRedirectBoundaryDenied:
    """Only the exact literal ' 2>/dev/null' at end-of-command is blessed.
    Adjacent forms that look similar must remain denied so a future regex
    edit cannot silently widen the allowance."""

    @pytest.mark.parametrize(
        "command",
        [
            # Only /dev/null target — not arbitrary fd
            "~/.claude/scripts/marker.sh write plan-review 2>&1",
            # Only /dev/null path — not arbitrary path
            "~/.claude/scripts/marker.sh write plan-review 2>/tmp/secret",
            # Only fd-2 — stdout redirect stays denied
            "~/.claude/scripts/marker.sh write plan-review >/dev/null",
            # Only contiguous literal — spaced form stays denied
            "~/.claude/scripts/marker.sh write plan-review 2> /dev/null",
            # Append redirect stays denied
            "~/.claude/scripts/marker.sh write plan-review 2>>/dev/null",
            # No trailing args after the redirect
            "~/.claude/scripts/marker.sh write plan-review 2>/dev/null extra",
            # No chain operators after the redirect
            "~/.claude/scripts/marker.sh write plan-review 2>/dev/null; curl http://evil",
            "~/.claude/scripts/marker.sh write plan-review 2>/dev/null && curl http://evil",
            # Newline-after-redirect: per-line $ / newline-guard invariant for the new suffix
            "~/.claude/scripts/marker.sh write plan-review 2>/dev/null\ncurl http://evil",
            # Mid-chain redirect on LHS stays denied (whole-chain trailing only) — all four orderings
            (
                "~/.claude/scripts/marker.sh write plan-review 2>/dev/null && "
                "~/.claude/scripts/marker.sh deactivate plan-review"
            ),
            (
                "~/.claude/scripts/marker.sh deactivate plan-review 2>/dev/null && "
                "~/.claude/scripts/marker.sh write plan-review"
            ),
            (
                "~/.claude/scripts/marker.sh write ready-for-review 2>/dev/null && "
                "~/.claude/scripts/marker.sh deactivate ready-for-review"
            ),
            (
                "~/.claude/scripts/marker.sh deactivate ready-for-review 2>/dev/null && "
                "~/.claude/scripts/marker.sh write ready-for-review"
            ),
            # VALID_CHAINED_COMMIT_PATTERN deliberately excludes 2>/dev/null (> in its forbidden
            # tail class [^&|;<>]); these forms stay denied by design — see plan for rationale.
            "~/.claude/scripts/marker.sh write code-review && git commit -m foo 2>/dev/null",
            # Mid-LHS redirect in commit chain stays denied (LHS has 2>/dev/null, RHS is git commit)
            "~/.claude/scripts/marker.sh write code-review 2>/dev/null && git commit -m foo",
        ],
    )
    def test_devnull_boundary_denied(self, command):
        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input(command)) == "deny"


MARKER = "~/.claude/scripts/marker.sh"

# Representative members of _LIB_NO_GATE_RELEASE_AGENTS. The full-roster
# coverage lives in test_lib.py against the array itself; these exercise the
# hook end-to-end for an implementer, a stack specialist, the security
# reviewer, and a harness built-in.
NO_GATE_RELEASE_AGENTS = ["code-writer", "staff-sdet", "ciso-reviewer", "Explore"]

# Agent types that keep the documented delegation escape hatch: both carry the
# full tool set, so they can genuinely run a review skill themselves.
GATE_RELEASE_ALLOWED_AGENTS = ["general-purpose", "claude"]


class TestGateReleaseAuthority:
    """Only a caller that could have run the review may release the gate.

    marker.sh resolves session_id by walking the process ancestor chain, so a
    subagent's marker write is attributed to — and releases the gate for — the
    whole parent session.
    """

    @pytest.mark.parametrize("agent_type", NO_GATE_RELEASE_AGENTS)
    @pytest.mark.parametrize(
        "skill", ["code-review", "skill-review", "plan-review", "ready-for-review"]
    )
    def test_write_denied_for_no_release_agents(self, agent_type, skill):
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(f"{MARKER} write {skill}", agent_type=agent_type),
            )
            == "deny"
        )

    @pytest.mark.parametrize("agent_type", NO_GATE_RELEASE_AGENTS)
    @pytest.mark.parametrize(
        "target", ["plan-review", "ready-for-review", "respond-pr", "memory-skill"]
    )
    def test_activate_denied_for_no_release_agents(self, agent_type, target):
        """`activate` is the more dangerous verb: the active-bypass marker holds a
        live PID and releases the plan gate with no hash comparison at all."""
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(f"{MARKER} activate {target}", agent_type=agent_type),
            )
            == "deny"
        )

    @pytest.mark.parametrize(
        "command",
        [
            # Chained forms — the check runs before Stage 2, which is what makes
            # these reachable at all.
            f"{MARKER} write plan-review && {MARKER} deactivate plan-review",
            f"{MARKER} write code-review && git commit -m foo",
            # Wrapped forms — Stage 2 fast-exits these and leaves them to
            # permissions.allow, so a check placed after it would let them through.
            f"bash -c '{MARKER} write plan-review'",
            f"MARKER_DEBUG=1 {MARKER} write plan-review",
            "./.claude/scripts/marker.sh write plan-review",
            f"echo hi; {MARKER} activate plan-review",
        ],
    )
    def test_wrapped_and_chained_forms_denied(self, command):
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(command, agent_type="code-writer"),
            )
            == "deny"
        )

    @pytest.mark.parametrize("agent_type", NO_GATE_RELEASE_AGENTS)
    @pytest.mark.parametrize(
        "command",
        [
            f"{MARKER} deactivate plan-review",
            f"{MARKER} clear-stale",
            f"{MARKER} clear-stale --dry-run",
        ],
    )
    def test_gate_rearming_ops_still_allowed(self, agent_type, command):
        """deactivate and clear-stale re-arm gates rather than releasing them."""
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(command, agent_type=agent_type),
            )
            == "allow"
        )

    def test_resolve_session_id_allowed_for_main_session(self):
        """resolve-session-id is a pure read that releases no gate, so it is
        not subject to the gate-release authority check at all — confirmed
        explicitly rather than assumed from the regex accepting the shape."""
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(f"{MARKER} resolve-session-id"),
            )
            == "allow"
        )

    @pytest.mark.parametrize("agent_type", NO_GATE_RELEASE_AGENTS)
    def test_resolve_session_id_allowed_for_restricted_subagent(self, agent_type):
        """Same as the main-session case, for a restricted subagent — releasing
        no gate means resolve-session-id is allowed for every agent type, not
        just ones with review authority."""
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(f"{MARKER} resolve-session-id", agent_type=agent_type),
            )
            == "allow"
        )

    @pytest.mark.parametrize("agent_type", GATE_RELEASE_ALLOWED_AGENTS)
    def test_full_tool_set_agents_may_still_write(self, agent_type):
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(f"{MARKER} write plan-review", agent_type=agent_type),
            )
            == "allow"
        )

    def test_main_session_may_still_write(self):
        """Absent agent_type is the main session — the ordinary path."""
        assert (
            run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input(f"{MARKER} write plan-review"))
            == "allow"
        )

    def test_empty_agent_type_may_still_write(self):
        """An explicitly empty agent_type is also the main session."""
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(f"{MARKER} write plan-review", agent_type=""),
            )
            == "allow"
        )

    def test_reviewer_may_still_grep_for_marker_script(self):
        """The accepted false-deny is narrow: matching the op keyword, not the
        bare tool name, keeps ordinary reviewer greps working."""
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input("grep -rn marker.sh claude/", agent_type="staff-sdet"),
            )
            == "allow"
        )

    def test_non_string_agent_type_does_not_match_the_roster(self):
        """A contract-violating agent_type must not accidentally satisfy the predicate.

        `jq -r` renders a non-string value rather than failing, so AGENT_TYPE
        becomes that rendering. The predicate is exact-match against a closed
        set, so no rendering of a structured value can match a roster entry —
        this pins that, rather than the (untriggerable-by-payload) jq read
        failure the hook's status check guards against."""
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": f"{MARKER} write plan-review"},
            "agent_type": {"unexpected": "object"},
        }
        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, payload) == "allow"

    def test_deny_reason_directs_agent_to_report_upward(self):
        reason = run_hook_reason(
            ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
            bash_input(f"{MARKER} write plan-review", agent_type="code-writer"),
        )
        assert "code-writer" in reason
        assert "report" in reason.lower()

    @pytest.mark.parametrize(
        "command",
        [
            # Variable indirection: the path is assigned, then invoked through
            # the variable, so `marker.sh` and the op keyword are no longer
            # textually adjacent.
            "MS=~/.claude/scripts/marker.sh; $MS write plan-review",
            # Function-wrapper indirection: same adjacency break.
            'f() { ~/.claude/scripts/marker.sh "$@"; }; f write plan-review',
        ],
    )
    def test_bash_arm_does_not_match_shell_indirection(self, command):
        """Pins the Bash arm's ACCEPTED scope limit rather than an intended behavior.

        This arm matches command text, so it only fires while `marker.sh` and
        the op keyword stay adjacent — the same carve-out Stage 2 already
        documents for wrapped forms. These commands are not pre-approved in
        permissions.allow either, so they surface as a permission prompt rather
        than a silent allow, and the path-based Write/Edit arm below is what
        makes the overall no-gate-release property hold.

        If a future change makes the Bash arm indirection-proof, this test
        should be inverted to assert deny — it is a scope pin, not a guarantee
        that indirection ought to work.
        """
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(command, agent_type="code-writer"),
            )
            == "allow"
        )

    @pytest.mark.xfail(
        reason=(
            "known gap: enforce-marker-script-shape.sh's Bash arm only matches "
            "marker.sh-mentioning commands, not raw redirects — see "
            "agent-reviews/ciso-reviewer-1786305269-plan-mode-review-gate.md "
            "finding 2; tracked for a follow-up PR"
        ),
        strict=True,
    )
    def test_bash_redirect_write_to_planmode_sibling_bypasses_write_authority(self):
        """A raw Bash redirect that writes the same sibling path the
        Write-tool arm correctly denies (see
        TestGateReleaseAuthorityFileWrites.test_marker_path_write_denied)
        never mentions `marker.sh`, so the Bash arm's Stage-1 fast-reject
        exits allow before the gate-release authority check ever runs — for
        any restricted agent type."""
        cmd = (
            'printf "%s" "/tmp/attacker-plan.md" > '
            "~/.claude/.plan-review-active.d/deadbeef.planmode-path"
        )
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(cmd, agent_type="code-writer"),
            )
            == "deny"
        )


class TestGateReleaseAuthorityFileWrites:
    """The path-based arm: marker state is guarded on the file-write surface too.

    Gating only Bash leaves the property false — every agent in
    _LIB_NO_GATE_RELEASE_AGENTS carries the Write tool, so it could fabricate a
    marker file directly and never invoke marker.sh at all. This arm matches on
    the resolved target path, so no shell-level indirection applies to it.
    """

    @pytest.fixture
    def marker_home(self, tmp_path):
        home = tmp_path / "home"
        for kind in ("code-review-markers", "plan-review-markers", "skill-review-markers",
                     "ready-for-review-markers"):
            (home / ".claude" / kind).mkdir(parents=True)
        (home / ".claude" / ".plan-review-active.d").mkdir(parents=True)
        return home

    @pytest.mark.parametrize("agent_type", NO_GATE_RELEASE_AGENTS)
    @pytest.mark.parametrize(
        "relative_path",
        [
            ".claude/code-review-markers/deadbeef.session",
            ".claude/plan-review-markers/deadbeef.session",
            ".claude/skill-review-markers/deadbeef.session",
            ".claude/ready-for-review-markers/deadbeef.session",
            ".claude/.plan-review-active.d/session",
            ".claude/.plan-review-active.d/session.planmode-path",
        ],
    )
    def test_marker_path_write_denied(self, marker_home, agent_type, relative_path):
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                write_input(str(marker_home / relative_path), agent_type=agent_type),
                home=marker_home,
            )
            == "deny"
        )

    def test_main_session_may_write_the_planmode_path_sibling(self, marker_home):
        """The plan-review skill's Step 0 declares the plan-mode file's path
        via a main-session Write to this sibling shape -- the specific claim
        flagged for ciso-reviewer/staff-sdet sign-off: no `agent_type` key
        passes through unconditionally, same as any other main-session Write
        under this arm."""
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                write_input(str(marker_home / ".claude/.plan-review-active.d/session.planmode-path")),
                home=marker_home,
            )
            == "allow"
        )

    @pytest.mark.parametrize("tool_input_builder", [write_input, edit_input, multiedit_input])
    def test_every_file_write_tool_is_covered(self, marker_home, tool_input_builder):
        """Write, Edit, and MultiEdit all reach the same state, so all three are gated."""
        payload = tool_input_builder(
            str(marker_home / ".claude/code-review-markers/deadbeef.session"),
            agent_type="code-writer",
        )
        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, payload, home=marker_home) == "deny"

    def test_tilde_path_denied(self, marker_home):
        """A tilde-form path resolves to the same file, so it is denied identically."""
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                write_input("~/.claude/code-review-markers/deadbeef.session", agent_type="code-writer"),
                home=marker_home,
            )
            == "deny"
        )

    def test_traversal_path_denied(self, marker_home):
        """Path normalization closes the `..` route into the markers directory."""
        sneaky = str(marker_home / ".claude/plans/../code-review-markers/deadbeef.session")
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                write_input(sneaky, agent_type="code-writer"),
                home=marker_home,
            )
            == "deny"
        )

    @pytest.mark.parametrize("agent_type", GATE_RELEASE_ALLOWED_AGENTS)
    def test_full_tool_set_agents_may_write_markers(self, marker_home, agent_type):
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                write_input(
                    str(marker_home / ".claude/code-review-markers/deadbeef.session"),
                    agent_type=agent_type,
                ),
                home=marker_home,
            )
            == "allow"
        )

    def test_main_session_may_write_markers(self, marker_home):
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                write_input(str(marker_home / ".claude/code-review-markers/deadbeef.session")),
                home=marker_home,
            )
            == "allow"
        )

    @pytest.mark.parametrize(
        "relative_path",
        [
            "src/feature.py",
            ".claude/plans/some-plan.md",
            ".claude/sessions/12345",
            "agent-reviews/staff-sdet-1-branch.md",
        ],
    )
    def test_non_marker_paths_allowed(self, marker_home, relative_path):
        """The arm is scoped to marker state — ordinary writes are untouched.

        agent-reviews/ matters specifically: reviewers must stay able to write
        their findings files."""
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                write_input(str(marker_home / relative_path), agent_type="code-writer"),
                home=marker_home,
            )
            == "allow"
        )

    def test_stow_directory_fold_physical_path_denied(self, marker_home, tmp_path):
        """The markers directory has more than one path alias; all of them are gated.

        Under stow directory-fold, `~/.claude` is a symlink to the stow package
        rather than a directory of per-file symlinks, so the same marker file is
        also addressable as `<repo>/claude/.claude/<kind>-markers/...` — a path
        that contains no `$HOME` component at all. Matching the directory SHAPE
        rather than a `$HOME` prefix is what covers both aliases.
        """
        physical = tmp_path / "repo" / "claude" / ".claude" / "code-review-markers" / "forged"
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                write_input(str(physical), agent_type="code-writer"),
                home=marker_home,
            )
            == "deny"
        )

    def test_traversal_path_denied_without_realpath(self, marker_home, tmp_path):
        """The deny must not depend on `realpath` being installed.

        realpath is GNU coreutils and is absent on stock macOS. A `..` segment
        keeps a marker path from carrying a literal `$HOME/.claude/` prefix
        until something normalizes it, so a check that leaned on realpath would
        silently ALLOW this write on those machines — turning a missing
        optional binary into a gate bypass.
        """
        stub_bin = tmp_path / "no-realpath-bin"
        stub_bin.mkdir()
        # A PATH containing everything the hook needs EXCEPT realpath.
        for binary in ("bash", "jq", "grep", "sed", "dirname", "cat", "timeout"):
            resolved = shutil.which(binary)
            if resolved:
                (stub_bin / binary).symlink_to(resolved)
        assert shutil.which("realpath", path=str(stub_bin)) is None

        sneaky = str(marker_home / "unrelated" / ".." / ".claude" / "code-review-markers" / "m")
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                write_input(sneaky, agent_type="code-writer"),
                home=marker_home,
                extra_env={"PATH": str(stub_bin)},
            )
            == "deny"
        )

    def test_large_write_cost_stays_near_the_parse_floor(self, marker_home):
        """A multi-MB Write must not cost much more than parsing it already does.

        This arm fires on every file write, and $INPUT carries the whole file
        content, so every check here is linear in payload size — including the
        jq parse the hook must do regardless. The property worth pinning is
        therefore relative, not absolute: the hook should add only a modest
        multiple of the parse it cannot avoid. An absolute budget would either
        flake on a slow runner or be too loose to catch a regression.

        The floor is measured in-process from the same _lib.sh the hook uses,
        so machine speed cancels out.
        """
        payload = write_input(str(marker_home / "big-generated-file.json"))
        payload["tool_input"]["content"] = "x" * (5 * 1024 * 1024)
        payload_json = json.dumps(payload)

        floor_harness = (
            f"emit_deny() {{ :; }}; . {HOOKS_DIR / '_lib.sh'}; "
            "_lib_parse_tool_input_or_deny x"
        )
        started = time.monotonic()
        subprocess.run(["bash", "-c", floor_harness], input=payload_json,
                       capture_output=True, text=True, check=False)
        floor_seconds = time.monotonic() - started

        started = time.monotonic()
        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, payload, home=marker_home) == "allow"
        hook_seconds = time.monotonic() - started

        allowed = floor_seconds * 2.5 + 0.5
        assert hook_seconds < allowed, (
            f"a 5MB Write cost {hook_seconds:.2f}s against a {floor_seconds:.2f}s "
            f"parse-only floor (allowed {allowed:.2f}s). This arm is doing more "
            f"content-proportional work than the parse it cannot avoid."
        )

    def test_deny_reason_names_the_path_and_directs_upward(self, marker_home):
        reason = run_hook_reason(
            ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
            write_input(
                str(marker_home / ".claude/code-review-markers/deadbeef.session"),
                agent_type="code-writer",
            ),
            home=marker_home,
        )
        assert "code-writer" in reason
        assert "report" in reason.lower()
        assert "code-review-markers" in reason


class TestPrescriptionAllowlistAlignment:
    """Every tilde-form marker.sh (subcommand, argument) shape the hook
    accepts must have a matching permissions.allow entry, except a fixed,
    literal exception set — not "any other shape A4/A5 decline", which would
    silently re-grant a future excluded shape. Absolute-path forms are out of
    scope: every existing and proposed permissions.allow rule is tilde-only
    by convention, even though the hook's MARKER_SHAPE regex also accepts an
    absolute-path prefix.
    """

    # clear-stale sweeps every session's dead-PID bypass markers machine-wide
    # (not just this session's) and is ungated by the hook's no-gate-release
    # check, so it fails A5 admission test (iii) even though CLAUDE.md
    # prescribes it and the hook accepts it — see GH-557 plan, A5.
    ALLOWLIST_EXCEPTIONS = frozenset({
        "clear-stale",  # follow-up: marker.sh clear-stale scoping issue (not yet filed)
        "clear-stale --dry-run",  # follow-up: marker.sh clear-stale scoping issue (not yet filed)
    })

    @staticmethod
    def _allowed_bash_commands() -> set[str]:
        settings = json.loads((CLAUDE_DIR / "settings.json").read_text())
        allow_entries = settings.get("permissions", {}).get("allow", [])
        commands = set()
        for entry in allow_entries:
            m = re.fullmatch(r"Bash\((.*)\)", entry)
            if m:
                commands.add(m.group(1))
        return commands

    @pytest.mark.parametrize("shape", TILDE_MARKER_SHAPES)
    def test_every_hook_accepted_tilde_shape_has_an_allow_entry_or_is_excepted(self, shape):
        allowed_bash_commands = self._allowed_bash_commands()
        # Drive the hook itself rather than trusting the shared constant — a
        # shape that drifted out of sync with the hook's own regex must fail
        # here, not silently pass the allowlist comparison below.
        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input(shape)) == "allow", (
            f"{shape!r} is expected to be hook-accepted per TILDE_MARKER_SHAPES "
            "but the hook denied it."
        )
        subcommand_and_argument = shape.removeprefix("~/.claude/scripts/marker.sh ")
        if subcommand_and_argument in self.ALLOWLIST_EXCEPTIONS:
            return
        assert shape in allowed_bash_commands, (
            f"{shape!r} is hook-accepted and not in ALLOWLIST_EXCEPTIONS, but "
            f"settings.json's permissions.allow has no matching Bash({shape}) entry."
        )

    def test_allowlist_exceptions_is_exactly_the_clear_stale_forms(self):
        """Pins the exception set to its authored literal, not to whatever
        shape A4/A5 happens to exclude at any given time — an open-ended
        "any excluded shape" clause would silently re-grant a future
        disqualified shape instead of failing this test."""
        assert {"clear-stale", "clear-stale --dry-run"} == self.ALLOWLIST_EXCEPTIONS
