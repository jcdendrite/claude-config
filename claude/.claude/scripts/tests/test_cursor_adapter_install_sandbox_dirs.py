"""The directories cursor-adapter.py's sandbox grant lists, each written by an identity consumer."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import types
from collections.abc import Iterable

import pytest
from helpers import CLAUDE_DIR

from . import _cursor_install_helpers as install_helpers
from ._cursor_install_helpers import (
    World,
)

sys.path.insert(0, str(CLAUDE_DIR))
from hooks.tests._cursor_payloads import (  # noqa: E402 (the import needs the sys.path entry above)
    IS_ROOT,
    write_sh_script,
)

# The fixtures live once in the helper module. An import would trip F811 on every test parameter that names one.
world = install_helpers.world
fast_world = install_helpers.fast_world


# --- the directories the grant lists, written by each identity consumer ---

# marker.sh's kinds that need more than a repository with one staged change, and the consumers no row drives. The meta row
# below fails a kind that is in neither a row nor this table.
WRITABLE_DIRS_NOT_DRIVEN = {
    "cumulative-review": "needs a subject recorded by pr-diff-against-base.sh --record against a base ref",
    "review-pr": "needs the provenance file review-pr-acquire.sh writes after a gh lookup",
    "pr-diff-against-base.sh": "needs a base ref and a recorded subject",
    "review-ledger.sh": "needs a ledger fixture",
    "review-pr-acquire.sh": "needs a gh lookup",
    "handoff-record-conversion.sh": "its .handoff-nudge.log append is the one write the grant leaves out",
}
DRIVEN_DIRECTORIES_BY_ROW = {
    "code-review": "code-review-markers",
    "skill-review": "skill-review-markers",
    "plan-review": "plan-review-markers",
    "ready-for-review": "ready-for-review-markers",
    "verification": "verification-markers",
}
ACTIVATE_DIRECTORIES = {
    "plan-review": ".plan-review-active.d",
    "ready-for-review": ".ready-for-review-active.d",
    "respond-pr": ".respond-pr-active.d",
    "memory-skill": ".memory-skill-active.d",
    "handoff": ".handoff-active.d",
}
# The two directories one `activate plan-review` row covers: the hook log-routing-read.sh writes the first, which the row
# seeds, and marker.sh backfills the second from it.
PLAN_REVIEW_READ_DIRECTORIES = (".plan-review-pending-read.d", ".plan-review-routing-read.d")
# The listed directories no row drives, each with why. The meta row below fails a listed directory that is in neither a row
# nor this table.
DIRECTORIES_NOT_DRIVEN = {
    "code-review-diff-markers": "pr-diff-against-base.sh --staged --diff-file writes it, and no row runs that script",
    "cumulative-review-markers": "needs a subject recorded by pr-diff-against-base.sh --record against a base ref",
    "cumulative-review-subject-markers": "pr-diff-against-base.sh --record writes it and needs a base ref",
    "cumulative-review-diff-markers": "pr-diff-against-base.sh --diff-file writes it and needs a base ref",
    "review-pr-markers": "needs the provenance file review-pr-acquire.sh writes after a gh lookup",
    ".review-pr-active.d": "review-pr-acquire.sh writes it after a gh lookup",
    "review-narrative-ledger": "review-ledger.sh needs a ledger fixture",
}
WRITABLE_DIRS_SESSION_ID = "sandbox-dirs-session"


def writable_directories_without_a_row_or_a_reason(listed: Iterable[str]) -> set[str]:
    accounted = {
        *DRIVEN_DIRECTORIES_BY_ROW.values(),
        *ACTIVATE_DIRECTORIES.values(),
        *PLAN_REVIEW_READ_DIRECTORIES,
        *DIRECTORIES_NOT_DRIVEN,
    }
    return set(listed) - accounted


def marker_script_array(name: str) -> list[str]:
    text = (CLAUDE_DIR / "scripts" / "marker.sh").read_text()
    match = re.search(rf"^{name}=\(([^)]*)\)", text, re.MULTILINE)
    assert match, name
    return match.group(1).split()


class TestSandboxWritableDirs:
    """Runs the identity consumers' write paths against a config dir that is writable only in the listed directories."""

    @pytest.fixture
    def scene(self, world: World) -> types.SimpleNamespace:
        if IS_ROOT:
            pytest.skip("absent: root writes through mode bits")
        config = world.tmp_path / "restricted-config"
        config.mkdir()
        for name in world.adapter.SANDBOX_WRITABLE_DIRECTORIES:
            (config / name).mkdir(mode=0o700)
        (config / "settings.json").write_text("{}")
        (config / "settings.json").chmod(0o400)
        config.chmod(0o500)
        stub_bin = world.tmp_path / "failing-ps-bin"
        stub_bin.mkdir()
        write_sh_script(stub_bin / "ps", "exit 1\n")
        repo = world.tmp_path / "repo"
        repo.mkdir()
        for command in (
            ["git", "init", "-q", "-b", "main", "."],
            ["git", "config", "user.email", "a@b"],
            ["git", "config", "user.name", "n"],
        ):
            subprocess.run(command, cwd=repo, check=True, capture_output=True)
        (repo / "f").write_text("a\n")
        (repo / "skills" / "example").mkdir(parents=True)
        (repo / "skills" / "example" / "SKILL.md").write_text("a\n")
        subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True, capture_output=True)
        yield types.SimpleNamespace(world=world, config=config, stub_bin=stub_bin, repo=repo)
        config.chmod(0o700)

    def run_marker(
        self, scene: types.SimpleNamespace, *argv: str, host_pid: str = "424242"
    ) -> subprocess.CompletedProcess:
        """Runs marker.sh as a sandboxed agent Shell call does: the pin's exports and a `ps` that fails."""
        environment = {
            "HOME": str(scene.world.home),
            "PATH": f"{scene.stub_bin}:{scene.world.bin_dir}:/usr/bin:/bin",
            "CLAUDE_CONFIG_DIR": str(scene.config),
            "CLAUDE_CONFIG_ADAPTER_SESSION_ID": WRITABLE_DIRS_SESSION_ID,
            "CLAUDE_CONFIG_ADAPTER_HOST_PID": host_pid,
        }
        return subprocess.run(
            ["bash", str(CLAUDE_DIR / "scripts" / "marker.sh"), *argv],
            cwd=scene.repo,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )

    def stage_change(self, scene: types.SimpleNamespace, path: str) -> None:
        (scene.repo / path).write_text("a\nb\n")
        subprocess.run(["git", "add", path], cwd=scene.repo, check=True, capture_output=True)

    def written(self, scene: types.SimpleNamespace, directory: str) -> list[str]:
        return sorted(os.listdir(scene.config / directory))

    @pytest.mark.parametrize("kind", ["code-review", "plan-review", "ready-for-review", "skill-review"])
    def test_a_completion_marker_write_lands_in_its_listed_directory_and_nowhere_else(
        self, scene: types.SimpleNamespace, kind: str
    ) -> None:
        self.stage_change(scene, "skills/example/SKILL.md" if kind == "skill-review" else "f")

        result = self.run_marker(scene, "write", kind)

        assert result.returncode == 0, result.stderr
        assert len(self.written(scene, DRIVEN_DIRECTORIES_BY_ROW[kind])) == 1
        assert sorted(os.listdir(scene.config)) == sorted(["settings.json", *scene.world.adapter.SANDBOX_WRITABLE_DIRECTORIES])

    def test_a_verification_marker_write_over_a_clean_tree_lands_in_its_directory(self, scene: types.SimpleNamespace) -> None:
        result = self.run_marker(scene, "write", "verification")

        assert result.returncode == 0, result.stderr
        assert len(self.written(scene, "verification-markers")) == 1

    @pytest.mark.parametrize("skill", list(ACTIVATE_DIRECTORIES))
    def test_an_activation_lands_in_its_active_bypass_directory_and_a_deactivation_removes_it(
        self, scene: types.SimpleNamespace, skill: str
    ) -> None:
        activated = self.run_marker(scene, "activate", skill)

        assert activated.returncode == 0, activated.stderr
        assert self.written(scene, ACTIVATE_DIRECTORIES[skill]) == [WRITABLE_DIRS_SESSION_ID]
        assert self.run_marker(scene, "deactivate", skill).returncode == 0
        assert self.written(scene, ACTIVATE_DIRECTORIES[skill]) == []

    def test_a_plan_review_activation_over_a_recent_pending_routing_read_backfills_the_routing_read_record(
        self, scene: types.SimpleNamespace
    ) -> None:
        pending_directory, routing_directory = PLAN_REVIEW_READ_DIRECTORIES
        (scene.config / pending_directory / WRITABLE_DIRS_SESSION_ID).write_text("")

        activated = self.run_marker(scene, "activate", "plan-review")

        assert activated.returncode == 0, activated.stderr
        assert self.written(scene, routing_directory) == [WRITABLE_DIRS_SESSION_ID]
        assert self.run_marker(scene, "deactivate", "plan-review").returncode == 0
        assert self.written(scene, routing_directory) == []
        assert self.written(scene, pending_directory) == []

    def test_clear_stale_evicts_the_marker_of_a_dead_host_in_the_restricted_dir(self, scene: types.SimpleNamespace) -> None:
        reaped = subprocess.Popen([sys.executable, "-c", "pass"])
        reaped.wait()
        with pytest.raises(ProcessLookupError):
            os.kill(reaped.pid, 0)
        dead_pid = str(reaped.pid)
        activated = self.run_marker(scene, "activate", "handoff", host_pid=dead_pid)
        assert activated.returncode == 0, activated.stderr
        assert self.written(scene, ".handoff-active.d") == [WRITABLE_DIRS_SESSION_ID]

        result = self.run_marker(scene, "clear-stale", host_pid=dead_pid)

        assert result.returncode == 0, result.stderr
        assert self.written(scene, ".handoff-active.d") == []

    def test_a_write_to_a_directory_the_list_omits_fails_which_shows_the_restriction_bites(
        self, scene: types.SimpleNamespace
    ) -> None:
        scene.config.chmod(0o700)
        (scene.config / "code-review-markers").rmdir()
        scene.config.chmod(0o500)
        self.stage_change(scene, "f")

        result = self.run_marker(scene, "write", "code-review")

        assert result.returncode != 0
        assert not (scene.config / "code-review-markers").exists()

    def test_every_marker_kind_and_listed_directory_has_a_row_or_a_stated_reason_it_has_none(self, world: World) -> None:
        write_kinds = set(marker_script_array("WRITE_SKILLS"))
        bypass_kinds = set(marker_script_array("ACTIVE_BYPASS_SKILLS"))
        assert write_kinds - set(DRIVEN_DIRECTORIES_BY_ROW) - set(WRITABLE_DIRS_NOT_DRIVEN) == set()
        assert bypass_kinds == set(ACTIVATE_DIRECTORIES)
        listed = world.adapter.SANDBOX_WRITABLE_DIRECTORIES
        assert writable_directories_without_a_row_or_a_reason(listed) == set()
        assert writable_directories_without_a_row_or_a_reason([*listed, "unlisted-markers"]) == {"unlisted-markers"}
        assert set(DIRECTORIES_NOT_DRIVEN) <= set(listed), "a not-driven directory is no longer listed"
        driven = {*DRIVEN_DIRECTORIES_BY_ROW.values(), *ACTIVATE_DIRECTORIES.values(), *PLAN_REVIEW_READ_DIRECTORIES}
        assert not driven & set(DIRECTORIES_NOT_DRIVEN), "a directory is both driven and in the not-driven table"
        assert all(WRITABLE_DIRS_NOT_DRIVEN.values())
        assert all(DIRECTORIES_NOT_DRIVEN.values())

    def test_every_listed_directory_is_a_single_plain_component_under_the_routed_config_dir(self, world: World) -> None:
        names = world.adapter.SANDBOX_WRITABLE_DIRECTORIES
        assert len(set(names)) == len(names)
        for name in names:
            assert name == os.path.basename(name) and name not in ("", ".", "..")
