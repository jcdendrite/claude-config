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
    SCRIPTS_DIR,
    build_path_without,
    run_hook,
    run_hook_reason,
)
from helpers import bash_input as _bash_input
from helpers import edit_input as _edit_input
from helpers import multiedit_input as _multiedit_input
from helpers import write_input as _write_input

from .conftest import _review_ledger_path, _seed_session

ENFORCE_MARKER_SCRIPT_SHAPE_HOOK = HOOKS_DIR / "enforce-marker-script-shape.sh"


def _marker_write_realpath_budget() -> int:
    """The hook's own budget, parsed from its source so a budget change cannot
    leave a past-the-budget test inside the budget."""
    match = re.search(
        r"^\s*MARKER_WRITE_REALPATH_BUDGET=(\d+)\s*$",
        ENFORCE_MARKER_SCRIPT_SHAPE_HOOK.read_text(),
        re.MULTILINE,
    )
    assert match, "MARKER_WRITE_REALPATH_BUDGET assignment not found in the hook"
    return int(match.group(1))


def _past_realpath_budget_padding() -> str:
    """`tee` targets that each spend one unit of the budget, one more than it
    holds, so a target listed after them gets no realpath-normalized form."""
    return " ".join(f"~/.claude/pad{i}" for i in range(_marker_write_realpath_budget() + 1))


# The harness sets `agent_id` only inside a subagent call, and sets
# `agent_type` for a subagent and for a main session started with `--agent`.
SUBAGENT_ID = "agent-0123456789abcdef"
_DERIVE_AGENT_ID = object()


def _with_agent_id(build_payload):
    """Wrap a payload builder so a payload naming a non-empty `agent_type`
    also carries `agent_id`, modelling a subagent call. Pass `agent_id=None`
    to model a main session started with `--agent`, or an explicit string to
    model a subagent with no `agent_type`."""

    def build(*args, agent_id=_DERIVE_AGENT_ID, **kwargs):
        payload = build_payload(*args, **kwargs)
        if agent_id is _DERIVE_AGENT_ID:
            agent_id = SUBAGENT_ID if payload.get("agent_type") else None
        if agent_id is not None:
            payload["agent_id"] = agent_id
        return payload

    return build


bash_input = _with_agent_id(_bash_input)
edit_input = _with_agent_id(_edit_input)
multiedit_input = _with_agent_id(_multiedit_input)
write_input = _with_agent_id(_write_input)

# The 22 single-command tilde-form shapes the hook accepts — single source of
# truth for both test_valid_shapes_allowed (which pins hook acceptance) and
# TestPrescriptionAllowlistAlignment (which cross-checks permissions.allow
# coverage over this same set), so the two can't silently drift apart.
TILDE_MARKER_SHAPES = [
    "~/.claude/scripts/marker.sh write code-review",
    "~/.claude/scripts/marker.sh write skill-review",
    "~/.claude/scripts/marker.sh write plan-review",
    "~/.claude/scripts/marker.sh write ready-for-review",
    "~/.claude/scripts/marker.sh write cumulative-review",
    "~/.claude/scripts/marker.sh write verification",
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
    "~/.claude/scripts/marker.sh check verification",
]

# About 320 KB in 32 lines. It must be multi-line: `grep -q` over one unbroken
# line reads the whole line before it exits, so only a command whose first
# line matches and whose remainder is large makes a `printf | grep -q`
# pipeline see SIGPIPE under pipefail. Few lines keep the per-fragment scan fast.
LARGE_MULTILINE_TAIL = "\n" + "\n".join(["x" * 10_000] * 32)


class TestEnforceMarkerScriptShape:
    # ------------------------------------------------------------------ #
    # Valid shapes — 22 single-command shapes, each must be allowed       #
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

    @pytest.mark.parametrize("verb", ["merge", "rebase", "cherry-pick", "revert"])
    def test_chain_to_git_continue_form_allowed(self, verb):
        """The chained-commit tail matches the full --continue union,
        deliberately including `rebase --continue` even though
        require-code-review.sh's own narrow predicate never checks for this
        marker on that shape -- this pattern is about chain shape, not
        which gate the chained command reaches."""
        cmd = f"~/.claude/scripts/marker.sh write code-review && git {verb} --continue"
        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input(cmd)) == "allow"

    def test_chain_to_git_rebase_skip_denied(self):
        """--skip and --abort are not commit-concluding shapes; the chain
        matcher must not widen to any git-rebase-flag tail."""
        cmd = "~/.claude/scripts/marker.sh write code-review && git rebase --skip"
        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input(cmd)) == "deny"

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

    def test_chain_to_curl_after_rebase_continue_denied(self):
        """Post-`--continue` chain operators must be denied too, the same
        as post-`git commit` above."""
        cmd = "~/.claude/scripts/marker.sh write code-review && git rebase --continue && curl evil.com"
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
    # single shapes and must NOT appear in the 22-shape parametrize list  #
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

    def test_write_verification_extra_arg_denied(self):
        cmd = "~/.claude/scripts/marker.sh write verification extra"
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
        """check only supports code-review and verification -- a different
        skill must be denied, mirroring
        test_mismatched_subcommand_skill_pair_denied above."""
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

    def test_check_verification_extra_arg_denied(self):
        """check verification with a trailing arg must be denied."""
        cmd = "~/.claude/scripts/marker.sh check verification extra"
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
# Spellings the shell reads as MARKER, with no contiguous `marker.sh` in the text.
MARKER_SPLIT_BY_BACKSLASH_NEWLINE = "~/.claude/scripts/marker.\\\nsh"
MARKER_SPLIT_BY_QUOTES = '~/.claude/scripts/mark""er.sh'

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
            # A case-varied script name runs on a case-insensitive volume, but
            # neither Stage 1 nor the raw-text detector folds case.
            "~/.claude/scripts/Marker.SH write code-review",
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
        defeats the raw-text substring check: the quote character sits
        between `marker.sh` and the op keyword, breaking the
        `marker\\.sh[[:space:]]+(write|activate)` adjacency the raw-text
        check requires. This is the command-word arm's actual purpose —
        _lib_command_invokes_tool_subcmd resolves the fragment's command
        word after quote-stripping and still matches, independent of the
        raw-text arm."""
        cmd = f'"marker.sh" {op} {target}'
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
        block never runs for this agent type, and Stage 2's anchor, which
        reads raw unstripped $COMMAND, never recognizes a quote-split
        `"marker.sh"` prefix as a marker.sh invocation shape at all — but
        the agent-authority scoping is what this test pins.)"""
        cmd = '"marker.sh" write code-review'
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(cmd, agent_type=agent_type),
            )
            == "allow"
        )

    def test_sed_absent_from_path_denies(self, tmp_path):
        """Status-2 propagation, fail-closed: with sed/tr unavailable, a
        no-gate-release agent's marker.sh write attempt still denies.

        The observed deny is reached via this hook's earlier, unconditional
        MARKER_WRITE_COMMAND_UNQUOTED quote-strip check (runs before Stage
        1, for every Bash call, not only marker.sh-shaped ones) rather than
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
        this hook's earlier, unconditional MARKER_WRITE_COMMAND_UNQUOTED
        quote-strip check and so never actually isolates this arm.

        The command here names an op (`status`) neither raw-text pass
        matches, since the quote-stripped command would match a quote-split
        `write`, and contains no `.claude`
        substring, which skips the pre-Stage-1 redirect-candidate scan (it
        requires a literal `.claude` to run at all). A sed shim that
        succeeds only for _lib_strip_shell_quotes's own `-e`-flagged
        invocation shape and fails for _lib_split_fragments's differently-
        shaped call (the same technique test_fragments_split_sed_failure_
        denied in test_deny_private_project_refs.py and
        test_redirect_candidates_split_sed_failure_denied above both use)
        lets MARKER_WRITE_COMMAND_UNQUOTED's own `-e`-shaped strip succeed
        via the real sed while _lib_command_invokes_tool_subcmd's internal
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
            bash_input('"marker.sh" status', agent_type="code-writer"),
            extra_env={"PATH": f"{shim_dir}{os.pathsep}{os.environ['PATH']}"},
        )
        assert reason is not None
        assert "could not determine whether" in reason
        assert "invokes marker.sh write/activate" in reason


class TestGateReleaseAuthorityBashRedirectAndUtility:
    """The pre-Stage-1 scan: a Bash write to a marker path via a redirect or
    write utility is caught even though the command never mentions
    `marker.sh`. Covers the closed half of the gap `_marker_shape_match`
    documents as still open (python3 -c, here-doc bodies, $(...)-computed
    paths, shell-function indirection).
    """

    @pytest.fixture
    def marker_home(self, tmp_path):
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
            # The per-fragment utility guard folds case and accepts a path
            # component, as the command-word check it fronts does.
            "CP /tmp/attacker-plan.md ~/.claude/code-review-markers/forged",
            "echo x | /usr/bin/tee -a ~/.claude/code-review-markers/forged",
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

    @pytest.mark.parametrize("utility", ["cp", "mv", "install", "ln", "link"])
    def test_directory_copy_into_the_bare_marker_directory_denied(self, utility):
        """The destination names the directory itself, with no file component:
        the utility writes the file inside it."""
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(
                    f"{utility} /tmp/attacker-plan.md ~/.claude/code-review-markers",
                    agent_type="code-writer",
                ),
            )
            == "deny"
        )

    @pytest.mark.parametrize("utility", ["cp", "mv", "install", "ln", "link"])
    def test_full_tool_set_agent_may_copy_into_the_bare_marker_directory(self, utility):
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(
                    f"{utility} /tmp/attacker-plan.md ~/.claude/code-review-markers",
                    agent_type="general-purpose",
                ),
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
        nested loop's no-realpath fallback has to hold
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
        command, but only the extracted write-target word is shape-tested --
        a `.claude`-rooted source with a non-marker destination must not
        deny."""
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
            "ln -s -t ~/.claude/code-review-markers /tmp/attacker-plan.md",
            "ln -s --target-directory=~/.claude/code-review-markers /tmp/attacker-plan.md",
        ],
    )
    def test_target_directory_form_allowed_residual(self, command):
        """Accepted residual: the destination isn't the last argument for
        `-t DIR`/`--target-directory=DIR`, so the last-argument heuristic
        misses it."""
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(command, agent_type="code-writer"),
            )
            == "allow"
        )

    @pytest.mark.parametrize("agent_type", ["code-writer", "general-purpose"])
    def test_hard_link_into_the_ledger_directory_with_target_directory_allowed_residual(self, agent_type):
        """Accepted residual: `ln -f -t <ledger directory> <file>` replaces a
        ledger file with a hard link to a file written elsewhere. The `-t`
        form puts the destination before the last argument, so the scan never
        sees the ledger directory, and review-ledger.sh accepts the result
        because a hard link is a regular file. Invert this to deny if the
        `-t` forms are ever extracted."""
        command = "ln -f -t ~/.claude/review-narrative-ledger ./forged-ledger.jsonl"
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(command, agent_type=agent_type),
            )
            == "allow"
        )

    def test_cp_target_directory_with_the_ledger_directory_as_its_last_argument_denied_as_over_emission(self):
        """Accepted over-emission: the last argument is classified as a
        destination directory, so `cp -t DIR <ledger directory>` is denied for
        a subagent although the ledger directory is only a source. Invert this
        if the last-argument rule is ever narrowed to real destinations."""
        command = "cp -t /tmp/copies ~/.claude/review-narrative-ledger"
        reason = run_hook_reason(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input(command, agent_type="general-purpose"))
        assert reason is not None
        assert "review-ledger state" in reason

    def test_ln_target_directory_with_the_ledger_directory_as_its_last_argument_denied_as_over_emission(self):
        """Accepted over-emission, as for `cp -t DIR <ledger directory>`."""
        command = "ln -s -t /tmp/links ~/.claude/review-narrative-ledger"
        reason = run_hook_reason(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input(command, agent_type="general-purpose"))
        assert reason is not None
        assert "review-ledger state" in reason

    def test_install_d_of_the_ledger_directory_denied_as_over_emission(self):
        """Accepted over-emission, as for `cp -t DIR <ledger directory>`:
        `install -d` makes its last argument a directory."""
        command = "install -d ~/.claude/review-narrative-ledger"
        reason = run_hook_reason(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input(command, agent_type="general-purpose"))
        assert reason is not None
        assert "review-ledger state" in reason

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

    @pytest.mark.parametrize(
        "state_directory_name",
        ["code-review-markers", "review-narrative-ledger"],
        ids=["markers-directory", "ledger-directory"],
    )
    def test_symlink_with_no_claude_in_its_own_path_allowed_residual(
        self, marker_home, tmp_path, state_directory_name
    ):
        """Accepted residual: this scan's fast-reject requires the literal
        `.claude` or the ledger directory name in the command text, unlike the
        Write/Edit arm's unconditional realpath resolution -- a symlink whose
        own path carries neither but resolves into the markers or the ledger
        directory is not caught here."""
        state_directory = marker_home / ".claude" / state_directory_name
        state_directory.mkdir(exist_ok=True)
        alias_dir = tmp_path / "aliasdir"
        alias_dir.mkdir()
        symlinked_path = alias_dir / "notclaudepath"
        symlinked_path.symlink_to(state_directory)
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(f"printf x > {symlinked_path}/forged", agent_type="code-writer"),
                home=marker_home,
            )
            == "allow"
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

    def test_sed_absent_from_path_denied(self, isolated_home, tmp_path):
        """MARKER_WRITE_COMMAND_UNQUOTED's sed/tr strip is the earliest fork
        this scan reaches, run unconditionally ahead of Stage 1 for every
        Bash call. A missing sed must deny (fail-closed) rather than let
        _lib_strip_shell_quotes's failure silently clear
        MARKER_WRITE_COMMAND_UNQUOTED and fall through to this scan's normal
        no-match allow path with no bypass valve on a real marker write."""
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
        its own, isolated from MARKER_WRITE_COMMAND_UNQUOTED_EXIT above --
        both checks depend on the same sed binary, so a total sed-absent test
        (like the one above) can't tell which of the two is actually catching
        the failure. A sed shim fails on any invocation that isn't
        _lib_strip_shell_quotes's own `-e`-flagged shape, so
        MARKER_WRITE_COMMAND_UNQUOTED succeeds via the real sed while the
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
                bash_input("~/.claude/scripts/marker.sh write code-review"),
                home=isolated_home,
                extra_env={"PATH": f"{shim_dir}{os.pathsep}{os.environ['PATH']}"},
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


