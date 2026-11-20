"""Gate matrix: the shim's verdict on a command with a leading cd equals the Claude-shaped reference's."""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from . import _cursor_gate_matrix_helpers as gate_matrix_helpers
from ._cursor_gate_matrix_helpers import (
    ALL_REGISTRATIONS,
    Registration,
    World,
    _arm_args,
    _decision,
    _direct,
    _require_worktree_sentinel,
    _run_outside,
    _stage_a_change,
)
from ._cursor_payloads import CONVERSATION_ID, shell_pre_payload, shim_argv, without_git_environment

# The fixture lives once in the helper module. An import would trip F811 on every test parameter that names it.
world = gate_matrix_helpers.world


# --- leading cd ---


@dataclass(frozen=True)
class WriteFamily:
    """A family of gates that evaluate a git write against the repository its working directory resolves to."""

    script: str
    write: str
    git_dash_c_write: Callable[[Path], str] | None
    setup: Callable[[World], None] | None


def _report_an_open_pull_request(world: World) -> None:
    """Make the gh stub print a PR number, so the gate reaches its HEAD and marker check instead of its empty-number exit."""
    gh_stub = world.stubs / "gh"
    gh_stub.write_text(f'#!/bin/sh\necho "gh $*" >> "{world.stubs / "calls.log"}"\necho 1\nexit 0\n')


def _git_in(directory: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(directory), "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid", *args],
        check=True,
        capture_output=True,
        env=without_git_environment(os.environ),
    )


def _add_linked_worktrees(world: World) -> None:
    """A main checkout on its default branch with a worktree-required sentinel, and two linked worktrees of it.

    The root worktree has a staged change and the sibling is clean, so a gate that evaluates the wrong checkout
    reaches a different verdict. All three sit outside the world's watched directories."""
    main = world.base / "linked-main"
    main.mkdir()
    subprocess.run(["git", "init", "-q", str(main)], check=True, capture_output=True, env=without_git_environment(os.environ))
    subprocess.run(
        ["git", "-C", str(main), "symbolic-ref", "HEAD", "refs/heads/main"],
        check=True,
        capture_output=True,
        env=without_git_environment(os.environ),
    )
    (main / "README.md").write_text("fixture\n")
    _git_in(main, "add", "README.md")
    _git_in(main, "commit", "-q", "-m", "initial")
    world.linked_main = main.resolve()
    world.linked_root = (world.base / "linked-root").resolve()
    world.linked_sibling = (world.base / "linked-sibling").resolve()
    _git_in(main, "worktree", "add", "-q", "-b", "linked-feature", str(world.linked_root))
    _git_in(main, "worktree", "add", "-q", "-b", "linked-sibling", str(world.linked_sibling))
    sentinel = main / ".claude" / "worktree-required"
    sentinel.parent.mkdir()
    sentinel.write_text("")
    (world.linked_root / "README.md").write_text("fixture\nstaged line\n")
    _git_in(world.linked_root, "add", "README.md")


WRITE_FAMILIES = {
    "code-review": WriteFamily(
        "require-code-review.sh", "git commit -m x", lambda repo: f"git -C {repo} commit -m x", _stage_a_change
    ),
    "ready-for-review": WriteFamily("require-ready-for-review.sh", "gh pr create --title t --body b", None, None),
    "ready-for-review-push": WriteFamily(
        "require-ready-for-review.sh", "git push origin feature", None, _report_an_open_pull_request
    ),
    "ready-for-review-pr-ready": WriteFamily("require-ready-for-review.sh", "gh pr ready", None, _report_an_open_pull_request),
    "worktree-git-writes": WriteFamily(
        "require-worktree-for-git-writes.sh",
        "git commit -m x",
        lambda repo: f"git -C {repo} commit -m x",
        _require_worktree_sentinel,
    ),
}


# The shapes a leading cd takes. The last one needs a family with a `git -C` spelling of its write.
LEADING_CD_SHAPE_IDS = ("plain-then-repo", "clean-repo-no-root", "clean-repo-second-root", "nonexistent", "plain-then-git-dash-c")
# The shapes with one workspace root that is a linked worktree. The sibling shape reaches it through a clean sibling worktree.
LINKED_WORKTREE_SHAPE_IDS = ("linked-main-then-root", "linked-sibling-then-root")


