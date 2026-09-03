"""Tests for require-architect-consult.sh."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from helpers import (
    HOOKS_DIR,
    agent_input,
    architect_consult_latch_path,
    bash_input,
    reviewer_round_state_value,
    run_hook,
    run_hook_reason,
    write_reviewer_round_state,
)

REQUIRE_ARCHITECT_CONSULT_HOOK = HOOKS_DIR / "require-architect-consult.sh"

REVIEWER_PERSONA = "staff-backend-engineer"


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "f.txt").write_text("first\n")
    subprocess.run(["git", "add", "f.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)


def _stage_change(repo: Path, content: str) -> None:
    (repo / "f.txt").write_text(content)
    subprocess.run(["git", "add", "f.txt"], cwd=repo, check=True)


def _commit_staged(repo: Path) -> None:
    subprocess.run(["git", "commit", "-q", "-m", "wip"], cwd=repo, check=True)


def _repo_at_cap(isolated_home: Path, tmp_path: Path, name: str) -> tuple[Path, str, str]:
    """Build a repo with exactly two distinct recorded round states, and
    return (repo, round1_value, round2_value). Repo is left at round2's
    exact state (round2's staged diff is empty, nothing staged) -- calling
    reviewer_round_state_value(repo) now reproduces round2_value exactly,
    which is what a "repeated state" test needs; staging a further, distinct
    change afterward produces a genuinely new third state."""
    repo = tmp_path / name
    _init_repo(repo)
    _stage_change(repo, "first\nround-one\n")
    round1 = reviewer_round_state_value(repo)
    _commit_staged(repo)
    round2 = reviewer_round_state_value(repo)
    config_dir = isolated_home / ".claude"
    write_reviewer_round_state(config_dir, repo, [round1, round2])
    return repo, round1, round2


class TestRequireArchitectConsult:
    def test_below_cap_allows(self, isolated_home, tmp_path):
        """Fewer than 2 recorded round states: allow regardless of the
        current state, and without needing to compute it."""
        repo = tmp_path / "below-cap"
        _init_repo(repo)
        _stage_change(repo, "first\nonly-round\n")
        value = reviewer_round_state_value(repo)
        write_reviewer_round_state(isolated_home / ".claude", repo, [value])
        assert run_hook(
            REQUIRE_ARCHITECT_CONSULT_HOOK,
            agent_input(session_id="s-below-cap", subagent_type=REVIEWER_PERSONA),
            cwd=repo,
        ) == "allow"

    def test_at_cap_repeated_state_allows(self, isolated_home, tmp_path):
        """At the cap, but the current state matches an already-recorded
        round (a repeated fan-out, or a whole parallel batch) -- allow."""
        repo, _round1, _round2 = _repo_at_cap(isolated_home, tmp_path, "at-cap-repeat")
        assert run_hook(
            REQUIRE_ARCHITECT_CONSULT_HOOK,
            agent_input(session_id="s-repeat", subagent_type=REVIEWER_PERSONA),
            cwd=repo,
        ) == "allow"

    def test_at_cap_new_state_denies(self, isolated_home, tmp_path):
        """At the cap, and the current state is a genuinely new, third
        distinct state -- deny."""
        repo, _round1, _round2 = _repo_at_cap(isolated_home, tmp_path, "at-cap-new")
        _stage_change(repo, "first\nround-one\nround-three\n")
        assert run_hook(
            REQUIRE_ARCHITECT_CONSULT_HOOK,
            agent_input(session_id="s-new", subagent_type=REVIEWER_PERSONA),
            cwd=repo,
        ) == "deny"

    def test_at_cap_new_state_with_latch_allows(self, isolated_home, tmp_path):
        """A new third state normally denies (test_at_cap_new_state_denies)
        -- but with the latch already present (a consult ran recently), it
        allows instead. Tested at this otherwise-denying state deliberately,
        not a trivially-allowing one, so a broken latch override wouldn't be
        caught by a vacuous pass."""
        repo, _round1, _round2 = _repo_at_cap(isolated_home, tmp_path, "at-cap-latch")
        _stage_change(repo, "first\nround-one\nround-three\n")
        latch = architect_consult_latch_path(isolated_home / ".claude", repo)
        latch.parent.mkdir(parents=True, exist_ok=True)
        latch.touch()
        assert run_hook(
            REQUIRE_ARCHITECT_CONSULT_HOOK,
            agent_input(session_id="s-latch", subagent_type=REVIEWER_PERSONA),
            cwd=repo,
        ) == "allow"

    def test_at_cap_new_state_with_disable_sentinel_allows(self, isolated_home, tmp_path):
        """Same otherwise-denying state as test_at_cap_new_state_denies, but
        with the machine-wide kill switch present -- allow."""
        repo, _round1, _round2 = _repo_at_cap(isolated_home, tmp_path, "at-cap-disabled")
        _stage_change(repo, "first\nround-one\nround-three\n")
        (isolated_home / ".claude" / ".round-consult-gate-disabled").touch()
        assert run_hook(
            REQUIRE_ARCHITECT_CONSULT_HOOK,
            agent_input(session_id="s-disabled", subagent_type=REVIEWER_PERSONA),
            cwd=repo,
        ) == "allow"

    def test_non_reviewer_subagent_type_allows(self, isolated_home, tmp_path):
        """A non-reviewer-persona dispatch (code-writer, general-purpose,
        Explore, Plan) is never gated -- allow even at an otherwise-denying
        state."""
        repo, _round1, _round2 = _repo_at_cap(isolated_home, tmp_path, "non-reviewer")
        _stage_change(repo, "first\nround-one\nround-three\n")
        assert run_hook(
            REQUIRE_ARCHITECT_CONSULT_HOOK,
            agent_input(session_id="s-code-writer", subagent_type="code-writer"),
            cwd=repo,
        ) == "allow"

    def test_live_plan_review_active_marker_allows(self, isolated_home, tmp_path):
        """A live /plan-review fan-out must not consume a round-counting
        slot -- allow even at an otherwise-denying state."""
        repo, _round1, _round2 = _repo_at_cap(isolated_home, tmp_path, "plan-review-active")
        _stage_change(repo, "first\nround-one\nround-three\n")
        sid = "s-plan-review-active"
        marker_dir = isolated_home / ".claude" / ".plan-review-active.d"
        marker_dir.mkdir(parents=True)
        (marker_dir / sid).write_text(str(os.getpid()))
        assert run_hook(
            REQUIRE_ARCHITECT_CONSULT_HOOK,
            agent_input(session_id=sid, subagent_type=REVIEWER_PERSONA),
            cwd=repo,
        ) == "allow"

    def test_live_ready_for_review_active_marker_allows(self, isolated_home, tmp_path):
        """Same as test_live_plan_review_active_marker_allows, for the
        sibling /ready-for-review active marker."""
        repo, _round1, _round2 = _repo_at_cap(isolated_home, tmp_path, "ready-for-review-active")
        _stage_change(repo, "first\nround-one\nround-three\n")
        sid = "s-ready-for-review-active"
        marker_dir = isolated_home / ".claude" / ".ready-for-review-active.d"
        marker_dir.mkdir(parents=True)
        (marker_dir / sid).write_text(str(os.getpid()))
        assert run_hook(
            REQUIRE_ARCHITECT_CONSULT_HOOK,
            agent_input(session_id=sid, subagent_type=REVIEWER_PERSONA),
            cwd=repo,
        ) == "allow"

    def test_agent_tool_name_gated(self, isolated_home, tmp_path):
        repo, _round1, _round2 = _repo_at_cap(isolated_home, tmp_path, "tool-name-agent")
        _stage_change(repo, "first\nround-one\nround-three\n")
        assert run_hook(
            REQUIRE_ARCHITECT_CONSULT_HOOK,
            agent_input(session_id="s-agent-tool", subagent_type=REVIEWER_PERSONA, tool_name="Agent"),
            cwd=repo,
        ) == "deny"

    def test_task_tool_name_gated(self, isolated_home, tmp_path):
        repo, _round1, _round2 = _repo_at_cap(isolated_home, tmp_path, "tool-name-task")
        _stage_change(repo, "first\nround-one\nround-three\n")
        assert run_hook(
            REQUIRE_ARCHITECT_CONSULT_HOOK,
            agent_input(session_id="s-task-tool", subagent_type=REVIEWER_PERSONA, tool_name="Task"),
            cwd=repo,
        ) == "deny"

    def test_other_tool_name_allows(self, isolated_home, tmp_path):
        """Defense-in-depth: neither Agent nor Task is never denied."""
        repo, _round1, _round2 = _repo_at_cap(isolated_home, tmp_path, "tool-name-other")
        _stage_change(repo, "first\nround-one\nround-three\n")
        assert run_hook(
            REQUIRE_ARCHITECT_CONSULT_HOOK,
            bash_input("ls", session_id="s-other-tool"),
            cwd=repo,
        ) == "allow"

    def test_detached_head_allows(self, isolated_home, tmp_path):
        """No branch name to key the round state on -- allow, per this
        gate's allow-on-state-failure posture."""
        repo = tmp_path / "detached"
        _init_repo(repo)
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
        ).stdout.strip()
        subprocess.run(["git", "checkout", "-q", sha], cwd=repo, check=True)
        assert run_hook(
            REQUIRE_ARCHITECT_CONSULT_HOOK,
            agent_input(session_id="s-detached", subagent_type=REVIEWER_PERSONA),
            cwd=repo,
        ) == "allow"

    def test_unresolvable_repo_root_allows(self, isolated_home, tmp_path):
        """cwd outside any git repository -- allow."""
        non_repo = tmp_path / "not-a-repo"
        non_repo.mkdir()
        assert run_hook(
            REQUIRE_ARCHITECT_CONSULT_HOOK,
            agent_input(session_id="s-no-repo", subagent_type=REVIEWER_PERSONA),
            cwd=non_repo,
        ) == "allow"

    def test_unresolvable_config_dir_allows(self, isolated_home, tmp_path):
        """CLAUDE_CONFIG_DIR set to a relative value cannot be resolved --
        allow rather than falling back to the legacy $HOME/.claude state."""
        repo, _round1, _round2 = _repo_at_cap(isolated_home, tmp_path, "bad-config-dir")
        _stage_change(repo, "first\nround-one\nround-three\n")
        assert run_hook(
            REQUIRE_ARCHITECT_CONSULT_HOOK,
            agent_input(session_id="s-bad-config-dir", subagent_type=REVIEWER_PERSONA),
            cwd=repo,
            home=isolated_home,
            extra_env={"CLAUDE_CONFIG_DIR": "relative/path"},
        ) == "allow"

    def test_payload_cwd_field_resolves_state_over_subprocess_pwd(self, isolated_home, tmp_path):
        """CWD=$(jq -r '.cwd // empty' ...) is the primary CWD-resolution
        signal, falling back to $PWD only when the payload carries no `.cwd`.
        Runs the subprocess from a neutral non-repo directory, but passes the
        real at-cap repo via the JSON `cwd` field: a hook that (wrongly) read
        $PWD instead would find no git repo there and allow (per this gate's
        allow-on-state-failure posture), so asserting "deny" here pins that
        `.cwd` -- not $PWD -- is what actually resolves the repo root."""
        repo, _round1, _round2 = _repo_at_cap(isolated_home, tmp_path, "payload-cwd")
        _stage_change(repo, "first\nround-one\nround-three\n")
        neutral_cwd = tmp_path / "neutral-cwd"
        neutral_cwd.mkdir()
        assert run_hook(
            REQUIRE_ARCHITECT_CONSULT_HOOK,
            agent_input(session_id="s-payload-cwd", subagent_type=REVIEWER_PERSONA, cwd=str(repo)),
            cwd=neutral_cwd,
        ) == "deny"

    def test_deny_message_contents(self, isolated_home, tmp_path):
        """Pins the deny message's content requirement: names the
        plan-architect MODE=consult dispatch, tells the agent to escalate to
        the engineer rather than resolve it unilaterally, and never
        instructs touching the disable sentinel directly."""
        repo, _round1, _round2 = _repo_at_cap(isolated_home, tmp_path, "deny-message")
        _stage_change(repo, "first\nround-one\nround-three\n")
        reason = run_hook_reason(
            REQUIRE_ARCHITECT_CONSULT_HOOK,
            agent_input(session_id="s-deny-message", subagent_type=REVIEWER_PERSONA),
            cwd=repo,
        )
        assert reason is not None
        assert "plan-architect MODE=consult" in reason
        assert "report this block to the engineer" in reason
        assert ".round-consult-gate-disabled" not in reason
        assert "subagent" in reason