class TestGateReleaseAuthorityBashArmConfigDirResidual:
    """The Bash redirect/utility arm shares `_marker_shape_match` with the
    Write/Edit/MultiEdit arm above, but its two `.claude`-substring
    pre-filters (Stage 0 and `_marker_write_candidate_mentions_claude`) run
    before `_marker_shape_match` is ever reached, so a config-dir-resolved
    marker write with no literal `.claude` substring anywhere in the command
    is never scanned — a named, accepted residual (see the hook's own header),
    not a bug to chase. Pinned here so a future change to either pre-filter
    doesn't silently assume this case is already covered."""

    def test_redirect_to_config_dir_marker_path_allowed_is_a_named_residual(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        config_dir = tmp_path / "profile-container"
        (config_dir / "code-review-markers").mkdir(parents=True)
        target = config_dir / "code-review-markers" / "forged"
        # Even with a `.claude` mention elsewhere in the same command, the
        # per-candidate filter still rejects `target` itself for lacking the
        # literal substring — confirming the gap is the candidate-level
        # filter, not merely the command-level Stage 0 one.
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(f"echo x > {target} && ls ~/.claude", agent_type="code-writer"),
                home=home,
                extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)},
            )
            == "allow"
        )

    @pytest.mark.parametrize(
        "command",
        [
            "printf x > $CLAUDE_CONFIG_DIR/code-review-markers/deadbeef",
            # The per-candidate filter drops the target even when `.claude`
            # appears elsewhere in the command.
            "printf x > $CLAUDE_CONFIG_DIR/code-review-markers/deadbeef && ls ~/.claude",
        ],
        ids=["no-dotclaude-anywhere", "dotclaude-elsewhere"],
    )
    def test_redirect_through_the_config_dir_variable_to_a_marker_path_allowed_residual(
        self, tmp_path, command
    ):
        """The leading `$CLAUDE_CONFIG_DIR` rewrite runs inside
        `_marker_shape_match`, after the pre-filter, so it is effective for
        ledger targets only. Invert this to deny if the pre-filter ever admits
        a candidate that names the variable."""
        home = tmp_path / "home"
        home.mkdir()
        config_dir = tmp_path / "profile-container"
        (config_dir / "code-review-markers").mkdir(parents=True)
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(command, agent_type="code-writer"),
                home=home,
                extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)},
            )
            == "allow"
        )


class TestGateReleaseAuthorityBashArmConfigDirShapeSurvivesBudgetExhaustion:
    """_lib_config_dir is subprocess-free, so _marker_shape_match resolves it
    unconditionally regardless of the realpath budget — only the follow-on
    realpath call (the one with real subprocess cost) is budget-gated. A
    config-dir-shape write must still deny even when it is the 11th
    `.claude`-mentioning candidate in one command (past
    MARKER_WRITE_REALPATH_BUDGET=10), the same way the $HOME-relative shape
    already degrades to raw-candidate-only rather than dropping coverage
    entirely once the budget is spent."""

    def test_config_dir_shape_denied_past_realpath_budget(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        # Contains the literal substring ".claude" (so the per-candidate
        # `.claude`-mention filter passes) without matching the $HOME-relative
        # `*/.claude/*-markers/*` glob shape as a real path segment — only the
        # config-dir-resolved shape check can catch this one.
        config_dir = tmp_path / "backup.claude-profile"
        (config_dir / "code-review-markers").mkdir(parents=True)
        forged = config_dir / "code-review-markers" / "forged"
        padding = " ".join(f"~/.claude/pad{i}" for i in range(11))
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(f"tee {padding} {forged}", agent_type="code-writer"),
                home=home,
                extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)},
            )
            == "deny"
        )

    def test_full_tool_set_agent_allowed_for_same_shape_past_realpath_budget(self, tmp_path):
        """The deny above is agent-scoped, not shape-scoped: an agent that
        could have run the review still passes the identical shape."""
        home = tmp_path / "home"
        home.mkdir()
        config_dir = tmp_path / "backup.claude-profile"
        (config_dir / "code-review-markers").mkdir(parents=True)
        forged = config_dir / "code-review-markers" / "forged"
        padding = " ".join(f"~/.claude/pad{i}" for i in range(11))
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(f"tee {padding} {forged}", agent_type="general-purpose"),
                home=home,
                extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)},
            )
            == "allow"
        )

    @pytest.mark.parametrize("agent_type", ["code-writer", "general-purpose"])
    def test_config_dir_ledger_shape_denied_past_realpath_budget(self, tmp_path, agent_type):
        """The ledger twin: with the realpath budget spent, the target is
        tested only against its raw text, so only the resolved-config-dir
        ledger pattern can match a config dir with no `.claude` segment."""
        home = tmp_path / "home"
        home.mkdir()
        config_dir = tmp_path / "profile-container"
        ledger_target = config_dir / "review-narrative-ledger" / "x.jsonl"
        padding = " ".join(f"~/.claude/pad{i}" for i in range(11))
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(f"tee {padding} {ledger_target}", agent_type=agent_type),
                home=home,
                extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)},
            )
            == "deny"
        )

    def test_main_session_allowed_for_the_same_ledger_shape_past_realpath_budget(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        config_dir = tmp_path / "profile-container"
        ledger_target = config_dir / "review-narrative-ledger" / "x.jsonl"
        padding = " ".join(f"~/.claude/pad{i}" for i in range(11))
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(f"tee {padding} {ledger_target}"),
                home=home,
                extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)},
            )
            == "allow"
        )


class TestGateReleaseRawDetectorOnQuotedAndLargeCommands:
    """The marker.sh Bash arm's detectors must not depend on quoting or on
    pipeline exit status. A multi-line command over the pipe buffer makes a
    `printf | grep -q` pipeline return 141 under pipefail, which would read as
    no match."""

    @pytest.mark.parametrize("agent_type", NO_GATE_RELEASE_AGENTS)
    @pytest.mark.parametrize(
        "command",
        [
            f"bash -c '{MARKER} write code-review'" + LARGE_MULTILINE_TAIL,
            "marker.sh write code-review" + LARGE_MULTILINE_TAIL,
            # Stage 0's `.claude` fast-reject on a large command whose first line
            # names a marker path.
            "printf x > ~/.claude/code-review-markers/forged" + LARGE_MULTILINE_TAIL,
        ],
        ids=["bash-c-wrapper", "bare-script-name", "redirect-to-marker-path"],
    )
    def test_roster_agent_denied_for_a_large_multiline_command(self, agent_type, command):
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(command, agent_type=agent_type),
            )
            == "deny"
        )

    @pytest.mark.parametrize("agent_type", NO_GATE_RELEASE_AGENTS)
    @pytest.mark.parametrize(
        "command",
        [
            # A quote between script and op breaks the raw-text adjacency, and
            # the `bash -c` wrapper hides the command word from the other detector.
            f"bash -c '{MARKER} \"write\" code-review'",
            f"bash -c '{MARKER} \"activate\" plan-review'",
        ],
        ids=["quoted-write", "quoted-activate"],
    )
    def test_roster_agent_denied_for_a_quote_split_op_inside_a_wrapper(self, agent_type, command):
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(command, agent_type=agent_type),
            )
            == "deny"
        )

    # The script name never appears contiguously in the raw text, so only the
    # quote-stripped, backslash-newline-joined text names it at Stage 1.
    SCRIPT_NAME_SPLIT_MARKER_WRITES = [
        f"{MARKER_SPLIT_BY_BACKSLASH_NEWLINE} write code-review",
        f"{MARKER_SPLIT_BY_QUOTES} write code-review",
        f"bash -c '{MARKER_SPLIT_BY_BACKSLASH_NEWLINE} write code-review'",
        f"bash -c '{MARKER_SPLIT_BY_QUOTES} write code-review'",
    ]
    SCRIPT_NAME_SPLIT_MARKER_WRITE_IDS = [
        "backslash-newline-in-name",
        "quote-in-name",
        "backslash-newline-in-name-in-wrapper",
        "quote-in-name-in-wrapper",
    ]

    @pytest.mark.parametrize("agent_type", NO_GATE_RELEASE_AGENTS)
    @pytest.mark.parametrize(
        "command",
        SCRIPT_NAME_SPLIT_MARKER_WRITES,
        ids=SCRIPT_NAME_SPLIT_MARKER_WRITE_IDS,
    )
    def test_roster_agent_denied_for_a_script_name_split_by_a_quote_or_a_continuation(
        self, agent_type, command
    ):
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(command, agent_type=agent_type),
            )
            == "deny"
        )

    @pytest.mark.parametrize("agent_type", GATE_RELEASE_ALLOWED_AGENTS)
    @pytest.mark.parametrize(
        "command",
        SCRIPT_NAME_SPLIT_MARKER_WRITES,
        ids=SCRIPT_NAME_SPLIT_MARKER_WRITE_IDS,
    )
    def test_full_tool_set_agent_allowed_for_the_same_split_script_name(self, agent_type, command):
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(command, agent_type=agent_type),
            )
            == "allow"
        )

    # The quote-stripped copy reads a regex-escaped `marker\.sh` as the script
    # name, but the raw text never names it, so the raw-text traversal guard
    # has no marker.sh invocation to protect. The `..` segment is the trigger.
    ESCAPED_NAME_PATTERN_WITH_PARENT_SEGMENT = [
        "grep -rn 'marker\\.sh' claude/.claude/hooks/../scripts",
        "grep -rn 'marker\\.sh' claude/.claude/hooks/..",
    ]
    ESCAPED_NAME_PATTERN_WITH_PARENT_SEGMENT_IDS = ["mid-path-segment", "trailing-segment"]

    @pytest.mark.parametrize("agent_type", [*GATE_RELEASE_ALLOWED_AGENTS, None])
    @pytest.mark.parametrize(
        "command",
        ESCAPED_NAME_PATTERN_WITH_PARENT_SEGMENT,
        ids=ESCAPED_NAME_PATTERN_WITH_PARENT_SEGMENT_IDS,
    )
    def test_non_roster_caller_allowed_for_an_escaped_name_pattern_with_a_parent_segment(
        self, agent_type, command
    ):
        """None is the main session, which carries no agent_type."""
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(command, agent_type=agent_type),
            )
            == "allow"
        )

    @pytest.mark.parametrize("agent_type", NO_GATE_RELEASE_AGENTS)
    @pytest.mark.parametrize(
        "command",
        ESCAPED_NAME_PATTERN_WITH_PARENT_SEGMENT,
        ids=ESCAPED_NAME_PATTERN_WITH_PARENT_SEGMENT_IDS,
    )
    def test_roster_agent_allowed_for_an_escaped_name_pattern_without_an_op_word(
        self, agent_type, command
    ):
        """The roster scan still runs for these commands and finds no write or
        activate op, so the early exit is what allows them."""
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(command, agent_type=agent_type),
            )
            == "allow"
        )

    @pytest.mark.parametrize("agent_type", NO_GATE_RELEASE_AGENTS)
    def test_roster_agent_denied_for_an_escaped_name_with_a_write_op(self, agent_type):
        """The roster scan reads the quote-stripped text, so the escaped spelling
        of a write is denied (an accepted false-deny for a grep pattern)."""
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input("grep -rn 'marker\\.sh write' .", agent_type=agent_type),
            )
            == "deny"
        )

    @pytest.mark.parametrize("agent_type", GATE_RELEASE_ALLOWED_AGENTS)
    def test_full_tool_set_agent_allowed_for_the_same_large_command(self, agent_type):
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(
                    f"bash -c '{MARKER} write code-review'" + LARGE_MULTILINE_TAIL,
                    agent_type=agent_type,
                ),
            )
            == "allow"
        )


