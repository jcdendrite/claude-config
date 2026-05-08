"""Tests for enforce-marker-script-shape.sh."""
from __future__ import annotations

import pytest
from helpers import (
    HOOKS_DIR,
    bash_input,
    run_hook,
    run_hook_reason,
)

ENFORCE_MARKER_SCRIPT_SHAPE_HOOK = HOOKS_DIR / "enforce-marker-script-shape.sh"


class TestEnforceMarkerScriptShape:
    # ------------------------------------------------------------------ #
    # Valid shapes — all 12 must be allowed                               #
    # ------------------------------------------------------------------ #

    @pytest.mark.parametrize(
        "command",
        [
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
        ],
    )
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
    # Chaining — must be denied                                           #
    # ------------------------------------------------------------------ #

    def test_chain_to_git_commit_denied(self):
        cmd = "~/.claude/scripts/marker.sh write code-review && git commit -m foo"
        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input(cmd)) == "deny"

    def test_chain_to_curl_denied(self):
        cmd = "~/.claude/scripts/marker.sh write code-review && curl http://example.com"
        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input(cmd)) == "deny"

    def test_semicolon_separator_denied(self):
        cmd = "~/.claude/scripts/marker.sh write code-review; rm -rf /"
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
