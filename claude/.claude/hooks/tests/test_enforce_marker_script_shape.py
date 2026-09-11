"""Tests for enforce-marker-script-shape.sh."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import textwrap
import time

import pytest
from helpers import (
    CLAUDE_DIR,
    HOOKS_DIR,
    bash_input,
    build_path_without,
    edit_input,
    multiedit_input,
    run_hook,
    run_hook_reason,
    write_input,
)

ENFORCE_MARKER_SCRIPT_SHAPE_HOOK = HOOKS_DIR / "enforce-marker-script-shape.sh"

# The 19 single-command tilde-form shapes the hook accepts — single source of
# truth for both test_valid_shapes_allowed (which pins hook acceptance) and
# TestPrescriptionAllowlistAlignment (which cross-checks permissions.allow
# coverage over this same set), so the two can't silently drift apart.
TILDE_MARKER_SHAPES = [
    "~/.claude/scripts/marker.sh write code-review",
    "~/.claude/scripts/marker.sh write skill-review",
    "~/.claude/scripts/marker.sh write plan-review",
    "~/.claude/scripts/marker.sh write ready-for-review",
    "~/.claude/scripts/marker.sh write cumulative-review",
    "~/.claude/scripts/marker.sh activate plan-review",
    "~/.claude/scripts/marker.sh activate ready-for-review",
    "~/.claude/scripts/marker.sh activate respond-pr",
    "~/.claude/scripts/marker.sh activate memory-skill",
    "~/.claude/scripts/marker.sh activate handoff",
    "~/.claude/scripts/marker.sh deactivate plan-review",
    "~/.claude/scripts/marker.sh deactivate ready-for-review",
    "~/.claude/scripts/marker.sh deactivate respond-pr",
    "~/.claude/scripts/marker.sh deactivate memory-skill",
    "~/.claude/scripts/marker.sh deactivate handoff",
    "~/.claude/scripts/marker.sh clear-stale",
    "~/.claude/scripts/marker.sh clear-stale --dry-run",
    "~/.claude/scripts/marker.sh resolve-session-id",
    "~/.claude/scripts/marker.sh status",
    "~/.claude/scripts/marker.sh check code-review",
]


class TestEnforceMarkerScriptShape:
    # ------------------------------------------------------------------ #
    # Valid shapes — 19 single-command shapes, each must be allowed       #
    # ------------------------------------------------------------------ #

    @pytest.mark.parametrize("command", TILDE_MARKER_SHAPES)
    def test_valid_shapes_allowed(self, command):
        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input(command)) == "allow"

    def test_case_varied_marker_script_path_allowed_for_main_session(self):
        """The case-fold that lets Stage 1/2 recognize a case-varied
        `Marker.sh` path (closing the fast-reject bypass) must not turn into
        a false deny for a legitimate case-varied invocation from a session
        that CAN release the gate -- VALID_PATTERN's own case-fold has to
        hold for the allow path, not just the gate-release-authority deny
        path this hook also gates."""
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input("~/.claude/scripts/Marker.sh write code-review"),
            )
            == "allow"
        )

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
    # single shapes and must NOT appear in the 19-shape parametrize list  #
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

    def test_chain_status_to_rm_denied(self):
        """status cannot ride a chain to a non-`git commit`, non-marker-shape
        tail, the same coverage every other op already has — status is not a
        `write` shape, so it doesn't even qualify for the chained-commit
        allowance either."""
        cmd = "~/.claude/scripts/marker.sh status && rm -rf /"
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

    def test_status_extra_arg_denied(self):
        """status takes no skill argument -- a trailing arg must be denied,
        mirroring the extra-arg guard every other no-argument subcommand
        (resolve-session-id) already gets."""
        cmd = "~/.claude/scripts/marker.sh status extra"
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

    def test_check_mismatched_skill_denied(self):
        """check only supports code-review -- a different skill must be
        denied, mirroring test_mismatched_subcommand_skill_pair_denied above."""
        cmd = "~/.claude/scripts/marker.sh check plan-review"
        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input(cmd)) == "deny"

    def test_check_missing_skill_argument_denied(self):
        """check requires a skill argument -- bare `check` must be denied."""
        cmd = "~/.claude/scripts/marker.sh check"
        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input(cmd)) == "deny"

    def test_check_code_review_extra_arg_denied(self):
        """check code-review with a trailing arg must be denied."""
        cmd = "~/.claude/scripts/marker.sh check code-review extra"
        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input(cmd)) == "deny"

    def test_handoff_extra_arg_denied(self):
        """activate handoff with a trailing arg must be denied."""
        cmd = "~/.claude/scripts/marker.sh activate handoff extra"
        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input(cmd)) == "deny"

    def test_handoff_bad_suffix_denied(self):
        """handoff_bad is not in the allowlist."""
        cmd = "~/.claude/scripts/marker.sh activate handoff_bad"
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
        "skill",
        ["code-review", "skill-review", "plan-review", "ready-for-review", "cumulative-review"],
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
        "target", ["plan-review", "ready-for-review", "respond-pr", "memory-skill", "handoff"]
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

    def test_case_varied_marker_script_path_denied(self):
        """Case-folded script path: on a case-insensitive-but-case-preserving
        filesystem (macOS APFS/HFS+, Windows NTFS), Marker.sh opens the same
        on-disk marker.sh a case-sensitive match here would miss -- Stage 1's
        fast-reject alone would otherwise skip this hook's entire deep
        validation, not just this gate-release-authority check."""
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input("~/.claude/scripts/Marker.sh write code-review", agent_type="code-writer"),
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

    def test_status_allowed_for_main_session(self):
        """status is a pure read that releases no gate, same as
        resolve-session-id — confirmed explicitly rather than assumed from
        the regex accepting the shape."""
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(f"{MARKER} status"),
            )
            == "allow"
        )

    @pytest.mark.parametrize("agent_type", NO_GATE_RELEASE_AGENTS)
    def test_status_allowed_for_restricted_subagent(self, agent_type):
        """Same as the main-session case, for a restricted subagent — status
        is allowed for every agent type, not just ones with review authority."""
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(f"{MARKER} status", agent_type=agent_type),
            )
            == "allow"
        )

    def test_check_allowed_for_main_session(self):
        """check is a pure read that releases no gate, same as status."""
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(f"{MARKER} check code-review"),
            )
            == "allow"
        )

    @pytest.mark.parametrize("agent_type", NO_GATE_RELEASE_AGENTS)
    def test_check_allowed_for_restricted_subagent(self, agent_type):
        """Same as the main-session case, for a restricted subagent -- check
        is allowed for every agent type, not just ones with review authority."""
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(f"{MARKER} check code-review", agent_type=agent_type),
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

    def test_bash_redirect_write_to_planmode_sibling_denied(self):
        """A raw Bash redirect that writes the same sibling path the
        Write-tool arm correctly denies (see
        TestGateReleaseAuthorityFileWrites.test_marker_path_write_denied)
        never mentions `marker.sh`, so it is caught by the pre-Stage-1
        redirect/utility scan rather than the marker.sh-mention check — for
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

    def test_bash_c_wrapper_denied_via_raw_text_arm(self):
        """A `bash -c "marker.sh write ..."` wrapper-hole invocation from a
        no-gate-release agent denies via the raw-text substring check.
        The command-word check alone cannot catch this: it walks past
        known runners via _lib_fragment_command_word, whose runner list
        excludes bash/sh/zsh/dash/ksh entirely, so it never sees past the
        `bash -c` wrapper to the marker.sh invocation inside the quoted
        string. Pins that the raw-text arm stays unconditionally OR'd with
        the command-word check rather than being gated behind detecting a
        specific shell name."""
        cmd = 'bash -c "marker.sh write code-review"'
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(cmd, agent_type="code-writer"),
            )
            == "deny"
        )

    @pytest.mark.parametrize(
        "op,target",
        [("write", "code-review"), ("activate", "plan-review")],
        ids=["write", "activate"],
    )
    def test_quote_split_denied_via_command_word_arm(self, op, target):
        """A quote-split top-level invocation (`"marker.sh" write ...`)
        quotes the whole `marker.sh` token, so the quote character sits
        between the closing `"` and the op keyword, breaking the raw-text OP
        check's `marker\\.sh[[:space:]]+(write|activate)` adjacency
        requirement. This is the command-word arm's actual purpose —
        _lib_command_invokes_tool_subcmd resolves the fragment's command word
        after quote-stripping and matches regardless of that adjacency break.
        (The raw-text OP check now also reads quote-stripped
        COMMAND_UNQUOTED, so this exact command independently matches there
        too — this test still pins the command-word arm's own resolution,
        just no longer in isolation from the raw-text OP check. See
        test_quote_split_inside_marker_token_denied below for a command that
        quotes *inside* the token instead, which is what actually defeats
        Stage 1 and the raw-text OP check rather than just their
        now-superseded adjacency requirement.)"""
        cmd = f'"marker.sh" {op} {target}'
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(cmd, agent_type="code-writer"),
            )
            == "deny"
        )

    def test_quote_split_inside_marker_token_denied(self):
        """A quote landing INSIDE the `marker.sh` token (as opposed to
        test_quote_split_denied_via_command_word_arm's whole-token quoting,
        e.g. `'"marker.sh" write ...'`, which leaves the substring
        `marker.sh` itself contiguous in raw text and was already caught by
        Stage 1's own substring check for a different reason) breaks that
        contiguous substring in raw $COMMAND while a real shell still
        executes it identically to the unquoted form. Reading raw $COMMAND
        for Stage 1's fast-reject (`grep -qFi 'marker.sh'`) rather than
        quote-stripped COMMAND_UNQUOTED would miss the match here entirely:
        the whole hook would exit early as an allow before any deeper
        validation ran, for a no-gate-release agent that cannot
        legitimately release this gate at all."""
        cmd = '~/.claude/scripts/"marker".sh write code-review'
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(cmd, agent_type="code-writer"),
            )
            == "deny"
        )

    def test_bash_c_wrapper_quote_split_inside_marker_token_denied(self):
        """A `bash -c` wrapper whose wrapped text quote-splits INSIDE the
        `marker.sh` token (as opposed to
        test_bash_c_wrapper_denied_via_raw_text_arm's unquoted wrapped
        text) defeats the raw-text OP check when it reads unstripped
        $COMMAND: the split breaks the contiguous `marker.sh` substring the
        same way it does at the top level, and this shape has no other
        coverage — _lib_fragment_command_word's runner list excludes
        bash/sh/zsh/dash/ksh entirely, so the command-word arm
        (_lib_command_invokes_tool_subcmd) cannot see inside the wrapper
        either. The raw-text OP check reading quote-stripped
        COMMAND_UNQUOTED, rather than unstripped $COMMAND, is the sole
        coverage for this shape."""
        cmd = 'bash -c "mark""er.sh write code-review"'
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(cmd, agent_type="code-writer"),
            )
            == "deny"
        )

    @pytest.mark.parametrize("agent_type", GATE_RELEASE_ALLOWED_AGENTS)
    def test_quote_split_write_allowed_for_full_tool_set_agents(self, agent_type):
        """The command-word arm gates on agent authority, not just command
        shape: the same quote-split write that denies for a no-gate-release
        agent above must not deny here. (This command's overall verdict is
        "allow" for two independent reasons — the gate-release-authority
        block never runs for this agent type, and Stage 2's anchor never
        recognizes this command as a marker.sh invocation shape at all,
        quote-stripped or not: the anchor requires a leading
        `~`/`$HOME`/absolute-path prefix immediately before `.claude/scripts/
        marker.sh`, and this bare `"marker.sh"` token has no such prefix —
        but the agent-authority scoping is what this test pins. See
        test_quote_split_inside_script_path_denied_even_for_full_authority
        below for the anchored-path form of a quote-split invocation, which
        Stage 2's anchor now DOES recognize post-fix and denies for every
        caller regardless of authority.)"""
        cmd = '"marker.sh" write code-review'
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(cmd, agent_type=agent_type),
            )
            == "allow"
        )

    @pytest.mark.parametrize("agent_type", GATE_RELEASE_ALLOWED_AGENTS + [None])
    def test_quote_split_inside_script_path_denied_even_for_full_authority(
        self, agent_type
    ):
        """A quote landing inside the `marker.sh` token, with the
        anchored `~/.claude/scripts/` path prefix present (as opposed to
        test_quote_split_write_allowed_for_full_tool_set_agents' bare,
        unprefixed token), denies for EVERY caller — including one with
        full gate-release authority (or no agent_type at all, the
        main-session shape) — not just a no-gate-release agent.

        This is an accepted over-deny, not a new restriction on authorized
        release: pre-fix, this exact shape fast-exited Stage 2 as an
        unrecognized "wrapped form" and fell through to
        permissions.allow, whose literal-string entries don't match a
        quoted command either — so it was never a guaranteed silent allow
        pre-fix, just a different enforcement layer's problem. Post-fix,
        Stage 2's anchor reads quote-stripped COMMAND_UNQUOTED and
        correctly recognizes this as *a marker.sh invocation shape*, so it
        proceeds to VALID_PATTERN — which is deliberately raw-text (see
        enforce-marker-script-shape.sh's Stage 2 comments) and can never
        match a quoted token — so it denies unconditionally. No legitimate
        caller naturally embeds a quote inside the marker.sh path itself,
        so denying this shape for authorized callers too is accepted as
        the safe default rather than special-cased to allow."""
        cmd = '~/.claude/scripts/"marker".sh write code-review'
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(cmd, agent_type=agent_type),
            )
            == "deny"
        )

    def test_sed_absent_from_path_denies(self, tmp_path):
        """Status-2 propagation, fail-closed: with sed/tr unavailable, a
        no-gate-release agent's marker.sh write attempt still denies.

        The observed deny is reached via this hook's earlier, unconditional
        COMMAND_UNQUOTED quote-strip check (runs before Stage 1, for every
        Bash call, not only marker.sh-shaped ones) rather than
        via the gate-release-authority arm's own
        _lib_command_invokes_tool_subcmd status-2 branch in isolation —
        confirmed by inspecting the deny reason below. Both checks consume
        the same $COMMAND through the same _lib_strip_shell_quotes call, so
        the earlier one always denies first when sed is absent entirely.
        See test_gate_release_authority_arm_own_status2_denied below for
        the new arm's own status-2 branch isolated via a shaped sed shim
        rather than total sed absence. Asserting on the fork-failure
        reason text, not just the verdict, distinguishes this from an
        ordinary raw-text-match deny (which would also fire for this
        command, but for an unrelated reason and without depending on sed
        at all)."""
        farm_dir = tmp_path / "path-without-sed"
        farm_dir.mkdir()
        restricted_path = build_path_without("sed", farm_dir)
        reason = run_hook_reason(
            ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
            bash_input(f"{MARKER} write code-review", agent_type="code-writer"),
            extra_env={"PATH": restricted_path},
        )
        assert reason is not None
        assert "sed/tr" in reason

    def test_gate_release_authority_arm_own_status2_denied(self, tmp_path):
        """Isolates the gate-release-authority arm's OWN status-2 branch
        (_lib_command_invokes_tool_subcmd could not determine a match),
        distinct from the sed-absent test above, which is caught first by
        this hook's earlier, unconditional COMMAND_UNQUOTED quote-strip
        check and so never actually isolates this arm.

        The command here (`marker.sh status`) names neither `write` nor
        `activate`, so the raw-text detector's own regex
        (`marker\\.sh[[:space:]]+(write|activate)`) never matches it
        regardless of sed's health — the loop below still calls
        _lib_command_invokes_tool_subcmd once per op unconditionally, so a
        status-only command is what isolates its own status-2 branch (a
        quote-split write/activate command no longer does, now that the
        raw-text detector reads quote-stripped COMMAND_UNQUOTED: it would
        match there too, denying via the "cannot release a review gate"
        branch before this arm's own status-2 branch is ever reached).
        Skips the pre-Stage-1 redirect-candidate scan not because of any
        `.claude`-substring requirement (that pre-filter was removed — see
        this hook's header comment recording the removal) but because a
        `status` command has no `>`/write-utility construct for
        _lib_command_has_write_construct to flag. A sed shim that succeeds
        only for _lib_strip_shell_quotes's own `-e`-flagged invocation
        shape and fails for _lib_split_fragments's differently-shaped call
        (the same technique test_fragments_split_sed_failure_denied in
        test_deny_private_project_refs.py and
        test_redirect_candidates_split_sed_failure_denied above both use)
        lets COMMAND_UNQUOTED's own `-e`-shaped strip succeed via the real
        sed while _lib_command_invokes_tool_subcmd's internal
        _lib_split_fragments call fails on its own, reaching this arm's
        status-2 branch in isolation. Asserted via the arm's own distinct
        deny-reason text ("could not determine whether ... invokes
        marker.sh write/activate"), not the shared "sed/tr" substring both
        this deny and the sed-absent test's deny contain — a bare
        "sed/tr" assertion cannot tell which check actually fired."""
        real_sed = shutil.which("sed")
        assert real_sed, "test host must have a real sed binary on PATH"

        shim_dir = tmp_path / "sed-fails-outside-strip-shell-quotes-shape"
        shim_dir.mkdir()
        shim_script = textwrap.dedent(f"""\
            #!/bin/bash
            if [ "$2" != "-e" ]; then
              exit 1
            fi
            exec "{real_sed}" "$@"
        """)
        (shim_dir / "sed").write_text(shim_script)
        (shim_dir / "sed").chmod(0o755)

        reason = run_hook_reason(
            ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
            bash_input(f"{MARKER} status", agent_type="code-writer"),
            extra_env={"PATH": f"{shim_dir}{os.pathsep}{os.environ['PATH']}"},
        )
        assert reason is not None
        assert "could not determine whether" in reason
        assert "invokes marker.sh write/activate" in reason

    def test_command_flattened_sed_failure_denied(self, tmp_path):
        """Isolates _lib_brace_flatten's own sed call, distinct from the
        sed-absent test above, which is caught first by this hook's
        earlier, unconditional COMMAND_UNQUOTED quote-strip check.
        _lib_brace_flatten's sed invocation carries the literal substring
        `\\{` in its own pattern arguments, which none of
        _lib_strip_shell_quotes's three sed patterns do (they use `\\$`
        and `\\(` instead), so a shim keyed on that substring lets
        COMMAND_UNQUOTED's strip succeed via the real sed while
        _lib_brace_flatten's own sed call fails on its own, reaching this
        hook's own $COMMAND_FLATTENED_EXIT check in isolation. The fixture
        must itself contain a brace group -- _lib_brace_flatten's
        fork-free fast path would otherwise never reach the sed call at
        all."""
        real_sed = shutil.which("sed")
        assert real_sed, "test host must have a real sed binary on PATH"

        shim_dir = tmp_path / "sed-fails-on-brace-flatten-pattern-shape"
        shim_dir.mkdir()
        shim_script = textwrap.dedent(f"""\
            #!/bin/bash
            for arg in "$@"; do
              case "$arg" in
                *'\\{{'*) exit 1 ;;
              esac
            done
            exec "{real_sed}" "$@"
        """)
        (shim_dir / "sed").write_text(shim_script)
        (shim_dir / "sed").chmod(0o755)

        reason = run_hook_reason(
            ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
            bash_input(f"{MARKER} write cod{{e,e}}-review", agent_type="code-writer"),
            extra_env={"PATH": f"{shim_dir}{os.pathsep}{os.environ['PATH']}"},
        )
        assert reason is not None
        assert "could not flatten brace-expansion constructs" in reason

    def test_pass_budget_exceeded_denied(self):
        """Hook-level counterpart to test_lib.py's
        test_lib_brace_flatten_fails_closed_past_the_16_pass_budget: 17
        levels of real nesting -- one past _lib_brace_flatten's fixed
        16-pass budget -- denies via this hook's own COMMAND_FLATTENED_EXIT
        -eq 2 branch, not just at the isolated _lib_brace_flatten unit.
        Fixture copied verbatim from test_lib.py's
        _BRACE_FLATTEN_17_LEVEL_NESTED, since a single dropped or added
        brace changes the required pass count."""
        seventeen_level_nested = (
            "l{s,{s,{s,{s,{s,{s,{s,{s,{s,{s,{s,{s,{s,{s,{s,{s,{s,s}}}}}}}}}}}}}}}}}"
        )
        reason = run_hook_reason(
            ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
            bash_input(
                f"{MARKER} write code-review {seventeen_level_nested}",
                agent_type="code-writer",
            ),
        )
        assert reason is not None
        assert "16-pass flattening budget" in reason

    @pytest.mark.timing
    def test_sixteen_level_nested_brace_returns_promptly(self):
        """End-to-end latency of the brace-flatten pass plus the doubled
        (raw + flattened) redirect-candidate scan this diff adds, driven
        through the real hook binary rather than the isolated
        _lib_brace_flatten unit test_lib.py's own timing test covers.
        `tee` (a recognized write utility, unaffected by flattening) keeps
        the doubled pipeline from fast-rejecting on either pass, so both
        passes' full fragment-split/extraction/shape-match cost is paid, on
        top of _lib_brace_flatten's own 16-pass cost. No `/` anywhere in the
        command, so `_lib_command_has_brace_expansion_bypass`'s categorical
        write-construct+brace+slash predicate doesn't fire and mask the
        timing measurement behind an early deny. Generous bound, not a
        tight wall-clock threshold, matching this repo's own stated
        timing-test convention -- so this isn't flaky on a loaded machine,
        but still catches a future regression to the doubled-scan wrapper
        or the pass count."""
        sixteen_level_nested = (
            "l{s,{s,{s,{s,{s,{s,{s,{s,{s,{s,{s,{s,{s,{s,{s,{s,s}}}}}}}}}}}}}}}}"
        )
        started = time.monotonic()
        result = run_hook(
            ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
            bash_input(f"tee destfile {sixteen_level_nested}", agent_type="code-writer"),
        )
        elapsed = time.monotonic() - started
        assert result == "allow"
        assert elapsed < 5.0, f"16-level nested brace fixture took {elapsed:.2f}s -- should return promptly"


