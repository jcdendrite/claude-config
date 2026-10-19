"""Tests for reconcile-project-plugins.sh.

Analog: test_register_marketplace.py. Runs the whole script against a
`claude` PATH shim and real scratch git repos, synchronously -- the
detached-child launch (`</dev/null >/dev/null 2>&1 &`) is
bootstrap-project-plugins.sh's own invocation contract, exercised by that
hook's own tests (claude/.claude/hooks/tests/test_bootstrap_project_plugins.py).
`_lib_missing_plugin_ids` and
`_lib_validate_plugin_id`, the two shared `_lib.sh` helpers this script
calls, have their own direct unit tests in
claude/.claude/hooks/tests/test_lib.py, independent of this file.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
import time
import unicodedata
from pathlib import Path

import pytest
from helpers import CLAUDE_DIR

# claude/.claude/ itself, so `hooks.tests...` below resolves, as in test_review_ledger_lib.py.
sys.path.insert(0, str(CLAUDE_DIR))
from hooks.tests.test_config_lib import _locale_that_widens_ascii_ranges  # noqa: E402

from .conftest import _dead_pid  # noqa: E402
from .test_update_claude_config_plugins import _make_claude_shim

_RECONCILE_SH = Path(__file__).resolve().parent.parent / "reconcile-project-plugins.sh"
_BASH = shutil.which("bash") or "/bin/bash"

# Isolates every scratch repo's `git check-ignore`/`ls-files` calls (M6) from
# whatever global git config the account running these tests happens to have
# -- row 11 of the plan's own assumption ledger found
# `.claude/settings.local.json` gitignored on the engineer's own machine via
# exactly such a global file, not the repo's own `.gitignore`, confirmed
# dynamically for this file: `git check-ignore -v` traced the match to
# `~/.config/git/ignore`. GIT_CONFIG_GLOBAL/GIT_CONFIG_SYSTEM alone don't
# reach that file -- it's git's XDG-default excludes path, resolved from
# $HOME independently of gitconfig content -- so HOME must be overridden too.
# Without all three, a test asserting "not ignored" could pass or fail
# depending on the executing account's own global git state instead of the
# repo fixture under test. Git >= 2.32 (this repo's CI and dev baseline) for
# GIT_CONFIG_GLOBAL/GIT_CONFIG_SYSTEM.
def _git_isolation_env(tmp_path: Path) -> dict:
    isolated_home = tmp_path / "isolated-home-for-git"
    isolated_home.mkdir(parents=True, exist_ok=True)
    return {
        "HOME": str(isolated_home),
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
    }

# The reconciler's own credential-bearing-plugin denylist (M4) -- kept in
# sync with reconcile-project-plugins.sh's _RECONCILER_CREDENTIAL_DENYLIST
# for the one test that exercises it.
_DENYLISTED_PLUGIN_ID = "issue-triage@claude-config"


# ---------------------------------------------------------------------------
# Fixtures: scratch repo, registry, and a `claude` PATH shim tailored to the
# reconciler's own two subcommands (marketplace list, plugin install).
# ---------------------------------------------------------------------------

def _init_repo(repo: Path, *, enabled_plugins: dict, gitignore_settings_local: bool = True) -> None:
    (repo / ".claude").mkdir(parents=True)
    subprocess.run(
        ["git", "init", "-q"], cwd=repo, check=True,
        env={**os.environ, **_git_isolation_env(repo.parent)},
    )
    if gitignore_settings_local:
        (repo / ".gitignore").write_text(".claude/settings.local.json\n")
    (repo / ".claude" / "settings.json").write_text(json.dumps({"enabledPlugins": enabled_plugins}))


def _write_registry(config_dir: Path, plugins: dict, version: int = 2) -> Path:
    registry_dir = config_dir / "plugins"
    registry_dir.mkdir(parents=True, exist_ok=True)
    registry_file = registry_dir / "installed_plugins.json"
    registry_file.write_text(json.dumps({"version": version, "plugins": plugins}))
    return registry_file


def _make_reconciler_claude_shim(
    bin_dir: Path,
    *,
    marketplace_entries: list[dict] | None = None,
    marketplace_raw_stdout: str | None = None,
    marketplace_list_exit_code: int = 0,
    marketplace_list_sleep_seconds: float = 0,
    fail_install_ids: frozenset[str] = frozenset(),
    install_log: Path | None = None,
    backdate_marker: Path | None = None,
    backdate_marker_seconds_ago: int = 700,
) -> None:
    """A `claude` PATH shim tailored to the reconciler's own two
    subcommands -- more configurable than test_update_claude_config_plugins
    .py's `_make_claude_shim` (which hardcodes a single "claude-config"/
    "directory" marketplace entry), needed here to vary the marketplace's
    name and `.source` value per M4 test.

    `plugin marketplace list --json` prints `marketplace_raw_stdout` verbatim
    when given (for the "unparseable output" case), else
    `json.dumps(marketplace_entries or [])`, and exits
    `marketplace_list_exit_code` (stdout is empty for a non-zero exit,
    matching a real CLI failure). When `marketplace_list_sleep_seconds` is
    set, this call sleeps that long before responding -- for exercising
    _lib_capped_for's own kill-and-nonzero-exit path at this call site,
    rather than a shim exiting nonzero directly. When `backdate_marker` is
    given, this call also sets that file's mtime `backdate_marker_seconds_ago`
    seconds into the past before returning -- the marketplace listing is the
    run's first `claude` call after the marker is created (M5), so this lets
    a test simulate the wall-clock ceiling already having elapsed without an
    actual 600s wait.

    `plugin install ... <id> ...` (the id found positionally, tolerating
    `-s`/`--scope`/`--` anywhere in argv) records `{"plugin": id, "args":
    argv, "cwd": os.getcwd()}` to `install_log` when given, and exits 1 for
    any id in `fail_install_ids`.
    """
    marketplace_stdout = (
        marketplace_raw_stdout
        if marketplace_raw_stdout is not None
        else json.dumps(marketplace_entries or [])
    )
    install_log_repr = repr(str(install_log)) if install_log else "None"
    backdate_marker_repr = repr(str(backdate_marker)) if backdate_marker else "None"

    shim = textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import json, os, sys, time

        args = sys.argv[1:]
        FAIL_INSTALL_IDS = {repr(sorted(fail_install_ids))}
        install_log = {install_log_repr}
        backdate_marker = {backdate_marker_repr}

        def is_subcommand(*parts):
            return args[:len(parts)] == list(parts)

        if is_subcommand("plugin", "marketplace", "list") and "--json" in args:
            if {marketplace_list_sleep_seconds}:
                time.sleep({marketplace_list_sleep_seconds})
            if backdate_marker and os.path.exists(backdate_marker):
                old = time.time() - {backdate_marker_seconds_ago}
                os.utime(backdate_marker, (old, old))
            if {marketplace_list_exit_code} == 0:
                print({repr(marketplace_stdout)})
            sys.exit({marketplace_list_exit_code})

        if is_subcommand("plugin", "install"):
            rest = args[2:]
            plugin_id = ""
            skip_next = False
            for tok in rest:
                if skip_next:
                    skip_next = False
                    continue
                if tok in ("-s", "--scope"):
                    skip_next = True
                    continue
                if tok == "--":
                    continue
                if tok.startswith("-"):
                    continue
                if not plugin_id:
                    plugin_id = tok
            if install_log:
                with open(install_log, "a") as f:
                    f.write(json.dumps({{"plugin": plugin_id, "args": args, "cwd": os.getcwd()}}) + "\\n")
            sys.exit(1 if plugin_id in FAIL_INSTALL_IDS else 0)

        sys.stderr.write("unhandled: " + repr(args) + chr(10))
        sys.exit(1)
    """)
    shim_path = bin_dir / "claude"
    shim_path.write_text(shim)
    shim_path.chmod(0o755)


