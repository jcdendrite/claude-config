"""Grant-boundary tests shared by the three hooks that print the autonomous-shipping statement.

announce-autonomous-shipping.sh (SessionStart), restore-authorization-boundary-on-compact.sh
(SessionStart compact) and advance-past-commit-stall.sh (Stop) each resolve a repo from the
payload's `.cwd` and print the statement only for an eligible root. They must agree on which
roots are eligible, so every scenario below runs against all three hooks.
"""
from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest
from helpers import HOOKS_DIR, init_git_repo_with_commit, run_hook_stop, stop_input

ANNOUNCE_HOOK = HOOKS_DIR / "announce-autonomous-shipping.sh"
COMPACT_HOOK = HOOKS_DIR / "restore-authorization-boundary-on-compact.sh"
STOP_HOOK = HOOKS_DIR / "advance-past-commit-stall.sh"

STATEMENT_LEAD = "Autonomous shipping is active"
STOP_ASK = "Want me to commit this?"


@dataclass
class Scenario:
    """`cwd` of None omits the payload field. `granted` is whether every hook must print the statement."""

    cwd: str | None
    process_cwd: Path
    granted: bool
    env_overrides: dict[str, str]


def _commit_all(repo: Path, message: str) -> None:
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, check=True)


def _make_dirty(repo: Path) -> None:
    """An untracked file keeps the Stop hook's work-pending check true."""
    (repo / "pending.txt").write_text("pending\n")


def _commit_optout(repo: Path) -> None:
    (repo / ".claude").mkdir(exist_ok=True)
    (repo / ".claude" / "autonomous-shipping-optout").touch()
    _commit_all(repo, "opt out of autonomous shipping")


def _add_linked_worktree(repo: Path, destination: Path) -> Path:
    subprocess.run(
        ["git", "worktree", "add", "-q", "-b", "feature", str(destination)],
        cwd=repo,
        check=True,
    )
    return destination


def _build_scenario(name: str, armed_home: Path, tmp_path: Path) -> Scenario:
    """Builds the filesystem for one named case. The first three cases are the allow controls for the rest."""
    repo = init_git_repo_with_commit(tmp_path / "shipping-repo", branch="main")
    non_repo_dir = tmp_path / "not-a-repo"
    non_repo_dir.mkdir()
    no_env: dict[str, str] = {}

    if name == "repo-root":
        _make_dirty(repo)
        return Scenario(str(repo), non_repo_dir, True, no_env)
    if name == "subdirectory":
        subdirectory = repo / "pkg" / "inner"
        subdirectory.mkdir(parents=True)
        _make_dirty(repo)
        return Scenario(str(subdirectory), non_repo_dir, True, no_env)
    if name == "linked-worktree":
        linked = _add_linked_worktree(repo, tmp_path / "linked-worktree")
        _make_dirty(linked)
        return Scenario(str(linked), non_repo_dir, True, no_env)
    if name == "optout-at-root":
        _commit_optout(repo)
        _make_dirty(repo)
        return Scenario(str(repo), non_repo_dir, False, no_env)
    if name == "optout-with-subdirectory-cwd":
        # A hook that passed the cwd instead of the resolved root would miss the optout here.
        subdirectory = repo / "pkg" / "inner"
        subdirectory.mkdir(parents=True)
        _commit_optout(repo)
        _make_dirty(repo)
        return Scenario(str(subdirectory), non_repo_dir, False, no_env)
    if name == "optout-with-linked-worktree-cwd":
        _commit_optout(repo)
        linked = _add_linked_worktree(repo, tmp_path / "linked-worktree")
        _make_dirty(linked)
        return Scenario(str(linked), non_repo_dir, False, no_env)
    if name == "root-equal-to-home":
        init_git_repo_with_commit(armed_home, branch="main")
        _make_dirty(armed_home)
        return Scenario(str(armed_home), non_repo_dir, False, no_env)
    if name == "project-below-a-home-rooted-repo":
        init_git_repo_with_commit(armed_home, branch="main")
        _make_dirty(armed_home)
        project_below_home = armed_home / "project"
        project_below_home.mkdir()
        return Scenario(str(project_below_home), non_repo_dir, False, no_env)
    if name == "root-is-an-ancestor-of-home":
        init_git_repo_with_commit(tmp_path, branch="main")
        _make_dirty(tmp_path)
        return Scenario(str(armed_home), non_repo_dir, False, no_env)
    if name == "empty-home":
        # The sentinel moves to an explicit config dir so the hooks stay armed with no HOME.
        config_dir = tmp_path / "profile"
        config_dir.mkdir()
        (config_dir / "autonomous-shipping-required").write_text("# machine sentinel\n")
        _make_dirty(repo)
        return Scenario(
            str(repo), non_repo_dir, False, {"HOME": "", "CLAUDE_CONFIG_DIR": str(config_dir)}
        )
    if name == "missing-cwd":
        # The process cwd is an eligible repo, so a `$PWD` fallback would grant.
        _make_dirty(repo)
        return Scenario(None, repo, False, no_env)
    if name == "relative-cwd-that-resolves-to-a-repo":
        _make_dirty(repo)
        return Scenario(".", repo, False, no_env)
    if name == "relative-subdirectory-cwd-that-resolves-to-a-repo":
        (repo / "pkg").mkdir()
        _make_dirty(repo)
        return Scenario("pkg", repo, False, no_env)
    if name == "empty-cwd":
        _make_dirty(repo)
        return Scenario("", repo, False, no_env)
    if name == "cwd-outside-a-repo":
        _make_dirty(repo)
        return Scenario(str(non_repo_dir), repo, False, no_env)
    raise AssertionError(f"unknown scenario {name}")