class TestBraceSplitCallSitesClosed:
    """A brace-split `marker.sh` token (`marker.s{h,h}`) reaches a raw-text
    scan as a literal that matches nothing, even though bash itself
    brace-expands it before executing, unless the scan is also run against
    the flattened companion COMMAND_FLATTENED. One deny-path test per shape
    (comma, range) at each call site, each paired with an "ordinary command
    still allows" control at the same call site. Stage 1's own fast-reject
    fix is a prerequisite every case here also exercises (an unfixed
    Stage 1 would fast-exit as "allow" before any later check ever ran), so
    it is not isolated as a separate case."""

    @pytest.mark.parametrize(
        "command",
        [
            pytest.param("marker.s{h,h} write code-review", id="gate-release-comma"),
            pytest.param("marker.s{h..h} write code-review", id="gate-release-range"),
        ],
    )
    def test_bare_invocation_denied_for_no_gate_release_agent(self, command):
        """Bare (no `~/.claude/scripts/` path prefix) form: Stage 2's
        anchor never matches this shape either way (fixed or not), so a
        denial here isolates the gate-release-authority arm's own fix
        specifically."""
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(command, agent_type="code-writer"),
            )
            == "deny"
        )

    def test_bare_ordinary_command_still_allowed_for_no_gate_release_agent(self):
        assert (
            run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input("echo hi", agent_type="code-writer"))
            == "allow"
        )

    def test_bash_c_wrapper_with_brace_split_marker_token_denied(self):
        """Crosses the two dimensions test_bash_c_wrapper_denied_via_raw_text_arm
        and test_bare_invocation_denied_for_no_gate_release_agent each test
        separately: a `bash -c` wrapper AND a brace-split `marker.sh` token,
        together. Stage 1's fast-reject passes because COMMAND_FLATTENED
        contains `marker.sh`, so the wrapped text reaches the gate-release-
        authority arm; the raw-text substring check's own COMMAND_FLATTENED
        companion is what denies it there, since
        _lib_command_invokes_tool_subcmd's command-word resolution treats
        `bash` (not `marker.sh`) as the fragment's command word regardless
        of flattening."""
        cmd = 'bash -c "marker.s{h,h} write code-review"'
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(cmd, agent_type="code-writer"),
            )
            == "deny"
        )

    @pytest.mark.parametrize(
        "command",
        [
            pytest.param("~/.claude/scripts/marker.s{h,h} write code-review", id="stage2-anchor-comma"),
            pytest.param("~/.claude/scripts/marker.s{h..h} write code-review", id="stage2-anchor-range"),
        ],
    )
    def test_anchored_invocation_denied_for_full_tool_set_agent(self, command):
        """Anchored (`~/.claude/scripts/` path prefix present) form, run
        for an agent that COULD release a gate (no agent_type -- main
        session): the gate-release-authority arm is skipped entirely for
        this agent type, so a denial here isolates Stage 2's anchor fix --
        an unfixed anchor would fast-exit as "allow", deferring to
        permissions.allow, rather than routing into this hook's own deep
        VALID_PATTERN validation, which then denies since the still-braced
        text never matches VALID_PATTERN's own literal `marker.sh`."""
        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input(command)) == "deny"

    def test_anchored_ordinary_command_still_allowed_for_full_tool_set_agent(self):
        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input(f"{MARKER} status")) == "allow"

    @pytest.mark.parametrize(
        "command",
        [
            pytest.param(
                "t{ee,ee} ~/.claude/code-review-markers/forged", id="alternation-comma"
            ),
            pytest.param(
                "t{e..e}e ~/.claude/code-review-markers/forged", id="alternation-range"
            ),
        ],
    )
    def test_write_utility_name_brace_split_denied(self, command):
        """Pre-Stage-1 redirect/utility-write scan: the write-utility name
        itself is brace-split, so `marker.sh` is never mentioned anywhere
        in the command at all -- distinct from every other case in this
        class, which brace-splits `marker.sh` itself."""
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(command, agent_type="code-writer"),
            )
            == "deny"
        )

    def test_ordinary_command_still_allowed_at_alternation(self):
        assert (
            run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input("echo hi", agent_type="code-writer"))
            == "allow"
        )


