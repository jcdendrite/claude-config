"""Tests for announce-autonomous-shipping.sh.

The hook is a SessionStart hook (matcher: startup|resume|clear) that emits
hookSpecificOutput.additionalContext holding the text of
_lib_autonomous_shipping_statement, and only where autonomous shipping is
active for the repository the payload's `.cwd` resolves to. Compaction belongs
to restore-authorization-boundary-on-compact.sh, so `compact` stays silent
here. Like that hook, it is advisory context rather than a sessionTitle hook,
so it cannot use run_hook_session_start.
"""
from __future__ import annotations

import functools
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
from helpers import (
    HOOKS_DIR,
    build_path_without,
    run_additional_context_session_start_hook,
    session_start_additional_context,
    session_start_registered_timeouts,
)

ANNOUNCE_HOOK = HOOKS_DIR / "announce-autonomous-shipping.sh"
SETTINGS_JSON = HOOKS_DIR.parent / "settings.json"
EMITTING_SOURCES = ["startup", "resume", "clear"]


_run_hook = functools.partial(run_additional_context_session_start_hook, ANNOUNCE_HOOK)
STATEMENT_LEAD = "Autonomous shipping is active for this repository"


class TestAnnounceAutonomousShipping:
    @pytest.mark.parametrize("source", EMITTING_SOURCES)
    def test_armed_emits_the_statement_for_the_payload_cwd_repo(
        self, armed_home, shipping_repo, non_repo_dir, shipping_statement, source
    ):
        """The process cwd is a non-repo dir, so only the payload `.cwd` can have located the repo."""
        result = _run_hook(
            {"source": source, "cwd": str(shipping_repo)}, armed_home, process_cwd=non_repo_dir
        )
        assert result.returncode == 0
        assert session_start_additional_context(result) == shipping_statement(shipping_repo)

    def test_subdirectory_cwd_emits(self, armed_home, shipping_repo, non_repo_dir, shipping_statement):
        subdirectory = shipping_repo / "pkg" / "inner"
        subdirectory.mkdir(parents=True)
        result = _run_hook(
            {"source": "startup", "cwd": str(subdirectory)}, armed_home, process_cwd=non_repo_dir
        )
        assert session_start_additional_context(result) == shipping_statement(shipping_repo)

    def test_linked_worktree_cwd_emits(
        self, armed_home, shipping_repo, non_repo_dir, tmp_path, shipping_statement
    ):
        linked_worktree = tmp_path / "linked-worktree"
        subprocess.run(
            ["git", "worktree", "add", "-q", "-b", "feature", str(linked_worktree)],
            cwd=shipping_repo,
            check=True,
        )
        result = _run_hook(
            {"source": "resume", "cwd": str(linked_worktree)}, armed_home, process_cwd=non_repo_dir
        )
        assert session_start_additional_context(result) == shipping_statement(linked_worktree)

    @pytest.mark.parametrize("source", ["compact", "fork", "unknown-source"])
    def test_other_sources_stay_silent(self, armed_home, shipping_repo, non_repo_dir, source):
        result = _run_hook(
            {"source": source, "cwd": str(shipping_repo)}, armed_home, process_cwd=non_repo_dir
        )
        assert result.returncode == 0
        assert result.stdout == b""

    def test_missing_source_stays_silent(self, armed_home, shipping_repo, non_repo_dir):
        result = _run_hook({"cwd": str(shipping_repo)}, armed_home, process_cwd=non_repo_dir)
        assert result.returncode == 0
        assert result.stdout == b""

    @pytest.mark.parametrize("bad_source", [123, True, ["startup"], {"nested": "startup"}])
    def test_non_string_source_stays_silent(
        self, armed_home, shipping_repo, non_repo_dir, bad_source
    ):
        result = _run_hook(
            {"source": bad_source, "cwd": str(shipping_repo)}, armed_home, process_cwd=non_repo_dir
        )
        assert result.returncode == 0
        assert result.stdout == b""

    def test_unarmed_machine_stays_silent(self, isolated_home, shipping_repo, non_repo_dir):
        result = _run_hook(
            {"source": "startup", "cwd": str(shipping_repo)}, isolated_home, process_cwd=non_repo_dir
        )
        assert result.returncode == 0
        assert result.stdout == b""

    @pytest.mark.parametrize(
        "bad_cwd",
        [42, ["/tmp"]],
        ids=["number", "array"],
    )
    def test_non_string_payload_cwd_stays_silent_even_when_process_cwd_is_an_armed_repo(
        self, armed_home, shipping_repo, bad_cwd
    ):
        """Missing, empty and relative cwd are rows of test_autonomous_shipping_grant_boundary.py."""
        result = _run_hook(
            {"source": "startup", "cwd": bad_cwd}, armed_home, process_cwd=shipping_repo
        )
        assert result.returncode == 0
        assert result.stdout == b""

    def test_sentinel_only_under_claude_config_dir_emits(
        self, isolated_home, shipping_repo, non_repo_dir, tmp_path
    ):
        """HOME carries no sentinel, so only the config dir can have armed the hook."""
        config_dir = tmp_path / "profile"
        config_dir.mkdir()
        (config_dir / "autonomous-shipping-required").write_text("# machine sentinel\n")
        result = _run_hook(
            {"source": "startup", "cwd": str(shipping_repo)},
            isolated_home,
            process_cwd=non_repo_dir,
            extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)},
        )
        assert result.returncode == 0
        assert session_start_additional_context(result).startswith(STATEMENT_LEAD)

    def test_sentinel_only_under_legacy_home_dir_emits_when_config_dir_is_empty(
        self, armed_home, shipping_repo, non_repo_dir, tmp_path, shipping_statement
    ):
        """The legacy `$HOME/.claude` location stays armed after CLAUDE_CONFIG_DIR adoption."""
        empty_config_dir = tmp_path / "empty-profile"
        empty_config_dir.mkdir()
        result = _run_hook(
            {"source": "startup", "cwd": str(shipping_repo)},
            armed_home,
            process_cwd=non_repo_dir,
            extra_env={"CLAUDE_CONFIG_DIR": str(empty_config_dir)},
        )
        assert result.returncode == 0
        assert session_start_additional_context(result) == shipping_statement(shipping_repo)

    def test_empty_stdin_stays_silent(self, armed_home, non_repo_dir):
        result = _run_hook(None, armed_home, process_cwd=non_repo_dir, raw_stdin=b"")
        assert result.returncode == 0
        assert result.stdout == b""

    def test_malformed_stdin_stays_silent(self, armed_home, non_repo_dir):
        result = _run_hook(None, armed_home, process_cwd=non_repo_dir, raw_stdin=b"not json")
        assert result.returncode == 0
        assert result.stdout == b""

    def test_missing_jq_stays_silent(self, armed_home, shipping_repo, non_repo_dir, tmp_path):
        """The armed fixture emits in test_armed_emits_the_statement_for_the_payload_cwd_repo, so silence here is jq's absence."""
        farm_dir = tmp_path / "path_without_jq"
        farm_dir.mkdir()
        result = _run_hook(
            {"source": "startup", "cwd": str(shipping_repo)},
            armed_home,
            process_cwd=non_repo_dir,
            extra_env={"PATH": build_path_without("jq", farm_dir)},
        )
        assert result.returncode == 0
        assert result.stdout == b""

    def test_unresolvable_config_dir_stays_silent(self, armed_home, shipping_repo, non_repo_dir):
        result = _run_hook(
            {"source": "startup", "cwd": str(shipping_repo)},
            armed_home,
            process_cwd=non_repo_dir,
            extra_env={"CLAUDE_CONFIG_DIR": "relative/path"},
        )
        assert result.returncode == 0
        assert result.stdout == b""

    @staticmethod
    def _install_recording_shims(tmp_path: Path) -> tuple[str, Path]:
        """Returns a PATH whose git and jq shims append their name to the returned log, then exec the real tool."""
        shim_dir = tmp_path / "recording-shims"
        shim_dir.mkdir()
        invocation_log = tmp_path / "invocations.log"
        for tool in ("git", "jq"):
            real_tool = shutil.which(tool)
            assert real_tool is not None, f"{tool} not found in PATH"
            shim = shim_dir / tool
            shim.write_text(
                "#!/bin/bash\n"
                f'printf "%s\\n" "{tool}" >> "{invocation_log}"\n'
                f'exec "{real_tool}" "$@"\n'
            )
            shim.chmod(shim.stat().st_mode | stat.S_IXUSR)
        return f"{shim_dir}{os.pathsep}{os.environ['PATH']}", invocation_log

    def test_shims_record_git_and_jq_spawns_on_an_armed_machine(
        self, armed_home, shipping_repo, non_repo_dir, tmp_path
    ):
        """Positive control: proves the shims detect spawns, so the unarmed run's empty log means none happened."""
        shimmed_path, invocation_log = self._install_recording_shims(tmp_path)
        result = _run_hook(
            {"source": "startup", "cwd": str(shipping_repo)},
            armed_home,
            process_cwd=non_repo_dir,
            extra_env={"PATH": shimmed_path},
        )
        assert result.returncode == 0
        assert result.stdout != b""
        assert set(invocation_log.read_text().split()) == {"git", "jq"}

    def test_non_adopting_machine_spawns_no_git_or_jq(
        self, isolated_home, shipping_repo, non_repo_dir, tmp_path
    ):
        """An unarmed machine pays no process spawn: the sentinel fast path exits before jq or git."""
        shimmed_path, invocation_log = self._install_recording_shims(tmp_path)
        result = _run_hook(
            {"source": "startup", "cwd": str(shipping_repo)},
            isolated_home,
            process_cwd=non_repo_dir,
            extra_env={"PATH": shimmed_path},
        )
        assert result.returncode == 0
        assert result.stdout == b""
        assert not invocation_log.exists()