LEDGER = "~/.claude/scripts/review-ledger.sh"
LEDGER_APPEND_ARGS = 'append code-review --finding "F1 sample" --disposition ADDRESS --round 1'
# An `ADDRESS` row with `--ref` retires an earlier decision and records no engineer words.
LEDGER_APPEND_ADDRESS_REF_ARGS = LEDGER_APPEND_ARGS + " --ref abc123"
LEDGER_APPEND_CARRY_ARGS = (
    'append code-review --finding "F1 sample" --disposition SETTLED --decided-by carry --ref abc123'
)
LEDGER_APPEND_ENGINEER_ARGS = (
    'append code-review --finding "F1 sample" --disposition SETTLED --decided-by engineer '
    '--engineer-quote "keep it as is" --carry-forward'
)
LEDGER_APPEND_ENGINEER_QUOTE_SPLIT_ARGS = LEDGER_APPEND_ENGINEER_ARGS.replace(
    "--engineer-quote", "--engineer-''quote"
)
# The shell deletes a backslash-newline pair, so this still runs as the flag.
LEDGER_APPEND_ENGINEER_BACKSLASH_NEWLINE_ARGS = LEDGER_APPEND_ENGINEER_ARGS.replace(
    "--engineer-quote", "--engineer-\\\nquote"
)

# Agent-type strings that sit in no roster: the two full-tool-set built-ins and
# a plugin-shaped name. The engineer-row and ledger-state predicates apply to
# them; the roster's `append` predicate does not.
NON_ROSTER_AGENT_TYPES = ["general-purpose", "claude", "someplugin:agent"]

# Every op of review-ledger.sh, and whether the hook denies it to the roster.
LEDGER_SCRIPT_OPS_GATED_FOR_ROSTER = {
    "append": True,
    "show": False,
    "render": False,
    "clear-stale": False,
}


class TestReviewLedgerAppendAuthority:
    """The Bash arm's ledger predicates.

    A roster agent (`agent_type` in the roster) is denied every
    `review-ledger.sh append`, because the row carries the parent session's id
    and lands in the file the ledger resolver names for that session. Any
    subagent (non-empty `agent_id`) is denied an `append` carrying
    `--engineer-quote`, because only the main session holds the engineer's
    turn. `agent_type` alone does not identify a subagent, so a named main
    session is allowed that append (TestSubagentIsIdentifiedByAgentId).
    """

    @pytest.mark.parametrize("agent_type", NO_GATE_RELEASE_AGENTS)
    @pytest.mark.parametrize(
        "command",
        [
            f"review-ledger.sh {LEDGER_APPEND_ARGS}",
            f"{LEDGER} {LEDGER_APPEND_ARGS}",
            f"$HOME/.claude/scripts/review-ledger.sh {LEDGER_APPEND_ARGS}",
            f"cd somedir && {LEDGER} {LEDGER_APPEND_ARGS}",
            f"env X=1 {LEDGER} {LEDGER_APPEND_ARGS}",
            # Raw-text detector only: the command-word detector cannot see
            # inside a `bash -c` wrapper.
            f"bash -c '{LEDGER} {LEDGER_APPEND_ARGS}'",
            # Quote splits. Either detector denies each of these, so they pin
            # neither alone; the wrapper cases further down pin the raw-text one.
            f'"$HOME/.claude/scripts/review-ledger.sh" {LEDGER_APPEND_ARGS}',
            f'~/.claude/scripts/"review-ledger.sh" {LEDGER_APPEND_ARGS}',
            # Raw-text detector only: a backslash-newline is whitespace to the
            # shell, and splits the command into two fragments for the
            # command-word detector.
            f"{LEDGER} \\\n  {LEDGER_APPEND_ARGS}",
            # Mid-name quote split; either detector denies it.
            f"~/.claude/scripts/review-led''ger.sh {LEDGER_APPEND_ARGS}",
        ],
        ids=[
            "bare",
            "tilde-path",
            "home-var-path",
            "cd-chain",
            "env-prefix",
            "bash-c-wrapper",
            "quote-split-home-var-path",
            "quote-split-script-name",
            "backslash-newline",
            "mid-name-quote-split",
        ],
    )
    def test_append_denied_for_roster_agents(self, agent_type, command):
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(command, agent_type=agent_type),
            )
            == "deny"
        )

    @pytest.mark.parametrize("agent_type", NO_GATE_RELEASE_AGENTS)
    @pytest.mark.parametrize(
        "command",
        [
            # A quote inside the wrapper breaks the raw text's adjacency, and the
            # wrapper hides the command word from the other detector. Only the
            # raw-text detector on the quote-stripped command sees these.
            f"bash -c \"review-led''ger.sh {LEDGER_APPEND_ARGS}\"",
            "bash -c 'review-ledger.sh \"append\" code-review --finding x'",
            # Raw-text detector on a command over the pipe buffer.
            f"bash -c '{LEDGER} {LEDGER_APPEND_ARGS}'" + LARGE_MULTILINE_TAIL,
        ],
        ids=["quote-split-name-in-wrapper", "quoted-op-in-wrapper", "large-multiline-command"],
    )
    def test_append_denied_for_roster_agents_through_quoting_and_size(self, agent_type, command):
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(command, agent_type=agent_type),
            )
            == "deny"
        )

    @pytest.mark.parametrize("agent_type", NON_ROSTER_AGENT_TYPES)
    @pytest.mark.parametrize(
        "command",
        [
            "bash -c 'review-ledger.sh \"append\" code-review --finding x --engineer-quote q'",
            f"bash -c '{LEDGER} {LEDGER_APPEND_ENGINEER_ARGS}'" + LARGE_MULTILINE_TAIL,
        ],
        ids=["quoted-op-in-wrapper", "large-multiline-command"],
    )
    def test_engineer_append_denied_for_non_roster_agents_through_quoting_and_size(
        self, agent_type, command
    ):
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(command, agent_type=agent_type),
            )
            == "deny"
        )

    @pytest.mark.parametrize(
        "command",
        [
            # A backslash-newline inside the op word: the shell deletes it, so
            # the op runs as `append`, but spacing it out leaves `app end`.
            f"{LEDGER} app\\\nend code-review --finding x --disposition ADDRESS --round 1",
            # The same inside the script name.
            "~/.claude/scripts/review-led\\\nger.sh append code-review --finding x "
            "--disposition ADDRESS --round 1",
        ],
        ids=["split-op-word", "split-script-name"],
    )
    def test_append_denied_for_a_roster_agent_through_an_in_token_backslash_newline(self, command):
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(command, agent_type="code-writer"),
            )
            == "deny"
        )

    def test_roster_agent_append_with_a_marker_status_prefix_denied_by_the_ledger_arm(self):
        """Without the ledger arm, Stage 2 denies this compound command and
        echoes its first 80 characters, which name the ledger script too. Only
        the ledger arm's own wording tells the two apart."""
        reason = run_hook_reason(
            ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
            bash_input(
                f"{MARKER} status; {LEDGER} {LEDGER_APPEND_ARGS}", agent_type="code-writer"
            ),
        )
        assert reason is not None
        assert "cannot append review-ledger rows" in reason
        assert "marker.sh invocation denied" not in reason

    def test_roster_agent_ledger_show_chained_to_a_marker_write_denied_by_the_marker_arm(self):
        """The ledger arm never exits on a non-match, so the marker arm still
        runs on a combined command."""
        reason = run_hook_reason(
            ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
            bash_input(f"{LEDGER} show && {MARKER} write code-review", agent_type="code-writer"),
        )
        assert reason is not None
        assert "Marker write" in reason

    @pytest.mark.parametrize("agent_type", NO_GATE_RELEASE_AGENTS)
    @pytest.mark.parametrize(
        "command",
        [
            f"{LEDGER} show",
            f"{LEDGER} render --out agent-reviews/x.md",
            "grep -rn review-ledger.sh claude/",
            f"{LEDGER} clear-stale",
            f"{LEDGER} clear-stale --dry-run",
        ],
    )
    def test_non_append_ops_allowed_for_roster_agents(self, agent_type, command):
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(command, agent_type=agent_type),
            )
            == "allow"
        )

    @pytest.mark.parametrize("agent_type", NON_ROSTER_AGENT_TYPES)
    @pytest.mark.parametrize(
        "append_args",
        [LEDGER_APPEND_ARGS, LEDGER_APPEND_CARRY_ARGS, LEDGER_APPEND_ADDRESS_REF_ARGS],
        ids=["address-row", "carry-row", "address-ref-row"],
    )
    def test_append_without_engineer_quote_allowed_for_non_roster_agents(
        self, agent_type, append_args
    ):
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(f"{LEDGER} {append_args}", agent_type=agent_type),
            )
            == "allow"
        )

    @pytest.mark.parametrize("agent_type", [None, ""], ids=["no-agent-type", "empty-agent-type"])
    def test_engineer_append_allowed_for_main_session(self, agent_type):
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(f"{LEDGER} {LEDGER_APPEND_ENGINEER_ARGS}", agent_type=agent_type),
            )
            == "allow"
        )

    @pytest.mark.parametrize("agent_type", NON_ROSTER_AGENT_TYPES)
    @pytest.mark.parametrize(
        "command",
        [
            f"{LEDGER} {LEDGER_APPEND_ENGINEER_ARGS}",
            # A quote between the flag's halves defeats a raw-text test of
            # `--engineer-quote` but not the quote-stripped one.
            f"{LEDGER} {LEDGER_APPEND_ENGINEER_QUOTE_SPLIT_ARGS}",
            f"{LEDGER} {LEDGER_APPEND_ENGINEER_BACKSLASH_NEWLINE_ARGS}",
        ],
        ids=["plain-flag", "quote-split-flag", "backslash-newline-flag"],
    )
    def test_engineer_append_denied_for_every_non_roster_agent_type(self, agent_type, command):
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(command, agent_type=agent_type),
            )
            == "deny"
        )

    @pytest.mark.parametrize("agent_type", NON_ROSTER_AGENT_TYPES)
    def test_engineer_flag_text_on_a_non_append_op_allowed(self, agent_type):
        """The flag text only selects which commands the append detectors run
        on; a `show` that names it is not an append."""
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(f"{LEDGER} show --engineer-quote", agent_type=agent_type),
            )
            == "allow"
        )

    @pytest.mark.parametrize(
        "command",
        [
            # Variable-wrapped script name: the name and the op are no longer adjacent.
            f"LS={LEDGER}; $LS {LEDGER_APPEND_ARGS}",
            # Variable-wrapped op.
            f"OP=append; {LEDGER} $OP code-review",
            # One brace word that the shell expands to `append code-review`, a
            # working invocation with no literal op next to the script name.
            f"{LEDGER} {{append,code-review}} --finding x --disposition ADDRESS --round 1",
            # Op supplied by command substitution at run time.
            f"{LEDGER} $(echo append) code-review --finding x --disposition ADDRESS --round 1",
            # Op supplied on stdin by xargs.
            f"echo append code-review --finding x --disposition ADDRESS --round 1 | xargs {LEDGER}",
            # A glob the shell resolves to the script name.
            f"~/.claude/scripts/review-ledg*.sh {LEDGER_APPEND_ARGS}",
            # A case-varied script name runs on a case-insensitive volume, but
            # neither text detector folds case.
            f"~/.claude/scripts/Review-Ledger.SH {LEDGER_APPEND_ARGS}",
            # A symlink to the script under another name runs identically,
            # because the script finds its libraries by dirname.
            f"~/.claude/scripts/rl {LEDGER_APPEND_ENGINEER_ARGS}",
        ],
        ids=[
            "variable-script-name",
            "variable-op",
            "brace-expanded-op-and-gate",
            "command-substitution-op",
            "xargs-supplied-op",
            "globbed-script-name",
            "case-varied-script-name",
            "renamed-script-link",
        ],
    )
    def test_shell_indirection_allowed_residual(self, command):
        """Pins the Bash arm's ACCEPTED scope limit rather than an intended
        behavior, as test_bash_arm_does_not_match_shell_indirection does for
        markers. Invert these to deny if the arm ever becomes indirection-proof."""
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(command, agent_type="code-writer"),
            )
            == "allow"
        )

    def test_roster_agent_grep_of_the_literal_op_denied_with_the_escape_wording(self):
        """Pinned false-deny: the raw-text detector matches the quoted search
        string. The reason names the escape that avoids it."""
        reason = run_hook_reason(
            ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
            bash_input('grep -rn "review-ledger.sh append" claude/', agent_type="staff-sdet"),
        )
        assert reason is not None
        assert "command text" in reason
        assert "grep -rn review-ledger.sh" in reason
        assert "Grep and Read tools are unaffected" in reason

    def test_non_roster_append_whose_finding_text_names_the_quote_flag_denied(self):
        """Pinned false-deny: the flag test is a substring match on the whole
        command, so finding text that names the flag trips it."""
        command = f'{LEDGER} append code-review --finding "the --engineer-quote flag" --disposition ADDRESS'
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(command, agent_type="general-purpose"),
            )
            == "deny"
        )

    def test_roster_deny_reason_names_the_ledger_and_directs_upward(self):
        reason = run_hook_reason(
            ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
            bash_input(f"{LEDGER} {LEDGER_APPEND_ARGS}", agent_type="code-writer"),
        )
        assert reason is not None
        assert "review-ledger" in reason
        assert "code-writer" in reason
        assert "report" in reason.lower()
        assert "release" not in reason.lower()
        # The dispatcher logs a ledger row; it does not rerun a review.
        assert "re-dispatches you" not in reason
        assert "review skill" not in reason

    def test_engineer_deny_reason_names_the_agent_and_the_main_session(self):
        reason = run_hook_reason(
            ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
            bash_input(f"{LEDGER} {LEDGER_APPEND_ENGINEER_ARGS}", agent_type="someplugin:agent"),
        )
        assert reason is not None
        assert "someplugin:agent" in reason
        assert "engineer" in reason
        assert "main session" in reason
        assert "report" in reason.lower()
        assert "release" not in reason.lower()
        assert "general-purpose" not in reason

    def test_engineer_deny_reason_says_the_match_was_on_the_flag_text_anywhere(self):
        """The flag test is a substring match, so the reason names that and the
        way out of a false match: rewording the row text."""
        reason = run_hook_reason(
            ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
            bash_input(
                f'{LEDGER} append code-review --finding "the --engineer-quote flag" --disposition ADDRESS',
                agent_type="general-purpose",
            ),
        )
        assert reason is not None
        assert "anywhere in the command" in reason
        assert "rewording" in reason
        assert "grep -rn" not in reason

    @pytest.mark.parametrize(
        ("agent_type", "command"),
        [
            ("code-writer", "review-ledger.sh show --finding SENTINELFINDINGTEXT"),
            ("general-purpose", "review-ledger.sh show --engineer-quote SENTINELFINDINGTEXT"),
        ],
        ids=["roster-kind", "engineer-kind"],
    )
    def test_ledger_scan_status2_denied_without_echoing_the_command(
        self, tmp_path, agent_type, command
    ):
        """A sed shim that fails outside _lib_strip_shell_quotes's `-e` call
        shape isolates the ledger arm's own fail-closed branch. Neither raw-text
        detector matches a non-`append` op, so only the command-word detector
        decides, and it cannot determine an answer."""
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
            bash_input(command, agent_type=agent_type),
            extra_env={"PATH": f"{shim_dir}{os.pathsep}{os.environ['PATH']}"},
        )
        assert reason is not None
        assert "could not determine whether" in reason
        assert "review-ledger.sh append" in reason
        assert "SENTINELFINDINGTEXT" not in reason

    def test_every_script_op_has_an_explicit_gating_decision(self):
        """A source scan, kept narrow: the script's `case` arms are the only
        list of its ops, so a new op fails here until it is classified in
        LEDGER_SCRIPT_OPS_GATED_FOR_ROSTER."""
        script_text = (SCRIPTS_DIR / "review-ledger.sh").read_text()
        script_ops = {
            op
            for arm in re.findall(r"^  ([a-z][a-z|-]*)\)$", script_text, re.M)
            for op in arm.split("|")
        }
        assert script_ops == set(LEDGER_SCRIPT_OPS_GATED_FOR_ROSTER)

    @pytest.mark.parametrize(("op", "gated"), sorted(LEDGER_SCRIPT_OPS_GATED_FOR_ROSTER.items()))
    def test_roster_decision_for_each_script_op_matches_its_classification(self, op, gated):
        assert run_hook(
            ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
            bash_input(f"{LEDGER} {op}", agent_type="code-writer"),
        ) == ("deny" if gated else "allow")