class TestGateReleaseAuthorityBashRedirectAndUtility:
    """The pre-Stage-1 scan: a Bash write to a marker path via a redirect or
    write utility is caught even though the command never mentions
    `marker.sh`. Covers the closed half of the gap `_marker_shape_match`
    documents as still open (python3 -c, here-doc bodies, $(...)-computed
    paths, shell-function indirection).
    """

    @pytest.fixture
    def marker_home(self, tmp_path, monkeypatch):
        # An ambient CLAUDE_CONFIG_DIR would resolve the config root away
        # from this fixture's own $HOME/.claude, which the `-ef`-based
        # symlink tests below depend on -- matching isolated_home's
        # convention (conftest.py).
        monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
        home = tmp_path / "home"
        (home / ".claude" / "code-review-markers").mkdir(parents=True)
        return home

    @pytest.mark.parametrize(
        "command",
        [
            "echo x | tee -a ~/.claude/code-review-markers/forged",
            "cp /tmp/attacker-plan.md ~/.claude/code-review-markers/forged",
            "mv /tmp/attacker-plan.md ~/.claude/code-review-markers/forged",
            "install /tmp/attacker-plan.md ~/.claude/code-review-markers/forged",
            "dd if=/tmp/attacker-plan.md of=~/.claude/code-review-markers/forged",
            "sed -i 's/a/b/' ~/.claude/code-review-markers/forged",
            # A trailing flag after the true destination -- an argument
            # shape cp's own parser accepts and still performs the write, so
            # it must still deny.
            "cp /tmp/attacker-plan.md ~/.claude/code-review-markers/forged -v",
            "rsync /tmp/attacker-plan.md ~/.claude/code-review-markers/forged",
            "curl -s -o ~/.claude/code-review-markers/forged https://example.invalid/payload",
            "scp /tmp/attacker-plan.md localhost:~/.claude/code-review-markers/forged",
            "wget -O ~/.claude/code-review-markers/forged https://example.invalid/payload",
            "openssl enc -out ~/.claude/code-review-markers/forged -in /tmp/attacker-plan.md",
        ],
    )
    def test_write_utility_to_marker_path_denied(self, command):
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(command, agent_type="code-writer"),
            )
            == "deny"
        )

    @pytest.mark.parametrize(
        "command",
        [
            "echo x | tee -a ~/.claude/code-review-markers/forged",
            "cp /tmp/attacker-plan.md ~/.claude/code-review-markers/forged",
            "mv /tmp/attacker-plan.md ~/.claude/code-review-markers/forged",
            "install /tmp/attacker-plan.md ~/.claude/code-review-markers/forged",
            "dd if=/tmp/attacker-plan.md of=~/.claude/code-review-markers/forged",
            "sed -i 's/a/b/' ~/.claude/code-review-markers/forged",
            "cp /tmp/attacker-plan.md ~/.claude/code-review-markers/forged -v",
            "rsync /tmp/attacker-plan.md ~/.claude/code-review-markers/forged",
            "curl -s -o ~/.claude/code-review-markers/forged https://example.invalid/payload",
            "scp /tmp/attacker-plan.md localhost:~/.claude/code-review-markers/forged",
            "wget -O ~/.claude/code-review-markers/forged https://example.invalid/payload",
            "openssl enc -out ~/.claude/code-review-markers/forged -in /tmp/attacker-plan.md",
        ],
    )
    def test_full_tool_set_agent_may_use_any_write_utility_on_a_marker_path(self, command):
        """The deny above is agent-scoped, not utility-scoped: each write
        mechanism must also have a verified allow path for an agent that
        could have run the review."""
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(command, agent_type="general-purpose"),
            )
            == "allow"
        )

    @pytest.mark.parametrize(
        "command",
        [
            "curl -O https://example.invalid/forged",
            "wget https://example.invalid/forged",
        ],
    )
    def test_curl_wget_implicit_destination_allowed_residual(self, command):
        """curl -O and a bare `wget URL` derive their write target from the
        URL or a server response rather than naming it as a literal token in
        the command -- the one residual _LIB_WRITE_UTILITIES's membership
        criterion excludes (see that array's own header comment in
        _lib.sh). Pins that this specific implicit-destination shape stays
        allowed, even for a no-gate-release agent, so a future attempt to
        close it reads as a deliberate mechanism change to this test, not
        an unnoticed capability drift."""
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(command, agent_type="code-writer"),
            )
            == "allow"
        )

    @pytest.mark.parametrize(
        "command",
        [
            # Quote-splitting: the shell collapses `~/.cla''ude/...` to
            # `~/.claude/...` at execution time even though no contiguous
            # `.claude` substring appears in the raw command text.
            "printf x > ~/.cla''ude/code-review-markers/forged",
            # Case-folding: macOS's default APFS volume is case-insensitive,
            # so this resolves to the same on-disk marker directory.
            "printf x > ~/.Claude/code-review-markers/forged",
        ],
    )
    def test_quote_split_and_case_fold_bypass_forms_denied(self, command):
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(command, agent_type="code-writer"),
            )
            == "deny"
        )

    @pytest.mark.parametrize(
        "command",
        [
            # Glued, no space -- a distinct branch (redirect_glued_re) from
            # the standalone-operator form the other tests already exercise.
            "printf x >~/.claude/code-review-markers/forged",
            # Append form.
            "printf x >> ~/.claude/code-review-markers/forged",
            # fd-prefixed, glued -- redirects stderr rather than stdout.
            "printf x 2>~/.claude/code-review-markers/forged",
            # fd-prefixed with a 2-digit descriptor -- pins the quantifier
            # against a regex that only tolerates a single digit.
            "printf x 35>~/.claude/code-review-markers/forged",
            # Combined stdout+stderr redirect, standalone and append forms --
            # cannot take an fd prefix, unlike the operators above.
            "printf x &> ~/.claude/code-review-markers/forged",
            "printf x &>> ~/.claude/code-review-markers/forged",
        ],
    )
    def test_redirect_operator_forms_to_marker_path_denied(self, command):
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(command, agent_type="code-writer"),
            )
            == "deny"
        )

    def test_stow_directory_fold_physical_path_denied(self, marker_home, tmp_path):
        """Mirrors TestGateReleaseAuthorityFileWrites' same-named test: the
        markers directory's stow-fold physical-path alias is gated on this
        arm too, not only the Write/Edit arm."""
        physical = tmp_path / "repo" / "claude" / ".claude" / "code-review-markers" / "forged"
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(f"printf x > {physical}", agent_type="code-writer"),
                home=marker_home,
            )
            == "deny"
        )

    def test_traversal_path_denied_without_realpath(self, marker_home, tmp_path):
        """Mirrors TestGateReleaseAuthorityFileWrites' same-named test: the
        nested loop's no-realpath fallback (raw candidate only) has to hold
        for N extracted Bash targets, not just the Write arm's one."""
        stub_bin = tmp_path / "no-realpath-bin"
        stub_bin.mkdir()
        # Adds `tr` to the Write-arm counterpart's binary list: the Bash arm's
        # redirect-target extraction quote-strips via _lib_strip_shell_quotes,
        # which the Write arm's file_path-only path never needs.
        for binary in ("bash", "jq", "grep", "sed", "dirname", "cat", "timeout", "tr"):
            resolved = shutil.which(binary)
            if resolved:
                (stub_bin / binary).symlink_to(resolved)
        assert shutil.which("realpath", path=str(stub_bin)) is None

        sneaky = str(marker_home / "unrelated" / ".." / ".claude" / "code-review-markers" / "m")
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(f"printf x > {sneaky}", agent_type="code-writer"),
                home=marker_home,
                extra_env={"PATH": str(stub_bin)},
            )
            == "deny"
        )

    def test_main_session_may_redirect_into_a_marker_path(self):
        """Absent agent_type is the main session — the ordinary path."""
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input("printf x > ~/.claude/code-review-markers/forged"),
            )
            == "allow"
        )

    @pytest.mark.parametrize("agent_type", GATE_RELEASE_ALLOWED_AGENTS)
    def test_full_tool_set_agents_may_redirect_into_a_marker_path(self, agent_type):
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(
                    "printf x > ~/.claude/code-review-markers/forged", agent_type=agent_type
                ),
            )
            == "allow"
        )

    @pytest.mark.parametrize(
        "command",
        [
            # .claude/-rooted but not marker-shaped -- the fast-reject's real
            # blast radius, since `grep -qF '.claude'` matches far more than
            # marker paths.
            "printf x > ~/.claude/plans/foo.md",
            "printf x > ~/.claude/scratch.log",
        ],
    )
    def test_non_marker_claude_rooted_redirect_allowed(self, command):
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(command, agent_type="code-writer"),
            )
            == "allow"
        )

    def test_claude_mention_in_a_non_target_argument_allowed(self):
        """The fast-reject fires on `.claude` appearing anywhere in the
        command, and every word of the fragment (source included) is now a
        shape-tested candidate -- but a `.claude`-rooted source whose own
        path shape doesn't match a marker suffix pattern (this one names a
        script, not a marker file under a `*-markers`/`.*-active.d`
        directory) must still not deny."""
        cmd = "cp ~/.claude/scripts/marker.sh /tmp/backup.sh"
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(cmd, agent_type="code-writer"),
            )
            == "allow"
        )

    def test_python_write_call_to_marker_path_allowed_residual(self):
        """Accepted residual: this arm matches command TEXT for redirect and
        utility shapes, not a Python string literal's runtime effect."""
        cmd = "python3 -c \"open('~/.claude/code-review-markers/forged', 'w').write('x')\""
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(cmd, agent_type="code-writer"),
            )
            == "allow"
        )

    def test_heredoc_body_targeting_marker_path_allowed_residual(self):
        """Accepted residual: a here-doc body handed to an interpreter is
        opaque text to this arm, the same carve-out as the python3 -c case."""
        cmd = (
            "python3 <<'EOF'\n"
            "open('/home/user/.claude/code-review-markers/forged', 'w').write('x')\n"
            "EOF"
        )
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(cmd, agent_type="code-writer"),
            )
            == "allow"
        )

    @pytest.mark.parametrize(
        "command",
        [
            "cp -t ~/.claude/code-review-markers /tmp/attacker-plan.md",
            "cp --target-directory=~/.claude/code-review-markers /tmp/attacker-plan.md",
            "mv -t ~/.claude/code-review-markers /tmp/attacker-plan.md",
            "install -t ~/.claude/code-review-markers /tmp/attacker-plan.md",
        ],
    )
    def test_target_directory_form_denied(self, command, marker_home):
        """`-t DIR`/`--target-directory=DIR` writes to DIR/basename(source),
        a path no single token in the command spells out literally -- but
        DIR alone still `-ef`-matches the marker directory itself via
        _lib_shape_match's Pass 4, so this is caught even though the joined
        DIR/basename path is never a literal token in the command. Pass 4's
        `-ef` comparison only fires against a marker directory that actually
        exists on disk, hence marker_home."""
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(command, agent_type="code-writer"),
                home=marker_home,
            )
            == "deny"
        )

    def test_bare_positional_directory_destination_denied(self, marker_home):
        """Same DIR-alone shape as the `-t DIR` form above, reached through
        cp's bare trailing-positional-argument form instead of the `-t`/
        `--target-directory` flag -- no trailing slash on DIR."""
        cmd = "cp /tmp/attacker-plan.md ~/.claude/code-review-markers"
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(cmd, agent_type="code-writer"),
                home=marker_home,
            )
            == "deny"
        )

    @pytest.mark.parametrize(
        "command_template",
        [
            "cp -t {target_dir} /tmp/attacker-plan.md",
            "cp --target-directory={target_dir} /tmp/attacker-plan.md",
            "mv -t {target_dir} /tmp/attacker-plan.md",
            "install -t {target_dir} /tmp/attacker-plan.md",
        ],
    )
    def test_target_directory_form_to_unrelated_directory_allowed(self, command_template, marker_home):
        """Pass 4's candidate-itself `-ef` comparison is scoped to the
        marker-kind directories `dir_expansions` glob-expands, not to "any
        directory destination" -- a directory-form write into a genuinely
        unrelated directory must stay allowed, or a future widening of Pass
        4's own glob-expansion set would over-deny with no test to catch
        it."""
        unrelated_dir = marker_home / "unrelated"
        unrelated_dir.mkdir()
        command = command_template.format(target_dir=unrelated_dir)
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(command, agent_type="code-writer"),
                home=marker_home,
            )
            == "allow"
        )

    def test_bare_positional_directory_destination_to_unrelated_directory_allowed(self, marker_home):
        """Bare-positional counterpart of the flag-based allow test above --
        `cp file DIR` with no trailing slash, into an unrelated directory."""
        unrelated_dir = marker_home / "unrelated"
        unrelated_dir.mkdir()
        cmd = f"cp /tmp/attacker-plan.md {unrelated_dir}"
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(cmd, agent_type="code-writer"),
                home=marker_home,
            )
            == "allow"
        )

    def test_command_substitution_computed_target_allowed_residual(self):
        """Accepted residual: a `$(...)`-computed target is opaque text to
        the word-splitting extraction, which reads the substitution syntax
        itself rather than its runtime output."""
        cmd = 'printf x > "$(echo ~/.claude/code-review-markers/forged)"'
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(cmd, agent_type="code-writer"),
            )
            == "allow"
        )

    @pytest.mark.parametrize(
        "command",
        [
            # Variable indirection: the target is a variable reference, not
            # the literal marker path, so word-splitting extracts the
            # variable name rather than a shape-matchable path.
            'T=~/.claude/code-review-markers/forged; printf x > "$T"',
            # Shell-function indirection: the fragment invoking the write
            # utility names the wrapper function, not `cp` itself, so
            # `_lib_fragment_invokes_tool` never recognizes it.
            'f() { cp "$@"; }; f /tmp/attacker-plan.md ~/.claude/code-review-markers/forged',
        ],
    )
    def test_shell_indirection_around_the_write_target_or_utility_allowed_residual(self, command):
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(command, agent_type="code-writer"),
            )
            == "allow"
        )

    def test_symlink_with_no_claude_in_its_own_path_denied(self, marker_home, tmp_path):
        """Closes the former residual: this scan no longer has a `.claude`-
        substring fast-reject, so a symlink whose own path carries no
        `.claude` segment but resolves (via `-ef` inode identity) into the
        real markers directory is now caught."""
        alias_dir = tmp_path / "aliasdir"
        alias_dir.mkdir()
        symlinked_path = alias_dir / "notclaudepath"
        symlinked_path.symlink_to(marker_home / ".claude" / "code-review-markers")
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(f"printf x > {symlinked_path}/forged", agent_type="code-writer"),
                home=marker_home,
            )
            == "deny"
        )

    def test_symlink_with_claude_not_followed_by_slash_denied(self, marker_home, tmp_path):
        """Mirrors the residual test above from the opposite direction: the
        pre-filter's `.claude` substring test is not anchored to a following
        `/`, so a symlink named e.g. `.claudetrick` (contains `.claude` but
        not immediately followed by a path separator) still reaches
        `_marker_shape_match`'s realpath resolution rather than being
        skipped as if it didn't mention `.claude` at all."""
        alias_dir = tmp_path / "aliasdir"
        alias_dir.mkdir()
        symlinked_path = alias_dir / ".claudetrick"
        symlinked_path.symlink_to(marker_home / ".claude" / "code-review-markers")
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(f"printf x > {symlinked_path}/forged", agent_type="code-writer"),
                home=marker_home,
            )
            == "deny"
        )

    @pytest.mark.timing
    def test_many_target_tee_fanout_with_no_claude_mention_completes_quickly(self):
        """A `tee` fanout with dozens of targets and no `.claude` mention
        must stay near the fast-reject's cost, not scale with target count --
        pins the pre-filter that skips realpath resolution for candidates
        the fast-reject already rejected."""
        targets = " ".join(f"/tmp/marker-shape-perf-target-{i}.log" for i in range(60))
        started = time.monotonic()
        decision = run_hook(
            ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
            bash_input(f"echo x | tee {targets}", agent_type="code-writer"),
        )
        elapsed = time.monotonic() - started
        assert decision == "allow"
        assert elapsed < 1.0, (
            f"a 60-target tee fanout with no .claude mention took {elapsed:.2f}s -- "
            "should stay near the fast-reject's cost, not scale with target count"
        )

    @pytest.mark.timing
    def test_many_header_flag_curl_with_no_claude_mention_completes_quickly(self):
        """Unlike tee's branch above (still flag-filtered), the widened cp/
        mv/install/dd/sed/curl/wget/rsync/scp/openssl class emits every word
        of the fragment as a candidate, flags included -- so its candidate
        count scales with total argv word count instead of staying O(1). A
        curl invocation with 30 unrelated `-H header` flags exercises that
        wider path specifically, with no target ever mentioning `.claude`."""
        headers = " ".join(f'-H "X-Test-{i}: v{i}"' for i in range(30))
        started = time.monotonic()
        decision = run_hook(
            ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
            bash_input(f"curl {headers} https://example.invalid/payload", agent_type="code-writer"),
        )
        elapsed = time.monotonic() - started
        assert decision == "allow"
        assert elapsed < 1.0, (
            f"a 30-header curl invocation with no .claude mention took {elapsed:.2f}s -- "
            "should stay near the fast-reject's cost, not scale with argv word count"
        )

    def test_sed_absent_from_path_denied(self, isolated_home, tmp_path):
        """COMMAND_UNQUOTED's sed/tr strip is the earliest fork this scan
        reaches, run unconditionally ahead of Stage 1 for every Bash call.
        A missing sed must deny (fail-closed) rather than let
        _lib_strip_shell_quotes's failure silently clear COMMAND_UNQUOTED
        and fall through to this scan's normal no-match allow path with no
        bypass valve on a real marker write."""
        farm_dir = tmp_path / "path-without-sed"
        farm_dir.mkdir()
        restricted_path = build_path_without("sed", farm_dir)
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input("~/.claude/scripts/marker.sh write code-review"),
                home=isolated_home,
                extra_env={"PATH": restricted_path},
            )
            == "deny"
        )

    def test_redirect_candidates_split_sed_failure_denied(self, isolated_home, tmp_path):
        """GH-783: MARKER_WRITE_REDIRECT_CANDIDATES_EXIT must fail closed on
        its own, isolated from COMMAND_UNQUOTED_EXIT above -- both checks
        depend on the same sed binary, so a total sed-absent test (like the
        one above) can't tell which of the two is actually catching the
        failure. The command carries a genuine write construct (`>`) so
        _lib_command_has_write_construct's fast-reject does not skip
        _lib_split_fragments outright -- see
        test_no_write_construct_command_skips_fragment_splitting below for
        that case. A sed shim fails on any invocation that isn't
        _lib_strip_shell_quotes's own `-e`-flagged shape, so
        COMMAND_UNQUOTED succeeds via the real sed while the
        later _lib_split_fragments call inside _bash_marker_redirect_candidates
        (a bare `sed -E 's/.../g'`, no `-e` token) fails on its own."""
        real_sed = shutil.which("sed")
        assert real_sed, "test host must have a real sed binary on PATH"

        shim_dir = tmp_path / "sed-fails-outside-strip-shell-quotes-shape"
        shim_dir.mkdir()
        shim_script = textwrap.dedent(f"""\
            #!/bin/bash
            if [ "$2" != "-e" ]; then
              exit 1
            fi
            exec "{real_sed}" "$@"
        """)
        (shim_dir / "sed").write_text(shim_script)
        (shim_dir / "sed").chmod(0o755)

        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input("printf x > ~/.claude/code-review-markers/forged"),
                home=isolated_home,
                extra_env={"PATH": f"{shim_dir}{os.pathsep}{os.environ['PATH']}"},
            )
            == "deny"
        )

    def test_no_write_construct_command_skips_fragment_splitting(self, tmp_path):
        """A command with no >-family redirect and no _LIB_WRITE_UTILITIES
        invocation must never reach _lib_split_fragments inside
        _lib_redirect_candidates -- _lib_command_has_write_construct's
        fork-free fast-reject is what proves this, not just the eventual
        allow verdict, which a command that merely fails to match every
        SUFFIX_PATTERN would also produce. Reuses the PATH-shim-records-
        invocation technique from test_require_plan_review.py's
        test_historical_plans_allows_and_skips_the_realpath_fast_path: the
        shim records only a sed invocation whose pattern contains `&&`, the
        substring unique to _lib_split_fragments's own first internal sed
        call (`s/&&/\\n/g`) -- distinct from every other sed call this hook
        makes (TRIMMED's whitespace strip, _lib_strip_shell_quotes's
        `-e`-flagged calls) -- so an untouched marker file proves that
        specific fork never ran. Main session (no agent_type) so the
        gate-release-authority arm's own, independent _lib_split_fragments
        calls (which run for a no-gate-release agent regardless of write
        construct) don't also touch the marker and mask the result."""
        real_sed = shutil.which("sed")
        assert real_sed, "test host must have a real sed binary on PATH"

        call_marker = tmp_path / "split-fragments-was-called"
        shim_dir = tmp_path / "sed-records-fragment-split-calls"
        shim_dir.mkdir()
        shim_script = textwrap.dedent(f"""\
            #!/bin/bash
            case "$2" in
              *'&&'*) touch "{call_marker}" ;;
            esac
            exec "{real_sed}" "$@"
        """)
        (shim_dir / "sed").write_text(shim_script)
        (shim_dir / "sed").chmod(0o755)

        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input("git status"),
                extra_env={"PATH": f"{shim_dir}{os.pathsep}{os.environ['PATH']}"},
            )
            == "allow"
        )
        assert not call_marker.exists(), (
            "_lib_split_fragments must not run for a command with no write construct"
        )

    def test_hardlink_to_real_marker_file_denied(self, marker_home):
        """A hardlink placed inside the real markers directory is a distinct
        filesystem object from a plain new file at the same path -- pinning
        that the `-ef`-based directory-prefix match (_lib_shape_match's Pass
        2) denies it the same way, not just an ordinary Write. Scope note:
        this places the hardlink INSIDE the real markers directory (`-ef`-
        comparable via the directory-prefix check) rather than at a wholly
        unrelated path aliasing one specific marker file's inode -- the
        latter would require enumerating every real marker file to catch,
        an unbounded-cost operation this redesign deliberately does not add
        to the write-path gate (marker directories can hold tens of
        thousands of entries; see _lib_shape_match's own header, Pass 3's
        glob-metachar gate)."""
        real_marker = marker_home / ".claude" / "code-review-markers" / "existing-reviewed-marker"
        real_marker.write_text("deadbeef\n")
        hardlinked_path = marker_home / ".claude" / "code-review-markers" / "hardlinked-forged"
        os.link(real_marker, hardlinked_path)
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(f"printf x > {hardlinked_path}", agent_type="code-writer"),
                home=marker_home,
            )
            == "deny"
        )

    def test_full_tool_set_agent_may_write_through_a_hardlink_to_a_marker_file(self, marker_home):
        """The deny above is agent-scoped, not hardlink-scoped: an agent that
        could have run the review still passes the identical hardlink shape."""
        real_marker = marker_home / ".claude" / "code-review-markers" / "existing-reviewed-marker"
        real_marker.write_text("deadbeef\n")
        hardlinked_path = marker_home / ".claude" / "code-review-markers" / "hardlinked-forged"
        os.link(real_marker, hardlinked_path)
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(f"printf x > {hardlinked_path}", agent_type="general-purpose"),
                home=marker_home,
            )
            == "allow"
        )


