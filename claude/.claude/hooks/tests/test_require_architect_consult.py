"""Tests for require-architect-consult.sh."""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest
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


def _repo_at_one_state(isolated_home: Path, tmp_path: Path, name: str) -> tuple[Path, str]:
    """Build a repo with exactly one distinct recorded round state -- "at
    cap" under the round-2 pilot's cap=1 -- and return (repo, round1_value).
    Repo is left at round1's exact state (its own staged diff reproduces
    round1 exactly), one state short of _repo_at_cap's shape, since cap=1
    trips at a single recorded state rather than two."""
    repo = tmp_path / name
    _init_repo(repo)
    _stage_change(repo, "first\nround-one\n")
    round1 = reviewer_round_state_value(repo)
    config_dir = isolated_home / ".claude"
    write_reviewer_round_state(config_dir, repo, [round1])
    return repo, round1


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

    @pytest.mark.timing
    def test_subagent_type_hung_jq_allows_within_timeout(self, isolated_home, tmp_path):
        """The subagent_type extraction at line ~63 uses _lib_jq, not bare jq
        (GH-489's uncapped-secondary-jq defect class) — a hung jq binary must
        resolve within the 5s _lib_jq backstop rather than blocking every
        Agent/Task dispatch indefinitely. The expected outcome is allow, not
        deny: an empty SUBAGENT_TYPE from a failed extraction reads as "not a
        reviewer-persona dispatch" (`_lib_is_reviewer_persona "" || exit 0`),
        the same fail-open direction this hook's header documents for state
        failures — the property under test is bounded latency, not a
        decision flip.

        The hook's first jq call (_lib_parse_tool_input_or_deny's six-field
        extraction) is already _lib_jq-wrapped and would itself resolve
        within budget on an always-hung jq, which would pass this test even
        if the second call (subagent_type, this test's actual target) were
        still bare jq. Matching narrowly on the subagent_type filter string
        (rather than a call counter) isolates that specific call — a
        call-counter stub would also catch any later, unrelated jq call
        this hook happens to make (e.g. a deny path's own reason-encoding
        call), which would add an unrelated timeout cycle to elapsed time."""
        if not shutil.which("timeout"):
            pytest.skip("timeout(1) not available — BSD/macOS without coreutils")
        real_jq = shutil.which("jq")
        if not real_jq:
            pytest.skip("jq not found in PATH")
        repo, _round1, _round2 = _repo_at_cap(isolated_home, tmp_path, "hung-jq")
        _stage_change(repo, "first\nround-one\nround-three\n")
        stub_dir = tmp_path / "stub-bin"
        stub_dir.mkdir()
        stub = stub_dir / "jq"
        stub.write_text(
            "#!/bin/bash\n"
            'case "$*" in\n'
            '  *subagent_type*) sleep 10 ;;\n'
            f'  *) exec "{real_jq}" "$@" ;;\n'
            "esac\n"
        )
        stub.chmod(0o755)

        start = time.monotonic()
        result = run_hook(
            REQUIRE_ARCHITECT_CONSULT_HOOK,
            agent_input(session_id="s-hung-jq", subagent_type=REVIEWER_PERSONA),
            cwd=repo,
            extra_env={"PATH": f"{stub_dir}:{os.environ['PATH']}"},
        )
        elapsed = time.monotonic() - start
        assert result == "allow"
        assert elapsed < 9.5, (
            f"expected the 5s _lib_jq timeout to fire on the second (subagent_type) "
            f"jq call (stub sleeps 10s if it does not), took {elapsed:.1f}s"
        )

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
        assert "2 distinct reviewed states " in reason
        assert "subagent" in reason