class TestAnnounceHookRegistration:
    @staticmethod
    def _session_start_commands_by_matcher() -> dict[str, list[str]]:
        groups = json.loads(SETTINGS_JSON.read_text())["hooks"]["SessionStart"]
        commands_by_matcher: dict[str, list[str]] = {}
        for group in groups:
            for hook in group["hooks"]:
                commands_by_matcher.setdefault(group.get("matcher", ""), []).append(hook["command"])
        return commands_by_matcher

    def test_registered_once_on_startup_resume_clear(self):
        registrations = [
            (matcher, command)
            for matcher, commands in self._session_start_commands_by_matcher().items()
            for command in commands
            if command.endswith("/announce-autonomous-shipping.sh")
        ]
        assert [matcher for matcher, _ in registrations] == ["startup|resume|clear"]

    def test_registered_with_a_ten_second_timeout(self):
        """The harness default is 600 s (docs/hooks.md § "Gate deadlock recovery"), so the registered cap is the binding bound."""
        assert session_start_registered_timeouts("announce-autonomous-shipping.sh") == [10]

    def test_matcher_sources_are_disjoint_from_the_compact_hook(self):
        commands_by_matcher = self._session_start_commands_by_matcher()
        announce_sources = {
            source
            for matcher, commands in commands_by_matcher.items()
            if any(command.endswith("/announce-autonomous-shipping.sh") for command in commands)
            for source in matcher.split("|")
        }
        compact_sources = {
            source
            for matcher, commands in commands_by_matcher.items()
            if any(
                command.endswith("/restore-authorization-boundary-on-compact.sh")
                for command in commands
            )
            for source in matcher.split("|")
        }
        assert announce_sources
        assert compact_sources
        assert announce_sources.isdisjoint(compact_sources)