class TestGateReleaseAuthorityGluedShortFlagBypassClosed:
    """Regression test for the same glued short-option bypass -- see
    TestGluedShortFlagBypassClosed in test_enforce_config_write_shape.py for
    the shared _lib_shape_match root cause and the curl/wget/openssl
    empirical verification of which of these three actually accept a glued
    (no space, no '=') short-option value. Parametrized across both a
    default (.claude-segment-present) and a .claude-segment-free
    CLAUDE_CONFIG_DIR to prove the fix isn't itself keyed off the '.claude'
    substring."""

    @pytest.fixture(params=[True, False], ids=["default-dotclaude-segment", "dotclaude-free-config-dir"])
    def config_dir_topology(self, request, tmp_path):
        home = tmp_path / "home"
        if request.param:
            (home / ".claude" / "code-review-markers").mkdir(parents=True)
            target = home / ".claude" / "code-review-markers" / "forged"
            extra_env = None
        else:
            config_dir = tmp_path / "profile"
            (config_dir / "code-review-markers").mkdir(parents=True)
            home.mkdir()
            target = config_dir / "code-review-markers" / "forged"
            extra_env = {"CLAUDE_CONFIG_DIR": str(config_dir)}
        return home, target, extra_env

    @pytest.mark.parametrize(
        "command_template",
        [
            "curl -sSo{target} https://example.invalid/payload",
            "wget -O{target} https://example.invalid/payload",
            "wget -qO{target} https://example.invalid/payload",
            "openssl enc -out{target} -in /tmp/attacker-plan.md",
        ],
        ids=["curl-sSo-glued", "wget-O-glued", "wget-qO-glued", "openssl-out-glued"],
    )
    def test_glued_flag_value_denied(self, command_template, config_dir_topology):
        home, target, extra_env = config_dir_topology
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(command_template.format(target=target), agent_type="code-writer"),
                home=home,
                extra_env=extra_env,
            )
            == "deny"
        )


