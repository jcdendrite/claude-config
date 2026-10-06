"""Tests for deny-plan-writer-off-plan-writes.sh."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from helpers import (
    HOOKS_DIR,
    edit_input,
    multiedit_input,
    read_input,
    run_hook_reason,
    write_input,
)

HOOK = HOOKS_DIR / "deny-plan-writer-off-plan-writes.sh"

PLAN_WRITER = "plan-architect"

SCAFFOLDED_PLAN = (
    "# Title\n\n## Context\n\nAsk: x\n\n"
    "## Approach\n\n(pending: plan-architect)\n\n"
    "## Critical files\n\n## Verification\n\n## Out of scope\n"
)


def _env_without_git_vars() -> dict[str, str]:
    """Ambient environment minus git's repo-selection and config variables, so
    an enclosing git hook's GIT_DIR cannot misbuild the fixture or the gate run."""
    return {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=_env_without_git_vars(),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _run_gate(
    payload: dict,
    hook_process_cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Raw hook run, so an allow can be told from a deny by empty stdout.

    `env` entries are layered over the ambient environment minus GIT_* vars."""
    return subprocess.run(
        [str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=hook_process_cwd,
        env={**_env_without_git_vars(), **(env or {})},
        check=False,
    )


def _assert_silent_exit_zero(result: subprocess.CompletedProcess) -> None:
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")


def _assert_denied(result: subprocess.CompletedProcess) -> None:
    assert result.returncode == 0, result.stderr
    decision = json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"]
    assert decision == "deny"


def _init_repo(repo: Path) -> None:
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t.com")
    _git(repo, "config", "user.name", "t")
    (repo / "README.md").write_text("seed\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-qm", "seed")


def _write_plan(plans_dir: Path, name: str) -> Path:
    plans_dir.mkdir(parents=True, exist_ok=True)
    plan = plans_dir / name
    plan.write_text(SCAFFOLDED_PLAN)
    return plan


@pytest.fixture(scope="module")
def linked_worktree_with_plan(tmp_path_factory):
    """Read-only layout shared by the module; no test mutates it.

    A repo with a sibling linked worktree and a worktree nested under the main
    tree's .claude/worktrees/. Each holds scaffolded plans. Also a source file,
    plan-directory symlinks, a loose remote-tracking ref under the common git
    dir, and a foreign repo with its own plan."""
    root = tmp_path_factory.mktemp("plan-writer-gate")
    main_repo = root / "main-repo"
    _init_repo(main_repo)
    main_repo_plan = _write_plan(main_repo / ".claude" / "plans", "main-plan.md")

    worktree = root / "linked-worktree"
    _git(main_repo, "worktree", "add", "-q", "-b", "feature", str(worktree))

    nested_worktree = main_repo / ".claude" / "worktrees" / "nested-slug"
    _git(main_repo, "worktree", "add", "-q", "-b", "nested", str(nested_worktree))
    nested_plan = _write_plan(nested_worktree / ".claude" / "plans", "n.md")

    plans_dir = worktree / ".claude" / "plans"
    plan = _write_plan(plans_dir, "p.md")
    nested_subdir_plan = _write_plan(plans_dir / "sub", "x.md")
    non_plan_suffix_file = plans_dir / "notes.rst"
    non_plan_suffix_file.write_text("notes\n")
    lexically_inside_plans_file = worktree / ".claude" / "outside.md"
    lexically_inside_plans_file.write_text("outside\n")

    source_file = worktree / "src" / "x.py"
    source_file.parent.mkdir()
    source_file.write_text("x = 1\n")
    # A plan under the subdirectory's own .claude/plans/, which a lookup that
    # took the subdirectory itself as the repo root would accept.
    subdirectory_plan = _write_plan(worktree / "src" / ".claude" / "plans", "inner-plan.md")

    (plans_dir / "link.md").symlink_to(source_file)

    escape_target_dir = root / "escape-target"
    escape_target_dir.mkdir()
    escaped_file = escape_target_dir / "escaped.md"
    escaped_file.write_text("escaped\n")
    (plans_dir / "escape-dir").symlink_to(escape_target_dir, target_is_directory=True)

    common_git_dir = Path(_git(worktree, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    loose_ref = common_git_dir / "refs" / "remotes" / "origin" / "main"
    loose_ref.parent.mkdir(parents=True)
    loose_ref.write_text(_git(worktree, "rev-parse", "HEAD") + "\n")

    foreign_repo = root / "foreign-repo"
    _init_repo(foreign_repo)
    foreign_plan = _write_plan(foreign_repo / ".claude" / "plans", "f.md")

    for path in (
        plan, source_file, loose_ref, main_repo_plan, nested_plan, nested_subdir_plan,
        non_plan_suffix_file, lexically_inside_plans_file, subdirectory_plan, escaped_file,
        foreign_plan,
    ):
        assert path.is_file(), f"fixture did not create {path}"

    return {
        "main_repo": main_repo,
        "worktree": worktree,
        "nested_worktree": nested_worktree,
        "plan": plan,
        "main_repo_plan": main_repo_plan,
        "nested_plan": nested_plan,
        "nested_subdir_plan": nested_subdir_plan,
        "non_plan_suffix_file": non_plan_suffix_file,
        "lexically_inside_plans_file": lexically_inside_plans_file,
        "source_file": source_file,
        "subdirectory_plan": subdirectory_plan,
        "escaped_file": escaped_file,
        "loose_ref": loose_ref,
        "foreign_repo": foreign_repo,
        "foreign_plan": foreign_plan,
    }


class TestPlanWriterEditAllowed:
    def test_first_authoring_edit_on_plan_allowed(self, linked_worktree_with_plan):
        fixture = linked_worktree_with_plan
        payload = edit_input(
            str(fixture["plan"]),
            agent_type=PLAN_WRITER,
            cwd=str(fixture["worktree"]),
            old_string="(pending: plan-architect)",
            new_string="Approach text.",
        )
        _assert_silent_exit_zero(_run_gate(payload))

    def test_edit_on_plan_allowed_from_subdirectory_cwd(self, linked_worktree_with_plan):
        fixture = linked_worktree_with_plan
        payload = edit_input(
            str(fixture["plan"]),
            agent_type=PLAN_WRITER,
            cwd=str(fixture["worktree"] / "src"),
        )
        _assert_silent_exit_zero(_run_gate(payload))


    def test_dotdot_spelling_that_normalizes_into_plans_allowed(self, linked_worktree_with_plan):
        fixture = linked_worktree_with_plan
        spelled_path = str(fixture["worktree"] / ".claude" / "plans" / ".." / "plans" / "p.md")
        assert ".." in spelled_path
        payload = edit_input(spelled_path, agent_type=PLAN_WRITER, cwd=str(fixture["worktree"]))
        _assert_silent_exit_zero(_run_gate(payload))


class TestPlanWriterDenied:
    def test_write_to_plan_denied(self, linked_worktree_with_plan):
        fixture = linked_worktree_with_plan
        payload = write_input(
            str(fixture["plan"]), agent_type=PLAN_WRITER, cwd=str(fixture["worktree"])
        )
        _assert_denied(_run_gate(payload))

    def test_multiedit_to_plan_denied(self, linked_worktree_with_plan):
        fixture = linked_worktree_with_plan
        payload = multiedit_input(
            str(fixture["plan"]), agent_type=PLAN_WRITER, cwd=str(fixture["worktree"])
        )
        _assert_denied(_run_gate(payload))

    def test_edit_to_source_file_denied(self, linked_worktree_with_plan):
        fixture = linked_worktree_with_plan
        payload = edit_input(
            str(fixture["source_file"]), agent_type=PLAN_WRITER, cwd=str(fixture["worktree"])
        )
        _assert_denied(_run_gate(payload))

    def test_edit_to_nonexistent_plan_denied(self, linked_worktree_with_plan):
        fixture = linked_worktree_with_plan
        missing_plan = fixture["worktree"] / ".claude" / "plans" / "new.md"
        assert not missing_plan.exists()
        payload = edit_input(
            str(missing_plan), agent_type=PLAN_WRITER, cwd=str(fixture["worktree"])
        )
        _assert_denied(_run_gate(payload))

    def test_edit_to_loose_remote_ref_denied(self, linked_worktree_with_plan):
        """The forged-anchor route in plan-review-gate-disarms-on-empty-active-plan-set.md:
        a hand-written ref under the common git dir sits outside the linked
        worktree, where the plan-review gate does not look."""
        fixture = linked_worktree_with_plan
        payload = edit_input(
            str(fixture["loose_ref"]), agent_type=PLAN_WRITER, cwd=str(fixture["worktree"])
        )
        _assert_denied(_run_gate(payload))

    def test_edit_through_symlinked_plan_denied(self, linked_worktree_with_plan):
        """A symlink under .claude/plans/ resolves to its target, which is not a plan."""
        fixture = linked_worktree_with_plan
        symlinked_plan = fixture["worktree"] / ".claude" / "plans" / "link.md"
        assert symlinked_plan.is_symlink()
        payload = edit_input(
            str(symlinked_plan), agent_type=PLAN_WRITER, cwd=str(fixture["worktree"])
        )
        _assert_denied(_run_gate(payload))

    def test_dotdot_spelling_that_normalizes_out_of_plans_denied(self, linked_worktree_with_plan):
        """The path reads as .claude/plans/<name>.md lexically but resolves to .claude/<name>.md."""
        fixture = linked_worktree_with_plan
        spelled_path = str(fixture["worktree"] / ".claude" / "plans" / ".." / "outside.md")
        assert fixture["lexically_inside_plans_file"].is_file()
        payload = edit_input(spelled_path, agent_type=PLAN_WRITER, cwd=str(fixture["worktree"]))
        _assert_denied(_run_gate(payload))

    def test_edit_to_nested_plan_subdirectory_denied(self, linked_worktree_with_plan):
        fixture = linked_worktree_with_plan
        payload = edit_input(
            str(fixture["nested_subdir_plan"]),
            agent_type=PLAN_WRITER,
            cwd=str(fixture["worktree"]),
        )
        _assert_denied(_run_gate(payload))

    def test_edit_to_direct_child_with_a_non_plan_suffix_denied(self, linked_worktree_with_plan):
        fixture = linked_worktree_with_plan
        payload = edit_input(
            str(fixture["non_plan_suffix_file"]),
            agent_type=PLAN_WRITER,
            cwd=str(fixture["worktree"]),
        )
        _assert_denied(_run_gate(payload))

    def test_edit_through_symlinked_plans_subdirectory_denied(self, linked_worktree_with_plan):
        """A directory symlink under .claude/plans/ resolves to a directory outside it."""
        fixture = linked_worktree_with_plan
        escaped_path = fixture["worktree"] / ".claude" / "plans" / "escape-dir" / "escaped.md"
        assert escaped_path.is_file()
        payload = edit_input(
            str(escaped_path), agent_type=PLAN_WRITER, cwd=str(fixture["worktree"])
        )
        _assert_denied(_run_gate(payload))

    def test_edit_to_plan_in_main_repo_from_linked_worktree_cwd_denied(
        self, linked_worktree_with_plan
    ):
        """An existing, well-formed plan in a different tree than the cwd's repo."""
        fixture = linked_worktree_with_plan
        payload = edit_input(
            str(fixture["main_repo_plan"]),
            agent_type=PLAN_WRITER,
            cwd=str(fixture["worktree"]),
        )
        _assert_denied(_run_gate(payload))

    def test_edit_to_nested_worktree_plan_from_main_tree_cwd_denied(
        self, linked_worktree_with_plan
    ):
        """The documented false deny: the nested worktree's plan is not directly
        under the main tree's .claude/plans/."""
        fixture = linked_worktree_with_plan
        payload = edit_input(
            str(fixture["nested_plan"]),
            agent_type=PLAN_WRITER,
            cwd=str(fixture["main_repo"]),
        )
        _assert_denied(_run_gate(payload))

    def test_path_with_trailing_newline_denied(self, linked_worktree_with_plan):
        """Command substitution strips a trailing newline, so without the guard
        the gate would check the existing plan's name, not the one the tool opens."""
        fixture = linked_worktree_with_plan
        payload = edit_input(
            str(fixture["plan"]) + "\n", agent_type=PLAN_WRITER, cwd=str(fixture["worktree"])
        )
        assert payload["tool_input"]["file_path"].endswith("\n")
        _assert_denied(_run_gate(payload))

    def test_foreign_git_dir_env_does_not_launder_the_repo_root(self, linked_worktree_with_plan):
        """GIT_DIR without GIT_WORK_TREE makes git take the cwd as the work tree
        top, so an unset-less gate would accept the subdirectory's own plan."""
        fixture = linked_worktree_with_plan
        subdirectory = fixture["worktree"] / "src"
        payload = edit_input(
            str(fixture["subdirectory_plan"]),
            agent_type=PLAN_WRITER,
            cwd=str(subdirectory),
        )
        _assert_denied(
            _run_gate(payload, env={"GIT_DIR": str(fixture["foreign_repo"] / ".git")})
        )

    def test_foreign_git_work_tree_env_does_not_launder_the_repo_root(
        self, linked_worktree_with_plan
    ):
        """GIT_WORK_TREE alone redirects --show-toplevel to the foreign repo, so an
        unset-less gate would accept that repo's plan."""
        fixture = linked_worktree_with_plan
        payload = edit_input(
            str(fixture["foreign_plan"]),
            agent_type=PLAN_WRITER,
            cwd=str(fixture["worktree"]),
        )
        _assert_denied(
            _run_gate(payload, env={"GIT_WORK_TREE": str(fixture["foreign_repo"])})
        )

    def test_empty_cwd_denied(self, linked_worktree_with_plan):
        """cwd is passed to the hook process so only the empty-.cwd guard can deny."""
        fixture = linked_worktree_with_plan
        payload = edit_input(str(fixture["plan"]), agent_type=PLAN_WRITER, cwd="")
        _assert_denied(_run_gate(payload, hook_process_cwd=fixture["worktree"]))

    def test_cwd_outside_any_repository_denied(self, linked_worktree_with_plan, tmp_path):
        outside_repo = tmp_path / "outside-any-repo"
        outside_repo.mkdir()
        payload = edit_input(
            str(linked_worktree_with_plan["plan"]),
            agent_type=PLAN_WRITER,
            cwd=str(outside_repo),
        )
        _assert_denied(_run_gate(payload))

    def test_relative_path_denied(self, linked_worktree_with_plan):
        """The hook process and .cwd both sit at the worktree root, so the
        relative path would resolve to the real plan if the guard were absent."""
        fixture = linked_worktree_with_plan
        payload = edit_input(
            ".claude/plans/p.md", agent_type=PLAN_WRITER, cwd=str(fixture["worktree"])
        )
        _assert_denied(_run_gate(payload, hook_process_cwd=fixture["worktree"]))

    def test_deny_reason_names_the_return_sections_fallback(self, linked_worktree_with_plan):
        fixture = linked_worktree_with_plan
        reason = run_hook_reason(
            HOOK,
            edit_input(
                str(fixture["source_file"]), agent_type=PLAN_WRITER, cwd=str(fixture["worktree"])
            ),
        )
        assert reason is not None
        assert "return your sections as text" in reason
        assert "Blocked by plan-writer-scope gate" in reason

    def test_unparseable_payload_denies_every_caller(self):
        result = subprocess.run(
            [str(HOOK)], input="not json", capture_output=True, text=True, check=False
        )
        _assert_denied(result)


class TestCallersAndToolsOutsideTheGate:
    def test_code_writer_edit_to_source_file_passes(self, linked_worktree_with_plan):
        fixture = linked_worktree_with_plan
        payload = edit_input(
            str(fixture["source_file"]), agent_type="code-writer", cwd=str(fixture["worktree"])
        )
        _assert_silent_exit_zero(_run_gate(payload))

    def test_main_session_edit_to_source_file_passes(self, linked_worktree_with_plan):
        """No agent_type in the payload reads as the main session."""
        fixture = linked_worktree_with_plan
        payload = edit_input(str(fixture["source_file"]), cwd=str(fixture["worktree"]))
        assert "agent_type" not in payload
        _assert_silent_exit_zero(_run_gate(payload))

    def test_read_tool_from_plan_writer_passes(self, linked_worktree_with_plan):
        """The tool-name filter lets Read through even for a plan-writer."""
        fixture = linked_worktree_with_plan
        payload = {
            **read_input(str(fixture["source_file"])),
            "agent_type": PLAN_WRITER,
            "cwd": str(fixture["worktree"]),
        }
        _assert_silent_exit_zero(_run_gate(payload))