def _run_reconciler(
    repo_root: Path,
    *,
    config_dir: Path,
    bin_dir: Path,
    cwd: Path | None = None,
    extra_env: dict | None = None,
    timeout: float = 30,
) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "CLAUDE_CONFIG_DIR": str(config_dir),
        **_git_isolation_env(config_dir.parent),
    }
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [_BASH, str(_RECONCILE_SH), str(repo_root)],
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd else None,
        env=env,
        timeout=timeout,
    )


def _audit_lines(config_dir: Path) -> list[dict]:
    log_path = config_dir / "plugin-bootstrap" / "audit.log"
    if not log_path.exists():
        return []
    return [json.loads(line) for line in log_path.read_text().splitlines() if line]


def _install_log_entries(install_log: Path) -> list[dict]:
    if not install_log.exists():
        return []
    return [json.loads(line) for line in install_log.read_text().splitlines() if line]


def _pid_start_time(pid: int) -> str:
    """The exact recipe reconcile-project-plugins.sh's own
    _reconciler_pid_start_time uses -- a test-side helper, not a call into
    production code, so a test can construct a lock file's expected stamp
    independently of the script under test."""
    return subprocess.run(
        ["ps", "-o", "lstart=", "-p", str(pid)],
        env={**os.environ, "LC_ALL": "C", "TZ": "UTC"},
        capture_output=True,
        text=True,
        check=True,
    ).stdout.rstrip("\n")


@pytest.fixture
def live_pid():
    """Yield the pid of a real process this fixture owns for the test's
    duration, terminated on teardown -- a safe stand-in for "some other live
    reconciler" in a lock-contention test. Mirrors
    claude/.claude/hooks/tests/conftest.py's fixture of the same name,
    duplicated rather than shared since neither test tree's conftest imports
    the other's."""
    proc = subprocess.Popen(["sleep", "3600"])
    try:
        yield proc.pid
    finally:
        proc.terminate()
        proc.wait()


_MINIMAL_TOOLS = ["dirname", "readlink"]


def _minimal_path_without_timeout(tmp_path: Path, *, include_claude: bool) -> Path:
    """A PATH carrying only what sourcing _lib.sh and reading argv $0 need
    (`dirname`, `readlink`), deliberately excluding `timeout`/`gtimeout` --
    for the M5 preconditions that must refuse before ever calling either."""
    bin_dir = tmp_path / "minimal-bin"
    bin_dir.mkdir(exist_ok=True)
    for tool in _MINIMAL_TOOLS:
        tool_path = shutil.which(tool)
        assert tool_path, f"{tool} not found on this machine's PATH"
        (bin_dir / tool).symlink_to(tool_path)
    if include_claude:
        claude_stub = bin_dir / "claude"
        claude_stub.write_text("#!/bin/sh\nexit 0\n")
        claude_stub.chmod(0o755)
    return bin_dir


# ---------------------------------------------------------------------------
# No-op paths
# ---------------------------------------------------------------------------