class TestGateReleaseAuthorityWriteUtilitiesAllowUnrelatedDestinations:
    """Mirrors TestGateReleaseAuthorityBashRedirectAndUtility's deny-path
    parametrization, pointed at a destination with no relationship to any
    marker/active-bypass path -- cp/dd/sed/tee already have this allow-path
    coverage elsewhere in that class (e.g.
    test_claude_mention_in_a_non_target_argument_allowed); rsync/scp/openssl
    had none."""

    @pytest.mark.parametrize(
        "command_template",
        [
            "rsync /tmp/attacker-plan.md {target}",
            "scp /tmp/attacker-plan.md localhost:{target}",
            "openssl enc -out {target} -in /tmp/attacker-plan.md",
        ],
        ids=["rsync", "scp", "openssl"],
    )
    def test_explicit_destination_token_to_unrelated_file_allowed(self, command_template, tmp_path):
        home = tmp_path / "home"
        (home / ".claude" / "code-review-markers").mkdir(parents=True)
        target = home / ".claude" / "some-other-file.md"
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(command_template.format(target=target), agent_type="code-writer"),
                home=home,
            )
            == "allow"
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

    def test_case_varied_path_denied(self, marker_home):
        """macOS's default APFS volume is case-insensitive, so a case-varied
        marker path (`.Claude` instead of `.claude`) resolves to the same
        on-disk file this arm's shape pattern would otherwise miss."""
        case_varied = str(marker_home / ".Claude/code-review-markers/deadbeef.session")
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                write_input(case_varied, agent_type="code-writer"),
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

    @pytest.mark.timing
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