class TestReviewLedgerStatePaths:
    """The path-keyed arms: Write/Edit/MultiEdit and a Bash redirect into the
    ledger directory are denied for every subagent (non-empty `agent_id`) and
    for every roster `agent_type` with or without `agent_id`, with the denied
    path derived from the documented key rules (the conftest oracle), not typed
    here. A named main session outside the roster is allowed
    (TestSubagentIsIdentifiedByAgentId). The oracle itself is pinned against the real resolver in
    test_lib_reviewer_round_state.py, and the hook reads only the directory, so
    one branch-scope key shape is enough here."""

    @pytest.fixture
    def ledger_files(self, isolated_home, git_repo):
        subprocess.run(["git", "checkout", "-q", "-b", "feature"], cwd=git_repo, check=True)
        ledger_file = _review_ledger_path(isolated_home, git_repo, "sess-ledger-test")
        return [ledger_file, ledger_file.with_name(ledger_file.name + ".lock")]

    @pytest.fixture
    def written_ledger_file(self, isolated_home, git_repo):
        """The file the real review-ledger.sh appends to, so the hook is
        exercised against the writer's own directory name."""
        _seed_session(isolated_home, "sess-ledger-test")
        env = {**os.environ, "HOME": str(isolated_home)}
        env.pop("CLAUDE_CONFIG_DIR", None)
        subprocess.run(
            [
                "bash", str(SCRIPTS_DIR / "review-ledger.sh"),
                "append", "code-review", "--disposition", "CLEAN", "--round", "1",
            ],
            cwd=git_repo, env=env, check=True, capture_output=True,
        )
        written = _review_ledger_path(isolated_home, git_repo, "sess-ledger-test")
        assert written.is_file(), "the oracle path is not where the real writer wrote"
        return written

    @pytest.mark.parametrize("agent_type", NO_GATE_RELEASE_AGENTS)
    @pytest.mark.parametrize(
        "tool_input_builder", [write_input, edit_input, multiedit_input],
        ids=["write", "edit", "multiedit"],
    )
    @pytest.mark.parametrize("lock_file", [False, True], ids=["ledger-file", "lock-file"])
    def test_file_write_tools_denied_for_roster_agents(
        self, isolated_home, ledger_files, agent_type, tool_input_builder, lock_file
    ):
        target = ledger_files[1] if lock_file else ledger_files[0]
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                tool_input_builder(str(target), agent_type=agent_type),
                home=isolated_home,
            )
            == "deny"
        )

    @pytest.mark.parametrize("agent_type", NON_ROSTER_AGENT_TYPES)
    @pytest.mark.parametrize(
        "tool_input_builder", [write_input, edit_input, multiedit_input],
        ids=["write", "edit", "multiedit"],
    )
    def test_file_write_tools_denied_for_non_roster_agents(
        self, isolated_home, ledger_files, agent_type, tool_input_builder
    ):
        """Only review-ledger.sh writes ledger files, so a full-tool-set agent
        has no more claim to a direct write than the roster does."""
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                tool_input_builder(str(ledger_files[0]), agent_type=agent_type),
                home=isolated_home,
            )
            == "deny"
        )

    @pytest.mark.parametrize("agent_type", [None, ""], ids=["no-agent-type", "empty-agent-type"])
    def test_file_write_allowed_for_the_main_session(self, isolated_home, ledger_files, agent_type):
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                write_input(str(ledger_files[0]), agent_type=agent_type),
                home=isolated_home,
            )
            == "allow"
        )

    @pytest.mark.parametrize("lock_file", [False, True], ids=["ledger-file", "lock-file"])
    def test_file_write_deny_reason_names_the_ledger_path_and_directs_upward(
        self, isolated_home, ledger_files, lock_file
    ):
        target = ledger_files[1] if lock_file else ledger_files[0]
        reason = run_hook_reason(
            ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
            write_input(str(target), agent_type="code-writer"),
            home=isolated_home,
        )
        assert reason is not None
        assert "review-ledger state" in reason
        assert str(target) in reason
        assert "code-writer" in reason
        assert "report" in reason.lower()
        assert "release" not in reason.lower()
        # A lock file holds no row, so the reason must not claim one.
        assert "row would be" not in reason
        assert "re-dispatches you" not in reason

    def test_file_write_deny_reason_for_a_non_roster_agent_names_it(self, isolated_home, ledger_files):
        reason = run_hook_reason(
            ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
            write_input(str(ledger_files[0]), agent_type="general-purpose"),
            home=isolated_home,
        )
        assert reason is not None
        assert "general-purpose" in reason
        assert "review-ledger state" in reason

    @pytest.mark.parametrize("via", ["write-tool", "bash-redirect"])
    def test_marker_path_deny_reason_keeps_the_gate_release_wording(self, isolated_home, via):
        """The two call sites that word their denial by the matched kind must
        still call a marker path a gate release, not ledger state."""
        marker_path = isolated_home / ".claude" / "code-review-markers" / "deadbeef.session"
        payload = (
            write_input(str(marker_path), agent_type="code-writer")
            if via == "write-tool"
            else bash_input(f"printf x > {marker_path}", agent_type="code-writer")
        )
        reason = run_hook_reason(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, payload, home=isolated_home)
        assert reason is not None
        assert "release a review gate" in reason
        assert "review-ledger state" not in reason

    @pytest.mark.parametrize(
        "relative_path",
        [
            "agent-reviews/review-ledger-x.md",
            ".claude/review-narrative-ledger-x/y",
            ".claude/plans/review-narrative-ledger.md",
            # The directory name sits mid-path, which the shape must not match.
            ".claude/plans/review-narrative-ledger/x.md",
        ],
    )
    def test_over_match_negatives_stay_writable(self, isolated_home, relative_path):
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                write_input(str(isolated_home / relative_path), agent_type="code-writer"),
                home=isolated_home,
            )
            == "allow"
        )

    @pytest.mark.parametrize("agent_type", NO_GATE_RELEASE_AGENTS + NON_ROSTER_AGENT_TYPES)
    def test_redirect_into_the_ledger_directory_denied_for_every_agent_type(
        self, isolated_home, agent_type
    ):
        reason = run_hook_reason(
            ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
            bash_input(
                "echo x >> ~/.claude/review-narrative-ledger/abc.def.jsonl", agent_type=agent_type
            ),
            home=isolated_home,
        )
        assert reason is not None
        assert "review-ledger state" in reason
        # The `.claude` in the displayed path would satisfy a bare name check
        # for the `claude` agent type.
        assert f"'{agent_type}' agent" in reason

    def test_redirect_into_the_ledger_directory_allowed_for_the_main_session(self, isolated_home):
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input("echo x >> ~/.claude/review-narrative-ledger/abc.def.jsonl"),
                home=isolated_home,
            )
            == "allow"
        )

    @pytest.mark.parametrize("agent_type", ["code-writer", "general-purpose"])
    @pytest.mark.parametrize("utility", ["cp", "mv", "install", "ln", "link"])
    @pytest.mark.parametrize(
        "destination",
        ["~/.claude/review-narrative-ledger", "~/.claude/review-narrative-ledger/"],
        ids=["bare-directory", "trailing-slash-directory"],
    )
    def test_directory_copy_into_the_ledger_directory_denied(
        self, isolated_home, agent_type, utility, destination
    ):
        """The destination names the directory itself, with no file component:
        the utility writes the file inside it."""
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(f"{utility} /tmp/x {destination}", agent_type=agent_type),
                home=isolated_home,
            )
            == "deny"
        )

    @pytest.mark.parametrize("agent_type", ["code-writer", "general-purpose"])
    @pytest.mark.parametrize("utility", ["cp", "mv", "install", "ln", "link"])
    def test_directory_copy_into_a_similarly_named_directory_allowed(
        self, isolated_home, agent_type, utility
    ):
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(
                    f"{utility} /tmp/x ~/.claude/review-narrative-ledger-x", agent_type=agent_type
                ),
                home=isolated_home,
            )
            == "allow"
        )

    @pytest.mark.parametrize("utility", ["cp", "mv", "install", "ln", "link"])
    def test_directory_copy_into_the_config_dir_ledger_directory_denied(self, tmp_path, utility):
        """A config dir with no `.claude` segment reaches only the config-dir arms."""
        home = tmp_path / "home"
        home.mkdir()
        config_dir = tmp_path / "profile-container"
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(
                    f"{utility} /tmp/x {config_dir / 'review-narrative-ledger'}",
                    agent_type="general-purpose",
                ),
                home=home,
                extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)},
            )
            == "deny"
        )

    def test_directory_copy_into_the_ledger_directory_allowed_for_the_main_session(
        self, isolated_home
    ):
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input("cp /tmp/x ~/.claude/review-narrative-ledger"),
                home=isolated_home,
            )
            == "allow"
        )

    @pytest.mark.parametrize("via", ["write-tool", "bash-redirect"])
    @pytest.mark.parametrize(
        "path_form",
        ["case-varied", "stow-fold-physical", "dot-dot-traversal"],
    )
    def test_alias_forms_of_the_ledger_path_denied(self, isolated_home, tmp_path, via, path_form):
        """The ledger directory has the aliases the marker directories do: a
        case-insensitive volume, a stow directory-fold, and a `..` route."""
        ledger_path = {
            "case-varied": isolated_home / ".Claude" / "Review-Narrative-Ledger" / "abc.def.jsonl",
            "stow-fold-physical": (
                tmp_path / "repo" / "claude" / ".claude" / "review-narrative-ledger" / "abc.def.jsonl"
            ),
            "dot-dot-traversal": (
                isolated_home / ".claude" / "plans" / ".." / "review-narrative-ledger" / "abc.def.jsonl"
            ),
        }[path_form]
        payload = (
            write_input(str(ledger_path), agent_type="code-writer")
            if via == "write-tool"
            else bash_input(f"printf x >> {ledger_path}", agent_type="code-writer")
        )
        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, payload, home=isolated_home) == "deny"

    def test_hook_denies_the_file_the_real_writer_produced(self, isolated_home, written_ledger_file):
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                write_input(str(written_ledger_file), agent_type="code-writer"),
                home=isolated_home,
            )
            == "deny"
        )

    @pytest.mark.parametrize("via", ["write-tool", "bash-redirect"])
    @pytest.mark.parametrize("agent_type", ["code-writer", "general-purpose"])
    def test_hook_denies_the_writers_directory_name_under_a_custom_config_dir(
        self, tmp_path, written_ledger_file, via, agent_type
    ):
        """A config dir with no `.claude` segment reaches only the config-dir
        arms, so the real writer's directory name is checked against those."""
        home = tmp_path / "home"
        config_dir = tmp_path / "profile-container"
        target = config_dir / written_ledger_file.parent.name / written_ledger_file.name
        payload = (
            write_input(str(target), agent_type=agent_type)
            if via == "write-tool"
            else bash_input(f"printf x >> {target}", agent_type=agent_type)
        )
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                payload,
                home=home,
                extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)},
            )
            == "deny"
        )

    @pytest.mark.parametrize("agent_type", NO_GATE_RELEASE_AGENTS)
    def test_write_denied_when_config_dir_is_a_symlink(
        self, tmp_path, written_ledger_file, agent_type
    ):
        """_lib_config_dir returns CLAUDE_CONFIG_DIR verbatim, so a write through
        the physical path of a symlinked config dir reaches only the realpath arm."""
        home = tmp_path / "home"
        physical = tmp_path / "physical-profile"
        (physical / written_ledger_file.parent.name).mkdir(parents=True)
        symlinked = tmp_path / "symlinked-profile"
        symlinked.symlink_to(physical)
        target = physical / written_ledger_file.parent.name / written_ledger_file.name
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                write_input(str(target), agent_type=agent_type),
                home=home,
                extra_env={"CLAUDE_CONFIG_DIR": str(symlinked)},
            )
            == "deny"
        )

    def test_main_session_write_allowed_under_a_config_dir_with_no_dotclaude_segment(
        self, tmp_path, written_ledger_file
    ):
        home = tmp_path / "home"
        config_dir = tmp_path / "profile-container"
        target = config_dir / written_ledger_file.parent.name / written_ledger_file.name
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                write_input(str(target)),
                home=home,
                extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)},
            )
            == "allow"
        )

    @pytest.mark.parametrize("agent_type", ["code-writer", "general-purpose"])
    @pytest.mark.parametrize("tool", ["write-tool", "bash-redirect"])
    def test_denied_when_config_dir_is_unresolvable_and_the_reason_covers_ledger_paths(
        self, tmp_path, agent_type, tool
    ):
        """A relative CLAUDE_CONFIG_DIR leaves the target unclassifiable, so
        every caller with a non-empty `agent_type` is denied, and the reason
        must not call the target only a marker path."""
        home = tmp_path / "home"
        target = tmp_path / "somewhere" / ".claude" / "review-narrative-ledger" / "abc.def.jsonl"
        payload = (
            write_input(str(target), agent_type=agent_type)
            if tool == "write-tool"
            else bash_input(f"printf x >> {target}", agent_type=agent_type)
        )
        reason = run_hook_reason(
            ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
            payload,
            home=home,
            extra_env={"CLAUDE_CONFIG_DIR": "relative-profile"},
        )
        assert reason is not None
        assert "could not resolve the Claude Code config directory" in reason
        assert "review-ledger path" in reason

    @pytest.mark.parametrize("agent_type", ["code-writer", "general-purpose"])
    @pytest.mark.parametrize("tool", ["write-tool", "bash-redirect"])
    def test_a_named_main_session_is_denied_when_config_dir_is_unresolvable(
        self, tmp_path, agent_type, tool
    ):
        """The unresolvable-config-dir deny keys on a non-empty `agent_id` or
        `agent_type`, so a main session started with `--agent` (an
        `agent_type` and no `agent_id`) is denied too, roster name or not."""
        home = tmp_path / "home"
        target = tmp_path / "somewhere" / ".claude" / "review-narrative-ledger" / "abc.def.jsonl"
        payload = (
            write_input(str(target), agent_type=agent_type, agent_id=None)
            if tool == "write-tool"
            else bash_input(f"printf x >> {target}", agent_type=agent_type, agent_id=None)
        )
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                payload,
                home=home,
                extra_env={"CLAUDE_CONFIG_DIR": "relative-profile"},
            )
            == "deny"
        )

    def test_an_unnamed_main_session_may_use_the_file_write_tools_when_config_dir_is_unresolvable(
        self, tmp_path
    ):
        """Control for the named-main-session deny: with neither `agent_id`
        nor `agent_type` the file-write arm exits before resolving the target."""
        home = tmp_path / "home"
        target = tmp_path / "somewhere" / ".claude" / "review-narrative-ledger" / "abc.def.jsonl"
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                write_input(str(target)),
                home=home,
                extra_env={"CLAUDE_CONFIG_DIR": "relative-profile"},
            )
            == "allow"
        )

    def test_similarly_named_directory_under_a_custom_config_dir_stays_writable(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        config_dir = tmp_path / "profile-container"
        config_dir.mkdir()
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                write_input(
                    str(config_dir / "review-narrative-ledger-x" / "y"), agent_type="code-writer"
                ),
                home=home,
                extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)},
            )
            == "allow"
        )

    @pytest.mark.parametrize(
        "command_template",
        [
            "echo x >> {target}",
            # A `.claude` mention elsewhere in the command must not be what decides.
            "echo x >> {target} && ls ~/.claude",
        ],
        ids=["no-dotclaude-anywhere", "dotclaude-elsewhere"],
    )
    @pytest.mark.parametrize("agent_type", ["code-writer", "general-purpose"])
    def test_redirect_to_a_config_dir_ledger_path_denied(
        self, tmp_path, command_template, agent_type
    ):
        """The Bash scan also runs when the command names the ledger directory,
        so a config dir with no `.claude` segment is covered."""
        home = tmp_path / "home"
        home.mkdir()
        config_dir = tmp_path / "profile-container"
        target = config_dir / "review-narrative-ledger" / "abc.def.jsonl"
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(command_template.format(target=target), agent_type=agent_type),
                home=home,
                extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)},
            )
            == "deny"
        )

    def test_redirect_to_a_config_dir_ledger_path_allowed_for_the_main_session(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        config_dir = tmp_path / "profile-container"
        target = config_dir / "review-narrative-ledger" / "abc.def.jsonl"
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(f"echo x >> {target}"),
                home=home,
                extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)},
            )
            == "allow"
        )

    @pytest.mark.parametrize("via", ["write-tool", "bash-redirect"])
    @pytest.mark.parametrize("marker_directory", ["code-review-markers", ".plan-review-active.d"])
    def test_a_marker_directory_segment_before_a_traversal_into_the_ledger_directory_denied_for_a_non_roster_agent(
        self, isolated_home, via, marker_directory
    ):
        """The raw form matches the marker shape and only the normalized form
        matches the ledger shape. The ledger kind must win, or a full-tool-set
        agent, which may write markers, could forge a ledger row."""
        forged = isolated_home / ".claude" / marker_directory / ".." / "review-narrative-ledger" / "abc.def.jsonl"
        payload = (
            write_input(str(forged), agent_type="general-purpose")
            if via == "write-tool"
            else bash_input(f"echo x >> {forged}", agent_type="general-purpose")
        )

        reason = run_hook_reason(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, payload, home=isolated_home)

        assert reason is not None
        assert "review-ledger state" in reason

    @pytest.mark.parametrize("via", ["write-tool", "bash-redirect"])
    def test_a_marker_directory_segment_before_a_traversal_into_the_config_dir_ledger_directory_denied(
        self, tmp_path, via
    ):
        """The same ordering rule across the config-dir candidate forms, for a
        config dir with no `.claude` segment."""
        home = tmp_path / "home"
        home.mkdir()
        config_dir = tmp_path / "profile-container"
        forged = config_dir / "code-review-markers" / ".." / "review-narrative-ledger" / "abc.def.jsonl"
        payload = (
            write_input(str(forged), agent_type="general-purpose")
            if via == "write-tool"
            else bash_input(f"echo x >> {forged}", agent_type="general-purpose")
        )

        reason = run_hook_reason(
            ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, payload, home=home,
            extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)},
        )

        assert reason is not None
        assert "review-ledger state" in reason

    @pytest.mark.parametrize("via", ["write-tool", "bash-redirect"])
    def test_a_path_matching_both_the_ledger_and_a_marker_shape_is_ledger_state(self, isolated_home, via):
        """One pattern list matches both shapes: the ledger directory contains a
        marker-shaped subdirectory. The ledger arm must come first, or a
        full-tool-set subagent, which may write markers, could write ledger state."""
        both_shapes = isolated_home / ".claude" / "review-narrative-ledger" / "x-markers" / "f"
        payload = (
            write_input(str(both_shapes), agent_type="general-purpose")
            if via == "write-tool"
            else bash_input(f"echo x >> {both_shapes}", agent_type="general-purpose")
        )

        reason = run_hook_reason(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, payload, home=isolated_home)

        assert reason is not None
        assert "review-ledger state" in reason

    @pytest.mark.parametrize("via", ["write-tool", "bash-redirect"])
    @pytest.mark.parametrize("config_dir_form", ["verbatim", "symlinked"])
    def test_a_path_matching_both_shapes_under_a_config_dir_with_no_dotclaude_segment_is_ledger_state(
        self, tmp_path, via, config_dir_form
    ):
        """The config-dir pattern lists carry the same two-arm shape as the
        `.claude` list, so each needs its ledger arm first. The symlinked
        form writes through the physical path, which only the realpath-anchored
        list matches."""
        home = tmp_path / "home"
        home.mkdir()
        physical = tmp_path / "profile-container"
        physical.mkdir()
        config_dir = physical
        if config_dir_form == "symlinked":
            config_dir = tmp_path / "symlinked-profile"
            config_dir.symlink_to(physical)
        both_shapes = physical / "review-narrative-ledger" / "x-markers" / "f"
        payload = (
            write_input(str(both_shapes), agent_type="general-purpose")
            if via == "write-tool"
            else bash_input(f"echo x >> {both_shapes}", agent_type="general-purpose")
        )

        reason = run_hook_reason(
            ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, payload, home=home,
            extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)},
        )

        assert reason is not None
        assert "review-ledger state" in reason

    def test_a_path_matching_both_shapes_past_the_realpath_budget_is_ledger_state(self, tmp_path):
        """With the realpath budget spent only the resolved-config-dir list can
        match the raw text, so this reaches that list's ledger arm alone."""
        home = tmp_path / "home"
        home.mkdir()
        config_dir = tmp_path / "profile-container"
        both_shapes = config_dir / "review-narrative-ledger" / "x-markers" / "f"
        padding = _past_realpath_budget_padding()

        reason = run_hook_reason(
            ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
            bash_input(f"tee {padding} {both_shapes}", agent_type="general-purpose"),
            home=home,
            extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)},
        )

        assert reason is not None
        assert "review-ledger state" in reason

    @pytest.fixture
    def no_realpath_path(self, tmp_path):
        """A PATH holding what the hook needs except `realpath` and `grealpath`,
        so no normalized candidate form exists. The premise is asserted in
        process here and by running the resolver once, in
        test_the_stub_path_leaves_no_normalized_form."""
        stub_bin = tmp_path / "no-realpath-bin"
        stub_bin.mkdir()
        for binary in ("bash", "jq", "grep", "sed", "dirname", "cat", "timeout", "tr"):
            resolved = shutil.which(binary)
            if resolved:
                (stub_bin / binary).symlink_to(resolved)
        assert shutil.which("realpath", path=str(stub_bin)) is None
        assert shutil.which("grealpath", path=str(stub_bin)) is None
        return str(stub_bin)

    def test_the_stub_path_leaves_no_normalized_form(self, tmp_path, no_realpath_path):
        """Every without-a-normalized-form case below rests on the stub PATH
        failing to resolve an existing and a missing target, checked once here
        instead of per case."""
        for target in (str(tmp_path), str(tmp_path / "missing" / "file")):
            resolved = subprocess.run(
                ["bash", "-c", '. "$1"; PATH="$2"; _lib_realpath_m "$3"', "bash",
                 str(HOOKS_DIR / "_lib.sh"), no_realpath_path, target],
                capture_output=True, text=True, check=False,
            )
            assert resolved.returncode != 0 or resolved.stdout == "", (
                f"the stub PATH still resolves {target!r} to {resolved.stdout!r}, "
                "so the without-a-normalized-form cases no longer test that"
            )

    def _ledger_write_payload(self, via, target, **agent_kwargs):
        """`via` picks the surface that carries TARGET. The tee form lists enough
        earlier targets to spend the realpath budget, so TARGET gets no
        normalized form."""
        if via == "write-tool":
            return write_input(str(target), **agent_kwargs)
        if via == "bash-redirect":
            return bash_input(f"echo x >> {target}", **agent_kwargs)
        return bash_input(f"tee {_past_realpath_budget_padding()} {target}", **agent_kwargs)

    def _no_normalized_form_env(self, via, no_realpath_path):
        """The tee form spends the budget; the others remove `realpath`."""
        return {} if via == "bash-tee-past-realpath-budget" else {"PATH": no_realpath_path}

    def _traversal_into_the_ledger_directory_payload(self, home, via, **agent_kwargs):
        """The raw form is marker-shaped and only a normalized form could show
        the ledger directory."""
        forged = home / ".claude" / "code-review-markers" / ".." / "review-narrative-ledger" / "abc.def.jsonl"
        return self._ledger_write_payload(via, forged, **agent_kwargs)

    @pytest.mark.parametrize("via", ["write-tool", "bash-redirect", "bash-tee-past-realpath-budget"])
    def test_a_marker_directory_traversal_into_the_ledger_directory_denied_for_a_non_roster_agent_without_a_normalized_form(
        self, isolated_home, no_realpath_path, via
    ):
        """No realpath on PATH (and, for the tee form, a spent budget) leaves the
        raw marker-shaped text and its lexical form as the only candidates. The
        lexical form resolves the `..` and matches the ledger glob, so a
        full-tool-set subagent must not pass as a marker writer."""
        payload = self._traversal_into_the_ledger_directory_payload(
            isolated_home, via, agent_type="general-purpose"
        )
        extra_env = {} if via == "bash-tee-past-realpath-budget" else {"PATH": no_realpath_path}

        reason = run_hook_reason(
            ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, payload, home=isolated_home, extra_env=extra_env
        )

        assert reason is not None
        assert "review-ledger state" in reason

    @pytest.mark.parametrize("via", ["write-tool", "bash-redirect", "bash-tee-past-realpath-budget"])
    def test_a_marker_directory_traversal_into_the_ledger_directory_allowed_for_the_main_session_without_a_normalized_form(
        self, isolated_home, no_realpath_path, via
    ):
        payload = self._traversal_into_the_ledger_directory_payload(isolated_home, via)
        extra_env = {} if via == "bash-tee-past-realpath-budget" else {"PATH": no_realpath_path}

        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, payload, home=isolated_home, extra_env=extra_env
            )
            == "allow"
        )

    @pytest.mark.parametrize("via", ["write-tool", "bash-redirect"])
    def test_a_plain_marker_path_stays_writable_for_a_non_roster_agent_without_a_normalized_form(
        self, isolated_home, no_realpath_path, via
    ):
        """Control: with no normalized form, a marker path whose text does not
        name the ledger directory keeps its marker kind."""
        marker_path = isolated_home / ".claude" / "code-review-markers" / "deadbeef.session"
        payload = (
            write_input(str(marker_path), agent_type="general-purpose")
            if via == "write-tool"
            else bash_input(f"echo x >> {marker_path}", agent_type="general-purpose")
        )

        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, payload, home=isolated_home,
                extra_env={"PATH": no_realpath_path},
            )
            == "allow"
        )

    @pytest.mark.parametrize("via", ["write-tool", "bash-redirect", "bash-tee-past-realpath-budget"])
    @pytest.mark.parametrize(
        "ledger_path_template",
        [
            "{home}/.claude//review-narrative-ledger/abc.def.jsonl",
            "{home}/.claude/./review-narrative-ledger/abc.def.jsonl",
            "{home}/.claude/missing/../review-narrative-ledger/abc.def.jsonl",
            "{home}/.claude/projects/../review-narrative-ledger/abc.def.jsonl",
        ],
        ids=[
            "doubled-slash",
            "dot-segment",
            "dotdot-after-a-missing-directory",
            "dotdot-from-a-non-marker-directory",
        ],
    )
    def test_a_dot_segment_or_doubled_slash_ledger_path_denied_for_a_non_roster_subagent_without_a_normalized_form(
        self, isolated_home, no_realpath_path, via, ledger_path_template
    ):
        """No `realpath` on PATH, or a spent realpath budget, leaves the written
        text and its lexical form as the only candidates. The ledger globs have
        no wildcard between the anchor and the directory name, so each of these
        spellings matches only through the lexical form."""
        ledger_path = ledger_path_template.format(home=isolated_home)
        payload = self._ledger_write_payload(via, ledger_path, agent_type="general-purpose")

        reason = run_hook_reason(
            ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, payload, home=isolated_home,
            extra_env=self._no_normalized_form_env(via, no_realpath_path),
        )

        assert reason is not None
        assert "review-ledger state" in reason

    @pytest.mark.parametrize("via", ["write-tool", "bash-redirect", "bash-tee-past-realpath-budget"])
    @pytest.mark.parametrize(
        ("config_dir_value_template", "ledger_path_template"),
        [
            ("{config_dir}", "{config_dir}//review-narrative-ledger/abc.def.jsonl"),
            ("{config_dir}", "{config_dir}/./review-narrative-ledger/abc.def.jsonl"),
            ("{config_dir}", "{config_dir}/missing/../review-narrative-ledger/abc.def.jsonl"),
            ("{parent}//profile-container", "{config_dir}/review-narrative-ledger/abc.def.jsonl"),
            ("{parent}/outer/../profile-container", "{config_dir}/review-narrative-ledger/abc.def.jsonl"),
            ("{parent}//profile-container", "{parent}//profile-container//review-narrative-ledger/abc.def.jsonl"),
            ("{config_dir}//", "{config_dir}/review-narrative-ledger/abc.def.jsonl"),
        ],
        ids=[
            "doubled-slash-in-the-target",
            "dot-segment-in-the-target",
            "dotdot-in-the-target",
            "doubled-slash-in-the-config-dir",
            "dotdot-in-the-config-dir",
            "doubled-slash-in-both",
            "trailing-doubled-slash-in-the-config-dir",
        ],
    )
    def test_a_dot_segment_or_doubled_slash_config_dir_ledger_path_denied_for_a_non_roster_subagent_without_a_normalized_form(
        self, tmp_path, no_realpath_path, via, config_dir_value_template, ledger_path_template
    ):
        """The config-dir anchors get the same lexical treatment as the target,
        for a config dir with no `.claude` segment. Each row matches only
        through a lexical candidate or the lexical config-dir anchor, which
        drops the trailing slash a `//`-terminated config dir keeps."""
        home = tmp_path / "home"
        home.mkdir()
        config_dir = tmp_path / "profile-container"
        names = {"config_dir": str(config_dir), "parent": str(tmp_path)}
        payload = self._ledger_write_payload(
            via, ledger_path_template.format(**names), agent_type="general-purpose"
        )
        # The tee form's padding names `~/.claude`, which is under HOME, so the
        # budget is spent whichever config dir is set.
        extra_env = {
            **self._no_normalized_form_env(via, no_realpath_path),
            "CLAUDE_CONFIG_DIR": config_dir_value_template.format(**names),
        }

        reason = run_hook_reason(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, payload, home=home, extra_env=extra_env)

        assert reason is not None
        assert "review-ledger state" in reason

    @pytest.mark.parametrize("via", ["write-tool", "bash-redirect", "bash-tee-past-realpath-budget"])
    @pytest.mark.parametrize(
        "session_kwargs",
        [{}, {"agent_type": "general-purpose", "agent_id": None}],
        ids=["unnamed-main-session", "named-non-roster-main-session"],
    )
    @pytest.mark.parametrize(
        "ledger_path_template",
        [
            "{home}/.claude//review-narrative-ledger/abc.def.jsonl",
            "{home}/.claude/missing/../review-narrative-ledger/abc.def.jsonl",
        ],
        ids=["doubled-slash", "dotdot-after-a-missing-directory"],
    )
    def test_a_dot_segment_or_doubled_slash_ledger_path_allowed_for_a_main_session_without_a_normalized_form(
        self, isolated_home, no_realpath_path, via, session_kwargs, ledger_path_template
    ):
        """A main session writes ledger state, named or not. A named main
        session has no `agent_id`, so the lexical form's ledger match does not
        bar it."""
        ledger_path = ledger_path_template.format(home=isolated_home)
        payload = self._ledger_write_payload(via, ledger_path, **session_kwargs)

        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, payload, home=isolated_home,
                extra_env=self._no_normalized_form_env(via, no_realpath_path),
            )
            == "allow"
        )

    @pytest.mark.parametrize("via", ["write-tool", "bash-redirect", "bash-tee-past-realpath-budget"])
    @pytest.mark.parametrize("anchor", ["home-claude-directory", "config-dir-without-a-claude-segment"])
    def test_a_ledger_path_whose_raw_text_already_matches_denied_for_a_non_roster_subagent_without_a_normalized_form(
        self, tmp_path, no_realpath_path, via, anchor
    ):
        """Control for the lexical-form cases above: a doubled slash after the
        ledger directory name leaves the raw text matching the ledger glob, so
        these pass with or without the lexical candidate."""
        home = tmp_path / "home"
        home.mkdir()
        config_dir = tmp_path / "profile-container"
        extra_env = self._no_normalized_form_env(via, no_realpath_path)
        if anchor == "home-claude-directory":
            ledger_path = f"{home}/.claude/review-narrative-ledger//abc.def.jsonl"
        else:
            ledger_path = f"{config_dir}/review-narrative-ledger//abc.def.jsonl"
            extra_env = {**extra_env, "CLAUDE_CONFIG_DIR": str(config_dir)}
        payload = self._ledger_write_payload(via, ledger_path, agent_type="general-purpose")

        reason = run_hook_reason(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, payload, home=home, extra_env=extra_env)

        assert reason is not None
        assert "review-ledger state" in reason

    @pytest.mark.parametrize("via", ["write-tool", "bash-redirect", "bash-tee-past-realpath-budget"])
    def test_a_ledger_path_under_a_trailing_doubled_slash_config_dir_allowed_for_the_unnamed_main_session_without_realpath(
        self, tmp_path, no_realpath_path, via
    ):
        """Counterpart of the `trailing-doubled-slash-in-the-config-dir` deny
        row: the unnamed main session writes ledger state."""
        home = tmp_path / "home"
        home.mkdir()
        config_dir = tmp_path / "profile-container"
        payload = self._ledger_write_payload(
            via, f"{config_dir}/review-narrative-ledger/abc.def.jsonl"
        )

        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, payload, home=home,
                extra_env={
                    **self._no_normalized_form_env(via, no_realpath_path),
                    "CLAUDE_CONFIG_DIR": f"{config_dir}//",
                },
            )
            == "allow"
        )

    @pytest.mark.parametrize("via", ["write-tool", "bash-redirect", "bash-tee-past-realpath-budget"])
    def test_a_bare_relative_ledger_path_allowed_residual_without_a_normalized_form(
        self, isolated_home, no_realpath_path, via
    ):
        """Accepted residual: with no leading `/` or `./` the text carries no
        `/.claude/` segment, so neither the raw text nor its lexical form
        matches, and only a `realpath` form resolves it. Invert this to a deny
        pin if a relative spelling is ever classified without `realpath`."""
        payload = self._ledger_write_payload(
            via, ".claude/review-narrative-ledger/abc.def.jsonl", agent_type="general-purpose"
        )

        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, payload, home=isolated_home,
                extra_env=self._no_normalized_form_env(via, no_realpath_path),
            )
            == "allow"
        )

    @pytest.fixture
    def ledger_file_alias(self, isolated_home):
        """A symlink whose own path names `.claude` but not the ledger directory,
        pointing at a ledger file."""
        ledger_dir = isolated_home / ".claude" / "review-narrative-ledger"
        ledger_dir.mkdir()
        ledger_file = ledger_dir / "abc.def.jsonl"
        ledger_file.write_text("")
        alias = isolated_home / ".claude" / "projects" / "alias.jsonl"
        alias.parent.mkdir()
        alias.symlink_to(ledger_file)
        return alias

    def test_a_symlink_alias_to_a_ledger_file_denied_for_a_non_roster_subagent_while_realpath_budget_remains(
        self, isolated_home, ledger_file_alias
    ):
        """Control for the allowed residual below: the same alias is resolved
        and denied when its candidate gets a realpath form."""
        reason = run_hook_reason(
            ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
            bash_input(f"echo x >> {ledger_file_alias}", agent_type="general-purpose"),
            home=isolated_home,
        )

        assert reason is not None
        assert "review-ledger state" in reason

    @pytest.mark.parametrize("via", ["write-tool", "bash-redirect", "bash-tee-past-realpath-budget"])
    def test_a_symlink_alias_to_a_ledger_file_allowed_residual_without_a_normalized_form(
        self, isolated_home, no_realpath_path, ledger_file_alias, via
    ):
        """Accepted residual: past the realpath budget, or with no `realpath`,
        a symlink alias goes unresolved, because only `realpath` follows a
        symlink and the lexical form does not. Invert this to a deny pin if
        symlink resolution ever stops depending on `realpath`."""
        payload = self._ledger_write_payload(via, ledger_file_alias, agent_type="general-purpose")

        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, payload, home=isolated_home,
                extra_env=self._no_normalized_form_env(via, no_realpath_path),
            )
            == "allow"
        )

    @pytest.mark.parametrize("via", ["write-tool", "bash-redirect"])
    def test_a_marker_directory_traversal_into_the_ledger_directory_allowed_for_the_main_session(
        self, isolated_home, via
    ):
        forged = isolated_home / ".claude" / "code-review-markers" / ".." / "review-narrative-ledger" / "abc.def.jsonl"
        payload = write_input(str(forged)) if via == "write-tool" else bash_input(f"echo x >> {forged}")

        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, payload, home=isolated_home) == "allow"

    @pytest.mark.parametrize("via", ["write-tool", "bash-redirect"])
    def test_a_plain_marker_path_stays_writable_for_a_non_roster_agent(self, isolated_home, via):
        """Control for the ledger-wins ordering: a marker path with no ledger
        form keeps its marker kind, which a full-tool-set agent may write."""
        marker_path = isolated_home / ".claude" / "code-review-markers" / "deadbeef.session"
        payload = (
            write_input(str(marker_path), agent_type="general-purpose")
            if via == "write-tool"
            else bash_input(f"echo x >> {marker_path}", agent_type="general-purpose")
        )

        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, payload, home=isolated_home) == "allow"

    @pytest.mark.parametrize("agent_type", ["code-writer", "general-purpose"])
    @pytest.mark.parametrize("utility", ["ln -s /tmp/forged", "ln -sf /tmp/forged", "link /tmp/forged"])
    @pytest.mark.parametrize(
        "link_path",
        ["~/.claude/review-narrative-ledger/abc.def.jsonl", "~/.claude/review-narrative-ledger/abc.def.jsonl.lock"],
        ids=["ledger-file", "lock-file"],
    )
    def test_a_link_created_at_a_ledger_path_denied(self, isolated_home, agent_type, utility, link_path):
        """A planted link redirects the script's read and append to a file the
        agent controls."""
        reason = run_hook_reason(
            ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
            bash_input(f"{utility} {link_path}", agent_type=agent_type),
            home=isolated_home,
        )

        assert reason is not None
        assert "review-ledger state" in reason

    def test_a_link_created_at_a_marker_path_denied_for_a_roster_agent(self, isolated_home):
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input("ln -s /tmp/forged ~/.claude/code-review-markers/deadbeef.session", agent_type="code-writer"),
                home=isolated_home,
            )
            == "deny"
        )

    def test_a_link_created_at_a_marker_path_allowed_for_a_non_roster_agent(self, isolated_home):
        """A full-tool-set agent may write markers, so the `ln` extractor keeps the roster test for marker paths."""
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(
                    "ln -s /tmp/forged ~/.claude/code-review-markers/deadbeef.session", agent_type="general-purpose",
                ),
                home=isolated_home,
            )
            == "allow"
        )

    def test_a_link_created_at_a_ledger_path_allowed_for_the_main_session(self, isolated_home):
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input("ln -s /tmp/forged ~/.claude/review-narrative-ledger/abc.def.jsonl"),
                home=isolated_home,
            )
            == "allow"
        )

    @pytest.mark.parametrize("agent_type", ["code-writer", "general-purpose"])
    def test_a_link_to_a_ledger_file_created_elsewhere_allowed(self, isolated_home, agent_type):
        """The link path, the last argument, is what the scan classifies, so a
        link that points at a ledger file from outside the directory is not
        a write to ledger state."""
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input("ln -s ~/.claude/review-narrative-ledger/abc.def.jsonl /tmp/view", agent_type=agent_type),
                home=isolated_home,
            )
            == "allow"
        )

    def test_redirect_into_a_ledger_path_split_by_a_backslash_newline_denied(self, isolated_home):
        """The shell deletes the backslash-newline pair, so the write lands in
        the ledger directory even though no line names it."""
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(
                    "echo x >> ~/.claude/review-narr\\\native-ledger/x.jsonl",
                    agent_type="general-purpose",
                ),
                home=isolated_home,
            )
            == "deny"
        )

    def test_redirect_on_a_line_after_an_escaped_backslash_denied(self, isolated_home):
        """A line ending in `\\\\` ends in a literal backslash, not a
        continuation, so the next line is its own command. Deleting every
        backslash-newline pair would glue `x>>` into one word and hide the
        redirect, so the unjoined text is scanned too."""
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(
                    "echo x\\\\\n>> ~/.claude/review-narrative-ledger/x.jsonl",
                    agent_type="general-purpose",
                ),
                home=isolated_home,
            )
            == "deny"
        )

    # Each form reaches the ledger directory of a config dir with no `.claude`
    # segment through a leading variable the shell expands before the write.
    LEADING_VARIABLE_LEDGER_REDIRECTS = [
        'echo x >> "$CLAUDE_CONFIG_DIR/review-narrative-ledger/f.jsonl"',
        'echo x >> "${CLAUDE_CONFIG_DIR}/review-narrative-ledger/f.jsonl"',
        "echo x >> $HOME/profile-container/review-narrative-ledger/f.jsonl",
        "echo x >> ${HOME}/profile-container/review-narrative-ledger/f.jsonl",
    ]
    LEADING_VARIABLE_LEDGER_REDIRECT_IDS = [
        "config-dir-var",
        "braced-config-dir-var",
        "home-var",
        "braced-home-var",
    ]

    @pytest.mark.parametrize("agent_type", ["code-writer", "general-purpose"])
    @pytest.mark.parametrize(
        "command", LEADING_VARIABLE_LEDGER_REDIRECTS, ids=LEADING_VARIABLE_LEDGER_REDIRECT_IDS
    )
    def test_redirect_through_a_leading_variable_to_a_ledger_path_denied(
        self, tmp_path, command, agent_type
    ):
        home = tmp_path / "home"
        home.mkdir()
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(command, agent_type=agent_type),
                home=home,
                extra_env={"CLAUDE_CONFIG_DIR": str(home / "profile-container")},
            )
            == "deny"
        )

    @pytest.mark.parametrize(
        "command", LEADING_VARIABLE_LEDGER_REDIRECTS, ids=LEADING_VARIABLE_LEDGER_REDIRECT_IDS
    )
    def test_redirect_through_a_leading_variable_to_a_ledger_path_allowed_for_the_main_session(
        self, tmp_path, command
    ):
        home = tmp_path / "home"
        home.mkdir()
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(command),
                home=home,
                extra_env={"CLAUDE_CONFIG_DIR": str(home / "profile-container")},
            )
            == "allow"
        )

    @pytest.mark.parametrize(
        "command",
        [
            # The relative target is resolved against the hook's directory, not
            # the `cd`, and carries neither `.claude` nor the ledger name.
            "cd ~/.claude/review-narrative-ledger && echo x >> f.jsonl",
            # `rm`, `truncate` and `mv` with a ledger file as the source are not
            # write shapes the scan extracts a target from.
            "rm ~/.claude/review-narrative-ledger/abc.def.jsonl",
            "truncate -s 0 ~/.claude/review-narrative-ledger/abc.def.jsonl",
            "mv ~/.claude/review-narrative-ledger/abc.def.jsonl /tmp/x",
        ],
        ids=["relative-target-after-cd", "rm-ledger-file", "truncate-ledger-file", "mv-ledger-file"],
    )
    def test_ledger_state_shapes_allowed_residual(self, isolated_home, command):
        """Pins accepted scan limits rather than intended behavior. Invert these
        to deny if the scan ever resolves a `cd` target or extracts a source
        operand."""
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(command, agent_type="general-purpose"),
                home=isolated_home,
            )
            == "allow"
        )

    @pytest.mark.parametrize(
        "command",
        [
            # `>&` reaches the extractor as a glued target `&`, and the real path
            # is the next word.
            "echo x >& ~/.claude/review-narrative-ledger/f.jsonl",
            # A trailing redirect, not the destination, is the last argument.
            "cp /tmp/x ~/.claude/review-narrative-ledger/f.jsonl 2>/dev/null",
            # The command word is the wrapper or the compound keyword, not the utility.
            'bash -c "cp /tmp/x ~/.claude/review-narrative-ledger/f.jsonl"',
            "if true; then cp /tmp/x ~/.claude/review-narrative-ledger/f.jsonl; fi",
            # The utility's own argument walk compares its name case-sensitively.
            "echo x | TEE ~/.claude/review-narrative-ledger/f.jsonl",
        ],
        ids=[
            "ampersand-redirect",
            "redirect-after-copy-destination",
            "utility-behind-bash-c",
            "utility-behind-compound-keyword",
            "case-varied-tee",
        ],
    )
    def test_ledger_write_shapes_beyond_the_extractor_allowed_residual(
        self, isolated_home, command
    ):
        """Pins accepted extractor limits rather than intended behavior. Invert
        these to deny if the scan ever reads the word after `>&`, skips a
        trailing redirect, unwraps `bash -c` or a compound keyword, or compares
        a write utility's name case-insensitively."""
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(command, agent_type="general-purpose"),
                home=isolated_home,
            )
            == "allow"
        )


