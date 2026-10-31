"""Tests for require-architect-consult.sh."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from helpers import (
    HOOKS_DIR,
    agent_input,
    architect_consult_latch_path,
    assert_cap_engaged,
    bash_input,
    enable_round2_pilot,
    init_git_repo_with_commit,
    reviewer_round_state_path,
    reviewer_round_state_value,
    run_hook,
    run_hook_advisory,
    run_hook_reason,
    scaled_shim_sleep,
    write_architect_consult_latch,
    write_reviewer_round_state,
    write_scaled_timeout_shim,
)

REQUIRE_ARCHITECT_CONSULT_HOOK = HOOKS_DIR / "require-architect-consult.sh"
LOG_REVIEWER_ROUND_HOOK = HOOKS_DIR / "log-reviewer-round.sh"

REVIEWER_PERSONA = "staff-backend-engineer"


def _init_repo(repo: Path) -> None:
    init_git_repo_with_commit(repo, branch="main", content="first\n")


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


def _repo_at_three_states(isolated_home: Path, tmp_path: Path, name: str) -> tuple[Path, list[str]]:
    """Build a repo with exactly three distinct recorded round states -- a
    full re-arm window -- and return (repo, [round1, round2, round3]). Repo is
    left at round3's exact state, so reviewer_round_state_value(repo)
    reproduces round3 (a repeated state); staging a further change produces a
    genuinely new fourth state."""
    repo = tmp_path / name
    _init_repo(repo)
    _stage_change(repo, "first\nround-one\n")
    round1 = reviewer_round_state_value(repo)
    _commit_staged(repo)
    round2 = reviewer_round_state_value(repo)
    _stage_change(repo, "first\nround-one\nround-two\n")
    round3 = reviewer_round_state_value(repo)
    write_reviewer_round_state(isolated_home / ".claude", repo, [round1, round2, round3])
    return repo, [round1, round2, round3]


def _gate_decision(repo: Path, session_id: str) -> str:
    return run_hook(
        REQUIRE_ARCHITECT_CONSULT_HOOK,
        agent_input(session_id=session_id, subagent_type=REVIEWER_PERSONA),
        cwd=repo,
    )


def _write_state_deleting_git_stub(stub_dir: Path, state_file: Path) -> None:
    """A `git` on PATH that deletes `state_file` when invoked as `git ... diff
    ...` -- the staged-diff call _lib_reviewer_round_state_value makes after
    the gate's count read and before its grep -- then runs the real git."""
    real_git = shutil.which("git")
    assert real_git, "git not found in PATH"
    stub_dir.mkdir(exist_ok=True)
    stub = stub_dir / "git"
    stub.write_text(
        "#!/bin/bash\n"
        'for arg in "$@"; do\n'
        f'  [ "$arg" = diff ] && rm -f "{state_file}"\n'
        "done\n"
        f'exec "{real_git}" "$@"\n'
    )
    stub.chmod(0o755)


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

    @pytest.mark.parametrize("pilot_enabled", [False, True], ids=["default", "pilot"])
    def test_latch_present_two_states_plus_new_state_allows(self, isolated_home, tmp_path, pilot_enabled):
        """With a latch the threshold is the re-arm interval (3), with or
        without the round-2 pilot, so a new third state allows where
        test_at_cap_new_state_denies denies the same shape without one."""
        repo, _round1, _round2 = _repo_at_cap(isolated_home, tmp_path, "latch-two-plus-new")
        _stage_change(repo, "first\nround-one\nround-three\n")
        write_architect_consult_latch(isolated_home / ".claude", repo)
        if pilot_enabled:
            enable_round2_pilot(isolated_home / ".claude")
        assert _gate_decision(repo, "s-latch-two-plus-new") == "allow"

    @pytest.mark.parametrize("pilot_enabled", [False, True], ids=["default", "pilot"])
    def test_latch_present_three_states_plus_new_state_denies(self, isolated_home, tmp_path, pilot_enabled):
        """A full re-arm window plus a genuinely new state denies even though
        a latch exists -- the re-arm -- with or without the round-2 pilot."""
        repo, _rounds = _repo_at_three_states(isolated_home, tmp_path, "latch-three-plus-new")
        _stage_change(repo, "first\nround-one\nround-two\nround-four\n")
        write_architect_consult_latch(isolated_home / ".claude", repo)
        if pilot_enabled:
            enable_round2_pilot(isolated_home / ".claude")
        assert _gate_decision(repo, "s-latch-three-plus-new") == "deny"

    def test_latch_present_three_states_repeated_state_allows(self, isolated_home, tmp_path):
        repo, _rounds = _repo_at_three_states(isolated_home, tmp_path, "latch-three-repeat")
        write_architect_consult_latch(isolated_home / ".claude", repo)
        assert _gate_decision(repo, "s-latch-three-repeat") == "allow"

    def test_latch_present_full_window_with_disable_sentinel_allows(self, isolated_home, tmp_path):
        """Same otherwise-denying shape as
        test_latch_present_three_states_plus_new_state_denies, with the
        machine-wide kill switch present."""
        repo, _rounds = _repo_at_three_states(isolated_home, tmp_path, "latch-three-disabled")
        _stage_change(repo, "first\nround-one\nround-two\nround-four\n")
        write_architect_consult_latch(isolated_home / ".claude", repo)
        (isolated_home / ".claude" / ".round-consult-gate-disabled").touch()
        assert _gate_decision(repo, "s-latch-three-disabled") == "allow"

    @pytest.mark.parametrize(
        "has_latch,expected_decision", [(True, "allow"), (False, "deny")], ids=["with-latch", "without-latch"]
    )
    def test_vanished_state_file_decision_follows_the_latch(
        self, isolated_home, tmp_path, has_latch, expected_decision
    ):
        """A consult's reset can delete the state file between the gate's
        count read and its grep. grep then exits 2, so the latch decides:
        allow with one, deny without."""
        repo, _rounds = _repo_at_three_states(isolated_home, tmp_path, "vanish")
        _stage_change(repo, "first\nround-one\nround-two\nround-four\n")
        if has_latch:
            write_architect_consult_latch(isolated_home / ".claude", repo)
        stub_dir = tmp_path / "stub-bin-vanish"
        _write_state_deleting_git_stub(stub_dir, reviewer_round_state_path(isolated_home / ".claude", repo))
        decision = run_hook(
            REQUIRE_ARCHITECT_CONSULT_HOOK,
            agent_input(session_id="s-vanish", subagent_type=REVIEWER_PERSONA),
            cwd=repo,
            extra_env={"PATH": f"{stub_dir}:{os.environ['PATH']}"},
        )
        assert decision == expected_decision

    @pytest.mark.skipif(os.geteuid() == 0, reason="root reads a mode-000 file, so the read never fails")
    @pytest.mark.parametrize(
        "has_latch,expected_decision", [(True, "allow"), (False, "deny")], ids=["with-latch", "without-latch"]
    )
    def test_unreadable_state_file_decision_follows_the_latch(
        self, isolated_home, tmp_path, has_latch, expected_decision
    ):
        """A state file that exists but cannot be read reaches the gate's grep
        through the failed count read: the empty count falls through to the
        hash and grep, and grep exits 2. The latch then decides: allow with
        one, deny without."""
        repo, _rounds = _repo_at_three_states(isolated_home, tmp_path, "unreadable")
        _stage_change(repo, "first\nround-one\nround-two\nround-four\n")
        if has_latch:
            write_architect_consult_latch(isolated_home / ".claude", repo)
        state_file = reviewer_round_state_path(isolated_home / ".claude", repo)
        state_file.chmod(0o000)
        try:
            decision = _gate_decision(repo, "s-unreadable")
        finally:
            state_file.chmod(0o600)
        assert decision == expected_decision

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

        The hook's first jq call (_lib_parse_tool_input_or_deny's shared
        extraction) is already _lib_jq-wrapped and would itself resolve
        within budget on an always-hung jq, which would pass this test even
        if the second call (subagent_type, this test's actual target) were
        still bare jq. Matching narrowly on the subagent_type filter string
        (rather than a call counter) isolates that specific call — a
        call-counter stub would also catch any later, unrelated jq call
        this hook happens to make (e.g. a deny path's own reason-encoding
        call), which would add an unrelated timeout cycle to elapsed time."""
        if not shutil.which("timeout") and not shutil.which("gtimeout"):
            pytest.skip("neither timeout(1) nor gtimeout(1) available — BSD/macOS without coreutils")
        real_jq = shutil.which("jq")
        if not real_jq:
            pytest.skip("jq not found in PATH")
        repo, _round1, _round2 = _repo_at_cap(isolated_home, tmp_path, "hung-jq")
        _stage_change(repo, "first\nround-one\nround-three\n")
        stub_dir = tmp_path / "stub-bin"
        stub_dir.mkdir()
        write_scaled_timeout_shim(stub_dir)
        stub = stub_dir / "jq"
        stub.write_text(
            "#!/bin/bash\n"
            'case "$*" in\n'
            f'  *subagent_type*) sleep {scaled_shim_sleep(10)} ;;\n'
            f'  *) exec "{real_jq}" "$@" ;;\n'
            "esac\n"
        )
        stub.chmod(0o755)

        with assert_cap_engaged(stub_dir, production_cap=5):
            result = run_hook(
                REQUIRE_ARCHITECT_CONSULT_HOOK,
                agent_input(session_id="s-hung-jq", subagent_type=REVIEWER_PERSONA),
                cwd=repo,
                extra_env={"PATH": f"{stub_dir}:{os.environ['PATH']}"},
            )
        assert result == "allow"

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

    def test_live_ready_for_review_active_marker_denies(self, isolated_home, tmp_path):
        """A /ready-for-review run's reviews count toward the consult window,
        so its live active marker does not exempt a spawn that the
        round-count logic denies (contrast
        test_live_plan_review_active_marker_allows)."""
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
        ) == "deny"

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
        """Pins the deny message's content requirements:

        - names the plan-architect MODE=consult dispatch
        - points at the Round-cap architect consult skill section for routing
          the consult's return
        - tells the agent to escalate to the engineer rather than resolve it
          unilaterally
        - never instructs touching the disable sentinel directly
        - tells an agent that already dispatched the consult to report to the
          engineer instead of consulting again, and keeps the do-not-disable
          clause
        - states the threshold as "2 distinct reviewed states" and, with no
          latch, that no architect consult is recorded for this branch
        - mentions "subagent" in the deny message
        """
        repo, _round1, _round2 = _repo_at_cap(isolated_home, tmp_path, "deny-message")
        _stage_change(repo, "first\nround-one\nround-three\n")
        reason = run_hook_reason(
            REQUIRE_ARCHITECT_CONSULT_HOOK,
            agent_input(session_id="s-deny-message", subagent_type=REVIEWER_PERSONA),
            cwd=repo,
        )
        assert reason is not None
        assert "plan-architect MODE=consult" in reason
        assert "Round-cap architect consult" in reason
        assert "report this block to the engineer" in reason
        assert "rather than resolving it unilaterally" in reason
        assert "already dispatched that consult" in reason
        assert "instead of consulting again" in reason
        assert "do not attempt to disable this gate yourself" in reason
        assert ".round-consult-gate-disabled" not in reason
        assert "round_consult_gate" not in reason
        assert "claude-config.toml" not in reason
        assert "2 distinct reviewed states " in reason
        assert "no architect consult is recorded for this branch" in reason
        assert "since the branch's last architect consult" not in reason
        assert "subagent" in reason

    @pytest.mark.parametrize("pilot_enabled", [False, True], ids=["default", "pilot"])
    def test_latch_present_deny_message_reports_the_rearm_window(self, isolated_home, tmp_path, pilot_enabled):
        """A latched branch's deny text reports the re-arm window and the
        since-last-consult wording under both pilot states."""
        repo, _rounds = _repo_at_three_states(isolated_home, tmp_path, "latch-deny-message")
        _stage_change(repo, "first\nround-one\nround-two\nround-four\n")
        write_architect_consult_latch(isolated_home / ".claude", repo)
        if pilot_enabled:
            enable_round2_pilot(isolated_home / ".claude")
        reason = run_hook_reason(
            REQUIRE_ARCHITECT_CONSULT_HOOK,
            agent_input(session_id="s-latch-deny-message", subagent_type=REVIEWER_PERSONA),
            cwd=repo,
        )
        assert reason is not None
        assert "3 distinct reviewed states " in reason
        assert "since the branch's last architect consult" in reason
        assert "no architect consult is recorded for this branch" not in reason
        assert "Round-cap architect consult" in reason
        assert "already dispatched that consult" in reason
        assert "instead of consulting again" in reason
        assert "rather than resolving it unilaterally" in reason
        assert "do not attempt to disable this gate yourself" in reason
        assert ".round-consult-gate-disabled" not in reason
        assert "round_consult_gate" not in reason
        assert "claude-config.toml" not in reason

    @pytest.mark.parametrize(
        "pilot_enabled,expected_denial_states",
        [(False, [3, 6, 9]), (True, [2, 5, 8])],
        ids=["default", "pilot"],
    )
    def test_gate_demands_a_consult_every_three_reviewed_states(
        self, isolated_home, tmp_path, pilot_enabled, expected_denial_states
    ):
        """The recorder never runs for a denied spawn, since a denied spawn
        runs no PostToolUse. The expected states are literals, so a changed
        interval or cap fails here."""
        repo = tmp_path / "cadence"
        _init_repo(repo)
        if pilot_enabled:
            enable_round2_pilot(isolated_home / ".claude")
        reviewer_payload = agent_input(session_id="s-cadence", subagent_type=REVIEWER_PERSONA)
        consult_payload = agent_input(
            session_id="s-cadence", subagent_type="plan-architect", prompt="MODE=consult\nIs the foundation wrong?"
        )

        denial_states = []
        for state_number in range(1, 11):
            _stage_change(repo, f"first\nstate-{state_number}\n")
            decision = run_hook(REQUIRE_ARCHITECT_CONSULT_HOOK, reviewer_payload, cwd=repo)
            if decision == "deny":
                denial_states.append(state_number)
                run_hook_advisory(LOG_REVIEWER_ROUND_HOOK, consult_payload, cwd=repo, home=isolated_home)
                retry_decision = run_hook(REQUIRE_ARCHITECT_CONSULT_HOOK, reviewer_payload, cwd=repo)
                assert retry_decision == "allow", f"state {state_number} still denied after its consult"
            run_hook_advisory(LOG_REVIEWER_ROUND_HOOK, reviewer_payload, cwd=repo, home=isolated_home)

        assert denial_states == expected_denial_states


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

    def test_one_recorded_state_repeated_state_allows_under_pilot(self, isolated_home, tmp_path):
        """At cap=1 under the pilot, a current state equal to the single
        recorded state (the rest of a parallel reviewer batch, once the first
        member's recorder has run) allows -- the repeated-state exemption
        does not depend on the cap."""
        repo, _round1 = _repo_at_one_state(isolated_home, tmp_path, "pilot-one-state-repeat")
        enable_round2_pilot(isolated_home / ".claude")
        assert run_hook(
            REQUIRE_ARCHITECT_CONSULT_HOOK,
            agent_input(session_id="s-pilot-repeat", subagent_type=REVIEWER_PERSONA),
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

    def test_live_ready_for_review_active_marker_denies_under_pilot(self, isolated_home, tmp_path):
        """Pilot variant of test_live_ready_for_review_active_marker_denies:
        under cap=1 the /ready-for-review marker still does not exempt a
        denied spawn."""
        repo, _round1 = _repo_at_one_state(isolated_home, tmp_path, "pilot-ready-for-review-active")
        enable_round2_pilot(isolated_home / ".claude")
        _stage_change(repo, "first\nround-one\nround-two\n")
        sid = "s-pilot-ready-for-review-active"
        marker_dir = isolated_home / ".claude" / ".ready-for-review-active.d"
        marker_dir.mkdir(parents=True)
        (marker_dir / sid).write_text(str(os.getpid()))
        assert run_hook(
            REQUIRE_ARCHITECT_CONSULT_HOOK,
            agent_input(session_id=sid, subagent_type=REVIEWER_PERSONA),
            cwd=repo,
        ) == "deny"

    def test_deny_message_singular_noun_under_pilot(self, isolated_home, tmp_path):
        """Under the pilot with no latch (threshold 1), the deny message's
        STATE_NOUN variable must read "state" (singular) rather than the
        default's "states" (plural), name the literal threshold 1, and say
        no consult is recorded for the branch."""
        repo, _round1 = _repo_at_one_state(isolated_home, tmp_path, "pilot-deny-message")
        enable_round2_pilot(isolated_home / ".claude")
        _stage_change(repo, "first\nround-one\nround-two\n")
        reason = run_hook_reason(
            REQUIRE_ARCHITECT_CONSULT_HOOK,
            agent_input(session_id="s-pilot-deny-message", subagent_type=REVIEWER_PERSONA),
            cwd=repo,
        )
        assert reason is not None
        assert "1 distinct reviewed state " in reason
        assert "no architect consult is recorded for this branch" in reason
        assert "states" not in reason

    def test_directory_at_the_latch_path_denies_with_the_no_consult_wording(self, isolated_home, tmp_path):
        """A directory at the latch path is not a latch: with two recorded
        states and a new one the gate denies, and the deny text carries the
        no-consult clause rather than the since-last-consult one."""
        repo, _round1, _round2 = _repo_at_cap(isolated_home, tmp_path, "latch-is-directory")
        architect_consult_latch_path(isolated_home / ".claude", repo).mkdir(parents=True)
        _stage_change(repo, "first\nround-one\nround-three\n")
        reason = run_hook_reason(
            REQUIRE_ARCHITECT_CONSULT_HOOK,
            agent_input(session_id="s-latch-is-directory", subagent_type=REVIEWER_PERSONA),
            cwd=repo,
        )
        assert reason is not None
        assert "2 distinct reviewed states " in reason
        assert "no architect consult is recorded for this branch" in reason
        assert "since the branch's last architect consult" not in reason

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