class TestGateReleaseAuthorityUnderCustomConfigDir:
    """The `.claude`-shape arm above assumes marker state always sits under a
    `.claude` path segment, true for the default $HOME/.claude resolution but
    not for a CLAUDE_CONFIG_DIR value with none (e.g. an account container
    under ~/.config/) — marker.sh still resolves and writes there via
    _lib_config_dir(), so the gate must too, or a no-gate-release agent can
    forge a passing review marker undetected."""

    @pytest.fixture
    def custom_config_dir(self, tmp_path):
        config_dir = tmp_path / "profile-container"
        (config_dir / "code-review-markers").mkdir(parents=True)
        return config_dir

    @pytest.mark.parametrize("agent_type", NO_GATE_RELEASE_AGENTS)
    def test_marker_write_denied_under_config_dir_with_no_dotclaude_segment(
        self, custom_config_dir, agent_type, tmp_path
    ):
        home = tmp_path / "home"
        home.mkdir()
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                write_input(
                    str(custom_config_dir / "code-review-markers/deadbeef.session"),
                    agent_type=agent_type,
                ),
                home=home,
                extra_env={"CLAUDE_CONFIG_DIR": str(custom_config_dir)},
            )
            == "deny"
        )

    def test_main_session_write_still_allowed_under_config_dir_with_no_dotclaude_segment(
        self, custom_config_dir, tmp_path
    ):
        home = tmp_path / "home"
        home.mkdir()
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                write_input(str(custom_config_dir / "code-review-markers/deadbeef.session")),
                home=home,
                extra_env={"CLAUDE_CONFIG_DIR": str(custom_config_dir)},
            )
            == "allow"
        )

    def test_unrelated_file_write_under_config_dir_not_denied(self, custom_config_dir, tmp_path):
        """The config-dir-resolved arm must stay shape-scoped the same as the
        .claude arm — it must not turn every write under the config dir into
        a gate-release-authority match."""
        home = tmp_path / "home"
        home.mkdir()
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                write_input(
                    str(custom_config_dir / "some-unrelated-file.txt"),
                    agent_type="code-writer",
                ),
                home=home,
                extra_env={"CLAUDE_CONFIG_DIR": str(custom_config_dir)},
            )
            == "allow"
        )

    @pytest.mark.parametrize("agent_type", NO_GATE_RELEASE_AGENTS)
    def test_marker_write_denied_when_config_dir_unresolvable(self, agent_type, tmp_path):
        """CLAUDE_CONFIG_DIR set to a relative value makes _lib_config_dir()
        fail — the arm must deny rather than silently skip, matching this
        file's declared fail-closed posture and the sibling gate hooks that
        deny on the identical resolver failure."""
        home = tmp_path / "home"
        home.mkdir()
        target = tmp_path / "somewhere" / "code-review-markers" / "deadbeef.session"
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                write_input(str(target), agent_type=agent_type),
                home=home,
                extra_env={"CLAUDE_CONFIG_DIR": "relative-profile"},
            )
            == "deny"
        )

    @pytest.mark.parametrize("agent_type", NO_GATE_RELEASE_AGENTS)
    def test_marker_write_denied_when_config_dir_is_a_symlink(self, agent_type, tmp_path):
        """_lib_config_dir() returns CLAUDE_CONFIG_DIR verbatim, not
        realpath-normalized — a symlink/stow-fold alias of a config dir with
        no `.claude` segment must not evade this arm the way it would evade
        the .claude-shape arm above."""
        home = tmp_path / "home"
        home.mkdir()
        physical = tmp_path / "physical-profile"
        (physical / "code-review-markers").mkdir(parents=True)
        symlinked = tmp_path / "symlinked-profile"
        symlinked.symlink_to(physical)
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                write_input(
                    str(physical / "code-review-markers/deadbeef.session"),
                    agent_type=agent_type,
                ),
                home=home,
                extra_env={"CLAUDE_CONFIG_DIR": str(symlinked)},
            )
            == "deny"
        )


