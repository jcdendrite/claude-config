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
    # Valid shapes — 14 single-command shapes, each must be allowed       #
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
            "~/.claude/scripts/marker.sh clear-stale",
            "~/.claude/scripts/marker.sh clear-stale --dry-run",
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