def _leading_cd_shape(world: World, family: WriteFamily, shape_id: str) -> tuple[str, list[Path], Path] | None:
    """(command, workspace roots, the directory the reference payload's cwd names), or None when the family has no such shape."""
    repo, plain, clean = world.repo, world.plain_directory, world.clean_repo
    if shape_id == "linked-main-then-root":
        return f"cd {world.linked_main} && cd {world.linked_root} && {family.write}", [world.linked_root], world.linked_root
    if shape_id == "linked-sibling-then-root":
        return f"cd {world.linked_sibling} && cd {world.linked_root} && {family.write}", [world.linked_root], world.linked_root
    if shape_id == "plain-then-repo":
        return f"cd {plain} && cd {repo} && {family.write}", [repo], repo
    if shape_id == "clean-repo-no-root":
        return f"cd {clean} && cd {repo} && {family.write}", [repo], repo
    if shape_id == "clean-repo-second-root":
        return f"cd {clean} && cd {repo} && {family.write}", [repo, clean], clean
    if shape_id == "nonexistent":
        return f"cd /nonexistent/nowhere && {family.write}", [repo], repo
    if family.git_dash_c_write is None:
        return None
    return f"cd {plain} && {family.git_dash_c_write(repo)}", [repo], repo


LEADING_CD_CASES = [
    pytest.param(family_id, shape_id, id=f"{family_id}/{shape_id}")
    for family_id, family in WRITE_FAMILIES.items()
    for shape_id in LEADING_CD_SHAPE_IDS
    if shape_id != "plain-then-git-dash-c" or family.git_dash_c_write is not None
]
# A commit is the write the sibling shape names, so only the families that gate a commit run it.
LINKED_WORKTREE_CASES = [
    pytest.param(family_id, shape_id, id=f"{family_id}/{shape_id}")
    for family_id, family in WRITE_FAMILIES.items()
    for shape_id in LINKED_WORKTREE_SHAPE_IDS
    if shape_id != "linked-sibling-then-root" or family.write.startswith("git commit")
]


class TestLeadingCdVerdictEquality:
    def _registration(self, family: WriteFamily) -> Registration:
        return next(r for r in ALL_REGISTRATIONS if r.script == family.script)

    def _verdicts(self, world: World, family_id: str, shape_id: str) -> tuple[str, str]:
        family = WRITE_FAMILIES[family_id]
        registration = self._registration(family)
        world.reset()
        world.apply_setup(family.setup)
        if shape_id in LINKED_WORKTREE_SHAPE_IDS:
            _add_linked_worktrees(world)
        shape = _leading_cd_shape(world, family, shape_id)
        assert shape is not None
        command, roots, reference_directory = shape
        world.reset()
        payload = shell_pre_payload(roots[0], command)
        payload["workspace_roots"] = [str(root) for root in roots]
        shim = _run_outside(world, shim_argv(*_arm_args(registration)), payload)
        assert shim.returncode == 0, shim.stderr
        shim_output = json.loads(shim.stdout)
        shim_verdict = "deny" if shim_output.get("permission") == "deny" else ("none" if shim_output == {} else "other")
        world.reset()
        twin = {
            "hook_event_name": "PreToolUse",
            "session_id": CONVERSATION_ID,
            "cwd": str(reference_directory),
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "tool_use_id": "tool-use-placeholder",
        }
        reference = _direct(
            world, registration, twin, cwd=reference_directory, env=world.cursor_env({"CLAUDE_CONFIG_DIR": str(world.config)})
        )
        return shim_verdict, _decision(reference)

    @pytest.mark.parametrize(("family_id", "shape_id"), [*LEADING_CD_CASES, *LINKED_WORKTREE_CASES])
    def test_the_shim_verdict_equals_the_claude_shaped_reference_verdict(
        self, world: World, family_id: str, shape_id: str
    ) -> None:
        shim_verdict, reference_verdict = self._verdicts(world, family_id, shape_id)
        assert shim_verdict == reference_verdict, (family_id, shape_id)

    @pytest.mark.parametrize(
        "family_id", ["code-review", "worktree-git-writes", "ready-for-review-push", "ready-for-review-pr-ready"]
    )
    def test_a_family_with_a_repo_only_trigger_denies_in_the_shapes_whose_directory_is_the_repo(
        self, world: World, family_id: str
    ) -> None:
        for shape_id in ("plain-then-repo", "plain-then-git-dash-c", "nonexistent"):
            if shape_id == "plain-then-git-dash-c" and WRITE_FAMILIES[family_id].git_dash_c_write is None:
                continue
            shim_verdict, reference_verdict = self._verdicts(world, family_id, shape_id)
            assert (shim_verdict, reference_verdict) == ("deny", "deny"), shape_id