class TestGateReleaseAuthorityBashArmConfigDirSubstringGapClosed:
    """A config-dir-resolved marker write with no literal `.claude`
    substring in the command is scanned via the fork-free write-construct
    fast-reject, independent of the `.claude`-substring pre-filters used
    elsewhere in this file. Pinned here so a future change to those
    pre-filters doesn't silently reopen this gap."""

    def test_redirect_to_config_dir_marker_path_denied(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        config_dir = tmp_path / "profile-container"
        (config_dir / "code-review-markers").mkdir(parents=True)
        target = config_dir / "code-review-markers" / "forged"
        # A `.claude` mention elsewhere in the same command is no longer
        # load-bearing for this scan (there is no more per-candidate
        # `.claude`-substring filter to satisfy) — kept anyway to show the
        # deny does not depend on it.
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(f"echo x > {target} && ls ~/.claude", agent_type="code-writer"),
                home=home,
                extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)},
            )
            == "deny"
        )


class TestGateReleaseAuthorityBashArmConfigDirShapeHasNoBudgetCliff:
    """The prior realpath-budget design bounded per-fire cost by shape-testing
    only the raw tilde-expanded form (no realpath) past the first
    MARKER_WRITE_REALPATH_BUDGET=10 `.claude`-mentioning candidates in one
    command — a documented, narrow degrade. The `-ef`-based redesign has no
    such budget (each candidate's cost is a handful of in-process stat calls,
    not a capped subprocess), so a config-dir-shape write must still deny
    even as the 101st candidate in one command, proving there's no cliff to
    fall off of."""

    def test_config_dir_shape_denied_as_the_101st_candidate(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        config_dir = tmp_path / "backup.claude-profile"
        (config_dir / "code-review-markers").mkdir(parents=True)
        forged = config_dir / "code-review-markers" / "forged"
        padding = " ".join(f"/tmp/marker-shape-pad-{i}" for i in range(100))
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(f"tee {padding} {forged}", agent_type="code-writer"),
                home=home,
                extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)},
            )
            == "deny"
        )

    def test_full_tool_set_agent_allowed_for_same_shape_as_the_101st_candidate(self, tmp_path):
        """The deny above is agent-scoped, not shape-scoped: an agent that
        could have run the review still passes the identical shape."""
        home = tmp_path / "home"
        home.mkdir()
        config_dir = tmp_path / "backup.claude-profile"
        (config_dir / "code-review-markers").mkdir(parents=True)
        forged = config_dir / "code-review-markers" / "forged"
        padding = " ".join(f"/tmp/marker-shape-pad-{i}" for i in range(100))
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(f"tee {padding} {forged}", agent_type="general-purpose"),
                home=home,
                extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)},
            )
            == "allow"
        )