class TestNoOpPaths:
    def test_exit_zero_when_no_enabled_plugins(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        _init_repo(repo, enabled_plugins={})
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        _make_reconciler_claude_shim(bin_dir)

        result = _run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        assert _audit_lines(config_dir) == []

    def test_exit_zero_when_everything_already_installed(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        _init_repo(repo, enabled_plugins={"foo@bar": True})
        config_dir = tmp_path / "cfg"
        _write_registry(config_dir, {"foo@bar": [{"scope": "local", "installPath": "x", "projectPath": str(repo)}]})
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        _make_reconciler_claude_shim(bin_dir, install_log=install_log)

        result = _run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        assert _audit_lines(config_dir) == []
        assert _install_log_entries(install_log) == []


class TestRegistryCorruption:
    def test_present_but_unparseable_registry_logs_and_exits_zero(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        _init_repo(repo, enabled_plugins={"foo@bar": True})
        config_dir = tmp_path / "cfg"
        registry_dir = config_dir / "plugins"
        registry_dir.mkdir(parents=True)
        (registry_dir / "installed_plugins.json").write_text("{not valid json")
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        _make_reconciler_claude_shim(bin_dir, install_log=install_log)

        result = _run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        decisions = [line["decision"] for line in _audit_lines(config_dir)]
        assert decisions == ["registry_unparseable_or_version_mismatch"]
        assert _install_log_entries(install_log) == []

    def test_registry_version_mismatch_logs_and_exits_zero(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        _init_repo(repo, enabled_plugins={"foo@bar": True})
        config_dir = tmp_path / "cfg"
        _write_registry(config_dir, {}, version=1)
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        _make_reconciler_claude_shim(bin_dir, install_log=install_log)

        result = _run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        decisions = [line["decision"] for line in _audit_lines(config_dir)]
        assert decisions == ["registry_unparseable_or_version_mismatch"]
        assert _install_log_entries(install_log) == []


# ---------------------------------------------------------------------------
# M5 preconditions: refuse before ever reaching a `claude` call.
# ---------------------------------------------------------------------------

class TestPreconditionRefusals:
    def test_no_launch_when_claude_absent_from_path(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        _init_repo(repo, enabled_plugins={"foo@bar": True})
        config_dir = tmp_path / "cfg"
        bin_dir = _minimal_path_without_timeout(tmp_path, include_claude=False)

        env = {"PATH": str(bin_dir), "HOME": str(tmp_path / "home"), "CLAUDE_CONFIG_DIR": str(config_dir)}
        result = subprocess.run(
            [_BASH, str(_RECONCILE_SH), str(repo)],
            capture_output=True, text=True, env=env, timeout=30,
        )

        assert result.returncode == 0, repr(result)
        assert not (config_dir / "plugin-bootstrap").exists()

    def test_no_launch_when_neither_timeout_nor_gtimeout_present(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        _init_repo(repo, enabled_plugins={"foo@bar": True})
        config_dir = tmp_path / "cfg"
        bin_dir = _minimal_path_without_timeout(tmp_path, include_claude=True)

        env = {"PATH": str(bin_dir), "HOME": str(tmp_path / "home"), "CLAUDE_CONFIG_DIR": str(config_dir)}
        result = subprocess.run(
            [_BASH, str(_RECONCILE_SH), str(repo)],
            capture_output=True, text=True, env=env, timeout=30,
        )

        assert result.returncode == 0, repr(result)
        assert not (config_dir / "plugin-bootstrap").exists()


# ---------------------------------------------------------------------------
# M3: re-validation at the reconciler's own point of use.
# ---------------------------------------------------------------------------

class TestPluginIdRevalidation:
    def test_invalid_id_is_skipped_and_valid_sibling_still_installs(self, tmp_path: Path) -> None:
        """A hand-run or stale-hook invocation could carry an id that never
        passed M3 -- the reconciler must still catch it, independent of
        whatever validation the hook already did, without derailing a
        legitimate sibling id in the same run."""
        repo = tmp_path / "repo"
        _init_repo(repo, enabled_plugins={"-bad@bar": True, "foo@bar": True})
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        _make_reconciler_claude_shim(
            bin_dir,
            marketplace_entries=[{"name": "bar", "source": "directory"}],
            install_log=install_log,
        )

        result = _run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        installed_ids = [entry["plugin"] for entry in _install_log_entries(install_log)]
        assert installed_ids == ["foo@bar"]
        audit_entries = {line["plugin_id"]: line["decision"] for line in _audit_lines(config_dir)}
        assert audit_entries[f"invalid-id(len={len('-bad@bar')})"] == "m3_invalid_id_skip"
        assert audit_entries["foo@bar"] == "install_succeeded"


# ---------------------------------------------------------------------------
# Local opt-out: a repo's settings.local.json can veto an id; an unreadable one
# vetoes the whole repo.
# ---------------------------------------------------------------------------

class TestLocalOptOut:
    def test_local_false_id_is_not_installed_but_sibling_id_is(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        _init_repo(repo, enabled_plugins={"foo@bar": True, "baz@bar": True})
        (repo / ".claude" / "settings.local.json").write_text(
            json.dumps({"enabledPlugins": {"foo@bar": False}})
        )
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        _make_reconciler_claude_shim(
            bin_dir,
            marketplace_entries=[{"name": "bar", "source": "directory"}],
            install_log=install_log,
        )

        result = _run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        assert [entry["plugin"] for entry in _install_log_entries(install_log)] == ["baz@bar"]
        audit_entries = {line["plugin_id"]: line["decision"] for line in _audit_lines(config_dir)}
        assert audit_entries == {"baz@bar": "install_succeeded"}

    def test_unreadable_local_settings_installs_nothing_and_logs_the_skip(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        _init_repo(repo, enabled_plugins={"foo@bar": True, "baz@bar": True})
        (repo / ".claude" / "settings.local.json").write_text("{not valid json")
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        _make_reconciler_claude_shim(
            bin_dir,
            marketplace_entries=[{"name": "bar", "source": "directory"}],
            install_log=install_log,
        )

        result = _run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        decisions = [line["decision"] for line in _audit_lines(config_dir)]
        assert decisions == ["m10_local_optout_unreadable"]
        assert _install_log_entries(install_log) == []


# ---------------------------------------------------------------------------
# M4: marketplace precondition, source-type allowlist, credential denylist.
# ---------------------------------------------------------------------------

class TestMarketplacePrecondition:
    def test_skip_when_marketplace_not_registered(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        _init_repo(repo, enabled_plugins={"foo@bar": True})
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        _make_reconciler_claude_shim(
            bin_dir,
            marketplace_entries=[{"name": "someone-else", "source": "directory"}],
            install_log=install_log,
        )

        result = _run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        decisions = [line["decision"] for line in _audit_lines(config_dir)]
        assert decisions == ["m4_marketplace_not_registered"]
        assert _install_log_entries(install_log) == []

    def test_skip_all_when_marketplace_listing_exits_nonzero(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        _init_repo(repo, enabled_plugins={"foo@bar": True})
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        _make_reconciler_claude_shim(bin_dir, marketplace_list_exit_code=1, install_log=install_log)

        result = _run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        decisions = [line["decision"] for line in _audit_lines(config_dir)]
        assert decisions == ["m4_marketplace_list_failed"]
        assert _install_log_entries(install_log) == []

    def test_skip_all_when_marketplace_listing_returns_empty_array(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        _init_repo(repo, enabled_plugins={"foo@bar": True})
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        _make_reconciler_claude_shim(bin_dir, marketplace_entries=[], install_log=install_log)

        result = _run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        decisions = [line["decision"] for line in _audit_lines(config_dir)]
        assert decisions == ["m4_marketplace_list_failed"]
        assert _install_log_entries(install_log) == []

    def test_skip_all_when_marketplace_listing_output_is_unparseable(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        _init_repo(repo, enabled_plugins={"foo@bar": True})
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        _make_reconciler_claude_shim(bin_dir, marketplace_raw_stdout="not json at all", install_log=install_log)

        result = _run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        decisions = [line["decision"] for line in _audit_lines(config_dir)]
        assert decisions == ["m4_marketplace_list_failed"]
        assert _install_log_entries(install_log) == []

    def test_skip_all_when_marketplace_listing_times_out(self, tmp_path: Path) -> None:
        """Plan bullet 177's "times out" case, exercised at this call site
        rather than only via _lib_capped_for's own generic tests: the shim
        sleeps past _RECONCILER_CLAUDE_CALL_CAP_SECONDS (30s) so
        _lib_capped_for's kill-and-nonzero-exit path, not a shim exiting
        nonzero directly, is what produces the skip. Real wall-clock cost
        (~30s) is accepted to exercise the actual cap value this script
        uses, not a shortened stand-in."""
        repo = tmp_path / "repo"
        _init_repo(repo, enabled_plugins={"foo@bar": True})
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        _make_reconciler_claude_shim(
            bin_dir,
            marketplace_entries=[{"name": "bar", "source": "directory"}],
            marketplace_list_sleep_seconds=32,
            install_log=install_log,
        )

        result = _run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir, timeout=45)

        assert result.returncode == 0, repr(result)
        decisions = [line["decision"] for line in _audit_lines(config_dir)]
        assert decisions == ["m4_marketplace_list_failed"]
        assert _install_log_entries(install_log) == []

    @pytest.mark.parametrize(
        "source_value",
        ["url", "unrecognized-value", "", None, "git-subdir", "Git", "github-enterprise", "directory "],
        ids=[
            "url",
            "unrecognized",
            "empty-string",
            "missing-field",
            "git-subdir",
            "capitalized-git",
            "github-enterprise",
            "directory-trailing-space",
        ],
    )
    def test_skip_when_source_type_is_not_allowlisted(
        self, tmp_path: Path, source_value: str | None
    ) -> None:
        """M4's allowlist: proceed only for `github`/`directory`/`git`; every other
        value -- including an unrecognized one, an empty string, or the
        field missing outright -- takes the same fail-closed path, so a
        wrong or absent field name can never silently admit every
        marketplace the way a denylist would."""
        repo = tmp_path / "repo"
        _init_repo(repo, enabled_plugins={"foo@bar": True})
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        entry = {"name": "bar"}
        if source_value is not None:
            entry["source"] = source_value
        _make_reconciler_claude_shim(bin_dir, marketplace_entries=[entry], install_log=install_log)

        result = _run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        decisions = [line["decision"] for line in _audit_lines(config_dir)]
        assert decisions == ["m4_source_type_not_allowed"]
        assert _install_log_entries(install_log) == []

    @pytest.mark.parametrize("source_value", ["github", "directory", "git"])
    def test_proceeds_when_source_type_is_allowed(self, tmp_path: Path, source_value: str) -> None:
        repo = tmp_path / "repo"
        _init_repo(repo, enabled_plugins={"foo@bar": True})
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        _make_reconciler_claude_shim(
            bin_dir,
            marketplace_entries=[{"name": "bar", "source": source_value}],
            install_log=install_log,
        )

        result = _run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        assert [entry["plugin"] for entry in _install_log_entries(install_log)] == ["foo@bar"]
        decisions = [line["decision"] for line in _audit_lines(config_dir)]
        assert decisions == ["install_succeeded"]

    def test_credential_denylisted_id_is_skipped(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        _init_repo(repo, enabled_plugins={_DENYLISTED_PLUGIN_ID: True})
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        _make_reconciler_claude_shim(
            bin_dir,
            marketplace_entries=[{"name": "claude-config", "source": "directory"}],
            install_log=install_log,
        )

        result = _run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        decisions = [line["decision"] for line in _audit_lines(config_dir)]
        assert decisions == ["m4_credential_denylist_skip"]
        assert _install_log_entries(install_log) == []


# ---------------------------------------------------------------------------
# M6: scope-ladder write-target guard.
# ---------------------------------------------------------------------------

class TestScopeGuard:
    def test_no_install_when_settings_local_not_gitignored(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        _init_repo(repo, enabled_plugins={"foo@bar": True}, gitignore_settings_local=False)
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        _make_reconciler_claude_shim(
            bin_dir,
            marketplace_entries=[{"name": "bar", "source": "directory"}],
            install_log=install_log,
        )

        result = _run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        decisions = [line["decision"] for line in _audit_lines(config_dir)]
        assert decisions == ["m6_not_ignored"]
        assert _install_log_entries(install_log) == []

    def test_no_install_when_settings_local_already_tracked(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        _init_repo(repo, enabled_plugins={"foo@bar": True})
        (repo / ".claude" / "settings.local.json").write_text("{}")
        subprocess.run(
            ["git", "add", "-f", ".claude/settings.local.json"], cwd=repo, check=True,
            env={**os.environ, **_git_isolation_env(tmp_path)},
        )
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        _make_reconciler_claude_shim(
            bin_dir,
            marketplace_entries=[{"name": "bar", "source": "directory"}],
            install_log=install_log,
        )

        result = _run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        decisions = [line["decision"] for line in _audit_lines(config_dir)]
        assert decisions == ["m6_already_tracked"]
        assert _install_log_entries(install_log) == []


# ---------------------------------------------------------------------------
# M9: hash-keyed failure stamp.
# ---------------------------------------------------------------------------

class TestFailureStamp:
    def test_failed_install_is_stamped_and_suppressed_until_settings_change(
        self, tmp_path: Path
    ) -> None:
        repo = tmp_path / "repo"
        _init_repo(repo, enabled_plugins={"foo@bar": True})
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        _make_reconciler_claude_shim(
            bin_dir,
            marketplace_entries=[{"name": "bar", "source": "directory"}],
            fail_install_ids=frozenset({"foo@bar"}),
            install_log=install_log,
        )

        first = _run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)
        assert first.returncode == 0, repr(first)
        assert len(_install_log_entries(install_log)) == 1
        assert [line["decision"] for line in _audit_lines(config_dir)] == ["install_failed"]

        second = _run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)
        assert second.returncode == 0, repr(second)
        assert len(_install_log_entries(install_log)) == 1, "a suppressed retry must not call install again"
        decisions = [line["decision"] for line in _audit_lines(config_dir)]
        assert decisions == ["install_failed", "m9_stamp_suppressed"]

        # Changing the repo's settings changes the stamp's hash, so a
        # once-failed id is retried rather than suppressed forever.
        (repo / ".claude" / "settings.json").write_text(
            json.dumps({"enabledPlugins": {"foo@bar": True, "extra-noop-key": False}})
        )
        third = _run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)
        assert third.returncode == 0, repr(third)
        assert len(_install_log_entries(install_log)) == 2, "a settings change must lift the suppression"


# ---------------------------------------------------------------------------
# M6/M7 argv content, via the shared, dispatch-extended claude shim.
# ---------------------------------------------------------------------------

class TestInstallArgv:
    def test_install_argv_uses_scope_local_and_omits_accept_flags(self, tmp_path: Path) -> None:
        """Exercises test_update_claude_config_plugins.py's own
        `_make_claude_shim`, extended for exactly this assertion -- the
        argv actually sent to `claude plugin install` carries `-s local`
        and never `-y`, `--yes`, or `--accept-command` (M6, M7)."""
        repo = tmp_path / "repo"
        _init_repo(repo, enabled_plugins={"foo@claude-config": True})
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        _make_claude_shim(
            bin_dir,
            marketplace_location=tmp_path / "unused-marketplace-dir",
            installed_plugins=[],
            update_log=tmp_path / "unused-update.log",
            install_log=install_log,
        )

        result = _run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        entries = _install_log_entries(install_log)
        assert len(entries) == 1
        argv = entries[0]["args"]
        assert "-s" in argv and "local" in argv[argv.index("-s") + 1 : argv.index("-s") + 2]
        for forbidden in ("-y", "--yes", "--accept-command"):
            assert forbidden not in argv


# ---------------------------------------------------------------------------
# M5 invocation contract: the passed repo root wins, not process cwd; an
# adversarial-shaped path survives argv intact.
# ---------------------------------------------------------------------------

class TestInvocationContract:
    def test_installs_using_passed_repo_root_not_process_cwd(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        _init_repo(repo, enabled_plugins={"foo@bar": True})
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        _make_reconciler_claude_shim(
            bin_dir,
            marketplace_entries=[{"name": "bar", "source": "directory"}],
            install_log=install_log,
        )

        result = _run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir, cwd=elsewhere)

        assert result.returncode == 0, repr(result)
        entries = _install_log_entries(install_log)
        assert len(entries) == 1
        # Real path, not `elsewhere`: `claude plugin install` has no
        # cwd-override flag (row 2), so the reconciler must literally `cd`
        # into the resolved repo root before invoking it, mirroring
        # test_set_session_title_from_branch.py's
        # test_payload_cwd_overrides_process_cwd for the hook side of this
        # same invariant.
        assert entries[0]["cwd"] == str(repo.resolve())

    def test_repo_root_with_space_and_metacharacter_survives_argv(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo dir $with metachar"
        _init_repo(repo, enabled_plugins={"foo@bar": True})
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        _make_reconciler_claude_shim(
            bin_dir,
            marketplace_entries=[{"name": "bar", "source": "directory"}],
            install_log=install_log,
        )

        result = _run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        assert [entry["plugin"] for entry in _install_log_entries(install_log)] == ["foo@bar"]


# ---------------------------------------------------------------------------
# M8: PID-plus-process-start-time lock.
# ---------------------------------------------------------------------------

class TestLock:
    def test_second_invocation_exits_silently_when_lock_genuinely_held(
        self, tmp_path: Path, live_pid: int
    ) -> None:
        """A lock file naming a still-live pid whose freshly re-queried
        start time matches the stamp is genuinely held -- the caller exits 0
        without acting, and its own install never runs. Run under a
        non-default ambient TZ to confirm the LC_ALL=C TZ=UTC pin, not the
        ambient environment, governs the comparison -- production always
        re-queries under that same pin, so a hostile ambient TZ must not
        make a genuinely-live holder look evicted."""
        repo = tmp_path / "repo"
        _init_repo(repo, enabled_plugins={"foo@bar": True})
        config_dir = tmp_path / "cfg"
        bootstrap_dir = config_dir / "plugin-bootstrap"
        bootstrap_dir.mkdir(parents=True)
        stamped_start = _pid_start_time(live_pid)
        (bootstrap_dir / "reconciler.lock").write_text(f"{live_pid}\t{stamped_start}\n")
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        _make_reconciler_claude_shim(
            bin_dir,
            marketplace_entries=[{"name": "bar", "source": "directory"}],
            install_log=install_log,
        )

        result = _run_reconciler(
            repo, config_dir=config_dir, bin_dir=bin_dir, extra_env={"TZ": "Asia/Tokyo"}
        )

        assert result.returncode == 0, repr(result)
        assert _install_log_entries(install_log) == []
        decisions = [line["decision"] for line in _audit_lines(config_dir)]
        assert decisions == ["lock_held"]
        # The original holder's lock file is untouched by the losing caller.
        assert (bootstrap_dir / "reconciler.lock").read_text() == f"{live_pid}\t{stamped_start}\n"

    def test_dead_pid_lock_is_evicted_and_install_proceeds(self, tmp_path: Path) -> None:
        """A lock file naming a pid that isn't running at all -- `kill -0`
        itself fails, a simpler and more common case than the PID-wraparound
        eviction below, where `kill -0` succeeds against an unrelated
        process."""
        repo = tmp_path / "repo"
        _init_repo(repo, enabled_plugins={"foo@bar": True})
        config_dir = tmp_path / "cfg"
        bootstrap_dir = config_dir / "plugin-bootstrap"
        bootstrap_dir.mkdir(parents=True)
        dead_pid = _dead_pid()
        (bootstrap_dir / "reconciler.lock").write_text(f"{dead_pid}\tirrelevant stamp\n")
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        _make_reconciler_claude_shim(
            bin_dir,
            marketplace_entries=[{"name": "bar", "source": "directory"}],
            install_log=install_log,
        )

        result = _run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        assert [entry["plugin"] for entry in _install_log_entries(install_log)] == ["foo@bar"]
        decisions = [line["decision"] for line in _audit_lines(config_dir)]
        assert decisions == ["install_succeeded"]

    def test_pid_wraparound_is_evicted_and_install_proceeds(
        self, tmp_path: Path, live_pid: int
    ) -> None:
        """A lock file naming a still-live pid whose freshly re-queried
        start time does NOT match the stamp means the pid was recycled to an
        unrelated live process since the original holder wrote the lock --
        the original holder is dead, so the caller evicts and proceeds."""
        repo = tmp_path / "repo"
        _init_repo(repo, enabled_plugins={"foo@bar": True})
        config_dir = tmp_path / "cfg"
        bootstrap_dir = config_dir / "plugin-bootstrap"
        bootstrap_dir.mkdir(parents=True)
        (bootstrap_dir / "reconciler.lock").write_text(f"{live_pid}\tsome mismatched stamp\n")
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        _make_reconciler_claude_shim(
            bin_dir,
            marketplace_entries=[{"name": "bar", "source": "directory"}],
            install_log=install_log,
        )

        result = _run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        assert [entry["plugin"] for entry in _install_log_entries(install_log)] == ["foo@bar"]
        decisions = [line["decision"] for line in _audit_lines(config_dir)]
        assert decisions == ["install_succeeded"]

    def test_losing_contender_never_touches_the_marker(self, tmp_path: Path, live_pid: int) -> None:
        """With the marker already in place from a prior lock-winning run, a
        second, losing invocation must not reset its mtime -- doing so would
        silently push out the wall-clock ceiling's baseline for whichever
        invocation actually holds the lock."""
        repo = tmp_path / "repo"
        _init_repo(repo, enabled_plugins={"foo@bar": True})
        config_dir = tmp_path / "cfg"
        bootstrap_dir = config_dir / "plugin-bootstrap"
        bootstrap_dir.mkdir(parents=True)
        stamped_start = _pid_start_time(live_pid)
        (bootstrap_dir / "reconciler.lock").write_text(f"{live_pid}\t{stamped_start}\n")
        marker_file = bootstrap_dir / ".reconciler-start"
        marker_file.write_text("")
        old_mtime = time.time() - 100
        os.utime(marker_file, (old_mtime, old_mtime))
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        _make_reconciler_claude_shim(bin_dir, marketplace_entries=[{"name": "bar", "source": "directory"}])

        result = _run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        assert marker_file.stat().st_mtime == pytest.approx(old_mtime, abs=1)


# ---------------------------------------------------------------------------
# M5: total per-invocation wall-clock ceiling.
# ---------------------------------------------------------------------------

class TestWallClockCeiling:
    def test_ceiling_reached_skips_remaining_ids_without_an_actual_wait(self, tmp_path: Path) -> None:
        """The claude shim backdates the freshly-created marker file (via
        os.utime, mirroring test_advance_past_commit_stall.py's own idiom)
        as a side effect of the run's own marketplace-list call -- the
        first `claude` call after the marker is created (M5) -- so every id
        in this run sees an already-exceeded ceiling with no real 600s
        wait."""
        repo = tmp_path / "repo"
        _init_repo(repo, enabled_plugins={"foo@bar": True})
        config_dir = tmp_path / "cfg"
        marker_file = config_dir / "plugin-bootstrap" / ".reconciler-start"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        _make_reconciler_claude_shim(
            bin_dir,
            marketplace_entries=[{"name": "bar", "source": "directory"}],
            install_log=install_log,
            backdate_marker=marker_file,
            backdate_marker_seconds_ago=700,
        )

        result = _run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        assert _install_log_entries(install_log) == []
        decisions = [line["decision"] for line in _audit_lines(config_dir)]
        assert decisions == ["wall_clock_ceiling_reached"]

    def test_ceiling_reached_logs_one_line_and_ends_the_run_however_many_ids_remain(
        self, tmp_path: Path
    ) -> None:
        """The ceiling stays hit once hit, so a run logs it once and stops
        iterating instead of paying a log append per remaining id."""
        repo = tmp_path / "repo"
        _init_repo(repo, enabled_plugins={"foo@bar": True, "baz@bar": True, "qux@bar": True})
        config_dir = tmp_path / "cfg"
        marker_file = config_dir / "plugin-bootstrap" / ".reconciler-start"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        _make_reconciler_claude_shim(
            bin_dir,
            marketplace_entries=[{"name": "bar", "source": "directory"}],
            install_log=install_log,
            backdate_marker=marker_file,
            backdate_marker_seconds_ago=700,
        )

        result = _run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        assert _install_log_entries(install_log) == []
        decisions = [line["decision"] for line in _audit_lines(config_dir)]
        assert decisions == ["wall_clock_ceiling_reached"]


# ---------------------------------------------------------------------------
# Audit-log fields: repo-controlled plugin ids reach the log unvalidated on
# m3_invalid_id_skip and wall_clock_ceiling_reached, so only an id that passes
# _lib_validate_plugin_id is logged as written.
# ---------------------------------------------------------------------------

# U+009B is the C1 control-sequence introducer; GNU `tr -d '[:cntrl:]'` leaves
# it in a UTF-8 locale. ESC is C0. U+00E9 is multibyte.
_C1_CSI = "\u009b"
_C0_ESC = "\x1b"
_MULTIBYTE_LETTER = "\u00e9"
# The whole run must finish well under _lib_jq's 5 s cap. One jq sanitize pass
# over a 16,000-character control-dense value takes seconds, so a flood of such
# ids would take far longer than this.
_HOSTILE_ID_RUN_MAX_SECONDS = 3.0
_CONTROL_DENSE_FLOOD_SIZE = 6

# Each case is the set of enabledPlugins keys; distinct lengths keep the logged markers distinct.
_HOSTILE_ID_SETS = {
    "c1-esc-del-multibyte": [f"-bad{_C1_CSI}{_C0_ESC}[31m{_MULTIBYTE_LETTER}\x7f@bar"],
    "200-kib-key": ["-" + "a" * (200 * 1024) + "@bar"],
    "20000-control-character-flood": [
        "foo" + "\x01" * (20000 + offset) + "@bar" for offset in range(_CONTROL_DENSE_FLOOD_SIZE)
    ],
    "20000-c1-character-flood": [
        "foo" + _C1_CSI * (20000 + offset) + "@bar" for offset in range(_CONTROL_DENSE_FLOOD_SIZE)
    ],
}


def _assert_no_control_characters(field: str) -> None:
    offending = [hex(ord(char)) for char in field if unicodedata.category(char) == "Cc"]
    assert offending == [], f"control characters survived in {field!r}: {offending}"


class TestAuditLogFields:
    @pytest.mark.parametrize("hostile_ids", list(_HOSTILE_ID_SETS.values()), ids=list(_HOSTILE_ID_SETS))
    def test_invalid_id_is_logged_as_a_length_marker_and_the_run_continues_fast(
        self, tmp_path: Path, hostile_ids: list[str]
    ) -> None:
        """An invalid id is repo-controlled text, so the log carries only its length.
        The run exits 0, processes the id after it, and stays far under the jq cap."""
        repo = tmp_path / "repo"
        _init_repo(repo, enabled_plugins={**dict.fromkeys(hostile_ids, True), "foo@bar": True})
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        _make_reconciler_claude_shim(
            bin_dir, marketplace_entries=[{"name": "someone-else", "source": "directory"}]
        )

        started = time.monotonic()
        result = _run_reconciler(
            repo, config_dir=config_dir, bin_dir=bin_dir, extra_env={"LC_ALL": "C"}
        )
        elapsed = time.monotonic() - started

        assert result.returncode == 0, repr(result)
        assert elapsed < _HOSTILE_ID_RUN_MAX_SECONDS, f"run took {elapsed:.1f}s"
        raw_log = (config_dir / "plugin-bootstrap" / "audit.log").read_text(encoding="utf-8")
        for line in raw_log.splitlines():
            _assert_no_control_characters(line)
        decision_by_plugin_id = {
            entry["plugin_id"]: entry["decision"] for entry in _audit_lines(config_dir)
        }
        assert decision_by_plugin_id == {
            **{
                f"invalid-id(len={len(hostile_id.encode('utf-8'))})": "m3_invalid_id_skip"
                for hostile_id in hostile_ids
            },
            "foo@bar": "m4_marketplace_not_registered",
        }

    def test_wall_clock_ceiling_line_with_hostile_id_logs_the_length_marker(self, tmp_path: Path) -> None:
        """wall_clock_ceiling_reached fires before id validation, so the
        unvalidated id reaches the log on that path too."""
        hostile_id = f"foo{_C1_CSI}{_C0_ESC}{_MULTIBYTE_LETTER}@bar"
        repo = tmp_path / "repo"
        _init_repo(repo, enabled_plugins={hostile_id: True})
        config_dir = tmp_path / "cfg"
        marker_file = config_dir / "plugin-bootstrap" / ".reconciler-start"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        _make_reconciler_claude_shim(
            bin_dir,
            marketplace_entries=[{"name": "bar", "source": "directory"}],
            backdate_marker=marker_file,
            backdate_marker_seconds_ago=700,
        )

        result = _run_reconciler(
            repo, config_dir=config_dir, bin_dir=bin_dir, extra_env={"LC_ALL": "C"}
        )

        assert result.returncode == 0, repr(result)
        [entry] = _audit_lines(config_dir)
        assert entry["decision"] == "wall_clock_ceiling_reached"
        assert entry["plugin_id"] == f"invalid-id(len={len(hostile_id.encode('utf-8'))})"

    def test_non_ascii_letter_id_is_invalid_even_under_a_locale_that_widens_bracket_ranges(
        self, tmp_path: Path
    ) -> None:
        """The id alphabet is ASCII-only only because the validator pins
        LC_ALL=C; under a widening locale an unpinned `[A-Za-z]` admits the
        accented letter and the id would be logged as written. N counts
        characters here because the ambient locale is UTF-8."""
        accented_id = f"caf{_MULTIBYTE_LETTER}@bar"
        repo = tmp_path / "repo"
        _init_repo(repo, enabled_plugins={accented_id: True})
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        _make_reconciler_claude_shim(
            bin_dir, marketplace_entries=[{"name": "bar", "source": "directory"}]
        )

        result = _run_reconciler(
            repo, config_dir=config_dir, bin_dir=bin_dir,
            extra_env={"LC_ALL": _locale_that_widens_ascii_ranges()},
        )

        assert result.returncode == 0, repr(result)
        [entry] = _audit_lines(config_dir)
        assert entry["decision"] == "m3_invalid_id_skip"
        assert entry["plugin_id"] == f"invalid-id(len={len(accented_id)})"

    def test_valid_shaped_id_over_the_max_length_is_invalid_and_the_run_stays_fast(
        self, tmp_path: Path
    ) -> None:
        """A 96 KiB marketplace half is valid-shaped but past the length bound,
        so it is refused before the super-linear `##*@` removal runs."""
        long_id = "a@" + "b" * (96 * 1024)
        repo = tmp_path / "repo"
        _init_repo(repo, enabled_plugins={long_id: True, "foo@bar": True})
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        _make_reconciler_claude_shim(
            bin_dir,
            marketplace_entries=[{"name": "bar", "source": "directory"}],
            install_log=install_log,
        )

        started = time.monotonic()
        result = _run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)
        elapsed = time.monotonic() - started

        assert result.returncode == 0, repr(result)
        assert elapsed < _HOSTILE_ID_RUN_MAX_SECONDS, f"run took {elapsed:.1f}s"
        assert [entry["plugin"] for entry in _install_log_entries(install_log)] == ["foo@bar"]
        decision_by_plugin_id = {
            entry["plugin_id"]: entry["decision"] for entry in _audit_lines(config_dir)
        }
        assert decision_by_plugin_id == {
            f"invalid-id(len={len(long_id)})": "m3_invalid_id_skip",
            "foo@bar": "install_succeeded",
        }

    def test_valid_id_is_logged_verbatim(self, tmp_path: Path) -> None:
        valid_id = "Foo.Bar_1-x@my-market.place_2"
        repo = tmp_path / "repo"
        _init_repo(repo, enabled_plugins={valid_id: True})
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        _make_reconciler_claude_shim(
            bin_dir, marketplace_entries=[{"name": "someone-else", "source": "directory"}]
        )

        result = _run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        [entry] = _audit_lines(config_dir)
        assert entry["plugin_id"] == valid_id
        assert entry["decision"] == "m4_marketplace_not_registered"

    def test_repo_root_control_characters_are_stripped_from_the_audit_line(self, tmp_path: Path) -> None:
        """The repo root is a directory name the repo's owner controls: C0 (ESC,
        newline) and C1 (U+009B) must not reach the shared log, and the entry
        must stay on one line."""
        repo = tmp_path / f"repo{_C0_ESC}[31m{_C1_CSI}\nend"
        _init_repo(repo, enabled_plugins={"foo@bar": True})
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        _make_reconciler_claude_shim(
            bin_dir, marketplace_entries=[{"name": "someone-else", "source": "directory"}]
        )

        result = _run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        raw_log = (config_dir / "plugin-bootstrap" / "audit.log").read_text(encoding="utf-8")
        [entry] = [json.loads(line) for line in raw_log.splitlines()]
        assert entry["decision"] == "m4_marketplace_not_registered"
        assert entry["repo_root"] == str(repo.parent / "repo[31mend")
        assert raw_log.count("\n") == 1

    def test_multibyte_repo_root_is_logged_intact(self, tmp_path: Path) -> None:
        repo = tmp_path / f"r{_MULTIBYTE_LETTER}po-\u65e5\u672c\u8a9e"
        _init_repo(repo, enabled_plugins={"foo@bar": True})
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        _make_reconciler_claude_shim(
            bin_dir, marketplace_entries=[{"name": "someone-else", "source": "directory"}]
        )

        result = _run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        [entry] = _audit_lines(config_dir)
        assert entry["repo_root"] == str(repo)


# ---------------------------------------------------------------------------
# M9: the shared, locked audit-log append survives two concurrent writers.
# ---------------------------------------------------------------------------

class TestAuditLogConcurrency:
    def test_two_concurrent_reconcilers_both_land_intact_and_distinct(self, tmp_path: Path) -> None:
        """Two independent repos reconciled at once against the same config
        dir, and so the same audit log and the same M8 lock -- mirrors
        test_lib_append_line_locked.py's own concurrency fixtures, since
        _lib_acquire_append_lock is the primitive both writers share here.
        M8's lock (a separate mutex from the audit log's own append lock)
        means exactly one of the two wins and reaches a real per-id
        decision while the other logs "lock held" -- both outcomes still
        append to the identical audit-log path at effectively the same
        moment, which is the property under test, and pins the M9/M8
        interaction as a side effect: a losing invocation's own decision is
        never lost or corrupted by racing the winner's append."""
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        _make_reconciler_claude_shim(bin_dir, marketplace_entries=[{"name": "someone-else", "source": "directory"}])

        repo_a = tmp_path / "repo-a"
        _init_repo(repo_a, enabled_plugins={"foo@bar": True})
        repo_b = tmp_path / "repo-b"
        _init_repo(repo_b, enabled_plugins={"baz@bar": True})

        env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "CLAUDE_CONFIG_DIR": str(config_dir),
            **_git_isolation_env(tmp_path),
        }
        proc_a = subprocess.Popen(
            [_BASH, str(_RECONCILE_SH), str(repo_a)], env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        proc_b = subprocess.Popen(
            [_BASH, str(_RECONCILE_SH), str(repo_b)], env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        out_a, err_a = proc_a.communicate(timeout=30)
        out_b, err_b = proc_b.communicate(timeout=30)

        assert proc_a.returncode == 0, err_a
        assert proc_b.returncode == 0, err_b
        lines = _audit_lines(config_dir)
        assert len(lines) == 2, "both writers' decisions must land as distinct, intact lines"
        decisions = sorted(line["decision"] for line in lines)
        # Exactly one of the two wins the shared M8 lock and reaches a real
        # per-id decision (the "bar" marketplace is registered as
        # "someone-else", so it resolves to "not registered"); the other
        # loses the lock. Either repo could be the winner, so only the
        # decision multiset -- not which repo produced which -- is pinned.
        assert decisions == ["lock_held", "m4_marketplace_not_registered"]
        plugin_ids = sorted(line["plugin_id"] for line in lines)
        assert plugin_ids == ["-", "baz@bar"] or plugin_ids == ["-", "foo@bar"]

    def test_two_lock_losers_on_the_same_repo_stay_distinct_via_pid(self, tmp_path: Path) -> None:
        """The scenario the `pid` field exists to disambiguate (M9's own
        text): two lock-losers on the *same* repo, reaching the identical
        decision/outcome/repo_root/plugin_id ("lock_held"/"skipped"/this
        repo/"-"), which would collapse into a single logged line under
        _lib_append_line_locked's whole-line dedup without `pid` making them
        byte-distinct. Distinct from the cross-repo race above, where
        repo_root/plugin_id already differ regardless of `pid`. Races three
        processes (not two) against the same repo so at least two lose the
        shared M8 lock -- with only two racers, exactly one always loses,
        never two."""
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        _make_reconciler_claude_shim(bin_dir, marketplace_entries=[{"name": "someone-else", "source": "directory"}])

        repo = tmp_path / "repo"
        _init_repo(repo, enabled_plugins={"foo@bar": True})

        env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "CLAUDE_CONFIG_DIR": str(config_dir),
            **_git_isolation_env(tmp_path),
        }
        procs = [
            subprocess.Popen(
                [_BASH, str(_RECONCILE_SH), str(repo)], env=env,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            for _ in range(3)
        ]
        results = [proc.communicate(timeout=30) for proc in procs]

        for proc, (_, err) in zip(procs, results, strict=True):
            assert proc.returncode == 0, err

        lines = _audit_lines(config_dir)
        assert len(lines) == 3, "all three writers' decisions must land as distinct, intact lines"
        decisions = sorted(line["decision"] for line in lines)
        # "bar" is registered as "someone-else", so the lock winner resolves
        # to "not registered"; the other two lose the shared M8 lock and
        # both log "lock_held" against the identical repo.
        assert decisions == ["lock_held", "lock_held", "m4_marketplace_not_registered"]
        lock_held_lines = [line for line in lines if line["decision"] == "lock_held"]
        assert lock_held_lines[0]["repo_root"] == lock_held_lines[1]["repo_root"]
        assert lock_held_lines[0]["plugin_id"] == lock_held_lines[1]["plugin_id"] == "-"
        assert lock_held_lines[0]["outcome"] == lock_held_lines[1]["outcome"] == "skipped"
        assert lock_held_lines[0]["pid"] != lock_held_lines[1]["pid"], (
            "two identical-content lock_held lines must stay distinct via pid alone"
        )


# ---------------------------------------------------------------------------
# M9: audit log's own size cap.
# ---------------------------------------------------------------------------

class TestAuditLogSizeCap:
    def test_audit_log_is_truncated_once_it_exceeds_the_size_threshold(self, tmp_path: Path) -> None:
        """M9's own text commits to a size-capped audit log -- confirm
        _audit_log truncates the file before appending once it exceeds
        _LIB_SIZE_THRESHOLD_BYTES (5 MB, _lib.sh), rather than letting a
        shared, never-swept log grow without bound."""
        repo = tmp_path / "repo"
        _init_repo(repo, enabled_plugins={"foo@bar": True})
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        _make_reconciler_claude_shim(
            bin_dir,
            marketplace_entries=[{"name": "someone-else", "source": "directory"}],
            install_log=install_log,
        )
        audit_log = config_dir / "plugin-bootstrap" / "audit.log"
        audit_log.parent.mkdir(parents=True)
        oversized_bytes = 5 * 1024 * 1024 + 1
        audit_log.write_text("x" * oversized_bytes)

        result = _run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        assert audit_log.stat().st_size < oversized_bytes, (
            "the oversized pre-existing content must be truncated away, not appended to"
        )
        lines = _audit_lines(config_dir)
        assert len(lines) == 1
        assert lines[0]["decision"] == "m4_marketplace_not_registered"

    def test_audit_log_past_size_cap_is_not_truncated_when_another_writer_holds_the_lock(self, tmp_path: Path) -> None:
        """Only the lock holder truncates: a live holder makes the bounded retries
        run out, and the reconciler then appends without truncating."""
        repo = tmp_path / "repo"
        _init_repo(repo, enabled_plugins={"foo@bar": True})
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        _make_reconciler_claude_shim(
            bin_dir, marketplace_entries=[{"name": "someone-else", "source": "directory"}]
        )
        audit_log = config_dir / "plugin-bootstrap" / "audit.log"
        audit_log.parent.mkdir(parents=True)
        oversized_line = "x" * (5 * 1024 * 1024 + 1)
        audit_log.write_text(oversized_line + "\n")
        lock_file = audit_log.parent / "audit.log.lock"
        lock_file.write_text(f"{os.getpid()}\n")

        result = _run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        logged_lines = audit_log.read_text().splitlines()
        assert logged_lines[0] == oversized_line
        assert json.loads(logged_lines[-1])["decision"] == "m4_marketplace_not_registered"
        assert lock_file.read_text().strip() == str(os.getpid()), "the reconciler must not release a lock it does not hold"

    @pytest.mark.skipif(os.geteuid() == 0, reason="root writes a read-only file")
    def test_failed_truncation_and_append_on_a_read_only_oversized_audit_log_keeps_the_run_clean(
        self, tmp_path: Path
    ) -> None:
        """A failing truncate or append must not abort the run (it would strand the
        reconciler lock) or leak the shell's redirection error to stderr."""
        repo = tmp_path / "repo"
        _init_repo(repo, enabled_plugins={"foo@bar": True})
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        _make_reconciler_claude_shim(
            bin_dir, marketplace_entries=[{"name": "someone-else", "source": "directory"}]
        )
        audit_log = config_dir / "plugin-bootstrap" / "audit.log"
        audit_log.parent.mkdir(parents=True)
        oversized_content = "x" * (5 * 1024 * 1024 + 1) + "\n"
        audit_log.write_text(oversized_content)
        audit_log.chmod(0o444)
        try:
            result = _run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)
        finally:
            audit_log.chmod(0o644)

        assert result.returncode == 0, repr(result)
        assert result.stderr == ""
        assert audit_log.read_text() == oversized_content
        assert not (audit_log.parent / "reconciler.lock").exists(), "the run must reach its lock release"