SCENARIO_NAMES = [
    "repo-root",
    "subdirectory",
    "linked-worktree",
    "optout-at-root",
    "optout-with-subdirectory-cwd",
    "optout-with-linked-worktree-cwd",
    "root-equal-to-home",
    "project-below-a-home-rooted-repo",
    "root-is-an-ancestor-of-home",
    "empty-home",
    "missing-cwd",
    "relative-cwd-that-resolves-to-a-repo",
    "relative-subdirectory-cwd-that-resolves-to-a-repo",
    "empty-cwd",
    "cwd-outside-a-repo",
]


def _hook_env(armed_home: Path, scenario: Scenario) -> dict[str, str]:
    return {**os.environ, "HOME": str(armed_home), **scenario.env_overrides}


def _session_start_statement(hook: Path, source: str, armed_home: Path, scenario: Scenario) -> str:
    """Returns the statement a SessionStart hook printed, or "" when its additionalContext carried none."""
    payload: dict = {"source": source}
    if scenario.cwd is not None:
        payload["cwd"] = scenario.cwd
    result = subprocess.run(
        [str(hook)],
        input=json.dumps(payload).encode(),
        capture_output=True,
        cwd=scenario.process_cwd,
        env=_hook_env(armed_home, scenario),
        check=False,
    )
    assert result.returncode == 0
    if not result.stdout:
        return ""
    additional_context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    start = additional_context.find(STATEMENT_LEAD)
    return "" if start == -1 else additional_context[start:]


def _announce_statement(armed_home: Path, scenario: Scenario) -> str:
    return _session_start_statement(ANNOUNCE_HOOK, "startup", armed_home, scenario)


def _compact_statement(armed_home: Path, scenario: Scenario) -> str:
    return _session_start_statement(COMPACT_HOOK, "compact", armed_home, scenario)


def _stop_statement(armed_home: Path, scenario: Scenario) -> str:
    payload = stop_input(
        STOP_ASK,
        session_id="s",
        prompt_id="p1",
        permission_mode="default",
        cwd=scenario.cwd,
    )
    blocked = run_hook_stop(
        STOP_HOOK,
        payload,
        cwd=scenario.process_cwd,
        home=armed_home,
        extra_env=scenario.env_overrides,
    )
    if blocked is None:
        return ""
    reason = blocked["reason"]
    return reason[reason.find(STATEMENT_LEAD) :]


HOOK_STATEMENTS: dict[str, Callable[[Path, Scenario], str]] = {
    "announce": _announce_statement,
    "compact": _compact_statement,
    "stop": _stop_statement,
}


@pytest.mark.parametrize("hook_name", list(HOOK_STATEMENTS))
@pytest.mark.parametrize("scenario_name", SCENARIO_NAMES)
def test_statement_is_printed_only_for_an_eligible_root(
    armed_home, tmp_path, shipping_statement, hook_name, scenario_name
):
    scenario = _build_scenario(scenario_name, armed_home, tmp_path)

    printed = HOOK_STATEMENTS[hook_name](armed_home, scenario)

    if scenario.granted:
        assert printed.startswith(shipping_statement(tmp_path / "shipping-repo"))
    else:
        assert printed == ""