class TestGateReleaseAuthorityBraceExpansionBypassClosed:
    """HIGH-severity review finding: bash brace-expands
    `pre{a,b}post` into one word per component at PARSE time, but
    _lib_fragment_candidates only ever sees a fragment's words AFTER
    runtime word-splitting (`for word in $fragment`), which bash never
    re-brace-expands -- so an unexpanded `{,x}`-shaped literal token
    reached _lib_shape_match unmatched, even though the real shell that
    executes the command DOES brace-expand it into the real marker-
    directory target. `_lib.sh`'s `_lib_command_has_brace_expansion_bypass`
    closes this by detecting the construct's shape on the whole command
    text and denying outright, rather than expanding it into candidates --
    see test_lib.py's own `_lib_redirect_candidates` brace-expansion-bypass
    tests for the shared predicate's full shape matrix; this class pins only
    this hook's own wiring of that shared mechanism."""

    def test_comma_list_glued_to_a_marker_directory_denied(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        (home / ".claude" / "code-review-markers").mkdir(parents=True)
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(
                    "tee ~/.claude/code-review-markers{,x}/forged", agent_type="code-writer"
                ),
                home=home,
            )
            == "deny"
        )

    def test_full_tool_set_agent_also_denied_for_the_same_brace_shape(self, tmp_path):
        """Unlike every other Bash-arm candidate check in this file, the
        brace-expansion-bypass predicate runs inside _lib_redirect_candidates
        itself, ahead of the per-candidate no-gate-release check, so it
        denies before the agent-type distinction is ever reached -- even an
        agent that could have run the review is denied for this shape."""
        home = tmp_path / "home"
        home.mkdir()
        (home / ".claude" / "code-review-markers").mkdir(parents=True)
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(
                    "tee ~/.claude/code-review-markers{,x}/forged", agent_type="general-purpose"
                ),
                home=home,
            )
            == "deny"
        )

    def test_quoted_space_containing_component_denied(self, tmp_path):
        """Testing a post-quote-strip, post-word-split candidate would let a
        quoted, space-containing brace component get re-split by `for word
        in $fragment` into two half-tokens, neither carrying a complete
        `{`...`}` pair. Testing the whole command text instead (after
        quote-stripping, before fragment-splitting/word-splitting) avoids
        this."""
        home = tmp_path / "home"
        home.mkdir()
        (home / ".claude" / "code-review-markers").mkdir(parents=True)
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(
                    'tee ~/.claude/code-review-markers/{"a b",forged}', agent_type="general-purpose"
                ),
                home=home,
            )
            == "deny"
        )

    def test_denial_message_names_the_brace_expansion_reason(self, tmp_path):
        """Distinguishes this deny from the generic "could not split the
        command into fragments" message a real sed/fragment-splitting
        failure would produce -- both share a non-zero
        _lib_redirect_candidates status, so the caller's status-check must
        branch on _LIB_BRACE_EXPANSION_BYPASS_STATUS specifically rather
        than falling into the generic branch."""
        home = tmp_path / "home"
        home.mkdir()
        (home / ".claude" / "code-review-markers").mkdir(parents=True)
        reason = run_hook_reason(
            ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
            bash_input("tee ~/.claude/code-review-markers{,x}/forged", agent_type="code-writer"),
            home=home,
        )
        assert reason is not None
        assert "brace-expansion" in reason

    def test_dollar_brace_shaped_command_denied(self, tmp_path):
        """A `${...}` parameter expansion whose body has a `,` (`${x:-a,b}`)
        denies, because real bash cannot distinguish "just a parameter
        expansion" from "a parameter expansion that also brace-expands"
        from command text alone. Writes to `/tmp/x`, not a marker path, so
        the deny is attributable only to the brace-bypass status, not to an
        actual marker-path candidate match."""
        home = tmp_path / "home"
        home.mkdir()
        (home / ".claude" / "code-review-markers").mkdir(parents=True)
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input("tee /tmp/x ${x:-a,b}", agent_type="code-writer"),
                home=home,
            )
            == "deny"
        )

    def test_ordinary_dollar_brace_command_still_allowed(self, tmp_path):
        """Control for the test above -- a solitary `${x}` parameter
        expansion with no comma or `..` in its body is not a brace-
        expansion construct at all, so it allows. Writes to `/tmp/x`, not a
        marker path, so this is a genuine allow control rather than a
        marker-write deny for an unrelated reason."""
        home = tmp_path / "home"
        home.mkdir()
        (home / ".claude" / "code-review-markers").mkdir(parents=True)
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input("tee /tmp/x ${x}", agent_type="code-writer"),
                home=home,
            )
            == "allow"
        )


class TestPrescriptionAllowlistAlignment:
    """Every tilde-form marker.sh (subcommand, argument) shape the hook
    accepts must have a matching permissions.allow entry, except a fixed,
    literal exception set — not "any shape the hook happens to decline",
    which would silently re-grant a future excluded shape. Absolute-path
    forms are out of scope: every existing and proposed permissions.allow
    rule is tilde-only by convention, even though the hook's MARKER_SHAPE
    regex also accepts an absolute-path prefix.
    """

    # clear-stale sweeps every session's dead-PID bypass markers machine-wide
    # (not just this session's) and is ungated by the hook's no-gate-release
    # check — a broader blast radius than a per-session marker write, so it
    # is excluded from permissions.allow even though CLAUDE.md prescribes it
    # and the hook accepts it.
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
        shape the hook happens to exclude at any given time — an open-ended
        "any excluded shape" clause would silently re-grant a future
        disqualified shape instead of failing this test."""
        assert {"clear-stale", "clear-stale --dry-run"} == self.ALLOWLIST_EXCEPTIONS