class TestRound2PilotCap:
    """The round-2 pilot sentinel (`.round-consult-round2-pilot`) lowers the
    resolved cap from 2 to 1 -- see
    docs/design-decisions/round2-consult-trigger-pilot.md. Every case here
    pins that the sentinel changes only the cap-comparison outcome, never
    the bypass-check ordering that precedes cap resolution."""

    def test_one_recorded_state_new_state_denies_under_pilot(self, isolated_home, tmp_path):
        """At cap=1 under the pilot, a single recorded state plus a
        genuinely new second state denies -- the pilot's whole point, one
        round earlier than the default cap=2's third-state trigger."""
        repo, _round1 = _repo_at_one_state(isolated_home, tmp_path, "pilot-one-state-new")
        (isolated_home / ".claude" / ".round-consult-round2-pilot").touch()
        _stage_change(repo, "first\nround-one\nround-two\n")
        assert run_hook(
            REQUIRE_ARCHITECT_CONSULT_HOOK,
            agent_input(session_id="s-pilot-new", subagent_type=REVIEWER_PERSONA),
            cwd=repo,
        ) == "deny"

    def test_one_recorded_state_new_state_allows_without_pilot_sentinel(self, isolated_home, tmp_path):
        """Default-path regression guard: the identical one-recorded-state,
        new-second-state setup that denies under the pilot
        (test_one_recorded_state_new_state_denies_under_pilot) must still
        allow with the sentinel absent -- the resolver's default branch
        keeps the cap at 2, so a single recorded state stays below cap."""
        repo, _round1 = _repo_at_one_state(isolated_home, tmp_path, "no-pilot-one-state-new")
        _stage_change(repo, "first\nround-one\nround-two\n")
        assert run_hook(
            REQUIRE_ARCHITECT_CONSULT_HOOK,
            agent_input(session_id="s-no-pilot-new", subagent_type=REVIEWER_PERSONA),
            cwd=repo,
        ) == "allow"

    def test_empty_state_file_allows_under_pilot(self, isolated_home, tmp_path):
        """No recorded state yet -- even under cap=1, allow without paying
        for the current state's own hash (the below-cap fast path is
        unaffected by which cap is in effect)."""
        repo = tmp_path / "pilot-empty-state"
        _init_repo(repo)
        _stage_change(repo, "first\nonly-round\n")
        (isolated_home / ".claude" / ".round-consult-round2-pilot").touch()
        assert run_hook(
            REQUIRE_ARCHITECT_CONSULT_HOOK,
            agent_input(session_id="s-pilot-empty", subagent_type=REVIEWER_PERSONA),
            cwd=repo,
        ) == "allow"

    def test_live_plan_review_active_marker_allows_under_pilot(self, isolated_home, tmp_path):
        """Regression guard for the pilot plan's row 15: under cap=1, the
        active-bypass marker check must still run BEFORE cap resolution, or
        the pilot silently degrades into the round-1 trigger design the
        case study rejected. The sibling sentinel-absent test
        (test_live_plan_review_active_marker_allows) never exercises the
        pilot sentinel at all, so it would keep passing unchanged even if
        the resolver's sentinel-present branch were wired in after the
        marker check instead of before it -- this variant, with the
        sentinel present and the fixture at cap=1, is the only one that
        actually pins the ordering under pilot conditions."""
        repo, _round1 = _repo_at_one_state(isolated_home, tmp_path, "pilot-plan-review-active")
        (isolated_home / ".claude" / ".round-consult-round2-pilot").touch()
        _stage_change(repo, "first\nround-one\nround-two\n")
        sid = "s-pilot-plan-review-active"
        marker_dir = isolated_home / ".claude" / ".plan-review-active.d"
        marker_dir.mkdir(parents=True)
        (marker_dir / sid).write_text(str(os.getpid()))
        assert run_hook(
            REQUIRE_ARCHITECT_CONSULT_HOOK,
            agent_input(session_id=sid, subagent_type=REVIEWER_PERSONA),
            cwd=repo,
        ) == "allow"

    def test_live_ready_for_review_active_marker_allows_under_pilot(self, isolated_home, tmp_path):
        """Same regression guard as
        test_live_plan_review_active_marker_allows_under_pilot, for the
        sibling /ready-for-review active marker."""
        repo, _round1 = _repo_at_one_state(isolated_home, tmp_path, "pilot-ready-for-review-active")
        (isolated_home / ".claude" / ".round-consult-round2-pilot").touch()
        _stage_change(repo, "first\nround-one\nround-two\n")
        sid = "s-pilot-ready-for-review-active"
        marker_dir = isolated_home / ".claude" / ".ready-for-review-active.d"
        marker_dir.mkdir(parents=True)
        (marker_dir / sid).write_text(str(os.getpid()))
        assert run_hook(
            REQUIRE_ARCHITECT_CONSULT_HOOK,
            agent_input(session_id=sid, subagent_type=REVIEWER_PERSONA),
            cwd=repo,
        ) == "allow"

    def test_deny_message_singular_noun_under_pilot(self, isolated_home, tmp_path):
        """Under the pilot (cap=1), the deny message's STATE_NOUN variable
        must read "state" (singular) rather than the cap=2 default's
        "states" (plural), and must name the literal cap value 1."""
        repo, _round1 = _repo_at_one_state(isolated_home, tmp_path, "pilot-deny-message")
        (isolated_home / ".claude" / ".round-consult-round2-pilot").touch()
        _stage_change(repo, "first\nround-one\nround-two\n")
        reason = run_hook_reason(
            REQUIRE_ARCHITECT_CONSULT_HOOK,
            agent_input(session_id="s-pilot-deny-message", subagent_type=REVIEWER_PERSONA),
            cwd=repo,
        )
        assert reason is not None
        assert "1 distinct reviewed state " in reason
        assert "states" not in reason

    def test_disable_sentinel_wins_even_with_pilot_sentinel_present(self, isolated_home, tmp_path):
        """Precedence: the machine-wide kill switch is checked before any
        git call or cap resolution, so it silences the gate even with the
        pilot sentinel also present."""
        repo, _round1 = _repo_at_one_state(isolated_home, tmp_path, "pilot-and-disabled")
        (isolated_home / ".claude" / ".round-consult-round2-pilot").touch()
        (isolated_home / ".claude" / ".round-consult-gate-disabled").touch()
        _stage_change(repo, "first\nround-one\nround-two\n")
        assert run_hook(
            REQUIRE_ARCHITECT_CONSULT_HOOK,
            agent_input(session_id="s-pilot-and-disabled", subagent_type=REVIEWER_PERSONA),
            cwd=repo,
        ) == "allow"