class TestSubagentIsIdentifiedByAgentId:
    """`agent_type` is also set for a main session started with `--agent`, so
    only a non-empty `agent_id` marks a subagent. The engineer-row deny and
    the non-roster ledger-state deny key on `agent_id`. Roster membership keys
    on `agent_type`, whatever `agent_id` says, for `append`, marker state and
    ledger state alike.
    """

    @pytest.fixture
    def ledger_file(self, isolated_home, git_repo):
        subprocess.run(["git", "checkout", "-q", "-b", "feature"], cwd=git_repo, check=True)
        return _review_ledger_path(isolated_home, git_repo, "sess-ledger-test")

    @pytest.mark.parametrize("agent_type", NON_ROSTER_AGENT_TYPES)
    def test_engineer_append_allowed_for_a_named_main_session(self, agent_type):
        """`claude --agent <name>`: agent_type present, agent_id absent."""
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(
                    f"{LEDGER} {LEDGER_APPEND_ENGINEER_ARGS}", agent_type=agent_type, agent_id=None
                ),
            )
            == "allow"
        )

    @pytest.mark.parametrize("agent_type", NON_ROSTER_AGENT_TYPES)
    def test_engineer_append_denied_for_a_subagent_with_an_unlisted_agent_type(self, agent_type):
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(
                    f"{LEDGER} {LEDGER_APPEND_ENGINEER_ARGS}",
                    agent_type=agent_type,
                    agent_id=SUBAGENT_ID,
                ),
            )
            == "deny"
        )

    @pytest.mark.parametrize("agent_type", [None, ""], ids=["no-agent-type", "empty-agent-type"])
    def test_engineer_append_denied_for_a_subagent_with_no_agent_type(self, agent_type):
        reason = run_hook_reason(
            ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
            bash_input(
                f"{LEDGER} {LEDGER_APPEND_ENGINEER_ARGS}", agent_type=agent_type, agent_id=SUBAGENT_ID
            ),
        )
        assert reason is not None
        assert "cannot log an engineer decision" in reason
        assert "'unnamed' agent" in reason

    def test_non_string_agent_id_reads_as_a_subagent(self):
        """A contract-violating agent_id is rendered rather than rejected, and
        any non-empty rendering marks a subagent."""
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": f"{LEDGER} {LEDGER_APPEND_ENGINEER_ARGS}"},
            "agent_id": {"unexpected": "object"},
        }
        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, payload) == "deny"

    @pytest.mark.parametrize("agent_type", NO_GATE_RELEASE_AGENTS)
    def test_roster_agent_append_denied_without_an_agent_id(self, agent_type):
        """Roster membership stays on agent_type, so a roster-named session is
        denied an append whether or not the harness sent an agent_id."""
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(f"{LEDGER} {LEDGER_APPEND_ARGS}", agent_type=agent_type, agent_id=None),
            )
            == "deny"
        )

    @pytest.mark.parametrize("agent_type", NO_GATE_RELEASE_AGENTS)
    def test_roster_agent_marker_write_denied_without_an_agent_id(self, agent_type):
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(f"{MARKER} write plan-review", agent_type=agent_type, agent_id=None),
            )
            == "deny"
        )

    @pytest.mark.parametrize("agent_type", NON_ROSTER_AGENT_TYPES)
    @pytest.mark.parametrize(
        "tool_input_builder", [write_input, edit_input, multiedit_input],
        ids=["write", "edit", "multiedit"],
    )
    def test_ledger_file_write_allowed_for_a_named_main_session(
        self, isolated_home, ledger_file, agent_type, tool_input_builder
    ):
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                tool_input_builder(str(ledger_file), agent_type=agent_type, agent_id=None),
                home=isolated_home,
            )
            == "allow"
        )

    @pytest.mark.parametrize("agent_type", NON_ROSTER_AGENT_TYPES)
    @pytest.mark.parametrize(
        "tool_input_builder", [write_input, edit_input, multiedit_input],
        ids=["write", "edit", "multiedit"],
    )
    def test_ledger_file_write_denied_for_a_subagent_with_an_unlisted_agent_type(
        self, isolated_home, ledger_file, agent_type, tool_input_builder
    ):
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                tool_input_builder(str(ledger_file), agent_type=agent_type, agent_id=SUBAGENT_ID),
                home=isolated_home,
            )
            == "deny"
        )

    @pytest.mark.parametrize("agent_type", [None, ""], ids=["no-agent-type", "empty-agent-type"])
    def test_ledger_file_write_denied_for_a_subagent_with_no_agent_type(
        self, isolated_home, ledger_file, agent_type
    ):
        reason = run_hook_reason(
            ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
            write_input(str(ledger_file), agent_type=agent_type, agent_id=SUBAGENT_ID),
            home=isolated_home,
        )
        assert reason is not None
        assert "review-ledger state" in reason
        assert "'unnamed' agent" in reason

    def test_ledger_redirect_allowed_for_a_named_main_session(self, isolated_home):
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(
                    "echo x >> ~/.claude/review-narrative-ledger/abc.def.jsonl",
                    agent_type="general-purpose",
                    agent_id=None,
                ),
                home=isolated_home,
            )
            == "allow"
        )

    @pytest.mark.parametrize("agent_type", [None, "general-purpose"], ids=["no-agent-type", "unlisted"])
    def test_ledger_redirect_denied_for_a_subagent_without_a_roster_agent_type(
        self, isolated_home, agent_type
    ):
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                bash_input(
                    "echo x >> ~/.claude/review-narrative-ledger/abc.def.jsonl",
                    agent_type=agent_type,
                    agent_id=SUBAGENT_ID,
                ),
                home=isolated_home,
            )
            == "deny"
        )

    @pytest.mark.parametrize("agent_type", NO_GATE_RELEASE_AGENTS)
    @pytest.mark.parametrize(
        "tool_input_builder", [write_input, edit_input, multiedit_input],
        ids=["write", "edit", "multiedit"],
    )
    def test_roster_agent_ledger_file_write_denied_without_an_agent_id(
        self, isolated_home, ledger_file, agent_type, tool_input_builder
    ):
        """Roster membership bars ledger state too, so a roster-named session
        cannot hand-write the rows its `append` is denied."""
        reason = run_hook_reason(
            ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
            tool_input_builder(str(ledger_file), agent_type=agent_type, agent_id=None),
            home=isolated_home,
        )
        assert reason is not None
        assert "review-ledger state" in reason

    @pytest.mark.parametrize("agent_type", NO_GATE_RELEASE_AGENTS)
    def test_roster_agent_ledger_redirect_denied_without_an_agent_id(self, isolated_home, agent_type):
        reason = run_hook_reason(
            ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
            bash_input(
                "echo x >> ~/.claude/review-narrative-ledger/abc.def.jsonl",
                agent_type=agent_type,
                agent_id=None,
            ),
            home=isolated_home,
        )
        assert reason is not None
        assert "review-ledger state" in reason

    @pytest.mark.parametrize("via", ["write-tool", "bash-redirect"])
    @pytest.mark.parametrize("agent_type", NO_GATE_RELEASE_AGENTS)
    def test_roster_agent_marker_file_reached_through_the_ledger_directory_denied_without_an_agent_id(
        self, isolated_home, agent_type, via
    ):
        """The ledger kind wins the shape match for this path, but the kernel
        resolves the `..` to a marker file, so the roster bar must not depend
        on which kind the match reports."""
        forged = isolated_home / ".claude" / "review-narrative-ledger" / ".." / "code-review-markers" / "deadbeef.session"
        payload = (
            write_input(str(forged), agent_type=agent_type, agent_id=None)
            if via == "write-tool"
            else bash_input(f"echo x >> {forged}", agent_type=agent_type, agent_id=None)
        )

        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, payload, home=isolated_home) == "deny"

    @pytest.mark.parametrize("via", ["write-tool", "bash-redirect"])
    def test_named_main_session_may_write_through_the_ledger_directory_traversal(self, isolated_home, via):
        """A non-roster named session without an agent_id is a main session:
        neither the marker nor the ledger kind bars it."""
        forged = isolated_home / ".claude" / "review-narrative-ledger" / ".." / "code-review-markers" / "deadbeef.session"
        payload = (
            write_input(str(forged), agent_type="general-purpose", agent_id=None)
            if via == "write-tool"
            else bash_input(f"echo x >> {forged}", agent_type="general-purpose", agent_id=None)
        )

        assert run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, payload, home=isolated_home) == "allow"

    @pytest.mark.parametrize("agent_type", NO_GATE_RELEASE_AGENTS)
    def test_roster_agent_marker_file_write_denied_without_an_agent_id(self, isolated_home, agent_type):
        marker_path = isolated_home / ".claude" / "code-review-markers" / "deadbeef.session"
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                write_input(str(marker_path), agent_type=agent_type, agent_id=None),
                home=isolated_home,
            )
            == "deny"
        )

    def test_marker_file_write_allowed_for_a_subagent_with_no_agent_type(self, isolated_home):
        """Marker state is barred by roster membership alone, so an agent_id
        without a roster agent_type does not bar it."""
        marker_path = isolated_home / ".claude" / "code-review-markers" / "deadbeef.session"
        assert (
            run_hook(
                ENFORCE_MARKER_SCRIPT_SHAPE_HOOK,
                write_input(str(marker_path), agent_type=None, agent_id=SUBAGENT_ID),
                home=isolated_home,
            )
            == "allow"
        )


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
        settings = json.loads((CLAUDE_DIR / "settings.base.json").read_text())
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
