"""Grant-boundary tests shared by the two hooks that act on the autonomous-shipping root guard.

restore-authorization-boundary-on-compact.sh (SessionStart compact) and advance-past-commit-stall.sh
(Stop) each resolve a repo from the payload's `.cwd`. The compact hook prints the statement and the
Stop hook fires only for an eligible root. They must agree on which roots are eligible, so every
scenario below runs against both hooks.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest
from helpers import (
    AUTONOMOUS_SHIPPING_STATEMENT_LEAD,
    HOOKS_DIR,
    add_linked_worktree,
    init_git_repo_with_commit,
    run_additional_context_session_start_hook,
    run_hook_stop,
    session_start_additional_context,
    stop_input,
)

COMPACT_HOOK = HOOKS_DIR / "restore-authorization-boundary-on-compact.sh"
STOP_HOOK = HOOKS_DIR / "advance-past-commit-stall.sh"

STOP_ASK = "Want me to commit this?"

# Not a default-branch name, and not "feature", which add_linked_worktree creates. The Stop hook
# stays silent on the default branch, so a denied scenario on "main" would be denied for its branch,
# not its root.
SCENARIO_BRANCH = "shipping-work"


@dataclass
class Scenario:
    """`cwd` of None omits the payload field. `granted` is whether every hook must print the statement or fire."""

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


def _write_machine_config(isolated_home: Path, content: str) -> None:
    (isolated_home / ".claude" / "claude-config.toml").write_text(content)


def _build_scenario(name: str, isolated_home: Path, tmp_path: Path, non_repo_dir: Path) -> Scenario:
    """Builds the filesystem for one named case. The cases with `granted` true are the allow controls for the rest."""
    repo = init_git_repo_with_commit(tmp_path / "shipping-repo", branch=SCENARIO_BRANCH)
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
        linked = add_linked_worktree(repo, tmp_path / "linked-worktree")
        _make_dirty(linked)
        return Scenario(str(linked), non_repo_dir, True, no_env)
    if name == "repo-beneath-home":
        # Nearly every real repo lives under HOME, so a rule that denied any root under HOME would disable the feature.
        repo_beneath_home = init_git_repo_with_commit(isolated_home / "projects" / "repo", branch=SCENARIO_BRANCH)
        _make_dirty(repo_beneath_home)
        return Scenario(str(repo_beneath_home), non_repo_dir, True, no_env)
    if name == "nested-own-repo-beneath-a-home-rooted-work-tree":
        init_git_repo_with_commit(isolated_home, branch=SCENARIO_BRANCH)
        nested_repo = init_git_repo_with_commit(isolated_home / "projects" / "repo", branch=SCENARIO_BRANCH)
        _make_dirty(nested_repo)
        return Scenario(str(nested_repo), non_repo_dir, True, no_env)
    if name == "symlinked-cwd-to-a-repo-beneath-home":
        repo_beneath_home = init_git_repo_with_commit(isolated_home / "projects" / "repo", branch=SCENARIO_BRANCH)
        _make_dirty(repo_beneath_home)
        cwd_link = tmp_path / "repo-link"
        cwd_link.symlink_to(repo_beneath_home, target_is_directory=True)
        return Scenario(str(cwd_link), non_repo_dir, True, no_env)
    if name == "space-and-non-ascii-in-the-repo-path":
        try:
            repo_with_awkward_name = init_git_repo_with_commit(
                tmp_path / "projects with space" / "répo ünï", branch=SCENARIO_BRANCH
            )
        except (OSError, UnicodeError) as error:
            pytest.skip(f"filesystem rejects a space and non-ASCII path: {error}")
        _make_dirty(repo_with_awkward_name)
        return Scenario(str(repo_with_awkward_name), non_repo_dir, True, no_env)
    if name == "leftover-committed-repo-opt-out-file":
        # Nothing reads the file, so a repo that still commits one stays granted.
        (repo / ".claude").mkdir()
        (repo / ".claude" / "autonomous-shipping-optout").touch()
        _commit_all(repo, "leave an old per-repo file")
        _make_dirty(repo)
        return Scenario(str(repo), non_repo_dir, True, no_env)
    if name == "leftover-machine-config-row":
        _write_machine_config(isolated_home, "autonomous_shipping = false\n")
        _make_dirty(repo)
        return Scenario(str(repo), non_repo_dir, True, no_env)
    if name == "leftover-machine-file-in-the-config-dir":
        (isolated_home / ".claude" / "autonomous-shipping-required").write_text("# old machine file\n")
        _make_dirty(repo)
        return Scenario(str(repo), non_repo_dir, True, no_env)
    if name == "relocated-config-dir":
        config_dir = tmp_path / "relocated-config"
        config_dir.mkdir()
        _make_dirty(repo)
        return Scenario(str(repo), non_repo_dir, True, {"CLAUDE_CONFIG_DIR": str(config_dir)})
    if name == "root-equal-to-home":
        init_git_repo_with_commit(isolated_home, branch=SCENARIO_BRANCH)
        _make_dirty(isolated_home)
        return Scenario(str(isolated_home), non_repo_dir, False, no_env)
    if name == "project-below-a-home-rooted-repo":
        init_git_repo_with_commit(isolated_home, branch=SCENARIO_BRANCH)
        _make_dirty(isolated_home)
        project_below_home = isolated_home / "project"
        project_below_home.mkdir()
        return Scenario(str(project_below_home), non_repo_dir, False, no_env)
    if name == "root-is-an-ancestor-of-home":
        init_git_repo_with_commit(tmp_path, branch=SCENARIO_BRANCH)
        _make_dirty(tmp_path)
        return Scenario(str(isolated_home), non_repo_dir, False, no_env)
    if name == "empty-home":
        # An explicit config dir keeps the hooks running with no HOME.
        config_dir = tmp_path / "profile"
        config_dir.mkdir()
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
    "repo-beneath-home",
    "nested-own-repo-beneath-a-home-rooted-work-tree",
    "symlinked-cwd-to-a-repo-beneath-home",
    "space-and-non-ascii-in-the-repo-path",
    "leftover-committed-repo-opt-out-file",
    "leftover-machine-config-row",
    "leftover-machine-file-in-the-config-dir",
    "relocated-config-dir",
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


def _compact_statement(isolated_home: Path, scenario: Scenario) -> str:
    """Returns the statement the compact hook printed, or "" when its additionalContext carried none."""
    payload: dict = {"source": "compact"}
    if scenario.cwd is not None:
        payload["cwd"] = scenario.cwd
    result = run_additional_context_session_start_hook(
        COMPACT_HOOK,
        payload,
        isolated_home,
        process_cwd=scenario.process_cwd,
        extra_env=scenario.env_overrides,
    )
    assert result.returncode == 0
    if not result.stdout:
        return ""
    additional_context = session_start_additional_context(result)
    start = additional_context.find(AUTONOMOUS_SHIPPING_STATEMENT_LEAD)
    return "" if start == -1 else additional_context[start:]


def _stop_hook_fired(isolated_home: Path, scenario: Scenario) -> bool:
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
        home=isolated_home,
        extra_env=scenario.env_overrides,
    )
    return blocked is not None


@pytest.mark.parametrize("scenario_name", SCENARIO_NAMES)
def test_statement_is_printed_only_for_an_eligible_root(
    isolated_home, tmp_path, non_repo_dir, shipping_statement, scenario_name
):
    scenario = _build_scenario(scenario_name, isolated_home, tmp_path, non_repo_dir)

    printed = _compact_statement(isolated_home, scenario)

    if scenario.granted:
        assert printed == shipping_statement
    else:
        assert printed == ""


@pytest.mark.parametrize("scenario_name", SCENARIO_NAMES)
def test_stop_hook_fires_only_for_an_eligible_root(isolated_home, tmp_path, non_repo_dir, scenario_name):
    scenario = _build_scenario(scenario_name, isolated_home, tmp_path, non_repo_dir)

    assert _stop_hook_fired(isolated_home, scenario) is scenario.granted
