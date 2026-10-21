"""Tests for bootstrap-project-plugins.sh.

The hook is a SessionStart hook (matcher: startup) that launches
reconcile-project-plugins.sh, found beside the hook's own directory
(`../scripts/`), detached and with the resolved repo root as $1, when a trusted
repo's checked-in settings enable a plugin the per-account registry lacks. It exits 0 on every path, prints nothing to stdout,
and prints nothing to stderr except _config_enabled's config warnings (Sandbox.run
asserts both; the cases that expect a warning name it).

The detached reconciler outlives the hook, so a launch is asserted through a
stub reconciler that writes a sentinel file, polled under a deadline. A
no-launch case with no other oracle polls a window instead. The window scales
from one measured launch latency taken in the same run (no_launch_window_seconds),
so it stays proportionate to this machine's load. A no-launch case where the
hook wrote a refusal audit line skips the poll: the line is written before the
hook exits and only on a path that never reaches the launch statement. One layer-1
and one layer-2 refusal case still poll, since the immediate check can miss a stub
that is launched a few milliseconds late.

Every case sets or unsets CLAUDE_CODE_ENTRYPOINT and CLAUDE_CONFIG_DIR
explicitly, since pytest inherits the caller's values. Every case runs under an
isolated $HOME.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from helpers import (
    CLAUDE_DIR,
    HOOKS_DIR,
    SCRIPTS_DIR,
    _build_subprocess_env,
    ambient_umask,
    file_mode,
    git_isolation_env,
    init_git_repo,
    init_reconciler_repo,
    install_log_entries,
    make_reconciler_claude_shim,
    plugin_registry_file,
    reconciler_audit_lines,
    run_reconciler,
    symlink_hooks_lib_chain,
    v2_registry_entry,
    write_plugin_registry,
)

from .test_config_lib import _locale_that_widens_ascii_ranges

BOOTSTRAP_HOOK = HOOKS_DIR / "bootstrap-project-plugins.sh"

INTERACTIVE_ENTRYPOINT = "cli"
MISSING_PLUGIN_ID = "plugin-a@market-a"

# A launched stub is one fork+exec that writes a file, so a positive launch
# lands in milliseconds; the deadline only has to outlast a loaded machine.
LAUNCH_DEADLINE_SECONDS = 10.0
# A no-launch case can only be shown by absence, so it polls a window before
# concluding nothing was launched. The window is NO_LAUNCH_WINDOW_LATENCY_FACTOR
# times one measured launch latency, clamped to [floor, ceiling]. The floor keeps
# a near-instant measurement from shrinking the window below one second, and the
# ceiling bounds suite time on a machine too loaded to measure.
NO_LAUNCH_WINDOW_FLOOR_SECONDS = 1.0
NO_LAUNCH_WINDOW_CEILING_SECONDS = 5.0
NO_LAUNCH_WINDOW_LATENCY_FACTOR = 50
POLL_INTERVAL_SECONDS = 0.02
# The launch-latency control polls finely so its reading is a real latency, not a
# multiple of POLL_INTERVAL_SECONDS.
LATENCY_PROBE_POLL_INTERVAL_SECONDS = 0.001

# Matches ENTRYPOINT_LOGGED_MAX_LENGTH in the hook.
ENTRYPOINT_LOGGED_MAX_LENGTH = 64
# Matches _LIB_SIZE_THRESHOLD_BYTES in _lib.sh.
SIZE_CAP_BYTES = 5242880
# Matches _LIB_ENABLED_PLUGIN_IDS_MAX in _lib.sh.
ENABLED_PLUGIN_IDS_MAX = 100
# A valid-shaped key whose marketplace half is long enough that an unbounded `##*@` removal
# takes many seconds, yet below the 128 KiB single-argument limit.
LONG_MARKETPLACE_HALF_LENGTH = 96 * 1024

AUDIT_LINE_KEYS = {"timestamp", "pid", "repo_root", "plugin_id", "decision", "outcome"}
AUDIT_TIMESTAMP_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
AUDIT_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
# A timezone far from UTC, so a local-time timestamp cannot pass as UTC.
NON_UTC_TIMEZONE = "Pacific/Auckland"
AUDIT_TIMESTAMP_MAX_AGE_SECONDS = 120
SETTINGS_JSON = CLAUDE_DIR / "settings.json"


class Sandbox:
    """An isolated $HOME, config dir, git repo, and an install tree of symlinked
    hooks with a stub reconciler in the sibling `scripts/` directory."""

    def __init__(
        self,
        tmp_path: Path,
        repo_name: str = "repo",
        no_launch_window_seconds: float = NO_LAUNCH_WINDOW_FLOOR_SECONDS,
    ) -> None:
        self.no_launch_window_seconds = no_launch_window_seconds
        self.home = tmp_path / "home"
        self.tmp_path = tmp_path
        self.config_dir = self.home / ".claude"
        self.config_dir.mkdir(parents=True)
        self.scripts_dir = tmp_path / "install" / "scripts"
        self.scripts_dir.mkdir(parents=True)
        self.hooks_dir = tmp_path / "install" / "hooks"
        self.hook = self.install_hooks_dir(self.hooks_dir)
        self.git_env = {**os.environ, **git_isolation_env(tmp_path)}
        self.repo = init_git_repo((tmp_path / repo_name).resolve(), env=self.git_env)
        self.sentinel = tmp_path / "launch-sentinel"
        self.release = tmp_path / "release-stub"
        self.write_settings({MISSING_PLUGIN_ID: True})
        self.write_trust()
        self.install_stub()

    # ---------- fixture writers -------------------------------------

    def write_settings(self, enabled_plugins: dict, repo: Path | None = None) -> None:
        settings_file = (repo or self.repo) / ".claude" / "settings.json"
        settings_file.parent.mkdir(parents=True, exist_ok=True)
        settings_file.write_text(json.dumps({"enabledPlugins": enabled_plugins}))

    def write_registry(self, plugin_ids: list[str], version: int | None = 2, project_path: str = "/elsewhere") -> None:
        write_plugin_registry(
            plugin_registry_file(self.config_dir),
            {pid: [v2_registry_entry(project_path)] for pid in plugin_ids},
            version,
        )

    def write_trust(self, repo: Path | None = None, accepted: object = True, trust_file: Path | None = None) -> None:
        target = trust_file or (self.config_dir / ".claude.json")
        target.write_text(
            json.dumps({"projects": {str(repo or self.repo): {"hasTrustDialogAccepted": accepted}}})
        )

    def write_config_toml(self, text: str) -> Path:
        toml_file = self.config_dir / "claude-config.toml"
        toml_file.write_text(text)
        return toml_file

    def add_linked_worktree(self, worktree_path: Path) -> Path:
        """Commits the checked-in settings, then adds a linked worktree of the repo at worktree_path."""
        subprocess.run(["git", "add", ".claude/settings.json"], cwd=self.repo, check=True, env=self.git_env)
        subprocess.run(["git", "commit", "-q", "-m", "settings"], cwd=self.repo, check=True, env=self.git_env)
        subprocess.run(
            ["git", "worktree", "add", "-q", str(worktree_path), "-b", "feature"],
            cwd=self.repo, check=True, env=self.git_env,
        )
        return worktree_path

    def install_hooks_dir(self, hooks_dir: Path) -> Path:
        """Populate hooks_dir with the real hook and its _lib.sh chain as symlinks, and point
        the `scripts/` directory beside it at this sandbox's. Returns the hook's path there.
        The hook finds the reconciler through its own location, so a hook copy elsewhere needs this sibling."""
        hooks_dir.mkdir(parents=True, exist_ok=True)
        symlink_hooks_lib_chain(hooks_dir)
        hook = hooks_dir / BOOTSTRAP_HOOK.name
        hook.symlink_to(BOOTSTRAP_HOOK)
        scripts_link = hooks_dir.parent / "scripts"
        if not scripts_link.exists():
            scripts_link.symlink_to(self.scripts_dir)
        return hook

    def install_stub(self, hold_until_release: bool = False) -> None:
        """Stub reconciler: records $1 atomically into the sentinel. With
        hold_until_release it then stays alive until the release file exists
        (bounded), so a leaked inherited pipe would hold the hook's caller open."""
        lines = [
            "#!/bin/sh",
            f"printf '%s\\n' \"$1\" > {shlex.quote(str(self.sentinel) + '.tmp')}",
            f"mv {shlex.quote(str(self.sentinel) + '.tmp')} {shlex.quote(str(self.sentinel))}",
        ]
        if hold_until_release:
            lines += [
                "n=0",
                f"while [ ! -e {shlex.quote(str(self.release))} ] && [ \"$n\" -lt 300 ]; do sleep 0.1; n=$((n+1)); done",
            ]
        stub = self.scripts_dir / "reconcile-project-plugins.sh"
        stub.unlink(missing_ok=True)
        stub.write_text("\n".join(lines) + "\n")
        stub.chmod(0o755)

    # ---------- running the hook ------------------------------------

    def env(
        self,
        entrypoint: str | None = INTERACTIVE_ENTRYPOINT,
        config_dir_env: str | None = "default",
        extra_env: dict | None = None,
        unset_home: bool = False,
    ) -> dict:
        env = _build_subprocess_env(self.home, extra_env)
        assert env is not None
        if unset_home:
            env.pop("HOME", None)
        if entrypoint is None:
            env.pop("CLAUDE_CODE_ENTRYPOINT", None)
        else:
            env["CLAUDE_CODE_ENTRYPOINT"] = entrypoint
        if config_dir_env is None:
            env.pop("CLAUDE_CONFIG_DIR", None)
        elif config_dir_env == "default":
            env["CLAUDE_CONFIG_DIR"] = str(self.config_dir)
        else:
            env["CLAUDE_CONFIG_DIR"] = config_dir_env
        return env

    def payload(self, payload_cwd: Path | str | None = None, source: str = "startup") -> bytes:
        return json.dumps(
            {"hook_event_name": "SessionStart", "source": source, "cwd": str(payload_cwd or self.repo)}
        ).encode()

    def run(
        self,
        entrypoint: str | None = INTERACTIVE_ENTRYPOINT,
        config_dir_env: str | None = "default",
        payload_cwd: Path | str | None = None,
        process_cwd: Path | None = None,
        source: str = "startup",
        hook: Path | None = None,
        stdin: bytes | None = None,
        unset_home: bool = False,
        stderr_contains: bytes | None = None,
        extra_env: dict | None = None,
    ) -> subprocess.CompletedProcess:
        """Runs the hook and asserts rc 0 and empty stdout. Stderr must be empty unless
        stderr_contains names the one warning the case expects."""
        result = subprocess.run(
            [str(hook or self.hook)],
            input=self.payload(payload_cwd, source) if stdin is None else stdin,
            capture_output=True,
            cwd=process_cwd or self.repo,
            env=self.env(entrypoint, config_dir_env, extra_env=extra_env, unset_home=unset_home),
            timeout=LAUNCH_DEADLINE_SECONDS,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == b""
        if stderr_contains is None:
            assert result.stderr == b""
        else:
            assert stderr_contains in result.stderr
        return result

    # ---------- observing the outcome -------------------------------

    def wait_for_launch(self, deadline_seconds: float, poll_interval_seconds: float = POLL_INTERVAL_SECONDS) -> bool:
        end = time.monotonic() + deadline_seconds
        while time.monotonic() < end:
            if self.sentinel.exists():
                return True
            time.sleep(poll_interval_seconds)
        return self.sentinel.exists()

    def assert_launched_with_root(self, expected_root: Path | None = None) -> None:
        assert self.wait_for_launch(LAUNCH_DEADLINE_SECONDS), "reconciler stub was never launched"
        assert self.sentinel.read_text(encoding="utf-8").rstrip("\n") == str(expected_root or self.repo)

    def assert_not_launched(self) -> None:
        assert not self.wait_for_launch(self.no_launch_window_seconds), "reconciler stub was launched"

    def assert_not_launched_after_logged_refusal(self) -> None:
        """No poll: run() has returned, and a refusal audit line is written before the hook exits
        on a path that never reaches the launch statement. Callers assert the line itself.
        The check is immediate and a launched stub needs a few milliseconds to write its
        sentinel, so for a regression that logs and then launches this is a statistical
        oracle: it catches the fault in some fraction of runs. The polled
        test_*_refusal_is_never_launched_after_logging cases are the deterministic ones."""
        assert self.audit_entries(), "no refusal audit line, so absence of a launch needs the polled window"
        assert not self.sentinel.exists(), "reconciler stub was launched"

    @property
    def audit_log(self) -> Path:
        return self.config_dir / "plugin-bootstrap" / "audit.log"

    def audit_entries(self) -> list[dict]:
        if not self.audit_log.exists():
            return []
        return [json.loads(line) for line in self.audit_log.read_text(encoding="utf-8").splitlines() if line]


@pytest.fixture(scope="module")
def launch_latency_seconds(tmp_path_factory) -> float:
    """Seconds from the hook returning to the stub's sentinel being observed, for one
    launch under this run's load. Polling granularity (LATENCY_PROBE_POLL_INTERVAL_SECONDS)
    bounds it from above."""
    control = Sandbox(tmp_path_factory.mktemp("launch-latency-control"))
    control.run()
    hook_returned_at = time.monotonic()
    assert control.wait_for_launch(
        LAUNCH_DEADLINE_SECONDS, poll_interval_seconds=LATENCY_PROBE_POLL_INTERVAL_SECONDS
    ), "reconciler stub was never launched"
    return time.monotonic() - hook_returned_at


@pytest.fixture(scope="module")
def no_launch_window_seconds(launch_latency_seconds) -> float:
    scaled_window = launch_latency_seconds * NO_LAUNCH_WINDOW_LATENCY_FACTOR
    return min(max(scaled_window, NO_LAUNCH_WINDOW_FLOOR_SECONDS), NO_LAUNCH_WINDOW_CEILING_SECONDS)


@pytest.fixture
def sandbox(tmp_path, no_launch_window_seconds):
    return Sandbox(tmp_path, no_launch_window_seconds=no_launch_window_seconds)


class TestLaunchContract:
    def test_trusted_interactive_session_with_missing_plugin_launches_reconciler_with_repo_root(self, sandbox):
        """Positive control: also proves the stub is observable within the launch deadline."""
        result = sandbox.run()

        sandbox.assert_launched_with_root()
        assert result.stderr == b""
        assert sandbox.audit_entries() == []

    def test_launches_when_registry_file_is_absent(self, sandbox):
        assert not (sandbox.config_dir / "plugins" / "installed_plugins.json").exists()

        sandbox.run()

        sandbox.assert_launched_with_root()

    def test_payload_cwd_subdirectory_resolves_to_git_toplevel(self, sandbox):
        subdir = sandbox.repo / "nested" / "deeper"
        subdir.mkdir(parents=True)

        sandbox.run(payload_cwd=subdir)

        sandbox.assert_launched_with_root(sandbox.repo)

    def test_payload_cwd_overrides_process_cwd(self, sandbox, tmp_path):
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()

        sandbox.run(payload_cwd=sandbox.repo, process_cwd=elsewhere)

        sandbox.assert_launched_with_root(sandbox.repo)

    @pytest.mark.parametrize("process_cwd_kind", ["other-git-repo", "non-git-dir"])
    def test_payload_cwd_subdirectory_resolves_against_payload_not_process_cwd(
        self, sandbox, tmp_path, process_cwd_kind
    ):
        """`git -C "$PAYLOAD_CWD"`, not the hook's own cwd: a hook started inside another
        repo (or outside any repo) must still resolve the payload's repo."""
        subdir = sandbox.repo / "nested" / "deeper"
        subdir.mkdir(parents=True)
        process_cwd = tmp_path / process_cwd_kind
        if process_cwd_kind == "other-git-repo":
            init_git_repo(process_cwd, env=sandbox.git_env)
        else:
            process_cwd.mkdir()

        sandbox.run(payload_cwd=subdir, process_cwd=process_cwd)

        sandbox.assert_launched_with_root(sandbox.repo)

    def test_linked_worktree_payload_cwd_wins_over_main_checkout_process_cwd(self, sandbox, tmp_path):
        linked_worktree = sandbox.add_linked_worktree((tmp_path / "linked-worktree").resolve())
        # ASSUMED: the vendor records trust for a linked worktree under the worktree's own path.
        # Unverified, so this fixture encodes the hook's keying, not the vendor's.
        sandbox.write_trust(repo=linked_worktree)

        sandbox.run(payload_cwd=linked_worktree, process_cwd=sandbox.repo)

        sandbox.assert_launched_with_root(linked_worktree)

    def test_linked_worktree_subdirectory_resolves_against_payload_not_main_checkout_process_cwd(
        self, sandbox, tmp_path
    ):
        linked_worktree = sandbox.add_linked_worktree((tmp_path / "linked-worktree").resolve())
        # ASSUMED: see the linked-worktree trust keying note above.
        sandbox.write_trust(repo=linked_worktree)
        subdir = linked_worktree / "nested"
        subdir.mkdir()

        sandbox.run(payload_cwd=subdir, process_cwd=sandbox.repo)

        sandbox.assert_launched_with_root(linked_worktree)

    def test_payload_cwd_trailing_slash_is_stripped_before_the_trust_lookup(self, sandbox):
        """Trust matches the exact repo-root string, so a retained slash would miss the record."""
        sandbox.run(payload_cwd=f"{sandbox.repo}/")

        sandbox.assert_launched_with_root(sandbox.repo)

    def test_launches_when_registry_lists_other_ids_but_not_the_enabled_one(self, sandbox):
        """The production-dominant state: a version-2 registry that already holds other plugins."""
        sandbox.write_registry(["other-plugin@market-b"])

        sandbox.run()

        sandbox.assert_launched_with_root()

    def test_repo_root_with_space_and_shell_metacharacters_reaches_reconciler_intact(self, tmp_path):
        hostile_sandbox = Sandbox(tmp_path, repo_name="my repo $(touch INJECTED) & ; `x`")

        hostile_sandbox.run()

        hostile_sandbox.assert_launched_with_root()
        # The hook and stub both run with the repo as cwd, so a reinterpreted
        # path would create the marker there.
        assert not (hostile_sandbox.repo / "INJECTED").exists()

    def test_non_ascii_repo_root_reaches_reconciler_intact(self, tmp_path, no_launch_window_seconds):
        non_ascii_sandbox = Sandbox(
            tmp_path, repo_name="r\u00e9po-\u65e5\u672c\u8a9e", no_launch_window_seconds=no_launch_window_seconds
        )

        non_ascii_sandbox.run()

        non_ascii_sandbox.assert_launched_with_root()

    def test_hook_returns_while_detached_reconciler_is_still_running(self, sandbox):
        """A reconciler that outlives the hook must not hold the hook's captured
        stdio open: run() uses capture_output, so a leaked inherited pipe makes
        communicate() block until its timeout instead of returning."""
        sandbox.install_stub(hold_until_release=True)
        try:
            sandbox.run()
            sandbox.assert_launched_with_root()
        finally:
            sandbox.release.write_text("")

    def test_unexecutable_reconciler_is_a_silent_no_launch(self, sandbox):
        (sandbox.scripts_dir / "reconcile-project-plugins.sh").chmod(0o644)

        sandbox.run()

        sandbox.assert_not_launched()


class TestMissingIdDecision:
    def test_no_enabled_plugins_is_silent_no_launch(self, sandbox):
        (sandbox.repo / ".claude" / "settings.json").write_text("{}")

        sandbox.run()

        sandbox.assert_not_launched()
        assert sandbox.audit_entries() == []

    def test_absent_settings_file_is_silent_no_launch(self, sandbox):
        (sandbox.repo / ".claude" / "settings.json").unlink()

        sandbox.run()

        sandbox.assert_not_launched()

    def test_every_id_already_in_registry_is_silent_no_launch(self, sandbox):
        sandbox.write_registry([MISSING_PLUGIN_ID])

        sandbox.run()

        sandbox.assert_not_launched()
        assert sandbox.audit_entries() == []

    def test_registry_entry_for_a_different_project_path_still_counts_as_present(self, sandbox):
        """Presence is name-keyed, so a worktree does not trigger a reinstall."""
        sandbox.write_registry([MISSING_PLUGIN_ID], project_path="/some/other/worktree")

        sandbox.run()

        sandbox.assert_not_launched()

    def test_false_valued_entries_are_skipped(self, sandbox):
        sandbox.write_settings({MISSING_PLUGIN_ID: False})

        sandbox.run()

        sandbox.assert_not_launched()

    @pytest.mark.parametrize(
        "invalid_id",
        [
            "-flag@market-a",
            ".hidden@market-a",
            "_under@market-a",
            "plugin-a@npm",
            "no-marketplace-part",
            "a@-b",
            "plugin-a@market-a\nplugin-b@market-a",
            "plugin-a\x1b@market-a",
        ],
        ids=[
            "leading-dash",
            "leading-dot",
            "leading-underscore",
            "npm-marketplace",
            "no-at-sign",
            "marketplace-leading-dash",
            "two-valid-ids-joined-by-a-newline",
            "escape-character",
        ],
    )
    def test_only_invalid_ids_missing_is_no_launch(self, sandbox, invalid_id):
        sandbox.write_settings({invalid_id: True})

        sandbox.run()

        sandbox.assert_not_launched()

    def test_valid_shaped_id_with_a_long_marketplace_half_never_launches(self, sandbox):
        """The id length bound rejects it. The bound's cost is timed at the unit layer, in test_lib.py."""
        sandbox.write_settings({"a@" + "b" * LONG_MARKETPLACE_HALF_LENGTH: True})

        sandbox.run()

        sandbox.assert_not_launched()

    def test_enabled_id_count_at_the_cap_still_launches(self, sandbox):
        sandbox.write_settings({f"plugin-{index}@market-a": True for index in range(ENABLED_PLUGIN_IDS_MAX)})

        sandbox.run()

        sandbox.assert_launched_with_root()

    def test_enabled_id_count_one_past_the_cap_is_silent_no_launch(self, sandbox):
        sandbox.write_settings({f"plugin-{index}@market-a": True for index in range(ENABLED_PLUGIN_IDS_MAX + 1)})

        sandbox.run()

        sandbox.assert_not_launched()
        assert sandbox.audit_entries() == []

    def test_one_valid_id_among_invalid_ids_still_launches(self, sandbox):
        sandbox.write_settings({"-flag@market-a": True, "plugin-a@npm": True, MISSING_PLUGIN_ID: True})

        sandbox.run()

        sandbox.assert_launched_with_root()

    def test_symlinked_settings_file_is_silent_no_launch(self, sandbox, tmp_path):
        real_settings = tmp_path / "real-settings.json"
        real_settings.write_text(json.dumps({"enabledPlugins": {MISSING_PLUGIN_ID: True}}))
        settings_file = sandbox.repo / ".claude" / "settings.json"
        settings_file.unlink()
        settings_file.symlink_to(real_settings)

        sandbox.run()

        sandbox.assert_not_launched()
        assert sandbox.audit_entries() == []

    def test_non_regular_settings_file_is_silent_no_launch(self, sandbox):
        settings_file = sandbox.repo / ".claude" / "settings.json"
        settings_file.unlink()
        settings_file.mkdir()

        sandbox.run()

        sandbox.assert_not_launched()

    def test_oversized_settings_file_is_silent_no_launch(self, sandbox):
        oversized = {"enabledPlugins": {MISSING_PLUGIN_ID: True}, "padding": "x" * (SIZE_CAP_BYTES + 1)}
        (sandbox.repo / ".claude" / "settings.json").write_text(json.dumps(oversized))

        sandbox.run()

        sandbox.assert_not_launched()
        assert sandbox.audit_entries() == []

    @pytest.mark.parametrize("registry_version", [None, 1, 3], ids=["version-missing", "version-1", "version-3"])
    def test_registry_with_wrong_version_is_no_launch_and_logged(self, sandbox, registry_version):
        sandbox.write_registry([], version=registry_version)

        sandbox.run()

        [entry] = sandbox.audit_entries()
        assert entry["decision"] == "registry_unparseable_or_version_mismatch"
        assert entry["outcome"] == "skipped"
        sandbox.assert_not_launched_after_logged_refusal()

    @pytest.mark.parametrize(
        "registry_text",
        ['{"version": 2}', '{"version": 2, "plugins": null}', '{"version": 2, "plugins": []}', '{"version": "2", "plugins": {}}'],
        ids=["plugins-absent", "plugins-null", "plugins-array", "version-string"],
    )
    def test_registry_that_is_not_a_version_2_object_of_plugins_is_no_launch_and_logged(self, sandbox, registry_text):
        registry_file = sandbox.config_dir / "plugins" / "installed_plugins.json"
        registry_file.parent.mkdir(parents=True)
        registry_file.write_text(registry_text)

        sandbox.run()

        [entry] = sandbox.audit_entries()
        assert entry["decision"] == "registry_unparseable_or_version_mismatch"
        sandbox.assert_not_launched_after_logged_refusal()

    def test_two_top_level_json_values_in_settings_is_silent_no_launch(self, sandbox):
        (sandbox.repo / ".claude" / "settings.json").write_text(
            json.dumps({"enabledPlugins": {MISSING_PLUGIN_ID: True}}) + json.dumps({"enabledPlugins": {}})
        )

        sandbox.run()

        sandbox.assert_not_launched()
        assert sandbox.audit_entries() == []

    def test_unparseable_registry_is_no_launch_and_logged(self, sandbox):
        registry_file = sandbox.config_dir / "plugins" / "installed_plugins.json"
        registry_file.parent.mkdir(parents=True)
        registry_file.write_text("{ not json")

        sandbox.run()

        [entry] = sandbox.audit_entries()
        assert entry["decision"] == "registry_unparseable_or_version_mismatch"
        sandbox.assert_not_launched_after_logged_refusal()

    @pytest.mark.parametrize(
        ("helper_status", "expected_decision"),
        [
            (0, None),
            (1, None),
            (2, "registry_unparseable_or_version_mismatch"),
            (3, "m10_local_optout_unreadable"),
            (4, None),
        ],
        ids=["status-0-launches", "status-1-silent", "status-2-logged", "status-3-logged", "status-4-unexpected-silent"],
    )
    def test_launch_requires_helper_status_zero_even_when_the_helper_printed_an_id(
        self, sandbox, tmp_path, helper_status, expected_decision
    ):
        """The shim prints an id with every status. Status 0 is the in-test control: it proves
        the shimmed hook can launch, so the other rows are no-launch for their status alone."""
        shim_hooks_dir = tmp_path / "shim" / "hooks"
        shim_hooks_dir.mkdir(parents=True)
        shutil.copy2(BOOTSTRAP_HOOK, shim_hooks_dir / BOOTSTRAP_HOOK.name)
        (shim_hooks_dir.parent / "scripts").symlink_to(sandbox.scripts_dir)
        (shim_hooks_dir / "_lib.sh").write_text(
            f'. "{HOOKS_DIR}/_lib.sh"\n'
            f'_lib_missing_plugin_ids() {{ printf "%s\\n" "{MISSING_PLUGIN_ID}"; return {helper_status}; }}\n'
        )

        sandbox.run(hook=shim_hooks_dir / BOOTSTRAP_HOOK.name)

        if helper_status == 0:
            sandbox.assert_launched_with_root()
            assert sandbox.audit_entries() == []
        elif expected_decision is None:
            sandbox.assert_not_launched()
            assert sandbox.audit_entries() == []
        else:
            [entry] = sandbox.audit_entries()
            assert entry["decision"] == expected_decision
            assert entry["outcome"] == "skipped"
            # Polled, not the logged-refusal shortcut: this case exists to catch a branch that logs and then falls through.
            sandbox.assert_not_launched()

    def test_non_startup_source_is_no_launch(self, sandbox):
        sandbox.run(source="resume")

        sandbox.assert_not_launched()


class TestPayloadResolution:
    """Payload to repo-root resolution: what the hook accepts as a repo before it reads any trust."""

    def test_untrusted_repo_without_checked_in_settings_writes_no_audit_line(self, sandbox):
        """A repo that could never participate is not a refusal worth logging, and the trust file is not read for it."""
        (sandbox.repo / ".claude" / "settings.json").unlink()
        (sandbox.config_dir / ".claude.json").unlink()

        sandbox.run()

        assert not sandbox.audit_log.exists()
        sandbox.assert_not_launched()

    def test_repo_without_claude_directory_writes_no_audit_line(self, sandbox):
        shutil.rmtree(sandbox.repo / ".claude")
        (sandbox.config_dir / ".claude.json").unlink()

        sandbox.run()

        assert not sandbox.audit_log.exists()

    def test_dangling_symlinked_settings_counts_as_present_and_is_logged_when_untrusted(self, sandbox, tmp_path):
        """A symlink is present for this gate, since the helper refuses it later; the layer-1 refusal is still logged."""
        settings_file = sandbox.repo / ".claude" / "settings.json"
        settings_file.unlink()
        settings_file.symlink_to(tmp_path / "no-such-target.json")
        (sandbox.config_dir / ".claude.json").unlink()

        sandbox.run()

        [entry] = sandbox.audit_entries()
        assert entry["decision"] == "m1a_not_trusted"

    def test_home_directory_with_account_level_settings_is_not_a_repo_root(self, sandbox):
        """cwd $HOME holds ~/.claude/settings.json, the account's own file, even when trust for $HOME is recorded."""
        (sandbox.config_dir / "settings.json").write_text(json.dumps({"enabledPlugins": {MISSING_PLUGIN_ID: True}}))
        sandbox.write_trust(repo=sandbox.home)

        sandbox.run(payload_cwd=sandbox.home, process_cwd=sandbox.home)

        assert not sandbox.audit_log.exists()
        sandbox.assert_not_launched()

    def test_home_directory_that_is_a_git_repo_is_not_a_repo_root(self, sandbox):
        init_git_repo(sandbox.home, env=sandbox.git_env)
        (sandbox.home / "projects-dir").mkdir()
        (sandbox.config_dir / "settings.json").write_text(json.dumps({"enabledPlugins": {MISSING_PLUGIN_ID: True}}))
        sandbox.write_trust(repo=sandbox.home)

        sandbox.run(payload_cwd=sandbox.home / "projects-dir", process_cwd=sandbox.home)

        assert not sandbox.audit_log.exists()
        sandbox.assert_not_launched()

    def test_home_reached_through_a_symlink_is_not_a_repo_root(self, sandbox, tmp_path):
        """$HOME is compared by identity, not spelling: with $HOME a symlink to the real home and the
        payload under the real path, trust is recorded for that path and the hook still must not launch."""
        home_alias = tmp_path / "home-alias"
        home_alias.symlink_to(sandbox.home)
        real_home = sandbox.home.resolve()
        (sandbox.config_dir / "settings.json").write_text(json.dumps({"enabledPlugins": {MISSING_PLUGIN_ID: True}}))
        sandbox.write_trust(repo=real_home)

        sandbox.run(payload_cwd=real_home, process_cwd=real_home, extra_env={"HOME": str(home_alias)})

        assert not sandbox.audit_log.exists()
        sandbox.assert_not_launched()

    @pytest.mark.parametrize(
        ("stdin", "payload_cwd", "process_cwd_is_repo_parent"),
        [
            (b"", None, False),
            (b"{ not json", None, False),
            (json.dumps({"source": "startup"}).encode(), None, False),
            (None, "repo", True),
            (None, "/", False),
            (None, "//", False),
            (None, "{tmp}/nonexistent", False),
            (None, "{tmp}/nonexistent/", False),
        ],
        ids=[
            "empty-stdin",
            "non-json",
            "missing-cwd",
            "relative-cwd",
            "root-directory",
            "double-slash",
            "nonexistent-path",
            "nonexistent-path-trailing-slash",
        ],
    )
    def test_odd_payload_exits_silently_without_launch_or_audit_line(
        self, sandbox, tmp_path, stdin, payload_cwd, process_cwd_is_repo_parent
    ):
        """The relative-cwd case runs from the repo's parent, where a relative `repo` would otherwise resolve."""
        sandbox.run(
            payload_cwd=None if payload_cwd is None else payload_cwd.format(tmp=tmp_path),
            process_cwd=sandbox.repo.parent if process_cwd_is_repo_parent else None,
            stdin=stdin,
        )

        assert not sandbox.audit_log.exists()
        sandbox.assert_not_launched()


class TestLocalOptOut:
    def test_id_set_false_in_local_settings_is_not_launched(self, sandbox):
        (sandbox.repo / ".claude" / "settings.local.json").write_text(
            json.dumps({"enabledPlugins": {MISSING_PLUGIN_ID: False}})
        )

        sandbox.run()

        sandbox.assert_not_launched()
        assert sandbox.audit_entries() == []

    def test_other_ids_opt_out_leaves_the_missing_id_launchable(self, sandbox):
        """Allow case: an unrelated local `false` does not suppress this id."""
        (sandbox.repo / ".claude" / "settings.local.json").write_text(
            json.dumps({"enabledPlugins": {"other@market-a": False}})
        )

        sandbox.run()

        sandbox.assert_launched_with_root()

    def test_id_set_true_in_local_settings_stays_launchable(self, sandbox):
        (sandbox.repo / ".claude" / "settings.local.json").write_text(
            json.dumps({"enabledPlugins": {MISSING_PLUGIN_ID: True}})
        )

        sandbox.run()

        sandbox.assert_launched_with_root()

    @pytest.mark.parametrize(
        "local_settings_text",
        ["{ not json", json.dumps([]), json.dumps({"enabledPlugins": "oops"})],
        ids=["malformed-json", "top-level-array", "enabledplugins-not-object"],
    )
    def test_unreadable_local_settings_is_no_launch_and_logged(self, sandbox, local_settings_text):
        """Deny case: status 3 logs m10_local_optout_unreadable, unlike the silent status 1."""
        (sandbox.repo / ".claude" / "settings.local.json").write_text(local_settings_text)

        sandbox.run()

        [entry] = sandbox.audit_entries()
        assert entry["decision"] == "m10_local_optout_unreadable"
        assert entry["outcome"] == "skipped"
        assert entry["plugin_id"] == "-"
        assert entry["repo_root"] == str(sandbox.repo)
        sandbox.assert_not_launched_after_logged_refusal()

    def test_symlinked_local_settings_is_no_launch_and_logged(self, sandbox, tmp_path):
        real_local_settings = tmp_path / "real-local.json"
        real_local_settings.write_text(json.dumps({"enabledPlugins": {}}))
        (sandbox.repo / ".claude" / "settings.local.json").symlink_to(real_local_settings)

        sandbox.run()

        [entry] = sandbox.audit_entries()
        assert entry["decision"] == "m10_local_optout_unreadable"
        sandbox.assert_not_launched_after_logged_refusal()


class TestTrustLayerOne:
    """Persisted trust record at <CLAUDE_CONFIG_DIR>/.claude.json, fail closed."""

    def test_missing_trust_file_blocks_launch_and_logs_for_interactive_session(self, sandbox):
        (sandbox.config_dir / ".claude.json").unlink()

        sandbox.run()

        [entry] = sandbox.audit_entries()
        assert set(entry) == AUDIT_LINE_KEYS
        assert entry["decision"] == "m1a_not_trusted"
        assert entry["outcome"] == "skipped"
        assert entry["plugin_id"] == "-"
        assert entry["repo_root"] == str(sandbox.repo)
        assert entry["pid"].isdigit()
        sandbox.assert_not_launched_after_logged_refusal()

    def test_layer_one_refusal_is_never_launched_after_logging(self, sandbox):
        """Polled: a branch that logs m1a_not_trusted and then falls through to the launch statement
        must fail every run, not the fraction the immediate check catches."""
        (sandbox.config_dir / ".claude.json").unlink()

        sandbox.run()

        [entry] = sandbox.audit_entries()
        assert entry["decision"] == "m1a_not_trusted"
        sandbox.assert_not_launched()

    @pytest.mark.parametrize(
        "trust_file_text",
        [
            json.dumps({"projects": {}}),
            json.dumps({}),
            "{ not json",
            "",
            json.dumps({"projects": []}),
            json.dumps({"projects": "oops"}),
            json.dumps(["not", "an", "object"]),
            json.dumps({"projects": {"REPO": {}}}),
            json.dumps({"projects": {"REPO": {"hasTrustDialogAccepted": False}}}),
            json.dumps({"projects": {"REPO": {"hasTrustDialogAccepted": "true"}}}),
            json.dumps({"projects": {"REPO": {"hasTrustDialogAccepted": 1}}}),
            json.dumps({"projects": {"REPO": {"hasTrustDialogAccepted": None}}}),
            json.dumps({"projects": {"REPO": "trusted"}}),
            json.dumps({"projects": {"/some/other/repo": {"hasTrustDialogAccepted": True}}}),
        ],
        ids=[
            "empty-projects",
            "empty-object",
            "malformed-json",
            "empty-file",
            "projects-is-array",
            "projects-is-string",
            "top-level-array",
            "field-missing",
            "declined",
            "string-true",
            "number-one",
            "null",
            "project-entry-not-object",
            "only-another-repo-trusted",
        ],
    )
    def test_untrusted_or_unrecognized_trust_record_blocks_launch(self, sandbox, trust_file_text):
        (sandbox.config_dir / ".claude.json").write_text(trust_file_text.replace("REPO", str(sandbox.repo)))

        sandbox.run()

        [entry] = sandbox.audit_entries()
        assert entry["decision"] == "m1a_not_trusted"
        sandbox.assert_not_launched_after_logged_refusal()

    def test_trust_in_home_dot_claude_json_does_not_count_when_config_dir_is_set(self, sandbox):
        """Trust must not leak across accounts: with CLAUDE_CONFIG_DIR set, only that dir's record is read."""
        (sandbox.config_dir / ".claude.json").unlink()
        sandbox.write_trust(trust_file=sandbox.home / ".claude.json")

        sandbox.run()

        sandbox.assert_not_launched()

    def test_trust_record_in_home_is_read_when_config_dir_env_is_unset(self, sandbox):
        (sandbox.config_dir / ".claude.json").unlink()
        sandbox.write_trust(trust_file=sandbox.home / ".claude.json")

        sandbox.run(config_dir_env=None)

        sandbox.assert_launched_with_root()

    def test_trust_in_config_dir_is_not_read_when_config_dir_env_is_unset(self, sandbox):
        sandbox.run(config_dir_env=None)

        sandbox.assert_not_launched()

    def test_trusted_ancestor_directory_does_not_trust_the_repo_below_it(self, sandbox):
        sandbox.write_trust(repo=sandbox.repo.parent)

        sandbox.run()

        [entry] = sandbox.audit_entries()
        assert entry["decision"] == "m1a_not_trusted"
        sandbox.assert_not_launched_after_logged_refusal()

    def test_trust_file_that_is_a_directory_is_untrusted_and_logged(self, sandbox):
        trust_file = sandbox.config_dir / ".claude.json"
        trust_file.unlink()
        trust_file.mkdir()

        sandbox.run()

        [entry] = sandbox.audit_entries()
        assert entry["decision"] == "m1a_not_trusted"
        sandbox.assert_not_launched_after_logged_refusal()

    @pytest.mark.skipif(os.geteuid() == 0, reason="root reads a mode-000 file")
    def test_unreadable_trust_file_is_untrusted_and_logged(self, sandbox):
        trust_file = sandbox.config_dir / ".claude.json"
        trust_file.chmod(0o000)
        try:
            sandbox.run()
        finally:
            trust_file.chmod(0o644)

        [entry] = sandbox.audit_entries()
        assert entry["decision"] == "m1a_not_trusted"
        sandbox.assert_not_launched_after_logged_refusal()

    def test_symlinked_trust_file_is_followed(self, sandbox, tmp_path):
        """Policy: the vendor owns .claude.json and may keep it behind a symlink, so the hook follows it."""
        real_trust_file = tmp_path / "real-claude.json"
        sandbox.write_trust(trust_file=real_trust_file)
        (sandbox.config_dir / ".claude.json").unlink()
        (sandbox.config_dir / ".claude.json").symlink_to(real_trust_file)

        sandbox.run()

        sandbox.assert_launched_with_root()

    def test_trailing_slash_on_config_dir_env_still_finds_the_trust_file_and_reconciler(self, sandbox):
        sandbox.run(config_dir_env=f"{sandbox.config_dir}/")

        sandbox.assert_launched_with_root()

    def test_non_interactive_session_in_untrusted_repo_writes_no_audit_line(self, sandbox):
        (sandbox.config_dir / ".claude.json").unlink()

        sandbox.run(entrypoint="sdk-cli")

        sandbox.assert_not_launched()
        assert sandbox.audit_entries() == []


class TestTrustLayerTwo:
    """CLAUDE_CODE_ENTRYPOINT must be exactly `cli`."""

    @pytest.mark.parametrize(
        ("entrypoint", "expected_outcome"),
        [
            ("sdk-cli", 'skipped entrypoint="sdk-cli"'),
            ("", "skipped entrypoint=unset"),
            ("CLI", 'skipped entrypoint="CLI"'),
            ("cli ", "skipped entrypoint=unrecognized"),
            (" cli", "skipped entrypoint=unrecognized"),
            ("cli\t", "skipped entrypoint=unrecognized"),
            ("vscode", 'skipped entrypoint="vscode"'),
            (None, "skipped entrypoint=unset"),
        ],
        ids=["sdk-cli", "empty", "uppercase", "trailing-space", "leading-space", "trailing-tab", "vscode", "unset"],
    )
    def test_anything_but_exact_cli_blocks_launch_and_logs_observed_value(
        self, sandbox, entrypoint, expected_outcome
    ):
        sandbox.run(entrypoint=entrypoint)

        [entry] = sandbox.audit_entries()
        assert set(entry) == AUDIT_LINE_KEYS
        assert entry["decision"] == "m1a_non_interactive"
        assert entry["outcome"] == expected_outcome
        sandbox.assert_not_launched_after_logged_refusal()

    def test_layer_two_refusal_is_never_launched_after_logging(self, sandbox):
        """Polled: a branch that logs m1a_non_interactive and then falls through to the launch statement
        must fail every run, not the fraction the immediate check catches."""
        sandbox.run(entrypoint="sdk-cli")

        [entry] = sandbox.audit_entries()
        assert entry["decision"] == "m1a_non_interactive"
        sandbox.assert_not_launched()

    @pytest.mark.parametrize(
        "hostile_entrypoint",
        [
            "sdk\ncli\x1b[31m\x07",
            "a\u009bb\u0080c\u009fd",
            "a" * (ENTRYPOINT_LOGGED_MAX_LENGTH + 1),
            "\u00e9" * 20000,
            '"quoted"',
        ],
        ids=["c0-and-newline", "c1", "one-past-the-length-limit", "20000-multibyte-characters", "quote-characters"],
    )
    def test_hostile_or_overlong_entrypoint_value_is_logged_as_unrecognized(self, sandbox, hostile_entrypoint):
        sandbox.run(entrypoint=hostile_entrypoint)

        [entry] = sandbox.audit_entries()
        assert entry["outcome"] == "skipped entrypoint=unrecognized"
        assert sandbox.audit_log.read_text().count("\n") == 1

    def test_non_ascii_entrypoint_is_unrecognized_even_under_a_locale_that_widens_the_glob_ranges(
        self, sandbox
    ):
        """The logged alphabet is ASCII-only only because the check pins LC_ALL=C. Under a
        locale that widens the entrypoint `case` glob, an unpinned `[A-Za-z0-9]` admits the
        fullwidth digit and the value would be logged."""
        widening_locale = _locale_that_widens_ascii_ranges()
        fullwidth_digit_entrypoint = "sdk\uff10"
        glob_probe = subprocess.run(
            ["bash", "-c", 'case "$1" in *[!A-Za-z0-9._-]*) exit 1 ;; esac', "bash", fullwidth_digit_entrypoint],
            env={**os.environ, "LC_ALL": widening_locale}, capture_output=True, check=False,
        )
        if glob_probe.returncode != 0:
            reason = f"{widening_locale} does not widen the `case` glob range"
            if os.environ.get("CI"):
                pytest.fail(f"{reason}; CI must provide a locale that does")
            pytest.skip(reason)

        sandbox.run(entrypoint=fullwidth_digit_entrypoint, extra_env={"LC_ALL": widening_locale})

        [entry] = sandbox.audit_entries()
        assert entry["outcome"] == "skipped entrypoint=unrecognized"

    def test_entrypoint_value_at_the_length_limit_is_logged_as_itself(self, sandbox):
        at_limit = "a" * ENTRYPOINT_LOGGED_MAX_LENGTH
        sandbox.run(entrypoint=at_limit)

        [entry] = sandbox.audit_entries()
        assert entry["outcome"] == f'skipped entrypoint="{at_limit}"'

    def test_entrypoint_value_using_every_allowed_character_kind_is_logged_as_itself(self, sandbox):
        sandbox.run(entrypoint="Sdk.cli_2-x")

        [entry] = sandbox.audit_entries()
        assert entry["outcome"] == 'skipped entrypoint="Sdk.cli_2-x"'

    @pytest.mark.parametrize(
        ("layer_one_trusted", "interactive", "expected_decision"),
        [
            (True, True, None),
            (True, False, "m1a_non_interactive"),
            (False, True, "m1a_not_trusted"),
            (False, False, None),
        ],
        ids=["both-pass", "layer-two-fails", "layer-one-fails", "both-fail"],
    )
    def test_each_layer_blocks_on_its_own(self, sandbox, layer_one_trusted, interactive, expected_decision):
        if not layer_one_trusted:
            (sandbox.config_dir / ".claude.json").unlink()

        sandbox.run(entrypoint=INTERACTIVE_ENTRYPOINT if interactive else "sdk-cli")

        decisions = [entry["decision"] for entry in sandbox.audit_entries()]
        assert decisions == ([expected_decision] if expected_decision else [])
        if layer_one_trusted and interactive:
            sandbox.assert_launched_with_root()
        elif expected_decision:
            sandbox.assert_not_launched_after_logged_refusal()
        else:
            sandbox.assert_not_launched()


class TestMachineLevelSwitch:
    def test_plugin_bootstrap_false_exits_before_any_repo_read(self, sandbox):
        """Untrusted plus interactive would write an m1a_not_trusted line if any repo-side check ran."""
        sandbox.write_config_toml("plugin_bootstrap = false\n")
        (sandbox.config_dir / ".claude.json").unlink()

        sandbox.run()

        sandbox.assert_not_launched()
        assert not sandbox.audit_log.exists()

    def test_plugin_bootstrap_false_blocks_an_otherwise_launchable_session(self, sandbox):
        sandbox.write_config_toml("plugin_bootstrap = false\n")

        sandbox.run()

        sandbox.assert_not_launched()

    def test_plugin_bootstrap_true_launches(self, sandbox):
        sandbox.write_config_toml("plugin_bootstrap = true\n")

        sandbox.run()

        sandbox.assert_launched_with_root()

    def test_wrong_type_value_falls_back_to_default_on(self, sandbox):
        """_config_enabled warns on stderr about the malformed line."""
        sandbox.write_config_toml('plugin_bootstrap = "false"\n')

        sandbox.run(stderr_contains=b"skipping malformed line")

        sandbox.assert_launched_with_root()

    @pytest.mark.skipif(os.geteuid() == 0, reason="root reads a mode-000 file")
    def test_unreadable_config_toml_leaves_the_hook_on(self, sandbox):
        toml_file = sandbox.write_config_toml("plugin_bootstrap = false\n")
        toml_file.chmod(0o000)
        try:
            sandbox.run()
            sandbox.assert_launched_with_root()
        finally:
            toml_file.chmod(0o644)

    def test_unset_home_and_config_dir_is_no_launch_and_writes_no_audit_line_in_the_sandbox(self, sandbox):
        """Pins the outcome only. Without the config-dir guard the hook would try a root-level
        plugin-bootstrap directory, which an unprivileged run cannot observe."""
        (sandbox.config_dir / ".claude.json").unlink()

        sandbox.run(config_dir_env=None, unset_home=True)

        assert not sandbox.audit_log.exists()
        sandbox.assert_not_launched()

    def _hook_dir_with_schema(self, sandbox: Sandbox, tmp_path: Path, psv_text: str) -> Path:
        """A hooks dir whose config-keys.psv is psv_text: _lib.sh sources _config.sh by its own
        location, which resolves the schema file beside the symlink, not beside its target."""
        isolated_hooks_dir = tmp_path / "isolated" / "hooks"
        isolated_hooks_dir.mkdir(parents=True)
        shutil.copy2(BOOTSTRAP_HOOK, isolated_hooks_dir / BOOTSTRAP_HOOK.name)
        (isolated_hooks_dir.parent / "scripts").symlink_to(sandbox.scripts_dir)
        (isolated_hooks_dir / "_lib.sh").symlink_to(HOOKS_DIR / "_lib.sh")
        (isolated_hooks_dir / "_config.sh").symlink_to(HOOKS_DIR / "_config.sh")
        (isolated_hooks_dir / "config-keys.psv").write_text(psv_text)
        return isolated_hooks_dir

    def test_isolated_hooks_dir_with_the_real_schema_launches(self, sandbox, tmp_path):
        """Control for the schema-gap cases below: the copied hook launches when the schema is intact."""
        isolated_hooks_dir = self._hook_dir_with_schema(sandbox, tmp_path, (HOOKS_DIR / "config-keys.psv").read_text())

        sandbox.run(hook=isolated_hooks_dir / BOOTSTRAP_HOOK.name)

        sandbox.assert_launched_with_root()

    def test_schema_without_a_plugin_bootstrap_row_exits_before_any_repo_read(self, sandbox, tmp_path):
        rows_without_plugin_bootstrap = [
            line for line in (HOOKS_DIR / "config-keys.psv").read_text().splitlines()
            if not line.startswith("plugin_bootstrap|")
        ]
        isolated_hooks_dir = self._hook_dir_with_schema(sandbox, tmp_path, "\n".join(rows_without_plugin_bootstrap) + "\n")
        (sandbox.config_dir / ".claude.json").unlink()

        sandbox.run(hook=isolated_hooks_dir / BOOTSTRAP_HOOK.name, stderr_contains=b"no schema row for key")

        assert not sandbox.audit_log.exists()
        sandbox.assert_not_launched()

    @pytest.mark.skipif(os.geteuid() == 0, reason="root reads a mode-000 file")
    def test_unreadable_schema_exits_before_any_repo_read(self, sandbox, tmp_path):
        isolated_hooks_dir = self._hook_dir_with_schema(sandbox, tmp_path, (HOOKS_DIR / "config-keys.psv").read_text())
        schema_file = isolated_hooks_dir / "config-keys.psv"
        schema_file.chmod(0o000)
        (sandbox.config_dir / ".claude.json").unlink()
        try:
            sandbox.run(
                hook=isolated_hooks_dir / BOOTSTRAP_HOOK.name, stderr_contains=b"schema file not found or unreadable"
            )
        finally:
            schema_file.chmod(0o644)

        assert not sandbox.audit_log.exists()
        sandbox.assert_not_launched()

    def test_relative_config_dir_env_is_no_launch_and_writes_no_audit_line_in_the_sandbox(self, sandbox):
        """Pins the outcome only, like the unset-HOME case above: the guard itself is unobservable
        without privileges."""
        sandbox.run(config_dir_env="relative/config-dir")

        assert not sandbox.audit_log.exists()
        sandbox.assert_not_launched()


class TestReconcilerLocation:
    """The reconciler is found beside the hook, and a config dir inside the repo is refused:
    a repo that chooses CLAUDE_CONFIG_DIR would otherwise choose both the trust record and the program run."""

    ATTACKER_SENTINEL_NAME = "attacker-ran"

    def _write_attacker_reconciler(self, config_dir: Path, sentinel: Path) -> None:
        scripts_dir = config_dir / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        attacker = scripts_dir / "reconcile-project-plugins.sh"
        attacker.write_text(f"#!/bin/sh\ntouch {shlex.quote(str(sentinel))}\n")
        attacker.chmod(0o755)

    def test_reconciler_beside_the_hook_is_launched_not_the_one_in_the_config_dir(self, sandbox, tmp_path):
        attacker_sentinel = tmp_path / self.ATTACKER_SENTINEL_NAME
        self._write_attacker_reconciler(sandbox.config_dir, attacker_sentinel)

        sandbox.run()

        sandbox.assert_launched_with_root()
        assert not attacker_sentinel.exists()

    @pytest.mark.parametrize(
        "config_dir_kind",
        ["subdirectory-of-repo", "repo-root-itself", "symlink-into-repo", "dot-dot-segment-back-into-repo", "trailing-slash"],
    )
    def test_config_dir_inside_the_repo_is_no_launch_even_with_a_self_trusting_record(
        self, sandbox, tmp_path, config_dir_kind
    ):
        attacker_sentinel = tmp_path / self.ATTACKER_SENTINEL_NAME
        in_repo_config_dir = sandbox.repo if config_dir_kind == "repo-root-itself" else sandbox.repo / ".attacker-config"
        in_repo_config_dir.mkdir(exist_ok=True)
        sandbox.write_trust(trust_file=in_repo_config_dir / ".claude.json")
        self._write_attacker_reconciler(in_repo_config_dir, attacker_sentinel)
        config_dir_env = str(in_repo_config_dir)
        if config_dir_kind == "symlink-into-repo":
            symlinked_config_dir = tmp_path / "config-dir-link"
            symlinked_config_dir.symlink_to(in_repo_config_dir)
            config_dir_env = str(symlinked_config_dir)
        elif config_dir_kind == "dot-dot-segment-back-into-repo":
            config_dir_env = f"{tmp_path}/elsewhere/../{sandbox.repo.name}/{in_repo_config_dir.name}"
        elif config_dir_kind == "trailing-slash":
            config_dir_env = f"{in_repo_config_dir}/"

        sandbox.run(config_dir_env=config_dir_env)

        sandbox.assert_not_launched()
        assert not attacker_sentinel.exists()
        assert not sandbox.audit_log.exists()
        assert not (in_repo_config_dir / "plugin-bootstrap").exists()

    def _prepare_home_with_a_symlinked_config_dir(self, sandbox, home: Path) -> None:
        """A HOME whose `.claude` is the sandbox's real config dir, and whose `.claude.json` names the repo as trusted."""
        home.mkdir(parents=True)
        (home / ".claude").symlink_to(sandbox.config_dir)
        sandbox.write_trust(trust_file=home / ".claude.json")

    def test_home_inside_the_repo_holding_the_trust_record_is_no_launch_though_the_config_dir_is_outside(
        self, sandbox
    ):
        """With CLAUDE_CONFIG_DIR unset the trust record is `$HOME/.claude.json`, a sibling of the config dir,
        so a repo-chosen HOME could trust itself while its `.claude` symlink keeps the config dir outside the repo."""
        self._prepare_home_with_a_symlinked_config_dir(sandbox, sandbox.repo / "fakehome")

        sandbox.run(config_dir_env=None, extra_env={"HOME": str(sandbox.repo / "fakehome")})

        sandbox.assert_not_launched()
        assert not sandbox.audit_log.exists()

    def test_home_dot_claude_symlinked_into_the_repo_is_no_launch_though_the_trust_record_is_outside(
        self, sandbox, tmp_path
    ):
        """The mirror of the in-repo trust record: with CLAUDE_CONFIG_DIR unset the config dir is `$HOME/.claude`,
        so a `.claude` symlink into the repo (a dotfiles checkout that is also the session's repo) puts the config dir
        in the repo while the trust record beside it stays outside."""
        in_repo_config_dir = sandbox.repo / ".attacker-config"
        in_repo_config_dir.mkdir()
        home_with_linked_config_dir = tmp_path / "home-with-linked-config-dir"
        home_with_linked_config_dir.mkdir()
        (home_with_linked_config_dir / ".claude").symlink_to(in_repo_config_dir)
        sandbox.write_trust(trust_file=home_with_linked_config_dir / ".claude.json")

        sandbox.run(config_dir_env=None, extra_env={"HOME": str(home_with_linked_config_dir)})

        sandbox.assert_not_launched()
        assert not (in_repo_config_dir / "plugin-bootstrap").exists()

    def test_home_outside_the_repo_holding_the_trust_record_still_launches(self, sandbox, tmp_path):
        """Allow control for the in-repo trust-record refusal: the same layout with HOME beside the repo launches."""
        self._prepare_home_with_a_symlinked_config_dir(sandbox, tmp_path / "fakehome-outside-the-repo")

        sandbox.run(config_dir_env=None, extra_env={"HOME": str(tmp_path / "fakehome-outside-the-repo")})

        sandbox.assert_launched_with_root()

    def test_config_dir_beside_the_repo_sharing_its_name_prefix_still_launches(self, sandbox, tmp_path):
        """Allow control for the in-repo refusal: a sibling whose path merely starts with the repo's is not inside it."""
        sibling_config_dir = Path(f"{sandbox.repo}-config")
        sibling_config_dir.mkdir()
        sandbox.write_trust(trust_file=sibling_config_dir / ".claude.json")

        sandbox.run(config_dir_env=str(sibling_config_dir))

        sandbox.assert_launched_with_root()


class TestConvergence:
    def test_hook_then_real_reconciler_then_hook_again_launches_once(self, sandbox, tmp_path):
        """The hook launches the real reconciler, which installs through a shim that records a
        version-2 registry entry. The next session then finds nothing missing, so it does not launch."""
        real_reconciler = sandbox.scripts_dir / "reconcile-project-plugins.sh"
        real_reconciler.unlink()
        real_reconciler.symlink_to(SCRIPTS_DIR / "reconcile-project-plugins.sh")
        (sandbox.repo / ".gitignore").write_text(".claude/settings.local.json\n")
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        registry_file = plugin_registry_file(sandbox.config_dir)
        make_reconciler_claude_shim(
            bin_dir,
            marketplace_entries=[{"name": "market-a", "source": "directory"}],
            install_log=install_log,
            install_writes_registry=registry_file,
        )
        git_isolation = {
            name: value for name, value in git_isolation_env(tmp_path).items() if name != "HOME"
        }
        session_env = {"PATH": f"{bin_dir}:{os.environ['PATH']}", **git_isolation}

        sandbox.run(extra_env=session_env)
        deadline = time.monotonic() + LAUNCH_DEADLINE_SECONDS
        while time.monotonic() < deadline and not any(
            entry["decision"] == "install_succeeded" for entry in sandbox.audit_entries()
        ):
            time.sleep(POLL_INTERVAL_SECONDS)

        assert [entry["plugin"] for entry in install_log_entries(install_log)] == [MISSING_PLUGIN_ID]
        assert MISSING_PLUGIN_ID in json.loads(registry_file.read_text())["plugins"]
        sandbox.install_stub()
        sandbox.run(extra_env=session_env)
        sandbox.assert_not_launched()
        assert [entry["decision"] for entry in sandbox.audit_entries()] == ["install_succeeded"], (
            "the second session found nothing missing, so the hook logged no refusal of its own"
        )


class TestAuditLog:
    def test_audit_directory_and_log_are_owner_only_under_a_permissive_umask(self, sandbox):
        """The log records the absolute path of every repo that was refused, so group and world get no access."""
        (sandbox.config_dir / ".claude.json").unlink()

        with ambient_umask(0o022):
            sandbox.run()

        assert file_mode(sandbox.audit_log.parent) == 0o700
        assert file_mode(sandbox.audit_log) == 0o600

    def test_concurrent_refusals_from_separate_sessions_all_land_intact_and_distinct(self, sandbox):
        (sandbox.config_dir / ".claude.json").unlink()
        concurrent_session_count = 4
        sessions = [
            subprocess.Popen(
                [str(sandbox.hook)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=sandbox.repo,
                env=sandbox.env(),
            )
            for _ in range(concurrent_session_count)
        ]
        for session in sessions:
            session.communicate(sandbox.payload(), timeout=LAUNCH_DEADLINE_SECONDS)
            assert session.returncode == 0

        entries = sandbox.audit_entries()
        assert len(entries) == concurrent_session_count
        assert {entry["decision"] for entry in entries} == {"m1a_not_trusted"}
        assert len({entry["pid"] for entry in entries}) == concurrent_session_count

    def test_audit_log_past_size_cap_is_not_emptied_while_another_writer_holds_the_lock(self, sandbox):
        """Truncation happens only under the lock: a live holder makes the bounded retries
        run out, and the hook then appends without truncating."""
        (sandbox.config_dir / ".claude.json").unlink()
        sandbox.audit_log.parent.mkdir(parents=True)
        oversized_line = "x" * (SIZE_CAP_BYTES + 1)
        sandbox.audit_log.write_text(oversized_line + "\n")
        lock_file = sandbox.audit_log.parent / "audit.log.lock"
        lock_file.write_text(f"{os.getpid()}\n")

        sandbox.run()

        logged_lines = sandbox.audit_log.read_text().splitlines()
        assert logged_lines[0] == oversized_line
        assert json.loads(logged_lines[-1])["decision"] == "m1a_not_trusted"
        assert lock_file.read_text().strip() == str(os.getpid()), "the hook must not release a lock it does not hold"

    def test_audit_log_past_size_cap_is_emptied_after_evicting_a_dead_holders_lock(self, sandbox):
        (sandbox.config_dir / ".claude.json").unlink()
        sandbox.audit_log.parent.mkdir(parents=True)
        sandbox.audit_log.write_text("x" * (SIZE_CAP_BYTES + 1) + "\n")
        finished = subprocess.Popen(["true"])
        finished.wait()
        lock_file = sandbox.audit_log.parent / "audit.log.lock"
        lock_file.write_text(f"{finished.pid}\n")

        sandbox.run()

        [entry] = sandbox.audit_entries()
        assert entry["decision"] == "m1a_not_trusted"
        assert not lock_file.exists(), "the hook releases the lock it took when it exits"

    @pytest.mark.skipif(os.geteuid() == 0, reason="root writes a read-only file")
    def test_failed_truncation_of_an_oversized_read_only_audit_log_keeps_stderr_clean(self, sandbox):
        """run() asserts rc 0 and an empty stderr: the truncating redirect's own open error is silenced
        only when `2>/dev/null` is applied before the file is opened."""
        (sandbox.config_dir / ".claude.json").unlink()
        sandbox.audit_log.parent.mkdir(parents=True)
        oversized_content = "x" * (SIZE_CAP_BYTES + 1) + "\n"
        sandbox.audit_log.write_text(oversized_content)
        sandbox.audit_log.chmod(0o444)
        try:
            sandbox.run()
        finally:
            sandbox.audit_log.chmod(0o644)

        assert sandbox.audit_log.read_text() == oversized_content
        sandbox.assert_not_launched()

    def test_unwritable_audit_log_leaves_the_exit_status_and_stderr_clean(self, sandbox):
        """run() asserts rc 0 and an empty stderr, so a failed append may not leak the shell's redirection error."""
        (sandbox.config_dir / ".claude.json").unlink()
        sandbox.audit_log.mkdir(parents=True)

        sandbox.run()

        sandbox.assert_not_launched()

    def test_repo_root_control_characters_are_stripped_from_the_audit_line(self, tmp_path, no_launch_window_seconds):
        """C0 (ESC, BEL, newline) and C1 (U+009B) in a directory name must not reach the shared log."""
        hostile_sandbox = Sandbox(
            tmp_path, repo_name="repo\x1b[31m\u009b\x07\nend", no_launch_window_seconds=no_launch_window_seconds
        )
        (hostile_sandbox.config_dir / ".claude.json").unlink()

        hostile_sandbox.run()

        [entry] = hostile_sandbox.audit_entries()
        assert entry["repo_root"] == str(hostile_sandbox.repo.parent / "repo[31mend")
        assert hostile_sandbox.audit_log.read_text().count("\n") == 1

    def test_non_ascii_repo_root_is_logged_intact(self, tmp_path, no_launch_window_seconds):
        """The cap counts characters, so a path well under it loses none of its multibyte characters."""
        non_ascii_sandbox = Sandbox(
            tmp_path, repo_name="r\u00e9po-\u65e5\u672c\u8a9e", no_launch_window_seconds=no_launch_window_seconds
        )
        (non_ascii_sandbox.config_dir / ".claude.json").unlink()

        non_ascii_sandbox.run()

        [entry] = non_ascii_sandbox.audit_entries()
        assert entry["repo_root"] == str(non_ascii_sandbox.repo)

    def test_hook_and_reconciler_write_audit_lines_with_the_same_keys_and_value_shapes(self, sandbox, tmp_path):
        """Both writers feed one log, so a reader sees one schema: same key order, a string pid, a
        UTC ISO-8601 timestamp, and "-" as the plugin id of a decision that names no id."""
        (sandbox.config_dir / ".claude.json").unlink()
        sandbox.run(extra_env={"TZ": NON_UTC_TIMEZONE})
        [hook_entry] = sandbox.audit_entries()

        reconciler_repo = tmp_path / "reconciler-repo"
        reconciler_config_dir = tmp_path / "reconciler-config"
        reconciler_bin_dir = tmp_path / "reconciler-bin"
        reconciler_bin_dir.mkdir()
        init_reconciler_repo(reconciler_repo, enabled_plugins={"foo@bar": True})
        write_plugin_registry(plugin_registry_file(reconciler_config_dir), {}, version=1)
        make_reconciler_claude_shim(reconciler_bin_dir)
        run_reconciler(
            reconciler_repo,
            config_dir=reconciler_config_dir,
            bin_dir=reconciler_bin_dir,
            extra_env={"TZ": NON_UTC_TIMEZONE},
        )
        [reconciler_entry] = reconciler_audit_lines(reconciler_config_dir)

        assert list(hook_entry) == list(reconciler_entry)
        assert set(hook_entry) == AUDIT_LINE_KEYS
        for writer, entry in (("hook", hook_entry), ("reconciler", reconciler_entry)):
            assert isinstance(entry["pid"], str) and entry["pid"].isdigit(), f"{writer} pid: {entry['pid']!r}"
            assert entry["plugin_id"] == "-", f"{writer} plugin_id: {entry['plugin_id']!r}"
            assert AUDIT_TIMESTAMP_PATTERN.fullmatch(entry["timestamp"]), f"{writer} timestamp: {entry['timestamp']!r}"
            logged_at = datetime.strptime(entry["timestamp"], AUDIT_TIMESTAMP_FORMAT).replace(tzinfo=UTC)
            age_seconds = abs((datetime.now(UTC) - logged_at).total_seconds())
            assert age_seconds < AUDIT_TIMESTAMP_MAX_AGE_SECONDS, f"{writer} timestamp is not UTC now: {entry['timestamp']!r}"

    def test_audit_log_past_size_cap_is_emptied_before_the_new_line_lands(self, sandbox):
        (sandbox.config_dir / ".claude.json").unlink()
        sandbox.audit_log.parent.mkdir(parents=True)
        sandbox.audit_log.write_text("x" * (SIZE_CAP_BYTES + 1) + "\n")

        sandbox.run()

        [entry] = sandbox.audit_entries()
        assert entry["decision"] == "m1a_not_trusted"


class TestRegistration:
    HOOK_COMMAND = "~/.claude/hooks/bootstrap-project-plugins.sh"

    def test_session_start_entry_is_startup_matched_literal_command_with_no_timeout(self):
        """No timeout: the hook spawns no `claude`, so the harness default applies as it does to the sibling entries."""
        session_start_groups = json.loads(SETTINGS_JSON.read_text())["hooks"]["SessionStart"]
        matching_groups = [
            group
            for group in session_start_groups
            if any(entry.get("command") == self.HOOK_COMMAND for entry in group["hooks"])
        ]

        [group] = matching_groups
        [hook_entry] = group["hooks"]
        assert group["matcher"] == "startup"
        assert hook_entry["type"] == "command"
        assert "timeout" not in hook_entry
